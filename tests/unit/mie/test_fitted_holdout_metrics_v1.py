"""Synthetic-only common-cohort controls; never open a protected holdout."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from app.mie.validation.artifact import ArtifactVerificationError
from app.mie.validation.fitted_holdout_inference_v1 import infer_fitted_holdout_v1
from app.mie.validation.fitted_holdout_metrics_v1 import (
    FittedOOSBaselineBundleV1,
    FittedOOSBaselineEventV1,
    FittedOOSBaselineSlotV1,
    FittedOOSMetricsError,
    FittedOOSOutcomeBundleV1,
    FittedOOSOutcomeEventV1,
    FittedOOSOutcomeSlotV1,
    FittedOOSScorePlanV1,
    score_fitted_holdout_v1,
)
from app.mie.validation.prospective_campaign_v2 import BAR_SECONDS, INSTRUMENTS
from app.mie.validation.prospective_devval_candidate_trials_v1 import (
    DevvalCandidateSourceV1,
    DevvalCandidateTrialV1,
    _FittedModel,
    _ModelBin,
)
from app.mie.validation.replay import ForwardDirectionLabelV2
from tests.unit.mie.test_fitted_holdout_inference_v1 import (
    _synthetic_binding,
    _synthetic_campaign,
    _synthetic_inventory,
    _synthetic_seal,
    feature_plan,
    sha,
    valid_prospective_preregistration,
)

D = Decimal


def _case(*, null_candidate: bool = False):
    source = DevvalCandidateSourceV1(
        commit="a" * 40, tree="b" * 40, manifest_sha256=sha("manifest")
    )
    feature = feature_plan()
    model = _FittedModel(
        trial=DevvalCandidateTrialV1(
            trial_id="trial:001",
            feature="normalized_momentum",
            bin_edges=(D(0),),
            prior_weight=D(2),
            minimum_bin_observations=2,
        ),
        bins=(
            _ModelBin(
                observations=0 if null_candidate else 2,
                positives=0 if null_candidate else 1,
                probability=None if null_candidate else D("0.5"),
            ),
            _ModelBin(observations=8, positives=7, probability=D("0.8")),
        ),
        development_rows_sha256=sha("development"),
        cost_model=valid_prospective_preregistration().cost_model,
    )
    seal = _synthetic_seal(source, model, feature)
    campaign = _synthetic_campaign(seal, feature)
    binding = _synthetic_binding(seal, source, model, feature)
    inventory = _synthetic_inventory(campaign, binding, feature)
    forecast = infer_fitted_holdout_v1(
        inventory_bytes=inventory.canonical_json_bytes(),
        expected_inventory_sha256=inventory.canonical_sha256(),
        campaign_bytes=campaign.canonical_json_bytes(),
        expected_campaign_sha256=campaign.canonical_sha256(),
        binding_bytes=binding.canonical_json_bytes(),
        expected_binding_sha256=binding.canonical_sha256(),
        seal_bytes=seal.canonical_json_bytes(),
        model_bytes=model.canonical_json_bytes(),
        feature_plan=feature,
    )
    baseline_events = []
    outcome_events = []
    for index, (predicted, source_event) in enumerate(
        zip(forecast.contract.events, inventory.events, strict=True)
    ):
        baseline_slots = []
        outcome_slots = []
        for symbol_index, (symbol, prediction, available) in enumerate(
            zip(
                INSTRUMENTS,
                predicted.slots,
                source_event.slots,
                strict=True,
            )
        ):
            baseline_slots.append(
                FittedOOSBaselineSlotV1(
                    instrument_id=symbol,
                    constant_prevalence=D("0.875" if null_candidate else "0.8"),
                    frozen_legacy_score=D("0.4") if symbol_index else D("0.6"),
                    no_skill=D("0.5"),
                )
            )
            outcome_at = predicted.bar_closed_at + timedelta(seconds=BAR_SECONDS)
            result = (index + symbol_index) % 2 == 0
            threshold = seal.outcome_label.positive_threshold
            label = ForwardDirectionLabelV2(
                instrument_id=symbol,
                base_bar_closed_at=predicted.bar_closed_at,
                base_available_at=available.current_bar_available_at,
                decision_at=predicted.decision_at,
                outcome_at=outcome_at,
                outcome_available_at=outcome_at + timedelta(seconds=1),
                read_at=campaign.first_permitted_access_at + timedelta(seconds=1),
                bar_horizon_seconds=BAR_SECONDS,
                horizon_seconds=BAR_SECONDS,
                positive_threshold=threshold,
                forward_return=threshold + (D("0.01") if result else D("-0.01")),
                positive=result,
                base_row_sha256=sha(f"base:{index}:{symbol}"),
                outcome_row_sha256=sha(f"outcome:{index}:{symbol}"),
                base_bar_sha256=sha(f"base-bar:{index}:{symbol}"),
                outcome_bar_sha256=sha(f"outcome-bar:{index}:{symbol}"),
                outcome_window_rows_sha256=sha(f"window:{index}:{symbol}"),
                feature_source_rows_sha256=prediction.source_rows_sha256,
                feature_replay_sha256=prediction.replay_sha256,
                feature_plan_sha256=forecast.contract.feature_plan_sha256,
            )
            outcome_slots.append(
                FittedOOSOutcomeSlotV1(instrument_id=symbol, label=label)
            )
        baseline_events.append(
            FittedOOSBaselineEventV1(
                bar_closed_at=predicted.bar_closed_at,
                slots=(baseline_slots[0], baseline_slots[1]),
            )
        )
        outcome_events.append(
            FittedOOSOutcomeEventV1(
                bar_closed_at=predicted.bar_closed_at,
                slots=(outcome_slots[0], outcome_slots[1]),
            )
        )
    baselines = FittedOOSBaselineBundleV1(
        campaign_sha256=campaign.canonical_sha256(),
        preregistration_sha256=seal.canonical_sha256(),
        baseline_specs=seal.baselines,
        events=tuple(baseline_events),
    )
    outcomes = FittedOOSOutcomeBundleV1(
        campaign_sha256=campaign.canonical_sha256(),
        forecast_sha256=forecast.sha256,
        events=tuple(outcome_events),
    )
    policy = FittedOOSScorePlanV1(
        campaign_sha256=campaign.canonical_sha256(),
        child_chain_sha256=campaign.child_chain_sha256,
        binding_sha256=binding.canonical_sha256(),
        preregistration_sha256=seal.canonical_sha256(),
        candidate_id=seal.candidate.candidate_id,
        baseline_specs=seal.baselines,
        outcome_horizon_seconds=seal.outcome_label.horizon_seconds,
        positive_threshold=seal.outcome_label.positive_threshold,
        reliability_bin_count=seal.evaluation.reliability_bin_count,
        minimum_candidate_events=3,
        minimum_scored_windows=2,
        minimum_brier_advantage=D("0.001"),
        maximum_candidate_ece=D(1),
        uncertainty=seal.evaluation.uncertainty,
        planned_at=seal.created_at,
    )
    arguments = {
        "score_plan_bytes": policy.canonical_json_bytes(),
        "expected_score_plan_sha256": policy.canonical_sha256(),
        "forecast_bytes": forecast.payload,
        "expected_forecast_sha256": forecast.sha256,
        "inventory_bytes": inventory.canonical_json_bytes(),
        "expected_inventory_sha256": inventory.canonical_sha256(),
        "baseline_bytes": baselines.canonical_json_bytes(),
        "expected_baseline_sha256": baselines.canonical_sha256(),
        "outcome_bytes": outcomes.canonical_json_bytes(),
        "expected_outcome_sha256": outcomes.canonical_sha256(),
        "campaign_bytes": campaign.canonical_json_bytes(),
        "expected_campaign_sha256": campaign.canonical_sha256(),
        "binding_bytes": binding.canonical_json_bytes(),
        "expected_binding_sha256": binding.canonical_sha256(),
        "seal_bytes": seal.canonical_json_bytes(),
        "model_bytes": model.canonical_json_bytes(),
        "feature_plan": feature,
    }
    return arguments, policy, baselines, outcomes


def test_complete_common_cohort_has_descriptive_metrics_but_no_authority():
    arguments, policy, _, _ = _case()
    first = score_fitted_holdout_v1(**arguments)
    second = score_fitted_holdout_v1(**arguments)
    assert first.payload == second.payload
    report = first.contract
    assert report.coverage_status == "complete"
    assert report.missing_slot_count == 0
    assert len(report.events) == 3
    assert tuple(window.event_count for window in report.windows) == (0, 0, 3)
    assert report.overall_candidate is not None
    assert report.overall_candidate.sample_count == 6
    assert len(report.overall_baselines) == 3
    assert len(report.overall_deltas) == 3
    assert tuple(item.instrument_id for item in report.symbol_scores) == INSTRUMENTS
    assert not report.descriptive_threshold_screen_passed
    assert all(not window.paired_blocks for window in report.windows[:2])
    if policy.uncertainty.block_length <= 3:
        assert len(report.windows[2].paired_blocks) == 3
        assert all(block.p_value is None for block in report.windows[2].paired_blocks)
    assert not report.prewindow_publication_verified
    assert not report.independent_source_custody_verified
    assert not report.unique_evaluation_attempt_verified
    assert not report.statistical_significance_accepted
    assert not report.formal_oos_accepted
    assert not report.gate4_eligible and not report.execution_authority


def test_missing_candidate_and_outcome_slots_remain_visible_without_metrics():
    arguments, _, _, outcomes = _case(null_candidate=True)
    first, *tail = outcomes.events
    btc, eth = first.slots
    incomplete = outcomes.model_copy(
        update={
            "events": (
                first.model_copy(
                    update={"slots": (btc.model_copy(update={"label": None}), eth)}
                ),
                *tail,
            )
        }
    )
    arguments.update(
        outcome_bytes=incomplete.canonical_json_bytes(),
        expected_outcome_sha256=incomplete.canonical_sha256(),
    )
    result = score_fitted_holdout_v1(**arguments).contract
    assert result.coverage_status == "incomplete"
    assert result.overall_candidate is None
    assert not result.overall_baselines and not result.overall_deltas
    assert not result.symbol_scores
    assert result.events[0].slots[0].missing == ("outcome",)
    assert result.events[0].slots[1].missing == ("candidate_probability",)
    assert all(window.candidate is None for window in result.windows)
    assert not result.descriptive_threshold_screen_passed


def test_reordered_outcome_and_rewritten_no_skill_refuse_even_when_rehashed():
    arguments, _, baselines, outcomes = _case()
    changed = outcomes.model_copy(update={"events": outcomes.events[::-1]})
    with pytest.raises(FittedOOSMetricsError, match="event_order_changed"):
        score_fitted_holdout_v1(
            **{
                **arguments,
                "outcome_bytes": changed.canonical_json_bytes(),
                "expected_outcome_sha256": changed.canonical_sha256(),
            }
        )
    first, *tail = baselines.events
    btc, eth = first.slots
    changed = baselines.model_copy(
        update={
            "events": (
                first.model_copy(
                    update={
                        "slots": (btc.model_copy(update={"no_skill": D("0.6")}), eth)
                    }
                ),
                *tail,
            )
        }
    )
    with pytest.raises(FittedOOSMetricsError, match="baseline_policy_changed"):
        score_fitted_holdout_v1(
            **{
                **arguments,
                "baseline_bytes": changed.canonical_json_bytes(),
                "expected_baseline_sha256": changed.canonical_sha256(),
            }
        )


def test_outcome_read_before_its_child_release_refuses_even_with_new_pin():
    arguments, _, _, outcomes = _case()
    first, *tail = outcomes.events
    btc, eth = first.slots
    assert btc.label is not None
    too_early = btc.label.model_copy(update={"read_at": btc.label.outcome_available_at})
    changed = outcomes.model_copy(
        update={
            "events": (
                first.model_copy(
                    update={
                        "slots": (
                            btc.model_copy(update={"label": too_early}),
                            eth,
                        )
                    }
                ),
                *tail,
            )
        }
    )
    with pytest.raises(FittedOOSMetricsError, match="label_access_too_early"):
        score_fitted_holdout_v1(
            **{
                **arguments,
                "outcome_bytes": changed.canonical_json_bytes(),
                "expected_outcome_sha256": changed.canonical_sha256(),
            }
        )


def test_constant_baseline_cannot_be_replaced_by_another_uniform_probability():
    arguments, _, baselines, _ = _case()
    changed = baselines.model_copy(
        update={
            "events": tuple(
                event.model_copy(
                    update={
                        "slots": tuple(
                            slot.model_copy(update={"constant_prevalence": D("0.6")})
                            for slot in event.slots
                        )
                    }
                )
                for event in baselines.events
            )
        }
    )
    with pytest.raises(FittedOOSMetricsError, match="baseline_policy_changed"):
        score_fitted_holdout_v1(
            **{
                **arguments,
                "baseline_bytes": changed.canonical_json_bytes(),
                "expected_baseline_sha256": changed.canonical_sha256(),
            }
        )


def test_changed_predeclared_plan_bytes_fail_the_original_external_pin():
    arguments, _, _, _ = _case()
    with pytest.raises(ArtifactVerificationError, match="SHA256 mismatch"):
        score_fitted_holdout_v1(
            **{**arguments, "score_plan_bytes": arguments["score_plan_bytes"] + b" "}
        )
