from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest

from app.config.settings import Settings
from app.exchange.okx.errors import OkxPrivateApiError
from app.exchange.okx.private_rest import OkxLiveExecutionRestClient


def execution_settings(**updates) -> Settings:
    values = {
        "environment": "production",
        "trading_mode": "live",
        "live_trading": True,
        "okx_live_enabled": True,
        "okx_live_allow_order_writes": True,
        "okx_live_api_key": "live-key",
        "okx_live_api_secret": "live-secret",
        "okx_live_api_passphrase": "live-passphrase",
        "api_token": "x" * 40,
        "web_concurrency": 1,
        "okx_live_read_max_retries": 5,
    }
    values.update(updates)
    return Settings(_env_file=None, **values)


@pytest.mark.asyncio
async def test_execution_transport_has_no_demo_header_and_uses_live_endpoints() -> None:
    seen: list[tuple[str, str]] = []
    fixed = datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc)

    def handler(request: httpx.Request) -> httpx.Response:
        assert "x-simulated-trading" not in request.headers
        seen.append((request.method, request.url.path))
        if request.url.path == "/api/v5/trade/order":
            assert request.headers["expTime"] == "1786276805000"
        else:
            assert "expTime" not in request.headers
        if request.url.path == "/api/v5/account/max-size":
            return httpx.Response(
                200,
                json={"code": "0", "msg": "", "data": [{"maxBuy": "2", "maxSell": "2"}]},
            )
        return httpx.Response(
            200,
            json={"code": "0", "msg": "", "data": [{"sCode": "0", "sMsg": ""}]},
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="https://openapi.okx.com",
    ) as client:
        live = OkxLiveExecutionRestClient(
            client,
            settings=execution_settings(),
            clock=lambda: fixed,
        )
        await live.max_order_size("BTC-USDT-SWAP", margin_mode="cross")
        await live.order_precheck({"instId": "BTC-USDT-SWAP", "sz": "1"})
        await live.cancel_all_after({"timeOut": "30", "tag": "CTCCV168"})
        await live.place_order({"instId": "BTC-USDT-SWAP", "sz": "1"})

    assert seen == [
        ("GET", "/api/v5/account/max-size"),
        ("POST", "/api/v5/trade/order-precheck"),
        ("POST", "/api/v5/trade/cancel-all-after"),
        ("POST", "/api/v5/trade/order"),
    ]


@pytest.mark.asyncio
async def test_execution_write_transport_failure_is_never_retried() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("ambiguous network failure", request=request)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="https://openapi.okx.com",
    ) as client:
        with pytest.raises(OkxPrivateApiError) as exc_info:
            await OkxLiveExecutionRestClient(
                client, settings=execution_settings()
            ).place_order({"instId": "BTC-USDT-SWAP"})

    assert calls == 1
    assert exc_info.value.code == "transport_error"


@pytest.mark.asyncio
async def test_execution_transport_blocks_before_http_when_not_enabled() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"code": "0", "msg": "", "data": []})

    settings = Settings(
        _env_file=None,
        okx_live_api_key="live-key",
        okx_live_api_secret="live-secret",
        okx_live_api_passphrase="live-passphrase",
    )
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="https://openapi.okx.com",
    ) as client:
        with pytest.raises(OkxPrivateApiError) as exc_info:
            await OkxLiveExecutionRestClient(
                client, settings=settings
            ).place_order({"instId": "BTC-USDT-SWAP"})

    assert calls == 0
    assert exc_info.value.code == "live_execution_not_enabled"


@pytest.mark.asyncio
async def test_empty_success_payload_after_write_is_ambiguous_and_not_retried() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json={"code": "0", "msg": "", "data": []},
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="https://openapi.okx.com",
    ) as client:
        with pytest.raises(OkxPrivateApiError) as exc_info:
            await OkxLiveExecutionRestClient(
                client, settings=execution_settings()
            ).set_leverage(
                {
                    "instId": "BTC-USDT-SWAP",
                    "lever": "1",
                    "mgnMode": "cross",
                }
            )

    assert calls == 1
    assert exc_info.value.code == "ambiguous_response"
