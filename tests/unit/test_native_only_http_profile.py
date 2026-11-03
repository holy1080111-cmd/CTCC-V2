"""Exercise the actual route registration through in-process ASGI only."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from app import main
from app.api import security
from app.api.routers import demo_automation, system
from app.core.native_only_lifespan import _MUST_BE_FALSE

_GET_PATHS = {
    "/liveness",
    "/readiness",
    "/api/version",
    "/api/demo-automation/status",
    "/api/demo-automation/history",
}
_TEST_TOKEN = "synthetic-http-test-token-only-00000"


def _settings(**overrides):
    return SimpleNamespace(
        **(
            dict.fromkeys(_MUST_BE_FALSE, False)
            | {
                "app_name": "test",
                "app_version": "test-version",
                "environment": "production",
                "trading_mode": "okx_demo",
                "web_concurrency": 1,
                "ctcc_native_only_lifespan_v1": True,
                "okx_demo_enabled": True,
                "okx_demo_credentials_configured": False,
                "api_token_is_safe": True,
                "api_token_value": _TEST_TOKEN,
            }
            | overrides
        )
    )


def _app(monkeypatch, **overrides):
    settings = _settings(**overrides)
    monkeypatch.setattr(main, "settings", settings)
    monkeypatch.setattr(system, "settings", settings)
    monkeypatch.setattr(security, "get_settings", lambda: settings)
    return main._build_app()


def _client(instance):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=instance),
        base_url="http://native-only.invalid",
    )


@pytest.mark.asyncio
async def test_native_only_registers_only_the_reviewed_get_routes(monkeypatch):
    instance = _app(monkeypatch)
    routes = main.native_only_api_router.routes
    assert len(instance.routes) == 1
    assert len(routes) == len(_GET_PATHS)
    assert {route.path for route in routes} == _GET_PATHS
    assert all(route.methods == {"GET"} for route in routes)
    async with _client(instance) as client:
        assert (await client.get("/liveness")).status_code == 200
        assert (await client.get("/api/version")).json()["architecture_stage"] == (
            "native_only_no_execution"
        )
        for path in (
            "/",
            "/docs",
            "/openapi.json",
            "/dashboard",
            "/api/dashboard/snapshot",
            "/api/capabilities",
            "/api/market/supported-symbols",
            "/api/okx-demo/balance",
            "/liveness/",
        ):
            assert (await client.get(path)).status_code == 404
        for path in (
            "/api/demo-automation/native/arm-execute",
            "/api/demo-automation/native/reconcile",
            "/api/demo-automation/clear-emergency-stop",
            "/api/okx-demo/reconcile",
            "/api/recovery/reconcile",
        ):
            assert (await client.post(path)).status_code == 404
        assert (await client.post("/api/demo-automation/status")).status_code == 405


@pytest.mark.asyncio
async def test_native_only_readiness_requires_actual_startup_marker(monkeypatch):
    instance = _app(monkeypatch)
    database = AsyncMock(return_value=(True, "database checked"))
    redis = AsyncMock(return_value=(True, "redis checked"))
    monkeypatch.setattr(system, "check_database", database)
    monkeypatch.setattr(system, "check_redis", redis)
    async with _client(instance) as client:
        response = await client.get("/readiness")
        assert response.status_code == 503
        assert response.json()["blockers"] == ["native_only_recovery_unconfirmed"]

        instance.state.native_only_recovery_confirmed = True
        response = await client.get("/readiness")
        assert response.status_code == 200
        assert response.json()["blockers"] == []

        database.return_value = (False, "database unavailable")
        redis.return_value = (False, "redis unavailable")
        system.settings.api_token_is_safe = False
        system.settings.okx_demo_allow_order_writes = True
        response = await client.get("/readiness")
        assert response.status_code == 503
        assert set(response.json()["blockers"]) == {
            "database_unavailable",
            "redis_unavailable",
            "api_token_not_safely_configured",
            "native_only_configuration_denied",
        }
        assert _TEST_TOKEN not in response.text


@pytest.mark.asyncio
async def test_native_only_keeps_status_and_history_token_dependencies(monkeypatch):
    instance = _app(monkeypatch)
    history = AsyncMock(return_value=[])
    monkeypatch.setattr(demo_automation.safe_demo_automation, "history", history)
    async with _client(instance) as client:
        before_recovery = await client.get(
            "/api/demo-automation/history",
            headers={"X-CTCC-Token": _TEST_TOKEN},
        )
        assert before_recovery.status_code == 503
        assert before_recovery.json()["detail"] == "native_only_recovery_unconfirmed"
        history.assert_not_awaited()

        instance.state.native_only_recovery_confirmed = True
        assert (await client.get("/api/demo-automation/status")).status_code == 401
        assert (await client.get("/api/demo-automation/history")).status_code == 401
        history.assert_not_awaited()

        response = await client.get(
            "/api/demo-automation/history?limit=3",
            headers={"X-CTCC-Token": _TEST_TOKEN},
        )
        assert response.status_code == 200
        assert response.json() == []
        history.assert_awaited_once_with(3)


@pytest.mark.asyncio
async def test_legacy_app_keeps_its_routes_and_demo_readiness_rule(monkeypatch):
    instance = _app(monkeypatch, ctcc_native_only_lifespan_v1=False)
    assert set(instance.openapi()["paths"]) == {
        route.path_format
        for route in main.api_router.routes
        if getattr(route, "include_in_schema", False)
    }
    monkeypatch.setattr(
        system, "check_database", AsyncMock(return_value=(True, "database checked"))
    )
    monkeypatch.setattr(
        system, "check_redis", AsyncMock(return_value=(True, "redis checked"))
    )
    async with _client(instance) as client:
        assert (await client.get("/")).status_code == 200
        assert (await client.get("/docs")).status_code == 200
        response = await client.get("/readiness")
        assert response.status_code == 503
        assert response.json()["blockers"] == ["okx_demo_credentials_missing"]
