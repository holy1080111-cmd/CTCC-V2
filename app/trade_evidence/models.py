"""Immutable chart inputs. Gate statements remain caller assertions, not truth."""

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, field_validator, model_validator

from app.market.quality.candles import BAR_SECONDS
from app.trade_qualification.event_models import Digest, TriggerDetection
from app.trade_qualification.location import ExecutableQuote
from app.trade_qualification.models import (
    EntryQualificationResult,
    Price,
    QualificationModel,
    ReportId,
    Text,
    require_aware,
)

Timeframe = Literal["4H", "1H", "15m", "5m"]
Purpose = Literal["synthetic_test", "observed"]
Indicator = Annotated[Decimal, Field(gt=0, max_digits=140, decimal_places=120)]
TIMEFRAMES = ("4H", "1H", "15m", "5m")
MAX_SOURCE_BYTES = 8 * 1024 * 1024


class EvidenceCandle(QualificationModel):
    open_time: datetime
    close_time: datetime
    open: Price
    high: Price
    low: Price
    close: Price
    ema20: Indicator | None = None
    ema50: Indicator | None = None
    ema200: Indicator | None = None

    _times = field_validator("open_time", "close_time")(require_aware)

    @model_validator(mode="after")
    def geometry(self):
        if self.close_time <= self.open_time:
            raise ValueError("candle_close_must_follow_open")
        if self.low > min(self.open, self.close) or self.high < max(
            self.open, self.close
        ):
            raise ValueError("invalid_ohlc_geometry")
        return self


class EvidenceLevel(QualificationModel):
    kind: Literal["support", "resistance", "liquidity", "fvg", "order_block"]
    low: Price
    high: Price
    known_at: datetime
    source: Text
    # The surviving-level inventory is selected as of this instant. It is not
    # evidence that the level remained active at every earlier candle.
    as_of: datetime

    _times = field_validator("known_at", "as_of")(require_aware)

    @model_validator(mode="after")
    def geometry(self):
        if self.low > self.high or self.known_at > self.as_of:
            raise ValueError("invalid_level_geometry_or_causality")
        return self


class EvidencePanel(QualificationModel):
    timeframe: Timeframe
    candles: tuple[EvidenceCandle, ...] = Field(min_length=1, max_length=200)
    trend: Text
    structure: Text
    levels: tuple[EvidenceLevel, ...] = Field(max_length=32)
    crop_start: datetime
    crop_end: datetime
    total_confirmed_candles: int = Field(ge=1, le=1024)

    _times = field_validator("crop_start", "crop_end")(require_aware)

    @model_validator(mode="after")
    def consistent_crop(self):
        if (
            self.crop_start != self.candles[0].open_time
            or self.crop_end != self.candles[-1].close_time
            or self.total_confirmed_candles < len(self.candles)
        ):
            raise ValueError("inconsistent_panel_crop")
        for index, candle in enumerate(self.candles):
            if (candle.close_time - candle.open_time).total_seconds() != BAR_SECONDS[
                self.timeframe
            ]:
                raise ValueError("candle_interval_mismatch")
            if index and candle.open_time != self.candles[index - 1].close_time:
                raise ValueError("panel_candle_gap_or_reordering")
        if any(level.as_of != self.crop_end for level in self.levels):
            raise ValueError("level_as_of_mismatch")
        return self


class EvidenceSnapshot(QualificationModel):
    schema_version: Literal["ctcc_trade_evidence_v1"] = "ctcc_trade_evidence_v1"
    report_id: ReportId
    instrument_id: Text
    symbol: Text
    strategy: Text
    direction: Literal["long", "short"]
    qualification: EntryQualificationResult
    detection: TriggerDetection | None
    quote: ExecutableQuote | None
    panels: tuple[EvidencePanel, ...] = Field(min_length=4, max_length=4)
    source_sha256: Digest
    candidate_sha256: Digest
    source_json: Annotated[str, StringConstraints(strip_whitespace=False)] = Field(
        min_length=2, max_length=MAX_SOURCE_BYTES
    )
    protection_audit_json: (
        Annotated[str, StringConstraints(strip_whitespace=False)] | None
    ) = Field(default=None, max_length=4 * 1024 * 1024)
    prepared_at: datetime
    purpose: Purpose
    candle_limit: int = Field(default=80, ge=80, le=200)
    zone_tick_size: Price | None = None
    execution_authority: Literal[False] = False
    gate_assessments_verified: Literal[False] = False
    source_authenticity_verified: Literal[False] = False
    evidence_gate: Literal["not_evaluated"] = "not_evaluated"
    execution_recheck: Literal["not_evaluated"] = "not_evaluated"

    _time = field_validator("prepared_at")(require_aware)

    @field_validator(
        "execution_authority",
        "gate_assessments_verified",
        "source_authenticity_verified",
        mode="before",
    )
    @classmethod
    def no_authority(cls, value):
        if type(value) is not bool or value is not False:
            raise ValueError("evidence_cannot_grant_authority")
        return value

    @model_validator(mode="after")
    def consistency(self):
        if tuple(panel.timeframe for panel in self.panels) != TIMEFRAMES:
            raise ValueError("evidence_requires_ordered_four_timeframes")
        if any(
            len(panel.candles) != min(self.candle_limit, panel.total_confirmed_candles)
            for panel in self.panels
        ):
            raise ValueError("candle_crop_limit_mismatch")
        if any(panel.crop_end > self.prepared_at for panel in self.panels):
            raise ValueError("future_panel")
        q = self.qualification
        if (q.report_id, q.symbol, q.strategy, q.direction) != (
            self.report_id,
            self.symbol,
            self.strategy,
            self.direction,
        ):
            raise ValueError("qualification_identity_mismatch")
        if q.evaluated_at > self.prepared_at or len(q.gates) > 11:
            raise ValueError("evidence_must_precede_g12_and_recheck")
        if len(self.source_json.encode("utf-8")) > MAX_SOURCE_BYTES:
            raise ValueError("source_bytes_out_of_bounds")
        return self
