from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext

import pytest
from pydantic import ValidationError

from app.mie.validation import (
    BaselineKind,
    BaselineSpec,
    BarConstruction,
    CandidateSpec,
    ComponentVersion,
    CostModel,
    DatasetIdentity,
    DatasetPartition,
    EvaluationPlan,
    FeatureSpec,
    FrozenParameter,
    FrozenTrial,
    Gate3Claim,
    Gate3EvidenceArtifact,
    Gate3Metric,
    Gate3Preregistration,
    MetricEstimate,
    MultipleTestingCorrection,
    OutcomeKind,
    OutcomeLabelSpec,
    PartitionWindow,
    PurgedWalkForwardSplit,
    ReliabilityBin,
    ReplayProvenance,
    ReviewerMetadata,
    TrialRegistry,
    TrialTestResult,
    UncertaintyPlan,
)
from app.mie.validation.artifact import (
    ArtifactVerificationError,
    freeze_evidence_artifact,
    freeze_preregistration,
    verify_evidence_artifact,
    verify_preregistration,
)

D = Decimal
UTC = timezone.utc
START = datetime(2025, 1, 1, tzinfo=UTC)


def sha(character: str) -> str:
    return hashlib.sha256(character.encode("utf-8")).hexdigest()


def valid_split() -> PurgedWalkForwardSplit:
    return PurgedWalkForwardSplit(
        development=PartitionWindow(
            partition=DatasetPartition.DEVELOPMENT,
            start_at=START + timedelta(days=1),
            end_at=START + timedelta(days=30),
        ),
        validation=PartitionWindow(
            partition=DatasetPartition.VALIDATION,
            start_at=START + timedelta(days=32),
            end_at=START + timedelta(days=60),
        ),
        holdout=PartitionWindow(
            partition=DatasetPartition.RETROSPECTIVE_HOLDOUT,
            start_at=START + timedelta(days=62),
            end_at=START + timedelta(days=100),
        ),
        purge_seconds=3600,
        embargo_seconds=3600,
        max_feature_dependency_seconds=3600,
        label_dependency_seconds=1800,
    )


def valid_preregistration() -> Gate3Preregistration:
    created_at = START + timedelta(days=102)
    baselines = tuple(
        BaselineSpec(
            baseline_id=f"baseline:{kind.value}",
            kind=kind,
            version="v1",
            configuration_sha256=sha(kind.value),
            frozen_at=created_at - timedelta(days=1),
        )
        for kind in sorted(BaselineKind, key=lambda item: item.value)
    )
    return Gate3Preregistration(
        preregistration_id="gate3:fixture:v1",
        created_at=created_at,
        source_tree_sha256=sha("source-tree"),
        dataset=DatasetIdentity(
            dataset_id="dataset:fixture:v1",
            source="committed.fixture",
            source_version="v1",
            instrument_ids=("BTC-USDT-SWAP",),
            manifest_sha256=sha("manifest"),
            content_sha256=sha("content"),
            expected_rows=10_000,
            first_event_at=START,
            last_event_at=START + timedelta(days=101),
            frozen_at=created_at - timedelta(days=1),
            event_time_field="closed_at",
            available_time_field="available_at",
        ),
        bar_construction=BarConstruction(interval_seconds=900),
        outcome_label=OutcomeLabelSpec(
            label_id="forward:direction:15m",
            kind=OutcomeKind.FORWARD_RETURN_DIRECTION,
            horizon_seconds=900,
            dependency_seconds=1800,
            positive_threshold=D("0"),
        ),
        candidate=CandidateSpec(
            candidate_id="candidate:primary",
            selected_trial_id="trial:001",
            model_version="v1",
            configuration_sha256=sha("trial-001"),
            source_sha256=sha("candidate-source"),
        ),
        features=(
            FeatureSpec(
                feature_id="mie:gate2:all",
                feature_version="v1",
                dependency_seconds=3600,
                parameters=(
                    FrozenParameter(name="alpha", value=D("0.2500")),
                    FrozenParameter(name="window", value=21),
                ),
            ),
        ),
        split=valid_split(),
        walk_forward_plan_sha256=sha("walk-forward-plan"),
        baselines=baselines,
        cost_model=CostModel(
            model_id="cost:model:v1",
            version="v1",
            fee_bps=D("1"),
            funding_bps=D("0.5"),
            spread_bps=D("0.75"),
            slippage_bps=D("1.25"),
            funding_interval_seconds=900,
        ),
        evaluation=EvaluationPlan(
            metrics=tuple(sorted(Gate3Metric, key=lambda item: item.value)),
            reliability_bin_count=2,
            cvar_confidence_level=D("0.95"),
            uncertainty=UncertaintyPlan(
                confidence_level=D("0.95"),
                resamples=1_000,
                block_length=5,
                seed=20260901,
                familywise_alpha=D("0.05"),
                multiple_testing_correction=(
                    MultipleTestingCorrection.HOLM_BONFERRONI
                ),
            ),
            trials=TrialRegistry(
                registry_id="trials:fixture:v1",
                trials=(
                    FrozenTrial(
                        trial_id="trial:001",
                        configuration_sha256=sha("trial-001"),
                    ),
                    FrozenTrial(
                        trial_id="trial:002",
                        configuration_sha256=sha("trial-002"),
                    ),
                ),
                declared_trial_count=2,
                selection_metric=Gate3Metric.BRIER_SCORE,
            ),
        ),
    )


def valid_artifact() -> Gate3EvidenceArtifact:
    preregistration = valid_preregistration()
    generated_at = preregistration.created_at + timedelta(days=2)
    probability_metrics = {
        Gate3Metric.BRIER_SCORE,
        Gate3Metric.EXPECTED_CALIBRATION_ERROR,
        Gate3Metric.LOG_LOSS,
        Gate3Metric.SAMPLE_COUNT,
    }
    candidate_metrics = probability_metrics | {
        Gate3Metric.CVAR,
        Gate3Metric.FEES,
        Gate3Metric.FUNDING,
        Gate3Metric.MAX_DRAWDOWN,
        Gate3Metric.SLIPPAGE,
        Gate3Metric.SPREAD,
        Gate3Metric.TURNOVER,
    }
    subject_metrics = {
        preregistration.candidate.candidate_id: candidate_metrics,
        **{
            item.baseline_id: probability_metrics
            for item in preregistration.baselines
        },
    }
    metrics = tuple(
        MetricEstimate(
            metric=metric,
            subject_id=subject_id,
            partition=DatasetPartition.RETROSPECTIVE_HOLDOUT,
            value=(
                D("1000")
                if metric == Gate3Metric.SAMPLE_COUNT
                else D("0.05")
                if metric == Gate3Metric.EXPECTED_CALIBRATION_ERROR
                else D("0.1")
            ),
            sample_count=1_000,
            confidence_lower=(
                None if metric == Gate3Metric.SAMPLE_COUNT else D("0.05")
            ),
            confidence_upper=(
                None if metric == Gate3Metric.SAMPLE_COUNT else D("0.15")
            ),
            cost_inclusive=True,
        )
        for subject_id in sorted(subject_metrics)
        for metric in sorted(subject_metrics[subject_id], key=lambda item: item.value)
    )
    reliability = tuple(
        ReliabilityBin(
            subject_id=subject_id,
            partition=DatasetPartition.RETROSPECTIVE_HOLDOUT,
            index=index,
            lower_bound=(D("0") if index == 0 else D("0.5")),
            upper_bound=(D("0.5") if index == 0 else D("1")),
            mean_prediction=(D("0.25") if index == 0 else D("0.75")),
            observed_frequency=(D("0.2") if index == 0 else D("0.8")),
            sample_count=500,
        )
        for subject_id in sorted(subject_metrics)
        for index in range(2)
    )
    return Gate3EvidenceArtifact(
        artifact_id="gate3:evidence:fixture:v1",
        preregistration=preregistration,
        provenance=ReplayProvenance(
            preregistration_sha256=preregistration.canonical_sha256(),
            dataset_manifest_sha256=preregistration.dataset.manifest_sha256,
            dataset_content_sha256=preregistration.dataset.content_sha256,
            replay_sha256=sha("replay"),
            source_tree_sha256=preregistration.source_tree_sha256,
            component_versions=(
                ComponentVersion(
                    component_id="mie:gate3:replay",
                    version="v1",
                    source_sha256=sha("component"),
                ),
            ),
            preregistered_at=preregistration.created_at,
            holdout_first_read_at=preregistration.created_at
            + timedelta(days=1),
            generated_at=generated_at,
        ),
        validation_claim=Gate3Claim.PREDICTIVE_OOS,
        metric_estimates=metrics,
        reliability_bins=reliability,
        trial_results=(
            TrialTestResult(
                trial_id="trial:001",
                raw_p_value=D("0.01"),
                adjusted_p_value=D("0.02"),
                rejected=True,
            ),
            TrialTestResult(
                trial_id="trial:002",
                raw_p_value=D("0.08"),
                adjusted_p_value=D("0.08"),
                rejected=False,
            ),
        ),
        executed_trial_count=2,
        costs_applied=True,
        reviewer=ReviewerMetadata(
            reviewer_id="reviewer:independent",
            reviewed_at=generated_at + timedelta(days=1),
            independent=True,
            leakage_review_passed=True,
            trial_accounting_review_passed=True,
            uncertainty_review_passed=True,
            cost_review_passed=True,
        ),
    )


def test_preregistration_is_canonical_immutable_and_zero_authority() -> None:
    preregistration = valid_preregistration()
    parsed = json.loads(preregistration.canonical_json())

    assert parsed["created_at"].endswith(".000000Z")
    assert parsed["features"][0]["parameters"][0]["value"] == "0.25"
    assert preregistration.canonical_sha256() == hashlib.sha256(
        preregistration.canonical_json_bytes()
    ).hexdigest()
    assert preregistration.canonical_json() == valid_preregistration().canonical_json()
    assert preregistration.holdout_state == "unread"
    assert preregistration.authority == "offline_shadow_only"
    assert preregistration.runtime_consumers == 0
    assert preregistration.execution_authority is False

    with pytest.raises(ValidationError, match="frozen"):
        preregistration.runtime_consumers = 1  # type: ignore[misc]

    payload = preregistration.model_dump()
    payload["order_id"] = "forbidden"
    with pytest.raises(ValidationError, match="Extra inputs"):
        Gate3Preregistration.model_validate(payload)


def test_canonical_decimal_hash_is_exact_across_ambient_contexts() -> None:
    value = D("123456789012345678901234567890.123450000")
    parameter = FrozenParameter(name="exact:decimal", value=value)

    with localcontext() as context:
        context.prec = 9
        low_precision = parameter.canonical_json()
        low_hash = parameter.canonical_sha256()
    with localcontext() as context:
        context.prec = 80
        high_precision = parameter.canonical_json()
        high_hash = parameter.canonical_sha256()

    assert low_precision == high_precision
    assert low_hash == high_hash
    assert json.loads(low_precision)["value"] == (
        "123456789012345678901234567890.12345"
    )


def test_gate3_contracts_do_not_coerce_security_or_numeric_fields() -> None:
    with pytest.raises(ValidationError):
        FrozenParameter(name="float:parameter", value=0.25)

    reviewer = valid_artifact().reviewer.model_dump()
    reviewer["independent"] = "true"
    with pytest.raises(ValidationError):
        ReviewerMetadata.model_validate(reviewer)


def test_split_requires_dependency_coverage_and_real_time_gaps() -> None:
    split = valid_split()
    payload = split.model_dump()
    payload["purge_seconds"] = 3599
    with pytest.raises(ValidationError, match="largest dependency"):
        PurgedWalkForwardSplit.model_validate(payload)

    payload = split.model_dump()
    payload["validation"]["start_at"] = split.development.end_at + timedelta(
        seconds=3599
    )
    with pytest.raises(ValidationError, match="below the purge"):
        PurgedWalkForwardSplit.model_validate(payload)

    payload = split.model_dump()
    payload["holdout"]["start_at"] = split.validation.end_at + timedelta(
        seconds=3599
    )
    with pytest.raises(ValidationError, match="below the embargo"):
        PurgedWalkForwardSplit.model_validate(payload)

    preregistration = valid_preregistration().model_dump()
    preregistration["candidate"]["configuration_sha256"] = sha("unrelated")
    with pytest.raises(ValidationError, match="configuration disagree"):
        Gate3Preregistration.model_validate(preregistration)


def test_contracts_reject_non_utc_nonfinite_and_incomplete_protocols() -> None:
    dataset = valid_preregistration().dataset.model_dump()
    dataset["first_event_at"] = datetime(2025, 1, 1)
    with pytest.raises(ValidationError, match="timezone-aware UTC"):
        DatasetIdentity.model_validate(dataset)

    cost = valid_preregistration().cost_model.model_dump()
    cost["slippage_bps"] = D("NaN")
    with pytest.raises(ValidationError):
        CostModel.model_validate(cost)

    plan = valid_preregistration().evaluation.model_dump()
    plan["metrics"] = plan["metrics"][:-1]
    with pytest.raises(ValidationError, match="every Gate 3 metric"):
        EvaluationPlan.model_validate(plan)

    preregistration = valid_preregistration().model_dump()
    preregistration["baselines"] = preregistration["baselines"][:-1]
    with pytest.raises(ValidationError, match="required Gate 3 baselines"):
        Gate3Preregistration.model_validate(preregistration)

    preregistration = valid_preregistration().model_dump()
    preregistration["split"]["development"]["start_at"] += timedelta(
        microseconds=1
    )
    with pytest.raises(ValidationError, match="align to bars"):
        Gate3Preregistration.model_validate(preregistration)


def test_reliability_contract_represents_empty_bins_without_fake_values() -> None:
    empty = ReliabilityBin(
        subject_id="candidate:primary",
        partition=DatasetPartition.RETROSPECTIVE_HOLDOUT,
        index=0,
        lower_bound=D("0"),
        upper_bound=D("0.5"),
        sample_count=0,
    )
    assert empty.mean_prediction is None
    assert empty.observed_frequency is None

    with pytest.raises(ValidationError, match="empty reliability"):
        ReliabilityBin(
            subject_id="candidate:primary",
            partition=DatasetPartition.RETROSPECTIVE_HOLDOUT,
            index=0,
            lower_bound=D("0"),
            upper_bound=D("0.5"),
            mean_prediction=D("0.25"),
            observed_frequency=D("0"),
            sample_count=0,
        )

    with pytest.raises(ValidationError, match="require estimates"):
        ReliabilityBin(
            subject_id="candidate:primary",
            partition=DatasetPartition.RETROSPECTIVE_HOLDOUT,
            index=0,
            lower_bound=D("0"),
            upper_bound=D("0.5"),
            sample_count=1,
        )


def test_evidence_artifact_binds_hashes_trials_costs_and_review() -> None:
    artifact = valid_artifact()

    assert artifact.validation_claim == Gate3Claim.PREDICTIVE_OOS
    assert artifact.runtime_consumers == 0
    assert artifact.execution_authority is False
    assert {item.subject_id for item in artifact.metric_estimates} == {
        artifact.preregistration.candidate.candidate_id,
        *(item.baseline_id for item in artifact.preregistration.baselines),
    }

    payload = artifact.model_dump()
    payload["provenance"]["preregistration_sha256"] = sha("wrong")
    with pytest.raises(ValidationError, match="preregistration provenance hash"):
        Gate3EvidenceArtifact.model_validate(payload)

    payload = artifact.model_dump()
    payload["costs_applied"] = False
    with pytest.raises(ValidationError, match="descriptive only"):
        Gate3EvidenceArtifact.model_validate(payload)

    payload = artifact.model_dump()
    payload["reviewer"]["independent"] = False
    with pytest.raises(ValidationError, match="independent review"):
        Gate3EvidenceArtifact.model_validate(payload)

    payload = artifact.model_dump()
    payload["executed_trial_count"] = 1
    with pytest.raises(ValidationError, match="trial count"):
        Gate3EvidenceArtifact.model_validate(payload)

    payload = artifact.model_dump()
    payload["trial_results"][0]["adjusted_p_value"] = D("0.01")
    with pytest.raises(ValidationError, match="Holm correction"):
        Gate3EvidenceArtifact.model_validate(payload)

    payload = artifact.model_dump()
    payload["trial_results"] = payload["trial_results"][:-1]
    with pytest.raises(ValidationError, match="every frozen trial"):
        Gate3EvidenceArtifact.model_validate(payload)

    preregistration_payload = artifact.preregistration.model_dump()
    preregistration_payload["candidate"]["selected_trial_id"] = "trial:002"
    preregistration_payload["candidate"]["configuration_sha256"] = sha(
        "trial-002"
    )
    unselected = Gate3Preregistration.model_validate(preregistration_payload)
    payload = artifact.model_dump()
    payload["preregistration"] = unselected.model_dump()
    payload["provenance"][
        "preregistration_sha256"
    ] = unselected.canonical_sha256()
    with pytest.raises(ValidationError, match="pass frozen trial correction"):
        Gate3EvidenceArtifact.model_validate(payload)

    baseline_id = artifact.preregistration.baselines[0].baseline_id
    payload = artifact.model_dump()
    payload["metric_estimates"] = tuple(
        item
        for item in payload["metric_estimates"]
        if item["subject_id"] != baseline_id
    )
    payload["reliability_bins"] = tuple(
        item
        for item in payload["reliability_bins"]
        if item["subject_id"] != baseline_id
    )
    with pytest.raises(ValidationError, match="missing a declared subject"):
        Gate3EvidenceArtifact.model_validate(payload)

    payload = artifact.model_dump()
    target = next(
        item
        for item in payload["metric_estimates"]
        if item["subject_id"]
        == artifact.preregistration.candidate.candidate_id
        and item["metric"] == Gate3Metric.BRIER_SCORE
    )
    target["confidence_lower"] = None
    target["confidence_upper"] = None
    with pytest.raises(ValidationError, match="confidence intervals"):
        Gate3EvidenceArtifact.model_validate(payload)

    payload = artifact.model_dump()
    payload["reliability_bins"][0]["sample_count"] = 499
    with pytest.raises(ValidationError, match="reliability samples"):
        Gate3EvidenceArtifact.model_validate(payload)

    payload = artifact.model_dump()
    target = next(
        item
        for item in payload["metric_estimates"]
        if item["metric"] == Gate3Metric.EXPECTED_CALIBRATION_ERROR
    )
    target["value"] = D("0.06")
    with pytest.raises(ValidationError, match="calibration error"):
        Gate3EvidenceArtifact.model_validate(payload)


def test_evidence_cannot_exceed_the_gate3_claim_ceiling() -> None:
    artifact = valid_artifact()
    payload = artifact.model_dump()
    payload["validation_claim"] = "economic_oos"
    with pytest.raises(ValidationError):
        Gate3EvidenceArtifact.model_validate(payload)


def test_canonical_preregistration_freeze_and_verification_are_exact() -> None:
    preregistration = valid_preregistration()
    frozen = freeze_preregistration(preregistration)

    assert frozen.payload == preregistration.canonical_json_bytes()
    assert frozen.sha256 == preregistration.canonical_sha256()
    assert verify_preregistration(
        frozen.payload,
        expected_sha256=frozen.sha256,
    ) == preregistration

    with pytest.raises(ArtifactVerificationError, match="SHA256 mismatch"):
        verify_preregistration(
            frozen.payload + b" ",
            expected_sha256=frozen.sha256,
        )

    noncanonical = b" " + frozen.payload
    noncanonical_digest = hashlib.sha256(noncanonical).hexdigest()
    with pytest.raises(ArtifactVerificationError, match="not canonical"):
        verify_preregistration(
            noncanonical,
            expected_sha256=noncanonical_digest,
        )


def test_canonical_evidence_freeze_rejects_tampering_and_wrong_schema() -> None:
    artifact = valid_artifact()
    frozen = freeze_evidence_artifact(artifact)

    assert verify_evidence_artifact(
        frozen.payload,
        expected_sha256=frozen.sha256,
    ) == artifact

    tampered = frozen.payload.replace(b'"runtime_consumers":0', b'"runtime_consumers":1')
    tampered_digest = hashlib.sha256(tampered).hexdigest()
    with pytest.raises(ArtifactVerificationError, match="schema validation"):
        verify_evidence_artifact(
            tampered,
            expected_sha256=tampered_digest,
        )

    preregistration = freeze_preregistration(valid_preregistration())
    with pytest.raises(ArtifactVerificationError, match="schema validation"):
        verify_evidence_artifact(
            preregistration.payload,
            expected_sha256=preregistration.sha256,
        )


def test_freeze_revalidates_model_copy_nested_and_subclass_tampering() -> None:
    artifact = valid_artifact()
    tampered = artifact.model_copy(update={"execution_authority": True})
    with pytest.raises(ArtifactVerificationError, match="revalidation failed"):
        freeze_evidence_artifact(tampered)

    nested = artifact.preregistration.model_copy(
        update={"runtime_consumers": 1}
    )
    tampered = artifact.model_copy(update={"preregistration": nested})
    with pytest.raises(ArtifactVerificationError, match="revalidation failed"):
        freeze_evidence_artifact(tampered)

    constructed_values = {
        name: getattr(artifact, name) for name in type(artifact).model_fields
    }
    constructed_values["execution_authority"] = True
    constructed = Gate3EvidenceArtifact.model_construct(**constructed_values)
    with pytest.raises(ArtifactVerificationError, match="revalidation failed"):
        freeze_evidence_artifact(constructed)

    class UnsafeEvidence(Gate3EvidenceArtifact):
        execution_authority: bool = False
        order_id: str

    unsafe = UnsafeEvidence.model_validate(
        {
            **artifact.model_dump(),
            "execution_authority": True,
            "order_id": "must-not-freeze",
        }
    )
    with pytest.raises(ArtifactVerificationError, match="revalidation failed"):
        freeze_evidence_artifact(unsafe)


def test_trial_correction_validation_is_context_independent() -> None:
    artifact = valid_artifact()
    payload = artifact.model_dump()
    raw = D("0.0123456789012345678901234567890123456789")
    with localcontext() as context:
        context.prec = 50
        adjusted = raw * D("2")
    payload["trial_results"][0]["raw_p_value"] = raw
    payload["trial_results"][0]["adjusted_p_value"] = adjusted

    with localcontext() as context:
        context.prec = 9
        low_precision = Gate3EvidenceArtifact.model_validate(payload)
    with localcontext() as context:
        context.prec = 80
        high_precision = Gate3EvidenceArtifact.model_validate(payload)

    assert low_precision == high_precision
