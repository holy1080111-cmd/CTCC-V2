"""Dedicated Live reconciler: read, inhibit and authenticated proof append only.

No entry/Arm/close submission lives here. The key table is never readable by the
runtime actor. Source-owned proof signing and SQL acceptance are separate gates.
"""

from __future__ import annotations

import asyncio
import re
import ssl
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from sqlalchemy.pool import NullPool

from app.database.repositories import native_live_ledger_v1 as base
from app.domain import native_live_lifecycle_schema_v1 as spec
from app.domain.native_live_inherited_acl_v1 import (
    require_inherited_live_data_helpers_v1,
)
from app.domain.native_live_intent_v1 import (
    LiveIntentDataV1,
    exact_live_intent_document_v1,
)
from app.domain.native_live_legacy_safety_v1 import TABLES as LEGACY_TABLES
from app.domain.source_primitives import canonical, decode, sha
from app.okx_live import lifecycle_key_v1 as keys

_PROOF_CONSUME_KEY_V1 = object()
_READ_TABLES = (
    "alembic_version",
    "native_live_controls_v1",
    "native_live_reservations_v1",
    "native_live_intents_v1",
    "native_live_transitions_v1",
    *LEGACY_TABLES,
    spec.TABLES[1],
)
_SETTINGS = (
    "SET TRANSACTION ISOLATION LEVEL READ COMMITTED READ WRITE",
    "SET LOCAL search_path TO pg_catalog, public, pg_temp",
    "SET LOCAL TIME ZONE 'UTC'",
    "SET LOCAL synchronous_commit='on'",
    "SET LOCAL lock_timeout='1000ms'",
    "SET LOCAL statement_timeout='10000ms'",
)


class LiveLifecycleRepositoryError(ValueError):
    pass


class LiveLifecycleCommitUnknown(RuntimeError):
    pass


class LiveLifecycleReadbackRequired(RuntimeError):
    pass


def _need(condition):
    if condition is not True:
        raise LiveLifecycleRepositoryError(
            "native_live_lifecycle_repository_unavailable"
        ) from None


@dataclass(frozen=True, slots=True, repr=False)
class LiveLifecycleRolePinsV1:
    database_oid: int
    owner_oid: int
    reconciler_oid: int
    reconciler_name: str


@dataclass(frozen=True, slots=True, repr=False)
class LiveLifecycleJournalDataV1:
    reservation_id: str
    reservation_state: str
    resolution_sha256: str | None
    intent: LiveIntentDataV1
    intent_json: bytes
    intent_sha256: str
    proofs: tuple[bytes, ...]
    execution_authority: bool = False


def _intent(raw, digest, reservation_id, metadata):
    _need(type(raw) is str and raw.isascii() and type(digest) is str)
    encoded = raw.encode("ascii")
    document = decode(encoded, maximum=65536)
    _need(canonical(document) == encoded and sha(encoded) == digest)
    _need(
        type(document) is dict and set(LiveIntentDataV1.model_fields) <= set(document)
    )
    values = {name: document[name] for name in LiveIntentDataV1.model_fields}
    try:
        for name in (
            "entry",
            "stop_loss",
            "take_profit",
            "executable_price_bound",
            "contracts",
            "margin_amount",
            "notional_amount",
        ):
            _need(type(values[name]) is str)
            values[name] = Decimal(values[name])
        _need(
            type(values["risk_samples"]) is list
            and all(type(item) is str for item in values["risk_samples"])
        )
        values["risk_samples"] = tuple(Decimal(item) for item in values["risk_samples"])
        _need(type(values["deadline"]) is str)
        values["deadline"] = datetime.fromisoformat(values["deadline"])
        _need(values["deadline"].tzinfo is UTC)
        value = LiveIntentDataV1.model_validate(values, strict=True)
    except (ValueError, TypeError, InvalidOperation):
        raise LiveLifecycleRepositoryError(
            "native_live_lifecycle_intent_readback_refused"
        ) from None
    exact_id, exact_raw, exact_digest = exact_live_intent_document_v1(value)
    _need(
        (exact_id, exact_raw, exact_digest) == (reservation_id, encoded, digest)
        and value.account_uid == metadata["account_uid"]
        and value.main_uid == metadata["main_uid"]
    )
    return value, encoded


def _retained_proof(
    raw,
    signature,
    *,
    key_owner,
    metadata,
    intent,
    intent_sha256,
    reservation_id,
    sequence,
    previous_sha256,
):
    _need(type(raw) is str and raw.isascii())
    encoded = raw.encode("ascii")
    document = decode(encoded, maximum=65536)
    _need(type(document) is dict and canonical(document) == encoded)
    keys._verify_retained_lifecycle_signature_v1(key_owner, encoded, signature)
    bindings = {
        "schema": spec.SCHEMA,
        "environment": "live",
        "execution_authority": False,
        "reservation_id": reservation_id,
        "sequence": sequence,
        "previous_sha256": previous_sha256,
        "intent_sha256": intent_sha256,
        "event_key": intent.event_key,
        "candidate_sha256": intent.candidate_sha256,
        "report_sha256": intent.report_sha256,
        "policy_sha256": intent.policy_sha256,
        "original_credential_session_sha256": intent.credential_session_sha256,
        "request_sha256": decode(
            exact_live_intent_document_v1(intent)[1], maximum=65536
        )["request_sha256"],
        **{
            name: metadata[name]
            for name in (
                "account_uid",
                "main_uid",
                "database_oid",
                "owner_oid",
                "reconciler_oid",
                "origin",
                "key_id",
            )
        },
    }
    _need(
        all(
            type(document.get(name)) is type(expected) and document[name] == expected
            for name, expected in bindings.items()
        )
    )
    _need(
        document.get("outcome")
        in ("UNCERTAIN", "PROTECTED_HOLD", "ZERO_FILL_FLAT", "RECONCILED_FLAT")
    )
    observed = datetime.fromisoformat(document["observed_at"])
    _need(observed.tzinfo is UTC)
    return encoded, document, observed


def _factory(factory, pins):
    from app.database.repositories.public_runtime_bundle_custody import _closure

    _need(type(factory) is async_sessionmaker and type(pins) is LiveLifecycleRolePinsV1)
    _need(
        all(
            type(value) is int and 0 < value < 2**32
            for value in (pins.database_oid, pins.owner_oid, pins.reconciler_oid)
        )
        and pins.owner_oid != pins.reconciler_oid
    )
    _need(
        type(pins.reconciler_name) is str
        and re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", pins.reconciler_name) is not None
        and pins.reconciler_name != "public"
        and not pins.reconciler_name.startswith("pg_")
    )
    engine = factory.kw.get("bind")
    _need(
        type(engine) is AsyncEngine
        and type(engine.sync_engine.pool) is NullPool
        and engine.sync_engine.hide_parameters is True
        and engine.dialect.name == "postgresql"
        and engine.dialect.driver == "asyncpg"
        and engine.url.username == pins.reconciler_name
        and not engine.url.query
    )
    tls = _closure(engine.sync_engine.pool._creator).get("cparams", {}).get("ssl")
    _need(
        type(tls) is ssl.SSLContext
        and tls.check_hostname is True
        and tls.verify_mode == ssl.CERT_REQUIRED
        and tls.minimum_version >= ssl.TLSVersion.TLSv1_2
        and tls.keylog_filename is None
        and tls._msg_callback is None
        and tls.sni_callback is None
    )


def _plan_shape(value, *, root=True):
    if root and type(value) is str:
        value = decode(value.encode("utf8"), maximum=1048576)
    if type(value) is list:
        return [_plan_shape(item, root=False) for item in value]
    if type(value) is dict:
        return {
            key: _plan_shape(item, root=False)
            for key, item in value.items()
            if key not in {"Startup Cost", "Total Cost", "Plan Rows", "Plan Width"}
        }
    return value


async def _catalog(session, pins):
    functions = {}
    for statement in spec.UPGRADE_SQL:
        if statement.startswith("CREATE FUNCTION public."):
            name = statement.split("public.", 1)[1].split("(", 1)[0]
            functions[name] = sha(statement.split("$body$")[1].encode("utf8"))
    records = (
        await session.execute(
            text("""SELECT p.proname::text,p.proowner::bigint,
 encode(sha256(convert_to(p.prosrc,'UTF8')),'hex'),p.prosecdef,p.proconfig,p.prokind::text,
 p.proleakproof,p.proisstrict,p.provolatile::text,p.proparallel::text,p.prosupport::bigint,
 l.lanname::text,p.pronargs,p.proargtypes::text,p.proretset,p.prorettype::bigint,
 p.pronargdefaults,p.provariadic::bigint,p.proallargtypes::oid[],p.proargmodes::text[],p.proargnames,
 NOT EXISTS(SELECT 1 FROM pg_catalog.aclexplode(COALESCE(p.proacl,pg_catalog.acldefault('f',p.proowner))) a
 WHERE a.grantee=0 OR (a.grantee<>p.proowner AND a.is_grantable))
 FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
 JOIN pg_catalog.pg_language l ON l.oid=p.prolang
 WHERE n.nspname='public' AND p.proname=ANY(CAST(:names AS text[]))"""),
            {"names": list(functions)},
        )
    ).all()
    _need(len(records) == len(functions))
    return_types = {
        spec.FUNCTIONS[0]: 3802,
        spec.FUNCTIONS[2]: 20,
        spec.FUNCTIONS[3]: 25,
        spec.FUNCTIONS[4]: 2279,
        spec.FUNCTIONS[5]: 3802,
    }
    control_type = await session.scalar(
        text("SELECT 'public.native_live_controls_v1'::regtype::oid::bigint")
    )
    return_types[spec.FUNCTIONS[1]] = control_type
    seen = set()
    for row in records:
        name = row[0]
        _need(name not in seen)
        seen.add(name)
        nargs = (
            2
            if name == spec.FUNCTIONS[3]
            else 0
            if name in (spec.FUNCTIONS[4], spec.FUNCTIONS[5])
            else 1
        )
        _need(
            tuple(row[1:])
            == (
                pins.owner_oid,
                functions[name],
                name != spec.FUNCTIONS[4],
                ["search_path=pg_catalog, pg_temp"],
                "f",
                False,
                False,
                "v",
                "u",
                0,
                "plpgsql",
                nargs,
                " ".join(["25"] * nargs),
                name == spec.FUNCTIONS[1],
                return_types[name],
                0,
                0,
                None,
                None,
                ["p", "s"] if nargs == 2 else ["u"] if nargs == 1 else None,
                True,
            )
        )
    table_rows = (
        await session.execute(
            text("""SELECT c.relname::text,c.relowner::bigint,c.relkind::text,c.relpersistence::text,c.relrowsecurity,c.relforcerowsecurity,c.reloptions,
 NOT EXISTS(SELECT 1 FROM pg_catalog.pg_inherits i WHERE i.inhrelid=c.oid OR i.inhparent=c.oid),
 NOT EXISTS(SELECT 1 FROM pg_catalog.pg_rewrite r WHERE r.ev_class=c.oid)
 FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
 WHERE n.nspname='public' AND c.relname=ANY(CAST(:names AS text[]))"""),
            {"names": list(spec.TABLES)},
        )
    ).all()
    _need(
        len(table_rows) == len(spec.TABLES)
        and {r[0] for r in table_rows} == set(spec.TABLES)
        and all(
            tuple(r[1:]) == (pins.owner_oid, "r", "p", False, False, None, True, True)
            for r in table_rows
        )
    )
    _need(
        await session.scalar(
            text("""SELECT NOT EXISTS(
 SELECT 1 FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
 CROSS JOIN LATERAL pg_catalog.aclexplode(COALESCE(c.relacl,pg_catalog.acldefault('r',c.relowner))) a
 WHERE n.nspname='public' AND c.relname='native_live_lifecycle_keys_v1' AND a.grantee<>c.relowner)
 AND NOT EXISTS(SELECT 1 FROM pg_catalog.pg_attribute a JOIN pg_catalog.pg_class c ON c.oid=a.attrelid
 JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace,LATERAL pg_catalog.aclexplode(a.attacl) x
 WHERE n.nspname='public' AND c.relname='native_live_lifecycle_keys_v1' AND x.grantee<>c.relowner)""")
        )
        is True
    )
    await _structural_catalog(session)
    triggers = (
        await session.execute(
            text("""SELECT c.relname::text,t.tgname::text,t.tgenabled::text,t.tgtype,p.proname::text,t.tgnargs,t.tgattr::text,octet_length(t.tgargs),t.tgqual IS NULL,t.tgconstraint::bigint
 FROM pg_catalog.pg_trigger t JOIN pg_catalog.pg_class c ON c.oid=t.tgrelid
 JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace JOIN pg_catalog.pg_proc p ON p.oid=t.tgfoid
 WHERE n.nspname='public' AND c.relname=ANY(CAST(:names AS text[])) AND NOT t.tgisinternal"""),
            {"names": list(spec.TABLES)},
        )
    ).all()
    _need(
        {tuple(row) for row in triggers}
        == {
            (table, table + "_immutable", "O", 31, spec.FUNCTIONS[4], 0, "", 0, True, 0)
            for table in spec.TABLES
        }
    )
    # Definer metadata returns fixed-source/actual EXPLAIN plans, never key rows.
    # The function's exact body/owner/security was verified above before call.
    plans = await session.scalar(
        text("SELECT public.native_live_lifecycle_catalog_v1()")
    )
    expected = {
        (table, name) for table in spec.TABLES for name, _ in spec.TABLE_CHECKS[table]
    }
    _need(type(plans) is list and len(plans) == len(expected))
    observed = set()
    for item in plans:
        _need(
            type(item) is dict
            and set(item) == {"table", "name", "source_plan", "actual_plan"}
        )
        identity = item["table"], item["name"]
        _need(identity in expected and identity not in observed)
        observed.add(identity)
        _need(
            canonical(_plan_shape(item["source_plan"]))
            == canonical(_plan_shape(item["actual_plan"]))
        )
    _need(observed == expected)


async def _structural_catalog(session):
    columns = {
        spec.TABLES[0]: (
            ("environment", 1043, 8, True, None),
            ("account_uid", 1043, 44, True, None),
            ("main_uid", 1043, 44, True, None),
            ("database_oid", 26, -1, True, None),
            ("owner_oid", 26, -1, True, None),
            ("reconciler_oid", 26, -1, True, None),
            ("origin", 1043, 68, True, None),
            ("key_id", 1043, 68, True, None),
            ("key_material", 17, -1, True, None),
            ("key_material_sha256", 1043, 68, True, None),
            ("created_at", 1184, -1, True, "clock_timestamp()"),
        ),
        spec.TABLES[1]: (
            ("reservation_id", 1043, 68, True, None),
            ("sequence", 20, -1, True, None),
            ("environment", 1043, 8, True, None),
            ("account_uid", 1043, 44, True, None),
            ("key_id", 1043, 68, True, None),
            ("previous_sha256", 1043, 68, False, None),
            ("proof_json", 25, -1, True, None),
            ("proof_sha256", 1043, 68, True, None),
            ("signature", 1043, 68, True, None),
            ("outcome", 1043, 36, True, None),
            ("observed_at", 1184, -1, True, None),
            ("recorded_at", 1184, -1, True, "clock_timestamp()"),
        ),
    }
    rows = (
        await session.execute(
            text("""SELECT c.relname::text,a.attname::text,a.atttypid::bigint,a.atttypmod,a.attnotnull,pg_get_expr(d.adbin,d.adrelid),a.attgenerated::text,a.attidentity::text,a.attisdropped
 FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
 JOIN pg_catalog.pg_attribute a ON a.attrelid=c.oid AND a.attnum>0
 LEFT JOIN pg_catalog.pg_attrdef d ON d.adrelid=c.oid AND d.adnum=a.attnum
 WHERE n.nspname='public' AND c.relname=ANY(CAST(:names AS text[])) ORDER BY c.relname,a.attnum"""),
            {"names": list(spec.TABLES)},
        )
    ).all()
    _need(
        [tuple(row) for row in rows]
        == [
            (table, *col, "", "", False)
            for table in sorted(columns)
            for col in columns[table]
        ]
    )
    expected = {
        (table, "pk_" + table): ("p", fields, None, None)
        for table, fields in spec.SOURCE_PRIMARY_KEYS.items()
    }
    expected.update(
        {
            (table, name): ("u", fields, None, None)
            for table in spec.TABLES
            for name, fields in spec.TABLE_UNIQUES[table]
        }
    )
    expected.update(
        {
            key: ("f", fields, parent, parent_fields)
            for key, (fields, parent, parent_fields) in spec.SOURCE_FOREIGN_KEYS.items()
        }
    )
    expected.update(
        {
            (table, name): ("c", None, None, None)
            for table in spec.TABLES
            for name, _ in spec.TABLE_CHECKS[table]
        }
    )
    entries = (
        await session.execute(
            text("""SELECT c.relname::text,k.conname::text,k.contype::text,k.convalidated,k.condeferrable,k.condeferred,
 ARRAY(SELECT a.attname::text FROM unnest(k.conkey) WITH ORDINALITY v(n,i) JOIN pg_catalog.pg_attribute a ON a.attrelid=k.conrelid AND a.attnum=v.n ORDER BY v.i),
 pn.nspname::text,pc.relname::text,
 ARRAY(SELECT a.attname::text FROM unnest(k.confkey) WITH ORDINALITY v(n,i) JOIN pg_catalog.pg_attribute a ON a.attrelid=k.confrelid AND a.attnum=v.n ORDER BY v.i),
 k.confdeltype::text,k.confupdtype::text,k.confmatchtype::text
 FROM pg_catalog.pg_constraint k JOIN pg_catalog.pg_class c ON c.oid=k.conrelid JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
 LEFT JOIN pg_catalog.pg_class pc ON pc.oid=k.confrelid LEFT JOIN pg_catalog.pg_namespace pn ON pn.oid=pc.relnamespace
 WHERE n.nspname='public' AND c.relname=ANY(CAST(:names AS text[]))"""),
            {"names": list(spec.TABLES)},
        )
    ).all()
    _need(len(entries) == len(expected))
    seen = set()
    for row in entries:
        identity = row[0], row[1]
        _need(
            identity in expected
            and identity not in seen
            and tuple(row[3:6]) == (True, False, False)
        )
        seen.add(identity)
        kind, fields, parent, parent_fields = expected[identity]
        _need(row[2] == kind)
        if kind != "c":
            _need(tuple(row[6]) == fields)
        if kind == "f":
            _need(
                (row[7], row[8], tuple(row[9]), row[10], row[11], row[12])
                == ("public", parent, parent_fields, "r", "a", "s")
            )
    _need(seen == set(expected))
    expected_indexes = {
        (table, name): (kind == "p", fields)
        for (table, name), (kind, fields, _, _) in expected.items()
        if kind in ("p", "u")
    }
    indexes = (
        await session.execute(
            text("""SELECT t.relname::text,c.relname::text,i.indisunique,i.indisprimary,i.indisvalid,i.indisready,i.indislive,i.indimmediate,i.indpred IS NULL,i.indexprs IS NULL,pg_get_indexdef(i.indexrelid)
 FROM pg_catalog.pg_index i JOIN pg_catalog.pg_class t ON t.oid=i.indrelid JOIN pg_catalog.pg_class c ON c.oid=i.indexrelid
 JOIN pg_catalog.pg_namespace n ON n.oid=t.relnamespace WHERE n.nspname='public' AND t.relname=ANY(CAST(:names AS text[]))"""),
            {"names": list(spec.TABLES)},
        )
    ).all()
    _need(len(indexes) == len(expected_indexes))
    for row in indexes:
        primary, fields = expected_indexes.get((row[0], row[1]), (None, None))
        _need(
            primary is not None
            and tuple(row[2:10]) == (True, primary, True, True, True, True, True, True)
        )
        _need(
            row[10]
            == "CREATE UNIQUE INDEX "
            + row[1]
            + " ON public."
            + row[0]
            + " USING btree ("
            + ", ".join(fields)
            + ")"
        )


async def _profile(session, pins):
    row = tuple((await session.execute(base._PROFILE)).one())
    _need(
        row[:11]
        == (
            pins.reconciler_oid,
            pins.reconciler_name,
            True,
            "none",
            pins.database_oid,
            pins.owner_oid,
            ["0058"],
            True,
            True,
            True,
            True,
        )
    )
    access = {tuple(value) for value in (await session.execute(base._ACCESS)).all()}
    _need(access == {(table, "SELECT") for table in _READ_TABLES})
    inherited = await require_inherited_live_data_helpers_v1(
        session, owner_oid=pins.owner_oid
    )
    _need(
        (await session.execute(base._ROUTINES)).all()
        == [(name,) for name in sorted((*spec.EXECUTABLE_FUNCTIONS, *inherited))]
    )
    _need(
        await session.scalar(
            text("""SELECT NOT has_schema_privilege(current_user,'public','CREATE') AND NOT has_database_privilege(current_user,current_database(),'CREATE')
 AND NOT EXISTS(SELECT 1 FROM pg_catalog.pg_attribute a JOIN pg_catalog.pg_class c ON c.oid=a.attrelid
 JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace,LATERAL pg_catalog.aclexplode(a.attacl) x WHERE n.nspname='public' AND x.grantee IN (0,:actor))
 AND NOT EXISTS(SELECT 1 FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind='S'
 AND(has_sequence_privilege(current_user,c.oid,'USAGE') OR has_sequence_privilege(current_user,c.oid,'SELECT') OR has_sequence_privilege(current_user,c.oid,'UPDATE')))"""),
            {"actor": pins.reconciler_oid},
        )
        is True
    )
    await base._catalog(
        session, owner_oid=pins.owner_oid, actor_oid=pins.reconciler_oid
    )
    await _catalog(session, pins)
    return row


class NativeLiveLifecycleRepositoryV1:
    def __init__(self, factory, pins):
        _factory(factory, pins)
        self.factory, self.pins = factory, pins

    def _key(self, key_owner):
        value = keys._record(key_owner)
        _need(
            value["state"]["reconciler_factory"] is self.factory
            and value["state"]["reconciler_pins"] is self.pins
        )
        return keys.lifecycle_key_metadata_v1(key_owner)

    async def preflight(self, key_owner):
        expected = self._key(key_owner)
        async with self.factory() as session, session.begin():
            for sql in (
                "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY",
                *_SETTINGS[1:],
            ):
                await session.execute(text(sql))
            await _profile(session, self.pins)
            actual = await session.scalar(
                text("SELECT public.native_live_lifecycle_key_identity_v1(:uid)"),
                {"uid": expected["account_uid"]},
            )
            _need(type(actual) is dict and canonical(actual) == canonical(expected))
        _need(self._key(key_owner) == expected)
        return {
            "schema": "ctcc.native_live.lifecycle_preflight_data.v1",
            "database_head": "0058",
            "key_id": expected["key_id"],
            "execution_authority": False,
        }

    async def inhibit_for_recovery_v1(self, key_owner):
        expected = self._key(key_owner)
        async with self.factory() as session, session.begin():
            for sql in _SETTINGS:
                await session.execute(text(sql))
            await _profile(session, self.pins)
            await session.scalar(
                text("SELECT public.native_live_lifecycle_inhibit_v1(:uid)"),
                {"uid": expected["account_uid"]},
            )
        return await self.read_control_v1(key_owner)

    async def read_control_v1(self, key_owner):
        expected = self._key(key_owner)
        async with self.factory() as session, session.begin():
            for sql in (
                "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY",
                *_SETTINGS[1:],
            ):
                await session.execute(text(sql))
            await _profile(session, self.pins)
            row = (
                await session.execute(
                    text(
                        "SELECT armed,emergency_stop,arm_id,arm_expires_at,revision FROM public.native_live_controls_v1 WHERE environment='live' AND account_uid=:uid AND main_uid=:main"
                    ),
                    {"uid": expected["account_uid"], "main": expected["main_uid"]},
                )
            ).one()
            _need(
                type(row[0]) is type(row[1]) is bool
                and type(row[4]) is int
                and row[4] > 0
            )
            return {
                "armed": row[0],
                "emergency_stop": row[1],
                "disarmed": not row[0] and row[2] is row[3] is None,
                "revision": row[4],
            }

    async def observe_pending_v1(self, key_owner):
        expected = self._key(key_owner)
        async with self.factory() as session, session.begin():
            for sql in (
                "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY",
                *_SETTINGS[1:],
            ):
                await session.execute(text(sql))
            await _profile(session, self.pins)
            rows = (
                await session.execute(
                    text("""SELECT r.reservation_id,r.state,r.resolution_sha256,i.intent_json,i.intent_sha256
 FROM public.native_live_reservations_v1 r LEFT JOIN public.native_live_intents_v1 i USING(reservation_id)
 WHERE r.environment='live' AND r.account_uid=:uid AND r.state<>'reconciled'
 ORDER BY r.created_at,r.reservation_id LIMIT 101"""),
                    {"uid": expected["account_uid"]},
                )
            ).all()
            _need(len(rows) <= 100)
            result = []
            for row in rows:
                _need(
                    type(row[0]) is str
                    and re.fullmatch(r"[a-f0-9]{64}", row[0]) is not None
                    and row[1] in ("reserved", "uncertain")
                    and row[2] is None
                )
                _intent(row[3], row[4], row[0], expected)
                result.append(tuple(row))
            return tuple(result)

    async def read_journal_v1(self, key_owner, reservation_id):
        expected = self._key(key_owner)
        _need(
            type(reservation_id) is str
            and re.fullmatch(r"[a-f0-9]{64}", reservation_id) is not None
        )
        async with self.factory() as session, session.begin():
            for sql in (
                "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY",
                *_SETTINGS[1:],
            ):
                await session.execute(text(sql))
            await _profile(session, self.pins)
            row = (
                await session.execute(
                    text("""SELECT r.state,r.resolution_sha256,i.intent_json,i.intent_sha256
 FROM public.native_live_reservations_v1 r LEFT JOIN public.native_live_intents_v1 i USING(reservation_id)
 WHERE r.reservation_id=:id AND r.environment='live' AND r.account_uid=:uid"""),
                    {"id": reservation_id, "uid": expected["account_uid"]},
                )
            ).one()
            _need(row[0] in ("reserved", "uncertain", "reconciled"))
            intent, encoded = _intent(row[2], row[3], reservation_id, expected)
            records = (
                await session.execute(
                    text("""SELECT sequence,previous_sha256,proof_json,proof_sha256,signature,
 outcome,observed_at,recorded_at,environment,account_uid,key_id FROM public.native_live_lifecycle_proofs_v1
 WHERE reservation_id=:id ORDER BY sequence LIMIT 1001"""),
                    {"id": reservation_id},
                )
            ).all()
            _need(len(records) <= 1000)
            proofs, previous = [], None
            now = await session.scalar(text("SELECT clock_timestamp()"))
            for sequence, proof in enumerate(records, 1):
                _need(
                    type(proof[0]) is int
                    and proof[0] == sequence
                    and proof[1] == previous
                )
                raw, document, observed = _retained_proof(
                    proof[2],
                    proof[4],
                    key_owner=key_owner,
                    metadata=expected,
                    intent=intent,
                    intent_sha256=row[3],
                    reservation_id=reservation_id,
                    sequence=sequence,
                    previous_sha256=previous,
                )
                _need(
                    sha(raw) == proof[3]
                    and document["outcome"] == proof[5]
                    and observed == proof[6]
                    and observed <= proof[7] <= now
                    and tuple(proof[8:])
                    == ("live", expected["account_uid"], expected["key_id"])
                )
                proofs.append(raw)
                previous = proof[3]
            terminal = bool(proofs) and decode(proofs[-1], maximum=65536)[
                "outcome"
            ] in ("ZERO_FILL_FLAT", "RECONCILED_FLAT")
            _need((row[0] == "reconciled") is terminal)
            _need(row[1] == previous if terminal else row[1] is None)
        _need(self._key(key_owner) == expected)
        return LiveLifecycleJournalDataV1(
            reservation_id, row[0], row[1], intent, encoded, row[3], tuple(proofs)
        )

    async def append_owned_proof_v1(self, lifecycle_owner):
        """One original source-owned proof; an uncertain commit is never replayed."""
        from app.okx_live import owned_lifecycle_v1 as lifecycle

        raw, signature, key_owner = lifecycle._consume_owned_live_proof_v1(
            lifecycle_owner, key=_PROOF_CONSUME_KEY_V1
        )
        expected = self._key(key_owner)
        document = decode(raw, maximum=65536)
        _need(
            canonical(document) == raw
            and document["account_uid"] == expected["account_uid"]
        )
        keys._verify_retained_lifecycle_signature_v1(key_owner, raw, signature)
        session = self.factory()
        committing = False
        try:
            async with session.begin():
                for sql in _SETTINGS:
                    await session.execute(text(sql))
                await _profile(session, self.pins)
                result = await session.scalar(
                    text(
                        "SELECT public.native_live_lifecycle_append_v1(:proof,:signature)"
                    ),
                    {"proof": raw.decode("ascii"), "signature": signature},
                )
                _need(result == sha(raw))
                committing = True
        except BaseException as exc:
            if committing:
                raise LiveLifecycleCommitUnknown(
                    "native_live_lifecycle_commit_unknown_no_retry"
                ) from None
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise LiveLifecycleRepositoryError(
                "native_live_lifecycle_append_refused"
            ) from None
        finally:
            await session.close()
        try:
            readback = await self.read_journal_v1(key_owner, document["reservation_id"])
            _need(
                len(readback.proofs) == document["sequence"]
                and readback.proofs[-1] == raw
            )
            return readback
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 -- preserve committed rows and hide private DB diagnostics.
            raise LiveLifecycleReadbackRequired(
                "native_live_lifecycle_committed_readback_required_no_retry"
            ) from None
