from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Iterable

from app.config.settings import Settings, get_settings
from app.database.repositories.performance import DemoPerformanceRepository
from app.domain.demo_automation import DemoAutomationRunResult
from app.domain.performance import (
    DISABLE_STRATEGY_PHRASE,
    ENABLE_STRATEGY_PHRASE,
    DemoDailyPerformanceReport,
    DemoEquityPoint,
    DemoOrderPerformanceSample,
    DemoPerformanceAlert,
    DemoPerformanceSummary,
    DemoReliabilityValidation,
    DemoStrategyControlView,
    DemoStrategyPerformance,
    DemoTradeAttribution,
    StrategyControlRequest,
)
from app.okx_demo.equity import resolve_demo_risk_capital
from app.strategies.registry import STRATEGY_NAMES

D = Decimal


class DemoPerformanceError(RuntimeError):
    pass


class DemoPerformanceService:
    """Read-only Demo reliability and performance analytics.

    The service derives statistics from the local OKX Demo mirror and durable
    automation runs. It never enables execution, arms automation, closes a
    position, or claims that a small Demo sample proves profitability.
    """

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        repository: DemoPerformanceRepository | None = None,
        demo_service=None,
    ) -> None:
        self.settings = settings or get_settings()
        self.repository = repository
        if repository is None and self.settings.environment != "test":
            from app.database.session import AsyncSessionFactory

            self.repository = DemoPerformanceRepository(AsyncSessionFactory)
        if demo_service is None and self.settings.environment != "test":
            from app.okx_demo.service import okx_demo_service

            demo_service = okx_demo_service
        self.demo_service = demo_service

    async def capture_snapshot(self) -> DemoEquityPoint:
        if self.demo_service is None:
            raise DemoPerformanceError("okx_demo_service_unavailable")
        snapshot = await self.demo_service.reconcile()
        performance_capital, _ = resolve_demo_risk_capital(
            snapshot.account_config,
            snapshot.balance,
        )
        return DemoEquityPoint(
            captured_at=snapshot.reconciled_at,
            total_equity=snapshot.balance.total_equity,
            available_equity=snapshot.balance.available_equity,
            performance_equity=(
                performance_capital.risk_equity
                if performance_capital is not None
                else None
            ),
            performance_available_equity=(
                performance_capital.available_equity
                if performance_capital is not None
                else None
            ),
            equity_basis=(
                performance_capital.basis
                if performance_capital is not None
                else None
            ),
            equity_currency=(
                performance_capital.currency
                if performance_capital is not None
                else None
            ),
            unrealized_pnl=sum(
                (item.unrealized_pnl for item in snapshot.balance.details), D("0")
            ),
            position_count=len(snapshot.positions),
            pending_order_count=len(snapshot.pending_orders),
            algo_order_count=len(snapshot.pending_algo_orders),
        )

    async def strategy_controls(self) -> list[DemoStrategyControlView]:
        persisted: dict[str, DemoStrategyControlView] = {}
        if self.repository is not None:
            persisted = {
                item.strategy: item for item in await self.repository.strategy_controls()
            }
        return [
            persisted.get(name)
            or DemoStrategyControlView(
                strategy=name,
                enabled=True,
                reason=None,
                updated_by="default",
            )
            for name in STRATEGY_NAMES
        ]

    async def disabled_strategies(self) -> set[str]:
        if self.repository is None:
            return set()
        return await self.repository.disabled_strategies()

    async def disable_strategy(
        self, strategy: str, request: StrategyControlRequest
    ) -> DemoStrategyControlView:
        if request.confirmation != DISABLE_STRATEGY_PHRASE:
            raise DemoPerformanceError(
                f"confirmation_must_equal_{DISABLE_STRATEGY_PHRASE}"
            )
        self._ensure_strategy(strategy)
        if self.repository is None:
            raise DemoPerformanceError("strategy_control_repository_unavailable")
        return await self.repository.set_strategy_enabled(
            strategy=strategy,
            enabled=False,
            reason=request.reason,
            actor=request.actor,
        )

    async def enable_strategy(
        self, strategy: str, request: StrategyControlRequest
    ) -> DemoStrategyControlView:
        if request.confirmation != ENABLE_STRATEGY_PHRASE:
            raise DemoPerformanceError(
                f"confirmation_must_equal_{ENABLE_STRATEGY_PHRASE}"
            )
        self._ensure_strategy(strategy)
        if self.repository is None:
            raise DemoPerformanceError("strategy_control_repository_unavailable")
        return await self.repository.set_strategy_enabled(
            strategy=strategy,
            enabled=True,
            reason=request.reason,
            actor=request.actor,
        )

    async def summary(self, window_days: int | None = None) -> DemoPerformanceSummary:
        days = window_days or self.settings.okx_demo_performance_window_days
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=days)
        return await self._summary_between(start, end, window_days=days)

    async def daily_report(
        self, report_date: date, *, refresh: bool = True
    ) -> DemoDailyPerformanceReport:
        if not refresh and self.repository is not None:
            stored = await self.repository.daily_report(report_date)
            if stored is not None:
                return stored
        start = datetime.combine(report_date, time.min, tzinfo=timezone.utc)
        end = start + timedelta(days=1)
        summary = await self._summary_between(start, end, window_days=1)
        report = DemoDailyPerformanceReport(
            report_date=report_date,
            performance_window_started_at=summary.performance_window_started_at,
            equity_basis=summary.equity_basis,
            equity_currency=summary.equity_currency,
            performance_snapshot_count=summary.performance_snapshot_count,
            excluded_snapshot_count=summary.excluded_snapshot_count,
            opening_equity=summary.opening_equity,
            closing_equity=summary.closing_equity,
            net_equity_change=summary.equity_change,
            realized_pnl=summary.realized_pnl,
            fees=summary.fees,
            rebates=summary.rebates,
            funding_fees=summary.funding_fees,
            net_after_costs=summary.net_after_costs,
            order_count=summary.order_count,
            filled_order_count=summary.filled_order_count,
            realized_trade_count=summary.realized_trade_count,
            attributed_realized_trade_count=(
                summary.attributed_realized_trade_count
            ),
            unattributed_realized_trade_count=(
                summary.unattributed_realized_trade_count
            ),
            wins=summary.wins,
            losses=summary.losses,
            breakeven=summary.breakeven,
            win_rate=summary.win_rate,
            profit_factor=summary.profit_factor,
            average_adverse_slippage_bps=summary.average_adverse_slippage_bps,
            max_adverse_slippage_bps=summary.max_adverse_slippage_bps,
            max_drawdown_pct=summary.max_drawdown_pct,
            account_opening_equity=summary.account_opening_equity,
            account_closing_equity=summary.account_closing_equity,
            account_equity_change=summary.account_equity_change,
            account_max_drawdown_pct=summary.account_max_drawdown_pct,
            strategy_stats=summary.strategy_stats,
            alerts=summary.alerts,
        )
        if self.repository is not None:
            await self.repository.upsert_daily_report(report)
        return report

    async def validation(
        self, window_days: int | None = None
    ) -> DemoReliabilityValidation:
        summary = await self.summary(window_days)
        blockers: list[str] = []
        warnings: list[str] = []
        min_days = self.settings.okx_demo_performance_min_active_days
        min_trades = self.settings.okx_demo_performance_min_realized_trades
        max_slippage = D(str(self.settings.okx_demo_performance_max_average_slippage_bps))
        min_pf = D(str(self.settings.okx_demo_performance_min_profit_factor))
        max_dd = D(str(self.settings.okx_demo_performance_max_drawdown_pct))

        if summary.equity_basis is None or summary.performance_snapshot_count == 0:
            blockers.append("performance_equity_unavailable")
        if summary.active_days < min_days:
            blockers.append("insufficient_active_days")
        if summary.attributed_realized_trade_count < min_trades:
            blockers.append("insufficient_realized_trades")
        if summary.unattributed_realized_trade_count:
            blockers.append("unattributed_realized_trades")
        if (
            summary.average_adverse_slippage_bps is not None
            and summary.average_adverse_slippage_bps > max_slippage
        ):
            blockers.append("average_slippage_exceeds_limit")
        elif summary.average_adverse_slippage_bps is None:
            warnings.append("no_slippage_samples")

        profit_factor_pass = False
        if summary.profit_factor is not None:
            profit_factor_pass = summary.profit_factor >= min_pf
            if not profit_factor_pass:
                blockers.append("profit_factor_below_threshold")
        elif summary.gross_loss == 0 and summary.gross_profit > 0:
            profit_factor_pass = True
            warnings.append("profit_factor_undefined_without_losses")
        else:
            blockers.append("profit_factor_unavailable")

        if summary.max_drawdown_pct > max_dd:
            blockers.append("max_drawdown_exceeds_limit")
        if summary.realized_trade_count and summary.net_after_costs < 0:
            warnings.append("window_net_after_costs_negative")
        if summary.excluded_snapshot_count:
            warnings.append("legacy_or_incompatible_equity_snapshots_excluded")

        data_ready = (
            summary.equity_basis is not None
            and summary.performance_snapshot_count > 0
            and summary.active_days >= min_days
            and summary.attributed_realized_trade_count >= min_trades
            and summary.unattributed_realized_trade_count == 0
        )
        reliability_ready = (
            data_ready
            and profit_factor_pass
            and summary.max_drawdown_pct <= max_dd
            and (
                summary.average_adverse_slippage_bps is None
                or summary.average_adverse_slippage_bps <= max_slippage
            )
            and not blockers
        )
        return DemoReliabilityValidation(
            window_days=summary.window_days,
            active_days=summary.active_days,
            minimum_active_days=min_days,
            realized_trades=summary.attributed_realized_trade_count,
            total_realized_trades=summary.realized_trade_count,
            unattributed_realized_trades=(
                summary.unattributed_realized_trade_count
            ),
            minimum_realized_trades=min_trades,
            average_adverse_slippage_bps=summary.average_adverse_slippage_bps,
            maximum_average_slippage_bps=max_slippage,
            profit_factor=summary.profit_factor,
            minimum_profit_factor=min_pf,
            max_drawdown_pct=summary.max_drawdown_pct,
            account_max_drawdown_pct=summary.account_max_drawdown_pct,
            maximum_drawdown_pct=max_dd,
            equity_basis=summary.equity_basis,
            performance_snapshot_count=summary.performance_snapshot_count,
            excluded_snapshot_count=summary.excluded_snapshot_count,
            data_coverage_ready=data_ready,
            reliability_ready=reliability_ready,
            blockers=sorted(set(blockers)),
            warnings=sorted(set(warnings)),
        )

    async def _summary_between(
        self, start: datetime, end: datetime, *, window_days: int
    ) -> DemoPerformanceSummary:
        if self.repository is None:
            raise DemoPerformanceError("performance_repository_unavailable")
        snapshots = await self.repository.snapshots_between(
            start,
            end,
            limit=self.settings.okx_demo_performance_snapshot_query_limit,
        )
        performance_snapshots = self._performance_segment(snapshots)
        performance_start = (
            performance_snapshots[0].captured_at
            if performance_snapshots
            else None
        )
        if performance_start is not None:
            orders = await self.repository.orders_between(
                performance_start,
                end,
                limit=self.settings.okx_demo_performance_order_query_limit,
            )
            runs = await self.repository.automation_runs_between(
                performance_start,
                end,
                limit=self.settings.okx_demo_observability_metrics_run_limit,
            )
            trade_attributions = await self.repository.trade_attributions_between(
                performance_start,
                end,
                limit=self.settings.okx_demo_performance_order_query_limit,
            )
        else:
            orders = []
            runs = []
            trade_attributions = []
        controls = await self.strategy_controls()
        control_map = {item.strategy: item for item in controls}
        run_map = self._run_attribution(runs)
        durable_map = self._durable_attribution(trade_attributions)

        decorated = [self._decorate_order(item) for item in orders]
        filled = [item for item in decorated if item.filled_size > 0]
        decorated_with_attribution = [
            (
                item,
                self._order_attribution(
                    item,
                    run_map=run_map,
                    durable_map=durable_map,
                ),
            )
            for item in decorated
        ]
        realized_with_attribution = [
            (item, attribution)
            for item, attribution in decorated_with_attribution
            if item.realized_pnl is not None
        ]
        realized = [item for item, _ in realized_with_attribution]
        attributed_realized = sum(
            attribution is not None
            for _, attribution in realized_with_attribution
        )
        unattributed_realized = len(realized) - attributed_realized
        net_trade_values = [self._net_trade(item) for item in realized]
        wins = sum(value > 0 for value in net_trade_values)
        losses = sum(value < 0 for value in net_trade_values)
        breakeven = sum(value == 0 for value in net_trade_values)
        gross_profit = sum((value for value in net_trade_values if value > 0), D("0"))
        gross_loss = sum((value for value in net_trade_values if value < 0), D("0"))
        realized_pnl = sum((item.realized_pnl or D("0") for item in realized), D("0"))
        fees = sum((item.fee for item in decorated), D("0"))
        rebates = sum((item.rebate for item in decorated), D("0"))
        funding = sum((item.funding_fee for item in decorated), D("0"))
        net_after_costs = realized_pnl - fees + rebates + funding
        count = len(net_trade_values)
        win_rate = D(wins) / D(count) if count else None
        profit_factor = gross_profit / abs(gross_loss) if gross_loss < 0 else None
        average_win = gross_profit / D(wins) if wins else None
        average_loss = gross_loss / D(losses) if losses else None
        expectancy = sum(net_trade_values, D("0")) / D(count) if count else None

        slippage: list[Decimal] = []
        strategy_buckets: dict[
            str,
            list[
                tuple[
                    DemoOrderPerformanceSample,
                    tuple[str, Decimal | None] | None,
                ]
            ],
        ] = defaultdict(list)
        submitted_counts: dict[str, int] = defaultdict(int)
        for run in runs:
            for result in run.results:
                if result.strategy and result.outcome == "submitted":
                    submitted_counts[result.strategy] += 1

        for order, attribution in decorated_with_attribution:
            strategy = attribution[0] if attribution else "unattributed"
            strategy_buckets[strategy].append((order, attribution))
            if (
                attribution
                and attribution[1] is not None
                and order.average_fill_price is not None
            ):
                reference = attribution[1]
                value = self._adverse_slippage_bps(
                    side=order.side,
                    reference=reference,
                    fill=order.average_fill_price,
                )
                if value is not None:
                    slippage.append(value)

        strategies = set(STRATEGY_NAMES) | set(strategy_buckets) | set(submitted_counts)
        strategy_stats: list[DemoStrategyPerformance] = []
        for strategy in sorted(strategies):
            bucket = strategy_buckets.get(strategy, [])
            bucket_realized = [
                item for item, _ in bucket if item.realized_pnl is not None
            ]
            values = [self._net_trade(item) for item in bucket_realized]
            strategy_wins = sum(value > 0 for value in values)
            strategy_losses = sum(value < 0 for value in values)
            strategy_breakeven = sum(value == 0 for value in values)
            strategy_gross_profit = sum((v for v in values if v > 0), D("0"))
            strategy_gross_loss = sum((v for v in values if v < 0), D("0"))
            strategy_net = sum(values, D("0"))
            strategy_slippage = []
            for item, attribution in bucket:
                if (
                    attribution
                    and attribution[1] is not None
                    and item.average_fill_price is not None
                ):
                    parsed = self._adverse_slippage_bps(
                        side=item.side,
                        reference=attribution[1],
                        fill=item.average_fill_price,
                    )
                    if parsed is not None:
                        strategy_slippage.append(parsed)
            realized_count = len(values)
            rate = D(strategy_wins) / D(realized_count) if realized_count else None
            review: list[str] = []
            if realized_count >= self.settings.okx_demo_strategy_review_min_trades:
                if rate is not None and rate < D(
                    str(self.settings.okx_demo_strategy_review_min_win_rate)
                ):
                    review.append("win_rate_below_review_threshold")
                if strategy_net < 0:
                    review.append("net_after_costs_negative")
            control = control_map.get(strategy)
            strategy_stats.append(
                DemoStrategyPerformance(
                    strategy=strategy,
                    enabled=control.enabled if control is not None else True,
                    submitted_orders=submitted_counts.get(strategy, 0),
                    filled_orders=sum(
                        item.filled_size > 0 for item, _ in bucket
                    ),
                    realized_trades=realized_count,
                    wins=strategy_wins,
                    losses=strategy_losses,
                    breakeven=strategy_breakeven,
                    win_rate=rate,
                    gross_profit=strategy_gross_profit,
                    gross_loss=strategy_gross_loss,
                    net_after_costs=strategy_net,
                    average_adverse_slippage_bps=(
                        sum(strategy_slippage, D("0")) / D(len(strategy_slippage))
                        if strategy_slippage
                        else None
                    ),
                    review_recommended=bool(review),
                    review_reasons=review,
                )
            )

        opening = (
            performance_snapshots[0].performance_equity
            if performance_snapshots
            else None
        )
        closing = (
            performance_snapshots[-1].performance_equity
            if performance_snapshots
            else None
        )
        drawdown = self._max_drawdown(
            performance_snapshots,
            performance_equity=True,
        )
        account_opening = snapshots[0].total_equity if snapshots else None
        account_closing = snapshots[-1].total_equity if snapshots else None
        account_drawdown = self._max_drawdown(snapshots)
        active_dates = {
            item.captured_at.date() for item in performance_snapshots
        }
        active_dates.update(
            item.updated_at.date() for item in decorated if item.updated_at is not None
        )
        avg_slippage = sum(slippage, D("0")) / D(len(slippage)) if slippage else None
        max_slippage = max(slippage) if slippage else None

        summary = DemoPerformanceSummary(
            window_days=window_days,
            window_started_at=start,
            window_ended_at=end,
            active_days=len(active_dates),
            snapshot_count=len(snapshots),
            performance_snapshot_count=len(performance_snapshots),
            excluded_snapshot_count=len(snapshots) - len(performance_snapshots),
            performance_window_started_at=performance_start,
            equity_basis=(
                performance_snapshots[0].equity_basis
                if performance_snapshots
                else None
            ),
            equity_currency=(
                performance_snapshots[0].equity_currency
                if performance_snapshots
                else None
            ),
            order_count=len(decorated),
            filled_order_count=len(filled),
            realized_trade_count=count,
            attributed_realized_trade_count=attributed_realized,
            unattributed_realized_trade_count=unattributed_realized,
            wins=wins,
            losses=losses,
            breakeven=breakeven,
            win_rate=win_rate,
            gross_profit=gross_profit,
            gross_loss=gross_loss,
            realized_pnl=realized_pnl,
            fees=fees,
            rebates=rebates,
            funding_fees=funding,
            net_after_costs=net_after_costs,
            profit_factor=profit_factor,
            average_win=average_win,
            average_loss=average_loss,
            expectancy=expectancy,
            opening_equity=opening,
            closing_equity=closing,
            equity_change=(closing - opening) if opening is not None and closing is not None else None,
            max_drawdown_pct=drawdown,
            account_opening_equity=account_opening,
            account_closing_equity=account_closing,
            account_equity_change=(
                account_closing - account_opening
                if account_opening is not None and account_closing is not None
                else None
            ),
            account_max_drawdown_pct=account_drawdown,
            slippage_sample_count=len(slippage),
            average_adverse_slippage_bps=avg_slippage,
            max_adverse_slippage_bps=max_slippage,
            strategy_stats=strategy_stats,
        )
        summary.alerts = self._alerts(summary)
        return summary

    def _alerts(self, summary: DemoPerformanceSummary) -> list[DemoPerformanceAlert]:
        alerts: list[DemoPerformanceAlert] = []
        if summary.equity_basis is None:
            alerts.append(
                DemoPerformanceAlert(
                    severity="critical",
                    code="performance_equity_unavailable",
                    message=(
                        "No explicitly based Demo strategy-equity snapshots are "
                        "available for reliability validation."
                    ),
                )
            )
        if summary.excluded_snapshot_count:
            alerts.append(
                DemoPerformanceAlert(
                    severity="info",
                    code="legacy_or_incompatible_equity_snapshots_excluded",
                    message=(
                        "Legacy or incompatible equity snapshots were excluded "
                        "from strategy performance."
                    ),
                    value=str(summary.excluded_snapshot_count),
                )
            )
        if summary.unattributed_realized_trade_count:
            alerts.append(
                DemoPerformanceAlert(
                    severity="critical",
                    code="unattributed_realized_trades",
                    message=(
                        "One or more realized trades lack durable strategy "
                        "attribution and cannot support reliability validation."
                    ),
                    value=str(summary.unattributed_realized_trade_count),
                )
            )
        if summary.active_days < self.settings.okx_demo_performance_min_active_days:
            alerts.append(
                DemoPerformanceAlert(
                    severity="info",
                    code="insufficient_active_days",
                    message="More Demo observation days are required before reliability validation.",
                    value=str(summary.active_days),
                    threshold=str(self.settings.okx_demo_performance_min_active_days),
                )
            )
        if summary.realized_trade_count < self.settings.okx_demo_performance_min_realized_trades:
            alerts.append(
                DemoPerformanceAlert(
                    severity="info",
                    code="insufficient_realized_trades",
                    message="The closed-trade sample is too small for performance conclusions.",
                    value=str(summary.realized_trade_count),
                    threshold=str(self.settings.okx_demo_performance_min_realized_trades),
                )
            )
        max_slippage = D(str(self.settings.okx_demo_performance_max_average_slippage_bps))
        if (
            summary.average_adverse_slippage_bps is not None
            and summary.average_adverse_slippage_bps > max_slippage
        ):
            alerts.append(
                DemoPerformanceAlert(
                    severity="warning",
                    code="average_slippage_high",
                    message="Average adverse slippage exceeds the configured Demo threshold.",
                    value=str(summary.average_adverse_slippage_bps),
                    threshold=str(max_slippage),
                )
            )
        max_drawdown = D(str(self.settings.okx_demo_performance_max_drawdown_pct))
        if summary.max_drawdown_pct > max_drawdown:
            alerts.append(
                DemoPerformanceAlert(
                    severity="critical",
                    code="performance_drawdown_limit_exceeded",
                    message="Observed Demo equity drawdown exceeds the validation threshold.",
                    value=str(summary.max_drawdown_pct),
                    threshold=str(max_drawdown),
                )
            )
        if summary.account_max_drawdown_pct > max_drawdown:
            alerts.append(
                DemoPerformanceAlert(
                    severity="warning",
                    code="account_equity_drawdown_limit_exceeded",
                    message=(
                        "The multi-asset Demo account equity drawdown exceeds the "
                        "strategy threshold; it is monitored separately and does "
                        "not determine strategy reliability."
                    ),
                    value=str(summary.account_max_drawdown_pct),
                    threshold=str(max_drawdown),
                )
            )
        if summary.realized_trade_count and summary.net_after_costs < 0:
            alerts.append(
                DemoPerformanceAlert(
                    severity="warning",
                    code="net_after_costs_negative",
                    message="Realized Demo results are negative after recorded costs.",
                    value=str(summary.net_after_costs),
                )
            )
        review = [item.strategy for item in summary.strategy_stats if item.review_recommended]
        if review:
            alerts.append(
                DemoPerformanceAlert(
                    severity="warning",
                    code="strategy_review_recommended",
                    message="One or more strategies reached the configured review threshold.",
                    value=",".join(review),
                )
            )
        return alerts

    @classmethod
    def _decorate_order(
        cls, order: DemoOrderPerformanceSample
    ) -> DemoOrderPerformanceSample:
        raw = order.raw
        fee_signed = cls._first_decimal(raw, "fee", "fillFee") or D("0")
        fee_cost = abs(fee_signed) if fee_signed < 0 else D("0")
        rebate = cls._first_decimal(raw, "rebate", "fillFee") or D("0")
        if rebate < 0:
            rebate = D("0")
        funding = cls._first_decimal(raw, "fundingFee") or D("0")
        realized, present = cls._decimal_with_presence(
            raw, "pnl", "fillPnl", "realizedPnl"
        )
        closing_order = cls._is_closing_order(order)
        realized_value = realized if present and (realized != 0 or closing_order) else None
        return order.model_copy(
            update={
                "fee": fee_cost,
                "rebate": rebate,
                "funding_fee": funding,
                "realized_pnl": realized_value,
            }
        )

    @staticmethod
    def _is_closing_order(order: DemoOrderPerformanceSample) -> bool:
        if order.reduce_only:
            return True
        position_side = str(order.raw.get("posSide") or "").lower()
        side = order.side.lower()
        return (position_side == "long" and side == "sell") or (
            position_side == "short" and side == "buy"
        )

    @staticmethod
    def _net_trade(order: DemoOrderPerformanceSample) -> Decimal:
        return (
            (order.realized_pnl or D("0"))
            - order.fee
            + order.rebate
            + order.funding_fee
        )

    @staticmethod
    def _run_attribution(
        runs: Iterable[DemoAutomationRunResult],
    ) -> dict[str, tuple[str, Decimal]]:
        values: dict[str, tuple[str, Decimal]] = {}
        for run in runs:
            for result in run.results:
                if (
                    result.client_order_id
                    and result.strategy
                    and result.reference_price is not None
                ):
                    values[result.client_order_id] = (
                        result.strategy,
                        result.reference_price,
                    )
        return values

    @staticmethod
    def _durable_attribution(
        values: Iterable[DemoTradeAttribution],
    ) -> dict[tuple[str, str], tuple[str, Decimal | None]]:
        mapped: dict[tuple[str, str], tuple[str, Decimal | None]] = {}
        for item in values:
            if not item.strategy:
                continue
            if item.entry_client_order_id:
                mapped[("client", item.entry_client_order_id)] = (
                    item.strategy,
                    item.reference_price,
                )
            if item.entry_exchange_order_id:
                mapped[("order", item.entry_exchange_order_id)] = (
                    item.strategy,
                    item.reference_price,
                )
            for order_id in item.closing_order_ids:
                mapped[("order", order_id)] = (item.strategy, None)
        return mapped

    @staticmethod
    def _order_attribution(
        order: DemoOrderPerformanceSample,
        *,
        run_map: dict[str, tuple[str, Decimal]],
        durable_map: dict[tuple[str, str], tuple[str, Decimal | None]],
    ) -> tuple[str, Decimal | None] | None:
        attribution = durable_map.get(("order", order.order_id))
        if attribution is not None:
            return attribution
        if order.client_order_id:
            attribution = durable_map.get(("client", order.client_order_id))
            if attribution is not None:
                return attribution
            return run_map.get(order.client_order_id)
        return None

    @staticmethod
    def _performance_segment(
        points: list[DemoEquityPoint],
    ) -> list[DemoEquityPoint]:
        """Return the newest contiguous, consistently based equity segment."""

        if not points:
            return []
        latest = points[-1]
        if (
            latest.performance_equity is None
            or latest.performance_equity <= 0
            or not latest.equity_basis
            or not latest.equity_currency
        ):
            return []
        basis = latest.equity_basis
        currency = latest.equity_currency
        values: list[DemoEquityPoint] = []
        for point in reversed(points):
            if (
                point.performance_equity is None
                or point.performance_equity <= 0
                or point.equity_basis != basis
                or point.equity_currency != currency
            ):
                break
            values.append(point)
        return list(reversed(values))

    @staticmethod
    def _adverse_slippage_bps(
        *, side: str, reference: Decimal, fill: Decimal
    ) -> Decimal | None:
        if reference <= 0 or fill <= 0:
            return None
        if side.lower() == "buy":
            return (fill - reference) / reference * D("10000")
        if side.lower() == "sell":
            return (reference - fill) / reference * D("10000")
        return None

    @staticmethod
    def _max_drawdown(
        points: list[DemoEquityPoint],
        *,
        performance_equity: bool = False,
    ) -> Decimal:
        peak: Decimal | None = None
        maximum = D("0")
        for point in points:
            equity = (
                point.performance_equity
                if performance_equity
                else point.total_equity
            )
            if equity is None:
                continue
            peak = equity if peak is None else max(peak, equity)
            if peak > 0:
                maximum = max(maximum, (peak - equity) / peak)
        return maximum

    @staticmethod
    def _first_decimal(raw: dict, *keys: str) -> Decimal | None:
        for key in keys:
            value = raw.get(key)
            if value not in (None, ""):
                try:
                    return D(str(value))
                except (InvalidOperation, ValueError, TypeError):
                    continue
        return None

    @classmethod
    def _decimal_with_presence(
        cls, raw: dict, *keys: str
    ) -> tuple[Decimal, bool]:
        for key in keys:
            if key not in raw or raw.get(key) in (None, ""):
                continue
            value = cls._first_decimal(raw, key)
            return (value or D("0"), True)
        return D("0"), False

    @staticmethod
    def _ensure_strategy(strategy: str) -> None:
        if strategy not in STRATEGY_NAMES:
            raise DemoPerformanceError("unknown_strategy")
