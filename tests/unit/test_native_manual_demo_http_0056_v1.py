"""Actual ASGI surface with inert action handler; no DB or exchange."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from app import main
from app.api import native_manual_demo_http_0056_v1 as manual
from app.api import security
from app.api.routers import demo_automation as demo
from app.domain.demo_automation import ARM_PHRASE, EXECUTE_PHRASE

TOKEN = "synthetic-local-manual-demo-0056-only-000000"


def app(monkeypatch, *, ready=True):
    config = SimpleNamespace(
        app_name="test",
        app_version="test",
        ctcc_native_only_lifespan_v1=False,
        ctcc_local_analysis_only_v1=False,
        ctcc_native_demo_manual_lifespan_0056_v1=True,
        api_token_value=TOKEN,
        api_token_is_safe=True,
        environment="production",
    )
    monkeypatch.setattr(main, "settings", config)
    monkeypatch.setattr(security, "get_settings", lambda: config)
    instance = main._build_app()
    instance.state.native_manual_demo_recovery_confirmed = ready
    return instance


@pytest.mark.asyncio
async def test_manual_surface_has_only_exact_native_controls(monkeypatch):
    instance = app(monkeypatch)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=instance), base_url="http://test"
    ) as client:
        assert (await client.get("/readiness")).json()["execution_authority"] is False
        for path in (
            "/docs",
            "/openapi.json",
            "/api/okx-demo/order",
            "/api/demo-automation/start",
            "/api/demo-automation/run-once",
            "/api/demo-automation/clear-emergency-stop",
        ):
            assert (await client.post(path)).status_code == 404
        for path in manual._ROUTES:
            if path.endswith("arm-execute"):
                assert (await client.post(path, json={})).status_code == 401


@pytest.mark.asyncio
async def test_unconfirmed_recovery_never_reaches_native_handler(monkeypatch):
    instance = app(monkeypatch, ready=False)
    action = AsyncMock()
    monkeypatch.setattr(demo.safe_demo_automation, "perform_native_action_v2", action)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=instance), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/demo-automation/native/arm-execute",
            headers={"X-CTCC-Token": TOKEN},
            json={},
        )
    assert response.status_code == 503
    action.assert_not_awaited()


@pytest.mark.asyncio
async def test_same_authenticated_native_request_reaches_original_caller(monkeypatch):
    instance = app(monkeypatch)
    action = AsyncMock(
        return_value={
            "invocation_id": "synthetic",
            "action": "arm_execute",
            "status": "reconciliation_data_hold_retained",
            "classification": "synthetic-no-execution",
        }
    )
    monkeypatch.setattr(demo.safe_demo_automation, "perform_native_action_v2", action)
    body = {
        "instrument_id": "BTC-USDT-SWAP",
        "strategy": "trend_pullback",
        "contracts": "1",
        "leverage": 3,
        "arm_confirmation": ARM_PHRASE,
        "execute_confirmation": EXECUTE_PHRASE,
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=instance), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/demo-automation/native/arm-execute",
            headers={"X-CTCC-Token": TOKEN},
            json=body,
        )
    assert response.status_code == 200
    assert response.json()["execution_complete"] is False
    action.assert_awaited_once()
    assert action.await_args.kwargs == {"operator_token": TOKEN}


def test_manual_and_monitor_profiles_cannot_be_combined(monkeypatch):
    instance = app(monkeypatch)
    assert instance is not None
    main.settings.ctcc_native_only_lifespan_v1 = True
    with pytest.raises(RuntimeError, match="runtime_profile_conflict"):
        main._build_app()
