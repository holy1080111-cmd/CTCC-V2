"""One precommitted research minute, with no retry or trading authority.

This is an opt-in computational acquisition seam. A frozen result is not a
trusted-clock, independent-custody, first-access, or predictive OOS receipt.
There is no scheduler or application-startup registration in this module.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.database.repositories.public_receipt_post_read_observation import (
    PublicReceiptPostReadRepository,
)
from app.mie.validation.artifact import (
    ArtifactVerificationError,
    FrozenGate3Artifact,
    _freeze,
    _verify,
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
)
from app.mie.validation.gate3_preregistration_seal_observer import (
    Gate3PreregistrationSealObserver,
)
from app.mie.validation.prospective_capture_schedule import (
    MINUTE_NS,
    ProspectiveCoordinatePlanV1,
)
from app.mie.validation.public_checkpoint_service import (
    OwnedPostReadCapture,
    PublicCheckpointCaptureService,
)
from app.public_market_source.public_clock import native_stamp
from app.public_market_source.public_market_receipts import utc_from_ns

_REASON = Literal[
    "source_chain_replayed_custody_unproven",
    "preflight_unavailable",
    "preflight_mismatch",
    "clock_unavailable",
    "not_due",
    "late",
    "capture_or_post_read_unresolved",
    "capture_result_mismatch",
]


class ScheduledPublicMinuteReceiptV1(Gate3Contract):
    """Hash-only one-coordinate result; failures retain their original sources."""

    schema_version: Literal["ctcc.mie.gate3.scheduled_public_minute.v1"] = (
        "ctcc.mie.gate3.scheduled_public_minute.v1"
    )
    seal_sha256: Sha256
    coordinate_plan_sha256: Sha256
    schedule_sha256: Sha256
    holdout_id: Identifier
    ordinal: int = Field(ge=0, le=4095)
    plan_sha256: Sha256 | None = None
    due_observed_at: datetime | None = None
    status: Literal["incomplete"] = "incomplete"
    reason_code: _REASON
    capture_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    capture_receipt_sha256: Sha256 | None = None
    witness_revision: int | None = Field(default=None, ge=3, le=8192)
    witness_record_sha256: Sha256 | None = None
    journal_checkpoint_sha256: Sha256 | None = None
    blind_capture_sha256: Sha256 | None = None
    post_read_v3_sha256: Sha256 | None = None
    post_read_observed_at: datetime | None = None
    current_claim: Literal[Gate3Claim.COMPUTATIONAL] = Gate3Claim.COMPUTATIONAL
    predictive_oos_eligible: Literal[False] = False
    promotion_eligible: Literal[False] = False
    reference_only: Literal[True] = True
    runtime_consumers: Literal[0] = 0
    execution_authority: Literal[False] = False

    @field_validator("due_observed_at", "post_read_observed_at")
    @classmethod
    def utc(cls, value: datetime | None, info) -> datetime | None:
        return None if value is None else require_utc(value, info.field_name)

    @model_validator(mode="after")
    def replayed_source_has_all_pins(self) -> ScheduledPublicMinuteReceiptV1:
        required = (
            self.plan_sha256,
            self.due_observed_at,
            self.capture_id,
            self.capture_receipt_sha256,
            self.witness_revision,
            self.witness_record_sha256,
            self.journal_checkpoint_sha256,
            self.blind_capture_sha256,
            self.post_read_v3_sha256,
            self.post_read_observed_at,
        )
        if self.reason_code == "source_chain_replayed_custody_unproven":
            if any(item is None for item in required):
                raise ValueError("replayed scheduled minute lacks exact source pins")
            if self.post_read_observed_at < self.due_observed_at:
                raise ValueError("post-read observation predates due check")
        elif (
            self.blind_capture_sha256 is not None
            or self.post_read_v3_sha256 is not None
        ):
            raise ValueError("unresolved scheduled minute cannot claim source replay")
        return self


class ScheduledPublicMinuteReceiptV2(ScheduledPublicMinuteReceiptV1):
    """Exact native due timestamp for later half-open boundary replay."""

    schema_version: Literal["ctcc.mie.gate3.scheduled_public_minute.v2"] = (
        "ctcc.mie.gate3.scheduled_public_minute.v2"
    )
    due_observed_utc_ns: int | None = Field(
        default=None, strict=True, ge=0, le=2**63 - 1
    )

    @model_validator(mode="after")
    def native_due_matches_display_time(self) -> ScheduledPublicMinuteReceiptV2:
        if (self.due_observed_utc_ns is None) != (self.due_observed_at is None):
            raise ValueError("native due timestamp and display time must pair")
        if (
            self.due_observed_utc_ns is not None
            and utc_from_ns(self.due_observed_utc_ns) != self.due_observed_at
        ):
            raise ValueError("native due timestamp differs from display time")
        return self


def _receipt(
    *,
    seal_sha256: str,
    coordinate_plan_sha256: str,
    schedule_sha256: str,
    holdout_id: str,
    ordinal: int,
    reason_code: _REASON,
    plan_sha256: str | None = None,
    due_observed_at: datetime | None = None,
    due_observed_utc_ns: int | None = None,
    result: OwnedPostReadCapture | None = None,
) -> FrozenGate3Artifact[ScheduledPublicMinuteReceiptV2]:
    witnessed = None if result is None else result.witnessed
    receipt = ScheduledPublicMinuteReceiptV2(
        seal_sha256=seal_sha256,
        coordinate_plan_sha256=coordinate_plan_sha256,
        schedule_sha256=schedule_sha256,
        holdout_id=holdout_id,
        ordinal=ordinal,
        plan_sha256=plan_sha256,
        due_observed_at=due_observed_at,
        due_observed_utc_ns=due_observed_utc_ns,
        reason_code=reason_code,
        capture_id=None if witnessed is None else witnessed.capture_id,
        capture_receipt_sha256=(
            None if witnessed is None else witnessed.receipt_sha256
        ),
        witness_revision=None if witnessed is None else witnessed.witness_revision,
        witness_record_sha256=(
            None if witnessed is None else witnessed.witness_record_sha256
        ),
        journal_checkpoint_sha256=(
            None if witnessed is None else witnessed.checkpoint_sha256
        ),
        blind_capture_sha256=(None if result is None else result.blind_capture_sha256),
        post_read_v3_sha256=(None if result is None else result.post_read_v3_sha256),
        post_read_observed_at=(
            None if result is None else result.post_read_observed_at
        ),
    )
    return _freeze(receipt, contract_type=ScheduledPublicMinuteReceiptV2)


async def capture_scheduled_public_minute_once(
    *,
    seal_observer: Gate3PreregistrationSealObserver,
    schedule_repository: Gate3CaptureSchedulePublicationAckRepository,
    capture_service: PublicCheckpointCaptureService,
    observation_repository: PublicReceiptPostReadRepository,
    coordinate_plan: ProspectiveCoordinatePlanV1,
    expected_coordinate_plan_sha256: str,
    expected_seal_sha256: str,
    expected_schedule_sha256: str,
    ordinal: int,
) -> FrozenGate3Artifact[ScheduledPublicMinuteReceiptV2]:
    """Attempt one due minute only; never resubmit after an uncertain result.

    A returned incomplete receipt is diagnostic, not a repaired source. The
    original journal and PostgreSQL rows are never removed or rewritten here.
    Cancellation propagates so the owning process can stop; it never retries.
    """
    if (
        type(seal_observer) is not Gate3PreregistrationSealObserver
        or type(schedule_repository) is not Gate3CaptureSchedulePublicationAckRepository
        or type(capture_service) is not PublicCheckpointCaptureService
        or type(observation_repository) is not PublicReceiptPostReadRepository
        or type(coordinate_plan) is not ProspectiveCoordinatePlanV1
        or type(ordinal) is not int
    ):
        raise ValueError("scheduled_minute_inputs_invalid")
    checked_coordinate = ProspectiveCoordinatePlanV1.model_validate(
        coordinate_plan.model_dump(mode="python")
    )
    if (
        checked_coordinate.canonical_sha256() != expected_coordinate_plan_sha256
        or not 0 <= ordinal < checked_coordinate.expected_rows
    ):
        raise ValueError("scheduled_minute_coordinate_pin_invalid")
    common = {
        "seal_sha256": expected_seal_sha256,
        "coordinate_plan_sha256": expected_coordinate_plan_sha256,
        "schedule_sha256": expected_schedule_sha256,
        "holdout_id": checked_coordinate.holdout_id,
        "ordinal": ordinal,
    }
    try:
        seal = await seal_observer.read_seal(
            expected_seal_sha256=expected_seal_sha256,
            expected_coordinate_plan_sha256=expected_coordinate_plan_sha256,
            coordinate_plan=checked_coordinate,
        )
        seal_ack = await seal_observer.read_ack(
            expected_seal_sha256=expected_seal_sha256,
            expected_coordinate_plan_sha256=expected_coordinate_plan_sha256,
            coordinate_plan=checked_coordinate,
        )
        schedule_ack = await schedule_repository.read(
            expected_schedule_sha256=expected_schedule_sha256, seal=seal.seal
        )
    except Exception:  # noqa: BLE001 - Redact DB and source details.
        return _receipt(**common, reason_code="preflight_unavailable")
    schedule = schedule_ack.schedule
    if (
        seal.seal_sha256 != expected_seal_sha256
        or seal.coordinate_plan_sha256 != expected_coordinate_plan_sha256
        or seal.window_key != checked_coordinate.window_key()
        or seal_ack.seal_sha256 != seal.seal_sha256
        or seal_ack.seal_recorded_at != seal.seal_recorded_at
        or schedule_ack.schedule_sha256 != expected_schedule_sha256
        or schedule_ack.seal_sha256 != seal.seal_sha256
        or schedule_ack.coordinate_plan_sha256 != expected_coordinate_plan_sha256
        or schedule_ack.window_key != seal.window_key
        or schedule.coordinate_plan != checked_coordinate
        or schedule.planned_at < seal_ack.acknowledged_at
        or schedule_ack.acknowledged_at < schedule_ack.schedule_recorded_at
        or schedule_ack.acknowledged_at >= checked_coordinate.start_at
        or ordinal >= len(schedule.plans)
    ):
        return _receipt(**common, reason_code="preflight_mismatch")
    plan = schedule.plans[ordinal]
    plan_sha256 = plan.canonical_sha256()
    try:
        stamp = native_stamp()
        now_ns = stamp["utc_ns"]
        if type(now_ns) is not int:
            raise ValueError("clock shape invalid")
        due_at = utc_from_ns(now_ns)
    except Exception:  # noqa: BLE001 - No host diagnostic data in receipt.
        return _receipt(
            **common, plan_sha256=plan_sha256, reason_code="clock_unavailable"
        )
    if now_ns < plan.end_ns:
        return _receipt(
            **common,
            plan_sha256=plan_sha256,
            due_observed_at=due_at,
            due_observed_utc_ns=now_ns,
            reason_code="not_due",
        )
    if now_ns >= plan.end_ns + MINUTE_NS:
        return _receipt(
            **common,
            plan_sha256=plan_sha256,
            due_observed_at=due_at,
            due_observed_utc_ns=now_ns,
            reason_code="late",
        )
    try:
        result = await capture_service.capture_with_post_read_v3(
            plan=plan,
            expected_plan_sha256=plan_sha256,
            preregistration=seal.seal,
            expected_preregistration_sha256=expected_seal_sha256,
            observation_repository=observation_repository,
        )
    except Exception:  # noqa: BLE001 - Never retry ambiguous source or DB writes.
        return _receipt(
            **common,
            plan_sha256=plan_sha256,
            due_observed_at=due_at,
            due_observed_utc_ns=now_ns,
            reason_code="capture_or_post_read_unresolved",
        )
    if (
        type(result) is not OwnedPostReadCapture
        or result.predictive_oos_eligible is not False
        or result.execution_authority is not False
        or result.post_read_observed_at < due_at
    ):
        return _receipt(
            **common,
            plan_sha256=plan_sha256,
            due_observed_at=due_at,
            due_observed_utc_ns=now_ns,
            reason_code="capture_result_mismatch",
        )
    return _receipt(
        **common,
        plan_sha256=plan_sha256,
        due_observed_at=due_at,
        due_observed_utc_ns=now_ns,
        reason_code="source_chain_replayed_custody_unproven",
        result=result,
    )


def verify_scheduled_public_minute_receipt(
    payload: bytes, *, expected_sha256: str
) -> ScheduledPublicMinuteReceiptV1 | ScheduledPublicMinuteReceiptV2:
    """Verify exact canonical receipt bytes; never promote its claim."""
    try:
        return _verify(
            payload,
            expected_sha256=expected_sha256,
            contract_type=ScheduledPublicMinuteReceiptV2,
        )
    except ArtifactVerificationError:
        return _verify(
            payload,
            expected_sha256=expected_sha256,
            contract_type=ScheduledPublicMinuteReceiptV1,
        )


__all__ = (
    "ScheduledPublicMinuteReceiptV1",
    "ScheduledPublicMinuteReceiptV2",
    "capture_scheduled_public_minute_once",
    "verify_scheduled_public_minute_receipt",
)
