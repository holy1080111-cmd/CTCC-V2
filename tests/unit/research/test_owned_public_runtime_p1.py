"""Synthetic end-to-end P1 contracts; no native TLS/path/clock acceptance claim."""

import asyncio
import copy
import os
import pickle
import ssl
from contextlib import contextmanager
from threading import get_ident

import httpx
import pytest

from app.public_market_source import public_clock as clock_module
from app.public_market_source import public_runtime_journal as journal
from app.public_market_source.public_market_receipts import (
    PublicReceiptError,
    canonical,
    decode,
    sha,
    utc_from_ns,
)
from app.trade_qualification import post_g12_public_runtime as coordinator
from app.trade_qualification import public_source_runtime as runtime
from tests.unit.research.test_public_clock_v2 import fixture as healthy_clock
from tests.unit.research.test_public_clock_v2 import resign
from tests.unit.research.test_public_journal_contracts import MemoryDirectory
from tests.unit.test_qualification_one_shot import (
    CaptureHarness,
    synthetic_publisher,
)
from tests.unit.test_qualification_one_shot import (
    inputs as inputs,  # noqa: PLC0414 -- re-export parametrized fixture
)
from tests.unit.test_qualification_public_market_collector import policy


class SyntheticClock:
    def __init__(self, start):
        self.base = int(start.timestamp()) * 1_000_000_000 + start.microsecond * 1000
        self.count = 0
        self.last = start

    def stamp(self):
        self.count += 1
        value = {
            "utc_ns": self.base + self.count * 1_000_000,
            "monotonic_ns": self.count * 1_000_000,
        }
        self.last = utc_from_ns(value["utc_ns"])
        return value

    def health(self):
        value = healthy_clock()
        for name in ("host_before", "status", "host_after"):
            value["diagnostic"][name]["request_start"] = self.stamp()
            value["diagnostic"][name]["completed"] = self.stamp()
        value["sample"] = self.stamp()
        return resign(value)


def synthetic_runtime(monkeypatch, inputs):
    """Only tests replace TLS admission and native storage/clock; never production."""
    source, _values, _run = inputs
    clock = SyntheticClock(source.evaluated_at)
    packets = synthetic_publisher(monkeypatch)
    directory = MemoryDirectory({})

    @contextmanager
    def root(_):
        yield directory

    monkeypatch.setattr(journal, "_root_context", root)
    monkeypatch.setattr(runtime, "native_stamp", clock.stamp)
    monkeypatch.setattr(coordinator, "native_stamp", clock.stamp)
    monkeypatch.setattr(clock_module, "native_os_clock", clock.health)
    harness = CaptureHarness(monkeypatch, source, clock)
    factory = runtime.public._new_client
    proof = {
        "classification": "owned_native_tls",
        "hostname": "www.okx.com",
        "version": "TLSv1.3",
        "peer_sha256": "1" * 64,
    }

    def new_client(owner, role):
        state = runtime._state(owner)
        assert role not in state["client_roles"]
        client = factory()
        state["clients"][client] = (role, ssl.create_default_context())
        state["client_roles"].add(role)
        return client

    def verified(owner, client):
        assert type(client) is httpx.AsyncClient
        return runtime._state(owner)["clients"][client]

    def connected(owner, socket):
        state = runtime._state(owner)
        assert socket is harness.socket and state["socket"] is None
        state["socket"] = socket
        runtime._event(
            owner,
            "ws_connected",
            {"connected": state["last"], "tls": {**proof, "hostname": "ws.okx.com"}},
        )

    monkeypatch.setattr(runtime, "_new_owned_client", new_client)
    monkeypatch.setattr(runtime, "_verified_client", verified)
    monkeypatch.setattr(runtime, "_http_tls", lambda *_: dict(proof))
    monkeypatch.setattr(runtime, "_ws_connected", connected)
    return clock, directory, harness, packets


async def invoke(inputs, tmp_path):
    source, values, run = inputs
    return await coordinator.publish_capture_public_recheck(
        tmp_path / "g12",
        tmp_path / "fresh",
        source.market,
        run=run,
        original_inputs=values,
        market_policy=policy(counts=(240, 240, 240, 240)),
    )


def replay(directory):
    summary, events, _ = runtime.replay_public_runtime(
        directory, expected_plan_sha256=sha(directory.content["plan.json"])
    )
    return summary, events


def resign_journal(directory, mutate):
    summary = decode(directory.content["summary.json"])
    head = sha(directory.content["plan.json"])
    for index in range(summary["event_count"]):
        name = f"event-{index:04d}.json"
        event = decode(directory.content[name])
        mutate(event, directory)
        event["previous_sha256"] = head
        directory.content[name] = canonical(event)
        head = sha(directory.content[name])
    summary["head_sha256"] = head
    directory.content["summary.json"] = canonical(summary)


@pytest.mark.asyncio
async def test_same_invocation_raw_collectors_bridge_recheck_no_authority(
    inputs, tmp_path, monkeypatch
):
    _clock, directory, harness, packets = synthetic_runtime(monkeypatch, inputs)
    result = await invoke(inputs, tmp_path)
    assert result.code == "public_runtime_captured_account_authority_missing", (
        result.code,
        tuple(directory.content),
    )
    assert result.packet is not None and result.recheck is not None
    assert len(packets) == 1
    assert (
        not result.execution_authority
        and not result.account_complete
        and not result.order_submitted
    )
    assert not result.atomic_risk_reserved and not result.original_source_verified
    summary, events = replay(directory)
    assert summary["disposition"] == "captured"
    assert result.journal_sha256 == sha(directory.content["summary.json"])
    assert all(req.method == "GET" for req in harness.requests)
    assert not any(
        "/account/" in req.url.path or "/trade/" in req.url.path
        for req in harness.requests
    )
    assert len(harness.clients) == 3 and len(harness.connections) == 1
    assert len([e for e in events if e["kind"] == "request"]) == len(harness.requests)
    assert (
        result.recheck.origin.candidate.candidate_entry
        == result.evidence.result.candidate_entry
    )
    harness.assert_closed()
    assert (
        not runtime._SOURCES and not runtime._RESULTS and not coordinator._PUBLICATIONS
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", ["clock_before", "malformed", "header_clock", "chunk_clock", "overflow"]
)
async def test_real_returned_bytes_retained_on_rejection(
    inputs, tmp_path, monkeypatch, failure
):
    clock, directory, harness, _packets = synthetic_runtime(monkeypatch, inputs)
    if failure == "clock_before":
        monkeypatch.setattr(
            clock_module,
            "native_os_clock",
            lambda: (_ for _ in ()).throw(PublicReceiptError("native_denied")),
        )
    elif failure == "malformed":
        original = harness.public
        harness.public = lambda request: (
            [] if request.url.path.endswith("/ticker") else original(request)
        )
    else:
        original = runtime._event
        triggered = False

        def event(owner, kind, metadata, raw=None):
            nonlocal triggered
            result = original(owner, kind, metadata, raw)
            if not triggered and kind == (
                "request" if failure == "header_clock" else "headers"
            ):
                triggered = True
                if failure == "overflow":
                    runtime._state(owner)["expires_at"] = clock.last
                else:
                    monkeypatch.setattr(
                        runtime,
                        "native_stamp",
                        lambda: (_ for _ in ()).throw(
                            PublicReceiptError("synthetic_receipt_clock_failed")
                        ),
                    )
            return result

        monkeypatch.setattr(runtime, "_event", event)
    result = await invoke(inputs, tmp_path)
    assert result.code == "public_runtime_denied"
    assert not result.execution_authority and result.packet is None
    summary, events = replay(directory)
    assert summary["disposition"] == "rejected" and summary["packet_sha256"] is None
    if failure == "clock_before":
        assert not harness.requests and not harness.connections
    elif failure == "malformed":
        assert any(e["kind"] == "chunk" for e in events)
        assert any(e["kind"] == "component_failed" for e in events)
    elif failure == "header_clock":
        assert any(
            e["kind"] == "headers" and e["metadata"]["received"] is None for e in events
        )
    else:
        assert any(
            e["kind"] == "chunk"
            and e["metadata"]["received"] is None
            and e["raw_bytes"] > 0
            for e in events
        )
    harness.assert_closed()
    assert not runtime._SOURCES and not runtime._RESULTS


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation", ["clock", "body", "packet", "extra_file", "summary_authority"]
)
async def test_resigned_tampered_audit_cannot_replay(
    inputs, tmp_path, monkeypatch, mutation
):
    _, directory, _, _ = synthetic_runtime(monkeypatch, inputs)
    result = await invoke(inputs, tmp_path)
    assert result.packet is not None
    if mutation == "extra_file":
        directory.content["extra.raw"] = b"unknown"
    elif mutation == "summary_authority":
        value = decode(directory.content["summary.json"])
        value["execution_authority"] = True
        directory.content["summary.json"] = canonical(value)
    else:
        changed = False

        def mutate(event, target):
            nonlocal changed
            if changed:
                return
            if mutation == "clock" and event["kind"] == "request":
                event["metadata"]["started"]["monotonic_ns"] = 0
                changed = True
            elif mutation == "body" and event["kind"] == "chunk":
                name = f"event-{event['index']:04d}.raw"
                target.content[name] += b" "
                event["raw_bytes"] += 1
                event["raw_sha256"] = sha(target.content[name])
                event["metadata"]["observed_bytes"] += 1
                event["metadata"]["observed_sha256"] = event["raw_sha256"]
                changed = True
            elif mutation == "packet" and event["kind"] == "headers":
                event["metadata"]["received"]["utc_ns"] += 1_000_000
                changed = True

        resign_journal(directory, mutate)
    with pytest.raises((PublicReceiptError, ValueError)):
        replay(directory)
    assert not runtime._RESULTS


@pytest.mark.asyncio
async def test_before_health_must_be_after_barrier_even_when_healthy(
    inputs, tmp_path, monkeypatch
):
    clock, _directory, harness, _ = synthetic_runtime(monkeypatch, inputs)
    stale = clock.health()
    stale["diagnostic"]["host_before"]["request_start"]["monotonic_ns"] = 0
    stale = resign(stale)
    monkeypatch.setattr(clock_module, "native_os_clock", lambda: stale)
    result = await invoke(inputs, tmp_path)
    assert result.code == "public_runtime_denied"
    assert not harness.requests and not harness.connections
    assert not runtime._RESULTS


@pytest.mark.parametrize(
    "kind",
    [
        runtime._Source,
        runtime._CapturedPublic,
        coordinator._Publication,
        journal._RuntimeAttempt,
    ],
)
@pytest.mark.parametrize("transfer", [copy.copy, copy.deepcopy, pickle.dumps])
def test_opaque_owners_cannot_be_copied_or_pickled(kind, transfer):
    value = object.__new__(kind)
    with pytest.raises(ValueError):
        transfer(value)


@pytest.mark.asyncio
async def test_exact_types_without_registry_do_not_create_authority():
    with pytest.raises(ValueError):
        coordinator._take_publication(object.__new__(coordinator._Publication))
    with pytest.raises(ValueError):
        runtime._consume_public_capture(
            object.__new__(runtime._CapturedPublic), object()
        )
    with pytest.raises(ValueError):
        runtime._state(object.__new__(runtime._Source))


@pytest.mark.parametrize(
    "mode", ["none", "object", "unhandshaken", "wrong_context", "wrong_hostname"]
)
def test_native_tls_requires_real_verified_negotiated_sslobject(mode):
    context = ssl.create_default_context()
    other = ssl.create_default_context()
    value = (
        None
        if mode == "none"
        else object()
        if mode == "object"
        else (other if mode == "wrong_context" else context).wrap_bio(
            ssl.MemoryBIO(),
            ssl.MemoryBIO(),
            server_side=False,
            server_hostname="wrong.invalid"
            if mode == "wrong_hostname"
            else "www.okx.com",
        )
    )
    with pytest.raises(runtime.PublicSourceRuntimeError):
        runtime._tls_proof(value, context, "www.okx.com")


@pytest.mark.asyncio
async def test_caller_mock_client_cannot_cross_native_transport_boundary():
    owner = runtime._Source(runtime._ISSUER)
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: None), trust_env=False
    )
    runtime._SOURCES[owner] = {
        "pid": os.getpid(),
        "thread": get_ident(),
        "loop": asyncio.get_running_loop(),
        "parent": asyncio.current_task(),
        "deadline": asyncio.get_running_loop().time() + 60,
        "clients": {client: ("quote", ssl.create_default_context())},
    }
    try:
        with pytest.raises(ValueError):
            runtime._verified_client(owner, client)
    finally:
        runtime._SOURCES.pop(owner)
        await client.aclose()


@pytest.mark.asyncio
async def test_external_cancellation_joins_every_owned_resource(
    inputs, tmp_path, monkeypatch
):
    _, directory, harness, _ = synthetic_runtime(monkeypatch, inputs)
    release = asyncio.Event()
    harness.public_hold = release
    task = asyncio.create_task(invoke(inputs, tmp_path))
    try:
        await asyncio.sleep(0)  # synchronous render completes before handshake budget
        async with asyncio.timeout(10):
            await harness.started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            async with asyncio.timeout(10):
                await task
        assert not runtime._SOURCES and not runtime._RESULTS
        assert replay(directory)[0]["disposition"] == "rejected"
        harness.assert_closed()
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_prestart_cancel_performs_no_publication_or_capture(
    inputs, tmp_path, monkeypatch
):
    _, directory, harness, packets = synthetic_runtime(monkeypatch, inputs)
    task = asyncio.create_task(invoke(inputs, tmp_path))
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not packets and not directory.content and not harness.requests
    assert (
        not runtime._SOURCES and not runtime._RESULTS and not coordinator._PUBLICATIONS
    )


@pytest.mark.asyncio
async def test_after_clock_failure_retains_all_public_raw_and_denies(
    inputs, tmp_path, monkeypatch
):
    clock, directory, harness, _ = synthetic_runtime(monkeypatch, inputs)
    calls = 0

    def health():
        nonlocal calls
        calls += 1
        if calls == 1:
            return clock.health()
        value = clock.health()
        raise clock_module.NativeClockObservationError(
            "synthetic_fixed_clock_denied", {"diagnostic": value["diagnostic"]}
        )

    monkeypatch.setattr(clock_module, "native_os_clock", health)
    result = await invoke(inputs, tmp_path)
    assert result.code == "public_runtime_denied"
    summary, events = replay(directory)
    assert summary["disposition"] == "rejected" and calls == 2
    assert len([e for e in events if e["kind"] == "response_closed"]) == len(
        harness.requests
    )
    assert any(
        e["kind"] == "clock_after" and e["metadata"]["outcome"] == "rejected"
        for e in events
    )
    assert not runtime._RESULTS
    harness.assert_closed()


@pytest.mark.asyncio
async def test_late_metadata_failure_keeps_raw_tail_without_complete_summary(
    inputs, tmp_path, monkeypatch
):
    _, directory, harness, _ = synthetic_runtime(monkeypatch, inputs)
    publish = directory.publish
    tail = []

    def failed(name, payload):
        if (
            name.startswith("event-")
            and name.endswith(".json")
            and decode(payload)["kind"] == "chunk"
        ):
            raw = name.removesuffix(".json") + ".raw"
            tail.append((raw, directory.content[raw]))
            raise OSError("synthetic_private_storage_detail")
        publish(name, payload)

    monkeypatch.setattr(directory, "publish", failed)
    result = await invoke(inputs, tmp_path)
    assert result.code == "public_runtime_denied" and result.journal_sha256 is None
    assert tail and all(directory.content[name] == raw for name, raw in tail)
    assert "summary.json" not in directory.content
    with pytest.raises((ValueError, KeyError)):
        replay(directory)
    assert not runtime._RESULTS
    harness.assert_closed()


@pytest.mark.asyncio
async def test_existing_receipt_cannot_start_new_capture(inputs, tmp_path, monkeypatch):
    from app.trade_evidence import gates

    _, directory, harness, _ = synthetic_runtime(monkeypatch, inputs)
    publish = gates.publish_evidence
    monkeypatch.setattr(
        gates,
        "publish_evidence",
        lambda *a, **kw: publish(*a, **kw).model_copy(
            update={"status": "already_present"}
        ),
    )
    result = await invoke(inputs, tmp_path)
    assert result.code in {"public_runtime_denied", "public_runtime_new_g12_required"}
    assert not directory.content and not harness.requests and not harness.connections
    assert not runtime._RESULTS


@pytest.mark.asyncio
async def test_cross_task_publication_consumption_burns_capability():
    value = coordinator._Publication(coordinator._ISSUER)
    coordinator._PUBLICATIONS[value] = {
        "parent": asyncio.current_task(),
        "pid": os.getpid(),
        "thread": get_ident(),
    }

    async def foreign():
        with pytest.raises(ValueError):
            coordinator._take_publication(value)

    await asyncio.create_task(foreign())
    with pytest.raises(ValueError):
        coordinator._take_publication(value)
    assert value not in coordinator._PUBLICATIONS


@pytest.mark.asyncio
async def test_capture_consume_wrong_invocation_burns_before_packet_or_clock(
    monkeypatch,
):
    value = runtime._CapturedPublic(runtime._ISSUER)
    runtime._RESULTS[value] = (
        object(),
        asyncio.current_task(),
        os.getpid(),
        get_ident(),
        object(),
        "1" * 64,
        object(),
        object(),
    )
    monkeypatch.setattr(
        runtime, "native_stamp", lambda: pytest.fail("wrong invocation reached clock")
    )
    with pytest.raises(ValueError):
        runtime._consume_public_capture(value, object())
    assert value not in runtime._RESULTS


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation",
    ["parsed_quote", "parsed_candle", "parsed_book", "flag", "extra_field", "policy"],
)
async def test_recomputed_storage_hashes_cannot_replace_packet_semantic_replay(
    inputs, tmp_path, monkeypatch, mutation
):
    _, directory, _, _ = synthetic_runtime(monkeypatch, inputs)
    result = await invoke(inputs, tmp_path)
    assert result.packet is not None

    def mutate(event, target):
        if event["kind"] != "packet":
            return
        name = f"event-{event['index']:04d}.raw"
        packet = decode(target.content[name], journal.MAX_RAW)
        if mutation == "parsed_quote":
            packet["quote"]["quote"]["bid"] = "0.0001"
        elif mutation == "parsed_candle":
            packet["candles"]["frames"][0]["verified_through"] = "2024-01-01T00:00:00Z"
        elif mutation == "parsed_book":
            packet["market_aux"]["bundle_sha256"] = "0" * 64
        elif mutation == "flag":
            packet["complete_path_verified"] = True
        elif mutation == "extra_field":
            packet["passed"] = True
        else:
            packet["policy"]["total_timeout_seconds"] += 1
        raw = canonical(packet)
        target.content[name] = raw
        event["raw_sha256"] = sha(raw)
        event["raw_bytes"] = len(raw)

    resign_journal(directory, mutate)
    summary = decode(directory.content["summary.json"])
    summary["packet_sha256"] = sha(
        directory.content[f"event-{summary['event_count'] - 1:04d}.raw"]
    )
    directory.content["summary.json"] = canonical(summary)
    # The storage graph alone remains intact. Typed component recomputation is
    # deliberately a separate qualification-layer obligation.
    journal.replay_runtime_attempt(
        directory, expected_plan_sha256=sha(directory.content["plan.json"])
    )
    with pytest.raises(ValueError):
        replay(directory)
    assert not runtime._RESULTS


@pytest.mark.asyncio
async def test_coherent_collection_policy_substitution_cannot_cross_plan_pin(
    inputs, tmp_path, monkeypatch
):
    _, directory, _, _ = synthetic_runtime(monkeypatch, inputs)
    result = await invoke(inputs, tmp_path)
    assert result.packet is not None
    policy = result.packet.policy.model_copy(update={"total_timeout_seconds": 31})
    packet = result.packet.model_copy(update={"policy": policy})
    packet = packet.model_copy(
        update={
            "bundle_sha256": runtime.public._digest(
                {k: v for k, v in packet.__dict__.items() if k != "bundle_sha256"}
            )
        }
    )
    packet = runtime.public.validate_collected_public_market(packet)
    raw = canonical(packet.model_dump(mode="json", round_trip=True))

    def mutate(event, target):
        if event["kind"] == "packet":
            target.content[f"event-{event['index']:04d}.raw"] = raw
            event["raw_sha256"] = sha(raw)
            event["raw_bytes"] = len(raw)
            event["metadata"]["bundle_sha256"] = packet.bundle_sha256

    resign_journal(directory, mutate)
    summary = decode(directory.content["summary.json"])
    summary["packet_sha256"] = sha(raw)
    directory.content["summary.json"] = canonical(summary)
    with pytest.raises(runtime.PublicSourceRuntimeError, match="readback_mismatch"):
        replay(directory)


@pytest.mark.asyncio
async def test_failed_terminal_cannot_precede_retained_observations(
    inputs, tmp_path, monkeypatch
):
    _, directory, harness, _ = synthetic_runtime(monkeypatch, inputs)
    original = harness.public
    harness.public = lambda request: (
        [] if request.url.path.endswith("/ticker") else original(request)
    )
    result = await invoke(inputs, tmp_path)
    assert result.code == "public_runtime_denied"
    assert replay(directory)[0]["disposition"] == "rejected"
    summary = decode(directory.content["summary.json"])
    summary["completed"] = decode(directory.content["plan.json"])["barrier"]
    directory.content["summary.json"] = canonical(summary)
    with pytest.raises(ValueError):
        replay(directory)


@pytest.mark.asyncio
async def test_ws_returned_bytes_survive_receipt_clock_failure(
    inputs, tmp_path, monkeypatch
):
    _, directory, harness, _ = synthetic_runtime(monkeypatch, inputs)
    receive = harness.socket.recv
    returned = []

    async def failed_clock_recv():
        raw = await receive()
        returned.append(raw.encode("utf-8"))
        monkeypatch.setattr(
            runtime,
            "native_stamp",
            lambda: (_ for _ in ()).throw(PublicReceiptError("synthetic_clock_failed")),
        )
        return raw

    monkeypatch.setattr(harness.socket, "recv", failed_clock_recv)
    result = await invoke(inputs, tmp_path)
    assert result.code == "public_runtime_denied"
    _, events = replay(directory)
    event = next(e for e in events if e["kind"] == "ws_ack")
    assert event["metadata"]["observed"] is None
    assert directory.content[f"event-{event['index']:04d}.raw"] == returned[0]
    harness.assert_closed()
    assert not runtime._RESULTS


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation", ["duplicate_header", "encoding", "length", "ws_hash", "terminal"]
)
async def test_resigned_response_metadata_cannot_launder_wire_observation(
    inputs, tmp_path, monkeypatch, mutation
):
    _, directory, _, _ = synthetic_runtime(monkeypatch, inputs)
    result = await invoke(inputs, tmp_path)
    assert result.packet is not None
    changed = False

    def mutate(event, _target):
        nonlocal changed
        if changed:
            return
        if event["kind"] == "headers" and mutation in {
            "duplicate_header",
            "encoding",
            "length",
        }:
            headers = event["metadata"]["safe_headers"]
            if mutation == "duplicate_header":
                headers.append(headers[0])
            elif mutation == "encoding":
                headers.append(["content-encoding", "gzip"])
            else:
                for pair in headers:
                    if pair[0] == "content-length":
                        pair[1] = "0"
            changed = True
        elif event["kind"] == "ws_ticker" and mutation == "ws_hash":
            event["metadata"]["observed_sha256"] = "0" * 64
            changed = True

    resign_journal(directory, mutate)
    if mutation == "terminal":
        summary = decode(directory.content["summary.json"])
        summary["completed"] = decode(directory.content["plan.json"])["barrier"]
        directory.content["summary.json"] = canonical(summary)
    with pytest.raises(ValueError):
        replay(directory)


@pytest.mark.asyncio
async def test_transport_swallowing_cancellation_does_not_issue_capture(
    inputs, tmp_path, monkeypatch
):
    _, directory, harness, _ = synthetic_runtime(monkeypatch, inputs)
    entered = asyncio.Event()

    class SwallowCancellation:
        async def wait(self):
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                return

    harness.public_hold = SwallowCancellation()
    task = asyncio.create_task(invoke(inputs, tmp_path))
    async with asyncio.timeout(10):
        await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        async with asyncio.timeout(10):
            await task
    summary, events = replay(directory)
    assert summary["disposition"] == "rejected"
    assert any(
        e["kind"] == "headers" and e["metadata"]["received"] is None for e in events
    )
    assert not runtime._SOURCES and not runtime._RESULTS
    harness.assert_closed()
