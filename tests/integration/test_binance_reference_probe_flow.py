from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
import zipfile

import httpx
import pytest

from app.research.external_benchmarks import (
    BinanceKlineCoordinates,
    acquire_external_artifact,
    prepare_binance_kline_request,
    profile_binance_kline_archive,
)
from app.research.external_benchmarks.evidence_io import write_contract_json
from tests.unit.research.helpers import MockAsyncByteStream


NOW = datetime(2026, 8, 17, 1, 2, 3, tzinfo=timezone.utc)


def kline_zip(coordinates: BinanceKlineCoordinates) -> bytes:
    header = (
        "open_time,open,high,low,close,volume,close_time,quote_volume,"
        "count,taker_buy_volume,taker_buy_quote_volume,ignore\n"
    )
    start = 1_704_067_200_000
    rows = []
    for index in range(1440):
        opened = start + index * 60_000
        rows.append(
            f"{opened},100,102,99,101,2,{opened + 59_999},"
            "201,10,1,100.5,0"
        )
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            coordinates.member_filename,
            header + "\n".join(rows) + "\n",
        )
    return buffer.getvalue()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_official_metadata_to_quality_evidence_remains_reference_only(
    tmp_path: Path,
) -> None:
    coordinates = BinanceKlineCoordinates(
        symbol="BTCUSDT",
        interval="1m",
        day=date(2024, 1, 1),
    )
    payload = kline_zip(coordinates)
    import hashlib

    digest = hashlib.sha256(payload).hexdigest()
    terms = tmp_path / "terms.md"
    terms.write_text("reviewed reference only\n", encoding="utf-8")

    def metadata_handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(
                200,
                stream=MockAsyncByteStream(
                    f"{digest}  {coordinates.filename}\n".encode()
                ),
            )
        return httpx.Response(
            200,
            headers={
                "content-length": str(len(payload)),
                "content-type": "application/zip",
                "last-modified": "Tue, 02 Jan 2024 06:07:08 GMT",
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(metadata_handler),
    ) as client:
        identity, request = await prepare_binance_kline_request(
            coordinates,
            terms,
            client=client,
            clock=lambda: NOW,
        )

    def artifact_handler(http_request: httpx.Request) -> httpx.Response:
        assert http_request.method == "GET"
        return httpx.Response(
            200,
            stream=MockAsyncByteStream(payload, chunk_size=997),
            headers={
                "content-length": str(len(payload)),
                "content-type": "application/zip",
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(artifact_handler),
    ) as client:
        receipt = await acquire_external_artifact(
            request,
            tmp_path,
            client=client,
            clock=iter(
                (NOW + timedelta(seconds=1), NOW + timedelta(seconds=2))
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
    for name, model in (
        ("evidence/identity.json", identity),
        ("evidence/request.json", request),
        ("evidence/receipt.json", receipt),
        ("evidence/manifest.json", manifest),
        ("evidence/generic-quality.json", generic),
        ("evidence/binance-quality.json", provider),
        ("evidence/final.json", evidence),
    ):
        assert write_contract_json(tmp_path, name, model) == "written"

    assert generic.passed is True
    assert provider.passed is True
    assert evidence.passed is True
    assert evidence.reference_only is True
    assert evidence.promotion_eligible is False
    assert evidence.execution_authority is False
