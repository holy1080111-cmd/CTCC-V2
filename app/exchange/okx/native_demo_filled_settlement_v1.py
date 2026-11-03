"""Passive closure of BOTH owned Demo POSTs after durable filled-flat readback.

Only actual postsubmit registry producers grant these opaque handles. The
original request scopes have already initiated close; this observer never
closes, flushes, retries or releases risk. All GETs and durable ledger state
must finish independently before Root may retire either authentication graph.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import threading
from dataclasses import dataclass, field
from types import FunctionType, ModuleType

from app.domain.native_clock import native_stamp
from app.exchange.okx import native_demo_settlement_v1 as passive
from app.trade_qualification import native_continuation_guard_v1 as guard

_SOURCE_KEY_V1 = object()
_HANDLE_KEY_V1 = object()
_UNKNOWN = {}
_CLOSED = {}
_FINALIZED = {}
_MAX = 16
_API_NAMES = (
    "_take_native_filled_flat_result_for_root_settlement_v1",
    "_replay_native_filled_flat_result_for_root_settlement_v1",
    "_take_native_filled_flat_cleanup_completion_for_root_v1",
    "_take_native_filled_flat_retirement_for_root_v1",
)


class NativeFilledTransportSettlementError(ValueError):
    pass


def _need(value, code="native_demo_filled_pair_settlement_unconfirmed"):
    if value is not True:
        raise NativeFilledTransportSettlementError(code) from None


@dataclass(frozen=True, slots=True, repr=False)
class NativeClosedTransportsViewV1:
    filled_result_view: object = field(repr=False)
    response_handle: object = field(repr=False)
    close_response_handle: object = field(repr=False)
    closure_outcome_id: str = field(repr=False)
    closure_receipt_sha256: str = field(repr=False)
    socket_observations: tuple = field(repr=False)
    closed_transports_handle: object = field(repr=False)
    execution_authority: bool = field(default=False, init=False)
    order_retry_authority: bool = field(default=False, init=False)


class OwnedNativeClosedTransportsV1:
    __slots__ = ()

    def __new__(cls, *, key=None):
        _need(
            cls is OwnedNativeClosedTransportsV1 and key is _HANDLE_KEY_V1,
            "native_demo_filled_pair_not_constructible",
        )
        return object.__new__(cls)

    def __reduce_ex__(self, protocol):
        raise TypeError("native_demo_filled_pair_not_serializable")

    def __copy__(self):
        raise TypeError("native_demo_filled_pair_not_copyable")

    def __deepcopy__(self, memo):
        raise TypeError("native_demo_filled_pair_not_copyable")


def capture_postsubmit_filled_api_v1(record):
    """Called at real locked gate creation; capture identities before awaits."""
    from app.exchange.okx import native_demo_write_v2 as write
    from app.trade_qualification import native_postsubmit_reconciliation_v1 as post

    _need(
        type(record) is dict
        and write._GATES.get(record["gate"]) is record
        and type(post) is ModuleType
        and "filled_settlement_api" not in record
    )
    write._live_facts(record)
    previous = record.get("settlement_api")
    _need(type(previous) is tuple and len(previous) == 6 and previous[0] is post)
    # The preceding actual gate hook already captured the postsubmit guard.
    # Validate it before any optional module attribute can invoke __getattr__.
    guard.check_saved_native_guard_v1(previous[5])
    members = vars(post)
    post_guard = members.get("_NATIVE_GUARD")
    _need(
        type(post_guard) is FunctionType
        and type(members.get("_SOURCE_KEY_V1")) is object
    )
    functions = []
    for name in _API_NAMES:
        function = members.get(name)
        _need(type(function) is FunctionType, "native_demo_filled_pair_source_missing")
        pin = guard._function_pin(function, {})
        guard._check_function(pin)
        functions.append((name, function, pin))
    saved = {}
    guard.save_native_guard_v1(saved, post_guard)
    record["filled_post_source_key"] = members["_SOURCE_KEY_V1"]
    record["filled_settlement_api"] = (
        post,
        members.get("NativeReconciliationResultV1"),
        members.get("NativeFilledFlatResultForRootSettlementV1"),
        members.get("NativeFilledFlatCleanupCompletionViewV1"),
        members.get("NativeFilledFlatRetirementViewV1"),
        members.get("_ROOT_FILLED_TRANSPORT_SETTLEMENT_KEY_V1"),
        tuple(functions),
        saved,
    )


def _api(record):
    from app.exchange.okx import native_demo_write_v2 as write

    write._NATIVE_GUARD()
    actual = record["filled_settlement_api"]
    _need(type(actual) is tuple and len(actual) == 8)
    (
        post,
        result_type,
        view_type,
        completed_type,
        retired_type,
        key,
        functions,
        saved,
    ) = actual
    _need(type(post) is ModuleType)
    guard.check_saved_native_guard_v1(saved)
    members = vars(post)
    _need(
        members.get("NativeReconciliationResultV1") is result_type
        and members.get("NativeFilledFlatResultForRootSettlementV1") is view_type
        and members.get("NativeFilledFlatCleanupCompletionViewV1") is completed_type
        and members.get("NativeFilledFlatRetirementViewV1") is retired_type
        and type(result_type) is type
        and type(view_type) is type
        and type(completed_type) is type
        and type(retired_type) is type
        and type(key) is object
        and members.get("_ROOT_FILLED_TRANSPORT_SETTLEMENT_KEY_V1") is key
        and members.get("_SOURCE_KEY_V1") is record["filled_post_source_key"]
        and type(functions) is tuple
        and len(functions) == len(_API_NAMES)
    )
    for expected, row in zip(_API_NAMES, functions, strict=True):
        name, function, pin = row
        _need(name == expected and members.get(name) is function)
        guard._check_function(pin)
    return actual


def _context(state):
    _need(
        type(state) is dict
        and _UNKNOWN.get(state["handle"]) is state
        and state["pid"] == os.getpid()
        and state["thread"] == threading.get_ident()
        and state["task"] is asyncio.current_task()
        and state["loop"] is asyncio.get_running_loop()
    )


def _replay(state):
    from app.exchange.okx import native_demo_write_v2 as write

    _context(state)
    entry, closing, flat = state["entry"], state["close"], state["flat"]
    api = _api(entry)
    close_api = _api(closing)
    _need(
        all(left is right for left, right in zip(api[:6], close_api[:6], strict=True))
        and all(
            left[1] is right[1]
            for left, right in zip(api[6], close_api[6], strict=True)
        )
    )
    _need(
        type(flat) is api[2]
        and api[6][1][1](flat, key=_SOURCE_KEY_V1) is flat
        and flat.response_handle is entry["gate"]
        and flat.close_response_handle is closing["gate"]
        and flat.response_handle is not flat.close_response_handle
        and flat.observed_result is entry["observed_result"]
        and flat.response_data is entry["reconciliation_view"]
        and flat.close_response_data is closing["close_reconciliation_view"]
    )
    _need(
        write._native_reconciliation_record_v1(
            entry["gate"], key=entry["filled_post_source_key"]
        )
        is entry
    )
    actual_close_module = write._close_api_v1(closing)[0]
    close_data = write._replay_native_close_response_for_reconciliation_v1(
        closing["gate"], key=vars(actual_close_module).get("_POSTSUBMIT_KEY_V1")
    )
    _need(
        close_data is flat.close_response_data
        and close_data.entry_response_handle is entry["gate"]
        and close_data.filled_result_view.response_handle is entry["gate"]
        and close_data.filled_result_view.observed_result is flat.observed_result
        and close_data.filled_result_view.response_data is flat.response_data
        and write._CLIENT_GATES.get(entry["client"]) is entry["gate"]
        and write._CLIENT_GATES.get(closing["client"]) is closing["gate"]
        and entry["client"] is not closing["client"]
    )
    attempt = state["close_attempt"]
    _need(
        type(attempt) is write._CloseSubmissionAttemptV1
        and write._CLOSE_ATTEMPTS_V1.get(state["close_owner"]) is attempt
        and attempt.state is closing["state"]
        and attempt.state["owner"] is state["close_owner"]
    )
    return api


def _observation(stamp):
    return (
        "ctcc.native_socket_closed_observation.v1",
        stamp["utc_ns"],
        stamp["monotonic_ns"],
        -1,
        "native_callback_tail_and_sdk_pool_closed",
    )


async def close_owned_native_demo_transports_after_filled_flat_v1(result):
    """Observe both already-started closes under ONE absolute five-second budget."""
    from app.exchange.okx import native_demo_write_v2 as write

    write._NATIVE_GUARD()
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 5
    started = native_stamp()
    _need(
        len(_UNKNOWN) < _MAX
        and not any(state["flat"].result is result for state in _UNKNOWN.values())
    )
    candidates = [
        record
        for record in write._GATES.values()
        if type(record) is dict
        and record["task"] is asyncio.current_task()
        and record.get("close_purpose") is not write._CLOSE_INTENT_KEY_V1
        and "filled_settlement_api" in record
        and type(result) is record["filled_settlement_api"][1]
        and result.reservation_id
        == record["original_transport_facts"].consumed.reservation_id
    ]
    _need(len(candidates) == 1)
    entry = candidates[0]
    api = _api(entry)
    flat = api[6][0][1](result, key=_SOURCE_KEY_V1)
    _need(
        type(flat) is api[2]
        and flat.result is result
        and flat.response_handle is entry["gate"]
    )
    closing = write._GATES.get(flat.close_response_handle)
    _need(
        type(closing) is dict
        and closing is not entry
        and closing.get("close_purpose") is write._CLOSE_INTENT_KEY_V1
    )
    owner = closing["state"]["owner"]
    attempt = write._CLOSE_ATTEMPTS_V1.get(owner)
    _need(
        type(attempt) is write._CloseSubmissionAttemptV1
        and attempt.state is closing["state"]
    )
    handle = OwnedNativeClosedTransportsV1(key=_HANDLE_KEY_V1)
    state = {
        "handle": handle,
        "entry": entry,
        "close": closing,
        "flat": flat,
        "close_owner": owner,
        "close_attempt": attempt,
        "pid": os.getpid(),
        "thread": threading.get_ident(),
        "task": asyncio.current_task(),
        "loop": loop,
        "started": started,
        "last": started,
        "taken": False,
        "view": None,
        "completion": None,
        "finalized": False,
    }
    _UNKNOWN[handle] = state
    try:
        async with asyncio.timeout_at(deadline):
            while True:
                _replay(state)
                write._require_vendor_state_v2()
                stamp = native_stamp()
                _need(
                    state["last"]["utc_ns"] <= stamp["utc_ns"]
                    and state["last"]["monotonic_ns"] <= stamp["monotonic_ns"]
                    and stamp["utc_ns"] - started["utc_ns"] < 5_000_000_000
                    and stamp["monotonic_ns"] - started["monotonic_ns"] < 5_000_000_000
                )
                state["last"] = stamp
                if (
                    passive._socket_closed(entry)
                    and passive._sdk_closed(entry)
                    and passive._socket_closed(closing)
                    and passive._sdk_closed(closing)
                ):
                    measured = native_stamp()
                    _need(
                        stamp["utc_ns"] <= measured["utc_ns"]
                        and stamp["monotonic_ns"] <= measured["monotonic_ns"]
                        and measured["utc_ns"] - started["utc_ns"] < 5_000_000_000
                        and measured["monotonic_ns"] - started["monotonic_ns"]
                        < 5_000_000_000
                    )
                    view = NativeClosedTransportsViewV1(
                        flat,
                        entry["gate"],
                        closing["gate"],
                        flat.closure_outcome_id,
                        flat.closure_receipt_sha256,
                        (_observation(measured), _observation(measured)),
                        handle,
                    )
                    state["view"] = view
                    _CLOSED[handle] = state
                    return handle
                await asyncio.sleep(0.01)
    except BaseException as error:
        # Retain both authentication graphs and keep private errors local.
        state["unconfirmed"] = True
        if isinstance(
            error,
            (asyncio.CancelledError, GeneratorExit, KeyboardInterrupt, SystemExit),
        ):
            raise
        raise NativeFilledTransportSettlementError(
            "native_demo_filled_pair_settlement_unconfirmed"
        ) from None


def _take_native_closed_transports_for_settlement_v1(handle, *, key):
    _need(type(handle) is OwnedNativeClosedTransportsV1 and handle in _CLOSED)
    state = _CLOSED[handle]
    api = _replay(state)
    _need(key is api[5] and state["taken"] is False)
    state["taken"] = True
    return state["view"]


def _replay_native_closed_transports_for_settlement_v1(handle, *, key):
    _need(type(handle) is OwnedNativeClosedTransportsV1 and handle in _CLOSED)
    state = _CLOSED[handle]
    api = _replay(state)
    _need(
        key is api[5]
        and state["taken"] is True
        and state["view"].closed_transports_handle is handle
    )
    return state["view"]


def _finalize_native_closed_transports_settlement_v1(handle, completion, *, key):
    from app.exchange.okx import native_demo_write_v2 as write

    view = _replay_native_closed_transports_for_settlement_v1(handle, key=key)
    state = _CLOSED[handle]
    api = _api(state["entry"])
    completed = api[6][2][1](completion, key=_SOURCE_KEY_V1)
    _need(
        type(completed) is api[3]
        and completed.filled_result_view is view.filled_result_view
        and completed.closed_transports_handle is handle
        and completed.ledger_completion is not None
        and completed.source_completion is not None
        and type(completed.receipt_json) is bytes
        and 0 < len(completed.receipt_json) <= 32768
    )
    for record in (state["entry"], state["close"]):
        _need(
            passive._socket_closed(record)
            and passive._sdk_closed(record)
            and write._GATES.get(record["gate"]) is record
            and write._CLIENT_GATES.get(record["client"]) is record["gate"]
        )
    # Source's final capacity and cap replays still need BOTH Root maps.
    # Mark completion, retain every actual authentication/data owner, and
    # remove nothing until the NEW after-retirement producer proves the rest.
    state["completion"] = completion
    state["completion_view"] = completed
    state["finalized"] = True
    _FINALIZED[handle] = state
    state["finalized_receipt"] = (
        "ctcc.native_filled_pair_resources_finalized.v1",
        view.closure_outcome_id,
        view.closure_receipt_sha256,
        hashlib.sha256(completed.receipt_json).hexdigest(),
    )
    _CLOSED.pop(handle)
    return state["finalized_receipt"]


def _replay_native_finalized_closed_transports_for_source_v1(handle, *, key):
    """Actual closed DATA after completion; no healthy trade/ledger replay."""
    from app.exchange.okx import native_demo_write_v2 as write

    _need(type(handle) is OwnedNativeClosedTransportsV1 and handle in _FINALIZED)
    state = _FINALIZED[handle]
    _context(state)
    api = _api(state["entry"])
    _need(
        key is api[5]
        and state["finalized"] is True
        and handle not in _CLOSED
        and state["completion"] is not None
    )
    for record in (state["entry"], state["close"]):
        _need(
            write._GATES.get(record["gate"]) is record
            and write._CLIENT_GATES.get(record["client"]) is record["gate"]
            and passive._socket_closed(record)
            and passive._sdk_closed(record)
        )
    _need(
        write._CLOSE_ATTEMPTS_V1.get(state["close_owner"]) is state["close_attempt"]
        and state["close_attempt"].state is state["close"]["state"]
    )
    return (
        state["view"],
        state["completion"],
        state["completion_view"],
        state["finalized_receipt"],
    )


def _retire_native_closed_transports_after_source_v1(handle, retirement, *, key):
    from app.exchange.okx import native_demo_write_v2 as write

    _need(type(handle) is OwnedNativeClosedTransportsV1 and handle in _FINALIZED)
    state = _FINALIZED[handle]
    _context(state)
    api = _api(state["entry"])
    _need(
        key is api[5]
        and state["finalized"] is True
        and handle not in _CLOSED
        and state["completion"] is not None
    )
    for record in (state["entry"], state["close"]):
        _need(
            write._GATES.get(record["gate"]) is record
            and write._CLIENT_GATES.get(record["client"]) is record["gate"]
            and passive._socket_closed(record)
            and passive._sdk_closed(record)
        )
    _need(
        write._CLOSE_ATTEMPTS_V1.get(state["close_owner"]) is state["close_attempt"]
        and state["close_attempt"].state is state["close"]["state"]
    )
    retired = api[6][3][1](retirement, key=_SOURCE_KEY_V1)
    _need(
        type(retired) is api[4]
        and retired.filled_result_view is state["flat"]
        and retired.closed_transports_handle is handle
        and retired.cleanup_completion is state["completion"]
        and retired.source_retirement is not None
        and retired.ledger_retirement is not None
        and retired.close_retirement is not None
        and type(retired.receipt_json) is bytes
        and 0 < len(retired.receipt_json) <= 32768
    )
    result = (
        "ctcc.native_filled_pair_authentication_retired.v1",
        state["flat"].closure_outcome_id,
        state["flat"].closure_receipt_sha256,
        hashlib.sha256(retired.receipt_json).hexdigest(),
    )
    # No await/callback or new clock lease occurs between the complete pair
    # identity/cap checks and the final removal of all original Root maps.
    for record in (state["entry"], state["close"]):
        write._CLIENT_GATES.pop(record["client"])
        write._GATES.pop(record["gate"])
    write._CLOSE_ATTEMPTS_V1.pop(state["close_owner"])
    _FINALIZED.pop(handle)
    _UNKNOWN.pop(handle)
    return result
