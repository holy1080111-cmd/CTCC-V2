from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext
from enum import Enum, StrEnum
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    field_validator,
    model_validator,
)


SHA256_PATTERN = r"^[0-9a-f]{64}$"
IDENTIFIER_PATTERN = r"^[a-z0-9]+(?:[._:-][a-z0-9]+)*$"
VERSION_PATTERN = r"^[A-Za-z0-9]+(?:[._+-][A-Za-z0-9]+)*$"
DECIMAL_PRECISION = 50

Sha256 = Annotated[str, Field(pattern=SHA256_PATTERN)]
Identifier = Annotated[
    str,
    Field(min_length=3, max_length=160, pattern=IDENTIFIER_PATTERN),
]
Version = Annotated[
    str,
    Field(min_length=1, max_length=80, pattern=VERSION_PATTERN),
]
ParameterValue = StrictBool | StrictInt | Decimal | str


def require_utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware UTC")
    if value.utcoffset() != timedelta(0):
        raise ValueError(f"{field_name} must use UTC")
    return value


def _aligned_to_interval(value: datetime, interval_seconds: int) -> bool:
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    delta = value - epoch
    whole_seconds = delta.days * 86_400 + delta.seconds
    return delta.microseconds == 0 and whole_seconds % interval_seconds == 0


def _canonical_decimal(value: Decimal) -> str:
    if not value.is_finite():
        raise ValueError("canonical decimals must be finite")
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    if text in {"-0", ""}:
        return "0"
    return text


def _canonical_value(value: object) -> object:
    if isinstance(value, BaseModel):
        return _canonical_value(value.model_dump(mode="python"))
    if isinstance(value, dict):
        return {str(key): _canonical_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_canonical_value(item) for item in value]
    if isinstance(value, datetime):
        utc_value = value.astimezone(timezone.utc)
        return utc_value.isoformat(timespec="microseconds").replace(
            "+00:00", "Z"
        )
    if isinstance(value, Decimal):
        return _canonical_decimal(value)
    if isinstance(value, Enum):
        return value.value
    return value


class Gate3Contract(BaseModel):
    """Strict immutable boundary for offline Gate 3 validation data."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        allow_inf_nan=False,
        revalidate_instances="always",
        str_strip_whitespace=True,
    )

    def canonical_json_bytes(self) -> bytes:
        return json.dumps(
            _canonical_value(self),
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")

    def canonical_json(self) -> str:
        return self.canonical_json_bytes().decode("utf-8")

    def canonical_sha256(self) -> str:
        return hashlib.sha256(self.canonical_json_bytes()).hexdigest()


class DatasetPartition(StrEnum):
    DEVELOPMENT = "development"
    VALIDATION = "validation"
    RETROSPECTIVE_HOLDOUT = "retrospective_holdout"


class OutcomeKind(StrEnum):
    FORWARD_RETURN_DIRECTION = "forward_return_direction"


class BaselineKind(StrEnum):
    CONSTANT_PREVALENCE = "constant_prevalence"
    FROZEN_LEGACY_SCORE = "frozen_legacy_score"
    NO_SKILL = "no_skill"


class Gate3Metric(StrEnum):
    BRIER_SCORE = "brier_score"
    CVAR = "cvar"
    EXPECTED_CALIBRATION_ERROR = "expected_calibration_error"
    FEES = "fees"
    FUNDING = "funding"
    LOG_LOSS = "log_loss"
    MAX_DRAWDOWN = "max_drawdown"
    RELIABILITY_BINS = "reliability_bins"
    SAMPLE_COUNT = "sample_count"
    SLIPPAGE = "slippage"
    SPREAD = "spread"
    TURNOVER = "turnover"


class MultipleTestingCorrection(StrEnum):
    HOLM_BONFERRONI = "holm_bonferroni"


class Gate3Claim(StrEnum):
    COMPUTATIONAL = "computational"
    PREDICTIVE_OOS = "predictive_oos"


REQUIRED_BASELINES = frozenset(BaselineKind)
REQUIRED_METRICS = frozenset(Gate3Metric)
PROBABILITY_ESTIMATE_METRICS = frozenset(
    {
        Gate3Metric.BRIER_SCORE,
        Gate3Metric.EXPECTED_CALIBRATION_ERROR,
        Gate3Metric.LOG_LOSS,
        Gate3Metric.SAMPLE_COUNT,
    }
)
ECONOMIC_ESTIMATE_METRICS = frozenset(
    {
        Gate3Metric.CVAR,
        Gate3Metric.FEES,
        Gate3Metric.FUNDING,
        Gate3Metric.MAX_DRAWDOWN,
        Gate3Metric.SLIPPAGE,
        Gate3Metric.SPREAD,
        Gate3Metric.TURNOVER,
    }
)
CANDIDATE_ESTIMATE_METRICS = (
    PROBABILITY_ESTIMATE_METRICS | ECONOMIC_ESTIMATE_METRICS
)


class DatasetIdentity(Gate3Contract):
    """Frozen identity and point-in-time provenance for replay input."""

    dataset_id: Identifier
    source: Identifier
    source_version: Version
    instrument_ids: tuple[str, ...] = Field(min_length=1)
    manifest_sha256: Sha256
    content_sha256: Sha256
    expected_rows: int = Field(ge=1)
    first_event_at: datetime
    last_event_at: datetime
    frozen_at: datetime
    event_time_field: Identifier
    available_time_field: Identifier
    point_in_time_provenance: Literal[True] = True

    @field_validator("first_event_at", "last_event_at", "frozen_at")
    @classmethod
    def validate_timestamps(cls, value: datetime, info) -> datetime:
        return require_utc(value, info.field_name)

    @field_validator("instrument_ids")
    @classmethod
    def validate_instruments(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item for item in value):
            raise ValueError("dataset instrument ids cannot be blank")
        if len(value) != len(set(value)):
            raise ValueError("dataset instrument ids must be unique")
        if value != tuple(sorted(value)):
            raise ValueError("dataset instrument ids must be canonically sorted")
        return value

    @model_validator(mode="after")
    def validate_window(self) -> "DatasetIdentity":
        if self.last_event_at < self.first_event_at:
            raise ValueError("last event cannot precede first event")
        if self.frozen_at < self.last_event_at:
            raise ValueError("dataset cannot be frozen before its last event")
        return self


class BarConstruction(Gate3Contract):
    interval_seconds: int = Field(ge=1)
    timestamp_semantics: Literal["end_exclusive"] = "end_exclusive"
    timezone: Literal["UTC"] = "UTC"
    require_confirmed_input: Literal[True] = True
    require_complete_bars: Literal[True] = True
    missing_bar_policy: Literal["reject"] = "reject"
    duplicate_bar_policy: Literal["reject"] = "reject"
    late_data_policy: Literal["reject"] = "reject"


class OutcomeLabelSpec(Gate3Contract):
    label_id: Identifier
    kind: OutcomeKind
    horizon_seconds: int = Field(ge=1)
    dependency_seconds: int = Field(ge=1)
    positive_threshold: Decimal
    available_after_horizon: Literal[True] = True

    @field_validator("positive_threshold")
    @classmethod
    def validate_threshold(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("outcome threshold must be finite")
        return value

    @model_validator(mode="after")
    def validate_dependency(self) -> "OutcomeLabelSpec":
        if self.dependency_seconds < self.horizon_seconds:
            raise ValueError("label dependency must cover its horizon")
        return self


class FrozenParameter(Gate3Contract):
    name: Identifier
    value: ParameterValue

    @field_validator("value")
    @classmethod
    def validate_value(cls, value: ParameterValue) -> ParameterValue:
        if isinstance(value, Decimal) and not value.is_finite():
            raise ValueError("feature parameters must be finite")
        if isinstance(value, str) and not value:
            raise ValueError("feature parameter strings cannot be blank")
        return value


class FeatureSpec(Gate3Contract):
    feature_id: Identifier
    feature_version: Version
    dependency_seconds: int = Field(ge=1)
    parameters: tuple[FrozenParameter, ...] = ()

    @field_validator("parameters")
    @classmethod
    def validate_parameters(
        cls, value: tuple[FrozenParameter, ...]
    ) -> tuple[FrozenParameter, ...]:
        names = tuple(item.name for item in value)
        if len(names) != len(set(names)):
            raise ValueError("feature parameter names must be unique")
        if names != tuple(sorted(names)):
            raise ValueError("feature parameters must be canonically sorted")
        return value


class PartitionWindow(Gate3Contract):
    partition: DatasetPartition
    start_at: datetime
    end_at: datetime

    @field_validator("start_at", "end_at")
    @classmethod
    def validate_timestamps(cls, value: datetime, info) -> datetime:
        return require_utc(value, info.field_name)

    @model_validator(mode="after")
    def validate_window(self) -> "PartitionWindow":
        if self.end_at <= self.start_at:
            raise ValueError("partition end must follow start")
        return self


class PurgedWalkForwardSplit(Gate3Contract):
    development: PartitionWindow
    validation: PartitionWindow
    holdout: PartitionWindow
    purge_seconds: int = Field(ge=1)
    embargo_seconds: int = Field(ge=1)
    max_feature_dependency_seconds: int = Field(ge=1)
    label_dependency_seconds: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_split(self) -> "PurgedWalkForwardSplit":
        expected = (
            (self.development, DatasetPartition.DEVELOPMENT),
            (self.validation, DatasetPartition.VALIDATION),
            (self.holdout, DatasetPartition.RETROSPECTIVE_HOLDOUT),
        )
        if any(window.partition != partition for window, partition in expected):
            raise ValueError("split window has the wrong partition identity")
        if self.development.end_at > self.validation.start_at:
            raise ValueError("development and validation windows cannot overlap")
        if self.validation.end_at > self.holdout.start_at:
            raise ValueError("validation and holdout windows cannot overlap")
        required = max(
            self.max_feature_dependency_seconds,
            self.label_dependency_seconds,
        )
        if self.purge_seconds < required:
            raise ValueError("purge must cover the largest dependency window")
        if self.embargo_seconds < required:
            raise ValueError("embargo must cover the largest dependency window")
        development_gap = int(
            (self.validation.start_at - self.development.end_at).total_seconds()
        )
        holdout_gap = int(
            (self.holdout.start_at - self.validation.end_at).total_seconds()
        )
        if development_gap < self.purge_seconds:
            raise ValueError("development/validation gap is below the purge")
        if holdout_gap < self.embargo_seconds:
            raise ValueError("validation/holdout gap is below the embargo")
        return self


class BaselineSpec(Gate3Contract):
    baseline_id: Identifier
    kind: BaselineKind
    version: Version
    configuration_sha256: Sha256
    frozen_at: datetime

    @field_validator("frozen_at")
    @classmethod
    def validate_frozen_at(cls, value: datetime) -> datetime:
        return require_utc(value, "frozen_at")


class CandidateSpec(Gate3Contract):
    candidate_id: Identifier
    selected_trial_id: Identifier
    model_version: Version
    configuration_sha256: Sha256
    source_sha256: Sha256


class CostModel(Gate3Contract):
    model_id: Identifier
    version: Version
    fee_bps: Decimal = Field(ge=0)
    funding_bps: Decimal = Field(ge=0)
    spread_bps: Decimal = Field(ge=0)
    slippage_bps: Decimal = Field(ge=0)
    funding_interval_seconds: int = Field(ge=1)
    flatten_at_end: Literal[True] = True
    cost_free_results_descriptive_only: Literal[True] = True

    @field_validator("fee_bps", "funding_bps", "spread_bps", "slippage_bps")
    @classmethod
    def validate_cost(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("cost inputs must be finite")
        return value

    @property
    def total_bps(self) -> Decimal:
        return self.fee_bps + self.funding_bps + self.spread_bps + self.slippage_bps


class UncertaintyPlan(Gate3Contract):
    method: Literal["block_bootstrap"] = "block_bootstrap"
    confidence_level: Decimal = Field(gt=0, lt=1)
    resamples: int = Field(ge=1_000)
    block_length: int = Field(ge=2)
    seed: int = Field(ge=0, le=9_223_372_036_854_775_807)
    familywise_alpha: Decimal = Field(gt=0, lt=1)
    multiple_testing_correction: MultipleTestingCorrection

    @field_validator("confidence_level", "familywise_alpha")
    @classmethod
    def validate_confidence(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("confidence level must be finite")
        return value


class FrozenTrial(Gate3Contract):
    trial_id: Identifier
    configuration_sha256: Sha256


class TrialRegistry(Gate3Contract):
    registry_id: Identifier
    trials: tuple[FrozenTrial, ...] = Field(min_length=1)
    declared_trial_count: int = Field(ge=1)
    selection_metric: Gate3Metric

    @field_validator("trials")
    @classmethod
    def validate_trials(
        cls, value: tuple[FrozenTrial, ...]
    ) -> tuple[FrozenTrial, ...]:
        trial_ids = tuple(item.trial_id for item in value)
        if len(trial_ids) != len(set(trial_ids)):
            raise ValueError("trial ids must be unique")
        if trial_ids != tuple(sorted(trial_ids)):
            raise ValueError("trial ids must be canonically sorted")
        return value

    @model_validator(mode="after")
    def validate_count(self) -> "TrialRegistry":
        if self.declared_trial_count != len(self.trials):
            raise ValueError("declared trial count must match trial ids")
        return self


class EvaluationPlan(Gate3Contract):
    metrics: tuple[Gate3Metric, ...]
    reliability_bin_count: int = Field(ge=2, le=100)
    cvar_confidence_level: Decimal = Field(gt=0, lt=1)
    uncertainty: UncertaintyPlan
    trials: TrialRegistry

    @field_validator("metrics")
    @classmethod
    def validate_metrics(
        cls, value: tuple[Gate3Metric, ...]
    ) -> tuple[Gate3Metric, ...]:
        if len(value) != len(set(value)):
            raise ValueError("evaluation metrics must be unique")
        if value != tuple(sorted(value, key=lambda item: item.value)):
            raise ValueError("evaluation metrics must be canonically sorted")
        if set(value) != REQUIRED_METRICS:
            raise ValueError("evaluation plan must include every Gate 3 metric")
        return value

    @field_validator("cvar_confidence_level")
    @classmethod
    def validate_cvar_confidence(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("CVaR confidence level must be finite")
        return value


class Gate3Preregistration(Gate3Contract):
    schema_version: Literal["ctcc.mie.gate3.preregistration.v1"] = (
        "ctcc.mie.gate3.preregistration.v1"
    )
    preregistration_id: Identifier
    created_at: datetime
    source_tree_sha256: Sha256
    dataset: DatasetIdentity
    bar_construction: BarConstruction
    outcome_label: OutcomeLabelSpec
    candidate: CandidateSpec
    features: tuple[FeatureSpec, ...] = Field(min_length=1)
    split: PurgedWalkForwardSplit
    walk_forward_plan_sha256: Sha256
    baselines: tuple[BaselineSpec, ...]
    cost_model: CostModel
    evaluation: EvaluationPlan
    holdout_state: Literal["unread"] = "unread"
    claim_ceiling: Literal[Gate3Claim.PREDICTIVE_OOS] = (
        Gate3Claim.PREDICTIVE_OOS
    )
    authority: Literal["offline_shadow_only"] = "offline_shadow_only"
    runtime_consumers: Literal[0] = 0
    execution_authority: Literal[False] = False

    @field_validator("created_at")
    @classmethod
    def validate_created_at(cls, value: datetime) -> datetime:
        return require_utc(value, "created_at")

    @field_validator("features")
    @classmethod
    def validate_features(
        cls, value: tuple[FeatureSpec, ...]
    ) -> tuple[FeatureSpec, ...]:
        identities = tuple((item.feature_id, item.feature_version) for item in value)
        if len(identities) != len(set(identities)):
            raise ValueError("feature identities must be unique")
        if identities != tuple(sorted(identities)):
            raise ValueError("features must be canonically sorted")
        return value

    @field_validator("baselines")
    @classmethod
    def validate_baselines(
        cls, value: tuple[BaselineSpec, ...]
    ) -> tuple[BaselineSpec, ...]:
        kinds = tuple(item.kind for item in value)
        if len(kinds) != len(set(kinds)):
            raise ValueError("baseline kinds must be unique")
        if value != tuple(sorted(value, key=lambda item: item.kind.value)):
            raise ValueError("baselines must be canonically sorted")
        if set(kinds) != REQUIRED_BASELINES:
            raise ValueError("all required Gate 3 baselines must be frozen")
        return value

    @model_validator(mode="after")
    def validate_preregistration(self) -> "Gate3Preregistration":
        if self.dataset.frozen_at > self.created_at:
            raise ValueError("dataset must be frozen before preregistration")
        if any(item.frozen_at > self.created_at for item in self.baselines):
            raise ValueError("baselines must be frozen before preregistration")
        max_feature_dependency = max(
            item.dependency_seconds for item in self.features
        )
        if (
            self.split.max_feature_dependency_seconds
            != max_feature_dependency
        ):
            raise ValueError("split must record the largest feature dependency")
        if (
            self.split.label_dependency_seconds
            != self.outcome_label.dependency_seconds
        ):
            raise ValueError("split and outcome label dependency must agree")
        if self.split.development.start_at < self.dataset.first_event_at:
            raise ValueError("development window precedes the dataset")
        if self.split.holdout.end_at > self.dataset.last_event_at:
            raise ValueError("holdout window exceeds the dataset")
        interval = self.bar_construction.interval_seconds
        aligned_durations = (
            self.outcome_label.horizon_seconds,
            self.outcome_label.dependency_seconds,
            self.split.purge_seconds,
            self.split.embargo_seconds,
            self.cost_model.funding_interval_seconds,
            *(item.dependency_seconds for item in self.features),
        )
        if any(value % interval for value in aligned_durations):
            raise ValueError("Gate 3 durations must align to the bar interval")
        boundaries = (
            self.split.development.start_at,
            self.split.development.end_at,
            self.split.validation.start_at,
            self.split.validation.end_at,
            self.split.holdout.start_at,
            self.split.holdout.end_at,
        )
        if any(not _aligned_to_interval(value, interval) for value in boundaries):
            raise ValueError("Gate 3 partition boundaries must align to bars")
        selected_trials = tuple(
            item
            for item in self.evaluation.trials.trials
            if item.trial_id == self.candidate.selected_trial_id
        )
        if len(selected_trials) != 1:
            raise ValueError("candidate must select one frozen trial")
        if (
            selected_trials[0].configuration_sha256
            != self.candidate.configuration_sha256
        ):
            raise ValueError(
                "candidate and selected trial configuration disagree"
            )
        return self


class ComponentVersion(Gate3Contract):
    component_id: Identifier
    version: Version
    source_sha256: Sha256


class ReplayProvenance(Gate3Contract):
    preregistration_sha256: Sha256
    dataset_manifest_sha256: Sha256
    dataset_content_sha256: Sha256
    replay_sha256: Sha256
    source_tree_sha256: Sha256
    component_versions: tuple[ComponentVersion, ...] = Field(min_length=1)
    preregistered_at: datetime
    holdout_first_read_at: datetime
    generated_at: datetime

    @field_validator(
        "preregistered_at", "holdout_first_read_at", "generated_at"
    )
    @classmethod
    def validate_timestamps(cls, value: datetime, info) -> datetime:
        return require_utc(value, info.field_name)

    @field_validator("component_versions")
    @classmethod
    def validate_components(
        cls, value: tuple[ComponentVersion, ...]
    ) -> tuple[ComponentVersion, ...]:
        identities = tuple(item.component_id for item in value)
        if len(identities) != len(set(identities)):
            raise ValueError("component identities must be unique")
        if identities != tuple(sorted(identities)):
            raise ValueError("component identities must be canonically sorted")
        return value

    @model_validator(mode="after")
    def validate_timeline(self) -> "ReplayProvenance":
        if self.holdout_first_read_at <= self.preregistered_at:
            raise ValueError("holdout can only be read after preregistration")
        if self.generated_at < self.holdout_first_read_at:
            raise ValueError("artifact cannot precede the holdout read")
        return self


class MetricEstimate(Gate3Contract):
    metric: Gate3Metric
    subject_id: Identifier
    partition: DatasetPartition
    value: Decimal
    sample_count: int = Field(ge=1)
    confidence_lower: Decimal | None = None
    confidence_upper: Decimal | None = None
    cost_inclusive: bool

    @field_validator("value", "confidence_lower", "confidence_upper")
    @classmethod
    def validate_values(cls, value: Decimal | None) -> Decimal | None:
        if value is not None and not value.is_finite():
            raise ValueError("metric estimates must be finite")
        return value

    @model_validator(mode="after")
    def validate_interval(self) -> "MetricEstimate":
        if self.metric == Gate3Metric.RELIABILITY_BINS:
            raise ValueError("reliability bins must use their structured contract")
        if (
            self.metric == Gate3Metric.SAMPLE_COUNT
            and self.value != Decimal(self.sample_count)
        ):
            raise ValueError("sample-count metric must equal its sample count")
        bounds = (self.confidence_lower, self.confidence_upper)
        if (bounds[0] is None) != (bounds[1] is None):
            raise ValueError("metric confidence bounds must be paired")
        if (
            bounds[0] is not None
            and bounds[1] is not None
            and bounds[0] > bounds[1]
        ):
            raise ValueError("metric confidence bounds are reversed")
        if self.metric == Gate3Metric.SAMPLE_COUNT and bounds != (None, None):
            raise ValueError("sample-count metric cannot have confidence bounds")
        return self


class ReliabilityBin(Gate3Contract):
    subject_id: Identifier
    partition: DatasetPartition
    index: int = Field(ge=0)
    lower_bound: Decimal = Field(ge=0, lt=1)
    upper_bound: Decimal = Field(gt=0, le=1)
    mean_prediction: Decimal | None = Field(default=None, ge=0, le=1)
    observed_frequency: Decimal | None = Field(default=None, ge=0, le=1)
    sample_count: int = Field(ge=0)

    @field_validator(
        "lower_bound", "upper_bound", "mean_prediction", "observed_frequency"
    )
    @classmethod
    def validate_values(cls, value: Decimal | None) -> Decimal | None:
        if value is not None and not value.is_finite():
            raise ValueError("reliability values must be finite")
        return value

    @model_validator(mode="after")
    def validate_bounds(self) -> "ReliabilityBin":
        if self.upper_bound <= self.lower_bound:
            raise ValueError("reliability bin upper bound must exceed lower bound")
        estimates = (self.mean_prediction, self.observed_frequency)
        if self.sample_count == 0 and estimates != (None, None):
            raise ValueError("empty reliability bins cannot report estimates")
        if self.sample_count > 0 and any(item is None for item in estimates):
            raise ValueError("non-empty reliability bins require estimates")
        if (
            self.mean_prediction is not None
            and not self.lower_bound
            <= self.mean_prediction
            <= self.upper_bound
        ):
            raise ValueError("mean prediction must lie inside its reliability bin")
        return self


class ExclusionSummary(Gate3Contract):
    reason_code: Identifier
    row_count: int = Field(ge=1)


class TrialTestResult(Gate3Contract):
    trial_id: Identifier
    raw_p_value: Decimal = Field(ge=0, le=1)
    adjusted_p_value: Decimal = Field(ge=0, le=1)
    rejected: bool

    @field_validator("raw_p_value", "adjusted_p_value")
    @classmethod
    def validate_p_values(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("trial p-values must be finite")
        return value


class ReviewerMetadata(Gate3Contract):
    reviewer_id: Identifier
    reviewed_at: datetime
    independent: bool
    leakage_review_passed: bool
    trial_accounting_review_passed: bool
    uncertainty_review_passed: bool
    cost_review_passed: bool

    @field_validator("reviewed_at")
    @classmethod
    def validate_reviewed_at(cls, value: datetime) -> datetime:
        return require_utc(value, "reviewed_at")


class Gate3EvidenceArtifact(Gate3Contract):
    """Self-contained immutable result with no runtime or trading authority."""

    schema_version: Literal["ctcc.mie.gate3.evidence.v1"] = (
        "ctcc.mie.gate3.evidence.v1"
    )
    artifact_id: Identifier
    preregistration: Gate3Preregistration
    provenance: ReplayProvenance
    validation_claim: Gate3Claim
    metric_estimates: tuple[MetricEstimate, ...] = Field(min_length=1)
    reliability_bins: tuple[ReliabilityBin, ...] = Field(min_length=2)
    exclusions: tuple[ExclusionSummary, ...] = ()
    trial_results: tuple[TrialTestResult, ...] = Field(min_length=1)
    executed_trial_count: int = Field(ge=1)
    costs_applied: bool
    reviewer: ReviewerMetadata
    authority: Literal["offline_shadow_only"] = "offline_shadow_only"
    runtime_consumers: Literal[0] = 0
    execution_authority: Literal[False] = False

    @field_validator("metric_estimates")
    @classmethod
    def validate_metric_order(
        cls, value: tuple[MetricEstimate, ...]
    ) -> tuple[MetricEstimate, ...]:
        identities = tuple(
            (item.partition.value, item.subject_id, item.metric.value)
            for item in value
        )
        if len(identities) != len(set(identities)):
            raise ValueError("metric estimate identities must be unique")
        if identities != tuple(sorted(identities)):
            raise ValueError("metric estimates must be canonically sorted")
        return value

    @field_validator("reliability_bins")
    @classmethod
    def validate_reliability_bins(
        cls, value: tuple[ReliabilityBin, ...]
    ) -> tuple[ReliabilityBin, ...]:
        identities = tuple(
            (item.partition.value, item.subject_id, item.index) for item in value
        )
        if len(identities) != len(set(identities)):
            raise ValueError("reliability bin identities must be unique")
        if identities != tuple(sorted(identities)):
            raise ValueError("reliability bins must be canonically sorted")
        groups: dict[tuple[DatasetPartition, str], list[ReliabilityBin]] = {}
        for item in value:
            groups.setdefault((item.partition, item.subject_id), []).append(item)
        for bins in groups.values():
            if tuple(item.index for item in bins) != tuple(range(len(bins))):
                raise ValueError(
                    "reliability bins must use contiguous canonical indexes"
                )
            for previous, current in zip(bins, bins[1:]):
                if previous.upper_bound != current.lower_bound:
                    raise ValueError("reliability bins must be contiguous")
            if bins[0].lower_bound != 0 or bins[-1].upper_bound != 1:
                raise ValueError("reliability bins must cover the unit interval")
        return value

    @field_validator("exclusions")
    @classmethod
    def validate_exclusions(
        cls, value: tuple[ExclusionSummary, ...]
    ) -> tuple[ExclusionSummary, ...]:
        reasons = tuple(item.reason_code for item in value)
        if len(reasons) != len(set(reasons)):
            raise ValueError("exclusion reasons must be unique")
        if reasons != tuple(sorted(reasons)):
            raise ValueError("exclusions must be canonically sorted")
        return value

    @field_validator("trial_results")
    @classmethod
    def validate_trial_result_order(
        cls, value: tuple[TrialTestResult, ...]
    ) -> tuple[TrialTestResult, ...]:
        trial_ids = tuple(item.trial_id for item in value)
        if len(trial_ids) != len(set(trial_ids)):
            raise ValueError("trial result ids must be unique")
        if trial_ids != tuple(sorted(trial_ids)):
            raise ValueError("trial results must be canonically sorted")
        return value

    @model_validator(mode="after")
    def validate_artifact(self) -> "Gate3EvidenceArtifact":
        preregistration = self.preregistration
        provenance = self.provenance
        if provenance.preregistration_sha256 != preregistration.canonical_sha256():
            raise ValueError("preregistration provenance hash mismatch")
        if provenance.preregistered_at != preregistration.created_at:
            raise ValueError("preregistration provenance timestamp mismatch")
        if (
            provenance.dataset_manifest_sha256
            != preregistration.dataset.manifest_sha256
            or provenance.dataset_content_sha256
            != preregistration.dataset.content_sha256
        ):
            raise ValueError("dataset provenance hash mismatch")
        if provenance.source_tree_sha256 != preregistration.source_tree_sha256:
            raise ValueError("source tree provenance hash mismatch")
        if self.executed_trial_count != (
            preregistration.evaluation.trials.declared_trial_count
        ):
            raise ValueError("executed trial count must match preregistration")
        declared_trial_ids = tuple(
            item.trial_id for item in preregistration.evaluation.trials.trials
        )
        if tuple(item.trial_id for item in self.trial_results) != declared_trial_ids:
            raise ValueError("trial results must cover every frozen trial")
        ranked_trials = sorted(
            self.trial_results,
            key=lambda item: (item.raw_p_value, item.trial_id),
        )
        adjusted_by_id: dict[str, Decimal] = {}
        with localcontext() as context:
            context.prec = DECIMAL_PRECISION
            running_maximum = Decimal(0)
            trial_count = len(ranked_trials)
            for rank, item in enumerate(ranked_trials):
                adjusted = min(
                    Decimal(1),
                    item.raw_p_value * Decimal(trial_count - rank),
                )
                running_maximum = max(running_maximum, adjusted)
                adjusted_by_id[item.trial_id] = running_maximum
        alpha = preregistration.evaluation.uncertainty.familywise_alpha
        if any(
            item.adjusted_p_value != adjusted_by_id[item.trial_id]
            or item.rejected != (adjusted_by_id[item.trial_id] <= alpha)
            for item in self.trial_results
        ):
            raise ValueError("trial results disagree with frozen Holm correction")
        if self.reviewer.reviewed_at < provenance.generated_at:
            raise ValueError("review cannot precede artifact generation")

        expected_subjects = {
            preregistration.candidate.candidate_id,
            *(item.baseline_id for item in preregistration.baselines),
        }
        actual_subjects = {item.subject_id for item in self.metric_estimates}
        if not actual_subjects.issubset(expected_subjects):
            raise ValueError("evidence contains an undeclared evaluation subject")

        grouped_metrics: dict[
            tuple[DatasetPartition, str], list[MetricEstimate]
        ] = {}
        for item in self.metric_estimates:
            grouped_metrics.setdefault(
                (item.partition, item.subject_id), []
            ).append(item)
        grouped_bins: dict[
            tuple[DatasetPartition, str], list[ReliabilityBin]
        ] = {}
        for item in self.reliability_bins:
            grouped_bins.setdefault(
                (item.partition, item.subject_id), []
            ).append(item)
        if not set(grouped_bins).issubset(set(grouped_metrics)):
            raise ValueError("reliability bins lack matching subject metrics")

        for identity, estimates in grouped_metrics.items():
            sample_counts = {item.sample_count for item in estimates}
            if len(sample_counts) != 1:
                raise ValueError("subject metric sample counts disagree")
            bins = grouped_bins.get(identity)
            if bins is not None:
                bin_count = preregistration.evaluation.reliability_bin_count
                if len(bins) != bin_count:
                    raise ValueError(
                        "reliability bin count must match preregistration"
                    )
                sample_count = next(iter(sample_counts))
                if sum(item.sample_count for item in bins) != sample_count:
                    raise ValueError(
                        "reliability samples must match subject metrics"
                    )
                denominator = Decimal(bin_count)
                for index, item in enumerate(bins):
                    expected_lower = Decimal(index) / denominator
                    expected_upper = Decimal(index + 1) / denominator
                    if (
                        item.lower_bound != expected_lower
                        or item.upper_bound != expected_upper
                    ):
                        raise ValueError(
                            "reliability bins must use frozen equal widths"
                        )
                    if (
                        index < bin_count - 1
                        and item.mean_prediction is not None
                        and item.mean_prediction >= item.upper_bound
                    ):
                        raise ValueError(
                            "non-final reliability bins are upper-exclusive"
                        )
                with localcontext() as context:
                    context.prec = DECIMAL_PRECISION
                    expected_ece = sum(
                        (
                            Decimal(item.sample_count)
                            / Decimal(sample_count)
                            * abs(
                                item.mean_prediction
                                - item.observed_frequency
                            )
                            for item in bins
                            if item.sample_count
                            and item.mean_prediction is not None
                            and item.observed_frequency is not None
                        ),
                        Decimal(0),
                    )
                reported_ece = next(
                    (
                        item.value
                        for item in estimates
                        if item.metric
                        == Gate3Metric.EXPECTED_CALIBRATION_ERROR
                    ),
                    None,
                )
                if reported_ece is not None and reported_ece != expected_ece:
                    raise ValueError(
                        "reported calibration error disagrees with bins"
                    )

        if self.validation_claim == Gate3Claim.PREDICTIVE_OOS:
            review_checks = (
                self.reviewer.independent,
                self.reviewer.leakage_review_passed,
                self.reviewer.trial_accounting_review_passed,
                self.reviewer.uncertainty_review_passed,
                self.reviewer.cost_review_passed,
            )
            if not all(review_checks):
                raise ValueError("predictive OOS claim requires independent review")
            if not self.costs_applied:
                raise ValueError("cost-free evidence is descriptive only")
            if any(not item.cost_inclusive for item in self.metric_estimates):
                raise ValueError("predictive OOS metrics must be cost inclusive")
            selected_result = next(
                item
                for item in self.trial_results
                if item.trial_id == preregistration.candidate.selected_trial_id
            )
            if not selected_result.rejected:
                raise ValueError(
                    "predictive OOS candidate must pass frozen trial correction"
                )
            holdout = DatasetPartition.RETROSPECTIVE_HOLDOUT
            for subject_id in sorted(expected_subjects):
                identity = (holdout, subject_id)
                estimates = grouped_metrics.get(identity)
                if estimates is None:
                    raise ValueError(
                        "predictive OOS evidence is missing a declared subject"
                    )
                expected_metrics = (
                    CANDIDATE_ESTIMATE_METRICS
                    if subject_id == preregistration.candidate.candidate_id
                    else PROBABILITY_ESTIMATE_METRICS
                )
                if {item.metric for item in estimates} != expected_metrics:
                    raise ValueError(
                        "predictive OOS subject metrics are incomplete"
                    )
                if any(
                    item.metric != Gate3Metric.SAMPLE_COUNT
                    and (
                        item.confidence_lower is None
                        or item.confidence_upper is None
                    )
                    for item in estimates
                ):
                    raise ValueError(
                        "predictive OOS metrics require confidence intervals"
                    )
                if identity not in grouped_bins:
                    raise ValueError(
                        "predictive OOS reliability bins are incomplete"
                    )
        return self
