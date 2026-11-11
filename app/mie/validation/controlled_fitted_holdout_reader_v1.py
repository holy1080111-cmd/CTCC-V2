"""Open pinned fitted-candidate holdout files after a committed DB reservation."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from app.domain.source_primitives import canonical, sha
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
    Gate3FittedCustodyError,
    _oid,
    _pin,
    _time,
    _verified,
    _xid,
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


@dataclass(frozen=True, slots=True)
class FittedHoldoutReadbackV1:
    binding_sha256: str
    receipt_sha256: str
    reservation_sha256: str
    manifest_sha256: str
    dataset_sha256: str
    current_claim: Literal[Gate3Claim.COMPUTATIONAL] = field(
        default=Gate3Claim.COMPUTATIONAL, init=False
    )
    independent_first_access_verified: Literal[False] = field(default=False, init=False)
    predictive_oos_eligible: Literal[False] = field(default=False, init=False)
    execution_authority: Literal[False] = field(default=False, init=False)


def fitted_holdout_reservation_sha256(
    reservation: FittedEvaluationReservationV1,
) -> str:
    """Digest one observed reservation so custody can retain its pin independently."""
    if (
        type(reservation) is not FittedEvaluationReservationV1
        or not all(
            _pin(value)
            for value in (
                reservation.seal_sha256,
                reservation.binding_sha256,
                reservation.receipt_sha256,
                reservation.dataset_sha256,
                reservation.attempt_sha256,
            )
        )
        or not _oid(reservation.database_oid)
        or not _oid(reservation.evaluator_oid)
        or not _xid(reservation.acknowledgement_xid)
        or not _xid(reservation.reservation_xid)
        or reservation.acknowledgement_xid == reservation.reservation_xid
    ):
        raise ControlledHoldoutReadError("fitted_holdout_reservation_invalid")
    try:
        reserved_at = _time(reservation.reserved_at)
        database_read_at = _time(reservation.database_read_at)
    except Gate3FittedCustodyError:
        raise ControlledHoldoutReadError("fitted_holdout_reservation_invalid") from None
    if database_read_at < reserved_at:
        raise ControlledHoldoutReadError("fitted_holdout_reservation_invalid")
    return sha(
        canonical(
            {
                "schema": "ctcc.mie.fitted_holdout_reservation_readback.v1",
                "seal_sha256": reservation.seal_sha256,
                "binding_sha256": reservation.binding_sha256,
                "receipt_sha256": reservation.receipt_sha256,
                "dataset_sha256": reservation.dataset_sha256,
                "attempt_sha256": reservation.attempt_sha256,
                "database_oid": reservation.database_oid,
                "evaluator_oid": reservation.evaluator_oid,
                "acknowledgement_xid": reservation.acknowledgement_xid,
                "reservation_xid": reservation.reservation_xid,
                "reserved_at": reserved_at.isoformat(),
                "database_read_at": database_read_at.isoformat(),
            }
        )
    )


def _verified_receipt(inputs, receipt_bytes, expected_receipt_sha256):
    if (
        type(inputs) is not FittedCandidateInputsV1
        or type(receipt_bytes) is not bytes
        or not 1 <= len(receipt_bytes) <= _MAX_RECEIPT_BYTES
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
    return receipt


def _require_matching_reservation(
    reservation, inputs, receipt, expected_receipt_sha256
):
    if (
        type(reservation) is not FittedEvaluationReservationV1
        or reservation.seal_sha256 != inputs.expected_preregistration_sha256
        or reservation.binding_sha256 != inputs.expected_binding_sha256
        or reservation.receipt_sha256 != expected_receipt_sha256
        or reservation.dataset_sha256 != receipt.holdout_dataset.content_sha256
        or reservation.reserved_at < receipt.recorded_at
    ):
        raise ControlledHoldoutReadError("fitted_holdout_reservation_mismatch")


def verify_fitted_holdout_readback_v1(
    readback: ControlledFittedHoldoutReadV1,
    *,
    inputs: FittedCandidateInputsV1,
    receipt_bytes: bytes,
    expected_binding_sha256: str,
    expected_receipt_sha256: str,
    expected_reservation_sha256: str,
) -> FittedHoldoutReadbackV1:
    """Recheck already-read bytes against custody pins without accessing paths or DB.

    The binding, acquisition receipt and observed reservation pins must have
    been retained separately from ``readback``. This checks computational
    lineage and byte integrity only.
    """
    if (
        type(readback) is not ControlledFittedHoldoutReadV1
        or type(inputs) is not FittedCandidateInputsV1
        or not _pin(expected_binding_sha256)
        or inputs.expected_binding_sha256 != expected_binding_sha256
        or not _pin(expected_reservation_sha256)
    ):
        raise ControlledHoldoutReadError("fitted_holdout_readback_inputs_invalid")
    receipt = _verified_receipt(inputs, receipt_bytes, expected_receipt_sha256)
    reservation = readback.reservation
    reservation_sha256 = fitted_holdout_reservation_sha256(reservation)
    if reservation_sha256 != expected_reservation_sha256:
        raise ControlledHoldoutReadError("fitted_holdout_reservation_pin_mismatch")
    _require_matching_reservation(reservation, inputs, receipt, expected_receipt_sha256)
    dataset = receipt.holdout_dataset
    for payload, maximum, expected in (
        (readback.manifest_bytes, MAX_MANIFEST_BYTES, dataset.manifest_sha256),
        (readback.dataset_bytes, MAX_DATASET_BYTES, dataset.content_sha256),
    ):
        if (
            type(payload) is not bytes
            or not 1 <= len(payload) <= maximum
            or sha(payload) != expected
        ):
            raise ControlledHoldoutReadError("fitted_holdout_readback_bytes_mismatch")
    _verified(inputs)
    return FittedHoldoutReadbackV1(
        binding_sha256=expected_binding_sha256,
        receipt_sha256=expected_receipt_sha256,
        reservation_sha256=reservation_sha256,
        manifest_sha256=dataset.manifest_sha256,
        dataset_sha256=dataset.content_sha256,
    )


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
        or not isinstance(manifest_path, Path)
        or not manifest_path.is_absolute()
        or not isinstance(dataset_path, Path)
        or not dataset_path.is_absolute()
        or manifest_path == dataset_path
    ):
        raise ControlledHoldoutReadError("fitted_holdout_inputs_invalid")
    receipt = _verified_receipt(inputs, receipt_bytes, expected_receipt_sha256)
    dataset = receipt.holdout_dataset
    reservation = await custody.reserve_evaluation(
        inputs,
        expected_ack_sha256=expected_ack_sha256,
        receipt_sha256=expected_receipt_sha256,
        dataset_sha256=dataset.content_sha256,
    )
    _require_matching_reservation(reservation, inputs, receipt, expected_receipt_sha256)

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
