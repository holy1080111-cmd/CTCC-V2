"""Synthetic-only signed position-history GET tests; no network or secrets."""

from __future__ import annotations

import base64
import hashlib
import json
import traceback
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from pydantic import SecretStr

from app.config.settings import Settings
from app.exchange.okx.private_rest import OkxDemoPrivateRestClient
from app.trade_qualification import account_collector as shared
from app.trade_qualification import account_positions_history_collector_v1 as collector
from app.trade_qualification import account_positions_history_v1 as history

NOW = datetime(2026, 10, 16, 0, 0, tzinfo=UTC)
NOW_MS = history._milliseconds(NOW)
IDENTITY = collector.DemoPositionIdentityV1("123456", "123456", "2", "net_mode", "USDC")


@contextmanager
def pinned_session():
    settings = Settings(
        _env_file=None,
        environment="test",
        trading_mode="okx_demo",
        okx_demo_enabled=True,
        okx_demo_api_key="synthetic-key-123",
        okx_demo_api_secret="synthetic-secret-123",
        okx_demo_api_passphrase="synthetic-pass-123",
        okx_demo_read_max_retries=0,
    )
    private = OkxDemoPrivateRestClient(settings=settings)
    with private.pinned_read_session() as session:
        yield session, settings


def plan(session) -> history.PositionHistoryPlanV1:
    return history.PositionHistoryPlanV1(
        registration_region="global",
        origin="https://openapi.okx.com",
        account_binding_sha256=IDENTITY.sha256,
        session_binding_sha256=hashlib.sha256(
            session.binding_id.encode("ascii")
        ).hexdigest(),
        created_at=NOW,
        cutoff_u_time_ms=NOW_MS - 10_000,
    )


def credentials(session, *, key="synthetic-key-123") -> shared.DemoAccountCredentials:
    return shared.DemoAccountCredentials(
        key, "synthetic-secret-123", "synthetic-pass-123", session.binding_id
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
    with pinned_session() as (session, _):
        selected = plan(session)
        result = await collector.collect_demo_position_history(
            credentials=credentials(session),
            read_session=session,
            clock=clock(),
            plan=selected,
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
    assert b"synthetic-key-123" not in result.raw_archive
    assert b"synthetic-secret-123" not in result.raw_archive
    assert b"synthetic-pass-123" not in result.raw_archive
    assert session.binding_id.encode("ascii") not in result.raw_archive
    replay = collector.verify_source_bound_archive(
        selected, IDENTITY, result.raw_archive, expected_sha256=result.archive_sha256
    )
    assert replay.chain == result.chain
    with pytest.raises(
        collector.OwnedPositionHistoryError, match="archive_pin_mismatch"
    ):
        collector.verify_source_bound_archive(
            selected,
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
            selected,
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
            selected,
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
    with (
        pinned_session() as (session, _),
        pytest.raises(
            collector.OwnedPositionHistoryError, match="account_identity_mismatch"
        ),
    ):
        await collector.collect_demo_position_history(
            credentials=credentials(session),
            read_session=session,
            clock=clock(),
            plan=plan(session),
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
    with (
        pinned_session() as (session, _),
        pytest.raises(
            collector.OwnedPositionHistoryError, match="account_identity_mismatch"
        ),
    ):
        await collector.collect_demo_position_history(
            credentials=credentials(session),
            read_session=session,
            clock=clock(),
            plan=plan(session),
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
    with (
        pinned_session() as (session, _),
        pytest.raises(
            collector.OwnedPositionHistoryError, match="account_identity_mismatch"
        ),
    ):
        await collector.collect_demo_position_history(
            credentials=credentials(session),
            read_session=session,
            clock=clock(),
            plan=plan(session),
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
    with pinned_session() as (session, _):
        wrong = shared.DemoAccountCredentials(
            "synthetic-key-123",
            "synthetic-secret-123",
            "synthetic-pass-123",
            "different-session",
        )
        with pytest.raises(
            collector.OwnedPositionHistoryError,
            match="credential_session_binding_mismatch",
        ):
            await collector.collect_demo_position_history(
                credentials=wrong,
                read_session=session,
                clock=clock(),
                plan=plan(session),
                identity=IDENTITY,
                barrier_completed_at=NOW,
            )
    assert called is False


@pytest.mark.asyncio
async def test_reused_session_label_with_different_key_stops_before_get(
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
    with (
        pinned_session() as (session, _),
        pytest.raises(
            collector.OwnedPositionHistoryError,
            match="credential_session_credentials_mismatch",
        ),
    ):
        await collector.collect_demo_position_history(
            credentials=credentials(session, key="synthetic-different-key"),
            read_session=session,
            clock=clock(),
            plan=plan(session),
            identity=IDENTITY,
            barrier_completed_at=NOW,
        )
    assert requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("changed_field", ("key", "origin"))
async def test_changed_runtime_binding_during_read_denies_without_followup_get(
    monkeypatch, changed_field
):
    requests = []

    def handler(request):
        requests.append(request.url.path)
        return response([config()])

    monkeypatch.setattr(
        collector,
        "_new_client",
        lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            trust_env=False,
            follow_redirects=False,
        ),
    )
    with pinned_session() as (session, settings):

        def mutate_binding():
            if len(requests) == 1:
                if changed_field == "key":
                    settings.okx_demo_api_key = SecretStr("synthetic-rotated-key")
                else:
                    settings.okx_demo_rest_base_url = "https://us.okx.com"
            return NOW + timedelta(milliseconds=len(requests) + 1)

        with pytest.raises(
            collector.OwnedPositionHistoryError, match="credential_session_inactive"
        ):
            await collector.collect_demo_position_history(
                credentials=credentials(session),
                read_session=session,
                clock=mutate_binding,
                plan=plan(session),
                identity=IDENTITY,
                barrier_completed_at=NOW,
            )
    assert requests == ["/api/v5/account/config"]


@pytest.mark.asyncio
async def test_unissued_witness_and_new_session_cannot_reuse_old_plan(monkeypatch):
    called = False

    def factory():
        nonlocal called
        called = True
        raise AssertionError("must not open a client")

    monkeypatch.setattr(collector, "_new_client", factory)
    with pinned_session() as (first, _):
        old_plan = plan(first)
        old_credentials = credentials(first)
        with pytest.raises(
            collector.OwnedPositionHistoryError,
            match="credential_session_witness_required",
        ):
            await collector.collect_demo_position_history(
                credentials=old_credentials,
                read_session=None,
                clock=clock(),
                plan=old_plan,
                identity=IDENTITY,
                barrier_completed_at=NOW,
            )
    with (
        pinned_session() as (second, _),
        pytest.raises(
            collector.OwnedPositionHistoryError,
            match="credential_session_binding_mismatch",
        ),
    ):
        await collector.collect_demo_position_history(
            credentials=credentials(second),
            read_session=second,
            clock=clock(),
            plan=old_plan,
            identity=IDENTITY,
            barrier_completed_at=NOW,
        )
    assert called is False


@pytest.mark.asyncio
async def test_closed_runtime_session_denies_before_get(monkeypatch):
    called = False

    def factory():
        nonlocal called
        called = True
        raise AssertionError("must not open a client")

    monkeypatch.setattr(collector, "_new_client", factory)
    with pinned_session() as (session, _):
        selected = plan(session)
        selected_credentials = credentials(session)
    with pytest.raises(
        collector.OwnedPositionHistoryError, match="credential_session_inactive"
    ):
        await collector.collect_demo_position_history(
            credentials=selected_credentials,
            read_session=session,
            clock=clock(),
            plan=selected,
            identity=IDENTITY,
            barrier_completed_at=NOW,
        )
    assert called is False


@pytest.mark.asyncio
async def test_response_echo_of_runtime_binding_is_rejected(monkeypatch):
    seen = []

    def handler(request):
        seen.append(request.url.path)
        return response([{**config(), "echo": session.binding_id}])

    monkeypatch.setattr(
        collector,
        "_new_client",
        lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            trust_env=False,
            follow_redirects=False,
        ),
    )
    with (
        pinned_session() as (session, _),
        pytest.raises(collector.OwnedPositionHistoryError, match="source_rejected"),
    ):
        await collector.collect_demo_position_history(
            credentials=credentials(session),
            read_session=session,
            clock=clock(),
            plan=plan(session),
            identity=IDENTITY,
            barrier_completed_at=NOW,
        )
    assert seen == ["/api/v5/account/config"]


@pytest.mark.asyncio
async def test_body_completion_time_precedes_response_cleanup(monkeypatch):
    current = [NOW]

    def ticking_clock():
        current[0] += timedelta(milliseconds=1)
        return current[0]

    original_close = shared._close

    async def slow_close(resource, **kwargs):
        if type(resource) is httpx.Response:
            current[0] += timedelta(hours=1)
        await original_close(resource, **kwargs)

    monkeypatch.setattr(shared, "_close", slow_close)
    with pinned_session() as (session, _):
        selected = plan(session)
        supplied = credentials(session)
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _request: response([config()])),
            trust_env=False,
            follow_redirects=False,
        ) as client:
            raw = await collector._signed_get(
                client,
                supplied,
                session,
                selected.session_binding_sha256,
                ticking_clock,
                collector._GetSpec(selected.origin, collector.CONFIG_ENDPOINT, ()),
                previous_completed=NOW,
                secret_tokens=[
                    supplied.api_key,
                    supplied.api_secret,
                    supplied.passphrase,
                    session.binding_id,
                ],
            )
    assert raw.started <= raw.headers <= raw.completed < NOW + timedelta(hours=1)


@pytest.mark.asyncio
async def test_transport_exception_context_is_hidden(monkeypatch):
    sentinel = "private-transport-token-must-not-escape"

    def failure():
        raise RuntimeError(sentinel)

    monkeypatch.setattr(collector, "_new_client", failure)
    with (
        pinned_session() as (session, _),
        pytest.raises(
            collector.OwnedPositionHistoryError, match="source_failed"
        ) as captured,
    ):
        await collector.collect_demo_position_history(
            credentials=credentials(session),
            read_session=session,
            clock=clock(),
            plan=plan(session),
            identity=IDENTITY,
            barrier_completed_at=NOW,
        )
    assert sentinel not in "".join(traceback.format_exception(captured.value))
