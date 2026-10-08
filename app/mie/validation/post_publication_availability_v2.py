"""Conservative post-publication minute timing from a committed witness read.

V1 minute and blind-window contracts remain untouched. This V2 record is still
computational only: database clock trust, independent custody and first access
are not established by a separately read witness row.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.database.repositories.public_receipt_witness import (
    CommittedWitnessObservation,
    PublicReceiptWitnessRepository,
)
from app.mie.features import FeatureBar
from app.mie.validation.artifact import (
    ArtifactVerificationError,
    FrozenGate3Artifact,
    _freeze,
    _verify,
)
from app.mie.validation.contracts import (
    Gate3Claim,
    Gate3Contract,
    Sha256,
    require_utc,
)
from app.mie.validation.measured_public_replay import measured_public_minutes
from app.mie.validation.replay import PointInTimeBar
from app.public_market_source.public_market_capture import replay_public_capture
from app.public_market_source.public_market_receipts import (
    canonical,
    sha,
    utc_from_ns,
)
from app.public_market_source.public_receipt_storage import (
    ControlledPublicReceiptJournal,
)

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_CAPTURE_ID = re.compile(r"[0-9a-f]{32}\Z")


class PostPublicationAvailabilityError(ValueError):
    """A source, committed witness, pin, or causal ordering failed closed."""


class PostPublicationMinuteV2(Gate3Contract):
    source_row_identity: Sha256
    source_row_sha256: Sha256
    raw_page_sha256: Sha256
    bar: FeatureBar


class PostPublicationCaptureV2(Gate3Contract):
    schema_version: Literal["ctcc.mie.gate3.post_publication_capture.v2"] = (
        "ctcc.mie.gate3.post_publication_capture.v2"
    )
    capture_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    plan_sha256: Sha256
    receipt_sha256: Sha256
    journal_genesis_sha256: Sha256
    capture_checkpoint_sha256: Sha256
    capture_sequence: int = Field(ge=1, le=1024)
    witness_revision: int = Field(ge=3, le=8192)
    witness_record_sha256: Sha256
    instrument_id: str = Field(pattern=r"^[A-Z0-9]{2,20}-USDT-SWAP$")
    validation_complete_at: datetime
    payload_readback_at: datetime
    committed_witness_observed_at: datetime
    available_at: datetime
    rows: tuple[PostPublicationMinuteV2, ...] = Field(min_length=1, max_length=3000)
    source_rows_sha256: Sha256
    availability_basis: Literal[
        "restricted_committed_witness_readback_server_clock_unverified"
    ] = "restricted_committed_witness_readback_server_clock_unverified"
    historical_observation_independently_replayable: Literal[False] = False
    trusted_clock_verified: Literal[False] = False
    independently_protected: Literal[False] = False
    evaluator_first_read_proven: Literal[False] = False
    current_claim: Literal[Gate3Claim.COMPUTATIONAL] = Gate3Claim.COMPUTATIONAL
    predictive_oos_eligible: Literal[False] = False
    promotion_eligible: Literal[False] = False
    runtime_consumers: Literal[0] = 0
    execution_authority: Literal[False] = False

    @field_validator(
        "validation_complete_at",
        "payload_readback_at",
        "committed_witness_observed_at",
        "available_at",
    )
    @classmethod
    def utc(cls, value: datetime, info) -> datetime:
        return require_utc(value, info.field_name)

    @model_validator(mode="after")
    def causal_order(self) -> PostPublicationCaptureV2:
        if not (
            self.validation_complete_at
            <= self.payload_readback_at
            <= self.committed_witness_observed_at
            == self.available_at
        ):
            raise ValueError("post-publication availability ordering invalid")
        if any(row.bar.closed_at > self.validation_complete_at for row in self.rows):
            raise ValueError("source minute closes after capture validation")
        if self.source_rows_sha256 != _rows_sha256(self.rows):
            raise ValueError("post-publication source row digest differs")
        if len({row.source_row_identity for row in self.rows}) != len(self.rows):
            raise ValueError("post-publication source identity duplicated")
        if any(
            later.bar.closed_at <= earlier.bar.closed_at
            for earlier, later in zip(self.rows, self.rows[1:], strict=False)
        ):
            raise ValueError("post-publication minutes are not increasing")
        return self

    def point_in_time_rows(self) -> tuple[PointInTimeBar, ...]:
        """Offline rows using the conservative observed time, never V1 time."""
        return tuple(
            PointInTimeBar(
                source_row_id="okx-public-minute:" + row.source_row_identity,
                source_row_sha256=row.source_row_sha256,
                instrument_id=self.instrument_id,
                available_at=self.available_at,
                bar=row.bar,
            )
            for row in self.rows
        )


def _rows_sha256(rows: tuple[PostPublicationMinuteV2, ...]) -> str:
    digest = hashlib.sha256()
    for row in rows:
        data = row.canonical_json_bytes()
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return digest.hexdigest()


def _pins_valid(
    *,
    capture_id: str,
    expected_plan_sha256: str,
    expected_receipt_sha256: str,
    expected_journal_genesis_sha256: str,
    expected_capture_checkpoint_sha256: str,
    expected_witness_revision: int,
    expected_witness_record_sha256: str,
) -> bool:
    return (
        type(capture_id) is str
        and _CAPTURE_ID.fullmatch(capture_id) is not None
        and all(
            type(value) is str and _SHA256.fullmatch(value) is not None
            for value in (
                expected_plan_sha256,
                expected_receipt_sha256,
                expected_journal_genesis_sha256,
                expected_capture_checkpoint_sha256,
                expected_witness_record_sha256,
            )
        )
        and type(expected_witness_revision) is int
        and 3 <= expected_witness_revision <= 8192
    )


async def _rebuild(
    *,
    journal: ControlledPublicReceiptJournal,
    witness_repository: PublicReceiptWitnessRepository,
    capture_id: str,
    expected_plan_sha256: str,
    expected_receipt_sha256: str,
    expected_journal_genesis_sha256: str,
    expected_capture_checkpoint_sha256: str,
    expected_witness_revision: int,
    expected_witness_record_sha256: str,
) -> PostPublicationCaptureV2:
    if (
        type(journal) is not ControlledPublicReceiptJournal
        or type(witness_repository) is not PublicReceiptWitnessRepository
        or not _pins_valid(
            capture_id=capture_id,
            expected_plan_sha256=expected_plan_sha256,
            expected_receipt_sha256=expected_receipt_sha256,
            expected_journal_genesis_sha256=expected_journal_genesis_sha256,
            expected_capture_checkpoint_sha256=expected_capture_checkpoint_sha256,
            expected_witness_revision=expected_witness_revision,
            expected_witness_record_sha256=expected_witness_record_sha256,
        )
    ):
        raise PostPublicationAvailabilityError("post_publication_inputs_invalid")
    try:
        current_checkpoint = journal.checkpoint
        if current_checkpoint.genesis_sha256 != expected_journal_genesis_sha256:
            raise PostPublicationAvailabilityError("journal_genesis_pin_mismatch")
        entries, first_rows = journal.read_all()
        matching = [
            (index, entry)
            for index, entry in enumerate(entries, start=1)
            if entry[1].capture_id == capture_id
        ]
        if len(matching) != 1:
            raise PostPublicationAvailabilityError("capture_identity_unavailable")
        sequence, (plan, receipt, raw_files, entry) = matching[0]
        replayed_plan, replayed_receipt = replay_public_capture(receipt, raw_files)
        if (
            replayed_plan.canonical_bytes() != plan.canonical_bytes()
            or replayed_receipt.canonical_bytes() != receipt.canonical_bytes()
            or plan.canonical_sha256() != expected_plan_sha256
            or receipt.canonical_sha256() != expected_receipt_sha256
            or entry["disposition"] != "accepted"
            or entry["sequence"] != sequence
            or receipt.attempt_sha256 is None
        ):
            raise PostPublicationAvailabilityError("capture_source_pin_mismatch")
        if any(
            first_rows.get(locator["identity"], (None, None))[1] != sequence - 1
            for locator in receipt.rows
        ):
            raise PostPublicationAvailabilityError("capture_not_first_observation")
        minute_rows = measured_public_minutes(
            journal=journal,
            capture_id=capture_id,
            expected_receipt_sha256=expected_receipt_sha256,
            expected_plan_sha256=expected_plan_sha256,
        )
        # This repository method opens a fresh read-only session. It replays
        # the committed chain before sampling PostgreSQL's server clock.
        observed = await witness_repository.observe_committed_revision(
            expected_journal_genesis_sha256, expected_witness_revision
        )
        if type(observed) is not CommittedWitnessObservation:
            raise PostPublicationAvailabilityError("witness_observation_invalid")
        witness = observed.revision
        target_checkpoint = witness.checkpoint
        if (
            witness.transition != "append_capture"
            or witness.state != "idle"
            or witness.attempt_outcome != "completed_collection"
            or witness.plan_sha256 != expected_plan_sha256
            or witness.record_sha256 != expected_witness_record_sha256
            or witness.revision != expected_witness_revision
            or witness.journal_key != expected_journal_genesis_sha256
            or target_checkpoint.genesis_sha256 != expected_journal_genesis_sha256
            or target_checkpoint.canonical_sha256()
            != expected_capture_checkpoint_sha256
            or witness.checkpoint_sha256 != expected_capture_checkpoint_sha256
            or target_checkpoint.sequence != sequence
            or target_checkpoint.head_sha256 != sha(canonical(entry))
            or target_checkpoint.attempt_sequence < 1
            or target_checkpoint.attempt_head_sha256 is None
            or (target_checkpoint.root_device, target_checkpoint.root_inode)
            != (current_checkpoint.root_device, current_checkpoint.root_inode)
            or journal.checkpoint != current_checkpoint
            or observed.trusted_clock_verified is not False
            or observed.independently_protected is not False
            or observed.predictive_oos_eligible is not False
            or observed.execution_authority is not False
        ):
            raise PostPublicationAvailabilityError("committed_witness_pin_mismatch")
        validation_at = utc_from_ns(receipt.validation_complete["utc_ns"])
        payload_at = utc_from_ns(entry["payload_readback_complete"]["utc_ns"])
        if (
            type(observed.observed_at) is not datetime
            or observed.observed_at.tzinfo is None
            or observed.observed_at.utcoffset().total_seconds() != 0
            or observed.observed_at < payload_at
        ):
            raise PostPublicationAvailabilityError("witness_clock_order_invalid")
        by_identity = {row["identity"]: row for row in receipt.rows}
        rows = tuple(
            PostPublicationMinuteV2(
                source_row_identity=source.row.source_row_id.removeprefix(
                    "okx-public-minute:"
                ),
                source_row_sha256=source.row.source_row_sha256,
                raw_page_sha256=by_identity[
                    source.row.source_row_id.removeprefix("okx-public-minute:")
                ]["raw_sha256"],
                bar=source.row.bar,
            )
            for source in minute_rows
        )
        return PostPublicationCaptureV2(
            capture_id=capture_id,
            plan_sha256=expected_plan_sha256,
            receipt_sha256=expected_receipt_sha256,
            journal_genesis_sha256=expected_journal_genesis_sha256,
            capture_checkpoint_sha256=expected_capture_checkpoint_sha256,
            capture_sequence=sequence,
            witness_revision=expected_witness_revision,
            witness_record_sha256=expected_witness_record_sha256,
            instrument_id=plan.instrument_id,
            validation_complete_at=validation_at,
            payload_readback_at=payload_at,
            committed_witness_observed_at=observed.observed_at,
            available_at=observed.observed_at,
            rows=rows,
            source_rows_sha256=_rows_sha256(rows),
        )
    except PostPublicationAvailabilityError:
        raise
    except Exception:  # noqa: BLE001 - Source/DB details must not enter evidence.
        raise PostPublicationAvailabilityError(
            "post_publication_replay_failed"
        ) from None


async def freeze_post_publication_capture_v2(
    **kwargs,
) -> FrozenGate3Artifact[PostPublicationCaptureV2]:
    """Bind one already-published capture to committed witness readback."""
    return _freeze(await _rebuild(**kwargs), contract_type=PostPublicationCaptureV2)


async def verify_post_publication_capture_v2(
    payload: bytes,
    *,
    expected_sha256: str,
    **kwargs,
) -> PostPublicationCaptureV2:
    """Recheck source/witness identity; original server sample is not replayable."""
    recorded = _verify(
        payload,
        expected_sha256=expected_sha256,
        contract_type=PostPublicationCaptureV2,
    )
    fresh = await _rebuild(**kwargs)
    recorded_fields = recorded.model_dump(mode="python")
    fresh_fields = fresh.model_dump(mode="python")
    recorded_fields.pop("committed_witness_observed_at")
    recorded_fields.pop("available_at")
    fresh_fields.pop("committed_witness_observed_at")
    fresh_fields.pop("available_at")
    if (
        recorded_fields != fresh_fields
        or recorded.committed_witness_observed_at > fresh.committed_witness_observed_at
    ):
        raise ArtifactVerificationError(
            "post-publication source or witness differs from recorded artifact"
        )
    return recorded


__all__ = (
    "PostPublicationAvailabilityError",
    "PostPublicationCaptureV2",
    "PostPublicationMinuteV2",
    "freeze_post_publication_capture_v2",
    "verify_post_publication_capture_v2",
)
