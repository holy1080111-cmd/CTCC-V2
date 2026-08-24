from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

DISABLE_STRATEGY_PHRASE = "DISABLE_DEMO_STRATEGY"
ENABLE_STRATEGY_PHRASE = "ENABLE_DEMO_STRATEGY"
GENERATE_DAILY_REPORT_PHRASE = "GENERATE_DEMO_DAILY_REPORT"


class StrategyControlRequest(BaseModel):
    confirmation: str
    reason: str = Field(min_length=3, max_length=250)
    actor: str = Field(default="operator", min_length=1, max_length=80)


class DemoStrategyControlView(BaseModel):
    strategy: str
    enabled: bool = True
    reason: str | None = None
    updated_by: str = "system"
    disabled_at: datetime | None = None
    updated_at: datetime | None = None


class DemoEquityPoint(BaseModel):
    captured_at: datetime
    # Account-level USD equity remains visible for wallet-risk diagnostics.
    total_equity: Decimal
    available_equity: Decimal
    # Only these explicitly based fields may drive strategy reliability.
    performance_equity: Decimal | None = None
    performance_available_equity: Decimal | None = None
    equity_basis: str | None = None
    equity_currency: str | None = None
    unrealized_pnl: Decimal = Decimal("0")
    position_count: int = 0
    pending_order_count: int = 0
    algo_order_count: int = 0


class DemoOrderPerformanceSample(BaseModel):
    order_id: str
    client_order_id: str | None = None
    instrument_id: str
    side: str
    state: str
    size: Decimal
    filled_size: Decimal
    requested_price: Decimal | None = None
    average_fill_price: Decimal | None = None
    reduce_only: bool = False
    fee: Decimal = Decimal("0")
    rebate: Decimal = Decimal("0")
    funding_fee: Decimal = Decimal("0")
    realized_pnl: Decimal | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    raw: dict = Field(default_factory=dict)


class DemoTradeAttribution(BaseModel):
    event_id: str
    instrument_id: str
    closed_at: datetime
    net_pnl: Decimal
    strategy: str | None = None
    direction: str | None = None
    settlement_currency: str | None = None
    reference_price: Decimal | None = None
    entry_client_order_id: str | None = None
    entry_exchange_order_id: str | None = None
    protection_client_order_id: str | None = None
    closing_order_ids: list[str] = Field(default_factory=list)
    started_at: datetime | None = None


class DemoStrategyPerformance(BaseModel):
    strategy: str
    enabled: bool
    submitted_orders: int = 0
    filled_orders: int = 0
    realized_trades: int = 0
    wins: int = 0
    losses: int = 0
    breakeven: int = 0
    win_rate: Decimal | None = None
    gross_profit: Decimal = Decimal("0")
    gross_loss: Decimal = Decimal("0")
    net_after_costs: Decimal = Decimal("0")
    average_adverse_slippage_bps: Decimal | None = None
    review_recommended: bool = False
    review_reasons: list[str] = Field(default_factory=list)


class DemoPerformanceAlert(BaseModel):
    severity: Literal["info", "warning", "critical"]
    code: str
    message: str
    value: str | None = None
    threshold: str | None = None


class DemoPerformanceSummary(BaseModel):
    window_days: int
    window_started_at: datetime
    window_ended_at: datetime
    active_days: int = 0
    snapshot_count: int = 0
    performance_snapshot_count: int = 0
    excluded_snapshot_count: int = 0
    performance_window_started_at: datetime | None = None
    equity_basis: str | None = None
    equity_currency: str | None = None
    order_count: int = 0
    filled_order_count: int = 0
    realized_trade_count: int = 0
    attributed_realized_trade_count: int = 0
    unattributed_realized_trade_count: int = 0
    wins: int = 0
    losses: int = 0
    breakeven: int = 0
    win_rate: Decimal | None = None
    gross_profit: Decimal = Decimal("0")
    gross_loss: Decimal = Decimal("0")
    realized_pnl: Decimal = Decimal("0")
    fees: Decimal = Decimal("0")
    rebates: Decimal = Decimal("0")
    funding_fees: Decimal = Decimal("0")
    net_after_costs: Decimal = Decimal("0")
    profit_factor: Decimal | None = None
    average_win: Decimal | None = None
    average_loss: Decimal | None = None
    expectancy: Decimal | None = None
    opening_equity: Decimal | None = None
    closing_equity: Decimal | None = None
    equity_change: Decimal | None = None
    max_drawdown_pct: Decimal = Decimal("0")
    account_opening_equity: Decimal | None = None
    account_closing_equity: Decimal | None = None
    account_equity_change: Decimal | None = None
    account_max_drawdown_pct: Decimal = Decimal("0")
    slippage_sample_count: int = 0
    average_adverse_slippage_bps: Decimal | None = None
    max_adverse_slippage_bps: Decimal | None = None
    strategy_stats: list[DemoStrategyPerformance] = Field(default_factory=list)
    alerts: list[DemoPerformanceAlert] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class DemoDailyPerformanceReport(BaseModel):
    report_date: date
    performance_window_started_at: datetime | None = None
    equity_basis: str | None = None
    equity_currency: str | None = None
    performance_snapshot_count: int = 0
    excluded_snapshot_count: int = 0
    opening_equity: Decimal | None = None
    closing_equity: Decimal | None = None
    net_equity_change: Decimal | None = None
    realized_pnl: Decimal = Decimal("0")
    fees: Decimal = Decimal("0")
    rebates: Decimal = Decimal("0")
    funding_fees: Decimal = Decimal("0")
    net_after_costs: Decimal = Decimal("0")
    order_count: int = 0
    filled_order_count: int = 0
    realized_trade_count: int = 0
    attributed_realized_trade_count: int = 0
    unattributed_realized_trade_count: int = 0
    wins: int = 0
    losses: int = 0
    breakeven: int = 0
    win_rate: Decimal | None = None
    profit_factor: Decimal | None = None
    average_adverse_slippage_bps: Decimal | None = None
    max_adverse_slippage_bps: Decimal | None = None
    max_drawdown_pct: Decimal = Decimal("0")
    account_opening_equity: Decimal | None = None
    account_closing_equity: Decimal | None = None
    account_equity_change: Decimal | None = None
    account_max_drawdown_pct: Decimal = Decimal("0")
    strategy_stats: list[DemoStrategyPerformance] = Field(default_factory=list)
    alerts: list[DemoPerformanceAlert] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class DemoReliabilityValidation(BaseModel):
    window_days: int
    active_days: int
    minimum_active_days: int
    realized_trades: int
    total_realized_trades: int = 0
    unattributed_realized_trades: int = 0
    minimum_realized_trades: int
    average_adverse_slippage_bps: Decimal | None = None
    maximum_average_slippage_bps: Decimal
    profit_factor: Decimal | None = None
    minimum_profit_factor: Decimal
    max_drawdown_pct: Decimal
    account_max_drawdown_pct: Decimal = Decimal("0")
    maximum_drawdown_pct: Decimal
    equity_basis: str | None = None
    performance_snapshot_count: int = 0
    excluded_snapshot_count: int = 0
    data_coverage_ready: bool
    reliability_ready: bool
    blockers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
