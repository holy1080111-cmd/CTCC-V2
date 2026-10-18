"""Source-replayed, DENY-only admission audit for historical archive rows.

An archive observed after its event can support a computational rehearsal, but
its hash and a caller's ``point_in_time_provenance=True`` cannot establish when
each row was available to a historical decision. This audit reconstructs the
original ZIP members before comparing any submitted research rows. It has no
candidate-seal, evaluator, network, or exchange authority.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from app.mie.validation.archive_batch import ArchiveBatchManifest
from app.mie.validation.availability import AvailabilityBasis
from app.mie.validation.batch_replay import (
    MAX_MINUTES,
    BoundMinute,
    archive_batch_minutes,
    minute_source_sha256,
)
from app.mie.validation.contracts import (
    DatasetIdentity,
    DatasetPartition,
    Gate3Claim,
    Gate3Contract,
    Sha256,
    require_utc,
)

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_Reason = Literal[
    "archive_observation_is_not_historical_row_receipt",
    "caller_pit_assertion_is_not_source_proof",
    "dataset_identity_differs_from_source",
    "duplicate_submitted_row_identity",
    "historical_partition_cutoff_precedes_receipt",
    "independent_pin_custody_unverified",
    "submitted_assumed_bar_close",
    "submitted_rows_differ_from_source",
]


class PITDatasetAdmissionError(ValueError):
    """Original source, retained pin, or submitted shape cannot be replayed."""


class ArchivePITDatasetAdmissionV1(Gate3Contract):
    """A source-bound denial receipt, never a predictive eligibility token."""

    schema_version: Literal["ctcc.mie.gate3.archive_pit_admission.v1"] = (
        "ctcc.mie.gate3.archive_pit_admission.v1"
    )
    plan_sha256: Sha256
    manifest_sha256: Sha256
    source_minutes_sha256: Sha256
    submitted_minutes_sha256: Sha256 | None
    claimed_dataset_sha256: Sha256
    partition: Literal[DatasetPartition.DEVELOPMENT, DatasetPartition.VALIDATION]
    instrument_id: str = Field(pattern=r"^[A-Z0-9]+(?:-[A-Z0-9]+)+$")
    partition_cutoff_at: datetime
    expected_rows: int = Field(ge=1, le=MAX_MINUTES)
    submitted_rows: int = Field(ge=0, le=MAX_MINUTES)
    late_source_rows: int = Field(ge=0, le=MAX_MINUTES)
    reasons: tuple[_Reason, ...] = Field(min_length=2)
    pin_custody: Literal["caller_supplied_unverified"] = "caller_supplied_unverified"
    status: Literal["DENY"] = "DENY"
    current_claim: Literal[Gate3Claim.COMPUTATIONAL] = Gate3Claim.COMPUTATIONAL
    point_in_time_provenance_qualified: Literal[False] = False
    candidate_seal_eligible: Literal[False] = False
    formal_evaluation_eligible: Literal[False] = False
    predictive_oos_eligible: Literal[False] = False
    runtime_consumers: Literal[0] = 0
    execution_authority: Literal[False] = False

    @model_validator(mode="after")
    def deny_is_consistent(self) -> ArchivePITDatasetAdmissionV1:
        require_utc(self.partition_cutoff_at, "partition_cutoff_at")
        if self.reasons != tuple(sorted(set(self.reasons))):
            raise ValueError("admission reasons must be unique and sorted")
        if not {
            "archive_observation_is_not_historical_row_receipt",
            "independent_pin_custody_unverified",
        }.issubset(self.reasons):
            raise ValueError("archive admission must preserve its permanent blockers")
        if self.late_source_rows > self.expected_rows:
            raise ValueError("late source count exceeds the source row count")
        if (self.submitted_rows == 0) != (self.submitted_minutes_sha256 is None):
            raise ValueError("submitted source digest disagrees with row count")
        return self


def audit_archive_pit_dataset_admission(
    *,
    manifest: ArchiveBatchManifest,
    archive_bytes: tuple[bytes, ...],
    expected_plan_sha256: str,
    expected_manifest_sha256: str,
    partition: DatasetPartition,
    instrument_id: str,
    claimed_dataset: DatasetIdentity,
    submitted_minutes: tuple[BoundMinute, ...],
) -> ArchivePITDatasetAdmissionV1:
    """Rebuild every original archive before auditing one D/V research group.

    The two expected digests must be retained outside the submitted source.
    Their caller custody remains unverified, so even a perfect replay is DENY.
    The original, already exposed holdout is never accepted as a partition.
    """
    if (
        type(manifest) is not ArchiveBatchManifest
        or type(archive_bytes) is not tuple
        or type(claimed_dataset) is not DatasetIdentity
        or type(submitted_minutes) is not tuple
        or len(submitted_minutes) > MAX_MINUTES
        or type(expected_plan_sha256) is not str
        or type(expected_manifest_sha256) is not str
        or _SHA256.fullmatch(expected_plan_sha256) is None
        or _SHA256.fullmatch(expected_manifest_sha256) is None
    ):
        raise PITDatasetAdmissionError(
            "exact bounded source and retained pins required"
        )
    checked_manifest = ArchiveBatchManifest.model_validate(
        manifest.model_dump(mode="python")
    )
    if checked_manifest.canonical_sha256() != expected_manifest_sha256:
        raise PITDatasetAdmissionError("retained source manifest pin differs")
    checked_dataset = DatasetIdentity.model_validate(
        claimed_dataset.model_dump(mode="python")
    )
    submitted = []
    for minute in submitted_minutes:
        if type(minute) is not BoundMinute:
            raise PITDatasetAdmissionError("submitted minutes require exact contracts")
        submitted.append(BoundMinute.model_validate(minute.model_dump(mode="python")))
    checked_submitted = tuple(submitted)

    source_plan, source_minutes = archive_batch_minutes(
        checked_manifest,
        archive_bytes=archive_bytes,
        expected_plan_sha256=expected_plan_sha256,
        partition=partition,
        instrument_id=instrument_id,
    )
    source_sha = minute_source_sha256(source_minutes)
    submitted_sha = (
        minute_source_sha256(checked_submitted) if checked_submitted else None
    )
    cutoff = source_plan.window.end_at
    reasons: set[_Reason] = {
        "archive_observation_is_not_historical_row_receipt",
        "independent_pin_custody_unverified",
    }
    if checked_dataset.point_in_time_provenance:
        reasons.add("caller_pit_assertion_is_not_source_proof")
    if any(
        minute.availability.basis == AvailabilityBasis.ASSUMED_BAR_CLOSE
        for minute in checked_submitted
    ):
        reasons.add("submitted_assumed_bar_close")
    identities = tuple(minute.row.source_row_id for minute in checked_submitted)
    if len(identities) != len(set(identities)):
        reasons.add("duplicate_submitted_row_identity")
    if checked_submitted != source_minutes:
        reasons.add("submitted_rows_differ_from_source")
    late_rows = sum(minute.row.available_at > cutoff for minute in source_minutes)
    if late_rows:
        reasons.add("historical_partition_cutoff_precedes_receipt")
    if (
        checked_dataset.source != "binance.public_data"
        or checked_dataset.source_version != "futures_um.daily.klines.v1"
        or checked_dataset.instrument_ids != (instrument_id,)
        or checked_dataset.manifest_sha256 != expected_manifest_sha256
        or checked_dataset.content_sha256 != source_sha
        or checked_dataset.expected_rows != len(source_minutes)
        or checked_dataset.first_event_at != source_minutes[0].row.bar.closed_at
        or checked_dataset.last_event_at != source_minutes[-1].row.bar.closed_at
        or checked_dataset.event_time_field != "closed_at"
        or checked_dataset.available_time_field != "available_at"
        or checked_dataset.frozen_at
        < max(minute.row.available_at for minute in source_minutes)
    ):
        reasons.add("dataset_identity_differs_from_source")
    return ArchivePITDatasetAdmissionV1(
        plan_sha256=expected_plan_sha256,
        manifest_sha256=expected_manifest_sha256,
        source_minutes_sha256=source_sha,
        submitted_minutes_sha256=submitted_sha,
        claimed_dataset_sha256=checked_dataset.canonical_sha256(),
        partition=partition,
        instrument_id=instrument_id,
        partition_cutoff_at=cutoff,
        expected_rows=len(source_minutes),
        submitted_rows=len(checked_submitted),
        late_source_rows=late_rows,
        reasons=tuple(sorted(reasons)),
    )


def verify_archive_pit_dataset_admission(
    receipt: ArchivePITDatasetAdmissionV1,
    *,
    expected_receipt_sha256: str,
    manifest: ArchiveBatchManifest,
    archive_bytes: tuple[bytes, ...],
    expected_plan_sha256: str,
    expected_manifest_sha256: str,
    partition: DatasetPartition,
    instrument_id: str,
    claimed_dataset: DatasetIdentity,
    submitted_minutes: tuple[BoundMinute, ...],
) -> ArchivePITDatasetAdmissionV1:
    """Recompute a pinned denial receipt from the original ZIPs on readback."""
    if (
        type(receipt) is not ArchivePITDatasetAdmissionV1
        or type(expected_receipt_sha256) is not str
        or _SHA256.fullmatch(expected_receipt_sha256) is None
    ):
        raise PITDatasetAdmissionError("exact admission receipt and hash required")
    checked = ArchivePITDatasetAdmissionV1.model_validate(
        receipt.model_dump(mode="python")
    )
    if checked.canonical_sha256() != expected_receipt_sha256:
        raise PITDatasetAdmissionError("admission receipt pin changed")
    rebuilt = audit_archive_pit_dataset_admission(
        manifest=manifest,
        archive_bytes=archive_bytes,
        expected_plan_sha256=expected_plan_sha256,
        expected_manifest_sha256=expected_manifest_sha256,
        partition=partition,
        instrument_id=instrument_id,
        claimed_dataset=claimed_dataset,
        submitted_minutes=submitted_minutes,
    )
    if checked.canonical_json_bytes() != rebuilt.canonical_json_bytes():
        raise PITDatasetAdmissionError("admission receipt source readback differs")
    return rebuilt


__all__ = (
    "ArchivePITDatasetAdmissionV1",
    "PITDatasetAdmissionError",
    "audit_archive_pit_dataset_admission",
    "verify_archive_pit_dataset_admission",
)
