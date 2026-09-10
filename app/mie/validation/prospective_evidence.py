"""Computational-only prospective reports bound to both acquisition stages.

These are offline attestations, not independently authenticated acquisition or
row-availability evidence. They cannot promote a predictive or execution claim.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.mie.validation.contracts import (
    CANDIDATE_ESTIMATE_METRICS,
    PROBABILITY_ESTIMATE_METRICS,
    ExclusionSummary,
    Gate3Claim,
    Gate3Contract,
    Gate3EvidenceArtifact,
    Gate3Metric,
    Identifier,
    MetricEstimate,
    ReliabilityBin,
    ReplayProvenance,
    ReviewerMetadata,
    Sha256,
    TrialTestResult,
    _validate_evaluation_results,
    require_utc,
)
from app.mie.validation.prospective import (
    Gate3ProspectiveHoldoutReceipt,
    Gate3ProspectivePreregistration,
)


class ProspectiveEvidencePartition(StrEnum):
    HOLDOUT = "prospective_holdout"


class ProspectiveMetricEstimate(MetricEstimate):
    partition: Literal[ProspectiveEvidencePartition.HOLDOUT] = (
        ProspectiveEvidencePartition.HOLDOUT
    )


class ProspectiveReliabilityBin(ReliabilityBin):
    partition: Literal[ProspectiveEvidencePartition.HOLDOUT] = (
        ProspectiveEvidencePartition.HOLDOUT
    )


class ProspectiveReplayProvenance(ReplayProvenance):
    holdout_receipt_sha256: Sha256
    evaluation_cohort_sha256: Sha256
    evaluation_started_at: datetime

    @field_validator("evaluation_started_at")
    @classmethod
    def validate_evaluation_timestamp(cls, value: datetime) -> datetime:
        return require_utc(value, "evaluation_started_at")

    @model_validator(mode="after")
    def validate_evaluation_timeline(self) -> ProspectiveReplayProvenance:
        if self.evaluation_started_at < self.holdout_first_read_at:
            raise ValueError("evaluation cannot precede the evaluator's first read")
        if self.generated_at < self.evaluation_started_at:
            raise ValueError("report cannot precede evaluation")
        return self


class Gate3ProspectiveEvidenceArtifact(Gate3Contract):
    """A complete common-cohort report, with no predictive eligibility.

    Acquisition access and evaluator access are distinct events. Every source
    row is one potential evaluation opportunity; all omitted rows, including
    feature warmup and label tails, must appear in the common exclusion ledger.
    Cohort/replay hashes are attestations; this schema does not recompute them.
    """

    schema_version: Literal["ctcc.mie.gate3.prospective_evidence.v1"] = (
        "ctcc.mie.gate3.prospective_evidence.v1"
    )
    artifact_id: Identifier
    preregistration: Gate3ProspectivePreregistration
    holdout_receipt: Gate3ProspectiveHoldoutReceipt
    provenance: ProspectiveReplayProvenance
    validation_claim: Literal[Gate3Claim.COMPUTATIONAL] = Gate3Claim.COMPUTATIONAL
    metric_estimates: tuple[ProspectiveMetricEstimate, ...] = Field(min_length=1)
    reliability_bins: tuple[ProspectiveReliabilityBin, ...] = Field(min_length=2)
    exclusions: tuple[ExclusionSummary, ...] = ()
    trial_results: tuple[TrialTestResult, ...] = Field(min_length=1)
    executed_trial_count: int = Field(ge=1)
    costs_applied: bool
    reviewer: ReviewerMetadata
    predictive_oos_eligible: Literal[False] = False
    promotion_eligible: Literal[False] = False
    authority: Literal["offline_shadow_only"] = "offline_shadow_only"
    runtime_consumers: Literal[0] = 0
    execution_authority: Literal[False] = False

    @field_validator("metric_estimates")
    @classmethod
    def validate_metric_order(
        cls, value: tuple[ProspectiveMetricEstimate, ...]
    ) -> tuple[ProspectiveMetricEstimate, ...]:
        Gate3EvidenceArtifact.validate_metric_order(value)
        return value

    @field_validator("reliability_bins")
    @classmethod
    def validate_bin_order(
        cls, value: tuple[ProspectiveReliabilityBin, ...]
    ) -> tuple[ProspectiveReliabilityBin, ...]:
        Gate3EvidenceArtifact.validate_reliability_bins(value)
        return value

    @field_validator("exclusions")
    @classmethod
    def validate_exclusions(
        cls, value: tuple[ExclusionSummary, ...]
    ) -> tuple[ExclusionSummary, ...]:
        return Gate3EvidenceArtifact.validate_exclusions(value)

    @field_validator("trial_results")
    @classmethod
    def validate_trial_order(
        cls, value: tuple[TrialTestResult, ...]
    ) -> tuple[TrialTestResult, ...]:
        return Gate3EvidenceArtifact.validate_trial_result_order(value)

    @model_validator(mode="after")
    def validate_artifact(self) -> Gate3ProspectiveEvidenceArtifact:
        protocol = self.preregistration
        receipt = self.holdout_receipt
        provenance = self.provenance
        if (
            receipt.preregistration.canonical_json_bytes()
            != protocol.canonical_json_bytes()
        ):
            raise ValueError("receipt belongs to a different prospective seal")
        if provenance.preregistration_sha256 != protocol.canonical_sha256():
            raise ValueError("prospective seal provenance hash mismatch")
        if provenance.holdout_receipt_sha256 != receipt.canonical_sha256():
            raise ValueError("holdout receipt provenance hash mismatch")
        if provenance.preregistered_at != protocol.created_at:
            raise ValueError("prospective seal provenance timestamp mismatch")
        if provenance.source_tree_sha256 != protocol.source_tree_sha256:
            raise ValueError("source tree provenance hash mismatch")
        dataset = receipt.holdout_dataset
        if (
            provenance.dataset_manifest_sha256 != dataset.manifest_sha256
            or provenance.dataset_content_sha256 != dataset.content_sha256
        ):
            raise ValueError("holdout dataset provenance hash mismatch")
        if provenance.holdout_first_read_at < receipt.recorded_at:
            raise ValueError(
                "evaluator access must follow the unread acquisition receipt"
            )

        _validate_evaluation_results(
            candidate=protocol.candidate,
            baselines=protocol.baselines,
            evaluation=protocol.evaluation,
            metric_estimates=self.metric_estimates,
            reliability_bins=self.reliability_bins,
            trial_results=self.trial_results,
            executed_trial_count=self.executed_trial_count,
            costs_applied=self.costs_applied,
            reviewer=self.reviewer,
            generated_at=provenance.generated_at,
            validation_claim=self.validation_claim,
            holdout_partition=ProspectiveEvidencePartition.HOLDOUT,
        )

        # Completeness is required even for computational reports; acquisition
        # eligibility never substitutes for measured row-availability evidence.
        subjects = {
            protocol.candidate.candidate_id,
            *(item.baseline_id for item in protocol.baselines),
        }
        if {item.subject_id for item in self.metric_estimates} != subjects:
            raise ValueError("prospective report must cover every frozen subject")
        if {item.subject_id for item in self.reliability_bins} != subjects:
            raise ValueError(
                "prospective report requires every subject's reliability bins"
            )
        for subject_id in sorted(subjects):
            expected_metrics = (
                CANDIDATE_ESTIMATE_METRICS
                if subject_id == protocol.candidate.candidate_id
                else PROBABILITY_ESTIMATE_METRICS
            )
            if {
                item.metric
                for item in self.metric_estimates
                if item.subject_id == subject_id
            } != expected_metrics:
                raise ValueError("prospective subject metrics are incomplete")
        if any(
            item.metric != Gate3Metric.SAMPLE_COUNT
            and (item.confidence_lower is None or item.confidence_upper is None)
            for item in self.metric_estimates
        ):
            raise ValueError("prospective report metrics require confidence intervals")
        if any(
            item.cost_inclusive != self.costs_applied for item in self.metric_estimates
        ):
            raise ValueError("metric cost status disagrees with the report")
        sample_counts = {item.sample_count for item in self.metric_estimates}
        if len(sample_counts) != 1:
            raise ValueError("all prospective subjects must use one common cohort")
        included_rows = next(iter(sample_counts))
        excluded_rows = sum(item.row_count for item in self.exclusions)
        if included_rows + excluded_rows != dataset.expected_rows:
            raise ValueError(
                "prospective sample/exclusion accounting must cover the dataset"
            )
        return self
