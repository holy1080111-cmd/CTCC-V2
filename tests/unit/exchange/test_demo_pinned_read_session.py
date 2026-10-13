"""A structural Demo read cannot mix credential sessions across awaits/retries."""

from __future__ import annotations

import json

import httpx
import pytest
from pydantic import SecretStr

from app.config.settings import Settings
from app.exchange.okx.errors import OkxPrivateApiError
from app.exchange.okx.private_rest import OkxDemoPrivateRestClient
from app.okx_demo import OkxDemoUnavailableError
from app.okx_demo.service import OkxDemoService


def _settings(**updates) -> Settings:
    values = {
        "environment": "test",
        "trading_mode": "okx_demo",
        "okx_demo_enabled": True,
        "okx_demo_api_key": "original-key",
        "okx_demo_api_secret": "original-secret",
        "okx_demo_api_passphrase": "original-passphrase",
        "okx_demo_read_max_retries": 0,
    }
    values.update(updates)
    return Settings(_env_file=None, **values)


def _config(uid: str = "123456", main_uid: str = "123456") -> dict:
    return {
        "uid": uid,
        "mainUid": main_uid,
        "acctLv": "2",
        "posMode": "net_mode",
    }


def _balance() -> dict:
    return {
        "totalEq": "100",
        "isoEq": "0",
        "adjEq": "100",
        "availEq": "100",
        "uTime": "1785858063000",
        "details": [
            {
                "ccy": "USDT",
                "eq": "100",
                "eqUsd": "100",
                "availEq": "100",
                "availBal": "100",
                "cashBal": "100",
                "frozenBal": "0",
                "upl": "0",
            }
        ],
    }


def _ok(data: list[dict]) -> httpx.Response:
    return httpx.Response(200, json={"code": "0", "data": data})


class _Repository:
    def __init__(self) -> None:
        self.saved: list[dict] = []
        self.failures: list[str] = []

    async def sync_snapshot(self, **values) -> None:
        self.saved.append(values)

    async def mark_failure(self, message: str) -> None:
        self.failures.append(message)


def _service(settings: Settings, handler):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    private = OkxDemoPrivateRestClient(client, settings=settings)
    repository = _Repository()
    service = OkxDemoService(private, object(), repository, settings=settings)
    return client, private, repository, service


def _normal_response(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/api/v5/account/config":
        return _ok([_config()])
    if path == "/api/v5/account/balance":
        return _ok([_balance()])
    return _ok([])


@pytest.mark.asyncio
async def test_pinned_reconcile_uses_one_opaque_binding_across_gather() -> None:
    settings = _settings()
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return _normal_response(request)

    client, private, repository, service = _service(settings, handler)
    async with client:
        with private.pinned_read_session() as session:
            before = await service.account_config()
            result = await service.reconcile()
            session.verify_current()

    assert before.uid == result.account_config.uid == "123456"
    assert result.credential_session_binding_id == session.binding_id
    assert len(session.binding_id) == 64
    assert repository.saved[0]["credential_session_binding_id"] == session.binding_id
    assert (
        sum(request.url.path == "/api/v5/account/config" for request in requests) == 3
    )
    assert all(request.method == "GET" for request in requests)
    assert all(
        request.headers["ok-access-key"] == "original-key" for request in requests
    )
    assert all(request.headers["x-simulated-trading"] == "1" for request in requests)
    assert all(request.url.host == "openapi.okx.com" for request in requests)
    assert "original-secret" not in repr(session)
    assert "original-key" not in json.dumps(repository.saved[0], default=str)


@pytest.mark.asyncio
@pytest.mark.parametrize("changed_field", ("key", "origin"))
async def test_settings_mutation_during_get_denies_reconcile(
    changed_field: str,
) -> None:
    settings = _settings()
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/v5/account/config":
            if changed_field == "key":
                settings.okx_demo_api_key = SecretStr("rotated-key")
            else:
                settings.okx_demo_rest_base_url = "https://us.okx.com"
        return _normal_response(request)

    client, private, repository, service = _service(settings, handler)
    async with client:
        with private.pinned_read_session(), pytest.raises(OkxPrivateApiError) as error:
            await service.reconcile()

    assert error.value.code == "demo_read_session_settings_changed"
    assert len(requests) == 1
    assert requests[0].url.host == "openapi.okx.com"
    assert requests[0].headers["ok-access-key"] == "original-key"
    assert repository.saved == []


@pytest.mark.asyncio
async def test_retry_keeps_pinned_signature_and_session() -> None:
    settings = _settings(okx_demo_read_max_retries=1)
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/v5/account/config" and len(requests) == 1:
            raise httpx.ReadTimeout("test timeout")
        return _normal_response(request)

    client, private, repository, service = _service(settings, handler)
    async with client:
        with private.pinned_read_session() as session:
            result = await service.reconcile()

    assert result.credential_session_binding_id == session.binding_id
    assert len(repository.saved) == 1
    assert all(
        request.headers["ok-access-key"] == "original-key" for request in requests
    )
    assert all(request.headers["x-simulated-trading"] == "1" for request in requests)
    assert (
        sum(request.url.path == "/api/v5/account/config" for request in requests) == 3
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ("config_zero", "config_two", "balance_two"))
async def test_reconcile_rejects_account_row_cardinality(source: str) -> None:
    settings = _settings()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v5/account/config":
            if source == "config_zero":
                return _ok([])
            if source == "config_two":
                return _ok([_config(), _config("999999")])
        if request.url.path == "/api/v5/account/balance" and source == "balance_two":
            return _ok([_balance(), _balance()])
        return _normal_response(request)

    client, private, repository, service = _service(settings, handler)
    async with client:
        with private.pinned_read_session(), pytest.raises(OkxDemoUnavailableError):
            await service.reconcile()

    assert repository.saved == []


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ("uid", "main_uid", "main_uid_missing"))
async def test_reconcile_rejects_account_identity_change(change: str) -> None:
    settings = _settings()
    configs = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal configs
        if request.url.path == "/api/v5/account/config":
            configs += 1
            if configs == 2:
                value = _config()
                if change == "uid":
                    value["uid"] = "999999"
                elif change == "main_uid":
                    value["mainUid"] = "888888"
                else:
                    value.pop("mainUid")
                return _ok([value])
        return _normal_response(request)

    client, private, repository, service = _service(settings, handler)
    async with client:
        with private.pinned_read_session(), pytest.raises(OkxDemoUnavailableError):
            await service.reconcile()

    assert configs == 2
    assert repository.saved == []


@pytest.mark.asyncio
async def test_pinned_session_forbids_post_and_closed_session_use() -> None:
    settings = _settings()
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return _normal_response(request)

    client, private, _, _ = _service(settings, handler)
    async with client:
        with private.pinned_read_session() as session:
            with pytest.raises(OkxPrivateApiError) as error:
                await private.place_order({"instId": "BTC-USDT-SWAP"})
            assert error.value.code == "demo_read_session_get_only"
        with pytest.raises(OkxPrivateApiError) as error:
            session.verify_current()
        assert error.value.code == "demo_read_session_invalid"

    assert requests == []


@pytest.mark.parametrize("mutation", ("transport", "clock"))
def test_production_pinned_session_rejects_runtime_adapter_change(
    mutation: str,
) -> None:
    settings = _settings(environment="production")
    private = OkxDemoPrivateRestClient(settings=settings)

    with private.pinned_read_session() as session:
        if mutation == "transport":
            private._external_client = object()
        else:
            private._clock_injected = True
        with pytest.raises(OkxPrivateApiError) as error:
            session.verify_current()

    assert error.value.code == "demo_read_transport_invalid"
