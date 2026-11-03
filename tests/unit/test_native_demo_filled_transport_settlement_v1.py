"""Negative authority checks; no fake Source, ledger, POST or closed position."""

import copy
import pickle
from types import ModuleType

import pytest

from app.exchange.okx import native_demo_filled_settlement_v1 as subject
from app.exchange.okx import native_demo_write_v2 as write
from app.trade_qualification import native_continuation_guard_v1 as guard


@pytest.mark.parametrize("key", (None, True, False, 0, {}, object()))
def test_caller_key_cannot_construct_a_pair_owner(key):
    with pytest.raises(
        subject.NativeFilledTransportSettlementError,
        match="^native_demo_filled_pair_not_constructible$",
    ):
        subject.OwnedNativeClosedTransportsV1(key=key)


@pytest.mark.parametrize("supplied", (None, True, False, {}, object()))
@pytest.mark.parametrize(
    "operation",
    (
        subject._take_native_closed_transports_for_settlement_v1,
        subject._replay_native_closed_transports_for_settlement_v1,
    ),
)
def test_passed_flags_or_data_do_not_authorize_the_pair(supplied, operation):
    with pytest.raises(subject.NativeFilledTransportSettlementError):
        operation(supplied, key=object())


@pytest.mark.parametrize("operation", (copy.copy, copy.deepcopy, pickle.dumps))
def test_object_new_does_not_make_a_copyable_authority(operation):
    forged = object.__new__(subject.OwnedNativeClosedTransportsV1)
    with pytest.raises(TypeError, match="^native_demo_filled_pair_not_"):
        operation(forged)


def test_object_new_cannot_enter_any_registry_or_retire_authentication():
    forged = object.__new__(subject.OwnedNativeClosedTransportsV1)
    operations = (
        lambda: subject._take_native_closed_transports_for_settlement_v1(
            forged, key=object()
        ),
        lambda: subject._replay_native_closed_transports_for_settlement_v1(
            forged, key=object()
        ),
        lambda: subject._finalize_native_closed_transports_settlement_v1(
            forged, object(), key=object()
        ),
        lambda: subject._retire_native_closed_transports_after_source_v1(
            forged, object(), key=object()
        ),
        lambda: subject._replay_native_finalized_closed_transports_for_source_v1(
            forged, key=object()
        ),
    )
    for operation in operations:
        with pytest.raises(subject.NativeFilledTransportSettlementError):
            operation()
    assert forged not in subject._UNKNOWN and forged not in subject._CLOSED
    assert forged not in subject._FINALIZED


def test_pair_view_is_inert_even_with_matching_text_digests():
    view = subject.NativeClosedTransportsViewV1(
        object(),
        object(),
        object(),
        "a" * 64,
        "b" * 64,
        (("claimed_closed",), ("claimed_closed",)),
        object(),
    )
    assert view.execution_authority is False and view.order_retry_authority is False
    with pytest.raises(subject.NativeFilledTransportSettlementError):
        subject._take_native_closed_transports_for_settlement_v1(view, key=object())


def test_foreign_module_getattr_never_runs_before_saved_guard_rejects():
    # Verify the actual Root module is healthy first, so a prior unrelated
    # failure cannot make this mutation-order assertion falsely pass.
    write._NATIVE_GUARD()
    foreign = ModuleType("foreign_filled_pair_callback")
    calls = []

    def unexpected_lookup(name):
        calls.append(name)
        return object()

    vars(foreign)["__getattr__"] = unexpected_lookup
    saved = {}
    guard.save_native_guard_v1(saved, write._NATIVE_GUARD)
    guard.check_saved_native_guard_v1(saved)
    # Malformed caller DATA has a real complete snapshot shape; it never
    # enters a Root registry. Its code pin must reject before __getattr__.
    pin = saved["native_source_guard_pin"]
    saved["native_source_guard_pin"] = (object(), *pin[1:])
    record = {
        "filled_settlement_api": (
            foreign,
            object,
            object,
            object,
            object,
            object(),
            (),
            saved,
        )
    }
    with pytest.raises(
        guard.NativeSourceClosureError, match="^native_continuation_source_changed$"
    ):
        subject._api(record)
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("supplied", (None, True, {"passed": True, "flat": True}))
async def test_public_observer_does_not_turn_a_caller_flat_claim_into_a_handle(
    supplied,
):
    before = (len(subject._UNKNOWN), len(subject._CLOSED), len(subject._FINALIZED))
    with pytest.raises(ValueError):
        await subject.close_owned_native_demo_transports_after_filled_flat_v1(supplied)
    assert before == (
        len(subject._UNKNOWN),
        len(subject._CLOSED),
        len(subject._FINALIZED),
    )
