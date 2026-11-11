from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import httpx
import pytest
from pydantic import SecretStr

from app.config.settings import Settings
from app.exchange.okx.errors import OkxPrivateApiError
from app.exchange.okx.live_read_lease_v1 import OkxLivePinnedGetClientV1
from app.exchange.okx.private_api import OkxPrivateApiClient
from app.exchange.okx.private_rest import (
    OkxLivePrivateRestClient,
    _OkxPrivateRestClientBase,
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
    fixed = datetime(2026, 8, 9, 0, 0, 0, tzinfo=UTC)

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
async def test_live_order_history_uses_ord_id_chain_without_demo_header() -> None:
    observed = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v5/trade/orders-history"
        assert request.url.params["instType"] == "SWAP"
        assert request.url.params["limit"] == "100"
        assert "x-simulated-trading" not in request.headers
        after = request.url.params.get("after")
        observed.append(after)
        rows = [{"ordId": "300"}] if after is None else []
        return httpx.Response(200, json={"code": "0", "msg": "", "data": rows})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://openapi.okx.com"
    ) as client:
        rows = await OkxLivePrivateRestClient(
            client, settings=live_settings()
        ).order_history()

    assert rows == [{"ordId": "300"}]
    assert observed == [None, "300"]


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


def _lease_settings(**updates) -> Settings:
    values = {
        "okx_live_enabled": True,
        "okx_live_expected_uid": "1234567",
        "okx_live_expected_main_uid": "1234568",
    }
    values.update(updates)
    return live_settings(**values)


@pytest.mark.asyncio
async def test_opt_in_live_get_lease_pins_signature_and_has_no_demo_header() -> None:
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.method == "GET"
        assert request.url.path == "/api/v5/account/config"
        assert "x-simulated-trading" not in request.headers
        assert request.headers["OK-ACCESS-KEY"] == "live-key"
        return httpx.Response(
            200,
            json={
                "code": "0",
                "msg": "",
                "data": [{"uid": "1234567", "mainUid": "1234568"}],
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://openapi.okx.com"
    ) as http:
        client = OkxLivePinnedGetClientV1(http, settings=_lease_settings())
        with pytest.raises(OkxPrivateApiError) as without_lease:
            await client.account_config()
        assert without_lease.value.code == "live_get_lease_invalid"
        with client.pinned_get_lease() as lease:
            assert len(lease.binding_id) == 64
            assert client.active_read_session_binding_id == lease.binding_id
            rows = await client.account_config()
        with pytest.raises(OkxPrivateApiError) as closed:
            lease.verify_current()
        assert closed.value.code == "live_get_lease_invalid"

    assert rows == [{"uid": "1234567", "mainUid": "1234568"}]
    assert len(seen) == 1
    assert "live-key" not in repr(lease)


@pytest.mark.asyncio
async def test_opt_in_live_get_lease_rejects_setting_change_after_response() -> None:
    settings = _lease_settings()
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        settings.okx_live_rest_base_url = "https://eea.okx.com"
        return httpx.Response(200, json={"code": "0", "msg": "", "data": []})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://openapi.okx.com"
    ) as http:
        client = OkxLivePinnedGetClientV1(http, settings=settings)
        with client.pinned_get_lease(), pytest.raises(OkxPrivateApiError) as caught:
            await client.account_config()
    assert caught.value.code == "live_get_lease_settings_changed"
    assert calls == 1


@pytest.mark.asyncio
async def test_opt_in_live_get_lease_rechecks_after_signing_before_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _lease_settings()
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"code": "0", "msg": "", "data": []})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://openapi.okx.com"
    ) as http:
        client = OkxLivePinnedGetClientV1(http, settings=settings)
        original_headers = client._headers

        def change_after_signing(**kwargs):
            headers = original_headers(**kwargs)
            settings.okx_live_expected_uid = SecretStr("7654321")
            return headers

        monkeypatch.setattr(client, "_headers", change_after_signing)
        with client.pinned_get_lease(), pytest.raises(OkxPrivateApiError) as caught:
            await client.account_config()
    assert caught.value.code == "live_get_lease_settings_changed"
    assert calls == 0


@pytest.mark.asyncio
async def test_opt_in_live_get_lease_is_single_task_and_not_nested() -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"code": "0", "msg": "", "data": []})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://openapi.okx.com"
    ) as http:
        client = OkxLivePinnedGetClientV1(http, settings=_lease_settings())
        with client.pinned_get_lease():
            with pytest.raises(OkxPrivateApiError) as nested, client.pinned_get_lease():
                pass
            assert nested.value.code == "live_get_lease_invalid"
            child = asyncio.create_task(client.account_config())
            with pytest.raises(OkxPrivateApiError) as detached:
                await child
            assert detached.value.code == "live_get_lease_invalid"
    assert calls == 0


@pytest.mark.asyncio
async def test_opt_in_live_get_lease_never_retries_and_denies_post() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("synthetic unavailable", request=request)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://openapi.okx.com"
    ) as http:
        client = OkxLivePinnedGetClientV1(
            http, settings=_lease_settings(okx_live_read_max_retries=5)
        )
        with client.pinned_get_lease():
            with pytest.raises(OkxPrivateApiError) as read:
                await client.account_config()
            assert read.value.code == "transport_error"
            with pytest.raises(OkxPrivateApiError) as write:
                await _OkxPrivateRestClientBase._request(
                    client,
                    "POST",
                    "/api/v5/trade/order",
                    body={"instId": "BTC-USDT-SWAP"},
                    write=False,
                )
            assert write.value.code == "live_writes_disabled"
    assert calls == 1


@pytest.mark.asyncio
async def test_opt_in_live_get_lease_requires_mock_and_explicit_enablement() -> None:
    settings = _lease_settings()
    client = OkxLivePinnedGetClientV1(settings=settings)
    with (
        pytest.raises(OkxPrivateApiError) as no_test_transport,
        client.pinned_get_lease(),
    ):
        pass
    assert no_test_transport.value.code == "live_read_transport_invalid"

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, json={"code": "0", "data": []})
        ),
        base_url="https://openapi.okx.com",
    ) as http:
        client = OkxLivePinnedGetClientV1(http, settings=live_settings())
        with pytest.raises(OkxPrivateApiError) as disabled, client.pinned_get_lease():
            pass
    assert disabled.value.code == "live_read_configuration_invalid"


@pytest.mark.asyncio
async def test_opt_in_live_get_lease_rejects_bad_uid_and_write_flag() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, json={"code": "0", "data": []})
        ),
        base_url="https://openapi.okx.com",
    ) as http:
        bad_uid = _lease_settings(okx_live_expected_uid="synthetic-uid")
        client = OkxLivePinnedGetClientV1(http, settings=bad_uid)
        with pytest.raises(OkxPrivateApiError) as identity, client.pinned_get_lease():
            pass
        assert identity.value.code == "live_read_identity_unpinned"

        write_enabled = _lease_settings()
        write_enabled.okx_live_allow_order_writes = True
        client = OkxLivePinnedGetClientV1(http, settings=write_enabled)
        with pytest.raises(OkxPrivateApiError) as write, client.pinned_get_lease():
            pass
        assert write.value.code == "live_read_configuration_invalid"
