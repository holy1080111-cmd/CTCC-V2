"""Default-off, finite two-symbol prospective public research dispatcher.

This owner does not provision seals, database roles, journals, or a custodian.
It may only dispatch an already committed exact 4H schedule. Every ordinal is
attempted at most once. A missed, ambiguous, or unpinned result ends the run;
neither sleep nor process restart is an authority to retry a minute.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Literal

from pydantic import Field

from app.database.repositories.public_receipt_post_read_observation import (
    PublicReceiptPostReadRepository,
)
from app.mie.validation.contracts import Gate3Claim, Gate3Contract, Sha256
from app.mie.validation.gate3_capture_schedule_publication_ack import (
    Gate3CaptureSchedulePublicationAckRepository,
)
from app.mie.validation.gate3_preregistration_seal_observer import (
    Gate3PreregistrationSealObserver,
)
from app.mie.validation.prospective_capture_schedule import ProspectiveCaptureScheduleV1
from app.mie.validation.public_checkpoint_service import PublicCheckpointCaptureService
from app.mie.validation.scheduled_public_minute_publication import (
    ScheduledMinuteReceiptStore,
)
from app.mie.validation.scheduled_public_one_shot import (
    drive_scheduled_public_minute_once,
)
from app.mie.validation.scheduled_public_pin_chain import (
    ScheduledPublicPinChain,
)
from app.public_market_source.public_clock import native_stamp

_MINUTE_NS = 60_000_000_000
_MAX_SLEEP_SECONDS = 30
_SUCCESS = "source_chain_replayed_custody_unproven"
_Reason = Literal[
    "disabled",
    "window_in_progress_custody_unverified",
    "source_chain_replayed_custody_unproven",
    "prior_minute_incomplete",
    "pin_or_original_source_unavailable",
    "native_clock_unavailable",
    "native_clock_regressed",
    "scheduled_capture_unresolved",
    "scheduled_source_incomplete",
    "scheduled_pin_append_unresolved",
]


class ScheduledFourHourRunStatusV1(Gate3Contract):
    schema_version: Literal["ctcc.mie.gate3.scheduled_four_hour_run.v1"] = (
        "ctcc.mie.gate3.scheduled_four_hour_run.v1"
    )
    schedule_sha256: Sha256
    expected_rows: Literal[480] = 480
    pinned_rows: int = Field(ge=0, le=480)
    source_replayed_rows: int = Field(ge=0, le=480)
    next_ordinal: int | None = Field(default=None, ge=0, lt=480)
    latest_pin_sha256: Sha256 | None = None
    reason_code: _Reason
    status: Literal["INCOMPLETE"] = "INCOMPLETE"
    current_claim: Literal[Gate3Claim.COMPUTATIONAL] = Gate3Claim.COMPUTATIONAL
    independently_protected: Literal[False] = False
    trusted_clock_verified: Literal[False] = False
    evaluator_first_read_proven: Literal[False] = False
    predictive_oos_eligible: Literal[False] = False
    promotion_eligible: Literal[False] = False
    runtime_consumers: Literal[0] = 0
    execution_authority: Literal[False] = False


async def _wait_until_due(due_ns: int) -> None:
    """Wait with native wall/monotonic samples; never treat sleep as a clock."""
    previous: tuple[int, int] | None = None
    while True:
        try:
            stamp = native_stamp()
            wall, monotonic = stamp["utc_ns"], stamp["monotonic_ns"]
            if (
                type(wall) is not int
                or type(monotonic) is not int
                or wall < 0
                or monotonic < 0
            ):
                raise ValueError("native stamp shape")
        except Exception as exc:
            raise ValueError("native_clock_unavailable") from exc
        if previous is not None:
            prior_wall, prior_monotonic = previous
            if monotonic <= prior_monotonic or wall < prior_wall:
                raise ValueError("native_clock_regressed")
            # A time adjustment beyond the native sampling bound is not proof
            # of a safe due-time. The owned collector repeats clock checks.
            if abs((wall - prior_wall) - (monotonic - prior_monotonic)) > 5_000_000:
                raise ValueError("native_clock_regressed")
        if wall >= due_ns:
            return
        previous = (wall, monotonic)
        await asyncio.sleep(min((due_ns - wall) / 1_000_000_000, _MAX_SLEEP_SECONDS))


class ScheduledPublicFourHourRunner:
    """One explicit dispatch per ordinal, or a finite 480-step timed run."""

    __slots__ = (
        "_capture_service",
        "_coordinate_sha",
        "_observation_repository",
        "_pin_chain",
        "_root",
        "_schedule",
        "_schedule_repository",
        "_schedule_sha",
        "_seal_observer",
        "_seal_sha",
    )

    def __init__(
        self,
        *,
        root: Path,
        schedule: ProspectiveCaptureScheduleV1,
        expected_seal_sha256: str,
        expected_coordinate_plan_sha256: str,
        expected_schedule_sha256: str,
        pin_chain: ScheduledPublicPinChain,
        seal_observer: Gate3PreregistrationSealObserver,
        schedule_repository: Gate3CaptureSchedulePublicationAckRepository,
        capture_service: PublicCheckpointCaptureService,
        observation_repository: PublicReceiptPostReadRepository,
    ) -> None:
        if (
            type(pin_chain) is not ScheduledPublicPinChain
            or type(schedule) is not ProspectiveCaptureScheduleV1
            or type(seal_observer) is not Gate3PreregistrationSealObserver
            or type(schedule_repository)
            is not Gate3CaptureSchedulePublicationAckRepository
            or type(capture_service) is not PublicCheckpointCaptureService
            or type(observation_repository) is not PublicReceiptPostReadRepository
            or schedule.coordinate_plan.instrument_ids
            != ("BTC-USDT-SWAP", "ETH-USDT-SWAP")
            or schedule.coordinate_plan.expected_rows != 480
            or (
                schedule.coordinate_plan.end_at - schedule.coordinate_plan.start_at
            ).total_seconds()
            != 14_400
            or schedule.canonical_sha256() != expected_schedule_sha256
            or schedule.preregistration_sha256 != expected_seal_sha256
            or schedule.coordinate_plan_sha256 != expected_coordinate_plan_sha256
        ):
            raise ValueError("scheduled_four_hour_exact_inputs_required")
        pin_chain.require_source_root(root)
        self._root = root
        self._schedule = schedule
        self._seal_sha = expected_seal_sha256
        self._coordinate_sha = expected_coordinate_plan_sha256
        self._schedule_sha = expected_schedule_sha256
        self._pin_chain = pin_chain
        self._seal_observer = seal_observer
        self._schedule_repository = schedule_repository
        self._capture_service = capture_service
        self._observation_repository = observation_repository

    def _status(self, reason: _Reason, pins=()) -> ScheduledFourHourRunStatusV1:
        count = len(pins)
        source_count = sum(pin.reason_code == _SUCCESS for pin in pins)
        return ScheduledFourHourRunStatusV1(
            schedule_sha256=self._schedule_sha,
            pinned_rows=count,
            source_replayed_rows=source_count,
            next_ordinal=None if count == 480 else count,
            latest_pin_sha256=None if not pins else pins[-1].canonical_sha256(),
            reason_code=reason,
        )

    def _source_store(self) -> ScheduledMinuteReceiptStore:
        return ScheduledMinuteReceiptStore.resume(
            self._root,
            schedule=self._schedule,
            expected_seal_sha256=self._seal_sha,
            expected_coordinate_plan_sha256=self._coordinate_sha,
            expected_schedule_sha256=self._schedule_sha,
        )

    async def dispatch_ordinal_once(
        self, *, ordinal: int, enabled: bool = False
    ) -> ScheduledFourHourRunStatusV1:
        """Wait for one exact due coordinate; no poll-and-retry of a claim."""
        if enabled is not True:
            return self._status("disabled")
        try:
            source_store = self._source_store()
            pins = self._pin_chain.read_prefix(
                source_store=source_store, capture_service=self._capture_service
            )
        except Exception:  # noqa: BLE001 - An unpinned claim must remain unresolved.
            return self._status("pin_or_original_source_unavailable")
        if (
            type(ordinal) is not int
            or ordinal != len(pins)
            or ordinal >= 480
            or (pins and pins[-1].reason_code != _SUCCESS)
        ):
            return self._status("prior_minute_incomplete", pins)
        try:
            await _wait_until_due(self._schedule.plans[ordinal].end_ns)
        except Exception as exc:  # noqa: BLE001 - A clock/wait failure never claims.
            reason = str(exc)
            return self._status(
                "native_clock_regressed"
                if reason == "native_clock_regressed"
                else "native_clock_unavailable",
                pins,
            )
        # The wait can span many native clock samples. A pinned source may
        # change while this coroutine is asleep; re-read its original journal
        # before the next one-use claim and any public capture request.
        try:
            if (
                self._pin_chain.read_prefix(
                    source_store=source_store,
                    capture_service=self._capture_service,
                )
                != pins
            ):
                raise ValueError("scheduled pin prefix changed during wait")
        except Exception:  # noqa: BLE001 - A changed source cannot advance.
            return self._status("pin_or_original_source_unavailable", pins)
        try:
            result = await drive_scheduled_public_minute_once(
                root=self._root,
                schedule=self._schedule,
                expected_seal_sha256=self._seal_sha,
                expected_coordinate_plan_sha256=self._coordinate_sha,
                expected_schedule_sha256=self._schedule_sha,
                ordinal=ordinal,
                expected_previous_receipt_sha256s=tuple(
                    pin.receipt_sha256 for pin in pins
                ),
                seal_observer=self._seal_observer,
                schedule_repository=self._schedule_repository,
                capture_service=self._capture_service,
                observation_repository=self._observation_repository,
                enabled=True,
            )
        except Exception:  # noqa: BLE001 - A claim may be durable; never retry.
            return self._status("scheduled_capture_unresolved", pins)
        if (
            result.verified_receipts != ordinal + 1
            or result.last_verified_receipt_sha256 is None
        ):
            return self._status("scheduled_capture_unresolved", pins)
        try:
            self._pin_chain.append(
                ordinal=ordinal,
                expected_receipt_sha256=result.last_verified_receipt_sha256,
                expected_previous_pin_sha256=(
                    None if not pins else pins[-1].canonical_sha256()
                ),
                source_store=source_store,
                capture_service=self._capture_service,
            )
            checked = self._pin_chain.read_prefix(
                source_store=source_store, capture_service=self._capture_service
            )
        except Exception:  # noqa: BLE001 - A pin may be durable; never retry.
            return self._status("scheduled_pin_append_unresolved", pins)
        if len(checked) != ordinal + 1:
            return self._status("scheduled_pin_append_unresolved", pins)
        if checked[-1].reason_code != _SUCCESS:
            return self._status("scheduled_source_incomplete", checked)
        return self._status(
            "source_chain_replayed_custody_unproven"
            if len(checked) == 480
            else "window_in_progress_custody_unverified",
            checked,
        )

    async def run_window(
        self, *, enabled: bool = False
    ) -> ScheduledFourHourRunStatusV1:
        """Continue only a verified prefix; stop on the first uncertain minute."""
        if enabled is not True:
            return self._status("disabled")
        try:
            pins = self._pin_chain.read_prefix(
                source_store=self._source_store(),
                capture_service=self._capture_service,
            )
        except Exception:  # noqa: BLE001 - Never repair a claimed gap.
            return self._status("pin_or_original_source_unavailable")
        if pins and pins[-1].reason_code != _SUCCESS:
            return self._status("prior_minute_incomplete", pins)
        if len(pins) == 480:
            return self._status("source_chain_replayed_custody_unproven", pins)
        for ordinal in range(len(pins), 480):
            status = await self.dispatch_ordinal_once(ordinal=ordinal, enabled=True)
            if status.reason_code != "window_in_progress_custody_unverified":
                return status
        raise AssertionError("finite scheduled window did not return")


__all__ = ("ScheduledFourHourRunStatusV1", "ScheduledPublicFourHourRunner")
