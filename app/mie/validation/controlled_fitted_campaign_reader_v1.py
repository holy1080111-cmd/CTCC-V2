"""Inert multi-child fitted holdout reader: all commits precede all file reads.

This composes the existing one-seal custody and two-file proof. It is not a
campaign publication, independent time witness, or OOS acceptance authority.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from app.mie.validation.artifact import ArtifactVerificationError
from app.mie.validation.contracts import Gate3Claim
from app.mie.validation.controlled_fitted_holdout_reader_v1 import (
    ControlledFittedHoldoutReadV1,
    _read_pinned_reserved_pair,
    _require_matching_reservation,
    _verified_receipt,
    fitted_holdout_reservation_sha256,
)
from app.mie.validation.controlled_holdout_reader import ControlledHoldoutReadError
from app.mie.validation.gate3_fitted_candidate_custody_v1 import (
    FittedCandidateInputsV1,
    Gate3FittedCandidateCustodyV1,
    _pin,
    _verified,
)
from app.mie.validation.prospective import Gate3ProspectivePreregistration
from app.mie.validation.prospective_campaign_v2 import (
    MAX_CAMPAIGN_CHILDREN,
    ProspectiveCampaignV2,
    verify_prospective_campaign_v2,
)
from app.mie.validation.prospective_capture_schedule import (
    ProspectiveCaptureScheduleV1,
)
from app.mie.validation.replay import FrozenFeatureReplayPlanV2


@dataclass(frozen=True, slots=True, repr=False)
class FittedCampaignChildReadV1:
    custody: Gate3FittedCandidateCustodyV1
    inputs: FittedCandidateInputsV1
    expected_ack_sha256: str
    receipt_bytes: bytes
    expected_receipt_sha256: str
    manifest_path: Path
    dataset_path: Path


@dataclass(frozen=True, slots=True, repr=False)
class ControlledFittedCampaignReadV1:
    campaign_sha256: str
    child_reads: tuple[ControlledFittedHoldoutReadV1, ...]
    reservation_sha256s: tuple[str, ...]
    current_claim: Literal[Gate3Claim.COMPUTATIONAL] = field(
        default=Gate3Claim.COMPUTATIONAL, init=False
    )
    independent_campaign_custody_verified: Literal[False] = field(
        default=False, init=False
    )
    predictive_oos_eligible: Literal[False] = field(default=False, init=False)
    execution_authority: Literal[False] = field(default=False, init=False)


def _lexical_path(path: Path) -> str:
    if not isinstance(path, Path) or not path.is_absolute():
        raise ControlledHoldoutReadError("fitted_campaign_path_invalid")
    return os.path.normcase(os.path.normpath(os.fspath(path)))


def _common_binding_document(binding) -> dict:
    document = binding.model_dump(mode="python")
    document.pop("preregistration_sha256")
    document.pop("holdout_coordinate_sha256")
    return document


async def read_fitted_campaign_once(
    *,
    campaign_bytes: bytes,
    expected_campaign_sha256: str,
    campaign_id: str,
    children: tuple[
        tuple[Gate3ProspectivePreregistration, ProspectiveCaptureScheduleV1], ...
    ],
    feature_plan: FrozenFeatureReplayPlanV2,
    expected_feature_plan_sha256: str,
    minimum_candidate_bars: int,
    requests: tuple[FittedCampaignChildReadV1, ...],
) -> ControlledFittedCampaignReadV1:
    """Reserve every child in canonical order, then open any holdout path.

    A failed or ambiguous child commit burns all earlier committed children.
    There is deliberately no retry, resume, or partial campaign result.
    """
    if (
        type(requests) is not tuple
        or not 1 <= len(requests) <= MAX_CAMPAIGN_CHILDREN
        or type(children) is not tuple
        or len(children) != len(requests)
        or type(campaign_bytes) is not bytes
        or not 1 <= len(campaign_bytes) <= 64 * 1024
    ):
        raise ControlledHoldoutReadError("fitted_campaign_child_count_invalid")
    try:
        campaign = verify_prospective_campaign_v2(
            campaign_bytes,
            expected_sha256=expected_campaign_sha256,
            campaign_id=campaign_id,
            children=children,
            feature_plan=feature_plan,
            expected_feature_plan_sha256=expected_feature_plan_sha256,
            minimum_candidate_bars=minimum_candidate_bars,
        )
    except (ArtifactVerificationError, ValueError, TypeError):
        raise ControlledHoldoutReadError(
            "fitted_campaign_declaration_invalid"
        ) from None
    if type(campaign) is not ProspectiveCampaignV2:
        raise ControlledHoldoutReadError("fitted_campaign_declaration_invalid")

    # No path stat/open occurs in this phase. Verify *all* pure evidence first.
    receipts = []
    bindings = []
    path_names = set()
    evaluator_scope = None
    for request, child, (seal, _) in zip(
        requests, campaign.children, children, strict=True
    ):
        if (
            type(request) is not FittedCampaignChildReadV1
            or type(request.custody) is not Gate3FittedCandidateCustodyV1
            or type(request.inputs) is not FittedCandidateInputsV1
            or not _pin(request.expected_ack_sha256)
            or request.inputs.preregistration != seal
            or request.inputs.expected_preregistration_sha256 != child.seal_sha256
        ):
            raise ControlledHoldoutReadError("fitted_campaign_child_input_invalid")
        request.custody._require_role("evaluator")
        checked = _verified(request.inputs)
        if (
            checked.holdout_coordinate_sha256 != child.coordinate_plan_sha256
            or checked.preregistration_sha256 != child.seal_sha256
        ):
            raise ControlledHoldoutReadError("fitted_campaign_child_binding_invalid")
        receipt = _verified_receipt(
            request.inputs, request.receipt_bytes, request.expected_receipt_sha256
        )
        if receipt.preregistration_sha256 != child.seal_sha256:
            raise ControlledHoldoutReadError("fitted_campaign_child_receipt_invalid")
        names = (
            _lexical_path(request.manifest_path),
            _lexical_path(request.dataset_path),
        )
        if names[0] == names[1] or any(name in path_names for name in names):
            raise ControlledHoldoutReadError("fitted_campaign_path_reused")
        path_names.update(names)
        # All three independently provisioned role OIDs are part of one
        # campaign's custody identity, not just the evaluator's login.
        scope = request.custody._expected
        if evaluator_scope is not None and scope != evaluator_scope:
            raise ControlledHoldoutReadError("fitted_campaign_evaluator_changed")
        evaluator_scope = scope
        receipts.append(receipt)
        bindings.append(checked)
    stable_binding = _common_binding_document(bindings[0])
    if any(_common_binding_document(item) != stable_binding for item in bindings[1:]):
        raise ControlledHoldoutReadError("fitted_campaign_model_lineage_changed")
    # A future acquisition receipt must delay every reservation, not burn an
    # earlier child under the campaign's older nominal access timestamp.
    global_minimum = max(
        campaign.first_permitted_access_at,
        *(receipt.recorded_at for receipt in receipts),
    )

    # Each ACK is a fresh restricted DB read; still no holdout path access.
    for request, child, checked in zip(
        requests, campaign.children, bindings, strict=True
    ):
        acknowledged = await request.custody.read_acknowledged(
            request.inputs, expected_ack_sha256=request.expected_ack_sha256
        )
        if (
            acknowledged.seal_sha256 != child.seal_sha256
            or acknowledged.database_oid != evaluator_scope[4]
            or acknowledged.binding != checked
        ):
            raise ControlledHoldoutReadError("fitted_campaign_ack_changed")

    # The optional SQL WHERE gate refuses before the append function executes
    # when the DB clock is early. Every committed timestamp is checked again.
    reservations = []
    reservation_pins = []
    for request, receipt in zip(requests, receipts, strict=True):
        reservation = await request.custody.reserve_evaluation(
            request.inputs,
            expected_ack_sha256=request.expected_ack_sha256,
            receipt_sha256=request.expected_receipt_sha256,
            dataset_sha256=receipt.holdout_dataset.content_sha256,
            not_before=global_minimum,
        )
        _require_matching_reservation(
            reservation, request.inputs, receipt, request.expected_receipt_sha256
        )
        if (
            reservation.reserved_at < global_minimum
            or reservation.database_oid != evaluator_scope[4]
            or reservation.evaluator_oid != evaluator_scope[8]
        ):
            raise ControlledHoldoutReadError("fitted_campaign_reservation_changed")
        reservations.append(reservation)
        reservation_pins.append(fitted_holdout_reservation_sha256(reservation))

    # All commits and independent DB readbacks are complete before this point.
    reads = []
    file_identities = set()
    for request, reservation in zip(requests, reservations, strict=True):
        readback, manifest_identity, dataset_identity = _read_pinned_reserved_pair(
            reservation=reservation,
            inputs=request.inputs,
            receipt_bytes=request.receipt_bytes,
            expected_receipt_sha256=request.expected_receipt_sha256,
            manifest_path=request.manifest_path,
            dataset_path=request.dataset_path,
        )
        if any(
            identity in file_identities
            for identity in (manifest_identity, dataset_identity)
        ):
            raise ControlledHoldoutReadError("fitted_campaign_file_identity_reused")
        file_identities.update((manifest_identity, dataset_identity))
        reads.append(readback)
    return ControlledFittedCampaignReadV1(
        campaign_sha256=expected_campaign_sha256,
        child_reads=tuple(reads),
        reservation_sha256s=tuple(reservation_pins),
    )
