"""Synthetic controls for the isolated Development-only N191 fit."""

from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.mie.validation import prospective_devval_development_fit_v1 as subject
from app.mie.validation.prospective_devval_campaign_study_v1 import (
    DevvalCampaignStudyFoldV1,
)
from app.mie.validation.prospective_devval_candidate_trials_v1 import (
    DevvalCandidateSourceV1,
)
from app.mie.validation.prospective_devval_legacy_calibration_policy_v1 import (
    LegacyCalibrationPolicyV1,
)
from app.mie.validation.replay import ForwardDirectionLabelV2

_UTC = UTC
_START = datetime(2026, 1, 1, tzinfo=_UTC)
_SHA = "a" * 64
_SOURCE = DevvalCandidateSourceV1(
    commit="1" * 40, tree="2" * 40, manifest_sha256="3" * 64
)


def _label(symbol, close, decision, positive):
    return ForwardDirectionLabelV2(
        instrument_id=symbol,
        base_bar_closed_at=close,
        base_available_at=close,
        decision_at=decision,
        outcome_at=close + timedelta(hours=1),
        outcome_available_at=close + timedelta(hours=1, minutes=1),
        read_at=close + timedelta(hours=1, minutes=5),
        bar_horizon_seconds=3600,
        horizon_seconds=3600,
        positive_threshold=Decimal(0),
        forward_return=Decimal("0.01") if positive else Decimal("-0.01"),
        positive=positive,
        base_row_sha256=_SHA,
        outcome_row_sha256=_SHA,
        base_bar_sha256=_SHA,
        outcome_bar_sha256=_SHA,
        outcome_window_rows_sha256=_SHA,
        feature_source_rows_sha256=_SHA,
        feature_replay_sha256=_SHA,
        feature_plan_sha256="8" * 64,
    )


def _side(
    symbol, close, decision, *, positive, status, score=None, direction="neutral"
):
    return subject.DevelopmentLegacySideV1(
        instrument_id=symbol,
        feature_history_start_at=close - timedelta(hours=4),
        feature_replay_sha256=_SHA,
        label=_label(symbol, close, decision, positive),
        status=status,
        direction=direction,
        raw_score=score,
        raw_result_sha256=None if status == "missing" else "b" * 64,
    )


def _input_and_policy():
    times = (0, 1, 10, 20)
    definitions = (
        ((True, "directional", 30, "long"), (False, "neutral", None, "neutral")),
        ((False, "directional", 40, "long"), (False, "missing", None, "neutral")),
        ((True, "directional", 35, "long"), (False, "directional", 80, "short")),
        ((True, "directional", 20, "long"), (True, "directional", 70, "short")),
    )
    slots = []
    for ordinal, (hour, pair) in enumerate(zip(times, definitions, strict=True)):
        close = _START + timedelta(hours=hour)
        decision = close + timedelta(minutes=1)
        sides = tuple(
            _side(
                symbol,
                close,
                decision,
                positive=value[0],
                status=value[1],
                score=value[2],
                direction=value[3],
            )
            for symbol, value in zip(
                ("BTC-USDT-SWAP", "ETH-USDT-SWAP"), pair, strict=True
            )
        )
        slots.append(
            subject.DevelopmentLegacySlotV1(
                ordinal=ordinal,
                event_closed_at=close,
                decision_at=decision,
                sides=sides,
            )
        )
    folds = (
        DevvalCampaignStudyFoldV1(
            training_indices=(0, 1, 2, 3),
            validation_indices=(4, 5),
            purged_indices=(),
            prior_embargoed_indices=(),
            embargoed_indices=(),
        ),
        DevvalCampaignStudyFoldV1(
            training_indices=(0, 1, 2, 3, 4, 5),
            validation_indices=(6, 7),
            purged_indices=(),
            prior_embargoed_indices=(),
            embargoed_indices=(),
        ),
    )
    original = subject.DevelopmentLegacyFitInputV1(
        study_plan_sha256="4" * 64,
        schedule_sha256="5" * 64,
        coverage_sha256="6" * 64,
        batch_sha256="7" * 64,
        completion_sha256="8" * 64,
        root_binding_sha256="9" * 64,
        row_inventory_sha256="a" * 64,
        source_checkpoint=_SOURCE,
        source_archive_sha256="9" * 64,
        feature_plan_sha256="8" * 64,
        scorer_config_sha256="7" * 64,
        development_event_times_sha256="d" * 64,
        slots=tuple(slots),
        development_folds=folds,
    )
    policy = LegacyCalibrationPolicyV1(
        scorer_config_sha256=original.scorer_config_sha256,
        joint_feature_plan_sha256=original.feature_plan_sha256,
        outcome_horizon_seconds=3600,
        positive_threshold=Decimal(0),
        source_checkpoint=_SOURCE,
        source_archive_sha256=original.source_archive_sha256,
        score_cuts=(50,),
        prior_alpha=1,
        prior_beta=1,
        minimum_distinct_joint_events=2,
    )
    return original, policy


def _fit(monkeypatch, original, policy):
    monkeypatch.setattr(
        subject,
        "verify_legacy_calibration_policy_v1",
        lambda *args, **kwargs: policy,
    )
    return subject.fit_development_legacy_calibration_v1(
        original.canonical_json_bytes(),
        expected_input_sha256=original.canonical_sha256(),
        policy_payload=policy.canonical_json_bytes(),
        expected_policy_sha256=policy.canonical_sha256(),
        scorer_config_json=b"{}",
        feature_plan=object(),
    ).contract


def test_only_development_fit_exact_beta_and_unsupported(monkeypatch):
    original, policy = _input_and_policy()
    result = _fit(monkeypatch, original, policy)
    assert len(result.fold_fits) == 2
    first = result.fold_fits[0]
    long_zero = next(
        item for item in first.bins if item.direction == "long" and item.index == 0
    )
    assert long_zero.estimate.status == "computed"
    assert long_zero.estimate.probability.numerator == 1
    assert long_zero.estimate.probability.denominator == 2
    assert [item.status for item in first.heldout] == ["computed", "unsupported"]
    assert [item.status for item in result.fold_fits[1].heldout] == [
        "computed",
        "unsupported",
    ]
    final_long = next(
        item
        for item in result.final_development_bins
        if item.direction == "long" and item.index == 0
    )
    final_short = next(
        item
        for item in result.final_development_bins
        if item.direction == "short" and item.index == 1
    )
    assert (
        final_long.estimate.probability.numerator,
        final_long.estimate.probability.denominator,
    ) == (2, 3)
    assert (
        final_short.estimate.probability.numerator,
        final_short.estimate.probability.denominator,
    ) == (1, 2)
    empty_short = next(
        item for item in first.bins if item.direction == "short" and item.index == 1
    )
    assert empty_short.estimate.status == "insufficient_distinct_events"
    assert empty_short.estimate.support.directional_symbol_rows == 0
    assert empty_short.estimate.probability is None
    assert result.validation_evaluated is False
    assert result.predictive_oos_eligible is False


def test_development_heldout_preserves_neutral_and_missing(monkeypatch):
    original, policy = _input_and_policy()
    result = _fit(monkeypatch, original, policy)
    bins = result.fold_fits[0].bins
    neutral = subject._heldout(
        original.slots[0], original.slots[0].sides[1], bins, policy
    )
    missing = subject._heldout(
        original.slots[1], original.slots[1].sides[1], bins, policy
    )
    assert (neutral.status, neutral.probability) == ("neutral", None)
    assert (missing.status, missing.probability) == ("missing", None)


def test_cross_fold_label_read_embargo_is_refused():
    original, _ = _input_and_policy()
    wire = original.model_dump(mode="python")
    wire["slots"][2]["sides"][0]["feature_history_start_at"] = _START + timedelta(
        hours=2
    )
    with pytest.raises(ValueError, match="Development fold chronology changed"):
        subject.DevelopmentLegacyFitInputV1.model_validate(wire)


def test_isolated_input_rejects_validation_field_and_evaluator_has_no_study_argument():
    original, _ = _input_and_policy()
    wire = original.model_dump(mode="python")
    wire["validation"] = ("forbidden",)
    with pytest.raises(ValueError):
        subject.DevelopmentLegacyFitInputV1.model_validate(wire)
    parameters = inspect.signature(
        subject.fit_development_legacy_calibration_v1
    ).parameters
    assert "study" not in parameters
    assert "validation" not in parameters
    assert "validation_rows_present" not in parameters


def test_policy_target_mismatch_refuses_without_validation(monkeypatch):
    original, policy = _input_and_policy()
    changed = policy.model_copy(update={"outcome_horizon_seconds": 7200})
    with pytest.raises(
        subject.DevelopmentLegacyFitError, match="development_legacy_fit_refused"
    ):
        _fit(monkeypatch, original, changed)
