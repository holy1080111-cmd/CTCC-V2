"""Fail-closed contracts for a genuinely prospective Gate 3 holdout.

The existing Gate 3 preregistration binds a dataset after every byte has been
materialized.  This module adds the earlier seal needed when the holdout has
not happened yet: model selection, costs, trials, coordinates, and the first
permitted access time are frozen before the first holdout event.

These contracts only describe offline evidence.  They do not fetch data,
evaluate a strategy, connect to an account, or grant execution authority.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Literal

from pydantic import Field, StrictBool, field_validator, model_validator

from app.mie.validation.contracts import (
    REQUIRED_BASELINES,
    BarConstruction,
    BaselineSpec,
    CandidateSpec,
    CostModel,
    DatasetIdentity,
    DatasetPartition,
    EvaluationPlan,
    FeatureSpec,
    Gate3Claim,
    Gate3Contract,
    Identifier,
    OutcomeLabelSpec,
    PartitionWindow,
    Sha256,
    Version,
    require_utc,
)


def _aligned_to_interval(value: datetime, interval_seconds: int) -> bool:
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    delta = value - epoch
    whole_seconds = delta.days * 86_400 + delta.seconds
    return delta.microseconds == 0 and whole_seconds % interval_seconds == 0


class ProspectiveHoldoutState(StrEnum):
    """State that is knowable when a future-window seal is frozen."""

    SCHEDULED_UNOBSERVED = "scheduled_unobserved"


class ProspectiveAccessOutcome(StrEnum):
    """Human-visibility outcome recorded after sealed acquisition."""

    SEALED_UNREAD = "sealed_unread"
    DESCRIPTIVE_SUMMARY_EXPOSED = "descriptive_summary_exposed"


class DevelopmentValidationSplit(Gate3Contract):
    """Past-data selection windows used before a prospective holdout."""

    development: PartitionWindow
    validation: PartitionWindow
    purge_seconds: int = Field(ge=1)
    embargo_seconds: int = Field(ge=1)
    max_feature_dependency_seconds: int = Field(ge=1)
    label_dependency_seconds: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_split(self) -> DevelopmentValidationSplit:
        if self.development.partition != DatasetPartition.DEVELOPMENT:
            raise ValueError("development window has the wrong partition identity")
        if self.validation.partition != DatasetPartition.VALIDATION:
            raise ValueError("validation window has the wrong partition identity")
        if self.development.end_at > self.validation.start_at:
            raise ValueError("development and validation windows cannot overlap")
        required = max(
            self.max_feature_dependency_seconds,
            self.label_dependency_seconds,
        )
        if self.purge_seconds < required:
            raise ValueError("purge must cover the largest dependency window")
        if self.embargo_seconds < required:
            raise ValueError("embargo must cover the largest dependency window")
        gap = int((self.validation.start_at - self.development.end_at).total_seconds())
        if gap < self.purge_seconds:
            raise ValueError("development/validation gap is below the purge")
        return self


class ProspectiveHoldoutSpec(Gate3Contract):
    """Exact future coordinates sealed before their first event exists."""

    holdout_id: Identifier
    source: Identifier
    source_version: Version
    instrument_ids: tuple[str, ...] = Field(min_length=1)
    coordinate_plan_sha256: Sha256
    bar_interval_seconds: int = Field(ge=1)
    artifact_interval_seconds: int = Field(ge=1)
    start_at: datetime
    end_at: datetime
    publication_lag_seconds: int = Field(ge=0)
    first_permitted_access_at: datetime
    expected_artifact_count: int = Field(ge=1, le=10_000)
    expected_rows: int = Field(ge=1, le=100_000_000)
    selection_policy: Literal["fixed_future_calendar_window"] = (
        "fixed_future_calendar_window"
    )
    window_semantics: Literal["start_inclusive_end_exclusive"] = (
        "start_inclusive_end_exclusive"
    )
    event_timestamp_semantics: Literal["bar_close"] = "bar_close"
    acquisition_policy: Literal["sealed_automation_no_summary"] = (
        "sealed_automation_no_summary"
    )
    reference_only: Literal[True] = True
    promotion_eligible: Literal[False] = False
    runtime_consumers: Literal[0] = 0
    execution_authority: Literal[False] = False

    @field_validator("start_at", "end_at", "first_permitted_access_at")
    @classmethod
    def validate_timestamps(cls, value: datetime, info) -> datetime:
        return require_utc(value, info.field_name)

    @field_validator("instrument_ids")
    @classmethod
    def validate_instruments(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item for item in value):
            raise ValueError("prospective instrument ids cannot be blank")
        if len(value) != len(set(value)):
            raise ValueError("prospective instrument ids must be unique")
        if value != tuple(sorted(value)):
            raise ValueError("prospective instrument ids must be canonically sorted")
        return value

    @model_validator(mode="after")
    def validate_window(self) -> ProspectiveHoldoutSpec:
        if self.end_at <= self.start_at:
            raise ValueError("prospective holdout end must follow start")
        if self.artifact_interval_seconds % self.bar_interval_seconds:
            raise ValueError("artifact interval must align to the bar interval")
        if any(
            not _aligned_to_interval(value, self.artifact_interval_seconds)
            for value in (self.start_at, self.end_at)
        ):
            raise ValueError("prospective boundaries must align to artifacts")
        duration_seconds = int((self.end_at - self.start_at).total_seconds())
        if duration_seconds % self.artifact_interval_seconds:
            raise ValueError("prospective duration must contain complete artifacts")
        expected_artifacts = (
            duration_seconds
            // self.artifact_interval_seconds
            * len(self.instrument_ids)
        )
        if self.expected_artifact_count != expected_artifacts:
            raise ValueError("prospective artifact count does not match coordinates")
        expected_rows = (
            duration_seconds // self.bar_interval_seconds * len(self.instrument_ids)
        )
        if self.expected_rows != expected_rows:
            raise ValueError("prospective row count does not match coordinates")
        expected_access_at = self.end_at + timedelta(
            seconds=self.publication_lag_seconds
        )
        if self.first_permitted_access_at != expected_access_at:
            raise ValueError("first permitted access must include publication lag")
        return self


class Gate3ProspectivePreregistration(Gate3Contract):
    """Candidate and protocol seal made before a future holdout begins."""

    schema_version: Literal["ctcc.mie.gate3.prospective_preregistration.v1"] = (
        "ctcc.mie.gate3.prospective_preregistration.v1"
    )
    preregistration_id: Identifier
    created_at: datetime
    source_tree_sha256: Sha256
    training_dataset: DatasetIdentity
    bar_construction: BarConstruction
    outcome_label: OutcomeLabelSpec
    candidate: CandidateSpec
    features: tuple[FeatureSpec, ...] = Field(min_length=1)
    selection_split: DevelopmentValidationSplit
    walk_forward_plan_sha256: Sha256
    baselines: tuple[BaselineSpec, ...]
    cost_model: CostModel
    evaluation: EvaluationPlan
    prospective_holdout: ProspectiveHoldoutSpec
    holdout_state: Literal[ProspectiveHoldoutState.SCHEDULED_UNOBSERVED] = (
        ProspectiveHoldoutState.SCHEDULED_UNOBSERVED
    )
    candidate_locked: Literal[True] = True
    single_holdout_evaluation: Literal[True] = True
    human_access_before_evidence_freeze: Literal[False] = False
    current_claim: Literal[Gate3Claim.COMPUTATIONAL] = Gate3Claim.COMPUTATIONAL
    claim_ceiling: Literal[Gate3Claim.PREDICTIVE_OOS] = Gate3Claim.PREDICTIVE_OOS
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
    def validate_preregistration(self) -> Gate3ProspectivePreregistration:
        if self.training_dataset.frozen_at > self.created_at:
            raise ValueError("training dataset must be frozen before preregistration")
        if any(item.frozen_at > self.created_at for item in self.baselines):
            raise ValueError("baselines must be frozen before preregistration")
        if self.created_at >= self.prospective_holdout.start_at:
            raise ValueError("prospective seal must predate the first holdout event")
        if (
            self.training_dataset.instrument_ids
            != self.prospective_holdout.instrument_ids
        ):
            raise ValueError("training and prospective instruments must agree")
        if (
            self.bar_construction.interval_seconds
            != self.prospective_holdout.bar_interval_seconds
        ):
            raise ValueError("bar and prospective holdout intervals must agree")

        max_feature_dependency = max(item.dependency_seconds for item in self.features)
        if (
            self.selection_split.max_feature_dependency_seconds
            != max_feature_dependency
        ):
            raise ValueError("split must record the largest feature dependency")
        if (
            self.selection_split.label_dependency_seconds
            != self.outcome_label.dependency_seconds
        ):
            raise ValueError("split and outcome label dependency must agree")
        if (
            self.selection_split.development.start_at
            < self.training_dataset.first_event_at
        ):
            raise ValueError("development window precedes the training dataset")
        if self.selection_split.validation.end_at > self.training_dataset.last_event_at:
            raise ValueError("validation window exceeds the training dataset")

        required_gap = timedelta(seconds=self.selection_split.embargo_seconds)
        if (
            self.prospective_holdout.start_at - self.selection_split.validation.end_at
            < required_gap
        ):
            raise ValueError("validation/holdout gap is below the embargo")
        if (
            self.prospective_holdout.start_at - self.training_dataset.last_event_at
            < required_gap
        ):
            raise ValueError("training data reaches inside the holdout embargo")

        interval = self.bar_construction.interval_seconds
        aligned_durations = (
            self.outcome_label.horizon_seconds,
            self.outcome_label.dependency_seconds,
            self.selection_split.purge_seconds,
            self.selection_split.embargo_seconds,
            self.cost_model.funding_interval_seconds,
            *(item.dependency_seconds for item in self.features),
        )
        if any(value % interval for value in aligned_durations):
            raise ValueError("Gate 3 durations must align to the bar interval")
        boundaries = (
            self.selection_split.development.start_at,
            self.selection_split.development.end_at,
            self.selection_split.validation.start_at,
            self.selection_split.validation.end_at,
        )
        if any(not _aligned_to_interval(value, interval) for value in boundaries):
            raise ValueError("Gate 3 selection boundaries must align to bars")

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
            raise ValueError("candidate and selected trial configuration disagree")
        return self


class Gate3ProspectiveHoldoutReceipt(Gate3Contract):
    """Post-acquisition record that binds data back to its earlier seal."""

    schema_version: Literal["ctcc.mie.gate3.prospective_holdout_receipt.v1"] = (
        "ctcc.mie.gate3.prospective_holdout_receipt.v1"
    )
    receipt_id: Identifier
    recorded_at: datetime
    preregistration: Gate3ProspectivePreregistration
    preregistration_sha256: Sha256
    first_accessed_at: datetime
    holdout_dataset: DatasetIdentity
    acquisition_plan_sha256: Sha256
    artifact_count: int = Field(ge=1, le=10_000)
    all_artifacts_verified: StrictBool
    access_policy_compliant: StrictBool
    access_outcome: ProspectiveAccessOutcome
    candidate_changed_after_preregistration: StrictBool
    evaluation_started: Literal[False] = False
    strategy_evaluated: Literal[False] = False
    predictive_oos_eligible: StrictBool
    current_claim: Literal[Gate3Claim.COMPUTATIONAL] = Gate3Claim.COMPUTATIONAL
    reference_only: Literal[True] = True
    promotion_eligible: Literal[False] = False
    runtime_consumers: Literal[0] = 0
    execution_authority: Literal[False] = False
    real_order_tested: Literal[False] = False

    @field_validator("recorded_at", "first_accessed_at")
    @classmethod
    def validate_timestamps(cls, value: datetime, info) -> datetime:
        return require_utc(value, info.field_name)

    @model_validator(mode="after")
    def validate_receipt(self) -> Gate3ProspectiveHoldoutReceipt:
        protocol = self.preregistration
        holdout = protocol.prospective_holdout
        dataset = self.holdout_dataset
        if self.preregistration_sha256 != protocol.canonical_sha256():
            raise ValueError("prospective preregistration hash does not match")
        if self.recorded_at < self.first_accessed_at:
            raise ValueError("receipt cannot predate first access")
        if self.recorded_at < dataset.frozen_at:
            raise ValueError("receipt cannot predate dataset freeze")
        if dataset.frozen_at < self.first_accessed_at:
            raise ValueError("dataset cannot be frozen before first access")
        if self.acquisition_plan_sha256 != holdout.coordinate_plan_sha256:
            raise ValueError("acquisition plan does not match the sealed coordinates")

        timing_compliant = self.first_accessed_at >= holdout.first_permitted_access_at
        if self.access_policy_compliant != timing_compliant:
            raise ValueError("access-policy result disagrees with access timing")
        if dataset.source != holdout.source:
            raise ValueError("holdout dataset source does not match the seal")
        if dataset.source_version != holdout.source_version:
            raise ValueError("holdout dataset source version does not match the seal")
        if dataset.instrument_ids != holdout.instrument_ids:
            raise ValueError("holdout dataset instruments do not match the seal")
        if dataset.expected_rows != holdout.expected_rows:
            raise ValueError("holdout dataset row count does not match the seal")
        expected_first_event = holdout.start_at + timedelta(
            seconds=holdout.bar_interval_seconds
        )
        if dataset.first_event_at != expected_first_event:
            raise ValueError("holdout first event does not match the sealed window")
        if dataset.last_event_at != holdout.end_at:
            raise ValueError("holdout last event does not match the sealed window")
        if self.artifact_count != holdout.expected_artifact_count:
            raise ValueError("holdout artifact count does not match the seal")

        sealed_unread = self.access_outcome == ProspectiveAccessOutcome.SEALED_UNREAD
        eligible = (
            timing_compliant
            and self.all_artifacts_verified
            and sealed_unread
            and not self.candidate_changed_after_preregistration
        )
        if self.predictive_oos_eligible != eligible:
            raise ValueError("predictive eligibility disagrees with sealed evidence")
        return self
