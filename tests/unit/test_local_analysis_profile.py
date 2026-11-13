"""The local analysis profile exposes only same-origin, non-exchange tools."""

from secrets import token_urlsafe

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app import main
from app.config.settings import Settings


def _local_settings(**updates):
    args = {
        "environment": "production",
        "trading_mode": "analysis_only",
        "ctcc_local_analysis_only_v1": True,
        "okx_demo_observability_enabled": False,
        "okx_demo_soak_enabled": False,
        "paper_auto_ticks": False,
        "api_token": token_urlsafe(32),
    } | updates
    return Settings(_env_file=None, **args)


@pytest.mark.parametrize(
    "update",
    (
        {"trading_mode": "live"},
        {"okx_demo_enabled": True},
        {"okx_demo_api_key": "partially-configured-demo-value"},
        {"okx_live_api_secret": "partially-configured-live-value"},
        {"okx_ws_enabled": True},
        {"paper_persistence_enabled": False},
        {"api_token": ""},
    ),
)
def test_local_analysis_profile_denies_unsafe_settings(update):
    with pytest.raises(ValidationError, match="local_analysis_configuration_denied"):
        _local_settings(**update)


def test_local_analysis_profile_keeps_only_reviewed_routes(monkeypatch):
    monkeypatch.setattr(main, "settings", _local_settings())
    client = TestClient(main._build_app(), follow_redirects=False)
    try:
        assert client.get("/liveness").status_code == 200
        assert (
            client.get("/dashboard").headers["location"] == "/dashboard/local-analysis"
        )
        assert client.get("/dashboard/local-analysis").status_code == 200
        for path in (
            "/docs",
            "/redoc",
            "/openapi.json",
            "/api/dashboard/snapshot",
            "/api/okx-demo/status",
            "/api/okx-live/status",
            "/api/analysis/BTC-USDT-SWAP",
        ):
            assert client.get(path).status_code == 404, path
        for path in (
            "/api/paper/reset",
            "/api/okx-demo/orders",
            "/api/demo-automation/native/arm-execute",
            "/api/okx-live/orders",
        ):
            assert client.post(path).status_code == 404, path
    finally:
        client.close()


def test_local_analysis_route_inventory_catches_duplicate_or_retyped_path(monkeypatch):
    route = next(
        r for r in main.api_router.routes if r.path == "/api/analysis/snapshot"
    )
    monkeypatch.setattr(route, "methods", {"GET"})
    with pytest.raises(RuntimeError, match="local_analysis_route_scope_changed"):
        main._local_analysis_router()
