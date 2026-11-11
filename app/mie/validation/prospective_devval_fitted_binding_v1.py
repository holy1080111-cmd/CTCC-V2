"""Bind a replayed Development-only fit to an exact prospective preregistration."""

from __future__ import annotations

from datetime import timedelta
from typing import Literal

from app.mie.validation.artifact import (
    FrozenGate3Artifact,
    _freeze,
    _verify,
    freeze_prospective_preregistration,
)
from app.mie.validation.contracts import BaselineKind, Gate3Contract, Sha256
from app.mie.validation.prospective import Gate3ProspectivePreregistration
from app.mie.validation.prospective_devval_campaign_batch_v1 import (
    _EPOCH,
    _MAX_BATCH_BYTES,
    DevvalCampaignBatchV1,
)
from app.mie.validation.prospective_devval_campaign_study_v1 import (
    _MAX_STUDY_BYTES,
    DevvalCampaignStudyV1,
    _prepare_study,
)
from app.mie.validation.prospective_devval_candidate_trials_v1 import (
    DevvalCandidateSourceV1,
    DevvalCandidateTrialsV1,
    DevvalLegacyScoresV1,
    _fit_candidate_trials,
)
from app.mie.validation.prospective_devval_research_blueprint_v1 import (
    DevvalResearchBlueprintV1,
    verify_research_batch_and_study,
    verify_research_trial_plan,
    verify_research_trial_policy,
)

_BAR_SECONDS = 60
_SOURCE = "okx.public_market_source"
_SOURCE_VERSION = "okx.history_candles.nine_strings.v1"
_MAX_TRIAL_BYTES = 16 * 1024 * 1024
_MAX_FITTED_BINDING_BYTES = 16_384


class DevvalFittedBindingError(ValueError):
    """A fixed refusal before any holdout read or independent publication."""


class DevvalFittedCandidateBindingV1(Gate3Contract):
    schema_version: Literal["ctcc.mie.devval_fitted_candidate_binding.v1"] = (
        "ctcc.mie.devval_fitted_candidate_binding.v1"
    )
    source_checkpoint: DevvalCandidateSourceV1
    source_archive_sha256: Sha256
    preregistration_sha256: Sha256
    holdout_coordinate_sha256: Sha256
    training_dataset_sha256: Sha256
    campaign_sha256: Sha256
    campaign_plan_sha256: Sha256
    campaign_completion_sha256: Sha256
    campaign_root_binding_sha256: Sha256
    row_inventory_sha256: Sha256
    batch_sha256: Sha256
    study_sha256: Sha256
    study_plan_sha256: Sha256
    feature_plan_sha256: Sha256
    legacy_score_bundle_sha256: Sha256
    legacy_score_source_sha256: Sha256
    trial_result_sha256: Sha256
    trial_plan_sha256: Sha256
    trial_registry_sha256: Sha256
    selected_trial_configuration_sha256: Sha256
    fitted_model_sha256: Sha256
    independent_publication_verified: Literal[False] = False
    legacy_score_origin_verified: Literal[False] = False
    evaluator_first_access_verified: Literal[False] = False
    predictive_oos_eligible: Literal[False] = False
    execution_authority: Literal[False] = False


def _read_artifact[T: Gate3Contract](
    artifact: FrozenGate3Artifact[T], contract: type[T], maximum: int
) -> T:
    if (
        type(artifact) is not FrozenGate3Artifact
        or type(artifact.contract) is not contract
        or type(artifact.payload) is not bytes
        or not 1 <= len(artifact.payload) <= maximum
    ):
        raise DevvalFittedBindingError("ARTIFACT_INVALID")
    checked = _verify(
        artifact.payload, expected_sha256=artifact.sha256, contract_type=contract
    )
    if checked != artifact.contract:
        raise DevvalFittedBindingError("ARTIFACT_CONTRACT_MISMATCH")
    return checked


def _whole_minutes(seconds: int) -> int:
    return (seconds + _BAR_SECONDS - 1) // _BAR_SECONDS * _BAR_SECONDS


def _require_training_dataset(seal, batch, batch_sha256):
    series = batch.series
    plans = tuple(member.aggregation.plan for member in series)
    development, validation = (
        seal.selection_split.development,
        seal.selection_split.validation,
    )
    dataset = seal.training_dataset
    expected_series = tuple(
        (window, instrument)
        for window in (development, validation)
        for instrument in dataset.instrument_ids
    )
    # Rounding up ensures a sub-microsecond measured receipt cannot fall after
    # the reported dataset freeze when represented by a datetime.
    cutoff = _EPOCH + timedelta(
        microseconds=(max(s.cutoff_utc_ns for s in series) + 999) // 1000
    )
    if (
        dataset.source != _SOURCE
        or dataset.source_version != _SOURCE_VERSION
        or tuple((plan.window, plan.instrument_id) for plan in plans) != expected_series
        or any(plan.source_manifest_sha256 != batch.campaign_sha256 for plan in plans)
        or dataset.manifest_sha256 != batch.campaign_sha256
        or dataset.content_sha256 != batch_sha256
        or dataset.expected_rows != sum(plan.expected_minute_rows for plan in plans)
        or dataset.first_event_at != development.start_at
        or dataset.last_event_at != validation.end_at
        or dataset.frozen_at < cutoff
        or dataset.event_time_field != "bar_interval_boundaries"
        or dataset.available_time_field != "available_at"
    ):
        raise DevvalFittedBindingError("TRAINING_DATASET_MISMATCH")
    window = seal.prospective_holdout
    if (
        window.source != _SOURCE
        or window.source_version != _SOURCE_VERSION
        or window.artifact_interval_seconds != _BAR_SECONDS
    ):
        raise DevvalFittedBindingError("HOLDOUT_SOURCE_MISMATCH")
    if window.start_at - dataset.frozen_at < timedelta(
        seconds=seal.selection_split.embargo_seconds
    ):
        raise DevvalFittedBindingError("HOLDOUT_EMBARGO")


def _require_protocol(seal, study, trials, model):
    feature = study.plan.feature_plan
    split = seal.selection_split
    outcome = seal.outcome_label
    if (
        seal.bar_construction.interval_seconds != _BAR_SECONDS
        or seal.walk_forward_plan_sha256 != study.study_plan_sha256
        or seal.cost_model != trials.plan.cost_model
        or seal.evaluation.trials != trials.plan.trial_registry
        or seal.evaluation.reliability_bin_count != trials.plan.reliability_bin_count
        or split.max_feature_dependency_seconds
        != _whole_minutes(study.plan.feature_dependency_seconds)
        or split.label_dependency_seconds
        != _whole_minutes(study.plan.label_dependency_seconds)
        or split.purge_seconds != _whole_minutes(study.plan.purge_seconds)
        or split.embargo_seconds != _whole_minutes(study.plan.embargo_seconds)
        or outcome.horizon_seconds != feature.outcome_horizon_seconds
        or outcome.positive_threshold != feature.positive_threshold
        or len(seal.features) != 1
    ):
        raise DevvalFittedBindingError("FORMAL_PROTOCOL_MISMATCH")
    selected_feature = seal.features[0]
    parameters = {item.name: item.value for item in selected_feature.parameters}
    if (
        selected_feature.feature_id != model.trial.feature
        or selected_feature.feature_version != feature.feature_version
        or selected_feature.dependency_seconds != split.max_feature_dependency_seconds
        or type(parameters.get("history_bars")) is not int
        or parameters
        != {
            "feature_plan_sha256": study.feature_plan_sha256,
            "history_bars": feature.history_bars,
        }
    ):
        raise DevvalFittedBindingError("FEATURE_PLAN_MISMATCH")
    legacy = next(
        baseline
        for baseline in seal.baselines
        if baseline.kind is BaselineKind.FROZEN_LEGACY_SCORE
    )
    if legacy.configuration_sha256 != trials.legacy_source_sha256:
        raise DevvalFittedBindingError("LEGACY_BASELINE_MISMATCH")


def build_devval_candidate_binding(
    study: FrozenGate3Artifact[DevvalCampaignStudyV1],
    *,
    batch: FrozenGate3Artifact[DevvalCampaignBatchV1],
    trial_result: FrozenGate3Artifact[DevvalCandidateTrialsV1],
    preregistration: Gate3ProspectivePreregistration,
    expected_preregistration_sha256: str,
    source_archive_sha256: str,
    research_blueprint: DevvalResearchBlueprintV1 | None = None,
) -> FrozenGate3Artifact[DevvalFittedCandidateBindingV1]:
    """Combine same-invocation replay artifacts for independent custody review."""
    if type(preregistration) is not Gate3ProspectivePreregistration:
        raise DevvalFittedBindingError("PREREGISTRATION_INVALID")
    formal = freeze_prospective_preregistration(preregistration)
    if formal.sha256 != expected_preregistration_sha256:
        raise DevvalFittedBindingError("PREREGISTRATION_PIN_MISMATCH")
    seal = formal.contract
    captured = _read_artifact(batch, DevvalCampaignBatchV1, _MAX_BATCH_BYTES)
    prepared = _read_artifact(study, DevvalCampaignStudyV1, _MAX_STUDY_BYTES)
    trials = _read_artifact(trial_result, DevvalCandidateTrialsV1, _MAX_TRIAL_BYTES)
    verify_research_batch_and_study(research_blueprint, captured, prepared)
    if research_blueprint is not None:
        verify_research_trial_policy(
            research_blueprint, trials.legacy_source_sha256, trials.plan
        )
    if (
        prepared.plan.campaign_sha256 != captured.campaign_sha256
        or prepared.batch_sha256 != batch.sha256
        or prepared.completion_sha256 != captured.completion_sha256
        or prepared.row_inventory_sha256 != captured.row_inventory_sha256
        or prepared.plan.canonical_sha256() != prepared.study_plan_sha256
        or prepared.plan.feature_plan.canonical_sha256 != prepared.feature_plan_sha256
        or trials.plan.canonical_sha256() != trials.plan_sha256
        or trials.plan.study_sha256 != study.sha256
        or trials.study_plan_sha256 != prepared.study_plan_sha256
        or trials.row_inventory_sha256 != prepared.row_inventory_sha256
    ):
        raise DevvalFittedBindingError("DEVVAL_LINEAGE_MISMATCH")
    source = trials.source_checkpoint
    if (source.commit, source.tree, source.manifest_sha256) != (
        captured.source_commit,
        captured.source_tree,
        captured.source_manifest_sha256,
    ) or source.canonical_sha256() != seal.source_tree_sha256:
        raise DevvalFittedBindingError("SOURCE_CHECKPOINT_MISMATCH")

    model = trials.selected_model
    candidate = trials.candidate
    if (
        trials.status != "candidate_ready_for_separate_freeze"
        or model is None
        or candidate is None
        or trials.validation_rejections
        or trials.validation_metrics is None
    ):
        raise DevvalFittedBindingError("CANDIDATE_NOT_READY")
    model_sha = model.canonical_sha256()
    trial_sha = model.trial.canonical_sha256()
    if (
        candidate.configuration_sha256 != model_sha
        or candidate.source_sha256 != source.canonical_sha256()
        or candidate.candidate_id != trials.plan.candidate_id
        or candidate.selected_trial_id != model.trial.trial_id
        or candidate.model_version != model.model_version
        or model.cost_model != trials.plan.cost_model
        or seal.candidate
        != candidate.model_copy(update={"configuration_sha256": trial_sha})
    ):
        raise DevvalFittedBindingError("FITTED_CANDIDATE_MISMATCH")
    _require_training_dataset(seal, captured, batch.sha256)
    _require_protocol(seal, prepared, trials, model)
    return _freeze(
        DevvalFittedCandidateBindingV1(
            source_checkpoint=source,
            source_archive_sha256=source_archive_sha256,
            preregistration_sha256=formal.sha256,
            holdout_coordinate_sha256=seal.prospective_holdout.coordinate_plan_sha256,
            training_dataset_sha256=seal.training_dataset.canonical_sha256(),
            campaign_sha256=captured.campaign_sha256,
            campaign_plan_sha256=captured.plan_sha256,
            campaign_completion_sha256=captured.completion_sha256,
            campaign_root_binding_sha256=captured.root_binding_sha256,
            row_inventory_sha256=captured.row_inventory_sha256,
            batch_sha256=batch.sha256,
            study_sha256=study.sha256,
            study_plan_sha256=prepared.study_plan_sha256,
            feature_plan_sha256=prepared.feature_plan_sha256,
            legacy_score_bundle_sha256=trials.plan.legacy_scores_sha256,
            legacy_score_source_sha256=trials.legacy_source_sha256,
            trial_result_sha256=trial_result.sha256,
            trial_plan_sha256=trials.plan_sha256,
            trial_registry_sha256=trials.plan.trial_registry.canonical_sha256(),
            selected_trial_configuration_sha256=trial_sha,
            fitted_model_sha256=model_sha,
        ),
        contract_type=DevvalFittedCandidateBindingV1,
    )


def verify_devval_candidate_binding(
    payload: bytes,
    *,
    expected_binding_sha256: str,
    batch: FrozenGate3Artifact[DevvalCampaignBatchV1],
    study: FrozenGate3Artifact[DevvalCampaignStudyV1],
    trial_result: FrozenGate3Artifact[DevvalCandidateTrialsV1],
    legacy_scores: DevvalLegacyScoresV1,
    preregistration: Gate3ProspectivePreregistration,
    expected_preregistration_sha256: str,
    source_archive_sha256: str,
    research_blueprint: DevvalResearchBlueprintV1 | None = None,
) -> DevvalFittedCandidateBindingV1:
    """Recompute retained study and fitted bytes before comparing a binding."""
    if type(payload) is not bytes or not 1 <= len(payload) <= _MAX_FITTED_BINDING_BYTES:
        raise DevvalFittedBindingError("BINDING_BYTES_INVALID")
    expected = _verify(
        payload,
        expected_sha256=expected_binding_sha256,
        contract_type=DevvalFittedCandidateBindingV1,
    )
    prepared = _read_artifact(study, DevvalCampaignStudyV1, _MAX_STUDY_BYTES)
    recorded_trials = _read_artifact(
        trial_result, DevvalCandidateTrialsV1, _MAX_TRIAL_BYTES
    )
    if research_blueprint is not None:
        verify_research_trial_plan(
            research_blueprint, recorded_trials.plan, legacy_scores
        )
    replayed_study = _prepare_study(
        batch, plan=prepared.plan, expected_plan_sha256=expected.study_plan_sha256
    )
    if replayed_study.payload != study.payload:
        raise DevvalFittedBindingError("STUDY_REPLAY_MISMATCH")
    replayed_trials = _fit_candidate_trials(
        replayed_study,
        plan=recorded_trials.plan,
        expected_plan_sha256=expected.trial_plan_sha256,
        legacy_scores=legacy_scores,
        source_checkpoint=recorded_trials.source_checkpoint,
    )
    if replayed_trials.payload != trial_result.payload:
        raise DevvalFittedBindingError("TRIAL_REPLAY_MISMATCH")
    replayed_binding = build_devval_candidate_binding(
        replayed_study,
        batch=batch,
        trial_result=replayed_trials,
        preregistration=preregistration,
        expected_preregistration_sha256=expected_preregistration_sha256,
        source_archive_sha256=source_archive_sha256,
        research_blueprint=research_blueprint,
    )
    if replayed_binding.payload != payload:
        raise DevvalFittedBindingError("BINDING_REPLAY_MISMATCH")
    return expected
