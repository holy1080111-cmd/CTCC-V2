"""Hermetic outbox protocol tests, not native filesystem or Notion attestation.

MemoryDirectory preserves immutable bytes under one shared root lock. It proves
protocol behavior only. Native POSIX coverage is explicitly platform-specific;
Windows ancestor-pin denial must not be disguised as successful native IO.
"""

import asyncio
import ctypes
import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier, RLock

import pytest
from pydantic import ValidationError, create_model

from app.trade_evidence import outbox as module

NOW = datetime(2026, 9, 12, 12, tzinfo=UTC)
REPORT = "synthetic-outbox-report"
INSTRUMENT = "BTC-USDT-SWAP"
SOURCE_SHA = "a" * 64
EVIDENCE_SHA = "b" * 64
SUBMISSION_SHA = "c" * 64
REMOTE_SHA = "d" * 64
PAGE_ID = "e" * 32


class Clock:
    def __init__(self, now=NOW):
        self.now = now
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return self.now

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)


class MemoryDirectory:
    def __init__(self, backend, relative=()):
        self.backend = backend
        self.relative = relative
        self.path = backend.root.joinpath(*relative)

    def names(self):
        self.backend.operations.append(("names", self.relative))
        length = len(self.relative)
        return sorted(
            {
                path[-1]
                for path in (*self.backend.directories, *self.backend.files)
                if len(path) == length + 1 and path[:-1] == self.relative
            }
        )

    def mkdir(self, name):
        path = (*self.relative, name)
        if path in self.backend.directories or path in self.backend.files:
            raise FileExistsError("synthetic existing directory")
        self.backend.directories.add(path)
        self.backend.operations.append(("mkdir", path))

    @contextmanager
    def child(self, name):
        path = (*self.relative, name)
        if path not in self.backend.directories:
            raise FileNotFoundError("synthetic missing directory")
        yield MemoryDirectory(self.backend, path)

    def read(self, name, maximum):
        path = (*self.relative, name)
        self.backend.operations.append(("read", path))
        if path in self.backend.fail_reads:
            raise OSError("synthetic readback failed")
        if path not in self.backend.files:
            raise FileNotFoundError("synthetic missing file")
        data = self.backend.files[path]
        if len(data) > maximum:
            raise ValueError("synthetic read bound exceeded")
        return data

    def publish(self, name, payload):
        path = (*self.relative, name)
        if path in self.backend.files or path in self.backend.directories:
            raise FileExistsError("synthetic no-clobber publication")
        if path in self.backend.fail_publish_before:
            raise OSError("synthetic failure before durable publish")
        assert type(payload) is bytes
        self.backend.files[path] = payload
        self.backend.operations.append(("publish", path))
        if path in self.backend.fail_read_after_publish:
            self.backend.fail_reads.add(path)
        if path in self.backend.fail_publish_after:
            raise OSError("synthetic failure after durable publish")


class MemoryBackend:
    def __init__(self):
        self.root = Path.cwd() / "synthetic-memory-only-outbox"
        self.lock = RLock()
        self.files = {}
        self.directories = {()}
        self.operations = []
        self.fail_publish_before = set()
        self.fail_publish_after = set()
        self.fail_read_after_publish = set()
        self.fail_reads = set()
        self.active_contexts = 0

    @contextmanager
    def context(self, root):
        assert root == self.root
        with self.lock:
            self.active_contexts += 1
            try:
                yield MemoryDirectory(self)
            finally:
                self.active_contexts -= 1


@pytest.fixture
def memory(monkeypatch):
    backend = MemoryBackend()
    monkeypatch.setattr(module, "_root_context", backend.context)
    return backend


@pytest.fixture
def clock():
    return Clock()


def payload(**updates):
    values = {
        "report_id": REPORT,
        "instrument_id": INSTRUMENT,
        "strategy": "trend_pullback",
        "direction": "long",
        "order_reference": "synthetic-order-reference",
        "submitted_at": NOW - timedelta(seconds=1),
        "evidence_completed_at": NOW - timedelta(seconds=2),
        "source_sha256": SOURCE_SHA,
        "evidence_report_sha256": EVIDENCE_SHA,
        "submission_receipt_sha256": SUBMISSION_SHA,
    }
    return module.OutboxPayload(**(values | updates))


def policy(**updates):
    return module.OutboxPolicy(**updates)


def enqueue(memory, clock, **updates):
    return module.enqueue(memory.root, payload(), clock=clock, **updates)


def claim(memory, clock, worker="worker-a"):
    return module.claim_job(memory.root, REPORT, worker_id=worker, clock=clock)


def outcome(token, status="delivered", **updates):
    values = {
        "report_id": token.report_id,
        "envelope_sha256": token.envelope_sha256,
        "fence_token": token.fence_token,
        "status": status,
        "remote_page_id": PAGE_ID if status == "delivered" else None,
        "receipt_sha256": None if status == "uncertain" else REMOTE_SHA,
    }
    return module.DeliveryOutcome(**(values | updates))


def dispatch_token(memory, clock):
    return module.begin_dispatch(memory.root, claim(memory, clock), clock=clock)


def event_path(revision, report_id=REPORT):
    return (f"{report_id}.state", f"{revision:08d}.json")


def decode(data):
    return json.loads(data)


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


@pytest.mark.parametrize("direction", ["long", "short"])
def test_payload_strict_json_roundtrip_and_false_authority(direction):
    value = payload(direction=direction)
    restored = module.OutboxPayload.model_validate_json(
        value.model_dump_json(), strict=True
    )
    assert restored == value
    assert value.execution_authority is False
    assert value.source_authenticity_verified is False
    with pytest.raises(ValidationError):
        value.order_reference = "changed"


@pytest.mark.parametrize(
    "field,value",
    [
        ("report_id", "../escape"),
        ("report_id", "CON"),
        ("report_id", ""),
        ("report_id", 1),
        ("instrument_id", True),
        ("direction", "neutral"),
        ("order_reference", None),
        ("submitted_at", NOW.replace(tzinfo=None)),
        ("submitted_at", NOW.isoformat()),
        ("submitted_at", True),
        ("evidence_completed_at", NOW.replace(tzinfo=None)),
        ("evidence_completed_at", NOW + timedelta(seconds=1)),
        ("source_sha256", "A" * 64),
        ("source_sha256", " " + SOURCE_SHA),
        ("evidence_report_sha256", "g" * 64),
        ("submission_receipt_sha256", "short"),
        ("execution_authority", True),
        ("execution_authority", 0),
        ("source_authenticity_verified", True),
        ("source_authenticity_verified", "false"),
    ],
)
def test_payload_rejects_malformed_or_authorizing_fields(field, value):
    with pytest.raises(ValueError):
        payload(**{field: value})


@pytest.mark.parametrize(
    "field",
    [
        "max_attempts",
        "lease_seconds",
        "adapter_timeout_seconds",
        "base_backoff_seconds",
        "max_backoff_seconds",
        "terminal_retention_seconds",
    ],
)
@pytest.mark.parametrize("value", [True, "3", 1.5, -1])
def test_policy_requires_exact_bounded_integers(field, value):
    with pytest.raises(ValueError):
        policy(**{field: value})


def test_enqueue_writes_immutable_canonical_job_and_initial_event(memory, clock):
    view = enqueue(memory, clock)
    assert view.status == "queued" and view.attempts == 0
    assert view.envelope.payload == payload()
    assert view.envelope.policy == policy()
    assert view.envelope.enqueued_at == NOW
    assert len(view.events) == 1 and view.head == view.events[-1]
    assert set(memory.files) == {(f"{REPORT}.json",), event_path(1)}
    for data in memory.files.values():
        assert data == encoded(decode(data))
    assert module.read_job(memory.root, REPORT, clock=clock) == view


def test_identical_enqueue_is_idempotent_without_clock_or_policy_rewrite(memory, clock):
    first = enqueue(memory, clock)
    previous = dict(memory.files)
    clock.advance(1)
    second = enqueue(memory, clock)
    assert second.envelope == first.envelope
    assert second.events == first.events
    assert second.verified_at == clock.now > first.verified_at
    assert memory.files == previous


def test_delivery_appends_events_without_replacing_job_or_history(memory, clock):
    queued = enqueue(memory, clock)
    queued_bytes = dict(memory.files)
    token = dispatch_token(memory, clock)
    before = dict(memory.files)
    delivered = module.finish_dispatch(memory.root, token, outcome(token), clock=clock)
    assert delivered.status == "delivered"
    assert tuple(item.status for item in delivered.events) == (
        "queued",
        "claimed",
        "dispatching",
        "delivered",
    )
    assert delivered.envelope == queued.envelope
    assert all(memory.files[path] == data for path, data in queued_bytes.items())
    assert all(memory.files[path] == data for path, data in before.items())
    assert len(memory.files) == 5
    assert module.read_job(memory.root, REPORT, clock=clock) == delivered


def test_models_remain_frozen_after_enqueue(memory, clock):
    view = enqueue(memory, clock)
    with pytest.raises(ValidationError):
        view.envelope.payload.direction = "short"
    with pytest.raises(ValidationError):
        view.head.status = "delivered"
    with pytest.raises(ValidationError):
        view.envelope.policy.max_attempts = 100


@pytest.mark.asyncio
async def test_adapter_runs_only_after_dispatching_is_durable_and_read_back(
    memory, clock
):
    enqueue(memory, clock)
    seen = []

    async def adapter(value, token):
        dispatch_path = event_path(3)
        publish_index = memory.operations.index(("publish", dispatch_path))
        readback_index = memory.operations.index(("read", dispatch_path), publish_index)
        seen.append(
            (value, token, publish_index, readback_index, memory.active_contexts)
        )
        assert module.read_job(memory.root, REPORT, clock=clock).status == "dispatching"
        return outcome(token)

    view = await module.dispatch_once(
        memory.root, REPORT, worker_id="worker-a", adapter=adapter, clock=clock
    )
    assert view.status == "delivered"
    assert len(seen) == 1
    assert seen[0][0] == payload()
    assert seen[0][2] < seen[0][3]
    assert seen[0][4] == 0  # No root lock held across remote work.


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["timeout", "exception", "invalid_result"])
async def test_unknown_remote_result_never_becomes_an_automatic_retry(
    memory, clock, failure
):
    enqueue(memory, clock, policy=policy(adapter_timeout_seconds=1))
    calls = []

    async def adapter(value, token):
        calls.append((value, token))
        if failure == "timeout":
            await asyncio.Future()
        if failure == "exception":
            raise RuntimeError("synthetic unknown remote outcome")
        return {"status": "not_created_retryable"}  # Unvalidated result is not proof.

    view = await module.dispatch_once(
        memory.root, REPORT, worker_id="worker-a", adapter=adapter, clock=clock
    )
    assert view.status == "uncertain"
    clock.advance(7200)
    assert module.recover_job(memory.root, REPORT, clock=clock).status == "uncertain"
    assert len(calls) == 1
    decision = module.retention_decision(view, now=clock())
    assert decision.must_retain is True
    assert decision.eligible_after is None


@pytest.mark.asyncio
async def test_caller_cancel_is_propagated_after_unknown_outcome_is_retained(
    memory, clock
):
    enqueue(memory, clock)
    entered = asyncio.Event()

    async def adapter(value, token):
        entered.set()
        await asyncio.Future()

    task = asyncio.create_task(
        module.dispatch_once(
            memory.root, REPORT, worker_id="worker-a", adapter=adapter, clock=clock
        )
    )
    await asyncio.wait_for(entered.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.cancelled()
    assert module.read_job(memory.root, REPORT, clock=clock).status == "uncertain"
    clock.advance(7200)
    assert module.recover_job(memory.root, REPORT, clock=clock).status == "uncertain"


@pytest.mark.parametrize("change", ["payload", "policy"])
def test_conflicting_enqueue_never_clobbers_existing_job(memory, clock, change):
    enqueue(memory, clock)
    before = dict(memory.files)
    values = {"clock": clock}
    candidate = payload()
    if change == "payload":
        candidate = payload(order_reference="different-synthetic-order")
    else:
        values["policy"] = policy(max_attempts=2)
    with pytest.raises(module.OutboxError):
        module.enqueue(memory.root, candidate, **values)
    assert memory.files == before


@pytest.mark.parametrize("kind", ["hidden", "subclass", "authority", "policy_subclass"])
def test_dirty_inputs_are_rejected_before_any_publication(memory, clock, kind):
    value, selected_policy = payload(), policy()
    if kind == "hidden":
        value = value.model_copy(update={"hidden": True})
    elif kind == "subclass":
        dirty = create_model(
            "DirtyPayload", __base__=module.OutboxPayload, hidden=(bool, False)
        )
        value = dirty(**value.model_dump())
    elif kind == "authority":
        value = value.model_copy(update={"execution_authority": True})
    else:
        dirty = create_model(
            "DirtyPolicy", __base__=module.OutboxPolicy, hidden=(bool, False)
        )
        selected_policy = dirty(**selected_policy.model_dump())
    with pytest.raises(module.OutboxError):
        module.enqueue(memory.root, value, policy=selected_policy, clock=clock)
    assert memory.files == {}


@pytest.mark.parametrize("value", [NOW.replace(tzinfo=None), NOW.isoformat(), True])
def test_enqueue_invalid_clock_is_rejected_before_publication(memory, value):
    with pytest.raises(module.OutboxError):
        module.enqueue(memory.root, payload(), clock=lambda: value)
    assert memory.files == {}


def test_utc_offset_normalizes_to_same_immutable_payload():
    shifted = timezone(timedelta(hours=8))
    value = payload(
        submitted_at=(NOW - timedelta(seconds=1)).astimezone(shifted),
        evidence_completed_at=(NOW - timedelta(seconds=2)).astimezone(shifted),
    )
    assert value == payload()
    assert value.submitted_at.tzinfo is UTC
    assert value.evidence_completed_at.tzinfo is UTC


def test_two_workers_cannot_claim_the_same_head(memory, clock):
    enqueue(memory, clock)
    barrier = Barrier(2)

    def attempt(worker):
        barrier.wait(timeout=2)
        try:
            return claim(memory, clock, worker)
        except module.OutboxError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(attempt, "worker-a")
        second = pool.submit(attempt, "worker-b")
        results = [first.result(timeout=3), second.result(timeout=3)]
    assert sum(item is not None for item in results) == 1
    view = module.read_job(memory.root, REPORT, clock=clock)
    assert view.status == "claimed"
    assert len(view.events) == 2


def test_expired_claim_can_be_reclaimed_but_old_fence_cannot_dispatch(memory, clock):
    enqueue(memory, clock)
    old = claim(memory, clock)
    clock.now = old.lease_expires_at
    recovered = module.recover_job(memory.root, REPORT, clock=clock)
    assert recovered.status == "retry_wait"
    clock.advance(3600)
    fresh = claim(memory, clock, "worker-b")
    assert fresh.fence_token != old.fence_token
    assert fresh.revision > old.revision
    before = dict(memory.files)
    with pytest.raises(module.OutboxError):
        module.begin_dispatch(memory.root, old, clock=clock)
    assert memory.files == before
    valid = module.begin_dispatch(memory.root, fresh, clock=clock)
    assert valid.fence_token == fresh.fence_token


def test_expired_dispatch_becomes_uncertain_never_reclaimable(memory, clock):
    enqueue(memory, clock)
    old = dispatch_token(memory, clock)
    clock.now = old.lease_expires_at
    view = module.recover_job(memory.root, REPORT, clock=clock)
    assert view.status == "uncertain"
    before = dict(memory.files)
    clock.advance(7200)
    with pytest.raises(module.OutboxError):
        claim(memory, clock, "worker-b")
    with pytest.raises(module.OutboxError):
        module.finish_dispatch(memory.root, old, outcome(old), clock=clock)
    assert memory.files == before


@pytest.mark.parametrize(
    "status", ["delivered", "not_created_retryable", "not_created_final", "uncertain"]
)
def test_expired_dispatch_cannot_finish_before_recovery_runs(memory, clock, status):
    enqueue(memory, clock)
    token = dispatch_token(memory, clock)
    clock.now = token.lease_expires_at
    before = dict(memory.files)
    with pytest.raises(module.OutboxError):
        module.finish_dispatch(memory.root, token, outcome(token, status), clock=clock)
    assert memory.files == before
    assert module.recover_job(memory.root, REPORT, clock=clock).status == "uncertain"


def test_finish_requires_dispatching_not_merely_claimed(memory, clock):
    enqueue(memory, clock)
    token = claim(memory, clock)
    before = dict(memory.files)
    with pytest.raises(module.OutboxError):
        module.finish_dispatch(memory.root, token, outcome(token), clock=clock)
    assert memory.files == before


@pytest.mark.parametrize(
    "field,value",
    [
        ("report_id", "foreign-report"),
        ("envelope_sha256", "f" * 64),
        ("fence_token", "foreign-fence"),
    ],
)
def test_outcome_identity_cannot_complete_another_dispatch(memory, clock, field, value):
    enqueue(memory, clock)
    token = dispatch_token(memory, clock)
    dirty = outcome(token).model_copy(update={field: value})
    before = dict(memory.files)
    with pytest.raises(module.OutboxError):
        module.finish_dispatch(memory.root, token, dirty, clock=clock)
    assert memory.files == before


@pytest.mark.parametrize("failure", ["before", "after", "readback"])
@pytest.mark.asyncio
async def test_dispatch_journal_failure_never_calls_adapter(memory, clock, failure):
    enqueue(memory, clock)
    destination = event_path(3)
    target = {
        "before": memory.fail_publish_before,
        "after": memory.fail_publish_after,
        "readback": memory.fail_read_after_publish,
    }[failure]
    target.add(destination)
    calls = []

    async def adapter(value, token):
        calls.append(True)
        return outcome(token)

    with pytest.raises(module.OutboxError):
        await module.dispatch_once(
            memory.root, REPORT, worker_id="worker-a", adapter=adapter, clock=clock
        )
    assert calls == []
    assert (destination in memory.files) is (failure != "before")
    memory.fail_reads.clear()
    if failure != "before":
        assert module.read_job(memory.root, REPORT, clock=clock).status == "dispatching"


@pytest.mark.asyncio
async def test_remote_created_but_delivery_journal_failure_cannot_trigger_retry(
    memory, clock
):
    enqueue(memory, clock)
    memory.fail_publish_before.add(event_path(4))
    calls = []

    async def adapter(value, token):
        calls.append(token)
        return outcome(token)

    with pytest.raises(module.OutboxError):
        await module.dispatch_once(
            memory.root, REPORT, worker_id="worker-a", adapter=adapter, clock=clock
        )
    assert len(calls) == 1
    assert module.read_job(memory.root, REPORT, clock=clock).status == "dispatching"
    memory.fail_publish_before.clear()
    clock.advance(7200)
    assert module.recover_job(memory.root, REPORT, clock=clock).status == "uncertain"
    with pytest.raises(module.OutboxError):
        claim(memory, clock, "worker-b")
    assert len(calls) == 1


@pytest.mark.parametrize("target", ["job", "head"])
@pytest.mark.parametrize(
    "mutation", ["content", "whitespace", "duplicate_key", "unknown_key", "truncated"]
)
def test_persisted_records_require_exact_canonical_hash_bound_bytes(
    memory, clock, target, mutation
):
    enqueue(memory, clock)
    path = (f"{REPORT}.json",) if target == "job" else event_path(1)
    raw = memory.files[path]
    value = decode(raw)
    if mutation == "content":
        changed = raw.replace(REPORT.encode(), b"foreign-report")
        assert changed != raw
    elif mutation == "whitespace":
        changed = b" " + raw
    elif mutation == "duplicate_key":
        key = next(iter(value))
        pair = encoded({key: value[key]})[1:-1]
        changed = b"{" + pair + b"," + raw[1:]
    elif mutation == "unknown_key":
        changed = encoded(value | {"unknown": True})
    else:
        changed = raw[:-1]
    memory.files[path] = changed
    before = dict(memory.files)
    with pytest.raises(module.OutboxError):
        module.read_job(memory.root, REPORT, clock=clock)
    assert memory.files == before


@pytest.mark.parametrize(
    "mutation",
    ["missing_first", "missing_middle", "wrong_name", "alias", "stale_version"],
)
def test_journal_gaps_aliases_and_rollback_are_not_hidden_by_latest_state(
    memory, clock, mutation
):
    enqueue(memory, clock)
    dispatch_token(memory, clock)
    if mutation == "missing_first":
        del memory.files[event_path(1)]
    elif mutation == "missing_middle":
        del memory.files[event_path(2)]
    elif mutation == "wrong_name":
        memory.files[(f"{REPORT}.state", "latest.json")] = memory.files.pop(
            event_path(3)
        )
    elif mutation == "alias":
        memory.files[(f"{REPORT}.state", "00000003.JSON")] = memory.files[event_path(3)]
    else:
        memory.files[event_path(3)] = memory.files[event_path(1)]
    before = dict(memory.files)
    with pytest.raises(module.OutboxError):
        module.read_job(memory.root, REPORT, clock=clock)
    assert memory.files == before


def test_hash_chain_checks_each_prior_event_not_only_final_status(memory, clock):
    enqueue(memory, clock)
    token = dispatch_token(memory, clock)
    delivered = module.finish_dispatch(memory.root, token, outcome(token), clock=clock)
    assert delivered.status == "delivered"
    # A valid immutable event from another report must not substitute for a
    # middle link, even while the final delivery record remains untouched.
    other = payload(report_id="synthetic-other-report")
    module.enqueue(memory.root, other, clock=clock)
    memory.files[event_path(2)] = memory.files[event_path(1, other.report_id)]
    with pytest.raises(module.OutboxError):
        module.read_job(memory.root, REPORT, clock=clock)


@pytest.mark.parametrize(
    "updates",
    [
        {"worker_id": "worker-a"},
        {"fence_token": "f" * 32},
        {"worker_id": "worker-a", "fence_token": "f" * 32},
    ],
)
def test_even_rehashed_genesis_cannot_claim_a_worker_or_fence(memory, clock, updates):
    view = enqueue(memory, clock)
    dirty = view.head.model_copy(update=updates)
    dirty = dirty.model_copy(
        update={"event_sha256": module._digest(dirty, "event_sha256")}
    )
    memory.files[event_path(1)] = module._wire(dirty)
    with pytest.raises(module.OutboxError):
        module.read_job(memory.root, REPORT, clock=clock)


def test_retry_wait_requires_positive_not_created_proof_and_bounded_backoff(
    memory, clock
):
    enqueue(
        memory,
        clock,
        policy=policy(max_attempts=3, base_backoff_seconds=5, max_backoff_seconds=8),
    )
    token = dispatch_token(memory, clock)
    view = module.finish_dispatch(
        memory.root, token, outcome(token, "not_created_retryable"), clock=clock
    )
    assert view.status == "retry_wait"
    clock.advance(4)
    with pytest.raises(module.OutboxError):
        claim(memory, clock)
    clock.advance(1)
    second = dispatch_token(memory, clock)
    assert second.fence_token != token.fence_token
    again = module.finish_dispatch(
        memory.root, second, outcome(second, "not_created_retryable"), clock=clock
    )
    assert again.status == "retry_wait"
    clock.advance(7)
    with pytest.raises(module.OutboxError):
        claim(memory, clock)
    clock.advance(1)
    third = dispatch_token(memory, clock)
    exhausted = module.finish_dispatch(
        memory.root, third, outcome(third, "not_created_retryable"), clock=clock
    )
    assert exhausted.status == "exhausted" and exhausted.attempts == 3
    clock.advance(7200)
    with pytest.raises(module.OutboxError):
        claim(memory, clock)


def test_known_final_not_created_is_terminal_without_using_remaining_attempts(
    memory, clock
):
    enqueue(memory, clock)
    token = dispatch_token(memory, clock)
    view = module.finish_dispatch(
        memory.root, token, outcome(token, "not_created_final"), clock=clock
    )
    assert view.status == "exhausted" and view.attempts == 1
    before = dict(memory.files)
    clock.advance(7200)
    with pytest.raises(module.OutboxError):
        claim(memory, clock)
    assert memory.files == before


@pytest.mark.parametrize(
    "status,updates",
    [
        ("delivered", {"remote_page_id": None}),
        ("delivered", {"remote_page_id": "not-a-page"}),
        ("delivered", {"receipt_sha256": None}),
        ("not_created_retryable", {"receipt_sha256": None}),
        ("not_created_retryable", {"remote_page_id": PAGE_ID}),
        ("not_created_final", {"receipt_sha256": None}),
        ("not_created_final", {"remote_page_id": PAGE_ID}),
        ("uncertain", {"remote_page_id": PAGE_ID}),
        ("uncertain", {"receipt_sha256": REMOTE_SHA}),
    ],
)
def test_outcome_cannot_invent_delivery_or_absence_proof(
    memory, clock, status, updates
):
    enqueue(memory, clock)
    token = dispatch_token(memory, clock)
    with pytest.raises(ValueError):
        outcome(token, status, **updates)


@pytest.mark.asyncio
async def test_duplicate_dispatch_once_after_delivery_does_not_call_adapter_again(
    memory, clock
):
    enqueue(memory, clock)
    calls = []

    async def adapter(value, token):
        calls.append(token)
        return outcome(token)

    first = await module.dispatch_once(
        memory.root, REPORT, worker_id="worker-a", adapter=adapter, clock=clock
    )
    assert first.status == "delivered"
    before = dict(memory.files)
    with pytest.raises(module.OutboxError):
        await module.dispatch_once(
            memory.root, REPORT, worker_id="worker-b", adapter=adapter, clock=clock
        )
    assert len(calls) == 1 and memory.files == before


@pytest.mark.parametrize(
    "status", ["queued", "claimed", "dispatching", "retry_wait", "uncertain"]
)
def test_nonterminal_or_uncertain_records_are_retained_without_any_delete(
    memory, clock, status
):
    view = enqueue(memory, clock)
    if status == "claimed":
        claim(memory, clock)
        view = module.read_job(memory.root, REPORT, clock=clock)
    elif status in {"dispatching", "retry_wait", "uncertain"}:
        token = dispatch_token(memory, clock)
        if status == "dispatching":
            view = module.read_job(memory.root, REPORT, clock=clock)
        else:
            remote_status = (
                "not_created_retryable" if status == "retry_wait" else "uncertain"
            )
            view = module.finish_dispatch(
                memory.root, token, outcome(token, remote_status), clock=clock
            )
    assert view.status == status
    before = dict(memory.files)
    decision = module.retention_decision(view, now=NOW + timedelta(days=3650))
    assert decision.must_retain is True and decision.eligible_after is None
    assert memory.files == before


@pytest.mark.parametrize("status", ["delivered", "not_created_final"])
def test_terminal_retention_boundary_only_reports_eligibility_never_deletes(
    memory, clock, status
):
    enqueue(memory, clock)
    token = dispatch_token(memory, clock)
    view = module.finish_dispatch(
        memory.root, token, outcome(token, status), clock=clock
    )
    before = dict(memory.files)
    deadline = NOW + timedelta(seconds=view.envelope.policy.terminal_retention_seconds)
    early = module.retention_decision(view, now=deadline - timedelta(microseconds=1))
    equal = module.retention_decision(view, now=deadline)
    assert early.must_retain is True and early.eligible_after == deadline
    assert equal.must_retain is True and equal.eligible_after == deadline
    assert equal.reason == "retained_no_automatic_deletion"
    assert memory.files == before


def reconciliation(view, resolution="delivered", **updates):
    values = {
        "report_id": view.envelope.payload.report_id,
        "envelope_sha256": view.envelope.envelope_sha256,
        "uncertain_event_sha256": view.head.event_sha256,
        "resolution": resolution,
        "worker_quiesced": True,
        "receipt_sha256": REMOTE_SHA,
        "remote_page_id": PAGE_ID if resolution == "delivered" else None,
    }
    return module.Reconciliation(**(values | updates))


def uncertain(memory, clock):
    token = dispatch_token(memory, clock)
    return module.finish_dispatch(
        memory.root, token, outcome(token, "uncertain"), clock=clock
    )


def test_explicit_receipted_reconciliation_can_record_existing_delivery(memory, clock):
    enqueue(memory, clock)
    view = uncertain(memory, clock)
    proof = reconciliation(view)
    before = dict(memory.files)
    completed = module.reconcile_job(memory.root, proof, clock=clock)
    assert completed.status == "delivered" and completed.attempts == 1
    assert completed.head.action == "reconcile"
    assert completed.head.reconciliation == proof
    assert completed.execution_authority is False
    assert all(memory.files[path] == data for path, data in before.items())
    with pytest.raises(module.OutboxError):
        module.reconcile_job(memory.root, proof, clock=clock)


def test_explicit_quiesced_not_created_reconciliation_alone_allows_retry(memory, clock):
    enqueue(memory, clock)
    view = uncertain(memory, clock)
    old_fence = view.head.fence_token
    with pytest.raises(module.OutboxError):
        claim(memory, clock)
    proof = reconciliation(view, "not_created")
    result = module.reconcile_job(memory.root, proof, clock=clock)
    assert result.status == "retry_wait" and result.attempts == 1
    with pytest.raises(module.OutboxError):
        claim(memory, clock)
    clock.advance(5)
    token = claim(memory, clock)
    assert token.fence_token != old_fence


def test_reconciliation_does_not_reset_attempt_budget(memory, clock):
    enqueue(memory, clock, policy=policy(max_attempts=1))
    view = uncertain(memory, clock)
    result = module.reconcile_job(
        memory.root, reconciliation(view, "not_created"), clock=clock
    )
    assert result.status == "exhausted" and result.attempts == 1
    clock.advance(7200)
    with pytest.raises(module.OutboxError):
        claim(memory, clock)


@pytest.mark.parametrize(
    "field,value",
    [
        ("report_id", "other-report"),
        ("envelope_sha256", "f" * 64),
        ("uncertain_event_sha256", "f" * 64),
    ],
)
def test_reconciliation_cannot_use_foreign_or_stale_identity(
    memory, clock, field, value
):
    enqueue(memory, clock)
    view = uncertain(memory, clock)
    proof = reconciliation(view, **{field: value})
    before = dict(memory.files)
    with pytest.raises(module.OutboxError):
        module.reconcile_job(memory.root, proof, clock=clock)
    assert memory.files == before


@pytest.mark.parametrize(
    "field,value",
    [
        ("worker_quiesced", False),
        ("worker_quiesced", 1),
        ("worker_quiesced", "true"),
        ("receipt_sha256", None),
        ("remote_page_id", None),
    ],
)
def test_reconciliation_requires_exact_quiescence_and_delivery_receipts(
    memory, clock, field, value
):
    enqueue(memory, clock)
    view = uncertain(memory, clock)
    with pytest.raises(ValueError):
        reconciliation(view, **{field: value})


@pytest.mark.asyncio
@pytest.mark.parametrize("cleanup", ["raises", "returns_delivered"])
async def test_adapter_cleanup_cannot_swallow_or_replace_caller_cancel(
    memory, clock, cleanup
):
    enqueue(memory, clock)
    entered = asyncio.Event()
    calls = []

    async def adapter(value, token):
        calls.append(token)
        entered.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            if cleanup == "raises":
                raise RuntimeError("synthetic cleanup failed") from None
            return outcome(token)

    task = asyncio.create_task(
        module.dispatch_once(
            memory.root, REPORT, worker_id="worker-a", adapter=adapter, clock=clock
        )
    )
    await asyncio.wait_for(entered.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.cancelled() and len(calls) == 1
    assert module.read_job(memory.root, REPORT, clock=clock).status == "uncertain"


@pytest.mark.asyncio
async def test_already_cancelling_caller_does_not_claim_or_dispatch(memory, clock):
    enqueue(memory, clock)
    before = dict(memory.files)
    calls = []

    async def adapter(value, token):
        calls.append(True)
        return outcome(token)

    async def runner():
        asyncio.current_task().cancel()
        await module.dispatch_once(
            memory.root, REPORT, worker_id="worker-a", adapter=adapter, clock=clock
        )

    task = asyncio.create_task(runner())
    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.cancelled() and calls == [] and memory.files == before


@pytest.mark.asyncio
async def test_adapter_cannot_turn_an_expired_internal_timeout_into_delivery(
    memory, clock
):
    enqueue(memory, clock, policy=policy(adapter_timeout_seconds=1))
    calls = []

    async def adapter(value, token):
        calls.append(token)
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            return outcome(token)

    task = asyncio.create_task(
        module.dispatch_once(
            memory.root, REPORT, worker_id="worker-a", adapter=adapter, clock=clock
        )
    )
    view = await asyncio.wait_for(task, timeout=2)
    assert not task.cancelled() and task.cancelling() == 0
    assert len(calls) == 1 and view.status == "uncertain"
    assert module.read_job(memory.root, REPORT, clock=clock).status == "uncertain"


@pytest.fixture
def native_root(tmp_path):
    root = tmp_path / "trusted-native-outbox"
    root.mkdir()
    return root


@pytest.mark.asyncio
async def test_dispatch_worker_recovers_expired_lease_as_uncertain_instead_of_late_delivery(
    memory, clock
):
    enqueue(memory, clock)
    calls = []

    async def adapter(value, token):
        calls.append(token)
        clock.now = token.lease_expires_at
        return outcome(token)

    view = await module.dispatch_once(
        memory.root, REPORT, worker_id="worker-a", adapter=adapter, clock=clock
    )
    assert view.status == "uncertain" and view.head.action == "recover"
    assert len(calls) == 1 and view.attempts == 1
    clock.advance(3600)
    with pytest.raises(module.OutboxError):
        await module.dispatch_once(
            memory.root, REPORT, worker_id="worker-b", adapter=adapter, clock=clock
        )
    assert len(calls) == 1


@pytest.mark.parametrize(
    "location", ["view", "envelope", "payload", "policy", "event", "outcome"]
)
@pytest.mark.parametrize("private", [{}, {"hidden": "synthetic-only"}])
def test_hidden_private_storage_is_rejected_before_output_revalidation(
    memory, clock, location, private
):
    enqueue(memory, clock)
    token = dispatch_token(memory, clock)
    view = module.finish_dispatch(memory.root, token, outcome(token), clock=clock)
    targets = {
        "view": view,
        "envelope": view.envelope,
        "payload": view.envelope.payload,
        "policy": view.envelope.policy,
        "event": view.head,
        "outcome": view.head.outcome,
    }
    object.__setattr__(targets[location], "__pydantic_private__", private)
    before = dict(memory.files)
    with pytest.raises(module.OutboxError):
        module.validate_outbox_view(view)
    assert memory.files == before


def test_small_journal_exhausts_known_not_created_before_another_remote_attempt(
    memory, clock, monkeypatch
):
    monkeypatch.setattr(module, "MAX_EVENTS", 8)
    enqueue(memory, clock, policy=policy(max_attempts=10))
    first = dispatch_token(memory, clock)
    waiting = module.finish_dispatch(
        memory.root, first, outcome(first, "not_created_retryable"), clock=clock
    )
    assert waiting.head.revision == 4 and waiting.status == "retry_wait"
    clock.advance(5)
    second = dispatch_token(memory, clock)
    final = module.finish_dispatch(
        memory.root, second, outcome(second, "not_created_retryable"), clock=clock
    )
    assert (
        final.head.revision == 7 and final.status == "exhausted" and final.attempts == 2
    )
    before = dict(memory.files)
    clock.advance(3600)
    with pytest.raises(module.OutboxError):
        claim(memory, clock)
    assert memory.files == before


@pytest.mark.parametrize("resolution", ["delivered", "not_created"])
@pytest.mark.parametrize("unknown_source", ["finish", "recovery"])
def test_small_journal_reserves_room_for_unknown_delivery_and_explicit_reconciliation(
    memory, clock, monkeypatch, resolution, unknown_source
):
    monkeypatch.setattr(module, "MAX_EVENTS", 8)
    enqueue(memory, clock, policy=policy(max_attempts=10))
    first = dispatch_token(memory, clock)
    module.finish_dispatch(
        memory.root, first, outcome(first, "not_created_retryable"), clock=clock
    )
    clock.advance(5)
    second = dispatch_token(memory, clock)
    if unknown_source == "finish":
        unknown = module.finish_dispatch(
            memory.root, second, outcome(second, "uncertain"), clock=clock
        )
    else:
        clock.now = second.lease_expires_at
        unknown = module.recover_job(memory.root, REPORT, clock=clock)
    assert unknown.head.revision == 7 and unknown.status == "uncertain"
    before = dict(memory.files)
    with pytest.raises(module.OutboxError):
        claim(memory, clock)
    assert memory.files == before
    resolved = module.reconcile_job(
        memory.root, reconciliation(unknown, resolution), clock=clock
    )
    assert resolved.head.revision == 8
    assert resolved.status == (
        "delivered" if resolution == "delivered" else "exhausted"
    )
    assert resolved.attempts == 2
    assert all(memory.files[path] == data for path, data in before.items())
    assert module.read_job(memory.root, REPORT, clock=clock) == resolved


def test_small_journal_exhausts_repeated_expired_claims_without_spending_remote_attempts(
    memory, clock, monkeypatch
):
    monkeypatch.setattr(module, "MAX_EVENTS", 8)
    enqueue(memory, clock, policy=policy(max_attempts=10))
    first = claim(memory, clock)
    clock.now = first.lease_expires_at
    recovered = module.recover_job(memory.root, REPORT, clock=clock)
    assert recovered.head.revision == 3 and recovered.status == "retry_wait"
    second = claim(memory, clock)
    clock.now = second.lease_expires_at
    exhausted = module.recover_job(memory.root, REPORT, clock=clock)
    assert exhausted.head.revision == 5 and exhausted.status == "exhausted"
    assert exhausted.attempts == 0
    before = dict(memory.files)
    with pytest.raises(module.OutboxError):
        claim(memory, clock)
    assert memory.files == before


def test_real_filesystem_root_is_rejected_without_unsafe_publication():
    with pytest.raises(module.OutboxError):
        module.enqueue(Path(Path.cwd().anchor), payload(), clock=Clock())


@pytest.mark.skipif(
    os.name == "nt",
    reason="Native POSIX pin/flock proof requires POSIX; not Windows success evidence",
)
def test_native_posix_journal_publication_readback_and_immutability(native_root, clock):
    first = module.enqueue(native_root, payload(), clock=clock)
    root_file = native_root / f"{REPORT}.json"
    initial_state = native_root / f"{REPORT}.state" / "00000001.json"
    before = {
        path: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (root_file, initial_state)
    }
    token = module.claim_job(native_root, REPORT, worker_id="worker-a", clock=clock)
    token = module.begin_dispatch(native_root, token, clock=clock)
    result = module.finish_dispatch(native_root, token, outcome(token), clock=clock)
    assert result.status == "delivered" and result.envelope == first.envelope
    assert module.read_job(native_root, REPORT, clock=clock) == result
    assert {path.name for path in initial_state.parent.iterdir()} == {
        f"{index:08d}.json" for index in range(1, 5)
    }
    assert all(
        hashlib.sha256(path.read_bytes()).hexdigest() == digest
        for path, digest in before.items()
    )


@pytest.mark.skipif(
    os.name == "nt",
    reason="Native POSIX no-clobber proof requires POSIX; not Windows success evidence",
)
def test_native_posix_identical_enqueue_and_conflict_never_replace_bytes(
    native_root, clock
):
    first = module.enqueue(native_root, payload(), clock=clock)
    before = {
        path.relative_to(native_root): path.read_bytes()
        for path in native_root.rglob("*.json")
    }
    assert module.enqueue(native_root, payload(), clock=clock) == first
    with pytest.raises(module.OutboxError):
        module.enqueue(
            native_root, payload(order_reference="another-order"), clock=clock
        )
    assert {
        path.relative_to(native_root): path.read_bytes()
        for path in native_root.rglob("*.json")
    } == before


@pytest.mark.skipif(
    os.name == "nt",
    reason="Native POSIX concurrent root lease proof requires POSIX; not Windows success evidence",
)
def test_native_posix_two_workers_cannot_both_claim(native_root, clock):
    module.enqueue(native_root, payload(), clock=clock)
    barrier = Barrier(2)

    def attempt(worker):
        barrier.wait(timeout=2)
        try:
            return module.claim_job(native_root, REPORT, worker_id=worker, clock=clock)
        except module.OutboxError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(attempt, worker) for worker in ("worker-a", "worker-b")]
        results = [future.result(timeout=3) for future in futures]
    assert sum(item is not None for item in results) == 1
    assert module.read_job(native_root, REPORT, clock=clock).status == "claimed"


@pytest.mark.skipif(
    os.name == "nt",
    reason="Native POSIX symlink and hardlink proof requires POSIX; not Windows success evidence",
)
@pytest.mark.parametrize("link_kind", ["root", "ancestor", "state", "job_hardlink"])
def test_native_posix_linked_paths_are_refused_without_altering_outside_bytes(
    native_root, clock, link_kind
):
    outside = native_root.parent / "outside-test-owned"
    outside.mkdir()
    sentinel = outside / "sentinel.bin"
    sentinel.write_bytes(b"synthetic outside sentinel must remain unchanged")
    expected_sentinel = sentinel.read_bytes()
    if link_kind in {"root", "ancestor"}:
        alias = native_root.parent / "linked-root"
        if link_kind == "root":
            alias.symlink_to(native_root, target_is_directory=True)
            target = alias
        else:
            alias.symlink_to(native_root.parent, target_is_directory=True)
            target = alias / native_root.name
        with pytest.raises(module.OutboxError):
            module.enqueue(target, payload(), clock=clock)
        assert list(native_root.iterdir()) == []
    else:
        module.enqueue(native_root, payload(), clock=clock)
        original_job = (native_root / f"{REPORT}.json").read_bytes()
        if link_kind == "state":
            state = native_root / f"{REPORT}.state"
            relocated = outside / "retained-state"
            state.rename(relocated)
            original_event = (relocated / "00000001.json").read_bytes()
            state.symlink_to(relocated, target_is_directory=True)
        else:
            duplicate = outside / "retained-job.json"
            os.link(native_root / f"{REPORT}.json", duplicate)
            assert duplicate.stat().st_nlink > 1
        with pytest.raises(module.OutboxError):
            module.read_job(native_root, REPORT, clock=clock)
        assert (native_root / f"{REPORT}.json").read_bytes() == original_job
        if link_kind == "state":
            assert (relocated / "00000001.json").read_bytes() == original_event
        else:
            assert duplicate.read_bytes() == original_job
    assert sentinel.read_bytes() == expected_sentinel


@pytest.mark.skipif(
    os.name != "nt",
    reason="This case records native Windows ancestor-pin behavior only",
)
@pytest.mark.parametrize("inject_late_denial", (False, True))
def test_native_windows_owned_root_truthfully_records_pin_denial_or_real_success(
    native_root, clock, monkeypatch, inject_late_denial
):
    pins, publications = [], []
    original_pin = module.storage._WindowsAPI.open_directory
    original_publish = module.storage._WindowsDirectory.publish

    def pin(api, path, *, publisher=False):
        try:
            handle = original_pin(api, path, publisher=publisher)
        except PermissionError as error:
            pins.append((path, publisher, "denied", error.winerror))
            raise
        pins.append((path, publisher, "opened", None))
        return handle

    def publish(directory, name, raw):
        try:
            original_publish(directory, name, raw)
        except PermissionError as error:
            publications.append((directory.path, name, "denied", error.winerror))
            raise
        publications.append((directory.path, name, "published", None))

    monkeypatch.setattr(module.storage._WindowsAPI, "open_directory", pin)
    monkeypatch.setattr(module.storage._WindowsDirectory, "publish", publish)
    if inject_late_denial:
        original_link = module.storage._WindowsAPI.link_same_directory

        def denied_envelope(api, fd, name):
            if name == f"{REPORT}.json":
                raise ctypes.WinError(32)
            return original_link(api, fd, name)

        monkeypatch.setattr(
            module.storage._WindowsAPI, "link_same_directory", denied_envelope
        )
    initial_at = clock.now
    try:
        result = module.enqueue(native_root, payload(), clock=clock)
    except module.OutboxError as error:
        assert error.code == "outbox_storage_permission_denied", (
            "Only a specifically classified native permission denial is expected"
        )
        names = {item.name for item in native_root.iterdir()}
        if not names:
            # Only actual ancestor/root pin denial may use this early branch.
            # Do not call a failed post-mkdir operation an ancestor denial.
            assert publications == []
            assert pins[-1][2] == "denied"
            assert pins[-1][0] in (*native_root.parents, native_root)
            assert not any(
                path == native_root and publisher and state == "opened"
                for path, publisher, state, _ in pins
            )
            print(
                "Native Windows outbox: early ancestor/root pin denied; "
                "no artifact created; native publication unverified"
            )
        else:
            # A root-level hardlink may fail with WinError 32 after the child
            # journal has been fully published. That is retained evidence, NOT
            # a successful enqueue and NOT permission to delete or reconstruct it.
            assert getattr(error.__context__, "winerror", None) == 32
            assert publications == [
                (
                    native_root / f"{REPORT}.state",
                    "00000001.json",
                    "published",
                    None,
                ),
                (native_root, f"{REPORT}.json", "denied", 32),
            ]
            assert names == {f"{REPORT}.state"}
            state = native_root / f"{REPORT}.state"
            assert {item.name for item in state.iterdir()} == {"00000001.json"}
            initial_file = state / "00000001.json"
            original = initial_file.read_bytes()
            envelope = module._seal(
                module.OutboxEnvelope,
                {"payload": payload(), "policy": policy(), "enqueued_at": initial_at},
                "envelope_sha256",
            )
            initial = module._event(envelope, None, "enqueue", initial_at)
            assert original == module._wire(initial)
            module._verify(envelope, (module._decode(original, module.OutboxEvent),))
            assert initial.action == "enqueue" and initial.revision == 1
            assert not (native_root / f"{REPORT}.json").exists()

            # A fresh root context cannot repair/backfill the missing marker,
            # even with identical payload or a newly reconstructed timestamp.
            clock.advance(1)
            for value in (payload(), payload(order_reference="changed-local-claim")):
                with pytest.raises(
                    module.OutboxError, match="^outbox_enqueue_incomplete$"
                ):
                    module.enqueue(native_root, value, clock=clock)
            with pytest.raises(
                module.OutboxError, match="^outbox_storage_unavailable$"
            ):
                module.read_job(native_root, REPORT, clock=clock)
            called = []

            async def adapter(*args):
                called.append(True)
                raise AssertionError("incomplete enqueue must not dispatch")

            with pytest.raises(
                module.OutboxError, match="^outbox_storage_unavailable$"
            ):
                asyncio.run(
                    module.dispatch_once(
                        native_root,
                        REPORT,
                        worker_id="native-partial-proof",
                        adapter=adapter,
                        clock=clock,
                    )
                )
            assert called == []
            assert {item.name for item in native_root.iterdir()} == names
            assert {item.name for item in state.iterdir()} == {"00000001.json"}
            assert initial_file.read_bytes() == original
            print(
                f"Native Windows outbox: late denial (injected={inject_late_denial}); "
                "exact initial journal retained, commit marker absent, no dispatch; "
                "this failed invocation is not an enqueue success"
            )
    else:
        # If this execution environment permits the real pin chain, demand real
        # bytes and readback instead of assuming a denial or using a fake pass.
        assert result.status == "queued"
        assert (native_root / f"{REPORT}.json").is_file()
        assert (native_root / f"{REPORT}.state" / "00000001.json").is_file()
        assert module.read_job(native_root, REPORT, clock=clock) == result
        print("Native Windows outbox: real pinned publication and readback succeeded")


@pytest.mark.skipif(os.name != "nt", reason="Required real Windows outbox acceptance")
def test_native_windows_root_publication_delivery_and_conflict_preserve_history(
    native_root, clock
):
    first = module.enqueue(native_root, payload(), clock=clock)
    assert first.status == "queued"
    original = {
        path.relative_to(native_root): path.read_bytes()
        for path in native_root.rglob("*.json")
    }
    assert len(original) == 2
    assert module.enqueue(native_root, payload(), clock=clock) == first
    with pytest.raises(module.OutboxError):
        module.enqueue(
            native_root, payload(order_reference="conflicting-order"), clock=clock
        )
    token = module.claim_job(native_root, REPORT, worker_id="windows-a", clock=clock)
    token = module.begin_dispatch(native_root, token, clock=clock)
    result = module.finish_dispatch(native_root, token, outcome(token), clock=clock)
    assert result.status == "delivered"
    assert module.read_job(native_root, REPORT, clock=clock) == result
    assert all(
        (native_root / name).read_bytes() == raw for name, raw in original.items()
    )
    assert len(tuple(native_root.rglob("*.json"))) == 5
    assert not tuple(native_root.rglob("*.partial"))
    with pytest.raises(module.OutboxError):
        module.claim_job(native_root, REPORT, worker_id="windows-b", clock=clock)


@pytest.mark.skipif(os.name != "nt", reason="Required real Windows worker fencing")
def test_native_windows_two_workers_cannot_both_claim(native_root, clock):
    module.enqueue(native_root, payload(), clock=clock)
    barrier = Barrier(2)

    def attempt(worker):
        barrier.wait(timeout=10)
        try:
            return module.claim_job(native_root, REPORT, worker_id=worker, clock=clock)
        except module.OutboxError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = tuple(pool.map(attempt, ("windows-a", "windows-b")))
    assert sum(result is not None for result in results) == 1
    assert module.read_job(native_root, REPORT, clock=clock).status == "claimed"


@pytest.mark.parametrize(
    "stage", ("journal_before", "journal_after", "envelope_before")
)
def test_permission_denial_after_mkdir_retains_exact_partial_and_refuses_rebuild(
    memory, clock, monkeypatch, stage
):
    original_publish = MemoryDirectory.publish
    initial_path = event_path(1)
    envelope_path = (f"{REPORT}.json",)
    denied_path = envelope_path if stage == "envelope_before" else initial_path

    def denied(directory, name, raw):
        destination = (*directory.relative, name)
        if destination == denied_path and stage != "journal_after":
            raise PermissionError("synthetic permission denial before publication")
        original_publish(directory, name, raw)
        if destination == denied_path:
            raise PermissionError("synthetic permission denial after publication")

    monkeypatch.setattr(MemoryDirectory, "publish", denied)
    with pytest.raises(module.OutboxError, match="^outbox_storage_permission_denied$"):
        enqueue(memory, clock)
    assert memory.directories == {(), (f"{REPORT}.state",)}
    if stage == "journal_before":
        assert memory.files == {}
    else:
        expected = module._seal(
            module.OutboxEnvelope,
            {"payload": payload(), "policy": policy(), "enqueued_at": clock.now},
            "envelope_sha256",
        )
        initial = module._event(expected, None, "enqueue", clock.now)
        assert memory.files == {initial_path: module._wire(initial)}
        module._verify(expected, (initial,))
    assert envelope_path not in memory.files
    retained = dict(memory.files)
    # Removing the injected fault does not license reconstruction of an
    # interrupted enqueue. Its retained state is not another submit allowance.
    monkeypatch.setattr(MemoryDirectory, "publish", original_publish)
    clock.advance(1)
    for value in (payload(), payload(order_reference="changed-local-claim")):
        with pytest.raises(module.OutboxError, match="^outbox_enqueue_incomplete$"):
            module.enqueue(memory.root, value, clock=clock)
    with pytest.raises(module.OutboxError, match="^outbox_storage_unavailable$"):
        module.claim_job(memory.root, REPORT, worker_id="new-worker", clock=clock)
    assert memory.files == retained
    assert memory.active_contexts == 0


def test_root_permission_denial_before_transaction_creates_no_artifacts(
    memory, clock, monkeypatch
):
    @contextmanager
    def denied_root(root):
        assert root == memory.root
        raise PermissionError("synthetic root pin permission denial")
        yield  # pragma: no cover -- required contextmanager generator shape

    monkeypatch.setattr(module, "_root_context", denied_root)
    with pytest.raises(module.OutboxError, match="^outbox_storage_permission_denied$"):
        enqueue(memory, clock)
    assert memory.files == {}
    assert memory.directories == {()}
    assert memory.operations == []


def test_permission_denial_after_commit_marker_is_not_assumed_to_be_zero_writes(
    memory, clock, monkeypatch
):
    original_publish = MemoryDirectory.publish

    def denied_after_marker(directory, name, raw):
        original_publish(directory, name, raw)
        if (*directory.relative, name) == (f"{REPORT}.json",):
            raise PermissionError("synthetic post-commit driver failure")

    monkeypatch.setattr(MemoryDirectory, "publish", denied_after_marker)
    with pytest.raises(module.OutboxError, match="^outbox_storage_permission_denied$"):
        enqueue(memory, clock)
    assert set(memory.files) == {event_path(1), (f"{REPORT}.json",)}
    retained = dict(memory.files)
    monkeypatch.setattr(MemoryDirectory, "publish", original_publish)
    # This is read-only reconciliation plus identical LOCAL enqueue, never a
    # second remote/order call. A changed report remains a conflict.
    readback = module.read_job(memory.root, REPORT, clock=clock)
    assert readback.status == "queued" and len(readback.events) == 1
    assert module.enqueue(memory.root, payload(), clock=clock) == readback
    with pytest.raises(module.OutboxError, match="^outbox_report_conflict$"):
        module.enqueue(memory.root, payload(order_reference="changed"), clock=clock)
    assert memory.files == retained
