from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.models.demo_automation import (
    DemoAutomationFingerprint,
    DemoAutomationRun,
    DemoAutomationState,
)
from app.domain.demo_automation import DemoAutomationRunResult


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
            "active_trades": dict(row.active_trades or {}),
            "symbol_cooldowns": dict(row.symbol_cooldowns or {}),
            "realized_pnl_events": list(row.realized_pnl_events or []),
            "last_trade_closed_at": row.last_trade_closed_at,
            "last_started_at": row.last_started_at,
            "last_completed_at": row.last_completed_at,
            "last_error": row.last_error,
        }

    async def save_state(self, state: dict[str, Any]) -> None:
        values = {"id": 1, **state, "updated_at": datetime.now(timezone.utc)}
        async with self.session_factory() as session:
            async with session.begin():
                stmt = pg_insert(DemoAutomationState).values(**values)
                await session.execute(
                    stmt.on_conflict_do_update(
                        index_elements=[DemoAutomationState.id],
                        set_={key: value for key, value in values.items() if key != "id"},
                    )
                )

    async def save_run(self, run: DemoAutomationRunResult, *, history_limit: int) -> None:
        async with self.session_factory() as session:
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
                    await session.execute(delete(DemoAutomationRun).where(DemoAutomationRun.id.in_(ids)))

    async def load_runs(self, limit: int) -> list[DemoAutomationRunResult]:
        async with self.session_factory() as session:
            rows = (
                await session.scalars(
                    select(DemoAutomationRun)
                    .order_by(DemoAutomationRun.completed_at.desc())
                    .limit(limit)
                )
            ).all()
        return [DemoAutomationRunResult.model_validate(row.result) for row in reversed(rows)]

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
        async with self.session_factory() as session:
            async with session.begin():
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
        async with self.session_factory() as session:
            async with session.begin():
                await session.execute(
                    delete(DemoAutomationFingerprint).where(
                        DemoAutomationFingerprint.expires_at <= now
                    )
                )
