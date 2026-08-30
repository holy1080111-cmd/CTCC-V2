from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal
from uuid import UUID, uuid4

from pydantic import Field, field_validator, model_validator

from app.mie.contracts._base import ForecastHorizon, MieContract, require_utc


_SUM_TOLERANCE = Decimal("1e-12")


class MarketRegime(StrEnum):
    BULL_TREND = "bull_trend"
    BEAR_TREND = "bear_trend"
    RANGE = "range"
    HIGH_VOLATILITY = "high_volatility"
    TRANSITION = "transition"


class RegimeProbabilityVector(MieContract):
    bull_trend: Decimal = Field(ge=0, le=1)
    bear_trend: Decimal = Field(ge=0, le=1)
    range: Decimal = Field(ge=0, le=1)
    high_volatility: Decimal = Field(ge=0, le=1)
    transition: Decimal = Field(ge=0, le=1)

    @model_validator(mode="after")
    def validate_total_probability(self) -> "RegimeProbabilityVector":
        total = sum(
            (
                self.bull_trend,
                self.bear_trend,
                self.range,
                self.high_volatility,
                self.transition,
            ),
            Decimal("0"),
        )
        if abs(total - Decimal("1")) > _SUM_TOLERANCE:
            raise ValueError("regime probabilities must sum to one")
        return self

    def as_dict(self) -> dict[MarketRegime, Decimal]:
        return {
            MarketRegime.BULL_TREND: self.bull_trend,
            MarketRegime.BEAR_TREND: self.bear_trend,
            MarketRegime.RANGE: self.range,
            MarketRegime.HIGH_VOLATILITY: self.high_volatility,
            MarketRegime.TRANSITION: self.transition,
        }


class RegimeSnapshot(MieContract):
    regime_snapshot_id: UUID = Field(default_factory=uuid4)
    instrument_id: str = Field(
        min_length=3,
        max_length=64,
        pattern=r"^[A-Z0-9]+(?:-[A-Z0-9]+)+$",
    )
    horizon: ForecastHorizon
    as_of: datetime
    data_cutoff: datetime
    generated_at: datetime
    probabilities: RegimeProbabilityVector
    dominant_regime: MarketRegime
    evidence_ids: tuple[UUID, ...] = Field(min_length=1)
    model_id: str = Field(
        min_length=1,
        max_length=160,
        pattern=r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$",
    )
    model_version: str = Field(min_length=1, max_length=80)
    provenance_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    execution_authority: Literal[False] = False

    @field_validator("as_of", "data_cutoff", "generated_at")
    @classmethod
    def validate_timestamps(cls, value: datetime, info) -> datetime:
        return require_utc(value, info.field_name)

    @model_validator(mode="after")
    def validate_regime(self) -> "RegimeSnapshot":
        if self.data_cutoff > self.as_of:
            raise ValueError("regime cannot use data after as_of")
        if self.generated_at < self.as_of:
            raise ValueError("regime cannot be generated before as_of")
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("regime evidence ids must be unique")
        probabilities = self.probabilities.as_dict()
        maximum = max(probabilities.values())
        if probabilities[self.dominant_regime] != maximum:
            raise ValueError("dominant regime must have maximum probability")
        if sum(value == maximum for value in probabilities.values()) != 1:
            raise ValueError("dominant regime must be unique")
        return self
