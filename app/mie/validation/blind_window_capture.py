"""Bind one prospective in-window minute to an owned public source journal.

This is an acquisition-owner audit record, not a holdout evaluation receipt.
It exposes no price, dataset partition, predictive claim, or runtime authority.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.mie.validation.batch_replay import minute_source_sha256
from app.mie.validation.contracts import (
    Gate3Claim,
    Gate3Contract,
    Identifier,
    Sha256,
    require_utc,
)
from app.mie.validation.measured_public_replay import measured_public_minutes
from app.mie.validation.prospective import Gate3ProspectivePreregistration
from app.public_market_source.public_market_receipts import (
    PublicMinuteCapturePlanV1,
    PublicReceiptError,
    utc_from_ns,
)
from app.public_market_source.public_receipt_storage import (
    ControlledPublicReceiptJournal,
)

SOURCE = "okx.public_market_source"
SOURCE_VERSION = "okx.history_candles.nine_strings.v1"
MAX_OBSERVATION_LAG = timedelta(minutes=1)


class BlindWindowMinuteCapture(Gate3Contract):
    """Hash-only audit of one captured future minute, with no claim promotion."""

    schema_version: Literal["ctcc.mie.gate3.blind_window_minute.v1"] = (
        "ctcc.mie.gate3.blind_window_minute.v1"
    )
    preregistration_sha256: Sha256
    holdout_id: Identifier
    coordinate_plan_sha256: Sha256
    capture_plan_sha256: Sha256
    capture_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    capture_receipt_sha256: Sha256
    attempt_sha256: Sha256
    journal_checkpoint_sha256: Sha256
    journal_sequence: int = Field(ge=1, le=1024)
    origin: Literal[
        "https://openapi.okx.com", "https://us.okx.com", "https://eea.okx.com"
    ]
    instrument_id: str = Field(pattern=r"^[A-Z0-9]{2,20}-USDT-SWAP$")
    source_row_id: str = Field(min_length=3, max_length=160)
    source_row_sha256: Sha256
    source_minutes_sha256: Sha256
    source_row_count: Literal[1] = 1
    holdout_start_at: datetime
    holdout_end_at: datetime
    bar_closed_at: datetime
    request_started_at: datetime
    observed_at: datetime
    retained_at: datetime
    first_permitted_evaluator_access_at: datetime
    availability_basis: Literal["measured_row_receipt"] = "measured_row_receipt"
    transport_origin: Literal["owned_native_tls"] = "owned_native_tls"
    current_claim: Literal[Gate3Claim.COMPUTATIONAL] = Gate3Claim.COMPUTATIONAL
    predictive_oos_eligible: Literal[False] = False
    reference_only: Literal[True] = True
    runtime_consumers: Literal[0] = 0
    execution_authority: Literal[False] = False

    @field_validator(
        "holdout_start_at",
        "holdout_end_at",
        "bar_closed_at",
        "request_started_at",
        "observed_at",
        "retained_at",
        "first_permitted_evaluator_access_at",
    )
    @classmethod
    def validate_utc(cls, value: datetime, info) -> datetime:
        return require_utc(value, info.field_name)

    @model_validator(mode="after")
    def validate_chronology(self) -> BlindWindowMinuteCapture:
        if not (
            self.holdout_start_at
            <= self.bar_closed_at - timedelta(minutes=1)
            < self.bar_closed_at
            <= self.holdout_end_at
        ):
            raise ValueError("minute is outside the sealed holdout window")
        if not (
            self.bar_closed_at
            <= self.request_started_at
            <= self.observed_at
            <= self.bar_closed_at + MAX_OBSERVATION_LAG
        ):
            raise ValueError("minute was not acquired causally and promptly")
        if self.retained_at < self.observed_at:
            raise ValueError("journal readback predates source validation")
        if self.first_permitted_evaluator_access_at < self.holdout_end_at:
            raise ValueError("evaluator access predates the sealed holdout end")
        return self


def bind_blind_window_minute(
    *,
    journal: ControlledPublicReceiptJournal,
    preregistration: Gate3ProspectivePreregistration,
    expected_preregistration_sha256: str,
    expected_capture_plan_sha256: str,
    expected_capture_receipt_sha256: str,
    expected_journal_checkpoint_sha256: str,
    capture_id: str,
) -> BlindWindowMinuteCapture:
    """Replay original bytes and bind one blind capture to independent pins.

    The pins must come from records outside the inspected journal. This function
    cannot prove who retained them or whether a human viewed source prices.
    """
    if (
        type(journal) is not ControlledPublicReceiptJournal
        or type(preregistration) is not Gate3ProspectivePreregistration
        or any(
            type(value) is not str
            for value in (
                expected_preregistration_sha256,
                expected_capture_plan_sha256,
                expected_capture_receipt_sha256,
                expected_journal_checkpoint_sha256,
                capture_id,
            )
        )
    ):
        raise PublicReceiptError("blind_capture_inputs_invalid")
    sealed = Gate3ProspectivePreregistration.model_validate(
        preregistration.model_dump(mode="python")
    )
    if sealed.canonical_sha256() != expected_preregistration_sha256:
        raise PublicReceiptError("prospective_seal_pin_mismatch")
    holdout = sealed.prospective_holdout
    if (
        holdout.source != SOURCE
        or holdout.source_version != SOURCE_VERSION
        or holdout.bar_interval_seconds != 60
    ):
        raise PublicReceiptError("prospective_source_incompatible")
    checkpoint = journal.checkpoint
    if checkpoint.canonical_sha256() != expected_journal_checkpoint_sha256:
        raise PublicReceiptError("journal_checkpoint_pin_mismatch")
    entries, first_rows = journal.read_all()
    selected = [
        (index, entry)
        for index, entry in enumerate(entries, start=1)
        if entry[1].capture_id == capture_id
    ]
    if len(selected) != 1:
        raise PublicReceiptError("blind_capture_identity_missing_or_duplicate")
    sequence, (plan, receipt, _, entry) = selected[0]
    plan = PublicMinuteCapturePlanV1.model_validate(plan.model_dump(mode="python"))
    if (
        entry["disposition"] != "accepted"
        or receipt.transport_origin != "owned_native_tls"
        or receipt.attempt_sha256 is None
    ):
        raise PublicReceiptError("owned_attempt_required")
    if (
        plan.canonical_sha256() != expected_capture_plan_sha256
        or receipt.canonical_sha256() != expected_capture_receipt_sha256
        or plan.source_parser != holdout.source_version
    ):
        raise PublicReceiptError("blind_capture_pin_mismatch")
    if plan.instrument_id not in holdout.instrument_ids or plan.expected_rows != 1:
        raise PublicReceiptError("blind_capture_coordinate_mismatch")
    if any(
        locator["identity"] not in first_rows
        or first_rows[locator["identity"]][:2]
        != (locator["content_sha256"], sequence - 1)
        for locator in receipt.rows
    ):
        raise PublicReceiptError("blind_capture_not_first_observation")
    if (
        utc_from_ns(plan.start_ns) < holdout.start_at
        or utc_from_ns(plan.end_ns) > holdout.end_at
        or plan.created_ns > plan.start_ns
        or utc_from_ns(plan.created_ns) < sealed.created_at
    ):
        raise PublicReceiptError("blind_capture_not_preplanned_in_window")
    minutes = measured_public_minutes(
        journal=journal,
        capture_id=capture_id,
        expected_receipt_sha256=expected_capture_receipt_sha256,
        expected_plan_sha256=expected_capture_plan_sha256,
    )
    if len(minutes) != 1 or journal.checkpoint != checkpoint:
        raise PublicReceiptError("blind_capture_changed_during_readback")
    minute = minutes[0]
    if minute.row.bar.closed_at != utc_from_ns(plan.end_ns):
        raise PublicReceiptError("blind_capture_row_coordinate_mismatch")
    return BlindWindowMinuteCapture(
        preregistration_sha256=expected_preregistration_sha256,
        holdout_id=holdout.holdout_id,
        coordinate_plan_sha256=holdout.coordinate_plan_sha256,
        capture_plan_sha256=expected_capture_plan_sha256,
        capture_id=capture_id,
        capture_receipt_sha256=expected_capture_receipt_sha256,
        attempt_sha256=receipt.attempt_sha256,
        journal_checkpoint_sha256=expected_journal_checkpoint_sha256,
        journal_sequence=sequence,
        origin=plan.origin,
        instrument_id=plan.instrument_id,
        source_row_id=minute.row.source_row_id,
        source_row_sha256=minute.row.source_row_sha256,
        source_minutes_sha256=minute_source_sha256(minutes),
        holdout_start_at=holdout.start_at,
        holdout_end_at=holdout.end_at,
        bar_closed_at=minute.row.bar.closed_at,
        request_started_at=utc_from_ns(receipt.time_before["request_start"]["utc_ns"]),
        observed_at=minute.availability.observed_at,
        retained_at=minute.availability.retrieved_at,
        first_permitted_evaluator_access_at=holdout.first_permitted_access_at,
    )
