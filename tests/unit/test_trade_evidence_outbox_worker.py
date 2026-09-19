"""Synthetic single-pass protocol proof with separately scoped native IO tests.

MemoryBackend replaces only the outbox root context. The real outbox transition,
hash-chain, recovery and dispatch APIs are used throughout successful workflows.
Native journal tests run on the actual platform; no ACL changes or runtime startup.
"""

import asyncio
import os
import time
from datetime import timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError, create_model

from app.trade_evidence import outbox
from app.trade_evidence import outbox_worker as module
from tests.unit.test_trade_evidence_outbox import (
    NOW,
    Clock,
    MemoryBackend,
    outcome,
    payload,
)

REPORT = "worker-report-a"
SECOND = "worker-report-b"


@pytest.fixture
def memory(monkeypatch):
    backend = MemoryBackend()
    monkeypatch.setattr(outbox, "_root_context", backend.context)
    return backend


@pytest.fixture
def clock():
    return Clock()


def seed(memory, clock, report_id=REPORT, **kwargs):
    return outbox.enqueue(
        memory.root, payload(report_id=report_id), clock=clock, **kwargs
    )


def begin(memory, clock, report_id=REPORT):
    token = outbox.claim_job(
        memory.root, report_id, worker_id="prior-worker", clock=clock
    )
    return outbox.begin_dispatch(memory.root, token, clock=clock)


async def delivered(value, token):
    return outcome(token)


async def run(memory, clock, **updates):
    values = {
        "report_ids": (REPORT,),
        "worker_id": "synthetic-worker",
        "adapter": delivered,
        "policy": module.OutboxWorkerPolicy(),
        "clock": clock,
    }
    return await module.run_outbox_pass(memory.root, **(values | updates))


@pytest.mark.asyncio
async def test_real_outbox_dispatches_ordered_explicit_jobs_once_after_durable_marker(
    memory, clock
):
    seed(memory, clock)
    seed(memory, clock, SECOND)
    unlisted = "unlisted-report"
    seed(memory, clock, unlisted)
    before = dict(memory.files)
    memory.operations.clear()
    calls = []

    async def adapter(value, token):
        assert memory.active_contexts == 0
        assert token.report_id == value.report_id
        marker = (f"{value.report_id}.state", "00000003.json")
        assert ("publish", marker) in memory.operations
        assert ("read", marker) in memory.operations
        calls.append(value.report_id)
        return outcome(token)

    result = await run(memory, clock, report_ids=(SECOND, REPORT), adapter=adapter)
    assert calls == [SECOND, REPORT]
    assert result.status == "completed"
    assert tuple(item.report_id for item in result.items) == (SECOND, REPORT)
    assert all(
        item.status == "processed"
        and item.outbox_status == "delivered"
        and item.attempts == 1
        for item in result.items
    )
    assert not any(
        operation == "read" and path[0].startswith(unlisted)
        for operation, path in memory.operations
    )
    assert all(memory.files[path] == data for path, data in before.items())
    assert result.execution_authority is result.source_authenticity_verified is False
    assert all(
        item.execution_authority is item.source_authenticity_verified is False
        for item in result.items
    )
    assert not hasattr(result, "passed")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state",
    ["delivered", "exhausted", "uncertain", "claimed", "dispatching", "retry_wait"],
)
async def test_terminal_unknown_live_lease_and_not_due_jobs_skip_without_adapter(
    memory, clock, state
):
    seed(memory, clock)
    if state == "claimed":
        outbox.claim_job(memory.root, REPORT, worker_id="prior-worker", clock=clock)
    else:
        token = begin(memory, clock)
        if state != "dispatching":
            remote = {
                "exhausted": "not_created_final",
                "retry_wait": "not_created_retryable",
            }.get(state, state)
            outbox.finish_dispatch(
                memory.root, token, outcome(token, remote), clock=clock
            )
    before = dict(memory.files)

    async def never(value, token):
        pytest.fail("ineligible job must never invoke adapter")

    result = await run(memory, clock, adapter=never)
    assert result.items[0].status == "skipped"
    assert result.items[0].outbox_status == state
    assert memory.files == before


@pytest.mark.asyncio
async def test_expired_claim_is_recovered_then_one_new_fenced_attempt(memory, clock):
    seed(memory, clock)
    old = outbox.claim_job(memory.root, REPORT, worker_id="prior-worker", clock=clock)
    clock.now = old.lease_expires_at
    fences = []

    async def adapter(value, token):
        fences.append(token.fence_token)
        return outcome(token)

    result = await run(memory, clock, adapter=adapter)
    assert result.items[0].outbox_status == "delivered"
    assert fences and fences[0] != old.fence_token
    assert result.items[0].attempts == 1


@pytest.mark.asyncio
async def test_expired_dispatch_is_recovered_to_uncertain_not_resent(memory, clock):
    seed(memory, clock)
    token = begin(memory, clock)
    clock.now = token.lease_expires_at

    async def never(value, token):
        pytest.fail("expired dispatched work must not be retried")

    result = await run(memory, clock, adapter=never)
    assert result.items[0].status == "skipped"
    assert result.items[0].code == "outbox_uncertain"
    assert outbox.read_job(memory.root, REPORT, clock=clock).head.action == "recover"


@pytest.mark.asyncio
async def test_retry_due_boundary_permits_one_attempt_but_does_not_loop(memory, clock):
    seed(memory, clock)
    token = begin(memory, clock)
    first = outbox.finish_dispatch(
        memory.root, token, outcome(token, "not_created_retryable"), clock=clock
    )
    clock.now = first.head.next_attempt_at
    calls = []

    async def adapter(value, token):
        calls.append(token)
        return outcome(token, "not_created_retryable")

    result = await run(memory, clock, adapter=adapter)
    assert len(calls) == 1 and result.items[0].outbox_status == "retry_wait"
    assert result.items[0].attempts == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "report_ids",
    [
        (),
        [],
        [REPORT],
        (REPORT, REPORT),
        (REPORT, REPORT.upper()),
        ("../escape",),
        ("CON",),
        ("NUL",),
        ("a.json",),
        ("a/b",),
        ("a\\b",),
        ("中文",),
        (" a",),
        ("a ",),
        ("a\n",),
        (True,),
        (b"abc",),
        ("x" * 97,),
        tuple(f"report-{i}" for i in range(17)),
    ],
)
async def test_report_id_contract_rejects_before_storage_or_clock(
    memory, clock, report_ids
):
    with pytest.raises(module.OutboxWorkerError, match="^outbox_worker_input_invalid$"):
        await run(memory, clock, report_ids=report_ids)
    assert memory.operations == [] and clock.calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "worker_id", [None, True, "", " x", "x\n", "../worker", "x" * 65, "中文"]
)
async def test_invalid_worker_id_is_pre_io(memory, clock, worker_id):
    with pytest.raises(module.OutboxWorkerError):
        await run(memory, clock, worker_id=worker_id)
    assert not memory.operations and not clock.calls


@pytest.mark.parametrize("value", [True, False, 0, -1, 301, "1", 1.0, None])
def test_policy_timeout_is_bounded_exact_integer(value):
    with pytest.raises(ValidationError):
        module.OutboxWorkerPolicy(pass_timeout_seconds=value)


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["extra", "private", "subclass", "invalid_value"])
async def test_dirty_policy_rejects_before_io(memory, clock, mutation):
    value = module.OutboxWorkerPolicy()
    if mutation == "extra":
        value = value.model_copy(update={"hidden": True})
    elif mutation == "private":
        object.__setattr__(value, "__pydantic_private__", {})
    elif mutation == "subclass":
        value = create_model(
            "HiddenPolicy", hidden=(str, "secret"), __base__=module.OutboxWorkerPolicy
        )()
    else:
        value = value.model_copy(update={"pass_timeout_seconds": 0.01})
    with pytest.raises(module.OutboxWorkerError):
        await run(memory, clock, policy=value)
    assert not memory.operations and not clock.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("cleanup", ["reraise", "raise_other", "return_delivery"])
async def test_total_deadline_retains_unknown_and_never_starts_the_next_report(
    memory, clock, cleanup
):
    seed(memory, clock)
    seed(memory, clock, SECOND)
    before_second = {
        path: data for path, data in memory.files.items() if path[0].startswith(SECOND)
    }
    calls = []

    async def adapter(value, token):
        calls.append(value.report_id)
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            if cleanup == "raise_other":
                raise RuntimeError("synthetic secret must not appear") from None
            if cleanup == "return_delivery":
                return outcome(token)
            raise

    result = await asyncio.wait_for(
        run(
            memory,
            clock,
            report_ids=(REPORT, SECOND),
            adapter=adapter,
            policy=module.OutboxWorkerPolicy(pass_timeout_seconds=1),
        ),
        timeout=2,
    )
    assert calls == [REPORT]
    assert result.status == "deadline_reached"
    assert (
        result.items[0].status == "failed"
        and result.items[0].code == "outbox_pass_deadline"
    )
    assert result.items[1].status == "not_started"
    assert outbox.read_job(memory.root, REPORT, clock=clock).status == "uncertain"
    assert all(memory.files[path] == data for path, data in before_second.items())
    assert "secret" not in result.model_dump_json()


@pytest.mark.asyncio
@pytest.mark.parametrize("cleanup", ["reraise", "raise_other", "return_delivery"])
async def test_external_cancellation_is_preserved_with_unknown_durable_and_no_later_job(
    memory, clock, cleanup
):
    seed(memory, clock)
    seed(memory, clock, SECOND)
    entered = asyncio.Event()
    calls = []

    async def adapter(value, token):
        calls.append(value.report_id)
        entered.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            if cleanup == "raise_other":
                raise RuntimeError("synthetic cleanup failure") from None
            if cleanup == "return_delivery":
                return outcome(token)
            raise

    task = asyncio.create_task(
        run(memory, clock, report_ids=(REPORT, SECOND), adapter=adapter)
    )
    await asyncio.wait_for(entered.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.cancelled() and calls == [REPORT]
    assert outbox.read_job(memory.root, REPORT, clock=clock).status == "uncertain"
    assert outbox.read_job(memory.root, SECOND, clock=clock).status == "queued"


@pytest.mark.asyncio
async def test_already_cancelling_worker_does_no_clock_or_storage_work(memory, clock):
    async def cancelling():
        asyncio.current_task().cancel()
        await run(memory, clock)

    task = asyncio.create_task(cancelling())
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not memory.operations and not clock.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["raises", "returns_invalid", "foreign_outcome"])
async def test_unknown_adapter_result_does_not_retry_this_report_but_can_process_next(
    memory, clock, failure
):
    seed(memory, clock)
    seed(memory, clock, SECOND)
    calls = []

    async def adapter(value, token):
        calls.append(value.report_id)
        if value.report_id == SECOND:
            return outcome(token)
        if failure == "raises":
            raise RuntimeError("synthetic private failure text")
        if failure == "returns_invalid":
            return {"untrusted": "synthetic private failure text"}
        return outcome(token, report_id=SECOND)

    result = await run(memory, clock, report_ids=(REPORT, SECOND), adapter=adapter)
    assert calls == [REPORT, SECOND]
    assert result.items[0].outbox_status == "uncertain"
    assert result.items[1].outbox_status == "delivered"
    assert "private failure" not in result.model_dump_json()


@pytest.mark.asyncio
async def test_missing_job_fails_statically_without_blocking_next_explicit_job(
    memory, clock
):
    seed(memory, clock, SECOND)
    result = await run(memory, clock, report_ids=(REPORT, SECOND))
    assert result.items[0].status == "failed"
    assert result.items[0].code == "outbox_storage_unavailable"
    assert result.items[0].attempts is None
    assert result.items[1].outbox_status == "delivered"


@pytest.mark.asyncio
@pytest.mark.parametrize("error_code", ["secret-user-value", ["unhashable"], None])
async def test_unknown_exception_codes_are_not_serialized_or_used_as_result_codes(
    memory, clock, monkeypatch, error_code
):
    def broken(*args, **kwargs):
        raise outbox.OutboxError(error_code)

    monkeypatch.setattr(outbox, "recover_job", broken)
    result = await run(memory, clock)
    assert result.items[0].code == "outbox_job_failed"
    assert "secret-user-value" not in result.model_dump_json()


@pytest.mark.asyncio
@pytest.mark.parametrize("dirty", ["foreign", "extra", "private", "subclass"])
async def test_recovery_result_is_revalidated_before_dispatch(
    memory, clock, monkeypatch, dirty
):
    view = seed(memory, clock, SECOND if dirty == "foreign" else REPORT)
    if dirty == "extra":
        view = view.model_copy(update={"hidden": True})
    elif dirty == "private":
        object.__setattr__(view.envelope.payload, "__pydantic_private__", {})
    elif dirty == "subclass":
        view = create_model(
            "HiddenView", hidden=(str, "secret"), __base__=outbox.OutboxView
        )(**view.model_dump())
    monkeypatch.setattr(outbox, "recover_job", lambda *args, **kwargs: view)

    async def never(value, token):
        pytest.fail("unvalidated source must not dispatch")

    result = await run(memory, clock, adapter=never)
    assert result.items[0].code == "outbox_view_invalid"


@pytest.mark.asyncio
async def test_result_is_frozen_nested_strict_and_json_roundtrippable(memory, clock):
    seed(memory, clock)
    result = await run(memory, clock)
    assert module.OutboxPassResult.model_validate(result, strict=True) == result
    assert (
        module.OutboxPassResult.model_validate_json(
            result.model_dump_json(), strict=True
        )
        == result
    )
    with pytest.raises(ValidationError):
        result.status = "deadline_reached"
    with pytest.raises(ValidationError):
        result.items[0].attempts = 9
    with pytest.raises(ValidationError):
        result.policy.pass_timeout_seconds = 1


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["started_at", "completed_at", "items"])
async def test_json_roundtrip_does_not_loosen_python_datetime_or_tuple_inputs(
    memory, clock, field
):
    seed(memory, clock)
    result = await run(memory, clock)
    values = result.model_dump()
    values[field] = (
        list(values[field]) if field == "items" else values[field].isoformat()
    )
    with pytest.raises(ValueError):
        module.OutboxPassResult.model_validate(values, strict=True)


@pytest.mark.asyncio
async def test_deadline_after_synchronous_read_stops_before_dispatch_even_without_timer_callback(
    memory, clock, monkeypatch
):
    seed(memory, clock)
    seed(memory, clock, SECOND)
    real_recover = outbox.recover_job
    reads = []

    def slow_recover(root, report_id, **kwargs):
        reads.append(report_id)
        view = real_recover(root, report_id, **kwargs)
        time.sleep(1.05)  # The documented non-preemptible synchronous boundary.
        return view

    async def never(value, token):
        pytest.fail("deadline elapsed during recovery; no remote attempt allowed")

    monkeypatch.setattr(outbox, "recover_job", slow_recover)
    result = await run(
        memory,
        clock,
        report_ids=(REPORT, SECOND),
        adapter=never,
        policy=module.OutboxWorkerPolicy(pass_timeout_seconds=1),
    )
    assert result.status == "deadline_reached" and reads == [REPORT]
    assert result.items[0].code == "outbox_pass_deadline"
    assert result.items[1].status == "not_started"
    assert outbox.read_job(memory.root, REPORT, clock=clock).status == "queued"


@pytest.mark.asyncio
async def test_invalid_midpass_clock_is_terminal_even_if_provider_then_recovers(
    memory, clock
):
    seed(memory, clock)
    seed(memory, clock, SECOND)
    readings = iter((NOW, NOW - timedelta(seconds=1), NOW, NOW, NOW))
    before = dict(memory.files)
    memory.operations.clear()
    with pytest.raises(module.OutboxWorkerError, match="^outbox_worker_clock_invalid$"):
        await run(memory, lambda: next(readings), report_ids=(REPORT, SECOND))
    assert memory.files == before
    assert not any(
        operation == "read" and path[0].startswith(SECOND)
        for operation, path in memory.operations
    )


@pytest.mark.asyncio
async def test_maximum_sixteen_explicit_jobs_are_processed_once(memory, clock):
    report_ids = tuple(f"bounded-report-{index}" for index in range(16))
    for report_id in report_ids:
        seed(memory, clock, report_id)
    result = await run(memory, clock, report_ids=report_ids)
    assert len(result.items) == 16
    assert all(
        item.outbox_status == "delivered" and item.attempts == 1
        for item in result.items
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("location", ["result", "policy", "item"])
@pytest.mark.parametrize("mutation", ["extra", "private", "subclass"])
async def test_nested_result_tamper_and_declared_subclass_fail_strict_reconstruction(
    memory, clock, location, mutation
):
    seed(memory, clock)
    result = await run(memory, clock)
    target = {"result": result, "policy": result.policy, "item": result.items[0]}[
        location
    ]
    if mutation == "extra":
        target = target.model_copy(update={"hidden": True})
    elif mutation == "private":
        object.__setattr__(target, "__pydantic_private__", {})
    else:
        kind = create_model(
            "DeclaredHidden", hidden=(str, "secret"), __base__=type(target)
        )
        target = kind(**target.model_dump())
    if location == "result":
        result = target
    else:
        result = result.model_copy(
            update={"policy": target} if location == "policy" else {"items": (target,)}
        )
    with pytest.raises(ValueError):
        module.OutboxPassResult.model_validate(result, strict=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("location", ["result", "item"])
@pytest.mark.parametrize(
    "flag", ["execution_authority", "source_authenticity_verified"]
)
@pytest.mark.parametrize("value", [True, 0, "false"])
async def test_false_authority_fields_do_not_accept_zero_or_coercion(
    memory, clock, location, flag, value
):
    seed(memory, clock)
    result = await run(memory, clock)
    if location == "result":
        result = result.model_copy(update={flag: value})
    else:
        result = result.model_copy(
            update={"items": (result.items[0].model_copy(update={flag: value}),)}
        )
    with pytest.raises(ValueError):
        module.OutboxPassResult.model_validate(result, strict=True)


@pytest.mark.asyncio
async def test_clock_utc_normalization_and_no_wall_clock_claims_from_timeout(
    memory, clock
):
    seed(memory, clock)
    clock.now = NOW.astimezone(timezone(timedelta(hours=8)))
    result = await run(memory, clock)
    assert result.started_at == result.completed_at == NOW
    assert result.started_at.utcoffset() == timedelta(0)


@pytest.mark.asyncio
@pytest.mark.parametrize("at", [NOW.replace(tzinfo=None), True, NOW.isoformat()])
async def test_invalid_clock_never_touches_storage(memory, at):
    with pytest.raises(module.OutboxWorkerError, match="^outbox_worker_clock_invalid$"):
        await run(memory, lambda: at)
    assert not memory.operations


@pytest.mark.asyncio
async def test_relative_or_filesystem_root_rejects_before_io(memory, clock):
    for root in (Path("relative"), Path(Path.cwd().anchor)):
        with pytest.raises(module.OutboxWorkerError):
            await module.run_outbox_pass(
                root,
                report_ids=(REPORT,),
                worker_id="worker",
                adapter=delivered,
                policy=module.OutboxWorkerPolicy(),
                clock=clock,
            )
    assert not memory.operations


@pytest.mark.asyncio
async def test_native_worker_uses_real_journal_and_readback(tmp_path, clock):
    root = tmp_path / "trusted-native-worker"
    root.mkdir()
    outbox.enqueue(root, payload(report_id=REPORT), clock=clock)
    result = await module.run_outbox_pass(
        root,
        report_ids=(REPORT,),
        worker_id="worker",
        adapter=delivered,
        policy=module.OutboxWorkerPolicy(),
        clock=clock,
    )
    assert result.items[0].outbox_status == "delivered"
    view = outbox.read_job(root, REPORT, clock=clock)
    assert len(view.events) == 4 and view.status == "delivered"
    assert len(list((root / f"{REPORT}.state").iterdir())) == 4


@pytest.mark.asyncio
@pytest.mark.skipif(
    os.name != "nt",
    reason="Records real Windows ancestor-pin behavior, without changing ACLs",
)
async def test_native_windows_worker_denial_or_real_empty_root_is_fail_closed(
    tmp_path, clock
):
    root = tmp_path / "trusted-native-worker"
    root.mkdir()

    async def never(value, token):
        pytest.fail("no job was enqueued and no adapter may run")

    result = await module.run_outbox_pass(
        root,
        report_ids=(REPORT,),
        worker_id="worker",
        adapter=never,
        policy=module.OutboxWorkerPolicy(),
        clock=clock,
    )
    item = result.items[0]
    assert item.status == "failed" and item.outbox_status is None
    assert item.code in {
        "outbox_storage_permission_denied",
        "outbox_storage_unavailable",
    }
    assert list(root.iterdir()) == []
    print(
        f"Native Windows worker: {item.code}; no adapter or publication success claimed"
    )
