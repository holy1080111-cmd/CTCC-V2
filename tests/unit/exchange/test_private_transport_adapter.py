from __future__ import annotations

import httpx
import pytest

from app.config.settings import Settings
from app.exchange.okx.errors import OkxPrivateApiError
from app.exchange.okx.private_rest import (
    OkxDemoPrivateRestClient,
    OkxLiveExecutionRestClient,
    OkxLivePrivateRestClient,
)
from tests.unit.exchange.test_live_execution_rest import execution_settings


def make_client(kind, client):
    if kind == "execution":
        return OkxLiveExecutionRestClient(client, settings=execution_settings())
    settings = Settings(
        _env_file=None,
        okx_demo_api_key="synthetic-demo-key",
        okx_demo_api_secret="synthetic-demo-secret",
        okx_demo_api_passphrase="synthetic-demo-passphrase",
        okx_live_api_key="synthetic-live-key",
        okx_live_api_secret="synthetic-live-secret",
        okx_live_api_passphrase="synthetic-live-passphrase",
    )
    return (
        OkxDemoPrivateRestClient(client, settings=settings)
        if kind == "demo"
        else OkxLivePrivateRestClient(client, settings=settings)
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["demo", "readonly", "execution"])
@pytest.mark.parametrize(
    "unsafe",
    ["simulation_header", "host", "auth", "hook", "params", "cookies", "network"],
)
async def test_injected_client_cannot_change_signed_request(kind, unsafe):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"code": "0", "data": []})

    async def hook(request):
        request.url = request.url.copy_with(path="/api/v5/trade/order")

    options = {"transport": httpx.MockTransport(handler), "trust_env": False}
    if unsafe == "simulation_header":
        options["headers"] = {"X-Simulated-Trading": "1"}
    elif unsafe == "host":
        options["headers"] = {"Host": "uncontrolled.example"}
    elif unsafe == "auth":
        options["auth"] = ("synthetic", "synthetic")
    elif unsafe == "hook":
        options["event_hooks"] = {"request": [hook]}
    elif unsafe == "params":
        options["params"] = {"instId": "uncontrolled"}
    elif unsafe == "cookies":
        options["cookies"] = {"session": "synthetic"}
    elif unsafe == "network":
        options["transport"] = httpx.AsyncHTTPTransport(retries=0)
    async with httpx.AsyncClient(**options) as client:
        with pytest.raises(OkxPrivateApiError) as error:
            await make_client(kind, client).account_config()
    assert error.value.code == "private_transport_adapter_rejected"
    assert not calls


@pytest.mark.asyncio
async def test_allowed_maintenance_does_not_follow_external_client_redirect():
    calls = []

    def handler(request):
        calls.append((request.method, request.url.path))
        return httpx.Response(307, headers={"Location": "/api/v5/trade/order"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=True
    ) as client:
        with pytest.raises(OkxPrivateApiError) as error:
            await make_client("execution", client).cancel_order(
                {"instId": "BTC-USDT-SWAP", "ordId": "synthetic"}
            )
    assert error.value.code == "transport_error"
    assert calls == [("POST", "/api/v5/trade/cancel-order")]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["demo", "readonly", "execution"])
async def test_configured_origin_and_environment_override_adapter_base(kind):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"code": "0", "data": []})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://uncontrolled.example"
    ) as client:
        await make_client(kind, client).account_config()
    assert len(calls) == 1
    assert calls[0].url.host == "openapi.okx.com"
    if kind == "demo":
        assert calls[0].headers["x-simulated-trading"] == "1"
    else:
        assert "x-simulated-trading" not in calls[0].headers


@pytest.mark.asyncio
async def test_external_client_is_rechecked_after_signing(monkeypatch):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"code": "0", "data": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        live = make_client("execution", client)
        original = live._headers

        def mutate_after_signing(**kwargs):
            headers = original(**kwargs)
            client.headers["x-simulated-trading"] = "1"
            return headers

        monkeypatch.setattr(live, "_headers", mutate_after_signing)
        with pytest.raises(OkxPrivateApiError) as error:
            await live.cancel_order({"instId": "BTC-USDT-SWAP", "ordId": "synthetic"})
    assert error.value.code == "private_transport_adapter_rejected"
    assert not calls


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["demo", "readonly", "execution"])
@pytest.mark.parametrize(
    "path",
    [
        "@uncontrolled.example/api/v5/account/config",
        "https://uncontrolled.example/api/v5/account/config",
        "//uncontrolled.example/api/v5/account/config",
        "/api/v5/account/../trade/order",
        "/api/v5/account/%2e%2e/trade/order",
        "/api/v5/account/config?other=1",
        "/api/v5/account/config#fragment",
        "/api/v5/account/config\r\n",
        "/api/v5/account\\config",
        "/api/v5//account/config",
    ],
)
async def test_noncanonical_path_is_rejected_before_signing(kind, path, monkeypatch):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: pytest.fail("HTTP reached"))
    ) as client:
        private = make_client(kind, client)
        monkeypatch.setattr(
            private, "_headers", lambda **kwargs: pytest.fail("Credentials used")
        )
        with pytest.raises(OkxPrivateApiError) as error:
            await private._request("GET", path)
    assert error.value.code == "private_transport_target_rejected"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["demo", "readonly", "execution"])
@pytest.mark.parametrize(
    "origin",
    [
        "https://uncontrolled.example",
        "http://openapi.okx.com",
        "https://synthetic:synthetic@openapi.okx.com",
        "https://openapi.okx.com:8443",
        "https://openapi.okx.com/base",
        "https://openapi.okx.com?extra=1",
        "https://openapi.okx.com#fragment",
    ],
)
async def test_mutated_origin_is_rejected_before_signing(kind, origin, monkeypatch):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: pytest.fail("HTTP reached"))
    ) as client:
        private = make_client(kind, client)
        if kind == "demo":
            private.settings.okx_demo_rest_base_url = origin
        else:
            private.settings.okx_live_rest_base_url = origin
        monkeypatch.setattr(
            private, "_headers", lambda **kwargs: pytest.fail("Credentials used")
        )
        with pytest.raises(OkxPrivateApiError) as error:
            await private.account_config()
    assert error.value.code == "private_transport_target_rejected"
