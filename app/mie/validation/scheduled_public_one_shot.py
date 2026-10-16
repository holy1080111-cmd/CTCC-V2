"""Default-off, externally timed entry point for one sealed public minute.

This function owns no timer, retry, source substitution, or research promotion.
It reopens the existing one-use store for every invocation. A failed database
preflight does not claim a minute; after a claim, any uncertain result remains
the original durable tombstone and must never be automatically resubmitted.
"""

from __future__ import annotations

from pathlib import Path

from app.database.repositories.public_receipt_post_read_observation import (
    PublicReceiptPostReadRepository,
)
from app.mie.validation.gate3_capture_schedule_publication_ack import (
    Gate3CaptureSchedulePublicationAckRepository,
)
from app.mie.validation.gate3_preregistration_seal_observer import (
    Gate3PreregistrationSealObserver,
)
from app.mie.validation.prospective_capture_schedule import (
    ProspectiveCaptureScheduleV1,
)
from app.mie.validation.public_checkpoint_service import PublicCheckpointCaptureService
from app.mie.validation.scheduled_public_minute_publication import (
    ScheduledMinutePublicationError,
    ScheduledMinuteReceiptStore,
)
from app.mie.validation.scheduled_public_window import (
    ScheduledPublicWindowCoordinator,
    ScheduledWindowReadbackV1,
)


class ScheduledPublicOneShotError(ValueError):
    """A redacted preclaim refusal with no predictive or capture authority."""


async def drive_scheduled_public_minute_once(
    *,
    root: Path,
    schedule: ProspectiveCaptureScheduleV1,
    expected_seal_sha256: str,
    expected_coordinate_plan_sha256: str,
    expected_schedule_sha256: str,
    ordinal: int,
    expected_previous_receipt_sha256s: tuple[str, ...],
    seal_observer: Gate3PreregistrationSealObserver,
    schedule_repository: Gate3CaptureSchedulePublicationAckRepository,
    capture_service: PublicCheckpointCaptureService,
    observation_repository: PublicReceiptPostReadRepository,
    enabled: bool = False,
) -> ScheduledWindowReadbackV1:
    """Reopen and attempt exactly one externally selected ordinal.

    The two restricted database readers are checked before the local claim and
    again by the owned minute capture after the claim. The second read is
    necessary: a successful first read cannot grant authority across awaits.
    Early and late invocations are deliberately passed to the owned capture,
    which records their one-use incomplete receipt rather than silently waiting
    or retrying. No result from this function is predictive or executable.
    """
    if enabled is not True:
        raise ScheduledPublicOneShotError("scheduled_public_capture_disabled")
    if (
        type(seal_observer) is not Gate3PreregistrationSealObserver
        or type(schedule_repository) is not Gate3CaptureSchedulePublicationAckRepository
        or type(capture_service) is not PublicCheckpointCaptureService
        or type(observation_repository) is not PublicReceiptPostReadRepository
    ):
        raise ScheduledPublicOneShotError("scheduled_public_service_invalid")
    try:
        store = ScheduledMinuteReceiptStore.resume(
            root,
            schedule=schedule,
            expected_coordinate_plan_sha256=expected_coordinate_plan_sha256,
            expected_seal_sha256=expected_seal_sha256,
            expected_schedule_sha256=expected_schedule_sha256,
        )
        window = ScheduledPublicWindowCoordinator(
            store=store,
            schedule=schedule,
            expected_coordinate_plan_sha256=expected_coordinate_plan_sha256,
            expected_seal_sha256=expected_seal_sha256,
            expected_schedule_sha256=expected_schedule_sha256,
        )
    except (ScheduledMinutePublicationError, ValueError):
        raise ScheduledPublicOneShotError(
            "scheduled_public_store_unavailable"
        ) from None

    # A changed or incomplete prior prefix stops before any database or source
    # request. The window owner repeats this check under the later native claim.
    if (
        type(ordinal) is not int
        or type(expected_previous_receipt_sha256s) is not tuple
        or len(expected_previous_receipt_sha256s) != ordinal
    ):
        raise ScheduledPublicOneShotError("scheduled_public_step_invalid")
    prior = window.read_window(
        expected_receipt_sha256s=expected_previous_receipt_sha256s
    )
    if not (
        prior.reason_code == "missing_ordinal"
        and prior.claimed_rows == ordinal
        and prior.verified_receipts == ordinal
        and prior.source_replayed_prefix == ordinal
    ):
        return prior

    # Both the committed DB0030 seal/ACK and DB0029 claimed schedule/ACK must
    # match the externally retained hashes before consuming the one-use slot.
    # The original minute path repeats all checks after the claim.
    try:
        coordinate = schedule.coordinate_plan
        seal = await seal_observer.read_seal(
            expected_seal_sha256=expected_seal_sha256,
            expected_coordinate_plan_sha256=expected_coordinate_plan_sha256,
            coordinate_plan=coordinate,
        )
        seal_ack = await seal_observer.read_ack(
            expected_seal_sha256=expected_seal_sha256,
            expected_coordinate_plan_sha256=expected_coordinate_plan_sha256,
            coordinate_plan=coordinate,
        )
        schedule_ack = await schedule_repository.read(
            expected_schedule_sha256=expected_schedule_sha256, seal=seal.seal
        )
        if (
            seal.seal_sha256 != expected_seal_sha256
            or seal.coordinate_plan_sha256 != expected_coordinate_plan_sha256
            or seal.window_key != coordinate.window_key()
            or seal_ack.seal_sha256 != seal.seal_sha256
            or seal_ack.seal_recorded_at != seal.seal_recorded_at
            or schedule_ack.schedule_sha256 != expected_schedule_sha256
            or schedule_ack.seal_sha256 != seal.seal_sha256
            or schedule_ack.coordinate_plan_sha256 != expected_coordinate_plan_sha256
            or schedule_ack.window_key != seal.window_key
            or schedule_ack.holdout_id != coordinate.holdout_id
            or schedule_ack.schedule != schedule
            or schedule.planned_at < seal_ack.acknowledged_at
            or schedule_ack.schedule_recorded_at > schedule_ack.acknowledged_at
            or schedule_ack.acknowledged_at >= coordinate.start_at
        ):
            raise ValueError("committed schedule identity mismatch")
    except Exception:  # noqa: BLE001 - No database details in the safe error.
        raise ScheduledPublicOneShotError(
            "scheduled_public_committed_preflight_unavailable"
        ) from None

    return await window.capture_next_once(
        ordinal=ordinal,
        expected_previous_receipt_sha256s=expected_previous_receipt_sha256s,
        seal_observer=seal_observer,
        schedule_repository=schedule_repository,
        capture_service=capture_service,
        observation_repository=observation_repository,
        enabled=True,
    )


__all__ = ("ScheduledPublicOneShotError", "drive_scheduled_public_minute_once")
