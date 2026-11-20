"""Same-request protected holding; revoke entry while retaining durable risk.

This consumes a genuine full-fill result, never a DTO or restored Arm. Resource
closure is independent of reservation resolution. Protective exits use a NEW
bounded DATA-only recovery; original entry/event/SL/TP/expiry are never renewed.
"""

import asyncio
import os
import sys
import threading
from dataclasses import dataclass, field

from app.database.repositories import native_protective_hold_v1 as _repository
from app.domain.native_clock import native_stamp
from app.trade_qualification import native_continuation_guard_v1 as guard

_PERSISTENCE = {}
_COMPLETIONS = {}
_COMPACT_BINDINGS = {}
_SOURCE_KEY_V1 = object()
_SECONDS = 15


def _need(value):
    if value is not True:
        raise ValueError("native_protected_hold_unconfirmed") from None


@dataclass(frozen=True, slots=True, repr=False)
class NativeProtectedHoldCompletionV1:
    reservation_id: str
    hold_outcome_id: str
    cleanup_outcome_id: str
    controller: object = field(repr=False)
    risk_hold_retained: bool = field(default=True, init=False)
    entry_authority_revoked: bool = field(default=True, init=False)
    execution_authority: bool = field(default=False, init=False)
    order_retry_authority: bool = field(default=False, init=False)


def _identity(saved):
    from app.demo_automation import native_caller_v2 as caller
    from app.trade_qualification import native_postsubmit_reconciliation_v1 as post

    _NATIVE_GUARD()
    _need(type(saved) is dict and _PERSISTENCE.get(saved["state"]["binding"]) is saved)
    _need(
        saved["task"] is asyncio.current_task()
        and saved["pid"] == os.getpid()
        and saved["thread"] == threading.get_ident()
    )
    guard.check_saved_native_guard_v1(saved["guard"])
    caller._ready(saved["caller"]["service"], saved["caller"])
    caller._check_request_v2(
        saved["caller"]["original_native_request"],
        saved["caller"]["frozen_native_plan"],
    )
    state, source, original = post._retained_filled_record_v1(saved["result"])
    _need(
        state is saved["state"]
        and source is saved["source"]
        and original is saved["original"]
    )
    stamp = native_stamp()
    _need(
        all(
            saved["last"][name]
            <= stamp[name]
            < saved["started"][name] + _SECONDS * 1_000_000_000
            for name in ("utc_ns", "monotonic_ns")
        )
    )
    saved["last"] = stamp
    return state


def _replay_owned_hold_persistence_v1(binding, *, key):
    from app.database.repositories import native_postsubmit_v1 as journal

    _need(key is journal._SOURCE_KEY_V1 and type(binding) is object)
    saved = _PERSISTENCE.get(binding)
    _need(type(saved) is dict)
    state = _identity(saved)
    return state, state["source"]


def has_owned_hold_persistence_v1(binding):
    return type(binding) is object and binding in _PERSISTENCE


async def _closed_transports(saved):
    from app.demo_automation import native_server_selectors_v2 as selectors
    from app.exchange.okx import native_demo_settlement_v1 as passive
    from app.exchange.okx import native_demo_write_v2 as write
    from app.trade_qualification import native_controlled_account_source_v6 as source

    state = _identity(saved)
    gate = write._native_reconciliation_record_v1(
        state["root_handle"], key=saved["post_key"]
    )
    record = source._RECONCILIATION_READ_V6.get(state["source"].source_generation)
    _need(type(record) is dict and record["client"] is state["source"].client)
    journals = vars(record["client"]).get("_native_reconciliation_read_journals_v1")
    _need(type(journals) is list and len(journals) == len(state["observations"]))
    selected = saved["caller"]["selection"]
    selected_record = selectors._record(selected)
    selector_journals = vars(saved["caller"]["client"]).get(
        "_native_reconciliation_read_journals_v1"
    )
    _need(type(selector_journals) is list and len(selector_journals) == 1)
    _need(
        selector_journals[0].signature == selected_record["selector_signature"]
        and selector_journals[0].observation.raw_body
        == selected_record["evidence"].selector_raw_body
    )
    while True:
        _identity(saved)
        _need(not gate["session"].in_transaction())
        if (
            passive._socket_closed(gate)
            and passive._sdk_closed(gate)
            and all(
                source._closed_reconciliation_get_v6(
                    item, record["read_lease_binding_id"]
                )
                is not None
                for item in journals
            )
            and source._closed_reconciliation_get_v6(
                selector_journals[0], selected_record["read_session"].binding_id
            )
            is not None
        ):
            break
        await asyncio.sleep(0.01)
    # These are this exact selector's NullPool read engines. Shared writer
    # ownership is preserved. Disposing pools neither resolves nor deletes risk.
    factories = saved["caller"]["factories"]
    _need(
        selected.factories is factories
        and selectors._record(selected) is selected_record
    )
    reader, custody = factories.frame_reader_engine, factories.custody_engine
    _need(
        reader is not custody
        and reader is not factories.writer_factory.kw["bind"]
        and custody is not factories.writer_factory.kw["bind"]
        and type(reader.sync_engine.pool) is selectors.NullPool
        and type(custody.sync_engine.pool) is selectors.NullPool
    )
    await reader.dispose()
    _identity(saved)
    await custody.dispose()
    _identity(saved)
    return True


async def complete_owned_protected_hold_v1(result, caller_state, *, key):
    """Durable actual hold -> genuine DISARM -> closed resources -> readback."""
    from app.database.repositories import native_postsubmit_v1 as journal
    from app.database.repositories import native_protective_hold_v1 as repository
    from app.demo_automation import native_caller_v2 as caller
    from app.trade_qualification import native_postsubmit_reconciliation_v1 as post

    _NATIVE_GUARD()
    _need(
        key is caller._SETTLEMENT_KEY_V1
        and caller._ACTIVE.get(caller_state.get("service")) is caller_state
    )
    caller._ready(caller_state["service"], caller_state)
    from app.demo_automation.native_requests_v2 import (
        PROTECTED_ENTRY_CONFIRMATION_V1,
        NativeDemoProtectedEntryRequestV1,
    )

    caller._check_request_v2(
        caller_state["original_native_request"], caller_state["frozen_native_plan"]
    )
    _need(
        caller_state.get("normal_protected_entry") is True
        and type(caller_state["original_native_request"])
        is NativeDemoProtectedEntryRequestV1
        and caller_state["frozen_native_plan"].confirmation
        == PROTECTED_ENTRY_CONFIRMATION_V1
        and caller_state.get("reconciliation_data") is result
    )
    state, response, original = post._retained_filled_record_v1(result)
    _need(
        state.get("filled_result_taken_for_close") is not True
        and state.get("normal_hold_taken") is not True
    )
    state["normal_hold_taken"] = True
    state["normal_hold_original"] = original
    controller = caller_state["controller"]
    from app.trade_qualification import native_demo_control_owner_v2 as facade

    facade._REQUIRE_KERNEL()
    owner_record = facade._HANDLES.get(controller)
    _need(
        type(owner_record) is dict
        and owner_record.get("owner") is state["source"].control_owner
        and owner_record.get("identity_selection") is caller_state["selection"]
    )
    started = native_stamp()
    saved = {
        "binding": state["binding"],
        "state": state,
        "source": response,
        "original": original,
        "result": result,
        "caller": caller_state,
        "task": asyncio.current_task(),
        "pid": os.getpid(),
        "thread": threading.get_ident(),
        "started": started,
        "last": started,
        "guard": {},
        "post_key": post._SOURCE_KEY_V1,
    }
    guard.save_native_guard_v1(saved["guard"], post._NATIVE_GUARD)
    _PERSISTENCE[state["binding"]] = saved
    try:
        async with asyncio.timeout(_SECONDS):
            _identity(saved)
            hold = await repository.append_owned_protected_hold_v1(state["binding"])
            _identity(saved)
            stopped = await controller.disarm()
            _need(
                stopped.state.arm_request_id is None
                and stopped.state.arm_expires_at is None
            )
            _identity(saved)
            await _closed_transports(saved)
            _identity(saved)
            document = {
                "version": "ctcc.native_protected_hold_resource_completion.v1",
                "reservation_id": result.reservation_id,
                "hold_outcome_id": hold,
                "entry_authority_revoked": True,
                "resources_closed": True,
                "risk_hold_retained": True,
                "execution_authority": False,
                "order_retry_authority": False,
            }
            async with journal._transaction(state["binding"]) as parts:
                cleanup = await journal._append(
                    state["binding"], parts, document, kind="reconciliation"
                )
                write_metadata = parts[-1]
            async with journal._transaction(state["binding"]) as parts:
                rows = await journal._outcomes(parts[3], state["target"])
                _need(
                    parts[-1][8] != write_metadata[8]
                    and rows[-1].outcome_id == cleanup
                    and journal.decode(rows[-1].capture_json.encode(), 140000)
                    == document
                )
                current = await journal._target_locked(
                    state["binding"], parts[1], parts[2], parts[3], parts[4], parts[-1]
                )
                _need(
                    (current.current_state, current.current_state_revision)
                    == ("uncertain", 3)
                )
            _identity(saved)
            completion = NativeProtectedHoldCompletionV1(
                result.reservation_id, hold, cleanup, controller
            )
            saved["completion"] = completion
            _COMPLETIONS[id(completion)] = saved
            saved["cleanup_outcome_id"] = cleanup
            from app.trade_qualification import (
                native_controlled_account_source_v6 as source,
            )

            saved["retirement_phase"] = "PREPARED"
            saved["ram_retirement_receipt"] = (
                source.retire_native_protected_hold_ram_v1(
                    completion, key=_SOURCE_KEY_V1
                )
            )
            saved["completed"] = True
            return completion
    except BaseException:
        saved["uncertain"] = True
        raise
    finally:
        if saved.get("completed") is True:
            _PERSISTENCE.pop(saved["binding"], None)


def _take_normal_hold_retirement_for_source_v1(completion, *, key):
    from app.trade_qualification import native_controlled_account_source_v6 as source

    _NATIVE_GUARD()
    _need(
        key is source._NORMAL_HOLD_SOURCE_KEY_V1
        and type(completion) is NativeProtectedHoldCompletionV1
    )
    saved = _COMPLETIONS.get(id(completion))
    _need(
        type(saved) is dict
        and saved.get("completion") is completion
        and saved.get("retirement_phase") == "PREPARED"
    )
    state = _identity(saved)
    _need(
        saved["cleanup_outcome_id"] == completion.cleanup_outcome_id
        and state["normal_hold_committed"][0] == completion.hold_outcome_id
    )
    saved["retirement_phase"] = "RETIRING"
    return saved


def _remember_normal_hold_ram_marker_v1(saved, marker, *, key):
    from app.trade_qualification import native_controlled_account_source_v6 as source

    _need(
        key is source._NORMAL_HOLD_SOURCE_KEY_V1
        and saved.get("retirement_phase") == "RETIRING"
    )
    completion = saved["completion"]
    _need(
        _COMPLETIONS.get(id(completion)) is saved
        and completion.reservation_id not in _COMPACT_BINDINGS
    )
    _COMPACT_BINDINGS[completion.reservation_id] = marker
    saved["retirement_phase"] = "RETIRED"


def finalize_normal_hold_ram_marker_after_flat_v1(state, result, *, key):
    """RAM capacity only after genuine NEW terminal SQL/raw readback; no Arm."""
    from app.trade_qualification import native_ledger_runtime_v2 as ledger
    from app.trade_qualification import native_postsubmit_reconciliation_v1 as post

    _NATIVE_GUARD()
    _need(
        key is post._SOURCE_KEY_V1
        and type(state) is dict
        and post._UNKNOWN.get(state["binding"]) is state
    )
    _need(
        state["task"] is asyncio.current_task()
        and state["loop"] is asyncio.get_running_loop()
        and state["pid"] == os.getpid()
        and state["thread"] == threading.get_ident()
    )
    verified = state.get("verified_normal_protected_recovery")
    _need(
        type(verified) is tuple
        and verified[0] is result
        and state.get("result") is result
        and result.risk_hold_retained is False
        and result.classification
        in (
            "original_oco_full_exit_flat",
            "original_oco_full_exit_flat_previously_reconciled",
        )
    )
    held = _COMPACT_BINDINGS.get(result.reservation_id)
    if held is None:
        return False  # Fresh process has no historical RAM/socket ownership.
    binding, original = held
    _need(
        ledger._UNKNOWN.get(binding) is original
        and original[2] == "protected_hold_retained_ram_retired_v1"
    )
    target = state["target"]
    _need(
        original[0] == target.request.origin.original_event_key
        and original[3] == post.facts.sha(target.request_json.encode())
        and original[4] == (target.scope.environment, target.scope.account_id)
    )
    ledger._UNKNOWN[binding] = (
        *original[:2],
        ledger._FINALIZED_PHASE_V1,
        *original[3:],
    )
    del _COMPACT_BINDINGS[result.reservation_id]
    return True


def take_owned_protected_hold_completion_v1(completion, caller_state, *, key):
    from app.demo_automation import native_caller_v2 as caller

    _NATIVE_GUARD()
    _need(
        key is caller._SETTLEMENT_KEY_V1
        and type(completion) is NativeProtectedHoldCompletionV1
    )
    saved = _COMPLETIONS.get(id(completion))
    _need(
        type(saved) is dict
        and saved.get("completed") is True
        and saved.get("completion") is completion
        and saved["caller"] is caller_state
        and saved["task"] is asyncio.current_task()
    )
    _need(
        completion.controller is caller_state["controller"]
        and completion.reservation_id == saved["result"].reservation_id
    )
    del _COMPLETIONS[id(completion)]
    return completion


_NATIVE_GUARD = guard.install_native_guards_v1(
    sys.modules[__name__],
    (
        "complete_owned_protected_hold_v1",
        "take_owned_protected_hold_completion_v1",
        "has_owned_hold_persistence_v1",
        "_replay_owned_hold_persistence_v1",
        "_take_normal_hold_retirement_for_source_v1",
        "_remember_normal_hold_ram_marker_v1",
        "finalize_normal_hold_ram_marker_after_flat_v1",
    ),
    dependencies=(_repository,),
)
