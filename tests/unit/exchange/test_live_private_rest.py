from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest

from app.config.settings import Settings
from app.exchange.okx.errors import OkxPrivateApiError
from app.exchange.okx.private_api import OkxPrivateApiClient
from app.exchange.okx.private_rest import (
    OkxLivePrivateRestClient,
    build_signature,
)


def live_settings(**updates) -> Settings:
    values = {
        "environment": "test",
        "trading_mode": "analysis_only",
        "auto_trade": False,
        "live_trading": False,
        "okx_live_rest_base_url": "https://openapi.okx.com",
        "okx_live_api_key": "live-key",
        "okx_live_api_secret": "live-secret",
        "okx_live_api_passphrase": "live-passphrase",
        "okx_live_read_max_retries": 0,
        "okx_demo_api_key": "demo-key",
        "okx_demo_api_secret": "demo-secret",
        "okx_demo_api_passphrase": "demo-passphrase",
    }
    values.update(updates)
    return Settings(_env_file=None, **values)


def test_live_private_rest_client_satisfies_private_api_contract() -> None:
    assert issubclass(OkxLivePrivateRestClient, OkxPrivateApiClient)


@pytest.mark.asyncio
async def test_live_authenticated_get_uses_live_signature_without_demo_header() -> None:
    settings = live_settings()
    fixed = datetime(2026, 8, 9, 0, 0, 0, tzinfo=timezone.utc)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v5/account/config"
        assert "x-simulated-trading" not in request.headers
        assert request.headers["OK-ACCESS-KEY"] == "live-key"
        timestamp = request.headers["OK-ACCESS-TIMESTAMP"]
        expected = build_signature(
            timestamp=timestamp,
            method="GET",
            request_path="/api/v5/account/config",
            body="",
            secret="live-secret",
        )
        assert request.headers["OK-ACCESS-SIGN"] == expected
        return httpx.Response(
            200,
            json={"code": "0", "msg": "", "data": [{"acctLv": "2"}]},
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="https://openapi.okx.com",
    ) as client:
        result = await OkxLivePrivateRestClient(
            client,
            settings=settings,
            clock=lambda: fixed,
        ).account_config()

    assert result == [{"acctLv": "2"}]


@pytest.mark.asyncio
async def test_live_client_does_not_fall_back_to_demo_credentials() -> None:
    settings = live_settings(
        okx_live_api_key="",
        okx_live_api_secret="",
        okx_live_api_passphrase="",
    )
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"code": "0", "msg": "", "data": []})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="https://openapi.okx.com",
    ) as client:
        with pytest.raises(OkxPrivateApiError) as exc_info:
            await OkxLivePrivateRestClient(client, settings=settings).balance()

    assert exc_info.value.code == "credentials_missing"
    assert calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operation", "payload"),
    [
        ("place_order", {"instId": "BTC-USDT-SWAP"}),
        ("cancel_order", {"instId": "BTC-USDT-SWAP", "ordId": "1"}),
        ("close_position", {"instId": "BTC-USDT-SWAP", "mgnMode": "cross"}),
        ("set_leverage", {"instId": "BTC-USDT-SWAP", "lever": "1", "mgnMode": "cross"}),
        ("order_precheck", {"instId": "BTC-USDT-SWAP", "sz": "1"}),
        ("cancel_all_after", {"timeOut": "30", "tag": "CTCCV168"}),
    ],
)
async def test_live_public_write_methods_are_blocked_before_http(
    operation: str,
    payload: dict[str, str],
) -> None:
    settings = live_settings()
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"code": "0", "msg": "", "data": []})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="https://openapi.okx.com",
    ) as client:
        live_client = OkxLivePrivateRestClient(client, settings=settings)
        with pytest.raises(OkxPrivateApiError) as exc_info:
            await getattr(live_client, operation)(payload)

    assert exc_info.value.code == "live_writes_disabled"
    assert calls == 0


@pytest.mark.asyncio
async def test_live_transport_rejects_non_get_even_without_write_flag() -> None:
    settings = live_settings()
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"code": "0", "msg": "", "data": []})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="https://openapi.okx.com",
    ) as client:
        live_client = OkxLivePrivateRestClient(client, settings=settings)
        with pytest.raises(OkxPrivateApiError) as exc_info:
            await live_client._request(
                "POST",
                "/api/v5/trade/order",
                body={"instId": "BTC-USDT-SWAP"},
                write=False,
            )

    assert exc_info.value.code == "live_writes_disabled"
    assert calls == 0
