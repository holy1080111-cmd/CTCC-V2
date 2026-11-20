"""Source-owned prospective Live financial prefix, never Arm/order authority.

The separate restricted collector writes once, commits once and independently
reads back. Unknown commit/readback never resets or deletes the epoch. The
unbound schema SPEC must first be installed by a reviewed owner migration.
"""

import asyncio
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime
from fractions import Fraction
from weakref import WeakKeyDictionary

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.repositories import native_live_ledger_v1 as ledger
from app.domain import native_live_financial_epoch_schema_v1 as spec
from app.domain.native_live_inherited_acl_v1 import (
    require_inherited_live_data_helpers_v1,
)
from app.domain.native_live_intent_v1 import live_account_lock_key_v1
from app.domain.native_live_legacy_safety_v1 import (
    require_legacy_live_negative_fence_v1,
)
from app.domain.source_primitives import canonical, decode, sha
from app.okx_live import financial_epoch_math_v1 as math
from app.okx_live import financial_prefix_auth_v1 as authentication
from app.okx_live import financial_rows_v1 as rows_source
from app.okx_live import trusted_source_bridge_v1 as source

_KEY = object()
_SEAL_KEY = object()
_OWNED = WeakKeyDictionary()
_SEAL_HELD = WeakKeyDictionary()
_SEAL_ACTIVE = ContextVar("ctcc_live_financial_native_seal_v1", default=None)
_CHECK_PLANS = set()


class LiveFinancialEpochError(ValueError):
    pass


class LiveFinancialCommitUnknown(RuntimeError):
    pass


class LiveFinancialReadbackRequired(RuntimeError):
    pass


def _need(condition):
    if condition is not True:
        raise LiveFinancialEpochError("native_live_financial_epoch_refused") from None


@dataclass(frozen=True, slots=True, repr=False)
class LiveFinancialCollectorRolePinsV1:
    database_oid: int
    owner_oid: int
    collector_oid: int
    collector_name: str


@dataclass(frozen=True, slots=True, repr=False)
class LiveRiskCashflowDataV1:
    at: datetime
    net: Fraction
    kind: str
    source_row_sha256: str


@dataclass(frozen=True, slots=True, repr=False)
class LiveFinancialWindowFactsV1:
    source_revision_sha256: str
    account_uid: str
    main_uid: str
    credential_session_sha256: str
    epoch_id: str
    sequence: int
    epoch_started_at: datetime
    observed_at: datetime
    available_at: datetime
    source_cutoff_at: datetime
    history_start: datetime
    history_end: datetime
    daily_net_pnl: Fraction
    weekly_net_pnl: Fraction
    loss_streak: int
    measured_peak: Fraction
    measured_current: Fraction
    peak_observed_at: datetime
    available_margin: Fraction
    controlled_outcomes: tuple
    cashflows: tuple[LiveRiskCashflowDataV1, ...]
    missing_obligations: tuple
    receipt_json: bytes

    @property
    def environment(self):
        return "live"

    @property
    def execution_authority(self):
        return False


class OwnedLiveFinancialEpochV1:
    __slots__ = ("__weakref__",)

    def __new__(cls, *, key):
        _need(cls is OwnedLiveFinancialEpochV1 and key is _KEY)
        return object.__new__(cls)

    def __copy__(self):
        _need(False)

    def __deepcopy__(self, memo):
        _need(False)

    def __reduce_ex__(self, protocol):
        _need(False)


def _factory(factory, pins):
    _need(type(pins) is LiveFinancialCollectorRolePinsV1)
    ledger._factory(
        factory,
        ledger.LiveLedgerRolePinsV1(
            pins.database_oid, pins.owner_oid, pins.collector_oid, pins.collector_name
        ),
    )


def _require_financial_seal_scope_v1(source_owner, *, key):
    _need(key is authentication._SOURCE_KEY)
    session = _SEAL_ACTIVE.get()
    record = _SEAL_HELD.get(session) if type(session) is AsyncSession else None
    _need(
        type(record) is dict
        and record["task"] is asyncio.current_task()
        and record["source"] is source_owner
        and session.in_transaction()
        and session.sync_session.get_transaction()
        is record["transaction"].sync_transaction
        and session.bind is record["factory"].kw["bind"]
        and not set(session.__dict__).intersection(
            {"execute", "scalar", "begin", "get_transaction", "connection"}
        )
    )
    _factory(record["factory"], record["pins"])
    return record


def _plan_shape(value):
    if type(value) is dict:
        return {
            name: _plan_shape(item)
            for name, item in value.items()
            if name not in {"Startup Cost", "Total Cost", "Plan Rows", "Plan Width"}
        }
    if type(value) is list:
        return [_plan_shape(item) for item in value]
    return value


async def _constraint_profile(session):
    """Compare backend-normalized CHECK predicates, without executing rows.

    EXPLAIN has no ANALYZE. Unknown executable routines have already been
    refused. Only catalog-deparsed expressions and fixed source expressions
    enter these statements; caller SQL and values are never interpolated.
    """
    entries = (
        await session.execute(
            text("""SELECT c.relname::text,k.conname::text,k.contype::text,
 k.convalidated,k.condeferrable,k.condeferred,pg_get_expr(k.conbin,k.conrelid)
 FROM pg_catalog.pg_constraint k JOIN pg_catalog.pg_class c ON c.oid=k.conrelid
 JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
 WHERE n.nspname='public' AND c.relname=ANY(CAST(:names AS text[]))"""),
            {"names": list(spec.TABLES)},
        )
    ).all()
    expected = {(table, f"pk_{table}"): "p" for table in spec.TABLES}
    for table in spec.TABLES:
        expected.update({(table, name): "c" for name, _ in spec.TABLE_CHECKS[table]})
        expected.update({(table, name): "u" for name, _ in spec.TABLE_UNIQUES[table]})
    expected[(spec.TABLES[1], "fk_native_live_financial_samples_v1_epoch")] = "f"
    _need(len(entries) == len(expected))
    seen = set()
    for (
        table,
        name,
        kind,
        validated,
        deferred,
        initially_deferred,
        expression,
    ) in entries:
        identity = table, name
        _need(
            identity not in seen
            and expected.get(identity) == kind
            and validated is True
            and deferred is False
            and initially_deferred is False
        )
        seen.add(identity)
        if kind != "c":
            continue
        source_expression = dict(spec.TABLE_CHECKS[table])[name]
        _need(type(expression) is str and len(expression) <= 8192)
        cache = table, name, source_expression, expression
        if cache in _CHECK_PLANS:
            continue
        _need(len(_CHECK_PLANS) < 256)
        expected_plan = await session.scalar(
            text(
                f"EXPLAIN (FORMAT JSON, VERBOSE, COSTS OFF) SELECT 1 FROM public.{table} WHERE ({source_expression}) IS NOT FALSE"
            )
        )
        actual_plan = await session.scalar(
            text(
                f"EXPLAIN (FORMAT JSON, VERBOSE, COSTS OFF) SELECT 1 FROM public.{table} WHERE ({expression}) IS NOT FALSE"
            )
        )
        _need(
            canonical(_plan_shape(expected_plan)) == canonical(_plan_shape(actual_plan))
        )
        _CHECK_PLANS.add(cache)
    _need(seen == set(expected))


async def _column_profile(session):
    expected = {
        spec.TABLES[0]: (
            ("epoch_id", 1043, 68, True),
            ("environment", 1043, 8, True),
            ("account_uid", 1043, 44, True),
            ("main_uid", 1043, 44, True),
            ("started_at", 1184, -1, True),
            ("policy_sha256", 1043, 68, True),
            ("epoch_json", 25, -1, True),
            ("epoch_sha256", 1043, 68, True),
        ),
        spec.TABLES[1]: (
            ("epoch_id", 1043, 68, True),
            ("sequence", 20, -1, True),
            ("previous_sha256", 1043, 68, False),
            ("observed_at", 1184, -1, True),
            ("source_revision_sha256", 1043, 68, True),
            ("equity", 1700, (60 << 16) + 20 + 4, True),
            ("sample_json", 25, -1, True),
            ("sample_sha256", 1043, 68, True),
        ),
    }
    observed = (
        await session.execute(
            text("""SELECT c.relname::text,a.attname::text,a.atttypid::bigint,a.atttypmod,
 a.attnotnull,a.attgenerated::text,a.attidentity::text,pg_get_expr(d.adbin,d.adrelid)
 FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
 JOIN pg_catalog.pg_attribute a ON a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped
 LEFT JOIN pg_catalog.pg_attrdef d ON d.adrelid=c.oid AND d.adnum=a.attnum
 WHERE n.nspname='public' AND c.relname=ANY(CAST(:names AS text[])) ORDER BY c.relname,a.attnum"""),
            {"names": list(spec.TABLES)},
        )
    ).all()
    _need(
        [tuple(item) for item in observed]
        == [
            (table, *column, "", "", None)
            for table in sorted(expected)
            for column in expected[table]
        ]
    )


async def _key_profile(session):
    expected = {
        (table, f"pk_{table}"): ("epoch_id",)
        if table == spec.TABLES[0]
        else ("epoch_id", "sequence")
        for table in spec.TABLES
    }
    for table in spec.TABLES:
        expected.update(
            {(table, name): columns for name, columns in spec.TABLE_UNIQUES[table]}
        )
    expected[(spec.TABLES[1], "fk_native_live_financial_samples_v1_epoch")] = (
        "epoch_id",
    )
    records = (
        await session.execute(
            text("""SELECT c.relname::text,k.conname::text,k.contype::text,
 ARRAY(SELECT a.attname::text FROM unnest(k.conkey) WITH ORDINALITY v(n,i)
 JOIN pg_catalog.pg_attribute a ON a.attrelid=k.conrelid AND a.attnum=v.n ORDER BY v.i),
 pn.nspname::text,pc.relname::text,k.confdeltype::text,k.confupdtype::text,k.confmatchtype::text
 FROM pg_catalog.pg_constraint k JOIN pg_catalog.pg_class c ON c.oid=k.conrelid
 JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
 LEFT JOIN pg_catalog.pg_class pc ON pc.oid=k.confrelid LEFT JOIN pg_catalog.pg_namespace pn ON pn.oid=pc.relnamespace
 WHERE n.nspname='public' AND c.relname=ANY(CAST(:names AS text[])) AND k.contype IN ('p','u','f')"""),
            {"names": list(spec.TABLES)},
        )
    ).all()
    _need(len(records) == len(expected))
    for (
        table,
        name,
        kind,
        columns,
        parent_schema,
        parent,
        delete,
        update,
        match,
    ) in records:
        _need(expected.get((table, name)) == tuple(columns))
        if kind == "f":
            _need(
                (parent_schema, parent, delete, update, match)
                == ("public", spec.TABLES[0], "r", "a", "s")
            )
    indexes = (
        await session.execute(
            text("""SELECT c.relname::text,ic.relname::text,i.indisunique,i.indisprimary,
 i.indisvalid,i.indisready,i.indislive,i.indpred IS NULL,i.indexprs IS NULL
 FROM pg_catalog.pg_index i JOIN pg_catalog.pg_class c ON c.oid=i.indrelid
 JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace JOIN pg_catalog.pg_class ic ON ic.oid=i.indexrelid
 WHERE n.nspname='public' AND c.relname=ANY(CAST(:names AS text[]))"""),
            {"names": list(spec.TABLES)},
        )
    ).all()
    _need(
        {tuple(item) for item in indexes}
        == {
            (table, name, True, name.startswith("pk_"), True, True, True, True, True)
            for table, name in expected
            if not name.startswith("fk_")
        }
    )


async def _profile(session, pins):
    row = tuple((await session.execute(ledger._PROFILE)).one())
    _need(
        row[:11]
        == (
            pins.collector_oid,
            pins.collector_name,
            True,
            "none",
            pins.database_oid,
            pins.owner_oid,
            [spec.HEAD],
            True,
            True,
            True,
            True,
        )
    )
    _need(
        {tuple(item) for item in (await session.execute(ledger._ACCESS)).all()}
        == {(table, "SELECT") for table in spec.READ_TABLES}
        | {(table, "INSERT") for table in spec.TABLES}
    )
    helpers = await require_inherited_live_data_helpers_v1(
        session, owner_oid=pins.owner_oid
    )
    _need(
        (await session.execute(ledger._ROUTINES)).all()
        == [(name,) for name in sorted(helpers)]
    )
    function = (
        await session.execute(
            text("""SELECT p.proowner::bigint,p.prosrc,p.prosecdef,p.proconfig,p.prokind::text,p.proleakproof
 FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
 WHERE n.nspname='public' AND p.proname='native_live_financial_guard_v1'""")
        )
    ).all()
    _need(
        len(function) == 1
        and tuple(function[0])
        == (
            pins.owner_oid,
            spec.GUARD_SQL.split("$body$")[1],
            False,
            ["search_path=pg_catalog, pg_temp"],
            "f",
            False,
        )
    )
    tables = (
        await session.execute(
            text("""SELECT c.relname::text,c.relowner::bigint,c.relkind::text,c.relrowsecurity,c.relforcerowsecurity
 FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
 WHERE n.nspname='public' AND c.relname=ANY(CAST(:names AS text[]))"""),
            {"names": list(spec.TABLES)},
        )
    ).all()
    _need(
        {tuple(item) for item in tables}
        == {(table, pins.owner_oid, "r", False, False) for table in spec.TABLES}
    )
    triggers = (
        await session.execute(
            text("""SELECT c.relname::text,t.tgname::text,t.tgenabled::text,t.tgtype,p.proname::text
 FROM pg_catalog.pg_trigger t JOIN pg_catalog.pg_class c ON c.oid=t.tgrelid
 JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace JOIN pg_catalog.pg_proc p ON p.oid=t.tgfoid
 WHERE n.nspname='public' AND c.relname=ANY(CAST(:names AS text[])) AND NOT t.tgisinternal"""),
            {"names": list(spec.TABLES)},
        )
    ).all()
    _need(
        {tuple(item) for item in triggers}
        == {
            (table, table + "_immutable", "O", 31, "native_live_financial_guard_v1")
            for table in spec.TABLES
        }
    )
    _need(
        await session.scalar(
            text("""SELECT NOT EXISTS(SELECT 1 FROM pg_catalog.pg_attribute a
 JOIN pg_catalog.pg_class c ON c.oid=a.attrelid JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace,
 LATERAL pg_catalog.aclexplode(a.attacl) x WHERE n.nspname='public' AND x.grantee IN (0,:actor))
 AND NOT EXISTS(SELECT 1 FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
 WHERE n.nspname='public' AND c.relkind='S' AND (has_sequence_privilege(current_user,c.oid,'USAGE') OR has_sequence_privilege(current_user,c.oid,'SELECT') OR has_sequence_privilege(current_user,c.oid,'UPDATE')))"""),
            {"actor": pins.collector_oid},
        )
        is True
    )
    _need(
        await session.scalar(
            text("SELECT NOT has_schema_privilege(current_user,'public','CREATE')")
        )
        is True
    )
    _need(
        await session.scalar(
            text("""SELECT NOT EXISTS(SELECT 1 FROM pg_catalog.pg_class c
 JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace,LATERAL pg_catalog.aclexplode(c.relacl) a
 WHERE n.nspname='public' AND a.grantee IN (0,:actor) AND a.is_grantable)
 AND NOT EXISTS(SELECT 1 FROM pg_catalog.pg_namespace n,LATERAL pg_catalog.aclexplode(n.nspacl) a
 WHERE n.nspname='public' AND a.grantee IN (0,:actor) AND a.is_grantable)"""),
            {"actor": pins.collector_oid},
        )
        is True
    )
    await _constraint_profile(session)
    await _column_profile(session)
    await _key_profile(session)
    await require_legacy_live_negative_fence_v1(session)


async def preflight_live_financial_collector_v1(*, factory, role_pins):
    """Read-only metadata preflight before the short-lived Live source capture."""
    _factory(factory, role_pins)
    async with asyncio.timeout(30), factory() as session, session.begin():
        for statement in ledger._SETTINGS:
            await session.execute(text(statement))
        await _profile(session, role_pins)
    return canonical(
        {
            "schema": "ctcc.native_live.financial_collector_preflight_data.v1",
            "head": spec.HEAD,
            "spec_sha256": spec.live_financial_epoch_spec_sha256_v1(),
            "execution_authority": False,
            "admission": "DENY",
        }
    )


async def _prefix(session, epoch_id):
    epoch = (
        await session.execute(
            text(
                "SELECT epoch_json,epoch_sha256 FROM public.native_live_financial_epochs_v1 WHERE epoch_id=:epoch"
            ),
            {"epoch": epoch_id},
        )
    ).one_or_none()
    if epoch is None:
        return None, ()
    raw = epoch[0].encode("ascii")
    _need(sha(raw) == epoch[1])
    count, nbytes = (
        await session.execute(
            text(
                "SELECT count(*),COALESCE(sum(octet_length(sample_json)),0) FROM public.native_live_financial_samples_v1 WHERE epoch_id=:epoch"
            ),
            {"epoch": epoch_id},
        )
    ).one()
    _need(type(count) is int and 0 <= count <= 1024 and 0 <= nbytes <= 64 * 1024 * 1024)
    samples = (
        await session.execute(
            text(
                "SELECT sequence,sample_json,sample_sha256 FROM public.native_live_financial_samples_v1 WHERE epoch_id=:epoch ORDER BY sequence LIMIT 1025"
            ),
            {"epoch": epoch_id},
        )
    ).all()
    _need(
        len(samples) == count
        and sum(len(item[1]) for item in samples) <= 64 * 1024 * 1024
    )
    values = []
    for index, item in enumerate(samples, 1):
        data = item[1].encode("ascii")
        _need(item[0] == index and sha(data) == item[2])
        values.append(data)
    return raw, tuple(values)


async def observe_owned_live_financial_epoch_v1(source_owner, *, factory, role_pins):
    """No seed, HWM, complete flag, source callback or outcome DTO is accepted."""
    _factory(factory, role_pins)
    rows = rows_source.read_owned_live_financial_rows_data_v1(source_owner)
    prospective = math.epoch_document(rows)
    identity = decode(prospective, maximum=65536)["epoch_id"]
    committed = False
    commit_started = False
    try:
        async with asyncio.timeout(15), factory() as session:
            transaction = await session.begin()
            seal_token = None
            try:
                for statement in ledger._SETTINGS:
                    await session.execute(text(statement))
                await session.execute(
                    text("SELECT pg_advisory_xact_lock(:key)"),
                    {"key": live_account_lock_key_v1(rows.account_uid)},
                )
                await _profile(session, role_pins)
                epoch, prefix = await _prefix(session, identity)
                scope = {
                    "task": asyncio.current_task(),
                    "source": source_owner,
                    "factory": factory,
                    "pins": role_pins,
                    "session": session,
                    "transaction": transaction,
                    "rows": rows,
                    "epoch": epoch,
                    "prefix": prefix,
                }
                _SEAL_HELD[session] = scope
                seal_token = _SEAL_ACTIVE.set(session)
                if epoch is None:
                    epoch = authentication.issue_original_genesis_v1(
                        source_owner, key=_SEAL_KEY
                    )
                    scope["epoch"] = epoch
                    await session.execute(
                        text("""INSERT INTO public.native_live_financial_epochs_v1(epoch_id,environment,account_uid,main_uid,started_at,policy_sha256,epoch_json,epoch_sha256)
 VALUES(:epoch,'live',:uid,:main,:started,:policy,:raw,:sha)"""),
                        {
                            "epoch": identity,
                            "uid": rows.account_uid,
                            "main": rows.main_uid,
                            "started": rows.observed_at,
                            "policy": spec.POLICY_SHA256,
                            "raw": epoch.decode("ascii"),
                            "sha": sha(epoch),
                        },
                    )
                sample = authentication.issue_original_sample_v1(
                    source_owner, key=_SEAL_KEY
                )
                data = decode(sample, maximum=2097152)
                # Replay original Live lease/config and raw source after the DB awaits.
                actual = rows_source.read_owned_live_financial_rows_data_v1(
                    source_owner
                )
                _need(
                    actual.receipt_json == rows.receipt_json
                    and actual.source_revision_sha256 == rows.source_revision_sha256
                )
                await session.execute(
                    text("""INSERT INTO public.native_live_financial_samples_v1(epoch_id,sequence,previous_sha256,observed_at,source_revision_sha256,equity,sample_json,sample_sha256)
 VALUES(:epoch,:sequence,:previous,:observed,:source,:equity,:raw,:sha)"""),
                    {
                        "epoch": identity,
                        "sequence": data["sequence"],
                        "previous": data["previous_sha256"],
                        "observed": rows.observed_at,
                        "source": rows.source_revision_sha256,
                        "equity": rows.equity,
                        "raw": sample.decode("ascii"),
                        "sha": sha(sample),
                    },
                )
                await _profile(session, role_pins)
                source._replay_owned_live_source_v1(
                    source_owner, key=source._SOURCE_KEY
                )
                commit_started = True
                await transaction.commit()
                committed = True
            except BaseException:
                if transaction.is_active:
                    await transaction.rollback()
                raise
            finally:
                if seal_token is not None:
                    _SEAL_ACTIVE.reset(seal_token)
                _SEAL_HELD.pop(session, None)
        async with factory() as readback, readback.begin():
            for statement in ledger._SETTINGS:
                await readback.execute(text(statement))
            await readback.execute(
                text("SELECT pg_advisory_xact_lock(:key)"),
                {"key": live_account_lock_key_v1(rows.account_uid)},
            )
            await _profile(readback, role_pins)
            actual_epoch, actual_prefix = await _prefix(readback, identity)
            _need(actual_epoch == epoch and actual_prefix == (*prefix, sample))
        source._replay_owned_live_source_v1(source_owner, key=source._SOURCE_KEY)
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 -- fixed uncertainty codes; no source or DSN details escape.
        if committed:
            raise LiveFinancialReadbackRequired(
                "live_financial_committed_readback_unconfirmed_do_not_reset"
            ) from None
        if commit_started:
            raise LiveFinancialCommitUnknown(
                "live_financial_commit_unknown_do_not_reset"
            ) from None
        raise LiveFinancialEpochError("native_live_financial_epoch_refused") from None
    owner = OwnedLiveFinancialEpochV1(key=_KEY)
    _OWNED[owner] = {
        "task": asyncio.current_task(),
        "source": source_owner,
        "factory": factory,
        "pins": role_pins,
        "epoch_json": epoch,
        "sample_json": sample,
    }
    _replay_owned_live_financial_epoch_v1(owner, key=_KEY)
    return owner


def _facts_from_original_sample_v1(rows, sample):
    document = decode(sample, maximum=2097152)
    _need(
        canonical(document) == sample
        and document["source_revision_sha256"] == rows.source_revision_sha256
        and document["financial_rows_sha256"] == sha(rows.receipt_json)
        and document["equity"] == format(rows.equity, "f")
        and document["available_margin"] == format(rows.available_margin, "f")
    )
    return LiveFinancialWindowFactsV1(
        rows.source_revision_sha256,
        rows.account_uid,
        rows.main_uid,
        rows.credential_session_sha256,
        document["epoch_id"],
        document["sequence"],
        datetime.fromisoformat(document["peak_window_started_at"]),
        rows.observed_at,
        rows.observed_at,
        rows.cutoff,
        rows.history_start,
        rows.observed_at,
        math.read_ratio(document["daily_net_pnl"]),
        math.read_ratio(document["weekly_net_pnl"]),
        document["loss_streak"],
        math.read_ratio(document["measured_peak"]),
        Fraction(rows.equity),
        datetime.fromisoformat(document["peak_observed_at"]),
        Fraction(rows.available_margin),
        tuple(canonical(item) for item in document["controlled_outcomes"]),
        tuple(
            LiveRiskCashflowDataV1(
                datetime.fromisoformat(item["at"]),
                math.read_ratio(item["net"]),
                item["kind"],
                item["source_row_sha256"],
            )
            for item in document["account_cashflows"]
        ),
        (),
        sample,
    )


def _replay_owned_live_financial_epoch_v1(owner, *, key):
    _need(key is _KEY and type(owner) is OwnedLiveFinancialEpochV1)
    record = _OWNED.get(owner)
    _need(type(record) is dict and record["task"] is asyncio.current_task())
    rows = rows_source.read_owned_live_financial_rows_data_v1(record["source"])
    facts = _facts_from_original_sample_v1(rows, record["sample_json"])
    view = source._replay_owned_live_source_v1(record["source"], key=source._SOURCE_KEY)
    _need(
        (
            view["uid"],
            view["main_uid"],
            view["credential_session_sha256"],
            view["source_revision_sha256"],
        )
        == (
            facts.account_uid,
            facts.main_uid,
            facts.credential_session_sha256,
            facts.source_revision_sha256,
        )
        and record["sample_json"] == facts.receipt_json
    )
    return facts
