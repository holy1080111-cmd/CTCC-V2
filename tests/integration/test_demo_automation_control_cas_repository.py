"""Real PostgreSQL CAS/stop semantics in a disposable isolated schema.

Requires an explicitly supplied isolated DATABASE_URL. This test never calls
OKX, creates an order, or touches an existing demo_automation_state row.
"""

import asyncio
import os
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.database.models.demo_automation import DemoAutomationState
from app.database.repositories.demo_automation import (
    DemoAutomationRepository,
    DemoAutomationStateConflict,
)
from tests.durable_migration_fixtures import load_migration

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def assert_check_violation(error: IntegrityError) -> None:
    candidates = (error.orig, error.orig.__cause__, error.orig.__context__)
    codes = {
        code
        for candidate in candidates
        if candidate is not None
        for code in (
            getattr(candidate, "sqlstate", None),
            getattr(candidate, "pgcode", None),
        )
        if code is not None
    }
    assert codes == {"23514"}


@pytest.fixture
async def repository():
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        pytest.skip("explicit isolated DATABASE_URL required")
    schema = f"ctcc_demo_control_cas_{uuid4().hex}"
    admin_engine = create_async_engine(url, poolclass=NullPool)
    test_engine = None
    created = False
    try:
        async with admin_engine.begin() as connection:
            await connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
        created = True
        test_engine = create_async_engine(
            url,
            poolclass=NullPool,
            connect_args={"server_settings": {"search_path": schema}},
        )
        async with test_engine.begin() as connection:
            await connection.run_sync(DemoAutomationState.__table__.create)
            migration = load_migration("0034")
            await connection.exec_driver_sql(migration.CONTROL_GUARD_FUNCTION_SQL)
            await connection.exec_driver_sql(migration.CONTROL_GUARD_TRIGGER_SQL)
        sessions = async_sessionmaker(
            test_engine, expire_on_commit=False, autoflush=False
        )
        yield DemoAutomationRepository(sessions), sessions
    finally:
        if test_engine is not None:
            await test_engine.dispose()
        if created:
            async with admin_engine.begin() as connection:
                await connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        await admin_engine.dispose()


def initial_state():
    return {
        "armed": False,
        "emergency_stop": False,
        "locked": False,
        "lock_reasons": [],
        "session_date": datetime.now(UTC).date(),
        "equity_basis": None,
        "baseline_equity": None,
        "peak_equity": None,
        "risk_peak_equity": None,
        "daily_pnl": Decimal(0),
        "trades_today": 0,
        "consecutive_losses": 0,
        "active_instrument_id": None,
        "active_client_order_id": None,
        "active_start_equity": None,
        "active_started_at": None,
        "active_trades": {},
        "symbol_cooldowns": {},
        "realized_pnl_events": [],
        "last_trade_closed_at": None,
        "last_started_at": None,
        "last_completed_at": None,
        "last_error": None,
        "_control_revision": 1,
        "_restart_latch_required": False,
    }


async def test_final_submit_guard_rechecks_fresh_database_control(repository):
    owner, sessions = repository
    with pytest.raises(DemoAutomationStateConflict, match="row_missing"):
        await owner.assert_current_execution_control(1)
    await owner.save_state(initial_state())
    armed = await owner.load_state()
    assert armed is not None
    armed["armed"] = True
    armed["_restart_latch_required"] = True
    armed["_control_revision"] = 2
    await owner.save_state(armed)
    stale_process = DemoAutomationRepository(sessions)
    assert await stale_process.assert_current_execution_control(2) == 2

    # A harmless-looking durable revision advance still invalidates a stale
    # process's Arm, even if the row remains armed.
    updated = await owner.load_state()
    assert updated is not None
    updated["_control_revision"] = 3
    updated["last_error"] = "other_process_updated_state"
    await owner.save_state(updated)
    with pytest.raises(DemoAutomationStateConflict, match="not_currently_authorized"):
        await stale_process.assert_current_execution_control(2)
    assert await owner.assert_current_execution_control(3) == 3

    await owner.latch_emergency_stop("other_process_stopped")
    with pytest.raises(DemoAutomationStateConflict, match="not_currently_authorized"):
        await stale_process.assert_current_execution_control(3)
    with pytest.raises(DemoAutomationStateConflict):
        await owner.assert_current_execution_control(True)


async def test_two_writers_one_revision_then_stale_stop_preserves_trade_state(
    repository,
):
    owner, sessions = repository
    await owner.save_state(initial_state())
    first = await owner.load_state()
    second = await owner.load_state()
    assert first is not None and second is not None
    for marker, state in (("worker_a", first), ("worker_b", second)):
        state["armed"] = True
        state["_restart_latch_required"] = True
        state["_control_revision"] = 2
        state["last_error"] = marker
        state["active_trades"] = {"synthetic_uncertain": {"origin": marker}}

    writer_a = DemoAutomationRepository(sessions)
    writer_b = DemoAutomationRepository(sessions)
    outcomes = await asyncio.gather(
        writer_a.save_state(first),
        writer_b.save_state(second),
        return_exceptions=True,
    )
    assert sum(result is None for result in outcomes) == 1
    assert (
        sum(isinstance(result, DemoAutomationStateConflict) for result in outcomes) == 1
    )
    committed = await owner.load_state()
    assert committed is not None
    assert committed["_control_revision"] == 2
    assert committed["armed"] is True
    marker = committed["last_error"]
    assert committed["active_trades"] == {"synthetic_uncertain": {"origin": marker}}

    # A process still holding revision 1 can stop the latest row without
    # writing its stale PnL, event, or active-trade copy into PostgreSQL.
    stop_revision = await writer_a.latch_emergency_stop("synthetic_cross_process_stop")
    observed = await writer_b.load_state()
    assert stop_revision == 3
    assert observed is not None and observed["_control_revision"] == 3
    assert observed["armed"] is False and observed["emergency_stop"] is True
    assert observed["_restart_latch_required"] is True
    assert "synthetic_cross_process_stop" in observed["lock_reasons"]
    assert observed["last_error"] == marker
    assert observed["active_trades"] == committed["active_trades"]

    stale_clear = dict(committed)
    stale_clear["_control_revision"] = 3
    stale_clear["armed"] = False
    stale_clear["emergency_stop"] = False
    with pytest.raises(DemoAutomationStateConflict):
        await writer_b.save_state(stale_clear)
    latch_clear = dict(observed)
    latch_clear["_control_revision"] = 4
    latch_clear["emergency_stop"] = False
    latch_clear["_restart_latch_required"] = False
    with pytest.raises(DemoAutomationStateConflict):
        await writer_b.save_state(latch_clear)
    assert (await owner.load_state())["emergency_stop"] is True


@pytest.mark.parametrize(
    "forbidden",
    (
        {"control_revision": -1},
        {"armed": True, "emergency_stop": True, "restart_latch_required": True},
        {"armed": True, "emergency_stop": False, "restart_latch_required": False},
    ),
)
async def test_database_control_constraints_reject_invalid_state(repository, forbidden):
    owner, sessions = repository
    if "control_revision" in forbidden:
        # An INSERT has no OLD row for the revision trigger; this probes the
        # nonnegative CHECK itself, not merely the trigger's revision guard.
        with pytest.raises(IntegrityError) as error:
            async with sessions() as session, session.begin():
                session.add(
                    DemoAutomationState(
                        id=1,
                        session_date=datetime.now(UTC).date(),
                        armed=False,
                        emergency_stop=False,
                        locked=False,
                        lock_reasons=[],
                        control_revision=-1,
                        restart_latch_required=False,
                    )
                )
        assert_check_violation(error.value)
    else:
        await owner.save_state(initial_state())
        with pytest.raises(IntegrityError) as error:
            async with sessions() as session, session.begin():
                await session.execute(
                    update(DemoAutomationState)
                    .where(DemoAutomationState.id == 1)
                    .values(control_revision=2, **forbidden)
                )
        assert_check_violation(error.value)
    if "control_revision" in forbidden:
        await owner.save_state(initial_state())
    state = await owner.load_state()
    assert state is not None
    assert state["_control_revision"] == 1
    assert state["armed"] is False and state["emergency_stop"] is False


async def test_pre_0034_writer_cannot_rearm_or_erase_stop_without_revision(repository):
    owner, sessions = repository
    await owner.save_state(initial_state())
    assert await owner.latch_emergency_stop("synthetic_stop") == 2
    stopped = await owner.load_state()
    assert stopped is not None and stopped["emergency_stop"] is True

    # These statements intentionally omit control_revision, exactly as an
    # old writer would after the migration adds that column with a default.
    for old_write in (
        {"armed": True, "emergency_stop": False, "restart_latch_required": True},
        {"last_error": "old_writer_overwrite"},
    ):
        with pytest.raises(IntegrityError) as error:
            async with sessions() as session, session.begin():
                await session.execute(
                    update(DemoAutomationState)
                    .where(DemoAutomationState.id == 1)
                    .values(**old_write)
                )
        assert_check_violation(error.value)
        observed = await owner.load_state()
        assert observed is not None and observed["_control_revision"] == 2
        assert observed["emergency_stop"] is True and observed["armed"] is False

    # Even a writer that advances the revision cannot erase the sticky boot
    # latch. It must remain through a permitted clear in the current process.
    with pytest.raises(IntegrityError) as error:
        async with sessions() as session, session.begin():
            await session.execute(
                update(DemoAutomationState)
                .where(DemoAutomationState.id == 1)
                .values(
                    control_revision=3,
                    emergency_stop=False,
                    restart_latch_required=False,
                )
            )
    assert_check_violation(error.value)
    assert (await owner.load_state())["_restart_latch_required"] is True
