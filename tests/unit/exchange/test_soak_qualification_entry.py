"""The registered Demo soak route cannot start unqualified execution."""

from __future__ import annotations

import importlib

import httpx
import pytest

from app.api.security import require_ctcc_token
from app.domain.observability import DemoSoakSessionView, DemoSoakStartRequest
from app.main import app


class SoakRecorder:
    def __init__(self) -> None:
        self.requests: list[DemoSoakStartRequest] = []

    async def start_soak(self, request: DemoSoakStartRequest) -> DemoSoakSessionView:
        self.requests.append(request)
        return DemoSoakSessionView(state="running", execute=request.execute)


@pytest.mark.asyncio
async def test_execute_soak_route_denies_before_service_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = SoakRecorder()
    route = importlib.import_module("app.api.routers.observability")
    monkeypatch.setattr(route, "demo_observability", recorder)
    app.dependency_overrides[require_ctcc_token] = lambda: None
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            response = await client.post(
                "/api/demo-observability/soak/start",
                json={
                    "execute": True,
                    "confirmation": "START_DEMO_SOAK_EXECUTE",
                },
            )
    finally:
        app.dependency_overrides.pop(require_ctcc_token, None)

    assert response.status_code == 409
    assert response.json() == {
        "detail": "demo_qualified_dispatch_coordinator_unavailable"
    }
    assert recorder.requests == []


@pytest.mark.asyncio
async def test_observation_soak_route_remains_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = SoakRecorder()
    route = importlib.import_module("app.api.routers.observability")
    monkeypatch.setattr(route, "demo_observability", recorder)
    app.dependency_overrides[require_ctcc_token] = lambda: None
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            response = await client.post(
                "/api/demo-observability/soak/start",
                json={
                    "execute": False,
                    "confirmation": "START_DEMO_SOAK_OBSERVE",
                },
            )
    finally:
        app.dependency_overrides.pop(require_ctcc_token, None)

    assert response.status_code == 200
    assert response.json()["execute"] is False
    assert len(recorder.requests) == 1
    assert recorder.requests[0].execute is False
