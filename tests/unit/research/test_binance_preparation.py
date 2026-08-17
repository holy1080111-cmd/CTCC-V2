from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from app.research.external_benchmarks import (
    BinanceKlineCoordinates,
    BinanceReferencePreparationError,
    parse_binance_checksum,
    prepare_binance_kline_request,
)
from tests.unit.research.helpers import MockAsyncByteStream


NOW = datetime(2026, 8, 17, 1, 2, 3, tzinfo=timezone.utc)
ARTIFACT_HASH = "ab" * 32
ARTIFACT_SIZE = 60_928


def coordinates() -> BinanceKlineCoordinates:
    return BinanceKlineCoordinates(
        symbol="BTCUSDT",
        interval="1m",
        day=date(2024, 1, 1),
    )


def test_coordinates_freeze_exact_first_reference() -> None:
    value = coordinates()
    assert value.filename == "BTCUSDT-1m-2024-01-01.zip"
    assert value.member_filename == "BTCUSDT-1m-2024-01-01.csv"
    assert value.instrument_id == "BTC-USDT-SWAP"
    assert value.execution_authority is False

    with pytest.raises(ValidationError, match="BTCUSDT"):
        BinanceKlineCoordinates(
            symbol="BTCUSD",
            interval="1m",
            day=date(2024, 1, 1),
        )
    with pytest.raises(ValidationError, match="2024-01-01"):
        BinanceKlineCoordinates(
            symbol="BTCUSDT",
            interval="1m",
            day=date(2999, 1, 1),
        )


def test_checksum_parser_requires_hash_and_exact_filename() -> None:
    filename = coordinates().filename
    payload = f"{ARTIFACT_HASH}  {filename}\n".encode()
    assert parse_binance_checksum(payload, filename) == ARTIFACT_HASH

    with pytest.raises(BinanceReferencePreparationError, match="filename"):
        parse_binance_checksum(payload, "ETHUSDT-1m-2024-01-01.zip")
    with pytest.raises(BinanceReferencePreparationError, match="format"):
        parse_binance_checksum(b"not-a-checksum", filename)


@pytest.mark.asyncio
async def test_preparation_gets_only_sidecar_then_heads_artifact(
    tmp_path: Path,
) -> None:
    value = coordinates()
    terms = tmp_path / "terms.md"
    terms.write_text("reviewed public reference only\n", encoding="utf-8")
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, str(request.url)))
        assert request.headers["accept-encoding"] == "identity"
        if request.method == "GET":
            return httpx.Response(
                200,
                stream=MockAsyncByteStream(
                    f"{ARTIFACT_HASH}  {value.filename}\n".encode()
                ),
                headers={"content-type": "text/plain"},
            )
        assert request.method == "HEAD"
        return httpx.Response(
            200,
            headers={
                "content-length": str(ARTIFACT_SIZE),
                "content-type": "application/zip",
                "last-modified": "Tue, 02 Jan 2024 06:07:08 GMT",
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
    ) as client:
        identity, request = await prepare_binance_kline_request(
            value,
            terms,
            client=client,
            clock=lambda: NOW,
        )

    assert calls == [
        ("GET", value.checksum_url),
        ("HEAD", value.download_url),
    ]
    assert identity.artifact_sha256 == ARTIFACT_HASH
    assert identity.artifact_byte_size == ARTIFACT_SIZE
    assert identity.revision_policy == "provider_correctable"
    assert request.expected_sha256 == ARTIFACT_HASH
    assert request.expected_byte_size == ARTIFACT_SIZE
    assert request.reference_only is True
    assert request.promotion_eligible is False
    assert request.execution_authority is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case", "match"),
    [
        ("oversized_sidecar", "declared byte limit"),
        ("missing_length", "Content-Length"),
        ("oversized_artifact", "reviewed limit"),
        ("evil_redirect", "exact reviewed data host"),
    ],
)
async def test_preparation_fails_closed_on_untrusted_metadata(
    tmp_path: Path,
    case: str,
    match: str,
) -> None:
    value = coordinates()
    terms = tmp_path / "terms.md"
    terms.write_text("reviewed\n", encoding="utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        if case == "evil_redirect":
            return httpx.Response(
                302,
                headers={"location": "https://example.com/fake"},
            )
        if request.method == "GET":
            if case == "oversized_sidecar":
                return httpx.Response(
                    200,
                    stream=MockAsyncByteStream(b"x" * 513),
                    headers={"content-length": "513"},
                )
            return httpx.Response(
                200,
                stream=MockAsyncByteStream(
                    f"{ARTIFACT_HASH}  {value.filename}\n".encode()
                ),
            )
        headers = {
            "content-type": "application/zip",
            "last-modified": "Tue, 02 Jan 2024 06:07:08 GMT",
        }
        if case != "missing_length":
            headers["content-length"] = str(
                2 * 1024 * 1024
                if case == "oversized_artifact"
                else ARTIFACT_SIZE
            )
        return httpx.Response(200, headers=headers)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
    ) as client:
        with pytest.raises(BinanceReferencePreparationError, match=match):
            await prepare_binance_kline_request(
                value,
                terms,
                client=client,
                clock=lambda: NOW,
            )


@pytest.mark.asyncio
async def test_preparation_rejects_same_host_path_redirection(
    tmp_path: Path,
) -> None:
    value = coordinates()
    terms = tmp_path / "terms.md"
    terms.write_text("reviewed\n", encoding="utf-8")
    redirected_checksum = value.checksum_url.replace(
        "/BTCUSDT/1m/",
        "/ETHUSDT/1m/",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == value.checksum_url:
            return httpx.Response(302, headers={"location": redirected_checksum})
        if request.method == "GET":
            return httpx.Response(
                200,
                stream=MockAsyncByteStream(
                    f"{ARTIFACT_HASH}  {value.filename}\n".encode()
                ),
            )
        return httpx.Response(
            200,
            headers={
                "content-length": str(ARTIFACT_SIZE),
                "content-type": "application/zip",
                "last-modified": "Tue, 02 Jan 2024 06:07:08 GMT",
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
    ) as client:
        with pytest.raises(
            BinanceReferencePreparationError,
            match="redirected away",
        ):
            await prepare_binance_kline_request(
                value,
                terms,
                client=client,
                clock=lambda: NOW,
            )
