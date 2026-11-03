"""One genuine current filled-result MOVE into negative close preparation.

No historical position, DTO, Arm, entry retry or direct write is admitted.
Fresh reads and a unique committed intent are separate fixed purposes. Only
the opaque durable intent may enter the later Root negative physical boundary.
"""

import asyncio
import os
import sys
import threading
from dataclasses import dataclass, field

from app.domain.native_clock import native_stamp
from app.domain.source_primitives import canonical, decode, sha, utc_from_ns
from app.trade_qualification import native_continuation_guard_v1 as guard
from app.trade_qualification import native_postsubmit_reconciliation_v1 as post

_POSTSUBMIT_KEY_V1 = object()
_SOURCE_KEY_V1 = object()
_DB_KEY_V1 = object()
_ENTRIES = {}
_UNKNOWN = {}
_INTENTS = {}
_POST_READS = {}
_FILLED_CLEANUP_KEY_V1 = object()
_FILLED_RETIRED = {}
_MAX = 16


class NativeControlledCloseError(ValueError):
    pass


def _need(value):
    if value is not True:
        raise NativeControlledCloseError(
            "native_controlled_close_owner_unavailable"
        ) from None


class OwnedNativeCloseEntryV1:
    __slots__ = ()

    def __new__(cls):
        _need(False)

    def __copy__(self):
        _need(False)

    def __deepcopy__(self, memo):
        _need(False)

    def __reduce_ex__(self, protocol):
        _need(False)


class OwnedNativeCloseIntentV1(OwnedNativeCloseEntryV1):
    __slots__ = ()


class OwnedNativeClosePostReadV1(OwnedNativeCloseEntryV1):
    __slots__ = ()


class _RetiredNativeFilledCloseV1(OwnedNativeCloseEntryV1):
    __slots__ = ()


@dataclass(frozen=True, slots=True, repr=False)
class NativeClosePostReadForSourceV1:
    close_response_handle: object = field(repr=False)
    response_data: object = field(repr=False)
    close_intent_view: post.NativeCloseIntentForOwnerV1 = field(repr=False)
    execution_authority: bool = field(default=False, init=False)
    order_retry_authority: bool = field(default=False, init=False)


@dataclass(frozen=True, slots=True, repr=False)
class NativeCloseEntryForSourceV1:
    transport_binding: object = field(repr=False)
    response_handle: object = field(repr=False)
    filled_result_view: post.NativeFilledResultForCloseV1 = field(repr=False)
    execution_authority: bool = field(default=False, init=False)
    order_retry_authority: bool = field(default=False, init=False)


@dataclass(frozen=True, slots=True, repr=False)
class NativeCloseSourceForTransportV1:
    entry_cap: object = field(repr=False)
    filled_result_view: post.NativeFilledResultForCloseV1 = field(repr=False)
    read_source_generation: object = field(repr=False)
    scope: object = field(repr=False)
    session_factory: object = field(repr=False)
    close_intent_view: post.NativeCloseIntentForOwnerV1 = field(repr=False)
    expires_at: object = field(repr=False)
    expiry_monotonic_ns: int = field(repr=False)
    execution_authority: bool = field(default=False, init=False)
    order_retry_authority: bool = field(default=False, init=False)


@dataclass(frozen=True, slots=True, repr=False)
class NativeCloseTransportFactsV1:
    request: object = field(repr=False)  # Original entry anchor, never a new entry.
    request_json: str = field(repr=False)
    consumed: object = field(repr=False)
    reserved: object = field(repr=False)
    session_factory: object = field(repr=False)
    transport_binding: object = field(repr=False)
    transport_context: object = field(repr=False)
    exact_order_json: bytes = field(repr=False)
    exact_order_sha256: str = field(repr=False)
    close_intent_json: bytes = field(repr=False)
    close_intent_outcome_id: str = field(repr=False)
    close_intent_row_sha256: str = field(repr=False)
    expires_at: object = field(repr=False)
    expiry_monotonic_ns: int = field(repr=False)
    execution_authority: bool = field(default=False, init=False)
    order_retry_authority: bool = field(default=False, init=False)


@dataclass(frozen=True, slots=True, repr=False)
class NativeCloseSubmissionResponseDataV1:
    """Inert received DATA; Root's exact private registry supplies identity."""

    transport_facts: NativeCloseTransportFactsV1 = field(repr=False)
    observed: object = field(repr=False)
    received_journal: object = field(repr=False)
    transport_state: object = field(repr=False)
    entry_response_handle: object = field(repr=False)
    filled_result_view: post.NativeFilledResultForCloseV1 = field(repr=False)
    execution_authority: bool = field(default=False, init=False)
    order_retry_authority: bool = field(default=False, init=False)


def _entry(cap):
    _NATIVE_GUARD()
    _need(type(cap) is OwnedNativeCloseEntryV1 and cap in _ENTRIES)
    state = _ENTRIES[cap]
    _need(
        state["cap"] is cap
        and _UNKNOWN.get(cap) is state
        and state["task"] is asyncio.current_task()
        and state["loop"] is asyncio.get_running_loop()
        and state["pid"] == os.getpid()
        and state["thread"] == threading.get_ident()
    )
    guard.check_saved_native_guard_v1(state["guard"])
    actual = post._replay_native_filled_result_for_close_v1(
        state["filled"], key=_POSTSUBMIT_KEY_V1
    )
    _need(
        actual is state["filled"]
        and state["view"].filled_result_view is actual
        and state["view"].response_handle is actual.response_handle
        and state["view"].transport_binding
        is actual.response_data.transport_facts.transport_binding
    )
    return state


def take_owned_native_close_entry_v1(result):
    """Synchronous same-task current result MOVE, before any new read await."""
    _NATIVE_GUARD()
    _need(len(_ENTRIES) < _MAX)
    filled = post._take_native_filled_result_for_close_v1(
        result, key=_POSTSUBMIT_KEY_V1
    )
    _need(type(filled) is post.NativeFilledResultForCloseV1)
    cap = object.__new__(OwnedNativeCloseEntryV1)
    state = {
        "cap": cap,
        "task": asyncio.current_task(),
        "loop": asyncio.get_running_loop(),
        "pid": os.getpid(),
        "thread": threading.get_ident(),
        "filled": filled,
        "guard": {},
        "view": NativeCloseEntryForSourceV1(
            filled.response_data.transport_facts.transport_binding,
            filled.response_handle,
            filled,
        ),
        "post_taken": False,
        "source_taken": False,
    }
    guard.save_native_guard_v1(state["guard"], _NATIVE_GUARD)
    # Full held entry/response/intent/source graph is strong immediately. An
    # abandoned cap never releases the risk hold or permits a second close.
    _ENTRIES[cap] = state
    _UNKNOWN[cap] = state
    _entry(cap)
    return cap


def _take_native_close_entry_for_source_v1(cap, *, key):
    from app.trade_qualification import native_controlled_account_source_v6 as source

    _need(key is source._CLOSE_READ_KEY_V1)
    state = _entry(cap)
    _need(state["post_taken"] is True and state["source_taken"] is False)
    state["source_taken"] = True
    return state["view"]


def _take_native_close_entry_for_post_v1(cap, *, key):
    _need(key is post._SOURCE_KEY_V1)
    state = _entry(cap)
    _need(state["post_taken"] is False and state["source_taken"] is False)
    state["post_taken"] = True
    return state["view"]


def _replay_native_close_entry_for_post_v1(cap, *, key):
    _need(key is post._SOURCE_KEY_V1)
    state = _entry(cap)
    _need(state["post_taken"] is True)
    return state["view"]


def _replay_native_close_entry_for_stop_v1(cap, *, key):
    _need(key is post._SOURCE_KEY_V1)
    # The already genuine filled/Root/transport identity suffices for scoped
    # inhibition even if a NEW read generation was never successfully minted.
    return _entry(cap)["view"]


def _replay_native_close_entry_for_source_v1(cap, *, key):
    from app.trade_qualification import native_controlled_account_source_v6 as source

    _need(key is source._CLOSE_READ_KEY_V1)
    state = _entry(cap)
    _need(state["source_taken"] is True)
    return state["view"]


def _finish_owned_native_close_intent_v1(cap, binding, *, key):
    _need(key is post._SOURCE_KEY_V1)
    entry = _entry(cap)
    _need(
        entry["post_taken"] is True
        and entry["source_taken"] is True
        and entry.get("intent_owner") is None
    )
    view = post._take_native_close_intent_for_owner_v1(binding, key=_SOURCE_KEY_V1)
    _need(
        type(view) is post.NativeCloseIntentForOwnerV1
        and view.entry_cap is cap
        and view.filled_result_view is entry["filled"]
    )
    owner = object.__new__(OwnedNativeCloseIntentV1)
    actual = {
        "owner": owner,
        "entry_cap": cap,
        "view": view,
        "guard": {},
        "task": asyncio.current_task(),
        "loop": asyncio.get_running_loop(),
        "pid": os.getpid(),
        "thread": threading.get_ident(),
        "root_taken": False,
        "transport_binding": None,
        "phase": "durable_intent",
    }
    guard.save_native_guard_v1(actual["guard"], _NATIVE_GUARD)
    _INTENTS[owner] = actual
    entry["intent_owner"] = owner
    _intent(actual)
    return owner


def _intent(state, *, deadline=True):
    _NATIVE_GUARD()
    _need(
        type(state) is dict
        and type(state.get("owner")) is OwnedNativeCloseIntentV1
        and _INTENTS.get(state["owner"]) is state
        and state["task"] is asyncio.current_task()
        and state["loop"] is asyncio.get_running_loop()
        and state["pid"] == os.getpid()
        and state["thread"] == threading.get_ident()
    )
    guard.check_saved_native_guard_v1(state["guard"])
    if deadline:
        _need(state["phase"] in {"durable_intent", "root_moved"})
        view = post._replay_native_close_intent_for_owner_v1(
            state["view"], key=_SOURCE_KEY_V1
        )
    else:
        view = post._replay_retained_native_close_intent_v1(
            state["view"], key=_SOURCE_KEY_V1
        )
    _need(view is state["view"] and view.entry_cap is state["entry_cap"])
    row_raw, intent_raw, outcome_id, metadata, budget = view.intent_readback
    row, document = decode(row_raw), decode(intent_raw)
    _need(
        row["outcome_id"] == outcome_id
        and row["capture_json"].encode() == intent_raw
        and row["capture_sha256"] == sha(intent_raw)
        and len(metadata) == 10
        and document["version"] == "ctcc.native_controlled_close_intent.v1"
        and document["reservation_id"] == view.target.reservation_id
        and document["negative_budget"] == budget
        and budget["entry_lease_renewed"] is False
        and budget["maximum_seconds"] == 10
        and document["close_request_sha256"]
        == sha(document["exact_close_order_json"].encode())
        and canonical(decode(document["exact_close_order_json"].encode())).decode()
        == document["exact_close_order_json"]
    )
    if deadline:
        stamp = native_stamp()
        _need(
            budget["started_utc_ns"] <= stamp["utc_ns"] < budget["expires_utc_ns"]
            and budget["started_monotonic_ns"]
            <= stamp["monotonic_ns"]
            < budget["expires_monotonic_ns"]
        )
    return view, document, budget


def _root_key(key):
    from app.exchange.okx import native_demo_write_v2 as write

    _need(type(key) is object and vars(write).get("_CLOSE_INTENT_KEY_V1") is key)
    return write


def _take_owned_native_close_intent_for_root_v1(owner, *, key):
    _root_key(key)
    _need(type(owner) is OwnedNativeCloseIntentV1 and owner in _INTENTS)
    state = _INTENTS[owner]
    _intent(state)
    _need(state["root_taken"] is False)
    state["root_taken"] = True
    state["root_close_spent"] = True
    state["phase"] = "root_moved"
    return state


def _peek_owned_native_close_intent_for_root_v1(owner, *, key):
    _root_key(key)
    _need(type(owner) is OwnedNativeCloseIntentV1 and owner in _INTENTS)
    state = _INTENTS[owner]
    _need(state["root_taken"] is False)
    return _intent(state)[0]


def _transport_source(state, *, key):
    from app.trade_qualification import native_account_risk_assembly_v1 as account

    _root_key(key)
    actual = account._require_native_close_transport_source_v1(key=key)
    _need(type(actual) is tuple and len(actual) == 3)
    source, kernel, functions = actual
    _need(
        sys.modules.get("app.trade_qualification.native_controlled_account_source_v6")
        is source
        and vars(source).get("_REQUIRE_SOURCE_KERNEL_V6") is kernel
        and tuple(name for name, _ in functions)
        == (
            "bind_native_close_transport_v1",
            "_replay_native_close_transport_v1",
            "_inhibit_native_close_transport_v1",
        )
        and all(vars(source).get(name) is function for name, function in functions)
    )
    prior = state.get("transport_source_capture")
    if prior is None:
        state["transport_source_capture"] = actual
    else:
        _need(
            prior[0] is source
            and prior[1] is kernel
            and all(
                left[0] == right[0] and left[1] is right[1]
                for left, right in zip(prior[2], functions, strict=True)
            )
        )
    kernel()
    return source


def _bind_owned_native_close_transport_v1(state, *, key):
    _root_key(key)
    _intent(state)
    _need(state["root_taken"] is True and state["transport_binding"] is None)
    source = _transport_source(state, key=key)
    state["transport_binding"] = source.bind_native_close_transport_v1(state, key=key)
    return _replay_native_close_transport_facts_v1(state, key=key)


def _replay_native_close_transport_facts_v1(state, *, key):
    from app.exchange.okx import native_demo_write_v2 as write

    _root_key(key)
    view, document, budget = _intent(state)
    _need(state["root_taken"] is True and state["transport_binding"] is not None)
    source = _transport_source(state, key=key)
    context = source._replay_native_close_transport_v1(
        state["transport_binding"], key=key
    )
    _need(
        type(context) is write._DemoCloseTransportContextV1
        and context.scope == view.target.scope
        and context.session_factory is view.target.session_factory
        and context.read_source_generation is view.source_generation
        and context.original_entry_transport_binding
        is view.filled_result_view.response_data.transport_facts.transport_binding
        and context.negative_expires_at <= utc_from_ns(budget["expires_utc_ns"])
        and context.monotonic_deadline_ns <= budget["expires_monotonic_ns"]
    )
    prior = state.get("transport_context")
    if prior is not None:
        _need(context == prior)
    state["transport_context"] = context
    raw = document["exact_close_order_json"].encode()
    return NativeCloseTransportFactsV1(
        view.target.request,
        view.target.request_json,
        view.target.consumed,
        view.target.reserved,
        context.session_factory,
        state["transport_binding"],
        context,
        raw,
        sha(raw),
        view.intent_readback[1],
        view.intent_readback[2],
        sha(view.intent_readback[0]),
        context.negative_expires_at,
        context.monotonic_deadline_ns,
    )


async def _lock_owned_native_close_transport_v1(state, *, session, key):
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.database.repositories import native_postsubmit_v1 as repository
    from app.database.repositories import qualification_ledger as ledger
    from app.database.repositories.demo_control_current55_v2 import (
        DemoControlRepositoryV2,
    )
    from app.trade_qualification import demo_control as control

    facts = _replay_native_close_transport_facts_v1(state, key=key)
    factory = facts.session_factory
    _need(
        type(session) is AsyncSession
        and session.in_transaction()
        and session.get_bind() is factory.kw["bind"].sync_engine
    )
    transaction = session.sync_session.get_transaction()
    for sql in (
        "SET LOCAL search_path=pg_catalog,public,pg_temp",
        "SET LOCAL TIME ZONE 'UTC'",
        "SET LOCAL synchronous_commit=on",
        "SET LOCAL statement_timeout='1500ms'",
        "SET LOCAL lock_timeout='1500ms'",
    ):
        await session.execute(text(sql))
        _replay_native_close_transport_facts_v1(state, key=key)
    _need(
        await session.scalar(text("SELECT current_setting('transaction_isolation')"))
        == "read committed"
    )
    _replay_native_close_transport_facts_v1(state, key=key)
    await ledger._require_event_journal_write_schema(session, facts.request.scope)
    _replay_native_close_transport_facts_v1(state, key=key)
    current, event = await DemoControlRepositoryV2.read_in_transaction(
        session, control.ControlScope("demo", facts.request.scope.account_id)
    )
    _replay_native_close_transport_facts_v1(state, key=key)
    repo = ledger.QualificationLedgerRepository(factory, clock=repository._now)
    scope_row = await repo._locked(session, facts.request.scope)
    _replay_native_close_transport_facts_v1(state, key=key)
    proof = await repository.read_native_close_intent_locked_v1(
        state["view"],
        session=session,
        scope_row=scope_row,
        control_state=current,
        control_event_sha256=event,
        key=_DB_KEY_V1,
    )
    _replay_native_close_transport_facts_v1(state, key=key)
    # The AsyncSession transaction proxy alone cannot prove physical locks.
    # Keep the actual Connection and its actual root transaction across TLS.
    connection = await session.connection()
    _replay_native_close_transport_facts_v1(state, key=key)
    metadata = tuple((await session.execute(repository._META)).one())
    _replay_native_close_transport_facts_v1(state, key=key)
    _need(
        session.sync_session.get_transaction() is transaction
        and len(metadata) == 10
        and metadata[:9] == proof["metadata"][:9]
        and proof["metadata"][9] <= metadata[9] < facts.expires_at
    )
    previous = state.get("transport_lock")
    if previous is not None:
        _need(
            previous["session"] is session
            and previous["transaction"] is transaction
            and previous["connection"] is connection.sync_connection
            and previous["connection_transaction"]
            is connection.sync_connection.get_transaction()
            and previous["metadata"][:9] == metadata[:9]
            and previous["metadata"][9] <= metadata[9]
        )
    state["transport_lock"] = {
        "session": session,
        "transaction": transaction,
        "connection": connection.sync_connection,
        "connection_transaction": connection.sync_connection.get_transaction(),
        "metadata": metadata,
        "scope_row": scope_row,
        "control_state": current,
        "control_event": event,
        "proof": proof,
        "source_view": state["view"],
    }
    return _require_owned_native_close_transport_lock_v1(
        state, session=session, key=key
    )


def _require_actual_close_connection_v1(held, session, engine):
    """Physical transaction DATA check; never Source or close eligibility."""
    from sqlalchemy.engine import Connection, RootTransaction
    from sqlalchemy.ext.asyncio import AsyncSession

    _need(
        type(held) is dict
        and type(session) is AsyncSession
        and session.in_transaction()
        and not session.in_nested_transaction()
        and session.get_bind() is engine
        and session.sync_session.get_transaction() is held["transaction"]
        and held["transaction"] is not None
        and held["transaction"].is_active
        and held["transaction"].parent is None
        and held["transaction"].nested is False
        and type(held["connection"]) is Connection
        and held["connection"].engine is engine
        and held["connection"].closed is False
        and held["connection"].invalidated is False
        and held["connection"].in_nested_transaction() is False
        and held["connection"].get_transaction() is held["connection_transaction"]
        and type(held["connection_transaction"]) is RootTransaction
        and held["connection_transaction"].is_active is True
    )


def _require_owned_native_close_transport_lock_v1(state, *, session, key):
    facts = _replay_native_close_transport_facts_v1(state, key=key)
    held = state.get("transport_lock")
    _need(
        type(held) is dict
        and held["session"] is session
        and held["source_view"] is state["view"]
    )
    _require_actual_close_connection_v1(
        held, session, facts.session_factory.kw["bind"].sync_engine
    )
    _need(
        held["metadata"][:9] == held["proof"]["metadata"][:9]
        and held["metadata"][9] >= held["proof"]["metadata"][9]
        and held["scope_row"].account_revision
        == facts.request.expected_account_revision
        and held["scope_row"].ledger_revision == held["proof"]["ledger_revision"]
        and held["scope_row"].claims_sha256 == held["proof"]["claims_sha256"]
    )
    from app.trade_qualification import demo_control as control

    _need(
        control.document(held["control_state"]) == held["proof"]["control_state_json"]
        and held["control_event"] == held["proof"]["control_event_sha256"]
        and held["proof"]["close_row_sha256"] == sha(state["view"].intent_readback[0])
    )
    return facts


def _inhibit_owned_native_close_transport_v1(state, *, key):
    _root_key(key)
    _intent(state, deadline=False)
    if state["transport_binding"] is None:
        return post._stop_close_original_entry_v1(state["entry_cap"])
    return _transport_source(state, key=key)._inhibit_native_close_transport_v1(
        state["transport_binding"], key=key
    )


def _replay_owned_native_close_intent_for_root_v1(state, *, key):
    _root_key(key)
    _need(
        type(state) is dict
        and state.get("root_taken") is True
        and state.get("phase") == "root_moved"
    )
    return _intent(state)


def _replay_retained_native_close_intent_for_root_v1(state, *, key):
    _root_key(key)
    _need(
        type(state) is dict
        and state.get("root_taken") is True
        and state.get("root_close_spent") is True
        and state.get("phase") == "unknown"
    )
    # This replay authenticates the spent graph after the transaction/lease.
    # It cannot return transport permission or renew either deadline.
    return _intent(state, deadline=False)[0]


def _replay_native_close_source_for_transport_v1(state, *, key):
    from app.trade_qualification import native_controlled_account_source_v6 as source

    _need(
        type(key) is object
        and vars(source).get("_CLOSE_TRANSPORT_KEY_V1") is key
        and type(state) is dict
        and state.get("root_taken") is True
    )
    view, _, budget = _intent(state)
    return NativeCloseSourceForTransportV1(
        view.entry_cap,
        view.filled_result_view,
        view.source_generation,
        view.source_context.scope,
        view.source_context.session_factory,
        view,
        utc_from_ns(budget["expires_utc_ns"]),
        budget["expires_monotonic_ns"],
    )


def _discard_owned_native_close_intent_for_root_v1(state, *, key):
    _root_key(key)
    _need(
        type(state) is dict
        and type(state.get("owner")) is OwnedNativeCloseIntentV1
        and _INTENTS.get(state["owner"]) is state
        and state.get("root_taken") is True
    )
    # Unknown retirement remains valid after expiry. Never close/delete/retry
    # actual SDK/DB/socket owners from a synchronous finalizer.
    state["phase"] = "unknown"
    post._retire_native_close_intent_read_v1(state["view"], key=_SOURCE_KEY_V1)


def _post_read_v1(cap):
    from app.exchange.okx import native_demo_write_v2 as write

    _NATIVE_GUARD()
    _need(type(cap) is OwnedNativeClosePostReadV1 and cap in _POST_READS)
    state = _POST_READS[cap]
    _need(
        state["cap"] is cap
        and state["pid"] == os.getpid()
        and state["thread"] == threading.get_ident()
        and state["task"] is asyncio.current_task()
        and state["loop"] is asyncio.get_running_loop()
    )
    guard.check_saved_native_guard_v1(state["guard"])
    actual = write._replay_native_close_response_for_reconciliation_v1(
        state["view"].close_response_handle, key=_POSTSUBMIT_KEY_V1
    )
    _need(
        actual is state["view"].response_data
        and type(actual) is NativeCloseSubmissionResponseDataV1
        and actual.transport_state is state["intent_state"]
        and _intent(state["intent_state"], deadline=False)[0]
        is state["view"].close_intent_view
        and actual.filled_result_view
        is state["view"].close_intent_view.filled_result_view
    )
    return state


def take_owned_native_close_post_read_v1(observed):
    """One-use received-response identity MOVE; no old read/write renewal."""
    from app.exchange.okx import native_demo_write_v2 as write

    _NATIVE_GUARD()
    _need(len(_POST_READS) < _MAX)
    handle = write._take_native_close_response_for_reconciliation_v1(
        observed, key=_POSTSUBMIT_KEY_V1
    )
    return _mint_native_close_post_read_v1(handle)


def take_owned_native_close_attempt_post_read_v1(intent_owner):
    """Known or partial response DATA, fetched by the genuine spent owner."""
    from app.exchange.okx import native_demo_write_v2 as write

    _NATIVE_GUARD()
    _need(len(_POST_READS) < _MAX)
    handle = write._take_native_close_attempt_for_reconciliation_v1(
        intent_owner, key=_POSTSUBMIT_KEY_V1
    )
    return _mint_native_close_post_read_v1(handle)


def _mint_native_close_post_read_v1(handle):
    from app.exchange.okx import native_demo_write_v2 as write

    data = write._replay_native_close_response_for_reconciliation_v1(
        handle, key=_POSTSUBMIT_KEY_V1
    )
    _need(type(data) is NativeCloseSubmissionResponseDataV1)
    intent = _intent(data.transport_state, deadline=False)[0]
    cap = object.__new__(OwnedNativeClosePostReadV1)
    state = {
        "cap": cap,
        "view": NativeClosePostReadForSourceV1(handle, data, intent),
        "intent_state": data.transport_state,
        "post_taken": False,
        "source_taken": False,
        "pid": os.getpid(),
        "thread": threading.get_ident(),
        "task": asyncio.current_task(),
        "loop": asyncio.get_running_loop(),
        "guard": {},
    }
    guard.save_native_guard_v1(state["guard"], _NATIVE_GUARD)
    _POST_READS[cap] = state
    _post_read_v1(cap)
    return cap


def _take_native_close_post_read_for_post_v1(cap, *, key):
    _need(key is post._SOURCE_KEY_V1)
    state = _post_read_v1(cap)
    _need(state["post_taken"] is False and state["source_taken"] is False)
    state["post_taken"] = True
    return state["view"]


def _replay_native_close_post_read_for_post_v1(cap, *, key):
    _need(key is post._SOURCE_KEY_V1)
    state = _post_read_v1(cap)
    _need(state["post_taken"] is True)
    return state["view"]


def _take_native_close_post_read_for_source_v1(cap, *, key):
    from app.trade_qualification import native_controlled_account_source_v6 as source

    _need(key is source._CLOSE_POST_READ_KEY_V1)
    state = _post_read_v1(cap)
    _need(state["post_taken"] is True and state["source_taken"] is False)
    state["source_taken"] = True
    return state["view"]


def _replay_native_close_post_read_for_source_v1(cap, *, key):
    from app.trade_qualification import native_controlled_account_source_v6 as source

    _need(key is source._CLOSE_POST_READ_KEY_V1)
    state = _post_read_v1(cap)
    _need(state["source_taken"] is True)
    return state["view"]


def _inhibit_native_close_post_read_before_source_v1(cap, *, key):
    from app.exchange.okx import native_demo_write_v2 as write

    _need(key is post._SOURCE_KEY_V1)
    state = _post_read_v1(cap)
    return _inhibit_owned_native_close_transport_v1(
        state["intent_state"], key=write._CLOSE_INTENT_KEY_V1
    )


def retire_native_filled_close_resources_v1(cap, *, key):
    """Retire only actual completed close registries; Root keeps its state."""
    _NATIVE_GUARD()
    _need(key is post._FILLED_SOURCE_SETTLEMENT_KEY_V1)
    _need(len(_FILLED_RETIRED) < _MAX)
    view = post._replay_native_filled_flat_for_close_cleanup_v1(
        cap, key=_FILLED_CLEANUP_KEY_V1
    )
    flat = view.filled_result_view
    state = flat.close_response_data.transport_state
    # The Root DATA holds the original filled-result view, while its retained
    # native intent state holds the exact full close-intent view.
    _need(
        type(state) is dict
        and type(state.get("owner")) is OwnedNativeCloseIntentV1
        and _INTENTS.get(state["owner"]) is state
        and state.get("root_close_spent") is True
        and state.get("phase") == "unknown"
        and state["view"].filled_result_view
        is flat.close_response_data.filled_result_view
        and state["view"].filled_result_view.response_handle is flat.response_handle
        and state["task"] is asyncio.current_task()
        and state["loop"] is asyncio.get_running_loop()
        and state["pid"] == os.getpid()
        and state["thread"] == threading.get_ident()
    )
    guard.check_saved_native_guard_v1(state["guard"])
    entry_cap = state["entry_cap"]
    entry = _ENTRIES.get(entry_cap)
    reads = [
        (token, actual)
        for token, actual in _POST_READS.items()
        if actual.get("intent_state") is state
    ]
    _need(
        type(entry) is dict
        and entry["cap"] is entry_cap
        and _UNKNOWN.get(entry_cap) is entry
        and entry.get("intent_owner") is state["owner"]
        and len(reads) == 1
    )
    read_cap, reading = reads[0]
    _need(
        reading["view"].close_response_handle is flat.close_response_handle
        and reading["view"].response_data is flat.close_response_data
        and reading["view"].close_intent_view is state["view"]
        and reading["post_taken"] is True
        and reading["source_taken"] is True
    )
    guard.check_saved_native_guard_v1(entry["guard"])
    guard.check_saved_native_guard_v1(reading["guard"])
    document = decode(state["view"].intent_readback[1])
    receipt = canonical(
        {
            "version": "ctcc.native_filled_close_resources_retired.v1",
            "reservation_id": flat.result.reservation_id,
            "closure_outcome_id": flat.closure_outcome_id,
            "closure_receipt_sha256": flat.closure_receipt_sha256,
            "close_intent_outcome_id": state["view"].intent_readback[2],
            "close_request_sha256": document["close_request_sha256"],
            "execution_authority": False,
            "order_retry_authority": False,
        }
    )
    # All identities and complete prior cleanup are checked before any pop.
    # Do not clear actual intent state: Root's retained attempt authenticates
    # that exact object until its final pair retirement consumes our proof.
    result = object.__new__(_RetiredNativeFilledCloseV1)
    _FILLED_RETIRED[result] = (cap, state, receipt)
    _POST_READS.pop(read_cap)
    _INTENTS.pop(state["owner"])
    _UNKNOWN.pop(entry_cap)
    _ENTRIES.pop(entry_cap)
    return result


def replay_native_filled_close_retirement_v1(retirement, *, key):
    _NATIVE_GUARD()
    _need(
        key is post._FILLED_SOURCE_SETTLEMENT_KEY_V1
        and type(retirement) is _RetiredNativeFilledCloseV1
        and retirement in _FILLED_RETIRED
    )
    _, state, receipt = _FILLED_RETIRED[retirement]
    _need(
        state["task"] is asyncio.current_task()
        and state["loop"] is asyncio.get_running_loop()
        and state["pid"] == os.getpid()
        and state["thread"] == threading.get_ident()
        and state["owner"] not in _INTENTS
        and state["entry_cap"] not in _ENTRIES
        and state["entry_cap"] not in _UNKNOWN
        and not any(
            actual.get("intent_state") is state for actual in _POST_READS.values()
        )
    )
    guard.check_saved_native_guard_v1(state["guard"])
    return receipt


def compact_native_filled_close_retirement_v1(retirement, completion, *, key):
    _NATIVE_GUARD()
    _need(key is post._FILLED_SOURCE_SETTLEMENT_KEY_V1)
    receipt = replay_native_filled_close_retirement_v1(retirement, key=key)
    view = post._replay_native_filled_cleanup_for_close_compaction_v1(
        completion, key=_FILLED_CLEANUP_KEY_V1
    )
    _need(
        type(view) is post.NativeFilledCleanupCommittedForCloseV1
        and view.close_retirement is retirement
        and type(view.cleanup_outcome_id) is str
        and len(view.cleanup_outcome_id) == 64
        and all(char in "0123456789abcdef" for char in view.cleanup_outcome_id)
        and type(view.cleanup_row_sha256) is str
        and len(view.cleanup_row_sha256) == 64
        and all(char in "0123456789abcdef" for char in view.cleanup_row_sha256)
    )
    result = (
        "ctcc.native_filled_close_data_compacted.v1",
        sha(receipt),
        view.cleanup_outcome_id,
        view.cleanup_row_sha256,
    )
    _FILLED_RETIRED.pop(retirement)
    return result


from app.exchange.okx import native_demo_write_v2 as _write

_NATIVE_GUARD = guard.install_native_guards_v1(
    sys.modules[__name__],
    (
        "take_owned_native_close_entry_v1",
        "retire_native_filled_close_resources_v1",
        "replay_native_filled_close_retirement_v1",
        "compact_native_filled_close_retirement_v1",
        "_finish_owned_native_close_intent_v1",
        "_take_owned_native_close_intent_for_root_v1",
        "_peek_owned_native_close_intent_for_root_v1",
        "_bind_owned_native_close_transport_v1",
        "_replay_native_close_transport_facts_v1",
        "_lock_owned_native_close_transport_v1",
        "_require_owned_native_close_transport_lock_v1",
        "_inhibit_owned_native_close_transport_v1",
        "_replay_owned_native_close_intent_for_root_v1",
        "_discard_owned_native_close_intent_for_root_v1",
        "_replay_retained_native_close_intent_for_root_v1",
        "_replay_native_close_source_for_transport_v1",
        "take_owned_native_close_post_read_v1",
        "take_owned_native_close_attempt_post_read_v1",
        "_take_native_close_post_read_for_post_v1",
        "_replay_native_close_post_read_for_post_v1",
        "_take_native_close_post_read_for_source_v1",
        "_replay_native_close_post_read_for_source_v1",
        "_take_native_close_entry_for_post_v1",
        "_replay_native_close_entry_for_post_v1",
        "_replay_native_close_entry_for_stop_v1",
        "_take_native_close_entry_for_source_v1",
        "_replay_native_close_entry_for_source_v1",
    ),
    dependencies=(
        post,
        post.facts,
        post.facts.wire,
        post._close_contracts,
        post._close_intent55,
        post._catalog55,
        post._catalog55.policy,
        post._catalog55.policy.native_close_intent,
        post._catalog55.policy.native_close_index,
        post._catalog55.policy.native_closure,
        post._catalog55.policy.native_closure.zero,
        post._catalog55.policy.native_terminal,
        post._catalog55.policy.native_report,
        post._catalog55.policy.native_terminal.filled,
        post._catalog55.policy.native_report.schema,
        post._catalog55.policy.native_report.helpers,
        post._event55,
        post._event55.schema,
        post._event55.live_schema,
        _write,
        _write._native_settlement,
        _write._native_filled_settlement,
        _write._native_vendors,
    ),
    bindings=_write._VENDOR_ADMISSION_V2.bindings,
)
