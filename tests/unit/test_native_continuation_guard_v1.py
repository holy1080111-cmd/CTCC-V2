"""Real source/code/namespace/closure fault checks; no native IO acceptance."""

import asyncio
import gc
from types import FunctionType
from weakref import ref

import pytest

from app.trade_qualification import native_continuation_guard_v1 as guard
from tests.unit import native_guard_external_fixture_v1 as external
from tests.unit import native_guard_fixture_v1 as fixture

ORIGINAL_ENTRY = fixture.entry
ORIGINAL_ASYNC_ENTRY = fixture.async_entry


@pytest.fixture
def installed(monkeypatch):
    monkeypatch.setattr(fixture, "entry", ORIGINAL_ENTRY)
    monkeypatch.setattr(fixture, "async_entry", ORIGINAL_ASYNC_ENTRY)
    monkeypatch.setattr(fixture, "_NATIVE_GUARD", None, raising=False)
    fixture.closed.clear()
    guard.install_native_guards_v1(fixture, ("entry", "async_entry"))
    return fixture


def test_source_compiled_original_entry_still_computes(installed):
    assert installed.entry() == 7


def test_rebound_leaf_rejected_before_foreign_callback(installed, monkeypatch):
    called = []
    monkeypatch.setattr(installed, "leaf", lambda: called.append("foreign"))
    with pytest.raises(guard.NativeSourceClosureError):
        installed.entry()
    assert not called


def test_in_place_leaf_code_swap_rejected(installed, monkeypatch):
    monkeypatch.setattr(installed.leaf, "__code__", (lambda value=7: 99).__code__)
    with pytest.raises(guard.NativeSourceClosureError):
        installed.entry()


def test_defaults_cannot_change_eligibility_without_source_change(
    installed, monkeypatch
):
    monkeypatch.setattr(installed.leaf, "__defaults__", (99,))
    with pytest.raises(guard.NativeSourceClosureError):
        installed.entry()


def test_preexisting_foreign_namespace_clone_is_not_blessed(monkeypatch):
    foreign = dict(ORIGINAL_ENTRY.__globals__)
    foreign["leaf"] = lambda: 99
    clone = FunctionType(ORIGINAL_ENTRY.__code__, foreign, ORIGINAL_ENTRY.__name__)
    clone.__qualname__ = ORIGINAL_ENTRY.__qualname__
    monkeypatch.setattr(fixture, "entry", clone)
    monkeypatch.setattr(fixture, "_NATIVE_GUARD", None, raising=False)
    with pytest.raises(ValueError):
        guard.install_native_guards_v1(fixture, ("entry",))


def test_caller_generated_string_code_is_not_a_source_dataclass(monkeypatch):
    clone = FunctionType(
        ORIGINAL_ENTRY.__code__.replace(co_filename="<string>"),
        ORIGINAL_ENTRY.__globals__,
        ORIGINAL_ENTRY.__name__,
    )
    clone.__qualname__ = ORIGINAL_ENTRY.__qualname__
    monkeypatch.setattr(fixture, "entry", clone)
    monkeypatch.setattr(fixture, "_NATIVE_GUARD", None, raising=False)
    with pytest.raises(guard.NativeSourceClosureError):
        guard.install_native_guards_v1(fixture, ("entry",))


def test_guard_alias_replacement_does_not_replace_captured_guard(
    installed, monkeypatch
):
    monkeypatch.setattr(installed, "_NATIVE_GUARD", lambda: None)
    with pytest.raises(guard.NativeSourceClosureError):
        installed.entry()


def test_captured_guard_closure_rebinding_is_detected(installed):
    held = installed._NATIVE_GUARD
    cell = next(cell for cell in held.__closure__ if type(cell.cell_contents) is tuple)
    original = cell.cell_contents
    cell.cell_contents = ("changed",)
    try:
        with pytest.raises(guard.NativeSourceClosureError):
            installed.entry()
    finally:
        cell.cell_contents = original


def test_rebound_source_hash_leaf_is_not_executed(installed, monkeypatch):
    called = []
    monkeypatch.setattr(guard, "sha", lambda raw: called.append(raw))
    with pytest.raises(guard.NativeSourceClosureError):
        installed.entry()
    assert not called


def test_leaf_swap_after_actual_await_retires_same_attempt(installed, monkeypatch):
    pending = installed.async_entry()
    assert pending.send(None) is None
    monkeypatch.setattr(installed, "leaf", lambda: 99)
    with pytest.raises(guard.NativeSourceClosureError):
        pending.send(None)
    assert installed.closed == ["closed"]
    with pytest.raises(guard.NativeSourceClosureError):
        pending.send(None)


@pytest.mark.parametrize("resume", ["send", "throw", "close"])
def test_replaced_class_checkpoint_cannot_suppress_captured_resume_guard(
    installed, monkeypatch, resume
):
    pending = installed.async_entry()
    assert pending.send(None) is None
    called = []
    monkeypatch.setattr(guard._GuardedCoroutine, "_check", lambda self: None)
    monkeypatch.setattr(installed, "leaf", lambda: called.append("foreign"))
    with pytest.raises(guard.NativeSourceClosureError):
        if resume == "send":
            pending.send(None)
        elif resume == "throw":
            pending.throw(asyncio.CancelledError())
        else:
            pending.close()
    assert not called
    assert installed.closed == ["closed"]


@pytest.mark.parametrize("mutation", ["attribute", "code"])
def test_imported_vendor_binding_is_checked_after_actual_await(monkeypatch, mutation):
    monkeypatch.setattr(fixture, "entry", ORIGINAL_ENTRY)
    monkeypatch.setattr(fixture, "async_entry", ORIGINAL_ASYNC_ENTRY)
    monkeypatch.setattr(fixture, "_NATIVE_GUARD", None, raising=False)
    fixture.closed.clear()
    guard.install_native_guards_v1(
        fixture,
        ("entry", "async_entry"),
        bindings=(
            (external.ExternalStream, "send", external.ExternalStream.send),
            (external.ExternalStream, "close", external.ExternalStream.close),
        ),
    )
    assert fixture.entry() == 7
    pending = fixture.async_entry()
    assert pending.send(None) is None
    called = []
    if mutation == "attribute":
        monkeypatch.setattr(
            external.ExternalStream,
            "send",
            lambda self, value=None: called.append("foreign"),
        )
    else:
        monkeypatch.setattr(
            external.ExternalStream.send,
            "__code__",
            (lambda self, value=None: 99).__code__,
        )
    with pytest.raises(guard.NativeSourceClosureError):
        pending.send(None)
    assert not called and fixture.closed == ["closed"]


def test_first_send_throw_and_close_never_create_an_alternative_resume(installed):
    closed_before_first_send = installed.async_entry()
    closed_before_first_send.close()
    with pytest.raises(guard.NativeSourceClosureError):
        closed_before_first_send.send(None)
    thrown = installed.async_entry()
    with pytest.raises(asyncio.CancelledError):
        thrown.throw(asyncio.CancelledError())
    with pytest.raises(guard.NativeSourceClosureError):
        thrown.send(None)


def test_garbage_discard_before_first_send_closes_original_coroutine(installed):
    pending = installed.async_entry()
    inner = guard._COROUTINES[id(pending)]["body"]
    held = ref(pending)
    del pending
    gc.collect()
    assert held() is None
    assert inner.cr_frame is None
    with pytest.raises(RuntimeError):
        inner.send(None)


def test_returned_coroutine_has_no_writable_resume_checkpoint(installed, monkeypatch):
    pending = installed.async_entry()
    assert pending.send(None) is None
    called = []
    with pytest.raises(AttributeError):
        pending._resume = lambda: None
    monkeypatch.setattr(installed, "leaf", lambda: called.append("foreign"))
    with pytest.raises(guard.NativeSourceClosureError):
        pending.send(None)
    assert not called and installed.closed == ["closed"]


def test_corrupted_retirement_code_is_never_invoked(installed, monkeypatch):
    pending = installed.async_entry()
    assert pending.send(None) is None
    inner = guard._COROUTINES[id(pending)]["body"]

    def foreign_retire(instance, registry):
        raise AssertionError("foreign cleanup callback executed")

    with monkeypatch.context() as corrupt:
        corrupt.setattr(
            guard._retire_coroutine_state, "__code__", foreign_retire.__code__
        )
        with pytest.raises(guard.NativeSourceClosureError):
            pending.send(None)
        assert installed.closed == []
        with pytest.raises(guard.NativeSourceClosureError):
            pending.send(None)
    # The inert fixture has no native/source/DB ownership. Explicitly close its
    # original coroutine after restoring source, without resuming the attempt.
    inner.close()
    assert installed.closed == ["closed"]


@pytest.mark.asyncio
async def test_actual_task_cancellation_reaches_original_finally(installed):
    pending = installed.async_entry()
    task = asyncio.create_task(pending)
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert installed.closed == ["closed"]
