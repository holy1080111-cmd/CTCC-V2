"""Registered API order routes reach the concrete, fail-closed OKX transport.

All account and market preflight inputs are synthetic. MockTransport records any
private HTTP request and cannot reach the exchange; these tests establish only
current hard denial, not qualified G12/R7/R6 execution authority.
"""

from __future__ import annotations

import importlib

import httpx
import pytest

from app.api.security import require_ctcc_token
from app.domain.demo_automation import EXECUTE_PHRASE
from app.domain.okx_live import LIVE_ARM_PHRASE, OkxLiveArmRequest
from app.exchange.okx.errors import OkxPrivateApiError
from app.exchange.okx.private_rest import (
    OkxDemoPrivateRestClient,
    OkxLiveExecutionRestClient,
)
from app.main import app
from app.okx_demo.service import OkxDemoService
from tests.unit.test_demo_automation import FakeDemo, make_service
from tests.unit.test_okx_demo_service import (
    FakePrivate,
    FakePublic,
)
from tests.unit.test_okx_demo_service import (
    request as demo_order,
)
from tests.unit.test_okx_demo_service import (
    settings as demo_settings,
)
from tests.unit.test_okx_live_service import live_order, service_fixture


@pytest.mark.asyncio
@pytest.mark.parametrize("order_type", ("market", "limit", "fok"))
async def test_registered_demo_order_route_cannot_send_unqualified_entry(
    monkeypatch: pytest.MonkeyPatch, order_type: str
) -> None:
    observed: list[httpx.Request] = []

    def exchange_handler(request: httpx.Request) -> httpx.Response:
        observed.append(request)
        pytest.fail("unqualified Demo entry reached private HTTP")

    config = demo_settings()
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(exchange_handler),
        base_url="https://www.okx.com",
    ) as exchange_http:
        transport = OkxDemoPrivateRestClient(exchange_http, settings=config)

        class PreflightPrivate(FakePrivate):
            async def place_order(self, payload: dict[str, object]):
                return await transport.place_order(payload)

        service = OkxDemoService(
            PreflightPrivate(), FakePublic(), None, settings=config
        )
        route = importlib.import_module("app.api.routers.okx_demo")
        monkeypatch.setattr(route, "okx_demo_service", service)
        app.dependency_overrides[require_ctcc_token] = lambda: None
        try:
            order = demo_order(
                order_type=order_type,
                price=None if order_type == "market" else "100010",
            )
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
            ) as api_http:
                response = await api_http.post(
                    "/api/okx-demo/orders",
                    json=order.model_dump(mode="json"),
                )
        finally:
            app.dependency_overrides.pop(require_ctcc_token, None)

    assert response.status_code == 502
    assert response.json()["detail"]["exchange_code"] == (
        "demo_qualification_authority_unavailable"
    )
    assert observed == []


@pytest.mark.asyncio
async def test_registered_live_order_route_cannot_send_after_full_preflight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[httpx.Request] = []

    def exchange_handler(request: httpx.Request) -> httpx.Response:
        observed.append(request)
        if request.url.path == "/api/v5/trade/order":
            pytest.fail("unqualified Live entry reached private HTTP")
        if request.url.path == "/api/v5/account/max-size":
            return httpx.Response(
                200, json={"code": "0", "data": [{"maxBuy": "10", "maxSell": "10"}]}
            )
        pytest.fail(f"unexpected private HTTP path: {request.url.path}")

    service, _, _, intents, _ = service_fixture()
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(exchange_handler),
        base_url="https://openapi.okx.com",
    ) as exchange_http:
        service.execution_client = OkxLiveExecutionRestClient(
            exchange_http, settings=service.settings
        )
        await service.arm(
            OkxLiveArmRequest(duration_seconds=60, confirmation=LIVE_ARM_PHRASE)
        )
        route = importlib.import_module("app.api.routers.okx_live")
        monkeypatch.setattr(route, "okx_live_service", service)
        app.dependency_overrides[require_ctcc_token] = lambda: None
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
            ) as api_http:
                response = await api_http.post(
                    "/api/okx-live/orders",
                    json=live_order().model_dump(mode="json"),
                )
        finally:
            app.dependency_overrides.pop(require_ctcc_token, None)

    assert response.status_code == 502
    # Live API intentionally withholds long internal exchange/safety codes.
    assert response.json()["detail"] == {
        "message": "okx_live_exchange_request_failed",
        "exchange_code": None,
    }
    assert [request.url.path for request in observed] == [
        "/api/v5/account/max-size",
    ]
    assert intents.rows["CTCCLabcdef"]["status"] == "rejected"
    assert intents.rows["CTCCLabcdef"]["detail_codes"] == ["order_precheck_rejected"]
    assert service.arm_status().armed is False


@pytest.mark.asyncio
async def test_live_service_rejects_unqualified_precheck_before_caa_or_order():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        pytest.fail(
            f"unqualified Live request reached private HTTP: {request.url.path}"
        )

    service, read, fake_execution, _, _ = service_fixture()
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://openapi.okx.com"
    ) as exchange_http:
        transport = OkxLiveExecutionRestClient(exchange_http, settings=service.settings)

        class ServiceExecution:
            def __getattr__(self, name):
                return getattr(fake_execution, name)

            async def order_precheck(self, payload):
                return await transport.order_precheck(payload)

            async def cancel_all_after(self, payload):
                return await transport.cancel_all_after(payload)

            async def place_order(self, payload):
                return await transport.place_order(payload)

        service.execution_client = ServiceExecution()
        await service.arm(
            OkxLiveArmRequest(duration_seconds=60, confirmation=LIVE_ARM_PHRASE)
        )
        with pytest.raises(OkxPrivateApiError) as caught:
            await service.place_order(live_order())

    assert caught.value.code == "live_qualification_authority_unavailable"
    assert seen == []
    assert service.arm_status().armed is False
    assert read.position_rows == []


@pytest.mark.asyncio
async def test_registered_demo_automation_run_once_reaches_concrete_denial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[httpx.Request] = []

    def exchange_handler(request: httpx.Request) -> httpx.Response:
        observed.append(request)
        pytest.fail("unqualified automation entry reached private HTTP")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(exchange_handler),
        base_url="https://www.okx.com",
    ) as exchange_http:
        transport = OkxDemoPrivateRestClient(exchange_http, settings=demo_settings())

        class AutomationPreflight(FakeDemo):
            async def place_order(self, order, *, before_submit=None):
                if before_submit is not None:
                    before_submit()
                self.place_calls.append(order)
                return await transport.place_order(
                    {"instId": order.instrument_id, "ordType": order.order_type}
                )

        preflight = AutomationPreflight()
        service = make_service(preflight)
        await service.recover()
        await service.arm()
        route = importlib.import_module("app.api.routers.demo_automation")
        monkeypatch.setattr(route, "safe_demo_automation", service)
        app.dependency_overrides[require_ctcc_token] = lambda: None
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
            ) as api_http:
                response = await api_http.post(
                    "/api/demo-automation/run-once",
                    json={"execute": True, "confirmation": EXECUTE_PHRASE},
                )
        finally:
            app.dependency_overrides.pop(require_ctcc_token, None)

    assert response.status_code == 200
    assert response.json()["results"][-1]["outcome"] == "error"
    assert len(preflight.place_calls) == 1
    assert preflight.place_calls[0].order_type == "fok"
    assert observed == []
    status = await service.status()
    assert status.emergency_stop is True
    assert status.armed is False
    assert "order_submission_outcome_unconfirmed" in status.lock_reasons


@pytest.mark.asyncio
async def test_registered_demo_automation_cannot_set_leverage_before_denied_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[httpx.Request] = []

    def exchange_handler(request: httpx.Request) -> httpx.Response:
        observed.append(request)
        pytest.fail("unqualified Demo maintenance reached private HTTP")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(exchange_handler),
        base_url="https://www.okx.com",
    ) as exchange_http:
        transport = OkxDemoPrivateRestClient(exchange_http, settings=demo_settings())

        class AutomationPreflight(FakeDemo):
            async def set_leverage(self, request):
                return await transport.set_leverage(
                    {
                        "instId": request.instrument_id,
                        "lever": str(request.leverage),
                        "mgnMode": request.margin_mode,
                    }
                )

        preflight = AutomationPreflight()
        service = make_service(preflight)
        await service.recover()
        await service.arm()
        route = importlib.import_module("app.api.routers.demo_automation")
        monkeypatch.setattr(route, "safe_demo_automation", service)
        app.dependency_overrides[require_ctcc_token] = lambda: None
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
            ) as api_http:
                response = await api_http.post(
                    "/api/demo-automation/run-once",
                    json={"execute": True, "confirmation": EXECUTE_PHRASE},
                )
        finally:
            app.dependency_overrides.pop(require_ctcc_token, None)

    assert response.status_code == 200
    assert response.json()["results"][-1]["outcome"] == "error"
    assert preflight.place_calls == []
    assert observed == []
    status = await service.status()
    assert status.emergency_stop is True
    assert status.armed is False
