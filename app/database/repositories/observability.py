from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.models.demo_automation import DemoAutomationRun
from app.database.models.observability import DemoObservabilityEvent, DemoSoakSession
from app.domain.demo_automation import DemoAutomationRunResult
from app.domain.observability import DemoObservabilityEventView, DemoSoakSessionView


class DemoObservabilityRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def interrupt_running_sessions(self) -> int:
        now = datetime.now(timezone.utc)
        async with self.session_factory() as session:
            async with session.begin():
                result = await session.execute(
                    update(DemoSoakSession)
                    .where(DemoSoakSession.state == "running")
                    .values(
                        state="interrupted",
                        stopped_at=now,
                        stop_reason="api_process_restarted",
                        updated_at=now,
                    )
                )
        return int(result.rowcount or 0)

    async def create_session(self, session_view: DemoSoakSessionView) -> DemoSoakSessionView:
        row = DemoSoakSession(
            state=session_view.state,
            execute=session_view.execute,
            symbols=session_view.symbols,
            interval_seconds=session_view.interval_seconds,
            duration_minutes=session_view.duration_minutes,
            max_runs=session_view.max_runs,
            max_submissions=session_view.max_submissions,
            started_at=session_view.started_at,
            planned_end_at=session_view.planned_end_at,
            stopped_at=session_view.stopped_at,
            completed_runs=session_view.completed_runs,
            submitted_runs=session_view.submitted_runs,
            dry_run_runs=session_view.dry_run_runs,
            blocked_runs=session_view.blocked_runs,
            error_runs=session_view.error_runs,
            consecutive_errors=session_view.consecutive_errors,
            starting_equity=session_view.starting_equity,
            latest_equity=session_view.latest_equity,
            session_pnl=session_view.session_pnl,
            max_drawdown_pct_observed=session_view.max_drawdown_pct_observed,
            protection_checks=session_view.protection_checks,
            protection_failures=session_view.protection_failures,
            active_position_count=session_view.active_position_count,
            active_pending_order_count=session_view.active_pending_order_count,
            active_algo_order_count=session_view.active_algo_order_count,
            protection_verified=session_view.protection_verified,
            auto_disarmed=session_view.auto_disarmed,
            last_run_at=session_view.last_run_at,
            last_outcome=session_view.last_outcome,
            stop_reason=session_view.stop_reason,
            safety_stop_reason=session_view.safety_stop_reason,
            last_error=session_view.last_error,
        )
        async with self.session_factory() as session:
            async with session.begin():
                session.add(row)
                await session.flush()
                await session.refresh(row)
        return self._session_view(row)

    async def update_session(self, session_view: DemoSoakSessionView) -> None:
        if session_view.id is None:
            return
        values = session_view.model_dump(exclude={"id"}, mode="python")
        values["updated_at"] = datetime.now(timezone.utc)
        async with self.session_factory() as session:
            async with session.begin():
                await session.execute(
                    update(DemoSoakSession)
                    .where(DemoSoakSession.id == session_view.id)
                    .values(**values)
                )

    async def latest_session(self) -> DemoSoakSessionView | None:
        async with self.session_factory() as session:
            row = await session.scalar(
                select(DemoSoakSession)
                .order_by(DemoSoakSession.started_at.desc())
                .limit(1)
            )
        return None if row is None else self._session_view(row)

    async def add_event(
        self,
        *,
        severity: str,
        code: str,
        message: str,
        details: dict[str, Any],
        event_limit: int,
    ) -> DemoObservabilityEventView:
        row = DemoObservabilityEvent(
            severity=severity,
            code=code,
            message=message,
            details=details,
            observed_at=datetime.now(timezone.utc),
        )
        async with self.session_factory() as session:
            async with session.begin():
                session.add(row)
                await session.flush()
                await session.refresh(row)
                ids = (
                    await session.scalars(
                        select(DemoObservabilityEvent.id)
                        .order_by(DemoObservabilityEvent.observed_at.desc())
                        .offset(event_limit)
                    )
                ).all()
                if ids:
                    await session.execute(
                        delete(DemoObservabilityEvent).where(
                            DemoObservabilityEvent.id.in_(ids)
                        )
                    )
        return self._event_view(row)


    async def automation_runs_since(
        self, cutoff: datetime, *, limit: int
    ) -> list[DemoAutomationRunResult]:
        async with self.session_factory() as session:
            rows = (
                await session.scalars(
                    select(DemoAutomationRun)
                    .where(DemoAutomationRun.completed_at >= cutoff)
                    .order_by(DemoAutomationRun.completed_at.asc())
                    .limit(limit)
                )
            ).all()
        return [DemoAutomationRunResult.model_validate(row.result) for row in rows]

    async def events(self, limit: int) -> list[DemoObservabilityEventView]:
        async with self.session_factory() as session:
            rows = (
                await session.scalars(
                    select(DemoObservabilityEvent)
                    .order_by(DemoObservabilityEvent.observed_at.desc())
                    .limit(limit)
                )
            ).all()
        return [self._event_view(row) for row in rows]

    @staticmethod
    def _session_view(row: DemoSoakSession) -> DemoSoakSessionView:
        return DemoSoakSessionView(
            id=row.id,
            state=row.state,
            execute=row.execute,
            symbols=list(row.symbols or []),
            interval_seconds=row.interval_seconds,
            duration_minutes=row.duration_minutes,
            max_runs=row.max_runs,
            max_submissions=row.max_submissions,
            started_at=row.started_at,
            planned_end_at=row.planned_end_at,
            stopped_at=row.stopped_at,
            completed_runs=row.completed_runs,
            submitted_runs=row.submitted_runs,
            dry_run_runs=row.dry_run_runs,
            blocked_runs=row.blocked_runs,
            error_runs=row.error_runs,
            consecutive_errors=row.consecutive_errors,
            starting_equity=row.starting_equity,
            latest_equity=row.latest_equity,
            session_pnl=row.session_pnl,
            max_drawdown_pct_observed=row.max_drawdown_pct_observed,
            protection_checks=row.protection_checks,
            protection_failures=row.protection_failures,
            active_position_count=row.active_position_count,
            active_pending_order_count=row.active_pending_order_count,
            active_algo_order_count=row.active_algo_order_count,
            protection_verified=row.protection_verified,
            auto_disarmed=row.auto_disarmed,
            last_run_at=row.last_run_at,
            last_outcome=row.last_outcome,
            stop_reason=row.stop_reason,
            safety_stop_reason=row.safety_stop_reason,
            last_error=row.last_error,
        )

    @staticmethod
    def _event_view(row: DemoObservabilityEvent) -> DemoObservabilityEventView:
        return DemoObservabilityEventView(
            id=row.id,
            severity=row.severity,
            code=row.code,
            message=row.message,
            details=dict(row.details or {}),
            observed_at=row.observed_at,
        )
