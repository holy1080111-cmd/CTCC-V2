"""Exercise the authenticated offline import and keep raw payloads out of errors."""

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routers import analysis
from app.api.security import require_ctcc_token
from app.config.settings import Settings
from app.domain.source_primitives import MAX_RAW, sha
from tests.unit.test_qualification_data import market  # noqa: F401


@pytest.fixture
def client(monkeypatch):
    blocked = AsyncMock(side_effect=AssertionError("offline import attempted HTTP"))
    monkeypatch.setattr(httpx.AsyncClient, "send", blocked)
    app = FastAPI()
    app.include_router(analysis.router)
    with TestClient(app) as test:
        yield test
    blocked.assert_not_awaited()


def test_auth_required(client, monkeypatch):
    from secrets import token_urlsafe

    from app.api import security

    token = token_urlsafe(32)
    settings = Settings(_env_file=None, api_token=token, environment="test")
    monkeypatch.setattr(security, "get_settings", lambda: settings)
    absent = client.post("/api/analysis/snapshot", json={})
    assert absent.status_code == 401
    assert absent.json()["detail"] == "invalid_ctcc_api_token"
    accepted = client.post(
        "/api/analysis/snapshot", json={}, headers={"X-CTCC-Token": token}
    )
    assert accepted.status_code == 422
    assert accepted.json()["detail"] == "snapshot_invalid"


def test_import_recomputes_analysis_without_issuing_permission(client, market):  # noqa: F811
    client.app.dependency_overrides[require_ctcc_token] = lambda: None
    original = market.model_dump(mode="json")
    original["quality"] = {
        "4H": {
            "ok": False,
            "candle_count": 0,
            "confirmed_count": 0,
            "expected_interval_seconds": 14400,
            "issues": [],
        }
    }
    body = json.dumps(original).encode()
    first = client.post(
        "/api/analysis/snapshot",
        content=body,
        headers={"Content-Type": "application/json"},
    )
    second = client.post(
        "/api/analysis/snapshot",
        content=body,
        headers={"Content-Type": "application/json"},
    )
    assert first.status_code == second.status_code == 200, first.json()
    observed = first.json()
    assert observed == second.json()
    assert observed["input_sha256"] == sha(body)
    assert (
        observed["source_authenticity_verified"]
        is observed["execution_authority"]
        is False
    )
    result = observed["analysis"]
    assert result["generated_at"] == market.received_at.isoformat().replace(
        "+00:00", "Z"
    )
    assert set(result["timeframe_analyses"]) == {"4H", "1H", "15m", "5m"}
    assert result["timeframe_analyses"]["4H"]["data_quality_ok"] is True
    assert result["trade_ready"] is False
    assert "imported_snapshot_unverified" in result["blockers"]


@pytest.mark.parametrize(
    "mutation",
    ("foreign_book", "future_ticker", "future_capture", "duplicate", "unknown"),
)
def test_invalid_snapshot_refuses_with_fixed_error(client, market, mutation):  # noqa: F811
    client.app.dependency_overrides[require_ctcc_token] = lambda: None
    item = market.model_dump(mode="json")
    if mutation == "foreign_book":
        item["order_book"]["instrument_id"] = "ETH-USDT-SWAP"
    elif mutation == "future_ticker":
        item["ticker"]["timestamp"] = (
            market.received_at + timedelta(seconds=1)
        ).isoformat()
    elif mutation == "future_capture":
        item["received_at"] = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    elif mutation == "unknown":
        item["candles"]["4H"][0]["private_unused"] = (
            "discarded-secret-should-stay-private"
        )
    body = json.dumps(item)
    if mutation == "duplicate":
        body = body.replace('"ticker":', '"ticker": null, "ticker":', 1)
    result = client.post(
        "/api/analysis/snapshot",
        content=body,
        headers={"Content-Type": "application/json"},
    )
    assert result.status_code == 422
    assert result.json() == {"detail": "snapshot_invalid"}


def test_payload_content_type_and_stream_bound(client):
    client.app.dependency_overrides[require_ctcc_token] = lambda: None
    assert client.post("/api/analysis/snapshot", content=b"{}").status_code == 415
    oversized = client.post(
        "/api/analysis/snapshot",
        content=b" " * (MAX_RAW + 1),
        headers={"Content-Type": "application/json"},
    )
    assert oversized.status_code == 413
    assert oversized.json() == {"detail": "snapshot_size_limit"}
