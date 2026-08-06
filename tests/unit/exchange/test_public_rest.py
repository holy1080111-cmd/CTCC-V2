import httpx
import pytest

from app.exchange.okx.public_rest import OkxPublicRestClient


@pytest.mark.asyncio
async def test_ticker_request_and_parsing() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v5/market/ticker"
        return httpx.Response(200, json={"code": "0", "msg": "", "data": [{
            "instId": "BTC-USDT-SWAP", "last": "100", "bidPx": "99", "askPx": "101",
            "bidSz": "2", "askSz": "3", "open24h": "90", "high24h": "110",
            "low24h": "80", "vol24h": "1000", "volCcy24h": "100000", "ts": "1750000000000"
        }]})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="https://example.test") as http_client:
        ticker = await OkxPublicRestClient(http_client).ticker("BTC-USDT-SWAP")
    assert str(ticker.last) == "100"
