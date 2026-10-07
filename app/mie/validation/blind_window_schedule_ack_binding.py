"""Bind an observed committed schedule pin to original-byte blind-window replay.

The separate-login 0028 ACK proves the 0026 pin was committed and visible
before the window according to the database server clock. This computational
record does not establish trusted UTC, independent custody, first evaluator
access, predictive validity, or execution authority.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.mie.validation.artifact import (
    ArtifactVerificationError,
    FrozenGate3Artifact,
    _freeze,
    _verify,
)
from app.mie.validation.blind_window_dataset import BlindWindowJournalSegment
from app.mie.validation.blind_window_schedule_binding import (
    ScheduleBoundBlindWindowDatasetV1,
    _rebuild_binding,
)
from app.mie.validation.contracts import (
    Gate3Claim,
    Gate3Contract,
    Identifier,
    Sha256,
    require_utc,
)
from app.mie.validation.gate3_capture_schedule_publication_ack import (
    Gate3CaptureSchedulePublicationAckRepository,
    Gate3SchedulePublicationAckError,
    Gate3SchedulePublicationAckReadback,
)
from app.mie.validation.prospective import Gate3ProspectivePreregistration

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class BlindWindowScheduleAckBindingError(ValueError):
    """Safe failure code for a missing or inconsistent ACK/dataset join."""


class ScheduleBoundBlindWindowDatasetV2(Gate3Contract):
    """Exact-plan join with a server-clock-ordered committed-pin observation."""

    schema_version: Literal["ctcc.mie.gate3.schedule_bound_blind_window.v2"] = (
        "ctcc.mie.gate3.schedule_bound_blind_window.v2"
    )
    preregistration_sha256: Sha256
    capture_schedule_sha256: Sha256
    coordinate_plan_sha256: Sha256
    complete_dataset_sha256: Sha256
    dataset_rows_sha256: Sha256
    holdout_id: Identifier
    window_start_at: datetime
    window_end_at: datetime
    schedule_recorded_at: datetime
    pin_commit_observed_at: datetime
    matched_plan_count: int = Field(ge=1, le=4096)
    pin_commit_visibility_basis: Literal[
        "restricted_ack_login_database_server_clock"
    ] = "restricted_ack_login_database_server_clock"
    row_availability_basis: Literal["durable_journal_readback_unverified_custody"] = (
        "durable_journal_readback_unverified_custody"
    )
    server_clock_ordered_committed_pin_observation: Literal[True] = True
    trusted_clock_verified: Literal[False] = False
    independently_protected: Literal[False] = False
    evaluator_first_read_proven: Literal[False] = False
    current_claim: Literal[Gate3Claim.COMPUTATIONAL] = Gate3Claim.COMPUTATIONAL
    predictive_oos_eligible: Literal[False] = False
    promotion_eligible: Literal[False] = False
    runtime_consumers: Literal[0] = 0
    execution_authority: Literal[False] = False

    @field_validator(
        "window_start_at",
        "window_end_at",
        "schedule_recorded_at",
        "pin_commit_observed_at",
    )
    @classmethod
    def validate_utc(cls, value: datetime, info) -> datetime:
        return require_utc(value, info.field_name)

    @model_validator(mode="after")
    def validate_window(self) -> ScheduleBoundBlindWindowDatasetV2:
        if not (
            self.schedule_recorded_at
            <= self.pin_commit_observed_at
            < self.window_start_at
            < self.window_end_at
        ):
            raise ValueError("schedule ACK lacks a pre-window server observation")
        return self


async def _rebuild_ack_binding(
    *,
    ack_repository: Gate3CaptureSchedulePublicationAckRepository,
    preregistration: Gate3ProspectivePreregistration,
    expected_preregistration_sha256: str,
    expected_schedule_sha256: str,
    dataset_payload: bytes,
    expected_dataset_sha256: str,
    segments: tuple[BlindWindowJournalSegment, ...],
) -> ScheduleBoundBlindWindowDatasetV2:
    if (
        type(ack_repository) is not Gate3CaptureSchedulePublicationAckRepository
        or type(preregistration) is not Gate3ProspectivePreregistration
        or type(expected_preregistration_sha256) is not str
        or _SHA256.fullmatch(expected_preregistration_sha256) is None
        or type(expected_schedule_sha256) is not str
        or _SHA256.fullmatch(expected_schedule_sha256) is None
        or type(expected_dataset_sha256) is not str
        or _SHA256.fullmatch(expected_dataset_sha256) is None
        or type(dataset_payload) is not bytes
        or not dataset_payload
        or type(segments) is not tuple
    ):
        raise BlindWindowScheduleAckBindingError("schedule_ack_binding_inputs_invalid")
    try:
        # Read existing evidence only. A replay must never create a new ACK.
        ack = await ack_repository.read(
            expected_schedule_sha256=expected_schedule_sha256,
            seal=preregistration,
        )
    except Gate3SchedulePublicationAckError:
        raise BlindWindowScheduleAckBindingError("schedule_ack_unavailable") from None
    except Exception:  # noqa: BLE001 - Keep failed readback details out of evidence.
        raise BlindWindowScheduleAckBindingError("schedule_ack_unavailable") from None
    if type(ack) is not Gate3SchedulePublicationAckReadback:
        raise BlindWindowScheduleAckBindingError("schedule_ack_readback_invalid")

    # V1 replays the complete dataset from every original journal byte, then
    # joins every ordinal to the exact 0026 schedule plan and coordinates.
    joined: ScheduleBoundBlindWindowDatasetV1 = await _rebuild_binding(
        repository=ack_repository.pin_repository,
        preregistration=preregistration,
        expected_preregistration_sha256=expected_preregistration_sha256,
        expected_schedule_sha256=expected_schedule_sha256,
        dataset_payload=dataset_payload,
        expected_dataset_sha256=expected_dataset_sha256,
        segments=segments,
    )
    schedule = ack.schedule
    coordinate = schedule.coordinate_plan
    if (
        ack.schedule_sha256 != joined.capture_schedule_sha256
        or schedule.canonical_sha256() != joined.capture_schedule_sha256
        or ack.seal_sha256 != joined.preregistration_sha256
        or schedule.preregistration_sha256 != joined.preregistration_sha256
        or ack.coordinate_plan_sha256 != joined.coordinate_plan_sha256
        or coordinate.canonical_sha256() != joined.coordinate_plan_sha256
        or ack.window_key != coordinate.window_key()
        or ack.holdout_id != joined.holdout_id
        or coordinate.holdout_id != joined.holdout_id
        or coordinate.start_at != joined.window_start_at
        or coordinate.end_at != joined.window_end_at
        or ack.schedule_recorded_at != joined.schedule_recorded_at
        or not schedule.planned_at
        <= ack.schedule_recorded_at
        <= ack.acknowledged_at
        < joined.window_start_at
        or ack.database_readback_at < ack.acknowledged_at
        or len(schedule.plans) != joined.matched_plan_count
        or ack.server_clock_ordered_committed_pin_observation is not True
        or ack.trusted_clock_verified is not False
        or ack.independently_protected is not False
        or ack.predictive_oos_eligible is not False
        or ack.execution_authority is not False
    ):
        raise BlindWindowScheduleAckBindingError(
            "schedule_ack_dataset_identity_mismatch"
        )
    return ScheduleBoundBlindWindowDatasetV2(
        preregistration_sha256=joined.preregistration_sha256,
        capture_schedule_sha256=joined.capture_schedule_sha256,
        coordinate_plan_sha256=joined.coordinate_plan_sha256,
        complete_dataset_sha256=joined.complete_dataset_sha256,
        dataset_rows_sha256=joined.dataset_rows_sha256,
        holdout_id=joined.holdout_id,
        window_start_at=joined.window_start_at,
        window_end_at=joined.window_end_at,
        schedule_recorded_at=joined.schedule_recorded_at,
        pin_commit_observed_at=ack.acknowledged_at,
        matched_plan_count=joined.matched_plan_count,
    )


async def freeze_schedule_bound_blind_window_dataset_v2(
    *,
    ack_repository: Gate3CaptureSchedulePublicationAckRepository,
    preregistration: Gate3ProspectivePreregistration,
    expected_preregistration_sha256: str,
    expected_schedule_sha256: str,
    dataset_payload: bytes,
    expected_dataset_sha256: str,
    segments: tuple[BlindWindowJournalSegment, ...],
) -> FrozenGate3Artifact[ScheduleBoundBlindWindowDatasetV2]:
    """Freeze exact ACK/dataset evidence with no predictive or order authority."""
    binding = await _rebuild_ack_binding(
        ack_repository=ack_repository,
        preregistration=preregistration,
        expected_preregistration_sha256=expected_preregistration_sha256,
        expected_schedule_sha256=expected_schedule_sha256,
        dataset_payload=dataset_payload,
        expected_dataset_sha256=expected_dataset_sha256,
        segments=segments,
    )
    return _freeze(binding, contract_type=ScheduleBoundBlindWindowDatasetV2)


async def verify_schedule_bound_blind_window_dataset_v2(
    payload: bytes,
    *,
    expected_sha256: str,
    ack_repository: Gate3CaptureSchedulePublicationAckRepository,
    preregistration: Gate3ProspectivePreregistration,
    expected_preregistration_sha256: str,
    expected_schedule_sha256: str,
    dataset_payload: bytes,
    expected_dataset_sha256: str,
    segments: tuple[BlindWindowJournalSegment, ...],
) -> ScheduleBoundBlindWindowDatasetV2:
    """Re-read ACK, 0026 pin, and all original source bytes, then compare."""
    contract = _verify(
        payload,
        expected_sha256=expected_sha256,
        contract_type=ScheduleBoundBlindWindowDatasetV2,
    )
    rebuilt = await _rebuild_ack_binding(
        ack_repository=ack_repository,
        preregistration=preregistration,
        expected_preregistration_sha256=expected_preregistration_sha256,
        expected_schedule_sha256=expected_schedule_sha256,
        dataset_payload=dataset_payload,
        expected_dataset_sha256=expected_dataset_sha256,
        segments=segments,
    )
    if contract.canonical_json_bytes() != rebuilt.canonical_json_bytes():
        raise ArtifactVerificationError(
            "schedule ACK binding differs from source replay"
        )
    return rebuilt


__all__ = (
    "BlindWindowScheduleAckBindingError",
    "ScheduleBoundBlindWindowDatasetV2",
    "freeze_schedule_bound_blind_window_dataset_v2",
    "verify_schedule_bound_blind_window_dataset_v2",
)
