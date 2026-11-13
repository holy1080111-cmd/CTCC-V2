"""Explicit legacy calibration settings and exact arithmetic. No data access."""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
from math import gcd
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from app.mie.validation.artifact import _verify
from app.mie.validation.contracts import Gate3Contract, Sha256
from app.mie.validation.frozen_legacy_raw_scorer_v1 import _checked_config
from app.mie.validation.prospective_devval_candidate_trials_v1 import (
    DevvalCandidateSourceV1,
)
from app.mie.validation.replay import FrozenFeatureReplayPlanV2

_MAX_POLICY_BYTES = 8 * 1024
# The joint study bounds its roster at 4,096 two-symbol slots.
_MAX_EVENTS = 4096
_MAX_ROWS = 2 * _MAX_EVENTS
_MAX_PRIOR_COUNT = 1_000_000
_MAX_DENOMINATOR = _MAX_ROWS + 2 * _MAX_PRIOR_COUNT
_Cut = Annotated[int, Field(ge=1, le=100)]
_PriorCount = Annotated[int, Field(ge=1, le=_MAX_PRIOR_COUNT)]
_EventMinimum = Annotated[int, Field(ge=1, le=_MAX_EVENTS)]
Direction = Literal["long", "short"]


class LegacyCalibrationPolicyError(ValueError):
    """Bounded refusal without printing experiment values or input payloads."""


class LegacyCalibrationPolicyV1(Gate3Contract):
    """All experiment settings are explicit. This contract cannot preregister them."""

    schema_version: Literal["ctcc.mie.legacy_calibration_policy.v1"] = (
        "ctcc.mie.legacy_calibration_policy.v1"
    )
    scorer_config_sha256: Sha256
    joint_feature_plan_sha256: Sha256
    outcome_horizon_seconds: int = Field(ge=1)
    positive_threshold: Decimal
    source_checkpoint: DevvalCandidateSourceV1
    source_archive_sha256: Sha256
    score_cuts: tuple[_Cut, ...] = Field(max_length=100)
    prior_alpha: _PriorCount
    prior_beta: _PriorCount
    minimum_distinct_joint_events: _EventMinimum
    target_rule: Literal["forward_return_strictly_greater_than_threshold"] = (
        "forward_return_strictly_greater_than_threshold"
    )
    bin_rule: Literal["direction_and_half_open_integer_score"] = (
        "direction_and_half_open_integer_score"
    )
    prior_rule: Literal["beta_posterior_mean_exact_rational"] = (
        "beta_posterior_mean_exact_rational"
    )
    support_rule: Literal["symbol_row_counts_with_distinct_joint_event_floor"] = (
        "symbol_row_counts_with_distinct_joint_event_floor"
    )
    independent_preregistration_verified: Literal[False] = False
    execution_authority: Literal[False] = False

    @field_validator("positive_threshold")
    @classmethod
    def bounded_exact_threshold(cls, value: Decimal) -> Decimal:
        parts = value.as_tuple()
        if not value.is_finite() or (
            len(parts.digits) + abs(parts.exponent) > _MAX_POLICY_BYTES
        ):
            raise ValueError("calibration target is outside its canonical byte bound")
        return value

    @model_validator(mode="after")
    def ordered_score_cuts(self) -> LegacyCalibrationPolicyV1:
        if any(a >= b for a, b in zip(self.score_cuts, self.score_cuts[1:])):
            raise ValueError("calibration cuts must be strictly increasing")
        return self


@dataclass(frozen=True, slots=True)
class LegacyCalibrationScoreBinV1:
    direction: Direction
    index: int
    lower_inclusive: int
    upper_exclusive: int


class LegacyCalibrationSupportV1(Gate3Contract):
    """Arithmetic inputs. Counts alone cannot authenticate distinct events."""

    directional_symbol_rows: int = Field(ge=0, le=_MAX_ROWS)
    positive_symbol_rows: int = Field(ge=0, le=_MAX_ROWS)
    distinct_joint_events: int = Field(ge=0, le=_MAX_EVENTS)

    @model_validator(mode="after")
    def coherent_support(self) -> LegacyCalibrationSupportV1:
        if not (
            self.distinct_joint_events
            <= self.directional_symbol_rows
            <= 2 * self.distinct_joint_events
            and self.positive_symbol_rows <= self.directional_symbol_rows
        ):
            raise ValueError("calibration support counts disagree")
        return self


class LegacyCalibrationRationalV1(Gate3Contract):
    numerator: int = Field(ge=1, le=_MAX_DENOMINATOR)
    denominator: int = Field(ge=2, le=_MAX_DENOMINATOR)

    @model_validator(mode="after")
    def reduced_probability(self) -> LegacyCalibrationRationalV1:
        if (
            self.numerator >= self.denominator
            or gcd(self.numerator, self.denominator) != 1
        ):
            raise ValueError(
                "calibration probability must be a reduced proper fraction"
            )
        return self


class LegacyCalibrationEstimateV1(Gate3Contract):
    policy_sha256: Sha256
    support: LegacyCalibrationSupportV1
    minimum_distinct_joint_events: _EventMinimum
    status: Literal["computed", "insufficient_distinct_events"]
    probability: LegacyCalibrationRationalV1 | None
    event_support_verified: Literal[False] = False
    predictive_oos_eligible: Literal[False] = False
    execution_authority: Literal[False] = False

    @model_validator(mode="after")
    def consistent_disposition(self) -> LegacyCalibrationEstimateV1:
        supported = (
            self.support.distinct_joint_events >= self.minimum_distinct_joint_events
        )
        if (self.status == "computed") != supported or (
            self.probability is not None
        ) != supported:
            raise ValueError("calibration estimate disposition differs from support")
        return self


def verify_legacy_calibration_policy_v1(
    payload: bytes,
    *,
    expected_policy_sha256: str,
    scorer_config_json: bytes,
    feature_plan: FrozenFeatureReplayPlanV2,
    expected_source_checkpoint: DevvalCandidateSourceV1,
    expected_source_archive_sha256: str,
) -> LegacyCalibrationPolicyV1:
    """Verify supplied policy/config bytes against the pinned replay target."""
    if (
        type(payload) is not bytes
        or not 0 < len(payload) <= _MAX_POLICY_BYTES
        or type(expected_policy_sha256) is not str
        or type(feature_plan) is not FrozenFeatureReplayPlanV2
        or type(expected_source_checkpoint) is not DevvalCandidateSourceV1
        or type(expected_source_archive_sha256) is not str
    ):
        raise LegacyCalibrationPolicyError("legacy_calibration_policy_unavailable")
    try:
        policy = _verify(
            payload,
            expected_sha256=expected_policy_sha256,
            contract_type=LegacyCalibrationPolicyV1,
        )
        plan = FrozenFeatureReplayPlanV2.model_validate(
            feature_plan.model_dump(mode="python")
        )
        source = DevvalCandidateSourceV1.model_validate(expected_source_checkpoint)
        _checked_config(
            scorer_config_json,
            expected_config_sha256=policy.scorer_config_sha256,
            expected_source_checkpoint=source,
            expected_source_archive_sha256=expected_source_archive_sha256,
        )
        if (
            policy.joint_feature_plan_sha256 != plan.canonical_sha256
            or policy.outcome_horizon_seconds != plan.outcome_horizon_seconds
            or policy.positive_threshold != plan.positive_threshold
            or policy.source_checkpoint != source
            or policy.source_archive_sha256 != expected_source_archive_sha256
        ):
            raise ValueError("legacy calibration policy target or source changed")
    except (ArithmeticError, TypeError, ValueError):
        raise LegacyCalibrationPolicyError(
            "legacy_calibration_policy_unavailable"
        ) from None
    return policy


def legacy_calibration_score_bin_v1(
    policy: LegacyCalibrationPolicyV1, *, direction: Direction, raw_score: int
) -> LegacyCalibrationScoreBinV1:
    """Locate one bounded directional integer without interpreting missing data."""
    policy = LegacyCalibrationPolicyV1.model_validate(policy)
    if (
        type(direction) is not str
        or direction not in ("long", "short")
        or (type(raw_score) is not int or not 0 <= raw_score <= 100)
    ):
        raise LegacyCalibrationPolicyError(
            "legacy_calibration_directional_score_required"
        )
    index = bisect_right(policy.score_cuts, raw_score)
    boundaries = (0, *policy.score_cuts, 101)
    return LegacyCalibrationScoreBinV1(
        direction, index, boundaries[index], boundaries[index + 1]
    )


def legacy_calibration_estimate_v1(
    policy: LegacyCalibrationPolicyV1, *, support: LegacyCalibrationSupportV1
) -> LegacyCalibrationEstimateV1:
    """Compute a reduced Beta mean once the supplied event count meets policy."""
    policy = LegacyCalibrationPolicyV1.model_validate(policy)
    support = LegacyCalibrationSupportV1.model_validate(support)
    minimum = policy.minimum_distinct_joint_events
    probability = None
    if support.distinct_joint_events >= minimum:
        exact = Fraction(
            support.positive_symbol_rows + policy.prior_alpha,
            support.directional_symbol_rows + policy.prior_alpha + policy.prior_beta,
        )
        probability = LegacyCalibrationRationalV1(
            numerator=exact.numerator, denominator=exact.denominator
        )
    return LegacyCalibrationEstimateV1(
        policy_sha256=policy.canonical_sha256(),
        support=support,
        minimum_distinct_joint_events=minimum,
        status="insufficient_distinct_events" if probability is None else "computed",
        probability=probability,
    )
