from __future__ import annotations

import hashlib
from datetime import timedelta
from decimal import Decimal, localcontext

import pytest
from pydantic import ValidationError
from test_gate3_contracts import valid_artifact as retrospective_fixture
from test_gate3_prospective import sha, valid_receipt

from app.mie.validation import (
    ArtifactVerificationError,
    DatasetPartition,
    Gate3Claim,
    Gate3Metric,
    Gate3ProspectiveEvidenceArtifact,
    Gate3ProspectiveHoldoutReceipt,
    Gate3ProspectivePreregistration,
    ProspectiveAccessOutcome,
    ProspectiveEvidencePartition,
    freeze_prospective_evidence_artifact,
    verify_evidence_artifact,
    verify_prospective_evidence_artifact,
)


def valid_evidence(
    receipt: Gate3ProspectiveHoldoutReceipt | None = None,
) -> Gate3ProspectiveEvidenceArtifact:
    # All identities, dates, source hashes and scores are synthetic fixtures.
    if receipt is None:
        receipt_data = valid_receipt().model_dump()
        receipt_data["preregistration"]["evaluation"]["reliability_bin_count"] = 2
        protocol = Gate3ProspectivePreregistration.model_validate(
            receipt_data["preregistration"]
        )
        receipt_data["preregistration_sha256"] = protocol.canonical_sha256()
        receipt = Gate3ProspectiveHoldoutReceipt.model_validate(receipt_data)
    protocol = receipt.preregistration
    old = retrospective_fixture()
    payload = old.model_dump()
    payload["schema_version"] = "ctcc.mie.gate3.prospective_evidence.v1"
    payload["artifact_id"] = "gate3:prospective:evidence:fixture:v1"
    payload["preregistration"] = protocol.model_dump()
    payload["holdout_receipt"] = receipt.model_dump()
    payload["validation_claim"] = Gate3Claim.COMPUTATIONAL
    payload["provenance"].update(
        preregistration_sha256=protocol.canonical_sha256(),
        holdout_receipt_sha256=receipt.canonical_sha256(),
        dataset_manifest_sha256=receipt.holdout_dataset.manifest_sha256,
        dataset_content_sha256=receipt.holdout_dataset.content_sha256,
        source_tree_sha256=protocol.source_tree_sha256,
        preregistered_at=protocol.created_at,
        holdout_first_read_at=receipt.recorded_at + timedelta(minutes=1),
        evaluation_started_at=receipt.recorded_at + timedelta(minutes=2),
        generated_at=receipt.recorded_at + timedelta(minutes=3),
        evaluation_cohort_sha256=sha("synthetic-common-cohort"),
    )
    payload["reviewer"]["reviewed_at"] = receipt.recorded_at + timedelta(minutes=4)
    for field in ("metric_estimates", "reliability_bins"):
        for item in payload[field]:
            item["partition"] = ProspectiveEvidencePartition.HOLDOUT
            if item["subject_id"] == old.preregistration.candidate.candidate_id:
                item["subject_id"] = protocol.candidate.candidate_id
    payload["exclusions"] = (
        {
            "reason_code": "fixture:omitted:opportunities",
            "row_count": receipt.holdout_dataset.expected_rows - 1_000,
        },
    )
    return Gate3ProspectiveEvidenceArtifact.model_validate(payload)


def pins(artifact: Gate3ProspectiveEvidenceArtifact) -> dict[str, str]:
    return {
        "expected_preregistration_sha256": artifact.preregistration.canonical_sha256(),
        "expected_holdout_receipt_sha256": artifact.holdout_receipt.canonical_sha256(),
    }


def test_prospective_evidence_round_trip_is_computational_and_zero_authority() -> None:
    artifact = valid_evidence()
    frozen = freeze_prospective_evidence_artifact(artifact, **pins(artifact))
    assert (
        verify_prospective_evidence_artifact(
            frozen.payload, expected_sha256=frozen.sha256, **pins(artifact)
        )
        == artifact
    )
    assert frozen.payload == artifact.canonical_json_bytes()
    assert artifact.holdout_receipt.predictive_oos_eligible is True
    assert artifact.predictive_oos_eligible is False
    assert artifact.validation_claim == Gate3Claim.COMPUTATIONAL
    assert artifact.runtime_consumers == 0
    assert artifact.execution_authority is False
    with pytest.raises(ValidationError, match="frozen"):
        artifact.execution_authority = True  # type: ignore[misc]
    with pytest.raises(TypeError):
        freeze_prospective_evidence_artifact(artifact)  # type: ignore[call-arg]


@pytest.mark.parametrize(
    "field",
    [
        "preregistration_sha256",
        "holdout_receipt_sha256",
        "dataset_manifest_sha256",
        "dataset_content_sha256",
        "source_tree_sha256",
    ],
)
def test_provenance_hash_mismatches_are_rejected(field: str) -> None:
    payload = valid_evidence().model_dump()
    payload["provenance"][field] = sha("wrong")
    with pytest.raises(ValidationError, match="hash mismatch"):
        Gate3ProspectiveEvidenceArtifact.model_validate(payload)


def test_training_dataset_cannot_replace_the_holdout_dataset() -> None:
    artifact = valid_evidence()
    payload = artifact.model_dump()
    payload["provenance"]["dataset_manifest_sha256"] = (
        artifact.preregistration.training_dataset.manifest_sha256
    )
    payload["provenance"]["dataset_content_sha256"] = (
        artifact.preregistration.training_dataset.content_sha256
    )
    with pytest.raises(ValidationError, match="holdout dataset"):
        Gate3ProspectiveEvidenceArtifact.model_validate(payload)


def test_separately_valid_receipt_from_another_seal_cannot_be_spliced() -> None:
    artifact = valid_evidence()
    payload = artifact.model_dump()
    changed_protocol = artifact.preregistration.model_dump()
    changed_protocol["cost_model"]["fee_bps"] = Decimal(2)
    other_protocol = Gate3ProspectivePreregistration.model_validate(changed_protocol)
    changed_receipt = artifact.holdout_receipt.model_dump()
    changed_receipt["preregistration"] = other_protocol
    changed_receipt["preregistration_sha256"] = other_protocol.canonical_sha256()
    other_receipt = Gate3ProspectiveHoldoutReceipt.model_validate(changed_receipt)
    payload["holdout_receipt"] = other_receipt
    payload["provenance"]["holdout_receipt_sha256"] = other_receipt.canonical_sha256()
    with pytest.raises(ValidationError, match="different prospective seal"):
        Gate3ProspectiveEvidenceArtifact.model_validate(payload)


@pytest.mark.parametrize("stage", ["seal", "receipt"])
def test_fully_rehashed_replacement_chain_fails_independent_pins(stage: str) -> None:
    original = valid_evidence()
    payload = original.model_dump()
    if stage == "seal":
        payload["preregistration"]["candidate"]["model_version"] = "v2"
        protocol = Gate3ProspectivePreregistration.model_validate(
            payload["preregistration"]
        )
        payload["holdout_receipt"]["preregistration"] = protocol
        payload["holdout_receipt"]["preregistration_sha256"] = (
            protocol.canonical_sha256()
        )
        payload["provenance"]["preregistration_sha256"] = protocol.canonical_sha256()
    else:
        payload["holdout_receipt"]["holdout_dataset"]["content_sha256"] = sha(
            "replacement-content"
        )
        payload["provenance"]["dataset_content_sha256"] = sha("replacement-content")
    receipt = Gate3ProspectiveHoldoutReceipt.model_validate(payload["holdout_receipt"])
    payload["provenance"]["holdout_receipt_sha256"] = receipt.canonical_sha256()
    replacement = Gate3ProspectiveEvidenceArtifact.model_validate(payload)
    with pytest.raises(ArtifactVerificationError, match=f"trusted prospective {stage}"):
        freeze_prospective_evidence_artifact(replacement, **pins(original))
    data = replacement.canonical_json_bytes()
    with pytest.raises(ArtifactVerificationError, match=f"trusted prospective {stage}"):
        verify_prospective_evidence_artifact(
            data, expected_sha256=hashlib.sha256(data).hexdigest(), **pins(original)
        )


@pytest.mark.parametrize(
    "event",
    [
        "seal_time",
        "before_receipt",
        "before_first_read",
        "before_evaluation",
        "before_generation",
    ],
)
def test_acquisition_evaluation_and_review_timelines_are_distinct(event: str) -> None:
    artifact = valid_evidence()
    payload = artifact.model_dump()
    provenance = payload["provenance"]
    if event == "seal_time":
        provenance["preregistered_at"] += timedelta(seconds=1)
    elif event == "before_receipt":
        provenance["holdout_first_read_at"] = (
            artifact.holdout_receipt.recorded_at - timedelta(microseconds=1)
        )
    elif event == "before_first_read":
        provenance["evaluation_started_at"] = provenance[
            "holdout_first_read_at"
        ] - timedelta(microseconds=1)
    elif event == "before_evaluation":
        provenance["generated_at"] = provenance["evaluation_started_at"] - timedelta(
            microseconds=1
        )
    else:
        payload["reviewer"]["reviewed_at"] = provenance["generated_at"] - timedelta(
            microseconds=1
        )
    with pytest.raises(ValidationError):
        Gate3ProspectiveEvidenceArtifact.model_validate(payload)


@pytest.mark.parametrize(
    "field,value",
    [
        ("validation_claim", Gate3Claim.PREDICTIVE_OOS),
        ("predictive_oos_eligible", True),
        ("promotion_eligible", True),
        ("runtime_consumers", 1),
        ("execution_authority", True),
    ],
)
def test_acquisition_eligibility_and_review_cannot_raise_authority(
    field: str, value: object
) -> None:
    payload = valid_evidence().model_dump()
    payload[field] = value
    with pytest.raises(ValidationError):
        Gate3ProspectiveEvidenceArtifact.model_validate(payload)


@pytest.mark.parametrize("reason", ["early", "exposed", "changed", "unverified"])
def test_failed_acquisition_remains_a_computational_audit_record(reason: str) -> None:
    payload = valid_evidence().holdout_receipt.model_dump()
    if reason == "early":
        payload["first_accessed_at"] -= timedelta(hours=1)
        payload["access_policy_compliant"] = False
    elif reason == "exposed":
        payload["access_outcome"] = ProspectiveAccessOutcome.DESCRIPTIVE_SUMMARY_EXPOSED
    elif reason == "changed":
        payload["candidate_changed_after_preregistration"] = True
    else:
        payload["all_artifacts_verified"] = False
    payload["predictive_oos_eligible"] = False
    artifact = valid_evidence(Gate3ProspectiveHoldoutReceipt.model_validate(payload))
    assert artifact.predictive_oos_eligible is False
    assert artifact.validation_claim == Gate3Claim.COMPUTATIONAL


@pytest.mark.parametrize("field", ["metric_estimates", "reliability_bins"])
@pytest.mark.parametrize("partition", list(DatasetPartition))
def test_nonprospective_partitions_are_rejected(
    field: str, partition: DatasetPartition
) -> None:
    payload = valid_evidence().model_dump()
    payload[field][0]["partition"] = partition
    with pytest.raises(ValidationError):
        Gate3ProspectiveEvidenceArtifact.model_validate(payload)


@pytest.mark.parametrize(
    "field", ["metric_estimates", "reliability_bins", "trial_results", "exclusions"]
)
def test_duplicate_report_identities_are_rejected(field: str) -> None:
    payload = valid_evidence().model_dump()
    payload[field] += (payload[field][0],)
    with pytest.raises(ValidationError, match="unique"):
        Gate3ProspectiveEvidenceArtifact.model_validate(payload)


@pytest.mark.parametrize(
    "omission", ["subject", "metric", "bins", "interval", "trial", "exclusions"]
)
def test_incomplete_computational_reports_fail_closed(omission: str) -> None:
    payload = valid_evidence().model_dump()
    subject = payload["preregistration"]["baselines"][0]["baseline_id"]
    if omission == "subject":
        for field in ("metric_estimates", "reliability_bins"):
            payload[field] = tuple(
                item for item in payload[field] if item["subject_id"] != subject
            )
    elif omission == "metric":
        payload["metric_estimates"] = tuple(
            item
            for item in payload["metric_estimates"]
            if not (
                item["subject_id"] == subject
                and item["metric"] == Gate3Metric.BRIER_SCORE
            )
        )
    elif omission == "bins":
        payload["reliability_bins"] = tuple(
            item
            for item in payload["reliability_bins"]
            if item["subject_id"] != subject
        )
    elif omission == "interval":
        payload["metric_estimates"][0]["confidence_lower"] = None
        payload["metric_estimates"][0]["confidence_upper"] = None
    elif omission == "trial":
        payload["trial_results"] = payload["trial_results"][:1]
    else:
        payload["exclusions"] = ()
    with pytest.raises(ValidationError):
        Gate3ProspectiveEvidenceArtifact.model_validate(payload)


def test_subjects_cannot_silently_use_different_holdout_cohorts() -> None:
    payload = valid_evidence().model_dump()
    subject = payload["preregistration"]["baselines"][0]["baseline_id"]
    for item in payload["metric_estimates"]:
        if item["subject_id"] == subject:
            item["sample_count"] = 500
            if item["metric"] == Gate3Metric.SAMPLE_COUNT:
                item["value"] = Decimal(500)
    for item in payload["reliability_bins"]:
        if item["subject_id"] == subject:
            item["sample_count"] = 250
    with pytest.raises(ValidationError, match="common cohort"):
        Gate3ProspectiveEvidenceArtifact.model_validate(payload)


def test_cost_free_reports_are_explicitly_computational() -> None:
    payload = valid_evidence().model_dump()
    payload["costs_applied"] = False
    with pytest.raises(ValidationError, match="cost status"):
        Gate3ProspectiveEvidenceArtifact.model_validate(payload)
    for item in payload["metric_estimates"]:
        item["cost_inclusive"] = False
    artifact = Gate3ProspectiveEvidenceArtifact.model_validate(payload)
    assert artifact.validation_claim == Gate3Claim.COMPUTATIONAL


def test_freeze_revalidates_nested_copy_construct_and_subclass_tampering() -> None:
    artifact = valid_evidence()
    bad_copy = artifact.model_copy(update={"execution_authority": True})
    typed_fields = {
        name: getattr(artifact, name) for name in type(artifact).model_fields
    }
    typed_fields["execution_authority"] = True
    bad_construct = Gate3ProspectiveEvidenceArtifact.model_construct(**typed_fields)
    bad_seal = artifact.preregistration.model_copy(update={"execution_authority": True})
    bad_receipt = artifact.holdout_receipt.model_copy(
        update={"preregistration": bad_seal}
    )
    bad_nested = artifact.model_copy(update={"holdout_receipt": bad_receipt})

    class MisleadingEvidence(Gate3ProspectiveEvidenceArtifact):
        def canonical_json_bytes(self) -> bytes:
            return b'{"execution_authority":true}'

    misleading = MisleadingEvidence.model_validate(artifact.model_dump())
    frozen = freeze_prospective_evidence_artifact(misleading, **pins(artifact))
    assert frozen.payload == artifact.canonical_json_bytes()
    assert type(frozen.contract) is Gate3ProspectiveEvidenceArtifact
    for bad in (bad_copy, bad_construct, bad_nested):
        with pytest.raises(ArtifactVerificationError, match="revalidation"):
            freeze_prospective_evidence_artifact(bad, **pins(artifact))


@pytest.mark.parametrize(
    "source", ["retrospective", "seal", "receipt", "noncanonical", "duplicate_json_key"]
)
def test_verification_rejects_wrong_schema_and_noncanonical_payloads(
    source: str,
) -> None:
    artifact = valid_evidence()
    data = {
        "retrospective": retrospective_fixture().canonical_json_bytes(),
        "seal": artifact.preregistration.canonical_json_bytes(),
        "receipt": artifact.holdout_receipt.canonical_json_bytes(),
        "noncanonical": b" " + artifact.canonical_json_bytes(),
        "duplicate_json_key": b'{"execution_authority":false,'
        + artifact.canonical_json_bytes()[1:],
    }[source]
    with pytest.raises(ArtifactVerificationError):
        verify_prospective_evidence_artifact(
            data, expected_sha256=hashlib.sha256(data).hexdigest(), **pins(artifact)
        )
    with pytest.raises(ArtifactVerificationError):
        verify_evidence_artifact(
            artifact.canonical_json_bytes(), expected_sha256=artifact.canonical_sha256()
        )


@pytest.mark.parametrize(
    "pin", ["expected_preregistration_sha256", "expected_holdout_receipt_sha256"]
)
@pytest.mark.parametrize("value", ["", "not-a-sha", "A" * 64, "0" * 64])
def test_external_pins_are_required_exact_sha256_values(pin: str, value: str) -> None:
    artifact = valid_evidence()
    expected = pins(artifact)
    expected[pin] = value
    with pytest.raises(ArtifactVerificationError):
        freeze_prospective_evidence_artifact(artifact, **expected)


def test_nonterminating_bin_widths_are_independent_of_decimal_context() -> None:
    payload = valid_evidence().model_dump()
    payload["preregistration"]["evaluation"]["reliability_bin_count"] = 3
    protocol = Gate3ProspectivePreregistration.model_validate(
        payload["preregistration"]
    )
    payload["holdout_receipt"]["preregistration"] = protocol
    payload["holdout_receipt"]["preregistration_sha256"] = protocol.canonical_sha256()
    receipt = Gate3ProspectiveHoldoutReceipt.model_validate(payload["holdout_receipt"])
    payload["provenance"]["preregistration_sha256"] = protocol.canonical_sha256()
    payload["provenance"]["holdout_receipt_sha256"] = receipt.canonical_sha256()
    subjects = sorted({item["subject_id"] for item in payload["metric_estimates"]})
    with localcontext() as context:
        context.prec = 50
        payload["reliability_bins"] = tuple(
            {
                "subject_id": subject,
                "partition": ProspectiveEvidencePartition.HOLDOUT,
                "index": index,
                "lower_bound": Decimal(index) / Decimal(3),
                "upper_bound": Decimal(index + 1) / Decimal(3),
                "mean_prediction": Decimal("0.5") if index == 1 else None,
                "observed_frequency": Decimal("0.45") if index == 1 else None,
                "sample_count": 1_000 if index == 1 else 0,
            }
            for subject in subjects
            for index in range(3)
        )
    digests = set()
    for precision in (9, 28, 50, 80):
        with localcontext() as context:
            context.prec = precision
            artifact = Gate3ProspectiveEvidenceArtifact.model_validate(payload)
            frozen = freeze_prospective_evidence_artifact(artifact, **pins(artifact))
            assert (
                verify_prospective_evidence_artifact(
                    frozen.payload, expected_sha256=frozen.sha256, **pins(artifact)
                )
                == artifact
            )
            digests.add(frozen.sha256)
    assert len(digests) == 1
