"""Synthetic, pre-capture controls for the research campaign blueprint.

No row here is a measured exchange receipt or an independently published plan.
The synthetic manifest inventory only tests exact canonical joins.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.domain.source_primitives import sha
from app.mie.validation.contracts import (
    CostModel,
    DatasetPartition,
    FrozenTrial,
    Gate3Metric,
    PartitionWindow,
    TrialRegistry,
)
from app.mie.validation.prospective_devval_campaign_plan_v1 import (
    DevvalCampaignCaptureCoordinatesV1,
    ProspectiveDevvalCampaignManifestV1,
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
    DevvalCandidateTrialV1,
    DevvalLegacyScoreRowV1,
    DevvalLegacyScoresV1,
)
from app.mie.validation.prospective_devval_research_blueprint_v1 import (
    RESEARCH_CAMPAIGN_PREFIX,
    DevvalResearchBlueprintV1,
    compile_research_campaign,
    verify_research_batch_and_study,
    verify_research_campaign,
    verify_research_study_plan,
    verify_research_study_result,
    verify_research_trial_plan,
)
from tests.unit.mie.test_devval_campaign_study import _feature_plan

START = datetime(2030, 1, 1, tzinfo=UTC)


def _blueprint(
    *,
    horizon: str = "1H",
    development_hours: int = 84,
    validation_hours: int = 84,
    gap_hours: int = 24,
    minimum_folds: int = 2,
) -> DevvalResearchBlueprintV1:
    feature = _feature_plan(horizon)
    width = feature.bar_horizon.seconds
    development_end = START + timedelta(hours=development_hours)
    validation_start = development_end + timedelta(hours=gap_hours)
    capture = DevvalCampaignCaptureCoordinatesV1(
        planned_at=START - timedelta(hours=1),
        declared_source_commit="a" * 40,
        declared_source_tree="b" * 40,
        manifest_sha256="c" * 64,
        origin="https://openapi.okx.com",
        development=PartitionWindow(
            partition=DatasetPartition.DEVELOPMENT,
            start_at=START,
            end_at=development_end,
        ),
        validation=PartitionWindow(
            partition=DatasetPartition.VALIDATION,
            start_at=validation_start,
            end_at=validation_start + timedelta(hours=validation_hours),
        ),
        expected_rows=(development_hours + validation_hours) * 120,
    )
    feature_dependency = feature.history_bars * width + 60
    label_dependency = feature.outcome_horizon_seconds + 60
    study = DevvalCampaignStudyPolicyV1(
        feature_plan=feature,
        minimum_training_events=5,
        fold_validation_events=4,
        minimum_final_validation_events=5,
        feature_dependency_seconds=feature_dependency,
        label_dependency_seconds=label_dependency,
        purge_seconds=feature_dependency + label_dependency + 60,
        embargo_seconds=feature_dependency + label_dependency + 60,
        step_events=4,
        maximum_folds=2,
    )
    trials = (
        DevvalCandidateTrialV1(
            trial_id="a.synthetic",
            feature="normalized_momentum",
            bin_edges=(Decimal(0),),
            prior_weight=Decimal(2),
            minimum_bin_observations=1,
        ),
        DevvalCandidateTrialV1(
            trial_id="z.synthetic",
            feature="velocity_to_volatility",
            bin_edges=(),
            prior_weight=Decimal(3),
            minimum_bin_observations=2,
        ),
    )
    selection = DevvalCandidateTrialPolicyV1(
        candidate_id="synthetic.candidate",
        trial_registry=TrialRegistry(
            registry_id="synthetic.registry",
            trials=tuple(
                FrozenTrial(
                    trial_id=trial.trial_id,
                    configuration_sha256=trial.canonical_sha256(),
                )
                for trial in trials
            ),
            declared_trial_count=len(trials),
            selection_metric=Gate3Metric.BRIER_SCORE,
        ),
        trials=trials,
        cost_model=CostModel(
            model_id="synthetic.cost",
            version="v1",
            fee_bps=Decimal(2),
            funding_bps=Decimal(1),
            spread_bps=Decimal(1),
            slippage_bps=Decimal(1),
            funding_interval_seconds=28_800,
        ),
        min_development_brier_improvement=Decimal("0.00001"),
        min_validation_brier_improvement=Decimal("0.00001"),
        max_validation_ece=Decimal("0.2"),
        reliability_bin_count=5,
    )
    return DevvalResearchBlueprintV1(
        capture=capture,
        study=study,
        selection=selection,
        legacy_score_source_sha256=sha(b"synthetic-legacy-source"),
        minimum_development_folds=minimum_folds,
    )


def _manifest(blueprint):
    plan = compile_research_campaign(blueprint)
    return ProspectiveDevvalCampaignManifestV1(
        plan=plan,
        plan_sha256=plan.canonical_sha256(),
        source_plan_sha256s=tuple(
            sha(f"synthetic-block:{index}".encode())
            for index in range(plan.block_count)
        ),
    )


def _legacy(blueprint):
    row = DevvalLegacyScoreRowV1(
        feature_replay_sha256=sha(b"synthetic-feature-row"),
        probability=Decimal("0.5"),
    )
    return DevvalLegacyScoresV1(
        study_sha256=sha(b"synthetic-study"),
        legacy_source_sha256=blueprint.legacy_score_source_sha256,
        development=(row,),
        validation=(row,),
    )


def _trial_plan(blueprint, legacy):
    return DevvalCandidateTrialPlanV1(
        **blueprint.selection.model_dump(mode="python"),
        study_sha256=legacy.study_sha256,
        legacy_scores_sha256=legacy.canonical_sha256(),
    )


@pytest.mark.parametrize(
    ("horizon", "development_hours", "validation_hours", "gap_hours"),
    (("1H", 84, 84, 24), ("4H", 400, 120, 92)),
)
def test_valid_blueprint_compiles_exact_campaign_without_source_capture(
    horizon, development_hours, validation_hours, gap_hours
):
    blueprint = _blueprint(
        horizon=horizon,
        development_hours=development_hours,
        validation_hours=validation_hours,
        gap_hours=gap_hours,
    )
    plan = compile_research_campaign(blueprint)
    assert plan.campaign_id == RESEARCH_CAMPAIGN_PREFIX + blueprint.canonical_sha256()
    assert plan.expected_rows == (development_hours + validation_hours) * 120
    assert plan.pre_window_commit_proven is False
    assert blueprint.predictive_oos_eligible is False
    assert blueprint.execution_authority is False
    verify_research_campaign(plan, blueprint)


def test_exact_campaign_study_and_trial_policy_join_without_real_rows():
    blueprint = _blueprint()
    manifest = _manifest(blueprint)
    study_plan = DevvalCampaignStudyPlanV1(
        **blueprint.study.model_dump(mode="python"),
        campaign_sha256=manifest.canonical_sha256(),
    )
    legacy = _legacy(blueprint)
    trial_plan = _trial_plan(blueprint, legacy)
    verify_research_study_plan(blueprint, manifest, study_plan)
    verify_research_trial_plan(blueprint, trial_plan, legacy)
    assert study_plan.campaign_sha256 == manifest.canonical_sha256()
    assert trial_plan.legacy_scores_sha256 == legacy.canonical_sha256()
    assert not manifest.predictive_oos_eligible


def test_changed_cost_or_campaign_coordinate_cannot_reuse_blueprint_pin():
    blueprint = _blueprint()
    original = compile_research_campaign(blueprint)
    changed_cost = blueprint.selection.cost_model.model_copy(
        update={"fee_bps": Decimal(3)}
    )
    selection = blueprint.selection.model_copy(update={"cost_model": changed_cost})
    changed = DevvalResearchBlueprintV1.model_validate(
        blueprint.model_copy(update={"selection": selection}).model_dump()
    )
    assert changed.canonical_sha256() != blueprint.canonical_sha256()
    assert compile_research_campaign(changed).campaign_id != original.campaign_id
    with pytest.raises(ValueError, match="^research blueprint campaign differs$"):
        verify_research_campaign(original, changed)

    capture = blueprint.capture.model_copy(
        update={"planned_at": blueprint.capture.planned_at - timedelta(hours=1)}
    )
    moved = DevvalResearchBlueprintV1.model_validate(
        blueprint.model_copy(update={"capture": capture}).model_dump()
    )
    with pytest.raises(ValueError, match="^research blueprint campaign differs$"):
        verify_research_campaign(original, moved)


def test_changed_study_policy_and_legacy_score_bytes_are_refused():
    blueprint = _blueprint()
    manifest = _manifest(blueprint)
    study_plan = DevvalCampaignStudyPlanV1(
        **blueprint.study.model_dump(mode="python"),
        campaign_sha256=manifest.canonical_sha256(),
    )
    changed_study = study_plan.model_copy(
        update={"minimum_training_events": study_plan.minimum_training_events + 1}
    )
    with pytest.raises(ValueError, match="^research blueprint study policy differs$"):
        verify_research_study_plan(blueprint, manifest, changed_study)

    legacy = _legacy(blueprint)
    trial_plan = _trial_plan(blueprint, legacy)
    row = legacy.development[0].model_copy(update={"probability": Decimal("0.6")})
    changed_legacy = legacy.model_copy(update={"development": (row,)})
    with pytest.raises(
        ValueError, match="^research blueprint legacy score artifact differs$"
    ):
        verify_research_trial_plan(blueprint, trial_plan, changed_legacy)
    changed_plan = trial_plan.model_copy(
        update={
            "cost_model": trial_plan.cost_model.model_copy(
                update={"fee_bps": Decimal(3)}
            )
        }
    )
    with pytest.raises(ValueError, match="^research blueprint trial policy differs$"):
        verify_research_trial_plan(blueprint, changed_plan, legacy)


def test_four_hour_minimum_capture_window_has_no_research_labels():
    with pytest.raises(
        ValueError, match="research blueprint final validation events insufficient"
    ):
        _blueprint(horizon="4H", gap_hours=92)


def test_validation_event_count_and_development_fold_guarantee_are_distinct():
    blueprint = _blueprint()
    short_validation = blueprint.study.model_copy(
        update={"minimum_final_validation_events": 64}
    )
    with pytest.raises(
        ValueError, match="research blueprint final validation events insufficient"
    ):
        DevvalResearchBlueprintV1.model_validate(
            blueprint.model_copy(update={"study": short_validation}).model_dump()
        )
    impossible_fold = blueprint.study.model_copy(
        update={"minimum_training_events": 40, "fold_validation_events": 20}
    )
    with pytest.raises(
        ValueError,
        match="research blueprint cannot guarantee a first development fold",
    ):
        DevvalResearchBlueprintV1.model_validate(
            blueprint.model_copy(update={"study": impossible_fold}).model_dump()
        )


def test_four_hour_partition_gap_is_not_enough_for_research_purge():
    with pytest.raises(
        ValueError,
        match="research blueprint development/validation gap insufficient",
    ):
        _blueprint(gap_hours=4)


def test_actual_study_with_too_few_folds_never_inherits_schedule_guarantee():
    blueprint = _blueprint()
    plan = DevvalCampaignStudyPlanV1(
        **blueprint.study.model_dump(mode="python"),
        campaign_sha256=sha(b"synthetic-campaign"),
    )
    study = DevvalCampaignStudyV1(
        plan=plan,
        study_plan_sha256=plan.canonical_sha256(),
        batch_sha256=sha(b"synthetic-batch"),
        completion_sha256=sha(b"synthetic-completion"),
        row_inventory_sha256=sha(b"synthetic-row-inventory"),
        feature_plan_sha256=plan.feature_plan.canonical_sha256,
        development_event_times_sha256=sha(b"synthetic-event-times"),
        development=(),
        validation=(),
        development_folds=(),
    )
    with pytest.raises(
        ValueError, match="^research blueprint measured development folds insufficient$"
    ):
        verify_research_study_result(blueprint, study)


def test_batch_cannot_substitute_a_campaign_digest_even_with_realigned_study():
    blueprint = _blueprint()
    plan = compile_research_campaign(blueprint)
    manifest = freeze_prospective_devval_campaign_v1(plan)
    study_plan = DevvalCampaignStudyPlanV1(
        **blueprint.study.model_dump(mode="python"), campaign_sha256=manifest.sha256
    )
    # Exercise only the handoff coordinates. The fitted-binding entry point
    # first checks the full typed batch and study payloads independently.
    batch = SimpleNamespace(
        research_blueprint_sha256=blueprint.canonical_sha256(),
        campaign_sha256=manifest.sha256,
        plan_sha256=plan.canonical_sha256(),
        source_commit=plan.declared_source_commit,
        source_tree=plan.declared_source_tree,
        source_manifest_sha256=plan.manifest_sha256,
        series=tuple(
            SimpleNamespace(
                aggregation=SimpleNamespace(
                    plan=SimpleNamespace(window=window, instrument_id=instrument)
                )
            )
            for window in (plan.development, plan.validation)
            for instrument in plan.instrument_ids
        ),
    )
    study = SimpleNamespace(plan=study_plan, development_folds=(None, None))
    verify_research_batch_and_study(blueprint, batch, study)
    batch.campaign_sha256 = sha(b"unrelated-campaign")
    study.plan = study_plan.model_copy(
        update={"campaign_sha256": batch.campaign_sha256}
    )
    with pytest.raises(ValueError, match="^research blueprint batch campaign differs$"):
        verify_research_batch_and_study(blueprint, batch, study)
