"""Bytes-only development/validation batches bound to a pinned calendar plan.

The manifest is computational evidence of input consistency, not independent
source authentication or proof of historical first-receipt availability.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from pydantic import Field, ValidationError, field_validator, model_validator

from app.mie.validation.archive_replay import (
    EXPECTED_ROWS,
    MAX_ARCHIVE_BYTES,
    ArchiveReplayValidationError,
    load_binance_archive_rehearsal,
)
from app.mie.validation.availability import ArchiveObservationReceipt
from app.mie.validation.contracts import (
    SHA256_PATTERN,
    DatasetPartition,
    Gate3Claim,
    Gate3Contract,
    Identifier,
    PurgedWalkForwardSplit,
    Sha256,
    require_utc,
)
from app.mie.validation.replay import PointInTimeBar

MAX_BATCH_ARTIFACTS = 256
ArchiveSymbol = Literal["BTCUSDT", "ETHUSDT"]


class ArchiveBatchValidationError(ValueError):
    """Plan, source coordinates, or original-byte reconstruction failed."""


class ArchiveBatchPlan(Gate3Contract):
    schema_version: Literal["ctcc.mie.gate3.archive_batch_plan.v1"] = (
        "ctcc.mie.gate3.archive_batch_plan.v1"
    )
    plan_id: Identifier
    created_at: datetime
    split: PurgedWalkForwardSplit
    symbols: tuple[ArchiveSymbol, ...] = Field(min_length=1, max_length=2)
    expected_artifact_count: int = Field(ge=2, le=MAX_BATCH_ARTIFACTS)
    expected_rows: int = Field(
        ge=2 * EXPECTED_ROWS, le=MAX_BATCH_ARTIFACTS * EXPECTED_ROWS
    )
    source: Literal["binance.public_data"] = "binance.public_data"
    market: Literal["futures_um"] = "futures_um"
    interval_seconds: Literal[60] = 60
    window_semantics: Literal["source_open_start_inclusive_end_exclusive"] = (
        "source_open_start_inclusive_end_exclusive"
    )
    current_claim: Literal[Gate3Claim.COMPUTATIONAL] = Gate3Claim.COMPUTATIONAL
    predictive_oos_eligible: Literal[False] = False
    runtime_consumers: Literal[0] = 0
    execution_authority: Literal[False] = False

    @field_validator("created_at")
    @classmethod
    def validate_created_at(cls, value: datetime) -> datetime:
        return require_utc(value, "created_at")

    @field_validator("symbols")
    @classmethod
    def validate_symbols(
        cls, value: tuple[ArchiveSymbol, ...]
    ) -> tuple[ArchiveSymbol, ...]:
        if value != tuple(sorted(set(value))):
            raise ValueError("batch symbols must be unique and canonically sorted")
        return value

    @model_validator(mode="after")
    def validate_plan(self) -> ArchiveBatchPlan:
        for window in (
            self.split.development,
            self.split.validation,
            self.split.holdout,
        ):
            for boundary in (window.start_at, window.end_at):
                if any(
                    (
                        boundary.hour,
                        boundary.minute,
                        boundary.second,
                        boundary.microsecond,
                    )
                ):
                    raise ValueError("batch boundaries must be exact UTC midnight")
        durations = (
            self.split.purge_seconds,
            self.split.embargo_seconds,
            self.split.max_feature_dependency_seconds,
            self.split.label_dependency_seconds,
        )
        if any(value % self.interval_seconds for value in durations):
            raise ValueError("batch dependency and gap durations must align to bars")
        days = sum(
            (window.end_at - window.start_at).days
            for window in (self.split.development, self.split.validation)
        )
        count = days * len(self.symbols)
        # Bound expansion before generating any per-day coordinate sequence.
        if count > MAX_BATCH_ARTIFACTS:
            raise ValueError("batch coordinates exceed the bounded artifact limit")
        if self.expected_artifact_count != count:
            raise ValueError("batch artifact count disagrees with calendar coordinates")
        if self.expected_rows != count * EXPECTED_ROWS:
            raise ValueError("batch row count disagrees with calendar coordinates")
        return self


def _expected_coordinates(plan: ArchiveBatchPlan) -> tuple[tuple, ...]:
    coordinates = []
    for window in (plan.split.development, plan.split.validation):
        day = window.start_at
        while day < window.end_at:
            for symbol in plan.symbols:
                coordinates.append(
                    (
                        window.partition,
                        symbol,
                        f"{symbol.removesuffix('USDT')}-USDT-SWAP",
                        day,
                        f"{symbol}-1m-{day.date().isoformat()}.csv",
                    )
                )
            day += timedelta(days=1)
    return tuple(coordinates)


def _receipt_coordinate(receipt: ArchiveObservationReceipt) -> tuple:
    return (
        receipt.partition,
        receipt.symbol,
        receipt.instrument_id,
        receipt.day_start_at,
        receipt.member_name,
    )


def _checked_plan(
    plan: ArchiveBatchPlan, expected_plan_sha256: str
) -> ArchiveBatchPlan:
    if type(plan) is not ArchiveBatchPlan:
        raise ArchiveBatchValidationError("batch plan must use the exact contract")
    validated = ArchiveBatchPlan.model_validate(plan.model_dump(mode="python"))
    if (
        not isinstance(expected_plan_sha256, str)
        or re.fullmatch(SHA256_PATTERN, expected_plan_sha256) is None
        or validated.canonical_sha256() != expected_plan_sha256
    ):
        raise ArchiveBatchValidationError("trusted batch plan SHA256 mismatch")
    return validated


@dataclass(frozen=True, slots=True)
class ArchiveBatchSource:
    archive_bytes: bytes
    receipt: ArchiveObservationReceipt
    receipt_sha256: str


class ArchiveBatchMember(Gate3Contract):
    receipt: ArchiveObservationReceipt
    receipt_sha256: Sha256
    dataset_sha256: Sha256

    @model_validator(mode="after")
    def validate_receipt(self) -> ArchiveBatchMember:
        if self.receipt_sha256 != self.receipt.canonical_sha256():
            raise ValueError("batch member receipt SHA256 mismatch")
        return self


class ArchiveBatchManifest(Gate3Contract):
    schema_version: Literal["ctcc.mie.gate3.archive_batch_manifest.v1"] = (
        "ctcc.mie.gate3.archive_batch_manifest.v1"
    )
    plan: ArchiveBatchPlan
    plan_sha256: Sha256
    members: tuple[ArchiveBatchMember, ...] = Field(
        min_length=2, max_length=MAX_BATCH_ARTIFACTS
    )
    total_rows: int = Field(
        ge=2 * EXPECTED_ROWS, le=MAX_BATCH_ARTIFACTS * EXPECTED_ROWS
    )
    total_archive_bytes: int = Field(ge=1, le=MAX_BATCH_ARTIFACTS * MAX_ARCHIVE_BYTES)
    current_claim: Literal[Gate3Claim.COMPUTATIONAL] = Gate3Claim.COMPUTATIONAL
    point_in_time_provenance: Literal[False] = False
    predictive_oos_eligible: Literal[False] = False
    runtime_consumers: Literal[0] = 0
    execution_authority: Literal[False] = False

    @model_validator(mode="after")
    def validate_manifest(self) -> ArchiveBatchManifest:
        if self.plan_sha256 != self.plan.canonical_sha256():
            raise ValueError("batch manifest plan SHA256 mismatch")
        if tuple(
            _receipt_coordinate(item.receipt) for item in self.members
        ) != _expected_coordinates(self.plan):
            raise ValueError(
                "batch members must exactly match ordered plan coordinates"
            )
        if len({item.receipt.archive_sha256 for item in self.members}) != len(
            self.members
        ):
            raise ValueError("batch archive identities must be unique")
        if self.total_rows != self.plan.expected_rows:
            raise ValueError("batch total rows disagree with the plan")
        if self.total_archive_bytes != sum(
            item.receipt.archive_byte_size for item in self.members
        ):
            raise ValueError("batch total archive bytes disagree with receipts")
        return self


def load_archive_batch_rehearsal(
    sources: tuple[ArchiveBatchSource, ...],
    *,
    plan: ArchiveBatchPlan,
    expected_plan_sha256: str,
) -> ArchiveBatchManifest:
    """Validate every coordinate before parsing any supplied ZIP, then rebuild.

    No path, URL, downloader, account, or holdout acquisition interface exists.
    The caller must retain the approved plan pin outside the supplied payload.
    """
    try:
        validated_plan = _checked_plan(plan, expected_plan_sha256)
        if (
            type(sources) is not tuple
            or len(sources) != validated_plan.expected_artifact_count
        ):
            raise ArchiveBatchValidationError("batch source count does not match plan")
        observations = []
        for source in sources:
            if (
                type(source) is not ArchiveBatchSource
                or type(source.receipt) is not ArchiveObservationReceipt
            ):
                raise ArchiveBatchValidationError(
                    "batch sources require exact source and receipt types"
                )
            receipt = ArchiveObservationReceipt.model_validate(
                source.receipt.model_dump(mode="python")
            )
            if receipt.canonical_sha256() != source.receipt_sha256:
                raise ArchiveBatchValidationError(
                    "batch supplied receipt SHA256 mismatch"
                )
            if (
                type(source.archive_bytes) is not bytes
                or not 0 < len(source.archive_bytes) <= MAX_ARCHIVE_BYTES
            ):
                raise ArchiveBatchValidationError(
                    "batch archive input must be bounded bytes"
                )
            observations.append(receipt)
        if tuple(
            _receipt_coordinate(item) for item in observations
        ) != _expected_coordinates(validated_plan):
            raise ArchiveBatchValidationError(
                "batch source coordinates do not match ordered plan"
            )

        members = []
        for source, receipt in zip(sources, observations, strict=True):
            dataset = load_binance_archive_rehearsal(
                source.archive_bytes,
                receipt=receipt,
                receipt_sha256=source.receipt_sha256,
            )
            members.append(
                ArchiveBatchMember(
                    receipt=receipt,
                    receipt_sha256=source.receipt_sha256,
                    dataset_sha256=dataset.canonical_sha256(),
                )
            )
        return ArchiveBatchManifest(
            plan=validated_plan,
            plan_sha256=expected_plan_sha256,
            members=tuple(members),
            total_rows=validated_plan.expected_rows,
            total_archive_bytes=sum(len(source.archive_bytes) for source in sources),
        )
    except (
        ValidationError,
        TypeError,
        AttributeError,
        ArchiveReplayValidationError,
    ) as exc:
        raise ArchiveBatchValidationError(
            "batch plan or source metadata failed revalidation"
        ) from exc


def validate_archive_batch_source(
    manifest: ArchiveBatchManifest,
    *,
    archive_bytes: tuple[bytes, ...],
    expected_plan_sha256: str,
) -> ArchiveBatchManifest:
    """Rebuild all member dataset hashes from original bytes on every use."""
    try:
        if type(manifest) is not ArchiveBatchManifest:
            raise ArchiveBatchValidationError(
                "batch manifest must use the exact contract"
            )
        validated = ArchiveBatchManifest.model_validate(
            manifest.model_dump(mode="python")
        )
        if type(archive_bytes) is not tuple or len(archive_bytes) != len(
            validated.members
        ):
            raise ArchiveBatchValidationError("batch original archive count mismatch")
        sources = tuple(
            ArchiveBatchSource(data, member.receipt, member.receipt_sha256)
            for data, member in zip(archive_bytes, validated.members, strict=True)
        )
        rebuilt = load_archive_batch_rehearsal(
            sources,
            plan=validated.plan,
            expected_plan_sha256=expected_plan_sha256,
        )
        if validated.canonical_json_bytes() != rebuilt.canonical_json_bytes():
            raise ArchiveBatchValidationError(
                "batch dataset hashes disagree with original ZIP bytes"
            )
        return rebuilt
    except (ValidationError, TypeError, AttributeError) as exc:
        raise ArchiveBatchValidationError(
            "batch manifest failed source revalidation"
        ) from exc


def conservative_batch_rows(
    manifest: ArchiveBatchManifest,
    *,
    archive_bytes: tuple[bytes, ...],
    expected_plan_sha256: str,
    partition: DatasetPartition,
    instrument_id: str,
) -> tuple[PointInTimeBar, ...]:
    """Return just one declared partition/instrument, preserving retrieval times."""
    if type(partition) is not DatasetPartition or partition not in (
        DatasetPartition.DEVELOPMENT,
        DatasetPartition.VALIDATION,
    ):
        raise ArchiveBatchValidationError(
            "batch conversion requires development or validation"
        )
    if type(manifest) is not ArchiveBatchManifest:
        raise ArchiveBatchValidationError("batch manifest must use the exact contract")
    plan = _checked_plan(manifest.plan, expected_plan_sha256)
    if type(instrument_id) is not str or instrument_id not in {
        f"{symbol.removesuffix('USDT')}-USDT-SWAP" for symbol in plan.symbols
    }:
        raise ArchiveBatchValidationError(
            "batch conversion requires one declared instrument"
        )
    validated = validate_archive_batch_source(
        manifest,
        archive_bytes=archive_bytes,
        expected_plan_sha256=expected_plan_sha256,
    )
    rows = []
    for data, member in zip(archive_bytes, validated.members, strict=True):
        receipt = member.receipt
        if receipt.partition != partition or receipt.instrument_id != instrument_id:
            continue
        dataset = load_binance_archive_rehearsal(
            data,
            receipt=receipt,
            receipt_sha256=member.receipt_sha256,
        )
        rows.extend(
            PointInTimeBar(
                source_row_id=f"archive:{member.receipt_sha256}:row:{row.provenance.row_ordinal}",
                source_row_sha256=row.provenance.canonical_sha256(),
                instrument_id=instrument_id,
                available_at=receipt.retrieved_at,
                bar=row.bar,
            )
            for row in dataset.rows
        )
    return tuple(rows)
