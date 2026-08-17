from __future__ import annotations

import csv
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Literal
import zipfile

from pydantic import Field, field_validator, model_validator

from app.research.external_benchmarks.archive import require_safe_zip_archive
from app.research.external_benchmarks.artifacts import sha256_file
from app.research.external_benchmarks.binance import (
    BinanceKlineCoordinates,
    BinancePublicArtifactIdentity,
)
from app.research.external_benchmarks.contracts import (
    DatasetArtifact,
    DatasetKind,
    DatasetQualityReport,
    DatasetWindow,
    ExternalArtifactAcquisitionReceipt,
    ExternalArtifactAcquisitionRequest,
    ExternalDatasetManifest,
    IntendedUse,
    LicenseStatus,
    ReferenceContract,
    RevisionPolicy,
    SourceKind,
    TimestampEncoding,
    require_utc,
)
from app.research.external_benchmarks.quality import profile_dataset_records


KLINE_FIELDS = (
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time",
    "quote_volume",
    "trade_count",
    "taker_buy_volume",
    "taker_buy_quote_volume",
    "ignore",
)
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
EXPECTED_ROWS_1M = 1440
INTERVAL_MILLISECONDS = 60_000
MAX_CSV_BYTES = 4 * 1024 * 1024


class BinanceKlineValidationError(RuntimeError):
    pass


class BinanceKlineQualityReport(ReferenceContract):
    dataset_id: str = Field(min_length=3, max_length=160)
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    generic_quality_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    generated_at: datetime
    expected_row_count: int = Field(ge=1)
    observed_row_count: int = Field(ge=0)
    header_present: bool
    invalid_schema_rows: int = Field(ge=0)
    invalid_numeric_rows: int = Field(ge=0)
    duplicate_open_time_rows: int = Field(ge=0)
    interval_mismatch_rows: int = Field(ge=0)
    invalid_close_time_rows: int = Field(ge=0)
    invalid_ohlc_rows: int = Field(ge=0)
    invalid_volume_rows: int = Field(ge=0)
    invalid_trade_count_rows: int = Field(ge=0)
    invalid_taker_volume_rows: int = Field(ge=0)
    passed: bool
    failure_codes: tuple[str, ...] = ()
    reference_only: Literal[True] = True
    promotion_eligible: Literal[False] = False
    execution_authority: Literal[False] = False

    @field_validator("generated_at")
    @classmethod
    def validate_generated_at(cls, value: datetime) -> datetime:
        return require_utc(value, "generated_at")

    @field_validator("failure_codes")
    @classmethod
    def validate_failure_codes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)) or any(not item for item in value):
            raise ValueError("Binance quality failure codes must be unique")
        return value

    @model_validator(mode="after")
    def validate_report(self) -> "BinanceKlineQualityReport":
        if self.passed != (len(self.failure_codes) == 0):
            raise ValueError("Binance quality state must match failure codes")
        counts = (
            self.invalid_schema_rows,
            self.invalid_numeric_rows,
            self.duplicate_open_time_rows,
            self.interval_mismatch_rows,
            self.invalid_close_time_rows,
            self.invalid_ohlc_rows,
            self.invalid_volume_rows,
            self.invalid_trade_count_rows,
            self.invalid_taker_volume_rows,
        )
        if any(value > self.observed_row_count for value in counts):
            raise ValueError("Binance quality defects cannot exceed observed rows")
        return self


class BinanceKlineEvidence(ReferenceContract):
    identity_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    receipt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    generic_quality_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    binance_quality_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    passed: bool
    failure_codes: tuple[str, ...] = ()
    reference_only: Literal[True] = True
    promotion_eligible: Literal[False] = False
    execution_authority: Literal[False] = False

    @model_validator(mode="after")
    def validate_evidence(self) -> "BinanceKlineEvidence":
        if len(self.failure_codes) != len(set(self.failure_codes)) or any(
            not item for item in self.failure_codes
        ):
            raise ValueError("evidence failure codes must be unique")
        if self.passed != (len(self.failure_codes) == 0):
            raise ValueError("evidence state must match failure codes")
        return self


def _integer(value: str) -> int | None:
    try:
        if not value or value.strip() != value:
            return None
        return int(value)
    except ValueError:
        return None


def _decimal(value: str) -> Decimal | None:
    try:
        if not value or value.strip() != value:
            return None
        result = Decimal(value)
    except (InvalidOperation, ValueError):
        return None
    return result if result.is_finite() else None


def _read_rows(
    path: Path,
    coordinates: BinanceKlineCoordinates,
) -> tuple[list[list[str]], bool]:
    require_safe_zip_archive(path)
    try:
        with zipfile.ZipFile(path, "r") as archive:
            members = archive.infolist()
            if len(members) != 1 or members[0].filename != coordinates.member_filename:
                raise BinanceKlineValidationError(
                    "Binance kline ZIP must contain the one expected CSV member"
                )
            if members[0].file_size > MAX_CSV_BYTES:
                raise BinanceKlineValidationError(
                    "Binance kline CSV exceeds the provider parser limit"
                )
            raw = archive.read(members[0])
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        if isinstance(exc, BinanceKlineValidationError):
            raise
        raise BinanceKlineValidationError(
            "Binance kline ZIP could not be read"
        ) from exc
    if b"\x00" in raw:
        raise BinanceKlineValidationError("Binance kline CSV contains NUL bytes")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise BinanceKlineValidationError(
            "Binance kline CSV must be UTF-8"
        ) from exc
    rows = list(csv.reader(text.splitlines()))
    if not rows:
        raise BinanceKlineValidationError("Binance kline CSV is empty")
    header_present = tuple(rows[0]) == PROVIDER_HEADER
    if not header_present and not rows[0][0].isdigit():
        raise BinanceKlineValidationError(
            "Binance kline CSV header is outside the reviewed schema"
        )
    return (rows[1:] if header_present else rows), header_present


def profile_binance_kline_archive(
    coordinates: BinanceKlineCoordinates,
    identity: BinancePublicArtifactIdentity,
    request: ExternalArtifactAcquisitionRequest,
    receipt: ExternalArtifactAcquisitionReceipt,
    dataset_root: Path,
    *,
    generated_at: datetime,
) -> tuple[
    ExternalDatasetManifest,
    DatasetQualityReport,
    BinanceKlineQualityReport,
    BinanceKlineEvidence,
]:
    """Parse one pinned Binance ZIP in memory and emit immutable evidence."""

    generated_at = require_utc(generated_at, "generated_at")
    if identity.coordinates_sha256 != coordinates.canonical_sha256():
        raise BinanceKlineValidationError("identity coordinates do not match")
    if (
        str(identity.artifact_url) != coordinates.download_url
        or str(identity.checksum_url) != coordinates.checksum_url
    ):
        raise BinanceKlineValidationError(
            "identity URLs do not match the reviewed coordinates"
        )
    if (
        request.request_id != coordinates.request_id
        or request.source_id != identity.source_id
        or str(request.download_url) != coordinates.download_url
        or request.relative_path != coordinates.relative_path
    ):
        raise BinanceKlineValidationError("request coordinates do not match")
    if (
        receipt.request_sha256 != request.canonical_sha256()
        or receipt.request_id != request.request_id
        or receipt.source_id != request.source_id
    ):
        raise BinanceKlineValidationError("receipt request identity does not match")
    if (
        identity.terms_review_sha256 != request.terms_review_sha256
        or identity.observed_at != request.terms_reviewed_at
        or identity.artifact_sha256 != request.expected_sha256
        or receipt.sha256 != request.expected_sha256
        or identity.artifact_byte_size != request.expected_byte_size
        or receipt.byte_size != request.expected_byte_size
        or receipt.relative_path != request.relative_path
        or str(receipt.final_url) != str(request.download_url)
        or receipt.media_type not in request.expected_media_types
    ):
        raise BinanceKlineValidationError("artifact identities do not agree")
    root = dataset_root.resolve(strict=True)
    if not root.is_dir() or dataset_root.is_symlink():
        raise BinanceKlineValidationError("dataset root must be a real directory")
    path = root.joinpath(*request.relative_path.split("/"))
    try:
        resolved_path = path.resolve(strict=True)
    except OSError as exc:
        raise BinanceKlineValidationError("Binance artifact does not exist") from exc
    if (
        not resolved_path.is_file()
        or resolved_path.is_symlink()
        or not resolved_path.is_relative_to(root)
    ):
        raise BinanceKlineValidationError(
            "Binance artifact escaped the dataset root"
        )
    rows, header_present = _read_rows(resolved_path, coordinates)
    if resolved_path.stat().st_size != request.expected_byte_size:
        raise BinanceKlineValidationError("Binance artifact byte size changed")
    if sha256_file(resolved_path) != request.expected_sha256:
        raise BinanceKlineValidationError("Binance artifact SHA-256 changed")

    day_start = datetime.combine(
        coordinates.day,
        time.min,
        tzinfo=timezone.utc,
    )
    start_ms = int(day_start.timestamp() * 1000)
    expected_last_ms = start_ms + (EXPECTED_ROWS_1M - 1) * INTERVAL_MILLISECONDS
    normalized: list[dict[str, Any]] = []
    invalid_schema_rows = 0
    invalid_numeric_rows = 0
    duplicate_open_time_rows = 0
    interval_mismatch_rows = 0
    invalid_close_time_rows = 0
    invalid_ohlc_rows = 0
    invalid_volume_rows = 0
    invalid_trade_count_rows = 0
    invalid_taker_volume_rows = 0
    seen_open_times: set[int] = set()

    for index, row in enumerate(rows):
        if len(row) != len(KLINE_FIELDS):
            invalid_schema_rows += 1
            normalized.append({field: None for field in KLINE_FIELDS})
            continue
        open_time = _integer(row[0])
        open_price = _decimal(row[1])
        high_price = _decimal(row[2])
        low_price = _decimal(row[3])
        close_price = _decimal(row[4])
        volume = _decimal(row[5])
        close_time = _integer(row[6])
        quote_volume = _decimal(row[7])
        trade_count = _integer(row[8])
        taker_volume = _decimal(row[9])
        taker_quote_volume = _decimal(row[10])
        ignored_value = _decimal(row[11])
        values = (
            open_time,
            open_price,
            high_price,
            low_price,
            close_price,
            volume,
            close_time,
            quote_volume,
            trade_count,
            taker_volume,
            taker_quote_volume,
            ignored_value,
        )
        if any(value is None for value in values):
            invalid_numeric_rows += 1
        if open_time is not None:
            if open_time in seen_open_times:
                duplicate_open_time_rows += 1
            seen_open_times.add(open_time)
            if open_time != start_ms + index * INTERVAL_MILLISECONDS:
                interval_mismatch_rows += 1
        if open_time is None or close_time != open_time + INTERVAL_MILLISECONDS - 1:
            invalid_close_time_rows += 1
        if (
            open_price is None
            or high_price is None
            or low_price is None
            or close_price is None
            or min(open_price, high_price, low_price, close_price) <= 0
            or high_price < max(open_price, close_price)
            or low_price > min(open_price, close_price)
            or high_price < low_price
        ):
            invalid_ohlc_rows += 1
        if volume is None or quote_volume is None or volume < 0 or quote_volume < 0:
            invalid_volume_rows += 1
        if trade_count is None or trade_count < 0:
            invalid_trade_count_rows += 1
        if (
            taker_volume is None
            or taker_quote_volume is None
            or volume is None
            or quote_volume is None
            or taker_volume < 0
            or taker_quote_volume < 0
            or taker_volume > volume
            or taker_quote_volume > quote_volume
        ):
            invalid_taker_volume_rows += 1
        normalized.append(dict(zip(KLINE_FIELDS, row, strict=True)))

    manifest = ExternalDatasetManifest(
        dataset_id=coordinates.dataset_id,
        source_id="binance.public_data",
        source_kind=SourceKind.EXCHANGE,
        dataset_kind=DatasetKind.CANDLE,
        source_url=request.download_url,
        terms_url=request.terms_url,
        license_status=LicenseStatus.REVIEW_REQUIRED,
        revision_policy=RevisionPolicy.PROVIDER_CORRECTABLE,
        retrieved_at=receipt.retrieved_at,
        window=DatasetWindow(
            start=day_start,
            end=datetime.fromtimestamp(
                expected_last_ms / 1000,
                tz=timezone.utc,
            ),
            available_at=identity.provider_last_modified_at,
        ),
        timestamp_encoding=TimestampEncoding.UNIX_MILLISECONDS,
        timestamp_field="open_time",
        fields=KLINE_FIELDS,
        required_fields=KLINE_FIELDS,
        key_fields=("open_time",),
        positive_numeric_fields=("open", "high", "low", "close", "volume"),
        instrument_ids=(coordinates.instrument_id,),
        intended_uses=(
            IntendedUse.DATA_QUALITY_REFERENCE,
            IntendedUse.RESEARCH_BASELINE,
        ),
        point_in_time_safe=True,
        row_count=EXPECTED_ROWS_1M,
        artifacts=(
            DatasetArtifact(
                relative_path=request.relative_path,
                sha256=request.expected_sha256,
                byte_size=request.expected_byte_size,
                row_count=EXPECTED_ROWS_1M,
                media_type=receipt.media_type,
            ),
        ),
    )
    generic_quality = profile_dataset_records(
        manifest,
        normalized,
        generated_at=generated_at,
    )
    failures = list(generic_quality.failure_codes)
    for count, code in (
        (invalid_schema_rows, "invalid_schema_rows"),
        (invalid_numeric_rows, "invalid_numeric_rows"),
        (duplicate_open_time_rows, "duplicate_open_time_rows"),
        (interval_mismatch_rows, "interval_mismatch_rows"),
        (invalid_close_time_rows, "invalid_close_time_rows"),
        (invalid_ohlc_rows, "invalid_ohlc_rows"),
        (invalid_volume_rows, "invalid_volume_rows"),
        (invalid_trade_count_rows, "invalid_trade_count_rows"),
        (invalid_taker_volume_rows, "invalid_taker_volume_rows"),
    ):
        if count and code not in failures:
            failures.append(code)
    binance_quality = BinanceKlineQualityReport(
        dataset_id=manifest.dataset_id,
        manifest_sha256=manifest.canonical_sha256(),
        generic_quality_sha256=generic_quality.canonical_sha256(),
        artifact_sha256=request.expected_sha256,
        generated_at=generated_at,
        expected_row_count=EXPECTED_ROWS_1M,
        observed_row_count=len(rows),
        header_present=header_present,
        invalid_schema_rows=invalid_schema_rows,
        invalid_numeric_rows=invalid_numeric_rows,
        duplicate_open_time_rows=duplicate_open_time_rows,
        interval_mismatch_rows=interval_mismatch_rows,
        invalid_close_time_rows=invalid_close_time_rows,
        invalid_ohlc_rows=invalid_ohlc_rows,
        invalid_volume_rows=invalid_volume_rows,
        invalid_trade_count_rows=invalid_trade_count_rows,
        invalid_taker_volume_rows=invalid_taker_volume_rows,
        passed=not failures,
        failure_codes=tuple(failures),
    )
    evidence = BinanceKlineEvidence(
        identity_sha256=identity.canonical_sha256(),
        request_sha256=request.canonical_sha256(),
        receipt_sha256=receipt.canonical_sha256(),
        manifest_sha256=manifest.canonical_sha256(),
        generic_quality_sha256=generic_quality.canonical_sha256(),
        binance_quality_sha256=binance_quality.canonical_sha256(),
        passed=binance_quality.passed,
        failure_codes=binance_quality.failure_codes,
    )
    return manifest, generic_quality, binance_quality, evidence
