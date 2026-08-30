from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    field_validator,
    model_validator,
)



def require_utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware UTC")
    if value.utcoffset() != timedelta(0):
        raise ValueError(f"{field_name} must use UTC")
    return value


class ReferenceContract(BaseModel):
    """Strict immutable boundary for untrusted external reference material."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        allow_inf_nan=False,
        revalidate_instances="always",
        str_strip_whitespace=True,
    )

    def canonical_sha256(self) -> str:
        payload = json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


class SourceKind(StrEnum):
    EXCHANGE = "exchange"
    MARKET_INFRASTRUCTURE = "market_infrastructure"
    OPEN_SOURCE_ENGINE = "open_source_engine"
    ACADEMIC = "academic"
    REGULATORY = "regulatory"
    MACROECONOMIC = "macroeconomic"


class DatasetKind(StrEnum):
    TRADE = "trade"
    CANDLE = "candle"
    FUNDING = "funding"
    ORDER_BOOK = "order_book"
    ENGINE_REGRESSION = "engine_regression"
    MODEL_BENCHMARK = "model_benchmark"
    POSITIONING = "positioning"
    MACRO_VINTAGE = "macro_vintage"


class IntendedUse(StrEnum):
    DATA_QUALITY_REFERENCE = "data_quality_reference"
    EXECUTION_CALIBRATION = "execution_calibration"
    DETERMINISTIC_REPLAY = "deterministic_replay"
    METRIC_PARITY = "metric_parity"
    RESEARCH_BASELINE = "research_baseline"


class LicenseStatus(StrEnum):
    VERIFIED_PUBLIC_USE = "verified_public_use"
    REVIEW_REQUIRED = "review_required"
    RESTRICTED = "restricted"


class RevisionPolicy(StrEnum):
    IMMUTABLE = "immutable"
    PROVIDER_CORRECTABLE = "provider_correctable"
    PROVIDER_REVISABLE = "provider_revisable"


class TimestampEncoding(StrEnum):
    ISO_8601_UTC = "iso_8601_utc"
    UNIX_MILLISECONDS = "unix_milliseconds"
    UNIX_MICROSECONDS = "unix_microseconds"
    UNIX_NANOSECONDS = "unix_nanoseconds"


class ArchiveKind(StrEnum):
    NONE = "none"
    ZIP = "zip"


class AcquisitionStatus(StrEnum):
    DOWNLOADED = "downloaded"
    ALREADY_PRESENT = "already_present"


class DatasetWindow(ReferenceContract):
    start: datetime
    end: datetime
    available_at: datetime

    @field_validator("start", "end", "available_at")
    @classmethod
    def validate_timestamps(cls, value: datetime, info) -> datetime:
        return require_utc(value, info.field_name)

    @model_validator(mode="after")
    def validate_window(self) -> "DatasetWindow":
        if self.end < self.start:
            raise ValueError("dataset window end cannot precede start")
        if self.available_at < self.end:
            raise ValueError("dataset cannot be available before its final event")
        return self


class DatasetArtifact(ReferenceContract):
    relative_path: str = Field(min_length=1, max_length=240)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    byte_size: int = Field(ge=1)
    row_count: int = Field(ge=1)
    media_type: str = Field(min_length=3, max_length=100)

    @field_validator("relative_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        if "\\" in value:
            raise ValueError("artifact paths must use POSIX separators")
        path = PurePosixPath(value)
        if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            raise ValueError("artifact path must remain below its dataset root")
        return value


class ExternalDatasetManifest(ReferenceContract):
    """Frozen provenance for one public dataset import.

    A valid manifest proves identity and timing, not predictive value.
    """

    dataset_id: str = Field(
        min_length=3,
        max_length=160,
        pattern=r"^[a-z0-9]+(?:[._:-][a-z0-9]+)*$",
    )
    source_id: str = Field(
        min_length=3,
        max_length=160,
        pattern=r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$",
    )
    source_kind: SourceKind
    dataset_kind: DatasetKind
    source_url: HttpUrl
    terms_url: HttpUrl
    license_status: LicenseStatus
    revision_policy: RevisionPolicy
    retrieved_at: datetime
    window: DatasetWindow
    timezone: Literal["UTC"] = "UTC"
    timestamp_encoding: TimestampEncoding
    timestamp_field: str = Field(min_length=1, max_length=80)
    fields: tuple[str, ...] = Field(min_length=1)
    required_fields: tuple[str, ...] = Field(min_length=1)
    key_fields: tuple[str, ...] = Field(min_length=1)
    positive_numeric_fields: tuple[str, ...] = ()
    instrument_ids: tuple[str, ...] = ()
    intended_uses: tuple[IntendedUse, ...] = Field(min_length=1)
    point_in_time_safe: bool
    row_count: int = Field(ge=1)
    artifacts: tuple[DatasetArtifact, ...] = Field(min_length=1)
    reference_only: Literal[True] = True
    promotion_eligible: Literal[False] = False
    execution_authority: Literal[False] = False

    @field_validator("retrieved_at")
    @classmethod
    def validate_retrieved_at(cls, value: datetime) -> datetime:
        return require_utc(value, "retrieved_at")

    @field_validator("source_url", "terms_url")
    @classmethod
    def validate_https_url(cls, value: HttpUrl, info) -> HttpUrl:
        if value.scheme != "https":
            raise ValueError(f"{info.field_name} must use HTTPS")
        if value.username or value.password:
            raise ValueError(f"{info.field_name} cannot contain credentials")
        return value

    @field_validator(
        "fields",
        "required_fields",
        "key_fields",
        "positive_numeric_fields",
    )
    @classmethod
    def validate_field_names(cls, value: tuple[str, ...], info) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError(f"{info.field_name} must be unique")
        if any(not item or not item.replace("_", "").isalnum() for item in value):
            raise ValueError(f"{info.field_name} contains an invalid field name")
        return value

    @field_validator("instrument_ids")
    @classmethod
    def validate_instruments(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("instrument_ids must be unique")
        for item in value:
            if "-" not in item or len(item) < 3 or len(item) > 64 or not all(
                part.isalnum() for part in item.split("-")
            ):
                raise ValueError("instrument_ids contains an invalid instrument")
        return value

    @model_validator(mode="after")
    def validate_manifest(self) -> "ExternalDatasetManifest":
        if self.window.available_at > self.retrieved_at:
            raise ValueError("dataset cannot be retrieved before it was available")
        field_set = set(self.fields)
        for name, subset in (
            ("required_fields", self.required_fields),
            ("key_fields", self.key_fields),
            ("positive_numeric_fields", self.positive_numeric_fields),
        ):
            if not set(subset).issubset(field_set):
                raise ValueError(f"{name} must be contained in fields")
        if self.timestamp_field not in field_set:
            raise ValueError("timestamp_field must be contained in fields")
        if self.timestamp_field not in self.required_fields:
            raise ValueError("timestamp_field must be required")
        if any(field not in self.required_fields for field in self.key_fields):
            raise ValueError("key_fields must also be required")
        if sum(artifact.row_count for artifact in self.artifacts) != self.row_count:
            raise ValueError("artifact row counts must equal manifest row_count")
        paths = [artifact.relative_path for artifact in self.artifacts]
        if len(paths) != len(set(paths)):
            raise ValueError("artifact paths must be unique")
        if len(self.intended_uses) != len(set(self.intended_uses)):
            raise ValueError("intended_uses must be unique")
        return self


class DatasetQualityPolicy(ReferenceContract):
    minimum_rows: int = Field(default=2, ge=1)
    max_missing_required_rate: Decimal = Field(default=Decimal("0"), ge=0, le=1)
    max_duplicate_key_rate: Decimal = Field(default=Decimal("0"), ge=0, le=1)
    max_invalid_timestamp_rate: Decimal = Field(default=Decimal("0"), ge=0, le=1)
    max_out_of_order_rate: Decimal = Field(default=Decimal("0"), ge=0, le=1)
    max_out_of_window_rate: Decimal = Field(default=Decimal("0"), ge=0, le=1)
    max_nonpositive_numeric_rate: Decimal = Field(default=Decimal("0"), ge=0, le=1)


class DatasetQualityReport(ReferenceContract):
    dataset_id: str = Field(min_length=3, max_length=160)
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    generated_at: datetime
    declared_row_count: int = Field(ge=1)
    observed_row_count: int = Field(ge=0)
    missing_required_rows: int = Field(ge=0)
    duplicate_key_rows: int = Field(ge=0)
    invalid_timestamp_rows: int = Field(ge=0)
    out_of_order_rows: int = Field(ge=0)
    out_of_window_rows: int = Field(ge=0)
    nonpositive_numeric_rows: int = Field(ge=0)
    missing_required_rate: Decimal = Field(ge=0, le=1)
    duplicate_key_rate: Decimal = Field(ge=0, le=1)
    invalid_timestamp_rate: Decimal = Field(ge=0, le=1)
    out_of_order_rate: Decimal = Field(ge=0, le=1)
    out_of_window_rate: Decimal = Field(ge=0, le=1)
    nonpositive_numeric_rate: Decimal = Field(ge=0, le=1)
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
    def validate_codes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)) or any(not code.strip() for code in value):
            raise ValueError("quality failure codes must be unique and nonblank")
        return value

    @model_validator(mode="after")
    def validate_result(self) -> "DatasetQualityReport":
        if self.passed != (len(self.failure_codes) == 0):
            raise ValueError("quality pass state must match failure codes")
        row_counts = (
            self.missing_required_rows,
            self.duplicate_key_rows,
            self.invalid_timestamp_rows,
            self.out_of_window_rows,
            self.nonpositive_numeric_rows,
        )
        if any(count > self.observed_row_count for count in row_counts):
            raise ValueError("quality defect counts cannot exceed observed rows")
        if self.out_of_order_rows > max(self.observed_row_count - 1, 0):
            raise ValueError("out-of-order count cannot exceed adjacent pairs")
        expected_rates = (
            (
                self.missing_required_rate,
                self.missing_required_rows,
                self.observed_row_count,
            ),
            (
                self.duplicate_key_rate,
                self.duplicate_key_rows,
                self.observed_row_count,
            ),
            (
                self.invalid_timestamp_rate,
                self.invalid_timestamp_rows,
                self.observed_row_count,
            ),
            (
                self.out_of_order_rate,
                self.out_of_order_rows,
                max(self.observed_row_count - 1, 0),
            ),
            (
                self.out_of_window_rate,
                self.out_of_window_rows,
                self.observed_row_count,
            ),
            (
                self.nonpositive_numeric_rate,
                self.nonpositive_numeric_rows,
                self.observed_row_count,
            ),
        )
        for rate, count, denominator in expected_rates:
            expected = (
                Decimal("0")
                if denominator == 0
                else Decimal(count) / Decimal(denominator)
            )
            if rate != expected:
                raise ValueError("quality rates must match their defect counts")
        return self


class ArtifactVerification(ReferenceContract):
    dataset_id: str = Field(min_length=3, max_length=160)
    relative_path: str = Field(min_length=1, max_length=240)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    byte_size: int = Field(ge=1)
    verified: Literal[True] = True
    execution_authority: Literal[False] = False


class ExternalArtifactAcquisitionRequest(ReferenceContract):
    """Operator-reviewed request for one immutable public artifact.

    The expected identity must be known before transport begins. This prevents
    HTTPS success from being mistaken for source-integrity verification.
    """

    request_id: str = Field(
        min_length=3,
        max_length=160,
        pattern=r"^[a-z0-9]+(?:[._:-][a-z0-9]+)*$",
    )
    source_id: str = Field(
        min_length=3,
        max_length=160,
        pattern=r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$",
    )
    download_url: HttpUrl
    terms_url: HttpUrl
    terms_review_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    terms_reviewed_at: datetime
    relative_path: str = Field(min_length=1, max_length=240)
    expected_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_byte_size: int = Field(ge=1)
    expected_media_types: tuple[str, ...] = Field(min_length=1)
    archive_kind: ArchiveKind = ArchiveKind.NONE
    terms_accepted: Literal[True] = True
    reference_only: Literal[True] = True
    promotion_eligible: Literal[False] = False
    execution_authority: Literal[False] = False

    @field_validator("download_url", "terms_url")
    @classmethod
    def validate_urls(cls, value: HttpUrl, info) -> HttpUrl:
        if value.scheme != "https":
            raise ValueError(f"{info.field_name} must use HTTPS")
        if value.username or value.password or value.query or value.fragment:
            raise ValueError(
                f"{info.field_name} cannot contain credentials, query, or fragment"
            )
        return value

    @field_validator("terms_reviewed_at")
    @classmethod
    def validate_terms_reviewed_at(cls, value: datetime) -> datetime:
        return require_utc(value, "terms_reviewed_at")

    @field_validator("relative_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        if "\\" in value:
            raise ValueError("acquisition paths must use POSIX separators")
        path = PurePosixPath(value)
        if path.is_absolute() or any(
            part in {"", ".", ".."} for part in path.parts
        ):
            raise ValueError("acquisition path must remain below its dataset root")
        return value

    @field_validator("expected_media_types")
    @classmethod
    def validate_expected_media_types(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("expected media types must be unique")
        if any(
            item != item.lower()
            or "/" not in item
            or ";" in item
            or not item.strip()
            for item in value
        ):
            raise ValueError("expected media types must be lowercase base types")
        return value

    @model_validator(mode="after")
    def validate_archive_identity(self) -> "ExternalArtifactAcquisitionRequest":
        is_zip_path = self.relative_path.lower().endswith(".zip")
        if is_zip_path != (self.archive_kind == ArchiveKind.ZIP):
            raise ValueError("zip path and archive_kind must agree")
        return self


class ArchiveInspectionPolicy(ReferenceContract):
    max_members: int = Field(default=10_000, ge=1, le=1_000_000)
    max_total_uncompressed_bytes: int = Field(
        default=4 * 1024 * 1024 * 1024,
        ge=1,
    )
    max_single_member_bytes: int = Field(
        default=1024 * 1024 * 1024,
        ge=1,
    )
    max_expansion_ratio: Decimal = Field(default=Decimal("100"), ge=1)
    allow_nested_archives: Literal[False] = False


class ArchiveInspectionReport(ReferenceContract):
    artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    member_count: int = Field(ge=0)
    total_compressed_bytes: int = Field(ge=0)
    total_uncompressed_bytes: int = Field(ge=0)
    maximum_expansion_ratio: Decimal = Field(ge=0)
    duplicate_member_count: int = Field(ge=0)
    unsafe_path_count: int = Field(ge=0)
    encrypted_member_count: int = Field(ge=0)
    symlink_member_count: int = Field(ge=0)
    nested_archive_count: int = Field(ge=0)
    passed: bool
    failure_codes: tuple[str, ...] = ()
    reference_only: Literal[True] = True
    promotion_eligible: Literal[False] = False
    execution_authority: Literal[False] = False

    @model_validator(mode="after")
    def validate_archive_report(self) -> "ArchiveInspectionReport":
        if len(self.failure_codes) != len(set(self.failure_codes)) or any(
            not code.strip() for code in self.failure_codes
        ):
            raise ValueError("archive failure codes must be unique and nonblank")
        if self.passed != (len(self.failure_codes) == 0):
            raise ValueError("archive pass state must match failure codes")
        for count in (
            self.duplicate_member_count,
            self.unsafe_path_count,
            self.encrypted_member_count,
            self.symlink_member_count,
            self.nested_archive_count,
        ):
            if count > self.member_count:
                raise ValueError("archive defect counts cannot exceed member count")
        return self


class AcquisitionLimits(ReferenceContract):
    max_bytes: int = Field(
        default=1024 * 1024 * 1024,
        ge=4096,
        le=16 * 1024 * 1024 * 1024,
    )
    max_redirects: int = Field(default=3, ge=0, le=10)
    chunk_size: int = Field(
        default=1024 * 1024,
        ge=4096,
        le=16 * 1024 * 1024,
    )
    connect_timeout_seconds: Decimal = Field(default=Decimal("10"), gt=0, le=120)
    read_timeout_seconds: Decimal = Field(default=Decimal("60"), gt=0, le=600)


class ExternalArtifactAcquisitionReceipt(ReferenceContract):
    request_id: str = Field(
        min_length=3,
        max_length=160,
        pattern=r"^[a-z0-9]+(?:[._:-][a-z0-9]+)*$",
    )
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_id: str = Field(
        min_length=3,
        max_length=160,
        pattern=r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$",
    )
    final_url: HttpUrl
    relative_path: str = Field(min_length=1, max_length=240)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    byte_size: int = Field(ge=1)
    media_type: str = Field(min_length=3, max_length=100)
    retrieved_at: datetime
    redirect_count: int = Field(ge=0, le=10)
    status: AcquisitionStatus
    archive_report_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    verified: Literal[True] = True
    reference_only: Literal[True] = True
    promotion_eligible: Literal[False] = False
    execution_authority: Literal[False] = False

    @field_validator("final_url")
    @classmethod
    def validate_final_url(cls, value: HttpUrl) -> HttpUrl:
        if (
            value.scheme != "https"
            or value.username
            or value.password
            or value.query
            or value.fragment
        ):
            raise ValueError("final_url must be credential-free HTTPS")
        return value

    @field_validator("retrieved_at")
    @classmethod
    def validate_retrieved_at(cls, value: datetime) -> datetime:
        return require_utc(value, "retrieved_at")

    @field_validator("relative_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        if "\\" in value:
            raise ValueError("receipt paths must use POSIX separators")
        path = PurePosixPath(value)
        if path.is_absolute() or any(
            part in {"", ".", ".."} for part in path.parts
        ):
            raise ValueError("receipt path must remain below its dataset root")
        return value

    @field_validator("media_type")
    @classmethod
    def validate_media_type(cls, value: str) -> str:
        if value != value.lower() or "/" not in value or ";" in value:
            raise ValueError("receipt media_type must be a lowercase base type")
        return value

    @model_validator(mode="after")
    def validate_archive_receipt(self) -> "ExternalArtifactAcquisitionReceipt":
        is_zip_path = self.relative_path.lower().endswith(".zip")
        if is_zip_path != (self.archive_report_sha256 is not None):
            raise ValueError("zip receipts require an archive report identity")
        return self


class BenchmarkMetric(ReferenceContract):
    name: str = Field(
        min_length=1,
        max_length=80,
        pattern=r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$",
    )
    value: Decimal
    unit: str = Field(min_length=1, max_length=40)

    @model_validator(mode="after")
    def validate_value(self) -> "BenchmarkMetric":
        if not self.value.is_finite():
            raise ValueError("benchmark metric must be finite")
        return self


class ReproducibilityLevel(StrEnum):
    REPORTED_ONLY = "reported_only"
    CODE_AVAILABLE = "code_available"
    DATA_AND_CODE_AVAILABLE = "data_and_code_available"
    INDEPENDENTLY_REPRODUCED = "independently_reproduced"


class PublishedBenchmarkRecord(ReferenceContract):
    """A frozen public result used only to check definitions and scale."""

    reference_id: str = Field(
        min_length=3,
        max_length=160,
        pattern=r"^[a-z0-9]+(?:[._:-][a-z0-9]+)*$",
    )
    source_id: str = Field(
        min_length=3,
        max_length=160,
        pattern=r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$",
    )
    project_name: str = Field(min_length=1, max_length=160)
    source_url: HttpUrl
    dataset_id: str = Field(min_length=3, max_length=160)
    model_or_engine_version: str = Field(min_length=1, max_length=80)
    metric_spec_version: str = Field(min_length=1, max_length=80)
    published_at: datetime
    retrieved_at: datetime
    seed_count: int = Field(default=1, ge=1)
    reproducibility: ReproducibilityLevel
    source_artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    metrics: tuple[BenchmarkMetric, ...] = Field(min_length=1)
    permitted_use: Literal["calculation_reference_only"] = (
        "calculation_reference_only"
    )
    promotion_eligible: Literal[False] = False
    execution_authority: Literal[False] = False

    @field_validator("source_url")
    @classmethod
    def validate_source_url(cls, value: HttpUrl) -> HttpUrl:
        if value.scheme != "https" or value.username or value.password:
            raise ValueError("benchmark source URL must be credential-free HTTPS")
        return value

    @field_validator("published_at", "retrieved_at")
    @classmethod
    def validate_timestamps(cls, value: datetime, info) -> datetime:
        return require_utc(value, info.field_name)

    @model_validator(mode="after")
    def validate_record(self) -> "PublishedBenchmarkRecord":
        if self.retrieved_at < self.published_at:
            raise ValueError("benchmark cannot be retrieved before publication")
        names = [metric.name for metric in self.metrics]
        if len(names) != len(set(names)):
            raise ValueError("published benchmark metric names must be unique")
        return self


class ReferenceMetricBundle(ReferenceContract):
    sample_size: int = Field(ge=2)
    periods_per_year: int = Field(ge=1)
    total_return: Decimal
    mean_return: Decimal
    sample_std: Decimal = Field(ge=0)
    annualized_volatility: Decimal = Field(ge=0)
    sharpe_ratio: Decimal | None = None
    max_drawdown: Decimal = Field(ge=0, le=1)
    hit_rate: Decimal = Field(ge=0, le=1)
    profit_factor: Decimal | None = Field(default=None, ge=0)
    input_semantics: Literal["net_simple_returns"] = "net_simple_returns"
    reference_only: Literal[True] = True
    promotion_eligible: Literal[False] = False
    execution_authority: Literal[False] = False

    @model_validator(mode="after")
    def validate_finite_values(self) -> "ReferenceMetricBundle":
        values = (
            self.total_return,
            self.mean_return,
            self.sample_std,
            self.annualized_volatility,
            self.sharpe_ratio,
            self.max_drawdown,
            self.hit_rate,
            self.profit_factor,
        )
        if any(value is not None and not value.is_finite() for value in values):
            raise ValueError("reference metrics must be finite")
        return self


class BenchmarkRunStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"


class ExternalBenchmarkRun(ReferenceContract):
    run_id: str = Field(
        min_length=3,
        max_length=160,
        pattern=r"^[a-z0-9]+(?:[._:-][a-z0-9]+)*$",
    )
    dataset_id: str = Field(min_length=3, max_length=160)
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    quality_report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_tree_sha: str = Field(pattern=r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
    configuration_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    metric_spec_version: str = Field(min_length=1, max_length=80)
    seed: int = Field(ge=0)
    started_at: datetime
    completed_at: datetime
    status: BenchmarkRunStatus
    metrics: tuple[BenchmarkMetric, ...] = ()
    reference_ids: tuple[str, ...] = ()
    failure_codes: tuple[str, ...] = ()
    permitted_use: Literal["research_reference_only"] = "research_reference_only"
    promotion_eligible: Literal[False] = False
    execution_authority: Literal[False] = False

    @field_validator("started_at", "completed_at")
    @classmethod
    def validate_timestamps(cls, value: datetime, info) -> datetime:
        return require_utc(value, info.field_name)

    @field_validator("reference_ids", "failure_codes")
    @classmethod
    def validate_unique_strings(cls, value: tuple[str, ...], info) -> tuple[str, ...]:
        if len(value) != len(set(value)) or any(not item.strip() for item in value):
            raise ValueError(f"{info.field_name} must be unique and nonblank")
        return value

    @model_validator(mode="after")
    def validate_run(self) -> "ExternalBenchmarkRun":
        if self.completed_at < self.started_at:
            raise ValueError("benchmark completion cannot precede start")
        metric_names = [metric.name for metric in self.metrics]
        if len(metric_names) != len(set(metric_names)):
            raise ValueError("benchmark run metric names must be unique")
        if self.status == BenchmarkRunStatus.PASSED and self.failure_codes:
            raise ValueError("passed benchmark cannot contain failures")
        if self.status == BenchmarkRunStatus.FAILED and not self.failure_codes:
            raise ValueError("failed benchmark requires failure codes")
        return self
