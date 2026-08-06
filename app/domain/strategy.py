from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, model_validator

Direction = Literal["long", "short", "neutral"]


class ScoreComponent(BaseModel):
    code: str
    label: str
    points: int
    maximum: int
    passed: bool
    detail: str


class TradeCandidate(BaseModel):
    strategy: str
    direction: Literal["long", "short"]
    score: int = Field(ge=0, le=100)
    entry: Decimal
    stop_loss: Decimal
    take_profit: Decimal
    risk_reward: Decimal
    invalidation: str
    expires_at: datetime
    reasons: list[str] = Field(default_factory=list)
    counter_evidence: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_geometry(self) -> "TradeCandidate":
        if self.direction == "long" and not (self.stop_loss < self.entry < self.take_profit):
            raise ValueError("long candidate requires stop_loss < entry < take_profit")
        if self.direction == "short" and not (self.take_profit < self.entry < self.stop_loss):
            raise ValueError("short candidate requires take_profit < entry < stop_loss")
        if self.risk_reward <= 0:
            raise ValueError("risk_reward must be positive")
        return self


class StrategyEvaluation(BaseModel):
    strategy: str
    direction: Direction
    eligible: bool
    completion_ratio: Decimal = Field(ge=0, le=1)
    score: int = Field(ge=0, le=100)
    passed_conditions: list[str] = Field(default_factory=list)
    failed_conditions: list[str] = Field(default_factory=list)
    vetoes: list[str] = Field(default_factory=list)
    score_components: list[ScoreComponent] = Field(default_factory=list)
    candidate: TradeCandidate | None = None


class StrategyDecision(BaseModel):
    symbol: str
    instrument_id: str
    decision: Literal["long", "short", "no_trade"]
    selected_strategy: str | None = None
    selected_candidate: TradeCandidate | None = None
    minimum_score: int
    evaluations: list[StrategyEvaluation]
    blockers: list[str] = Field(default_factory=list)
    generated_at: datetime
    version: str = "1.0.0"
