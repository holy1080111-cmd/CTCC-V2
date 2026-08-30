from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, model_validator

Direction = Literal["long", "short", "neutral"]


class DerivativeConfirmation(BaseModel):
    status: Literal["confirmed", "mixed", "opposed", "insufficient"]
    confidence: Decimal = Field(ge=0, le=1)
    alignment_score: Decimal = Field(ge=-1, le=1)
    qualified_timeframes: list[str] = Field(default_factory=list)
    aligned_timeframes: list[str] = Field(default_factory=list)
    opposed_timeframes: list[str] = Field(default_factory=list)


class MathematicalConfirmation(BaseModel):
    """Direction-specific interpretation of the shared mathematical core."""

    status: Literal["confirmed", "mixed", "opposed", "insufficient", "unstable"]
    risk_grade: Literal["high", "medium", "low", "blocked"]
    confidence: Decimal = Field(ge=0, le=1)
    directional_support: Decimal = Field(ge=-1, le=1)
    reliability: Decimal = Field(ge=0, le=1)
    coverage: Decimal = Field(ge=0, le=1)
    consensus: Decimal = Field(ge=0, le=1)
    instability: Decimal = Field(ge=0, le=1)
    component_codes: list[str] = Field(default_factory=list)
    auxiliary_bonus: int = Field(default=0, ge=0, le=5)
    auxiliary_directional_support: Decimal = Field(default=Decimal("0"), ge=-1, le=1)
    auxiliary_component_codes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_monotonic_risk_grade(self) -> "MathematicalConfirmation":
        if len(self.component_codes) != len(set(self.component_codes)):
            raise ValueError("validated mathematical component codes must be unique")
        if len(self.auxiliary_component_codes) != len(
            set(self.auxiliary_component_codes)
        ):
            raise ValueError("auxiliary mathematical component codes must be unique")
        if set(self.component_codes) & set(self.auxiliary_component_codes):
            raise ValueError(
                "mathematical evidence cannot be both validated and auxiliary"
            )
        if self.auxiliary_bonus > 0 and (
            self.auxiliary_directional_support <= 0
            or not self.auxiliary_component_codes
        ):
            raise ValueError(
                "auxiliary bonus requires aligned, explicitly identified evidence"
            )
        if self.status in {"opposed", "unstable"}:
            if self.risk_grade != "blocked":
                raise ValueError("opposed or unstable mathematics must be blocked")
            if self.auxiliary_bonus != 0:
                raise ValueError("blocked mathematics cannot retain auxiliary bonus")
            return self
        if self.risk_grade == "blocked":
            raise ValueError("blocked grade requires opposed or unstable mathematics")
        if self.risk_grade in {"high", "medium"} and self.status != "confirmed":
            raise ValueError("medium or high grade requires confirmed mathematics")
        if self.risk_grade in {"high", "medium"} and not self.component_codes:
            raise ValueError(
                "medium or high grade requires validated mathematical components"
            )
        if self.risk_grade == "high" and (
            self.confidence < Decimal("0.65")
            or self.reliability < Decimal("0.65")
            or self.instability > Decimal("0.20")
        ):
            raise ValueError("high grade requires high-confidence core geometry")
        if self.risk_grade == "medium" and (
            self.confidence < Decimal("0.35")
            or self.reliability < Decimal("0.45")
        ):
            raise ValueError("medium grade requires sufficient core reliability")
        return self


class StructuralProtectionGeometry(BaseModel):
    """Past-only K-line structure proposed for exchange protection.

    The geometry is evidence, not execution authority.  Demo automation must
    still apply exchange tick alignment, cost-adjusted reward/risk checks, and
    every portfolio safety gate before it can use these prices.
    """

    timeframe: str
    source_closed_at: datetime
    reference_entry: Decimal = Field(gt=0)
    stop_anchor: Decimal = Field(gt=0)
    target_anchor: Decimal = Field(gt=0)
    volatility_buffer: Decimal = Field(gt=0)
    stop_loss: Decimal = Field(gt=0)
    take_profit: Decimal = Field(gt=0)
    gross_risk_reward: Decimal = Field(gt=0)

    @model_validator(mode="after")
    def validate_geometry(self) -> "StructuralProtectionGeometry":
        if (
            self.source_closed_at.tzinfo is None
            or self.source_closed_at.utcoffset() is None
        ):
            raise ValueError("structural source timestamp must be timezone-aware")
        if self.stop_loss < self.reference_entry < self.take_profit:
            if not self.stop_loss < self.stop_anchor < self.reference_entry:
                raise ValueError("long structural stop anchor is invalid")
            if self.stop_anchor - self.stop_loss != self.volatility_buffer:
                raise ValueError("long structural volatility buffer is inconsistent")
            if self.target_anchor != self.take_profit:
                raise ValueError("long structural target must equal its anchor")
            return self
        if self.take_profit < self.reference_entry < self.stop_loss:
            if not self.reference_entry < self.stop_anchor < self.stop_loss:
                raise ValueError("short structural stop anchor is invalid")
            if self.stop_loss - self.stop_anchor != self.volatility_buffer:
                raise ValueError("short structural volatility buffer is inconsistent")
            if self.target_anchor != self.take_profit:
                raise ValueError("short structural target must equal its anchor")
            return self
        raise ValueError("structural protection geometry is invalid")


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
    derivative_confirmation: DerivativeConfirmation | None = None
    mathematical_confirmation: MathematicalConfirmation | None = None
    risk_score: int | None = Field(default=None, ge=0, le=100)
    protection_model: Literal["atr", "structure"] = "atr"
    structural_protection: StructuralProtectionGeometry | None = None
    estimated_round_trip_cost_pct: Decimal = Field(
        default=Decimal("0"), ge=0, le=Decimal("0.20")
    )
    gross_risk_reward: Decimal | None = Field(default=None, gt=0)
    net_risk_reward: Decimal | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def validate_geometry(self) -> "TradeCandidate":
        if self.direction == "long" and not (self.stop_loss < self.entry < self.take_profit):
            raise ValueError("long candidate requires stop_loss < entry < take_profit")
        if self.direction == "short" and not (self.take_profit < self.entry < self.stop_loss):
            raise ValueError("short candidate requires take_profit < entry < stop_loss")
        if self.risk_reward <= 0:
            raise ValueError("risk_reward must be positive")
        if self.protection_model == "structure":
            geometry = self.structural_protection
            if geometry is None:
                raise ValueError("structural protection model requires structural geometry")
            if self.direction == "long" and not (
                self.stop_loss < geometry.stop_anchor < self.entry
                < self.take_profit <= geometry.target_anchor
            ):
                raise ValueError("candidate prices exceed long structural anchors")
            if self.direction == "short" and not (
                geometry.target_anchor <= self.take_profit < self.entry
                < geometry.stop_anchor < self.stop_loss
            ):
                raise ValueError("candidate prices exceed short structural anchors")
            if geometry.source_closed_at >= self.expires_at:
                raise ValueError("structural source must predate candidate expiry")
        if self.net_risk_reward is not None and self.risk_reward != self.net_risk_reward:
            raise ValueError("risk_reward must equal net_risk_reward when net RR is set")
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
