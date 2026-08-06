from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.models.demo_automation import DemoAutomationRun
from app.database.models.okx_demo import OkxDemoOrderState
from app.database.models.performance import (
    DemoDailyPerformanceReport as DemoDailyPerformanceReportRow,
    DemoPerformanceSnapshot,
    DemoStrategyControl,
)
from app.domain.demo_automation import DemoAutomationRunResult
from app.domain.performance import (
    DemoDailyPerformanceReport,
    DemoEquityPoint,
    DemoOrderPerformanceSample,
    DemoStrategyControlView,
)


class DemoPerformanceRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def add_snapshot(
        self,
        *,
        captured_at: datetime,
        total_equity: Decimal,
        available_equity: Decimal,
        unrealized_pnl: Decimal,
        position_count: int,
        pending_order_count: int,
        algo_order_count: int,
        details: dict,
        retention_days: int,
    ) -> None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
        async with self.session_factory() as session:
            async with session.begin():
                session.add(
                    DemoPerformanceSnapshot(
                        captured_at=captured_at,
                        total_equity=total_equity,
                        available_equity=available_equity,
                        unrealized_pnl=unrealized_pnl,
                        position_count=position_count,
                        pending_order_count=pending_order_count,
                        algo_order_count=algo_order_count,
                        details=details,
                    )
                )
                await session.execute(
                    delete(DemoPerformanceSnapshot).where(
                        DemoPerformanceSnapshot.captured_at < cutoff
                    )
                )

    async def snapshots_between(
        self, start: datetime, end: datetime, *, limit: int
    ) -> list[DemoEquityPoint]:
        async with self.session_factory() as session:
            rows = (
                await session.scalars(
                    select(DemoPerformanceSnapshot)
                    .where(
                        DemoPerformanceSnapshot.captured_at >= start,
                        DemoPerformanceSnapshot.captured_at < end,
                    )
                    .order_by(DemoPerformanceSnapshot.captured_at.asc())
                    .limit(limit)
                )
            ).all()
        return [
            DemoEquityPoint(
                captured_at=row.captured_at,
                total_equity=row.total_equity,
                available_equity=row.available_equity,
                unrealized_pnl=row.unrealized_pnl,
                position_count=row.position_count,
                pending_order_count=row.pending_order_count,
                algo_order_count=row.algo_order_count,
            )
            for row in rows
        ]

    async def orders_between(
        self, start: datetime, end: datetime, *, limit: int
    ) -> list[DemoOrderPerformanceSample]:
        timestamp = func.coalesce(
            OkxDemoOrderState.exchange_updated_at,
            OkxDemoOrderState.exchange_created_at,
            OkxDemoOrderState.persisted_at,
        )
        async with self.session_factory() as session:
            rows = (
                await session.scalars(
                    select(OkxDemoOrderState)
                    .where(timestamp >= start, timestamp < end)
                    .order_by(timestamp.asc())
                    .limit(limit)
                )
            ).all()
        return [
            DemoOrderPerformanceSample(
                order_id=row.order_id,
                client_order_id=row.client_order_id,
                instrument_id=row.instrument_id,
                side=row.side,
                state=row.state,
                size=row.size,
                filled_size=row.accumulated_fill_size,
                requested_price=row.price,
                average_fill_price=row.average_fill_price,
                reduce_only=row.reduce_only,
                created_at=row.exchange_created_at,
                updated_at=row.exchange_updated_at or row.persisted_at,
                raw=dict(row.raw or {}),
            )
            for row in rows
        ]

    async def automation_runs_between(
        self, start: datetime, end: datetime, *, limit: int
    ) -> list[DemoAutomationRunResult]:
        async with self.session_factory() as session:
            rows = (
                await session.scalars(
                    select(DemoAutomationRun)
                    .where(
                        DemoAutomationRun.completed_at >= start,
                        DemoAutomationRun.completed_at < end,
                    )
                    .order_by(DemoAutomationRun.completed_at.asc())
                    .limit(limit)
                )
            ).all()
        return [DemoAutomationRunResult.model_validate(row.result) for row in rows]

    async def strategy_controls(self) -> list[DemoStrategyControlView]:
        async with self.session_factory() as session:
            rows = (
                await session.scalars(
                    select(DemoStrategyControl).order_by(DemoStrategyControl.strategy.asc())
                )
            ).all()
        return [self._control_view(row) for row in rows]

    async def disabled_strategies(self) -> set[str]:
        async with self.session_factory() as session:
            values = (
                await session.scalars(
                    select(DemoStrategyControl.strategy).where(
                        DemoStrategyControl.enabled.is_(False)
                    )
                )
            ).all()
        return set(values)

    async def set_strategy_enabled(
        self,
        *,
        strategy: str,
        enabled: bool,
        reason: str,
        actor: str,
    ) -> DemoStrategyControlView:
        now = datetime.now(timezone.utc)
        values = {
            "strategy": strategy,
            "enabled": enabled,
            "reason": reason,
            "updated_by": actor,
            "disabled_at": None if enabled else now,
            "updated_at": now,
        }
        async with self.session_factory() as session:
            async with session.begin():
                stmt = pg_insert(DemoStrategyControl).values(**values)
                await session.execute(
                    stmt.on_conflict_do_update(
                        index_elements=[DemoStrategyControl.strategy],
                        set_={key: value for key, value in values.items() if key != "strategy"},
                    )
                )
            row = await session.get(DemoStrategyControl, strategy)
        if row is None:
            raise RuntimeError("strategy_control_persistence_failed")
        return self._control_view(row)

    async def upsert_daily_report(
        self, report: DemoDailyPerformanceReport
    ) -> DemoDailyPerformanceReport:
        values = {
            "report_date": report.report_date,
            "opening_equity": report.opening_equity,
            "closing_equity": report.closing_equity,
            "net_equity_change": report.net_equity_change,
            "realized_pnl": report.realized_pnl,
            "fees": report.fees,
            "rebates": report.rebates,
            "funding_fees": report.funding_fees,
            "net_after_costs": report.net_after_costs,
            "order_count": report.order_count,
            "filled_order_count": report.filled_order_count,
            "realized_trade_count": report.realized_trade_count,
            "wins": report.wins,
            "losses": report.losses,
            "breakeven": report.breakeven,
            "win_rate": report.win_rate,
            "profit_factor": report.profit_factor,
            "average_adverse_slippage_bps": report.average_adverse_slippage_bps,
            "max_adverse_slippage_bps": report.max_adverse_slippage_bps,
            "max_drawdown_pct": report.max_drawdown_pct,
            "strategy_stats": [item.model_dump(mode="json") for item in report.strategy_stats],
            "alerts": [item.model_dump(mode="json") for item in report.alerts],
            "generated_at": report.generated_at,
            "updated_at": datetime.now(timezone.utc),
        }
        async with self.session_factory() as session:
            async with session.begin():
                stmt = pg_insert(DemoDailyPerformanceReportRow).values(**values)
                await session.execute(
                    stmt.on_conflict_do_update(
                        index_elements=[DemoDailyPerformanceReportRow.report_date],
                        set_={key: value for key, value in values.items() if key != "report_date"},
                    )
                )
        return report

    async def daily_report(self, report_date: date) -> DemoDailyPerformanceReport | None:
        async with self.session_factory() as session:
            row = await session.get(DemoDailyPerformanceReportRow, report_date)
        if row is None:
            return None
        return DemoDailyPerformanceReport(
            report_date=row.report_date,
            opening_equity=row.opening_equity,
            closing_equity=row.closing_equity,
            net_equity_change=row.net_equity_change,
            realized_pnl=row.realized_pnl,
            fees=row.fees,
            rebates=row.rebates,
            funding_fees=row.funding_fees,
            net_after_costs=row.net_after_costs,
            order_count=row.order_count,
            filled_order_count=row.filled_order_count,
            realized_trade_count=row.realized_trade_count,
            wins=row.wins,
            losses=row.losses,
            breakeven=row.breakeven,
            win_rate=row.win_rate,
            profit_factor=row.profit_factor,
            average_adverse_slippage_bps=row.average_adverse_slippage_bps,
            max_adverse_slippage_bps=row.max_adverse_slippage_bps,
            max_drawdown_pct=row.max_drawdown_pct,
            strategy_stats=row.strategy_stats or [],
            alerts=row.alerts or [],
            generated_at=row.generated_at,
        )

    @staticmethod
    def _control_view(row: DemoStrategyControl) -> DemoStrategyControlView:
        return DemoStrategyControlView(
            strategy=row.strategy,
            enabled=row.enabled,
            reason=row.reason,
            updated_by=row.updated_by,
            disabled_at=row.disabled_at,
            updated_at=row.updated_at,
        )
