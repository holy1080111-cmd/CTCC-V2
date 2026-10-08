from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select, true, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.models.demo_automation import (
    DemoAutomationFingerprint,
    DemoAutomationRun,
    DemoAutomationState,
)
from app.domain.demo_automation import DemoAutomationRunResult


class DemoAutomationStateConflict(RuntimeError):
    """A stale process must not overwrite newer durable control state."""


class DemoAutomationRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def load_state(self) -> dict[str, Any] | None:
        async with self.session_factory() as session:
            row = await session.get(DemoAutomationState, 1)
        if row is None:
            return None
        return {
            "armed": row.armed,
            "emergency_stop": row.emergency_stop,
            "_control_revision": row.control_revision,
            "_restart_latch_required": row.restart_latch_required,
            "locked": row.locked,
            "lock_reasons": list(row.lock_reasons or []),
            "session_date": row.session_date,
            "equity_basis": row.equity_basis,
            "baseline_equity": row.baseline_equity,
            "peak_equity": row.peak_equity,
            "risk_peak_equity": row.risk_peak_equity,
            "daily_pnl": row.daily_pnl,
            "trades_today": row.trades_today,
            "consecutive_losses": row.consecutive_losses,
            "active_instrument_id": row.active_instrument_id,
            "active_client_order_id": row.active_client_order_id,
            "active_start_equity": row.active_start_equity,
            "active_started_at": row.active_started_at,
            "active_trades": deepcopy(row.active_trades),
            "symbol_cooldowns": dict(row.symbol_cooldowns or {}),
            "realized_pnl_events": deepcopy(row.realized_pnl_events),
            "last_trade_closed_at": row.last_trade_closed_at,
            "last_started_at": row.last_started_at,
            "last_completed_at": row.last_completed_at,
            "last_error": row.last_error,
        }

    async def assert_current_execution_control(self, expected_revision: int) -> int:
        """Read the current durable Arm/stop state at the final submit boundary.

        This is a fresh database read, separate from the process's cached
        state. A missing row, stale revision, or any uncertain control field
        denies submission. It does not create execution authority.
        """
        if type(expected_revision) is not int or expected_revision < 1:
            raise DemoAutomationStateConflict(
                "demo_automation_control_revision_unconfirmed"
            )
        async with self.session_factory() as session:
            result = await session.execute(
                select(
                    DemoAutomationState.control_revision,
                    DemoAutomationState.armed,
                    DemoAutomationState.emergency_stop,
                    DemoAutomationState.locked,
                    DemoAutomationState.restart_latch_required,
                ).where(DemoAutomationState.id == 1)
            )
            control = result.one_or_none()
        if control is None:
            raise DemoAutomationStateConflict("demo_automation_control_row_missing")
        revision, armed, emergency_stop, locked, restart_latch_required = control
        if (
            type(revision) is not int
            or revision != expected_revision
            or armed is not True
            or emergency_stop is not False
            or locked is not False
            or restart_latch_required is not True
        ):
            raise DemoAutomationStateConflict(
                "demo_automation_control_not_currently_authorized"
            )
        return revision

    async def save_state(self, state: dict[str, Any]) -> None:
        # These two internal keys are protocol metadata, not model columns.
        # A successful write must advance the revision exactly once. Every
        # writer, including routine state persistence, participates in CAS so
        # a stale worker cannot resurrect Arm or clear an EStop.
        revision = state.get("_control_revision")
        restart_latch_required = state.get("_restart_latch_required")
        if (
            not isinstance(revision, int)
            or isinstance(revision, bool)
            or revision < 1
            or not isinstance(restart_latch_required, bool)
        ):
            raise ValueError("demo_automation_control_metadata_invalid")
        values = {
            "id": 1,
            **{key: value for key, value in state.items() if not key.startswith("_")},
            "control_revision": revision,
            "restart_latch_required": restart_latch_required,
            "updated_at": datetime.now(UTC),
        }
        latch_not_cleared = (
            true()
            if restart_latch_required
            else DemoAutomationState.restart_latch_required.is_(False)
        )
        async with self.session_factory() as session, session.begin():
            if revision == 1:
                # A fresh singleton may be inserted; a pre-0034 singleton at
                # revision zero may be upgraded by the first current writer.
                stmt = pg_insert(DemoAutomationState).values(**values)
                stmt = stmt.on_conflict_do_update(
                    index_elements=[DemoAutomationState.id],
                    set_={key: value for key, value in values.items() if key != "id"},
                    where=(DemoAutomationState.control_revision == 0)
                    & latch_not_cleared,
                ).returning(DemoAutomationState.control_revision)
            else:
                stmt = (
                    update(DemoAutomationState)
                    .where(
                        DemoAutomationState.id == 1,
                        DemoAutomationState.control_revision == revision - 1,
                        latch_not_cleared,
                    )
                    .values(
                        **{key: value for key, value in values.items() if key != "id"}
                    )
                    .returning(DemoAutomationState.control_revision)
                )
            observed = await session.scalar(stmt)
            if observed != revision:
                raise DemoAutomationStateConflict(
                    "demo_automation_control_revision_conflict"
                )

    async def latch_emergency_stop(self, reason: str) -> int:
        """Stop the current DB state, even when the caller's copy is stale.

        Only control fields are touched. In particular, an older process must
        never replace newer exposure, PnL or outcome evidence to stop trading.
        The row lock serializes this operation with CAS state writes.
        """
        async with self.session_factory() as session, session.begin():
            row = await session.scalar(
                select(DemoAutomationState)
                .where(DemoAutomationState.id == 1)
                .with_for_update()
            )
            if row is None:
                raise DemoAutomationStateConflict("demo_automation_control_row_missing")
            row.armed = False
            row.emergency_stop = True
            row.locked = True
            row.lock_reasons = sorted(
                {*(row.lock_reasons or []), "emergency_stop_engaged", reason}
            )
            row.restart_latch_required = True
            row.control_revision += 1
            await session.flush()
            revision = row.control_revision
        return revision

    async def save_run(
        self, run: DemoAutomationRunResult, *, history_limit: int
    ) -> None:
        async with self.session_factory() as session:  # noqa: SIM117 - Keep database session and transaction lifetimes explicit at the durable boundary.
            async with session.begin():
                session.add(
                    DemoAutomationRun(
                        trigger=run.trigger,
                        execute=run.execute,
                        result=run.model_dump(mode="json"),
                        started_at=run.started_at,
                        completed_at=run.completed_at,
                    )
                )
                ids = (
                    await session.scalars(
                        select(DemoAutomationRun.id)
                        .order_by(DemoAutomationRun.completed_at.desc())
                        .offset(history_limit)
                    )
                ).all()
                if ids:
                    await session.execute(
                        delete(DemoAutomationRun).where(DemoAutomationRun.id.in_(ids))
                    )

    async def load_runs(self, limit: int) -> list[DemoAutomationRunResult]:
        async with self.session_factory() as session:
            rows = (
                await session.scalars(
                    select(DemoAutomationRun)
                    .order_by(DemoAutomationRun.completed_at.desc())
                    .limit(limit)
                )
            ).all()
        return [
            DemoAutomationRunResult.model_validate(row.result) for row in reversed(rows)
        ]

    async def fingerprint_exists(self, fingerprint: str, now: datetime) -> bool:
        async with self.session_factory() as session:
            expiry = await session.scalar(
                select(DemoAutomationFingerprint.expires_at).where(
                    DemoAutomationFingerprint.fingerprint == fingerprint
                )
            )
        return expiry is not None and expiry > now

    async def save_fingerprint(
        self, fingerprint: str, expires_at: datetime, details: dict[str, Any]
    ) -> None:
        async with self.session_factory() as session, session.begin():
            stmt = pg_insert(DemoAutomationFingerprint).values(
                fingerprint=fingerprint,
                expires_at=expires_at,
                details=details,
            )
            await session.execute(
                stmt.on_conflict_do_update(
                    index_elements=[DemoAutomationFingerprint.fingerprint],
                    set_={"expires_at": expires_at, "details": details},
                )
            )

    async def cleanup_fingerprints(self, now: datetime) -> None:
        async with self.session_factory() as session, session.begin():
            await session.execute(
                delete(DemoAutomationFingerprint).where(
                    DemoAutomationFingerprint.expires_at <= now
                )
            )
