"""Default-off, one-shot coordination of a sealed public-minute window.

This module never schedules a retry or promotes a research claim. Each call may
claim only the next ordinal, and the original minute store owns all durable
claims and readbacks. A complete local source chain remains INCOMPLETE until
independent clock, custody and evaluator-access evidence is established.
"""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import Field, model_validator

from app.mie.validation.artifact import SHA256_RE
from app.mie.validation.contracts import Gate3Claim, Gate3Contract, Identifier, Sha256
from app.mie.validation.prospective_capture_schedule import (
    MAX_SCHEDULE_ROWS,
    ProspectiveCaptureScheduleV1,
)
from app.mie.validation.scheduled_public_minute_publication import (
    ScheduledMinutePublicationError,
    ScheduledMinuteReceiptStore,
)

_SOURCE_REPLAYED = "source_chain_replayed_custody_unproven"
_Reason = Literal[
    "disabled",
    "invalid_step",
    "store_unavailable",
    "sequence_conflict",
    "external_pin_invalid",
    "external_pin_missing",
    "missing_ordinal",
    "receipt_unresolved",
    "source_incomplete",
    "duplicate_source",
    "capture_unresolved",
    "source_chain_replayed_custody_unproven",
]


class ScheduledWindowReadbackV1(Gate3Contract):
    """Hash-only computational status; even a full local window is incomplete."""

    schema_version: Literal["ctcc.mie.gate3.scheduled_window_readback.v1"] = (
        "ctcc.mie.gate3.scheduled_window_readback.v1"
    )
    seal_sha256: Sha256
    coordinate_plan_sha256: Sha256
    schedule_sha256: Sha256
    holdout_id: Identifier
    expected_rows: int = Field(ge=1, le=MAX_SCHEDULE_ROWS)
    claimed_rows: int | None = Field(default=None, ge=0, le=MAX_SCHEDULE_ROWS)
    verified_receipts: int = Field(ge=0, le=MAX_SCHEDULE_ROWS)
    source_replayed_prefix: int = Field(ge=0, le=MAX_SCHEDULE_ROWS)
    next_unread_receipt_ordinal: int | None = Field(
        default=None, ge=0, le=MAX_SCHEDULE_ROWS
    )
    last_verified_receipt_sha256: Sha256 | None = None
    receipt_prefix_sha256: Sha256 | None = None
    status: Literal["INCOMPLETE"] = "INCOMPLETE"
    reason_code: _Reason
    current_claim: Literal[Gate3Claim.COMPUTATIONAL] = Gate3Claim.COMPUTATIONAL
    predictive_oos_eligible: Literal[False] = False
    promotion_eligible: Literal[False] = False
    reference_only: Literal[True] = True
    runtime_consumers: Literal[0] = 0
    execution_authority: Literal[False] = False

    @model_validator(mode="after")
    def counts_are_consistent(self) -> ScheduledWindowReadbackV1:
        if not (
            self.source_replayed_prefix <= self.verified_receipts <= self.expected_rows
        ):
            raise ValueError("window readback counts are inconsistent")
        if self.claimed_rows is not None and self.verified_receipts > self.claimed_rows:
            raise ValueError("verified receipts exceed durable claims")
        if (
            self.verified_receipts == 0
            and (
                self.last_verified_receipt_sha256 is not None
                or self.receipt_prefix_sha256 is not None
            )
        ) or (
            self.verified_receipts > 0
            and (
                self.last_verified_receipt_sha256 is None
                or self.receipt_prefix_sha256 is None
            )
        ):
            raise ValueError("window receipt prefix is inconsistent")
        if self.next_unread_receipt_ordinal != (
            None
            if self.verified_receipts == self.expected_rows
            else self.verified_receipts
        ):
            raise ValueError("window next unread receipt ordinal is inconsistent")
        if self.reason_code == _SOURCE_REPLAYED and (
            self.source_replayed_prefix != self.expected_rows
            or self.claimed_rows != self.expected_rows
        ):
            raise ValueError("complete source replay requires every claimed row")
        return self


class ScheduledPublicWindowCoordinator:
    """One externally timed attempt per ordinal, with no automatic loop."""

    __slots__ = ("_identity", "_schedule", "_store")

    def __init__(
        self,
        *,
        store: ScheduledMinuteReceiptStore,
        schedule: ProspectiveCaptureScheduleV1,
        expected_seal_sha256: str,
        expected_coordinate_plan_sha256: str,
        expected_schedule_sha256: str,
    ) -> None:
        if (
            type(store) is not ScheduledMinuteReceiptStore
            or type(schedule) is not ProspectiveCaptureScheduleV1
            or any(
                type(pin) is not str or SHA256_RE.fullmatch(pin) is None
                for pin in (
                    expected_seal_sha256,
                    expected_coordinate_plan_sha256,
                    expected_schedule_sha256,
                )
            )
        ):
            raise ValueError("scheduled window inputs are invalid")
        checked = ProspectiveCaptureScheduleV1.model_validate(
            schedule.model_dump(mode="python")
        )
        identity, _ = store.inspect_inventory()
        if (
            checked.canonical_sha256() != expected_schedule_sha256
            or checked.preregistration_sha256 != expected_seal_sha256
            or checked.coordinate_plan_sha256 != expected_coordinate_plan_sha256
            or identity.schedule_sha256 != expected_schedule_sha256
            or identity.seal_sha256 != expected_seal_sha256
            or identity.coordinate_plan_sha256 != expected_coordinate_plan_sha256
            or identity.holdout_id != checked.coordinate_plan.holdout_id
        ):
            raise ValueError("scheduled window external identity pins differ")
        self._store = store
        self._schedule = checked
        self._identity = identity

    def _result(
        self,
        reason_code: _Reason,
        *,
        claimed_rows: int | None = None,
        receipt_hashes: tuple[str, ...] = (),
        source_replayed_prefix: int = 0,
    ) -> ScheduledWindowReadbackV1:
        count = len(receipt_hashes)
        digest = None
        if receipt_hashes:
            digest = hashlib.sha256(
                json.dumps(receipt_hashes, separators=(",", ":")).encode("ascii")
            ).hexdigest()
        return ScheduledWindowReadbackV1(
            seal_sha256=self._identity.seal_sha256,
            coordinate_plan_sha256=self._identity.coordinate_plan_sha256,
            schedule_sha256=self._identity.schedule_sha256,
            holdout_id=self._identity.holdout_id,
            expected_rows=self._schedule.coordinate_plan.expected_rows,
            claimed_rows=claimed_rows,
            verified_receipts=count,
            source_replayed_prefix=source_replayed_prefix,
            next_unread_receipt_ordinal=(
                None if count == self._schedule.coordinate_plan.expected_rows else count
            ),
            last_verified_receipt_sha256=(
                None if not receipt_hashes else receipt_hashes[-1]
            ),
            receipt_prefix_sha256=digest,
            reason_code=reason_code,
        )

    def read_window(
        self, *, expected_receipt_sha256s: tuple[str, ...]
    ) -> ScheduledWindowReadbackV1:
        """Reopen every claimed minute against external pins, never infer gaps."""
        expected_rows = self._schedule.coordinate_plan.expected_rows
        if (
            type(expected_receipt_sha256s) is not tuple
            or len(expected_receipt_sha256s) > expected_rows
            or any(
                type(pin) is not str or SHA256_RE.fullmatch(pin) is None
                for pin in expected_receipt_sha256s
            )
        ):
            return self._result("external_pin_invalid")
        try:
            identity, claimed = self._store.inspect_inventory()
        except ScheduledMinutePublicationError:
            return self._result("store_unavailable")
        if identity != self._identity:
            return self._result("store_unavailable")
        if claimed != tuple(range(len(claimed))):
            return self._result("sequence_conflict", claimed_rows=len(claimed))

        hashes: list[str] = []
        source_replayed = 0
        seen: dict[str, set[str]] = {
            "capture_id": set(),
            "capture_receipt_sha256": set(),
            "witness_record_sha256": set(),
            "blind_capture_sha256": set(),
            "post_read_v3_sha256": set(),
        }
        for ordinal in claimed:
            if ordinal >= len(expected_receipt_sha256s):
                return self._result(
                    "external_pin_missing",
                    claimed_rows=len(claimed),
                    receipt_hashes=tuple(hashes),
                    source_replayed_prefix=source_replayed,
                )
            try:
                frozen = self._store.read_ordinal(
                    ordinal,
                    expected_receipt_sha256=expected_receipt_sha256s[ordinal],
                )
            except ScheduledMinutePublicationError:
                return self._result(
                    "receipt_unresolved",
                    claimed_rows=len(claimed),
                    receipt_hashes=tuple(hashes),
                    source_replayed_prefix=source_replayed,
                )
            hashes.append(frozen.sha256)
            if frozen.contract.reason_code != _SOURCE_REPLAYED:
                return self._result(
                    "source_incomplete",
                    claimed_rows=len(claimed),
                    receipt_hashes=tuple(hashes),
                    source_replayed_prefix=source_replayed,
                )
            if any(
                getattr(frozen.contract, name) in identities
                for name, identities in seen.items()
            ):
                return self._result(
                    "duplicate_source",
                    claimed_rows=len(claimed),
                    receipt_hashes=tuple(hashes),
                    source_replayed_prefix=source_replayed,
                )
            for name, identities in seen.items():
                identities.add(getattr(frozen.contract, name))
            source_replayed += 1

        return self._result(
            _SOURCE_REPLAYED if len(claimed) == expected_rows else "missing_ordinal",
            claimed_rows=len(claimed),
            receipt_hashes=tuple(hashes),
            source_replayed_prefix=source_replayed,
        )

    async def capture_next_once(
        self,
        *,
        ordinal: int,
        expected_previous_receipt_sha256s: tuple[str, ...],
        seal_observer,
        schedule_repository,
        capture_service,
        observation_repository,
        enabled: bool = False,
    ) -> ScheduledWindowReadbackV1:
        """Attempt exactly one next due minute; never wait, retry or skip."""
        if enabled is not True:
            return self._result("disabled")
        if (
            type(ordinal) is not int
            or not 0 <= ordinal < self._schedule.coordinate_plan.expected_rows
            or type(expected_previous_receipt_sha256s) is not tuple
            or len(expected_previous_receipt_sha256s) != ordinal
        ):
            return self._result("invalid_step")
        prior = self.read_window(
            expected_receipt_sha256s=expected_previous_receipt_sha256s
        )
        if not (
            prior.reason_code == "missing_ordinal"
            and prior.claimed_rows == ordinal
            and prior.verified_receipts == ordinal
            and prior.source_replayed_prefix == ordinal
        ):
            return prior
        try:
            frozen = await self._store.capture_once(
                seal_observer=seal_observer,
                schedule_repository=schedule_repository,
                capture_service=capture_service,
                observation_repository=observation_repository,
                ordinal=ordinal,
                expected_previous_receipt_sha256s=(expected_previous_receipt_sha256s),
            )
        except Exception:  # noqa: BLE001 - A claim may now be durable; never retry.
            return self._result("capture_unresolved")
        result = self.read_window(
            expected_receipt_sha256s=(
                *expected_previous_receipt_sha256s,
                frozen.sha256,
            )
        )
        if result.verified_receipts != ordinal + 1:
            return self._result("capture_unresolved")
        return result


__all__ = ("ScheduledPublicWindowCoordinator", "ScheduledWindowReadbackV1")
