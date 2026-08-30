from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from app.research.external_benchmarks.contracts import (
    DatasetQualityPolicy,
    DatasetQualityReport,
    ExternalDatasetManifest,
    TimestampEncoding,
    require_utc,
)
from app.research.external_benchmarks.catalog import validate_manifest_source


EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _datetime_to_nanoseconds(value: datetime) -> int:
    value = require_utc(value, "timestamp")
    delta = value - EPOCH
    return (
        delta.days * 86_400 * 1_000_000_000
        + delta.seconds * 1_000_000_000
        + delta.microseconds * 1_000
    )


def _integer_timestamp(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("boolean is not a timestamp")
    if isinstance(value, int):
        result = value
    elif isinstance(value, str) and value.strip().isdigit():
        result = int(value.strip())
    else:
        raise ValueError("Unix timestamps must be integers")
    if result < 0:
        raise ValueError("Unix timestamps cannot be negative")
    return result


def _timestamp_to_nanoseconds(value: Any, encoding: TimestampEncoding) -> int:
    if encoding == TimestampEncoding.ISO_8601_UTC:
        if isinstance(value, datetime):
            parsed = value
        elif isinstance(value, str):
            text = value.strip()
            if text.endswith("Z"):
                text = f"{text[:-1]}+00:00"
            try:
                parsed = datetime.fromisoformat(text)
            except ValueError as exc:
                raise ValueError("invalid ISO-8601 timestamp") from exc
        else:
            raise ValueError("ISO-8601 timestamps must be strings or datetimes")
        return _datetime_to_nanoseconds(parsed)

    raw = _integer_timestamp(value)
    multiplier = {
        TimestampEncoding.UNIX_MILLISECONDS: 1_000_000,
        TimestampEncoding.UNIX_MICROSECONDS: 1_000,
        TimestampEncoding.UNIX_NANOSECONDS: 1,
    }[encoding]
    return raw * multiplier


def _missing(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _positive_number(value: Any) -> bool:
    if isinstance(value, bool) or _missing(value):
        return False
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return False
    return number.is_finite() and number > 0


def _rate(count: int, denominator: int) -> Decimal:
    if denominator <= 0:
        return Decimal("0")
    return Decimal(count) / Decimal(denominator)


def profile_dataset_records(
    manifest: ExternalDatasetManifest,
    records: Sequence[Mapping[str, Any]],
    *,
    generated_at: datetime,
    policy: DatasetQualityPolicy | None = None,
) -> DatasetQualityReport:
    """Profile canonical records against their frozen manifest.

    This function deliberately does not repair, reorder, fill, or deduplicate.
    """

    validate_manifest_source(manifest)
    generated_at = require_utc(generated_at, "generated_at")
    if generated_at < manifest.retrieved_at:
        raise ValueError("quality report cannot precede dataset retrieval")
    quality_policy = policy or DatasetQualityPolicy()
    row_count = len(records)
    missing_required_rows = 0
    duplicate_key_rows = 0
    invalid_timestamp_rows = 0
    out_of_order_rows = 0
    out_of_window_rows = 0
    nonpositive_numeric_rows = 0
    seen_keys: set[tuple[Any, ...]] = set()
    previous_timestamp: int | None = None
    window_start = _datetime_to_nanoseconds(manifest.window.start)
    window_end = _datetime_to_nanoseconds(manifest.window.end)

    for record in records:
        missing_required = any(
            field not in record or _missing(record.get(field))
            for field in manifest.required_fields
        )
        if missing_required:
            missing_required_rows += 1

        if all(field in record and not _missing(record.get(field)) for field in manifest.key_fields):
            key = tuple(record[field] for field in manifest.key_fields)
            if key in seen_keys:
                duplicate_key_rows += 1
            else:
                seen_keys.add(key)

        timestamp: int | None = None
        if manifest.timestamp_field in record and not _missing(
            record.get(manifest.timestamp_field)
        ):
            try:
                timestamp = _timestamp_to_nanoseconds(
                    record[manifest.timestamp_field],
                    manifest.timestamp_encoding,
                )
            except (ValueError, OverflowError):
                invalid_timestamp_rows += 1
        else:
            invalid_timestamp_rows += 1

        if timestamp is not None:
            if timestamp < window_start or timestamp > window_end:
                out_of_window_rows += 1
            if previous_timestamp is not None and timestamp < previous_timestamp:
                out_of_order_rows += 1
            previous_timestamp = timestamp

        if any(
            field not in record or not _positive_number(record.get(field))
            for field in manifest.positive_numeric_fields
        ):
            nonpositive_numeric_rows += 1

    pair_count = max(row_count - 1, 0)
    rates = {
        "missing_required_rate": _rate(missing_required_rows, row_count),
        "duplicate_key_rate": _rate(duplicate_key_rows, row_count),
        "invalid_timestamp_rate": _rate(invalid_timestamp_rows, row_count),
        "out_of_order_rate": _rate(out_of_order_rows, pair_count),
        "out_of_window_rate": _rate(out_of_window_rows, row_count),
        "nonpositive_numeric_rate": _rate(nonpositive_numeric_rows, row_count),
    }
    failures: list[str] = []
    if row_count != manifest.row_count:
        failures.append("row_count_mismatch")
    if row_count < quality_policy.minimum_rows:
        failures.append("row_count_below_minimum")
    for rate_name, maximum, failure_code in (
        (
            "missing_required_rate",
            quality_policy.max_missing_required_rate,
            "missing_required_rate_exceeded",
        ),
        (
            "duplicate_key_rate",
            quality_policy.max_duplicate_key_rate,
            "duplicate_key_rate_exceeded",
        ),
        (
            "invalid_timestamp_rate",
            quality_policy.max_invalid_timestamp_rate,
            "invalid_timestamp_rate_exceeded",
        ),
        (
            "out_of_order_rate",
            quality_policy.max_out_of_order_rate,
            "out_of_order_rate_exceeded",
        ),
        (
            "out_of_window_rate",
            quality_policy.max_out_of_window_rate,
            "out_of_window_rate_exceeded",
        ),
        (
            "nonpositive_numeric_rate",
            quality_policy.max_nonpositive_numeric_rate,
            "nonpositive_numeric_rate_exceeded",
        ),
    ):
        if rates[rate_name] > maximum:
            failures.append(failure_code)

    return DatasetQualityReport(
        dataset_id=manifest.dataset_id,
        manifest_sha256=manifest.canonical_sha256(),
        generated_at=generated_at,
        declared_row_count=manifest.row_count,
        observed_row_count=row_count,
        missing_required_rows=missing_required_rows,
        duplicate_key_rows=duplicate_key_rows,
        invalid_timestamp_rows=invalid_timestamp_rows,
        out_of_order_rows=out_of_order_rows,
        out_of_window_rows=out_of_window_rows,
        nonpositive_numeric_rows=nonpositive_numeric_rows,
        missing_required_rate=rates["missing_required_rate"],
        duplicate_key_rate=rates["duplicate_key_rate"],
        invalid_timestamp_rate=rates["invalid_timestamp_rate"],
        out_of_order_rate=rates["out_of_order_rate"],
        out_of_window_rate=rates["out_of_window_rate"],
        nonpositive_numeric_rate=rates["nonpositive_numeric_rate"],
        passed=not failures,
        failure_codes=tuple(failures),
    )
