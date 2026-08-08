from datetime import datetime, timezone

import httpx
import pytest

from app.config.settings import Settings
from app.exchange.okx.errors import OkxPrivateApiError
from app.exchange.okx.private_rest import OkxDemoPrivateRestClient, build_signature, utc_iso_timestamp


def demo_settings(**updates) -> Settings:
    values = {
        "environment": "test",
        "trading_mode": "okx_demo",
        "paper_auto_execution": False,
        "okx_demo_enabled": True,
        "okx_demo_allow_order_writes": True,
        "okx_demo_api_key": "demo-key",
        "okx_demo_api_secret": "demo-secret",
        "okx_demo_api_passphrase": "demo-passphrase",
        "okx_demo_read_max_retries": 0,
    }
    values.update(updates)
    return Settings(_env_file=None, **values)


def test_signature_matches_okx_prehash_definition() -> None:
    signature = build_signature(
        timestamp="2020-12-08T09:08:57.715Z",
        method="GET",
        request_path="/api/v5/account/balance?ccy=BTC",
        body="",
        secret="secret",
    )
    assert signature == "wpDvCwYCprcMQsQkxWJiWy+YADoQE4ep+OEKKLimMoY="


def test_utc_timestamp_is_millisecond_iso8601() -> None:
    value = utc_iso_timestamp(datetime(2026, 8, 4, 13, 1, 2, 345678, tzinfo=timezone.utc))
    assert value == "2026-08-04T13:01:02.345Z"


@pytest.mark.asyncio
async def test_authenticated_get_includes_simulated_header_and_signed_query() -> None:
    settings = demo_settings()
    fixed = datetime(2026, 8, 4, 13, 1, 2, 345000, tzinfo=timezone.utc)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v5/account/positions"
        assert request.url.query.decode() == "instType=SWAP&instId=BTC-USDT-SWAP"
        assert request.headers["x-simulated-trading"] == "1"
        timestamp = request.headers["OK-ACCESS-TIMESTAMP"]
        expected = build_signature(
            timestamp=timestamp,
            method="GET",
            request_path="/api/v5/account/positions?instType=SWAP&instId=BTC-USDT-SWAP",
            body="",
            secret="demo-secret",
        )
        assert request.headers["OK-ACCESS-SIGN"] == expected
        return httpx.Response(200, json={"code": "0", "msg": "", "data": []})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="https://www.okx.com") as client:
        result = await OkxDemoPrivateRestClient(client, settings=settings, clock=lambda: fixed).positions(
            "BTC-USDT-SWAP"
        )
    assert result == []


@pytest.mark.asyncio
async def test_write_item_error_is_raised() -> None:
    settings = demo_settings()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "code": "0",
                "msg": "",
                "data": [{"ordId": "", "sCode": "51000", "sMsg": "bad order"}],
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="https://www.okx.com") as client:
        with pytest.raises(OkxPrivateApiError) as exc_info:
            await OkxDemoPrivateRestClient(client, settings=settings).place_order({"instId": "BTC-USDT-SWAP"})
    assert exc_info.value.code == "51000"
    assert "demo-secret" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_write_transport_failure_is_not_retried() -> None:
    settings = demo_settings(okx_demo_read_max_retries=5)
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("network down", request=request)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="https://www.okx.com") as client:
        with pytest.raises(OkxPrivateApiError) as exc_info:
            await OkxDemoPrivateRestClient(client, settings=settings).place_order({"instId": "BTC-USDT-SWAP"})
    assert calls == 1
    assert exc_info.value.code == "transport_error"


@pytest.mark.asyncio
async def test_read_retry_refreshes_timestamp_and_signature() -> None:
    settings = demo_settings(okx_demo_read_max_retries=1)
    moments = iter([
        datetime(2026, 8, 4, 13, 1, 2, tzinfo=timezone.utc),
        datetime(2026, 8, 4, 13, 1, 3, tzinfo=timezone.utc),
    ])
    seen_timestamps: list[str] = []
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        seen_timestamps.append(request.headers["OK-ACCESS-TIMESTAMP"])
        if calls == 1:
            raise httpx.ConnectError("temporary network error", request=request)
        return httpx.Response(200, json={"code": "0", "msg": "", "data": []})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="https://www.okx.com") as client:
        result = await OkxDemoPrivateRestClient(
            client, settings=settings, clock=lambda: next(moments)
        ).balance()

    assert result == []
    assert seen_timestamps == [
        "2026-08-04T13:01:02.000Z",
        "2026-08-04T13:01:03.000Z",
    ]
