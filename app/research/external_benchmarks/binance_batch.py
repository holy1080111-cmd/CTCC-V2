from __future__ import annotations

import csv
from datetime import datetime, time, timezone
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path
import zipfile

from app.research.external_benchmarks.archive import require_safe_zip_archive
from app.research.external_benchmarks.artifacts import sha256_file
from app.research.external_benchmarks.binance_batch_contracts import (
    BinanceBatchEvidence,
    BinanceBatchKlineCoordinates,
    BinanceBatchPartition,
    BinanceBatchPlan,
    BinanceBatchPreparation,
    BinanceBatchResultEntry,
    BinanceDailyMarketSummary,
    BinancePartitionMarketSummary,
    EXPECTED_MINUTE_ROWS_PER_DAY,
)
from app.research.external_benchmarks.binance_klines import PROVIDER_HEADER
from app.research.external_benchmarks.contracts import ArchiveInspectionPolicy
from app.research.external_benchmarks.metrics import (
    calculate_reference_return_metrics,
)


BATCH_PLAN_PATH = "evidence/binance-reference-batch-v1-plan.json"
BATCH_PREPARATION_PATH = "evidence/binance-reference-batch-v1-preparation.json"
BATCH_EVIDENCE_PATH = "evidence/binance-reference-batch-v1-evidence.json"
MINUTE_MILLISECONDS = 60_000
MAX_KLINE_CSV_BYTES = 4 * 1024 * 1024


class BinanceBatchValidationError(RuntimeError):
    pass


def _decimal(value: str, name: str) -> Decimal:
    try:
        if not value or value.strip() != value:
            raise ValueError
        result = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise BinanceBatchValidationError(
            f"batch kline {name} is not a canonical decimal"
        ) from exc
    if not result.is_finite():
        raise BinanceBatchValidationError(f"batch kline {name} must be finite")
    return result


def _integer(value: str, name: str) -> int:
    try:
        if not value or value.strip() != value:
            raise ValueError
        return int(value)
    except ValueError as exc:
        raise BinanceBatchValidationError(
            f"batch kline {name} is not a canonical integer"
        ) from exc


def _artifact_path(path: Path, expected_sha256: str) -> Path:
    if path.is_symlink():
        raise BinanceBatchValidationError("batch artifact cannot be a symlink")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise BinanceBatchValidationError("batch artifact does not exist") from exc
    if not resolved.is_file():
        raise BinanceBatchValidationError("batch artifact must be a real file")
    if sha256_file(resolved) != expected_sha256:
        raise BinanceBatchValidationError("batch artifact SHA-256 changed")
    return resolved


def _archive_rows(
    coordinates: BinanceBatchKlineCoordinates,
    path: Path,
) -> list[list[str]]:
    require_safe_zip_archive(
        path,
        policy=ArchiveInspectionPolicy(
            max_members=1,
            max_total_uncompressed_bytes=MAX_KLINE_CSV_BYTES,
            max_single_member_bytes=MAX_KLINE_CSV_BYTES,
            max_expansion_ratio=Decimal("20"),
        ),
    )
    try:
        with zipfile.ZipFile(path, "r") as archive:
            members = archive.infolist()
            if len(members) != 1 or members[0].filename != coordinates.member_filename:
                raise BinanceBatchValidationError(
                    "batch ZIP member does not match reviewed coordinates"
                )
            if members[0].file_size > MAX_KLINE_CSV_BYTES:
                raise BinanceBatchValidationError(
                    "batch kline CSV exceeds its byte limit"
                )
            payload = archive.read(members[0])
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        if isinstance(exc, BinanceBatchValidationError):
            raise
        raise BinanceBatchValidationError("batch kline ZIP could not be read") from exc
    if b"\x00" in payload:
        raise BinanceBatchValidationError("batch kline CSV contains NUL bytes")
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise BinanceBatchValidationError("batch kline CSV must be UTF-8") from exc
    rows = list(csv.reader(text.splitlines()))
    if not rows:
        raise BinanceBatchValidationError("batch kline CSV is empty")
    if tuple(rows[0]) == PROVIDER_HEADER:
        rows = rows[1:]
    elif not rows[0] or not rows[0][0].isdigit():
        raise BinanceBatchValidationError(
            "batch kline CSV header is outside the reviewed schema"
        )
    if len(rows) != EXPECTED_MINUTE_ROWS_PER_DAY:
        raise BinanceBatchValidationError(
            "batch kline CSV must contain exactly 1440 records"
        )
    return rows


def summarize_binance_daily_archive(
    coordinates: BinanceBatchKlineCoordinates,
    partition: BinanceBatchPartition,
    artifact_path: Path,
    *,
    artifact_sha256: str,
) -> BinanceDailyMarketSummary:
    """Create a descriptive daily summary after provider quality passed.

    The summary repeats the key structural checks. It is not a signal or a
    claim of predictive validity.
    """

    path = _artifact_path(artifact_path, artifact_sha256)
    rows = _archive_rows(coordinates, path)
    day_start = datetime.combine(
        coordinates.day,
        time.min,
        tzinfo=timezone.utc,
    )
    start_ms = int(day_start.timestamp() * 1000)
    first_open: Decimal | None = None
    last_close: Decimal | None = None
    period_high: Decimal | None = None
    period_low: Decimal | None = None
    base_volume = Decimal("0")
    quote_volume = Decimal("0")
    trade_count = 0

    for index, row in enumerate(rows):
        if len(row) != len(PROVIDER_HEADER):
            raise BinanceBatchValidationError(
                "batch kline row is outside the reviewed schema"
            )
        open_time = _integer(row[0], "open_time")
        open_price = _decimal(row[1], "open")
        high_price = _decimal(row[2], "high")
        low_price = _decimal(row[3], "low")
        close_price = _decimal(row[4], "close")
        row_base_volume = _decimal(row[5], "volume")
        close_time = _integer(row[6], "close_time")
        row_quote_volume = _decimal(row[7], "quote_volume")
        row_trade_count = _integer(row[8], "trade_count")
        expected_open = start_ms + index * MINUTE_MILLISECONDS
        if open_time != expected_open:
            raise BinanceBatchValidationError(
                "batch kline open times are not an exact minute sequence"
            )
        if close_time != open_time + MINUTE_MILLISECONDS - 1:
            raise BinanceBatchValidationError("batch kline close time is invalid")
        if (
            min(open_price, high_price, low_price, close_price) <= 0
            or high_price < max(open_price, close_price)
            or low_price > min(open_price, close_price)
            or high_price < low_price
        ):
            raise BinanceBatchValidationError("batch kline OHLC geometry is invalid")
        if row_base_volume < 0 or row_quote_volume < 0:
            raise BinanceBatchValidationError("batch kline volume cannot be negative")
        if row_trade_count < 0:
            raise BinanceBatchValidationError(
                "batch kline trade count cannot be negative"
            )
        if first_open is None:
            first_open = open_price
        last_close = close_price
        period_high = (
            high_price if period_high is None else max(period_high, high_price)
        )
        period_low = low_price if period_low is None else min(period_low, low_price)
        base_volume += row_base_volume
        quote_volume += row_quote_volume
        trade_count += row_trade_count

    if (
        first_open is None
        or last_close is None
        or period_high is None
        or period_low is None
    ):
        raise BinanceBatchValidationError("batch kline summary is empty")
    simple_return = (last_close / first_open) - Decimal("1")
    direction = (
        "rising" if simple_return > 0 else "falling" if simple_return < 0 else "flat"
    )
    return BinanceDailyMarketSummary(
        partition=partition,
        dataset_id=coordinates.dataset_id,
        symbol=coordinates.symbol,
        instrument_id=coordinates.instrument_id,
        day=coordinates.day,
        artifact_sha256=artifact_sha256,
        first_open=first_open,
        last_close=last_close,
        period_high=period_high,
        period_low=period_low,
        base_volume=base_volume,
        quote_volume=quote_volume,
        trade_count=trade_count,
        simple_return=simple_return,
        observed_direction=direction,
    )


def _median(values: list[Decimal]) -> Decimal:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / Decimal("2")


def _theil_sen_log_slope(closes: list[Decimal]) -> Decimal:
    if len(closes) < 2:
        raise BinanceBatchValidationError(
            "Theil-Sen slope requires at least two closes"
        )
    with localcontext() as context:
        context.prec = 50
        logs = [price.ln() for price in closes]
        slopes = [
            (logs[right] - logs[left]) / Decimal(right - left)
            for left in range(len(logs) - 1)
            for right in range(left + 1, len(logs))
        ]
        return +_median(slopes)


def _path_efficiency(
    first_open: Decimal,
    closes: list[Decimal],
) -> Decimal:
    with localcontext() as context:
        context.prec = 50
        points = [first_open, *closes]
        path = sum(
            (
                abs((points[index] / points[index - 1]).ln())
                for index in range(1, len(points))
            ),
            Decimal("0"),
        )
        if path == 0:
            return Decimal("0")
        displacement = abs((points[-1] / points[0]).ln())
        return +(displacement / path)


def summarize_binance_partitions(
    plan: BinanceBatchPlan,
    daily_summaries: tuple[BinanceDailyMarketSummary, ...],
) -> tuple[BinancePartitionMarketSummary, ...]:
    by_key = {
        (summary.partition, summary.symbol, summary.day): summary
        for summary in daily_summaries
    }
    if len(by_key) != len(daily_summaries):
        raise BinanceBatchValidationError(
            "daily batch summaries contain duplicate coordinates"
        )
    expected_keys = {
        (partition, coordinates.symbol, coordinates.day)
        for partition, coordinates in plan.coordinate_items()
    }
    if set(by_key) != expected_keys:
        raise BinanceBatchValidationError(
            "daily batch summaries do not match the frozen plan"
        )

    results: list[BinancePartitionMarketSummary] = []
    for window in plan.windows:
        for symbol in plan.symbols:
            summaries = [
                by_key[(window.partition, symbol, day)] for day in window.days()
            ]
            closes = [summary.last_close for summary in summaries]
            returns: list[Decimal] = []
            previous = summaries[0].first_open
            for close_price in closes:
                returns.append((close_price / previous) - Decimal("1"))
                previous = close_price
            metrics = calculate_reference_return_metrics(
                returns,
                periods_per_year=365,
            )
            slope = _theil_sen_log_slope(closes)
            direction = "rising" if slope > 0 else "falling" if slope < 0 else "flat"
            results.append(
                BinancePartitionMarketSummary(
                    partition=window.partition,
                    symbol=symbol,
                    instrument_id=summaries[0].instrument_id,
                    start_day=window.start_day,
                    end_day=window.end_day,
                    day_count=window.day_count,
                    minute_row_count=(window.day_count * EXPECTED_MINUTE_ROWS_PER_DAY),
                    first_open=summaries[0].first_open,
                    last_close=summaries[-1].last_close,
                    period_high=max(summary.period_high for summary in summaries),
                    period_low=min(summary.period_low for summary in summaries),
                    base_volume=sum(
                        (summary.base_volume for summary in summaries),
                        Decimal("0"),
                    ),
                    quote_volume=sum(
                        (summary.quote_volume for summary in summaries),
                        Decimal("0"),
                    ),
                    trade_count=sum(summary.trade_count for summary in summaries),
                    close_path_metrics=metrics,
                    theil_sen_log_slope_per_day=slope,
                    path_efficiency=_path_efficiency(
                        summaries[0].first_open,
                        closes,
                    ),
                    observed_direction=direction,
                )
            )
    return tuple(results)


def build_binance_batch_evidence(
    plan: BinanceBatchPlan,
    preparation: BinanceBatchPreparation,
    entries: tuple[BinanceBatchResultEntry, ...],
    daily_summaries: tuple[BinanceDailyMarketSummary, ...],
    *,
    generated_at: datetime,
) -> BinanceBatchEvidence:
    if preparation.plan_id != plan.plan_id:
        raise BinanceBatchValidationError("batch preparation plan ID does not match")
    if preparation.plan_sha256 != plan.canonical_sha256():
        raise BinanceBatchValidationError("batch preparation plan hash does not match")
    if generated_at < preparation.prepared_at:
        raise BinanceBatchValidationError("batch evidence cannot predate preparation")
    expected_ids = [
        coordinates.request_id for _, coordinates in plan.coordinate_items()
    ]
    by_id = {entry.request_id: entry for entry in entries}
    if len(by_id) != len(entries) or set(by_id) != set(expected_ids):
        raise BinanceBatchValidationError(
            "batch result entries do not match the frozen plan"
        )
    ordered_entries = tuple(by_id[request_id] for request_id in expected_ids)
    summaries = summarize_binance_partitions(plan, daily_summaries)
    return BinanceBatchEvidence(
        plan_id=plan.plan_id,
        plan_sha256=plan.canonical_sha256(),
        preparation_sha256=preparation.canonical_sha256(),
        generated_at=generated_at,
        expected_artifact_count=plan.expected_artifact_count,
        completed_artifact_count=len(ordered_entries),
        total_minute_rows=sum(entry.row_count for entry in ordered_entries),
        entries=ordered_entries,
        partition_summaries=summaries,
    )
