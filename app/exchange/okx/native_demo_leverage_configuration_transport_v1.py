"""Fixed configuration purpose in the original Root Demo physical engine.

SOURCE DRAFT: no installed issuer, schema seal, execution or acceptance claim.
No DATA constructor/flag/callback grants a gate, response or retirement handle.
"""

from __future__ import annotations

import asyncio
import math
import os
import re
import sys
import threading
from collections.abc import Coroutine
from datetime import UTC, datetime
from types import FunctionType, ModuleType

from sqlalchemy.engine import Connection, RootTransaction
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.native_clock import native_stamp
from app.domain.source_primitives import canonical, decode, sha, utc_from_ns
from app.trade_qualification import native_continuation_guard_v1 as guard
from app.trade_qualification import (
    native_leverage_configuration_contracts_v1 as contracts,
)

PATH = "/api/v5/account/set-leverage"
MAX_BODY = 65536
PURPOSE = "flat_leverage_configuration_v1"
_ROOT = {}
_API = {}
_ATTEMPTS = {}
_STATES = {}
_RESPONSES = {}
_OBSERVED = {}
_CLOSED = {}
_FINALIZED = {}
_RETIRED_DATA_MARKER_V1 = object()
_STATE_KEYS = frozenset(
    {
        "schema_version",
        "binding",
        "owner",
        "view",
        "root_phase",
        "root_record",
        "context",
        "credential_document",
        "response_handle",
        "observed",
        "received_buffers",
        "prepared_signature",
        "retired_root_receipt",
        "cleanup_phase",
    }
)
_FUNCTIONS = (
    "_take_native_leverage_configuration_for_transport_v1",
    "_replay_native_leverage_configuration_transport_v1",
    "_lock_native_leverage_configuration_v1",
    "_check_native_leverage_configuration_physical_v1",
    "_retain_native_leverage_configuration_transport_buffers_v1",
    "_inhibit_native_leverage_configuration_v1",
    "_native_leverage_configuration_transport_state_v1",
    "_take_native_leverage_configuration_cleanup_for_root_v1",
    "_replay_native_leverage_configuration_retirement_for_root_v1",
)
_KEYS = ("_ROOT_DATA_KEY_V1", "_ROOT_RESPONSE_KEY_V1", "_ROOT_SETTLEMENT_KEY_V1")


def _need(condition):
    if condition is not True:
        raise ValueError(
            "native_leverage_configuration_transport_unavailable"
        ) from None


class OwnedNativeClosedLeverageConfigurationV1:
    __slots__ = ()

    def __new__(cls):
        _need(False)

    def __copy__(self):
        _need(False)

    def __deepcopy__(self, memo):
        _need(False)

    def __reduce_ex__(self, protocol):
        _need(False)


class _ResponseHandleV1(OwnedNativeClosedLeverageConfigurationV1):
    __slots__ = ()


def _root():
    from app.exchange.okx import native_demo_write_v2 as root

    _need(
        type(root) is ModuleType
        and sys.modules.get("app.exchange.okx.native_demo_write_v2") is root
    )
    saved = _ROOT.get("original")
    if saved is None:
        original = vars(root).get("_NATIVE_GUARD")
        _need(type(original) is FunctionType)
        pin = guard._function_pin(original, {})
        guard._check_function(pin)
        original()
        saved = root, original, pin
        _ROOT["original"] = saved
    _need(saved[0] is root and vars(root).get("_NATIVE_GUARD") is saved[1])
    guard._check_function(saved[2])
    saved[1]()
    return root


def _api():
    root = _root()
    value = _API.get("original")
    if value is None:
        from app.trade_qualification import native_leverage_configuration_v1 as actor

        _need(
            type(actor) is ModuleType
            and sys.modules.get(
                "app.trade_qualification.native_leverage_configuration_v1"
            )
            is actor
        )
        checker = vars(actor).get("_NATIVE_GUARD")
        _need(type(checker) is FunctionType)
        checker_pin = guard._function_pin(checker, {})
        guard._check_function(checker_pin)
        checker()  # Complete genuine actor namespace, before optional attributes.
        owned = vars(actor).get("OwnedNativeLeverageConfigurationIntentV1")
        _need(type(owned) is type)
        keys = tuple(vars(actor).get(name) for name in _KEYS)
        _need(
            all(type(key) is object for key in keys)
            and len({id(key) for key in keys}) == 3
        )
        functions = []
        for name in _FUNCTIONS:
            function = vars(actor).get(name)
            _need(type(function) is FunctionType)
            pin = guard._function_pin(function, {})
            guard._check_function(pin)
            functions.append((name, function, pin))
        saved = {}
        guard.save_native_guard_v1(saved, checker)
        value = actor, owned, keys, tuple(functions), saved, checker, checker_pin
        _API["original"] = value
    # Check saved guard/code FIRST; a Module.__getattr__ never supplies an API.
    guard.check_saved_native_guard_v1(value[4])
    guard._check_function(value[6])
    _need(
        sys.modules.get("app.trade_qualification.native_leverage_configuration_v1")
        is value[0]
        and vars(value[0]).get("_NATIVE_GUARD") is value[5]
        and vars(value[0]).get("OwnedNativeLeverageConfigurationIntentV1") is value[1]
    )
    _need(
        all(
            vars(value[0]).get(name) is key
            for name, key in zip(_KEYS, value[2], strict=True)
        )
    )
    for name, function, pin in value[3]:
        guard._check_function(pin)
        _need(vars(value[0]).get(name) is function)
    _need(type(vars(root).get("_LEVERAGE_CONFIGURATION_KEY_V1")) is object)
    return value


def _function(name):
    for expected, function, _ in _api()[3]:
        if expected == name:
            return function
    _need(False)


def _context(record):
    _api()
    _need(
        type(record) is dict
        and _ATTEMPTS.get(record.get("binding")) is record
        and _STATES.get(id(record["state"])) is record
        and record["pid"] == os.getpid()
        and record["thread"] == threading.get_ident()
        and record["task"] is asyncio.current_task()
        and record["loop"] is asyncio.get_running_loop()
    )
    state, view = record["state"], record["view"]
    _need(
        type(state) is dict
        and set(state) == _STATE_KEYS
        and state["schema_version"]
        == "ctcc.native_leverage_configuration.private_transport.v1"
        and state["binding"] is record["binding"]
        and state["view"] is view
        and state["context"] is view.context
        and state["owner"] is record["owner"]
        and state["root_record"] is record
        and record["received"] is record["original_received"]
    )
    return record


def _retain_unknown_without_callbacks_v1(record):
    # Integrity failure cannot invoke a changed Source or cleanup callee.
    # Durable intent/attempt is already committed; retain all actual owners.
    if (
        type(record) is dict
        and _ATTEMPTS.get(record.get("binding")) is record
        and type(record.get("state")) is dict
        and record["state"].get("root_record") is record
    ):
        record["state"]["root_phase"] = "UNKNOWN"
        record["phase"] = "unknown"


def is_state_v1(state):
    record = _STATES.get(id(state))
    return type(record) is dict and record.get("state") is state


def received_record_v1(record):
    """Root-only DATA admission before any post-receive Actor/live check."""
    root = _root()
    _need(
        type(record) is dict
        and type(record.get("state")) is dict
        and _STATES.get(id(record["state"])) is record
        and record["state"].get("root_record") is record
        and root._GATES.get(record["gate"]) is record
        and record["pid"] == os.getpid()
        and record["thread"] == threading.get_ident()
        and record["task"] is asyncio.current_task()
        and record["loop"] is asyncio.get_running_loop()
        and type(record["received"]) is root._native_transport._ResponseDataJournalV2
        and record["received"] is record["original_received"]
        and record["received"]._body_limit == MAX_BODY
    )
    return record


def retain_httpx_response_v1(record, response):
    root = _root()
    received_record_v1(record)
    _need(
        type(response) is root.httpx.Response
        and response.request is record["request"]
        and (
            record.get("httpx_response") is None or record["httpx_response"] is response
        )
    )
    record["httpx_response"] = response
    charge_v1(record)


def adopt_gate_record_v1(state, session, view, context):
    record = _context(_STATES.get(id(state)))
    _need(
        record["view"] is view
        and view.context is context
        and record["session"] is session
        and record["gate"] is None
        and record["phase"] == "retained"
    )
    return record


def _request_document(view):
    _need(
        type(view) is contracts.NativeLeverageConfigurationTransportViewV1
        and type(view.request_json) is bytes
        and 0 < len(view.request_json) <= 1024
        and type(view.request_sha256) is str
        and sha(view.request_json) == view.request_sha256
    )
    document = decode(view.request_json, 1024)
    _need(
        type(document) is dict
        and set(document)
        in ({"instId", "lever", "mgnMode"}, {"instId", "lever", "mgnMode", "posSide"})
        and canonical(document) == view.request_json
        and all(type(value) is str for value in document.values())
    )
    _need(
        re.fullmatch(r"[A-Z0-9]{1,16}-USDT-SWAP", document["instId"]) is not None
        and document["lever"] in {"3", "5", "8", "10", "20"}
        and document["mgnMode"] == "isolated"
        and ("posSide" not in document or document["posSide"] in {"long", "short"})
    )
    return document


def live_v1(record):
    root = _root()
    _context(record)
    view = _function("_replay_native_leverage_configuration_transport_v1")(
        record["binding"], key=root._LEVERAGE_CONFIGURATION_KEY_V1
    )
    _need(
        view is record["view"]
        and type(view.context)
        is contracts.NativeLeverageConfigurationTransportContextV1
    )
    ctx, document = view.context, _request_document(view)
    _need(
        type(ctx.client) is root._native_rest.OkxDemoPrivateRestClient
        and ctx.client is record["client"]
        and type(ctx.scope) is root._native_ledger.wire.ledger.LedgerScope
        and ctx.scope.environment == "demo"
        and ctx.scope.settlement_currency == "USDT"
        and ctx.configuration_intent_id == view.intent_id
        and ctx.session_factory is record["factory"]
        and type(ctx.origin) is str
        and ctx.origin in root._ORIGINS
        and ctx.origin == record["origin"]
        and ctx.client._external_client is None
        and ctx.client._clock_injected is False
    )
    _need(
        type(ctx.read_session) is root._native_rest.DemoPinnedReadSession
        and type(ctx.read_session.binding_id) is str
        and ctx.read_session.binding_id.isascii()
        and ctx.credential_session_sha256
        == sha(ctx.read_session.binding_id.encode("ascii"))
    )
    settings = ctx.client.settings
    _need(
        settings.trading_mode == "okx_demo"
        and settings.okx_demo_enabled is True
        and settings.okx_demo_allow_order_writes is True
        and settings.okx_demo_structural_dynamic_leverage_enabled is True
        and settings.live_trading is False
        and settings.okx_live_enabled is False
        and settings.okx_live_allow_order_writes is False
        and settings.okx_live_auto_execution is False
        and settings.paper_auto_execution is False
        and document["instId"] in settings.okx_demo_allowed_symbol_list
        and int(document["lever"]) <= settings.okx_demo_max_leverage
    )
    stamp = native_stamp()
    now = utc_from_ns(stamp["utc_ns"])
    _need(
        type(ctx.original_expires_at) is datetime
        and ctx.original_expires_at.tzinfo is UTC
        and type(ctx.monotonic_deadline_ns) is int
        and record["opened_at"] <= now < ctx.original_expires_at
        and record["last_stamp"]["utc_ns"] <= stamp["utc_ns"]
        and record["last_stamp"]["monotonic_ns"]
        <= stamp["monotonic_ns"]
        < ctx.monotonic_deadline_ns
    )
    record["last_stamp"] = stamp
    return view, ctx, now


def require_lock_v1(record):
    root = _root()
    view, ctx, _ = live_v1(record)
    locked = record["configuration_locked"]
    _need(type(locked) is contracts.NativeLeverageConfigurationLockViewV1)
    actual = _function("_check_native_leverage_configuration_physical_v1")(
        record["binding"], locked, key=root._LEVERAGE_CONFIGURATION_KEY_V1
    )
    session, transaction, connection, native_tx = (
        locked.session,
        locked.session_transaction,
        locked.connection,
        locked.root_transaction,
    )
    _need(
        actual is view
        and locked.binding is record["binding"]
        and locked.context is ctx
        and session is record["session"]
        and type(session) is AsyncSession
        and session.in_transaction()
        and not session.in_nested_transaction()
        and session.get_bind() is ctx.session_factory.kw["bind"].sync_engine
        and session.sync_session.get_transaction() is transaction
        and transaction.is_active
        and transaction.parent is None
        and transaction.nested is False
        and type(connection) is Connection
        and connection.engine is session.get_bind()
        and connection.closed is False
        and connection.invalidated is False
        and not connection.in_nested_transaction()
        and connection.get_transaction() is native_tx
        and type(native_tx) is RootTransaction
        and native_tx.is_active
        and native_tx.connection is connection
    )
    return view


async def lock_after_tls_v1(record):
    root = _root()
    previous = record["configuration_locked"]
    locked = await _function("_lock_native_leverage_configuration_v1")(
        record["binding"], record["session"], key=root._LEVERAGE_CONFIGURATION_KEY_V1
    )
    _accept_lock_v1(record, locked, previous)


def _accept_lock_v1(record, locked, previous):
    _need(
        type(locked) is contracts.NativeLeverageConfigurationLockViewV1
        and locked.session is record["session"]
        and locked.binding is record["binding"]
        and locked.context is record["view"].context
        and type(locked.database_metadata) is tuple
        and len(locked.database_metadata) == 10
        and locked.intent_row_json == record["view"].intent_row_json
        and locked.attempt_row_json == record["view"].attempt_row_json
    )
    if previous is not None:
        _need(
            locked.session_transaction is previous.session_transaction
            and locked.connection is previous.connection
            and locked.root_transaction is previous.root_transaction
            and locked.database_metadata[:9] == previous.database_metadata[:9]
            and previous.database_metadata[9] <= locked.database_metadata[9]
        )
    record["configuration_locked"] = locked
    require_lock_v1(record)


def _snapshot_v1(record):
    root = _root()
    state, view, journal = record["state"], record["view"], record["received"]
    values = [
        view.request_json,
        view.intent_row_json,
        view.attempt_row_json,
        view.source_receipt_json,
        view.plan_receipt_json,
        view.expected_scope_json,
        state["credential_document"],
        record["wire"],
    ]
    signature = record.get("actual_post_signature")
    if signature is not None:
        values.append(signature)
    request = record.get("request")
    if request is not None:
        values.extend(
            root._native_rest.reconciliation_read.retained_httpx_request_data_v1(
                request
            )
        )
    response = record.get("httpx_response")
    if response is not None:
        # The immutable content copy is populated only after the bounded stream.
        content = vars(response).get("_content")
        if content is not None:
            _need(type(content) is bytes)
            values.append(content)
    values.extend(journal.tls_chunks)
    values.extend(journal.body_chunks)
    values.extend(part for pair in journal.headers for part in pair)
    values.extend(journal.faults)
    result, seen = [], set()
    for value in values:
        _need(type(value) in (bytes, str))
        if id(value) not in seen:
            seen.add(id(value))
            result.append(value)
    _need(len(result) <= 8192)
    return tuple(result)


def charge_v1(record):
    root = _root()
    _context(record)
    actual = _snapshot_v1(record)
    record["state"]["received_buffers"] = actual
    _function("_retain_native_leverage_configuration_transport_buffers_v1")(
        record["binding"], actual, key=root._LEVERAGE_CONFIGURATION_KEY_V1
    )


def prepared_v1(record):
    _context(record)
    signature = record.get("actual_post_signature")
    _need(type(signature) is str and signature and signature.isascii())
    record["state"]["prepared_signature"] = signature
    document = decode(record["state"]["credential_document"], 32768)
    _need(
        type(document) is dict
        and set(document) == {"key", "secret", "passphrase"}
        and all(
            type(value) is str and 0 < len(value) <= 4096 and value.isascii()
            for value in document.values()
        )
    )
    captured = tuple(document[name] for name in ("key", "secret", "passphrase"))
    _need(
        captured == record["view"].context.read_session._credentials
        and captured == record["client"]._credentials()
    )
    charge_v1(record)


def _unknown_v1(record):
    _context(record)
    record["state"]["root_phase"] = "UNKNOWN"
    if record["phase"] not in ("completed", "unknown", "denied"):
        record["phase"] = "unknown"


def _inhibit_v1(record):
    _unknown_v1(record)
    if record.get("stop") is None:
        record["stop"] = _function("_inhibit_native_leverage_configuration_v1")(
            record["binding"], key=_root()._LEVERAGE_CONFIGURATION_KEY_V1
        )
    return record["stop"]


class _ConfigurationAttemptV1(Coroutine):
    __slots__ = ("body", "record")

    def __init__(self, record, deadline):
        self.record, self.body = record, _submit_v1(record, deadline)

    def __await__(self):
        return self

    def __iter__(self):
        return self

    def __next__(self):
        return self.send(None)

    def send(self, value):
        try:
            _context(self.record)
        except BaseException:
            _retain_unknown_without_callbacks_v1(self.record)
            raise
        return self.body.send(value)

    def throw(self, *arguments):
        try:
            _context(self.record)
        except BaseException:
            _retain_unknown_without_callbacks_v1(self.record)
            raise
        return self.body.throw(*arguments)

    def close(self):
        try:
            _context(self.record)
        except BaseException:
            _retain_unknown_without_callbacks_v1(self.record)
            raise
        if not self.record.get("driver_finished", False):
            _inhibit_v1(
                self.record
            )  # Sync local revoke; durable attempt already exists.
        try:
            return self.body.close()
        finally:
            if not self.record.get("driver_finished", False):
                _unknown_v1(self.record)


def submit_owned_native_demo_leverage_configuration_v1(owner):
    root, api = _root(), _api()
    _need(type(owner) is api[1] and len(_ATTEMPTS) < 16)
    loop_before, stamp = asyncio.get_running_loop().time(), native_stamp()
    view = _function("_take_native_leverage_configuration_for_transport_v1")(
        owner, key=root._LEVERAGE_CONFIGURATION_KEY_V1
    )
    _need(
        type(view) is contracts.NativeLeverageConfigurationTransportViewV1
        and type(view.context)
        is contracts.NativeLeverageConfigurationTransportContextV1
    )
    _request_document(view)
    state = _function("_native_leverage_configuration_transport_state_v1")(
        view.binding, key=root._LEVERAGE_CONFIGURATION_KEY_V1
    )
    _need(
        type(state) is dict
        and set(state) == _STATE_KEYS
        and state["view"] is view
        and state["owner"] is owner
        and state["root_phase"] == "MOVED"
        and state["root_record"] is None
        and state["observed"] is None
        and state["response_handle"] is None
        and state["prepared_signature"] is None
        and type(state["credential_document"]) is bytes
        and 0 < len(state["credential_document"]) <= 32768
        and view.binding not in _ATTEMPTS
    )
    journal = root._native_transport._ResponseDataJournalV2(body_limit=MAX_BODY)
    record = {
        "state": state,
        "view": view,
        "binding": view.binding,
        "owner": owner,
        "gate": None,
        "session": None,
        "configuration_locked": None,
        "configuration_purpose": root._LEVERAGE_CONFIGURATION_KEY_V1,
        "original_transport_facts": view,
        "transport_context": view.context,
        "client": view.context.client,
        "factory": view.context.session_factory,
        "origin": view.context.origin,
        "pid": os.getpid(),
        "thread": threading.get_ident(),
        "task": asyncio.current_task(),
        "loop": asyncio.get_running_loop(),
        "opened_at": utc_from_ns(stamp["utc_ns"]),
        "last_stamp": stamp,
        "phase": "retained",
        "lock": threading.RLock(),
        "request": None,
        "wire": b"",
        "accepted": 0,
        "raw_calls": 0,
        "attempted": False,
        "transport_taken": False,
        "tls_ready": False,
        "received": journal,
        "original_received": journal,
        "native_socket_owners": None,
        "cleanup_corrupt_socket": None,
        "cleanup_unknown_sockets": [],
        "unlocked": False,
    }
    _ATTEMPTS[view.binding], _STATES[id(state)] = record, record
    state["root_record"], state["root_phase"] = record, "ROOT_RETAINED"
    try:
        live_v1(record)
        remaining = min(
            (
                view.context.original_expires_at - utc_from_ns(stamp["utc_ns"])
            ).total_seconds(),
            (view.context.monotonic_deadline_ns - stamp["monotonic_ns"]) / 1e9,
        )
        _need(math.isfinite(remaining) and remaining > 0)
        operation = _ConfigurationAttemptV1(
            record, math.nextafter(loop_before + remaining, -math.inf)
        )
        record["operation"] = (
            operation  # Dropping an unstarted awaitable cannot release it.
        )
        charge_v1(record)
        return operation
    except BaseException:
        _inhibit_v1(record)
        raise


def _mint_response_v1(record):
    _context(record)
    _need(record.get("response_view") is None)
    journal = record["received"]
    code = None
    if journal.body_complete and 0 < len(journal.body_raw) <= MAX_BODY:
        try:
            document = decode(journal.body_raw, MAX_BODY)
            candidate = document.get("code") if type(document) is dict else None
            if (
                type(candidate) is str
                and re.fullmatch(r"[0-9]{1,12}", candidate) is not None
            ):
                code = candidate
        except (ValueError, UnicodeError, TypeError):
            pass  # Invalid/partial DATA remains private, never an ACK.
    observed = contracts.ObservedNativeLeverageConfigurationV1(
        journal.status, code, sha(journal.body_raw) if journal.body_chunks else None
    )
    handle = object.__new__(_ResponseHandleV1)
    view = contracts.NativeLeverageConfigurationResponseViewV1(
        record["binding"], record["view"], handle, observed, journal, record["state"]
    )
    record["observed"], record["response_handle"], record["response_view"] = (
        observed,
        handle,
        view,
    )
    record["state"]["observed"], record["state"]["response_handle"] = observed, handle
    _OBSERVED[id(observed)], _RESPONSES[handle] = record, record
    return observed


async def _submit_v1(record, deadline):
    root = _root()
    failure = None
    try:
        async with asyncio.timeout_at(deadline):
            view, context, _ = live_v1(record)
            async with context.session_factory() as session, session.begin():
                record["session"] = session
                locked = await _function("_lock_native_leverage_configuration_v1")(
                    record["binding"], session, key=root._LEVERAGE_CONFIGURATION_KEY_V1
                )
                _accept_lock_v1(record, locked, None)
                gate = root._create_gate_record_v1(
                    record["state"], session, view, context
                )
                _need(root._GATES.get(gate) is record)
                await context.client._submit_native_demo_leverage_configuration_v1(
                    _request_document(view), native_write_gate=gate
                )
                _need(record["phase"] == "completed")
                require_lock_v1(record)
            record["unlocked"] = True
    except BaseException as error:  # noqa: BLE001 -- Retain the original failure through transaction cleanup.
        failure = error
    finally:
        # Reaching here means transaction __aexit__ has returned/raised. A
        # GeneratorExit during rollback is unknown, never a locks-released fact.
        locked = record.get("configuration_locked")
        record["unlocked"] = (
            type(locked) is contracts.NativeLeverageConfigurationLockViewV1
            and type(record.get("session")) is AsyncSession
            and not record["session"].in_transaction()
            and not record["session"].in_nested_transaction()
            and locked.session_transaction.is_active is False
            and locked.root_transaction.is_active is False
            and locked.connection.get_transaction() is None
        )
        root._retire_gate(record["gate"])
        if failure is not None:
            _unknown_v1(record)
        else:
            record["state"]["root_phase"] = "SPENT"
        observed = _mint_response_v1(record)
        charge_v1(record)  # Actual received DATA survives any later denial.
        record["driver_finished"] = True
    if failure is not None:
        if isinstance(failure, GeneratorExit):
            raise failure
        try:
            if record["unlocked"] is not True:
                # Local revoke is synchronous, but never await an account stop
                # behind locks whose rollback/close has not been proved.
                _inhibit_v1(record)
                raise root.NativeDemoWriteError(
                    "native_leverage_configuration_locks_or_stop_unconfirmed"
                )
            async with asyncio.timeout(5):
                await _inhibit_v1(record)
        except BaseException:  # noqa: BLE001 -- Failed stop persistence leaves execution inhibited.
            raise root.NativeDemoWriteError(
                "native_leverage_configuration_stop_unconfirmed"
            ) from None
        if isinstance(failure, asyncio.CancelledError):
            raise asyncio.CancelledError() from None
        if isinstance(failure, (KeyboardInterrupt, SystemExit)):
            raise type(failure)() from None
        raise root.NativeDemoWriteError(
            "native_leverage_configuration_send_or_readback_unknown"
        ) from None
    return observed


def _passive_v1(binding, key_index, key):
    root, api = _root(), _api()
    _need(type(key) is object and key is api[2][key_index])
    # Actor's fixed PURE getter rejects a foreign binding type before hashing;
    # the real opaque binding is a nonconstructible class, not object().
    state = _function("_native_leverage_configuration_transport_state_v1")(
        binding, key=root._LEVERAGE_CONFIGURATION_KEY_V1
    )
    _need(
        type(state) is dict
        and set(state) == _STATE_KEYS
        and state["binding"] is binding
    )
    record = _STATES.get(id(state))
    if record is not None:
        _need(record.get("state") is state and record.get("binding") is binding)
        return _context(record)
    # Genuine pre-adoption only. No Source/clock/budget/client getter is called.
    if (
        type(state) is dict
        and set(state) == _STATE_KEYS
        and state.get("root_phase") == "RETIRED"
    ):
        retired = state.get("root_record")
        _need(
            type(retired) is dict
            and retired.get("retired_data_marker") is _RETIRED_DATA_MARKER_V1
            and retired.get("original_root_record") is retired
            and retired.get("state") is state
            and retired["binding"] is binding
            and state["retired_root_receipt"] is retired["retirement_receipt"]
            and retired["pid"] == os.getpid()
            and retired["thread"] == threading.get_ident()
            and retired["task"] is asyncio.current_task()
            and retired["loop"] is asyncio.get_running_loop()
            and retired["received"] is retired["original_received"]
        )
        return retired
    _need(
        type(state) is dict
        and set(state) == _STATE_KEYS
        and state["binding"] is binding
        and state["root_phase"] in ("NOT_STARTED", "MOVED")
        and state["root_record"] is None
        and state["response_handle"] is None
        and state["observed"] is None
        and state["prepared_signature"] is None
        and state["received_buffers"] == ()
    )
    return state


def _native_leverage_configuration_retained_buffers_v1(binding, *, key):
    value = _passive_v1(binding, 0, key)
    if (
        is_state_v1(value.get("state"))
        or value.get("retired_data_marker") is _RETIRED_DATA_MARKER_V1
    ):
        return _snapshot_v1(value)
    view = value["view"]
    _need(type(view) is contracts.NativeLeverageConfigurationTransportViewV1)
    values = (
        view.request_json,
        view.intent_row_json,
        view.attempt_row_json,
        view.source_receipt_json,
        view.plan_receipt_json,
        view.expected_scope_json,
        value["credential_document"],
    )
    _need(all(type(raw) is bytes for raw in values))
    return values


def _native_leverage_configuration_scan_tokens_v1(binding, *, key):
    record = _passive_v1(binding, 0, key)
    if (
        not is_state_v1(record.get("state"))
        and record.get("retired_data_marker") is not _RETIRED_DATA_MARKER_V1
    ):
        return ()  # Exact genuine never-prepared state above, not absent custody.
    signature = record.get("actual_post_signature")
    if signature is None:
        _need(
            record["request"] is None
            and not record["attempted"]
            and record["wire"] == b""
        )
        return ()
    _need(type(signature) is str and record["state"]["prepared_signature"] is signature)
    values = [
        value.lstrip(b" ")
        for line in record["wire"].split(b"\r\n\r\n", 1)[0].split(b"\r\n")[1:]
        for name, separator, value in (line.partition(b":"),)
        if separator and name.lower() == b"ok-access-sign"
    ]
    _need(values == [signature.encode("ascii")])
    return (signature,)


def _response_v1(handle, key):
    _need(key is _api()[2][1] and type(handle) is _ResponseHandleV1)
    record = _context(_RESPONSES.get(handle))
    _need(
        record["response_handle"] is handle
        and record.get("driver_finished") is True
        and record.get("unlocked") is True
    )
    return record


def _take_native_leverage_configuration_response_v1(observed, *, key):
    _need(
        key is _api()[2][1]
        and type(observed) is contracts.ObservedNativeLeverageConfigurationV1
    )
    record = _OBSERVED.get(id(observed))
    _need(
        type(record) is dict
        and record.get("observed") is observed
        and record.get("response_taken") is not True
    )
    _response_v1(record["response_handle"], key)
    record["response_taken"] = True
    return record["response_view"]


def _replay_native_leverage_configuration_response_v1(handle, *, key):
    record = _response_v1(handle, key)
    _need(record.get("response_taken") is True)
    return record["response_view"]


def _closed_v1(closed, key):
    _need(
        key is _api()[2][2] and type(closed) is OwnedNativeClosedLeverageConfigurationV1
    )
    record = _context(_CLOSED.get(closed))
    _need(record["closed_handle"] is closed and record.get("response_taken") is True)
    root = _root()
    root._require_vendor_state_v2()
    _need(
        root._native_settlement._socket_closed(record)
        and root._native_settlement._sdk_closed(record)
    )
    return record


def _observe_native_closed_leverage_configuration_v1(handle, *, key):
    _need(key is _api()[2][2] and type(handle) is _ResponseHandleV1)
    record = _context(_RESPONSES.get(handle))
    _need(
        record.get("driver_finished") is True
        and record.get("unlocked") is True
        and record.get("response_taken") is True
    )
    root = _root()
    root._require_vendor_state_v2()
    # A missing/unexposed socket is unknown. No absent-pointer close proof.
    _need(
        record["gate"] is not None
        and record.get("native_socket_owners") is not None
        and record.get("httpx_client") is not None
        and record.get("transport") is not None
    )
    if not root._native_settlement._socket_closed(
        record
    ) or not root._native_settlement._sdk_closed(record):
        return None
    if record.get("closed_handle") is not None:
        return record["closed_handle"]
    owners = record["native_socket_owners"]
    observation = root._native_settlement.observe_native_closed_httpx_tcp_data_v1(
        record["httpx_client"],
        record["transport"],
        owners.native_transport,
        owners.raw_socket,
    )
    _need(type(observation) is tuple)
    closed = object.__new__(OwnedNativeClosedLeverageConfigurationV1)
    record["closed_handle"] = closed
    record["closed_view"] = contracts.NativeLeverageConfigurationClosedViewV1(
        record["binding"], record["response_view"], observation, closed
    )
    _CLOSED[closed] = record
    return closed


def _take_native_closed_leverage_configuration_v1(closed, *, key):
    record = _closed_v1(closed, key)
    _need(record.get("closed_taken") is not True)
    record["closed_taken"] = True
    return record["closed_view"]


def _replay_native_closed_leverage_configuration_v1(closed, *, key):
    record = _closed_v1(closed, key)
    _need(record.get("closed_taken") is True)
    return record["closed_view"]


def _finalize_native_leverage_configuration_cleanup_v1(closed, cleanup_cap, *, key):
    record = _closed_v1(closed, key)
    _need(record.get("closed_taken") is True and closed not in _FINALIZED)
    view = _function("_take_native_leverage_configuration_cleanup_for_root_v1")(
        cleanup_cap, key=_root()._LEVERAGE_CONFIGURATION_KEY_V1
    )
    _need(
        type(view) is contracts.NativeLeverageConfigurationCleanupViewV1
        and view.binding is record["binding"]
        and view.closed_handle is closed
        and view.transport_view is record["view"]
        and view.original_source_generation is record["view"].original_source_generation
        and view.result_cap is not None
        and type(view.result_view) is contracts.NativeLeverageConfigurationResultV1
        and view.result_view.intent_id == record["view"].intent_id
        and view.result_view.classification == "verified"
        and view.source_completion is not None
        and view.source_completion_view is not None
        and type(view.result_receipt_json) is bytes
        and 0 < len(view.result_receipt_json) <= 65536
        and type(view.source_cleanup_receipt_json) is bytes
        and 0 < len(view.source_cleanup_receipt_json) <= 65536
        and view.result_view.receipt_sha256 == sha(view.result_receipt_json)
        and canonical(decode(view.result_receipt_json, 65536))
        == view.result_receipt_json
        and canonical(decode(view.source_cleanup_receipt_json, 65536))
        == view.source_cleanup_receipt_json
    )
    receipt = (
        "ctcc.native_leverage_configuration.root_resources_finalized.v1",
        record["view"].intent_id,
        sha(view.result_receipt_json),
        sha(view.source_cleanup_receipt_json),
    )
    record["cleanup_cap"], record["cleanup_view"], record["finalized_receipt"] = (
        cleanup_cap,
        view,
        receipt,
    )
    record["state"]["cleanup_phase"] = "FINALIZED"
    _FINALIZED[closed] = record
    return receipt  # ALL Root/actor/socket authenticators remain retained.


def _retire_native_leverage_configuration_after_source_v1(closed, retirement, *, key):
    record = _closed_v1(closed, key)
    _need(
        _FINALIZED.get(closed) is record
        and record["state"]["cleanup_phase"] == "FINALIZED"
    )
    view = _function("_replay_native_leverage_configuration_retirement_for_root_v1")(
        retirement, key=_root()._LEVERAGE_CONFIGURATION_KEY_V1
    )
    _need(
        type(view) is contracts.NativeLeverageConfigurationRetirementViewV1
        and view.binding is record["binding"]
        and view.closed_handle is closed
        and view.cleanup_cap is record["cleanup_cap"]
        and view.source_retirement is not None
        and view.result_receipt_json == record["cleanup_view"].result_receipt_json
        and type(view.source_retirement_receipt_json) is bytes
        and 0 < len(view.source_retirement_receipt_json) <= 65536
        and canonical(decode(view.source_retirement_receipt_json, 65536))
        == view.source_retirement_receipt_json
    )
    root = _root()
    _need(
        root._GATES.get(record["gate"]) is record
        and root._CLIENT_GATES.get(record["client"]) is record["gate"]
        and _RESPONSES.get(record["response_handle"]) is record
        and _OBSERVED.get(id(record["observed"])) is record
    )
    receipt = (
        "ctcc.native_leverage_configuration.root_authentication_retired.v1",
        record["view"].intent_id,
        sha(view.result_receipt_json),
        sha(view.source_retirement_receipt_json),
    )
    # Actor/Source has captured the actual terminal byte/token snapshot before
    # this genuine one-use retirement. No DB row or uncertainty is deleted.
    record["state"]["retired_root_receipt"] = receipt
    record["state"]["root_phase"], record["state"]["cleanup_phase"] = (
        "RETIRED",
        "RETIRED",
    )
    (
        record["retired_data_marker"],
        record["original_root_record"],
        record["retirement_receipt"],
    ) = _RETIRED_DATA_MARKER_V1, record, receipt
    root._CLIENT_GATES.pop(record["client"])
    root._GATES.pop(record["gate"])
    _RESPONSES.pop(record["response_handle"])
    _OBSERVED.pop(id(record["observed"]))
    _CLOSED.pop(closed)
    _FINALIZED.pop(closed)
    _ATTEMPTS.pop(record["binding"])
    _STATES.pop(id(record["state"]))
    return receipt
