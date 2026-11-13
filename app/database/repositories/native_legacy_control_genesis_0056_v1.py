"""Owner-only seeding of the missing legacy Demo singleton as a durable stop."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from sqlalchemy import inspect, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from sqlalchemy.pool import NullPool

from app.database.models.demo_automation import DemoAutomationState
from app.database.models.demo_control import DemoAccountControl, DemoControlJournal
from app.database.repositories.demo_control import DemoControlRepository
from app.database.repositories.demo_control_current55_v2 import DemoControlRepositoryV2
from app.trade_qualification.demo_control import ControlScope, sha, utc

_REASONS = ("emergency_stop_engaged", "native_legacy_genesis_unreconciled")
_EMPTY_TABLES = (
    "demo_automation_runs",
    "demo_automation_fingerprints",
    "demo_soak_sessions",
    "demo_daily_performance_reports",
    "demo_performance_snapshots",
    "demo_control_clear_acknowledgements_v2",
    "okx_demo_balance_state",
    "okx_demo_order_state",
    "okx_demo_position_state",
    "okx_demo_algo_order_state",
    "okx_demo_sync_checkpoints",
    "orders",
    "positions",
    "fills",
    "protective_orders",
    "trades",
    "demo_policy_counter_initializations_v1",
    "qualification_reservations",
    "qualification_reservation_transitions",
    "qualification_submission_outcomes",
    "qualification_report_spool",
    "qualification_report_projection_receipts",
    "qualification_leverage_configuration_intents_v1",
    "qualification_leverage_configuration_attempts_v1",
    "qualification_leverage_configuration_results_v1",
)
_FENCED_TABLES = tuple(
    sorted(
        (
            *_EMPTY_TABLES,
            "demo_automation_state",
            "demo_account_controls",
            "demo_control_journal",
            "qualification_account_scopes",
        )
    )
)
_FENCE = text(
    "LOCK TABLE "
    + ", ".join(f"public.{table}" for table in _FENCED_TABLES)
    + " IN SHARE ROW EXCLUSIVE MODE NOWAIT"
)
_SCHEMA = text("""
    SELECT relname::text, relkind::text, relpersistence::text,
           relowner::bigint, relrowsecurity, relforcerowsecurity, relhassubclass,
           relhasrules
    FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
    WHERE n.nspname='public' AND relname::text=ANY(CAST(:tables AS text[]))
""")
_INSERT_TRIGGERS = text("""
    SELECT EXISTS(
        SELECT 1 FROM pg_catalog.pg_trigger
        WHERE tgrelid = 'public.demo_automation_state'::regclass
          AND (tgtype::int & 4) = 4
    )
""")
_CONTROL_POPULATION = text(
    "SELECT environment, account_id FROM public.demo_account_controls LIMIT 2"
)
_ACTIVE_REVISIONS = text("""
    SELECT EXISTS(SELECT 1 FROM public.qualification_account_scopes
     WHERE account_revision <> 0 OR ledger_revision <> 0
        OR claims_json IS NOT NULL OR claims_sha256 IS NOT NULL
        OR environment <> :environment OR account_id <> :account_id)
""")
_TIME = text("SELECT pg_catalog.clock_timestamp()")
_IDENTITY = text("""
    SELECT pg_catalog.pg_current_xact_id_if_assigned()::text, pg_catalog.pg_backend_pid(),
           pg_catalog.current_setting('synchronous_commit'),
           pg_catalog.current_setting('TimeZone'), pg_catalog.pg_is_in_recovery()
""")


class LegacyControlGenesis0056Error(ValueError):
    """Fixed refusal; database and account values are private."""


class LegacyControlGenesis0056Uncertain(LegacyControlGenesis0056Error):
    """The possible commit requires a separate observation."""


def _need(value):
    if value is not True:
        raise LegacyControlGenesis0056Error(
            "native_legacy_control_genesis_0056_refused"
        ) from None


def _stopped_values(at, event_sha256):
    """Persist an unknown, locked baseline using the existing singleton columns."""
    at = utc(at)
    optional = {
        key: None
        for key in (
            "equity_basis",
            "baseline_equity",
            "peak_equity",
            "risk_peak_equity",
            "active_instrument_id",
            "active_client_order_id",
            "active_start_equity",
            "active_started_at",
            "last_trade_closed_at",
            "last_started_at",
            "last_completed_at",
            "last_error",
        )
    }
    return {
        **optional,
        "id": 1,
        "armed": False,
        "emergency_stop": True,
        "locked": True,
        "restart_latch_required": True,
        "lock_reasons": [*_REASONS, f"native_legacy_genesis_control_{event_sha256}"],
        "control_revision": 1,
        "session_date": at.date(),
        "updated_at": at,
        "daily_pnl": 0,
        "trades_today": 0,
        "consecutive_losses": 0,
        "active_trades": {},
        "symbol_cooldowns": {},
        "realized_pnl_events": [],
    }


def _exact_readback(rows, at, event_sha256):
    rows = tuple(rows)
    _need(len(rows) == 1)
    row = rows[0]
    _need(type(row) is DemoAutomationState and utc(row.updated_at) <= utc(at))
    expected = _stopped_values(row.updated_at, event_sha256)
    _need(
        set(expected) == set(inspect(DemoAutomationState).columns.keys())
        and all(getattr(row, field) == value for field, value in expected.items())
        and row.armed is False
        and row.emergency_stop is row.locked is row.restart_latch_required is True
    )
    return row.updated_at


async def _read(session, event_sha256):
    now = await session.scalar(_TIME)
    rows = (
        await session.scalars(
            select(DemoAutomationState)
            .limit(2)
            .execution_options(populate_existing=True)
        )
    ).all()
    return _exact_readback(rows, now, event_sha256) if rows else None


async def _stopped_scope(session, event_sha256, *, read_only):
    identities = (await session.execute(_CONTROL_POPULATION)).all()
    _need(len(identities) == 1)
    scope = ControlScope(*identities[0])
    if read_only:
        row = await session.get(
            DemoAccountControl, (scope.environment, scope.account_id)
        )
    else:
        row = await DemoControlRepository._locked(session, scope)
    state, event = await DemoControlRepositoryV2._snapshot(session, row)
    _need(
        state.scope == scope
        and event == event_sha256
        and state.emergency_stop is True
        and state.arm_request_id is state.arm_expires_at is None
    )
    at = utc(await session.scalar(_TIME))
    _need(state.updated_at <= at and state.lease_until <= at)
    return scope


@dataclass(frozen=True, slots=True, repr=False)
class LegacyControlGenesisResult0056V1:
    status: str
    updated_at: datetime
    control_revision: Literal[1] = field(default=1, init=False)
    emergency_stop: Literal[True] = field(default=True, init=False)
    armed: Literal[False] = field(default=False, init=False)
    locked: Literal[True] = field(default=True, init=False)
    startup_admission: Literal[False] = field(default=False, init=False)
    execution_authority: Literal[False] = field(default=False, init=False)


async def _owner_identity(
    session, engine, pins, server_incarnation_sha256, *, read_only
):
    from app.core import native_role_preflight_0056_full_diagnostic_v1 as roles56
    from scripts import native_owner_postgrant_readback_0056_v1 as postgrant
    from scripts import native_owner_route_preflight_0056_v1 as route
    from scripts import native_owner_upgrade_0056_v1 as upgrade

    _need(
        type(session) is AsyncSession
        and session.in_transaction()
        and session.get_nested_transaction() is None
        and session.autoflush is False
        and not (session.new or session.dirty or session.deleted)
        and all(
            session.get_bind(mapper=model) is engine.sync_engine
            for model in (DemoAutomationState, DemoAccountControl, DemoControlJournal)
        )
        and session.get_bind() is engine.sync_engine
        and type(pins) is route.OwnerRoutePinsV1
        and pins.database_head == "0056"
    )
    identity = await roles56._identity_at_0056(session, pins.owner)
    upgrade._checked_profile(
        tuple((await session.execute(route._PROFILE_SQL)).one()),
        pins,
        "0056",
        read_only=read_only,
    )
    _need(postgrant._owner_server_sha256(identity) == server_incarnation_sha256)
    xid, pid, synchronous_commit, timezone, in_recovery = (
        await session.execute(_IDENTITY)
    ).one()
    _need(
        (synchronous_commit, timezone, in_recovery) == ("on", "UTC", False)
        and type(pid) is int
        and pid > 0
    )
    return identity, xid, pid


def _owner_engine(factory, pins):
    from app.core.native_locked_demo_control_0056_v1 import _direct_tls_route

    _need(type(factory) is async_sessionmaker and factory.class_ is AsyncSession)
    engine = factory.kw.get("bind")
    _need(
        type(engine) is AsyncEngine
        and type(engine.pool) is NullPool
        and factory.kw.get("autoflush") is False
        and engine.url.username == pins.owner
        and _direct_tls_route(engine)
        == ("postgresql+asyncpg", pins.host, pins.port, pins.database)
    )
    return engine


async def seed_owner_legacy_control_genesis_0056_v1(
    factory, *, event_sha256, pins, server_incarnation_sha256, reconcile_only
):
    """Use the caller's authenticated exact owner route. This never clears a stop."""
    attempted = False
    try:
        sha(event_sha256)
        sha(server_incarnation_sha256)
        _need(type(reconcile_only) is bool)
        engine = _owner_engine(factory, pins)
        async with asyncio.timeout(25):
            async with factory() as write:
                tx = await write.begin()
                for statement in (
                    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
                    if reconcile_only
                    else "SET TRANSACTION ISOLATION LEVEL READ COMMITTED, READ WRITE",
                    "SET LOCAL search_path TO pg_catalog, public, pg_temp",
                    "SET LOCAL TIME ZONE 'UTC'",
                    "SET LOCAL row_security='off'",
                    "SET LOCAL synchronous_commit='on'",
                    "SET LOCAL lock_timeout='1000ms'",
                    "SET LOCAL statement_timeout='3000ms'",
                ):
                    await write.execute(text(statement))
                identity, _, first_pid = await _owner_identity(
                    write,
                    engine,
                    pins,
                    server_incarnation_sha256,
                    read_only=reconcile_only,
                )
                scope = await _stopped_scope(
                    write, event_sha256, read_only=reconcile_only
                )
                if not reconcile_only:
                    # The NO WAIT fence cannot get ahead of an active legacy
                    # capture or native reservation transaction.
                    await write.execute(_FENCE)
                schema = (
                    await write.execute(_SCHEMA, {"tables": list(_FENCED_TABLES)})
                ).all()
                _need(
                    len(schema) == len(_FENCED_TABLES)
                    and {row[0] for row in schema} == set(_FENCED_TABLES)
                    and all(
                        tuple(row[1:])
                        == ("r", "p", pins.owner_oid, False, False, False, False)
                        for row in schema
                    )
                )
                _need(not await write.scalar(_INSERT_TRIGGERS))
                _need(
                    await _stopped_scope(write, event_sha256, read_only=reconcile_only)
                    == scope
                )
                first = await _read(write, event_sha256)
                if first is not None:
                    # Only the exact initial marker may be reconciled. An
                    # existing legacy revision is never rewritten here.
                    await tx.rollback()
                else:
                    _need(not reconcile_only)
                    # Table identifiers below are fixed literals above, not UI or SQL input.
                    for table in _EMPTY_TABLES:
                        _need(
                            not await write.scalar(
                                text(f"SELECT EXISTS(SELECT 1 FROM public.{table})")
                            )
                        )
                    _need(
                        not await write.scalar(
                            _ACTIVE_REVISIONS,
                            {
                                "environment": scope.environment,
                                "account_id": scope.account_id,
                            },
                        )
                    )
                    at = utc(await write.scalar(_TIME))
                    stmt = (
                        insert(DemoAutomationState)
                        .values(**_stopped_values(at, event_sha256))
                        .on_conflict_do_nothing(index_elements=[DemoAutomationState.id])
                        .returning(DemoAutomationState.id)
                    )
                    _need(await write.scalar(stmt) == 1)
                    first = await _read(write, event_sha256)
                    second_identity, xid, second_pid = await _owner_identity(
                        write, engine, pins, server_incarnation_sha256, read_only=False
                    )
                    _need(
                        first == at
                        and identity == second_identity
                        and first_pid == second_pid
                        and type(xid) is str
                        and xid.isascii()
                        and xid.isdigit()
                        and 0 < int(xid) < 2**64
                        and write.sync_session.get_transaction() is tx.sync_transaction
                    )
                    attempted = True
                    await tx.commit()
            async with factory() as reader, reader.begin():
                await reader.execute(
                    text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
                )
                await reader.execute(
                    text("SET LOCAL search_path TO pg_catalog, public, pg_temp")
                )
                await reader.execute(text("SET LOCAL TIME ZONE 'UTC'"))
                await reader.execute(text("SET LOCAL row_security='off'"))
                await reader.execute(text("SET LOCAL synchronous_commit='on'"))
                await reader.execute(text("SET LOCAL statement_timeout='3000ms'"))
                observed_identity, xid, read_pid = await _owner_identity(
                    reader, engine, pins, server_incarnation_sha256, read_only=True
                )
                _need(
                    reader is not write
                    and read_pid != first_pid
                    and xid is None
                    and observed_identity == identity
                )
                _need(
                    await _stopped_scope(reader, event_sha256, read_only=True) == scope
                )
                _need(await _read(reader, event_sha256) == first)
            return LegacyControlGenesisResult0056V1(
                "STOPPED_CREATED" if attempted else "STOPPED_OBSERVED", first
            )
    except (Exception, asyncio.CancelledError) as error:
        if attempted:
            raise LegacyControlGenesis0056Uncertain(
                "native_legacy_control_genesis_0056_uncertain"
            ) from None
        if isinstance(error, (asyncio.CancelledError, LegacyControlGenesis0056Error)):
            raise
        _need(False)


async def read_restricted_legacy_control_genesis_0056_v1(
    selected, roles, *, event_sha256, expected_at
):
    """Read an already committed stopped marker under the actual restricted writer."""
    from app.core import native_locked_demo_control_0056_v1 as locked

    sha(event_sha256)
    utc(expected_at)
    async with (
        asyncio.timeout(20),
        selected.writer_factory() as session,
        session.begin(),
    ):
        await session.execute(text("SET LOCAL statement_timeout='1000ms'"))
        identities = (
            await session.execute(
                select(DemoControlJournal.environment, DemoControlJournal.account_id)
                .where(DemoControlJournal.event_sha256 == event_sha256)
                .limit(2)
            )
        ).all()
        _need(len(identities) == 1)
        scope = ControlScope(*identities[0])
        async with locked.held_native_writer_control_0056_v1(
            selected,
            session,
            scope,
            roles=roles,
            purpose=locked.NativeWriterControlPurpose0056.PREPARE_OBSERVATION,
        ) as held:
            actual = held.observation.control
            _need(
                actual.event_sha256 == event_sha256
                and actual.state.emergency_stop is True
                and actual.state.arm_request_id is None
            )
            observed = await _read(session, event_sha256)
            held.check_held()
            _need(observed == expected_at)
