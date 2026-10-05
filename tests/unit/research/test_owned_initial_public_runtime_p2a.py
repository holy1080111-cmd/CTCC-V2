"""Synthetic P2a source-stage contracts; no native or trading acceptance claim."""

import asyncio
import copy
import pickle
from dataclasses import asdict

import pytest

from app.public_market_source.public_market_receipts import (
    PublicReceiptError,
    canonical,
    decode,
    sha,
    utc_from_ns,
)
from app.trade_qualification import qualification_runtime as initial
from tests.unit.research.test_owned_public_runtime_p1 import (
    coordinator,
    journal,
    replay,
    resign_journal,
    runtime,
    synthetic_runtime,
)
from tests.unit.test_qualification_market_bridge import v2_engine_source
from tests.unit.test_qualification_public_market_collector import policy


@pytest.fixture(scope="module")
def public_source():
    # Only this fixture's public market feeds the synthetic HTTP/WS harness.
    # No caller intent, G1--G11 run, account record or G12 reaches the new entry.
    return v2_engine_source("long")


def setup(monkeypatch, source):
    clock, directory, harness, publications = synthetic_runtime(
        monkeypatch, (source, None, None)
    )
    monkeypatch.setattr(initial, "native_stamp", clock.stamp)
    return clock, directory, harness, publications


async def invoke(root, **kwargs):
    return await initial.capture_initial_public_market(
        root,
        instrument_id="BTC-USDT-SWAP",
        market_policy=policy(counts=(240, 240, 240, 240)),
        **kwargs,
    )


def empty_registries():
    assert not initial._INITIAL_SCOPES
    assert not runtime._INITIAL_RESULTS
    assert not runtime._SOURCES
    assert not runtime._RESULTS
    assert not coordinator._PUBLICATIONS


@pytest.mark.asyncio
async def test_real_initial_stage_has_no_candidate_publication_or_account_claims(
    public_source, tmp_path, monkeypatch
):
    _, directory, harness, publications = setup(monkeypatch, public_source)
    result = await invoke(tmp_path)
    assert result.code == "initial_public_captured_metadata_account_required"
    assert result.market is not None and result.packet is not None
    assert result.admission == "DENY"
    assert not any(
        getattr(result, flag)
        for flag in (
            "original_source_verified",
            "candidate_created",
            "g12_published",
            "metadata_complete",
            "account_complete",
            "execution_authority",
            "atomic_risk_reserved",
            "order_submitted",
        )
    )
    assert not publications
    plan = decode(directory.content["plan.json"])
    assert plan["schema_version"] == "ctcc.public.initial_runtime_plan.v1"
    assert plan["ws_origin"] == "wss://ws.okx.com:443/ws/v5/public"
    assert plan["stage"] == "initial_public"
    assert not {
        "barrier",
        "publication_completed_at",
        "candidate_sha256",
        "event_key",
        "original_policy_sha256",
        "evidence_sha256",
        "report_sha256",
    }.intersection(plan)
    assert result.report_id == "initial-" + plan["invocation_id"]
    assert result.packet.barrier_completed_at is None
    legacy_plan = {**plan, "ws_origin": "wss://ws.okx.com:8443/ws/v5/public"}
    journal._validate_plan(legacy_plan)
    summary, events = replay(directory)
    assert summary["disposition"] == "captured"
    assert result.journal_sha256 == sha(directory.content["summary.json"])
    assert all(
        event["metadata"]["started"]["utc_ns"] > plan["invocation_started"]["utc_ns"]
        for event in events
        if event["kind"] == "request"
    )
    assert all(
        req.method == "GET"
        and req.url.host == "www.okx.com"
        and not req.url.path.startswith(("/api/v5/account/", "/api/v5/trade/"))
        for req in harness.requests
    )
    assert result.market.candles["5m"] == list(result.packet.candles.frames[-1].candles)
    # The mutable output is a disposable copy, not the sealed packet or a permit.
    result.market.candles.clear()
    _, _, packet = runtime.replay_public_runtime(
        directory, expected_plan_sha256=sha(directory.content["plan.json"])
    )
    assert len(packet.candles.frames) == 4
    harness.assert_closed()
    empty_registries()


@pytest.mark.parametrize(
    "kind", [initial._InitialScope, runtime._CapturedInitialPublic]
)
@pytest.mark.parametrize("transfer", [copy.copy, copy.deepcopy, pickle.dumps])
def test_initial_owners_are_not_transferable(kind, transfer):
    with pytest.raises(ValueError):
        transfer(object.__new__(kind))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "supplied", ["original_market", "run", "clock", "receipt", "scope", "report_id"]
)
async def test_entry_accepts_no_caller_source_or_issuer(
    public_source, tmp_path, monkeypatch, supplied
):
    _, directory, harness, publications = setup(monkeypatch, public_source)
    with pytest.raises(TypeError):
        await invoke(tmp_path, **{supplied: object()})
    assert not directory.content and not harness.requests and not publications
    empty_registries()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "instrument", ["btc-usdt-swap", "BTC-USD-SWAP", "UNKNOWN-USDT-SWAP", None]
)
async def test_unreviewed_identity_is_rejected_before_io(
    public_source, tmp_path, monkeypatch, instrument
):
    _, directory, harness, _ = setup(monkeypatch, public_source)
    with pytest.raises(ValueError, match="initial_public_inputs_invalid"):
        await initial.capture_initial_public_market(
            tmp_path, instrument_id=instrument, market_policy=policy()
        )
    assert not directory.content and not harness.requests
    empty_registries()


@pytest.mark.asyncio
async def test_stage_scope_and_handoff_are_not_interchangeable(
    public_source, tmp_path, monkeypatch
):
    _, _, _, _ = setup(monkeypatch, public_source)
    capture, consume = (
        initial._capture_initial_public,
        initial._consume_initial_public_capture,
    )

    async def checked_capture(scope, selected, root):
        with pytest.raises(ValueError, match="owned_publication_required"):
            await runtime._capture_after_publication(scope, selected, root)
        carrier = await capture(scope, selected, root)
        with pytest.raises(ValueError, match="owned_initial_scope_unavailable"):
            await capture(scope, selected, root)
        return carrier

    def checked_consume(carrier, invocation):
        with pytest.raises(ValueError, match="owned_public_capture_required"):
            runtime._consume_public_capture(carrier, invocation)
        result = consume(carrier, invocation)
        with pytest.raises(ValueError, match="owned_public_capture_unavailable"):
            consume(carrier, invocation)
        return result

    monkeypatch.setattr(initial, "_capture_initial_public", checked_capture)
    monkeypatch.setattr(initial, "_consume_initial_public_capture", checked_consume)
    result = await invoke(tmp_path)
    assert result.packet is not None
    empty_registries()


@pytest.mark.asyncio
async def test_post_publication_types_cannot_enter_initial_stage(monkeypatch, tmp_path):
    monkeypatch.setattr(
        runtime, "native_stamp", lambda: pytest.fail("cross-stage token reached clock")
    )
    with pytest.raises(ValueError, match="owned_initial_scope_required"):
        await runtime._capture_initial_public(
            object.__new__(coordinator._Publication), policy(), tmp_path
        )
    with pytest.raises(ValueError, match="owned_public_capture_required"):
        runtime._consume_initial_public_capture(
            object.__new__(runtime._CapturedPublic), object()
        )
    empty_registries()


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["caller_copy", "owned_policy"])
async def test_policy_is_detached_and_pinned_before_initial_transport(
    public_source, tmp_path, monkeypatch, mutation
):
    _, directory, harness, _ = setup(monkeypatch, public_source)
    selected = policy(counts=(240, 240, 240, 240))
    original = initial._capture_initial_public

    async def changed(scope, owned, root):
        (selected if mutation == "caller_copy" else owned).__dict__[
            "total_timeout_seconds"
        ] = 60
        return await original(scope, owned, root)

    monkeypatch.setattr(initial, "_capture_initial_public", changed)
    result = await initial.capture_initial_public_market(
        tmp_path, instrument_id="BTC-USDT-SWAP", market_policy=selected
    )
    if mutation == "caller_copy":
        assert result.packet.policy.total_timeout_seconds == 30
    else:
        assert result.code == "initial_public_denied" and result.market is None
        assert not directory.content and not harness.requests
    harness.assert_closed()
    empty_registries()


@pytest.mark.asyncio
@pytest.mark.parametrize("point", ["scope", "handoff", "market"])
async def test_expiry_at_each_initial_boundary_never_returns_market(
    public_source, tmp_path, monkeypatch, point
):
    clock, directory, harness, _ = setup(monkeypatch, public_source)
    original = initial._capture_initial_public

    async def changed(scope, selected, root):
        if point == "scope":
            clock.count += 31_000
        carrier = await original(scope, selected, root)
        if point == "handoff":
            clock.count += 31_000
        return carrier

    monkeypatch.setattr(initial, "_capture_initial_public", changed)
    if point == "market":
        bridge = initial.public_market_snapshot

        def expired(*args, **kwargs):
            result = bridge(*args, **kwargs)
            clock.count += 31_000
            return result

        monkeypatch.setattr(initial, "public_market_snapshot", expired)
    result = await invoke(tmp_path)
    assert result.code == "initial_public_denied" and result.market is None
    if point == "scope":
        assert not harness.requests
        assert replay(directory)[0]["disposition"] == "rejected"
    else:
        assert replay(directory)[0]["disposition"] == "captured"
    harness.assert_closed()
    empty_registries()


@pytest.mark.asyncio
@pytest.mark.parametrize("point", ["scope", "handoff"])
async def test_foreign_task_burns_handoff_and_never_returns_market(
    public_source, tmp_path, monkeypatch, point
):
    _, directory, harness, _ = setup(monkeypatch, public_source)
    original = initial._capture_initial_public

    async def changed(scope, selected, root):
        if point == "scope":

            async def foreign():
                with pytest.raises(ValueError, match="owned_initial_scope_unavailable"):
                    initial._take_initial_scope(scope)

            await asyncio.create_task(foreign())
        carrier = await original(scope, selected, root)
        if point == "handoff":

            async def foreign():
                with pytest.raises(
                    ValueError, match="owned_public_capture_unavailable"
                ):
                    runtime._consume_initial_public_capture(carrier, object())

            await asyncio.create_task(foreign())
        return carrier

    monkeypatch.setattr(initial, "_capture_initial_public", changed)
    result = await invoke(tmp_path)
    assert result.code == "initial_public_denied" and result.market is None
    if point == "scope":
        assert not directory.content and not harness.requests
    else:
        assert replay(directory)[0]["disposition"] == "captured"
    harness.assert_closed()
    empty_registries()


@pytest.mark.asyncio
async def test_output_and_replay_dto_cannot_recreate_ownership(
    public_source, tmp_path, monkeypatch
):
    _, directory, _, _ = setup(monkeypatch, public_source)
    result = await invoke(tmp_path)
    assert result.packet is not None
    _, _, packet = runtime.replay_public_runtime(
        directory, expected_plan_sha256=sha(directory.content["plan.json"])
    )
    for value in (
        result,
        asdict(result),
        result.market,
        packet,
        object.__new__(initial._InitialScope),
    ):
        with pytest.raises(ValueError):
            initial._take_initial_scope(value)
        with pytest.raises(ValueError):
            runtime._consume_initial_public_capture(value, object())
    with pytest.raises(ValueError):
        runtime._consume_initial_public_capture(
            object.__new__(runtime._CapturedInitialPublic), object()
        )
    empty_registries()


def rechain(directory, *, plan_edit=None, packet_edit=None):
    plan = decode(directory.content["plan.json"])
    if plan_edit:
        plan_edit(plan)
    directory.content["plan.json"] = canonical(plan)
    pin = sha(directory.content["plan.json"])
    summary = decode(directory.content["summary.json"])
    summary["plan_sha256"] = pin
    directory.content["summary.json"] = canonical(summary)

    def mutate(event, target):
        event["plan_sha256"] = pin
        if packet_edit and event["kind"] == "packet":
            name = f"event-{event['index']:04d}.raw"
            packet = decode(target.content[name], journal.MAX_RAW)
            packet_edit(packet)
            raw = canonical(packet)
            target.content[name] = raw
            event["raw_sha256"], event["raw_bytes"] = sha(raw), len(raw)
            summary = decode(target.content["summary.json"])
            summary["packet_sha256"] = sha(raw)
            target.content["summary.json"] = canonical(summary)

    # Re-sign every event and the terminal chain, including a changed packet pin.
    resign_journal(directory, mutate)
    if packet_edit:
        summary = decode(directory.content["summary.json"])
        last = decode(directory.content[f"event-{summary['event_count'] - 1:04d}.json"])
        summary["packet_sha256"] = last["raw_sha256"]
        directory.content["summary.json"] = canonical(summary)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation",
    [
        "claim_publication",
        "legacy_version",
        "candidate",
        "stage",
        "start_after_request",
        "deadline",
        "packet_barrier",
        "packet_quote",
        "instrument",
    ],
)
async def test_fully_resigned_stage_or_source_tampering_still_fails(
    public_source, tmp_path, monkeypatch, mutation
):
    _, directory, _, _ = setup(monkeypatch, public_source)
    result = await invoke(tmp_path)
    assert result.packet is not None

    def change_plan(plan):
        if mutation == "claim_publication":
            plan["publication_completed_at"] = result.observed_at.isoformat()
        elif mutation == "legacy_version":
            plan["schema_version"] = "ctcc.public.runtime_plan.v1"
        elif mutation == "candidate":
            plan["candidate_sha256"] = "1" * 64
        elif mutation == "stage":
            plan["stage"] = "post_publication"
        elif mutation == "start_after_request":
            plan["invocation_started"] = decode(directory.content["summary.json"])[
                "completed"
            ]
        elif mutation == "deadline":
            plan["expires_at"] = utc_from_ns(
                plan["invocation_started"]["utc_ns"]
            ).isoformat()
        elif mutation == "instrument":
            plan["instrument_id"] = "ETH-USDT-SWAP"

    def change_packet(packet):
        if mutation == "packet_barrier":
            packet["barrier_completed_at"] = result.observed_at.isoformat()
        else:
            packet["quote"]["quote"]["bid"] = "0.0001"

    rechain(
        directory,
        plan_edit=change_plan,
        packet_edit=change_packet if mutation.startswith("packet_") else None,
    )
    with pytest.raises(ValueError):
        replay(directory)
    empty_registries()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", ["malformed", "received_clock", "late_readback", "root_used"]
)
async def test_failed_initial_capture_retains_observations_without_issuing_market(
    public_source, tmp_path, monkeypatch, failure
):
    _, directory, harness, _ = setup(monkeypatch, public_source)
    if failure == "malformed":
        original = harness.public
        harness.public = lambda request: (
            [] if request.url.path.endswith("/ticker") else original(request)
        )
    elif failure == "received_clock":
        original = runtime._event

        def event(owner, kind, meta, raw=None):
            result = original(owner, kind, meta, raw)
            if kind == "headers":
                monkeypatch.setattr(
                    runtime,
                    "native_stamp",
                    lambda: (_ for _ in ()).throw(PublicReceiptError("clock_failed")),
                )
            return result

        monkeypatch.setattr(runtime, "_event", event)
    elif failure == "late_readback":
        monkeypatch.setattr(
            runtime,
            "_sealed_readback",
            lambda _: (_ for _ in ()).throw(PublicReceiptError("readback_failed")),
        )
    else:
        directory.content["existing.raw"] = b"preserve"
    result = await invoke(tmp_path)
    assert result.code == "initial_public_denied" and result.market is None
    assert not result.execution_authority
    if failure == "root_used":
        assert directory.content == {"existing.raw": b"preserve"}
        assert not harness.requests
    else:
        summary, events = replay(directory)
        assert any(event["kind"] == "chunk" for event in events)
        assert summary["disposition"] == (
            "captured" if failure == "late_readback" else "rejected"
        )
        if failure == "received_clock":
            assert any(
                event["kind"] == "chunk"
                and event["metadata"]["received"] is None
                and event["raw_bytes"] > 0
                for event in events
            )
    harness.assert_closed()
    empty_registries()


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["prestart", "request", "swallowed", "handoff"])
async def test_cancellation_never_issues_initial_market_and_closes_resources(
    public_source, tmp_path, monkeypatch, phase
):
    _, directory, harness, publications = setup(monkeypatch, public_source)
    entered = asyncio.Event()
    if phase in {"request", "swallowed"}:

        class Hold:
            async def wait(self):
                entered.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    if phase != "swallowed":
                        raise

        harness.public_hold = Hold()
    if phase == "handoff":
        original = initial._capture_initial_public

        async def capture(*args):
            carrier = await original(*args)
            asyncio.current_task().cancel()
            return carrier

        monkeypatch.setattr(initial, "_capture_initial_public", capture)
    task = asyncio.create_task(invoke(tmp_path))
    if phase == "prestart":
        task.cancel()
    elif phase != "handoff":
        async with asyncio.timeout(10):
            await entered.wait()
        task.cancel()
    with pytest.raises(asyncio.CancelledError):
        async with asyncio.timeout(10):
            await task
    assert not publications
    if phase == "prestart":
        assert not directory.content and not harness.requests
    else:
        assert replay(directory)[0]["disposition"] == (
            "captured" if phase == "handoff" else "rejected"
        )
    harness.assert_closed()
    empty_registries()
