"""Bounded, original-byte replay of a complete blind minute window.

This is a computational acquisition audit. Caller-supplied journal pins do not
prove independent retention or evaluator non-access, and no predictive claim is
possible from this contract.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.mie.validation.artifact import ArtifactVerificationError, _verify
from app.mie.validation.blind_window_capture import (
    MAX_OBSERVATION_LAG,
    SOURCE,
    SOURCE_VERSION,
    _exact_datetime_ns,
)
from app.mie.validation.contracts import (
    Gate3Claim,
    Gate3Contract,
    Identifier,
    Sha256,
    require_utc,
)
from app.mie.validation.prospective import Gate3ProspectivePreregistration
from app.public_market_source.public_market_capture import replay_public_capture
from app.public_market_source.public_market_receipts import (
    MINUTE_NS,
    MeasuredPublicMinuteReceiptV1,
    PublicMinuteCapturePlanV1,
    PublicReceiptError,
    parsed_rows,
    row_content_sha,
    row_identity,
    utc_from_ns,
)
from app.public_market_source.public_receipt_storage import (
    MAX_CAPTURES,
    ControlledPublicReceiptJournal,
)

MAX_DATASET_ROWS = 4096
MAX_SEGMENTS = 16
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_CAPTURE_ID = re.compile(r"[0-9a-f]{32}\Z")


class BlindWindowDatasetError(ValueError):
    """Sealed window, owned source, original bytes, or coverage failed closed."""


@dataclass(frozen=True, slots=True)
class BlindWindowCapturePin:
    capture_id: str
    plan_sha256: str
    receipt_sha256: str


@dataclass(frozen=True, slots=True)
class BlindWindowJournalSegment:
    journal: ControlledPublicReceiptJournal
    expected_checkpoint_sha256: str
    capture_pins: tuple[BlindWindowCapturePin, ...]


class BlindWindowDatasetRow(Gate3Contract):
    ordinal: int = Field(ge=0, le=MAX_DATASET_ROWS - 1)
    journal_index: int = Field(ge=0, le=MAX_SEGMENTS - 1)
    journal_checkpoint_sha256: Sha256
    capture_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    capture_plan_sha256: Sha256
    capture_receipt_sha256: Sha256
    attempt_sha256: Sha256
    instrument_id: str = Field(pattern=r"^[A-Z0-9]{2,20}-USDT-SWAP$")
    source_row_identity: Sha256
    source_row_sha256: Sha256
    raw_page_sha256: Sha256
    opened_at: datetime
    closed_at: datetime
    request_started_at: datetime
    observed_at: datetime
    retained_at: datetime
    decision_available_at: datetime
    availability_basis: Literal["durable_journal_readback"] = "durable_journal_readback"

    @field_validator(
        "opened_at",
        "closed_at",
        "request_started_at",
        "observed_at",
        "retained_at",
        "decision_available_at",
    )
    @classmethod
    def validate_utc(cls, value: datetime, info) -> datetime:
        return require_utc(value, info.field_name)

    @model_validator(mode="after")
    def validate_chronology(self) -> BlindWindowDatasetRow:
        if not (
            self.opened_at + timedelta(minutes=1)
            == self.closed_at
            <= self.request_started_at
            <= self.observed_at
            <= self.closed_at + MAX_OBSERVATION_LAG
        ):
            raise ValueError("blind minute acquisition was late or noncausal")
        if (
            self.retained_at < self.observed_at
            or self.decision_available_at != self.retained_at
        ):
            raise ValueError("blind minute lacks conservative durable availability")
        return self


def _rows_sha256(rows: tuple[BlindWindowDatasetRow, ...]) -> str:
    digest = hashlib.sha256()
    for row in rows:
        payload = row.canonical_json_bytes()
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()


class CompleteBlindWindowDataset(Gate3Contract):
    """Complete coordinate and hash inventory, without a predictive assertion."""

    schema_version: Literal["ctcc.mie.gate3.complete_blind_window.v1"] = (
        "ctcc.mie.gate3.complete_blind_window.v1"
    )
    preregistration_sha256: Sha256
    source_tree_sha256: Sha256
    holdout_id: Identifier
    coordinate_plan_sha256: Sha256
    start_at: datetime
    end_at: datetime
    first_permitted_evaluator_access_at: datetime
    instrument_ids: tuple[str, ...] = Field(min_length=1)
    expected_rows: int = Field(ge=1, le=MAX_DATASET_ROWS)
    journal_checkpoint_sha256s: tuple[Sha256, ...] = Field(min_length=1)
    rows: tuple[BlindWindowDatasetRow, ...] = Field(min_length=1)
    rows_sha256: Sha256
    journal_pin_basis: Literal["caller_supplied_external_pins_unverified"] = (
        "caller_supplied_external_pins_unverified"
    )
    evaluator_first_read_proven: Literal[False] = False
    current_claim: Literal[Gate3Claim.COMPUTATIONAL] = Gate3Claim.COMPUTATIONAL
    predictive_oos_eligible: Literal[False] = False
    promotion_eligible: Literal[False] = False
    runtime_consumers: Literal[0] = 0
    execution_authority: Literal[False] = False

    @field_validator("start_at", "end_at", "first_permitted_evaluator_access_at")
    @classmethod
    def validate_utc(cls, value: datetime, info) -> datetime:
        return require_utc(value, info.field_name)

    @model_validator(mode="after")
    def validate_complete_coverage(self) -> CompleteBlindWindowDataset:
        if (
            self.end_at <= self.start_at
            or self.first_permitted_evaluator_access_at < self.end_at
            or self.instrument_ids != tuple(sorted(set(self.instrument_ids)))
            or len(self.journal_checkpoint_sha256s) > MAX_SEGMENTS
            or len(set(self.journal_checkpoint_sha256s))
            != len(self.journal_checkpoint_sha256s)
        ):
            raise ValueError("blind window coordinates or journals are invalid")
        duration = self.end_at - self.start_at
        if duration % timedelta(minutes=1) or (
            duration // timedelta(minutes=1) * len(self.instrument_ids)
            != self.expected_rows
        ):
            raise ValueError("blind window expected coordinate count differs")
        if len(self.rows) != self.expected_rows:
            raise ValueError("blind window rows are incomplete")
        used_journals = []
        capture_ids = set()
        identities = set()
        previous_retained_at = None
        for ordinal, row in enumerate(self.rows):
            minute, symbol = divmod(ordinal, len(self.instrument_ids))
            expected_open = self.start_at + timedelta(minutes=minute)
            if (
                row.ordinal != ordinal
                or row.instrument_id != self.instrument_ids[symbol]
                or row.opened_at != expected_open
                or row.journal_index >= len(self.journal_checkpoint_sha256s)
                or row.journal_checkpoint_sha256
                != self.journal_checkpoint_sha256s[row.journal_index]
                or row.retained_at >= self.first_permitted_evaluator_access_at
                or (
                    previous_retained_at is not None
                    and row.retained_at < previous_retained_at
                )
            ):
                raise ValueError("blind window row differs from sealed coordinates")
            if row.journal_index not in used_journals:
                if row.journal_index != len(used_journals):
                    raise ValueError("blind journal rotation order is incomplete")
                used_journals.append(row.journal_index)
            elif row.journal_index != used_journals[-1]:
                raise ValueError("blind journal segment was reused out of order")
            if row.capture_id in capture_ids or row.source_row_identity in identities:
                raise ValueError("blind window contains duplicate capture or source")
            capture_ids.add(row.capture_id)
            identities.add(row.source_row_identity)
            previous_retained_at = row.retained_at
        if len(used_journals) != len(self.journal_checkpoint_sha256s):
            raise ValueError("blind journal segment has no covered coordinates")
        if self.rows_sha256 != _rows_sha256(self.rows):
            raise ValueError("blind window row digest differs")
        return self


def _checked_capture_pin(pin: BlindWindowCapturePin) -> None:
    if (
        type(pin) is not BlindWindowCapturePin
        or type(pin.capture_id) is not str
        or not _CAPTURE_ID.fullmatch(pin.capture_id)
        or type(pin.plan_sha256) is not str
        or not _SHA256.fullmatch(pin.plan_sha256)
        or type(pin.receipt_sha256) is not str
        or not _SHA256.fullmatch(pin.receipt_sha256)
    ):
        raise BlindWindowDatasetError("blind capture external pin is invalid")


def bind_complete_blind_window_dataset(
    *,
    preregistration: Gate3ProspectivePreregistration,
    expected_preregistration_sha256: str,
    segments: tuple[BlindWindowJournalSegment, ...],
) -> CompleteBlindWindowDataset:
    """Reconstruct every sealed minute from exact, independently pinned journals.

    The caller must retain each journal checkpoint/pin outside the journals.
    This function validates supplied pins, but cannot establish their custody.
    """
    if (
        type(preregistration) is not Gate3ProspectivePreregistration
        or type(expected_preregistration_sha256) is not str
        or not _SHA256.fullmatch(expected_preregistration_sha256)
        or type(segments) is not tuple
        or not 1 <= len(segments) <= MAX_SEGMENTS
    ):
        raise BlindWindowDatasetError("blind dataset inputs are invalid")
    seal = Gate3ProspectivePreregistration.model_validate(
        preregistration.model_dump(mode="python")
    )
    if seal.canonical_sha256() != expected_preregistration_sha256:
        raise BlindWindowDatasetError("prospective seal external pin differs")
    holdout = seal.prospective_holdout
    if (
        holdout.source != SOURCE
        or holdout.source_version != SOURCE_VERSION
        or holdout.bar_interval_seconds != 60
        or holdout.artifact_interval_seconds != 60
        or holdout.expected_artifact_count != holdout.expected_rows
        or holdout.expected_rows > MAX_DATASET_ROWS
    ):
        raise BlindWindowDatasetError("seal is incompatible with bounded capture")

    rows = []
    checkpoint_hashes = []
    journal_roots = set()
    capture_ids = set()
    source_identities = set()
    for journal_index, segment in enumerate(segments):
        if (
            type(segment) is not BlindWindowJournalSegment
            or type(segment.journal) is not ControlledPublicReceiptJournal
            or type(segment.expected_checkpoint_sha256) is not str
            or not _SHA256.fullmatch(segment.expected_checkpoint_sha256)
            or type(segment.capture_pins) is not tuple
            or not 1 <= len(segment.capture_pins) <= MAX_CAPTURES
        ):
            raise BlindWindowDatasetError("blind journal segment is invalid")
        for pin in segment.capture_pins:
            _checked_capture_pin(pin)
        checkpoint = segment.journal.checkpoint
        checkpoint_sha = checkpoint.canonical_sha256()
        journal_root = (checkpoint.root_device, checkpoint.root_inode)
        if (
            checkpoint_sha != segment.expected_checkpoint_sha256
            or checkpoint_sha in checkpoint_hashes
            or journal_root in journal_roots
            or checkpoint.sequence != len(segment.capture_pins)
        ):
            raise BlindWindowDatasetError("journal checkpoint or inventory differs")
        entries, first_rows = segment.journal.read_all()
        if len(entries) != len(segment.capture_pins):
            raise BlindWindowDatasetError("journal capture inventory is incomplete")
        for local_index, (entry, pin) in enumerate(
            zip(entries, segment.capture_pins, strict=True)
        ):
            if len(rows) >= holdout.expected_rows:
                raise BlindWindowDatasetError("journal exceeds sealed coordinates")
            plan, receipt, raw_files, journal_entry = entry
            if (
                type(plan) is not PublicMinuteCapturePlanV1
                or type(receipt) is not MeasuredPublicMinuteReceiptV1
                or type(raw_files) is not dict
                or type(journal_entry) is not dict
            ):
                raise BlindWindowDatasetError("journal replay has an invalid record")
            # Native journal replay already checks the full chain. Repeat the
            # original-byte parser here before deriving any row digest.
            replayed_plan, replayed_receipt = replay_public_capture(receipt, raw_files)
            if (
                replayed_plan.canonical_bytes() != plan.canonical_bytes()
                or replayed_receipt.canonical_bytes() != receipt.canonical_bytes()
                or journal_entry["disposition"] != "accepted"
                or receipt.transport_origin != "owned_native_tls"
                or receipt.attempt_sha256 is None
                or plan.canonical_sha256() != pin.plan_sha256
                or receipt.canonical_sha256() != pin.receipt_sha256
                or receipt.capture_id != pin.capture_id
                or plan.source_parser != holdout.source_version
                or plan.instrument_id not in holdout.instrument_ids
                or plan.expected_rows != 1
                or len(receipt.rows) != 1
                or plan.created_ns <= _exact_datetime_ns(seal.created_at)
                or plan.created_ns >= plan.start_ns
            ):
                raise BlindWindowDatasetError("blind capture source or pin differs")
            locator = receipt.rows[0]
            first = first_rows.get(locator["identity"])
            if first is None or first[:2] != (
                locator["content_sha256"],
                local_index,
            ):
                raise BlindWindowDatasetError(
                    "capture was not first source observation"
                )
            raw_page = raw_files[f"page-{locator['page_index']:03d}.raw"]
            source_rows, _ = parsed_rows(raw_page)
            source_row = source_rows[locator["row_ordinal"]]
            if (
                row_identity(plan, source_row) != locator["identity"]
                or row_content_sha(plan, source_row) != locator["content_sha256"]
            ):
                raise BlindWindowDatasetError("source row locator differs from bytes")
            opened_at = utc_from_ns(int(source_row[0]) * 1_000_000)
            closed_at = utc_from_ns(int(source_row[0]) * 1_000_000 + MINUTE_NS)
            request_started_at = utc_from_ns(
                receipt.time_before["request_start"]["utc_ns"]
            )
            observed_at = utc_from_ns(receipt.validation_complete["utc_ns"])
            retained_at = utc_from_ns(
                journal_entry["payload_readback_complete"]["utc_ns"]
            )
            if (
                locator["identity"] in source_identities
                or receipt.capture_id in capture_ids
                or opened_at != utc_from_ns(plan.start_ns)
                or closed_at != utc_from_ns(plan.end_ns)
            ):
                raise BlindWindowDatasetError("duplicate or mismatched source minute")
            ordinal = len(rows)
            minute, symbol = divmod(ordinal, len(holdout.instrument_ids))
            if (
                opened_at != holdout.start_at + timedelta(minutes=minute)
                or plan.instrument_id != holdout.instrument_ids[symbol]
                or closed_at > holdout.end_at
            ):
                raise BlindWindowDatasetError("missing or reordered sealed coordinate")
            rows.append(
                BlindWindowDatasetRow(
                    ordinal=ordinal,
                    journal_index=journal_index,
                    journal_checkpoint_sha256=checkpoint_sha,
                    capture_id=receipt.capture_id,
                    capture_plan_sha256=pin.plan_sha256,
                    capture_receipt_sha256=pin.receipt_sha256,
                    attempt_sha256=receipt.attempt_sha256,
                    instrument_id=plan.instrument_id,
                    source_row_identity=locator["identity"],
                    source_row_sha256=locator["content_sha256"],
                    raw_page_sha256=locator["raw_sha256"],
                    opened_at=opened_at,
                    closed_at=closed_at,
                    request_started_at=request_started_at,
                    observed_at=observed_at,
                    retained_at=retained_at,
                    decision_available_at=retained_at,
                )
            )
            capture_ids.add(receipt.capture_id)
            source_identities.add(locator["identity"])
        if segment.journal.checkpoint.canonical_sha256() != checkpoint_sha:
            raise BlindWindowDatasetError("journal changed during readback")
        checkpoint_hashes.append(checkpoint_sha)
        journal_roots.add(journal_root)
    if len(rows) != holdout.expected_rows:
        raise BlindWindowDatasetError("blind window coordinate coverage is incomplete")
    bound_rows = tuple(rows)
    return CompleteBlindWindowDataset(
        preregistration_sha256=expected_preregistration_sha256,
        source_tree_sha256=seal.source_tree_sha256,
        holdout_id=holdout.holdout_id,
        coordinate_plan_sha256=holdout.coordinate_plan_sha256,
        start_at=holdout.start_at,
        end_at=holdout.end_at,
        first_permitted_evaluator_access_at=holdout.first_permitted_access_at,
        instrument_ids=holdout.instrument_ids,
        expected_rows=holdout.expected_rows,
        journal_checkpoint_sha256s=tuple(checkpoint_hashes),
        rows=bound_rows,
        rows_sha256=_rows_sha256(bound_rows),
    )


def verify_complete_blind_window_dataset(
    payload: bytes,
    *,
    expected_sha256: str,
    preregistration: Gate3ProspectivePreregistration,
    expected_preregistration_sha256: str,
    segments: tuple[BlindWindowJournalSegment, ...],
) -> CompleteBlindWindowDataset:
    """Read back canonical bytes and rebuild them from every original journal."""
    contract = _verify(
        payload,
        expected_sha256=expected_sha256,
        contract_type=CompleteBlindWindowDataset,
    )
    try:
        rebuilt = bind_complete_blind_window_dataset(
            preregistration=preregistration,
            expected_preregistration_sha256=expected_preregistration_sha256,
            segments=segments,
        )
    except (BlindWindowDatasetError, PublicReceiptError, ValueError) as exc:
        raise ArtifactVerificationError("blind dataset source replay failed") from exc
    if contract.canonical_json_bytes() != rebuilt.canonical_json_bytes():
        raise ArtifactVerificationError("blind dataset differs from original source")
    return rebuilt
