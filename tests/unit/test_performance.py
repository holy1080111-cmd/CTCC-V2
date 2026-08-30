from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.config.settings import Settings
from app.domain.demo_automation import (
    DemoAutomationRunResult,
    DemoAutomationSymbolResult,
)
from app.domain.performance import (
    DISABLE_STRATEGY_PHRASE,
    ENABLE_STRATEGY_PHRASE,
    DemoEquityPoint,
    DemoOrderPerformanceSample,
    DemoStrategyControlView,
    DemoTradeAttribution,
    StrategyControlRequest,
)
from app.performance.service import DemoPerformanceError, DemoPerformanceService

D = Decimal


class FakeRepository:
    def __init__(
        self,
        *,
        snapshots=None,
        orders=None,
        runs=None,
        attributions=None,
        controls=None,
    ) -> None:
        self.snapshots = snapshots or []
        self.orders = orders or []
        self.runs = runs or []
        self.attributions = attributions or []
        self.controls = {item.strategy: item for item in (controls or [])}
        self.saved_report = None

    async def snapshots_between(self, start, end, *, limit):
        return [item for item in self.snapshots if start <= item.captured_at < end][:limit]

    async def orders_between(self, start, end, *, limit):
        return [
            item
            for item in self.orders
            if item.updated_at is not None and start <= item.updated_at < end
        ][:limit]

    async def automation_runs_between(self, start, end, *, limit):
        return [item for item in self.runs if start <= item.completed_at < end][:limit]

    async def trade_attributions_between(self, start, end, *, limit):
        return [
            item
            for item in self.attributions
            if start <= item.closed_at < end
        ][:limit]

    async def strategy_controls(self):
        return list(self.controls.values())

    async def disabled_strategies(self):
        return {name for name, item in self.controls.items() if not item.enabled}

    async def set_strategy_enabled(self, *, strategy, enabled, reason, actor):
        item = DemoStrategyControlView(
            strategy=strategy,
            enabled=enabled,
            reason=reason,
            updated_by=actor,
            disabled_at=None if enabled else datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        self.controls[strategy] = item
        return item

    async def upsert_daily_report(self, report):
        self.saved_report = report
        return report

    async def daily_report(self, report_date):
        if self.saved_report and self.saved_report.report_date == report_date:
            return self.saved_report
        return None


class FakeDemoService:
    async def reconcile(self):
        now = datetime.now(timezone.utc)
        return SimpleNamespace(
            reconciled_at=now,
            account_config=SimpleNamespace(account_level="2"),
            balance=SimpleNamespace(
                total_equity=D("1000"),
                available_equity=D("900"),
                adjusted_equity=D("0"),
                details=[
                    SimpleNamespace(
                        currency="USDT",
                        equity=D("500"),
                        available_equity=D("450"),
                        unrealized_pnl=D("5"),
                    )
                ],
            ),
            positions=[object()],
            pending_orders=[],
            pending_algo_orders=[object()],
        )


def settings(**updates) -> Settings:
    values = dict(
        environment="test",
        okx_demo_performance_window_days=30,
        okx_demo_performance_snapshot_retention_days=90,
        okx_demo_performance_min_active_days=2,
        okx_demo_performance_min_realized_trades=2,
        okx_demo_strategy_review_min_trades=1,
        okx_demo_strategy_review_min_win_rate="0.60",
    )
    values.update(updates)
    return Settings(_env_file=None, **values)


def performance_data():
    now = datetime.now(timezone.utc)
    snapshots = [
        DemoEquityPoint(
            captured_at=now - timedelta(days=2),
            total_equity=D("1000"),
            available_equity=D("1000"),
            performance_equity=D("1000"),
            performance_available_equity=D("1000"),
            equity_basis="single_currency:USDT",
            equity_currency="USDT",
        ),
        DemoEquityPoint(
            captured_at=now - timedelta(days=1),
            total_equity=D("900"),
            available_equity=D("900"),
            performance_equity=D("900"),
            performance_available_equity=D("900"),
            equity_basis="single_currency:USDT",
            equity_currency="USDT",
        ),
        DemoEquityPoint(
            captured_at=now - timedelta(hours=1),
            total_equity=D("1050"),
            available_equity=D("1050"),
            performance_equity=D("1050"),
            performance_available_equity=D("1050"),
            equity_basis="single_currency:USDT",
            equity_currency="USDT",
        ),
    ]
    orders = [
        DemoOrderPerformanceSample(
            order_id="1",
            client_order_id="AUT1",
            instrument_id="BTC-USDT-SWAP",
            side="buy",
            state="filled",
            size=D("1"),
            filled_size=D("1"),
            average_fill_price=D("101"),
            updated_at=now - timedelta(days=1),
            raw={"pnl": "10", "fee": "-1", "fundingFee": "0.5"},
        ),
        DemoOrderPerformanceSample(
            order_id="2",
            client_order_id="AUT2",
            instrument_id="ETH-USDT-SWAP",
            side="sell",
            state="filled",
            size=D("1"),
            filled_size=D("1"),
            average_fill_price=D("199"),
            updated_at=now - timedelta(hours=2),
            raw={"pnl": "-4", "fee": "-0.5"},
        ),
    ]
    run = DemoAutomationRunResult(
        trigger="scheduled",
        execute=True,
        started_at=now - timedelta(days=1),
        completed_at=now - timedelta(hours=1),
        results=[
            DemoAutomationSymbolResult(
                symbol="BTC-USDT-SWAP",
                instrument_id="BTC-USDT-SWAP",
                outcome="submitted",
                direction="long",
                strategy="trend_pullback",
                reference_price=D("100"),
                client_order_id="AUT1",
                detail="test",
            ),
            DemoAutomationSymbolResult(
                symbol="ETH-USDT-SWAP",
                instrument_id="ETH-USDT-SWAP",
                outcome="submitted",
                direction="short",
                strategy="breakout_continuation",
                reference_price=D("200"),
                client_order_id="AUT2",
                detail="test",
            ),
        ],
    )
    return snapshots, orders, [run]


@pytest.mark.asyncio
async def test_summary_calculates_costs_slippage_and_drawdown() -> None:
    snapshots, orders, runs = performance_data()
    service = DemoPerformanceService(
        settings=settings(),
        repository=FakeRepository(snapshots=snapshots, orders=orders, runs=runs),
    )
    summary = await service.summary(30)
    assert summary.realized_trade_count == 2
    assert summary.wins == 1
    assert summary.losses == 1
    assert summary.realized_pnl == D("6")
    assert summary.fees == D("1.5")
    assert summary.funding_fees == D("0.5")
    assert summary.net_after_costs == D("5")
    assert summary.average_adverse_slippage_bps == D("75")
    assert summary.max_adverse_slippage_bps == D("100")
    assert summary.max_drawdown_pct == D("0.1")
    assert "average_slippage_high" in {item.code for item in summary.alerts}


@pytest.mark.asyncio
async def test_strategy_review_is_recommended_after_bad_sample() -> None:
    snapshots, orders, runs = performance_data()
    service = DemoPerformanceService(
        settings=settings(),
        repository=FakeRepository(snapshots=snapshots, orders=orders, runs=runs),
    )
    summary = await service.summary(30)
    stats = {item.strategy: item for item in summary.strategy_stats}
    assert stats["breakout_continuation"].review_recommended is True
    assert "net_after_costs_negative" in stats["breakout_continuation"].review_reasons


@pytest.mark.asyncio
async def test_validation_requires_coverage_and_thresholds() -> None:
    snapshots, orders, runs = performance_data()
    service = DemoPerformanceService(
        settings=settings(okx_demo_performance_max_drawdown_pct="0.05"),
        repository=FakeRepository(snapshots=snapshots, orders=orders, runs=runs),
    )
    validation = await service.validation(30)
    assert validation.data_coverage_ready is True
    assert validation.reliability_ready is False
    assert "average_slippage_exceeds_limit" in validation.blockers
    assert "max_drawdown_exceeds_limit" in validation.blockers


@pytest.mark.asyncio
async def test_daily_report_is_persisted() -> None:
    now = datetime.now(timezone.utc)
    snapshot = DemoEquityPoint(
        captured_at=now,
        total_equity=D("1000"),
        available_equity=D("1000"),
        performance_equity=D("1000"),
        performance_available_equity=D("1000"),
        equity_basis="single_currency:USDT",
        equity_currency="USDT",
    )
    repo = FakeRepository(snapshots=[snapshot])
    service = DemoPerformanceService(settings=settings(), repository=repo)
    report = await service.daily_report(now.date())
    assert report.report_date == now.date()
    assert repo.saved_report is report


@pytest.mark.asyncio
async def test_strategy_disable_and_enable_require_exact_confirmation() -> None:
    repo = FakeRepository()
    service = DemoPerformanceService(settings=settings(), repository=repo)
    with pytest.raises(DemoPerformanceError):
        await service.disable_strategy(
            "trend_pullback",
            StrategyControlRequest(confirmation="wrong", reason="unit test"),
        )
    disabled = await service.disable_strategy(
        "trend_pullback",
        StrategyControlRequest(
            confirmation=DISABLE_STRATEGY_PHRASE,
            reason="unit test review",
        ),
    )
    assert disabled.enabled is False
    enabled = await service.enable_strategy(
        "trend_pullback",
        StrategyControlRequest(
            confirmation=ENABLE_STRATEGY_PHRASE,
            reason="unit test restored",
        ),
    )
    assert enabled.enabled is True


@pytest.mark.asyncio
async def test_unknown_strategy_is_rejected() -> None:
    service = DemoPerformanceService(settings=settings(), repository=FakeRepository())
    with pytest.raises(DemoPerformanceError, match="unknown_strategy"):
        await service.disable_strategy(
            "not-a-strategy",
            StrategyControlRequest(
                confirmation=DISABLE_STRATEGY_PHRASE,
                reason="unit test",
            ),
        )


@pytest.mark.asyncio
async def test_capture_snapshot_is_read_only_reconcile() -> None:
    service = DemoPerformanceService(
        settings=settings(),
        repository=FakeRepository(),
        demo_service=FakeDemoService(),
    )
    point = await service.capture_snapshot()
    assert point.total_equity == D("1000")
    assert point.performance_equity == D("500")
    assert point.equity_basis == "single_currency:USDT"
    assert point.unrealized_pnl == D("5")
    assert point.position_count == 1
    assert point.algo_order_count == 1


@pytest.mark.asyncio
async def test_multi_asset_total_equity_drawdown_is_not_strategy_drawdown() -> None:
    now = datetime.now(timezone.utc)
    snapshots = [
        DemoEquityPoint(
            captured_at=now - timedelta(hours=2),
            total_equity=D("98184.44"),
            available_equity=D("0"),
            performance_equity=D("5000"),
            performance_available_equity=D("5000"),
            equity_basis="single_currency:USDT",
            equity_currency="USDT",
        ),
        DemoEquityPoint(
            captured_at=now - timedelta(hours=1),
            total_equity=D("95161.01"),
            available_equity=D("0"),
            performance_equity=D("5000"),
            performance_available_equity=D("5000"),
            equity_basis="single_currency:USDT",
            equity_currency="USDT",
        ),
    ]
    service = DemoPerformanceService(
        settings=settings(),
        repository=FakeRepository(snapshots=snapshots),
    )

    summary = await service.summary(30)

    assert summary.opening_equity == D("5000")
    assert summary.closing_equity == D("5000")
    assert summary.max_drawdown_pct == D("0")
    assert summary.account_opening_equity == D("98184.44")
    assert summary.account_closing_equity == D("95161.01")
    assert summary.account_max_drawdown_pct == D("3023.43") / D("98184.44")
    assert "performance_drawdown_limit_exceeded" not in {
        item.code for item in summary.alerts
    }
    assert "account_equity_drawdown_limit_exceeded" in {
        item.code for item in summary.alerts
    }


@pytest.mark.asyncio
async def test_legacy_unbased_snapshots_fail_closed_without_fake_backfill() -> None:
    now = datetime.now(timezone.utc)
    snapshot = DemoEquityPoint(
        captured_at=now - timedelta(hours=1),
        total_equity=D("95161.01"),
        available_equity=D("0"),
    )
    service = DemoPerformanceService(
        settings=settings(
            okx_demo_performance_min_active_days=1,
            okx_demo_performance_min_realized_trades=1,
        ),
        repository=FakeRepository(snapshots=[snapshot]),
    )

    validation = await service.validation(30)

    assert validation.performance_snapshot_count == 0
    assert validation.excluded_snapshot_count == 1
    assert validation.data_coverage_ready is False
    assert validation.reliability_ready is False
    assert "performance_equity_unavailable" in validation.blockers


@pytest.mark.asyncio
async def test_durable_closing_order_attribution_reaches_strategy_stats() -> None:
    now = datetime.now(timezone.utc)
    snapshot = DemoEquityPoint(
        captured_at=now - timedelta(hours=2),
        total_equity=D("95000"),
        available_equity=D("0"),
        performance_equity=D("5000"),
        performance_available_equity=D("5000"),
        equity_basis="single_currency:USDT",
        equity_currency="USDT",
    )
    order = DemoOrderPerformanceSample(
        order_id="close-1",
        instrument_id="BTC-USDT-SWAP",
        side="sell",
        state="filled",
        size=D("1"),
        filled_size=D("1"),
        reduce_only=True,
        updated_at=now - timedelta(hours=1),
        raw={"pnl": "10", "fee": "-1"},
    )
    attribution = DemoTradeAttribution(
        event_id="event-1",
        instrument_id="BTC-USDT-SWAP",
        strategy="trend_pullback",
        reference_price=D("100"),
        closing_order_ids=["close-1"],
        closed_at=now - timedelta(hours=1),
        net_pnl=D("9"),
    )
    service = DemoPerformanceService(
        settings=settings(),
        repository=FakeRepository(
            snapshots=[snapshot],
            orders=[order],
            attributions=[attribution],
        ),
    )

    summary = await service.summary(30)
    stats = {item.strategy: item for item in summary.strategy_stats}

    assert summary.attributed_realized_trade_count == 1
    assert summary.unattributed_realized_trade_count == 0
    assert stats["trend_pullback"].realized_trades == 1
    assert "unattributed" not in stats


@pytest.mark.asyncio
async def test_equity_basis_change_restarts_the_performance_evidence_window() -> None:
    now = datetime.now(timezone.utc)
    snapshots = [
        DemoEquityPoint(
            captured_at=now - timedelta(days=2),
            total_equity=D("90000"),
            available_equity=D("0"),
            performance_equity=D("5000"),
            equity_basis="single_currency:USDT",
            equity_currency="USDT",
        ),
        DemoEquityPoint(
            captured_at=now - timedelta(days=1),
            total_equity=D("91000"),
            available_equity=D("0"),
            performance_equity=D("5100"),
            equity_basis="single_currency:USDT",
            equity_currency="USDT",
        ),
        DemoEquityPoint(
            captured_at=now - timedelta(hours=1),
            total_equity=D("92000"),
            available_equity=D("1000"),
            performance_equity=D("80000"),
            equity_basis="account_adjusted:USD",
            equity_currency="USD",
        ),
    ]
    old_order = DemoOrderPerformanceSample(
        order_id="old-close",
        instrument_id="BTC-USDT-SWAP",
        side="sell",
        state="filled",
        size=D("1"),
        filled_size=D("1"),
        reduce_only=True,
        updated_at=now - timedelta(hours=2),
        raw={"pnl": "100"},
    )
    service = DemoPerformanceService(
        settings=settings(),
        repository=FakeRepository(snapshots=snapshots, orders=[old_order]),
    )

    summary = await service.summary(30)

    assert summary.equity_basis == "account_adjusted:USD"
    assert summary.performance_snapshot_count == 1
    assert summary.excluded_snapshot_count == 2
    assert summary.performance_window_started_at == snapshots[-1].captured_at
    assert summary.opening_equity == D("80000")
    assert summary.order_count == 0


@pytest.mark.asyncio
async def test_unattributed_realized_trade_cannot_satisfy_reliability_gate() -> None:
    now = datetime.now(timezone.utc)
    snapshots = [
        DemoEquityPoint(
            captured_at=now - timedelta(days=1),
            total_equity=D("95000"),
            available_equity=D("0"),
            performance_equity=D("5000"),
            equity_basis="single_currency:USDT",
            equity_currency="USDT",
        ),
        DemoEquityPoint(
            captured_at=now - timedelta(hours=1),
            total_equity=D("95010"),
            available_equity=D("0"),
            performance_equity=D("5010"),
            equity_basis="single_currency:USDT",
            equity_currency="USDT",
        ),
    ]
    order = DemoOrderPerformanceSample(
        order_id="unknown-close",
        instrument_id="BTC-USDT-SWAP",
        side="sell",
        state="filled",
        size=D("1"),
        filled_size=D("1"),
        reduce_only=True,
        updated_at=now - timedelta(hours=2),
        raw={"pnl": "10"},
    )
    service = DemoPerformanceService(
        settings=settings(
            okx_demo_performance_min_active_days=1,
            okx_demo_performance_min_realized_trades=1,
        ),
        repository=FakeRepository(snapshots=snapshots, orders=[order]),
    )

    validation = await service.validation(30)

    assert validation.total_realized_trades == 1
    assert validation.realized_trades == 0
    assert validation.unattributed_realized_trades == 1
    assert validation.data_coverage_ready is False
    assert validation.reliability_ready is False
    assert "insufficient_realized_trades" in validation.blockers
    assert "unattributed_realized_trades" in validation.blockers
