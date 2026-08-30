from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import hashlib
from io import BytesIO
from pathlib import Path
import zipfile

import httpx
import pytest

from app.research.external_benchmarks import (
    BinanceBatchKlineCoordinates,
    BinanceBatchPartition,
    BinanceBatchPlan,
    BinanceBatchPreparation,
    BinanceBatchPreparationEntry,
    BinanceBatchResultEntry,
    BinanceBatchWindow,
    acquire_external_artifact,
    batch_evidence_prefix,
    build_binance_batch_evidence,
    prepare_binance_kline_request,
    profile_binance_kline_archive,
    summarize_binance_daily_archive,
)
from app.research.external_benchmarks.evidence_io import write_contract_json
from tests.unit.research.helpers import MockAsyncByteStream


NOW = datetime(2026, 8, 22, 16, 0, tzinfo=timezone.utc)


def _payload(
    coordinates: BinanceBatchKlineCoordinates,
    first_open: Decimal,
    final_close: Decimal,
) -> bytes:
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
    rows = []
    previous = first_open
    for index in range(1440):
        opened = start + index * 60_000
        closed = first_open + step * Decimal(index + 1)
        rows.append(
            f"{opened},{previous},{max(previous, closed) + 1},"
            f"{min(previous, closed) - 1},{closed},2,"
            f"{opened + 59_999},200,10,1,100,0"
        )
        previous = closed
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            coordinates.member_filename,
            header + "\n".join(rows) + "\n",
        )
    return buffer.getvalue()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_two_day_batch_remains_reference_only_end_to_end(
    tmp_path: Path,
) -> None:
    terms = tmp_path / "terms.md"
    terms.write_text("reviewed public reference only\n", encoding="utf-8")
    plan = BinanceBatchPlan(
        plan_id="binance.integration.batch",
        symbols=("BTCUSDT",),
        windows=(
            BinanceBatchWindow(
                partition=BinanceBatchPartition.DEVELOPMENT,
                start_day=date(2024, 1, 1),
                end_day=date(2024, 1, 2),
            ),
        ),
    )
    payloads = {}
    for (_, coordinates), prices in zip(
        plan.coordinate_items(),
        (
            (Decimal("100"), Decimal("110")),
            (Decimal("110"), Decimal("121")),
        ),
        strict=True,
    ):
        payloads[coordinates.download_url] = _payload(
            coordinates,
            *prices,
        )

    def metadata_handler(request: httpx.Request) -> httpx.Response:
        artifact_url = str(request.url).removesuffix(".CHECKSUM")
        payload = payloads[artifact_url]
        digest = hashlib.sha256(payload).hexdigest()
        filename = artifact_url.rsplit("/", 1)[-1]
        provider_day = date.fromisoformat(filename.removesuffix(".zip")[-10:])
        provider_last_modified = datetime.combine(
            provider_day + timedelta(days=1),
            datetime.min.time(),
            tzinfo=timezone.utc,
        ) + timedelta(hours=6, minutes=7, seconds=8)
        if request.method == "GET":
            return httpx.Response(
                200,
                stream=MockAsyncByteStream(f"{digest}  {filename}\n".encode()),
            )
        return httpx.Response(
            200,
            headers={
                "content-length": str(len(payload)),
                "content-type": "binary/octet-stream",
                "last-modified": provider_last_modified.strftime(
                    "%a, %d %b %Y %H:%M:%S GMT"
                ),
            },
        )

    prepared = []
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(metadata_handler),
    ) as client:
        for partition, coordinates in plan.coordinate_items():
            identity, request = await prepare_binance_kline_request(
                coordinates,
                terms,
                client=client,
                clock=lambda: NOW,
            )
            prefix = batch_evidence_prefix(coordinates)
            write_contract_json(
                tmp_path,
                f"{prefix}-identity.json",
                identity,
            )
            write_contract_json(
                tmp_path,
                f"{prefix}-request.json",
                request,
            )
            prepared.append((partition, coordinates, identity, request))

    preparation = BinanceBatchPreparation(
        plan_id=plan.plan_id,
        plan_sha256=plan.canonical_sha256(),
        prepared_at=NOW,
        expected_artifact_count=2,
        total_expected_bytes=sum(
            request.expected_byte_size for _, _, _, request in prepared
        ),
        entries=tuple(
            BinanceBatchPreparationEntry(
                partition=partition,
                symbol=coordinates.symbol,
                day=coordinates.day,
                request_id=coordinates.request_id,
                identity_relative_path=(
                    f"{batch_evidence_prefix(coordinates)}-identity.json"
                ),
                request_relative_path=(
                    f"{batch_evidence_prefix(coordinates)}-request.json"
                ),
                identity_sha256=identity.canonical_sha256(),
                request_sha256=request.canonical_sha256(),
                artifact_sha256=request.expected_sha256,
                artifact_byte_size=request.expected_byte_size,
                provider_last_modified_at=(identity.provider_last_modified_at),
            )
            for partition, coordinates, identity, request in prepared
        ),
    )

    def artifact_handler(request: httpx.Request) -> httpx.Response:
        payload = payloads[str(request.url)]
        return httpx.Response(
            200,
            stream=MockAsyncByteStream(payload, chunk_size=997),
            headers={
                "content-length": str(len(payload)),
                "content-type": "binary/octet-stream",
            },
        )

    results = []
    daily = []
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(artifact_handler),
    ) as client:
        for partition, coordinates, identity, request in prepared:
            receipt = await acquire_external_artifact(
                request,
                tmp_path,
                client=client,
                clock=iter(
                    (
                        NOW + timedelta(seconds=1),
                        NOW + timedelta(seconds=2),
                    )
                ).__next__,
            )
            manifest, generic, provider, evidence = profile_binance_kline_archive(
                coordinates,
                identity,
                request,
                receipt,
                tmp_path,
                generated_at=receipt.retrieved_at,
            )
            artifact_path = tmp_path.joinpath(*request.relative_path.split("/"))
            summary = summarize_binance_daily_archive(
                coordinates,
                partition,
                artifact_path,
                artifact_sha256=request.expected_sha256,
            )
            assert generic.passed is True
            assert provider.passed is True
            assert evidence.passed is True
            daily.append(summary)
            results.append(
                BinanceBatchResultEntry(
                    partition=partition,
                    request_id=coordinates.request_id,
                    artifact_sha256=request.expected_sha256,
                    request_sha256=request.canonical_sha256(),
                    receipt_sha256=receipt.canonical_sha256(),
                    manifest_sha256=manifest.canonical_sha256(),
                    generic_quality_sha256=generic.canonical_sha256(),
                    provider_quality_sha256=provider.canonical_sha256(),
                    evidence_sha256=evidence.canonical_sha256(),
                    daily_summary_sha256=summary.canonical_sha256(),
                )
            )

    batch = build_binance_batch_evidence(
        plan,
        preparation,
        tuple(results),
        tuple(daily),
        generated_at=NOW + timedelta(seconds=3),
    )
    assert batch.passed is True
    assert batch.total_minute_rows == 2880
    assert batch.partition_summaries[0].observed_direction == "rising"
    assert batch.partition_summaries[0].close_path_metrics.total_return == Decimal(
        "0.21"
    )
    assert batch.strategy_evaluated is False
    assert batch.costs_evaluated is False
    assert batch.runtime_consumers == 0
    assert batch.promotion_eligible is False
    assert batch.execution_authority is False
