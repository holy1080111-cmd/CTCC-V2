"""Synthetic Live source ownership; no external or native path acceptance claim."""

import asyncio
import copy
import pickle
import sys
from contextlib import contextmanager
from dataclasses import replace
from datetime import timedelta
from types import ModuleType

import pytest

from app.domain.source_primitives import ClockStamp, decode, sha, utc_from_ns
from app.public_market_source import public_runtime_journal as journal
from app.trade_qualification import native_live_public_source_v1 as subject
from tests.unit.research.test_owned_public_runtime_p1 import synthetic_runtime
from tests.unit.research.test_public_journal_contracts import MemoryDirectory
from tests.unit.test_qualification_market_bridge import v2_engine_source
from tests.unit.test_qualification_one_shot import ms


@pytest.fixture(scope="module")
def market():
    return v2_engine_source("long")


@pytest.fixture
def live_source(monkeypatch, market, tmp_path):
    clock, raw, harness, _ = synthetic_runtime(
        monkeypatch, (market, None, None), rest_hostname="openapi.okx.com"
    )
    root = tmp_path / "live-public"

    class Parent(MemoryDirectory):
        def mkdir(self, name):
            super().mkdir(name)
            assert name == "raw"
            raw.content = self.content[name]

    parent = Parent({})
    parent.path, raw.path = root, root / "raw"

    @contextmanager
    def storage(path):
        assert path in {root, root / "raw"}
        yield parent if path == root else raw

    monkeypatch.setattr(journal, "_root_context", storage)
    monkeypatch.setattr(subject, "native_stamp", clock.stamp)
    monkeypatch.setattr(subject.production, "native_stamp", clock.stamp)
    monkeypatch.setattr(
        subject.transport,
        "_native_recheck_root_identity",
        lambda path: (1, 1) if path == root else (2, 2),
    )
    public = harness.public

    def server(request):
        if request.url.path == "/api/v5/public/time":
            return [{"ts": ms(clock.last)}]
        rows = public(request)
        if request.url.path == "/api/v5/public/funding-rate":
            rows[0].update(
                fundingTime=ms(market.market.next_funding_time),
                nextFundingTime=ms(
                    market.market.next_funding_time + timedelta(hours=8)
                ),
                method="current_period",
                formulaType="withRate",
                minFundingRate="-0.00375",
                maxFundingRate="0.00375",
                settState="settled",
                settFundingRate="0",
                nextFundingRate="",
                premium="0",
            )
        return rows

    harness.public = server
    key, requests = object(), {}

    def consumer(request, *, key):
        assert key is subject._COORDINATOR_REQUEST_KEY_V1
        return requests.pop(request)

    def coordinator(candidate):
        if candidate is not key:
            raise subject.LivePublicSourceError("synthetic_private_key_refused")
        return consumer

    monkeypatch.setattr(subject, "_coordinator", coordinator)

    def request(stage="initial", **changes):
        barrier = (
            ClockStamp.model_validate(clock.stamp()) if stage == "post_g12" else None
        )
        params = subject.LivePublicCaptureParametersV1(
            root=root,
            report_id="fixed-live-report",
            instrument_id="BTC-USDT-SWAP",
            invocation=object(),
            stage=stage,
            expires_at=utc_from_ns(clock.base) + timedelta(seconds=20),
            expires_monotonic_ns=20_000_000_000,
            candidate_sha256="a" * 64 if stage == "post_g12" else None,
            publication_barrier=barrier,
            publication_receipt_sha256="b" * 64 if stage == "post_g12" else None,
            public_policy_sha256=subject.POLICY_SHA256,
        )
        params = replace(params, **changes)
        opaque = object()
        requests[opaque] = params
        return opaque, params

    yield clock, parent, raw, harness, key, request
    for owner in tuple(subject._OWNERS):
        owner.close()
    assert not subject._INFLIGHT
    harness.assert_closed()


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["initial", "post_g12"])
async def test_owned_capture_reopens_unchanged_production_raw_and_projects_live(
    live_source, stage, market
):
    _, parent, raw, harness, key, request = live_source
    opaque, params = request(stage)
    owner = await subject.capture_owned_live_public_v1(opaque, key=key)
    value = subject._replay_owned_live_public_v1(owner, key=key)
    assert value.invocation is params.invocation
    assert value.environment == "live"
    assert (
        value.source_environment
        == value.packet.environment
        == journal.GLOBAL_ENVIRONMENT
    )
    assert value.packet.report_id != value.report_id == params.report_id
    assert (
        value.quote.environment == "live" and value.quote.report_id == params.report_id
    )
    assert value.reference.report_id == params.report_id
    assert value.stage == stage
    assert value.candidate_sha256 == params.candidate_sha256
    assert value.publication_receipt_sha256 == params.publication_receipt_sha256
    assert value.execution_authority is value.account_complete is False
    assert value.historical_availability_verified is False
    assert value.quote.execution_authority is False
    assert value.packet.barrier_completed_at is None
    assert value.packet.rest_origin == journal.GLOBAL_REST_ORIGIN
    assert value.packet.ws_origin == journal.GLOBAL_WS_ORIGIN
    assert tuple(value.market.candles) == ("4H", "1H", "15m", "5m")
    assert (
        value.market.ticker.volume_contracts_24h
        == market.market.ticker.volume_contracts_24h
    )
    assert (
        value.market.ticker.volume_currency_24h
        == market.market.ticker.volume_currency_24h
    )
    assert (
        value.market.candles["5m"][0].volume_quote
        == market.market.candles["5m"][0].volume_quote
    )
    assert (
        value.quote.funding.raw_body == value.packet.quote.provenance[2].response_body
    )
    assert value.quote.funding.forecast.settlement_at == market.market.next_funding_time
    assert {item.name: item.raw for item in value.raw_files} == raw.content
    assert all(sha(item.raw) == item.sha256 for item in value.raw_files)
    receipt = decode(value.receipt_json)
    assert receipt == decode(parent.content[subject._FILE])
    assert receipt["raw_packet_relabelled"] is False
    assert receipt["execution_authority"] is False
    assert receipt["raw_file_count"] == len(value.raw_files)
    assert receipt["raw_total_bytes"] == sum(len(item.raw) for item in value.raw_files)
    assert receipt["packet_sha256"] == value.packet.bundle_sha256
    assert receipt["plan_sha256"] == value.plan_sha256 == sha(raw.content["plan.json"])
    assert (
        receipt["journal_sha256"]
        == value.journal_sha256
        == sha(raw.content["summary.json"])
    )
    assert value.raw_inventory_sha256 == receipt["raw_inventory_sha256"]
    _, events = journal.replay_runtime_attempt(
        raw, expected_plan_sha256=value.plan_sha256
    )
    starts = [
        event["metadata"]["started"]
        for event in events
        if event["kind"] in {"request", "ws_connect"}
    ]
    for stamp in starts:
        assert all(stamp[field] > receipt["capture_started"][field] for field in stamp)
        if stage == "post_g12":
            assert all(
                stamp[field] > receipt["publication_barrier"][field] for field in stamp
            )
    assert receipt["http_request_count"] == len(harness.requests)
    assert receipt["ws_connection_count"] == len(harness.connections) == 1
    assert all(
        req.method == "GET" and req.url.host == "openapi.okx.com"
        for req in harness.requests
    )
    assert all(
        not any(
            name in {"authorization", "cookie", "x-simulated-trading"}
            or name.startswith("ok-access")
            for name in req.headers
        )
        for req in harness.requests
    )
    before = (len(harness.requests), len(harness.connections))
    replay = subject._replay_owned_live_public_v1(owner, key=key)
    assert replay.receipt_json == value.receipt_json
    assert replay.packet.packet_json == value.packet.packet_json
    assert replay.evaluated_at > value.evaluated_at
    assert (len(harness.requests), len(harness.connections)) == before


@pytest.mark.asyncio
async def test_real_entry_refuses_missing_coordinator_before_clock_or_io(monkeypatch):
    monkeypatch.delitem(sys.modules, subject._COORDINATOR, raising=False)

    def forbidden():
        pytest.fail("unowned request reached clock")

    monkeypatch.setattr(subject, "native_stamp", forbidden)
    with pytest.raises(subject.LivePublicSourceError):
        await subject.capture_owned_live_public_v1(object(), key=object())


@pytest.mark.asyncio
async def test_params_and_saved_receipts_do_not_construct_or_recover_owner(live_source):
    _, parent, _, harness, key, request = live_source
    opaque, params = request()
    with pytest.raises(subject.LivePublicSourceError):
        await subject.capture_owned_live_public_v1(params, key=key)
    assert not parent.content and not harness.requests
    owner = await subject.capture_owned_live_public_v1(opaque, key=key)
    for operation in (
        lambda: subject.OwnedLivePublicSourceV1(),
        lambda: copy.copy(owner),
        lambda: copy.deepcopy(owner),
        lambda: pickle.dumps(owner),
    ):
        with pytest.raises(subject.LivePublicSourceError):
            operation()
    forged = object.__new__(subject.OwnedLivePublicSourceV1)
    with pytest.raises(subject.LivePublicSourceError):
        subject._replay_owned_live_public_v1(forged, key=key)
    owner.close()
    with pytest.raises(subject.LivePublicSourceError):
        subject._replay_owned_live_public_v1(owner, key=key)
    assert subject._FILE in parent.content
    with pytest.raises(subject.LivePublicSourceError):
        await subject.capture_owned_live_public_v1(opaque, key=key)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        {"public_policy_sha256": "0" * 64},
        {"candidate_sha256": "bad"},
        {"instrument_id": "SOL-USDT-SWAP"},
        {"stage": "demo"},
        {"report_id": "../report"},
        {"publication_receipt_sha256": "a" * 64},
        {"expires_monotonic_ns": True},
        {"expires_monotonic_ns": 1},
    ],
)
async def test_invalid_or_expired_parameters_never_start_io(live_source, change):
    _, parent, _, harness, key, request = live_source
    opaque, _ = request(**change)
    with pytest.raises(subject.LivePublicSourceError):
        await subject.capture_owned_live_public_v1(opaque, key=key)
    assert not parent.content and not harness.requests and not harness.connections


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        {"publication_barrier": None},
        {"candidate_sha256": None},
        {"publication_receipt_sha256": None},
    ],
)
async def test_post_g12_requires_original_candidate_barrier_receipt(
    live_source, change
):
    _, parent, _, harness, key, request = live_source
    opaque, _ = request("post_g12", **change)
    with pytest.raises(subject.LivePublicSourceError):
        await subject.capture_owned_live_public_v1(opaque, key=key)
    assert not parent.content and not harness.requests


@pytest.mark.asyncio
async def test_future_original_barrier_does_not_get_replaced_by_now(live_source):
    clock, parent, _, harness, key, request = live_source
    future = ClockStamp.model_validate(
        {"utc_ns": clock.base + 1_000_000_000, "monotonic_ns": 1_000_000_000}
    )
    opaque, _ = request("post_g12", publication_barrier=future)
    with pytest.raises(subject.LivePublicSourceError):
        await subject.capture_owned_live_public_v1(opaque, key=key)
    assert not parent.content and not harness.requests


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation", ["raw", "envelope", "extra", "root_identity", "stale", "expired"]
)
async def test_replay_tamper_freshness_or_identity_burns_owner(
    live_source, monkeypatch, mutation
):
    clock, parent, raw, harness, key, request = live_source
    opaque, _ = request("post_g12")
    owner = await subject.capture_owned_live_public_v1(opaque, key=key)
    before = (len(harness.requests), len(harness.connections))
    if mutation == "raw":
        name = next(name for name in raw.content if name.endswith(".raw"))
        raw.content[name] += b" "
    elif mutation == "envelope":
        parent.content[subject._FILE] += b" "
    elif mutation == "extra":
        raw.content["extra.json"] = b"{}"
    elif mutation == "root_identity":
        monkeypatch.setattr(
            subject.transport, "_native_recheck_root_identity", lambda _: (99, 99)
        )
    else:
        clock.count += 6000 if mutation == "stale" else 40_000
    with pytest.raises(subject.LivePublicSourceError):
        subject._replay_owned_live_public_v1(owner, key=key)
    assert owner not in subject._OWNERS
    assert (len(harness.requests), len(harness.connections)) == before
    assert "summary.json" in raw.content


@pytest.mark.asyncio
async def test_cross_task_owner_refusal_leaves_source_and_no_new_io(live_source):
    _, parent, _, harness, key, request = live_source
    opaque, _ = request()
    owner = await subject.capture_owned_live_public_v1(opaque, key=key)
    before = len(harness.requests)

    async def another_task():
        with pytest.raises(subject.LivePublicSourceError):
            subject._replay_owned_live_public_v1(owner, key=key)

    await asyncio.create_task(another_task())
    assert owner not in subject._OWNERS
    assert len(harness.requests) == before and subject._FILE in parent.content


@pytest.mark.asyncio
async def test_late_envelope_publish_failure_retains_successful_raw_journal(
    live_source, monkeypatch
):
    _, parent, raw, _, key, request = live_source
    publish = parent.publish

    def late(name, payload):
        publish(name, payload)
        raise OSError("synthetic private filesystem detail")

    monkeypatch.setattr(parent, "publish", late)
    opaque, _ = request()
    with pytest.raises(
        subject.LivePublicSourceError, match="^native_live_public_capture_failed$"
    ):
        await subject.capture_owned_live_public_v1(opaque, key=key)
    assert subject._FILE in parent.content and "summary.json" in raw.content
    assert not subject._OWNERS


@pytest.mark.asyncio
async def test_cancelled_capture_keeps_rejection_journal_and_closes_transport(
    live_source,
):
    _, parent, raw, harness, key, request = live_source
    harness.public_hold = asyncio.Event()
    opaque, _ = request()
    task = asyncio.create_task(subject.capture_owned_live_public_v1(opaque, key=key))
    await harness.started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert "raw" in parent.content and subject._FILE not in parent.content
    assert decode(raw.content["summary.json"])["disposition"] == "rejected"
    assert not subject._OWNERS and not subject._INFLIGHT


@pytest.mark.asyncio
async def test_wrong_private_key_never_consumes_genuine_request(live_source):
    _, parent, _, harness, key, request = live_source
    opaque, _ = request()
    with pytest.raises(subject.LivePublicSourceError):
        await subject.capture_owned_live_public_v1(opaque, key=object())
    assert not parent.content and not harness.requests
    owner = await subject.capture_owned_live_public_v1(opaque, key=key)
    assert subject._replay_owned_live_public_v1(owner, key=key).stage == "initial"


def test_fixed_coordinator_rejects_unregistered_function_or_key(monkeypatch):
    module = ModuleType(subject._COORDINATOR)
    module._PUBLIC_SOURCE_KEY_V1 = object()
    module._consume_live_public_request_v1 = lambda request, key: request
    monkeypatch.setitem(sys.modules, subject._COORDINATOR, module)
    for key in (object(), None, module._PUBLIC_SOURCE_KEY_V1):
        with pytest.raises(subject.LivePublicSourceError):
            subject._coordinator(key)


@pytest.mark.asyncio
async def test_initial_clock_failure_is_redacted_and_leaves_no_inflight(
    live_source, monkeypatch
):
    _, parent, _, harness, key, request = live_source

    def unavailable():
        raise OSError("synthetic private host details")

    monkeypatch.setattr(subject, "native_stamp", unavailable)
    opaque, _ = request()
    with pytest.raises(
        subject.LivePublicSourceError, match="^native_live_public_clock_unavailable$"
    ):
        await subject.capture_owned_live_public_v1(opaque, key=key)
    assert not parent.content and not harness.requests and not subject._INFLIGHT


@pytest.mark.asyncio
async def test_cached_before_barrier_journal_cannot_become_post_g12_capture(
    live_source, monkeypatch
):
    _, parent, raw, harness, key, request = live_source
    capture = subject.production.capture_initial_global_public_market_v1
    retained = []

    async def original(*args, **kwargs):
        result = await capture(*args, **kwargs)
        retained.append(result)
        return result

    monkeypatch.setattr(
        subject.production, "capture_initial_global_public_market_v1", original
    )
    opaque, _ = request()
    first = await subject.capture_owned_live_public_v1(opaque, key=key)
    first.close()
    files = dict(raw.content)
    before = (len(harness.requests), len(harness.connections))
    parent.content.clear()

    def restored(name):
        assert name == "raw"
        parent.content[name] = dict(files)
        raw.content = parent.content[name]

    async def cached(*_args, **_kwargs):
        return retained[0]

    monkeypatch.setattr(parent, "mkdir", restored)
    monkeypatch.setattr(
        subject.production, "capture_initial_global_public_market_v1", cached
    )
    opaque, _ = request("post_g12")
    with pytest.raises(subject.LivePublicSourceError):
        await subject.capture_owned_live_public_v1(opaque, key=key)
    assert (len(harness.requests), len(harness.connections)) == before
    assert "summary.json" in raw.content and subject._FILE not in parent.content
    assert not subject._OWNERS
