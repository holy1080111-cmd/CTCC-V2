"""Join a schedule insert-guard timestamp to original-byte window replay.

This is a computational audit of preplanning and measured local availability.
The row's recorded time says the insert guard ran before the window, not that
the transaction committed then. A later readback proves only an eventual
committed row. Neither proves independent custody or a first evaluator read.
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
from app.mie.validation.blind_window_dataset import (
    BlindWindowJournalSegment,
    verify_complete_blind_window_dataset,
)
from app.mie.validation.contracts import (
    Gate3Claim,
    Gate3Contract,
    Identifier,
    Sha256,
    require_utc,
)
from app.mie.validation.gate3_capture_schedule_pin import (
    Gate3CaptureSchedulePinRepository,
    Gate3SchedulePinError,
    Gate3SchedulePinReadback,
)
from app.mie.validation.prospective import Gate3ProspectivePreregistration
from app.public_market_source.public_market_receipts import utc_from_ns

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class BlindWindowScheduleBindingError(ValueError):
    """Safe code for a missing or inconsistent schedule/dataset join."""


class ScheduleBoundBlindWindowDatasetV1(Gate3Contract):
    """Hash-only join to plans checked by a pre-window database insert guard."""

    schema_version: Literal["ctcc.mie.gate3.schedule_bound_blind_window.v1"] = (
        "ctcc.mie.gate3.schedule_bound_blind_window.v1"
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
    matched_plan_count: int = Field(ge=1, le=4096)
    schedule_time_basis: Literal["database_insert_guard_recorded_at"] = (
        "database_insert_guard_recorded_at"
    )
    row_availability_basis: Literal["durable_journal_readback_unverified_custody"] = (
        "durable_journal_readback_unverified_custody"
    )
    independently_protected: Literal[False] = False
    pre_window_commit_proven: Literal[False] = False
    evaluator_first_read_proven: Literal[False] = False
    current_claim: Literal[Gate3Claim.COMPUTATIONAL] = Gate3Claim.COMPUTATIONAL
    predictive_oos_eligible: Literal[False] = False
    promotion_eligible: Literal[False] = False
    runtime_consumers: Literal[0] = 0
    execution_authority: Literal[False] = False

    @field_validator("window_start_at", "window_end_at", "schedule_recorded_at")
    @classmethod
    def validate_utc(cls, value: datetime, info) -> datetime:
        return require_utc(value, info.field_name)

    @model_validator(mode="after")
    def validate_window(self) -> ScheduleBoundBlindWindowDatasetV1:
        if not self.schedule_recorded_at < self.window_start_at < self.window_end_at:
            raise ValueError("schedule binding lacks a pre-window insert timestamp")
        return self


async def _rebuild_binding(
    *,
    repository: Gate3CaptureSchedulePinRepository,
    preregistration: Gate3ProspectivePreregistration,
    expected_preregistration_sha256: str,
    expected_schedule_sha256: str,
    dataset_payload: bytes,
    expected_dataset_sha256: str,
    segments: tuple[BlindWindowJournalSegment, ...],
) -> ScheduleBoundBlindWindowDatasetV1:
    if (
        type(repository) is not Gate3CaptureSchedulePinRepository
        or type(preregistration) is not Gate3ProspectivePreregistration
        or type(expected_preregistration_sha256) is not str
        or _SHA256.fullmatch(expected_preregistration_sha256) is None
        or type(expected_schedule_sha256) is not str
        or _SHA256.fullmatch(expected_schedule_sha256) is None
        or type(expected_dataset_sha256) is not str
        or _SHA256.fullmatch(expected_dataset_sha256) is None
        or type(dataset_payload) is not bytes
        or not dataset_payload
    ):
        raise BlindWindowScheduleBindingError("schedule_binding_inputs_invalid")
    try:
        pin = await repository.read(
            expected_schedule_sha256=expected_schedule_sha256,
            seal=preregistration,
        )
    except Gate3SchedulePinError as exc:
        raise BlindWindowScheduleBindingError("schedule_pin_unavailable") from exc
    if type(pin) is not Gate3SchedulePinReadback:
        raise BlindWindowScheduleBindingError("schedule_pin_readback_invalid")
    try:
        dataset = verify_complete_blind_window_dataset(
            dataset_payload,
            expected_sha256=expected_dataset_sha256,
            preregistration=preregistration,
            expected_preregistration_sha256=expected_preregistration_sha256,
            segments=segments,
        )
    except Exception:  # noqa: BLE001 - Untrusted journal shape; expose safe code only.
        raise BlindWindowScheduleBindingError("blind_dataset_replay_failed") from None
    schedule = pin.schedule
    coordinate = schedule.coordinate_plan
    if (
        pin.schedule_sha256 != expected_schedule_sha256
        or schedule.canonical_sha256() != expected_schedule_sha256
        or pin.seal_sha256 != expected_preregistration_sha256
        or schedule.preregistration_sha256 != expected_preregistration_sha256
        or pin.coordinate_plan_sha256 != dataset.coordinate_plan_sha256
        or coordinate.canonical_sha256() != dataset.coordinate_plan_sha256
        or coordinate.holdout_id != dataset.holdout_id
        or coordinate.start_at != dataset.start_at
        or coordinate.end_at != dataset.end_at
        or coordinate.instrument_ids != dataset.instrument_ids
        or coordinate.expected_rows != dataset.expected_rows
        or len(schedule.plans) != len(dataset.rows)
        or not schedule.planned_at <= pin.recorded_at < coordinate.start_at
        or pin.database_readback_at < pin.recorded_at
    ):
        raise BlindWindowScheduleBindingError("schedule_dataset_identity_mismatch")
    # The dataset verifier has already replayed each original journal plan and
    # checked its hash against each row. Matching the exact schedule plan hash
    # now binds every original-byte capture to bytes checked by the database
    # insert guard before the window. The commit time remains unproven here.
    for ordinal, (plan, row) in enumerate(
        zip(schedule.plans, dataset.rows, strict=True)
    ):
        if (
            row.ordinal != ordinal
            or plan.canonical_sha256() != row.capture_plan_sha256
            or plan.instrument_id != row.instrument_id
            or utc_from_ns(plan.start_ns) != row.opened_at
            or utc_from_ns(plan.end_ns) != row.closed_at
        ):
            raise BlindWindowScheduleBindingError("schedule_plan_mismatch")
    return ScheduleBoundBlindWindowDatasetV1(
        preregistration_sha256=expected_preregistration_sha256,
        capture_schedule_sha256=expected_schedule_sha256,
        coordinate_plan_sha256=dataset.coordinate_plan_sha256,
        complete_dataset_sha256=expected_dataset_sha256,
        dataset_rows_sha256=dataset.rows_sha256,
        holdout_id=dataset.holdout_id,
        window_start_at=dataset.start_at,
        window_end_at=dataset.end_at,
        schedule_recorded_at=pin.recorded_at,
        matched_plan_count=len(dataset.rows),
    )


async def freeze_schedule_bound_blind_window_dataset(
    *,
    repository: Gate3CaptureSchedulePinRepository,
    preregistration: Gate3ProspectivePreregistration,
    expected_preregistration_sha256: str,
    expected_schedule_sha256: str,
    dataset_payload: bytes,
    expected_dataset_sha256: str,
    segments: tuple[BlindWindowJournalSegment, ...],
) -> FrozenGate3Artifact[ScheduleBoundBlindWindowDatasetV1]:
    """Freeze an exact-plan join with an insert-guard time, not a commit time."""
    binding = await _rebuild_binding(
        repository=repository,
        preregistration=preregistration,
        expected_preregistration_sha256=expected_preregistration_sha256,
        expected_schedule_sha256=expected_schedule_sha256,
        dataset_payload=dataset_payload,
        expected_dataset_sha256=expected_dataset_sha256,
        segments=segments,
    )
    return _freeze(binding, contract_type=ScheduleBoundBlindWindowDatasetV1)


async def verify_schedule_bound_blind_window_dataset(
    payload: bytes,
    *,
    expected_sha256: str,
    repository: Gate3CaptureSchedulePinRepository,
    preregistration: Gate3ProspectivePreregistration,
    expected_preregistration_sha256: str,
    expected_schedule_sha256: str,
    dataset_payload: bytes,
    expected_dataset_sha256: str,
    segments: tuple[BlindWindowJournalSegment, ...],
) -> ScheduleBoundBlindWindowDatasetV1:
    """Re-read 0026 and all original bytes without treating a late read as early."""
    contract = _verify(
        payload,
        expected_sha256=expected_sha256,
        contract_type=ScheduleBoundBlindWindowDatasetV1,
    )
    rebuilt = await _rebuild_binding(
        repository=repository,
        preregistration=preregistration,
        expected_preregistration_sha256=expected_preregistration_sha256,
        expected_schedule_sha256=expected_schedule_sha256,
        dataset_payload=dataset_payload,
        expected_dataset_sha256=expected_dataset_sha256,
        segments=segments,
    )
    if contract.canonical_json_bytes() != rebuilt.canonical_json_bytes():
        raise ArtifactVerificationError("schedule binding differs from source replay")
    return rebuilt


__all__ = (
    "BlindWindowScheduleBindingError",
    "ScheduleBoundBlindWindowDatasetV1",
    "freeze_schedule_bound_blind_window_dataset",
    "verify_schedule_bound_blind_window_dataset",
)
