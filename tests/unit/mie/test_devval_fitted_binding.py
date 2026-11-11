"""Synthetic controls for a fitted D/V candidate's prospective seal.

The full minute grid is deliberately synthetic. These tests establish only
contract binding and refusal behavior, never independent custody or OOS.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from app.domain.source_primitives import sha
from app.mie.validation.artifact import freeze_prospective_preregistration
from app.mie.validation.contracts import (
    BarConstruction,
    BaselineKind,
    DatasetIdentity,
    FeatureSpec,
    FrozenParameter,
    FrozenTrial,
    OutcomeKind,
    OutcomeLabelSpec,
)
from app.mie.validation.prospective import (
    DevelopmentValidationSplit,
    Gate3ProspectivePreregistration,
    ProspectiveHoldoutSpec,
)
from app.mie.validation.prospective_devval_campaign_batch_v1 import _build_batch
from app.mie.validation.prospective_devval_campaign_plan_v1 import (
    ProspectiveDevvalCampaignPlanV1,
    freeze_prospective_devval_campaign_v1,
)
from app.mie.validation.prospective_devval_campaign_study_v1 import _prepare_study
from app.mie.validation.prospective_devval_candidate_trials_v1 import (
    DevvalCandidateTrialPlanV1,
)
from app.mie.validation.prospective_devval_fitted_binding_v1 import (
    DevvalFittedBindingError,
    build_devval_candidate_binding,
    verify_devval_candidate_binding,
)
from tests.unit.mie.test_devval_campaign_batch import (  # noqa: F401
    _ns,
    full_grid,
)
from tests.unit.mie.test_devval_campaign_study import _study_plan
from tests.unit.mie.test_devval_candidate_trials import _fit, _inputs, _with_outcomes
from tests.unit.mie.test_gate3_prospective import valid_prospective_preregistration


def _whole_minute(seconds: int) -> int:
    return (seconds + 59) // 60 * 60


@pytest.fixture(scope="module")
def aligned_synthetic_case(full_grid):  # noqa: F811 - Imported pytest fixture.
    """Retain the original synthetic tape with a declared 24-hour D/V gap."""
    original_campaign, original_rows, pins = full_grid
    old_plan = original_campaign.plan
    shift = timedelta(hours=20)
    new_validation = old_plan.validation.model_copy(
        update={
            "start_at": old_plan.validation.start_at + shift,
            "end_at": old_plan.validation.end_at + shift,
        }
    )
    new_plan = ProspectiveDevvalCampaignPlanV1.model_validate(
        old_plan.model_copy(update={"validation": new_validation}).model_dump()
    )
    campaign = freeze_prospective_devval_campaign_v1(new_plan).contract
    shifted = []
    for item in original_rows:
        is_validation = item.row.bar.closed_at >= old_plan.validation.start_at
        offset = shift if is_validation else timedelta(0)
        opening = (
            old_plan.validation.start_at
            if is_validation
            else old_plan.development.start_at
        )
        minute = (item.row.bar.closed_at - opening) // timedelta(minutes=1)
        is_btc = item.row.instrument_id == "BTC-USDT-SWAP"
        price = (
            Decimal(100) + Decimal(minute) / Decimal(300)
            if is_btc
            else Decimal(200) - Decimal(minute) / Decimal(300)
        )
        pin = sha(
            b"n151-causal-synthetic:"
            + bytes.fromhex(item.row.source_row_sha256)
            + str(price).encode()
            + str(offset).encode()
        )
        closed = item.row.bar.closed_at + offset
        available = item.row.available_at + offset
        shifted.append(
            item.model_copy(
                update={
                    "row": item.row.model_copy(
                        update={
                            "source_row_id": "synthetic:" + pin,
                            "source_row_sha256": pin,
                            "available_at": available,
                            "bar": item.row.bar.model_copy(
                                update={
                                    "closed_at": closed,
                                    "open": price,
                                    "high": price + Decimal("0.25"),
                                    "low": price - Decimal("0.25"),
                                    "close": price,
                                }
                            ),
                        }
                    ),
                    "availability": item.availability.model_copy(
                        update={
                            "source_row_sha256": pin,
                            "receipt_sha256": pin,
                            "bar_closed_at": closed,
                            "observed_at": available,
                            "retrieved_at": available,
                            "available_at": available,
                        }
                    ),
                }
            )
        )
    shifted_pins = {
        **pins,
        "validation_cutoff_utc_ns": _ns(new_validation.end_at) + 2_000_000_000,
    }
    batch = _build_batch(campaign, tuple(shifted), **shifted_pins)
    study_plan = _study_plan(batch)
    original_study = _prepare_study(
        batch,
        plan=study_plan,
        expected_plan_sha256=study_plan.canonical_sha256(),
    )
    plan, legacy, source = _inputs(original_study)
    first = plan.trials[0].model_copy(
        update={"bin_edges": (Decimal(0),), "prior_weight": Decimal(2)}
    )
    trials = (first, *plan.trials[1:])
    registry = plan.trial_registry.model_copy(
        update={
            "trials": tuple(
                FrozenTrial(
                    trial_id=trial.trial_id,
                    configuration_sha256=trial.canonical_sha256(),
                )
                for trial in trials
            )
        }
    )
    plan = DevvalCandidateTrialPlanV1.model_validate(
        plan.model_copy(
            update={"trials": trials, "trial_registry": registry}
        ).model_dump()
    )
    trial_result = _fit(original_study, plan, legacy, source)
    assert trial_result.contract.status == "candidate_ready_for_separate_freeze"
    assert batch.contract.source_commit == source.commit
    assert batch.contract.source_tree == source.tree
    assert batch.contract.source_manifest_sha256 == source.manifest_sha256
    return batch, original_study, trial_result


def _formal_preregistration(
    batch, study, trial_result, *, frozen_at=None, holdout_start=None
):
    basis = valid_prospective_preregistration()
    captured = batch.contract
    prepared = study.contract
    fitted = trial_result.contract
    model = fitted.selected_model
    assert model is not None and fitted.candidate is not None
    development = captured.series[0].aggregation.plan.window
    validation = captured.series[2].aggregation.plan.window
    feature_plan = prepared.plan.feature_plan
    if frozen_at is None:
        frozen_at = validation.end_at + timedelta(minutes=1)
    if holdout_start is None:
        holdout_start = validation.end_at + timedelta(days=3)
    holdout_end = holdout_start + timedelta(days=1)
    split = DevelopmentValidationSplit(
        development=development,
        validation=validation,
        purge_seconds=_whole_minute(prepared.plan.purge_seconds),
        embargo_seconds=_whole_minute(prepared.plan.embargo_seconds),
        max_feature_dependency_seconds=_whole_minute(
            prepared.plan.feature_dependency_seconds
        ),
        label_dependency_seconds=_whole_minute(prepared.plan.label_dependency_seconds),
    )
    legacy_baselines = tuple(
        item.model_copy(
            update={
                "configuration_sha256": fitted.legacy_source_sha256
                if item.kind is BaselineKind.FROZEN_LEGACY_SCORE
                else item.configuration_sha256
            }
        )
        for item in basis.baselines
    )
    seal = Gate3ProspectivePreregistration(
        preregistration_id="gate3:synthetic-devval-fitted:v1",
        created_at=frozen_at + timedelta(minutes=1),
        source_tree_sha256=fitted.source_checkpoint.canonical_sha256(),
        training_dataset=DatasetIdentity(
            dataset_id="dataset:synthetic-devval-fitted:v1",
            source="okx.public_market_source",
            source_version="okx.history_candles.nine_strings.v1",
            instrument_ids=("BTC-USDT-SWAP", "ETH-USDT-SWAP"),
            manifest_sha256=captured.campaign_sha256,
            content_sha256=batch.sha256,
            expected_rows=sum(
                part.aggregation.plan.expected_minute_rows for part in captured.series
            ),
            first_event_at=development.start_at,
            last_event_at=validation.end_at,
            frozen_at=frozen_at,
            event_time_field="bar_interval_boundaries",
            available_time_field="available_at",
        ),
        bar_construction=BarConstruction(interval_seconds=60),
        outcome_label=OutcomeLabelSpec(
            label_id="forward:direction:1h",
            kind=OutcomeKind.FORWARD_RETURN_DIRECTION,
            horizon_seconds=feature_plan.outcome_horizon_seconds,
            dependency_seconds=split.label_dependency_seconds,
            positive_threshold=feature_plan.positive_threshold,
        ),
        candidate=fitted.candidate.model_copy(
            update={"configuration_sha256": model.trial.canonical_sha256()}
        ),
        features=(
            FeatureSpec(
                feature_id=model.trial.feature,
                feature_version=feature_plan.feature_version,
                dependency_seconds=split.max_feature_dependency_seconds,
                parameters=(
                    FrozenParameter(
                        name="feature_plan_sha256", value=prepared.feature_plan_sha256
                    ),
                    FrozenParameter(
                        name="history_bars", value=feature_plan.history_bars
                    ),
                ),
            ),
        ),
        selection_split=split,
        walk_forward_plan_sha256=prepared.study_plan_sha256,
        baselines=legacy_baselines,
        cost_model=fitted.plan.cost_model,
        evaluation=basis.evaluation.model_copy(
            update={
                "trials": fitted.plan.trial_registry,
                "reliability_bin_count": fitted.plan.reliability_bin_count,
            }
        ),
        prospective_holdout=ProspectiveHoldoutSpec(
            holdout_id="okx:synthetic-future-holdout:v1",
            source="okx.public_market_source",
            source_version="okx.history_candles.nine_strings.v1",
            instrument_ids=("BTC-USDT-SWAP", "ETH-USDT-SWAP"),
            coordinate_plan_sha256=sha(b"synthetic-future-coordinate-plan"),
            bar_interval_seconds=60,
            artifact_interval_seconds=60,
            start_at=holdout_start,
            end_at=holdout_end,
            publication_lag_seconds=3_600,
            first_permitted_access_at=holdout_end + timedelta(hours=1),
            expected_artifact_count=2_880,
            expected_rows=2_880,
        ),
    )
    assert split.validation.start_at - split.development.end_at >= timedelta(
        seconds=split.purge_seconds
    )
    return seal


def _bind(batch, study, trial_result, preregistration, *, expected_sha256=None):
    if expected_sha256 is None:
        expected_sha256 = freeze_prospective_preregistration(preregistration).sha256
    return build_devval_candidate_binding(
        study,
        batch=batch,
        trial_result=trial_result,
        preregistration=preregistration,
        expected_preregistration_sha256=expected_sha256,
        source_archive_sha256=sha(b"synthetic-source-archive"),
    )


def test_fitted_trial_and_model_have_distinct_pins_and_zero_authority(
    aligned_synthetic_case,
):
    batch, study, trial_result = aligned_synthetic_case
    seal = _formal_preregistration(batch, study, trial_result)
    frozen = _bind(batch, study, trial_result, seal)
    result = frozen.contract
    selected = trial_result.contract.selected_model
    assert selected is not None
    assert (
        result.selected_trial_configuration_sha256 == selected.trial.canonical_sha256()
    )
    assert result.fitted_model_sha256 == selected.canonical_sha256()
    assert result.selected_trial_configuration_sha256 != result.fitted_model_sha256
    assert (
        result.preregistration_sha256 == freeze_prospective_preregistration(seal).sha256
    )
    assert result.batch_sha256 == batch.sha256
    assert result.study_sha256 == study.sha256
    assert result.trial_result_sha256 == trial_result.sha256
    assert not result.independent_publication_verified
    assert not result.evaluator_first_access_verified
    assert not result.predictive_oos_eligible
    assert not result.execution_authority


def test_causal_synthetic_fit_survives_independent_batch_and_trial_replay(
    aligned_synthetic_case,
):
    batch, study, trial_result = aligned_synthetic_case
    seal = _formal_preregistration(batch, study, trial_result)
    frozen = _bind(batch, study, trial_result, seal)
    _, legacy, _ = _inputs(study)
    verified = verify_devval_candidate_binding(
        frozen.payload,
        expected_binding_sha256=frozen.sha256,
        batch=batch,
        study=study,
        trial_result=trial_result,
        legacy_scores=legacy,
        preregistration=seal,
        expected_preregistration_sha256=freeze_prospective_preregistration(seal).sha256,
        source_archive_sha256=sha(b"synthetic-source-archive"),
    )
    assert verified == frozen.contract
    assert not verified.predictive_oos_eligible


def test_wrong_formal_seal_pin_is_refused(aligned_synthetic_case):
    batch, study, trial_result = aligned_synthetic_case
    seal = _formal_preregistration(batch, study, trial_result)
    with pytest.raises(
        DevvalFittedBindingError, match="^PREREGISTRATION_PIN_MISMATCH$"
    ):
        _bind(batch, study, trial_result, seal, expected_sha256=sha(b"wrong seal"))


def test_adjusted_training_dataset_cannot_inherit_fit(aligned_synthetic_case):
    batch, study, trial_result = aligned_synthetic_case
    seal = _formal_preregistration(batch, study, trial_result)
    changed_dataset = seal.training_dataset.model_copy(
        update={"content_sha256": sha(b"different measured rows")}
    )
    adjusted = Gate3ProspectivePreregistration.model_validate(
        seal.model_copy(update={"training_dataset": changed_dataset}).model_dump()
    )
    with pytest.raises(DevvalFittedBindingError, match="^TRAINING_DATASET_MISMATCH$"):
        _bind(batch, study, trial_result, adjusted)


def test_changed_source_checkpoint_cannot_inherit_fit(aligned_synthetic_case):
    batch, study, trial_result = aligned_synthetic_case
    seal = _formal_preregistration(batch, study, trial_result)
    changed = Gate3ProspectivePreregistration.model_validate(
        seal.model_copy(
            update={"source_tree_sha256": sha(b"another source")}
        ).model_dump()
    )
    with pytest.raises(DevvalFittedBindingError, match="^SOURCE_CHECKPOINT_MISMATCH$"):
        _bind(batch, study, trial_result, changed)


def test_validation_rejection_cannot_be_bound_as_candidate(aligned_synthetic_case):
    batch, study, accepted_result = aligned_synthetic_case
    seal = _formal_preregistration(batch, study, accepted_result)
    _, legacy, source = _inputs(study)
    plan = accepted_result.contract.plan.model_copy(
        update={"min_validation_brier_improvement": Decimal(1)}
    )
    rejected_result = _fit(study, plan, legacy, source)
    assert (
        rejected_result.contract.selected_model
        == accepted_result.contract.selected_model
    )
    assert rejected_result.contract.status == "validation_rejected"
    with pytest.raises(DevvalFittedBindingError, match="^CANDIDATE_NOT_READY$"):
        _bind(batch, study, rejected_result, seal)


def test_self_consistent_altered_labels_fail_independent_replay(aligned_synthetic_case):
    batch, causal_study, _ = aligned_synthetic_case
    study = _with_outcomes(causal_study, validation_positive_count=79)
    plan, legacy, source = _inputs(study)
    trial_result = _fit(study, plan, legacy, source)
    assert trial_result.contract.status == "candidate_ready_for_separate_freeze"
    seal = _formal_preregistration(batch, study, trial_result)
    frozen = _bind(batch, study, trial_result, seal)
    assert legacy.canonical_sha256() == trial_result.contract.plan.legacy_scores_sha256
    with pytest.raises(DevvalFittedBindingError, match="^STUDY_REPLAY_MISMATCH$"):
        verify_devval_candidate_binding(
            frozen.payload,
            expected_binding_sha256=frozen.sha256,
            batch=batch,
            study=study,
            trial_result=trial_result,
            legacy_scores=legacy,
            preregistration=seal,
            expected_preregistration_sha256=freeze_prospective_preregistration(
                seal
            ).sha256,
            source_archive_sha256=sha(b"synthetic-source-archive"),
        )


def test_holdout_must_follow_dataset_freeze_by_full_embargo(aligned_synthetic_case):
    batch, study, trial_result = aligned_synthetic_case
    validation_end = batch.contract.series[2].aggregation.plan.window.end_at
    seal = _formal_preregistration(
        batch,
        study,
        trial_result,
        frozen_at=validation_end + timedelta(hours=2),
        holdout_start=validation_end + timedelta(days=1),
    )
    assert seal.prospective_holdout.start_at - validation_end >= timedelta(
        seconds=seal.selection_split.embargo_seconds
    )
    assert (
        seal.prospective_holdout.start_at - seal.training_dataset.frozen_at
        < timedelta(seconds=seal.selection_split.embargo_seconds)
    )
    with pytest.raises(DevvalFittedBindingError, match="^HOLDOUT_EMBARGO$"):
        _bind(batch, study, trial_result, seal)
