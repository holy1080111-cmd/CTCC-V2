"""Pure bytes-only archive adapter for computational development rehearsal.

No filesystem, URL, transport, account, or holdout input is accepted. Archive
observation is conservatively available only at retrieval; this adapter never
substitutes bar close for a missing historical receipt.
"""

from __future__ import annotations

import csv
import hashlib
import re
import stat
import zipfile
import zlib
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from io import BytesIO
from typing import Literal

from pydantic import Field, ValidationError, field_validator, model_validator

from app.mie.features import FeatureBar
from app.mie.validation.availability import (
    ArchiveObservationReceipt,
    AvailabilityBasis,
    AvailabilityProvenance,
)
from app.mie.validation.contracts import Gate3Claim, Gate3Contract, Sha256
from app.mie.validation.replay import PointInTimeBar

MAX_ARCHIVE_BYTES = 1024 * 1024
MAX_CSV_BYTES = 4 * 1024 * 1024
MAX_ROW_BYTES = 2048
EXPECTED_ROWS = 1440
PROVIDER_HEADER = (
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time",
    "quote_volume",
    "count",
    "taker_buy_volume",
    "taker_buy_quote_volume",
    "ignore",
)
INTEGER_PATTERN = re.compile(r"[0-9]+\Z", flags=re.ASCII)
DECIMAL_PATTERN = re.compile(r"[0-9]+(?:\.[0-9]+)?\Z", flags=re.ASCII)
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class ArchiveReplayValidationError(ValueError):
    """Archive bytes or their observation/provenance chain failed validation."""


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _integer(value: str) -> int:
    if len(value) > 20 or INTEGER_PATTERN.fullmatch(value) is None:
        raise ArchiveReplayValidationError(
            "kline integer must use unsigned ASCII digits"
        )
    return int(value)


def _decimal(value: str) -> Decimal:
    if len(value) > 80 or DECIMAL_PATTERN.fullmatch(value) is None:
        raise ArchiveReplayValidationError(
            "kline decimal must use finite decimal notation"
        )
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise ArchiveReplayValidationError("invalid kline decimal") from exc
    if not result.is_finite():
        raise ArchiveReplayValidationError("kline decimal must be finite")
    return result


def _bar_from_raw_row(raw_row: str) -> FeatureBar:
    if len(raw_row.encode("utf-8")) > MAX_ROW_BYTES or any(
        ord(character) < 32 or ord(character) > 126 for character in raw_row
    ):
        raise ArchiveReplayValidationError("kline row contains unsupported bytes")
    try:
        fields = next(csv.reader([raw_row], strict=True))
    except (csv.Error, StopIteration) as exc:
        raise ArchiveReplayValidationError("invalid single-line kline CSV") from exc
    if len(fields) != 12:
        raise ArchiveReplayValidationError(
            "kline CSV must contain exactly twelve fields"
        )
    opened_ms = _integer(fields[0])
    closed_ms = _integer(fields[6])
    if closed_ms != opened_ms + 59_999 or opened_ms % 60_000:
        raise ArchiveReplayValidationError(
            "kline timestamps must be inclusive USD-M milliseconds"
        )
    open_price, high, low, close, volume = tuple(_decimal(item) for item in fields[1:6])
    quote_volume = _decimal(fields[7])
    _integer(fields[8])
    taker_volume = _decimal(fields[9])
    taker_quote_volume = _decimal(fields[10])
    _decimal(fields[11])
    if taker_volume > volume or taker_quote_volume > quote_volume:
        raise ArchiveReplayValidationError("kline taker volume exceeds total volume")
    try:
        return FeatureBar(
            closed_at=EPOCH + timedelta(milliseconds=opened_ms + 60_000),
            open=open_price,
            high=high,
            low=low,
            close=close,
            volume=volume,
        )
    except (OverflowError, ValidationError) as exc:
        raise ArchiveReplayValidationError(
            "invalid kline time or OHLCV geometry"
        ) from exc


class ArchiveRowProvenance(Gate3Contract):
    """Canonical fields prevent concatenation ambiguity in source row hashes."""

    archive_sha256: Sha256
    receipt_sha256: Sha256
    member_name: str = Field(min_length=1, max_length=80)
    row_ordinal: int = Field(ge=1, le=EXPECTED_ROWS)
    raw_row_sha256: Sha256
    raw_row_hash_semantics: Literal["utf8_row_without_line_terminator"] = (
        "utf8_row_without_line_terminator"
    )


class ArchiveReplayRow(Gate3Contract):
    provenance: ArchiveRowProvenance
    raw_row: str = Field(min_length=1, max_length=MAX_ROW_BYTES)
    availability: AvailabilityProvenance
    bar: FeatureBar

    @field_validator("bar", mode="before")
    @classmethod
    def validate_strict_bar(cls, value, info):
        # FeatureBar predates this strict boundary and permits coercion. Recheck
        # Python payload types so nested model_copy edits cannot exploit that.
        if info.mode == "python":
            payload = (
                value.model_dump(mode="python")
                if isinstance(value, FeatureBar)
                else value
            )
            if not isinstance(payload, dict) or any(
                type(payload.get(field)) is not Decimal
                for field in ("open", "high", "low", "close", "volume")
            ):
                raise ValueError(
                    "archive bar prices and volume require exact Decimal values"
                )
            if type(payload.get("closed_at")) is not datetime:
                raise ValueError("archive bar time requires a datetime")
            if "confirmed" in payload and type(payload["confirmed"]) is not bool:
                raise ValueError("archive confirmation requires a boolean")
        return value

    @model_validator(mode="after")
    def validate_links(self) -> ArchiveReplayRow:
        if _sha256(self.raw_row.encode("utf-8")) != self.provenance.raw_row_sha256:
            raise ValueError("raw row content hash mismatch")
        if _bar_from_raw_row(self.raw_row) != self.bar:
            raise ValueError("derived bar disagrees with the raw source row")
        if self.availability.source_row_sha256 != self.provenance.canonical_sha256():
            raise ValueError("row availability does not bind its source provenance")
        if self.availability.receipt_sha256 != self.provenance.receipt_sha256:
            raise ValueError("row receipt hash mismatch")
        if self.availability.bar_closed_at != self.bar.closed_at:
            raise ValueError("row availability event time mismatch")
        return self


class ArchiveReplayDataset(Gate3Contract):
    schema_version: Literal["ctcc.mie.gate3.archive_rehearsal.v1"] = (
        "ctcc.mie.gate3.archive_rehearsal.v1"
    )
    receipt: ArchiveObservationReceipt
    receipt_sha256: Sha256
    rows: tuple[ArchiveReplayRow, ...] = Field(
        min_length=EXPECTED_ROWS, max_length=EXPECTED_ROWS
    )
    current_claim: Literal[Gate3Claim.COMPUTATIONAL] = Gate3Claim.COMPUTATIONAL
    predictive_oos_eligible: Literal[False] = False
    point_in_time_provenance: Literal[False] = False
    runtime_consumers: Literal[0] = 0
    execution_authority: Literal[False] = False

    @model_validator(mode="after")
    def validate_chain(self) -> ArchiveReplayDataset:
        receipt = self.receipt
        if receipt.canonical_sha256() != self.receipt_sha256:
            raise ValueError("archive observation receipt hash mismatch")
        for ordinal, row in enumerate(self.rows, start=1):
            provenance = row.provenance
            availability = row.availability
            if (
                provenance.archive_sha256 != receipt.archive_sha256
                or provenance.member_name != receipt.member_name
                or provenance.receipt_sha256 != self.receipt_sha256
                or provenance.row_ordinal != ordinal
            ):
                raise ValueError(
                    "archive row identity does not match the receipt/order"
                )
            expected_close = receipt.day_start_at + timedelta(minutes=ordinal)
            if row.bar.closed_at != expected_close:
                raise ValueError(
                    "archive rows must cover the entire declared day in order"
                )
            if (
                availability.basis != AvailabilityBasis.ARCHIVE_OBSERVATION
                or availability.observed_at != receipt.observed_at
                or availability.retrieved_at != receipt.retrieved_at
                or availability.available_at != receipt.retrieved_at
            ):
                raise ValueError(
                    "archive rows require conservative receipt availability"
                )
        return self


def _read_csv_bytes(archive_bytes: bytes, member_name: str) -> bytes:
    try:
        with zipfile.ZipFile(BytesIO(archive_bytes), "r") as archive:
            members = archive.infolist()
            if len(members) != 1:
                raise ArchiveReplayValidationError(
                    "archive must contain one CSV member"
                )
            member = members[0]
            mode = stat.S_IFMT(member.external_attr >> 16)
            if (
                member.filename != member_name
                or member.orig_filename != member_name
                or member.is_dir()
                or mode not in {0, stat.S_IFREG}
                or member.flag_bits & 1
                or member.compress_type
                not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
            ):
                raise ArchiveReplayValidationError(
                    "archive member is unsafe or unexpected"
                )
            if (
                member.file_size < 1
                or member.file_size > MAX_CSV_BYTES
                or member.compress_size < 1
                or member.file_size > member.compress_size * 20
            ):
                raise ArchiveReplayValidationError(
                    "archive member exceeds safe size limits"
                )
            with archive.open(member, "r") as stream:
                payload = stream.read(MAX_CSV_BYTES + 1)
            if len(payload) != member.file_size or len(payload) > MAX_CSV_BYTES:
                raise ArchiveReplayValidationError("archive CSV size mismatch")
            return payload
    except (
        OSError,
        RuntimeError,
        EOFError,
        zlib.error,
        zipfile.BadZipFile,
        zipfile.LargeZipFile,
    ) as exc:
        raise ArchiveReplayValidationError(
            "invalid archive structure, compression, or CRC"
        ) from exc


def load_binance_archive_rehearsal(
    archive_bytes: bytes,
    *,
    receipt: ArchiveObservationReceipt,
    receipt_sha256: str,
) -> ArchiveReplayDataset:
    """Validate supplied bytes and bind complete daily rows to observation time.

    This function performs no I/O. A matching receipt is an attestation, not
    proof of historical row-level receipt or provider publication history.
    """

    try:
        if (
            type(archive_bytes) is not bytes
            or not 0 < len(archive_bytes) <= MAX_ARCHIVE_BYTES
        ):
            raise ArchiveReplayValidationError("archive input must be bounded bytes")
        if type(receipt) is not ArchiveObservationReceipt:
            raise ArchiveReplayValidationError(
                "archive receipt must use the exact contract"
            )
        observation = ArchiveObservationReceipt.model_validate(
            receipt.model_dump(mode="python")
        )
        if observation.canonical_sha256() != receipt_sha256:
            raise ArchiveReplayValidationError("supplied receipt SHA-256 mismatch")
        if (
            len(archive_bytes) != observation.archive_byte_size
            or _sha256(archive_bytes) != observation.archive_sha256
        ):
            raise ArchiveReplayValidationError("archive bytes do not match the receipt")
        payload = _read_csv_bytes(archive_bytes, observation.member_name)
        try:
            text = payload.decode("utf-8-sig").replace("\r\n", "\n")
        except UnicodeDecodeError as exc:
            raise ArchiveReplayValidationError("archive CSV must be UTF-8") from exc
        lines = text.removesuffix("\n").split("\n")
        if lines and lines[0] == ",".join(PROVIDER_HEADER):
            lines = lines[1:]
        if len(lines) != EXPECTED_ROWS:
            raise ArchiveReplayValidationError(
                "archive must contain exactly 1440 data rows"
            )
        rows: list[ArchiveReplayRow] = []
        for ordinal, raw_row in enumerate(lines, start=1):
            bar = _bar_from_raw_row(raw_row)
            provenance = ArchiveRowProvenance(
                archive_sha256=observation.archive_sha256,
                receipt_sha256=receipt_sha256,
                member_name=observation.member_name,
                row_ordinal=ordinal,
                raw_row_sha256=_sha256(raw_row.encode("utf-8")),
            )
            availability = AvailabilityProvenance(
                basis=AvailabilityBasis.ARCHIVE_OBSERVATION,
                source_row_sha256=provenance.canonical_sha256(),
                receipt_sha256=receipt_sha256,
                bar_closed_at=bar.closed_at,
                observed_at=observation.observed_at,
                retrieved_at=observation.retrieved_at,
                available_at=observation.retrieved_at,
            )
            rows.append(
                ArchiveReplayRow(
                    provenance=provenance,
                    raw_row=raw_row,
                    availability=availability,
                    bar=bar,
                )
            )
        return ArchiveReplayDataset(
            receipt=observation, receipt_sha256=receipt_sha256, rows=tuple(rows)
        )
    except (ValidationError, TypeError, AttributeError) as exc:
        raise ArchiveReplayValidationError(
            "archive observation or row chain is invalid"
        ) from exc


def validate_archive_replay_source(
    dataset: ArchiveReplayDataset,
    *,
    archive_bytes: bytes,
) -> ArchiveReplayDataset:
    """Rebuild from the original archive; self-consistent row hashes are insufficient.

    Dataset validation checks structural consistency. Only rebuilding from
    hash-verified bytes establishes that every declared row belongs to the
    archive named by the supplied observation receipt.
    """

    try:
        if type(dataset) is not ArchiveReplayDataset:
            raise ArchiveReplayValidationError(
                "archive dataset must use the exact contract"
            )
        validated = ArchiveReplayDataset.model_validate(
            dataset.model_dump(mode="python")
        )
        rebuilt = load_binance_archive_rehearsal(
            archive_bytes,
            receipt=validated.receipt,
            receipt_sha256=validated.receipt_sha256,
        )
        if validated.canonical_json_bytes() != rebuilt.canonical_json_bytes():
            raise ArchiveReplayValidationError(
                "archive dataset rows do not match the original source bytes"
            )
        return rebuilt
    except (ValidationError, TypeError, AttributeError) as exc:
        raise ArchiveReplayValidationError(
            "archive source failed provenance revalidation"
        ) from exc


def conservative_archive_rows(
    dataset: ArchiveReplayDataset,
    *,
    archive_bytes: bytes,
) -> tuple[PointInTimeBar, ...]:
    """Materialize source-verified bars with their actual retrieval cutoff.

    Original bytes are mandatory on every conversion. Assumed-close and
    measured-row attestations cannot pass this archive-only boundary.
    """

    validated = validate_archive_replay_source(dataset, archive_bytes=archive_bytes)
    return tuple(
        PointInTimeBar(
            source_row_id=f"archive:{validated.receipt_sha256}:row:{row.provenance.row_ordinal}",
            source_row_sha256=row.provenance.canonical_sha256(),
            instrument_id=validated.receipt.instrument_id,
            available_at=validated.receipt.retrieved_at,
            bar=row.bar,
        )
        for row in validated.rows
    )
