from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Literal, Sequence

from pydantic import (
    ConfigDict,
    Field,
    StrictBool,
    ValidationError,
    field_validator,
    model_validator,
)

from app.mie.contracts import ForecastHorizon
from app.mie.contracts._base import MieContract, require_utc
from app.mie.features import (
    FeatureBar,
    FeatureWindow,
    MathematicalFeatureSnapshot,
    mathematical_feature_snapshot,
)

D = Decimal
SHA256_PATTERN = r"^[0-9a-f]{64}$"


class ReplayValidationError(ValueError):
    """Raised when an offline replay cannot prove its causal boundary."""


class ReplayContract(MieContract):
    """Strict immutable base for point-in-time Gate 3 replay values."""

    model_config = ConfigDict(**MieContract.model_config, strict=True)


def _canonical_sha256(value: object) -> str:
    payload = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _aligned_to_horizon(value: datetime, horizon_seconds: int) -> bool:
    epoch = datetime(1970, 1, 1, tzinfo=value.tzinfo)
    delta = value - epoch
    whole_seconds = delta.days * 86_400 + delta.seconds
    return delta.microseconds == 0 and whole_seconds % horizon_seconds == 0


class PointInTimeBar(ReplayContract):
    """A confirmed bar plus the timestamp when it became observable."""

    source_row_id: str = Field(
        min_length=3,
        max_length=160,
        pattern=r"^[a-zA-Z0-9]+(?:[._:-][a-zA-Z0-9]+)*$",
    )
    source_row_sha256: str = Field(pattern=SHA256_PATTERN)
    instrument_id: str = Field(
        min_length=3,
        max_length=64,
        pattern=r"^[A-Z0-9]+(?:-[A-Z0-9]+)+$",
    )
    available_at: datetime
    bar: FeatureBar

    @field_validator("available_at")
    @classmethod
    def validate_available_at(cls, value: datetime) -> datetime:
        return require_utc(value, "available_at")

    @model_validator(mode="after")
    def validate_point_in_time_boundary(self) -> "PointInTimeBar":
        if self.available_at < self.bar.closed_at:
            raise ValueError("a bar cannot be available before it closes")
        return self


class PointInTimeReplaySnapshot(ReplayContract):
    """One deterministic Gate 2 feature replay with zero runtime authority."""

    instrument_id: str = Field(
        min_length=3,
        max_length=64,
        pattern=r"^[A-Z0-9]+(?:-[A-Z0-9]+)+$",
    )
    as_of: datetime
    data_cutoff: datetime
    source_row_count: int = Field(ge=5, le=10_000)
    source_rows_sha256: str = Field(pattern=SHA256_PATTERN)
    feature_snapshot: MathematicalFeatureSnapshot
    authority: Literal["offline_shadow_only"] = "offline_shadow_only"
    runtime_consumers: Literal[0] = 0
    execution_authority: Literal[False] = False

    @field_validator("as_of", "data_cutoff")
    @classmethod
    def validate_timestamps(cls, value: datetime, info) -> datetime:
        return require_utc(value, info.field_name)

    @model_validator(mode="after")
    def validate_links(self) -> "PointInTimeReplaySnapshot":
        snapshot = self.feature_snapshot
        if self.data_cutoff > self.as_of:
            raise ValueError("replay data cutoff cannot follow its as-of time")
        if snapshot.instrument_id != self.instrument_id:
            raise ValueError("replay instrument does not match its feature snapshot")
        if snapshot.as_of != self.as_of or snapshot.data_cutoff != self.data_cutoff:
            raise ValueError("replay timestamps do not match its feature snapshot")
        if snapshot.execution_authority:
            raise ValueError("replay feature snapshot cannot carry execution authority")
        return self

    @property
    def replay_sha256(self) -> str:
        return _canonical_sha256(self.model_dump(mode="json"))


class ForwardDirectionLabel(ReplayContract):
    """A binary outcome revealed only after its forward horizon is observable."""

    instrument_id: str = Field(
        min_length=3,
        max_length=64,
        pattern=r"^[A-Z0-9]+(?:-[A-Z0-9]+)+$",
    )
    feature_cutoff: datetime
    outcome_at: datetime
    available_at: datetime
    horizon_seconds: int = Field(ge=1)
    positive_threshold: Decimal
    forward_return: Decimal
    positive: StrictBool
    base_row_sha256: str = Field(pattern=SHA256_PATTERN)
    outcome_row_sha256: str = Field(pattern=SHA256_PATTERN)
    authority: Literal["offline_label_only"] = "offline_label_only"
    runtime_consumers: Literal[0] = 0
    execution_authority: Literal[False] = False

    @field_validator("feature_cutoff", "outcome_at", "available_at")
    @classmethod
    def validate_timestamps(cls, value: datetime, info) -> datetime:
        return require_utc(value, info.field_name)

    @field_validator("positive_threshold", "forward_return")
    @classmethod
    def validate_decimals(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("outcome label decimals must be finite")
        return value

    @model_validator(mode="after")
    def validate_label_boundary(self) -> "ForwardDirectionLabel":
        if self.outcome_at != self.feature_cutoff + timedelta(
            seconds=self.horizon_seconds
        ):
            raise ValueError("outcome timestamp must match the declared horizon")
        if self.available_at < self.outcome_at:
            raise ValueError("outcome cannot be available before its timestamp")
        if self.positive != (self.forward_return > self.positive_threshold):
            raise ValueError("outcome direction disagrees with its frozen threshold")
        return self


def _validate_records(
    records: Sequence[PointInTimeBar],
    *,
    bar_horizon: ForecastHorizon,
) -> tuple[PointInTimeBar, ...]:
    try:
        rows = tuple(
            PointInTimeBar.model_validate(row.model_dump(mode="python"))
            for row in records
        )
    except (AttributeError, ValidationError, ValueError) as exc:
        raise ReplayValidationError("replay source row validation failed") from exc
    if len(rows) < 2:
        raise ReplayValidationError("replay requires at least two source rows")

    instruments = {row.instrument_id for row in rows}
    if len(instruments) != 1:
        raise ReplayValidationError("replay rows must use one instrument")

    row_ids = tuple(row.source_row_id for row in rows)
    if len(row_ids) != len(set(row_ids)):
        raise ReplayValidationError("replay source row ids must be unique")

    closed_at = tuple(row.bar.closed_at for row in rows)
    if any(
        current <= previous
        for previous, current in zip(
            closed_at[:-1], closed_at[1:], strict=True
        )
    ):
        raise ReplayValidationError("replay rows must be strictly chronological")

    expected_step = timedelta(seconds=bar_horizon.seconds)
    if any(
        current - previous != expected_step
        for previous, current in zip(
            closed_at[:-1], closed_at[1:], strict=True
        )
    ):
        raise ReplayValidationError("replay rejects missing or irregular bars")

    if any(
        not _aligned_to_horizon(row.bar.closed_at, bar_horizon.seconds)
        for row in rows
    ):
        raise ReplayValidationError("replay bars must align to the declared horizon")
    return rows


def _source_rows_sha256(records: Sequence[PointInTimeBar]) -> str:
    return _canonical_sha256(
        [row.model_dump(mode="json") for row in records]
    )


def replay_features_at(
    records: Sequence[PointInTimeBar],
    *,
    as_of: datetime,
    bar_horizon: ForecastHorizon,
    history_bars: int = 256,
    signal_alpha: Decimal = D("0.25"),
    dynamics_window: int = 21,
    momentum_fast_bars: int = 5,
    momentum_slow_bars: int = 20,
    pivot_left_bars: int = 2,
    pivot_right_bars: int = 2,
) -> PointInTimeReplaySnapshot:
    """Replay features using only rows observable at ``as_of``.

    The function rejects missing, late, duplicated, or irregular historical rows
    instead of silently filling them. Rows whose bars close after ``as_of`` are
    unobserved and therefore ignored by validation, calculation, and replay hash.
    """

    cutoff = require_utc(as_of, "as_of")
    if history_bars < max(5, dynamics_window, momentum_slow_bars + 1):
        raise ReplayValidationError("history window cannot satisfy feature dependencies")
    if history_bars > 10_000:
        raise ReplayValidationError("history window exceeds the feature contract")

    due_rows = tuple(row for row in records if row.bar.closed_at <= cutoff)
    if not due_rows:
        raise ReplayValidationError("no replay rows exist at the declared cutoff")
    due = _validate_records(due_rows, bar_horizon=bar_horizon)
    if any(row.available_at > cutoff for row in due):
        raise ReplayValidationError("a due replay bar was not available at the cutoff")

    selected = due[-history_bars:]
    if len(selected) < max(5, dynamics_window, momentum_slow_bars + 1):
        raise ReplayValidationError("insufficient causal history for Gate 2 features")

    window = FeatureWindow(
        instrument_id=selected[0].instrument_id,
        horizon=bar_horizon,
        as_of=cutoff,
        bars=tuple(row.bar for row in selected),
    )
    snapshot = mathematical_feature_snapshot(
        window,
        signal_alpha=signal_alpha,
        dynamics_window=dynamics_window,
        momentum_fast_bars=momentum_fast_bars,
        momentum_slow_bars=momentum_slow_bars,
        pivot_left_bars=pivot_left_bars,
        pivot_right_bars=pivot_right_bars,
    )
    if snapshot is None:
        raise ReplayValidationError("Gate 2 feature replay failed closed")

    return PointInTimeReplaySnapshot(
        instrument_id=window.instrument_id,
        as_of=cutoff,
        data_cutoff=window.data_cutoff,
        source_row_count=len(selected),
        source_rows_sha256=_source_rows_sha256(selected),
        feature_snapshot=snapshot,
    )


def replay_features_walk_forward(
    records: Sequence[PointInTimeBar],
    *,
    cutoffs: Sequence[datetime],
    bar_horizon: ForecastHorizon,
    history_bars: int = 256,
    **feature_parameters: object,
) -> tuple[PointInTimeReplaySnapshot, ...]:
    """Replay a strictly increasing set of frozen point-in-time cutoffs."""

    replay_cutoffs = tuple(require_utc(item, "cutoff") for item in cutoffs)
    if not replay_cutoffs:
        raise ReplayValidationError("walk-forward replay requires cutoffs")
    if any(
        current <= previous
        for previous, current in zip(
            replay_cutoffs[:-1], replay_cutoffs[1:], strict=True
        )
    ):
        raise ReplayValidationError("walk-forward cutoffs must be strictly increasing")

    return tuple(
        replay_features_at(
            records,
            as_of=cutoff,
            bar_horizon=bar_horizon,
            history_bars=history_bars,
            **feature_parameters,
        )
        for cutoff in replay_cutoffs
    )


def forward_direction_label(
    records: Sequence[PointInTimeBar],
    *,
    feature_cutoff: datetime,
    read_at: datetime,
    bar_horizon: ForecastHorizon,
    outcome_horizon_seconds: int,
    positive_threshold: Decimal = D("0"),
) -> ForwardDirectionLabel:
    """Reveal a frozen forward-return label only after its row is available."""

    cutoff = require_utc(feature_cutoff, "feature_cutoff")
    observed_at = require_utc(read_at, "read_at")
    if outcome_horizon_seconds < 1:
        raise ReplayValidationError("outcome horizon must be positive")
    if outcome_horizon_seconds % bar_horizon.seconds:
        raise ReplayValidationError("outcome horizon must align to the bar horizon")
    if not positive_threshold.is_finite():
        raise ReplayValidationError("outcome threshold must be finite")

    target_at = cutoff + timedelta(seconds=outcome_horizon_seconds)
    causal_rows = tuple(
        row for row in records if row.bar.closed_at <= target_at
    )
    rows = _validate_records(causal_rows, bar_horizon=bar_horizon)
    by_closed_at = {row.bar.closed_at: row for row in rows}
    try:
        base = by_closed_at[cutoff]
        outcome = by_closed_at[target_at]
    except KeyError as exc:
        raise ReplayValidationError("outcome label requires exact boundary rows") from exc
    if base.available_at > cutoff:
        raise ReplayValidationError("feature cutoff row was not causally available")
    if outcome.available_at > observed_at:
        raise ReplayValidationError("outcome label was read before it became available")

    forward_return = outcome.bar.close / base.bar.close - D("1")
    return ForwardDirectionLabel(
        instrument_id=base.instrument_id,
        feature_cutoff=cutoff,
        outcome_at=target_at,
        available_at=outcome.available_at,
        horizon_seconds=outcome_horizon_seconds,
        positive_threshold=positive_threshold,
        forward_return=forward_return,
        positive=forward_return > positive_threshold,
        base_row_sha256=base.source_row_sha256,
        outcome_row_sha256=outcome.source_row_sha256,
    )
