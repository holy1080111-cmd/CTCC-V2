from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from app.domain.enums import Decision, LifecycleState, Side


class TradeCandidateInput(BaseModel):
    symbol: str = Field(min_length=3, max_length=40)
    side: Side
    strategy_name: str = Field(min_length=2, max_length=100)
    score: int = Field(ge=0, le=100)
    entry_price: Decimal = Field(gt=0)
    stop_loss: Decimal = Field(gt=0)
    take_profit: Decimal = Field(gt=0)
    reasons: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_price_geometry(self) -> "TradeCandidateInput":
        if self.side == Side.LONG and not self.stop_loss < self.entry_price < self.take_profit:
            raise ValueError("long candidate requires stop_loss < entry_price < take_profit")
        if self.side == Side.SHORT and not self.take_profit < self.entry_price < self.stop_loss:
            raise ValueError("short candidate requires take_profit < entry_price < stop_loss")
        return self

    @property
    def risk_reward(self) -> Decimal:
        risk = abs(self.entry_price - self.stop_loss)
        reward = abs(self.take_profit - self.entry_price)
        return reward / risk


class RiskDecisionInput(BaseModel):
    candidate_id: UUID
    decision: Decision
    reason_codes: list[str] = Field(default_factory=list)
    requested_risk_pct: Decimal = Field(ge=0)
    approved_risk_pct: Decimal = Field(ge=0)
    approved_quantity: Decimal = Field(ge=0)

    @model_validator(mode="after")
    def validate_rejection_amounts(self) -> "RiskDecisionInput":
        if self.decision == Decision.REJECTED:
            if self.approved_risk_pct != 0 or self.approved_quantity != 0:
                raise ValueError("rejected risk decision must approve zero risk and quantity")
        return self


class LifecycleView(BaseModel):
    id: UUID
    candidate_id: UUID
    state: LifecycleState
    version: int
    failure_code: str | None = None
    created_at: datetime
    updated_at: datetime


class LifecycleTransitionResult(BaseModel):
    lifecycle_id: UUID
    previous_state: LifecycleState
    new_state: LifecycleState
    version: int
    transitioned_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
