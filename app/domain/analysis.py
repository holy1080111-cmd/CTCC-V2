from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field


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
    generated_at: datetime
    version: str = "1.0.0"
