"""Synthetic native-owned Global component protocol, never external evidence."""

import pytest

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
