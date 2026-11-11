"""Open pinned fitted-candidate holdout files after a committed DB reservation."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from app.mie.validation.artifact import verify_prospective_holdout_receipt
from app.mie.validation.contracts import Gate3Claim
from app.mie.validation.controlled_holdout_reader import (
    MAX_DATASET_BYTES,
    MAX_MANIFEST_BYTES,
    ControlledHoldoutReadError,
    _read_pinned_file,
)
from app.mie.validation.gate3_fitted_candidate_custody_v1 import (
    FittedCandidateInputsV1,
    FittedEvaluationReservationV1,
    Gate3FittedCandidateCustodyV1,
)
from app.mie.validation.prospective import ProspectiveAccessOutcome

_MAX_RECEIPT_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class ControlledFittedHoldoutReadV1:
    reservation: FittedEvaluationReservationV1
    manifest_bytes: bytes
    dataset_bytes: bytes
    current_claim: Literal[Gate3Claim.COMPUTATIONAL] = field(
        default=Gate3Claim.COMPUTATIONAL, init=False
    )
    independent_first_access_verified: Literal[False] = field(default=False, init=False)
    predictive_oos_eligible: Literal[False] = field(default=False, init=False)
    execution_authority: Literal[False] = field(default=False, init=False)


async def read_fitted_prospective_holdout_once(
    *,
    custody: Gate3FittedCandidateCustodyV1,
    inputs: FittedCandidateInputsV1,
    expected_ack_sha256: str,
    receipt_bytes: bytes,
    expected_receipt_sha256: str,
    manifest_path: Path,
    dataset_path: Path,
) -> ControlledFittedHoldoutReadV1:
    """Consume the cross-process slot before either holdout path is touched."""
    if (
        type(custody) is not Gate3FittedCandidateCustodyV1
        or type(inputs) is not FittedCandidateInputsV1
        or type(receipt_bytes) is not bytes
        or not 1 <= len(receipt_bytes) <= _MAX_RECEIPT_BYTES
        or not isinstance(manifest_path, Path)
        or not manifest_path.is_absolute()
        or not isinstance(dataset_path, Path)
        or not dataset_path.is_absolute()
        or manifest_path == dataset_path
    ):
        raise ControlledHoldoutReadError("fitted_holdout_inputs_invalid")
    receipt = verify_prospective_holdout_receipt(
        receipt_bytes, expected_sha256=expected_receipt_sha256
    )
    if (
        receipt.preregistration_sha256 != inputs.expected_preregistration_sha256
        or receipt.preregistration != inputs.preregistration
        or not receipt.predictive_oos_eligible
        or not receipt.all_artifacts_verified
        or not receipt.access_policy_compliant
        or receipt.access_outcome is not ProspectiveAccessOutcome.SEALED_UNREAD
        or receipt.candidate_changed_after_preregistration
    ):
        raise ControlledHoldoutReadError("fitted_holdout_receipt_ineligible")

    dataset = receipt.holdout_dataset
    reservation = await custody.reserve_evaluation(
        inputs,
        expected_ack_sha256=expected_ack_sha256,
        receipt_sha256=expected_receipt_sha256,
        dataset_sha256=dataset.content_sha256,
    )
    if (
        type(reservation) is not FittedEvaluationReservationV1
        or reservation.seal_sha256 != inputs.expected_preregistration_sha256
        or reservation.binding_sha256 != inputs.binding.sha256
        or reservation.receipt_sha256 != expected_receipt_sha256
        or reservation.dataset_sha256 != dataset.content_sha256
        or reservation.reserved_at < receipt.recorded_at
    ):
        raise ControlledHoldoutReadError("fitted_holdout_reservation_mismatch")

    try:
        if os.path.samefile(manifest_path, dataset_path):
            raise ControlledHoldoutReadError("fitted_holdout_paths_match")
    except OSError:
        raise ControlledHoldoutReadError(
            "fitted_holdout_path_identity_unavailable"
        ) from None
    manifest, manifest_identity = _read_pinned_file(
        manifest_path, dataset.manifest_sha256, MAX_MANIFEST_BYTES
    )
    content, dataset_identity = _read_pinned_file(
        dataset_path, dataset.content_sha256, MAX_DATASET_BYTES
    )
    if manifest_identity == dataset_identity:
        raise ControlledHoldoutReadError("fitted_holdout_file_identity_matches")
    return ControlledFittedHoldoutReadV1(reservation, manifest, content)
