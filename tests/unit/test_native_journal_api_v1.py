"""Authenticated HTTP wiring with declared DATA responses; no provider or DB."""

from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from app.api import security
from app.api.routers import demo_automation as routes
from app.demo_automation.native_journal_models_v1 import NativeJournalReadoutV1

TOKEN = "declared-synthetic-local-journal-token-000000000000"


def response():
    return NativeJournalReadoutV1(
        account_binding_sha256="b" * 64,
        database_head="0058",
        observed_at=datetime(2026, 11, 20, tzinfo=UTC),
        limit=20,
        total_native=0,
        unresolved_native=0,
        unresolved_other=0,
        truncated=False,
        entries=(),
    )


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setattr(
        security,
        "get_settings",
        lambda: SimpleNamespace(api_token_is_safe=True, api_token_value=TOKEN),
    )
    application = FastAPI()
    application.include_router(routes.router)
    return application


@pytest.mark.asyncio
async def test_unauthenticated_journal_never_reaches_native_source(app, monkeypatch):
    calls = []

    async def unavailable(**kwargs):
        calls.append(kwargs)
        raise AssertionError("must not reach native source")

    monkeypatch.setattr(routes.safe_demo_automation, "native_journal_v1", unavailable)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://local"
    ) as client:
        for headers in ({}, {"X-CTCC-Token": "wrong"}):
            result = await client.get(
                "/api/demo-automation/native/journal", headers=headers
            )
            assert result.status_code == 401
    assert calls == []


@pytest.mark.asyncio
async def test_authenticated_get_passes_exact_header_and_bounded_limit_only(
    app, monkeypatch
):
    calls = []

    async def data_only(**kwargs):
        calls.append(kwargs)
        return response()

    monkeypatch.setattr(routes.safe_demo_automation, "native_journal_v1", data_only)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://local"
    ) as client:
        result = await client.get(
            "/api/demo-automation/native/journal?limit=20",
            headers={"X-CTCC-Token": TOKEN},
        )
        assert result.status_code == 200
        value = result.json()
        assert value["entries"] == [] and value["account_complete"] is False
        assert value["execution_authority"] is value["order_retry_authority"] is False
        assert TOKEN not in result.text
    assert calls == [{"limit": 20, "operator_token": TOKEN}]


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", ("0", "101", "true"))
async def test_unbounded_or_noninteger_page_is_rejected_before_source(
    app, monkeypatch, limit
):
    calls = []

    async def data_only(**kwargs):
        calls.append(kwargs)
        return response()

    monkeypatch.setattr(routes.safe_demo_automation, "native_journal_v1", data_only)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://local"
    ) as client:
        result = await client.get(
            "/api/demo-automation/native/journal?limit=" + limit,
            headers={"X-CTCC-Token": TOKEN},
        )
        assert result.status_code == 422
    assert calls == []


@pytest.mark.asyncio
async def test_source_failure_is_unknown_and_private_details_never_reach_ui(
    app, monkeypatch
):
    async def refused(**kwargs):
        raise RuntimeError("private source credential account uid must stay hidden")

    monkeypatch.setattr(routes.safe_demo_automation, "native_journal_v1", refused)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://local"
    ) as client:
        result = await client.get(
            "/api/demo-automation/native/journal", headers={"X-CTCC-Token": TOKEN}
        )
        assert result.status_code == 409
        assert result.json() == {"detail": "native_demo_journal_unavailable"}
