"""Synthetic-only signed position-history GET tests; no network or secrets."""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.trade_qualification import account_collector as shared
from app.trade_qualification import account_positions_history_collector_v1 as collector
from app.trade_qualification import account_positions_history_v1 as history

NOW = datetime(2026, 10, 16, 0, 0, tzinfo=UTC)
NOW_MS = history._milliseconds(NOW)
IDENTITY = collector.DemoPositionIdentityV1("123456", "123456", "2", "net_mode", "USDC")
SESSION = "reviewed-test-session"


def plan() -> history.PositionHistoryPlanV1:
    return history.PositionHistoryPlanV1(
        registration_region="global",
        origin="https://openapi.okx.com",
        account_binding_sha256=IDENTITY.sha256,
        session_binding_sha256=hashlib.sha256(SESSION.encode("ascii")).hexdigest(),
        created_at=NOW,
        cutoff_u_time_ms=NOW_MS - 10_000,
    )


def credentials() -> shared.DemoAccountCredentials:
    return shared.DemoAccountCredentials(
        "synthetic-key-123", "synthetic-secret-123", "synthetic-pass-123", SESSION
    )


def clock():
    tick = 0

    def now():
        nonlocal tick
        tick += 1
        return NOW + timedelta(milliseconds=tick)

    return now


def response(rows):
    return httpx.Response(
        200,
        headers={"content-type": "application/json"},
        stream=httpx.ByteStream(
            json.dumps({"code": "0", "msg": "", "data": rows}).encode()
        ),
    )


def config(uid="123456", settlement_currency="USDC"):
    return {
        "uid": uid,
        "mainUid": "123456",
        "acctLv": "2",
        "posMode": "net_mode",
        "settleCcy": settlement_currency,
    }


def position():
    return {
        "posId": "99",
        "instId": "BTC-USDT-SWAP",
        "instType": "SWAP",
        "posSide": "net",
        "mgnMode": "isolated",
        "type": "2",
        "cTime": str(NOW_MS - 6000),
        "uTime": str(NOW_MS - 5000),
        "realizedPnl": "0.10",
        "fee": "-0.01",
        "fundingFee": "",
        "liqPenalty": "0",
        "pnl": "0.11",
    }


@pytest.mark.asyncio
async def test_signed_get_chain_is_bound_to_config_before_and_after_and_stays_deny(
    monkeypatch,
):
    seen = []

    def handler(request):
        seen.append(request)
        assert request.headers["x-simulated-trading"] == "1"
        assert request.headers["ok-access-key"] == "synthetic-key-123"
        assert request.url.host == "openapi.okx.com"
        if len(seen) in (1, 4):
            assert request.url.path == "/api/v5/account/config"
            return response([config()])
        assert request.url.path == history.ENDPOINT
        assert request.url.params["limit"] == "100"
        if len(seen) == 2:
            assert "after" not in request.url.params
            return response([position()])
        assert request.url.params["after"] == str(NOW_MS - 5000)
        return response([])

    monkeypatch.setattr(
        collector,
        "_new_client",
        lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            trust_env=False,
            follow_redirects=False,
        ),
    )
    result = await collector.collect_demo_position_history(
        credentials=credentials(),
        clock=clock(),
        plan=plan(),
        identity=IDENTITY,
        barrier_completed_at=NOW,
    )
    assert len(seen) == 4
    assert result.chain.row_count == 1
    assert result.chain.cutoff_observed is False
    assert result.peer_certificate_sha256 == (None, None)
    assert result.admission == "DENY"
    assert result.account_complete is False
    assert result.source_authenticity_verified is False
    assert result.execution_authority is False
    replay = collector.verify_source_bound_archive(
        plan(), IDENTITY, result.raw_archive, expected_sha256=result.archive_sha256
    )
    assert replay.chain == result.chain
    with pytest.raises(
        collector.OwnedPositionHistoryError, match="archive_pin_mismatch"
    ):
        collector.verify_source_bound_archive(
            plan(),
            IDENTITY,
            result.raw_archive + b" ",
            expected_sha256=result.archive_sha256,
        )
    altered = json.loads(result.raw_archive)
    altered["identity_sha256"] = "0" * 64
    altered_bytes = collector._canonical(altered)
    with pytest.raises(
        collector.OwnedPositionHistoryError, match="archive_binding_mismatch"
    ):
        collector.verify_source_bound_archive(
            plan(),
            IDENTITY,
            altered_bytes,
            expected_sha256=hashlib.sha256(altered_bytes).hexdigest(),
        )
    altered = json.loads(result.raw_archive)
    original = json.loads(base64.b64decode(altered["config_before"]["raw_body_base64"]))
    original["data"][0]["uid"] = "999999"
    altered["config_before"]["raw_body_base64"] = base64.b64encode(
        json.dumps(original).encode()
    ).decode("ascii")
    altered_bytes = collector._canonical(altered)
    with pytest.raises(
        collector.OwnedPositionHistoryError, match="account_identity_mismatch"
    ):
        collector.verify_source_bound_archive(
            plan(),
            IDENTITY,
            altered_bytes,
            expected_sha256=hashlib.sha256(altered_bytes).hexdigest(),
        )


@pytest.mark.asyncio
async def test_wrong_signed_account_stops_before_history_get(monkeypatch):
    seen = []

    def handler(request):
        seen.append(request.url.path)
        return response([config(uid="999999")])

    monkeypatch.setattr(
        collector,
        "_new_client",
        lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            trust_env=False,
            follow_redirects=False,
        ),
    )
    with pytest.raises(
        collector.OwnedPositionHistoryError, match="account_identity_mismatch"
    ):
        await collector.collect_demo_position_history(
            credentials=credentials(),
            clock=clock(),
            plan=plan(),
            identity=IDENTITY,
            barrier_completed_at=NOW,
        )
    assert seen == ["/api/v5/account/config"]


@pytest.mark.asyncio
async def test_account_identity_change_after_pages_rejects_archive(monkeypatch):
    seen = []

    def handler(request):
        seen.append(request.url.path)
        if len(seen) == 1:
            return response([config()])
        if len(seen) == 2:
            return response([position()])
        if len(seen) == 3:
            return response([])
        return response([config(uid="999999")])

    monkeypatch.setattr(
        collector,
        "_new_client",
        lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            trust_env=False,
            follow_redirects=False,
        ),
    )
    with pytest.raises(
        collector.OwnedPositionHistoryError, match="account_identity_mismatch"
    ):
        await collector.collect_demo_position_history(
            credentials=credentials(),
            clock=clock(),
            plan=plan(),
            identity=IDENTITY,
            barrier_completed_at=NOW,
        )
    assert seen == [
        "/api/v5/account/config",
        history.ENDPOINT,
        history.ENDPOINT,
        "/api/v5/account/config",
    ]


@pytest.mark.asyncio
async def test_settlement_currency_change_after_pages_rejects_archive(monkeypatch):
    calls = 0

    def handler(_request):
        nonlocal calls
        calls += 1
        if calls == 1:
            return response([config()])
        if calls == 2:
            return response([position()])
        if calls == 3:
            return response([])
        return response([config(settlement_currency="USD")])

    monkeypatch.setattr(
        collector,
        "_new_client",
        lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            trust_env=False,
            follow_redirects=False,
        ),
    )
    with pytest.raises(
        collector.OwnedPositionHistoryError, match="account_identity_mismatch"
    ):
        await collector.collect_demo_position_history(
            credentials=credentials(),
            clock=clock(),
            plan=plan(),
            identity=IDENTITY,
            barrier_completed_at=NOW,
        )
    assert calls == 4


@pytest.mark.asyncio
async def test_wrong_session_binding_causes_zero_requests(monkeypatch):
    called = False

    def factory():
        nonlocal called
        called = True
        raise AssertionError("must not open a client")

    monkeypatch.setattr(collector, "_new_client", factory)
    wrong = shared.DemoAccountCredentials(
        "synthetic-key-123",
        "synthetic-secret-123",
        "synthetic-pass-123",
        "different-session",
    )
    with pytest.raises(
        collector.OwnedPositionHistoryError, match="session_binding_mismatch"
    ):
        await collector.collect_demo_position_history(
            credentials=wrong,
            clock=clock(),
            plan=plan(),
            identity=IDENTITY,
            barrier_completed_at=NOW,
        )
    assert called is False


@pytest.mark.asyncio
async def test_reused_session_label_with_different_key_never_grants_authority(
    monkeypatch,
):
    requests = []

    def handler(request):
        requests.append(request)
        if len(requests) in (1, 4):
            return response([config()])
        return response([position()] if len(requests) == 2 else [])

    monkeypatch.setattr(
        collector,
        "_new_client",
        lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            trust_env=False,
            follow_redirects=False,
        ),
    )
    different_key = shared.DemoAccountCredentials(
        "synthetic-different-key", "synthetic-secret-123", "synthetic-pass-123", SESSION
    )
    result = await collector.collect_demo_position_history(
        credentials=different_key,
        clock=clock(),
        plan=plan(),
        identity=IDENTITY,
        barrier_completed_at=NOW,
    )
    assert len(requests) == 4
    assert result.admission == "DENY"
    assert result.source_authenticity_verified is False
    assert result.execution_authority is False
