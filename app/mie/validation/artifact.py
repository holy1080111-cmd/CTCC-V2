"""Canonical freeze and verification helpers for MIE Gate 3 JSON evidence."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Generic, TypeVar

from pydantic import ValidationError

from app.mie.validation.contracts import (
    Gate3Contract,
    Gate3EvidenceArtifact,
    Gate3Preregistration,
)
from app.mie.validation.prospective import (
    Gate3ProspectiveHoldoutReceipt,
    Gate3ProspectivePreregistration,
)

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
        validated = contract_type.model_validate(
            contract.model_dump(mode="python")
        )
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
    if not isinstance(expected_sha256, str) or not SHA256_RE.fullmatch(
        expected_sha256
    ):
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
