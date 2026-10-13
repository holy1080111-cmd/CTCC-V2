"""Registered legacy API writes stop before preflight and private transport.

All account and market preflight inputs are synthetic. MockTransport records any
private HTTP request and cannot reach the exchange. The independent final private
transport denial remains covered separately; these routes issue no qualification.
"""

from __future__ import annotations

import importlib
import inspect
from datetime import UTC, datetime

import httpx
import pytest

from app.api.security import require_ctcc_token
from app.domain.demo_automation import EXECUTE_PHRASE, DemoAutomationRunResult
from app.domain.okx_demo import DEMO_CONFIRMATION_PHRASE
from app.domain.okx_live import (
    LIVE_ARM_PHRASE,
    LIVE_AUTOMATION_EXECUTE_PHRASE,
    LIVE_CANCEL_PHRASE,
    LIVE_CLOSE_PHRASE,
    LIVE_LEVERAGE_PHRASE,
    OkxLiveAutomationRunResult,
)
from app.exchange.okx.errors import OkxPrivateApiError
from app.exchange.okx.private_rest import (
    OkxDemoPrivateRestClient,
    OkxLiveExecutionRestClient,
)
from app.main import app
from app.okx_demo.service import OkxDemoService
from app.okx_live.automation import ControlledLiveAutomation
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
from tests.unit.test_okx_live_automation import (
    FakeHub as FakeLiveHub,
)
from tests.unit.test_okx_live_automation import (
    FakeMarketClient as FakeLiveMarketClient,
)
from tests.unit.test_okx_live_automation import (
    FakePublic as FakeLivePublic,
)
from tests.unit.test_okx_live_automation import (
    FakeRisk as FakeLiveRisk,
)
from tests.unit.test_okx_live_automation import (
    FakeStrategy as FakeLiveStrategy,
)
from tests.unit.test_okx_live_service import (
    live_order,
    live_settings,
    seed_legacy_local_lease_for_downstream_unit_test,
    service_fixture,
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("environment", "path", "payload"),
    (
        (
            "demo",
            "/api/okx-demo/orders",
            demo_order().model_dump(mode="json"),
        ),
        (
            "demo",
            "/api/okx-demo/orders/cancel",
            {
                "instrument_id": "BTC-USDT-SWAP",
                "order_id": "123",
                "confirmation": DEMO_CONFIRMATION_PHRASE,
            },
        ),
        (
            "demo",
            "/api/okx-demo/positions/close",
            {
                "instrument_id": "BTC-USDT-SWAP",
                "confirmation": DEMO_CONFIRMATION_PHRASE,
            },
        ),
        (
            "demo",
            "/api/okx-demo/leverage",
            {
                "instrument_id": "BTC-USDT-SWAP",
                "leverage": 3,
                "confirmation": DEMO_CONFIRMATION_PHRASE,
            },
        ),
        (
            "live",
            "/api/okx-live/orders",
            live_order().model_dump(mode="json"),
        ),
        (
            "live",
            "/api/okx-live/orders/cancel",
            {
                "instrument_id": "BTC-USDT-SWAP",
                "order_id": "123",
                "idempotency_key": "CTCCXabcdef",
                "confirmation": LIVE_CANCEL_PHRASE,
            },
        ),
        (
            "live",
            "/api/okx-live/positions/close",
            {
                "instrument_id": "BTC-USDT-SWAP",
                "idempotency_key": "CTCCXabcdef",
                "confirmation": LIVE_CLOSE_PHRASE,
            },
        ),
        (
            "live",
            "/api/okx-live/leverage",
            {
                "instrument_id": "BTC-USDT-SWAP",
                "leverage": 3,
                "idempotency_key": "CTCCXabcdef",
                "confirmation": LIVE_LEVERAGE_PHRASE,
            },
        ),
    ),
)
async def test_manual_mutation_routes_never_invoke_legacy_service_or_private_http(
    monkeypatch: pytest.MonkeyPatch,
    environment: str,
    path: str,
    payload: dict[str, object],
) -> None:
    observed: list[httpx.Request] = []
    called: list[str] = []

    def exchange_handler(request: httpx.Request) -> httpx.Response:
        observed.append(request)
        pytest.fail("legacy route reached mutating private HTTP")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(exchange_handler),
        base_url="https://www.okx.com",
    ) as exchange_http:

        class LegacyServiceTrap:
            def __getattr__(self, action: str):
                async def mutate(*_args, **_kwargs):
                    called.append(action)
                    return await exchange_http.post("/api/v5/trade/order", json={})

                return mutate

        route = importlib.import_module(f"app.api.routers.okx_{environment}")
        monkeypatch.setattr(route, f"okx_{environment}_service", LegacyServiceTrap())
        app.dependency_overrides[require_ctcc_token] = lambda: None
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
            ) as api_http:
                response = await api_http.post(
                    path,
                    json={
                        **payload,
                        "passed": True,
                        "qualified_dispatch_permit": "caller-invented",
                    },
                )
        finally:
            app.dependency_overrides.pop(require_ctcc_token, None)

    assert response.status_code == 409
    assert response.json()["detail"] == (
        f"{environment}_qualified_dispatch_coordinator_unavailable"
    )
    assert called == []
    assert observed == []


@pytest.mark.asyncio
@pytest.mark.parametrize("environment", ("demo", "live"))
async def test_automation_dry_run_remains_available(
    monkeypatch: pytest.MonkeyPatch, environment: str
) -> None:
    now = datetime.now(UTC)
    calls: list[tuple[object, bool, str]] = []
    if environment == "demo":
        route = importlib.import_module("app.api.routers.demo_automation")
        path = "/api/demo-automation/run-once"
        worker_name = "safe_demo_automation"
        result = DemoAutomationRunResult(
            trigger="manual",
            execute=False,
            started_at=now,
            completed_at=now,
            results=[],
        )
    else:
        route = importlib.import_module("app.api.routers.okx_live")
        path = "/api/okx-live/automation/run-once"
        worker_name = "controlled_live_automation"
        result = OkxLiveAutomationRunResult(
            trigger="manual",
            execute=False,
            started_at=now,
            completed_at=now,
            results=[],
        )

    class DryRunWorker:
        async def run_once(self, *, symbols=None, execute=False, trigger="manual"):
            calls.append((symbols, execute, trigger))
            return result

    monkeypatch.setattr(route, worker_name, DryRunWorker())
    app.dependency_overrides[require_ctcc_token] = lambda: None
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as api_http:
            response = await api_http.post(path, json={"execute": False})
    finally:
        app.dependency_overrides.pop(require_ctcc_token, None)

    assert response.status_code == 200
    assert response.json()["execute"] is False
    assert calls == [(None, False, "manual")]


@pytest.mark.asyncio
async def test_registered_live_arm_route_rejects_absent_qualification_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _, execution, intents, _ = service_fixture()
    route = importlib.import_module("app.api.routers.okx_live")
    monkeypatch.setattr(route, "okx_live_service", service)
    app.dependency_overrides[require_ctcc_token] = lambda: None
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as api_http:
            response = await api_http.post(
                "/api/okx-live/arm",
                json={"duration_seconds": 60, "confirmation": LIVE_ARM_PHRASE},
            )
    finally:
        app.dependency_overrides.pop(require_ctcc_token, None)

    assert response.status_code == 409
    assert response.json()["detail"] == "okx_live_qualification_authority_unavailable"
    assert service.arm_status().armed is False
    assert intents.rows == {}
    assert execution.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("order_type", ("market", "limit", "fok"))
@pytest.mark.parametrize("legacy_classifier_removed", (False, True))
async def test_registered_demo_order_route_cannot_send_unqualified_entry(
    monkeypatch: pytest.MonkeyPatch,
    order_type: str,
    legacy_classifier_removed: bool,
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
        if legacy_classifier_removed:
            monkeypatch.setattr(transport, "_before_send", lambda **_: None)
            monkeypatch.setattr(transport, "_final_dispatch_check", lambda **_: None)

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

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "demo_qualified_dispatch_coordinator_unavailable"
    )
    assert observed == []


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy_classifier_removed", (False, True))
async def test_registered_live_order_route_cannot_send_after_full_preflight(
    monkeypatch: pytest.MonkeyPatch,
    legacy_classifier_removed: bool,
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
        if legacy_classifier_removed:
            monkeypatch.setattr(
                service.execution_client, "_before_send", lambda **_: None
            )
            monkeypatch.setattr(
                service.execution_client, "_final_dispatch_check", lambda **_: None
            )
        await seed_legacy_local_lease_for_downstream_unit_test(service)
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

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "live_qualified_dispatch_coordinator_unavailable"
    )
    assert observed == []
    assert intents.rows == {}
    assert service.arm_status().armed is True


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
        await seed_legacy_local_lease_for_downstream_unit_test(service)
        with pytest.raises(OkxPrivateApiError) as caught:
            await service.place_order(live_order())

    assert caught.value.code == "live_qualification_authority_unavailable"
    assert seen == []
    assert service.arm_status().armed is False
    assert read.position_rows == []


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy_classifier_removed", (False, True))
async def test_registered_demo_automation_run_once_reaches_concrete_denial(
    monkeypatch: pytest.MonkeyPatch,
    legacy_classifier_removed: bool,
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
        if legacy_classifier_removed:
            monkeypatch.setattr(transport, "_before_send", lambda **_: None)
            monkeypatch.setattr(transport, "_final_dispatch_check", lambda **_: None)

        class AutomationPreflight(FakeDemo):
            async def place_order(self, order, *, before_submit=None):
                if before_submit is not None:
                    callback_result = before_submit()
                    if inspect.isawaitable(callback_result):
                        await callback_result
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

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "demo_qualified_dispatch_coordinator_unavailable"
    )
    assert preflight.place_calls == []
    assert observed == []
    status = await service.status()
    assert status.emergency_stop is False
    assert status.armed is True


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

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "demo_qualified_dispatch_coordinator_unavailable"
    )
    assert preflight.place_calls == []
    assert observed == []
    status = await service.status()
    assert status.emergency_stop is False
    assert status.armed is True


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy_classifier_removed", (False, True))
async def test_registered_demo_scheduler_is_denied_before_creation(
    monkeypatch: pytest.MonkeyPatch,
    legacy_classifier_removed: bool,
) -> None:
    observed: list[httpx.Request] = []

    def exchange_handler(request: httpx.Request) -> httpx.Response:
        observed.append(request)
        pytest.fail("unqualified scheduled Demo entry reached private HTTP")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(exchange_handler),
        base_url="https://www.okx.com",
    ) as exchange_http:
        transport = OkxDemoPrivateRestClient(exchange_http, settings=demo_settings())
        if legacy_classifier_removed:
            monkeypatch.setattr(transport, "_before_send", lambda **_: None)
            monkeypatch.setattr(transport, "_final_dispatch_check", lambda **_: None)

        class AutomationPreflight(FakeDemo):
            async def place_order(self, order, *, before_submit=None):
                if before_submit is not None:
                    callback_result = before_submit()
                    if inspect.isawaitable(callback_result):
                        await callback_result
                self.place_calls.append(order)
                return await transport.place_order(
                    {"instId": order.instrument_id, "ordType": order.order_type}
                )

        preflight = AutomationPreflight()
        service = make_service(preflight, okx_demo_scan_initial_delay_seconds=0)
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
                response = await api_http.post("/api/demo-automation/start")
        finally:
            app.dependency_overrides.pop(require_ctcc_token, None)
            await service.stop()

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "demo_qualified_dispatch_coordinator_unavailable"
    )
    assert service.running is False
    assert preflight.place_calls == []
    assert observed == []
    status = await service.status()
    assert status.emergency_stop is False
    assert status.armed is True


@pytest.mark.asyncio
@pytest.mark.parametrize("scheduled", (False, True))
async def test_registered_live_automation_routes_deny_before_execution(
    monkeypatch: pytest.MonkeyPatch, scheduled: bool
) -> None:
    observed: list[httpx.Request] = []

    def exchange_handler(request: httpx.Request) -> httpx.Response:
        observed.append(request)
        if request.url.path == "/api/v5/account/max-size":
            return httpx.Response(
                200, json={"code": "0", "data": [{"maxBuy": "10", "maxSell": "10"}]}
            )
        pytest.fail("unqualified Live automation reached private write HTTP")

    service, _, _, intents, _ = service_fixture()
    service.settings = live_settings(
        okx_live_auto_execution=True,
        okx_ws_enabled=True,
        okx_live_scan_initial_delay_seconds=0,
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(exchange_handler),
        base_url="https://openapi.okx.com",
    ) as exchange_http:
        service.execution_client = OkxLiveExecutionRestClient(
            exchange_http, settings=service.settings
        )
        await seed_legacy_local_lease_for_downstream_unit_test(service)
        worker = ControlledLiveAutomation(
            service,
            settings=service.settings,
            strategy_service=FakeLiveStrategy(),
            risk_service=FakeLiveRisk(),
            public_client=FakeLivePublic(),
            market_hub=FakeLiveHub(),
            market_client=FakeLiveMarketClient(),
        )
        route = importlib.import_module("app.api.routers.okx_live")
        monkeypatch.setattr(route, "controlled_live_automation", worker)
        app.dependency_overrides[require_ctcc_token] = lambda: None
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
            ) as api_http:
                endpoint = (
                    "/api/okx-live/automation/start"
                    if scheduled
                    else "/api/okx-live/automation/run-once"
                )
                response = await api_http.post(
                    endpoint,
                    json={
                        "execute": True,
                        "confirmation": LIVE_AUTOMATION_EXECUTE_PHRASE,
                        "symbols": ["BTC-USDT-SWAP"],
                    },
                )
        finally:
            app.dependency_overrides.pop(require_ctcc_token, None)
            await worker.stop()

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "live_qualified_dispatch_coordinator_unavailable"
    )
    assert worker.running is False
    assert observed == []
    assert intents.rows == {}
    assert service.arm_status().armed is True
