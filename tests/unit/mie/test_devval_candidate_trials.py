"""Synthetic controls for all-trial accounting and D-only candidate fitting.

The altered outcomes below deliberately exercise the pure fitting seam; they
are not source-replayed exchange outcomes or predictive evidence.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.domain.source_primitives import sha
from app.mie.validation.artifact import _freeze
from app.mie.validation.contracts import (
    CostModel,
    FrozenTrial,
    Gate3Metric,
    TrialRegistry,
)
from app.mie.validation.prospective_devval_campaign_study_v1 import (
    DevvalCampaignStudyV1,
    _prepare_study,
)
from app.mie.validation.prospective_devval_candidate_trials_v1 import (
    DevvalCandidateSourceV1,
    DevvalCandidateTrialPlanV1,
    DevvalCandidateTrialsError,
    DevvalCandidateTrialV1,
    DevvalLegacyScoreRowV1,
    DevvalLegacyScoresV1,
    _fit_candidate_trials,
    _select_development,
)
from tests.unit.mie.test_devval_campaign_study import (
    _study_plan,
    synthetic_batch,  # noqa: F401 - Imported pytest fixture.
)


@pytest.fixture(scope="module")
def synthetic_study(synthetic_batch):  # noqa: F811 - Pytest resolves the imported fixture.
    plan = _study_plan(synthetic_batch)
    return _prepare_study(
        synthetic_batch, plan=plan, expected_plan_sha256=plan.canonical_sha256()
    )


def _with_outcomes(study, *, validation_positive_count: int):
    """Make explicit fake outcomes while leaving all feature bytes untouched."""
    original = study.contract
    negative_development = {
        index
        for fold in original.development_folds
        for index in fold.validation_indices[-3:]
    }

    def replace(rows, positive_at):
        changed = []
        for index, row in enumerate(rows):
            positive = positive_at(index)
            label = row.label.model_copy(
                update={
                    "positive": positive,
                    "forward_return": Decimal("0.01") if positive else Decimal("-0.01"),
                }
            )
            changed.append(row.model_copy(update={"label": label}))
        return tuple(changed)

    contract = original.model_copy(
        update={
            "development": replace(
                original.development, lambda index: index not in negative_development
            ),
            "validation": replace(
                original.validation, lambda index: index < validation_positive_count
            ),
        }
    )
    return _freeze(contract, contract_type=DevvalCampaignStudyV1)


@pytest.fixture(scope="module")
def synthetic_accepted_study(synthetic_study):
    return _with_outcomes(synthetic_study, validation_positive_count=79)


def _inputs(study):
    legacy = DevvalLegacyScoresV1(
        study_sha256=study.sha256,
        legacy_source_sha256=sha(b"synthetic-frozen-legacy-vector"),
        development=tuple(
            DevvalLegacyScoreRowV1(
                feature_replay_sha256=row.feature.replay_sha256,
                probability=Decimal("0.5"),
            )
            for row in study.contract.development
        ),
        validation=tuple(
            DevvalLegacyScoreRowV1(
                feature_replay_sha256=row.feature.replay_sha256,
                probability=Decimal("0.5"),
            )
            for row in study.contract.validation
        ),
    )
    trials = (
        DevvalCandidateTrialV1(
            trial_id="a.all",
            feature="normalized_momentum",
            bin_edges=(),
            prior_weight=Decimal(1000),
            minimum_bin_observations=1,
        ),
        DevvalCandidateTrialV1(
            trial_id="z.reject",
            feature="velocity_to_volatility",
            bin_edges=(),
            prior_weight=Decimal(1000),
            minimum_bin_observations=1000,
        ),
    )
    plan = DevvalCandidateTrialPlanV1(
        candidate_id="synthetic.candidate",
        study_sha256=study.sha256,
        legacy_scores_sha256=legacy.canonical_sha256(),
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
    source = DevvalCandidateSourceV1(
        commit="a" * 40, tree="b" * 40, manifest_sha256="c" * 64
    )
    return plan, legacy, source


def _fit(study, plan, legacy, source):
    return _fit_candidate_trials(
        study,
        plan=plan,
        expected_plan_sha256=plan.canonical_sha256(),
        legacy_scores=legacy,
        source_checkpoint=source,
    )


def test_all_trials_remain_recorded_including_coverage_rejection(
    synthetic_accepted_study,
):
    plan, legacy, source = _inputs(synthetic_accepted_study)
    result = _fit(synthetic_accepted_study, plan, legacy, source).contract
    assert [item.trial_id for item in result.development_trials] == [
        "a.all",
        "z.reject",
    ]
    assert result.development_trials[0].rejection is None
    assert result.development_trials[1].rejection == "bin_coverage"
    assert all(
        fold.rejection == "bin_coverage" for fold in result.development_trials[1].folds
    )
    assert result.selected_model is not None
    assert result.selected_model.trial.trial_id == "a.all"
    assert result.status == "candidate_ready_for_separate_freeze"
    assert result.candidate is not None
    assert result.cost_model_basis == "declared_assumptions"
    assert not result.independent_preregistration_verified
    assert not result.predictive_oos_eligible
    assert not result.economic_validation


def test_validation_outcome_change_cannot_change_development_model(
    synthetic_accepted_study,
):
    rejected_study = _with_outcomes(
        synthetic_accepted_study, validation_positive_count=126
    )
    plan, legacy, source = _inputs(synthetic_accepted_study)
    first_trials, first_model = _select_development(
        synthetic_accepted_study.contract.development,
        synthetic_accepted_study.contract.development_folds,
        plan,
        legacy.development,
    )
    other_trials, other_model = _select_development(
        rejected_study.contract.development,
        rejected_study.contract.development_folds,
        plan,
        legacy.development,
    )
    assert first_model is not None
    assert first_model == other_model and first_trials == other_trials
    with pytest.raises(DevvalCandidateTrialsError, match="STUDY_PIN_MISMATCH"):
        _fit(rejected_study, plan, legacy, source)


def test_legacy_vector_must_match_exact_feature_replay_sequence(
    synthetic_accepted_study,
):
    plan, legacy, source = _inputs(synthetic_accepted_study)
    changed = list(legacy.validation)
    changed[0], changed[1] = changed[1], changed[0]
    misaligned = legacy.model_copy(update={"validation": tuple(changed)})
    repinned = plan.model_copy(
        update={"legacy_scores_sha256": misaligned.canonical_sha256()}
    )
    with pytest.raises(DevvalCandidateTrialsError, match="LEGACY_ALIGNMENT"):
        _fit(synthetic_accepted_study, repinned, misaligned, source)


def test_validation_rejection_retains_model_and_never_freezes_candidate(
    synthetic_accepted_study,
):
    rejected_study = _with_outcomes(
        synthetic_accepted_study, validation_positive_count=126
    )
    good_plan, good_legacy, source = _inputs(synthetic_accepted_study)
    bad_plan, bad_legacy, _ = _inputs(rejected_study)
    accepted = _fit(synthetic_accepted_study, good_plan, good_legacy, source).contract
    rejected = _fit(rejected_study, bad_plan, bad_legacy, source).contract
    assert accepted.selected_model == rejected.selected_model
    assert accepted.development_trials == rejected.development_trials
    assert rejected.status == "validation_rejected"
    assert "validation_brier" in rejected.validation_rejections
    assert rejected.validation_metrics is not None
    assert rejected.candidate is None
    assert not rejected.predictive_oos_eligible and not rejected.execution_authority
