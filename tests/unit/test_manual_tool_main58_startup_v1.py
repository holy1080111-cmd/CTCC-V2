"""Actual ASGI lifespan/router composition with synthetic lower source seams."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from app import main
from app.api.routers import demo_automation as routes
from app.core import native_manual_demo_lifespan_0056_v1 as startup
from tests.unit.test_native_journal_api_v1 import response
from tests.unit.test_native_manual_demo_http_0056_v1 import TOKEN, app


@pytest.mark.asyncio
@pytest.mark.parametrize("head", ("0056", "0057", "0058"))
async def test_verified_manual_startup_reaches_same_authenticated_journal_and_retires(
    monkeypatch, head
):
    instance = app(monkeypatch, ready=False)
    lower = AsyncMock(return_value=head)
    stop, dispose = AsyncMock(), AsyncMock()
    journal = AsyncMock(return_value=response())
    monkeypatch.setattr(startup, "start_manual_demo_0056_v1", lower)
    monkeypatch.setattr(main.safe_demo_automation, "stop", stop)
    monkeypatch.setattr(main, "engine", SimpleNamespace(dispose=dispose))
    monkeypatch.setattr(routes.safe_demo_automation, "native_journal_v1", journal)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=instance), base_url="http://local"
    ) as client:
        assert (await client.get("/readiness")).status_code == 503
        async with main.lifespan(instance):
            lower.assert_awaited_once_with(
                main.settings, main.AsyncSessionFactory, main.safe_demo_automation
            )
            ready = await client.get("/readiness")
            assert ready.status_code == 200 and ready.json()["database_head"] == head
            denied = await client.get("/api/demo-automation/native/journal")
            assert denied.status_code == 401
            journal.assert_not_awaited()
            result = await client.get(
                "/api/demo-automation/native/journal?limit=20",
                headers={"X-CTCC-Token": TOKEN},
            )
            assert result.status_code == 200
            assert result.json()["execution_authority"] is False
            journal.assert_awaited_once_with(limit=20, operator_token=TOKEN)
            assert (
                await client.post("/api/demo-automation/native/journal")
            ).status_code == 405
            assert (await client.post("/api/okx-live/order")).status_code == 404
        assert instance.state.native_manual_demo_recovery_confirmed is False
        assert instance.state.native_manual_demo_database_head is None
        assert (
            await client.get(
                "/api/demo-automation/native/journal", headers={"X-CTCC-Token": TOKEN}
            )
        ).status_code == 503
    stop.assert_awaited_once()
    dispose.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("head", (None, True, "0059", "0058 "))
async def test_unknown_head_never_exposes_manual_journal_or_ready(monkeypatch, head):
    instance = app(monkeypatch, ready=False)
    stop, dispose = AsyncMock(), AsyncMock()
    monkeypatch.setattr(
        startup, "start_manual_demo_0056_v1", AsyncMock(return_value=head)
    )
    monkeypatch.setattr(main.safe_demo_automation, "stop", stop)
    monkeypatch.setattr(main, "engine", SimpleNamespace(dispose=dispose))
    with pytest.raises(
        RuntimeError, match="^native_manual_demo_database_head_unconfirmed$"
    ):
        async with main.lifespan(instance):
            raise AssertionError("unverified source cannot enter the HTTP lifespan")
    assert instance.state.native_manual_demo_recovery_confirmed is False
    assert instance.state.native_manual_demo_database_head is None
    stop.assert_not_awaited()
    dispose.assert_awaited_once()
