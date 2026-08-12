from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.domain.strategy import TradeCandidate


class AccountRiskState(BaseModel):
    equity: Decimal = Field(gt=0)
    daily_realized_pnl: Decimal = Decimal("0")
    weekly_realized_pnl: Decimal = Decimal("0")
    peak_equity: Decimal | None = None
    consecutive_losses: int = Field(default=0, ge=0)
    open_positions: int = Field(default=0, ge=0)
    same_direction_positions: int = Field(default=0, ge=0)
    correlated_positions: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def default_peak_equity(self) -> "AccountRiskState":
        if self.peak_equity is None:
            self.peak_equity = self.equity
        if self.peak_equity <= 0:
            raise ValueError("peak_equity must be positive")
        return self


class RiskLimits(BaseModel):
    risk_per_trade_pct: Decimal = Field(default=Decimal("0.005"), gt=0, le=Decimal("0.10"))
    max_daily_loss_pct: Decimal = Field(default=Decimal("0.02"), gt=0, le=Decimal("0.25"))
    max_weekly_loss_pct: Decimal = Field(default=Decimal("0.05"), gt=0, le=Decimal("0.50"))
    max_drawdown_pct: Decimal = Field(default=Decimal("0.10"), gt=0, le=Decimal("0.80"))
    max_consecutive_losses: int = Field(default=3, ge=1, le=20)
    max_open_positions: int = Field(default=2, ge=1, le=20)
    max_same_direction_positions: int = Field(default=1, ge=1, le=20)
    max_correlated_positions: int = Field(default=1, ge=1, le=20)
    max_notional: Decimal = Field(default=Decimal("5000"), gt=0)
    minimum_quantity: Decimal = Field(default=Decimal("0.00000001"), gt=0)
    minimum_score: int = Field(default=72, ge=0, le=100)
    minimum_risk_reward: Decimal = Field(default=Decimal("1.8"), gt=0, le=Decimal("10"))


class RiskEvaluationRequest(BaseModel):
    candidate: TradeCandidate
    account: AccountRiskState
    limits: RiskLimits | None = None


class RiskDecision(BaseModel):
    decision: Literal["approved", "rejected"]
    candidate: TradeCandidate
    requested_risk_pct: Decimal
    approved_risk_pct: Decimal
    approved_quantity: Decimal
    notional: Decimal
    max_loss_amount: Decimal
    stop_distance: Decimal
    effective_risk_distance: Decimal = Decimal("0")
    estimated_cost_amount: Decimal = Decimal("0")
    reason_codes: list[str] = Field(default_factory=list)
    evaluated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    version: str = "1.0.0"
