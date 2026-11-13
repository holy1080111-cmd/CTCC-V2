"""Exercise the dedicated monitor through its actual ASGI entry points."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from scripts import serve_native_control_monitor_0056 as monitor

_HOST = "http://127.0.0.1:8126"
_TOKEN = "a" * 48
_OPTIONS = {
    "descriptor_file": Path("private-descriptor"),
    "source_archive_file": Path("exact-archive"),
    "port": 8126,
}
_READBACK = {
    "status": "CONTROL_OBSERVED",
    "database_head": "0056",
    "source_commit": "a" * 40,
    "source_tree": "b" * 40,
    "source_manifest_sha256": "c" * 64,
    "deployment_admin_anchor_sha256": "d" * 64,
    "control_snapshot_sha256": "e" * 64,
    "control_revision": 3,
    "stored_armed": False,
    "emergency_stop": True,
    "locked": True,
    "restart_latch_required": True,
}


@pytest.fixture
def profile(monkeypatch):
    settings = SimpleNamespace(api_token_value=_TOKEN, api_token_is_safe=True)
    monkeypatch.setattr(monitor.status.deployment, "get_settings", lambda: settings)
    return settings


def _app(monkeypatch, *reads):
    observe = AsyncMock(side_effect=reads)
    monkeypatch.setattr(
        monitor.status, "read_restricted_control_status_0056_v1", observe
    )
    return monitor.build_native_control_monitor_0056(**_OPTIONS), observe


def test_only_authenticated_get_can_trigger_a_current_observation(monkeypatch, profile):
    updated = {**_READBACK, "control_revision": 4}
    app, observe = _app(monkeypatch, _READBACK, updated)
    with TestClient(app, base_url=_HOST) as client:
        assert client.get("/liveness").json() == {"status": "RUNNING"}
        assert client.post("/api/demo/control").status_code == 405
        assert client.get("/docs").status_code == 404
        assert client.get("/api/demo-automation/status").status_code == 404
        assert client.get("/api/demo/control").status_code == 401
        assert (
            client.get(
                "/api/demo/control",
                headers=[("X-CTCC-Token", _TOKEN), ("X-CTCC-Token", _TOKEN)],
            ).status_code
            == 401
        )
        assert (
            client.get(
                "/api/demo/control", headers={"Origin": "https://foreign"}
            ).status_code
            == 403
        )
        assert (
            client.get("/api/demo/control", headers={"Host": "alternate"}).status_code
            == 403
        )
        observe.assert_awaited_once()
        readback = client.get("/api/demo/control", headers={"X-CTCC-Token": _TOKEN})
        assert readback.status_code == 200
        assert readback.headers["cache-control"] == "no-store"
        assert readback.json() == {
            **updated,
            "monitoring_only": True,
            "execution_authority": False,
            "order_writes": 0,
        }
        assert observe.await_count == 2


@pytest.mark.parametrize("defect", ["anchor", "provider", "token"])
def test_loss_of_binding_withholds_stale_control(monkeypatch, profile, defect):
    changed = {
        "anchor": {**_READBACK, "deployment_admin_anchor_sha256": "f" * 64},
        "provider": RuntimeError("private database info"),
        "token": _READBACK,
    }[defect]
    app, observe = _app(monkeypatch, _READBACK, changed)
    with TestClient(app, base_url=_HOST) as client:
        if defect == "token":
            profile.api_token_value = "b" * 48
        for _ in range(2):
            response = client.get("/api/demo/control", headers={"X-CTCC-Token": _TOKEN})
            assert response.status_code == 503
            assert response.json() == {"detail": "native_control_monitor_unavailable"}
        assert observe.await_count == (1 if defect == "token" else 2)


def test_startup_requires_original_independent_control_readback(monkeypatch, profile):
    app, observe = _app(monkeypatch, {**_READBACK, "control_revision": False})
    with (
        pytest.raises(monitor.status.deployment.NativeDeploymentDescriptor0056Error),
        TestClient(app, base_url=_HOST),
    ):
        pytest.fail("Started without verified stored control")
    observe.assert_awaited_once()
