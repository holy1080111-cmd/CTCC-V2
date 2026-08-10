from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class CausalTrendSnapshot(BaseModel):
    window: int = Field(ge=5)
    log_velocity_per_bar: Decimal
    log_acceleration_per_bar2: Decimal
    log_return_rms_per_bar: Decimal = Field(ge=0)
    velocity_to_volatility: Decimal
    acceleration_to_volatility: Decimal
    fit_r2: Decimal = Field(ge=0, le=1)
    residual_std: Decimal = Field(ge=0)
    confidence: Decimal = Field(ge=0, le=1)
    direction: Literal["rising", "falling", "flat"]


class CausalStateSnapshot(BaseModel):
    """Robust causal state estimate with explicit model-based uncertainty."""

    window: int = Field(ge=20)
    log_velocity_per_bar: Decimal
    log_acceleration_per_bar2: Decimal
    velocity_std: Decimal = Field(ge=0)
    acceleration_std: Decimal = Field(ge=0)
    velocity_z: Decimal = Field(ge=-20, le=20)
    acceleration_z: Decimal = Field(ge=-20, le=20)
    innovation_z: Decimal = Field(ge=-20, le=20)
    shock_score: Decimal = Field(ge=0, le=1)
    confidence: Decimal = Field(ge=0, le=1)
    direction: Literal["rising", "falling", "flat"]
    outlier_count: int = Field(ge=0)


class CausalReturnIntervalSnapshot(BaseModel):
    """One-bar, past-only conformal interval for the next log return."""

    horizon_bars: int = Field(default=1, ge=1, le=1)
    confidence_level: Decimal = Field(ge=0, le=1)
    predicted_log_return: Decimal
    lower_log_return: Decimal
    upper_log_return: Decimal
    half_width: Decimal = Field(ge=0)
    calibration_size: int = Field(ge=20)
    coverage_sample_size: int = Field(ge=1)
    empirical_coverage: Decimal = Field(ge=0, le=1)
    direction: Literal["rising", "falling", "uncertain"]

    @model_validator(mode="after")
    def validate_interval_geometry(self) -> "CausalReturnIntervalSnapshot":
        if not (
            self.lower_log_return
            <= self.predicted_log_return
            <= self.upper_log_return
        ):
            raise ValueError("predicted return must lie inside conformal interval")
        if self.direction == "rising" and self.lower_log_return <= 0:
            raise ValueError("rising conformal interval must be strictly positive")
        if self.direction == "falling" and self.upper_log_return >= 0:
            raise ValueError("falling conformal interval must be strictly negative")
        if self.direction == "uncertain" and not (
            self.lower_log_return <= 0 <= self.upper_log_return
        ):
            raise ValueError("uncertain conformal interval must cross zero")
        return self


class MathematicalCoreComponent(BaseModel):
    code: str
    signal: Decimal = Field(ge=-1, le=1)
    reliability: Decimal = Field(ge=0, le=1)
    validation_level: Literal["analytical", "prequential", "auxiliary"]
    validation_sample_size: int = Field(default=0, ge=0)
    validation_metric: Decimal | None = Field(default=None, ge=0, le=1)
    detail: str


class MathematicalCoreSnapshot(BaseModel):
    """Single causal contract joining analysis evidence without adding score."""

    status: Literal["long", "short", "neutral", "unstable", "insufficient"]
    directional_score: Decimal = Field(ge=-1, le=1)
    confidence: Decimal = Field(ge=0, le=1)
    coverage: Decimal = Field(ge=0, le=1)
    consensus: Decimal = Field(ge=0, le=1)
    instability: Decimal = Field(ge=0, le=1)
    auxiliary_directional_score: Decimal = Field(default=Decimal("0"), ge=-1, le=1)
    auxiliary_confidence: Decimal = Field(default=Decimal("0"), ge=0, le=1)
    components: list[MathematicalCoreComponent] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_status_geometry(self) -> "MathematicalCoreSnapshot":
        codes = [component.code for component in self.components]
        if len(codes) != len(set(codes)):
            raise ValueError("mathematical core component codes must be unique")
        executable = [
            component
            for component in self.components
            if component.validation_level != "auxiliary"
            and component.reliability > 0
        ]
        if self.status == "long" and not (
            self.directional_score >= Decimal("0.25")
            and self.confidence >= Decimal("0.20")
            and executable
        ):
            raise ValueError("long mathematical state lacks directional support")
        if self.status == "short" and not (
            self.directional_score <= Decimal("-0.25")
            and self.confidence >= Decimal("0.20")
            and executable
        ):
            raise ValueError("short mathematical state lacks directional support")
        if self.status == "unstable" and self.instability < Decimal("0.65"):
            raise ValueError("unstable mathematical state requires instability")
        if self.status == "insufficient" and self.coverage >= Decimal("0.35"):
            raise ValueError("insufficient mathematical state requires low coverage")
        return self


class IndicatorSnapshot(BaseModel):
    ema20: Decimal | None = None
    ema50: Decimal | None = None
    ema200: Decimal | None = None
    atr14: Decimal | None = None
    atr_pct: Decimal | None = None
    rsi14: Decimal | None = None
    macd: Decimal | None = None
    macd_signal: Decimal | None = None
    macd_histogram: Decimal | None = None
    adx14: Decimal | None = None
    vwap: Decimal | None = None
    volume_ratio20: Decimal | None = None
    causal_trend: CausalTrendSnapshot | None = None
    causal_state: CausalStateSnapshot | None = None
    return_interval: CausalReturnIntervalSnapshot | None = None


class SwingPoint(BaseModel):
    kind: Literal["high", "low"]
    timestamp: datetime
    price: Decimal
    index: int


class FairValueGap(BaseModel):
    direction: Literal["bullish", "bearish"]
    lower: Decimal
    upper: Decimal
    created_at: datetime
    filled_ratio: Decimal = Decimal("0")


class OrderBlock(BaseModel):
    direction: Literal["bullish", "bearish"]
    lower: Decimal
    upper: Decimal
    created_at: datetime
    mitigated: bool = False


class StructureSnapshot(BaseModel):
    trend: Literal["strong_bullish", "bullish", "neutral", "bearish", "strong_bearish"]
    swing_structure: str
    bos: Literal["up", "down"] | None = None
    choch: Literal["up", "down"] | None = None
    last_swing_high: Decimal | None = None
    last_swing_low: Decimal | None = None
    fair_value_gaps: list[FairValueGap] = Field(default_factory=list)
    order_blocks: list[OrderBlock] = Field(default_factory=list)
    support_levels: list[Decimal] = Field(default_factory=list)
    resistance_levels: list[Decimal] = Field(default_factory=list)


class TimeframeAnalysis(BaseModel):
    timeframe: str
    candle_count: int
    last_closed_at: datetime
    close: Decimal
    data_quality_ok: bool
    data_quality_issues: list[str] = Field(default_factory=list)
    indicators: IndicatorSnapshot
    structure: StructureSnapshot
    volatility: Literal["low", "normal", "high", "extreme"]
    directional_bias: Literal["long", "short", "neutral"]
    evidence: list[str] = Field(default_factory=list)
    counter_evidence: list[str] = Field(default_factory=list)


class MultiTimeframeAnalysis(BaseModel):
    symbol: str
    instrument_id: str
    price: Decimal
    regime: str
    overall_bias: Literal["long", "short", "neutral"]
    alignment_score: int
    trade_ready: bool
    blockers: list[str] = Field(default_factory=list)
    timeframe_analyses: dict[str, TimeframeAnalysis]
    mathematical_core: MathematicalCoreSnapshot | None = None
    generated_at: datetime
    version: str = "1.0.0"
