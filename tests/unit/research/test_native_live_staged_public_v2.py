"""Synthetic wire and coordinator handoffs; no account or venue acceptance."""

import asyncio
import copy
import json
import pickle
import sys
from dataclasses import replace
from time import perf_counter, perf_counter_ns
from types import FunctionType, ModuleType

import httpx
import pytest

from app.domain.source_primitives import ClockStamp, canonical, decode, sha, utc_from_ns
from app.okx_live import qualification_math_v1 as math
from app.okx_live.entry_input_dag_v2 import exact_input_dag_digest_v2
from app.public_market_source import public_runtime_journal as journal
from app.trade_qualification import global_public_staged_v2 as lower
from app.trade_qualification import native_live_public_source_v1 as subject
from tests.unit.research.test_native_live_public_source_v1 import (  # noqa: F401
    live_source,
    market,
)
from tests.unit.research.test_owned_public_runtime_p1 import resign_journal


@pytest.fixture
def staged_source(live_source, monkeypatch):  # noqa: F811 -- imported pytest fixture
    clock, parent, raw, harness, key, request = live_source
    monkeypatch.setattr(lower, "native_stamp", clock.stamp)
    module = ModuleType(subject._COORDINATOR)
    module.subject, module.clock = subject, clock
    module.ready = {}
    module._consume_live_staged_account_ready_v2 = FunctionType(
        _ready_callback.__code__, vars(module), "_consume_live_staged_account_ready_v2"
    )
    monkeypatch.setitem(sys.modules, subject._COORDINATOR, module)

    def issue():
        return request("post_g12", public_policy_sha256=subject.STAGED_POLICY_SHA256)

    def ready(stage, params, **changes):
        result = subject.LiveStagedAccountReadyV2(
            params.invocation,
            object(),
            ClockStamp.model_validate(clock.stamp()),
            "c" * 64,
        )
        module.ready[stage] = replace(result, **changes)
        return result

    yield clock, parent, raw, harness, key, issue, ready
    assert not subject._OHLC_STAGES and not lower._STAGES


def _ready_callback(stage, *, key):
    assert key is subject._ACCOUNT_READY_KEY_V2
    return ready.pop(stage)  # noqa: F821 -- explicit synthetic module globals


@pytest.mark.asyncio
async def test_staged_exact_wire_ohlc_then_account_then_mutable_and_full_replay(
    staged_source,
):
    clock, parent, raw, harness, key, issue, ready = staged_source
    request, params = issue()
    async with subject.begin_owned_live_post_g12_public_v2(request, key=key) as stage:
        preparation = lower._STAGES[subject._OHLC_STAGES[stage]["runtime_stage"]]
        prepared = preparation["prepared_candles"]
        data = subject.replay_owned_live_public_ohlc_stage_v2(stage, key=key)
        assert prepared == data.candles and prepared is not data.candles
        assert type(data) is subject.LivePublicOHLCStageDataV2
        assert data.invocation is params.invocation
        assert data.publication_barrier == params.publication_barrier
        assert data.candidate_sha256 == params.candidate_sha256
        assert data.execution_authority is data.public_complete is False
        assert not hasattr(data, "quote") and not hasattr(data, "market")
        assert {req.url.path for req in harness.requests} == {
            "/api/v5/public/time",
            "/api/v5/market/candles",
        }
        assert not harness.connections and "summary.json" not in raw.content
        assert subject._FILE not in parent.content
        assert [frame.timeframe for frame in data.candles.frames] == [
            "4H",
            "1H",
            "15m",
            "5m",
        ]
        account = ready(stage, params)
        assert account.completed.monotonic_ns > data.completed.monotonic_ns
        owner = await subject.complete_owned_live_post_g12_public_v2(stage, key=key)
        with pytest.raises(subject.LivePublicSourceError):
            await subject.complete_owned_live_post_g12_public_v2(stage, key=key)
    value = subject._replay_owned_live_public_v1(owner, key=key)
    assert value.packet.environment == journal.GLOBAL_ENVIRONMENT
    assert value.packet.report_id != value.report_id == params.report_id
    assert value.execution_authority is value.account_complete is False
    assert value.packet.candles == data.candles
    assert decode(value.receipt_json)["policy_sha256"] == subject.STAGED_POLICY_SHA256
    legacy = subject._project(
        value.packet, report_id=value.report_id, at=value.evaluated_at
    )
    staged_view = (value.market, value.quote, value.reference)
    assert legacy == staged_view
    assert legacy[0].candles["4H"][0] is not value.market.candles["4H"][0]
    assert exact_input_dag_digest_v2(
        (value.packet, legacy), invocation=params.invocation
    ) == exact_input_dag_digest_v2(
        (value.packet, staged_view), invocation=params.invocation
    )
    market_view, quote, reference = math._source(value)
    assert (market_view, quote, reference) == (
        value.market,
        value.quote,
        value.reference,
    )
    assert market_view.candles["4H"][0] is not value.market.candles["4H"][0]
    changed_market = value.market.model_copy(deep=True)
    changed_market.candles["4H"][0].confirmed = False
    with pytest.raises(
        math.LiveQualificationMathError, match="live_qualification_math_invalid"
    ):
        math._source(replace(value, market=changed_market))
    assert (
        decode(raw.content["plan.json"])["schema_version"] == journal.GLOBAL_STAGED_PLAN
    )
    summary, events, packet = subject.transport.replay_public_runtime(
        raw, expected_plan_sha256=value.plan_sha256
    )
    assert (
        summary["disposition"] == "captured"
        and packet.packet_json == value.packet.packet_json
    )
    assert sum(event["kind"] == "ohlc_stage_complete" for event in events) == 1
    assert sum(event["kind"] == "account_stage_ready" for event in events) == 1
    mutable = [
        event
        for event in events
        if event["kind"] == "ws_connect"
        or (
            event["kind"] == "request"
            and event["metadata"]["role"] in {"quote", "market_aux", "time_post"}
        )
    ]
    assert mutable
    for event in mutable:
        assert all(
            event["metadata"]["started"][field] > account.completed.model_dump()[field]
            for field in ("utc_ns", "monotonic_ns")
        )
    before = len(harness.requests), len(harness.connections)
    subject._replay_owned_live_public_v1(owner, key=key)
    assert before == (len(harness.requests), len(harness.connections))
    assert clock.last is not None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operation",
    ["copy", "deepcopy", "pickle", "other_task", "unready", "partial_mutation"],
)
async def test_staged_partial_never_transfers_or_mints_complete_source(
    staged_source, operation
):
    _clock, _parent, raw, harness, key, issue, _ready = staged_source
    request, _params = issue()
    with pytest.raises((ValueError, KeyError)):
        async with subject.begin_owned_live_post_g12_public_v2(
            request, key=key
        ) as stage:
            if operation == "copy":
                copy.copy(stage)
            elif operation == "deepcopy":
                copy.deepcopy(stage)
            elif operation == "pickle":
                pickle.dumps(stage)
            elif operation == "other_task":
                await asyncio.create_task(_replay(stage, key))
            elif operation == "unready":
                await subject.complete_owned_live_post_g12_public_v2(stage, key=key)
            else:
                name = next(name for name in raw.content if name.endswith(".raw"))
                raw.content[name] += b" "
                subject.replay_owned_live_public_ohlc_stage_v2(stage, key=key)
    assert not harness.connections
    assert decode(raw.content["summary.json"])["disposition"] == "rejected"
    assert not subject._OWNERS


async def _replay(stage, key):
    return subject.replay_owned_live_public_ohlc_stage_v2(stage, key=key)


@pytest.mark.asyncio
async def test_staged_cancel_burns_prefix_and_request_without_mutable_attempt(
    staged_source,
):
    _clock, _parent, raw, harness, key, issue, _ready = staged_source
    request, _params = issue()
    stage = None
    with pytest.raises(asyncio.CancelledError):
        async with subject.begin_owned_live_post_g12_public_v2(
            request, key=key
        ) as stage:
            raise asyncio.CancelledError
    assert decode(raw.content["summary.json"])["disposition"] == "rejected"
    assert not harness.connections
    with pytest.raises(subject.LivePublicSourceError):
        subject.replay_owned_live_public_ohlc_stage_v2(stage, key=key)
    with pytest.raises(KeyError):
        async with subject.begin_owned_live_post_g12_public_v2(request, key=key):
            pytest.fail("spent request reused")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changed", ["invocation", "source_owner", "completed", "revision"]
)
async def test_staged_account_ready_must_bind_original_invocation_and_new_time(
    staged_source, changed
):
    _clock, _parent, raw, harness, key, issue, ready = staged_source
    request, params = issue()
    with pytest.raises(subject.LivePublicSourceError):
        async with subject.begin_owned_live_post_g12_public_v2(
            request, key=key
        ) as stage:
            data = subject.replay_owned_live_public_ohlc_stage_v2(stage, key=key)
            changes = (
                {"invocation": object()}
                if changed == "invocation"
                else (
                    {"source_owner": None}
                    if changed == "source_owner"
                    else (
                        {"completed": data.completed}
                        if changed == "completed"
                        else {"source_revision_sha256": "invalid"}
                    )
                )
            )
            ready(stage, params, **changes)
            await subject.complete_owned_live_post_g12_public_v2(stage, key=key)
    assert not harness.connections and not subject._OWNERS
    assert decode(raw.content["summary.json"])["disposition"] == "rejected"


def test_staged_plan_cannot_replace_or_weaken_original_initial_schema():
    assert (
        subject.STAGED_POLICY_SHA256
        != subject.POLICY_SHA256
        != subject.CLOSE_POLICY_SHA256
    )
    with pytest.raises(ValueError):
        journal._validate_global_staged_plan(
            {"schema_version": journal.GLOBAL_INITIAL_PLAN}
        )
    assert (
        sha(canonical(decode(subject.STAGED_POLICY_BYTES)))
        == subject.STAGED_POLICY_SHA256
    )


@pytest.mark.asyncio
async def test_staged_bar_rollover_denies_original_ohlc_without_refetch(staged_source):
    clock, _parent, raw, harness, key, issue, ready = staged_source
    clock.base = clock.base // 300_000_000_000 * 300_000_000_000 + 298_000_000_000
    clock.last = utc_from_ns(clock.base)
    request, params = issue()
    with pytest.raises(ValueError, match="global_public_component_stale"):
        async with subject.begin_owned_live_post_g12_public_v2(
            request, key=key
        ) as stage:
            data = subject.replay_owned_live_public_ohlc_stage_v2(stage, key=key)
            old_requests = sum(
                req.url.path.endswith("/candles") for req in harness.requests
            )
            clock.count += 2500
            ready(stage, params)
            await subject.complete_owned_live_post_g12_public_v2(stage, key=key)
    assert not subject._OWNERS
    assert (
        sum(req.url.path.endswith("/candles") for req in harness.requests)
        == old_requests
    )
    assert decode(raw.content["summary.json"])["disposition"] == "rejected"
    assert data.candidate_sha256 == params.candidate_sha256


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changed",
    ["ohlc_hash", "account_prefix", "account_time", "mutable_time", "initial_schema"],
)
async def test_full_staged_journal_rejects_rehashed_changed_phase_evidence(
    staged_source, changed
):
    _clock, _parent, raw, _harness, key, issue, ready = staged_source
    request, params = issue()
    async with subject.begin_owned_live_post_g12_public_v2(request, key=key) as stage:
        data = subject.replay_owned_live_public_ohlc_stage_v2(stage, key=key)
        ready(stage, params)
        owner = await subject.complete_owned_live_post_g12_public_v2(stage, key=key)
    value = subject._replay_owned_live_public_v1(owner, key=key)
    if changed == "initial_schema":
        plan = decode(raw.content["plan.json"])
        plan["schema_version"] = journal.GLOBAL_INITIAL_PLAN
        raw.content["plan.json"] = canonical(plan)
    else:

        def mutate(event, _directory):
            if event["kind"] == "ohlc_stage_complete" and changed == "ohlc_hash":
                event["metadata"]["candles_sha256"] = "0" * 64
            if event["kind"] == "account_stage_ready":
                if changed == "account_prefix":
                    event["metadata"]["ohlc_prefix_sha256"] = "0" * 64
                elif changed == "account_time":
                    event["metadata"]["completed"] = data.completed.model_dump()
            if (
                event["kind"] == "request"
                and event["metadata"]["role"] == "quote"
                and changed == "mutable_time"
            ):
                event["metadata"]["started"] = data.completed.model_dump()

        resign_journal(raw, mutate)
    with pytest.raises(ValueError):
        subject.transport.replay_public_runtime(
            raw, expected_plan_sha256=sha(raw.content["plan.json"])
        )
    with pytest.raises(subject.LivePublicSourceError):
        subject._replay_owned_live_public_v1(owner, key=key)
    assert value.execution_authority is False


@pytest.mark.asyncio
async def test_staged_native_paced_complete_source_and_replay_fit_unchanged_freshness(
    staged_source, monkeypatch, record_property
):
    clock, _parent, _raw, _harness, key, issue, ready = staged_source
    first, last = perf_counter_ns(), 0

    def paced_stamp():
        nonlocal last
        clock.count += 1
        last = max(
            last + 1_000_000,
            ((perf_counter_ns() - first + 999_999) // 1_000_000) * 1_000_000,
        )
        clock.last = utc_from_ns(clock.base + last)
        return {"utc_ns": clock.base + last, "monotonic_ns": last}

    monkeypatch.setattr(clock, "stamp", paced_stamp)
    for module in (subject, lower, subject.production, subject.transport):
        monkeypatch.setattr(module, "native_stamp", paced_stamp)
    request, params = issue()
    begin = perf_counter()
    async with subject.begin_owned_live_post_g12_public_v2(request, key=key) as stage:
        data = subject.replay_owned_live_public_ohlc_stage_v2(stage, key=key)
        ready(stage, params)
        owner = await subject.complete_owned_live_post_g12_public_v2(stage, key=key)
    capture_seconds = perf_counter() - begin
    value = subject._replay_owned_live_public_v1(owner, key=key)
    final = paced_stamp()
    age = (
        final["utc_ns"]
        - int(value.quote.ticker.source_generated_at.timestamp() * 1_000_000_000)
    ) / 1e9
    assert age < 5
    assert data.public_complete is value.execution_authority is False
    record_property(
        "synthetic_native_paced_staged_public",
        json.dumps(
            {
                "capture_seconds": capture_seconds,
                "capture_and_replay_seconds": perf_counter() - begin,
                "ticker_age_seconds": age,
                "source_admission_storage_clock_tls_synthetic": True,
                "account_and_full_entry_worker_exercised": False,
                "execution_authority": False,
            }
        ),
    )


@pytest.mark.asyncio
async def test_staged_native_directory_releases_publisher_before_independent_readback(
    staged_source, monkeypatch
):
    _clock, _parent, _raw, _harness, key, issue, ready = staged_source
    storage = subject.transport._receipt_storage
    monkeypatch.setattr(journal, "_root_context", storage._root_context)
    monkeypatch.setattr(
        subject.transport, "_native_recheck_root_identity", storage._root_identity
    )
    request, params = issue()
    params.root.mkdir()
    async with subject.begin_owned_live_post_g12_public_v2(request, key=key) as stage:
        partial = subject.replay_owned_live_public_ohlc_stage_v2(stage, key=key)
        assert partial.raw_files
        assert not (params.root / "raw" / "summary.json").exists()
        ready(stage, params)
        owner = await subject.complete_owned_live_post_g12_public_v2(stage, key=key)
    value = subject._replay_owned_live_public_v1(owner, key=key)
    assert value.packet.candles == partial.candles
    assert value.execution_authority is False
    for path in (params.root, params.root / "raw"):
        with storage._root_context(path) as directory:
            assert directory.names()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["timeout", "cancel", "foreign_callback_globals"])
async def test_staged_mutable_failure_burns_stage_preserves_raw_and_joins_clients(
    staged_source, failure, monkeypatch
):
    _clock, _parent, raw, harness, key, issue, ready = staged_source
    request, params = issue()
    error = (
        asyncio.CancelledError if failure == "cancel" else (ValueError, httpx.HTTPError)
    )
    with pytest.raises(error):
        async with subject.begin_owned_live_post_g12_public_v2(
            request, key=key
        ) as stage:
            ready(stage, params)
            if failure == "foreign_callback_globals":
                module = sys.modules[subject._COORDINATOR]
                callback = module._consume_live_staged_account_ready_v2
                replacement = FunctionType(callback.__code__, dict(vars(module)))
                monkeypatch.setattr(
                    module, "_consume_live_staged_account_ready_v2", replacement
                )
            elif failure == "timeout":
                harness.public_error = httpx.ReadTimeout("synthetic staged timeout")
            else:
                harness.public_hold = asyncio.Event()
                asyncio.get_running_loop().call_later(
                    0.01, asyncio.current_task().cancel
                )
            await subject.complete_owned_live_post_g12_public_v2(stage, key=key)
    assert not subject._OWNERS
    assert decode(raw.content["summary.json"])["disposition"] == "rejected"
    assert any(name.endswith(".raw") for name in raw.content)
    harness.assert_closed()


@pytest.mark.asyncio
async def test_staged_caller_mutation_never_replaces_retained_original_candle_bytes(
    staged_source,
):
    _clock, _parent, _raw, _harness, key, issue, ready = staged_source
    request, params = issue()
    async with subject.begin_owned_live_post_g12_public_v2(request, key=key) as stage:
        one = subject.replay_owned_live_public_ohlc_stage_v2(stage, key=key)
        original = one.candles.frames[0].confirmed[0].close
        object.__setattr__(one.candles.frames[0].confirmed[0], "close", original + 1)
        two = subject.replay_owned_live_public_ohlc_stage_v2(stage, key=key)
        assert two.candles.frames[0].confirmed[0].close == original
        ready(stage, params)
        owner = await subject.complete_owned_live_post_g12_public_v2(stage, key=key)
    complete = subject._replay_owned_live_public_v1(owner, key=key)
    assert complete.packet.candles == two.candles


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ("private_model", "both_views"))
async def test_staged_prepared_candles_are_bound_to_original_journal(
    staged_source, change
):
    _clock, parent, raw, harness, key, issue, ready = staged_source
    request, params = issue()
    refusal = (
        "global_public_stage_prepared_candles_changed"
        if change == "private_model"
        else "global_public_stage_prefix_changed"
    )
    with pytest.raises(lower.StagedGlobalPublicError, match=refusal):
        async with subject.begin_owned_live_post_g12_public_v2(
            request, key=key
        ) as stage:
            state = lower._STAGES[subject._OHLC_STAGES[stage]["runtime_stage"]]
            prepared = state["prepared_candles"]
            candle = prepared.frames[0].confirmed[0]
            object.__setattr__(candle, "close", candle.close + 1)
            if change == "both_views":
                state["candles"] = canonical(
                    prepared.model_dump(mode="json", round_trip=True)
                )
            ready(stage, params)
            await subject.complete_owned_live_post_g12_public_v2(stage, key=key)
    assert not subject._OWNERS and subject._FILE not in parent.content
    assert decode(raw.content["summary.json"])["disposition"] == "rejected"
    harness.assert_closed()


@pytest.mark.asyncio
async def test_staged_policy_does_not_enter_legacy_capture(staged_source):
    _clock, _parent, _raw, harness, key, issue, _ready = staged_source
    request, _params = issue()
    with pytest.raises(subject.LivePublicSourceError):
        await subject.capture_owned_live_public_v1(request, key=key)
    assert not harness.requests and not harness.connections


@pytest.mark.asyncio
async def test_staged_join_only_journal_never_bypasses_complete_candle_semantics(
    staged_source,
):
    _clock, _parent, raw, _harness, key, issue, ready = staged_source
    request, params = issue()
    async with subject.begin_owned_live_post_g12_public_v2(request, key=key) as stage:
        ready(stage, params)
        await subject.complete_owned_live_post_g12_public_v2(stage, key=key)
    new_prefix, packet_pin = None, None

    def mutate(event, directory):
        nonlocal new_prefix, packet_pin
        name = f"event-{event['index']:04d}.raw"
        if event["kind"] in {"ohlc_stage_complete", "packet"}:
            document = decode(directory.content[name])
            collected = (
                document
                if event["kind"] == "ohlc_stage_complete"
                else document["candles"]
            )
            # Raw HTTP page bytes remain unchanged and fully joined. This dirty
            # derived candle must still fail mandatory typed component replay.
            collected["frames"][0]["confirmed"][0]["close"] = "NaN"
            encoded = canonical(document)
            directory.content[name] = encoded
            event.update(raw_bytes=len(encoded), raw_sha256=sha(encoded))
            if event["kind"] == "ohlc_stage_complete":
                event["metadata"]["candles_sha256"] = sha(encoded)
                new_prefix = sha(canonical(event))
            else:
                packet_pin = sha(encoded)
                event["metadata"]["bundle_sha256"] = packet_pin
        elif event["kind"] == "account_stage_ready":
            event["metadata"]["ohlc_prefix_sha256"] = new_prefix

    resign_journal(raw, mutate)
    summary = decode(raw.content["summary.json"])
    summary["packet_sha256"] = packet_pin
    raw.content["summary.json"] = canonical(summary)
    # Integrity/raw joining alone is explicitly DATA, never qualification.
    journal.replay_runtime_attempt(
        raw, expected_plan_sha256=sha(raw.content["plan.json"])
    )
    with pytest.raises(ValueError):
        subject.transport.replay_public_runtime(
            raw, expected_plan_sha256=sha(raw.content["plan.json"])
        )
