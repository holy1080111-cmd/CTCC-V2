from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from app.config.settings import Settings
from app.exchange.okx.errors import OkxPrivateApiError
from app.exchange.okx.private_rest import (
    OkxLiveExecutionRestClient,
    OkxLivePrivateRestClient,
    _OkxPrivateRestClientBase,
)


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
        "okx_live_expected_uid": "synthetic-live-uid",
        "okx_live_expected_main_uid": "synthetic-live-main-uid",
        "api_token": "x" * 40,
        "web_concurrency": 1,
        "okx_live_read_max_retries": 5,
    }
    values.update(updates)
    return Settings(_env_file=None, **values)


@pytest.mark.asyncio
async def test_read_only_live_client_blocks_explicit_shared_transport_order_call() -> (
    None
):
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        pytest.fail("Read-only Live client reached HTTP with an order")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://openapi.okx.com"
    ) as http:
        client = OkxLivePrivateRestClient(http, settings=execution_settings())
        with pytest.raises(OkxPrivateApiError) as error:
            await _OkxPrivateRestClientBase._request(
                client,
                "POST",
                "/api/v5/trade/order",
                body={"instId": "BTC-USDT-SWAP", "sz": "1"},
                write=False,
            )
    assert error.value.code == "live_writes_disabled"
    assert requests == []


@pytest.mark.asyncio
async def test_live_read_has_no_demo_header_and_maintenance_has_zero_http() -> None:
    seen: list[tuple[str, str]] = []
    fixed = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)

    def handler(request: httpx.Request) -> httpx.Response:
        assert "x-simulated-trading" not in request.headers
        seen.append((request.method, request.url.path))
        assert "expTime" not in request.headers
        if request.url.path == "/api/v5/account/max-size":
            return httpx.Response(
                200,
                json={
                    "code": "0",
                    "msg": "",
                    "data": [{"maxBuy": "2", "maxSell": "2"}],
                },
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
        for operation, payload in (
            ("cancel_all_after", {"timeOut": "30", "tag": "CTCCV168"}),
            ("cancel_order", {"instId": "BTC-USDT-SWAP", "ordId": "synthetic"}),
            (
                "close_position",
                {"instId": "BTC-USDT-SWAP", "mgnMode": "cross", "posSide": "net"},
            ),
        ):
            with pytest.raises(OkxPrivateApiError) as maintenance_error:
                await getattr(live, operation)(payload)
            assert maintenance_error.value.code == (
                "live_maintenance_authority_unavailable"
            )
        with pytest.raises(OkxPrivateApiError) as error:
            await live.place_order({"instId": "BTC-USDT-SWAP", "sz": "1"})
        assert error.value.code == "live_qualification_authority_unavailable"

    assert seen == [("GET", "/api/v5/account/max-size")]


@pytest.mark.asyncio
async def test_legacy_live_write_adapter_never_sets_demo_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "app.exchange.okx.private_rest.enforce_live_final_dispatch",
        lambda method, path: None,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert "x-simulated-trading" not in request.headers
        seen.append((request.method, request.url.path))
        return httpx.Response(
            200,
            json={"code": "0", "msg": "", "data": [{"sCode": "0", "sMsg": ""}]},
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://openapi.okx.com"
    ) as http:
        live = OkxLiveExecutionRestClient(http, settings=execution_settings())
        await live.cancel_all_after({"timeOut": "30", "tag": "CTCCV168"})
        await live.cancel_order({"instId": "BTC-USDT-SWAP", "ordId": "synthetic"})
    assert seen == [
        ("POST", "/api/v5/trade/cancel-all-after"),
        ("POST", "/api/v5/trade/cancel-order"),
    ]


@pytest.mark.asyncio
async def test_direct_live_order_precheck_has_zero_http_io() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        pytest.fail("unqualified Live precheck reached private HTTP")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://openapi.okx.com"
    ) as http:
        client = OkxLiveExecutionRestClient(http, settings=execution_settings())
        with pytest.raises(OkxPrivateApiError) as caught:
            await client.order_precheck({"instId": "BTC-USDT-SWAP", "sz": "1"})
    assert caught.value.code == "live_qualification_authority_unavailable"
    assert requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,body",
    (
        (
            "/api/v5/trade/cancel-order",
            {"instId": "BTC-USDT-SWAP", "ordId": "synthetic", "reduceOnly": True},
        ),
        (
            "/api/v5/trade/close-position",
            {"instId": "BTC-USDT-SWAP", "mgnMode": "isolated"},
        ),
        (
            "/api/v5/trade/cancel-all-after",
            {"timeOut": "30", "tag": "CTCCV168"},
        ),
    ),
)
async def test_live_maintenance_direct_base_call_has_zero_http_without_permit(
    path: str, body: dict[str, object]
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        pytest.fail("Unqualified Live maintenance reached private HTTP")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://openapi.okx.com"
    ) as http:
        client = OkxLiveExecutionRestClient(http, settings=execution_settings())
        with pytest.raises(OkxPrivateApiError) as caught:
            await _OkxPrivateRestClientBase._request(
                client, "POST", path, body=body, write=False
            )
    assert caught.value.code == "live_maintenance_authority_unavailable"
    assert requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "params"),
    (
        ({"timeOut": "0"}, None),
        ({"timeOut": 0}, None),
        ({"timeOut": "121"}, None),
        ({"timeOut": "30", "unexpected": True}, None),
        ({"timeOut": "30", "tag": "bad!"}, None),
        ({"timeOut": "30"}, {"timeOut": "0"}),
    ),
)
async def test_live_caa_invalid_or_query_overridden_body_has_zero_http(payload, params):
    requests = []

    def handler(request):
        requests.append(request)
        pytest.fail("invalid Live CAA reached private HTTP")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://openapi.okx.com"
    ) as http:
        client = OkxLiveExecutionRestClient(http, settings=execution_settings())
        with pytest.raises(OkxPrivateApiError) as caught:
            await client._request(
                "POST", "/api/v5/trade/cancel-all-after", body=payload, params=params
            )
    assert caught.value.code == "cancel_all_after_payload_rejected"
    assert requests == []


@pytest.mark.asyncio
async def test_live_caa_rejects_signed_zero_even_if_caller_mutates_dict_to_positive(
    monkeypatch,
):
    requests = []

    def handler(request):
        requests.append(request)
        pytest.fail("zero-timeout Live CAA reached private HTTP")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://openapi.okx.com"
    ) as http:
        client = OkxLiveExecutionRestClient(http, settings=execution_settings())
        payload = {"timeOut": "0"}
        original_headers = client._headers

        def mutate_after_serialization(**kwargs):
            result = original_headers(**kwargs)
            payload["timeOut"] = "30"
            return result

        monkeypatch.setattr(client, "_headers", mutate_after_serialization)
        with pytest.raises(OkxPrivateApiError) as caught:
            await client.cancel_all_after(payload)
    assert payload == {"timeOut": "30"}
    assert caught.value.code == "cancel_all_after_payload_rejected"
    assert requests == []


@pytest.mark.asyncio
async def test_legacy_execution_write_transport_failure_is_never_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    # Isolated MockTransport regression of the old write-attempt semantics.
    # Production's final maintenance authority check remains fail-closed.
    monkeypatch.setattr(
        "app.exchange.okx.private_rest.enforce_live_final_dispatch",
        lambda method, path: None,
    )

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
            ).cancel_order({"instId": "BTC-USDT-SWAP", "ordId": "synthetic"})

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
            await OkxLiveExecutionRestClient(client, settings=settings).place_order(
                {"instId": "BTC-USDT-SWAP"}
            )

    assert calls == 0
    assert exc_info.value.code == "live_execution_not_enabled"


@pytest.mark.asyncio
async def test_legacy_empty_write_ack_is_ambiguous_and_not_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    monkeypatch.setattr(
        "app.exchange.okx.private_rest.enforce_live_final_dispatch",
        lambda method, path: None,
    )

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
            ).cancel_order({"instId": "BTC-USDT-SWAP", "ordId": "synthetic"})

    assert calls == 1
    assert exc_info.value.code == "ambiguous_response"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,path",
    [
        ("POST", "/api/v5/trade/order"),
        ("POST", "/api/v5/trade/order-precheck"),
        ("POST", "/api/v5/trade/batch-orders"),
        ("POST", "/api/v5/trade/order-algo"),
        ("POST", "/api/v5/trade/amend-order"),
        ("POST", "/api/v5/trade/amend-algos"),
        ("POST", "/api/v5/trade/unknown-write"),
        ("POST", "/api/v5/account/set-leverage"),
        ("PUT", "/api/v5/trade/cancel-order"),
    ],
)
async def test_live_entry_cannot_bypass_qualification_with_flags_or_direct_call(
    method: str,
    path: str,
) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        pytest.fail("Unqualified Live request reached HTTP")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://openapi.okx.com"
    ) as client:
        live = OkxLiveExecutionRestClient(client, settings=execution_settings())
        with pytest.raises(OkxPrivateApiError) as error:
            await live._request(
                method,
                path,
                body={"passed": True, "reduceOnly": True, "armed": True},
                write=False,
            )
    assert error.value.code == "live_qualification_authority_unavailable"
    assert calls == 0


@pytest.mark.asyncio
async def test_live_configuration_is_checked_again_after_signing_before_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    settings = execution_settings()

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        pytest.fail("Disabled Live write reached HTTP")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://openapi.okx.com"
    ) as client:
        live = OkxLiveExecutionRestClient(client, settings=settings)
        original_headers = live._headers

        def disable_after_signing(**kwargs: str) -> dict[str, str]:
            result = original_headers(**kwargs)
            settings.okx_live_allow_order_writes = False
            return result

        monkeypatch.setattr(live, "_headers", disable_after_signing)
        with pytest.raises(OkxPrivateApiError) as error:
            await live.cancel_order({"instId": "BTC-USDT-SWAP", "ordId": "synthetic"})
    assert error.value.code == "live_execution_not_enabled"
    assert calls == 0


def test_live_expiry_header_does_not_itself_grant_dispatch_authority() -> None:
    live = OkxLiveExecutionRestClient(
        settings=execution_settings(),
        clock=lambda: datetime(2026, 8, 9, 12, 0, tzinfo=UTC),
    )
    assert live._request_extra_headers(
        method="POST", path="/api/v5/trade/order", write=True
    ) == {"expTime": "1786276805000"}
    with pytest.raises(OkxPrivateApiError) as error:
        live._before_send(method="POST", path="/api/v5/trade/order")
    assert error.value.code == "live_qualification_authority_unavailable"
