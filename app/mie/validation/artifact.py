"""Canonical freeze and verification helpers for MIE Gate 3 JSON evidence."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Generic, TypeVar

from pydantic import ValidationError

from app.mie.validation.archive_batch import (
    ArchiveBatchManifest,
    ArchiveBatchPlan,
    validate_archive_batch_source,
)
from app.mie.validation.archive_replay import (
    ArchiveReplayDataset,
    validate_archive_replay_source,
)
from app.mie.validation.availability import ArchiveObservationReceipt
from app.mie.validation.contracts import (
    Gate3Contract,
    Gate3EvidenceArtifact,
    Gate3Preregistration,
)
from app.mie.validation.prospective import (
    Gate3ProspectiveHoldoutReceipt,
    Gate3ProspectivePreregistration,
)
from app.mie.validation.prospective_evidence import Gate3ProspectiveEvidenceArtifact

TGate3 = TypeVar("TGate3", bound=Gate3Contract)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class ArtifactVerificationError(ValueError):
    """Raised when bytes, digest, schema, or canonical form cannot be verified."""


@dataclass(frozen=True, slots=True)
class FrozenGate3Artifact(Generic[TGate3]):
    payload: bytes
    sha256: str
    contract: TGate3


def _freeze(
    contract: TGate3,
    *,
    contract_type: type[TGate3],
) -> FrozenGate3Artifact[TGate3]:
    try:
        validated = contract_type.model_validate(contract.model_dump(mode="python"))
    except (ValidationError, ValueError) as exc:
        raise ArtifactVerificationError(
            "artifact contract revalidation failed"
        ) from exc
    payload = validated.canonical_json_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    return FrozenGate3Artifact(
        payload=payload,
        sha256=digest,
        contract=validated,
    )


def freeze_preregistration(
    preregistration: Gate3Preregistration,
) -> FrozenGate3Artifact[Gate3Preregistration]:
    """Freeze preregistration bytes before any retrospective holdout read."""

    return _freeze(
        preregistration,
        contract_type=Gate3Preregistration,
    )


def freeze_archive_observation_receipt(
    receipt: ArchiveObservationReceipt,
) -> FrozenGate3Artifact[ArchiveObservationReceipt]:
    """Freeze an offline observation attestation, not historical receipt proof."""

    return _freeze(receipt, contract_type=ArchiveObservationReceipt)


def freeze_archive_replay_dataset(
    dataset: ArchiveReplayDataset,
    *,
    archive_bytes: bytes,
) -> FrozenGate3Artifact[ArchiveReplayDataset]:
    """Freeze a computational-only archive rehearsal with row provenance."""

    try:
        validated = validate_archive_replay_source(dataset, archive_bytes=archive_bytes)
    except ValueError as exc:
        raise ArtifactVerificationError("archive source revalidation failed") from exc
    return _freeze(validated, contract_type=ArchiveReplayDataset)


def freeze_evidence_artifact(
    artifact: Gate3EvidenceArtifact,
) -> FrozenGate3Artifact[Gate3EvidenceArtifact]:
    """Freeze one self-contained, shadow-only evidence artifact."""

    return _freeze(
        artifact,
        contract_type=Gate3EvidenceArtifact,
    )


def freeze_prospective_preregistration(
    preregistration: Gate3ProspectivePreregistration,
) -> FrozenGate3Artifact[Gate3ProspectivePreregistration]:
    """Freeze candidate/protocol bytes before a future holdout begins."""

    return _freeze(
        preregistration,
        contract_type=Gate3ProspectivePreregistration,
    )


def freeze_prospective_holdout_receipt(
    receipt: Gate3ProspectiveHoldoutReceipt,
) -> FrozenGate3Artifact[Gate3ProspectiveHoldoutReceipt]:
    """Freeze a post-acquisition receipt without evaluating the holdout."""

    return _freeze(
        receipt,
        contract_type=Gate3ProspectiveHoldoutReceipt,
    )


def _verify(
    payload: bytes,
    *,
    expected_sha256: str,
    contract_type: type[TGate3],
) -> TGate3:
    if not isinstance(payload, bytes) or not payload:
        raise ArtifactVerificationError("artifact payload must be non-empty bytes")
    if not isinstance(expected_sha256, str) or not SHA256_RE.fullmatch(expected_sha256):
        raise ArtifactVerificationError("expected artifact SHA256 is invalid")
    actual_sha256 = hashlib.sha256(payload).hexdigest()
    if actual_sha256 != expected_sha256:
        raise ArtifactVerificationError("artifact SHA256 mismatch")
    try:
        contract = contract_type.model_validate_json(payload)
    except (ValidationError, ValueError) as exc:
        raise ArtifactVerificationError("artifact schema validation failed") from exc
    if contract.canonical_json_bytes() != payload:
        raise ArtifactVerificationError("artifact JSON is not canonical")
    return contract


def verify_preregistration(
    payload: bytes,
    *,
    expected_sha256: str,
) -> Gate3Preregistration:
    return _verify(
        payload,
        expected_sha256=expected_sha256,
        contract_type=Gate3Preregistration,
    )


def verify_archive_observation_receipt(
    payload: bytes,
    *,
    expected_sha256: str,
) -> ArchiveObservationReceipt:
    return _verify(
        payload,
        expected_sha256=expected_sha256,
        contract_type=ArchiveObservationReceipt,
    )


def verify_archive_replay_dataset(
    payload: bytes,
    *,
    expected_sha256: str,
    archive_bytes: bytes,
) -> ArchiveReplayDataset:
    contract = _verify(
        payload,
        expected_sha256=expected_sha256,
        contract_type=ArchiveReplayDataset,
    )
    try:
        return validate_archive_replay_source(contract, archive_bytes=archive_bytes)
    except ValueError as exc:
        raise ArtifactVerificationError("archive source revalidation failed") from exc


def verify_evidence_artifact(
    payload: bytes,
    *,
    expected_sha256: str,
) -> Gate3EvidenceArtifact:
    return _verify(
        payload,
        expected_sha256=expected_sha256,
        contract_type=Gate3EvidenceArtifact,
    )


def verify_prospective_preregistration(
    payload: bytes,
    *,
    expected_sha256: str,
) -> Gate3ProspectivePreregistration:
    return _verify(
        payload,
        expected_sha256=expected_sha256,
        contract_type=Gate3ProspectivePreregistration,
    )


def verify_prospective_holdout_receipt(
    payload: bytes,
    *,
    expected_sha256: str,
) -> Gate3ProspectiveHoldoutReceipt:
    return _verify(
        payload,
        expected_sha256=expected_sha256,
        contract_type=Gate3ProspectiveHoldoutReceipt,
    )


def _verify_prospective_evidence_pins(
    artifact: Gate3ProspectiveEvidenceArtifact,
    *,
    expected_preregistration_sha256: str,
    expected_holdout_receipt_sha256: str,
) -> None:
    for label, expected, actual in (
        (
            "seal",
            expected_preregistration_sha256,
            artifact.preregistration.canonical_sha256(),
        ),
        (
            "receipt",
            expected_holdout_receipt_sha256,
            artifact.holdout_receipt.canonical_sha256(),
        ),
    ):
        if not isinstance(expected, str) or not SHA256_RE.fullmatch(expected):
            raise ArtifactVerificationError(
                f"expected prospective {label} SHA256 is invalid"
            )
        if expected != actual:
            raise ArtifactVerificationError(
                f"trusted prospective {label} SHA256 mismatch"
            )


def freeze_prospective_evidence_artifact(
    artifact: Gate3ProspectiveEvidenceArtifact,
    *,
    expected_preregistration_sha256: str,
    expected_holdout_receipt_sha256: str,
) -> FrozenGate3Artifact[Gate3ProspectiveEvidenceArtifact]:
    """Freeze a report against independently retained seal and receipt digests."""

    frozen = _freeze(artifact, contract_type=Gate3ProspectiveEvidenceArtifact)
    _verify_prospective_evidence_pins(
        frozen.contract,
        expected_preregistration_sha256=expected_preregistration_sha256,
        expected_holdout_receipt_sha256=expected_holdout_receipt_sha256,
    )
    return frozen


def verify_prospective_evidence_artifact(
    payload: bytes,
    *,
    expected_sha256: str,
    expected_preregistration_sha256: str,
    expected_holdout_receipt_sha256: str,
) -> Gate3ProspectiveEvidenceArtifact:
    """Verify bytes and both prior-stage pins; embedded self-hashes are insufficient."""

    artifact = _verify(
        payload,
        expected_sha256=expected_sha256,
        contract_type=Gate3ProspectiveEvidenceArtifact,
    )
    _verify_prospective_evidence_pins(
        artifact,
        expected_preregistration_sha256=expected_preregistration_sha256,
        expected_holdout_receipt_sha256=expected_holdout_receipt_sha256,
    )
    return artifact


def freeze_archive_batch_plan(
    plan: ArchiveBatchPlan,
) -> FrozenGate3Artifact[ArchiveBatchPlan]:
    """Freeze calendar choices; retain the returned digest independently."""

    return _freeze(plan, contract_type=ArchiveBatchPlan)


def verify_archive_batch_plan(
    payload: bytes,
    *,
    expected_sha256: str,
) -> ArchiveBatchPlan:
    return _verify(
        payload, expected_sha256=expected_sha256, contract_type=ArchiveBatchPlan
    )


def freeze_archive_batch_manifest(
    manifest: ArchiveBatchManifest,
    *,
    archive_bytes: tuple[bytes, ...],
    expected_plan_sha256: str,
) -> FrozenGate3Artifact[ArchiveBatchManifest]:
    """Freeze only after every original archive agrees with the pinned plan."""

    try:
        validated = validate_archive_batch_source(
            manifest,
            archive_bytes=archive_bytes,
            expected_plan_sha256=expected_plan_sha256,
        )
    except ValueError as exc:
        raise ArtifactVerificationError(
            "batch original source revalidation failed"
        ) from exc
    return _freeze(validated, contract_type=ArchiveBatchManifest)


def verify_archive_batch_manifest(
    payload: bytes,
    *,
    expected_sha256: str,
    archive_bytes: tuple[bytes, ...],
    expected_plan_sha256: str,
) -> ArchiveBatchManifest:
    manifest = _verify(
        payload,
        expected_sha256=expected_sha256,
        contract_type=ArchiveBatchManifest,
    )
    try:
        return validate_archive_batch_source(
            manifest,
            archive_bytes=archive_bytes,
            expected_plan_sha256=expected_plan_sha256,
        )
    except ValueError as exc:
        raise ArtifactVerificationError(
            "batch original source revalidation failed"
        ) from exc
