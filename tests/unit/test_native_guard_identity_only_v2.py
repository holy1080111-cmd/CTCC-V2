"""Metadata checks are inert; legacy guard delegation remains mandatory."""

from types import FunctionType

import pytest

from app.trade_qualification import native_continuation_guard_v1 as guard


def _saved():
    calls = []

    def check():
        calls.append("executed")

    state = {}
    guard.save_native_guard_v1(state, check)
    return state, calls


def test_identity_only_never_executes_or_grants_authority_but_old_checker_executes():
    state, calls = _saved()
    assert guard.check_saved_native_guard_identity_v2(state) is None
    assert calls == []
    assert guard.check_saved_native_guard_v1(state) is None
    assert calls == ["executed"]


@pytest.mark.parametrize(
    "mutation",
    [
        "code",
        "defaults",
        "kwdefaults",
        "globals",
        "closure",
        "cells",
        "shape",
        "foreign",
    ],
)
def test_identity_mutation_refuses_without_invoking_callback(monkeypatch, mutation):
    state, calls = _saved()
    original = state["native_source_guard"]

    if mutation == "code":

        def other():
            calls.append("foreign")

        monkeypatch.setattr(original, "__code__", other.__code__)
    elif mutation == "defaults":
        monkeypatch.setattr(original, "__defaults__", (object(),))
    elif mutation == "kwdefaults":
        monkeypatch.setattr(original, "__kwdefaults__", {"extra": object()})
    elif mutation == "globals":
        state["native_source_guard"] = FunctionType(
            original.__code__, dict(original.__globals__), closure=original.__closure__
        )
    elif mutation == "closure":
        replacement, _ = _saved()
        state["native_source_guard"] = replacement["native_source_guard"]
    elif mutation == "cells":
        monkeypatch.setattr(original.__closure__[0], "cell_contents", [])
    elif mutation == "shape":
        state["native_source_guard_pin"] = ()
    else:

        class Foreign:
            def __getattribute__(self, name):
                calls.append(name)
                raise AssertionError("foreign metadata callback")

        state["native_source_guard"] = Foreign()
    with pytest.raises(guard.NativeSourceClosureError):
        guard.check_saved_native_guard_identity_v2(state)
    assert not calls


def test_legacy_check_still_invokes_and_propagates_original_guard_refusal():
    calls = []

    def refusing():
        calls.append("checked")
        raise guard.NativeSourceClosureError("synthetic_source_refused")

    state = {}
    guard.save_native_guard_v1(state, refusing)
    assert guard.check_saved_native_guard_identity_v2(state) is None
    assert calls == []
    with pytest.raises(
        guard.NativeSourceClosureError, match="synthetic_source_refused"
    ):
        guard.check_saved_native_guard_v1(state)
    assert calls == ["checked"]
