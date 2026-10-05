"""Synthetic native samples/private mechanism fixtures; no host or account IO."""

import asyncio
import copy
import pickle
from datetime import UTC, datetime

import pytest

from app.domain.source_primitives import utc_from_ns
from app.trade_qualification import account_capture_journal as journal
from app.trade_qualification import account_native_clock as native
from tests.unit.test_qualification_account_capture import NOW


class Samples:
    def __init__(self):
        epoch = datetime(1970, 1, 1, tzinfo=UTC)
        delta = NOW - epoch
        self.wall = (delta.days * 86400 + delta.seconds) * 1_000_000_000
        self.calls = 0

    def __call__(self):
        self.calls += 1
        offset = self.calls * 1_000_000
        return {"utc_ns": self.wall + offset, "monotonic_ns": offset}


def synthetic_phase_binding(stage):
    """Explicit test-only healthy-stage marker; does not acquire host admission."""
    state = native._state(stage)
    state["clock_results"]["before"] = "accepted"
    owner = object.__new__(journal._OwnedAccountJournal)
    owner.closed = False
    native._bind_collector(state["observer"], state["clock"], "a" * 64, owner)
    return state, owner


def emit(state, phase, *, index=0, stream="balance", page=0):
    return native._phase(
        state["observer"],
        state["clock"],
        phase=phase,
        request_index=index,
        stream=stream,
        page_index=page,
    )


@pytest.mark.asyncio
async def test_actual_explicit_phases_do_not_depend_on_number_of_clock_calls(
    monkeypatch,
):
    samples = Samples()
    monkeypatch.setattr(native.clock, "native_stamp", samples)
    with native._initial_stage(plan_sha256="a" * 64, scope_sha256="b" * 64) as stage:
        state, _ = synthetic_phase_binding(stage)
        for _ in range(17):
            state["clock"]()
        assert not state["witnesses"]
        for phase in native.PAGE_PHASES:
            observed = emit(state, phase)
            assert observed == utc_from_ns(state["witnesses"][-1]["stamp"]["utc_ns"])
        emit(state, "source_closed", index=1, stream="account_source")
        assert [item["phase"] for item in state["witnesses"]] == [
            *native.PAGE_PHASES,
            "source_closed",
        ]
        assert state["closed"]
        with pytest.raises(native.NativeAccountClockError):
            emit(state, "request_start", index=1)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation",
    ["skip", "repeat", "index", "stream", "page", "premature_close", "foreign_clock"],
)
async def test_phase_inventory_rejects_missing_duplicate_or_rebound_source(
    monkeypatch, mutation
):
    monkeypatch.setattr(native.clock, "native_stamp", Samples())
    with native._initial_stage(plan_sha256="a" * 64, scope_sha256="b" * 64) as stage:
        state, _ = synthetic_phase_binding(stage)
        emit(state, "request_start")
        before = len(state["witnesses"])
        with pytest.raises(native.NativeAccountClockError):
            if mutation == "foreign_clock":
                native._phase(
                    state["observer"],
                    lambda: NOW,
                    phase="request_dispatch",
                    request_index=0,
                    stream="balance",
                    page_index=0,
                )
            else:
                emit(
                    state,
                    "headers_received"
                    if mutation == "skip"
                    else "request_start"
                    if mutation == "repeat"
                    else "source_closed"
                    if mutation == "premature_close"
                    else "request_dispatch",
                    index=int(mutation == "index"),
                    stream="positions" if mutation == "stream" else "balance",
                    page=int(mutation == "page"),
                )
        assert len(state["witnesses"]) == before


@pytest.mark.asyncio
async def test_foreign_task_cannot_observe_or_borrow_clock_finalizer_registration(
    monkeypatch,
):
    monkeypatch.setattr(native.clock, "native_stamp", Samples())
    with native._initial_stage(plan_sha256="a" * 64, scope_sha256="b" * 64) as stage:
        state, owner = synthetic_phase_binding(stage)

        async def foreign():
            with pytest.raises(native.NativeAccountClockError):
                state["clock"]()
            with pytest.raises(native.NativeAccountClockError):
                emit(state, "request_start")

        await asyncio.create_task(foreign())

        async def caller_coroutine():
            raise AssertionError("caller coroutine must never run")

        with pytest.raises(native.NativeAccountClockError):
            native._finalization_awaitable(state["observer"], owner, caller_coroutine())
        assert not state["finalizers"] and not state["finalization_witnesses"]
        await asyncio.create_task(foreign())


@pytest.mark.asyncio
async def test_actual_other_thread_and_loop_cannot_borrow_phase(monkeypatch):
    monkeypatch.setattr(native.clock, "native_stamp", Samples())
    with native._initial_stage(plan_sha256="a" * 64, scope_sha256="b" * 64) as stage:
        state, _ = synthetic_phase_binding(stage)

        async def foreign():
            with pytest.raises(native.NativeAccountClockError):
                emit(state, "request_start")

        await asyncio.to_thread(lambda: asyncio.run(foreign()))
        assert not state["witnesses"]


@pytest.mark.asyncio
@pytest.mark.parametrize("input_value", [None, {}, lambda: NOW])
async def test_callback_or_clockproof_dto_cannot_bind_native_observer(
    monkeypatch, input_value
):
    monkeypatch.setattr(native.clock, "native_stamp", Samples())
    with pytest.raises(native.NativeAccountClockError):
        native._bind_collector(input_value, lambda: NOW, "a" * 64, None)


@pytest.mark.asyncio
async def test_finalizer_failure_revokes_child_and_records_no_complete_witness(
    monkeypatch,
):
    monkeypatch.setattr(native.clock, "native_stamp", Samples())
    with native._initial_stage(plan_sha256="a" * 64, scope_sha256="b" * 64) as stage:
        state, owner = synthetic_phase_binding(stage)

        # Exact original B1 coroutine, deliberately incomplete journal state.
        # It samples the registered child's UTC then fails before persistence.
        owner.clock = state["clock"]
        owner.finalization_complete = False
        owner.pages = []
        with pytest.raises(AttributeError):
            await journal.bounded_finalization(
                native._finalization_awaitable(state["observer"], owner, owner.finish())
            )
        assert not state["finalizers"] and not state["finalization_witnesses"]


@pytest.mark.parametrize("cls", [native._InitialAccountStage, native._PhaseObserver])
def test_private_stage_and_phase_observer_are_not_portable(cls):
    with pytest.raises(native.NativeAccountClockError):
        cls()
    value = object.__new__(cls)
    for action in (copy.copy, copy.deepcopy, pickle.dumps):
        with pytest.raises(native.NativeAccountClockError):
            action(value)
