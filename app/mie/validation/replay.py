from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from datetime import datetime, timedelta
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from itertools import pairwise
from typing import Literal

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
    def validate_point_in_time_boundary(self) -> PointInTimeBar:
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
    def validate_links(self) -> PointInTimeReplaySnapshot:
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
    def validate_label_boundary(self) -> ForwardDirectionLabel:
        if self.outcome_at != self.feature_cutoff + timedelta(
            seconds=self.horizon_seconds
        ):
            raise ValueError("outcome timestamp must match the declared horizon")
        if self.available_at < self.outcome_at:
            raise ValueError("outcome cannot be available before its timestamp")
        if self.positive != (self.forward_return > self.positive_threshold):
            raise ValueError("outcome direction disagrees with its frozen threshold")
        return self


class FrozenFeatureReplayPlanV2(ReplayContract):
    """Independently pinned feature parameters for a measured-receipt label."""

    schema_version: Literal["ctcc.mie.feature_replay_plan.v2"] = (
        "ctcc.mie.feature_replay_plan.v2"
    )
    decimal_precision: Literal[28] = 28
    bar_horizon: ForecastHorizon
    outcome_horizon_seconds: int = Field(ge=1)
    positive_threshold: Decimal
    history_bars: int = Field(ge=21, le=10_000)
    signal_alpha: Decimal = Field(gt=0, le=1)
    dynamics_window: int = Field(ge=5)
    momentum_fast_bars: int = Field(ge=2)
    momentum_slow_bars: int = Field(ge=3)
    pivot_left_bars: int = Field(ge=1)
    pivot_right_bars: int = Field(ge=1)
    feature_version: Literal["mie-gate2-features-v1"] = "mie-gate2-features-v1"
    authority: Literal["offline_shadow_only"] = "offline_shadow_only"
    runtime_consumers: Literal[0] = 0
    execution_authority: Literal[False] = False

    @field_validator("signal_alpha", "positive_threshold")
    @classmethod
    def validate_decimals(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("frozen feature or outcome decimal must be finite")
        return value

    @model_validator(mode="after")
    def validate_dependencies(self) -> FrozenFeatureReplayPlanV2:
        if self.outcome_horizon_seconds % self.bar_horizon.seconds:
            raise ValueError("frozen outcome horizon must align to the bar horizon")
        if self.momentum_fast_bars >= self.momentum_slow_bars:
            raise ValueError("frozen momentum fast window must precede slow window")
        if self.history_bars < max(
            5, self.dynamics_window, self.momentum_slow_bars + 1
        ):
            raise ValueError("frozen history cannot satisfy feature dependencies")
        return self

    @property
    def canonical_sha256(self) -> str:
        return _canonical_sha256(self.model_dump(mode="json"))


class ForwardDirectionLabelV2(ReplayContract):
    """Offline label whose decision and outcome receipts remain distinct."""

    schema_version: Literal["ctcc.mie.forward_direction_label.v2"] = (
        "ctcc.mie.forward_direction_label.v2"
    )
    instrument_id: str = Field(
        min_length=3,
        max_length=64,
        pattern=r"^[A-Z0-9]+(?:-[A-Z0-9]+)+$",
    )
    base_bar_closed_at: datetime
    base_available_at: datetime
    decision_at: datetime
    outcome_at: datetime
    outcome_available_at: datetime
    read_at: datetime
    bar_horizon_seconds: int = Field(ge=1)
    horizon_seconds: int = Field(ge=1)
    positive_threshold: Decimal
    forward_return: Decimal
    positive: StrictBool
    base_row_sha256: str = Field(pattern=SHA256_PATTERN)
    outcome_row_sha256: str = Field(pattern=SHA256_PATTERN)
    base_bar_sha256: str = Field(pattern=SHA256_PATTERN)
    outcome_bar_sha256: str = Field(pattern=SHA256_PATTERN)
    outcome_window_rows_sha256: str = Field(pattern=SHA256_PATTERN)
    feature_source_rows_sha256: str = Field(pattern=SHA256_PATTERN)
    feature_replay_sha256: str = Field(pattern=SHA256_PATTERN)
    feature_plan_sha256: str = Field(pattern=SHA256_PATTERN)
    authority: Literal["offline_label_only"] = "offline_label_only"
    runtime_consumers: Literal[0] = 0
    execution_authority: Literal[False] = False

    @field_validator(
        "base_bar_closed_at",
        "base_available_at",
        "decision_at",
        "outcome_at",
        "outcome_available_at",
        "read_at",
    )
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
    def validate_label_boundary(self) -> ForwardDirectionLabelV2:
        if self.horizon_seconds % self.bar_horizon_seconds:
            raise ValueError("outcome horizon must align to the bar horizon")
        if self.base_available_at < self.base_bar_closed_at:
            raise ValueError("base bar cannot be available before it closes")
        if (
            not self.base_available_at
            <= self.decision_at
            < (self.base_bar_closed_at + timedelta(seconds=self.bar_horizon_seconds))
        ):
            raise ValueError("decision must follow base receipt before the next close")
        if self.outcome_at != self.base_bar_closed_at + timedelta(
            seconds=self.horizon_seconds
        ):
            raise ValueError("outcome timestamp must match the declared horizon")
        if self.outcome_available_at < self.outcome_at:
            raise ValueError("outcome cannot be available before its timestamp")
        if self.read_at < max(self.decision_at, self.outcome_available_at):
            raise ValueError("outcome was read before decision or availability")
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
    if any(current <= previous for previous, current in pairwise(closed_at)):
        raise ReplayValidationError("replay rows must be strictly chronological")

    expected_step = timedelta(seconds=bar_horizon.seconds)
    if any(
        current - previous != expected_step for previous, current in pairwise(closed_at)
    ):
        raise ReplayValidationError("replay rejects missing or irregular bars")

    if any(
        not _aligned_to_horizon(row.bar.closed_at, bar_horizon.seconds) for row in rows
    ):
        raise ReplayValidationError("replay bars must align to the declared horizon")
    return rows


def _source_rows_sha256(records: Sequence[PointInTimeBar]) -> str:
    return _canonical_sha256([row.model_dump(mode="json") for row in records])


def replay_features_at(
    records: Sequence[PointInTimeBar],
    *,
    as_of: datetime,
    bar_horizon: ForecastHorizon,
    history_bars: int = 256,
    signal_alpha: Decimal = D("0.25"),  # noqa: B008 - D constructs immutable exact Decimal values.
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
        raise ReplayValidationError(
            "history window cannot satisfy feature dependencies"
        )
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
    if any(current <= previous for previous, current in pairwise(replay_cutoffs)):
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
    positive_threshold: Decimal = D("0"),  # noqa: B008 - D constructs immutable exact Decimal values.
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
    causal_rows = tuple(row for row in records if row.bar.closed_at <= target_at)
    rows = _validate_records(causal_rows, bar_horizon=bar_horizon)
    by_closed_at = {row.bar.closed_at: row for row in rows}
    try:
        base = by_closed_at[cutoff]
        outcome = by_closed_at[target_at]
    except KeyError as exc:
        raise ReplayValidationError(
            "outcome label requires exact boundary rows"
        ) from exc
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


def forward_direction_label_v2(
    records: Sequence[PointInTimeBar],
    *,
    replay_snapshot: PointInTimeReplaySnapshot,
    feature_plan: FrozenFeatureReplayPlanV2,
    expected_feature_plan_sha256: str,
    decision_at: datetime,
    read_at: datetime,
    bar_horizon: ForecastHorizon,
    outcome_horizon_seconds: int,
    positive_threshold: Decimal = D("0"),  # noqa: B008 - D constructs immutable exact Decimal values.
) -> ForwardDirectionLabelV2:
    """Label a measured decision only after a pinned source replay and outcome read.

    The plan SHA256 must be retained independently of these caller-supplied
    objects. This pure function does not authenticate the original market bytes.
    """

    if type(feature_plan) is not FrozenFeatureReplayPlanV2:
        raise ReplayValidationError("V2 label requires the exact feature plan")
    try:
        plan = FrozenFeatureReplayPlanV2.model_validate(
            feature_plan.model_dump(mode="python")
        )
        snapshot = PointInTimeReplaySnapshot.model_validate(
            replay_snapshot.model_dump(mode="python")
        )
        horizon = ForecastHorizon.model_validate(bar_horizon.model_dump(mode="python"))
    except (AttributeError, ValidationError, ValueError) as exc:
        raise ReplayValidationError(
            "V2 label input contract validation failed"
        ) from exc
    if (
        type(expected_feature_plan_sha256) is not str
        or plan.canonical_sha256 != expected_feature_plan_sha256
    ):
        raise ReplayValidationError("frozen V2 feature plan pin changed")
    decision = require_utc(decision_at, "decision_at")
    read = require_utc(read_at, "read_at")
    if snapshot.as_of != decision:
        raise ReplayValidationError("feature replay decision time differs")
    if snapshot.feature_snapshot.feature_version != plan.feature_version:
        raise ReplayValidationError("feature replay version differs from frozen plan")
    if (
        horizon != plan.bar_horizon
        or outcome_horizon_seconds != plan.outcome_horizon_seconds
        or positive_threshold != plan.positive_threshold
    ):
        raise ReplayValidationError("V2 label definition differs from frozen plan")
    if (
        isinstance(outcome_horizon_seconds, bool)
        or not isinstance(outcome_horizon_seconds, int)
        or outcome_horizon_seconds < 1
        or outcome_horizon_seconds % horizon.seconds
    ):
        raise ReplayValidationError("outcome horizon must align to the bar horizon")
    if type(positive_threshold) is not Decimal or not positive_threshold.is_finite():
        raise ReplayValidationError("outcome threshold must be a finite Decimal")

    base_closed_at = snapshot.data_cutoff
    target_at = base_closed_at + timedelta(seconds=outcome_horizon_seconds)
    if (
        not base_closed_at
        <= decision
        < (base_closed_at + timedelta(seconds=horizon.seconds))
    ):
        raise ReplayValidationError("decision must precede the next bar close")
    try:
        causal_rows = tuple(row for row in records if row.bar.closed_at <= target_at)
    except (AttributeError, TypeError) as exc:
        raise ReplayValidationError("V2 label source row validation failed") from exc
    rows = _validate_records(causal_rows, bar_horizon=horizon)
    by_closed_at = {row.bar.closed_at: row for row in rows}
    try:
        base = by_closed_at[base_closed_at]
        outcome = by_closed_at[target_at]
    except KeyError as exc:
        raise ReplayValidationError(
            "V2 outcome label requires exact boundary rows"
        ) from exc
    if base.instrument_id != snapshot.instrument_id:
        raise ReplayValidationError("V2 label instrument differs from feature replay")
    if base.available_at > decision:
        raise ReplayValidationError("base row was not available at decision time")
    if any(row.available_at > read for row in rows):
        raise ReplayValidationError("outcome was read before it became available")

    with localcontext(Context(prec=plan.decimal_precision, rounding=ROUND_HALF_EVEN)):
        rebuilt = replay_features_at(
            rows,
            as_of=decision,
            bar_horizon=horizon,
            history_bars=plan.history_bars,
            signal_alpha=plan.signal_alpha,
            dynamics_window=plan.dynamics_window,
            momentum_fast_bars=plan.momentum_fast_bars,
            momentum_slow_bars=plan.momentum_slow_bars,
            pivot_left_bars=plan.pivot_left_bars,
            pivot_right_bars=plan.pivot_right_bars,
        )
    if rebuilt != snapshot or rebuilt.replay_sha256 != snapshot.replay_sha256:
        raise ReplayValidationError("V2 feature replay differs from original source")

    with localcontext(Context(prec=50, rounding=ROUND_HALF_EVEN)):
        forward_return = outcome.bar.close / base.bar.close - D("1")
    outcome_window = tuple(
        row for row in rows if base_closed_at <= row.bar.closed_at <= target_at
    )
    return ForwardDirectionLabelV2(
        instrument_id=base.instrument_id,
        base_bar_closed_at=base_closed_at,
        base_available_at=base.available_at,
        decision_at=decision,
        outcome_at=target_at,
        outcome_available_at=outcome.available_at,
        read_at=read,
        bar_horizon_seconds=horizon.seconds,
        horizon_seconds=outcome_horizon_seconds,
        positive_threshold=positive_threshold,
        forward_return=forward_return,
        positive=forward_return > positive_threshold,
        base_row_sha256=base.source_row_sha256,
        outcome_row_sha256=outcome.source_row_sha256,
        base_bar_sha256=_canonical_sha256(base.bar.model_dump(mode="json")),
        outcome_bar_sha256=_canonical_sha256(outcome.bar.model_dump(mode="json")),
        outcome_window_rows_sha256=_canonical_sha256(
            [row.model_dump(mode="json") for row in outcome_window]
        ),
        feature_source_rows_sha256=rebuilt.source_rows_sha256,
        feature_replay_sha256=rebuilt.replay_sha256,
        feature_plan_sha256=plan.canonical_sha256,
    )
