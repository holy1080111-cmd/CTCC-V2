"""One native R7 hold through the EXISTING DB0017 reserve/consume mechanisms.

This module contains no SQL ledger engine or exchange client. The repository
keeps its UID->Control->scope locks, event uniqueness, exact coverage, durable
state/intent transaction and independent readback. Only a live R7 owner enters;
all serialized native V2 contracts remain inert historical audit records.
"""

import asyncio
import json
import sys as _native_sys
from collections.abc import Coroutine
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from weakref import WeakKeyDictionary, finalize

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings as _settings_module
from app.database.repositories import (
    collector_current_0055_multirole_catalog_v1 as _native_catalog55,
)
from app.database.repositories import demo_control as _historical_control_repository
from app.database.repositories import (
    demo_control_current55_v2 as _native_control_repository55,
)
from app.database.repositories import (
    native_issuer_transaction as _native_issuer_transaction,
)
from app.database.repositories import native_zero_fok_event_schema_v55 as _zero_event55
from app.database.repositories import qualification_ledger as _native_repository
from app.domain import native_zero_fok_closure_schema_v55 as _zero_schema55
from app.domain.native_clock import native_stamp
from app.domain.source_primitives import sha, utc_from_ns
from app.exchange.okx import private_rest as _private_rest
from app.trade_qualification import control_bound_ledger as _native_control
from app.trade_qualification import native_continuation_guard_v1 as _native_guard
from app.trade_qualification import native_ledger_contracts_v2 as wire
from app.trade_qualification import reservations as ledger
from app.trade_qualification import submission_intent as _native_intent

_ATTEMPTS = {}
_INTENTS = WeakKeyDictionary()
_UNKNOWN = {}
_MAX_ATTEMPTS = 16
_FLAT_CLEANUP_KEY_V1 = object()
_FLAT_CLEANUPS = {}
_FINALIZED_PHASE_V1 = "native_flat_cleanup_finalized_v1"
_FILLED_CLEANUP_KEY_V1 = object()
_FILLED_CLEANUPS = {}


def _deny(code="native_ledger_owner_unavailable"):
    raise ledger.QualificationLedgerError(code) from None


def _clock():
    return utc_from_ns(native_stamp()["utc_ns"])


class OwnedNativeDurableIntentV2:
    """Same-task handoff; final writes require the fixed native transport gate."""

    __slots__ = ("__weakref__",)

    def __new__(cls):
        _deny()

    def __copy__(self):
        _deny()

    def __deepcopy__(self, memo):
        _deny()

    def __reduce_ex__(self, protocol):
        _deny()

    def close(self):
        record = _INTENTS.pop(self, None)
        if record is not None:
            _release(record)


class _OwnedNativeFlatLedgerCleanupV1:
    __slots__ = ()

    def __new__(cls):
        _deny()

    def __copy__(self):
        _deny()

    def __deepcopy__(self, memo):
        _deny()

    def __reduce_ex__(self, protocol):
        _deny()


@dataclass(frozen=True, slots=True, repr=False)
class NativeFlatLedgerCleanupViewV1:
    flat_result_view: object = field(repr=False)
    closed_transport_handle: object = field(repr=False)
    reservation_id: str = field(repr=False)
    original_event_key: str = field(repr=False)
    request_sha256: str = field(repr=False)


def _unknown_flat_record_v1(state):
    if type(state) is not dict or type(state.get("binding")) is not object:
        _deny("native_flat_ledger_cleanup_owner_required")
    marker = _UNKNOWN.get(state["binding"])
    if (
        type(marker) is not tuple
        or len(marker) != 6
        or marker[5] is not state
        or state.get("transport_spent") is not True
        or state.get("transport_unknown_hold") is not True
        or state.get("closed") is not True
        or state["task"] is not asyncio.current_task()
        or _ATTEMPTS.get(state["binding"]) is not None
    ):
        _deny("native_flat_ledger_cleanup_owner_required")
    _native_guard.check_saved_native_guard_v1(state)
    _native_guard.check_saved_native_guard_v1(state["transport_source_guard"])
    request = wire.checked_request(state["request"])
    if wire.canonical_request(request) != state["request_json"]:
        _deny("native_flat_ledger_cleanup_request_changed")
    return request


def prepare_native_flat_ledger_cleanup_v1(cap, *, key):
    """Prepare from actual committed closure; preserve all Root authenticators."""
    from app.trade_qualification import native_postpublication_recheck_v2 as r7
    from app.trade_qualification import native_postsubmit_reconciliation_v1 as post

    _NATIVE_GUARD()
    if key is not post._SOURCE_SETTLEMENT_KEY_V1:
        _deny()
    view = post._take_native_flat_ledger_settlement_v1(cap, key=_FLAT_CLEANUP_KEY_V1)
    state = view.flat_result_view.response_data.transport_state
    request = _unknown_flat_record_v1(state)
    closure = view.flat_result_view.closure_readback
    if (
        view.flat_result_view.response_data.transport_facts.request_json
        != state["request_json"]
        or view.transport_binding is not state["transport_binding"]
        or closure[0] != view.flat_result_view.result.outcome_id
        or closure[1].classification != "terminal_zero_fill_flat"
        or sha(closure[1].receipt_json)
        != view.closed_transport_view.closure_receipt_sha256
        or view.flat_result_view.result.reservation_id
        != state["consumed"].reservation_id
        or state["r7"].get("closed", False)
    ):
        _deny("native_flat_ledger_cleanup_closure_changed")
    result = object.__new__(_OwnedNativeFlatLedgerCleanupV1)
    actual = {
        "cap": cap,
        "state": state,
        "task": asyncio.current_task(),
        "guard": {},
        "r7_guard": {},
        "view": NativeFlatLedgerCleanupViewV1(
            view.flat_result_view,
            view.closed_transport_view.closed_transport_handle,
            state["consumed"].reservation_id,
            request.origin.original_event_key,
            sha(state["request_json"].encode()),
        ),
    }
    _native_guard.save_native_guard_v1(actual["guard"], _NATIVE_GUARD)
    _native_guard.save_native_guard_v1(actual["r7_guard"], r7._NATIVE_GUARD)
    _FLAT_CLEANUPS[result] = actual
    return result


def replay_native_flat_ledger_cleanup_v1(completion, *, key):
    from app.trade_qualification import native_postsubmit_reconciliation_v1 as post

    _NATIVE_GUARD()
    if (
        key is not post._SOURCE_SETTLEMENT_KEY_V1
        or type(completion) is not _OwnedNativeFlatLedgerCleanupV1
    ):
        _deny()
    actual = _FLAT_CLEANUPS.get(completion)
    if type(actual) is not dict or actual["task"] is not asyncio.current_task():
        _deny()
    _native_guard.check_saved_native_guard_v1(actual["guard"])
    _native_guard.check_saved_native_guard_v1(actual["r7_guard"])
    _unknown_flat_record_v1(actual["state"])
    view = post._replay_native_flat_ledger_settlement_v1(
        actual["cap"], key=_FLAT_CLEANUP_KEY_V1
    )
    if (
        view.flat_result_view is not actual["view"].flat_result_view
        or view.closed_transport_view.closed_transport_handle
        is not actual["view"].closed_transport_handle
    ):
        _deny()
    return actual["view"]


def retire_native_flat_ledger_cleanup_v1(completion, *, key):
    """Only after actual Root finalizer returned: release real R7 source chain."""
    from app.trade_qualification import native_postpublication_recheck_v2 as r7
    from app.trade_qualification import native_postsubmit_reconciliation_v1 as post

    _NATIVE_GUARD()
    if (
        key is not post._SOURCE_SETTLEMENT_KEY_V1
        or type(completion) is not _OwnedNativeFlatLedgerCleanupV1
    ):
        _deny()
    actual = _FLAT_CLEANUPS.get(completion)
    if type(actual) is not dict or actual["task"] is not asyncio.current_task():
        _deny()
    _native_guard.check_saved_native_guard_v1(actual["guard"])
    _native_guard.check_saved_native_guard_v1(actual["r7_guard"])
    post._replay_native_flat_ledger_after_finalization_v1(
        actual["cap"], key=_FLAT_CLEANUP_KEY_V1
    )
    state = actual["state"]
    _unknown_flat_record_v1(state)
    r7._release(state["r7"])
    if state["r7"].get("closed") is not True:
        _deny("native_flat_source_release_unconfirmed")
    actual["r7_retired"] = True
    receipt = (
        "ctcc.native_flat_r7_source_chain_retired.v1",
        actual["view"].reservation_id,
        actual["view"].original_event_key,
        actual["view"].request_sha256,
    )
    actual["r7_retirement_receipt"] = receipt
    return receipt


def replay_native_zero_r7_retirement_v2(completion, *, key):
    from app.trade_qualification import native_postsubmit_reconciliation_v1 as post

    _NATIVE_GUARD()
    if (
        key is not post._SOURCE_SETTLEMENT_KEY_V1
        or type(completion) is not _OwnedNativeFlatLedgerCleanupV1
    ):
        _deny()
    actual = _FLAT_CLEANUPS.get(completion)
    if (
        type(actual) is not dict
        or actual["task"] is not asyncio.current_task()
        or actual.get("r7_retired") is not True
    ):
        _deny("native_zero_final_own_R7_retirement_required")
    _native_guard.check_saved_native_guard_v1(actual["guard"])
    _unknown_flat_record_v1(actual["state"])
    receipt = actual.get("r7_retirement_receipt")
    if (
        type(receipt) is not tuple
        or len(receipt) != 4
        or receipt
        != (
            "ctcc.native_flat_r7_source_chain_retired.v1",
            actual["view"].reservation_id,
            actual["view"].original_event_key,
            actual["view"].request_sha256,
        )
        or actual["state"]["r7"].get("closed") is not True
    ):
        _deny("native_zero_final_own_R7_retirement_changed")
    return receipt


def finalize_native_flat_ledger_cleanup_v1(completion, *, key):
    """Compact only after real Source retirement, following Root finalization."""
    from app.trade_qualification import native_postsubmit_reconciliation_v1 as post

    _NATIVE_GUARD()
    if (
        key is not post._SOURCE_SETTLEMENT_KEY_V1
        or type(completion) is not _OwnedNativeFlatLedgerCleanupV1
    ):
        _deny()
    actual = _FLAT_CLEANUPS.get(completion)
    if (
        type(actual) is not dict
        or actual["task"] is not asyncio.current_task()
        or actual.get("r7_retired") is not True
    ):
        _deny()
    _native_guard.check_saved_native_guard_v1(actual["guard"])
    post._require_native_flat_source_retired_for_ledger_v1(
        actual["cap"], key=_FLAT_CLEANUP_KEY_V1
    )
    state = actual["state"]
    request = _unknown_flat_record_v1(state)
    binding = state["binding"]
    # The event/UID tombstone survives. Only successful, already-finalized RAM
    # ownership is compacted; unresolved UNKNOWN keeps its complete graph.
    marker = (
        request.origin.original_event_key,
        request.issuer_sha256,
        _FINALIZED_PHASE_V1,
        sha(state["request_json"].encode()),
        (request.scope.environment, request.scope.account_id),
        None,
    )
    _UNKNOWN[binding] = marker
    receipt = (
        "ctcc.native_flat_ledger_resources_retired.v1",
        actual["view"].reservation_id,
        actual["view"].original_event_key,
        actual["view"].request_sha256,
    )
    state.clear()
    state.update(
        closed=True, transport_spent=True, transport_unknown_hold=False, settled=True
    )
    _FLAT_CLEANUPS.pop(completion)
    return receipt


class _OwnedNativeFilledFlatLedgerCleanupV1(_OwnedNativeFlatLedgerCleanupV1):
    __slots__ = ()


@dataclass(frozen=True, slots=True, repr=False)
class NativeFilledFlatLedgerCleanupViewV1:
    filled_result_view: object = field(repr=False)
    closed_transports_handle: object = field(repr=False)
    reservation_id: str = field(repr=False)
    original_event_key: str = field(repr=False)
    request_sha256: str = field(repr=False)


def prepare_native_filled_flat_ledger_cleanup_v1(cap, *, key):
    """Authenticate the committed filled closure; keep both Root POST graphs."""
    from app.trade_qualification import native_postpublication_recheck_v2 as r7
    from app.trade_qualification import native_postsubmit_reconciliation_v1 as post

    _NATIVE_GUARD()
    if key is not post._FILLED_SOURCE_SETTLEMENT_KEY_V1:
        _deny()
    view = post._take_native_filled_flat_ledger_settlement_v1(
        cap, key=_FILLED_CLEANUP_KEY_V1
    )
    state = view.filled_result_view.response_data.transport_state
    request = _unknown_flat_record_v1(state)
    closure = view.filled_result_view.closure_readback
    if (
        len(closure) != 7
        or closure[1].classification != "own_entry_and_close_filled_flat"
        or view.entry_transport_binding is not state["transport_binding"]
        or closure[0] != view.filled_result_view.result.outcome_id
        or sha(closure[1].receipt_json)
        != view.closed_transports_view.closure_receipt_sha256
        or view.filled_result_view.response_data.transport_facts.request_json
        != state["request_json"]
        or view.filled_result_view.result.reservation_id
        != state["consumed"].reservation_id
        or state["r7"].get("closed", False)
    ):
        _deny("native_filled_flat_ledger_closure_changed")
    result = object.__new__(_OwnedNativeFilledFlatLedgerCleanupV1)
    actual = {
        "cap": cap,
        "state": state,
        "task": asyncio.current_task(),
        "guard": {},
        "r7_guard": {},
        "view": NativeFilledFlatLedgerCleanupViewV1(
            view.filled_result_view,
            view.closed_transports_handle,
            state["consumed"].reservation_id,
            request.origin.original_event_key,
            sha(state["request_json"].encode()),
        ),
    }
    _native_guard.save_native_guard_v1(actual["guard"], _NATIVE_GUARD)
    _native_guard.save_native_guard_v1(actual["r7_guard"], r7._NATIVE_GUARD)
    _FILLED_CLEANUPS[result] = actual
    return result


def _filled_ledger_cleanup_v1(completion, key):
    from app.trade_qualification import native_postsubmit_reconciliation_v1 as post

    _NATIVE_GUARD()
    if (
        key is not post._FILLED_SOURCE_SETTLEMENT_KEY_V1
        or type(completion) is not _OwnedNativeFilledFlatLedgerCleanupV1
    ):
        _deny()
    actual = _FILLED_CLEANUPS.get(completion)
    if type(actual) is not dict or actual["task"] is not asyncio.current_task():
        _deny()
    _native_guard.check_saved_native_guard_v1(actual["guard"])
    _native_guard.check_saved_native_guard_v1(actual["r7_guard"])
    _unknown_flat_record_v1(actual["state"])
    return actual


def replay_native_filled_flat_ledger_cleanup_v1(completion, *, key):
    from app.trade_qualification import native_postsubmit_reconciliation_v1 as post

    actual = _filled_ledger_cleanup_v1(completion, key)
    view = post._replay_native_filled_flat_ledger_settlement_v1(
        actual["cap"], key=_FILLED_CLEANUP_KEY_V1
    )
    if (
        view.filled_result_view is not actual["view"].filled_result_view
        or view.closed_transports_handle is not actual["view"].closed_transports_handle
    ):
        _deny()
    return actual["view"]


def retire_native_filled_flat_ledger_cleanup_v1(completion, *, key):
    """Release actual R7/G12 holders only after genuine Root pair finalization."""
    from app.trade_qualification import native_postpublication_recheck_v2 as r7
    from app.trade_qualification import native_postsubmit_reconciliation_v1 as post

    actual = _filled_ledger_cleanup_v1(completion, key)
    if actual.get("r7_retired") is True:
        _deny()
    post._replay_native_filled_flat_ledger_after_finalization_v1(
        actual["cap"], key=_FILLED_CLEANUP_KEY_V1
    )
    r7._release(actual["state"]["r7"])
    if actual["state"]["r7"].get("closed") is not True:
        _deny("native_filled_flat_r7_release_unconfirmed")
    actual["r7_retired"] = True
    actual["r7_receipt"] = (
        "ctcc.native_filled_flat_r7_source_chain_retired.v1",
        actual["view"].reservation_id,
        actual["view"].original_event_key,
        actual["view"].request_sha256,
    )
    return actual["r7_receipt"]


def finalize_native_filled_flat_ledger_cleanup_v1(completion, *, key):
    """Compact RAM after real Source/close retirement; the event tombstone stays."""
    from app.trade_qualification import native_postsubmit_reconciliation_v1 as post

    actual = _filled_ledger_cleanup_v1(completion, key)
    if actual.get("r7_retired") is not True:
        _deny()
    post._require_native_filled_flat_source_retired_for_ledger_v1(
        actual["cap"], key=_FILLED_CLEANUP_KEY_V1
    )
    state, view = actual["state"], actual["view"]
    request = _unknown_flat_record_v1(state)
    marker = (
        request.origin.original_event_key,
        request.issuer_sha256,
        _FINALIZED_PHASE_V1,
        sha(state["request_json"].encode()),
        (request.scope.environment, request.scope.account_id),
        None,
    )
    receipt = (
        "ctcc.native_filled_flat_ledger_resources_retired.v1",
        view.reservation_id,
        view.original_event_key,
        view.request_sha256,
    )
    _UNKNOWN[state["binding"]] = marker
    state.clear()
    state.update(
        closed=True, transport_spent=True, transport_unknown_hold=False, settled=True
    )
    _FILLED_CLEANUPS.pop(completion)
    return receipt


def replay_native_filled_flat_r7_retirement_v1(completion, *, key):
    """Exact completed own release, with authenticators retained for Source."""
    actual = _filled_ledger_cleanup_v1(completion, key)
    if (
        actual.get("r7_retired") is not True
        or actual["state"]["r7"].get("closed") is not True
    ):
        _deny()
    receipt = actual["r7_receipt"]
    if (
        type(receipt) is not tuple
        or len(receipt) != 4
        or receipt
        != (
            "ctcc.native_filled_flat_r7_source_chain_retired.v1",
            actual["view"].reservation_id,
            actual["view"].original_event_key,
            actual["view"].request_sha256,
        )
    ):
        _deny()
    return receipt


@dataclass(frozen=True, slots=True, repr=False)
class NativeIntentTransportFactsV2:
    """Private replay view. Constructing/copying DATA never creates a permit."""

    request: wire.NativeReservationRequestV4
    request_json: str
    intent: _native_control.ControlBoundSubmissionIntentRecord
    reserved: ledger.ReservationReceipt
    consumed: ledger.ReservationReceipt
    control_expectation: _native_control.ControlExpectation
    session_factory: object
    transport_binding: object
    transport_context: object
    expires_at: datetime
    expiry_monotonic_ns: int
    current_recheck_json: bytes
    exact_order_json: bytes
    exact_order_sha256: str
    account_revision: int
    ledger_revision: int
    execution_authority: bool = field(default=False, init=False)
    order_retry_authority: bool = field(default=False, init=False)


@dataclass(frozen=True, slots=True, repr=False)
class NativeRiskReservationContextV2:
    """Comparison DATA from actual owned holds, never an exclusion permit."""

    phase: str
    own_receipt: ledger.ReservationReceipt | None
    active: tuple | None
    account_revision: int
    ledger_revision: int


def _record(repository, binding):
    # Registry keys are freshly allocated builtin objects, never caller DTOs.
    # Check before dict lookup so a foreign __hash__/__eq__ cannot run here.
    if type(binding) is not object or type(_ATTEMPTS) is not dict:
        _deny()
    record = _ATTEMPTS.get(binding)
    if (
        type(record) is not dict
        or record.get("closed", False)
        or record["repository"] is not repository
        or repository.clock is not _clock
        or record["task"] is not asyncio.current_task()
        or record["task"].cancelling()
    ):
        _deny()
    _native_guard.check_saved_native_guard_v1(record)
    from app.trade_qualification import native_postpublication_recheck_v2 as r7

    issued = r7._fresh_account(record["r7"])
    if issued.session_factory is not repository.session_factory:
        _deny("native_ledger_factory_changed")
    _require_execution_settings(issued, record["request"])
    return record, issued


def _require_execution_settings(issued, request):
    """Captured client AND current configured switches/caps must both admit."""
    for settings in (issued.private_client.settings, _settings_module.get_settings()):
        if not (
            settings.trading_mode == "okx_demo"
            and settings.okx_demo_enabled is True
            and settings.okx_demo_allow_order_writes is True
            and settings.okx_demo_auto_execution is True
            and settings.okx_demo_require_protection is True
            and settings.okx_ws_enabled is True
            and settings.paper_auto_execution is False
            and settings.live_trading is False
            and settings.okx_live_enabled is False
            and settings.okx_live_allow_order_writes is False
            and settings.okx_live_auto_execution is False
            and request.risk_inputs.instrument.instrument_id
            in settings.okx_demo_allowed_symbol_list
            and request.risk_inputs.requested_leverage <= settings.okx_demo_max_leverage
            and request.risk_inputs.requested_contracts
            <= settings.okx_demo_max_order_size_contracts
        ):
            _deny("native_ledger_demo_execution_disabled_or_cap_changed")


def _release(record):
    from app.trade_qualification import native_postpublication_recheck_v2 as r7

    if record.get("transport_unknown_hold", False):
        # A synchronous close can interrupt cleanup which still needs await.
        # Keep every original source/transport binding and actual TX handle.
        return
    if record.get("transport_module") is not None:
        # The synchronous handoff is retained in _ATTEMPTS immediately. If
        # its returned awaitable is abandoned, task settlement must tombstone
        # the full invocation rather than release an unstarted coroutine.
        _transport_unknown(record)
        return
    if record.get("closed", False):
        return
    record["closed"] = True
    _ATTEMPTS.pop(record["binding"], None)
    r7._release(record["r7"])


def _request(state, evaluated):
    issued, spec, packet, _context, coverage, _risks, proof = evaluated
    published = state["published"]["facts"]
    fixed = published.original
    return wire.NativeReservationRequestV4(
        contract_version=wire.REQUEST_VERSION,
        scope=issued.scope,
        origin=wire.NativeOriginV2(
            contract_version="ctcc-native-recheck-origin-v2",
            original_candidate_json=fixed.candidate_json.decode(),
            original_candidate_sha256=fixed.candidate_sha256,
            original_event_key=fixed.original_event_key,
            original_policy_sha256=fixed.original_policy_sha256,
            evidence_sha256=published.evaluation_sha256,
            report_sha256=published.receipt.report_sha256,
            claim_sha256=published.claim.claim_sha256,
            publication_sha256=published.publication.publication_sha256,
            publication_completed_at=published.receipt.completed_at,
            deadline=state["deadline"],
        ),
        risk_inputs=wire.NativeRiskInputsV2(
            requested_contracts=fixed.requested_contracts,
            requested_leverage=fixed.requested_leverage,
            instrument=spec,
        ),
        risk_policy=issued.claims.risk_policy,
        expected_account_revision=issued.account_revision,
        expected_ledger_revision=issued.ledger_revision,
        issuer_sha256=issued.issuer_sha256,
        issued_row_sha256=issued.issued_row_sha256,
        credential_session_sha256=issued.control_expectation.pins.credential_session_sha256,
        public_packet_json=packet.packet_json.decode(),
        public_packet_sha256=packet.bundle_sha256,
        public_custody_id=str(state["public_state"]["custody_id"]),
        public_bundle_sha256=state["public_state"]["bundle_sha256"],
        r7_proof_json=proof.decode(),
        r7_proof_sha256=sha(proof),
        coverage=coverage,
    )


async def configure_transaction(repository, binding, session):
    record = _actual_session(repository, binding, session)
    remaining = int((record["r7"]["deadline"] - _clock()).total_seconds() * 1000)
    if remaining <= 0:
        _deny("native_ledger_original_deadline_expired")
    for statement in (
        "SET TRANSACTION ISOLATION LEVEL READ COMMITTED",
        "SET LOCAL search_path=pg_catalog,public,pg_temp",
        "SET LOCAL TIME ZONE 'UTC'",
        "SET LOCAL synchronous_commit='on'",
    ):
        await session.execute(text(statement))
        _record(repository, binding)
    await session.execute(
        text(
            "SELECT pg_catalog.set_config('statement_timeout',:value,true), "
            "pg_catalog.set_config('lock_timeout',:value,true)"
        ),
        {"value": str(remaining)},
    )
    _record(repository, binding)
    await _require_issued_database_head(repository, binding, session)
    _record(repository, binding)


def _actual_session(repository, binding, session):
    record, issued = _record(repository, binding)
    if (
        type(session) is not AsyncSession
        or not session.in_transaction()
        or session.get_bind() is not issued.session_factory.kw["bind"].sync_engine
    ):
        _deny("native_ledger_actual_transaction_required")
    return record


async def _require_issued_database_head(repository, binding, session):
    _, issued = _record(repository, binding)
    catalog, _ = await _native_issuer_transaction.read_actual_native_issuer_transaction(
        session
    )
    _actual_session(repository, binding, session)
    if catalog.head != issued.database_head:
        _deny("native_ledger_issued_database_changed")


async def read_control_v2(repository, binding, session, scope):
    record = _actual_session(repository, binding, session)
    if scope != record["request"].scope:
        _deny("native_ledger_original_scope_required")
    _, issued = _record(repository, binding)
    control_scope = _native_control.control.ControlScope(
        scope.environment, scope.account_id
    )
    if issued.database_head == "0056":
        state, event = await _native_issuer_transaction.read_actual_issuer_control_0056(
            session, control_scope
        )
    else:
        (
            state,
            event,
        ) = await _native_control_repository55.DemoControlRepositoryV2.read_in_transaction(
            session, control_scope
        )
    record = _actual_session(repository, binding, session)
    _native_control.guard_current_control(
        state,
        event,
        record["control_expectation"],
        scope,
        now=_clock(),
        request=record["request"],
    )
    return state, event


async def control_locked_v2(repository, binding, session, scope, *, request=None):
    record = _actual_session(repository, binding, session)
    if (
        request is not None
        and wire.canonical_request(request) != record["request_json"]
    ):
        _deny("native_ledger_original_request_required")
    state, event = await read_control_v2(repository, binding, session, scope)
    row = await repository._locked(session, scope)
    record = _actual_session(repository, binding, session)
    _native_control.guard_current_control(
        state,
        event,
        record["control_expectation"],
        scope,
        now=repository._now(row),
        request=record["request"],
    )
    return row, state, event


async def verify_control_history_v2(repository, binding, session, state, event):
    record = _actual_session(repository, binding, session)
    _native_control.guard_current_control(
        state,
        event,
        record["control_expectation"],
        record["request"].scope,
        now=_clock(),
        request=record["request"],
    )
    await (
        _native_control_repository55.DemoControlRepositoryV2.verify_historical_binding(
            session, state, event
        )
    )
    _actual_session(repository, binding, session)


async def require_intent_read_session_v2(
    repository, binding, session, *, scope, event_key, expected_sha256
):
    record = _actual_session(repository, binding, session)
    prepared = record.get("prepared_intent")
    if (
        prepared is None
        or scope != record["request"].scope
        or event_key != record["request"].origin.original_event_key
        or expected_sha256 != prepared.sha256
    ):
        _deny("native_ledger_own_intent_required")
    await _require_issued_database_head(repository, binding, session)
    _actual_session(repository, binding, session)


async def require_locked_intent_readback_v2(
    repository, binding, *, session, scope_row, request, intent, current, consumed
):
    record = _actual_session(repository, binding, session)
    if (
        wire.canonical_request(request) != record["request_json"]
        or intent != record["prepared_intent"]
        or consumed != record["consumed"]
        or current != consumed
    ):
        _deny("native_ledger_exact_own_consumed_intent_required")
    state, event = await read_control_v2(repository, binding, session, request.scope)
    claims = await require_locked(
        repository,
        binding,
        session=session,
        scope_row=scope_row,
        control_state=state,
        control_event_sha256=event,
        request=request,
    )
    all_active = await repository._active(session, request.scope, scope_row)
    _actual_session(repository, binding, session)
    active = tuple(
        item for item in all_active if item.reservation_id != consumed.reservation_id
    )
    repository._require_no_uncertain(active)
    await repository._require_resolved_submissions(session, active)
    _actual_session(repository, binding, session)
    coverage, _risk = prepare_locked(
        repository,
        binding,
        request=request,
        claims=claims,
        active=active,
        observed_at=repository._now(scope_row),
    )
    repository._coverage_caps(request, claims, active, coverage)


async def require_locked(
    repository,
    binding,
    *,
    session,
    scope_row,
    control_state,
    control_event_sha256,
    request,
):
    from app.database.repositories import qualification_ledger as repository_module
    from app.trade_qualification import native_account_risk_assembly_v1 as account

    record, issued = _record(repository, binding)
    if (
        not wire.is_native_request(request)
        or wire.canonical_request(request) != record["request_json"]
        or scope_row.account_revision != issued.account_revision
        or scope_row.ledger_revision != record["expected_ledger_revision"]
    ):
        _deny("native_ledger_original_revision_changed")
    # The genuine source wrapper checks the actual full20 DB0054 row via the
    # issuer repository's data reader. It derives own-transition allowances
    # from THIS registry; no caller supplied revision or DTO can authorize it.
    claims = await account.require_issued_account_revision_locked_v1(
        record["r7"]["account_binding"],
        session=session,
        scope_row=scope_row,
        control_state=control_state,
        control_event_sha256=control_event_sha256,
        key=repository_module._NATIVE_V2_KEY,
    )
    _record(repository, binding)
    if claims != issued.claims:
        _deny("native_ledger_issued_claims_changed")
    return claims


def issued_revision_expectation_v2(account_binding, *, key):
    """Native issuer comparison DATA, derived solely from the held real chain."""
    from app.database.repositories import qualification_ledger as repository_module

    if key is not repository_module._NATIVE_V2_KEY:
        _deny()
    matches = [
        record
        for record in _ATTEMPTS.values()
        if record["r7"]["account_binding"] is account_binding
        and not record.get("closed", False)
    ]
    if len(matches) != 1:
        _deny()
    record = matches[0]
    if (
        record["task"] is not asyncio.current_task()
        or record["task"].cancelling()
        or record["repository"].clock is not _clock
    ):
        _deny()
    return record["request"].expected_account_revision, record[
        "expected_ledger_revision"
    ]


def native_reservation_risk_context_v2(account_binding, *, key):
    if key is not _native_repository._NATIVE_V2_KEY or type(_ATTEMPTS) is not dict:
        _deny("native_ledger_fixed_risk_context_purpose_required")
    matches = [
        record
        for record in _ATTEMPTS.values()
        if record["r7"]["account_binding"] is account_binding
        and not record.get("closed", False)
    ]
    if not matches:
        return None  # No owned live DB evaluation. This never proves empty holds.
    if len(matches) != 1:
        _deny("native_ledger_exact_owned_risk_context_required")
    record = matches[0]
    _native_guard.check_saved_native_guard_v1(record)
    stamp = native_stamp()
    if (
        record["task"] is not asyncio.current_task()
        or record["task"].cancelling()
        or record["repository"].clock is not _clock
        or utc_from_ns(stamp["utc_ns"]) >= record["r7"]["deadline"]
        or stamp["monotonic_ns"] >= record["monotonic_deadline_ns"]
    ):
        _deny("native_ledger_owned_risk_context_expired_or_changed")
    own = record.get("consumed") or record.get("reserved")
    if own is not None:
        if type(own) is not ledger.ReservationReceipt:
            _deny("native_ledger_exact_own_risk_receipt_required")
        request = record["request"]
        delta = 2 if own.state == "consumed" else 1
        if (
            type(own) is not ledger.ReservationReceipt
            or own.state not in {"reserved", "consumed"}
            or own.scope != request.scope
            or own.original_event_key != request.origin.original_event_key
            or own.reservation_id
            != ledger.reservation_id(request.scope, request.origin.original_event_key)
            or own.request_sha256 != sha(record["request_json"].encode())
            or own.account_revision != request.expected_account_revision
            or own.ledger_revision != request.expected_ledger_revision + delta
            or own.state_revision != delta
        ):
            _deny("native_ledger_exact_own_risk_receipt_required")
    return NativeRiskReservationContextV2(
        record["phase"],
        own,
        record.get("evaluating_active", record.get("active")),
        record["request"].expected_account_revision,
        record["expected_ledger_revision"],
    )


def prepare_locked(repository, binding, *, request, claims, active, observed_at):
    from app.trade_qualification import native_postpublication_recheck_v2 as r7

    record, issued = _record(repository, binding)
    if claims != issued.claims or type(active) is not tuple:
        _deny("native_ledger_actual_claims_and_holds_required")
    record["evaluating_active"] = active
    try:
        actual = r7._evaluate(record["r7"], active=active, observed_at=observed_at)
    finally:
        record.pop("evaluating_active", None)
    if actual[0].issued_row_sha256 != request.issued_row_sha256:
        _deny("native_ledger_current_issuer_changed")
    coverage = actual[4]
    if any(
        getattr(coverage, name) > getattr(request.coverage, name)
        for name in ("risk_amount", "margin_amount", "notional_amount")
    ):
        _deny("native_ledger_reserved_coverage_insufficient")
    record["latest_proof"] = actual[-1]
    record["active"] = active
    return coverage, actual[5][0]


def note_reserve_mutation(repository, binding, receipt):
    record, issued = _record(repository, binding)
    if (
        record["phase"] != "reserving"
        or receipt.state != "reserved"
        or receipt.account_revision != issued.account_revision
        or receipt.ledger_revision != issued.ledger_revision + 1
        or receipt.state_revision != 1
        or receipt.request_sha256 != sha(record["request_json"].encode())
    ):
        _deny("native_ledger_own_reservation_transition_invalid")
    record["expected_ledger_revision"] = receipt.ledger_revision
    record["reserved"] = receipt


def note_consume_mutation(repository, binding, receipt):
    record, issued = _record(repository, binding)
    reserved = record.get("reserved")
    if (
        record["phase"] != "consuming"
        or reserved is None
        or receipt.state != "consumed"
        or receipt.state_revision != 2
        or receipt.reservation_id != reserved.reservation_id
        or receipt.request_sha256 != reserved.request_sha256
        or receipt.coverage != reserved.coverage
        or receipt.account_revision != issued.account_revision
        or receipt.ledger_revision != reserved.ledger_revision + 1
    ):
        _deny("native_ledger_own_consumption_transition_invalid")
    record["expected_ledger_revision"] = receipt.ledger_revision
    record["consumed"] = receipt


def execution_binding(repository, binding):
    record, _issued = _record(repository, binding)
    proof = record["latest_proof"]
    document = json.loads(proof)
    execution = document["execution_mode"]
    return wire.NativeExecutionBindingV2(
        contract_version="ctcc-native-execution-binding-v2",
        recheck_json=proof.decode(),
        recheck_sha256=sha(proof),
        position_mode=execution["position_mode"],
        position_side=execution["position_side"],
        configured_isolated_leverage=Decimal(execution["leverage"]),
        execution_selection_sha256=document["execution_selection_sha256"],
        captured_execution_mode_sha256=execution["captured_execution_mode_sha256"],
        leverage_member_sha256=execution["raw_member_sha256"],
        leverage_source_reference_sha256=execution["source_reference_sha256"],
    )


async def late_guard(
    repository,
    binding,
    *,
    session,
    scope_row,
    request,
    claims,
    control_state,
    control_event_sha256,
):
    record, _issued = _record(repository, binding)
    await require_locked(
        repository,
        binding,
        session=session,
        scope_row=scope_row,
        control_state=control_state,
        control_event_sha256=control_event_sha256,
        request=request,
    )
    coverage, _ = prepare_locked(
        repository,
        binding,
        request=request,
        claims=claims,
        active=record["active"],
        observed_at=repository._now(scope_row),
    )
    repository._coverage_caps(request, claims, record["active"], coverage)


class _Attempt(Coroutine):
    def __init__(self, record):
        self.record, self.body = record, _run(record)

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
            if not self.record.get("issued", False):
                _release(self.record)
            raise

    def throw(self, *args):
        try:
            return self.body.throw(*args)
        except BaseException:
            if not self.record.get("issued", False):
                _release(self.record)
            raise

    def close(self):
        try:
            self.body.close()
        finally:
            if not self.record.get("issued", False):
                _release(self.record)


def begin(repository, owner, *, key):
    from app.database.repositories import qualification_ledger as repository_module
    from app.trade_qualification import native_postpublication_recheck_v2 as r7

    if (
        key is not repository_module._NATIVE_V2_KEY
        or type(repository) is not repository_module.QualificationLedgerRepository
        or len(_ATTEMPTS)
        + sum(marker[2] != _FINALIZED_PHASE_V1 for marker in _UNKNOWN.values())
        >= _MAX_ATTEMPTS
    ):
        _deny()
    state = r7._take_native_r7_for_ledger_v2(owner, key=key)
    record = {
        "repository": repository,
        "r7": state,
        "task": asyncio.current_task(),
        "binding": object(),
        "phase": "prepared",
    }
    _native_guard.save_native_guard_v1(record, _NATIVE_GUARD)
    try:
        actual = r7._evaluate(state)
        original_issued = r7._check(state)[2]
        request = _request(state, actual)
        if any(
            marker[2] != _FINALIZED_PHASE_V1
            and marker[4] == (request.scope.environment, request.scope.account_id)
            for marker in _UNKNOWN.values()
        ):
            _deny("native_ledger_unknown_account_inhibits_entry")
        record.update(
            request=request,
            request_json=wire.canonical_request(request),
            expected_ledger_revision=actual[0].ledger_revision,
            monotonic_deadline_ns=min(
                actual[0].expiry_monotonic_ns, original_issued.expiry_monotonic_ns
            ),
            latest_proof=actual[-1],
            control_expectation=actual[0].control_expectation,
        )
        _ATTEMPTS[record["binding"]] = record
        _record(repository, record["binding"])
        return _Attempt(record)
    except BaseException:
        _release(record)
        raise


def begin_owned(owner):
    """Use only the real R7 factory and fixed native clock, never caller IO."""
    from app.database.repositories import qualification_ledger as repository_module
    from app.trade_qualification import native_postpublication_recheck_v2 as r7

    state = (
        r7._OWNERS.get(owner)
        if type(owner) is r7.OwnedNativePostpublicationR7V2
        else None
    )
    if state is None:
        _deny()
    issued = r7._fresh_account(state)
    repository = repository_module.QualificationLedgerRepository(
        issued.session_factory, clock=_clock
    )
    return begin(repository, owner, key=repository_module._NATIVE_V2_KEY)


def _transport_module_v2(key):
    from app.exchange.okx import native_demo_write_v2 as transport

    if key is not transport._INTENT_KEY:
        _deny("native_ledger_transport_purpose_required")
    return transport


def _transport_record_v2(state, *, key):
    module = _transport_module_v2(key)
    if (
        type(state) is not dict
        or state.get("transport_module") is not module
        or state.get("transport_key") is not key
        or state.get("transport_spent", False)
        or type(state.get("binding")) is not object
        or type(_ATTEMPTS) is not dict
        or _ATTEMPTS.get(state["binding"]) is not state
    ):
        _deny("native_ledger_transport_owner_required")
    _native_guard.check_saved_native_guard_v1(state["transport_source_guard"])
    actual, _issued = _record(state["repository"], state["binding"])
    if actual is not state:
        _deny("native_ledger_transport_owner_required")
    return state


def _transport_unknown(state):
    request = state["request"]
    _UNKNOWN[state["binding"]] = (
        request.origin.original_event_key,
        request.issuer_sha256,
        "transport_discarded_or_unknown",
        sha(state["request_json"].encode()),
        (request.scope.environment, request.scope.account_id),
        state,
    )
    state["transport_spent"] = True
    state["transport_unknown_hold"] = True
    state["closed"] = True
    _ATTEMPTS.pop(state["binding"], None)
    # The strong exact-state marker keeps the complete invocation, issued
    # sources, real session/transaction/connection and native transport binding.
    # No async rollback/socket/stop completion is inferred from body.close().


def _take_native_intent_for_transport_v2(owner, *, key):
    """Synchronous same-task MOVE. No saved intent, DTO, flag or callback enters."""
    module = _transport_module_v2(key)
    state = (
        _INTENTS.pop(owner, None) if type(owner) is OwnedNativeDurableIntentV2 else None
    )
    if state is None:
        _deny("native_ledger_transport_owner_required")
    state["finalizer"].detach()
    try:
        if state.get("transport_module") is not None:
            _deny("native_ledger_transport_already_moved")
        state["transport_module"], state["transport_key"] = module, key
        state["transport_source_guard"] = {}
        _native_guard.save_native_guard_v1(
            state["transport_source_guard"], module._NATIVE_GUARD
        )
        from app.trade_qualification import native_account_risk_assembly_v1 as account

        state["transport_binding"] = account.bind_issued_demo_transport_v2(
            state["r7"]["account_binding"], key=key
        )
        _replay_native_intent_for_transport_v2(state, key=key)
        return state
    except BaseException:
        _transport_unknown(state)
        raise


def _replay_native_intent_for_transport_v2(state, *, key):
    from app.trade_qualification import native_account_risk_assembly_v1 as account
    from app.trade_qualification import native_postpublication_recheck_v2 as r7

    record = _transport_record_v2(state, key=key)
    request = wire.checked_request(record["request"])
    if wire.canonical_request(request) != record["request_json"]:
        _deny("native_ledger_original_request_changed")
    intent, consumed = _native_control.replay_control_bound_intent(
        record["intent"].canonical_json,
        request,
        expected_sha256=record["intent"].sha256,
    )
    if (
        intent != record["prepared_intent"]
        or consumed != record["consumed"]
        or consumed.ledger_revision != request.expected_ledger_revision + 2
        or consumed.account_revision != request.expected_account_revision
    ):
        _deny("native_ledger_exact_own_consumed_intent_required")
    actual = r7._evaluate(record["r7"], active=record["active"])
    issued = actual[0]
    if any(
        getattr(actual[4], name) > getattr(consumed.coverage, name)
        for name in ("risk_amount", "margin_amount", "notional_amount")
    ):
        _deny("native_ledger_reserved_coverage_insufficient")
    original = r7.publication.candidate.account._replay_issued_account_for_g11_v1(
        record["r7"]["published"]["candidate"]["account_binding"],
        key=r7.publication.candidate._G11_KEY_V1,
    )
    context = account._replay_issued_demo_transport_v2(
        record["transport_binding"], key=key
    )
    if type(context) is not record["transport_module"]._DemoTransportContextV2:
        _deny("native_ledger_exact_transport_context_required")
    envelope = json.loads(intent.canonical_json)
    inner = json.loads(envelope["intent_json"])
    payload = json.dumps(
        inner["exact_order"],
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")
    return NativeIntentTransportFactsV2(
        request,
        record["request_json"],
        intent,
        record["reserved"],
        consumed,
        record["control_expectation"],
        issued.session_factory,
        record["transport_binding"],
        context,
        min(record["r7"]["deadline"], issued.expires_at, original.expires_at),
        min(issued.expiry_monotonic_ns, original.expiry_monotonic_ns),
        actual[-1],
        payload,
        sha(payload),
        consumed.account_revision,
        consumed.ledger_revision,
    )


async def _lock_native_intent_for_transport_v2(state, *, session, key):
    """Real same-session UID→Control55→scope lock/replay, held by the caller."""
    record = _transport_record_v2(state, key=key)
    repository, binding, request = (
        record["repository"],
        record["binding"],
        record["request"],
    )
    _actual_session(repository, binding, session)
    if record.get("transport_lock") is None:
        await configure_transaction(repository, binding, session)
    else:
        _require_native_transport_lock_v2(state, session=session, key=key)
    await _require_issued_database_head(repository, binding, session)
    _actual_session(repository, binding, session)
    row, control_state, control_event = await control_locked_v2(
        repository, binding, session, request.scope, request=request
    )
    intent = await repository._read_submission_intent_in_transaction(
        session,
        request.scope,
        request.origin.original_event_key,
        expected_sha256=record["intent"].sha256,
        control_bound=True,
        _native_binding=binding,
    )
    _actual_session(repository, binding, session)
    if intent != record["intent"]:
        _deny("native_ledger_durable_intent_changed")
    metadata = tuple(
        (
            await session.execute(
                text(
                    "SELECT pg_catalog.pg_backend_pid(),pg_catalog.pg_current_xact_id()::text,"
                    "pg_catalog.clock_timestamp()"
                )
            )
        ).one()
    )
    _actual_session(repository, binding, session)
    connection = await session.connection()
    _actual_session(repository, binding, session)
    transaction = session.sync_session.get_transaction()
    sync_connection = connection.sync_connection
    if (
        len(metadata) != 3
        or type(metadata[0]) is not int
        or metadata[0] <= 0
        or type(metadata[1]) is not str
        or not metadata[1].isascii()
        or not metadata[1].isdigit()
        or len(metadata[1]) > 20
        or type(metadata[2]) is not datetime
        or metadata[2].tzinfo is None
    ):
        _deny("native_ledger_actual_locked_transaction_metadata_required")
    if not record["consumed"].updated_at <= metadata[2] < record["r7"]["deadline"]:
        _deny("native_ledger_locked_database_clock_outside_original_window")
    previous = record.get("transport_lock")
    if previous is not None and (
        previous["session"] is not session
        or previous["transaction"] is not transaction
        or previous["metadata"][:2] != metadata[:2]
        or metadata[2] < previous["metadata"][2]
    ):
        _deny("native_ledger_transport_transaction_changed")
    record["transport_lock"] = {
        "session": session,
        "transaction": transaction,
        "connection": sync_connection,
        "connection_transaction": sync_connection.get_transaction(),
        "metadata": metadata,
        "scope_row": row,
        "control_state": control_state,
        "control_event": control_event,
    }
    return _require_native_transport_lock_v2(state, session=session, key=key)


def _require_native_transport_lock_v2(state, *, session, key):
    record = _transport_record_v2(state, key=key)
    _actual_session(record["repository"], record["binding"], session)
    held = record.get("transport_lock")
    if (
        type(held) is not dict
        or held["session"] is not session
        or session.sync_session.get_transaction() is not held["transaction"]
        or held["transaction"] is None
        or not held["transaction"].is_active
        or held["transaction"].parent is not None
        or held["transaction"].nested
        or held["connection"].closed
        or held["connection"].invalidated
        or held["connection"].get_transaction() is not held["connection_transaction"]
        or held["connection_transaction"] is None
        or not held["connection_transaction"].is_active
        or held["scope_row"].account_revision != record["consumed"].account_revision
        or held["scope_row"].ledger_revision != record["consumed"].ledger_revision
    ):
        _deny("native_ledger_transport_lock_not_retained")
    _native_control.guard_current_control(
        held["control_state"],
        held["control_event"],
        record["control_expectation"],
        record["request"].scope,
        now=_clock(),
        request=record["request"],
    )
    return _replay_native_intent_for_transport_v2(state, key=key)


def _discard_native_intent_for_transport_v2(state, *, key):
    module = _transport_module_v2(key)
    if (
        type(state) is not dict
        or state.get("transport_module") is not module
        or state.get("transport_key") is not key
        or type(state.get("binding")) is not object
    ):
        _deny("native_ledger_transport_owner_required")
    if _ATTEMPTS.get(state["binding"]) is not state:
        unknown = _UNKNOWN.get(state["binding"])
        if (
            type(unknown) is tuple
            and len(unknown) == 6
            and unknown[5] is state
            and state.get("transport_spent") is True
        ):
            return
        _deny("native_ledger_transport_owner_required")
    # Retirement must work even when a later source guard/expiry has failed.
    # Registry identity authenticates this spent hold; no positive action occurs.
    _transport_unknown(state)


async def _run(record):
    repository, binding, request = (
        record["repository"],
        record["binding"],
        record["request"],
    )
    try:
        _record(repository, binding)
        loop_started = asyncio.get_running_loop().time()
        stamp = native_stamp()
        remaining = min(
            (record["r7"]["deadline"] - utc_from_ns(stamp["utc_ns"])).total_seconds(),
            (record["monotonic_deadline_ns"] - stamp["monotonic_ns"]) / 1_000_000_000,
        )
        if remaining <= 0:
            _deny("native_ledger_original_deadline_expired")
        # One original absolute budget includes session acquisition, reserve,
        # independent readback, consume, intent readback and transaction exits.
        # Cleanup is awaited; timeout never implies rollback/close succeeded.
        async with asyncio.timeout_at(loop_started + remaining):
            record["phase"] = "reserving"
            reserved = await repository._reserve(
                request,
                control_expectation=record["control_expectation"],
                _native_binding=binding,
            )
            _record(repository, binding)
            observed = await repository.read_event_observation(
                request.scope, request.origin.original_event_key
            )
            _record(repository, binding)
            if (
                observed.matched != reserved
                or observed.request_started_at < reserved.updated_at
            ):
                _deny("native_ledger_reservation_readback_unknown")
            record["phase"] = "consuming"
            intent = await repository._transition(
                request.scope,
                request.origin.original_event_key,
                expected_revision=reserved.ledger_revision,
                target="consumed",
                record_intent=True,
                execution_binding=execution_binding(repository, binding),
                control_expectation=record["control_expectation"],
                _native_binding=binding,
            )
            _record(repository, binding)
            record["prepared_intent"] = intent
            committed = await repository._read_submission_intent(
                request.scope,
                request.origin.original_event_key,
                expected_sha256=intent.sha256,
                control_bound=True,
                _native_binding=binding,
            )
            _record(repository, binding)
            if committed != intent:
                _deny("native_ledger_intent_readback_unknown")
            record["intent"] = committed
            record["phase"] = "intent_prepared"
            result = object.__new__(OwnedNativeDurableIntentV2)
            _INTENTS[result] = record
            record["finalizer"] = finalize(result, _release, record)
            record["task"].add_done_callback(lambda task: _release(record))
            record["issued"] = True
            return result
    except BaseException as error:  # noqa: BLE001 -- A possibly committed intent must retain its tombstone.
        # Durable hold/intent might have committed. Retain a bounded tombstone;
        # never turn a readback error or cancellation into retry permission.
        _UNKNOWN[binding] = (
            request.origin.original_event_key,
            request.issuer_sha256,
            record["phase"],
            sha(record["request_json"].encode()),
            (request.scope.environment, request.scope.account_id),
            record,
        )
        if isinstance(error, asyncio.CancelledError):
            raise asyncio.CancelledError() from None
        if isinstance(error, (KeyboardInterrupt, SystemExit)):
            raise type(error)() from None
        _deny("native_ledger_commit_or_readback_unknown")
    finally:
        if not record.get("issued", False):
            _release(record)


_NATIVE_GUARD = _native_guard.install_native_guards_v1(
    _native_sys.modules[__name__],
    (
        "begin",
        "begin_owned",
        "prepare_native_flat_ledger_cleanup_v1",
        "replay_native_zero_r7_retirement_v2",
        "replay_native_flat_ledger_cleanup_v1",
        "retire_native_flat_ledger_cleanup_v1",
        "finalize_native_flat_ledger_cleanup_v1",
        "prepare_native_filled_flat_ledger_cleanup_v1",
        "replay_native_filled_flat_ledger_cleanup_v1",
        "retire_native_filled_flat_ledger_cleanup_v1",
        "finalize_native_filled_flat_ledger_cleanup_v1",
        "replay_native_filled_flat_r7_retirement_v1",
    ),
    dependencies=(
        wire,
        ledger,
        _native_repository,
        _native_control,
        _native_intent,
        _native_catalog55,
        _native_issuer_transaction,
        _native_issuer_transaction.locked,
        _native_issuer_transaction.writer56,
        _native_issuer_transaction.writer56.inherited,
        _native_catalog55.policy,
        _native_catalog55.policy.native_closure,
        _native_catalog55.policy.native_close_intent,
        _native_catalog55.policy.native_close_index,
        _native_catalog55.policy.native_closure.zero,
        _native_catalog55.policy.native_terminal,
        _native_catalog55.policy.native_terminal.filled,
        _native_catalog55.policy.native_report,
        _native_catalog55.policy.native_report.schema,
        _native_catalog55.policy.native_report.helpers,
        _native_catalog55.policy.old54,
        _native_catalog55.policy.old53,
        _native_catalog55.policy.lineage,
        _native_catalog55.policy.issued,
        _native_catalog55.policy.operator,
        _zero_event55,
        _zero_event55.schema,
        _zero_event55.live_schema,
        _zero_schema55,
        _native_control_repository55,
        _historical_control_repository,
        _settings_module,
        _private_rest,
    ),
)
