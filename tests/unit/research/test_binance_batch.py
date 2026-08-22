from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import hashlib
from pathlib import Path
import zipfile

import pytest
from pydantic import ValidationError

from app.research.external_benchmarks import (
    BinanceBatchKlineCoordinates,
    BinanceBatchPartition,
    BinanceBatchPlan,
    BinanceBatchPreparation,
    BinanceBatchPreparationEntry,
    BinanceBatchResultEntry,
    BinanceBatchValidationError,
    BinanceBatchWindow,
    batch_evidence_prefix,
    build_binance_batch_evidence,
    canonical_binance_batch_plan,
    summarize_binance_daily_archive,
    summarize_binance_partitions,
)


NOW = datetime(2026, 8, 22, 16, 0, tzinfo=timezone.utc)


def _coordinates(day: date) -> BinanceBatchKlineCoordinates:
    return BinanceBatchKlineCoordinates(
        symbol="BTCUSDT",
        interval="1m",
        day=day,
    )


def _write_kline_zip(
    path: Path,
    coordinates: BinanceBatchKlineCoordinates,
    *,
    first_open: Decimal,
    final_close: Decimal,
) -> str:
    header = (
        "open_time,open,high,low,close,volume,close_time,quote_volume,"
        "count,taker_buy_volume,taker_buy_quote_volume,ignore\n"
    )
    start = int(
        datetime.combine(
            coordinates.day,
            datetime.min.time(),
            tzinfo=timezone.utc,
        ).timestamp()
        * 1000
    )
    step = (final_close - first_open) / Decimal("1440")
    rows: list[str] = []
    previous = first_open
    for index in range(1440):
        opened = start + index * 60_000
        closed = first_open + step * Decimal(index + 1)
        high = max(previous, closed) + Decimal("1")
        low = min(previous, closed) - Decimal("1")
        rows.append(
            f"{opened},{previous},{high},{low},{closed},2,"
            f"{opened + 59_999},200,10,1,100,0"
        )
        previous = closed
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            coordinates.member_filename,
            header + "\n".join(rows) + "\n",
        )
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _two_day_plan() -> BinanceBatchPlan:
    return BinanceBatchPlan(
        plan_id="binance.test.batch",
        symbols=("BTCUSDT",),
        windows=(
            BinanceBatchWindow(
                partition=BinanceBatchPartition.DEVELOPMENT,
                start_day=date(2024, 1, 1),
                end_day=date(2024, 1, 2),
            ),
        ),
    )


def test_canonical_batch_plan_is_fixed_and_non_overlapping() -> None:
    plan = canonical_binance_batch_plan()

    assert plan.symbols == ("BTCUSDT", "ETHUSDT")
    assert [window.day_count for window in plan.windows] == [30, 30, 30]
    assert plan.expected_artifact_count == 180
    assert plan.windows[-1].partition == (BinanceBatchPartition.RETROSPECTIVE_HOLDOUT)
    assert plan.windows[-1].end_day == date(2026, 8, 21)
    assert plan.holdout_semantics == "retrospective_not_prospective"
    assert plan.reference_only is True
    assert plan.promotion_eligible is False
    assert plan.execution_authority is False
    items = plan.coordinate_items()
    assert len(items) == 180
    assert items[0][1].request_id == ("binance.btcusdt.klines.1m.2024-01-01")
    assert items[-1][1].request_id == ("binance.ethusdt.klines.1m.2026-08-21")


def test_batch_coordinates_keep_exact_symbol_and_date_boundaries() -> None:
    btc = _coordinates(date(2026, 8, 21))
    eth = BinanceBatchKlineCoordinates(
        symbol="ETHUSDT",
        interval="1m",
        day=date(2024, 1, 1),
    )
    assert btc.instrument_id == "BTC-USDT-SWAP"
    assert eth.instrument_id == "ETH-USDT-SWAP"
    assert btc.download_url.startswith(
        "https://data.binance.vision/data/futures/um/daily/klines/"
    )

    with pytest.raises(ValidationError, match="BTCUSDT|ETHUSDT"):
        BinanceBatchKlineCoordinates(
            symbol="SOLUSDT",
            interval="1m",
            day=date(2026, 8, 21),
        )
    with pytest.raises(ValidationError, match="reviewed"):
        _coordinates(date(2026, 8, 22))


def test_batch_plan_rejects_overlap_and_excessive_scope() -> None:
    with pytest.raises(ValidationError, match="overlap"):
        BinanceBatchPlan(
            plan_id="binance.overlap.test",
            symbols=("BTCUSDT",),
            windows=(
                BinanceBatchWindow(
                    partition=BinanceBatchPartition.DEVELOPMENT,
                    start_day=date(2024, 1, 1),
                    end_day=date(2024, 1, 3),
                ),
                BinanceBatchWindow(
                    partition=BinanceBatchPartition.VALIDATION,
                    start_day=date(2024, 1, 3),
                    end_day=date(2024, 1, 5),
                ),
            ),
        )

    with pytest.raises(ValidationError, match="artifact count"):
        BinanceBatchPlan(
            plan_id="binance.too.large",
            symbols=("BTCUSDT", "ETHUSDT"),
            windows=(
                BinanceBatchWindow(
                    partition=BinanceBatchPartition.DEVELOPMENT,
                    start_day=date(2024, 1, 1),
                    end_day=date(2024, 4, 10),
                ),
            ),
        )


def test_daily_and_partition_summaries_are_deterministic(
    tmp_path: Path,
) -> None:
    plan = _two_day_plan()
    daily = []
    hashes = []
    for day, opened, closed in (
        (date(2024, 1, 1), Decimal("100"), Decimal("110")),
        (date(2024, 1, 2), Decimal("110"), Decimal("121")),
    ):
        coordinates = _coordinates(day)
        path = tmp_path / coordinates.filename
        digest = _write_kline_zip(
            path,
            coordinates,
            first_open=opened,
            final_close=closed,
        )
        hashes.append(digest)
        daily.append(
            summarize_binance_daily_archive(
                coordinates,
                BinanceBatchPartition.DEVELOPMENT,
                path,
                artifact_sha256=digest,
            )
        )

    assert daily[0].row_count == 1440
    assert daily[0].simple_return == Decimal("0.1")
    assert daily[0].observed_direction == "rising"
    summaries = summarize_binance_partitions(plan, tuple(daily))
    assert len(summaries) == 1
    summary = summaries[0]
    assert summary.day_count == 2
    assert summary.minute_row_count == 2880
    assert summary.close_path_metrics.total_return == Decimal("0.21")
    assert summary.close_path_metrics.hit_rate == Decimal("1")
    assert summary.observed_direction == "rising"
    assert summary.theil_sen_log_slope_per_day > 0
    assert Decimal("0") < summary.path_efficiency <= Decimal("1")
    assert summary.strategy_evaluated is False
    assert summary.predictive_validity_claimed is False

    entries = []
    preparation_entries = []
    for (partition, coordinates), digest in zip(
        plan.coordinate_items(), hashes, strict=True
    ):
        prefix = batch_evidence_prefix(coordinates)
        preparation_entries.append(
            BinanceBatchPreparationEntry(
                partition=partition,
                symbol=coordinates.symbol,
                day=coordinates.day,
                request_id=coordinates.request_id,
                identity_relative_path=f"{prefix}-identity.json",
                request_relative_path=f"{prefix}-request.json",
                identity_sha256="1" * 64,
                request_sha256="2" * 64,
                artifact_sha256=digest,
                artifact_byte_size=100,
                provider_last_modified_at=NOW - timedelta(days=1),
            )
        )
        entries.append(
            BinanceBatchResultEntry(
                partition=partition,
                request_id=coordinates.request_id,
                artifact_sha256=digest,
                request_sha256="2" * 64,
                receipt_sha256="3" * 64,
                manifest_sha256="4" * 64,
                generic_quality_sha256="5" * 64,
                provider_quality_sha256="6" * 64,
                evidence_sha256="7" * 64,
                daily_summary_sha256="8" * 64,
            )
        )
    preparation = BinanceBatchPreparation(
        plan_id=plan.plan_id,
        plan_sha256=plan.canonical_sha256(),
        prepared_at=NOW,
        expected_artifact_count=2,
        total_expected_bytes=200,
        entries=tuple(preparation_entries),
    )
    evidence = build_binance_batch_evidence(
        plan,
        preparation,
        tuple(entries),
        tuple(daily),
        generated_at=NOW + timedelta(seconds=1),
    )
    assert evidence.completed_artifact_count == 2
    assert evidence.total_minute_rows == 2880
    assert evidence.runtime_consumers == 0
    assert evidence.strategy_evaluated is False
    assert evidence.execution_authority is False


def test_partition_summary_rejects_missing_planned_day(
    tmp_path: Path,
) -> None:
    coordinates = _coordinates(date(2024, 1, 1))
    path = tmp_path / coordinates.filename
    digest = _write_kline_zip(
        path,
        coordinates,
        first_open=Decimal("100"),
        final_close=Decimal("101"),
    )
    summary = summarize_binance_daily_archive(
        coordinates,
        BinanceBatchPartition.DEVELOPMENT,
        path,
        artifact_sha256=digest,
    )
    with pytest.raises(BinanceBatchValidationError, match="frozen plan"):
        summarize_binance_partitions(_two_day_plan(), (summary,))
