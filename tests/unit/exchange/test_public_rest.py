import httpx
import pytest

from app.exchange.okx.errors import OkxPublicApiError
from app.exchange.okx.public_rest import OkxPublicRestClient


@pytest.mark.asyncio
async def test_ticker_request_and_parsing() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v5/market/ticker"
        return httpx.Response(
            200,
            json={
                "code": "0",
                "msg": "",
                "data": [
                    {
                        "instType": "SWAP",
                        "instId": "BTC-USDT-SWAP",
                        "last": "100",
                        "bidPx": "99",
                        "askPx": "101",
                        "bidSz": "2",
                        "askSz": "3",
                        "open24h": "90",
                        "high24h": "110",
                        "low24h": "80",
                        "vol24h": "1000",
                        "volCcy24h": "100000",
                        "ts": "1750000000000",
                    }
                ],
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(
        transport=transport, base_url="https://example.test"
    ) as http_client:
        ticker = await OkxPublicRestClient(http_client).ticker("BTC-USDT-SWAP")
    assert str(ticker.last) == "100"


@pytest.mark.asyncio
async def test_ticker_rejects_other_instrument_volume() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["instId"] == "BTC-USDT-SWAP"
        return httpx.Response(
            200,
            json={
                "code": "0",
                "msg": "",
                "data": [
                    {
                        "instType": "SWAP",
                        "instId": "ETH-USDT-SWAP",
                        "last": "100",
                        "bidPx": "99",
                        "askPx": "101",
                        "bidSz": "2",
                        "askSz": "3",
                        "open24h": "90",
                        "high24h": "110",
                        "low24h": "80",
                        "vol24h": "1000",
                        "volCcy24h": "100000",
                        "ts": "1750000000000",
                    }
                ],
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://example.test"
    ) as client:
        with pytest.raises(OkxPublicApiError, match="ticker instrument mismatch"):
            await OkxPublicRestClient(client).ticker("BTC-USDT-SWAP")


@pytest.mark.asyncio
@pytest.mark.parametrize("data", [[], [{}, {}]])
async def test_ticker_requires_exactly_one_response_row(data) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": "0", "msg": "", "data": data})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://example.test"
    ) as client:
        with pytest.raises(OkxPublicApiError, match="non-singleton"):
            await OkxPublicRestClient(client).ticker("BTC-USDT-SWAP")


@pytest.mark.asyncio
async def test_candles_reject_spot_before_request() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        raise AssertionError("no SPOT candle may enter SWAP parser")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://example.test"
    ) as client:
        with pytest.raises(OkxPublicApiError, match="SWAP candle instrument required"):
            await OkxPublicRestClient(client).candles("BTC-USDT", "1m", 1)
    assert not calls
