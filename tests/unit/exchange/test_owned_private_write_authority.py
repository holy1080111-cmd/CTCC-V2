"""The shared transport still denies entry if legacy route classifiers vanish.

Only synthetic credentials and MockTransport are used; no exchange IO occurs.
"""

from __future__ import annotations

import httpx
import pytest

from app.exchange.okx.errors import OkxPrivateApiError
from app.exchange.okx.private_rest import (
    OkxDemoPrivateRestClient,
    OkxLiveExecutionRestClient,
)
from app.trade_qualification.dispatch_ownership import (
    DispatchOwnership,
    DispatchSlot,
)
from tests.unit.exchange.test_live_execution_rest import execution_settings
from tests.unit.exchange.test_private_rest import demo_settings


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("client_type", "settings_factory", "origin"),
    (
        (OkxDemoPrivateRestClient, demo_settings, "https://www.okx.com"),
        (OkxLiveExecutionRestClient, execution_settings, "https://openapi.okx.com"),
    ),
)
@pytest.mark.parametrize(
    "path",
    (
        "/api/v5/trade/order",
        "/api/v5/trade/order-precheck",
        "/api/v5/trade/batch-orders",
        "/api/v5/trade/order-algo",
        "/api/v5/trade/amend-order",
        "/api/v5/account/set-leverage",
        "/api/v5/trade/cancel-order",
        "/api/v5/trade/close-position",
        "/api/v5/trade/cancel-all-after",
        "/api/v5/trade/unrecognized-entry",
    ),
)
async def test_independent_owned_boundary_denies_after_legacy_gates_removed(
    monkeypatch, client_type, settings_factory, origin, path
):
    observed: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed.append(request)
        pytest.fail("unqualified private write reached HTTP")

    # This deliberately simulates removal of both old per-environment checks.
    monkeypatch.setattr(client_type, "_before_send", lambda self, **_: None)
    monkeypatch.setattr(client_type, "_final_dispatch_check", lambda self, **_: None)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url=origin
    ) as http:
        client = client_type(http, settings=settings_factory())
        with pytest.raises(OkxPrivateApiError) as error:
            await client._request(
                "POST",
                path,
                body=(
                    {"timeOut": "10"}
                    if path == "/api/v5/trade/cancel-all-after"
                    else {"passed": True, "armed": True}
                ),
                write=False,
            )

    assert error.value.code == "private_write_authority_unavailable"
    assert observed == []


@pytest.mark.asyncio
async def test_caller_claims_and_truthy_owner_result_cannot_become_a_permit(
    monkeypatch,
):
    observed: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed.append(request)
        pytest.fail("unqualified private write reached HTTP")

    monkeypatch.setattr(
        OkxDemoPrivateRestClient, "_before_send", lambda self, **_: None
    )
    monkeypatch.setattr(
        OkxDemoPrivateRestClient, "_final_dispatch_check", lambda self, **_: None
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://www.okx.com"
    ) as http:
        client = OkxDemoPrivateRestClient(http, settings=demo_settings())
        client._qualification_dispatch_owner = {"passed": True}
        client._qualification_dispatch_slot = {"execution_authority": True}
        with pytest.raises(OkxPrivateApiError) as foreign:
            await client.place_order({"passed": True, "dispatch_ownership": True})
        assert foreign.value.code == "private_write_authority_unavailable"

        # Even reflection-forged exact types and a truthy READY method return
        # cannot supply the missing exact-request dispatch authority.
        monkeypatch.setattr(DispatchOwnership, "require_ready", lambda self, _: True)
        client._qualification_dispatch_owner = object.__new__(DispatchOwnership)
        client._qualification_dispatch_slot = object.__new__(DispatchSlot)
        with pytest.raises(OkxPrivateApiError) as forged:
            await client.place_order({"passed": True})
        assert forged.value.code == "private_write_authority_unavailable"

    assert observed == []
