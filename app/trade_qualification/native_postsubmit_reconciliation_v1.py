"""Owned current/restart post-submit DATA reconciliation, never a write permit.

Original trade expiry is not renewed. New GET-only evidence has one separately
bounded read budget. ACKs, supplied receipts and copied observations cannot mint
this module's private persistence witness. Unknowns strongly retain all sources.
"""

from __future__ import annotations

import asyncio
import os
import sys
import threading
from collections.abc import Coroutine
from contextlib import contextmanager
from dataclasses import dataclass, field

from app.domain.native_clock import native_stamp
from app.exchange.okx import native_reconciliation_network_custody_v1 as _read_network
from app.exchange.okx import native_reconciliation_observation_v1 as read
from app.trade_qualification import native_continuation_guard_v1 as guard
from app.trade_qualification import native_reconciliation_facts_v1 as facts

_SOURCE_KEY_V1 = object()
_ROOT_TRANSPORT_SETTLEMENT_KEY_V1 = object()
_SOURCE_SETTLEMENT_KEY_V1 = object()
_ROOT_FILLED_TRANSPORT_SETTLEMENT_KEY_V1 = object()
_FILLED_SOURCE_SETTLEMENT_KEY_V1 = object()
_LIVE = {}
_UNKNOWN = {}
_FLAT_SETTLEMENTS = {}
_CLEANUP_COMPLETIONS = {}
_FILLED_CLEANUP_COMPLETIONS = {}
_FILLED_RETIREMENTS = {}
_FILLED_SETTLEMENTS = {}
_FILLED_PERSISTENCE = {}
_FILLED_COMMITTED = {}
_FILLED_SETTLED_RESULTS = {}
_FILLED_WORKFLOWS = {}
_ZERO_PERSISTENCE_V2 = {}
_ZERO_COMMITTED_V2 = {}
_ZERO_SETTLED_RESULTS_V2 = {}
_MAX_READS = 64
_READ_SECONDS = 60
_PUBLIC_UNKNOWN_REASONS = frozenset(
    {
        "native_reconcile_unresolved_average_precision",
        "native_reconcile_fok_partial_or_ambiguous",
        "native_reconcile_fill_quantity_mismatch",
        "native_reconcile_protection_geometry_or_identity_mismatch",
        "native_reconcile_protection_missing_duplicate_or_extra",
        "native_reconcile_fill_exceeds_reserved_coverage",
        "native_reconcile_future_source_clock",
        "native_reconcile_source_clock_missing_or_invalid",
        "native_reconcile_fill_generation_precedes_trade",
        "native_reconcile_account_snapshot_cutoff_conflict",
    }
)


class NativePostsubmitError(ValueError):
    pass


def _need(condition, code="native_postsubmit_source_unavailable"):
    if condition is not True:
        raise NativePostsubmitError(code) from None


@dataclass(frozen=True, slots=True, repr=False)
class NativeReconciliationSourceContextV1:
    """Inert exact view; only a genuine native source registry can admit it."""

    client: object = field(repr=False)
    scope: object = field(repr=False)
    session_factory: object = field(repr=False)
    origin: str = field(repr=False)
    credential_session_sha256: str = field(repr=False)
    source_generation: object = field(repr=False)
    control_owner: object = field(repr=False)


@dataclass(frozen=True, slots=True, repr=False)
class NativeSubmissionResponseDataV1:
    """Root-created DATA view; a constructed view never passes its registry."""

    transport_facts: object = field(repr=False)
    observed: object = field(repr=False)
    received_journal: object = field(repr=False)
    transport_state: object = field(repr=False)


@dataclass(frozen=True, slots=True, repr=False)
class NativeReconciliationResultV1:
    reservation_id: str = field(repr=False)
    classification: str
    outcome_id: str = field(repr=False)
    receipt_sha256: str = field(repr=False)
    risk_hold_retained: bool
    execution_authority: bool = field(default=False, init=False)
    order_retry_authority: bool = field(default=False, init=False)


@dataclass(frozen=True, slots=True, repr=False)
class NativeFlatResultForRootSettlementV1:
    """Inert view of a genuine one-use result MOVE; construction grants none."""

    response_handle: object = field(repr=False)
    observed_result: object = field(repr=False)
    result: NativeReconciliationResultV1 = field(repr=False)
    response_data: NativeSubmissionResponseDataV1 = field(repr=False)
    closure_outcome_id: str = field(repr=False)
    closure_receipt_sha256: str = field(repr=False)
    closure_readback: tuple = field(repr=False)


@dataclass(frozen=True, slots=True, repr=False)
class NativeFilledResultForCloseV1:
    """Historical owned filled-result view; never an entry/close write permit."""

    response_handle: object = field(repr=False)
    observed_result: object = field(repr=False)
    result: NativeReconciliationResultV1 = field(repr=False)
    response_data: NativeSubmissionResponseDataV1 = field(repr=False)
    readback_outcome_id: str = field(repr=False)
    readback_receipt_sha256: str = field(repr=False)
    readback: tuple = field(repr=False)


@dataclass(frozen=True, slots=True, repr=False)
class NativeFilledFlatResultForRootSettlementV1:
    response_handle: object = field(repr=False)
    observed_result: object = field(repr=False)
    result: NativeReconciliationResultV1 = field(repr=False)
    response_data: NativeSubmissionResponseDataV1 = field(repr=False)
    close_response_handle: object = field(repr=False)
    close_response_data: object = field(repr=False)
    closure_outcome_id: str = field(repr=False)
    closure_receipt_sha256: str = field(repr=False)
    closure_readback: tuple = field(repr=False)
    original_source_generation: object = field(repr=False)
    preflight_source_generation: object = field(repr=False)
    final_source_generation: object = field(repr=False)


@dataclass(frozen=True, slots=True, repr=False)
class NativeFilledFlatCleanupCompletionViewV1:
    filled_result_view: NativeFilledFlatResultForRootSettlementV1 = field(repr=False)
    closed_transports_handle: object = field(repr=False)
    ledger_completion: object = field(repr=False)
    source_completion: object = field(repr=False)
    receipt_json: bytes = field(repr=False)


@dataclass(frozen=True, slots=True, repr=False)
class NativeFilledFlatRetirementViewV1:
    filled_result_view: NativeFilledFlatResultForRootSettlementV1 = field(repr=False)
    closed_transports_handle: object = field(repr=False)
    cleanup_completion: object = field(repr=False)
    source_retirement: object = field(repr=False)
    ledger_retirement: object = field(repr=False)
    close_retirement: object = field(repr=False)
    receipt_json: bytes = field(repr=False)


@dataclass(frozen=True, slots=True, repr=False)
class NativeFilledFlatSourceSettlementV1:
    settlement_cap: object = field(repr=False)
    filled_result_view: NativeFilledFlatResultForRootSettlementV1 = field(repr=False)
    closed_transports_view: object = field(repr=False)
    source_generations: tuple = field(repr=False)
    entry_transport_binding: object = field(repr=False)
    close_transport_binding: object = field(repr=False)
    closed_transports_handle: object = field(repr=False)


@dataclass(frozen=True, slots=True, repr=False)
class NativeFilledCleanupCommittedForSourceV1:
    source_retirement: object = field(repr=False)
    cleanup_outcome_id: str = field(repr=False)
    cleanup_row_json: bytes = field(repr=False)
    read_metadata: tuple = field(repr=False)


@dataclass(frozen=True, slots=True, repr=False)
class NativeFilledReconciliationSettlementResultV1:
    reservation_id: str = field(repr=False)
    closure_outcome_id: str = field(repr=False)
    closure_receipt_sha256: str = field(repr=False)
    cleanup_outcome_id: str = field(repr=False)
    forensics_outcome_id: str = field(repr=False)
    report_spool_id: str | None = field(repr=False)
    classification: str = field(
        default="own_entry_and_close_filled_flat_settled", init=False
    )
    execution_authority: bool = field(default=False, init=False)
    order_retry_authority: bool = field(default=False, init=False)


@dataclass(frozen=True, slots=True, repr=False)
class NativeFilledCallerCompletionV1:
    controller: object = field(repr=False)
    cleanup_outcome_id: str = field(repr=False)
    report_spool_id: str | None = field(repr=False)


@dataclass(frozen=True, slots=True, repr=False)
class NativeFilledCleanupCommittedForCloseV1:
    close_retirement: object = field(repr=False)
    cleanup_outcome_id: str = field(repr=False)
    cleanup_row_sha256: str = field(repr=False)


@dataclass(frozen=True, slots=True, repr=False)
class NativeCloseIntentForOwnerV1:
    binding: object = field(repr=False)
    entry_cap: object = field(repr=False)
    source_generation: object = field(repr=False)
    source_context: NativeReconciliationSourceContextV1 = field(repr=False)
    filled_result_view: NativeFilledResultForCloseV1 = field(repr=False)
    target: object = field(repr=False)
    intent_readback: tuple = field(repr=False)
    execution_authority: bool = field(default=False, init=False)
    order_retry_authority: bool = field(default=False, init=False)


@dataclass(frozen=True, slots=True, repr=False)
class NativeFlatSourceSettlementForAccountV1:
    source_generation: object = field(repr=False)
    transport_binding: object = field(repr=False)
    closed_transport_view: object = field(repr=False)
    flat_result_view: NativeFlatResultForRootSettlementV1 = field(repr=False)


@dataclass(frozen=True, slots=True, repr=False)
class NativeFlatCleanupCompletionViewV1:
    flat_result_view: NativeFlatResultForRootSettlementV1 = field(repr=False)
    closed_transport_handle: object = field(repr=False)
    ledger_completion: object = field(repr=False)
    source_completion: object = field(repr=False)


@dataclass(frozen=True, slots=True, repr=False)
class NativeReconciliationSettlementResultV1:
    reservation_id: str = field(repr=False)
    closure_outcome_id: str = field(repr=False)
    receipt_sha256: str = field(repr=False)
    classification: str = "zero_fill_flat_settled"
    execution_authority: bool = field(default=False, init=False)
    order_retry_authority: bool = field(default=False, init=False)


@dataclass(frozen=True, slots=True, repr=False)
class NativeZeroReconciliationSettlementResultV2:
    reservation_id: str = field(repr=False)
    closure_outcome_id: str = field(repr=False)
    receipt_sha256: str = field(repr=False)
    cleanup_outcome_id: str = field(repr=False)
    classification: str = field(default="terminal_zero_fill_flat_settled", init=False)
    execution_authority: bool = field(default=False, init=False)
    order_retry_authority: bool = field(default=False, init=False)


@dataclass(frozen=True, slots=True, repr=False)
class NativeZeroCallerCompletionV2:
    controller: object = field(repr=False)
    cleanup_outcome_id: str = field(repr=False)


@dataclass(frozen=True, slots=True, repr=False)
class NativeZeroCleanupCommittedForSourceV2:
    source_retirement: object = field(repr=False)
    cleanup_outcome_id: str = field(repr=False)
    cleanup_row_json: bytes = field(repr=False)
    read_metadata: tuple = field(repr=False)


class _OwnedNativeFlatSettlementV1:
    __slots__ = ()

    def __new__(cls):
        _need(False)

    def __copy__(self):
        _need(False)

    def __deepcopy__(self, memo):
        _need(False)

    def __reduce_ex__(self, protocol):
        _need(False)


class OwnedNativeFlatCleanupCompletionV1(_OwnedNativeFlatSettlementV1):
    __slots__ = ()


class OwnedNativeFilledFlatCleanupCompletionV1(_OwnedNativeFlatSettlementV1):
    __slots__ = ()


class OwnedNativeFilledFlatRetirementV1(_OwnedNativeFlatSettlementV1):
    __slots__ = ()


class _OwnedNativeFilledFlatSettlementV1(_OwnedNativeFlatSettlementV1):
    __slots__ = ()


class OwnedNativeFilledCleanupCommittedV1(_OwnedNativeFlatSettlementV1):
    __slots__ = ()


class OwnedNativeZeroCleanupCommittedV2(_OwnedNativeFlatSettlementV1):
    __slots__ = ()


def _retained_cleanup_identity_v1(state):
    _need(
        type(state) is dict
        and type(state.get("binding")) is object
        and _UNKNOWN.get(state["binding"]) is state
        and state["binding"] not in _LIVE
        and state.get("closed") is True
        and state["kind"] == "current"
        and state["task"] is asyncio.current_task()
        and state["loop"] is asyncio.get_running_loop()
        and state["pid"] == os.getpid()
        and state["thread"] == threading.get_ident()
    )
    guard.check_saved_native_guard_v1(state["guard"])
    return state


def _owned_settlement_v1(cap, *, after_root=False):
    from app.exchange.okx import native_demo_write_v2 as write

    _NATIVE_GUARD()
    _need(type(cap) is _OwnedNativeFlatSettlementV1 and cap in _FLAT_SETTLEMENTS)
    state = _retained_cleanup_identity_v1(_FLAT_SETTLEMENTS[cap])
    _need(state.get("flat_source_settlement_cap") is cap)
    if after_root:
        _need(
            state.get("root_finalization_returned") is True
            and state.get("root_cleanup_completion_taken") is True
            and state["closed_transport_handle"] not in write._native_settlement._CLOSED
            and state["closed_transport_handle"]
            not in write._native_settlement._UNKNOWN
            and state["root_handle"] not in write._GATES
            and state["response_source"].transport_facts.transport_context.client
            not in write._CLIENT_GATES
        )
    else:
        closed = write._replay_native_closed_transport_for_settlement_v1(
            state["closed_transport_handle"], key=_ROOT_TRANSPORT_SETTLEMENT_KEY_V1
        )
        _need(
            closed is state["closed_transport_view"]
            and closed.flat_result_view is state["root_flat_settlement_view"]
            and closed.flat_result_view.result is state["owned_flat_result"]
            and closed.closed_transport_handle is state["closed_transport_handle"]
        )
    return state


def _own_closed_flat_settlement_v1(state, handle):
    from app.exchange.okx import native_demo_write_v2 as write

    _retained_cleanup_identity_v1(state)
    _need(state.get("flat_source_settlement_cap") is None)
    closed = write._take_native_closed_transport_for_settlement_v1(
        handle, key=_ROOT_TRANSPORT_SETTLEMENT_KEY_V1
    )
    _need(
        type(closed) is write._native_settlement.NativeClosedTransportViewV1
        and closed.flat_result_view is state["root_flat_settlement_view"]
        and closed.flat_result_view.result is state["owned_flat_result"]
        and closed.response_handle is state["root_handle"]
        and closed.observed_result is state["response_source"].observed
        and closed.closure_outcome_id == state["verified_flat_closure"][0]
        and closed.closed_transport_handle is handle
    )
    cap = object.__new__(_OwnedNativeFlatSettlementV1)
    state["closed_transport_handle"], state["closed_transport_view"] = handle, closed
    state["flat_source_settlement_cap"] = cap
    state["flat_source_settlement_view"] = NativeFlatSourceSettlementForAccountV1(
        state["source"].source_generation,
        state["response_source"].transport_facts.transport_binding,
        closed,
        closed.flat_result_view,
    )
    _FLAT_SETTLEMENTS[cap] = state
    return cap


def _take_native_flat_source_settlement_v1(cap, *, key):
    from app.trade_qualification import native_controlled_account_source_v6 as source

    _need(key is source._FLAT_CLEANUP_KEY_V1)
    state = _owned_settlement_v1(cap)
    _need(state.get("source_settlement_taken") is not True)
    state["source_settlement_taken"] = True
    return state["flat_source_settlement_view"]


def _replay_native_flat_source_settlement_v1(cap, *, key):
    from app.trade_qualification import native_controlled_account_source_v6 as source

    _need(key is source._FLAT_CLEANUP_KEY_V1)
    state = _owned_settlement_v1(cap)
    _need(state.get("source_settlement_taken") is True)
    return state["flat_source_settlement_view"]


def _take_native_flat_ledger_settlement_v1(cap, *, key):
    from app.trade_qualification import native_ledger_runtime_v2 as ledger

    _need(key is ledger._FLAT_CLEANUP_KEY_V1)
    state = _owned_settlement_v1(cap)
    _need(
        state.get("ledger_settlement_taken") is not True
        and state.get("source_prepared") is not None
    )
    state["ledger_settlement_taken"] = True
    return state["flat_source_settlement_view"]


def _replay_native_flat_ledger_settlement_v1(cap, *, key):
    from app.trade_qualification import native_ledger_runtime_v2 as ledger

    _need(key is ledger._FLAT_CLEANUP_KEY_V1)
    state = _owned_settlement_v1(cap)
    _need(state.get("ledger_settlement_taken") is True)
    return state["flat_source_settlement_view"]


def _replay_native_flat_source_after_finalization_v1(cap, *, key):
    from app.trade_qualification import native_controlled_account_source_v6 as source

    _need(key is source._FLAT_CLEANUP_KEY_V1)
    return _owned_settlement_v1(cap, after_root=True)["flat_source_settlement_view"]


def _replay_native_flat_ledger_after_finalization_v1(cap, *, key):
    from app.trade_qualification import native_ledger_runtime_v2 as ledger

    _need(key is ledger._FLAT_CLEANUP_KEY_V1)
    return _owned_settlement_v1(cap, after_root=True)["flat_source_settlement_view"]


def _require_native_flat_source_retired_for_ledger_v1(cap, *, key):
    from app.trade_qualification import native_ledger_runtime_v2 as ledger

    _need(key is ledger._FLAT_CLEANUP_KEY_V1)
    state = _owned_settlement_v1(cap, after_root=True)
    _need(state.get("source_retirement_call_returned") is True)
    return state["flat_source_settlement_view"]


def _verify_prepared_cleanup_v1(state):
    from app.trade_qualification import native_ledger_runtime_v2 as ledger

    source = _captured_flat_cleanup_source_v1(state)
    _owned_settlement_v1(state["flat_source_settlement_cap"])
    source_view = source._replay_native_zero_final_source_cleanup_completion_v2(
        state["source_completion"], key=_SOURCE_SETTLEMENT_KEY_V1
    )
    ledger_view = ledger.replay_native_flat_ledger_cleanup_v1(
        state["ledger_completion"], key=_SOURCE_SETTLEMENT_KEY_V1
    )
    _need(
        type(source_view) is source.NativeFlatSourceCleanupCompletionViewV1
        and type(ledger_view) is ledger.NativeFlatLedgerCleanupViewV1
        and source_view.settlement_cap is state["flat_source_settlement_cap"]
        and source_view.source_generation is state["source"].source_generation
        and source_view.transport_binding
        is state["response_source"].transport_facts.transport_binding
        and source_view.flat_result_view
        is ledger_view.flat_result_view
        is state["root_flat_settlement_view"]
        and source_view.closed_transport_handle
        is ledger_view.closed_transport_handle
        is state["closed_transport_handle"]
        and ledger_view.reservation_id == state["reservation_id"]
    )
    return source_view, ledger_view


def _captured_flat_cleanup_source_v1(state):
    """Use the existing genuine V6 bootstrap; never pin a partial import."""
    from app.trade_qualification import native_account_risk_assembly_v1 as account

    actual = account._require_native_flat_cleanup_source_v1(
        key=_SOURCE_SETTLEMENT_KEY_V1
    )
    _need(type(actual) is tuple and len(actual) == 3)
    source, kernel, functions = actual
    _need(
        sys.modules.get("app.trade_qualification.native_controlled_account_source_v6")
        is source
        and vars(source).get("_REQUIRE_SOURCE_KERNEL_V6") is kernel
        and type(functions) is tuple
        and len(functions) == 7
        and tuple(name for name, _ in functions)
        == (
            "prepare_native_zero_final_source_cleanup_v2",
            "finish_native_zero_final_source_cleanup_v2",
            "_replay_native_zero_final_source_cleanup_completion_v2",
            "retire_native_zero_final_source_cleanup_v2",
            "_replay_native_zero_final_source_retirement_v2",
            "_scan_native_zero_final_retained_data_v2",
            "compact_native_zero_final_source_retirement_v2",
        )
        and all(vars(source).get(name) is function for name, function in functions)
    )
    previous = state.get("flat_cleanup_source_capture")
    if previous is None:
        state["flat_cleanup_source_capture"] = actual
    else:
        _need(
            previous[0] is source
            and previous[1] is kernel
            and all(
                left[0] == right[0] and left[1] is right[1]
                for left, right in zip(previous[2], functions, strict=True)
            )
        )
    kernel()
    return source


def _captured_native_close_read_source_v1(state):
    from app.trade_qualification import native_account_risk_assembly_v1 as account

    actual = account._require_native_close_read_source_v1(key=_SOURCE_KEY_V1)
    _need(type(actual) is tuple and len(actual) == 3)
    source, kernel, functions = actual
    _need(
        sys.modules.get("app.trade_qualification.native_controlled_account_source_v6")
        is source
        and vars(source).get("_REQUIRE_SOURCE_KERNEL_V6") is kernel
        and type(functions) is tuple
        and len(functions) == 3
        and tuple(name for name, _ in functions)
        == (
            "acquire_native_close_read_source_v1",
            "_replay_native_close_read_source_v1",
            "_inhibit_native_close_read_source_v1",
        )
        and all(vars(source).get(name) is function for name, function in functions)
    )
    previous = state.get("close_read_source_capture")
    if previous is None:
        state["close_read_source_capture"] = actual
    else:
        _need(
            previous[0] is source
            and previous[1] is kernel
            and all(
                left[0] == right[0] and left[1] is right[1]
                for left, right in zip(previous[2], functions, strict=True)
            )
        )
    kernel()
    return source


def _captured_native_close_post_read_source_v1(state):
    from app.trade_qualification import native_account_risk_assembly_v1 as account

    actual = account._require_native_close_post_read_source_v1(key=_SOURCE_KEY_V1)
    _need(type(actual) is tuple and len(actual) == 3)
    source, kernel, functions = actual
    _need(
        sys.modules.get("app.trade_qualification.native_controlled_account_source_v6")
        is source
        and vars(source).get("_REQUIRE_SOURCE_KERNEL_V6") is kernel
        and tuple(name for name, _ in functions)
        == (
            "acquire_native_close_post_read_source_v1",
            "_replay_native_close_post_read_source_v1",
            "_inhibit_native_close_post_read_source_v1",
        )
        and all(vars(source).get(name) is function for name, function in functions)
    )
    previous = state.get("close_post_read_source_capture")
    if previous is None:
        state["close_post_read_source_capture"] = actual
    else:
        _need(
            previous[0] is source
            and previous[1] is kernel
            and all(
                left[0] == right[0] and left[1] is right[1]
                for left, right in zip(previous[2], functions, strict=True)
            )
        )
    kernel()
    return source


def _mint_native_flat_cleanup_completion_v1(state):
    _verify_prepared_cleanup_v1(state)
    _need(state.get("cleanup_completion") is None)
    _control_owner55._REQUIRE_KERNEL()
    controls = [
        (cap, value)
        for cap, value in _control_owner55._HANDLES.items()
        if type(cap) is _control_owner55.OwnedDemoControlSessionV2
        and type(value) is dict
        and value.get("owner") is state["source"].control_owner
        and value.get("factory") is state["source"].session_factory
        and value.get("ledger_scope") == state["source"].scope
    ]
    _need(len(controls) == 1)
    state["zero_caller_controller"], state["zero_caller_control_state"] = controls[0]
    selection = controls[0][1]["identity_selection"]
    selected = _selector._SELECTIONS.get(selection)
    _need(
        type(selection) is _selector.ConfiguredDemoIdentitySelectionV2
        and type(selected) is dict
        and selected.get("record") is controls[0][1]["identity_record"]
        and type(selected.get("factories")) is _selector.ConfiguredNativeDemoFactoriesV2
        and selected["factories"].writer_factory is state["source"].session_factory
    )
    state["zero_caller_selection"], state["zero_caller_factories"] = (
        selection,
        selected["factories"],
    )
    completion = object.__new__(OwnedNativeFlatCleanupCompletionV1)
    state["cleanup_completion"] = completion
    state["cleanup_completion_view"] = NativeFlatCleanupCompletionViewV1(
        state["root_flat_settlement_view"],
        state["closed_transport_handle"],
        state["ledger_completion"],
        state["source_completion"],
    )
    _CLEANUP_COMPLETIONS[completion] = state
    return completion


def _take_native_flat_cleanup_completion_for_root_v1(completion, *, key):
    """Root consumes actual Source+ledger completion, never matching DATA."""
    from app.exchange.okx import native_demo_write_v2 as write

    _need(
        key is write._POSTSUBMIT_SETTLEMENT_KEY_V1
        and type(completion) is OwnedNativeFlatCleanupCompletionV1
        and completion in _CLEANUP_COMPLETIONS
    )
    state = _retained_cleanup_identity_v1(_CLEANUP_COMPLETIONS[completion])
    _need(
        state.get("cleanup_completion") is completion
        and state.get("root_cleanup_completion_taken") is not True
    )
    state["root_cleanup_completion_taken"] = True
    _verify_prepared_cleanup_v1(state)
    return state["cleanup_completion_view"]


class _CleanupAttempt(Coroutine):
    def __init__(self, state):
        self.state, self.body = state, _run_flat_cleanup_v1(state)

    def __await__(self):
        return self

    def __iter__(self):
        return self

    def __next__(self):
        return self.send(None)

    def send(self, value):
        try:
            return self.body.send(value)
        except BaseException:
            if self.state.get("cleanup_status") != "FINALIZED":
                self.state["cleanup_status"] = "UNKNOWN"
            raise

    def throw(self, *arguments):
        try:
            return self.body.throw(*arguments)
        except BaseException:
            if self.state.get("cleanup_status") != "FINALIZED":
                self.state["cleanup_status"] = "UNKNOWN"
            raise

    def close(self):
        try:
            return self.body.close()
        finally:
            if self.state.get("cleanup_status") != "FINALIZED":
                self.state["cleanup_status"] = "UNKNOWN"


def settle_owned_native_reconciliation_v1(result):
    """Current genuine zero result only; no DATA flag can release an owner."""
    from app.exchange.okx import native_demo_write_v2 as write

    state, _, _ = _flat_result_record_for_root_settlement_v1(
        result, key=write._POSTSUBMIT_SETTLEMENT_KEY_V1
    )
    _need(
        state.get("cleanup_status") is None
        and state.get("root_flat_settlement_taken") is not True
    )
    # Retain/spend this orchestration synchronously. Abandonment never deletes
    # the real Root/R7/source owners or creates a second request/cleanup retry.
    state["cleanup_status"] = "STARTED"
    return _CleanupAttempt(state)


def _zero_source_settlement_state_v2(cap, key):
    from app.trade_qualification import native_controlled_account_source_v6 as source

    _need(
        key is source._ZERO_FINAL_CLEANUP_KEY_V2
        and type(cap) is _OwnedNativeFlatSettlementV1
    )
    state = _retained_cleanup_identity_v1(_FLAT_SETTLEMENTS.get(cap))
    return _owned_settlement_v1(
        cap, after_root=state.get("root_finalization_returned") is True
    )


def _replay_native_zero_source_readset_v2(cap, *, key):
    state = _zero_source_settlement_state_v2(cap, key)
    closure = state["verified_flat_closure"]
    _need(
        type(closure) is tuple
        and len(closure) == 4
        and closure[1].classification == "terminal_zero_fill_flat"
        and type(closure[2]) is tuple
        and len(closure[2]) == 19
        and all(
            type(row) is tuple
            and len(row) == 2
            and type(row[0]) is str
            and 0 < len(row[0]) <= 96
            and row[0].isascii()
            and type(row[1]) is read.DemoReconciliationObservationV1
            for row in closure[2]
        )
        and closure[0] == state["root_flat_settlement_view"].closure_outcome_id
    )
    return state["source"].source_generation, closure[0], closure[2]


def _replay_native_zero_r7_retirement_for_source_v2(cap, *, key):
    from app.trade_qualification import native_ledger_runtime_v2 as ledger

    state = _zero_source_settlement_state_v2(cap, key)
    _need(state.get("root_finalization_returned") is True)
    actual = ledger.replay_native_zero_r7_retirement_v2(
        state["ledger_completion"], key=_SOURCE_SETTLEMENT_KEY_V1
    )
    _need(actual is state.get("r7_retirement_receipt"))
    return actual


def _replay_native_zero_root_finalization_v2(cap, *, key):
    state = _zero_source_settlement_state_v2(cap, key)
    actual = state.get("root_finalization_receipt")
    expected = (
        "ctcc.native_transport_resources_finalized.v1",
        state["root_flat_settlement_view"].closure_outcome_id,
        state["root_flat_settlement_view"].closure_receipt_sha256,
    )
    _need(
        type(actual) is tuple
        and actual == expected
        and state.get("root_finalization_returned") is True
    )
    return actual


def _zero_persistence_clock_v2(state):
    _retained_cleanup_identity_v1(state)
    _need(
        _ZERO_PERSISTENCE_V2.get(state["binding"]) is state
        and state.get("root_finalization_returned") is True
        and state.get("source_retirement_call_returned") is True
        and state.get("zero_ledger_finalized") is True
    )
    original, previous = (
        state["zero_persistence_started"],
        state["zero_persistence_last"],
    )
    stamp = native_stamp()
    _need(
        all(
            previous[name] <= stamp[name] < original[name] + 5_000_000_000
            for name in ("utc_ns", "monotonic_ns")
        ),
        "native_zero_cleanup_persistence_deadline",
    )
    state["zero_persistence_last"] = stamp
    source = _captured_flat_cleanup_source_v1(state)
    if state.get("zero_source_compacted") is True:
        receipt = source._ZERO_FINAL_TERMINAL_V2.get(state["zero_source_retirement"])
        _need(
            receipt is state.get("zero_source_compaction_receipt")
            and type(receipt) is tuple
            and len(receipt) == 4
            and receipt[0] == "ctcc.native_zero_final_source_compacted.v2"
            and receipt[2] == state["verified_zero_cleanup_readback"][0]
        )
    else:
        receipt = source._replay_native_zero_final_source_retirement_v2(
            state["zero_source_retirement"], key=_SOURCE_SETTLEMENT_KEY_V1
        )
        _need(receipt is state["zero_source_retirement_json"])
    return state


def _scan_native_zero_cleanup_persistence_v2(binding, raw, *, key):
    from app.database.repositories import native_postsubmit_v1 as repository

    _need(
        key is repository._SOURCE_KEY_V1
        and type(binding) is object
        and type(raw) is bytes
    )
    state = _zero_persistence_clock_v2(_ZERO_PERSISTENCE_V2.get(binding))
    source = _captured_flat_cleanup_source_v1(state)
    source._scan_native_zero_final_retained_data_v2(
        state["zero_source_retirement"], raw, key=_SOURCE_SETTLEMENT_KEY_V1
    )
    _zero_persistence_clock_v2(state)


def _take_native_zero_cleanup_committed_for_source_v2(committed, *, key):
    from app.trade_qualification import native_controlled_account_source_v6 as source

    _NATIVE_GUARD()
    _need(
        key is source._ZERO_FINAL_CLEANUP_KEY_V2
        and type(committed) is OwnedNativeZeroCleanupCommittedV2
        and committed in _ZERO_COMMITTED_V2
    )
    state = _zero_persistence_clock_v2(_ZERO_COMMITTED_V2[committed])
    _need(
        state.get("zero_committed_cap") is committed
        and state.get("zero_committed_taken") is not True
    )
    proof = state["verified_zero_cleanup_readback"]
    _need(
        type(proof) is tuple
        and len(proof) == 4
        and type(proof[1]) is bytes
        and len(proof[1]) <= 320000
        and facts.decode(proof[1], 320000)["outcome_id"] == proof[0]
        and facts.decode(proof[1], 320000)["capture_json"].encode() == proof[2]
        and facts.decode(proof[2], 140000)["version"]
        == "ctcc.native_zero_trade_cleanup.v2"
    )
    state["zero_committed_taken"] = True
    return NativeZeroCleanupCommittedForSourceV2(
        state["zero_source_retirement"], proof[0], proof[1], proof[3]
    )


def _compact_native_zero_post_completion_v2(state):
    from app.exchange.okx import native_demo_write_v2 as write

    _zero_persistence_clock_v2(state)
    _need(
        state.get("zero_source_compacted") is True
        and state.get("zero_committed_taken") is True
    )
    handle = state["closed_transport_handle"]
    _need(
        handle not in write._native_settlement._CLOSED
        and handle not in write._native_settlement._UNKNOWN
        and state["root_handle"] not in write._GATES
        and state["response_source"].transport_facts.transport_context.client
        not in write._CLIENT_GATES
    )
    controller, ctrl = (
        state["zero_caller_controller"],
        state["zero_caller_control_state"],
    )
    _control_owner55._REQUIRE_KERNEL()
    _need(
        _control_owner55._HANDLES.get(controller) is None
        and _control_owner55._OWNERS.get(controller) is None
        and _control_owner55._ACTIVE.get(id(ctrl)) is None
        and _control_owner55._UNKNOWN.get(id(ctrl)) is None
    )
    result = NativeZeroReconciliationSettlementResultV2(
        state["reservation_id"],
        state["root_flat_settlement_view"].closure_outcome_id,
        state["root_flat_settlement_view"].closure_receipt_sha256,
        state["verified_zero_cleanup_readback"][0],
    )
    record = {
        "result": result,
        "controller": controller,
        "selection": state["zero_caller_selection"],
        "factories": state["zero_caller_factories"],
        "task": state["task"],
        "loop": state["loop"],
        "pid": state["pid"],
        "thread": state["thread"],
        "taken": False,
        "disposal_phase": "PENDING",
    }
    if len(_ZERO_SETTLED_RESULTS_V2) >= 16:
        completed = [
            identity
            for identity, value in _ZERO_SETTLED_RESULTS_V2.items()
            if value["disposal_phase"] == "CLOSED"
        ]
        _need(bool(completed), "native_zero_caller_cleanup_capacity")
        _ZERO_SETTLED_RESULTS_V2.pop(completed[0])
    _ZERO_SETTLED_RESULTS_V2[id(result)] = record
    _FLAT_SETTLEMENTS.pop(state["flat_source_settlement_cap"])
    _CLEANUP_COMPLETIONS.pop(state["cleanup_completion"])
    _ZERO_COMMITTED_V2.pop(state["zero_committed_cap"])
    _ZERO_PERSISTENCE_V2.pop(state["binding"])
    _UNKNOWN.pop(state["binding"])
    state.clear()
    state.update(closed=True, cleanup_status="FINALIZED")
    return result


def _take_native_zero_caller_completion_v2(result, *, key):
    _NATIVE_GUARD()
    _need(
        type(key) is object
        and vars(_caller).get("_SETTLEMENT_KEY_V1") is key
        and type(result) is NativeZeroReconciliationSettlementResultV2
    )
    record = _ZERO_SETTLED_RESULTS_V2.get(id(result))
    _need(
        type(record) is dict
        and record["result"] is result
        and record["taken"] is False
        and record["task"] is asyncio.current_task()
        and record["loop"] is asyncio.get_running_loop()
        and record["pid"] == os.getpid()
        and record["thread"] == threading.get_ident()
    )
    record["taken"] = True
    return NativeZeroCallerCompletionV2(record["controller"], result.cleanup_outcome_id)


async def dispose_owned_native_zero_caller_resources_v2(result, *, key):
    _NATIVE_GUARD()
    _need(
        type(key) is object
        and vars(_caller).get("_SETTLEMENT_KEY_V1") is key
        and type(result) is NativeZeroReconciliationSettlementResultV2
    )
    record = _ZERO_SETTLED_RESULTS_V2.get(id(result))
    _need(
        type(record) is dict
        and record["result"] is result
        and record["taken"] is True
        and record["disposal_phase"] == "PENDING"
        and record["task"] is asyncio.current_task()
        and record["loop"] is asyncio.get_running_loop()
        and record["pid"] == os.getpid()
        and record["thread"] == threading.get_ident()
    )
    selection, factories = record["selection"], record["factories"]
    _need(
        type(selection) is _selector.ConfiguredDemoIdentitySelectionV2
        and type(factories) is _selector.ConfiguredNativeDemoFactoriesV2
        and _selector._SELECTIONS.get(selection) is not None
        and _selector._SELECTIONS[selection]["factories"] is factories
    )
    record["disposal_phase"] = "CLOSING"
    started = native_stamp()
    try:
        async with asyncio.timeout_at(asyncio.get_running_loop().time() + 5):
            await factories.frame_reader_engine.dispose()
            stamp = native_stamp()
            _need(
                all(
                    started[name] <= stamp[name] < started[name] + 5_000_000_000
                    for name in ("utc_ns", "monotonic_ns")
                )
            )
            await factories.custody_engine.dispose()
            stamp = native_stamp()
            _need(
                all(
                    started[name] <= stamp[name] < started[name] + 5_000_000_000
                    for name in ("utc_ns", "monotonic_ns")
                )
            )
            selection.close()
            _need(selection not in _selector._SELECTIONS)
        record["disposal_phase"] = "CLOSED"
        record.pop("selection")
        record.pop("factories")
    except BaseException:
        record["disposal_phase"] = "UNKNOWN"
        raise


def _cleanup_clock_v1(state):
    current = native_stamp()
    previous, original = state["cleanup_last"], state["cleanup_original"]
    _need(
        all(
            previous[name] <= current[name] < original[name] + 5_000_000_000
            for name in ("utc_ns", "monotonic_ns")
        ),
        "native_flat_cleanup_deadline",
    )
    state["cleanup_last"] = current
    return current


async def _run_flat_cleanup_v1(state):
    from app.exchange.okx import native_demo_write_v2 as write
    from app.trade_qualification import native_ledger_runtime_v2 as ledger

    try:
        source = _captured_flat_cleanup_source_v1(state)
        # Root's own fixed5s observer never resends or flushes pending TLS.
        handle = await write.close_owned_native_demo_transport_after_flat_v1(
            state["owned_flat_result"]
        )
        cap = _own_closed_flat_settlement_v1(state, handle)
        stamp = native_stamp()
        state["cleanup_original"], state["cleanup_last"] = stamp, stamp
        deadline = asyncio.get_running_loop().time() + 5
        # A separate negative budget, derived once; no trade/read lease renewal.
        async with asyncio.timeout_at(deadline):
            _cleanup_clock_v1(state)
            source = _captured_flat_cleanup_source_v1(state)
            state[
                "source_prepared"
            ] = await source.prepare_native_zero_final_source_cleanup_v2(
                cap, key=_SOURCE_SETTLEMENT_KEY_V1
            )
            _cleanup_clock_v1(state)
            source = _captured_flat_cleanup_source_v1(state)
            state["ledger_completion"] = ledger.prepare_native_flat_ledger_cleanup_v1(
                cap, key=_SOURCE_SETTLEMENT_KEY_V1
            )
            state["source_completion"] = (
                source.finish_native_zero_final_source_cleanup_v2(
                    state["source_prepared"], key=_SOURCE_SETTLEMENT_KEY_V1
                )
            )
            _cleanup_clock_v1(state)
            completion = _mint_native_flat_cleanup_completion_v1(state)
            # No authenticator has been removed. Root independently consumes
            # our actual Source+ledger completion before dropping its maps.
            _cleanup_clock_v1(state)
            finalized = write._finalize_native_closed_transport_settlement_v1(
                handle, completion, key=_ROOT_TRANSPORT_SETTLEMENT_KEY_V1
            )
            expected = (
                "ctcc.native_transport_resources_finalized.v1",
                state["root_flat_settlement_view"].closure_outcome_id,
                state["root_flat_settlement_view"].closure_receipt_sha256,
            )
            _need(type(finalized) is tuple and finalized == expected)
            state["root_finalization_returned"] = True
            state["root_finalization_receipt"] = finalized
            _owned_settlement_v1(cap, after_root=True)
            # Real R7/G12/candidate/source release follows confirmed Root map
            # retirement. Source independently verifies its actual registries.
            _cleanup_clock_v1(state)
            state["r7_retirement_receipt"] = (
                ledger.retire_native_flat_ledger_cleanup_v1(
                    state["ledger_completion"], key=_SOURCE_SETTLEMENT_KEY_V1
                )
            )
            _cleanup_clock_v1(state)
            source = _captured_flat_cleanup_source_v1(state)
            state["zero_source_retirement"] = (
                source.retire_native_zero_final_source_cleanup_v2(
                    state["source_completion"], key=_SOURCE_SETTLEMENT_KEY_V1
                )
            )
            state["source_retirement_call_returned"] = True
            state["zero_source_retirement_json"] = (
                source._replay_native_zero_final_source_retirement_v2(
                    state["zero_source_retirement"], key=_SOURCE_SETTLEMENT_KEY_V1
                )
            )
            state["ledger_retirement_receipt"] = (
                ledger.finalize_native_flat_ledger_cleanup_v1(
                    state["ledger_completion"], key=_SOURCE_SETTLEMENT_KEY_V1
                )
            )
            state["zero_ledger_finalized"] = True
        # A new DATA-only five-second budget covers actual append/commit and
        # independent readback. It cannot issue an exchange request or Arm.
        from app.database.repositories import native_postsubmit_v1 as repository

        stamp = native_stamp()
        state["zero_persistence_started"], state["zero_persistence_last"] = stamp, stamp
        _ZERO_PERSISTENCE_V2[state["binding"]] = state
        async with asyncio.timeout_at(asyncio.get_running_loop().time() + 5):
            await repository.record_native_zero_cleanup_v2(state["binding"])
            _zero_persistence_clock_v2(state)
            committed = object.__new__(OwnedNativeZeroCleanupCommittedV2)
            state["zero_committed_cap"] = committed
            _ZERO_COMMITTED_V2[committed] = state
            source = _captured_flat_cleanup_source_v1(state)
            state["zero_source_compaction_receipt"] = (
                source.compact_native_zero_final_source_retirement_v2(
                    state["zero_source_retirement"],
                    committed,
                    key=_SOURCE_SETTLEMENT_KEY_V1,
                )
            )
            state["zero_source_compacted"] = True
        return _compact_native_zero_post_completion_v2(state)
    except BaseException as error:
        state["cleanup_status"] = "UNKNOWN"
        if isinstance(error, GeneratorExit):
            # Synchronous coroutine.close cannot complete an async stop/DB
            # await. Preserve every remaining owner and let close finish.
            raise
        # This operation already has an independently committed SQL zero/flat
        # proof. Its failure is resource custody, not new exposure. Never latch
        # or disarm a superseding database owner while retiring an old event.
        if state.get("source") is not None:
            try:
                owner = state["source"].control_owner
                _need(type(owner) is _control_owner55.original.DemoControlOwner)
                _control_owner55.original.DemoControlOwner._revoke(owner, stop=False)
            except BaseException:  # noqa: BLE001 -- preserve full UNKNOWN custody; no changed callback or scoped mutation.
                state["cleanup_local_revoke"] = "unconfirmed"
        if isinstance(error, asyncio.CancelledError):
            raise asyncio.CancelledError() from None
        if isinstance(error, (KeyboardInterrupt, SystemExit)):
            raise type(error)() from None
        raise NativePostsubmitError("native_flat_cleanup_unknown") from None


def _flat_result_record_for_root_settlement_v1(result, *, key):
    from app.exchange.okx import native_demo_write_v2 as write

    _need(
        key is write._POSTSUBMIT_SETTLEMENT_KEY_V1
        and type(result) is NativeReconciliationResultV1
    )
    _NATIVE_GUARD()
    matches = [
        state
        for state in _UNKNOWN.values()
        if type(state) is dict
        and state.get("owned_flat_result") is result
        and state.get("result") is result
    ]
    _need(len(matches) == 1)
    state = matches[0]
    _need(
        type(state.get("binding")) is object
        and _UNKNOWN.get(state["binding"]) is state
        and state["binding"] not in _LIVE
        and state.get("closed") is True
        and state.get("kind") == "current"
        and state["pid"] == os.getpid()
        and state["thread"] == threading.get_ident()
        and state["task"] is asyncio.current_task()
        and state["loop"] is asyncio.get_running_loop()
    )
    guard.check_saved_native_guard_v1(state["guard"])
    closure = state.get("verified_flat_closure")
    _need(
        type(closure) is tuple
        and len(closure) == 4
        and type(closure[1]) is facts.NativeReconciliationAssessmentV1
        and closure[1].classification == "terminal_zero_fill_flat"
        and type(closure[2]) is tuple
        and closure[2]
        and type(closure[3]) is tuple
        and len(closure[3]) == 10
        and result.classification == "terminal_zero_fill_flat"
        and result.risk_hold_retained is False
        and result.reservation_id == state["reservation_id"]
        and result.outcome_id == closure[0]
        and result.receipt_sha256 == facts.sha(closure[1].receipt_json)
    )
    # This is historical negative cleanup, so neither the spent trade lease nor
    # the completed GET budget is renewed or required healthy. Root's original
    # private record must still authenticate its exact retained identities.
    source = write._replay_native_response_for_reconciliation_v1(
        state["root_handle"], key=_SOURCE_KEY_V1
    )
    _need(
        type(source) is NativeSubmissionResponseDataV1
        and source is state["response_source"]
        and source.transport_facts.consumed.reservation_id == result.reservation_id
    )
    return state, source, closure


def _retained_filled_record_v1(result):
    from app.exchange.okx import native_demo_write_v2 as write

    _need(type(result) is NativeReconciliationResultV1)
    _NATIVE_GUARD()
    matches = [
        state
        for state in _UNKNOWN.values()
        if type(state) is dict
        and state.get("owned_filled_result") is result
        and state.get("result") is result
    ]
    _need(len(matches) == 1)
    state = _retained_cleanup_identity_v1(matches[0])
    actual = state.get("verified_filled_readback")
    _need(
        type(actual) is tuple
        and len(actual) == 4
        and type(actual[1]) is facts.NativeReconciliationAssessmentV1
        and actual[1].classification == "filled_and_protected_hold_retained"
        and actual[1].filled_and_protected is True
        and actual[1].terminal_zero_fill_flat is False
        and type(actual[2]) is tuple
        and actual[2]
        and type(actual[3]) is tuple
        and len(actual[3]) == 10
        and result.classification == actual[1].classification
        and result.risk_hold_retained is True
        and result.reservation_id == state["reservation_id"]
        and result.outcome_id == actual[0]
        and result.receipt_sha256 == facts.sha(actual[1].receipt_json)
        and state.get("cleanup_status") is None
        and state.get("root_flat_settlement_taken") is not True
    )
    source = write._replay_native_response_for_reconciliation_v1(
        state["root_handle"], key=_SOURCE_KEY_V1
    )
    _need(
        type(source) is NativeSubmissionResponseDataV1
        and source is state["response_source"]
        and source.transport_facts.consumed.reservation_id == result.reservation_id
    )
    return state, source, actual


def _filled_result_record_for_close_v1(result, *, key):
    from app.trade_qualification import native_controlled_close_v1 as close

    _need(key is close._POSTSUBMIT_KEY_V1)
    return _retained_filled_record_v1(result)


def _replay_native_forensics_entry_v1(result, *, key):
    """Read genuine current entry evidence without consuming the close MOVE."""
    from app.trade_evidence import native_forensics_v2 as forensics

    _need(key is forensics._SOURCE_KEY)
    state, source, readback = _retained_filled_record_v1(result)
    _need(state["target"].facts.request_json == source.transport_facts.request_json)
    return forensics.NativeEntryForensicsSourceV2(state["target"], readback, source)


def _take_native_filled_result_for_close_v1(result, *, key):
    state, source, actual = _filled_result_record_for_close_v1(result, key=key)
    _need(state.get("filled_result_taken_for_close") is not True)
    state["filled_result_taken_for_close"] = True
    view = NativeFilledResultForCloseV1(
        state["root_handle"],
        source.observed,
        result,
        source,
        actual[0],
        facts.sha(actual[1].receipt_json),
        actual,
    )
    state["filled_result_view_for_close"] = view
    return view


def _replay_native_filled_result_for_close_v1(view, *, key):
    _need(type(view) is NativeFilledResultForCloseV1)
    state, source, actual = _filled_result_record_for_close_v1(view.result, key=key)
    _need(
        state.get("filled_result_taken_for_close") is True
        and state.get("filled_result_view_for_close") is view
        and view.response_handle is state["root_handle"]
        and view.observed_result is source.observed
        and view.response_data is source
        and view.readback is actual
        and view.readback_outcome_id == actual[0]
        and view.readback_receipt_sha256 == facts.sha(actual[1].receipt_json)
    )
    return view


def _take_native_flat_result_for_root_settlement_v1(result, *, key):
    """Synchronous current same-task MOVE after committed full closure readback."""
    state, source, closure = _flat_result_record_for_root_settlement_v1(result, key=key)
    _need(state.get("root_flat_settlement_taken") is not True)
    # Spend before exposing a view. Abandonment or later failure retains the
    # complete UNKNOWN invocation and cannot make this result reusable.
    state["root_flat_settlement_taken"] = True
    view = NativeFlatResultForRootSettlementV1(
        state["root_handle"],
        source.observed,
        result,
        source,
        closure[0],
        facts.sha(closure[1].receipt_json),
        closure,
    )
    state["root_flat_settlement_view"] = view
    return view


def _replay_native_flat_result_for_root_settlement_v1(view, *, key):
    """Root's fixed post-await replay; copied/rebound view identities refuse."""
    _need(type(view) is NativeFlatResultForRootSettlementV1)
    state, source, closure = _flat_result_record_for_root_settlement_v1(
        view.result, key=key
    )
    _need(
        state.get("root_flat_settlement_taken") is True
        and state.get("root_flat_settlement_view") is view
        and view.response_handle is state["root_handle"]
        and view.observed_result is source.observed
        and view.response_data is source
        and view.closure_readback is closure
        and view.closure_outcome_id == closure[0]
        and view.closure_receipt_sha256 == facts.sha(closure[1].receipt_json)
    )
    return view


def _filled_root_key_v1(key):
    from app.exchange.okx import native_demo_write_v2 as write

    _need(
        type(key) is object
        and vars(write).get("_POSTSUBMIT_FILLED_SETTLEMENT_KEY_V1") is key
    )
    return write


def _retained_filled_flat_state_v1(state):
    _need(
        type(state) is dict
        and type(state.get("binding")) is object
        and _UNKNOWN.get(state["binding"]) is state
        and state["binding"] not in _LIVE
        and state.get("closed") is True
        and state["kind"] == "controlled_close_post"
        and state["task"] is asyncio.current_task()
        and state["loop"] is asyncio.get_running_loop()
        and state["pid"] == os.getpid()
        and state["thread"] == threading.get_ident()
    )
    guard.check_saved_native_guard_v1(state["guard"])
    return state


def _filled_flat_result_record_v1(result):
    from app.exchange.okx import native_demo_write_v2 as write
    from app.trade_qualification import native_controlled_close_v1 as close

    _NATIVE_GUARD()
    _need(type(result) is NativeReconciliationResultV1)
    matches = [
        state
        for state in _UNKNOWN.values()
        if type(state) is dict
        and state.get("owned_filled_flat_result") is result
        and state.get("result") is result
    ]
    _need(len(matches) == 1)
    state = _retained_filled_flat_state_v1(matches[0])
    proof = state.get("verified_filled_flat_closure")
    forensic = state.get("verified_closed_forensics")
    _need(
        type(proof) is tuple
        and len(proof) == 7
        and type(proof[0]) is str
        and type(proof[1]) is _close_final_facts.NativeClosedTradeDataV1
        and type(proof[2]) is tuple
        and proof[2]
        and type(proof[3]) is tuple
        and len(proof[3]) == 10
        and result.classification == "own_entry_and_close_filled_flat"
        and result.risk_hold_retained is False
        and result.reservation_id == state["reservation_id"]
        and result.outcome_id == proof[0]
        and result.receipt_sha256 == facts.sha(proof[1].receipt_json)
        and type(forensic) is tuple
        and len(forensic) == 5
        and type(forensic[1]) is _closed_forensics.NativeClosedForensicsDataV55
        and facts.decode(forensic[4], 140000)["closure_outcome_id"] == proof[0]
        and state.get("filled_ledger_finalized") is not True
    )
    close_data = write._replay_native_close_response_for_reconciliation_v1(
        state["root_handle"], key=close._POSTSUBMIT_KEY_V1
    )
    close_view = state["close_response_view"].close_intent_view
    first, entry_data, _ = _retained_filled_record_v1(
        close_view.filled_result_view.result
    )
    _need(
        close_data is state["response_source"]
        and close_data is state["close_response_view"].response_data
        and close_data.entry_response_handle
        is close_view.filled_result_view.response_handle
        and entry_data is close_view.filled_result_view.response_data
        and proof[6] == close_view.intent_readback[2]
        and proof[5] == state["verified_closed_trade_readback"][0]
        and proof[4] == state["verified_closed_trade_readback"][4]
    )
    return state, first, entry_data, close_data, proof


def _take_native_filled_flat_result_for_root_settlement_v1(result, *, key):
    _filled_root_key_v1(key)
    state, first, entry, closing, proof = _filled_flat_result_record_v1(result)
    _need(state.get("filled_root_result_taken") is not True)
    state["filled_root_result_taken"] = True
    intent = state["close_response_view"].close_intent_view
    view = NativeFilledFlatResultForRootSettlementV1(
        intent.filled_result_view.response_handle,
        entry.observed,
        result,
        entry,
        state["root_handle"],
        closing,
        proof[0],
        facts.sha(proof[1].receipt_json),
        proof,
        first["source"].source_generation,
        intent.source_generation,
        state["source"].source_generation,
    )
    state["filled_root_result_view"] = view
    return view


def _replay_native_filled_flat_result_for_root_settlement_v1(view, *, key):
    _filled_root_key_v1(key)
    _need(type(view) is NativeFilledFlatResultForRootSettlementV1)
    state, first, entry, closing, proof = _filled_flat_result_record_v1(view.result)
    intent = state["close_response_view"].close_intent_view
    _need(
        state.get("filled_root_result_taken") is True
        and state["filled_root_result_view"] is view
        and view.response_data is entry
        and view.observed_result is entry.observed
        and view.close_response_data is closing
        and view.close_response_handle is state["root_handle"]
        and view.response_handle is intent.filled_result_view.response_handle
        and view.closure_readback is proof
        and view.closure_outcome_id == proof[0]
        and view.closure_receipt_sha256 == facts.sha(proof[1].receipt_json)
        and view.original_source_generation is first["source"].source_generation
        and view.preflight_source_generation is intent.source_generation
        and view.final_source_generation is state["source"].source_generation
    )
    return view


def _take_native_filled_flat_cleanup_completion_for_root_v1(completion, *, key):
    write = _filled_root_key_v1(key)
    _NATIVE_GUARD()
    _need(
        type(completion) is OwnedNativeFilledFlatCleanupCompletionV1
        and completion in _FILLED_CLEANUP_COMPLETIONS
    )
    state = _retained_filled_flat_state_v1(_FILLED_CLEANUP_COMPLETIONS[completion])
    _need(
        state.get("filled_cleanup_completion") is completion
        and state.get("filled_root_completion_taken") is not True
    )
    # Revalidate the exact actually prepared Source/ledger opaque completions.
    source_view, ledger_view = _verify_prepared_filled_cleanup_v1(state)
    paired = write._replay_native_closed_transports_for_settlement_v1(
        state["closed_transports_handle"], key=_ROOT_FILLED_TRANSPORT_SETTLEMENT_KEY_V1
    )
    view = state["filled_cleanup_completion_view"]
    _need(
        type(view) is NativeFilledFlatCleanupCompletionViewV1
        and view.filled_result_view
        is paired.filled_result_view
        is state["filled_root_result_view"]
        and view.closed_transports_handle is paired.closed_transports_handle
        and view.ledger_completion is state["filled_ledger_completion"]
        and view.source_completion is state["filled_source_completion"]
        and source_view.filled_result_view
        is ledger_view.filled_result_view
        is view.filled_result_view
    )
    state["filled_root_completion_taken"] = True
    return view


def _owned_filled_settlement_v1(cap, *, finalized=False):
    from app.exchange.okx import native_demo_write_v2 as write

    _NATIVE_GUARD()
    _need(
        type(cap) is _OwnedNativeFilledFlatSettlementV1 and cap in _FILLED_SETTLEMENTS
    )
    state = _retained_filled_flat_state_v1(_FILLED_SETTLEMENTS[cap])
    _need(state.get("filled_settlement_cap") is cap)
    if finalized:
        view, completion, completed, receipt = (
            write._replay_native_finalized_closed_transports_for_source_v1(
                state["closed_transports_handle"],
                key=_ROOT_FILLED_TRANSPORT_SETTLEMENT_KEY_V1,
            )
        )
        _need(
            state.get("filled_root_finalized") is True
            and completion is state["filled_cleanup_completion"]
            and completed is state["filled_cleanup_completion_view"]
            and receipt is state["filled_root_finalization_receipt"]
        )
    else:
        view = write._replay_native_closed_transports_for_settlement_v1(
            state["closed_transports_handle"],
            key=_ROOT_FILLED_TRANSPORT_SETTLEMENT_KEY_V1,
        )
    _need(
        view is state["closed_transports_view"]
        and view.filled_result_view is state["filled_root_result_view"]
        and view.closed_transports_handle is state["closed_transports_handle"]
    )
    return state


def _own_native_filled_settlement_v1(state, handle):
    from app.exchange.okx import native_demo_write_v2 as write

    _retained_filled_flat_state_v1(state)
    _need(state.get("filled_settlement_cap") is None)
    paired = write._take_native_closed_transports_for_settlement_v1(
        handle, key=_ROOT_FILLED_TRANSPORT_SETTLEMENT_KEY_V1
    )
    _need(
        type(paired) is write._native_filled_settlement.NativeClosedTransportsViewV1
        and paired.filled_result_view is state["filled_root_result_view"]
        and paired.close_response_handle is state["root_handle"]
        and paired.closure_outcome_id == state["verified_filled_flat_closure"][0]
    )
    flat = paired.filled_result_view
    cap = object.__new__(_OwnedNativeFilledFlatSettlementV1)
    state["closed_transports_handle"], state["closed_transports_view"] = handle, paired
    state["filled_settlement_cap"] = cap
    state["filled_source_settlement_view"] = NativeFilledFlatSourceSettlementV1(
        cap,
        flat,
        paired,
        (
            flat.original_source_generation,
            flat.preflight_source_generation,
            flat.final_source_generation,
        ),
        flat.response_data.transport_facts.transport_binding,
        flat.close_response_data.transport_facts.transport_binding,
        handle,
    )
    _FILLED_SETTLEMENTS[cap] = state
    return cap


def _take_native_filled_flat_source_settlement_v1(cap, *, key):
    from app.trade_qualification import native_controlled_account_source_v6 as source

    _need(key is source._FILLED_FLAT_CLEANUP_KEY_V1)
    state = _owned_filled_settlement_v1(cap)
    _need(state.get("filled_source_taken") is not True)
    state["filled_source_taken"] = True
    return state["filled_source_settlement_view"]


def _replay_native_filled_flat_source_settlement_v1(cap, *, key):
    from app.trade_qualification import native_controlled_account_source_v6 as source

    _need(key is source._FILLED_FLAT_CLEANUP_KEY_V1)
    state = _owned_filled_settlement_v1(cap)
    _need(state.get("filled_source_taken") is True)
    return state["filled_source_settlement_view"]


def _replay_native_filled_flat_source_after_finalization_v1(cap, *, key):
    from app.trade_qualification import native_controlled_account_source_v6 as source

    _need(key is source._FILLED_FLAT_CLEANUP_KEY_V1)
    state = _owned_filled_settlement_v1(cap, finalized=True)
    _need(state.get("filled_source_taken") is True)
    return state["filled_source_settlement_view"]


def _replay_native_filled_flat_source_readsets_v1(cap, *, key):
    from app.trade_qualification import native_controlled_account_source_v6 as source

    _need(key is source._FILLED_FLAT_CLEANUP_KEY_V1)
    _need(
        type(cap) is _OwnedNativeFilledFlatSettlementV1 and cap in _FILLED_SETTLEMENTS
    )
    candidate = _FILLED_SETTLEMENTS[cap]
    state = _owned_filled_settlement_v1(
        cap, finalized=candidate.get("filled_root_finalized") is True
    )
    intent = state["close_response_view"].close_intent_view
    first, _, _ = _retained_filled_record_v1(intent.filled_result_view.result)
    preflight = _UNKNOWN.get(intent.binding)
    _need(type(preflight) is dict and preflight.get("close_intent_view") is intent)
    guard.check_saved_native_guard_v1(preflight["guard"])
    document = facts.decode(intent.intent_readback[1], 140000)
    before = preflight["durable_close_preflight"]
    final = state["verified_filled_flat_closure"]
    _need(
        before[0] == document["preflight_manifest_outcome_id"]
        and final[4] == state["verified_closed_trade_readback"][4]
    )
    return (
        (
            first["source"].source_generation,
            first["durable_readback_manifest_id"],
            intent.filled_result_view.readback[2],
        ),
        (intent.source_generation, before[0], before[2]),
        (state["source"].source_generation, final[4], final[2]),
    )


def _replay_native_filled_flat_r7_retirement_for_source_v1(cap, *, key):
    from app.trade_qualification import native_controlled_account_source_v6 as source
    from app.trade_qualification import native_ledger_runtime_v2 as ledger

    _need(key is source._FILLED_FLAT_CLEANUP_KEY_V1)
    state = _owned_filled_settlement_v1(cap, finalized=True)
    receipt = ledger.replay_native_filled_flat_r7_retirement_v1(
        state["filled_ledger_completion"], key=_FILLED_SOURCE_SETTLEMENT_KEY_V1
    )
    _need(receipt is state["filled_r7_retirement"])
    return receipt, state["filled_source_settlement_view"].source_generations


def _captured_filled_cleanup_source_v1(state):
    from app.trade_qualification import native_account_risk_assembly_v1 as account

    actual = account._require_native_filled_flat_cleanup_source_v1(
        key=_FILLED_SOURCE_SETTLEMENT_KEY_V1
    )
    _need(type(actual) is tuple and len(actual) == 3)
    source, kernel, functions = actual
    names = (
        "prepare_native_filled_flat_source_cleanup_v1",
        "finish_native_filled_flat_source_cleanup_v1",
        "_replay_native_filled_flat_source_cleanup_completion_v1",
        "retire_native_filled_flat_source_cleanup_v1",
    )
    _need(
        sys.modules.get("app.trade_qualification.native_controlled_account_source_v6")
        is source
        and vars(source).get("_REQUIRE_SOURCE_KERNEL_V6") is kernel
        and tuple(name for name, _ in functions) == names
        and all(vars(source).get(name) is function for name, function in functions)
    )
    previous = state.get("filled_cleanup_source_capture")
    if previous is None:
        state["filled_cleanup_source_capture"] = actual
    else:
        _need(
            previous[0] is source
            and previous[1] is kernel
            and all(
                left[0] == right[0] and left[1] is right[1]
                for left, right in zip(previous[2], functions, strict=True)
            )
        )
    kernel()
    return source


def _take_native_filled_flat_ledger_settlement_v1(cap, *, key):
    from app.trade_qualification import native_ledger_runtime_v2 as ledger

    _need(key is ledger._FILLED_CLEANUP_KEY_V1)
    state = _owned_filled_settlement_v1(cap)
    _need(
        state.get("filled_ledger_taken") is not True
        and state.get("filled_source_prepared") is not None
    )
    state["filled_ledger_taken"] = True
    return state["filled_source_settlement_view"]


def _replay_native_filled_flat_ledger_settlement_v1(cap, *, key):
    from app.trade_qualification import native_ledger_runtime_v2 as ledger

    _need(key is ledger._FILLED_CLEANUP_KEY_V1)
    state = _owned_filled_settlement_v1(cap)
    _need(state.get("filled_ledger_taken") is True)
    return state["filled_source_settlement_view"]


def _replay_native_filled_flat_ledger_after_finalization_v1(cap, *, key):
    from app.trade_qualification import native_ledger_runtime_v2 as ledger

    _need(key is ledger._FILLED_CLEANUP_KEY_V1)
    state = _owned_filled_settlement_v1(cap, finalized=True)
    _need(state.get("filled_ledger_taken") is True)
    return state["filled_source_settlement_view"]


def _require_native_filled_flat_source_retired_for_ledger_v1(cap, *, key):
    from app.trade_qualification import native_ledger_runtime_v2 as ledger

    _need(key is ledger._FILLED_CLEANUP_KEY_V1)
    state = _owned_filled_settlement_v1(cap, finalized=True)
    _need(
        state.get("filled_source_retired") is True
        and state.get("filled_source_retirement") is not None
        and state.get("filled_close_retired") is True
        and state.get("filled_close_retirement") is not None
    )
    return state["filled_source_settlement_view"]


def _replay_native_filled_flat_for_close_cleanup_v1(cap, *, key):
    from app.trade_qualification import native_controlled_close_v1 as close

    _need(key is close._FILLED_CLEANUP_KEY_V1)
    state = _owned_filled_settlement_v1(cap, finalized=True)
    _need(
        state.get("filled_source_retired") is True
        and state.get("filled_source_retirement") is not None
        and state.get("filled_close_retired") is not True
    )
    return state["filled_source_settlement_view"]


def _verify_prepared_filled_cleanup_v1(state):
    from app.trade_qualification import native_ledger_runtime_v2 as ledger

    _owned_filled_settlement_v1(state["filled_settlement_cap"])
    source = _captured_filled_cleanup_source_v1(state)
    source_view = source._replay_native_filled_flat_source_cleanup_completion_v1(
        state["filled_source_completion"], key=_FILLED_SOURCE_SETTLEMENT_KEY_V1
    )
    ledger_view = ledger.replay_native_filled_flat_ledger_cleanup_v1(
        state["filled_ledger_completion"], key=_FILLED_SOURCE_SETTLEMENT_KEY_V1
    )
    _need(
        type(source_view) is source.NativeFilledFlatSourceCleanupCompletionViewV1
        and type(ledger_view) is ledger.NativeFilledFlatLedgerCleanupViewV1
        and source_view.settlement_cap is state["filled_settlement_cap"]
        and source_view.filled_result_view
        is ledger_view.filled_result_view
        is state["filled_root_result_view"]
        and source_view.closed_transports_handle
        is ledger_view.closed_transports_handle
        is state["closed_transports_handle"]
    )
    return source_view, ledger_view


def _take_native_filled_flat_retirement_for_root_v1(retirement, *, key):
    write = _filled_root_key_v1(key)
    _NATIVE_GUARD()
    _need(
        type(retirement) is OwnedNativeFilledFlatRetirementV1
        and retirement in _FILLED_RETIREMENTS
    )
    state = _retained_filled_flat_state_v1(_FILLED_RETIREMENTS[retirement])
    _need(
        state.get("filled_retirement") is retirement
        and state.get("filled_root_retirement_taken") is not True
        and state.get("filled_source_retired") is True
        and state.get("filled_ledger_finalized") is True
        and state.get("filled_close_retired") is True
    )
    paired, completion, completed, finalized = (
        write._replay_native_finalized_closed_transports_for_source_v1(
            state["closed_transports_handle"],
            key=_ROOT_FILLED_TRANSPORT_SETTLEMENT_KEY_V1,
        )
    )
    view = state["filled_retirement_view"]
    _need(
        type(view) is NativeFilledFlatRetirementViewV1
        and view.filled_result_view
        is paired.filled_result_view
        is state["filled_root_result_view"]
        and view.closed_transports_handle is paired.closed_transports_handle
        and view.cleanup_completion is completion is state["filled_cleanup_completion"]
        and completed is state["filled_cleanup_completion_view"]
        and finalized is state["filled_root_finalization_receipt"]
        and view.source_retirement is state["filled_source_retirement"]
        and view.ledger_retirement is state["filled_ledger_retirement"]
        and view.close_retirement is state["filled_close_retirement"]
    )
    state["filled_root_retirement_taken"] = True
    return view


def _filled_persistence_clock_v1(state):
    _retained_filled_flat_state_v1(state)
    _need(
        _FILLED_PERSISTENCE.get(state["binding"]) is state
        and state.get("filled_cleanup_status") == "RETIRED"
        and state.get("filled_root_retirement_taken") is True
        and state.get("filled_ledger_finalized") is True
        and state.get("filled_source_retired") is True
        and state.get("filled_close_retired") is True
    )
    original, previous = (
        state["filled_persistence_started"],
        state["filled_persistence_last"],
    )
    stamp = native_stamp()
    _need(
        all(
            previous[name] <= stamp[name] < original[name] + 5_000_000_000
            for name in ("utc_ns", "monotonic_ns")
        ),
        "native_filled_cleanup_persistence_deadline",
    )
    state["filled_persistence_last"] = stamp
    return state


def _scan_native_filled_cleanup_persistence_v1(binding, raw, *, key):
    from app.database.repositories import native_postsubmit_v1 as repository

    _need(
        key is repository._SOURCE_KEY_V1
        and type(binding) is object
        and type(raw) is bytes
    )
    state = _filled_persistence_clock_v1(_FILLED_PERSISTENCE.get(binding))
    source = _captured_filled_cleanup_source_v1(state)
    source._scan_native_filled_cleanup_retained_data_v1(
        state["filled_source_retirement"], raw, key=_FILLED_SOURCE_SETTLEMENT_KEY_V1
    )
    _filled_persistence_clock_v1(state)


def _take_native_filled_cleanup_committed_for_source_v1(completion, *, key):
    from app.trade_qualification import native_controlled_account_source_v6 as source

    _NATIVE_GUARD()
    _need(
        key is source._FILLED_FLAT_CLEANUP_KEY_V1
        and type(completion) is OwnedNativeFilledCleanupCommittedV1
        and completion in _FILLED_COMMITTED
    )
    state = _retained_filled_flat_state_v1(_FILLED_COMMITTED[completion])
    _need(
        state.get("filled_committed_cap") is completion
        and state.get("filled_committed_source_taken") is not True
    )
    actual = state["verified_filled_cleanup_readback"]
    _need(
        type(actual) is tuple
        and len(actual) == 4
        and type(actual[0]) is str
        and type(actual[1]) is bytes
        and 0 < len(actual[1]) <= 320000
        and type(actual[3]) is tuple
        and len(actual[3]) == 10
        and facts.decode(actual[2], 140000)["version"]
        == "ctcc.native_closed_trade_cleanup.v1"
        and facts.decode(actual[1], 320000)["outcome_id"] == actual[0]
        and facts.decode(actual[1], 320000)["capture_json"].encode() == actual[2]
    )
    state["filled_committed_source_taken"] = True
    return NativeFilledCleanupCommittedForSourceV1(
        state["filled_source_retirement"], actual[0], actual[1], actual[3]
    )


def _replay_native_filled_cleanup_for_close_compaction_v1(completion, *, key):
    from app.trade_qualification import native_controlled_close_v1 as close

    _NATIVE_GUARD()
    _need(
        key is close._FILLED_CLEANUP_KEY_V1
        and type(completion) is OwnedNativeFilledCleanupCommittedV1
        and completion in _FILLED_COMMITTED
    )
    state = _retained_filled_flat_state_v1(_FILLED_COMMITTED[completion])
    _need(
        state.get("filled_committed_cap") is completion
        and state.get("filled_committed_source_taken") is True
        and state.get("filled_retired_data_compacted") is True
        and state.get("filled_root_retirement_taken") is True
        and state.get("filled_close_data_compacted") is not True
    )
    proof = state["verified_filled_cleanup_readback"]
    return NativeFilledCleanupCommittedForCloseV1(
        state["filled_close_retirement"], proof[0], facts.sha(proof[1])
    )


def _compact_native_filled_post_completion_v1(state):
    from app.exchange.okx import native_demo_write_v2 as write
    from app.trade_qualification import native_controlled_close_v1 as close

    _retained_filled_flat_state_v1(state)
    _need(
        state.get("filled_retired_data_compacted") is True
        and state.get("filled_close_data_compacted") is True
        and state.get("filled_root_retirement_taken") is True
        and state.get("filled_committed_source_taken") is True
    )
    handle, view = state["closed_transports_handle"], state["filled_root_result_view"]
    _need(
        handle not in write._native_filled_settlement._UNKNOWN
        and handle not in write._native_filled_settlement._CLOSED
        and handle not in write._native_filled_settlement._FINALIZED
        and view.response_handle not in write._GATES
        and view.close_response_handle not in write._GATES
        and state["filled_close_retirement"] not in close._FILLED_RETIRED
    )
    _control_owner55._REQUIRE_KERNEL()
    controller, control_state = (
        state["filled_caller_controller"],
        state["filled_caller_control_state"],
    )
    _need(
        _control_owner55._HANDLES.get(controller) is None
        and _control_owner55._OWNERS.get(controller) is None
        and _control_owner55._ACTIVE.get(id(control_state)) is None
        and _control_owner55._UNKNOWN.get(id(control_state)) is None
    )
    states = state["filled_compaction_states"]
    _need(
        type(states) is tuple
        and len(states) == 3
        and len({id(item) for item in states}) == 3
    )
    for actual in states:
        _need(
            type(actual) is dict
            and _UNKNOWN.get(actual["binding"]) is actual
            and actual["binding"] not in _LIVE
            and actual.get("closed") is True
            and actual["task"] is asyncio.current_task()
        )
        guard.check_saved_native_guard_v1(actual["guard"])
    cleanup, forensic, report = (
        state["verified_filled_cleanup_readback"],
        state["verified_closed_forensics"],
        state["filled_report_policy_result"],
    )
    result = NativeFilledReconciliationSettlementResultV1(
        state["reservation_id"],
        view.closure_outcome_id,
        view.closure_receipt_sha256,
        cleanup[0],
        forensic[0],
        report[2],
    )
    record = {
        "result": result,
        "controller": controller,
        "task": state["task"],
        "loop": state["loop"],
        "pid": state["pid"],
        "thread": state["thread"],
        "taken": False,
        "selection": state["filled_caller_selection"],
        "factories": state["filled_caller_factories"],
        "disposal_phase": "PENDING",
        "cleanup_row_sha256": facts.sha(cleanup[1]),
        "cleanup_read_metadata": cleanup[3],
    }
    for mapping, cap in (
        (_FILLED_SETTLEMENTS, state["filled_settlement_cap"]),
        (_FILLED_CLEANUP_COMPLETIONS, state["filled_cleanup_completion"]),
        (_FILLED_RETIREMENTS, state["filled_retirement"]),
        (_FILLED_COMMITTED, state["filled_committed_cap"]),
    ):
        _need(mapping.get(cap) is state)
    # No await/callback lies between complete checks and exact map removal.
    # Only completed tiny tombstones may leave the bounded ring; UNKNOWN
    # never reaches this routine and keeps its complete actual graph.
    if len(_FILLED_SETTLED_RESULTS) >= 16:
        closed = [
            identity
            for identity, saved in _FILLED_SETTLED_RESULTS.items()
            if saved.get("disposal_phase") == "CLOSED"
        ]
        _need(bool(closed), "native_filled_caller_cleanup_capacity_exhausted")
        _FILLED_SETTLED_RESULTS.pop(closed[0])
    _FILLED_SETTLED_RESULTS[id(result)] = record
    for mapping, cap in (
        (_FILLED_SETTLEMENTS, state["filled_settlement_cap"]),
        (_FILLED_CLEANUP_COMPLETIONS, state["filled_cleanup_completion"]),
        (_FILLED_RETIREMENTS, state["filled_retirement"]),
        (_FILLED_COMMITTED, state["filled_committed_cap"]),
    ):
        mapping.pop(cap)
    _FILLED_PERSISTENCE.pop(state["binding"])
    for actual in states:
        _UNKNOWN.pop(actual["binding"])
    for actual in states:
        actual.clear()
        actual.update(closed=True, filled_cleanup_status="COMPLETED")
    return result


def _take_native_filled_caller_completion_v1(result, *, key):
    _NATIVE_GUARD()
    _need(
        type(key) is object
        and vars(_caller).get("_SETTLEMENT_KEY_V1") is key
        and type(result) is NativeFilledReconciliationSettlementResultV1
    )
    record = _FILLED_SETTLED_RESULTS.get(id(result))
    _need(
        type(record) is dict
        and record["result"] is result
        and record["taken"] is False
        and record["task"] is asyncio.current_task()
        and record["loop"] is asyncio.get_running_loop()
        and record["pid"] == os.getpid()
        and record["thread"] == threading.get_ident()
    )
    record["taken"] = True
    return NativeFilledCallerCompletionV1(
        record["controller"], result.cleanup_outcome_id, result.report_spool_id
    )


async def dispose_owned_native_filled_caller_resources_v1(result, *, key):
    """Close only the actual held selector's owned reader/custody DB engines."""
    _NATIVE_GUARD()
    _need(
        type(key) is object
        and vars(_caller).get("_SETTLEMENT_KEY_V1") is key
        and type(result) is NativeFilledReconciliationSettlementResultV1
    )
    record = _FILLED_SETTLED_RESULTS.get(id(result))
    _need(
        type(record) is dict
        and record["result"] is result
        and record["taken"] is True
        and record["disposal_phase"] == "PENDING"
        and record["task"] is asyncio.current_task()
        and record["loop"] is asyncio.get_running_loop()
        and record["pid"] == os.getpid()
        and record["thread"] == threading.get_ident()
    )
    selection, factories = record["selection"], record["factories"]
    _need(
        type(selection) is _selector.ConfiguredDemoIdentitySelectionV2
        and type(factories) is _selector.ConfiguredNativeDemoFactoriesV2
        and _selector._SELECTIONS.get(selection) is not None
        and _selector._SELECTIONS[selection]["factories"] is factories
    )
    # Spend before the first await. Failure retains exact engines/selection
    # in this private record and in the caller; no changed cleanup callback is
    # invoked by a fallback, and no entry permission is renewed.
    record["disposal_phase"] = "CLOSING"
    started = native_stamp()
    try:
        async with asyncio.timeout_at(asyncio.get_running_loop().time() + 5):
            await factories.frame_reader_engine.dispose()
            stamp = native_stamp()
            _need(
                all(
                    started[name] <= stamp[name] < started[name] + 5_000_000_000
                    for name in ("utc_ns", "monotonic_ns")
                )
            )
            await factories.custody_engine.dispose()
            stamp = native_stamp()
            _need(
                all(
                    started[name] <= stamp[name] < started[name] + 5_000_000_000
                    for name in ("utc_ns", "monotonic_ns")
                )
            )
            selection.close()
            _need(selection not in _selector._SELECTIONS)
        record["disposal_phase"] = "CLOSED"
        record.pop("selection")
        record.pop("factories")
    except BaseException:
        record["disposal_phase"] = "UNKNOWN"
        raise


def _mint_native_filled_cleanup_completion_v1(state):
    source_view, ledger_view = _verify_prepared_filled_cleanup_v1(state)
    _need(state.get("filled_cleanup_completion") is None)
    namespace = state["source"].client.settings.submission_outbox_queue_namespace
    _need(type(namespace) is str)
    state["filled_report_namespace"] = namespace
    _control_owner55._REQUIRE_KERNEL()
    controls = [
        (cap, value)
        for cap, value in _control_owner55._HANDLES.items()
        if type(cap) is _control_owner55.OwnedDemoControlSessionV2
        and type(value) is dict
        and value.get("owner") is state["source"].control_owner
        and value.get("factory") is state["source"].session_factory
        and value.get("ledger_scope") == state["source"].scope
    ]
    _need(len(controls) == 1)
    state["filled_caller_controller"], state["filled_caller_control_state"] = controls[
        0
    ]
    selection = controls[0][1]["identity_selection"]
    selected = _selector._SELECTIONS.get(selection)
    _need(
        type(selection) is _selector.ConfiguredDemoIdentitySelectionV2
        and type(selected) is dict
        and selected.get("record") is controls[0][1]["identity_record"]
        and type(selected.get("factories")) is _selector.ConfiguredNativeDemoFactoriesV2
        and selected["factories"].writer_factory is state["source"].session_factory
    )
    state["filled_caller_selection"], state["filled_caller_factories"] = (
        selection,
        selected["factories"],
    )
    intent = state["close_response_view"].close_intent_view
    first, _, _ = _retained_filled_record_v1(intent.filled_result_view.result)
    preflight = _UNKNOWN.get(intent.binding)
    _need(type(preflight) is dict and preflight.get("close_intent_view") is intent)
    state["filled_compaction_states"] = (first, preflight, state)
    flat = state["filled_root_result_view"]
    receipt = facts.canonical(
        {
            "version": "ctcc.native_filled_flat_cleanup_prepared.v1",
            "reservation_id": state["reservation_id"],
            "closure_outcome_id": flat.closure_outcome_id,
            "closure_receipt_sha256": flat.closure_receipt_sha256,
            "source_cleanup_receipt_sha256": facts.sha(source_view.receipt_json),
            "forensics_outcome_id": state["verified_closed_forensics"][0],
            "forensics_sha256": facts.sha(
                state["verified_closed_forensics"][1].receipt_json
            ),
            "original_event_key": ledger_view.original_event_key,
            "request_sha256": ledger_view.request_sha256,
            "both_post_socket_observations": state[
                "closed_transports_view"
            ].socket_observations,
            "execution_authority": False,
            "order_retry_authority": False,
        }
    )
    _need(0 < len(receipt) <= 32768)
    result = object.__new__(OwnedNativeFilledFlatCleanupCompletionV1)
    state["filled_cleanup_completion"] = result
    state["filled_cleanup_completion_view"] = NativeFilledFlatCleanupCompletionViewV1(
        flat,
        state["closed_transports_handle"],
        state["filled_ledger_completion"],
        state["filled_source_completion"],
        receipt,
    )
    _FILLED_CLEANUP_COMPLETIONS[result] = state
    return result


def _mint_native_filled_retirement_v1(state):
    from app.trade_qualification import native_controlled_close_v1 as close

    source = _captured_filled_cleanup_source_v1(state)
    _owned_filled_settlement_v1(state["filled_settlement_cap"], finalized=True)
    _need(
        state.get("filled_retirement") is None
        and state.get("filled_source_retired") is True
        and state.get("filled_ledger_finalized") is True
        and state.get("filled_close_retired") is True
    )
    source_receipt = source._replay_native_filled_flat_source_retirement_v1(
        state["filled_source_retirement"], key=_FILLED_SOURCE_SETTLEMENT_KEY_V1
    )
    close_receipt = close.replay_native_filled_close_retirement_v1(
        state["filled_close_retirement"], key=_FILLED_SOURCE_SETTLEMENT_KEY_V1
    )
    _need(type(source_receipt) is bytes and type(close_receipt) is bytes)
    receipt = facts.canonical(
        {
            "version": "ctcc.native_filled_flat_authentication_retired.v1",
            "reservation_id": state["reservation_id"],
            "closure_outcome_id": state["filled_root_result_view"].closure_outcome_id,
            "closure_receipt_sha256": state[
                "filled_root_result_view"
            ].closure_receipt_sha256,
            "source_retirement_receipt_sha256": facts.sha(source_receipt),
            "ledger_retirement": state["filled_ledger_retirement"],
            "close_retirement_receipt_sha256": facts.sha(close_receipt),
            "root_finalized_receipt": state["filled_root_finalization_receipt"],
            "execution_authority": False,
            "order_retry_authority": False,
        }
    )
    _need(0 < len(receipt) <= 32768)
    result = object.__new__(OwnedNativeFilledFlatRetirementV1)
    state["filled_retirement"] = result
    state["filled_source_retirement_json"], state["filled_close_retirement_json"] = (
        source_receipt,
        close_receipt,
    )
    state["filled_retirement_view"] = NativeFilledFlatRetirementViewV1(
        state["filled_root_result_view"],
        state["closed_transports_handle"],
        state["filled_cleanup_completion"],
        state["filled_source_retirement"],
        state["filled_ledger_retirement"],
        state["filled_close_retirement"],
        receipt,
    )
    _FILLED_RETIREMENTS[result] = state
    return result


class _FilledCleanupAttempt(Coroutine):
    __slots__ = ("body", "state")

    def __init__(self, state):
        self.state, self.body = state, _run_filled_flat_cleanup_v1(state)

    def __await__(self):
        return self

    def __iter__(self):
        return self

    def __next__(self):
        return self.send(None)

    def send(self, value):
        try:
            return self.body.send(value)
        except BaseException:
            if self.state.get("filled_cleanup_status") not in ("RETIRED", "COMPLETED"):
                self.state["filled_cleanup_status"] = "UNKNOWN"
            raise

    def throw(self, *arguments):
        try:
            return self.body.throw(*arguments)
        except BaseException:
            if self.state.get("filled_cleanup_status") not in ("RETIRED", "COMPLETED"):
                self.state["filled_cleanup_status"] = "UNKNOWN"
            raise

    def close(self):
        try:
            return self.body.close()
        finally:
            if self.state.get("filled_cleanup_status") not in ("RETIRED", "COMPLETED"):
                self.state["filled_cleanup_status"] = "UNKNOWN"


def settle_owned_native_filled_reconciliation_v1(result):
    """Same-task owned SQL-flat result only; DATA flags never release resources."""
    state, _, _, _, _ = _filled_flat_result_record_v1(result)
    _need(
        state.get("filled_cleanup_status") is None
        and state.get("filled_root_result_taken") is not True
    )
    # A dropped/unstarted awaitable remains spent and strongly held.
    state["filled_cleanup_status"] = "STARTED"
    return _FilledCleanupAttempt(state)


class _FilledWorkflowAttempt(Coroutine):
    __slots__ = ("body", "state")

    def __init__(self, state):
        self.state, self.body = state, _run_owned_native_filled_demo_v1(state)

    def __await__(self):
        return self

    def __iter__(self):
        return self

    def __next__(self):
        return self.send(None)

    def send(self, value):
        return self.body.send(value)

    def throw(self, *arguments):
        return self.body.throw(*arguments)

    def close(self):
        return self.body.close()


def complete_owned_native_filled_demo_v1(result):
    """One genuine new entry -> owned negative close -> final durable cleanup."""
    _NATIVE_GUARD()
    _need(len(_FILLED_WORKFLOWS) < 16)
    # The existing fixed preflight moves the real filled-result capability
    # synchronously before this returned awaitable can be dropped.
    prepared = prepare_owned_native_close_intent_v1(result)
    token = object()
    state = {
        "token": token,
        "prepared": prepared,
        "owner": None,
        "phase": "PREPARING",
        "task": asyncio.current_task(),
        "loop": asyncio.get_running_loop(),
        "pid": os.getpid(),
        "thread": threading.get_ident(),
        "guard": {},
    }
    guard.save_native_guard_v1(state["guard"], _NATIVE_GUARD)
    _FILLED_WORKFLOWS[token] = state
    return _FilledWorkflowAttempt(state)


async def _run_owned_native_filled_demo_v1(state):
    from app.exchange.okx import native_demo_write_v2 as write
    from app.trade_qualification import native_controlled_close_v1 as close

    try:
        _need(
            _FILLED_WORKFLOWS.get(state["token"]) is state
            and state["task"] is asyncio.current_task()
        )
        guard.check_saved_native_guard_v1(state["guard"])
        state["owner"] = await state["prepared"]
        _need(type(state["owner"]) is close.OwnedNativeCloseIntentV1)
        state["phase"] = "SINGLE_NEGATIVE_POST"
        try:
            state["observed"] = await write.submit_owned_native_demo_close_v1(
                state["owner"]
            )
        except write.NativeDemoWriteError:
            # Exact spent Root attempt DATA, not a second POST or a caller
            # response substitute. Missing actual gate remains UNKNOWN.
            state["phase"] = "ACTUAL_FAILED_ATTEMPT_READBACK"
            state["flat"] = await reconcile_owned_native_close_attempt_v1(
                state["owner"]
            )
        else:
            state["phase"] = "ACTUAL_CLOSE_RESPONSE_READBACK"
            state["flat"] = await reconcile_owned_native_close_response_v1(
                state["observed"]
            )
        state["phase"] = "DURABLE_FLAT_RESOURCE_COMPLETION"
        result = await settle_owned_native_filled_reconciliation_v1(state["flat"])
        _need(type(result) is NativeFilledReconciliationSettlementResultV1)
        _FILLED_WORKFLOWS.pop(state["token"])
        state.clear()
        state.update(phase="COMPLETED", result=result)
        return result
    except BaseException as error:
        state["phase"] = "UNKNOWN"
        if isinstance(error, GeneratorExit):
            raise
        owner = state.get("owner")
        if type(owner) is close.OwnedNativeCloseIntentV1:
            actual = close._INTENTS.get(owner)
            if type(actual) is dict:
                try:
                    stop = close._inhibit_owned_native_close_transport_v1(
                        actual, key=write._CLOSE_INTENT_KEY_V1
                    )
                    async with asyncio.timeout(5):
                        await stop
                except BaseException:  # noqa: BLE001 -- no submit retry; retain every remaining actual owner.
                    state["persistent_stop"] = "unconfirmed"
        if isinstance(error, asyncio.CancelledError):
            raise asyncio.CancelledError() from None
        if isinstance(error, (KeyboardInterrupt, SystemExit)):
            raise type(error)() from None
        raise NativePostsubmitError("native_owned_filled_demo_unknown") from None


async def _run_filled_flat_cleanup_v1(state):
    from app.exchange.okx import native_demo_write_v2 as write
    from app.trade_qualification import native_controlled_close_v1 as close
    from app.trade_qualification import native_ledger_runtime_v2 as ledger

    try:
        _captured_filled_cleanup_source_v1(state)
        handle = await write.close_owned_native_demo_transports_after_filled_flat_v1(
            state["owned_filled_flat_result"]
        )
        cap = _own_native_filled_settlement_v1(state, handle)
        stamp = native_stamp()
        state["cleanup_original"], state["cleanup_last"] = stamp, stamp
        # One new negative cleanup budget, never an entry/read lease renewal.
        async with asyncio.timeout_at(asyncio.get_running_loop().time() + 5):
            _cleanup_clock_v1(state)
            source = _captured_filled_cleanup_source_v1(state)
            state[
                "filled_source_prepared"
            ] = await source.prepare_native_filled_flat_source_cleanup_v1(
                cap, key=_FILLED_SOURCE_SETTLEMENT_KEY_V1
            )
            _cleanup_clock_v1(state)
            state["filled_ledger_completion"] = (
                ledger.prepare_native_filled_flat_ledger_cleanup_v1(
                    cap, key=_FILLED_SOURCE_SETTLEMENT_KEY_V1
                )
            )
            source = _captured_filled_cleanup_source_v1(state)
            state["filled_source_completion"] = (
                source.finish_native_filled_flat_source_cleanup_v1(
                    state["filled_source_prepared"],
                    key=_FILLED_SOURCE_SETTLEMENT_KEY_V1,
                )
            )
            completion = _mint_native_filled_cleanup_completion_v1(state)
            _cleanup_clock_v1(state)
            finalized = write._finalize_native_closed_transports_settlement_v1(
                handle, completion, key=_ROOT_FILLED_TRANSPORT_SETTLEMENT_KEY_V1
            )
            expected = (
                "ctcc.native_filled_pair_resources_finalized.v1",
                state["filled_root_result_view"].closure_outcome_id,
                state["filled_root_result_view"].closure_receipt_sha256,
                facts.sha(state["filled_cleanup_completion_view"].receipt_json),
            )
            _need(type(finalized) is tuple and finalized == expected)
            (
                state["filled_root_finalization_receipt"],
                state["filled_root_finalized"],
            ) = finalized, True
            _owned_filled_settlement_v1(cap, finalized=True)
            _cleanup_clock_v1(state)
            state["filled_r7_retirement"] = (
                ledger.retire_native_filled_flat_ledger_cleanup_v1(
                    state["filled_ledger_completion"],
                    key=_FILLED_SOURCE_SETTLEMENT_KEY_V1,
                )
            )
            source = _captured_filled_cleanup_source_v1(state)
            state["filled_source_retirement"] = (
                source.retire_native_filled_flat_source_cleanup_v1(
                    state["filled_source_completion"],
                    key=_FILLED_SOURCE_SETTLEMENT_KEY_V1,
                )
            )
            state["filled_source_retired"] = True
            _cleanup_clock_v1(state)
            _captured_filled_cleanup_source_v1(state)
            state["filled_close_retirement"] = (
                close.retire_native_filled_close_resources_v1(
                    cap, key=_FILLED_SOURCE_SETTLEMENT_KEY_V1
                )
            )
            state["filled_close_retired"] = True
            state["filled_ledger_retirement"] = (
                ledger.finalize_native_filled_flat_ledger_cleanup_v1(
                    state["filled_ledger_completion"],
                    key=_FILLED_SOURCE_SETTLEMENT_KEY_V1,
                )
            )
            state["filled_ledger_finalized"] = True
            retirement = _mint_native_filled_retirement_v1(state)
            _cleanup_clock_v1(state)
            retired = write._retire_native_closed_transports_after_source_v1(
                handle, retirement, key=_ROOT_FILLED_TRANSPORT_SETTLEMENT_KEY_V1
            )
            expected = (
                "ctcc.native_filled_pair_authentication_retired.v1",
                state["filled_root_result_view"].closure_outcome_id,
                state["filled_root_result_view"].closure_receipt_sha256,
                facts.sha(state["filled_retirement_view"].receipt_json),
            )
            _need(type(retired) is tuple and retired == expected)
            state["filled_root_retirement_receipt"], state["filled_cleanup_status"] = (
                retired,
                "RETIRED",
            )
        # A NEW negative DATA-only budget covers DB session acquisition,
        # append/commit and independent readback, never any exchange request.
        from app.database.repositories import native_postsubmit_v1 as repository

        stamp = native_stamp()
        state["filled_persistence_started"], state["filled_persistence_last"] = (
            stamp,
            stamp,
        )
        _FILLED_PERSISTENCE[state["binding"]] = state
        async with asyncio.timeout_at(asyncio.get_running_loop().time() + 5):
            await repository.record_native_filled_cleanup_v1(state["binding"])
            _filled_persistence_clock_v1(state)
            committed = object.__new__(OwnedNativeFilledCleanupCommittedV1)
            state["filled_committed_cap"] = committed
            _FILLED_COMMITTED[committed] = state
            # Report policy failure retains DATA and does not create a legacy
            # ineligible native spool. The actual SQL predicate decides later.
            await repository.record_native_filled_report_ready_v1(state["binding"])
            _filled_persistence_clock_v1(state)
            source = _captured_filled_cleanup_source_v1(state)
            source.compact_native_filled_cleanup_retirement_v1(
                state["filled_source_retirement"],
                committed,
                key=_FILLED_SOURCE_SETTLEMENT_KEY_V1,
            )
            state["filled_retired_data_compacted"] = True
            close.compact_native_filled_close_retirement_v1(
                state["filled_close_retirement"],
                committed,
                key=_FILLED_SOURCE_SETTLEMENT_KEY_V1,
            )
            state["filled_close_data_compacted"] = True
        return _compact_native_filled_post_completion_v1(state)
    except BaseException as error:
        state["filled_cleanup_status"] = "UNKNOWN"
        if isinstance(error, GeneratorExit):
            raise
        # SQL already proved this exact entry and close flat. Resource-custody
        # failure must not tighten a newer database owner after a read-only
        # superseded resolution; retain UNKNOWN and revoke only our old intent.
        if state.get("source") is not None:
            try:
                owner = state["source"].control_owner
                _need(type(owner) is _control_owner55.original.DemoControlOwner)
                _control_owner55.original.DemoControlOwner._revoke(owner, stop=False)
            except BaseException:  # noqa: BLE001 -- retain exact remaining owners, never private exception text.
                state["filled_cleanup_local_revoke"] = "unconfirmed"
        if isinstance(error, asyncio.CancelledError):
            raise asyncio.CancelledError() from None
        if isinstance(error, (KeyboardInterrupt, SystemExit)):
            raise type(error)() from None
        raise NativePostsubmitError("native_filled_flat_cleanup_unknown") from None


def _identity(state):
    _need(
        type(state) is dict
        and type(state.get("binding")) is object
        and _LIVE.get(state["binding"]) is state
        and state["pid"] == os.getpid()
        and state["thread"] == threading.get_ident()
        and state["task"] is asyncio.current_task()
        and state["loop"] is asyncio.get_running_loop()
    )
    guard.check_saved_native_guard_v1(state["guard"])
    return state


def _clock(state):
    _identity(state)
    current = native_stamp()
    _need(
        state["last"]["utc_ns"] <= current["utc_ns"]
        and state["last"]["monotonic_ns"] <= current["monotonic_ns"]
        and current["utc_ns"] < state["utc_deadline_ns"]
        and current["monotonic_ns"] < state["mono_deadline_ns"],
        "native_postsubmit_read_deadline",
    )
    state["last"] = current
    return current


def _source(state):
    from app.exchange.okx.private_rest import OkxDemoPrivateRestClient
    from app.trade_qualification import native_account_risk_assembly_v1 as account

    _clock(state)
    if state["kind"] == "current":
        from app.exchange.okx import native_demo_write_v2 as write

        data = write._replay_native_response_for_reconciliation_v1(
            state["root_handle"], key=_SOURCE_KEY_V1
        )
        _need(
            type(data) is NativeSubmissionResponseDataV1
            and data is state["response_source"]
        )
        value = account._replay_issued_demo_reconciliation_context_v2(
            data.transport_facts.transport_binding, key=_SOURCE_KEY_V1
        )
    elif state["kind"] == "controlled_close":
        from app.trade_qualification import native_controlled_close_v1 as close

        entry = close._replay_native_close_entry_for_post_v1(
            state["close_entry_cap"], key=_SOURCE_KEY_V1
        )
        _need(entry is state["close_entry_view"])
        data = entry.filled_result_view.response_data
        source = _captured_native_close_read_source_v1(state)
        if state.get("source") is None:
            value = source.acquire_native_close_read_source_v1(
                state["close_entry_cap"], key=close._SOURCE_KEY_V1
            )
        else:
            value = source._replay_native_close_read_source_v1(
                state["source"].source_generation, key=_SOURCE_KEY_V1
            )
    elif state["kind"] == "controlled_close_post":
        from app.trade_qualification import native_controlled_close_v1 as close

        entry = close._replay_native_close_post_read_for_post_v1(
            state["close_response_cap"], key=_SOURCE_KEY_V1
        )
        _need(entry is state["close_response_view"])
        data = entry.response_data
        source = _captured_native_close_post_read_source_v1(state)
        if state.get("source") is None:
            value = source.acquire_native_close_post_read_source_v1(
                state["close_response_cap"], key=close._SOURCE_KEY_V1
            )
        else:
            value = source._replay_native_close_post_read_source_v1(
                state["source"].source_generation, key=_SOURCE_KEY_V1
            )
    else:
        value = account._replay_native_recovery_read_source_v1(
            state["source"].source_generation, key=_SOURCE_KEY_V1
        )
    _need(
        type(value) is NativeReconciliationSourceContextV1
        and type(value.client) is OkxDemoPrivateRestClient
        and type(value.scope) is facts.wire.ledger.LedgerScope
        and value.client._external_client is None
        and value.client._clock_injected is False
        and value.scope.environment == "demo"
        and value.client.settings.trading_mode == "okx_demo"
        and value.client.settings.live_trading is False
    )
    if state["kind"] in {"current", "controlled_close", "controlled_close_post"}:
        _need(
            value.scope == data.transport_facts.request.scope
            and value.session_factory is data.transport_facts.session_factory
            and value.origin == data.transport_facts.transport_context.origin
        )
    previous = state.get("source")
    if previous is not None:
        _need(
            value.client is previous.client
            and value.scope == previous.scope
            and value.session_factory is previous.session_factory
            and value.origin == previous.origin
            and value.credential_session_sha256 == previous.credential_session_sha256
            and value.source_generation is previous.source_generation
            and value.control_owner is previous.control_owner
        )
    state["source"] = value
    return value


@contextmanager
def _new_read_context(state):
    """Fixed owned purpose: replace a spent trade's GET context, then restore it.

    Ordinary pinned_read_session nesting remains denied. This source was minted
    by native registry replay after Root's writer transaction exited. The new
    client/lease never impersonates the old client/session or revives its expiry.
    """
    from app.exchange.okx import private_rest as private

    source = _source(state)
    previous = private._DEMO_READ_SESSION.get()
    _need(
        previous is None
        or (
            type(previous) is private.DemoPinnedReadSession
            and previous._settings is source.client.settings
        ),
        "native_postsubmit_foreign_read_context",
    )
    if previous is not None and state["kind"] in {
        "current",
        "controlled_close",
        "controlled_close_post",
    }:
        _need(
            previous
            is state["response_source"].transport_facts.transport_context.read_session,
            "native_postsubmit_foreign_read_context",
        )
    # The genuine native DATA registry checked captured Settings/credentials/
    # origin/factory directly. A closed/expired old GET lease must not become
    # a prerequisite for this NEW read-only lease; it is never revived here.
    token = private._DEMO_READ_SESSION.set(None)
    try:
        with source.client.pinned_read_session() as lease:
            _need(lease._owner is source.client and lease._origin == source.origin)
            state["read_lease"] = lease
            state["read_lease_binding_id"] = lease.binding_id
            yield lease
    finally:
        state["read_lease"] = None
        private._DEMO_READ_SESSION.reset(token)


def _retire(state):
    # Immediately strong-held on MOVE; finalizers need not execute for custody.
    if type(state) is not dict or type(state.get("binding")) is not object:
        return
    if _LIVE.get(state["binding"]) is state:
        _UNKNOWN[state["binding"]] = state
        _LIVE.pop(state["binding"], None)
        state["closed"] = True


def _new(kind):
    _need(len(_UNKNOWN) < _MAX_READS, "native_postsubmit_capacity_exhausted")
    stamp = native_stamp()
    state = {
        "kind": kind,
        "binding": object(),
        "pid": os.getpid(),
        "thread": threading.get_ident(),
        "task": asyncio.current_task(),
        "loop": asyncio.get_running_loop(),
        "last": stamp,
        "utc_deadline_ns": stamp["utc_ns"] + _READ_SECONDS * 1_000_000_000,
        "mono_deadline_ns": stamp["monotonic_ns"] + _READ_SECONDS * 1_000_000_000,
        "loop_deadline": asyncio.get_running_loop().time() + _READ_SECONDS,
        "phase": "taken",
        "closed": False,
        "observations": [],
        "read_lease": None,
        "guard": {},
    }
    guard.save_native_guard_v1(state["guard"], _NATIVE_GUARD)
    _LIVE[state["binding"]] = state
    # Strong memory custody starts synchronously, before the first send or await.
    _UNKNOWN[state["binding"]] = state
    return state


def _register_native_close_read_v1(cap, *, key):
    """Fixed close-purpose registration in the existing owned SDK GET path."""
    from app.trade_qualification import native_controlled_close_v1 as close

    _need(key is close._SOURCE_KEY_V1)
    _NATIVE_GUARD()
    entry = close._take_native_close_entry_for_post_v1(cap, key=_SOURCE_KEY_V1)
    _need(type(entry) is close.NativeCloseEntryForSourceV1)
    state = _new("controlled_close")
    state.update(
        close_entry_cap=cap,
        close_entry_view=entry,
        reservation_id=entry.filled_result_view.result.reservation_id,
        root_handle=entry.response_handle,
        response_source=entry.filled_result_view.response_data,
    )
    try:
        # Source construction is a genuine once-only NEW negative client and
        # generation. Every SDK GET still uses _new_read_context/_get below.
        _source(state)
        return state["binding"]
    except BaseException:
        _retire(state)
        raise


def prepare_owned_native_close_intent_v1(result):
    """Same-task actual filled result to one durable negative close intent."""
    from app.trade_qualification import native_controlled_close_v1 as close

    cap = close.take_owned_native_close_entry_v1(result)
    try:
        binding = _register_native_close_read_v1(cap, key=close._SOURCE_KEY_V1)
    except BaseException as error:  # noqa: BLE001 -- Retain failure in the owned close-registration attempt.
        return _CloseRegistrationFailureAttempt(cap, error)
    return _CloseReadAttempt(_LIVE[binding])


def _register_native_close_post_read_v1(cap, *, key):
    from app.trade_qualification import native_controlled_close_v1 as close

    _need(key is close._SOURCE_KEY_V1)
    _NATIVE_GUARD()
    view = close._take_native_close_post_read_for_post_v1(cap, key=_SOURCE_KEY_V1)
    _need(type(view) is close.NativeClosePostReadForSourceV1)
    state = _new("controlled_close_post")
    state.update(
        close_response_cap=cap,
        close_response_view=view,
        reservation_id=view.close_intent_view.target.reservation_id,
        root_handle=view.close_response_handle,
        response_source=view.response_data,
    )
    try:
        _source(state)
        return state["binding"]
    except BaseException:
        _retire(state)
        raise


def reconcile_owned_native_close_response_v1(observed):
    """Same task: actual negative response to NEW read DATA, no hold release."""
    from app.trade_qualification import native_controlled_close_v1 as close

    cap = close.take_owned_native_close_post_read_v1(observed)
    return _begin_native_close_post_read_v1(cap)


def reconcile_owned_native_close_attempt_v1(intent_owner):
    """A failed same-task POST still uses its actual retained attempt DATA."""
    from app.trade_qualification import native_controlled_close_v1 as close

    cap = close.take_owned_native_close_attempt_post_read_v1(intent_owner)
    return _begin_native_close_post_read_v1(cap)


def _begin_native_close_post_read_v1(cap):
    from app.trade_qualification import native_controlled_close_v1 as close

    try:
        binding = _register_native_close_post_read_v1(cap, key=close._SOURCE_KEY_V1)
    except BaseException as error:  # noqa: BLE001 -- Retain failure in the owned post-close registration attempt.
        return _ClosePostRegistrationFailureAttempt(cap, error)
    return _ClosePostReadAttempt(_LIVE[binding])


class _ClosePostRegistrationFailureAttempt(Coroutine):
    def __init__(self, cap, error):
        self.cap, self.error = cap, error
        self.body = _failed_close_post_registration_v1(cap, error)

    def __await__(self):
        return self

    def __iter__(self):
        return self

    def __next__(self):
        return self.send(None)

    def send(self, value):
        return self.body.send(value)

    def throw(self, *arguments):
        return self.body.throw(*arguments)

    def close(self):
        return self.body.close()


async def _failed_close_post_registration_v1(cap, error):
    from app.trade_qualification import native_controlled_close_v1 as close

    try:
        stop = close._inhibit_native_close_post_read_before_source_v1(
            cap, key=_SOURCE_KEY_V1
        )
        async with asyncio.timeout(5):
            await stop
    except BaseException:  # noqa: BLE001 -- original entry/close graph retained, no retry or private text.
        raise NativePostsubmitError(
            "native_close_post_registration_stop_unconfirmed"
        ) from None
    if isinstance(error, asyncio.CancelledError):
        raise asyncio.CancelledError() from None
    if isinstance(error, (KeyboardInterrupt, SystemExit)):
        raise type(error)() from None
    raise NativePostsubmitError("native_close_post_registration_unknown") from None


class _ClosePostReadAttempt(Coroutine):
    def __init__(self, state):
        self.state, self.body = state, _run_close_post_read_v1(state)

    def __await__(self):
        return self

    def __iter__(self):
        return self

    def __next__(self):
        return self.send(None)

    def send(self, value):
        try:
            return self.body.send(value)
        except BaseException:
            _retire(self.state)
            raise

    def throw(self, *arguments):
        try:
            return self.body.throw(*arguments)
        except BaseException:
            _retire(self.state)
            raise

    def close(self):
        try:
            return self.body.close()
        finally:
            _retire(self.state)


class _CloseRegistrationFailureAttempt(Coroutine):
    def __init__(self, cap, error):
        self.cap, self.error = cap, error
        self.body = _failed_close_registration_v1(cap, error)

    def __await__(self):
        return self

    def __iter__(self):
        return self

    def __next__(self):
        return self.send(None)

    def send(self, value):
        return self.body.send(value)

    def throw(self, *arguments):
        return self.body.throw(*arguments)

    def close(self):
        return self.body.close()


async def _stop_close_original_entry_v1(cap):
    from app.exchange.okx import native_demo_write_v2 as write
    from app.trade_qualification import native_account_risk_assembly_v1 as account
    from app.trade_qualification import native_controlled_close_v1 as close

    original = close._replay_native_close_entry_for_stop_v1(cap, key=_SOURCE_KEY_V1)
    stop = account._inhibit_issued_demo_transport_v2(
        original.transport_binding, key=write._INTENT_KEY
    )
    _need(stop is not None)
    async with asyncio.timeout(5):
        await stop


async def _failed_close_registration_v1(cap, error):
    # No invented source generation is needed. Transactions/constructors have
    # unwound; the genuine retained ORIGINAL transport supplies scope/owner.
    try:
        await _stop_close_original_entry_v1(cap)
    except BaseException:  # noqa: BLE001 -- preserve actual cap/Root graph, no second stop or submit retry.
        raise NativePostsubmitError(
            "native_close_registration_stop_unconfirmed"
        ) from None
    if isinstance(error, asyncio.CancelledError):
        raise asyncio.CancelledError() from None
    if isinstance(error, (KeyboardInterrupt, SystemExit)):
        raise type(error)() from None
    raise NativePostsubmitError("native_close_registration_unknown") from None


class _CloseReadAttempt(Coroutine):
    def __init__(self, state):
        self.state, self.body = state, _run_close_preflight_v1(state)

    def __await__(self):
        return self

    def __iter__(self):
        return self

    def __next__(self):
        return self.send(None)

    def send(self, value):
        try:
            return self.body.send(value)
        except StopIteration:
            # The actual opaque intent keeps this LIVE data owner for fixed
            # DB/transport replay. No healthy entry lease is recreated.
            if self.state.get("close_intent_owner") is None:
                _retire(self.state)
            raise
        except BaseException:
            _retire(self.state)
            raise

    def throw(self, *arguments):
        try:
            return self.body.throw(*arguments)
        except BaseException:
            _retire(self.state)
            raise

    def close(self):
        try:
            return self.body.close()
        finally:
            if self.state.get("close_intent_owner") is None:
                _retire(self.state)


def _close_read_owner_v1(binding, *, key):
    from app.trade_qualification import native_controlled_close_v1 as close

    _need(key is close._SOURCE_KEY_V1 and type(binding) is object)
    state = _LIVE.get(binding)
    _identity(state)
    _need(state["kind"] == "controlled_close")
    _source(state)
    return state


def _take_native_close_intent_for_owner_v1(binding, *, key):
    from app.database.repositories import native_postsubmit_v1 as repository

    state = _close_read_owner_v1(binding, key=key)
    _need(state.get("close_intent_taken") is not True)
    proof = state.get("durable_close_intent")
    _need(
        type(proof) is tuple
        and len(proof) == 5
        and type(proof[0]) is bytes
        and type(proof[1]) is bytes
        and type(proof[2]) is str
        and type(proof[3]) is tuple
        and len(proof[3]) == 10
        and type(proof[4]) is dict
        and type(state["target"]) is repository.NativeTargetReadbackV1
        and state["target"].current_state == "uncertain"
        and state["target"].current_state_revision == 3
    )
    state["close_intent_taken"] = True
    view = NativeCloseIntentForOwnerV1(
        binding,
        state["close_entry_cap"],
        state["source"].source_generation,
        state["source"],
        state["close_entry_view"].filled_result_view,
        state["target"],
        proof,
    )
    state["close_intent_view"] = view
    return view


def _replay_native_close_intent_for_owner_v1(view, *, key):
    _need(type(view) is NativeCloseIntentForOwnerV1)
    state = _close_read_owner_v1(view.binding, key=key)
    _need(
        state.get("close_intent_taken") is True
        and state.get("close_intent_view") is view
        and view.entry_cap is state["close_entry_cap"]
        and view.intent_readback is state["durable_close_intent"]
        and view.target is state["target"]
        and view.source_generation is state["source"].source_generation
        and view.source_context.client is state["source"].client
        and view.source_context.scope == state["source"].scope
        and view.source_context.session_factory is state["source"].session_factory
    )
    return view


def _replay_retained_native_close_intent_v1(view, *, key):
    """Passive identity only after close-spend/expiry; never read/write renewal."""
    from app.trade_qualification import native_controlled_close_v1 as close

    _need(key is close._SOURCE_KEY_V1 and type(view) is NativeCloseIntentForOwnerV1)
    state = _UNKNOWN.get(view.binding)
    _need(
        type(state) is dict
        and state["binding"] is view.binding
        and state["kind"] == "controlled_close"
        and state["close_intent_view"] is view
        and state["close_intent_taken"] is True
        and state["task"] is asyncio.current_task()
        and state["loop"] is asyncio.get_running_loop()
        and state["pid"] == os.getpid()
        and state["thread"] == threading.get_ident()
        and view.entry_cap is state["close_entry_cap"]
        and view.intent_readback is state["durable_close_intent"]
        and view.target is state["target"]
        and view.source_generation is state["source"].source_generation
        and view.source_context.client is state["source"].client
        and view.source_context.scope == state["source"].scope
        and view.source_context.session_factory is state["source"].session_factory
    )
    guard.check_saved_native_guard_v1(state["guard"])
    return view


def _retire_native_close_intent_read_v1(view, *, key):
    _replay_retained_native_close_intent_v1(view, key=key)
    _retire(_UNKNOWN[view.binding])


class _Attempt(Coroutine):
    def __init__(self, state):
        self.state, self.body = state, _run(state)

    def __await__(self):
        return self

    def __iter__(self):
        return self

    def __next__(self):
        return self.send(None)

    def send(self, value):
        try:
            return self.body.send(value)
        except BaseException:
            _retire(self.state)
            raise

    def throw(self, *arguments):
        try:
            return self.body.throw(*arguments)
        except BaseException:
            _retire(self.state)
            raise

    def close(self):
        try:
            return self.body.close()
        finally:
            _retire(self.state)


def reconcile_owned_native_response_v1(observed):
    """MOVE an actual Root-owned response identity into one DATA reconciliation."""
    from app.exchange.okx import native_demo_write_v2 as write

    state = _new("current")
    try:
        state["root_handle"] = write._take_native_response_for_reconciliation_v1(
            observed, key=_SOURCE_KEY_V1
        )
        state["response_source"] = write._replay_native_response_for_reconciliation_v1(
            state["root_handle"], key=_SOURCE_KEY_V1
        )
        state["reservation_id"] = state[
            "response_source"
        ].transport_facts.consumed.reservation_id
        _source(state)
        return _Attempt(state)
    except BaseException:
        _retire(state)
        raise


def reconcile_native_recovery_v1(selection, reservation_id):
    """NEW genuine same-account auth + durable target; never recreate a write lease."""
    import re

    from app.trade_qualification import native_account_risk_assembly_v1 as account

    _need(
        type(reservation_id) is str
        and re.fullmatch(r"[a-f0-9]{64}", reservation_id) is not None
    )
    state = _new("recovery")
    try:
        state["recovery_selection"] = selection
        state["source"] = account._acquire_native_recovery_read_source_v1(
            selection, key=_SOURCE_KEY_V1
        )
        state["reservation_id"] = reservation_id
        _source(state)
        _need(state["source"].control_owner is None)
        return _Attempt(state)
    except BaseException:
        _retire(state)
        raise


def _native_recovery_request_id_v1(request):
    import re

    _need(type(request) is _caller_models.NativeDemoRecoveryRequestV1)
    raw = object.__getattribute__(request, "__dict__")
    extra = object.__getattribute__(request, "__pydantic_extra__")
    _need(
        type(raw) is dict
        and set(raw) == {"reservation_id"}
        and (extra is None or type(extra) is dict and not extra)
    )
    result = raw["reservation_id"]
    _need(type(result) is str and re.fullmatch(r"[a-f0-9]{64}", result) is not None)
    return result


def _recovery_committed_pages_v1(state, result):
    """Only actual registered, committed evidence can release fresh local custody."""
    if result is not state.get("result") or result.risk_hold_retained is not False:
        return None
    if result is state.get("recovered_filled_flat_result"):
        closure = state["verified_filled_flat_closure"]
        fresh = state["verified_closed_trade_readback"]
        _need(
            result.outcome_id == closure[0]
            and result.receipt_sha256 == facts.sha(fresh[1].receipt_json)
        )
        pages = fresh[2]
    elif result is state.get("owned_flat_result"):
        closure = state["verified_flat_closure"]
        _need(result.outcome_id == closure[0])
        fresh = state.get("verified_previously_closed_zero_recovery")
        receipt, pages = (
            (closure[1].receipt_json, closure[2]) if fresh is None else fresh[1:3]
        )
        _need(result.receipt_sha256 == facts.sha(receipt))
    else:
        _need(False, "native_recovery_durable_verification_unavailable")
    _need(
        pages and pages == state["durable_observations"] == tuple(state["observations"])
    )
    return pages


async def _dispose_completed_recovery_v1(service, caller, attempt, result):
    """Retire only this invocation's new read owners after actual closed readback."""
    state = attempt.state
    pages = _recovery_committed_pages_v1(state, result)
    if pages is None:
        return False
    binding, selection, factories = (
        state["binding"],
        caller["selection"],
        caller["factories"],
    )
    source = _captured_flat_cleanup_source_v1(state)
    generation = state["source"].source_generation
    record = source._RECONCILIATION_READ_V6.get(generation)
    selector = _selector._record(selection)
    _need(
        type(attempt) is _Attempt
        and state["kind"] == "recovery"
        and state["closed"] is True
        and binding not in _LIVE
        and _UNKNOWN.get(binding) is state
        and state["task"] is asyncio.current_task()
        and state["loop"] is asyncio.get_running_loop()
        and state["pid"] == os.getpid()
        and state["thread"] == threading.get_ident()
        and state["recovery_selection"] is selection
        and caller["controller"] is None
        and _caller._HELD.get(caller["id"]) is caller
        and selector["factories"] is factories
        and selector["private_client"] is caller["client"]
        and type(record) is dict
        and record["generation"] is generation
        and record["client"] is state["source"].client
        and record["writer_factory"] is state["source"].session_factory
        and record["selector_record"] is selector["record"]
        and record["previous_read_context"] is selector["read_session"]
        and record["source_state"]
        is record["transport"]
        is record["control_owner"]
        is None
        and source._RECOVERY_SELECTION_USES_V6.get(selection) is generation
    )
    source._reconciliation_identity_v6(record, deadline=False)
    source_guard = source._REQUIRE_SOURCE_KERNEL_V6
    reader, custody = factories.frame_reader_engine, factories.custody_engine
    pools = reader.sync_engine.pool, custody.sync_engine.pool
    _need(
        all(type(pool) is _selector.NullPool for pool in pools)
        and reader is not custody
        and reader is not record["writer_factory"].kw["bind"]
        and custody is not record["writer_factory"].kw["bind"]
    )
    selector_gets = vars(caller["client"]).get(
        "_native_reconciliation_read_journals_v1"
    )
    fresh_gets = vars(record["client"]).get("_native_reconciliation_read_journals_v1")
    _need(
        type(selector_gets) is list
        and len(selector_gets) == 1
        and type(fresh_gets) is list
        and len(fresh_gets) == len(pages)
    )
    initial = selector_gets[0]
    _need(
        type(initial) is read.ReadJournalV1
        and type(initial.observation) is read.DemoReconciliationObservationV1
        and initial.path == "/api/v5/account/config"
        and initial.parameters == ()
        and initial.signature == selector["selector_signature"]
        and initial.observation.raw_body == selector["evidence"].selector_raw_body
    )
    for item, (_, page), (_, live_page) in zip(
        fresh_gets, pages, state["observations"], strict=True
    ):
        _need(
            type(item) is read.ReadJournalV1
            and item.observation is live_page
            and item.observation == page
        )
    read_set = (
        (initial, selector["read_session"].binding_id),
        *((item, record["read_lease_binding_id"]) for item in fresh_gets),
    )

    def check_owners():
        _caller._ready(service, caller)
        guard.check_saved_native_guard_v1(state["guard"])
        _need(source._REQUIRE_SOURCE_KERNEL_V6 is source_guard)
        source_guard()
        _need(
            _UNKNOWN.get(binding) is state
            and _caller._HELD.get(caller["id"]) is caller
            and source._RECONCILIATION_READ_V6.get(generation) is record
            and source._RECOVERY_SELECTION_USES_V6.get(selection) is generation
            and _selector._SELECTIONS.get(selection) is selector
        )

    caller["fresh_resource_cleanup"] = "pending"
    started = native_stamp()
    last = started
    async with asyncio.timeout(5):
        while True:
            check_owners()
            stamp = native_stamp()
            _need(
                all(
                    last[name] <= stamp[name] < started[name] + 5_000_000_000
                    for name in ("utc_ns", "monotonic_ns")
                ),
                "native_recovery_resource_cleanup_deadline",
            )
            last = stamp
            if all(
                source._closed_reconciliation_get_v6(item, lease) is not None
                for item, lease in read_set
            ):
                break
            await asyncio.sleep(0.01)
        # NullPool reader/custody engines are owned by this selection. The
        # shared writer stays open. On any disposal failure keep both registries.
        await reader.dispose()
        check_owners()
        _need(reader.sync_engine.pool is not pools[0])
        await custody.dispose()
        check_owners()
        _need(custody.sync_engine.pool is not pools[1])
        stamp = native_stamp()
        _need(
            all(
                last[name] <= stamp[name] < started[name] + 5_000_000_000
                for name in ("utc_ns", "monotonic_ns")
            ),
            "native_recovery_resource_cleanup_deadline",
        )
        _need(
            _repository._RECOVERY_HISTORY_BUFFERS_V1.get(binding)
            is state.get("recovery_history_retained")
            and _repository._RECOVERY_CLOSE_HISTORIES_V1.get(binding)
            is state.get("recovery_close_history")
        )
        selection.close()
        _need(selection not in _selector._SELECTIONS)
        # No await after exact retirement. The closed selection can never be
        # acquired again, and the authoritative journal remains in PostgreSQL.
        del source._RECOVERY_SELECTION_USES_V6[selection]
        del source._RECONCILIATION_READ_V6[generation]
        _repository._RECOVERY_HISTORY_BUFFERS_V1.pop(binding, None)
        _repository._RECOVERY_CLOSE_HISTORIES_V1.pop(binding, None)
        del _UNKNOWN[binding]
        del _caller._HELD[caller["id"]]
    caller["fresh_resource_cleanup"] = "confirmed"
    return True


async def recover_native_for_service_v1(service, request, *, operator_token):
    """Canonical authenticated recovery adapter, never Arm, close or resubmit."""
    from uuid import uuid4

    selected = _native_recovery_request_id_v1(request)
    await _caller.require_ctcc_token(operator_token)
    settings = _caller._service(service)
    _need(_native_recovery_request_id_v1(request) == selected)
    if service._run_lock.locked():
        raise _caller.DemoAutomationBusyError("native_demo_run_in_progress")
    async with service._run_lock:
        _need(len(_caller._HELD) + len(_caller._ACTIVE) < _caller._MAX_HELD)
        invocation = uuid4().hex
        state = {
            "id": invocation,
            "service": service,
            "settings": settings,
            "task": asyncio.current_task(),
            "revoked": False,
            "controller": None,
            "selection": None,
            "submitted_or_unknown": True,
            "action": "reconcile",
        }
        _caller._ACTIVE[service] = state
        # Retain before selection/DB/GET awaits. No later DATA field removes
        # this hold or attests to the previous process's socket/SDK resources.
        _caller._HELD[invocation] = state
        try:
            _caller._ready(service, state)
            instruments = tuple(settings.okx_demo_allowed_symbol_list)
            _need(
                0 < len(instruments) <= 20 and len(set(instruments)) == len(instruments)
            )
            client = _private.OkxDemoPrivateRestClient(settings=settings)
            state["client"] = client
            with client.pinned_read_session() as lease:
                selection = await _selector.select_configured_demo_identity_v2(
                    private_client=client,
                    read_session=lease,
                    instrument_ids=instruments,
                    retain_read_transport=True,
                )
                state["selection"], state["factories"] = selection, selection.factories
                _caller._ready(service, state)
                _need(_native_recovery_request_id_v1(request) == selected)
                attempt = reconcile_native_recovery_v1(selection, selected)
                result = await attempt
                _caller._ready(service, state)
                _need(
                    type(result) is NativeReconciliationResultV1
                    and result.reservation_id == selected
                )
                state["reconciliation_data"] = result
                resources_closed = False
                try:
                    resources_closed = await _dispose_completed_recovery_v1(
                        service, state, attempt, result
                    )
                except asyncio.CancelledError:
                    state["fresh_resource_cleanup"] = "unknown"
                    raise
                except Exception:  # noqa: BLE001 -- keep exact owners; a late cleanup failure changes no exchange or ledger outcome.
                    state["fresh_resource_cleanup"] = "unknown"
                return _caller_models.NativeDemoRecoveryResultV1(
                    invocation_id=invocation,
                    reservation_id=selected,
                    classification=result.classification,
                    outcome_id=result.outcome_id,
                    receipt_sha256=result.receipt_sha256,
                    risk_hold_retained=result.risk_hold_retained,
                    fresh_resource_cleanup_confirmed=resources_closed,
                )
        finally:
            # A new authenticated recovery read cannot restore old credentials,
            # resources, Arm or report-complete authority. Unknowns remain held.
            state["revoked"] = True
            if _caller._ACTIVE.get(service) is state:
                _caller._ACTIVE.pop(service)


def _retained_native_recovery_history_v1(source_generation, *, key):
    """Fixed Source accountant/scanner DATA replay; no Source/clock recursion."""
    from app.database.repositories import native_postsubmit_v1 as repository
    from app.trade_qualification import native_controlled_account_source_v6 as source

    _need(key is source._RECONCILIATION_KEY_V1 and type(source_generation) is object)
    _NATIVE_GUARD()
    actual = source._RECONCILIATION_READ_V6.get(source_generation)
    _need(
        type(actual) is dict
        and actual["generation"] is source_generation
        and actual["source_state"] is None
        and actual["transport"] is None
        and actual["control_owner"] is None
    )
    matches = [
        state
        for state in _UNKNOWN.values()
        if type(state) is dict
        and state.get("kind") == "recovery"
        and (
            state.get("source") is not None
            and state["source"].source_generation is source_generation
            or state.get("source") is None
            and source._RECOVERY_SELECTION_USES_V6.get(state.get("recovery_selection"))
            is source_generation
        )
    ]
    _need(len(matches) == 1)
    state = matches[0]
    _need(
        state["task"] is asyncio.current_task()
        and state["loop"] is asyncio.get_running_loop()
        and state["pid"] == os.getpid()
        and state["thread"] == threading.get_ident()
        and _UNKNOWN.get(state["binding"]) is state
    )
    guard.check_saved_native_guard_v1(state["guard"])
    roots = repository._RECOVERY_HISTORY_BUFFERS_V1.get(state["binding"])
    if roots is None:
        _need(
            state.get("recovery_history_retained") is None
            and state.get("recovery_history_loaded") is not True
            and state["binding"] not in repository._RECOVERY_CLOSE_HISTORIES_V1
        )
        return ()
    _need(
        type(roots) is tuple
        and state.get("recovery_history_retained") is roots
        and all(type(raw) is bytes for raw in roots)
    )
    value = state.get("recovery_close_history")
    if value is None:
        # Genuine initial acquisition or two actual reads proving no close.
        # Never translate a missing source/registry/binding into empty custody.
        _need(state["binding"] not in repository._RECOVERY_CLOSE_HISTORIES_V1)
        return roots
    _need(
        type(value) is repository.NativeRecoveryCloseHistoryV1
        and repository._RECOVERY_CLOSE_HISTORIES_V1.get(state["binding"]) is value
        and state.get("recovery_history_loaded") is True
    )
    return roots


async def _get(state, role, path, query):
    source = _source(state)
    state["read_lease"].verify_current()
    result = await source.client.native_reconciliation_get_v1(path, query)
    _clock(state)
    journals = source.client._native_reconciliation_read_journals_v1
    _need(
        type(journals) is list
        and journals
        and type(journals[-1]) is read.ReadJournalV1
        and journals[-1].observation is result
        and not journals[-1].faults
        and result.read_session_id == state["read_lease"].binding_id
    )
    state["observations"].append((role, result))
    _source(state)
    return facts.rows(result)


async def _pages(state, role, path, query, cursor):
    rows, after, seen = [], None, set()
    for _ in range(16):
        current = {**query, "limit": "100"}
        if after is not None:
            current["after"] = after
        page = await _get(state, role, path, current)
        if not page:
            return rows
        _need(len(page) <= 100)
        identifiers = [item.get(cursor) for item in page]
        _need(
            all(
                type(item) is str
                and 1 <= len(item) <= 40
                and item.isascii()
                and item.isdigit()
                and int(item) > 0
                for item in identifiers
            )
            and len(set(identifiers)) == len(identifiers)
            and not seen.intersection(identifiers)
            and (after is None or int(identifiers[0]) < int(after))
        )
        seen.update(identifiers)
        after = identifiers[-1]
        rows.extend(page)
    _need(False, "native_postsubmit_terminal_chain_missing")


async def _collect(state, target):
    from app.domain.source_primitives import decode

    outer = decode(target.facts.intent_json.encode(), 16 * 1024 * 1024)
    expected = decode(outer["intent_json"].encode(), 16 * 1024 * 1024)["exact_order"]
    order_query = {"instId": expected["instId"], "clOrdId": expected["clOrdId"]}
    await _get(state, "config_before", "/api/v5/account/config", {})
    first = await _get(state, "order_before", "/api/v5/trade/order", order_query)
    _need(len(first) == 1 and type(first[0].get("ordId")) is str)
    fills = {
        "instType": "SWAP",
        "instId": expected["instId"],
        "ordId": first[0]["ordId"],
    }
    await _pages(state, "fills_recent", "/api/v5/trade/fills", fills, "billId")
    await _pages(state, "fills_history", "/api/v5/trade/fills-history", fills, "billId")
    await _get(state, "balance", "/api/v5/account/balance", {})
    await _get(state, "positions", "/api/v5/account/positions", {})
    await _get(state, "account_risk", "/api/v5/account/account-position-risk", {})
    await _pages(state, "pending_orders", "/api/v5/trade/orders-pending", {}, "ordId")
    for kind in read.ALGO_TYPES:
        await _pages(
            state,
            "algo_" + kind,
            "/api/v5/trade/orders-algo-pending",
            {"ordType": kind},
            "algoId",
        )
    await _get(
        state,
        "leverage",
        "/api/v5/account/leverage-info",
        {"instId": expected["instId"], "mgnMode": "isolated"},
    )
    await _get(state, "order_after", "/api/v5/trade/order", order_query)
    await _get(state, "config_after", "/api/v5/account/config", {})
    return tuple(state["observations"])


async def _collect_previously_closed(state, target):
    from app.domain.source_primitives import decode

    expected = decode(
        decode(target.facts.intent_json.encode())["intent_json"].encode()
    )["exact_order"]
    query = {"instId": expected["instId"], "clOrdId": expected["clOrdId"]}
    await _get(state, "config_before", "/api/v5/account/config", {})
    first = await _get(state, "order_before", "/api/v5/trade/order", query)
    _need(len(first) == 1 and type(first[0].get("ordId")) is str)
    fill_query = {
        "instType": "SWAP",
        "instId": expected["instId"],
        "ordId": first[0]["ordId"],
    }
    await _pages(state, "fills_recent", "/api/v5/trade/fills", fill_query, "billId")
    await _pages(
        state, "fills_history", "/api/v5/trade/fills-history", fill_query, "billId"
    )
    await _get(state, "order_after", "/api/v5/trade/order", query)
    await _get(state, "config_after", "/api/v5/account/config", {})


async def _collect_close_preflight_v1(state, target):
    outer = facts.decode(target.facts.intent_json.encode(), facts.wire.MAX_NATIVE_BYTES)
    expected = facts.decode(outer["intent_json"].encode(), facts.wire.MAX_NATIVE_BYTES)[
        "exact_order"
    ]
    query = {"instId": expected["instId"], "clOrdId": expected["clOrdId"]}
    await _get(state, "config_before", "/api/v5/account/config", {})
    first = await _get(state, "order_before", "/api/v5/trade/order", query)
    _need(len(first) == 1)
    facts.source_time(first[0].get("cTime"), state["observations"][-1][1])
    fills = {
        "instType": "SWAP",
        "instId": expected["instId"],
        "ordId": first[0]["ordId"],
    }
    await _pages(state, "fills_recent", "/api/v5/trade/fills", fills, "billId")
    await _pages(state, "fills_history", "/api/v5/trade/fills-history", fills, "billId")
    await _get(state, "balance", "/api/v5/account/balance", {})
    await _get(state, "positions", "/api/v5/account/positions", {})
    await _get(state, "account_risk", "/api/v5/account/account-position-risk", {})
    await _pages(state, "pending_orders", "/api/v5/trade/orders-pending", {}, "ordId")
    for kind in read.ALGO_TYPES:
        await _pages(
            state,
            "algo_" + kind,
            "/api/v5/trade/orders-algo-pending",
            {"ordType": kind},
            "algoId",
        )
    await _get(
        state,
        "leverage",
        "/api/v5/account/leverage-info",
        {"instId": expected["instId"], "mgnMode": "isolated"},
    )
    stamp = _clock(state)
    window = {
        "instType": "SWAP",
        "instId": expected["instId"],
        "begin": str(int(first[0]["cTime"]) - 1),
        "end": str(stamp["utc_ns"] // 1_000_000),
    }
    await _pages(
        state, "close_fills_recent_all", "/api/v5/trade/fills", window, "billId"
    )
    await _pages(
        state,
        "close_fills_history_all",
        "/api/v5/trade/fills-history",
        window,
        "billId",
    )
    await _get(state, "order_after", "/api/v5/trade/order", query)
    await _get(state, "config_after", "/api/v5/account/config", {})


async def _collect_close_final_v1(state, target):
    if state["kind"] == "recovery":
        from app.database.repositories import native_postsubmit_v1 as repository

        close_raw = repository._close_history_context_v1(state["binding"])[2]
    else:
        close_raw = state["close_response_view"].close_intent_view.intent_readback[1]
    close_document = facts.decode(close_raw, 140000)
    expected = facts.decode(
        facts.decode(target.facts.intent_json.encode())["intent_json"].encode()
    )["exact_order"]
    closed = facts.decode(close_document["exact_close_order_json"].encode())
    queries = {
        "entry": {"instId": expected["instId"], "clOrdId": expected["clOrdId"]},
        "close": {"instId": closed["instId"], "clOrdId": closed["clOrdId"]},
    }
    await _get(state, "config_before", "/api/v5/account/config", {})
    orders = {}
    for prefix in ("entry", "close"):
        rows = await _get(
            state, prefix + "_order_before", "/api/v5/trade/order", queries[prefix]
        )
        _need(len(rows) == 1 and type(rows[0].get("ordId")) is str)
        facts.source_time(rows[0].get("cTime"), state["observations"][-1][1])
        orders[prefix] = rows[0]
    for prefix in ("entry", "close"):
        query = {
            "instType": "SWAP",
            "instId": expected["instId"],
            "ordId": orders[prefix]["ordId"],
        }
        await _pages(
            state, prefix + "_fills_recent", "/api/v5/trade/fills", query, "billId"
        )
        await _pages(
            state,
            prefix + "_fills_history",
            "/api/v5/trade/fills-history",
            query,
            "billId",
        )
    await _get(state, "balance", "/api/v5/account/balance", {})
    await _get(state, "positions", "/api/v5/account/positions", {})
    await _get(state, "account_risk", "/api/v5/account/account-position-risk", {})
    await _pages(state, "pending_orders", "/api/v5/trade/orders-pending", {}, "ordId")
    for kind in read.ALGO_TYPES:
        await _pages(
            state,
            "algo_" + kind,
            "/api/v5/trade/orders-algo-pending",
            {"ordType": kind},
            "algoId",
        )
    await _get(
        state,
        "original_protection_detail",
        "/api/v5/trade/order-algo",
        {"algoId": close_document["protection_algo_id"]},
    )
    await _get(
        state,
        "leverage",
        "/api/v5/account/leverage-info",
        {"instId": expected["instId"], "mgnMode": "isolated"},
    )
    stamp = _clock(state)
    window = {
        "begin": str(int(orders["entry"]["cTime"]) - 1),
        "end": str(stamp["utc_ns"] // 1_000_000),
    }
    for role, path in (
        ("final_fills_recent_all", "/api/v5/trade/fills"),
        ("final_fills_history_all", "/api/v5/trade/fills-history"),
    ):
        await _pages(
            state,
            role,
            path,
            {"instType": "SWAP", "instId": expected["instId"], **window},
            "billId",
        )
    await _pages(state, "bills", "/api/v5/account/bills-archive", window, "billId")
    for prefix in ("entry", "close"):
        await _get(
            state, prefix + "_order_after", "/api/v5/trade/order", queries[prefix]
        )
    await _get(state, "config_after", "/api/v5/account/config", {})


async def _run_close_post_read_v1(state):
    from app.database.repositories import native_postsubmit_v1 as repository
    from app.trade_qualification import native_close_final_facts_v1 as closing
    from app.trade_qualification import native_controlled_close_v1 as close

    try:
        async with asyncio.timeout_at(state["loop_deadline"]):
            state["phase"] = "close_final_original_target_readback"
            state["target"] = await repository.read_native_target_v1(state["binding"])
            view = state["close_response_view"].close_intent_view
            _need(
                state["target"].core_json == view.target.core_json
                and state["target"].current_state == "uncertain"
                and state["target"].current_state_revision == 3
            )
            state["phase"] = "close_final_actual_post_response_readback"
            await repository.record_native_close_response_v1(state["binding"])
            with _new_read_context(state):
                state["phase"] = "close_final_fresh_gets"
                await _collect_close_final_v1(state, state["target"])
            state["phase"] = "close_final_durable_raw_parts"
            await repository.retain_native_readback_v1(state["binding"])
            # Original/preflight receipts remain exact held native objects.
            # The repository then independently rebuilds ALL three groups from
            # actual committed manifests before recording this DATA outcome.
            _replay_retained_native_close_intent_v1(view, key=close._SOURCE_KEY_V1)
            prior = _UNKNOWN[view.binding]["durable_close_preflight"]
            state["closed_trade_assessment"] = closing.assess_native_closed_trade_v1(
                state["target"].facts,
                view.filled_result_view.readback[2],
                prior[2],
                view.intent_readback[1],
                state["durable_observations"],
            )
            state["phase"] = "close_final_independent_data_readback"
            outcome = await repository.record_native_closed_trade_readback_v1(
                state["binding"]
            )
            verified = state["verified_closed_trade_readback"]
            state["owned_closed_trade_data_result"] = NativeReconciliationResultV1(
                state["reservation_id"],
                "own_entry_and_close_filled_flat_hold_retained",
                outcome,
                facts.sha(verified[1].receipt_json),
                True,
            )
            state["phase"] = "close_final_sql_transition_and_committed_readback"
            result = await repository.finish_native_filled_flat_reconciliation_v1(
                state["binding"]
            )
            state["phase"] = "close_final_durable_full_forensics_readback"
            await repository.record_native_closed_forensics_v1(state["binding"])
            state["result"] = result
            return result
    except BaseException as error:
        if isinstance(error, GeneratorExit):
            raise
        # No account transaction remains held here. Persistent safety latch
        # takes priority over best-effort adverse DATA retention.
        try:
            if state.get("source") is None:
                stop = close._inhibit_native_close_post_read_before_source_v1(
                    state["close_response_cap"], key=_SOURCE_KEY_V1
                )
            else:
                source = _captured_native_close_post_read_source_v1(state)
                stop = source._inhibit_native_close_post_read_source_v1(
                    state["source"].source_generation, key=_SOURCE_KEY_V1
                )
            async with asyncio.timeout(5):
                await stop
            state["close_final_persistent_stop"] = "confirmed"
        except BaseException:  # noqa: BLE001 -- all owners retained; no actual release/retry or private error text.
            state["close_final_persistent_stop"] = "unconfirmed"
        try:
            if state.get("target") is not None:
                async with asyncio.timeout_at(state["loop_deadline"]):
                    await repository.retain_native_failed_attempt_v1(state["binding"])
        except BaseException as retention_error:  # noqa: BLE001 -- partial bytes stay in private RAM, never fake EOF.
            state["close_final_failure_retention"] = "unconfirmed_native_memory_only"
            if isinstance(
                retention_error, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)
            ):
                error = retention_error
        if isinstance(error, asyncio.CancelledError):
            raise asyncio.CancelledError() from None
        if isinstance(error, (KeyboardInterrupt, SystemExit)):
            raise type(error)() from None
        raise NativePostsubmitError("native_close_final_readback_unknown") from None
    finally:
        _retire(state)


async def _run_close_preflight_v1(state):
    from app.database.repositories import native_postsubmit_v1 as repository
    from app.trade_qualification import native_close_contracts_v1 as contracts
    from app.trade_qualification import native_controlled_close_v1 as close

    try:
        async with asyncio.timeout_at(state["loop_deadline"]):
            state["phase"] = "close_original_target_readback"
            state["target"] = await repository.read_native_target_v1(state["binding"])
            original = state["close_entry_view"].filled_result_view
            _need(
                state["target"].current_state == "uncertain"
                and state["target"].current_state_revision == 3
                and state["target"].request_json
                == original.response_data.transport_facts.request_json
                and state["target"].facts.durable_lineage_sha256
                == facts.decode(original.readback[1].receipt_json)[
                    "durable_lineage_sha256"
                ]
            )
            with _new_read_context(state):
                state["phase"] = "close_fresh_preflight"
                await _collect_close_preflight_v1(state, state["target"])
            state["phase"] = "close_durable_raw_parts"
            await repository.retain_native_readback_v1(state["binding"])
            state["phase"] = "close_preflight_computation"
            state["close_preflight"] = contracts.assess_native_close_preflight_v1(
                state["target"].facts,
                original.readback[2],
                state["durable_observations"],
            )
            await repository.record_native_close_preflight_v1(state["binding"])
            state["phase"] = "close_unique_durable_intent"
            await repository.create_native_close_intent_v1(state["binding"])
            owner = close._finish_owned_native_close_intent_v1(
                state["close_entry_cap"], state["binding"], key=_SOURCE_KEY_V1
            )
            state["close_intent_owner"] = owner
            return owner
    except BaseException as error:
        if isinstance(error, GeneratorExit):
            _retire(state)
            raise
        # Genuine Source inhibition is independent of expired entry/read clocks.
        try:
            if state.get("source") is None:
                await _stop_close_original_entry_v1(state["close_entry_cap"])
            else:
                source = _captured_native_close_read_source_v1(state)
                stop = source._inhibit_native_close_read_source_v1(
                    state["source"].source_generation, key=_SOURCE_KEY_V1
                )
                async with asyncio.timeout(5):
                    await stop
            state["close_persistent_stop"] = "confirmed"
        except BaseException:  # noqa: BLE001 -- actual owners/raw bytes stay UNKNOWN; never private exception text.
            state["close_persistent_stop"] = "unconfirmed"
        try:
            if state.get("target") is not None:
                # Persist conservative stop first, after all transactions
                # exited; then retain bounded adverse DATA under the SAME
                # original read budget, without fabricating EOF/completeness.
                async with asyncio.timeout_at(state["loop_deadline"]):
                    await repository.retain_native_failed_attempt_v1(state["binding"])
        except BaseException as retention_error:  # noqa: BLE001 -- memory custody/risk hold survive failed DATA persistence.
            state["close_failure_retention"] = "unconfirmed_native_memory_only"
            if isinstance(
                retention_error, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)
            ):
                error = retention_error
        finally:
            _retire(state)
        if isinstance(error, asyncio.CancelledError):
            raise asyncio.CancelledError() from None
        if isinstance(error, (KeyboardInterrupt, SystemExit)):
            raise type(error)() from None
        raise NativePostsubmitError(
            "native_close_preflight_or_intent_unknown"
        ) from None


def _replay_persistence_source_v1(binding, *, key):
    from app.database.repositories import native_postsubmit_v1 as repository

    _need(key is repository._SOURCE_KEY_V1 and type(binding) is object)
    if _ZERO_PERSISTENCE_V2.get(binding) is not None:
        state = _zero_persistence_clock_v2(_ZERO_PERSISTENCE_V2[binding])
        return state, state["source"]
    if _FILLED_PERSISTENCE.get(binding) is not None:
        state = _filled_persistence_clock_v1(_FILLED_PERSISTENCE[binding])
        # Same actual configured writer factory/scope; Source authority has
        # retired and cannot be revived. This branch permits DATA DB only.
        return state, state["source"]
    state = _LIVE.get(binding)
    _identity(state)
    return state, _source(state)


def _note_durable_target_v1(binding, target, *, key):
    from app.database.repositories import native_postsubmit_v1 as repository

    _need(
        key is repository._SOURCE_KEY_V1
        and type(binding) is object
        and type(target) is repository.NativeTargetReadbackV1
    )
    state = _LIVE.get(binding)
    source = _source(state)
    _need(
        target.scope == source.scope
        and target.session_factory is source.session_factory
        and target.reservation_id == state["reservation_id"]
    )
    previous = state.get("durable_target")
    if previous is not None:
        _need(
            previous.core_json == target.core_json
            and previous.current_capture_id == target.current_capture_id
            and previous.current_head_sha256 == target.current_head_sha256
        )
    state["durable_target"] = target


def _replay_durable_target_for_stop_v1(source_generation, *, key):
    """Actual retained UID/factory target for native read/negative-stop purpose.

    No trade clock/Arm or old credential is restored after timeout/retirement.
    The native source module still compares its own exact scope and factory.
    """
    from app.database.repositories import native_postsubmit_v1 as repository
    from app.trade_qualification import native_controlled_account_source_v6 as source

    _need(key is source._RECONCILIATION_KEY_V1 and type(source_generation) is object)
    matches = [
        state
        for state in _UNKNOWN.values()
        if type(state) is dict
        and state.get("source") is not None
        and state["source"].source_generation is source_generation
        and state["pid"] == os.getpid()
        and state["thread"] == threading.get_ident()
        and state["task"] is asyncio.current_task()
        and state["loop"] is asyncio.get_running_loop()
    ]
    _need(len(matches) == 1)
    state = matches[0]
    guard.check_saved_native_guard_v1(state["guard"])
    target = state.get("durable_target")
    _need(
        type(target) is repository.NativeTargetReadbackV1
        and target.scope == state["source"].scope
        and target.session_factory is state["source"].session_factory
        and target.reservation_id == state["reservation_id"]
    )
    return target


async def _run(state):
    from app.database.repositories import native_postsubmit_v1 as repository
    from app.trade_qualification import native_account_risk_assembly_v1 as account

    failure, stop = None, None
    try:
        # A fresh read-only budget is not a renewed trading budget. It covers
        # new source, all GETs, DB commits/readbacks and transaction exits once.
        async with asyncio.timeout_at(state["loop_deadline"]):
            _source(state)
            state["target"] = await repository.read_native_target_v1(state["binding"])
            _source(state)
            if state["kind"] == "recovery":
                state["phase"] = "durable_unique_close_history_readback"
                history = await repository.read_native_recovery_close_history_v1(
                    state["binding"]
                )
                if history is not None:
                    # A committed close request is a permanent no-retry fact.
                    # Fresh GETs reconstruct only that exact existing request.
                    if state["target"].current_state == "reconciled_flat":
                        await repository.read_native_recovered_filled_closure_v1(
                            state["binding"]
                        )
                    with _new_read_context(state):
                        state["phase"] = "fresh_recovered_close_gets"
                        await _collect_close_final_v1(state, state["target"])
                    state["phase"] = "recovered_close_durable_raw_parts"
                    await repository.retain_native_readback_v1(state["binding"])
                    state["closed_trade_assessment"] = (
                        _close_final_facts.assess_native_closed_trade_v1(
                            state["target"].facts,
                            history.original_observations,
                            history.preflight_observations,
                            history.close_intent_json,
                            state["durable_observations"],
                        )
                    )
                    await repository.record_native_closed_trade_readback_v1(
                        state["binding"]
                    )
                    state["phase"] = "recovered_close_sql_and_new_xid_readback"
                    result = await repository.finish_native_recovered_filled_reconciliation_v1(
                        state["binding"]
                    )
                    state["result"] = result
                    return result
            if state["target"].current_state == "reconciled_flat":
                state["phase"] = "historical_sql_closure_readback"
                await repository.read_native_flat_closure_v1(state["binding"])
                with _new_read_context(state):
                    state["phase"] = "fresh_previously_closed_order_readback"
                    await _collect_previously_closed(state, state["target"])
                result = await repository.finish_previously_closed_recovery_v1(
                    state["binding"]
                )
                state["result"] = result
                return result
            state["phase"] = "initial_observation"
            await repository.record_native_initial_v1(state["binding"])
            _source(state)
            with _new_read_context(state):
                state["phase"] = "fresh_gets"
                await _collect(state, state["target"])
            state["phase"] = "durable_raw_parts"
            await repository.retain_native_readback_v1(state["binding"])
            _source(state)
            # Adverse/partial/future facts are evaluated only after complete
            # received raw pages have a separate committed reconstruction.
            state["phase"] = "computation"
            state["assessment"] = facts.assess_native_reconciliation_v1(
                state["target"].facts, state["durable_observations"]
            )
            _source(state)
            await repository.record_native_assessment_v1(state["binding"])
            _source(state)
            state["phase"] = "reconciliation"
            result = await repository.finish_native_reconciliation_v1(state["binding"])
            _source(state)
            state["result"] = result
            return result
    except BaseException as error:  # noqa: BLE001 -- Preserve the reconciliation failure through owner cleanup.
        failure = error
        if (
            type(error) is facts.NativeReconciliationFactsError
            and len(error.args) == 1
            and type(error.args[0]) is str
            and error.args[0] in _PUBLIC_UNKNOWN_REASONS
        ):
            state["unknown_reason"] = error.args[0]
        source = state.get("source")
        if source is not None:
            # Native getter tightens synchronously even after trade/read expiry;
            # persistence is awaited only after writer transactions have exited.
            stop = account._inhibit_native_reconciliation_read_source_v1(
                source.source_generation, key=_SOURCE_KEY_V1
            )
            _need(stop is not None, "native_postsubmit_stop_persistence_unavailable")
        if stop is not None:
            # All data transactions above have exited. Persist the genuine
            # safety latch immediately, before optional forensic retention;
            # a crash must not leave that latch waiting behind the read budget.
            try:
                async with asyncio.timeout(5):
                    await stop
            except BaseException:  # noqa: BLE001 - explicit unknown stop; preserve all actual owners and raw buffers.
                state["persistent_stop"] = "unconfirmed"
                raise NativePostsubmitError(
                    "native_postsubmit_unknown_stop_unconfirmed"
                ) from None
            state["persistent_stop"] = "confirmed"
            stop = None
        # Negative local inhibition above is synchronous and its persistent
        # latch has now been attempted. Save the adverse
        # attempt only within the SAME original read budget; no new GET, trade
        # lease or persistence retry is created. Unsafe partial JSON remains
        # explicitly memory-only, with counts/hashes/times when possible.
        if state.get("initial_outcome_id") is not None:
            try:
                async with asyncio.timeout_at(state["loop_deadline"]):
                    await repository.retain_native_failed_attempt_v1(state["binding"])
            except BaseException as retention_error:  # noqa: BLE001 - preserve full unknown owner and control exceptions; never log private errors.
                state["failed_attempt_retention"] = "unconfirmed_native_memory_only"
                if isinstance(
                    retention_error,
                    (asyncio.CancelledError, KeyboardInterrupt, SystemExit),
                ):
                    failure = retention_error
    finally:
        _retire(state)
    if isinstance(failure, asyncio.CancelledError):
        raise asyncio.CancelledError() from None
    if isinstance(failure, (KeyboardInterrupt, SystemExit)):
        raise type(failure)() from None
    raise NativePostsubmitError(
        state.get("unknown_reason", "native_postsubmit_reconciliation_unknown")
    ) from None


# Source proposal only: exact native source/catalog/response owners must be
# composed and actually validated before admission. DATA never replaces them.
from app.database.repositories import (
    collector_current_0055_multirole_catalog_v1 as _catalog55,
)
from app.database.repositories import demo_control_current55_v2 as _control55
from app.database.repositories import native_closed_reporting_v55 as _closed_reporting
from app.database.repositories import native_postsubmit_v1 as _repository
from app.database.repositories import native_zero_fok_event_schema_v55 as _event55
from app.database.repositories import submission_reporting as _shared_reporting
from app.demo_automation import native_caller_v2 as _caller
from app.demo_automation import native_requests_v2 as _caller_models
from app.demo_automation import native_server_selectors_v2 as _selector
from app.domain import native_close_intent_schema_v55 as _close_intent55
from app.domain import native_filled_flat_closure_schema_v55 as _filled_schema55
from app.domain import native_zero_fok_closure_schema_v55 as _zero_schema55
from app.exchange.okx import native_demo_write_v2 as _write
from app.exchange.okx import private_rest as _private
from app.trade_evidence import native_closed_forensics_v55 as _closed_forensics
from app.trade_evidence import native_forensics_v2 as _entry_forensics
from app.trade_evidence import outbox as _outbox
from app.trade_evidence import submission_reporting as _report_facts
from app.trade_qualification import native_account_risk_assembly_v1 as _account
from app.trade_qualification import native_close_contracts_v1 as _close_contracts
from app.trade_qualification import native_close_final_facts_v1 as _close_final_facts
from app.trade_qualification import native_demo_control_owner_v2 as _control_owner55

_NATIVE_GUARD = guard.install_native_guards_v1(
    sys.modules[__name__],
    (
        "reconcile_owned_native_response_v1",
        "reconcile_native_recovery_v1",
        "_replay_native_zero_source_readset_v2",
        "_replay_native_zero_r7_retirement_for_source_v2",
        "_replay_native_zero_root_finalization_v2",
        "_take_native_zero_cleanup_committed_for_source_v2",
        "_scan_native_zero_cleanup_persistence_v2",
        "_take_native_zero_caller_completion_v2",
        "dispose_owned_native_zero_caller_resources_v2",
        "recover_native_for_service_v1",
        "_retained_native_recovery_history_v1",
        "_take_native_flat_result_for_root_settlement_v1",
        "_replay_native_flat_result_for_root_settlement_v1",
        "_take_native_flat_cleanup_completion_for_root_v1",
        "_take_native_flat_source_settlement_v1",
        "_replay_native_flat_source_settlement_v1",
        "_take_native_flat_ledger_settlement_v1",
        "_replay_native_flat_ledger_settlement_v1",
        "_replay_native_flat_source_after_finalization_v1",
        "_replay_native_flat_ledger_after_finalization_v1",
        "_require_native_flat_source_retired_for_ledger_v1",
        "_take_native_filled_result_for_close_v1",
        "_replay_native_filled_result_for_close_v1",
        "_replay_native_forensics_entry_v1",
        "_take_native_filled_flat_result_for_root_settlement_v1",
        "_replay_native_filled_flat_result_for_root_settlement_v1",
        "_take_native_filled_flat_cleanup_completion_for_root_v1",
        "_take_native_filled_flat_retirement_for_root_v1",
        "_take_native_filled_flat_source_settlement_v1",
        "_replay_native_filled_flat_source_settlement_v1",
        "_replay_native_filled_flat_source_after_finalization_v1",
        "_replay_native_filled_flat_source_readsets_v1",
        "_replay_native_filled_flat_r7_retirement_for_source_v1",
        "_take_native_filled_flat_ledger_settlement_v1",
        "_replay_native_filled_flat_ledger_settlement_v1",
        "_replay_native_filled_flat_ledger_after_finalization_v1",
        "_require_native_filled_flat_source_retired_for_ledger_v1",
        "_replay_native_filled_flat_for_close_cleanup_v1",
        "_take_native_filled_cleanup_committed_for_source_v1",
        "_replay_native_filled_cleanup_for_close_compaction_v1",
        "_take_native_filled_caller_completion_v1",
        "dispose_owned_native_filled_caller_resources_v1",
        "_scan_native_filled_cleanup_persistence_v1",
        "settle_owned_native_filled_reconciliation_v1",
        "complete_owned_native_filled_demo_v1",
        "_register_native_close_read_v1",
        "_register_native_close_post_read_v1",
        "reconcile_owned_native_close_response_v1",
        "reconcile_owned_native_close_attempt_v1",
        "prepare_owned_native_close_intent_v1",
        "_take_native_close_intent_for_owner_v1",
        "_replay_native_close_intent_for_owner_v1",
        "_replay_retained_native_close_intent_v1",
        "_retire_native_close_intent_read_v1",
        "settle_owned_native_reconciliation_v1",
    ),
    dependencies=(
        facts,
        read,
        _read_network,
        facts.materializer,
        facts.wire,
        _repository,
        _repository.ledger,
        _repository.issued,
        _repository.control_bound,
        _control55,
        _catalog55,
        _catalog55.policy,
        _catalog55.policy.native_closure,
        _catalog55.policy.native_close_intent,
        _catalog55.policy.native_close_index,
        _catalog55.policy.native_closure.zero,
        _catalog55.policy.native_terminal,
        _catalog55.policy.native_terminal.filled,
        _catalog55.policy.native_report,
        _catalog55.policy.native_report.schema,
        _catalog55.policy.native_report.helpers,
        _event55,
        _event55.schema,
        _event55.live_schema,
        _zero_schema55,
        _close_intent55,
        _filled_schema55,
        _close_contracts,
        _close_final_facts,
        _write,
        _write._native_vendors,
        _write._native_settlement,
        _private,
        _account,
        _write._native_filled_settlement,
        _write._native_filled_settlement.passive,
        _entry_forensics,
        _entry_forensics.core,
        _entry_forensics.execution,
        _closed_forensics,
        _closed_forensics.entry,
        _closed_forensics.core,
        _closed_forensics.execution,
        _closed_reporting,
        _closed_reporting.catalog,
        _closed_reporting.report_schema,
        _shared_reporting,
        _outbox,
        _report_facts,
        _control_owner55,
        _control_owner55.original,
        _caller,
        _caller_models,
        _selector,
    ),
    bindings=_write._VENDOR_ADMISSION_V2.bindings,
)
