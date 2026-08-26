from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, model_validator

ARM_PHRASE = "ARM_OKX_DEMO_AUTOMATION"
DISARM_PHRASE = "DISARM_OKX_DEMO_AUTOMATION"
EXECUTE_PHRASE = "OKX_DEMO_AUTOMATION_EXECUTE"
EMERGENCY_STOP_PHRASE = "EMERGENCY_STOP_OKX_DEMO"
CLEAR_STOP_PHRASE = "CLEAR_OKX_DEMO_STOP"

DemoAutomationOutcome = Literal[
    "submitted",
    "approved_dry_run",
    "no_trade",
    "risk_rejected",
    "blocked",
    "duplicate",
    "monitoring",
    "locked",
    "error",
]


class DemoAutomationArmRequest(BaseModel):
    confirmation: Literal[ARM_PHRASE]


class DemoAutomationDisarmRequest(BaseModel):
    confirmation: Literal[DISARM_PHRASE]


class DemoAutomationEmergencyStopRequest(BaseModel):
    confirmation: Literal[EMERGENCY_STOP_PHRASE]


class DemoAutomationClearStopRequest(BaseModel):
    confirmation: Literal[CLEAR_STOP_PHRASE]


class DemoAutomationRunRequest(BaseModel):
    symbols: list[str] | None = None
    execute: bool = False
    confirmation: str | None = None

    @model_validator(mode="after")
    def require_execute_confirmation(self) -> "DemoAutomationRunRequest":
        if self.execute and self.confirmation != EXECUTE_PHRASE:
            raise ValueError(f"execute=true requires confirmation={EXECUTE_PHRASE}")
        return self


class DemoAutomationRiskTier(BaseModel):
    name: Literal["low", "medium", "high", "elite", "extreme"]
    minimum_score: int = Field(ge=0, le=100)
    maximum_score: int = Field(ge=0, le=100)
    risk_pct: Decimal = Field(gt=0)
    leverage: int = Field(ge=1)
    margin_allocation_pct: Decimal = Field(gt=0, le=1)

    @model_validator(mode="after")
    def validate_score_range(self) -> "DemoAutomationRiskTier":
        if self.minimum_score > self.maximum_score:
            raise ValueError("minimum_score cannot exceed maximum_score")
        return self


class DemoAutomationActiveTrade(BaseModel):
    instrument_id: str
    settlement_currency: str | None = None
    direction: Literal["long", "short"] | None = None
    strategy: str | None = None
    score: int | None = Field(default=None, ge=0, le=100)
    effective_score: int | None = Field(default=None, ge=0, le=100)
    derivative_status: Literal[
        "confirmed", "mixed", "opposed", "insufficient"
    ] | None = None
    derivative_confidence: Decimal | None = Field(default=None, ge=0, le=1)
    mathematical_status: Literal[
        "confirmed", "mixed", "opposed", "insufficient", "unstable"
    ] | None = None
    mathematical_risk_grade: Literal[
        "high", "medium", "low", "blocked"
    ] | None = None
    mathematical_confidence: Decimal | None = Field(default=None, ge=0, le=1)
    mathematical_reliability: Decimal | None = Field(default=None, ge=0, le=1)
    mathematical_auxiliary_bonus: int = Field(default=0, ge=0, le=5)
    mathematical_validated_components: list[str] = Field(default_factory=list)
    mathematical_auxiliary_components: list[str] = Field(default_factory=list)
    tier: Literal["low", "medium", "high", "elite", "extreme", "legacy"] = "legacy"
    client_order_id: str | None = None
    exchange_order_id: str | None = None
    protection_client_order_id: str | None = None
    contracts: Decimal = Field(default=Decimal("0"), ge=0)
    leverage: int = Field(default=1, ge=1)
    required_leverage: int | None = Field(default=None, ge=1)
    leverage_cap: int | None = Field(default=None, ge=1)
    leverage_cap_reasons: list[str] = Field(default_factory=list)
    margin_mode: Literal["cross", "isolated"] = "cross"
    risk_budget_pct: Decimal = Field(default=Decimal("0"), ge=0, le=1)
    estimated_stop_loss_amount: Decimal = Field(default=Decimal("0"), ge=0)
    # Reconciled cross-margin ratios can exceed current equity after adverse
    # price/equity movement. Preserve unsafe values so recovery can lock them.
    estimated_stop_loss_pct: Decimal = Field(default=Decimal("0"), ge=0)
    estimated_notional: Decimal = Field(default=Decimal("0"), ge=0)
    margin_allocation_pct: Decimal = Field(default=Decimal("0"), ge=0)
    estimated_margin: Decimal = Field(default=Decimal("0"), ge=0)
    estimated_round_trip_cost_pct: Decimal = Field(default=Decimal("0"), ge=0)
    estimated_cost_amount: Decimal = Field(default=Decimal("0"), ge=0)
    position_margin_cap_usdt: Decimal | None = Field(default=None, gt=0)
    capital_bucket_usdt: Decimal | None = Field(default=None, gt=0)
    reference_price: Decimal | None = Field(default=None, gt=0)
    execution_order_type: Literal["fok"] | None = None
    execution_limit_price: Decimal | None = Field(default=None, gt=0)
    average_fill_price: Decimal | None = Field(default=None, gt=0)
    actual_gross_risk_reward: Decimal | None = Field(default=None, gt=0)
    actual_net_risk_reward: Decimal | None = Field(default=None, gt=0)
    actual_enforced_risk_reward: Decimal | None = Field(default=None, gt=0)
    adverse_fill_slippage_bps: Decimal | None = None
    stop_loss: Decimal | None = Field(default=None, gt=0)
    take_profit: Decimal | None = Field(default=None, gt=0)
    protection_model: Literal["atr", "structure"] = "atr"
    structure_timeframe: str | None = None
    structure_source_closed_at: datetime | None = None
    structure_stop_anchor: Decimal | None = Field(default=None, gt=0)
    structure_target_anchor: Decimal | None = Field(default=None, gt=0)
    structure_volatility_buffer: Decimal | None = Field(default=None, gt=0)
    gross_risk_reward: Decimal | None = Field(default=None, gt=0)
    net_risk_reward: Decimal | None = Field(default=None, gt=0)
    start_equity: Decimal | None = Field(default=None, gt=0)
    started_at: datetime

    @model_validator(mode="after")
    def validate_adaptive_trade(self) -> "DemoAutomationActiveTrade":
        if self.started_at.tzinfo is None or self.started_at.utcoffset() is None:
            raise ValueError("active trade started_at must be timezone-aware")
        if self.tier == "legacy":
            return self
        if self.protection_model == "structure":
            if (
                self.margin_mode != "isolated"
                or self.structure_timeframe is None
                or self.structure_source_closed_at is None
                or self.structure_stop_anchor is None
                or self.structure_target_anchor is None
                or self.structure_volatility_buffer is None
                or self.net_risk_reward is None
                or self.gross_risk_reward is None
            ):
                raise ValueError(
                    "structural active trade requires isolated, auditable protection"
                )
            if (
                self.structure_source_closed_at.tzinfo is None
                or self.structure_source_closed_at.utcoffset() is None
            ):
                raise ValueError(
                    "structural active trade source timestamp must be timezone-aware"
                )
        if (
            self.direction is None
            or self.strategy is None
            or self.score is None
            or self.reference_price is None
            or self.stop_loss is None
            or self.take_profit is None
            or self.start_equity is None
            or self.contracts <= 0
            or self.risk_budget_pct <= 0
            or self.estimated_stop_loss_amount <= 0
            or self.estimated_notional <= 0
            or self.estimated_margin <= 0
        ):
            raise ValueError("adaptive active trade is incomplete")
        if self.direction == "long" and not (
            self.stop_loss < self.reference_price < self.take_profit
        ):
            raise ValueError("long active trade protection is invalid")
        if self.direction == "short" and not (
            self.take_profit < self.reference_price < self.stop_loss
        ):
            raise ValueError("short active trade protection is invalid")
        return self


class DemoAutomationSymbolResult(BaseModel):
    symbol: str
    instrument_id: str | None = None
    outcome: DemoAutomationOutcome
    direction: Literal["long", "short"] | None = None
    strategy: str | None = None
    score: int | None = None
    effective_score: int | None = Field(default=None, ge=0, le=100)
    derivative_status: Literal[
        "confirmed", "mixed", "opposed", "insufficient"
    ] | None = None
    derivative_confidence: Decimal | None = Field(default=None, ge=0, le=1)
    mathematical_status: Literal[
        "confirmed", "mixed", "opposed", "insufficient", "unstable"
    ] | None = None
    mathematical_risk_grade: Literal[
        "high", "medium", "low", "blocked"
    ] | None = None
    mathematical_confidence: Decimal | None = Field(default=None, ge=0, le=1)
    mathematical_reliability: Decimal | None = Field(default=None, ge=0, le=1)
    mathematical_auxiliary_bonus: int = Field(default=0, ge=0, le=5)
    mathematical_validated_components: list[str] = Field(default_factory=list)
    mathematical_auxiliary_components: list[str] = Field(default_factory=list)
    reference_price: Decimal | None = None
    execution_order_type: Literal["fok"] | None = None
    execution_limit_price: Decimal | None = None
    average_fill_price: Decimal | None = None
    actual_gross_risk_reward: Decimal | None = None
    actual_net_risk_reward: Decimal | None = None
    actual_enforced_risk_reward: Decimal | None = None
    adverse_fill_slippage_bps: Decimal | None = None
    stop_loss: Decimal | None = None
    take_profit: Decimal | None = None
    risk_reward: Decimal | None = None
    approved_base_quantity: Decimal | None = None
    approved_contracts: Decimal | None = None
    score_tier: Literal["low", "medium", "high", "elite", "extreme"] | None = None
    selected_leverage: int | None = Field(default=None, ge=1)
    required_leverage: int | None = Field(default=None, ge=1)
    leverage_cap: int | None = Field(default=None, ge=1)
    leverage_cap_reasons: list[str] = Field(default_factory=list)
    margin_mode: Literal["cross", "isolated"] | None = None
    risk_budget_pct: Decimal | None = None
    estimated_stop_loss_pct: Decimal | None = None
    margin_allocation_pct: Decimal | None = None
    estimated_margin: Decimal | None = None
    protection_model: Literal["atr", "structure"] | None = None
    structure_timeframe: str | None = None
    structure_source_closed_at: datetime | None = None
    structure_stop_anchor: Decimal | None = None
    structure_target_anchor: Decimal | None = None
    structure_volatility_buffer: Decimal | None = None
    estimated_round_trip_cost_pct: Decimal | None = None
    estimated_cost_amount: Decimal | None = None
    gross_risk_reward: Decimal | None = None
    net_risk_reward: Decimal | None = None
    position_margin_cap_usdt: Decimal | None = Field(default=None, gt=0)
    capital_bucket_usdt: Decimal | None = Field(default=None, gt=0)
    client_order_id: str | None = None
    exchange_order_id: str | None = None
    order_submission_attempted: bool = False
    reason_codes: list[str] = Field(default_factory=list)
    detail: str
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class DemoAutomationRunResult(BaseModel):
    trigger: Literal["manual", "scheduled"]
    execute: bool
    started_at: datetime
    completed_at: datetime
    results: list[DemoAutomationSymbolResult]
    total_equity: Decimal | None = None
    risk_equity: Decimal | None = None
    risk_equity_currency: str | None = None
    capital_bucket_enabled: bool = False
    capital_bucket_usdt: Decimal | None = Field(default=None, gt=0)
    capital_bucket_position_limit: int | None = Field(default=None, ge=1)
    daily_pnl: Decimal = Decimal("0")
    trades_today: int = 0
    consecutive_losses: int = 0
    rolling_7d_realized_pnl: Decimal = Decimal("0")
    active_position_count: int = Field(default=0, ge=0)
    portfolio_open_risk_pct: Decimal = Field(default=Decimal("0"), ge=0)
    portfolio_margin_pct: Decimal = Field(default=Decimal("0"), ge=0)
    portfolio_estimated_margin: Decimal = Field(default=Decimal("0"), ge=0)


class DemoAutomationStatus(BaseModel):
    capability_enabled: bool
    trading_mode: str
    demo_writes_enabled: bool
    armed: bool
    running: bool
    run_in_progress: bool = False
    emergency_stop: bool
    locked: bool
    lock_reasons: list[str] = Field(default_factory=list)
    configuration_blockers: list[str] = Field(default_factory=list)
    symbols: list[str]
    scan_interval_seconds: int
    execution_order_type: Literal["fok"] = "fok"
    execution_max_adverse_slippage_bps: Decimal = Field(
        default=Decimal("5"), ge=0
    )
    minimum_execution_risk_reward: Decimal = Field(
        default=Decimal("1.8"), gt=0
    )
    max_trades_per_day: int
    daily_loss_limit_pct: Decimal
    max_consecutive_losses: int
    session_date: date
    continuous_session_enabled: bool = False
    daily_loss_limit_enforced: bool = True
    daily_trade_limit_enforced: bool = True
    consecutive_loss_limit_enforced: bool = True
    effective_trade_cooldown_seconds: int = Field(default=0, ge=0)
    score_risk_enabled: bool = False
    derivative_risk_gate_enabled: bool = False
    mathematical_risk_gate_enabled: bool = False
    structural_dynamic_leverage_enabled: bool = False
    structural_margin_mode: Literal["isolated"] | None = None
    structural_min_net_risk_reward: Decimal | None = None
    structural_estimated_cost_bps: Decimal | None = None
    score_risk_tiers: list[DemoAutomationRiskTier] = Field(default_factory=list)
    max_open_positions: int = 1
    portfolio_max_risk_pct: Decimal = Decimal("0")
    portfolio_max_margin_pct: Decimal = Decimal("0")
    capital_bucket_enabled: bool = False
    capital_bucket_usdt: Decimal | None = Field(default=None, gt=0)
    capital_bucket_position_limit: int | None = Field(default=None, ge=1)
    portfolio_open_risk_pct: Decimal = Decimal("0")
    portfolio_margin_pct: Decimal = Decimal("0")
    portfolio_estimated_margin: Decimal = Decimal("0")
    active_position_count: int = 0
    active_trades: list[DemoAutomationActiveTrade] = Field(default_factory=list)
    equity_basis: str | None = None
    baseline_equity: Decimal | None = None
    peak_equity: Decimal | None = None
    risk_peak_equity: Decimal | None = None
    daily_pnl: Decimal = Decimal("0")
    rolling_7d_realized_pnl: Decimal = Decimal("0")
    trades_today: int = 0
    consecutive_losses: int = 0
    active_instrument_id: str | None = None
    active_client_order_id: str | None = None
    active_started_at: datetime | None = None
    last_trade_closed_at: datetime | None = None
    last_started_at: datetime | None = None
    last_completed_at: datetime | None = None
    next_run_at: datetime | None = None
    last_error: str | None = None
    recovered: bool = False
