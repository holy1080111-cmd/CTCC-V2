"""Synthetic native-owned Global component protocol, never external evidence."""

import asyncio

import httpx
import pytest
from websockets.exceptions import ConnectionClosedError

from app.domain import native_clock
from app.domain.source_primitives import PublicReceiptError, canonical, decode, sha
from app.public_market_source import public_runtime_journal as journal
from app.trade_qualification import global_public_runtime_v1 as subject
from app.trade_qualification import public_market_collector as legacy
from app.trade_qualification import public_source_runtime as runtime
from tests.unit.research.test_owned_public_runtime_p1 import (
    resign_journal,
    synthetic_runtime,
)
from tests.unit.test_qualification_account_collector import Stream
from tests.unit.test_qualification_market_bridge import v2_engine_source
from tests.unit.test_qualification_one_shot import ms
from tests.unit.test_qualification_public_market_collector import policy


@pytest.fixture(scope="module")
def market():
    return v2_engine_source("long")


@pytest.fixture
def owned_global(monkeypatch, market):
    clock, directory, harness, _ = synthetic_runtime(
        monkeypatch, (market, None, None), rest_hostname="openapi.okx.com"
    )
    monkeypatch.setattr(subject, "native_stamp", clock.stamp)
    monkeypatch.setattr(runtime, "_native_recheck_root_identity", lambda _: (1, 2))
    public = harness.public

    def server(request):
        if request.url.path == "/api/v5/public/time":
            return [{"ts": ms(clock.last)}]
        return public(request)

    harness.public = server
    return directory, harness


@pytest.mark.asyncio
async def test_initial_global_replays_every_component_and_never_issues_legacy_authority(
    owned_global, tmp_path, monkeypatch
):
    directory, harness = owned_global
    monkeypatch.setattr(
        subject.ws, "PUBLIC_WS_URL", "wss://ws.okx.com:8443/ws/v5/public"
    )
    result = await subject.capture_initial_global_public_market_v1(
        tmp_path, instrument_id="BTC-USDT-SWAP", market_policy=policy(counts=(240,) * 4)
    )
    assert result.code == "initial_global_public_captured", tuple(directory.content)
    assert result.admission == "DENY"
    assert result.account_requests == result.order_writes == 0
    assert result.execution_authority is result.qualification_performed is False
    assert type(result.packet) is subject.GlobalPublicComponentPacketV1
    assert result.packet.environment == "anonymous_global_public"
    assert result.packet.barrier_completed_at is None
    assert [frame.timeframe for frame in result.packet.candles.frames] == [
        "4H",
        "1H",
        "15m",
        "5m",
    ]
    assert result.packet.ws.endpoint == journal.GLOBAL_WS_ORIGIN
    assert harness.connections == [journal.GLOBAL_WS_ORIGIN]
    assert all(
        request.method == "GET"
        and request.url.host == "openapi.okx.com"
        and not any(
            name.startswith("ok-access")
            or name in {"authorization", "cookie", "x-simulated-trading"}
            for name in request.headers
        )
        for request in harness.requests
    )
    assert (
        harness.requests[0].url.path
        == harness.requests[-1].url.path
        == ("/api/v5/public/time")
    )
    assert not harness.requests[0].url.query and not harness.requests[-1].url.query
    assert {request.url.path for request in harness.requests} == {
        "/api/v5/public/time",
        "/api/v5/market/ticker",
        "/api/v5/public/mark-price",
        "/api/v5/public/funding-rate",
        "/api/v5/market/candles",
        "/api/v5/market/books",
        "/api/v5/public/open-interest",
    }
    raw_plan = directory.content["plan.json"]
    assert sha(raw_plan) == result.plan_sha256
    summary, events, packet = runtime.replay_public_runtime(
        directory, expected_plan_sha256=result.plan_sha256
    )
    assert summary["disposition"] == "captured"
    assert packet == result.packet
    assert sum(item["kind"] == "time_validated" for item in events) == 2
    assert [
        decode(directory.content[f"event-{item['index']:04d}.raw"])["observation"][
            "schema_version"
        ]
        for item in events
        if item["kind"].startswith("clock_")
    ] == ["ctcc.windows_clock_observation.v3"] * 2
    readback = subject.read_global_public_capture_v1(
        tmp_path,
        expected_plan_sha256=result.plan_sha256,
        expected_journal_sha256=result.journal_sha256,
    )
    assert readback.packet.packet_json == result.packet.packet_json
    with pytest.raises(ValueError, match="public_record_invalid"):
        legacy.validate_collected_public_market(result.packet)
    with pytest.raises(ValueError, match="global_public_readback_unavailable"):
        subject.read_global_public_capture_v1(
            tmp_path,
            expected_plan_sha256=result.plan_sha256,
            expected_journal_sha256="0" * 64,
        )
    harness.assert_closed()


@pytest.mark.asyncio
async def test_clock_refusal_preserves_raw_attempt_without_provider(
    owned_global, tmp_path, monkeypatch
):
    directory, harness = owned_global

    def refuse():
        raise PublicReceiptError("synthetic_no_clock")

    monkeypatch.setattr(native_clock, "native_os_clock_ipc", refuse)
    result = await subject.capture_initial_global_public_market_v1(
        tmp_path, instrument_id="BTC-USDT-SWAP", market_policy=policy(counts=(240,) * 4)
    )
    assert result.code == "initial_global_public_denied"
    assert not harness.requests and not harness.connections
    summary = decode(directory.content["summary.json"])
    assert summary["disposition"] == "rejected"
    assert decode(directory.content["event-0000.raw"])["outcome"] == "unobserved"


@pytest.mark.asyncio
async def test_unbracketed_exchange_time_denies_all_component_io(
    owned_global, tmp_path
):
    directory, harness = owned_global
    harness.public = lambda _: [{"ts": "1000000000000"}]
    result = await subject.capture_initial_global_public_market_v1(
        tmp_path, instrument_id="BTC-USDT-SWAP", market_policy=policy(counts=(240,) * 4)
    )
    assert result.code == "initial_global_public_denied"
    assert [request.url.path for request in harness.requests] == ["/api/v5/public/time"]
    assert not harness.connections
    assert decode(directory.content["summary.json"])["disposition"] == "rejected"
    assert any(
        name.endswith(".raw") and b"1000000000000" in raw
        for name, raw in directory.content.items()
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["ws_disconnect", "rest_read_timeout"])
async def test_global_transport_fault_joins_siblings_and_seals_rejection(
    owned_global, tmp_path, monkeypatch, failure
):
    directory, harness = owned_global
    ticker_path = "/api/v5/market/ticker"
    quote_started = asyncio.Event()
    ws_waiting = asyncio.Event()
    blocked = asyncio.Event()
    quote_cancelled = asyncio.Event()
    ws_cancelled = asyncio.Event()
    fault_detail = "synthetic-private-exception-text"
    prefix = b'{"code":"0",'
    transport_request = httpx.MockTransport.handle_async_request
    recv = harness.socket.recv

    class TimeoutStream(Stream):
        async def __aiter__(self):
            yield prefix
            await ws_waiting.wait()
            raise httpx.ReadTimeout(fault_detail)

    async def request(transport, outbound):
        response = await transport_request(transport, outbound)
        if outbound.url.path != ticker_path:
            return response
        quote_started.set()
        if failure == "rest_read_timeout":
            original = harness.streams[-1]
            assert response.stream is original
            stream = TimeoutStream(prefix)
            await original.aclose()
            harness.streams.append(stream)
            response.stream = stream
            return response
        try:
            await blocked.wait()
        finally:
            await response.aclose()
            quote_cancelled.set()

    async def receive():
        if not harness.socket.receives:
            return await recv()
        ws_waiting.set()
        if failure == "ws_disconnect":
            await quote_started.wait()
            raise ConnectionClosedError(None, None)
        try:
            await blocked.wait()
        finally:
            ws_cancelled.set()

    monkeypatch.setattr(httpx.MockTransport, "handle_async_request", request)
    monkeypatch.setattr(harness.socket, "recv", receive)
    market_policy = policy(counts=(240,) * 4)
    async with asyncio.timeout(10):
        result = await subject.capture_initial_global_public_market_v1(
            tmp_path, instrument_id="BTC-USDT-SWAP", market_policy=market_policy
        )
    assert result.code == "initial_global_public_denied"
    assert result.packet is result.post_readback_receipt_json is None
    assert result.account_requests == result.order_writes == 0
    assert result.execution_authority is result.qualification_performed is False
    harness.assert_closed()
    assert len(harness.clients) == 4
    assert harness.connections == [journal.GLOBAL_WS_ORIGIN]
    assert len(harness.socket.sent) == harness.socket.closes == 1
    assert sum(item.url.path == ticker_path for item in harness.requests) == 1
    assert all(item.method == "GET" for item in harness.requests)

    plan_pin = sha(directory.content["plan.json"])
    summary, events, packet = runtime.replay_public_runtime(
        directory, expected_plan_sha256=plan_pin
    )
    assert summary["disposition"] == "rejected" and packet is None
    assert summary["packet_sha256"] is None
    kinds = [item["kind"] for item in events]
    assert kinds.count("ws_ack") == 1
    assert "ws_ticker" not in kinds and "packet" not in kinds
    failed_roles = [
        item["metadata"]["role"]
        for item in events
        if item["kind"] == "component_failed"
    ]
    if failure == "ws_disconnect":
        assert quote_cancelled.is_set() and failed_roles == ["ws"]
    else:
        assert ws_cancelled.is_set() and failed_roles == ["quote"]
        assert any(
            name.endswith(".raw") and raw == prefix
            for name, raw in directory.content.items()
        )
        quote_id = next(
            item["metadata"]["id"]
            for item in events
            if item["kind"] == "chunk"
            and directory.content[f"event-{item['index']:04d}.raw"] == prefix
        )
        quote_events = [
            item["kind"] for item in events if item["metadata"].get("id") == quote_id
        ]
        assert quote_events == ["request", "headers", "chunk", "request_failed"]
    assert all(fault_detail.encode() not in raw for raw in directory.content.values())

    original = directory.content.copy()
    observed = len(harness.requests)
    with pytest.raises(ValueError, match="global_public_readback_unavailable"):
        subject.read_global_public_capture_v1(
            tmp_path,
            expected_plan_sha256=plan_pin,
            expected_journal_sha256=sha(original["summary.json"]),
        )
    repeat = await subject.capture_initial_global_public_market_v1(
        tmp_path, instrument_id="BTC-USDT-SWAP", market_policy=market_policy
    )
    assert repeat.code == "initial_global_public_denied"
    assert directory.content == original and len(harness.requests) == observed
    assert harness.connections == [journal.GLOBAL_WS_ORIGIN]


@pytest.mark.asyncio
async def test_global_replay_refuses_cross_origin_packet_and_route_labels(
    owned_global, tmp_path
):
    directory, _ = owned_global
    result = await subject.capture_initial_global_public_market_v1(
        tmp_path, instrument_id="BTC-USDT-SWAP", market_policy=policy(counts=(240,) * 4)
    )
    assert result.packet is not None
    plan = decode(directory.content["plan.json"])
    for changes in (
        {"rest_origin": "https://www.okx.com"},
        {"ws_origin": "wss://ws.okx.com:8443/ws/v5/public"},
        {"environment": "demo"},
        {"registration_region": "global"},
    ):
        with pytest.raises(PublicReceiptError):
            journal._validate_plan(plan | changes)
    summary = decode(directory.content["summary.json"])
    packet_event_index = summary["event_count"] - 1
    leaf = f"event-{packet_event_index:04d}.raw"
    packet = decode(directory.content[leaf])
    packet["rest_origin"] = "https://www.okx.com"
    raw = canonical(packet)
    directory.content[leaf] = raw
    summary["packet_sha256"] = sha(raw)
    directory.content["summary.json"] = canonical(summary)

    def change(event, _directory):
        if event["kind"] == "packet":
            event.update(raw_sha256=sha(raw), raw_bytes=len(raw))
            event["metadata"]["bundle_sha256"] = sha(raw)

    resign_journal(directory, change)
    with pytest.raises(PublicReceiptError, match="runtime_global_packet_plan_mismatch"):
        runtime.replay_public_runtime(
            directory, expected_plan_sha256=result.plan_sha256
        )
