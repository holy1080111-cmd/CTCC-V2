"""Evaluate predeclared probability models on Development and Validation."""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import asdict
from decimal import Decimal, localcontext
from typing import Annotated, Literal

from pydantic import Field, model_validator

from app.domain.source_primitives import sha
from app.mie.validation.artifact import FrozenGate3Artifact, _freeze
from app.mie.validation.contracts import (
    BaselineKind,
    CandidateSpec,
    CostModel,
    DatasetPartition,
    Gate3Contract,
    Gate3Metric,
    Identifier,
    ReliabilityBin,
    Sha256,
    TrialRegistry,
)
from app.mie.validation.metrics import (
    _ARITHMETIC_CONTEXT,
    brier_score,
    constant_prevalence_baseline,
    no_skill_baseline,
    probability_metrics,
)
from app.mie.validation.prospective_devval_campaign_study_v1 import (
    DevvalCampaignStudyV1,
)
from app.mie.validation.replay import PointInTimeReplaySnapshot

_MODEL_VERSION = "empirical-probability-bins.v1"
_HALF = Decimal("0.5")
_Probability = Annotated[Decimal, Field(ge=0, le=1)]
_Threshold = Annotated[Decimal, Field(gt=0, le=1, decimal_places=12)]
_BinEdge = Annotated[Decimal, Field(gt=-10, lt=10, decimal_places=12)]
_GitObjectId = Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
_Status = Literal[
    "development_rejected",
    "validation_rejected",
    "candidate_ready_for_separate_freeze",
]
_Rejection = Literal[
    "bin_coverage", "development_brier", "validation_brier", "validation_calibration"
]


class DevvalCandidateTrialsError(ValueError):
    """Refuse malformed or unpinned inputs using a fixed diagnostic."""


class DevvalCandidateTrialV1(Gate3Contract):
    trial_id: Identifier
    feature: Literal["normalized_momentum", "velocity_to_volatility"]
    bin_edges: tuple[_BinEdge, ...] = Field(max_length=31)
    prior_weight: Decimal = Field(gt=0, le=10_000, decimal_places=12)
    minimum_bin_observations: int = Field(ge=1, le=1_000_000)

    @model_validator(mode="after")
    def validate_edges(self) -> DevvalCandidateTrialV1:
        if self.bin_edges != tuple(sorted(set(self.bin_edges))):
            raise ValueError("bin edges must be strictly increasing")
        return self


class DevvalCandidateTrialPolicyV1(Gate3Contract):
    candidate_id: Identifier
    trial_registry: TrialRegistry
    trials: tuple[DevvalCandidateTrialV1, ...] = Field(min_length=1, max_length=64)
    cost_model: CostModel
    min_development_brier_improvement: _Threshold
    min_validation_brier_improvement: _Threshold
    max_validation_ece: Decimal = Field(ge=0, le=1, decimal_places=12)
    reliability_bin_count: int = Field(ge=2, le=100)

    @model_validator(mode="after")
    def validate_registry(self) -> DevvalCandidateTrialPolicyV1:
        pinned = tuple(
            (trial.trial_id, trial.configuration_sha256)
            for trial in self.trial_registry.trials
        )
        configured = tuple(
            (trial.trial_id, trial.canonical_sha256()) for trial in self.trials
        )
        if (
            pinned != configured
            or self.trial_registry.selection_metric != Gate3Metric.BRIER_SCORE
        ):
            raise ValueError(
                "trial registry must pin all configured Brier trials in order"
            )
        return self


class DevvalCandidateTrialPlanV1(DevvalCandidateTrialPolicyV1):
    schema_version: Literal["ctcc.mie.devval_candidate_trial_plan.v1"] = (
        "ctcc.mie.devval_candidate_trial_plan.v1"
    )
    study_sha256: Sha256
    legacy_scores_sha256: Sha256


class DevvalLegacyScoreRowV1(Gate3Contract):
    feature_replay_sha256: Sha256
    probability: _Probability


class DevvalLegacyScoresV1(Gate3Contract):
    schema_version: Literal["ctcc.mie.devval_legacy_scores.v1"] = (
        "ctcc.mie.devval_legacy_scores.v1"
    )
    study_sha256: Sha256
    legacy_source_sha256: Sha256
    development: tuple[DevvalLegacyScoreRowV1, ...]
    validation: tuple[DevvalLegacyScoreRowV1, ...]


class DevvalCandidateSourceV1(Gate3Contract):
    commit: _GitObjectId
    tree: _GitObjectId
    manifest_sha256: Sha256


class _ModelBin(Gate3Contract):
    observations: int = Field(ge=0)
    positives: int = Field(ge=0)
    probability: _Probability | None


class _FittedModel(Gate3Contract):
    model_version: Literal["empirical-probability-bins.v1"] = _MODEL_VERSION
    trial: DevvalCandidateTrialV1
    bins: tuple[_ModelBin, ...]
    development_rows_sha256: Sha256
    cost_model: CostModel


class _BrierComparison(Gate3Contract):
    observations: int = Field(gt=0)
    candidate: _Probability | None
    baselines: dict[BaselineKind, _Probability]
    improvement: Decimal | None


class _DevelopmentFold(Gate3Contract):
    fold_index: int = Field(ge=0)
    training_observations: int = Field(gt=0)
    bins: tuple[_ModelBin, ...]
    comparison: _BrierComparison
    rejection: _Rejection | None


class _DevelopmentTrial(Gate3Contract):
    trial_id: Identifier
    folds: tuple[_DevelopmentFold, ...] = Field(min_length=1)
    pooled_comparison: _BrierComparison
    rejection: _Rejection | None


class _ValidationScore(Gate3Contract):
    subject_id: Identifier
    brier_score: _Probability
    log_loss: Decimal = Field(ge=0)
    expected_calibration_error: _Probability
    sample_count: int = Field(gt=0)
    reliability_bins: tuple[ReliabilityBin, ...]


class DevvalCandidateTrialsV1(Gate3Contract):
    schema_version: Literal["ctcc.mie.devval_candidate_trials.v1"] = (
        "ctcc.mie.devval_candidate_trials.v1"
    )
    plan: DevvalCandidateTrialPlanV1
    plan_sha256: Sha256
    source_checkpoint: DevvalCandidateSourceV1
    study_plan_sha256: Sha256
    row_inventory_sha256: Sha256
    legacy_source_sha256: Sha256
    cost_model_basis: Literal["declared_assumptions"] = "declared_assumptions"
    development_trials: tuple[_DevelopmentTrial, ...]
    selected_model: _FittedModel | None
    validation_comparison: _BrierComparison | None
    validation_metrics: tuple[_ValidationScore, ...] | None
    validation_rejections: tuple[_Rejection, ...]
    candidate: CandidateSpec | None
    status: _Status
    independent_preregistration_verified: Literal[False] = False
    predictive_oos_eligible: Literal[False] = False
    economic_validation: Literal[False] = False
    execution_authority: Literal[False] = False


def _bin_index(replay: PointInTimeReplaySnapshot, trial: DevvalCandidateTrialV1) -> int:
    snapshot = replay.feature_snapshot
    value = (
        snapshot.momentum.normalized_momentum
        if trial.feature == "normalized_momentum"
        else snapshot.dynamics.velocity_to_volatility
    )
    return bisect_right(trial.bin_edges, value)


def _fitted_bin_probability(
    trial: DevvalCandidateTrialV1, observations: int, positives: int
) -> Decimal | None:
    if observations < trial.minimum_bin_observations:
        return None
    with localcontext(_ARITHMETIC_CONTEXT):
        return (Decimal(positives) + _HALF * trial.prior_weight) / (
            Decimal(observations) + trial.prior_weight
        )


def _fit_bins(rows, trial):
    counts = [0] * (len(trial.bin_edges) + 1)
    positives = counts.copy()
    for row in rows:
        index = _bin_index(row.feature, trial)
        counts[index] += 1
        positives[index] += int(row.label.positive)

    return tuple(
        _ModelBin(
            observations=count,
            positives=positive,
            probability=_fitted_bin_probability(trial, count, positive),
        )
        for count, positive in zip(counts, positives, strict=True)
    )


def _predictions(rows, trial, bins):
    return tuple(bins[_bin_index(row.feature, trial)].probability for row in rows)


def _labels(rows):
    return tuple(row.label.positive for row in rows)


def _baseline_predictions(training, legacy):
    count = len(legacy)
    return {
        BaselineKind.CONSTANT_PREVALENCE: constant_prevalence_baseline(
            _labels(training), evaluation_observation_count=count
        ),
        BaselineKind.FROZEN_LEGACY_SCORE: tuple(row.probability for row in legacy),
        BaselineKind.NO_SKILL: no_skill_baseline(observation_count=count),
    }


def _compare(predictions, baselines, outcomes):
    scores = {kind: brier_score(values, outcomes) for kind, values in baselines.items()}
    score = None
    improvement = None
    if all(value is not None for value in predictions):
        score = brier_score(predictions, outcomes)
        with localcontext(_ARITHMETIC_CONTEXT):
            improvement = min(scores.values()) - score
    return _BrierComparison(
        observations=len(outcomes),
        candidate=score,
        baselines=scores,
        improvement=improvement,
    )


def _development_trial(rows, folds, plan, trial, legacy):
    results = []
    outcomes = []
    predictions = []
    baseline_predictions = {kind: [] for kind in BaselineKind}
    for index, fold in enumerate(folds):
        training = tuple(rows[i] for i in fold.training_indices)
        evaluation = tuple(rows[i] for i in fold.validation_indices)
        bins = _fit_bins(training, trial)
        fold_predictions = _predictions(evaluation, trial, bins)
        fold_baselines = _baseline_predictions(
            training, tuple(legacy[i] for i in fold.validation_indices)
        )
        fold_outcomes = _labels(evaluation)
        comparison = _compare(fold_predictions, fold_baselines, fold_outcomes)
        results.append(
            _DevelopmentFold(
                fold_index=index,
                training_observations=len(training),
                bins=bins,
                comparison=comparison,
                rejection="bin_coverage" if comparison.candidate is None else None,
            )
        )
        outcomes.extend(fold_outcomes)
        predictions.extend(fold_predictions)
        for kind, values in fold_baselines.items():
            baseline_predictions[kind].extend(values)
    pooled = _compare(predictions, baseline_predictions, outcomes)
    rejection = None
    if pooled.improvement is None:
        rejection = "bin_coverage"
    elif pooled.improvement < plan.min_development_brier_improvement:
        rejection = "development_brier"
    return _DevelopmentTrial(
        trial_id=trial.trial_id,
        folds=tuple(results),
        pooled_comparison=pooled,
        rejection=rejection,
    )


def _select_development(rows, folds, plan, legacy):
    results = tuple(
        _development_trial(rows, folds, plan, trial, legacy) for trial in plan.trials
    )
    eligible = tuple(item for item in results if item.rejection is None)
    if not eligible:
        return results, None
    winner = min(
        eligible, key=lambda item: (item.pooled_comparison.candidate, item.trial_id)
    )
    trial = next(item for item in plan.trials if item.trial_id == winner.trial_id)
    return results, _FittedModel(
        trial=trial,
        bins=_fit_bins(rows, trial),
        development_rows_sha256=sha(
            b"".join(bytes.fromhex(row.canonical_sha256()) for row in rows)
        ),
        cost_model=plan.cost_model,
    )


def _validation_score(subject, predictions, outcomes, bin_count):
    metrics = probability_metrics(predictions, outcomes, bin_count=bin_count)
    bins = tuple(
        ReliabilityBin(
            subject_id=subject, partition=DatasetPartition.VALIDATION, **asdict(item)
        )
        for item in metrics.reliability_bins
    )
    return _ValidationScore(
        subject_id=subject,
        brier_score=metrics.brier_score,
        log_loss=metrics.log_loss,
        expected_calibration_error=metrics.expected_calibration_error,
        sample_count=metrics.sample_count,
        reliability_bins=bins,
    )


def _evaluate_validation(study, model, plan, legacy):
    predictions = _predictions(study.validation, model.trial, model.bins)
    baselines = _baseline_predictions(study.development, legacy)
    outcomes = _labels(study.validation)
    comparison = _compare(predictions, baselines, outcomes)
    if comparison.improvement is None:
        return comparison, None, ("bin_coverage",)
    model_score = _validation_score(
        "candidate", predictions, outcomes, plan.reliability_bin_count
    )
    metrics = (
        model_score,
        *(
            _validation_score(kind.value, values, outcomes, plan.reliability_bin_count)
            for kind, values in baselines.items()
        ),
    )
    rejections = []
    if comparison.improvement < plan.min_validation_brier_improvement:
        rejections.append("validation_brier")
    if model_score.expected_calibration_error > plan.max_validation_ece:
        rejections.append("validation_calibration")
    return comparison, metrics, tuple(rejections)


def _require_alignment(rows, legacy):
    if tuple(row.feature.replay_sha256 for row in rows) != tuple(
        score.feature_replay_sha256 for score in legacy
    ):
        raise DevvalCandidateTrialsError("LEGACY_ALIGNMENT")


def _require_distinct_evaluation_rows(rows, folds):
    used = set()
    for fold in folds:
        training = set(fold.training_indices)
        evaluation = set(fold.validation_indices)
        if (
            not training
            or not evaluation
            or len(training) != len(fold.training_indices)
            or len(evaluation) != len(fold.validation_indices)
            or min(training | evaluation) < 0
            or max(training | evaluation) >= len(rows)
            or max(training) >= min(evaluation)
            or used.intersection(evaluation)
        ):
            raise DevvalCandidateTrialsError("FOLD_ALIGNMENT")
        used.update(evaluation)


def _fit_candidate_trials(
    study: FrozenGate3Artifact[DevvalCampaignStudyV1],
    *,
    plan: DevvalCandidateTrialPlanV1,
    expected_plan_sha256: str,
    legacy_scores: DevvalLegacyScoresV1,
    source_checkpoint: DevvalCandidateSourceV1,
) -> FrozenGate3Artifact[DevvalCandidateTrialsV1]:
    """Evaluate only the authenticated same-invocation research study."""

    plan = DevvalCandidateTrialPlanV1.model_validate(plan)
    legacy_scores = DevvalLegacyScoresV1.model_validate(legacy_scores)
    source_checkpoint = DevvalCandidateSourceV1.model_validate(source_checkpoint)
    if plan.canonical_sha256() != expected_plan_sha256:
        raise DevvalCandidateTrialsError("PLAN_PIN_MISMATCH")
    if (
        not isinstance(study.contract, DevvalCampaignStudyV1)
        or sha(study.payload) != study.sha256
        or study.sha256 != study.contract.canonical_sha256()
        or study.sha256 != plan.study_sha256
    ):
        raise DevvalCandidateTrialsError("STUDY_PIN_MISMATCH")
    if (
        legacy_scores.study_sha256 != study.sha256
        or legacy_scores.canonical_sha256() != plan.legacy_scores_sha256
    ):
        raise DevvalCandidateTrialsError("LEGACY_PIN_MISMATCH")
    research = study.contract
    if (
        not research.development
        or not research.validation
        or not research.development_folds
    ):
        raise DevvalCandidateTrialsError("STUDY_INCOMPLETE")
    _require_alignment(research.development, legacy_scores.development)
    _require_alignment(research.validation, legacy_scores.validation)
    _require_distinct_evaluation_rows(research.development, research.development_folds)

    trials, model = _select_development(
        research.development,
        research.development_folds,
        plan,
        legacy_scores.development,
    )
    status = "development_rejected"
    validation_comparison = validation_metrics = candidate = None
    validation_rejections = ()
    if model is not None:
        validation_comparison, validation_metrics, validation_rejections = (
            _evaluate_validation(research, model, plan, legacy_scores.validation)
        )
        status = "validation_rejected"
        if not validation_rejections:
            status = "candidate_ready_for_separate_freeze"
            candidate = CandidateSpec(
                candidate_id=plan.candidate_id,
                selected_trial_id=model.trial.trial_id,
                model_version=model.model_version,
                configuration_sha256=model.canonical_sha256(),
                source_sha256=source_checkpoint.canonical_sha256(),
            )
    return _freeze(
        DevvalCandidateTrialsV1(
            plan=plan,
            plan_sha256=expected_plan_sha256,
            source_checkpoint=source_checkpoint,
            study_plan_sha256=research.study_plan_sha256,
            row_inventory_sha256=research.row_inventory_sha256,
            legacy_source_sha256=legacy_scores.legacy_source_sha256,
            development_trials=trials,
            selected_model=model,
            validation_comparison=validation_comparison,
            validation_metrics=validation_metrics,
            validation_rejections=validation_rejections,
            candidate=candidate,
            status=status,
        ),
        contract_type=DevvalCandidateTrialsV1,
    )
