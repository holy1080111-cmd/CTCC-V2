"""Live-only durable DATA holds/intents; no Arm writer or dispatch permit.

Only a live, original same-task source can enter this repository. Candidate
inputs remain unqualified DATA until the future source-owned Live coordinator
replays G1–G12, economics and risk. Neither this receipt nor its saved readback
can submit, arm, release a tombstone or turn a legacy Live denial into a permit.
"""

from __future__ import annotations

import asyncio
import ssl
from dataclasses import dataclass
from datetime import UTC, datetime
from fractions import Fraction

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from sqlalchemy.pool import NullPool

from app.database.repositories import native_live_catalog_v1 as structural_catalog
from app.domain import native_live_ledger_schema_v1 as schema
from app.domain.native_live_inherited_acl_v1 import (
    require_inherited_live_data_helpers_v1,
)
from app.domain.native_live_intent_v1 import (
    checked_live_intent_data_v1,
    exact_live_intent_document_v1,
    live_account_lock_key_v1,
)
from app.domain.native_live_legacy_safety_v1 import (
    require_legacy_live_negative_fence_v1,
)
from app.domain.source_primitives import canonical, sha
from app.okx_live import trusted_source_bridge_v1 as source
from app.trade_qualification.hard_risk_caps import (
    MAX_PORTFOLIO_MARGIN_PCT,
    MAX_PORTFOLIO_STOP_RISK_PCT,
    MAX_SINGLE_POSITION_MARGIN_USDT,
    MAX_TRADE_RISK_PCT,
)


class LiveLedgerDataError(ValueError):
    pass


class LiveLedgerCommitUnknown(RuntimeError):
    pass


class LiveLedgerReadbackRequired(RuntimeError):
    pass


def _need(condition):
    if condition is not True:
        raise LiveLedgerDataError("native_live_ledger_data_refused") from None


@dataclass(frozen=True, slots=True, repr=False)
class LiveLedgerRolePinsV1:
    database_oid: int
    owner_oid: int
    writer_oid: int
    writer_name: str


def _factory(factory, pins):
    from app.database.repositories.public_runtime_bundle_custody import _closure

    _need(
        type(factory) is async_sessionmaker
        and type(pins) is LiveLedgerRolePinsV1
        and all(
            type(oid) is int and 0 < oid < 2**32
            for oid in (pins.database_oid, pins.owner_oid, pins.writer_oid)
        )
        and pins.owner_oid != pins.writer_oid
    )
    engine = factory.kw.get("bind")
    _need(
        type(engine) is AsyncEngine
        and type(engine.sync_engine.pool) is NullPool
        and engine.sync_engine.hide_parameters is True
        and engine.dialect.name == "postgresql"
        and engine.dialect.driver == "asyncpg"
        and engine.url.username == pins.writer_name
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


_PROFILE = text("""
 SELECT r.oid::bigint,r.rolname::text,current_user=session_user,current_setting('role'),
 d.oid::bigint,d.datdba::bigint,
 (SELECT array_agg(version_num::text) FROM public.alembic_version),
 EXISTS(SELECT 1 FROM pg_catalog.pg_stat_ssl WHERE pid=pg_backend_pid() AND ssl),
 r.rolcanlogin,NOT(r.rolsuper OR r.rolcreatedb OR r.rolcreaterole OR r.rolreplication OR r.rolbypassrls),
 NOT EXISTS(SELECT 1 FROM pg_catalog.pg_auth_members WHERE member=r.oid OR roleid=r.oid),
 pg_backend_pid(),pg_current_xact_id_if_assigned()::text
 FROM pg_catalog.pg_roles r CROSS JOIN pg_catalog.pg_database d
 WHERE r.rolname=current_user AND d.datname=current_database()
""")
_ACCESS = text("""
 SELECT c.relname::text,p.privilege_name FROM pg_catalog.pg_class c
 JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
 CROSS JOIN (VALUES ('SELECT'),('INSERT'),('UPDATE'),('DELETE'),('TRUNCATE'),('REFERENCES'),('TRIGGER'),('MAINTAIN')) p(privilege_name)
 WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m','f')
 AND pg_catalog.has_table_privilege(current_user,c.oid,p.privilege_name)
 ORDER BY c.relname,p.privilege_name
""")
_ROUTINES = text("""
 SELECT p.proname::text FROM pg_catalog.pg_proc p
 JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
 WHERE n.nspname='public' AND pg_catalog.has_function_privilege(current_user,p.oid,'EXECUTE')
 AND (p.prorettype<>'pg_catalog.trigger'::regtype OR p.prosecdef)
 ORDER BY p.proname
""")
_SETTINGS = (
    "SET TRANSACTION ISOLATION LEVEL READ COMMITTED READ WRITE",
    "SET LOCAL search_path TO pg_catalog, public, pg_temp",
    "SET LOCAL TIME ZONE 'UTC'",
    "SET LOCAL synchronous_commit='on'",
    "SET LOCAL lock_timeout='1000ms'",
    "SET LOCAL statement_timeout='10000ms'",
)


async def _profile(session, pins):
    row = tuple((await session.execute(_PROFILE)).one())
    _need(
        row[:11]
        == (
            pins.writer_oid,
            pins.writer_name,
            True,
            "none",
            pins.database_oid,
            pins.owner_oid,
            [schema.HEAD],
            True,
            True,
            True,
            True,
        )
    )
    observed = {tuple(row) for row in (await session.execute(_ACCESS)).all()}
    expected = {
        (table, "SELECT")
        for table in (
            *schema.TABLES,
            *schema.METADATA_READ_TABLES,
            *schema.LEGACY_READ_TABLES,
        )
    } | {(table, "INSERT") for table in schema.TABLES[1:]}
    inherited_helpers = await require_inherited_live_data_helpers_v1(
        session, owner_oid=pins.owner_oid
    )
    _need(
        observed == expected
        and (await session.execute(_ROUTINES)).all()
        == [
            (name,)
            for name in sorted((*schema.EXECUTABLE_FUNCTIONS, *inherited_helpers))
        ]
    )
    await _catalog(session, owner_oid=pins.owner_oid, actor_oid=pins.writer_oid)
    return row


async def _catalog(session, *, owner_oid, actor_oid):
    """Same source-pinned tables/functions for separate restricted Live roles."""
    _need(
        await session.scalar(
            text("""SELECT
      has_schema_privilege(current_user,'public','USAGE')
      AND NOT has_schema_privilege(current_user,'public','CREATE')
      AND NOT has_database_privilege(current_user,current_database(),'CREATE')
      AND NOT EXISTS(SELECT 1 FROM pg_catalog.pg_class c
        JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace,
        LATERAL pg_catalog.aclexplode(c.relacl) x
        WHERE n.nspname='public' AND x.grantee IN (0,:actor) AND x.is_grantable)
      AND NOT EXISTS(SELECT 1 FROM pg_catalog.pg_proc p
        JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace,
        LATERAL pg_catalog.aclexplode(p.proacl) x
        WHERE n.nspname='public' AND x.grantee IN (0,:actor) AND x.is_grantable)"""),
            {"actor": actor_oid},
        )
        is True
    )
    expected_functions = {}
    for statement in schema.UPGRADE_SQL:
        if statement.startswith("CREATE FUNCTION public."):
            name = statement.split("public.", 1)[1].split("(", 1)[0]
            expected_functions[name] = (
                sha(statement.split("$body$")[1].encode("utf8")),
                name in schema.EXECUTABLE_FUNCTIONS,
            )
    functions = (
        await session.execute(
            text("""SELECT p.proname::text,p.proowner::bigint,
      encode(sha256(convert_to(p.prosrc,'UTF8')),'hex'),p.prosecdef,p.proconfig,p.prokind::text,p.proleakproof
      FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
      WHERE n.nspname='public' AND p.proname=ANY(CAST(:names AS text[]))"""),
            {"names": list(expected_functions)},
        )
    ).all()
    _need(len(functions) == len(expected_functions))
    for item in functions:
        expected = (
            owner_oid,
            *expected_functions[item[0]],
            ["search_path=pg_catalog, pg_temp"],
            "f",
            False,
        )
        for actual, required, field in zip(
            item[1:],
            expected,
            ("owner", "source", "security_mode", "search_path", "kind", "leakproof"),
            strict=True,
        ):
            if actual != required:
                # Fixed catalog-field codes keep operational errors useful
                # without logging private rows, IDs, SQL text or source bytes.
                raise LiveLedgerDataError("native_live_function_" + field + "_changed")
    tables = (
        await session.execute(
            text("""SELECT c.relname::text,c.relowner::bigint,c.relkind::text,c.relrowsecurity,c.relforcerowsecurity
      FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
      WHERE n.nspname='public' AND c.relname=ANY(CAST(:names AS text[]))"""),
            {"names": list(schema.TABLES)},
        )
    ).all()
    _need(
        len(tables) == len(schema.TABLES)
        and all(tuple(item[1:]) == (owner_oid, "r", False, False) for item in tables)
    )
    triggers = (
        await session.execute(
            text("""SELECT c.relname::text,t.tgname::text,t.tgenabled::text,t.tgtype,p.proname::text
      FROM pg_catalog.pg_trigger t JOIN pg_catalog.pg_class c ON c.oid=t.tgrelid
      JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace JOIN pg_catalog.pg_proc p ON p.oid=t.tgfoid
      WHERE n.nspname='public' AND c.relname=ANY(CAST(:names AS text[])) AND NOT t.tgisinternal"""),
            {"names": list(schema.TABLES)},
        )
    ).all()
    _need(
        {tuple(item) for item in triggers}
        == {
            (
                table,
                table + "_immutable",
                "O",
                31,
                "native_live_reservation_guard_v1"
                if table == schema.TABLES[1]
                else "native_live_data_guard_v1",
            )
            for table in schema.TABLES[1:]
        }
    )
    # A role cannot reach around the table contract through a column grant or sequence.
    _need(
        await session.scalar(
            text("""SELECT NOT EXISTS(
      SELECT 1 FROM pg_catalog.pg_attribute a JOIN pg_catalog.pg_class c ON c.oid=a.attrelid
      JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace,
      LATERAL pg_catalog.aclexplode(a.attacl) x
      WHERE n.nspname='public' AND x.grantee IN (0,:writer))
      AND NOT EXISTS(SELECT 1 FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
      WHERE n.nspname='public' AND c.relkind='S' AND
      (has_sequence_privilege(current_user,c.oid,'USAGE') OR has_sequence_privilege(current_user,c.oid,'SELECT') OR has_sequence_privilege(current_user,c.oid,'UPDATE')))"""),
            {"writer": actor_oid},
        )
        is True
    )
    await structural_catalog.require_live4_source_catalog_v1(session)


def _source(value, owner):
    view = source._replay_owned_live_source_v1(owner, key=source._SOURCE_KEY)
    _need(
        value.account_uid == view["uid"]
        and value.main_uid == view["main_uid"]
        and value.credential_session_sha256 == view["credential_session_sha256"]
        and value.source_revision_sha256 == view["source_revision_sha256"]
    )
    current = view["current_readback"]
    _need(
        current.receipt["no_open_positions_or_pending_orders_observed"] is True
        and current.usdt_available_equity is not None
        and current.usdt_available_equity.available_equity.is_finite()
        and current.usdt_available_equity.available_equity > 0
        and source._utc_ns(value.deadline)
        <= view["current_completed"].utc_ns + view["plan"].max_current_age_ns
    )
    return view


def _hard_caps(row, view):
    """Apply shared structural ceilings to the known available-equity field.

    This conservative DATA ceiling does not replace complete portfolio/loss
    qualification, which remains explicitly false in this repository.
    """
    available = Fraction(
        view["current_readback"].usdt_available_equity.available_equity
    )
    _need(
        Fraction(row["per_trade_risk_cap"]) <= available * MAX_TRADE_RISK_PCT
        and Fraction(row["portfolio_risk_cap"])
        <= available * MAX_PORTFOLIO_STOP_RISK_PCT
        and Fraction(row["margin_cap"]) <= available * MAX_PORTFOLIO_MARGIN_PCT
        and Fraction(row["margin_cap"]) <= MAX_SINGLE_POSITION_MARGIN_USDT
    )


def _control(row, value, now):
    _need(
        type(row) is dict
        and type(now) is datetime
        and now.tzinfo is UTC
        and (row["environment"], row["account_uid"], row["main_uid"])
        == ("live", value.account_uid, value.main_uid)
        and row["credential_session_sha256"] == value.credential_session_sha256
        and row["source_revision_sha256"] == value.source_revision_sha256
        and row["policy_sha256"] == value.policy_sha256
        and row["process_sha256"] == value.process_sha256
        and row["revision"] == value.expected_control_revision
        and row["armed"] is True
        and row["emergency_stop"] is False
        and row["arm_id"] == value.arm_id
        and row["updated_at"]
        <= now
        < value.deadline
        <= row["arm_expires_at"]
        <= row["lease_until"]
    )
    for name in (
        "per_trade_risk_cap",
        "portfolio_risk_cap",
        "margin_cap",
        "micro_notional_cap",
    ):
        _need(row[name] is not None and row[name].is_finite() and row[name] > 0)


def _caps(row, value, active):
    _need(
        type(active) is tuple
        and len(active) == 3
        and all(
            amount is not None and amount.is_finite() and amount >= 0
            for amount in active
        )
    )
    worst = max(value.risk_samples)
    _need(
        worst <= row["per_trade_risk_cap"]
        and Fraction(active[0]) + Fraction(worst) <= Fraction(row["portfolio_risk_cap"])
        and Fraction(active[1]) + Fraction(value.margin_amount)
        <= Fraction(row["margin_cap"])
        and Fraction(active[2]) + Fraction(value.notional_amount)
        <= Fraction(row["micro_notional_cap"])
    )
    return worst


class NativeLiveLedgerDataRepositoryV1:
    def __init__(self, factory, pins):
        _factory(factory, pins)
        self.factory, self.pins = factory, pins

    async def preflight_data_v1(self):
        """Read-only actor/catalog proof; no source, Arm or intent is created."""
        async with self.factory() as session, session.begin():
            for statement in (
                "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY",
                *_SETTINGS[1:],
            ):
                await session.execute(text(statement))
            await _profile(session, self.pins)

    async def reserve_and_commit_intent_data_v1(self, value, *, owned_source):
        """One durable DATA hold and intent; duplicate events never replace it."""
        attempted = committed = False
        try:
            value = checked_live_intent_data_v1(value)
            reservation_id, raw, digest = exact_live_intent_document_v1(value)
            _source(value, owned_source)
            async with asyncio.timeout(30), self.factory() as session:
                transaction = await session.begin()
                try:
                    for sql in _SETTINGS:
                        await session.execute(text(sql))
                    first = await _profile(session, self.pins)
                    await session.execute(
                        text("SELECT pg_advisory_xact_lock(:key)"),
                        {"key": live_account_lock_key_v1(value.account_uid)},
                    )
                    await require_legacy_live_negative_fence_v1(session)
                    rows = (
                        (
                            await session.execute(
                                text(
                                    "SELECT * FROM public.native_live_control_fence_v1('live',:uid)"
                                ),
                                {"uid": value.account_uid},
                            )
                        )
                        .mappings()
                        .all()
                    )
                    _need(len(rows) == 1)
                    row = dict(rows[0])
                    now = await session.scalar(text("SELECT clock_timestamp()"))
                    _control(row, value, now)
                    current_source = _source(value, owned_source)
                    _hard_caps(row, current_source)
                    active = tuple(
                        (
                            await session.execute(
                                text(
                                    "SELECT COALESCE(sum(risk_amount),0),COALESCE(sum(margin_amount),0),COALESCE(sum(notional_amount),0) FROM public.native_live_reservations_v1 WHERE environment='live' AND account_uid=:uid AND state<>'reconciled'"
                                ),
                                {"uid": value.account_uid},
                            )
                        ).one()
                    )
                    worst = _caps(row, value, active)
                    await session.execute(
                        text("""INSERT INTO public.native_live_reservations_v1
                     (reservation_id,environment,account_uid,event_key,candidate_sha256,report_sha256,source_revision_sha256,control_revision,arm_id,risk_amount,margin_amount,notional_amount,deadline,state)
                     VALUES(:id,'live',:uid,:event,:candidate,:report,:source,:revision,:arm,:risk,:margin,:notional,:deadline,'reserved')"""),
                        {
                            "id": reservation_id,
                            "uid": value.account_uid,
                            "event": value.event_key,
                            "candidate": value.candidate_sha256,
                            "report": value.report_sha256,
                            "source": value.source_revision_sha256,
                            "revision": value.expected_control_revision,
                            "arm": value.arm_id,
                            "risk": worst,
                            "margin": value.margin_amount,
                            "notional": value.notional_amount,
                            "deadline": value.deadline,
                        },
                    )
                    await session.execute(
                        text("""INSERT INTO public.native_live_intents_v1
                     (reservation_id,environment,account_uid,client_order_id,protection_client_id,intent_json,intent_sha256)
                     VALUES(:id,'live',:uid,:client,:protection,:raw,:sha)"""),
                        {
                            "id": reservation_id,
                            "uid": value.account_uid,
                            "client": value.client_order_id,
                            "protection": value.protection_client_id,
                            "raw": raw.decode("ascii"),
                            "sha": digest,
                        },
                    )
                    journal = canonical(
                        {
                            "schema": schema.SCHEMA,
                            "reservation_id": reservation_id,
                            "intent_sha256": digest,
                            "kind": "intent_committed",
                            "execution_authority": False,
                            "admission": "DENY",
                        }
                    )
                    await session.execute(
                        text("""INSERT INTO public.native_live_transitions_v1
                     (reservation_id,sequence,kind,previous_sha256,journal_json,journal_sha256)
                     VALUES(:id,1,'intent_committed',NULL,:raw,:sha)"""),
                        {
                            "id": reservation_id,
                            "raw": journal.decode("ascii"),
                            "sha": sha(journal),
                        },
                    )
                    last = await _profile(session, self.pins)
                    _need(
                        last[:12] == first[:12]
                        and type(last[12]) is str
                        and last[12].isascii()
                        and last[12].isdecimal()
                        and int(last[12]) > 0
                        and session.get_transaction() is transaction
                    )
                    _control(
                        row,
                        value,
                        await session.scalar(text("SELECT clock_timestamp()")),
                    )
                    _source(value, owned_source)
                    attempted = True
                    await transaction.commit()
                    committed = True
                except BaseException:
                    if not attempted:
                        await transaction.rollback()
                    raise
            result = await self.read_intent_data_v1(
                reservation_id, expected_sha256=digest
            )
            _source(value, owned_source)
            return result
        except BaseException:  # noqa: BLE001 -- uncertain COMMIT/cancellation must never enable retry.
            if committed:
                raise LiveLedgerReadbackRequired(
                    "native_live_committed_readback_required"
                ) from None
            if attempted:
                raise LiveLedgerCommitUnknown(
                    "native_live_commit_unknown_do_not_retry"
                ) from None
            raise LiveLedgerDataError("native_live_ledger_data_refused") from None

    async def read_intent_data_v1(self, reservation_id, *, expected_sha256):
        from pydantic import TypeAdapter

        from app.domain.native_live_intent_v1 import Digest

        TypeAdapter(Digest).validate_python(reservation_id, strict=True)
        TypeAdapter(Digest).validate_python(expected_sha256, strict=True)
        async with self.factory() as session, session.begin():
            for sql in (
                "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY",
                *_SETTINGS[1:],
            ):
                await session.execute(text(sql))
            await _profile(session, self.pins)
            row = (
                await session.execute(
                    text("""SELECT i.intent_json,i.intent_sha256,r.state,
               t.journal_json,t.journal_sha256 FROM public.native_live_intents_v1 i
               JOIN public.native_live_reservations_v1 r USING(reservation_id)
               JOIN public.native_live_transitions_v1 t USING(reservation_id)
               WHERE i.reservation_id=:id AND t.sequence=1"""),
                    {"id": reservation_id},
                )
            ).one()
            _need(
                row[1] == expected_sha256 == sha(row[0].encode("ascii"))
                and sha(row[3].encode("ascii")) == row[4]
            )
            return {
                "schema": "ctcc.native_live.intent_readback_data.v1",
                "reservation_id": reservation_id,
                "intent_sha256": expected_sha256,
                "state": row[2],
                "journal_sha256": row[4],
                "qualification_verified": False,
                "execution_authority": False,
                "admission": "DENY",
            }
