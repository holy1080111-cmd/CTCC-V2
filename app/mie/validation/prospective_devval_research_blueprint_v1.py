"""Bind pre-capture research choices to the native source campaign."""

from __future__ import annotations

from datetime import timedelta
from typing import Literal

from pydantic import Field, model_validator

from app.mie.validation.contracts import Gate3Contract, Sha256
from app.mie.validation.prospective_devval_campaign_batch_v1 import (
    DevvalCampaignBatchV1,
)
from app.mie.validation.prospective_devval_campaign_plan_v1 import (
    RESEARCH_CAMPAIGN_PREFIX,
    DevvalCampaignCaptureCoordinatesV1,
    ProspectiveDevvalCampaignManifestV1,
    ProspectiveDevvalCampaignPlanV1,
    freeze_prospective_devval_campaign_v1,
)
from app.mie.validation.prospective_devval_campaign_study_v1 import (
    DevvalCampaignStudyPlanV1,
    DevvalCampaignStudyPolicyV1,
    DevvalCampaignStudyV1,
)
from app.mie.validation.prospective_devval_candidate_trials_v1 import (
    DevvalCandidateTrialPlanV1,
    DevvalCandidateTrialPolicyV1,
    DevvalLegacyScoresV1,
)
from app.mie.validation.splits import replay_plan_bound_walk_forward_folds


class DevvalResearchBlueprintV1(Gate3Contract):
    schema_version: Literal["ctcc.mie.devval_research_blueprint.v1"] = (
        "ctcc.mie.devval_research_blueprint.v1"
    )
    capture: DevvalCampaignCaptureCoordinatesV1
    study: DevvalCampaignStudyPolicyV1
    selection: DevvalCandidateTrialPolicyV1
    legacy_score_source_sha256: Sha256
    minimum_development_folds: int = Field(ge=1)
    independent_preregistration_verified: Literal[False] = False
    predictive_oos_eligible: Literal[False] = False
    execution_authority: Literal[False] = False

    @model_validator(mode="after")
    def validate_feasibility(self) -> DevvalResearchBlueprintV1:
        _check_schedule(self)
        return self


def _check_schedule(blueprint: DevvalResearchBlueprintV1) -> None:
    capture = blueprint.capture
    study = blueprint.study
    feature = study.feature_plan
    width = feature.bar_horizon.seconds
    history = feature.history_bars * width
    outcome = feature.outcome_horizon_seconds
    lag = capture.maximum_observation_lag_seconds
    if study.feature_dependency_seconds < history + lag or (
        study.label_dependency_seconds < outcome + lag
    ):
        raise ValueError("research blueprint understates receipt-time dependencies")

    # A training decision can precede its label while the next validation
    # decision follows the opening of its feature history. Cover both sides.
    separation = study.feature_dependency_seconds + study.label_dependency_seconds
    if min(study.purge_seconds, study.embargo_seconds) < separation:
        raise ValueError("research blueprint purge or embargo understates separation")
    partition_gap = capture.validation.start_at - capture.development.end_at
    rounded_purge = (study.purge_seconds + 59) // 60 * 60
    if partition_gap.total_seconds() < rounded_purge:
        raise ValueError("research blueprint development/validation gap insufficient")

    # The last eligible decision leaves its entire outcome in the partition.
    bar = timedelta(seconds=width)
    development = capture.development
    validation = capture.validation
    counts = tuple(
        (window.end_at - window.start_at) // bar
        - outcome // width
        - feature.history_bars
        + 1
        for window in (development, validation)
    )
    if counts[1] < study.minimum_final_validation_events:
        raise ValueError("research blueprint final validation events insufficient")
    if counts[0] < study.minimum_training_events + study.fold_validation_events:
        raise ValueError("research blueprint development events insufficient")

    # Require at least one complete fold even when the last required training
    # decision arrives at the lag limit and the validation decision is on time.
    last_validation_start = counts[0] - study.fold_validation_events
    last_required_train = study.minimum_training_events - 1
    if (
        last_validation_start - last_required_train
    ) * width <= study.purge_seconds + lag:
        raise ValueError("research blueprint cannot guarantee a first development fold")
    if study.maximum_folds is not None and (
        study.maximum_folds < blueprint.minimum_development_folds
    ):
        raise ValueError("research blueprint fold limits conflict")
    if blueprint.minimum_development_folds == 1:
        return
    # The calendar also needs to fit the declared fold count. Actual receipts
    # are split again and must meet that count; jitter can affect membership.
    first = development.start_at + feature.history_bars * bar
    scheduled = tuple(first + index * bar for index in range(counts[0]))
    folds = replay_plan_bound_walk_forward_folds(
        scheduled,
        feature_plan=feature,
        expected_feature_plan_sha256=feature.canonical_sha256,
        minimum_training_observations=study.minimum_training_events,
        validation_observations=study.fold_validation_events,
        feature_dependency_seconds=study.feature_dependency_seconds,
        label_dependency_seconds=study.label_dependency_seconds,
        purge_seconds=study.purge_seconds,
        embargo_seconds=study.embargo_seconds,
        step_observations=study.step_events,
        maximum_folds=blueprint.minimum_development_folds,
    )
    if len(folds.folds) < blueprint.minimum_development_folds:
        raise ValueError("research blueprint scheduled development folds insufficient")


def compile_research_campaign(
    blueprint: DevvalResearchBlueprintV1,
) -> ProspectiveDevvalCampaignPlanV1:
    if type(blueprint) is not DevvalResearchBlueprintV1:
        raise ValueError("research blueprint required")
    checked = DevvalResearchBlueprintV1.model_validate(blueprint)
    return ProspectiveDevvalCampaignPlanV1(
        **checked.capture.model_dump(mode="python"),
        campaign_id=f"{RESEARCH_CAMPAIGN_PREFIX}{checked.canonical_sha256()}",
    )


def verify_research_campaign(
    plan: ProspectiveDevvalCampaignPlanV1, blueprint: DevvalResearchBlueprintV1 | None
) -> None:
    if type(plan) is not ProspectiveDevvalCampaignPlanV1:
        raise ValueError("research campaign plan required")
    if plan.research_blueprint_sha256 is None:
        if blueprint is not None:
            raise ValueError("research blueprint absent from campaign")
        return
    if blueprint is None:
        raise ValueError("research blueprint required by campaign")
    expected = compile_research_campaign(blueprint)
    if expected.canonical_json_bytes() != plan.canonical_json_bytes():
        raise ValueError("research blueprint campaign differs")


def verify_research_study_plan(
    blueprint: DevvalResearchBlueprintV1,
    manifest: ProspectiveDevvalCampaignManifestV1,
    study_plan: DevvalCampaignStudyPlanV1,
) -> None:
    verify_research_campaign(manifest.plan, blueprint)
    _verify_study_policy(blueprint, manifest.canonical_sha256(), study_plan)


def _verify_study_policy(
    blueprint: DevvalResearchBlueprintV1,
    campaign_sha256: str,
    study_plan: DevvalCampaignStudyPlanV1,
) -> None:
    expected = DevvalCampaignStudyPlanV1(
        **blueprint.study.model_dump(mode="python"), campaign_sha256=campaign_sha256
    )
    if type(study_plan) is not DevvalCampaignStudyPlanV1 or (
        study_plan.canonical_json_bytes() != expected.canonical_json_bytes()
    ):
        raise ValueError("research blueprint study policy differs")


def verify_research_study_result(
    blueprint: DevvalResearchBlueprintV1, study: DevvalCampaignStudyV1
) -> None:
    if len(study.development_folds) < blueprint.minimum_development_folds:
        raise ValueError("research blueprint measured development folds insufficient")


def verify_research_batch_and_study(
    blueprint: DevvalResearchBlueprintV1 | None,
    batch: DevvalCampaignBatchV1,
    study: DevvalCampaignStudyV1,
) -> None:
    if batch.research_blueprint_sha256 is None:
        if blueprint is not None:
            raise ValueError("research blueprint absent from batch")
        return
    if blueprint is None:
        raise ValueError("research blueprint required by batch")
    plan = compile_research_campaign(blueprint)
    actual_series = tuple(
        (series.aggregation.plan.window, series.aggregation.plan.instrument_id)
        for series in batch.series
    )
    expected_series = tuple(
        (window, instrument)
        for window in (plan.development, plan.validation)
        for instrument in plan.instrument_ids
    )
    if (
        plan.research_blueprint_sha256 != batch.research_blueprint_sha256
        or plan.canonical_sha256() != batch.plan_sha256
        or plan.declared_source_commit != batch.source_commit
        or plan.declared_source_tree != batch.source_tree
        or plan.manifest_sha256 != batch.source_manifest_sha256
        or actual_series != expected_series
    ):
        raise ValueError("research blueprint batch plan differs")
    if batch.campaign_sha256 != freeze_prospective_devval_campaign_v1(plan).sha256:
        raise ValueError("research blueprint batch campaign differs")
    _verify_study_policy(blueprint, batch.campaign_sha256, study.plan)
    verify_research_study_result(blueprint, study)


def verify_research_trial_plan(
    blueprint: DevvalResearchBlueprintV1,
    trial_plan: DevvalCandidateTrialPlanV1,
    legacy_scores: DevvalLegacyScoresV1,
) -> None:
    if type(legacy_scores) is not DevvalLegacyScoresV1:
        raise ValueError("research blueprint legacy score artifact required")
    verify_research_trial_policy(
        blueprint, legacy_scores.legacy_source_sha256, trial_plan
    )
    if (
        trial_plan.study_sha256 != legacy_scores.study_sha256
        or trial_plan.legacy_scores_sha256 != legacy_scores.canonical_sha256()
    ):
        raise ValueError("research blueprint legacy score artifact differs")


def verify_research_trial_policy(
    blueprint: DevvalResearchBlueprintV1,
    legacy_source_sha256: str,
    trial_plan: DevvalCandidateTrialPlanV1,
) -> None:
    if type(trial_plan) is not DevvalCandidateTrialPlanV1:
        raise ValueError("research blueprint candidate trial plan required")
    expected = DevvalCandidateTrialPlanV1(
        **blueprint.selection.model_dump(mode="python"),
        study_sha256=trial_plan.study_sha256,
        legacy_scores_sha256=trial_plan.legacy_scores_sha256,
    )
    if (
        legacy_source_sha256 != blueprint.legacy_score_source_sha256
        or trial_plan.canonical_json_bytes() != expected.canonical_json_bytes()
    ):
        raise ValueError("research blueprint trial policy differs")
