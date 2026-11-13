"""Synthetic policy and arithmetic tests for the legacy baseline."""

from decimal import ROUND_DOWN, Decimal, localcontext

import pytest
from pydantic import ValidationError

from app.mie.validation import (
    prospective_devval_legacy_calibration_policy_v1 as subject,
)
from app.mie.validation.frozen_legacy_raw_scorer_v1 import FrozenLegacyRawScorerConfigV1
from app.mie.validation.prospective_devval_candidate_trials_v1 import (
    DevvalCandidateSourceV1,
)
from tests.unit.mie.test_devval_campaign_study import _feature_plan


@pytest.fixture
def policy_input():
    source = DevvalCandidateSourceV1(
        commit="a" * 40, tree="b" * 40, manifest_sha256="c" * 64
    )
    config = FrozenLegacyRawScorerConfigV1(
        strategy="trend_pullback",
        analysis_version="synthetic-v1",
        source_checkpoint=source,
        source_archive_sha256="d" * 64,
    )
    feature = _feature_plan()
    policy = subject.LegacyCalibrationPolicyV1(
        scorer_config_sha256=config.canonical_sha256(),
        joint_feature_plan_sha256=feature.canonical_sha256,
        outcome_horizon_seconds=feature.outcome_horizon_seconds,
        positive_threshold=feature.positive_threshold,
        source_checkpoint=source,
        source_archive_sha256=config.source_archive_sha256,
        score_cuts=(20, 80, 100),
        prior_alpha=2,
        prior_beta=3,
        minimum_distinct_joint_events=2,
    )
    return policy, {
        "expected_policy_sha256": policy.canonical_sha256(),
        "scorer_config_json": config.canonical_json_bytes(),
        "feature_plan": feature,
        "expected_source_checkpoint": source,
        "expected_source_archive_sha256": config.source_archive_sha256,
    }


def test_exact_policy_pins_match_config_source_and_feature_target(policy_input):
    policy, pins = policy_input
    assert (
        subject.verify_legacy_calibration_policy_v1(
            policy.canonical_json_bytes(), **pins
        )
        == policy
    )
    for field, replacement in (
        ("expected_policy_sha256", "1" * 64),
        ("expected_source_archive_sha256", "2" * 64),
        ("scorer_config_json", pins["scorer_config_json"] + b" "),
    ):
        with pytest.raises(
            subject.LegacyCalibrationPolicyError, match="policy_unavailable"
        ):
            subject.verify_legacy_calibration_policy_v1(
                policy.canonical_json_bytes(), **(pins | {field: replacement})
            )
    for field, replacement in (
        ("positive_threshold", policy.positive_threshold + Decimal("0.01")),
        ("outcome_horizon_seconds", policy.outcome_horizon_seconds * 2),
        ("joint_feature_plan_sha256", "3" * 64),
    ):
        changed = policy.model_copy(update={field: replacement})
        with pytest.raises(
            subject.LegacyCalibrationPolicyError, match="policy_unavailable"
        ):
            subject.verify_legacy_calibration_policy_v1(
                changed.canonical_json_bytes(),
                **(pins | {"expected_policy_sha256": changed.canonical_sha256()}),
            )


@pytest.mark.parametrize(
    ("raw", "interval"),
    (
        (0, (0, 20)),
        (19, (0, 20)),
        (20, (20, 80)),
        (79, (20, 80)),
        (80, (80, 100)),
        (99, (80, 100)),
        (100, (100, 101)),
    ),
)
def test_bins_preserve_zero_and_exact_edges_without_combining_direction(
    policy_input, raw, interval
):
    policy, _ = policy_input
    long_bin = subject.legacy_calibration_score_bin_v1(
        policy, direction="long", raw_score=raw
    )
    short_bin = subject.legacy_calibration_score_bin_v1(
        policy, direction="short", raw_score=raw
    )
    assert (long_bin.lower_inclusive, long_bin.upper_exclusive) == interval
    assert (short_bin.lower_inclusive, short_bin.upper_exclusive) == interval
    assert long_bin != short_bin


@pytest.mark.parametrize(
    ("direction", "raw"),
    (
        ("neutral", 0),
        ("long", None),
        ("long", True),
        ("short", -1),
        ("short", 101),
    ),
)
def test_missing_neutral_and_invalid_scores_have_no_bin(policy_input, direction, raw):
    policy, _ = policy_input
    with pytest.raises(
        subject.LegacyCalibrationPolicyError, match="directional_score_required"
    ):
        subject.legacy_calibration_score_bin_v1(
            policy, direction=direction, raw_score=raw
        )


def test_paired_symbols_are_one_event_and_prior_is_exact_rational(policy_input):
    policy, _ = policy_input
    support = subject.LegacyCalibrationSupportV1(
        directional_symbol_rows=2, positive_symbol_rows=1, distinct_joint_events=1
    )
    unsupported = subject.legacy_calibration_estimate_v1(policy, support=support)
    assert unsupported.status == "insufficient_distinct_events"
    assert unsupported.probability is None
    counts = support.model_copy(
        update={"distinct_joint_events": 2, "directional_symbol_rows": 3}
    )
    estimate = subject.legacy_calibration_estimate_v1(policy, support=counts)
    assert estimate.probability == subject.LegacyCalibrationRationalV1(
        numerator=3, denominator=8
    )
    assert estimate.status == "computed"
    assert estimate.event_support_verified is estimate.execution_authority is False
    with localcontext() as ambient:
        ambient.prec = 2
        ambient.rounding = ROUND_DOWN
        assert (
            subject.legacy_calibration_estimate_v1(
                policy, support=counts
            ).canonical_json_bytes()
            == estimate.canonical_json_bytes()
        )


@pytest.mark.parametrize(
    "changes",
    (
        {"prior_alpha": 0},
        {"prior_beta": 0},
        {"score_cuts": (80, 20)},
        {"score_cuts": (20, 20)},
        {"score_cuts": (0,)},
        {"minimum_distinct_joint_events": 0},
        {"independent_preregistration_verified": True},
    ),
)
def test_invalid_or_authority_claiming_experiment_settings_are_refused(
    policy_input, changes
):
    policy, _ = policy_input
    with pytest.raises(ValidationError):
        subject.LegacyCalibrationPolicyV1.model_validate(policy.model_dump() | changes)
