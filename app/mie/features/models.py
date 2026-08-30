from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.mie.contracts._base import ForecastHorizon, MieContract, require_utc


FeatureDirection = Literal["rising", "falling", "flat"]
SwingKind = Literal["high", "low"]


class FeatureBar(MieContract):
    """One closed, confirmed OHLCV bar accepted by the MIE feature core."""

    closed_at: datetime
    open: Decimal = Field(gt=0)
    high: Decimal = Field(gt=0)
    low: Decimal = Field(gt=0)
    close: Decimal = Field(gt=0)
    volume: Decimal = Field(ge=0)
    confirmed: Literal[True] = True

    @field_validator("closed_at")
    @classmethod
    def validate_closed_at(cls, value: datetime) -> datetime:
        return require_utc(value, "closed_at")

    @model_validator(mode="after")
    def validate_ohlc_geometry(self) -> "FeatureBar":
        if self.high < max(self.open, self.close, self.low):
            raise ValueError("bar high is below an OHLC value")
        if self.low > min(self.open, self.close, self.high):
            raise ValueError("bar low is above an OHLC value")
        return self


class FeatureWindow(MieContract):
    """Strict chronological input boundary for every Gate 2 engine."""

    instrument_id: str = Field(
        min_length=3,
        max_length=64,
        pattern=r"^[A-Z0-9]+(?:-[A-Z0-9]+)+$",
    )
    horizon: ForecastHorizon
    as_of: datetime
    bars: tuple[FeatureBar, ...] = Field(min_length=5, max_length=10_000)
    feature_version: str = Field(
        default="mie-gate2-features-v1",
        min_length=1,
        max_length=80,
    )

    @field_validator("as_of")
    @classmethod
    def validate_as_of(cls, value: datetime) -> datetime:
        return require_utc(value, "as_of")

    @model_validator(mode="after")
    def validate_causal_window(self) -> "FeatureWindow":
        timestamps = [bar.closed_at for bar in self.bars]
        if any(
            current <= previous
            for previous, current in zip(
                timestamps[:-1], timestamps[1:], strict=True
            )
        ):
            raise ValueError("feature bars must be strictly chronological")
        expected_step = timedelta(seconds=self.horizon.seconds)
        if any(
            current - previous != expected_step
            for previous, current in zip(
                timestamps[:-1], timestamps[1:], strict=True
            )
        ):
            raise ValueError("feature bars must match the declared horizon")
        if timestamps[-1] > self.as_of:
            raise ValueError("feature window cannot contain a future bar")
        return self

    @property
    def data_cutoff(self) -> datetime:
        return self.bars[-1].closed_at

    @property
    def provenance_sha256(self) -> str:
        return hashlib.sha256(
            self.model_dump_json().encode("utf-8")
        ).hexdigest()


class StatisticsFeatures(MieContract):
    sample_size: int = Field(ge=2)
    mean_log_return: Decimal
    return_std: Decimal = Field(ge=0)
    median_log_return: Decimal
    mad_scale: Decimal = Field(ge=0)
    downside_deviation: Decimal = Field(ge=0)
    outlier_fraction: Decimal = Field(ge=0, le=1)


class SignalFeatures(MieContract):
    sample_size: int = Field(ge=2)
    alpha: Decimal = Field(gt=0, le=1)
    smoothed_log_return: Decimal
    raw_return_rms: Decimal = Field(ge=0)
    residual_rms: Decimal = Field(ge=0)
    noise_ratio: Decimal = Field(ge=0, le=1)
    strength: Decimal = Field(ge=0, le=1)
    direction: FeatureDirection


class DynamicsFeatures(MieContract):
    window: int = Field(ge=5)
    log_velocity_per_bar: Decimal
    log_acceleration_per_bar2: Decimal
    log_return_rms_per_bar: Decimal = Field(ge=0)
    velocity_to_volatility: Decimal = Field(ge=-10, le=10)
    acceleration_to_volatility: Decimal = Field(ge=-10, le=10)
    fit_r2: Decimal = Field(ge=0, le=1)
    residual_std: Decimal = Field(ge=0)
    confidence: Decimal = Field(ge=0, le=1)
    direction: FeatureDirection


class MomentumFeatures(MieContract):
    fast_bars: int = Field(ge=2)
    slow_bars: int = Field(ge=3)
    fast_log_return: Decimal
    slow_log_return: Decimal
    normalized_momentum: Decimal = Field(ge=-10, le=10)
    directional_persistence: Decimal = Field(ge=0, le=1)
    volume_ratio: Decimal | None = Field(default=None, ge=0)
    volume_confirmation: Decimal = Field(ge=0, le=1)
    strength: Decimal = Field(ge=0, le=1)
    direction: FeatureDirection

    @model_validator(mode="after")
    def validate_windows(self) -> "MomentumFeatures":
        if self.fast_bars >= self.slow_bars:
            raise ValueError("fast momentum window must be below slow window")
        return self


class SwingPoint(MieContract):
    kind: SwingKind
    index: int = Field(ge=0)
    price: Decimal = Field(gt=0)
    occurred_at: datetime
    confirmed_at: datetime

    @field_validator("occurred_at", "confirmed_at")
    @classmethod
    def validate_timestamps(cls, value: datetime, info) -> datetime:
        return require_utc(value, info.field_name)

    @model_validator(mode="after")
    def validate_confirmation(self) -> "SwingPoint":
        if self.confirmed_at < self.occurred_at:
            raise ValueError("swing confirmation cannot precede the pivot")
        return self


class GeometryFeatures(MieContract):
    pivot_left_bars: int = Field(ge=1)
    pivot_right_bars: int = Field(ge=1)
    confirmed_pivot_count: int = Field(ge=0)
    last_swing_high: SwingPoint | None = None
    last_swing_low: SwingPoint | None = None
    nearest_support: Decimal | None = Field(default=None, gt=0)
    nearest_resistance: Decimal | None = Field(default=None, gt=0)
    range_position: Decimal | None = Field(default=None, ge=0, le=1)


class MathematicalFeatureSnapshot(MieContract):
    """Replayable Gate 2 output with no decision or execution authority."""

    instrument_id: str = Field(
        min_length=3,
        max_length=64,
        pattern=r"^[A-Z0-9]+(?:-[A-Z0-9]+)+$",
    )
    horizon: ForecastHorizon
    as_of: datetime
    data_cutoff: datetime
    feature_version: str = Field(min_length=1, max_length=80)
    source_provenance_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    statistics: StatisticsFeatures
    signal: SignalFeatures
    dynamics: DynamicsFeatures
    momentum: MomentumFeatures
    geometry: GeometryFeatures
    authority: Literal["shadow_only"] = "shadow_only"
    execution_authority: Literal[False] = False

    @field_validator("as_of", "data_cutoff")
    @classmethod
    def validate_timestamps(cls, value: datetime, info) -> datetime:
        return require_utc(value, info.field_name)

    @model_validator(mode="after")
    def validate_snapshot_boundary(self) -> "MathematicalFeatureSnapshot":
        if self.data_cutoff > self.as_of:
            raise ValueError("feature snapshot cannot use future data")
        return self

    @property
    def replay_sha256(self) -> str:
        return hashlib.sha256(
            self.model_dump_json().encode("utf-8")
        ).hexdigest()
