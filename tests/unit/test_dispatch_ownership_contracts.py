"""D0 ownership races with synthetic replay and a test-only DB read double.

No source authenticity, PostgreSQL, credential, exchange or transport acceptance
is claimed. There is no fake authority issuer in application code or these tests.
"""

import asyncio
import copy
import gc
import hashlib
import json
import pickle
from datetime import UTC, datetime, timedelta

import pytest

from app.database.repositories.qualification_ledger import (
    QualificationLedgerRepository,
)
from app.trade_qualification import dispatch_ownership as ownership
from app.trade_qualification.dispatch_ownership import (
    DispatchObservation,
    DispatchOwnership,
    DispatchOwnershipError,
    DispatchSlot,
)
from app.trade_qualification.reservations import (
    LedgerScope,
    ReservationRequest,
    checked,
)
from app.trade_qualification.submission_intent import (
    SubmissionIntentRecord,
    build_submission_intent,
)
from tests.unit.qualification_execution_binding_fixtures import (
    consumed_receipt,
    execution_binding,
)
from tests.unit.qualification_ledger_fixtures import ledger_fixture


@pytest.fixture(scope="module")
def examples():
    # The fixture builder calls asyncio.run; construct it in synchronous pytest
    # setup, never inside an async test's already-running event loop.
    result = {}
    for direction in ("long", "short"):
        fixture = ledger_fixture(direction)
        record = build_submission_intent(
            fixture.request,
            consumed_receipt(fixture),
            execution_binding=execution_binding(fixture),
        )
        result[direction] = fixture, record
    return result


@pytest.fixture(scope="module")
def hedged_record(examples):
    fixture, _ = examples["long"]
    return build_submission_intent(
        fixture.request,
        consumed_receipt(fixture),
        execution_binding=execution_binding(fixture, position_mode="long_short_mode"),
    )


@pytest.fixture(scope="module")
def renamed_request():
    return ledger_fixture(report_id="ownership-renamed-event").request


@pytest.fixture(params=("long", "short"))
def example(examples, request):
    return examples[request.param]


class Clock:
    def __init__(self, fixture):
        self.now = fixture.now - timedelta(milliseconds=1)
        self.mono = 100.0

    def after_commit(self, fixture):
        self.now = fixture.now + timedelta(milliseconds=1)
        self.mono += 0.002


class Reader:
    """Only a repository read double, not a B/C producer or dispatch issuer."""

    def __init__(self, record):
        self.result = record
        self.calls = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.release.set()
        self.failure = None
        self.suppress_cancel = False

    async def read(self, repository, scope, event_key, *, expected_sha256):
        self.calls.append((scope, event_key, expected_sha256))
        self.started.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            if not self.suppress_cancel:
                raise
        if self.failure is not None:
            raise self.failure
        return self.result


def setup_owner(monkeypatch, example):
    fixture, record = example
    clock = Clock(fixture)
    reader = Reader(record)
    repository = QualificationLedgerRepository(None, clock=lambda: clock.now)
    monkeypatch.setattr(
        QualificationLedgerRepository, "read_submission_intent", reader.read
    )
    owner = DispatchOwnership(
        repository,
        fixture.request.scope,
        clock=lambda: clock.now,
        monotonic=lambda: clock.mono,
    )
    return owner, reader, clock, repository


def repin(body):
    raw = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return SubmissionIntentRecord(raw, hashlib.sha256(raw.encode()).hexdigest())


async def test_bound_lineage_still_terminal_denies_without_trusted_producer(
    monkeypatch, example
):
    fixture, record = example
    owner, reader, clock, _ = setup_owner(monkeypatch, example)
    slot = owner.begin(fixture.request)
    clock.after_commit(fixture)
    result = await owner.bind_intent(slot, record)
    assert result.state == "bound_denied"
    assert result.intent_sha256 == record.sha256
    assert not result.execution_authority and not result.order_retry_authority
    assert not owner.execution_authority and not owner.order_retry_authority
    assert reader.calls == [
        (
            fixture.request.scope,
            fixture.request.origin.original_event_key,
            record.sha256,
        )
    ]
    with pytest.raises(
        DispatchOwnershipError, match="^dispatch_trusted_producer_unavailable$"
    ):
        owner.require_ready(slot)
    assert owner.inspect(slot).state == "denied"
    with pytest.raises(DispatchOwnershipError):
        await owner.bind_intent(slot, record)
    assert len(reader.calls) == 1


@pytest.mark.parametrize("phase", ("opened", "bound"))
async def test_cancel_tombstone_cannot_be_reused(monkeypatch, example, phase):
    fixture, record = example
    owner, reader, clock, _ = setup_owner(monkeypatch, example)
    slot = owner.begin(fixture.request)
    clock.after_commit(fixture)
    if phase == "bound":
        await owner.bind_intent(slot, record)
    owner.cancel(slot)
    owner.cancel(slot)
    with pytest.raises(DispatchOwnershipError):
        await owner.bind_intent(slot, record)
    assert owner.inspect(slot).state == "cancelled"
    with pytest.raises(DispatchOwnershipError, match="dispatch_event_already_owned"):
        owner.begin(fixture.request)
    assert len(reader.calls) == (1 if phase == "bound" else 0)


async def test_cancel_during_read_rejects_late_success(monkeypatch, example):
    fixture, record = example
    owner, reader, clock, _ = setup_owner(monkeypatch, example)
    slot = owner.begin(fixture.request)
    clock.after_commit(fixture)
    reader.release.clear()
    task = asyncio.ensure_future(owner.bind_intent(slot, record))
    await reader.started.wait()
    owner.cancel(slot)
    reader.release.set()
    with pytest.raises(DispatchOwnershipError):
        await task
    assert owner.inspect(slot).state == "cancelled"
    assert len(reader.calls) == 1


async def test_task_cancellation_preserves_event_tombstone(monkeypatch, example):
    fixture, record = example
    owner, reader, clock, _ = setup_owner(monkeypatch, example)
    slot = owner.begin(fixture.request)
    clock.after_commit(fixture)
    reader.release.clear()
    task = asyncio.ensure_future(owner.bind_intent(slot, record))
    await reader.started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert owner.inspect(slot).state == "cancelled"
    with pytest.raises(DispatchOwnershipError, match="dispatch_event_already_owned"):
        owner.begin(fixture.request)
    assert len(reader.calls) == 1


async def test_generation_captured_before_queued_read_mutex(monkeypatch, examples):
    fixture, record = examples["long"]
    other, other_record = examples["short"]
    owner, reader, clock, _ = setup_owner(monkeypatch, examples["long"])
    first, queued = owner.begin(fixture.request), owner.begin(other.request)
    clock.after_commit(fixture)
    reader.release.clear()
    first_task = asyncio.ensure_future(owner.bind_intent(first, record))
    await reader.started.wait()
    queued_task = asyncio.ensure_future(owner.bind_intent(queued, other_record))
    await asyncio.sleep(0)
    assert owner.inspect(queued).state == "reading"
    assert len(reader.calls) == 1
    owner.revoke_now()
    reader.release.set()
    results = await asyncio.gather(first_task, queued_task, return_exceptions=True)
    assert all(type(result) is DispatchOwnershipError for result in results)
    assert owner.inspect(first).state == owner.inspect(queued).state == "revoked"
    # Queued work must not capture the new generation and reach the DB later.
    assert len(reader.calls) == 1
    with pytest.raises(DispatchOwnershipError, match="dispatch_owner_revoked"):
        owner.begin(fixture.request)


async def test_cancel_queued_read_never_reaches_repository(monkeypatch, examples):
    fixture, record = examples["long"]
    other, other_record = examples["short"]
    owner, reader, clock, _ = setup_owner(monkeypatch, examples["long"])
    first, queued = owner.begin(fixture.request), owner.begin(other.request)
    clock.after_commit(fixture)
    reader.release.clear()
    first_task = asyncio.ensure_future(owner.bind_intent(first, record))
    await reader.started.wait()
    queued_task = asyncio.ensure_future(owner.bind_intent(queued, other_record))
    await asyncio.sleep(0)
    assert owner.inspect(queued).state == "reading"
    queued_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await queued_task
    reader.release.set()
    assert (await first_task).state == "bound_denied"
    assert owner.inspect(queued).state == "cancelled"
    assert len(reader.calls) == 1


async def test_concurrent_duplicate_bind_reads_at_most_once(monkeypatch, example):
    fixture, record = example
    owner, reader, clock, _ = setup_owner(monkeypatch, example)
    slot = owner.begin(fixture.request)
    clock.after_commit(fixture)
    reader.release.clear()
    first = asyncio.ensure_future(owner.bind_intent(slot, record))
    await reader.started.wait()
    with pytest.raises(DispatchOwnershipError, match="terminal_or_replayed"):
        await owner.bind_intent(slot, record)
    reader.release.set()
    with pytest.raises(DispatchOwnershipError):
        await first
    assert owner.inspect(slot).state == "denied"
    assert len(reader.calls) == 1


@pytest.mark.parametrize(
    "failure",
    (
        RuntimeError("synthetic-private-value"),
        TimeoutError(),
        DispatchOwnershipError("synthetic-private-value"),
    ),
)
async def test_db_uncertainty_revokes_owner_without_retry_or_error_echo(
    monkeypatch, example, failure
):
    fixture, record = example
    owner, reader, clock, _ = setup_owner(monkeypatch, example)
    slot = owner.begin(fixture.request)
    clock.after_commit(fixture)
    reader.failure = failure
    with pytest.raises(
        DispatchOwnershipError, match="^dispatch_readback_failed$"
    ) as exc:
        await owner.bind_intent(slot, record)
    assert "synthetic-private-value" not in str(exc.value)
    assert owner.inspect(slot).state == "denied"
    with pytest.raises(DispatchOwnershipError, match="dispatch_owner_revoked"):
        owner.begin(fixture.request)
    assert len(reader.calls) == 1


@pytest.mark.parametrize(
    "change", ("utc_backwards", "mono_backwards", "utc_expiry", "mono_expiry")
)
async def test_clock_change_during_read_never_binds(monkeypatch, example, change):
    fixture, record = example
    owner, reader, clock, _ = setup_owner(monkeypatch, example)
    slot = owner.begin(fixture.request)
    clock.after_commit(fixture)
    reader.release.clear()
    task = asyncio.ensure_future(owner.bind_intent(slot, record))
    await reader.started.wait()
    if change == "utc_backwards":
        clock.now -= timedelta(seconds=1)
    elif change == "mono_backwards":
        clock.mono -= 1
    elif change == "utc_expiry":
        clock.now = fixture.request.origin.deadline
    else:
        clock.mono += 100000
    reader.release.set()
    with pytest.raises(DispatchOwnershipError):
        await task
    assert owner.inspect(slot).state in ("revoked", "denied")


@pytest.mark.parametrize("kind", ("historical", "future_commit", "legacy"))
async def test_historical_future_and_legacy_intents_rejected_before_db(
    monkeypatch, example, kind
):
    fixture, record = example
    owner, reader, clock, _ = setup_owner(monkeypatch, example)
    if kind == "historical":
        clock.after_commit(fixture)
    slot = owner.begin(fixture.request)
    if kind != "future_commit":
        clock.after_commit(fixture)
    if kind == "legacy":
        record = build_submission_intent(fixture.request, consumed_receipt(fixture))
    with pytest.raises(DispatchOwnershipError):
        await owner.bind_intent(slot, record)
    assert owner.inspect(slot).state == "denied"
    assert reader.calls == []


@pytest.mark.parametrize(
    "path",
    (
        ("request_sha256",),
        ("origin_sha256",),
        ("candidate_entry",),
        ("stop_loss",),
        ("take_profit",),
        ("contracts",),
        ("leverage",),
        ("client_order_id",),
        ("protection_client_order_id",),
        ("evidence_sha256",),
        ("recheck_sha256",),
        ("economics_sha256",),
        ("account_packet_sha256",),
        ("account_plan_sha256",),
        ("execution_binding_sha256",),
        ("exchange_request_sha256",),
        ("consumed_receipt", "reservation_id"),
        ("consumed_receipt", "original_event_key"),
        ("consumed_receipt", "report_id"),
        ("consumed_receipt", "account_revision"),
        ("consumed_receipt", "ledger_revision"),
        ("consumed_receipt", "deadline"),
        ("exchange_request", "account_uid"),
        ("exchange_request", "environment"),
        ("exchange_request", "path"),
        ("exchange_request", "headers", "expTime"),
        ("exchange_request", "body", "instId"),
        ("exchange_request", "body", "posSide"),
        ("exchange_request", "body", "tdMode"),
        ("source_authenticity_verified",),
        ("account_complete",),
        ("intrabar_path_verified",),
        ("execution_authority",),
    ),
)
async def test_rehashed_lineage_or_authority_mutation_cannot_bind(
    monkeypatch, examples, path
):
    example = examples["long"]
    fixture, record = example
    owner, reader, clock, _ = setup_owner(monkeypatch, example)
    slot = owner.begin(fixture.request)
    clock.after_commit(fixture)
    body = json.loads(record.canonical_json)
    target = body
    for key in path[:-1]:
        target = target[key]
    value = target[path[-1]]
    target[path[-1]] = (
        True
        if type(value) is bool
        else (value + 1 if type(value) is int else "changed")
    )
    changed = repin(body)
    # Keep the independently read DB record fixed. Some other consumed ledger
    # revisions are computationally valid claims; replay alone cannot know the
    # real journal if a test changes both inputs to the same invented revision.
    with pytest.raises(
        DispatchOwnershipError,
        match="dispatch_intent_invalid|dispatch_readback_mismatch",
    ):
        await owner.bind_intent(slot, changed)
    assert len(reader.calls) <= 1
    assert owner.inspect(slot).state == "denied"


async def test_successful_record_cannot_be_replaced_during_readback(
    monkeypatch, example
):
    fixture, record = example
    owner, reader, clock, _ = setup_owner(monkeypatch, example)
    slot = owner.begin(fixture.request)
    clock.after_commit(fixture)
    body = json.loads(record.canonical_json)
    body["take_profit"] = "1"
    reader.result = repin(body)
    with pytest.raises(DispatchOwnershipError, match="dispatch_readback_mismatch"):
        await owner.bind_intent(slot, record)
    assert len(reader.calls) == 1
    assert owner.inspect(slot).state == "denied"


async def test_caller_mutation_after_begin_does_not_change_frozen_request(
    monkeypatch, example
):
    fixture, record = example
    owner, _, clock, _ = setup_owner(monkeypatch, example)
    # Existing canonical revalidation copies nested MappingProxyType values;
    # Pydantic's general-purpose deepcopy cannot pickle those immutable maps.
    external = checked(fixture.request, ReservationRequest)
    slot = owner.begin(external)
    object.__setattr__(external.origin.candidate, "take_profit", 1)
    clock.after_commit(fixture)
    assert (await owner.bind_intent(slot, record)).state == "bound_denied"


@pytest.mark.parametrize("operation", (copy.copy, copy.deepcopy, pickle.dumps))
def test_copy_and_pickle_owner_or_slot_rejected(monkeypatch, example, operation):
    fixture, _ = example
    owner, _, _, _ = setup_owner(monkeypatch, example)
    slot = owner.begin(fixture.request)
    for target in (owner, slot):
        with pytest.raises(DispatchOwnershipError, match="not_transferable"):
            operation(target)


def test_forged_slot_and_diagnostic_are_not_registry_members(monkeypatch, example):
    fixture, _ = example
    owner, _, _, _ = setup_owner(monkeypatch, example)
    owner.begin(fixture.request)
    with pytest.raises(DispatchOwnershipError, match="owned_creation_required"):
        DispatchSlot()
    for item in (
        object.__new__(DispatchSlot),
        DispatchObservation("READY", "passed", "f" * 64),
        {"passed": True},
    ):
        with pytest.raises(DispatchOwnershipError, match="dispatch_slot_not_owned"):
            owner.require_ready(item)


def test_new_owner_cannot_adopt_slot_or_copy_from_restart(monkeypatch, example):
    fixture, _ = example
    owner, _, clock, repository = setup_owner(monkeypatch, example)
    slot = owner.begin(fixture.request)
    new = DispatchOwnership(repository, fixture.request.scope, clock=lambda: clock.now)
    with pytest.raises(DispatchOwnershipError, match="dispatch_slot_not_owned"):
        new.require_ready(slot)
    with pytest.raises(DispatchOwnershipError, match="dispatch_slot_not_owned"):
        new.inspect(slot)


def test_process_context_change_revokes_inherited_registry(monkeypatch, example):
    fixture, _ = example
    owner, _, _, _ = setup_owner(monkeypatch, example)
    slot = owner.begin(fixture.request)
    old_pid = ownership.os.getpid()
    monkeypatch.setattr(ownership.os, "getpid", lambda: old_pid + 1)
    with pytest.raises(
        DispatchOwnershipError, match="dispatch_process_context_changed"
    ):
        owner.require_ready(slot)
    monkeypatch.setattr(ownership.os, "getpid", lambda: old_pid)
    assert owner.inspect(slot).state == "revoked"
    with pytest.raises(DispatchOwnershipError, match="dispatch_owner_revoked"):
        owner.begin(fixture.request)


def test_foreign_input_does_not_execute_hash_eq_or_class_callbacks(
    monkeypatch, example
):
    fixture, _ = example
    owner, _, _, _ = setup_owner(monkeypatch, example)
    owner.begin(fixture.request)
    calls = []

    class Foreign:
        @property
        def __class__(self):
            calls.append("class")
            raise AssertionError

        def __hash__(self):
            calls.append("hash")
            raise AssertionError

        def __eq__(self, other):
            calls.append("eq")
            raise AssertionError

    for operation in (owner.begin, owner.inspect, owner.require_ready):
        with pytest.raises(DispatchOwnershipError):
            operation(Foreign())
    assert calls == []


def test_cross_uid_and_live_scopes_denied(monkeypatch, example):
    fixture, _ = example
    owner, _, _, repository = setup_owner(monkeypatch, example)
    other = fixture.request.model_copy(
        update={"scope": LedgerScope(account_id="9999", settlement_currency="USDT")}
    )
    with pytest.raises(DispatchOwnershipError):
        owner.begin(other)
    forged_live = fixture.request.scope.model_copy(update={"environment": "live"})
    with pytest.raises(DispatchOwnershipError, match="dispatch_scope_invalid"):
        DispatchOwnership(repository, forged_live)


@pytest.mark.parametrize(
    "bad_clock",
    (datetime.now(UTC).replace(tzinfo=None), datetime.now(UTC).isoformat(), None),
)
def test_invalid_clock_denies_and_revokes(monkeypatch, example, bad_clock):
    fixture, _ = example
    owner, _, clock, _ = setup_owner(monkeypatch, example)
    clock.now = bad_clock
    with pytest.raises(DispatchOwnershipError, match="dispatch_clock_invalid"):
        owner.begin(fixture.request)
    clock.now = fixture.now
    with pytest.raises(DispatchOwnershipError, match="dispatch_owner_revoked"):
        owner.begin(fixture.request)


def test_production_owner_rejects_arbitrary_reader_as_issuer(example):
    fixture, record = example
    with pytest.raises(
        DispatchOwnershipError, match="dispatch_exact_repository_required"
    ):
        DispatchOwnership(Reader(record), fixture.request.scope)


async def test_individually_valid_other_v2_cannot_replace_expected_readback(
    monkeypatch, examples, hedged_record
):
    fixture, record = examples["long"]
    owner, reader, clock, _ = setup_owner(monkeypatch, examples["long"])
    slot = owner.begin(fixture.request)
    clock.after_commit(fixture)
    reader.result = hedged_record
    assert hedged_record != record
    with pytest.raises(DispatchOwnershipError, match="dispatch_readback_mismatch"):
        await owner.bind_intent(slot, record)
    assert len(reader.calls) == 1
    assert owner.inspect(slot).state == "denied"


def test_renamed_report_does_not_release_same_event(
    monkeypatch, examples, renamed_request
):
    fixture, _ = examples["long"]
    assert (
        renamed_request.origin.original_event_key
        == fixture.request.origin.original_event_key
    )
    assert (
        renamed_request.origin.candidate.report_id
        != fixture.request.origin.candidate.report_id
    )
    owner, _, _, _ = setup_owner(monkeypatch, examples["long"])
    owner.cancel(owner.begin(fixture.request))
    with pytest.raises(DispatchOwnershipError, match="dispatch_event_already_owned"):
        owner.begin(renamed_request)


async def test_instance_read_callback_cannot_replace_repository_path(
    monkeypatch, examples
):
    fixture, record = examples["long"]
    owner, reader, clock, repository = setup_owner(monkeypatch, examples["long"])
    calls = []

    async def foreign(*args, **kwargs):
        calls.append("instance_callback")
        return record

    repository.read_submission_intent = foreign
    slot = owner.begin(fixture.request)
    clock.after_commit(fixture)
    assert (await owner.bind_intent(slot, record)).state == "bound_denied"
    assert len(reader.calls) == 1
    assert calls == []


async def test_bound_diagnostic_is_not_accepted_by_real_entry_boundary(
    monkeypatch, examples
):
    from app.exchange.okx.errors import OkxPrivateApiError
    from app.exchange.okx.private_rest import OkxDemoPrivateRestClient
    from tests.unit.exchange.test_private_rest import demo_settings

    fixture, record = examples["long"]
    owner, _, clock, _ = setup_owner(monkeypatch, examples["long"])
    slot = owner.begin(fixture.request)
    clock.after_commit(fixture)
    result = await owner.bind_intent(slot, record)
    credentials = []

    class NoCredentialRead(OkxDemoPrivateRestClient):
        def _credentials(self):
            credentials.append(True)
            raise AssertionError("D0 must not reach credential access")

    client = NoCredentialRead(settings=demo_settings())
    for item in (slot, result, record, {"passed": True}):
        with pytest.raises(OkxPrivateApiError) as caught:
            await client.place_order({"dispatch_ownership": item})
        assert caught.value.code == "demo_qualification_authority_unavailable"
    assert credentials == []


async def test_cancel_before_first_task_step_tombstones_attempt(monkeypatch, examples):
    fixture, record = examples["long"]
    owner, reader, clock, _ = setup_owner(monkeypatch, examples["long"])
    slot = owner.begin(fixture.request)
    clock.after_commit(fixture)
    attempt = asyncio.ensure_future(owner.bind_intent(slot, record))
    attempt.cancel()
    with pytest.raises(asyncio.CancelledError):
        await attempt
    await asyncio.sleep(0)
    assert reader.calls == []
    assert owner.inspect(slot).state == "cancelled"
    with pytest.raises(DispatchOwnershipError):
        await owner.bind_intent(slot, record)


async def test_unawaited_failure_is_owned_and_never_logged(monkeypatch, examples):
    fixture, record = examples["long"]
    owner, reader, clock, _ = setup_owner(monkeypatch, examples["long"])
    slot = owner.begin(fixture.request)
    clock.after_commit(fixture)
    reader.failure = RuntimeError("synthetic-private-db-message")
    loop = asyncio.get_running_loop()
    previous = loop.get_exception_handler()
    observed = []
    loop.set_exception_handler(lambda loop, context: observed.append(context))
    try:
        # Intentionally discard the returned Task. Owner holds it until its
        # completion callback has retrieved the sanitized error and tombstoned.
        owner.bind_intent(slot, record)
        await reader.started.wait()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        gc.collect()
        await asyncio.sleep(0)
        assert owner.inspect(slot).state == "denied"
        assert observed == []
        assert len(reader.calls) == 1
    finally:
        loop.set_exception_handler(previous)


@pytest.mark.parametrize("error_type", (RuntimeError, DispatchOwnershipError))
async def test_task_creation_failure_closes_work_and_tombstones(
    monkeypatch, examples, error_type
):
    fixture, record = examples["long"]
    owner, reader, clock, _ = setup_owner(monkeypatch, examples["long"])
    slot = owner.begin(fixture.request)
    clock.after_commit(fixture)
    loop = asyncio.get_running_loop()
    previous = loop.get_task_factory()

    def fail(loop, coro, **kwargs):
        raise error_type("synthetic-private-task-failure")

    loop.set_task_factory(fail)
    try:
        with pytest.raises(
            DispatchOwnershipError, match="^dispatch_task_creation_failed$"
        ):
            owner.bind_intent(slot, record)
    finally:
        loop.set_task_factory(previous)
    assert reader.calls == []
    assert owner.inspect(slot).state == "denied"
    with pytest.raises(DispatchOwnershipError):
        owner.bind_intent(slot, record)


async def test_swallowed_dependency_cancellation_cannot_bind(monkeypatch, examples):
    fixture, record = examples["long"]
    owner, reader, clock, _ = setup_owner(monkeypatch, examples["long"])
    slot = owner.begin(fixture.request)
    clock.after_commit(fixture)
    reader.release.clear()
    reader.suppress_cancel = True
    task = owner.bind_intent(slot, record)
    await reader.started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert owner.inspect(slot).state == "cancelled"
    assert len(reader.calls) == 1
