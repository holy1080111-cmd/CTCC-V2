"""One-use genuine disarmed-source configuration issuer and physical lock seam.

No caller DTO, desired leverage, source flag or old trade owner is accepted.
Only the explicit configuration purpose can reach Root's same physical gate.
"""

import asyncio
import sys
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.engine import Connection, RootTransaction
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.repositories import native_leverage_configuration_v55 as repository
from app.database.repositories import qualification_ledger as ledger
from app.domain.source_primitives import canonical, decode, sha, utc_from_ns
from app.exchange.okx import private_rest
from app.trade_qualification import demo_control as control
from app.trade_qualification import native_account_risk_assembly_v1 as account
from app.trade_qualification import native_continuation_guard_v1 as continuation
from app.trade_qualification import native_flat_leverage_account_source_v1 as source
from app.trade_qualification import (
    native_leverage_configuration_contracts_v1 as contracts,
)

_SOURCE_KEY_V1 = object()
_REPOSITORY_KEY_V1 = object()
_ROOT_DATA_KEY_V1 = object()
_ROOT_RESPONSE_KEY_V1 = object()
_ROOT_SETTLEMENT_KEY_V1 = object()
_STATES = {}
_OWNERS = {}
_NO_CHANGE = {}
_UNKNOWN = {}
_CLEANUPS = {}
_RETIREMENTS = {}
_MAX_OPERATIONS = 16


class NativeLeverageConfigurationError(ValueError):
    pass


def _need(condition, code="native_leverage_configuration_unavailable"):
    if condition is not True:
        raise NativeLeverageConfigurationError(code) from None


class _OpaqueConfigurationV1:
    __slots__ = ()

    def __new__(cls):
        raise NativeLeverageConfigurationError(
            "native_leverage_configuration_not_constructible"
        )

    def __copy__(self):
        raise NativeLeverageConfigurationError(
            "native_leverage_configuration_not_transferable"
        )

    def __deepcopy__(self, _memo):
        raise NativeLeverageConfigurationError(
            "native_leverage_configuration_not_transferable"
        )

    def __reduce_ex__(self, _protocol):
        raise NativeLeverageConfigurationError(
            "native_leverage_configuration_not_transferable"
        )


class OwnedNativeLeverageConfigurationIntentV1(_OpaqueConfigurationV1):
    __slots__ = ()


class OwnedNativeNoChangeConfigurationV1(_OpaqueConfigurationV1):
    __slots__ = ()


class _ConfigurationBindingV1(_OpaqueConfigurationV1):
    __slots__ = ()


@dataclass(frozen=True, slots=True, repr=False)
class _RepositoryContextV1:
    source_view: source.FlatLeverageSourceViewV1
    scope_json: bytes
    intent_id: str | None
    attempt_id: str | None
    intent_row_json: bytes | None
    attempt_row_json: bytes | None
    result_row_json: bytes | None
    result_sequence: int
    database_phase: str | None
    sampled_at: datetime
    expected_control_state: control.ControlState
    expected_control_event_sha256: str


def _root_key(key):
    from app.exchange.okx import native_demo_write_v2 as root

    _need(key is root._LEVERAGE_CONFIGURATION_KEY_V1)
    return root


def _original_state(binding):
    _need(type(binding) is _ConfigurationBindingV1)
    state = _STATES.get(binding)
    _need(
        type(state) is dict
        and state["binding"] is binding
        and state["task"] is asyncio.current_task()
    )
    return state


def _check(binding):
    state = _original_state(binding)
    _need(
        _UNKNOWN.get(binding) is None and state["phase"] not in ("UNKNOWN", "RETIRED")
    )
    view = source._replay_flat_leverage_source_v1(
        state["source"].binding, key=_SOURCE_KEY_V1
    )
    _need(
        view is state["source"]
        and view.source_generation is state["generation"]
        and view.plan.request_json == state["request_json"]
        and view.plan.receipt_json == state["plan_json"]
    )
    client = state.get("client")
    if client is not None:
        _need(
            type(client) is private_rest.OkxDemoPrivateRestClient
            and client.settings is view.settings
            and client._external_client is None
            and client._clock_injected is False
            and account._v3_data_pin(client.__dict__) == state["client_pin"]
        )
    return state


def _unknown(state):
    if type(state) is dict and _STATES.get(state.get("binding")) is state:
        state["phase"] = "UNKNOWN"
        _UNKNOWN[state["binding"]] = state
        _OWNERS.pop(state.get("owner"), None)
        _NO_CHANGE.pop(state.get("owner"), None)
        source._retain_flat_leverage_source_unknown_v1(
            state["source"].binding, key=_SOURCE_KEY_V1
        )


def _task_finished(task, binding):
    state = _STATES.get(binding)
    if state is not None and state["task"] is task and state["phase"] != "COMPACTED":
        _unknown(state)


def issue_owned_native_leverage_configuration_v1(owner):
    """Synchronous source MOVE/strong custody, then actual durable issuance."""
    _need(len(_STATES) < _MAX_OPERATIONS)
    view = source._take_flat_leverage_source_v1(owner, key=_SOURCE_KEY_V1)
    _need(type(view) is source.FlatLeverageSourceViewV1)
    binding = object.__new__(_ConfigurationBindingV1)
    state = {
        "binding": binding,
        "task": asyncio.current_task(),
        "source": view,
        "generation": view.source_generation,
        "phase": "ISSUING",
        "request_json": view.plan.request_json,
        "plan_json": view.plan.receipt_json,
        "scope_json": view.source_state["base_scope_json"],
        "intent_id": None,
        "attempt_id": None,
        "intent_row_json": None,
        "attempt_row_json": None,
        "result_row_json": None,
        "result_sequence": 0,
        "database_phase": None,
        "commits": [],
        "readbacks": [],
        "client": None,
        "lock": None,
        "buffers": (),
    }
    _STATES[binding] = state
    try:
        _check(binding)
        state["task"].add_done_callback(lambda task: _task_finished(task, binding))
        return _issue(state)
    except BaseException:
        _unknown(state)
        raise


async def _accept_commit(state, operation):
    binding = state["binding"]
    cap = await operation
    state["commits"].append(cap)
    _check(binding)
    readback = await repository.read_configuration_commit_v1(
        cap, key=_REPOSITORY_KEY_V1
    )
    _check(binding)
    observed_binding, actual = repository._replay_configuration_readback_v1(
        readback, key=_REPOSITORY_KEY_V1
    )
    _need(observed_binding is binding and actual is readback)
    state["readbacks"].append(readback)
    state["scope_json"] = readback.scope_json
    state["intent_row_json"], state["attempt_row_json"], state["result_row_json"] = (
        readback.intent_row_json,
        readback.attempt_row_json,
        readback.result_row_json,
    )
    row = decode(readback.intent_row_json, 2097152)
    state["intent_id"], state["database_phase"], state["result_sequence"] = (
        row["intent_id"],
        row["phase"],
        row["latest_result_sequence"],
    )
    if readback.attempt_row_json is not None:
        state["attempt_id"] = decode(readback.attempt_row_json)["attempt_id"]
    _check(binding)
    return readback


async def _issue(state):
    try:
        binding, view = state["binding"], state["source"]
        local = decode(state["scope_json"])
        previous = local["ledger_revision"]
        core = canonical(
            {
                "schema_version": "ctcc.native_leverage_configuration.intent.v1",
                "scope": [
                    view.scope.environment,
                    view.scope.account_id,
                    view.scope.settlement_currency,
                ],
                "account_revision": local["account_revision"],
                "previous_ledger_revision": previous,
                "ledger_revision": previous + 1,
                "claims_sha256": local["claims_sha256"],
                "control_revision": view.control_state.revision,
                "control_event_sha256": view.control_event_sha256,
                "owner_sha256": view.control_state.owner_sha256,
                "owner_epoch": view.control_state.owner_epoch,
                "source_sha256": sha(view.source_receipt_json),
                "plan_sha256": sha(view.plan.receipt_json),
                "request_sha256": sha(view.plan.request_json),
                "original_expires_at": view.original_expires_at.isoformat(),
                "execution_authority": False,
                "order_retry_authority": False,
            }
        )
        state["pending_event_json"], state["pending_intent_id"] = core, sha(core)
        await _accept_commit(
            state,
            repository.insert_configuration_intent_v1(
                binding, core, key=_REPOSITORY_KEY_V1
            ),
        )
        if view.plan.selected_leverage == view.plan.configured_leverage:
            owner = object.__new__(OwnedNativeNoChangeConfigurationV1)
            state["owner"], state["phase"] = owner, "NO_CHANGE_READY"
            _NO_CHANGE[owner] = state
            return owner
        local = decode(state["scope_json"])
        attempt = canonical(
            {
                "schema_version": "ctcc.native_leverage_configuration.attempt.v1",
                "intent_id": state["intent_id"],
                "request_sha256": sha(view.plan.request_json),
                "account_revision": local["account_revision"],
                "previous_ledger_revision": local["ledger_revision"],
                "ledger_revision": local["ledger_revision"] + 1,
                "execution_authority": False,
                "order_retry_authority": False,
            }
        )
        state["pending_event_json"] = attempt
        await _accept_commit(
            state,
            repository.insert_configuration_attempt_v1(
                binding, attempt, key=_REPOSITORY_KEY_V1
            ),
        )
        # Read credentials only through the already genuine formal READ owner.
        # The dedicated foreign WRITE getter is correctly unavailable pre-gate.
        credentials = view.private_client._credentials()
        _need(
            type(credentials) is tuple
            and len(credentials) == 3
            and all(
                type(value) is str and value.isascii() and 0 < len(value) <= 4096
                for value in credentials
            )
        )
        credential_document = canonical(
            {
                "key": credentials[0],
                "secret": credentials[1],
                "passphrase": credentials[2],
            }
        )
        client = private_rest.OkxDemoPrivateRestClient(settings=view.settings)
        _need(
            client is not view.private_client
            and client._external_client is None
            and client._clock_injected is False
        )
        state["client"], state["client_pin"] = (
            client,
            account._v3_data_pin(client.__dict__),
        )
        context = contracts.NativeLeverageConfigurationTransportContextV1(
            client,
            view.scope,
            view.session_factory,
            view.control_owner,
            view.current_record.packet.plan.origin,
            view.source_state["control"]["pins"].credential_session_sha256,
            view.original_expires_at,
            view.monotonic_deadline_ns,
            view.read_session,
            state["intent_id"],
        )
        transport = contracts.NativeLeverageConfigurationTransportViewV1(
            binding,
            context,
            state["intent_id"],
            state["attempt_id"],
            view.plan.request_json,
            sha(view.plan.request_json),
            state["intent_row_json"],
            state["attempt_row_json"],
            view.source_receipt_json,
            view.plan.receipt_json,
            state["scope_json"],
            view.source_generation,
        )
        owner = object.__new__(OwnedNativeLeverageConfigurationIntentV1)
        state["owner"], state["view"], state["phase"] = owner, transport, "ISSUED"
        state["transport"] = {
            "schema_version": "ctcc.native_leverage_configuration.private_transport.v1",
            "binding": binding,
            "owner": owner,
            "view": transport,
            "root_phase": "NOT_STARTED",
            "root_record": None,
            "context": context,
            "credential_document": credential_document,
            "response_handle": None,
            "observed": None,
            "received_buffers": (),
            "prepared_signature": None,
            "retired_root_receipt": None,
            "cleanup_phase": "NONE",
        }
        _OWNERS[owner] = state
        view.source_state["configuration_actor_binding"] = binding
        _check(binding)
        return owner
    except BaseException:
        _unknown(state)
        raise


def _replay_native_leverage_configuration_repository_v1(binding, *, key):
    _need(key is repository._SOURCE_KEY_V1)
    state = _original_state(binding)
    view = state["source"]
    expected_control, expected_event = view.control_state, view.control_event_sha256
    if state["phase"] in ("PERSISTING_VERIFIED", "FINAL_NEGATIVE_PERSISTENCE"):
        _negative_state(binding)
        sampled = utc_from_ns(state["negative_budget"]["last"]["utc_ns"])
        if state["phase"] == "FINAL_NEGATIVE_PERSISTENCE":
            retirement = source._retired_flat_configuration_v1(
                state["source_retirement"], key=_SOURCE_KEY_V1
            )
            _need(
                retirement["view"] is state["source_completion_view"]
                and len(retirement["control_reads"]) == 2
            )
            expected_control, expected_event = retirement["control_reads"][1][:2]
    else:
        _check(binding)
        sampled = utc_from_ns(source._check(view.source_state)["utc_ns"])
    return _RepositoryContextV1(
        state["source"],
        state["scope_json"],
        state["intent_id"],
        state["attempt_id"],
        state["intent_row_json"],
        state["attempt_row_json"],
        state["result_row_json"],
        state["result_sequence"],
        state["database_phase"],
        sampled,
        expected_control,
        expected_event,
    )


def _take_native_leverage_configuration_for_transport_v1(owner, *, key):
    _root_key(key)
    state = (
        _OWNERS.pop(owner, None)
        if type(owner) is OwnedNativeLeverageConfigurationIntentV1
        else None
    )
    _need(state is not None)
    try:
        _check(state["binding"])
        _need(
            state["owner"] is owner
            and state["phase"] == "ISSUED"
            and state["database_phase"] == "attempted"
        )
        state["phase"], state["transport"]["root_phase"] = "MOVED", "MOVED"
        return state["view"]
    except BaseException:
        _unknown(state)
        raise


def _native_leverage_configuration_transport_state_v1(binding, *, key):
    """Pure actual registry/task replay: deliberately no Source/clock/budget."""
    _root_key(key)
    state = _original_state(binding)
    actual = state.get("transport")
    _need(
        type(actual) is dict
        and actual["binding"] is binding
        and actual["view"] is state["view"]
        and actual["owner"] is state["owner"]
        and len(actual) == 14
    )
    return actual


def _replay_native_leverage_configuration_transport_v1(binding, *, key):
    _root_key(key)
    state = _check(binding)
    _need(
        state["phase"] == "MOVED"
        and state["transport"]["root_phase"] in ("MOVED", "ROOT_RETAINED")
        and state["transport"]["view"] is state["view"]
    )
    return state["view"]


async def _lock_native_leverage_configuration_v1(binding, session, *, key):
    _root_key(key)
    state = _check(binding)
    view = state["view"]
    factory = view.context.session_factory
    _need(
        type(session) is AsyncSession
        and session.in_transaction()
        and not session.in_nested_transaction()
        and session.get_bind() is factory.kw["bind"].sync_engine
    )
    transaction = session.sync_session.get_transaction()
    _need(
        transaction is not None
        and transaction.is_active
        and transaction.parent is None
        and transaction.nested is False
    )
    for sql in (
        "SET LOCAL search_path=pg_catalog,public,pg_temp",
        "SET LOCAL TIME ZONE 'UTC'",
        "SET LOCAL synchronous_commit='on'",
        "SET LOCAL lock_timeout='1000ms'",
        "SET LOCAL statement_timeout='1000ms'",
    ):
        await session.execute(text(sql))
        _check(binding)
    _need(
        await session.scalar(text("SELECT current_setting('transaction_isolation')"))
        == "read committed"
    )
    _check(binding)
    await repository.verify_actual_configuration_catalog_v1(session)
    _check(binding)
    (
        actual,
        event,
    ) = await repository.controls.DemoControlRepositoryV2.read_in_transaction(
        session, state["source"].control_state.scope
    )
    _check(binding)
    _need(
        canonical(control.document(actual))
        == canonical(control.document(state["source"].control_state))
        and event == state["source"].control_event_sha256
    )
    local = ledger.QualificationLedgerRepository(
        factory,
        clock=lambda: utc_from_ns(
            source._check(state["source"].source_state)["utc_ns"]
        ),
    )
    row = await local._locked(session, view.context.scope)
    _check(binding)
    before = await repository._read_metadata(session, binding)
    scope_json = repository._row(
        {name: getattr(row, name) for name in repository._SCOPE_NAMES},
        repository._SCOPE_NAMES,
    )
    _need(scope_json == state["scope_json"])
    actual_rows = await repository._actual_rows(session, binding, state["intent_id"])
    _need(
        actual_rows
        == (
            state["intent_row_json"],
            state["attempt_row_json"],
            state["result_row_json"],
        )
    )
    ids = (
        (
            await session.execute(
                text(
                    "SELECT intent_id FROM public.qualification_leverage_configuration_intents_v1 WHERE environment=:e AND account_id=:a AND phase IN ('prepared','attempted','verified','unknown') ORDER BY intent_id FOR UPDATE"
                ),
                {
                    "e": view.context.scope.environment,
                    "a": view.context.scope.account_id,
                },
            )
        )
        .scalars()
        .all()
    )
    _check(binding)
    _need(tuple(ids) == (state["intent_id"],))
    checkpoint = await local._read_portfolio_checkpoint_locked(
        session, view.context.scope, row
    )
    _check(binding)
    _need(checkpoint.known_flat is True)
    connection = await session.connection()
    _check(binding)
    metadata = await repository._read_metadata(session, binding)
    _need(
        before[:9] == metadata[:9]
        and before[9] <= metadata[9] < view.context.original_expires_at
    )
    previous = state["lock"]
    if previous is not None:
        _need(
            previous.session is session
            and previous.session_transaction is transaction
            and previous.connection is connection.sync_connection
            and previous.root_transaction
            is connection.sync_connection.get_transaction()
            and previous.database_metadata[:9] == metadata[:9]
            and previous.database_metadata[9] <= metadata[9]
        )
    locked = contracts.NativeLeverageConfigurationLockViewV1(
        binding,
        view.context,
        session,
        transaction,
        connection.sync_connection,
        connection.sync_connection.get_transaction(),
        metadata,
        actual,
        event,
        scope_json,
        actual_rows[0],
        actual_rows[1],
        checkpoint.document_json,
    )
    state["lock"] = locked
    _check_native_leverage_configuration_physical_v1(binding, locked, key=key)
    return locked


def _check_native_leverage_configuration_physical_v1(binding, locked, *, key):
    _root_key(key)
    state = _check(binding)
    _need(
        type(locked) is contracts.NativeLeverageConfigurationLockViewV1
        and state["lock"] is locked
        and locked.binding is binding
        and locked.context is state["view"].context
    )
    session, transaction, connection, root = (
        locked.session,
        locked.session_transaction,
        locked.connection,
        locked.root_transaction,
    )
    engine = state["view"].context.session_factory.kw["bind"].sync_engine
    _need(
        type(session) is AsyncSession
        and session.in_transaction()
        and not session.in_nested_transaction()
        and session.get_bind() is engine
        and session.sync_session.get_transaction() is transaction
        and transaction is not None
        and transaction.is_active
        and transaction.parent is None
        and transaction.nested is False
        and type(connection) is Connection
        and connection.engine is engine
        and connection.closed is False
        and connection.invalidated is False
        and connection.in_nested_transaction() is False
        and connection.get_transaction() is root
        and type(root) is RootTransaction
        and root.is_active
        and root.connection is connection
    )
    _need(
        locked.scope_json == state["scope_json"]
        and locked.intent_row_json == state["intent_row_json"]
        and locked.attempt_row_json == state["attempt_row_json"]
        and locked.control_event_sha256 == state["source"].control_event_sha256
    )
    return state["view"]


def _retain_native_leverage_configuration_transport_buffers_v1(
    binding, buffers, *, key
):
    _root_key(key)
    state = _original_state(binding)
    _need(
        type(buffers) is tuple and all(type(item) in (bytes, str) for item in buffers)
    )
    retained = {id(value): value for value in state["buffers"]}
    for value in buffers:
        retained.setdefault(id(value), value)
    state["buffers"] = tuple(retained.values())
    source._budget(state["source"].source_state)


def _retained_native_leverage_configuration_buffers_v1(generation, *, key):
    _need(key is source._source()._SOURCE_KEY_V6 and type(generation) is object)
    values = []
    for state in _STATES.values():
        if state["generation"] is generation:
            values.extend(
                raw
                for name in (
                    "request_json",
                    "plan_json",
                    "scope_json",
                    "intent_row_json",
                    "attempt_row_json",
                    "result_row_json",
                    "pending_event_json",
                )
                if type(raw := state.get(name)) in (bytes, str)
            )
            values.extend(state["buffers"])
            values.extend(
                repository._retained_configuration_repository_buffers_v1(
                    state["binding"], key=_REPOSITORY_KEY_V1
                )
            )
            transport = state.get("transport")
            if transport is not None:
                values.append(transport["credential_document"])
                values.extend(transport["received_buffers"])
                if transport["prepared_signature"] is not None:
                    values.append(transport["prepared_signature"])
    return tuple(values)


def _inhibit_native_leverage_configuration_v1(binding, *, key):
    _root_key(key)
    state = _original_state(binding)
    _unknown(state)
    return source._inhibit_flat_leverage_source_v1(
        state["source"].binding, key=_SOURCE_KEY_V1
    )


_RESULTS = {}
_RESULT_DATA = {}
_COMMITTED = {}
_CALLER_COMPLETIONS = {}


class _VerifiedConfigurationV1(_OpaqueConfigurationV1):
    __slots__ = ()


class _ConfigurationCleanupV1(_OpaqueConfigurationV1):
    __slots__ = ()


class _ConfigurationCommittedV1(_OpaqueConfigurationV1):
    __slots__ = ()


@dataclass(frozen=True, slots=True, repr=False)
class _ConfigurationCleanupInputV1:
    source_view: source.FlatLeverageSourceViewV1
    closed_handle: object
    closed_view: object
    readback: object
    scope_json: bytes
    intent_id: str
    result_receipt_json: bytes
    kind: str


def _negative_stage(state, phase, seconds):
    _need(type(seconds) is int and seconds in (5, 10))
    stamp = source._source()._stamp_v6()
    state["negative_budget"] = {
        "started": stamp,
        "last": stamp,
        "budget_ns": seconds * 1_000_000_000,
    }
    state["phase"] = phase


def _negative_state(binding):
    state = _original_state(binding)
    _need(state.get("negative_budget") is not None)
    source._source()._REQUIRE_SOURCE_KERNEL_V6()
    if state.get("source_retirement") is not None:
        source._retired_flat_configuration_v1(
            state["source_retirement"], key=_SOURCE_KEY_V1
        )
    else:
        source._negative_context_v1(state["source"].binding, key=_SOURCE_KEY_V1)
    source._negative_window_v1(state["negative_budget"])
    if state.get("response") is not None and state.get("root_retired_receipt") is None:
        from app.exchange.okx import native_demo_write_v2 as root

        _need(
            root._replay_native_leverage_configuration_response_v1(
                state["response"].response_handle, key=_ROOT_RESPONSE_KEY_V1
            )
            is state["response"]
        )
    if (
        state.get("root_retired_receipt") is not None
        and state.get("transport") is not None
    ):
        _need(
            state["transport"]["retired_root_receipt"] is state["root_retired_receipt"]
            and state["transport"]["root_phase"] == "RETIRED"
            and state["transport"]["cleanup_phase"] == "RETIRED"
        )
    return state


def _source_scan(state, raw):
    _need(type(raw) is bytes and 0 < len(raw) <= 320000)
    if state.get("source_retirement") is not None:
        source.scan_flat_leverage_retired_data_v1(
            state["source_retirement"], raw, key=_SOURCE_KEY_V1
        )
    else:
        source._budget(state["source"].source_state, extra=(raw,))
        source._source()._scan_reconciliation_complete_document_v6(
            raw, source._scan_tokens(state["source"].source_state)
        )


def _response_receipt_v1(state, view):
    from app.exchange.okx import native_demo_transport_v2 as transport

    journal = view.received_journal
    _need(
        type(view) is contracts.NativeLeverageConfigurationResponseViewV1
        and view.binding is state["binding"]
        and view.transport_view is state["view"]
        and view.transport_state is state["transport"]
        and view.observed is state["transport"]["observed"]
        and type(journal) is transport._ResponseDataJournalV2
    )
    _need(
        type(journal.status) is int
        and journal.status == 200
        and journal.eof is True
        and journal.overflow is False
        and type(journal.faults) is list
        and len(journal.faults) == 0
        and type(journal.body_chunks) is list
        and all(type(raw) is bytes for raw in journal.body_chunks)
        and type(journal.body_bytes) is int
        and 0
        < journal.body_bytes
        == sum(len(raw) for raw in journal.body_chunks)
        <= 65536
    )
    raw = journal.body_raw
    _need(type(raw) is bytes and len(raw) == journal.body_bytes)
    value = decode(raw, 65536)
    _need(
        type(value) is dict
        and type(value.get("code")) is str
        and value["code"] == "0"
        and type(value.get("data")) is list
        and len(value["data"]) == 1
    )
    member, request = value["data"][0], decode(state["request_json"])
    _need(
        type(member) is dict
        and type(member.get("lever")) is str
        and member["lever"] == request["lever"]
        and member.get("mgnMode") == "isolated"
        and member.get("instId") == request["instId"]
        and ("sCode" not in member or member["sCode"] == "0")
    )
    if "posSide" in request:
        _need(member.get("posSide") == request["posSide"])
    else:
        _need(
            "posSide" not in member
            or (type(member["posSide"]) is str and member["posSide"] in ("", "net"))
        )
    _need(
        type(journal.headers) is tuple
        and len(journal.headers) <= 256
        and all(
            type(row) is tuple
            and len(row) == 2
            and all(type(item) is bytes for item in row)
            for row in journal.headers
        )
    )
    receipt = canonical(
        {
            "schema_version": "ctcc.native_leverage_configuration.response.v1",
            "intent_id": state["intent_id"],
            "request_sha256": sha(state["request_json"]),
            "http_status": journal.status,
            "body_json": raw.decode("utf-8"),
            "body_sha256": sha(raw),
            "headers": [
                [name.decode("ascii"), val.decode("latin1")]
                for name, val in journal.headers
            ],
            "eof": journal.eof,
            "overflow": journal.overflow,
            "faults": list(journal.faults),
            "execution_authority": False,
            "order_retry_authority": False,
        }
    )
    _need(len(receipt) <= 65536)
    _source_scan(state, receipt)
    return receipt


def _result_document_v1(state, classification, extras):
    local = decode(state["scope_json"])
    body = {
        "schema_version": "ctcc.native_leverage_configuration.result.v1",
        "intent_id": state["intent_id"],
        "attempt_id": state["attempt_id"],
        "request_sha256": sha(state["request_json"]),
        "classification": classification,
        "account_revision": local["account_revision"],
        "previous_ledger_revision": local["ledger_revision"],
        "ledger_revision": local["ledger_revision"] + 1,
        "result_sequence": state["result_sequence"] + 1,
        "previous_phase": state["database_phase"],
        "execution_authority": False,
        "order_retry_authority": False,
    }
    _need(type(extras) is dict and not (set(extras) & set(body)))
    body.update(extras)
    raw = canonical(body)
    _need(0 < len(raw) <= 262144)
    state["pending_event_json"] = raw
    _source_scan(state, raw)
    return raw


async def _append_result_v1(state, raw):
    cap = await repository.append_configuration_result_v1(
        state["binding"], raw, key=_REPOSITORY_KEY_V1
    )
    state["commits"].append(cap)
    _negative_state(state["binding"])
    readback = await repository.read_configuration_commit_v1(
        cap, key=_REPOSITORY_KEY_V1
    )
    _negative_state(state["binding"])
    binding, actual = repository._replay_configuration_readback_v1(
        readback, key=_REPOSITORY_KEY_V1
    )
    _need(binding is state["binding"] and actual is readback)
    state["readbacks"].append(readback)
    state["scope_json"] = readback.scope_json
    state["intent_row_json"], state["attempt_row_json"], state["result_row_json"] = (
        readback.intent_row_json,
        readback.attempt_row_json,
        readback.result_row_json,
    )
    value = decode(readback.intent_row_json, 2097152)
    state["database_phase"], state["result_sequence"] = (
        value["phase"],
        value["latest_result_sequence"],
    )
    _source_scan(state, readback.result_row_json)
    return readback


def reconcile_owned_native_leverage_configuration_v1(observed):
    from app.exchange.okx import native_demo_write_v2 as root

    response = root._take_native_leverage_configuration_response_v1(
        observed, key=_ROOT_RESPONSE_KEY_V1
    )
    state = _original_state(response.binding)
    _need(state["transport"]["observed"] is observed and state.get("response") is None)
    state["response"] = response
    _negative_stage(state, "RECONCILING", 10)
    return _reconcile_configuration_v1(state)


async def _reconcile_configuration_v1(state):
    from app.exchange.okx import native_demo_write_v2 as root

    try:
        _negative_state(state["binding"])
        state["root_response_receipt_json"] = _response_receipt_v1(
            state, state["response"]
        )
        state["readback"] = await source.acquire_flat_leverage_readback_v1(
            state["source"].binding, key=_SOURCE_KEY_V1
        )
        _negative_state(state["binding"])
        _need(
            source._replay_flat_leverage_readback_v1(
                state["readback"], key=_SOURCE_KEY_V1
            )
            is state["readback"]
        )
        _need(len(state["readback"].receipt_json) <= 65536)
        closed = root._observe_native_closed_leverage_configuration_v1(
            state["response"].response_handle, key=_ROOT_SETTLEMENT_KEY_V1
        )
        _need(
            closed is not None, "native_leverage_configuration_physical_cleanup_unknown"
        )
        closed_view = root._take_native_closed_leverage_configuration_v1(
            closed, key=_ROOT_SETTLEMENT_KEY_V1
        )
        _need(
            type(closed_view) is contracts.NativeLeverageConfigurationClosedViewV1
            and closed_view.binding is state["binding"]
            and closed_view.response_view is state["response"]
            and closed_view.closed_handle is closed
        )
        state["closed_handle"], state["closed_view"] = closed, closed_view
        _negative_stage(state, "PERSISTING_VERIFIED", 5)
        verified = _result_document_v1(
            state,
            "verified",
            {
                "root_response_receipt_json": state[
                    "root_response_receipt_json"
                ].decode("ascii"),
                "signed_readback_json": state["readback"].receipt_json.decode("ascii"),
            },
        )
        state["verified_readback"] = await _append_result_v1(state, verified)
        _need(state["database_phase"] == "verified")
        state["verified_receipt_json"] = verified
        result_cap = object.__new__(_VerifiedConfigurationV1)
        data = contracts.NativeLeverageConfigurationResultV1(
            state["intent_id"], sha(verified), sha(verified), "verified"
        )
        state["result_cap"], state["verified_data"] = result_cap, data
        _RESULTS[result_cap] = state
        return await _finish_configuration_v1(state)
    except BaseException:
        _unknown(state)
        raise


def complete_owned_no_change_configuration_v1(owner):
    state = (
        _NO_CHANGE.pop(owner, None)
        if type(owner) is OwnedNativeNoChangeConfigurationV1
        else None
    )
    _need(
        state is not None
        and state["owner"] is owner
        and state["database_phase"] == "prepared"
        and state["attempt_id"] is None
        and state["attempt_row_json"] is None
        and state["source"].plan.selected_leverage
        == state["source"].plan.configured_leverage
    )
    _negative_stage(state, "NO_CHANGE_CLEANUP", 5)
    state["closed_handle"], state["closed_view"], state["readback"] = None, None, None
    state["result_cap"], state["verified_receipt_json"] = (
        owner,
        decode(state["intent_row_json"], 2097152)["core_json"].encode("ascii"),
    )
    _RESULTS[owner] = state
    return _finish_configuration_v1(state)


def _replay_configuration_cleanup_input_for_source_v1(cap, *, key):
    _need(key is source._SOURCE_KEY_V1)
    state = _RESULTS.get(cap)
    _need(state is not None and state["result_cap"] is cap)
    _negative_state(state["binding"])
    kind = (
        "NO_CHANGE" if type(cap) is OwnedNativeNoChangeConfigurationV1 else "VERIFIED"
    )
    _need(
        (
            kind == "NO_CHANGE"
            and state["database_phase"] == "prepared"
            and state["attempt_id"] is None
        )
        or (
            type(cap) is _VerifiedConfigurationV1
            and state["database_phase"] == "verified"
            and repository._replay_configuration_readback_v1(
                state["verified_readback"], key=_REPOSITORY_KEY_V1
            )[1]
            is state["verified_readback"]
        )
    )
    return _ConfigurationCleanupInputV1(
        state["source"],
        state["closed_handle"],
        state["closed_view"],
        state["readback"],
        state["scope_json"],
        state["intent_id"],
        state["verified_receipt_json"],
        kind,
    )


def _take_native_leverage_configuration_cleanup_for_root_v1(cap, *, key):
    _root_key(key)
    state = _CLEANUPS.pop(cap, None) if type(cap) is _ConfigurationCleanupV1 else None
    _need(
        state is not None
        and state["cleanup_cap"] is cap
        and type(state["result_cap"]) is _VerifiedConfigurationV1
    )
    _negative_state(state["binding"])
    source_view = source._replay_flat_leverage_source_cleanup_v1(
        state["source_completion"], key=_SOURCE_KEY_V1
    )
    _need(
        source_view is state["source_completion_view"]
        and source_view.result_cap is state["result_cap"]
        and source_view.root_closed_handle is state["closed_handle"]
        and state["verified_data"].classification == "verified"
    )
    return state["cleanup_view"]


def _replay_configuration_root_finalization_for_source_v1(cap, *, key):
    _need(key is source._SOURCE_KEY_V1)
    state = _RESULTS.get(cap)
    _need(
        state is not None
        and state["result_cap"] is cap
        and state.get("root_finalized_receipt") is not None
    )
    _negative_state(state["binding"])
    return state["root_finalized_receipt"]


def _replay_native_leverage_configuration_retirement_for_root_v1(retirement, *, key):
    _root_key(key)
    state = _RETIREMENTS.get(retirement)
    _need(state is not None and state["source_retirement"] is retirement)
    _negative_state(state["binding"])
    raw = source._replay_flat_leverage_retirement_v1(retirement, key=_SOURCE_KEY_V1)
    _need(raw == state["source_retirement_receipt_json"])
    return contracts.NativeLeverageConfigurationRetirementViewV1(
        state["binding"],
        state["closed_handle"],
        state["cleanup_cap"],
        retirement,
        state["verified_receipt_json"],
        raw,
    )


async def _finish_configuration_v1(state):
    from app.exchange.okx import native_demo_write_v2 as root

    try:
        _negative_stage(state, "CLEANING_SOURCE", 5)
        completed = await source.prepare_flat_leverage_source_cleanup_v1(
            state["result_cap"], key=_SOURCE_KEY_V1
        )
        state["source_completion"] = completed
        state["source_completion_view"] = (
            source._replay_flat_leverage_source_cleanup_v1(
                completed, key=_SOURCE_KEY_V1
            )
        )
        if state["closed_handle"] is not None:
            cleanup = object.__new__(_ConfigurationCleanupV1)
            state["cleanup_cap"] = cleanup
            state["cleanup_view"] = contracts.NativeLeverageConfigurationCleanupViewV1(
                state["binding"],
                state["closed_handle"],
                state["result_cap"],
                state["verified_data"],
                completed,
                state["source_completion_view"],
                state["verified_receipt_json"],
                state["source_completion_view"].receipt_json,
                state["generation"],
                state["view"],
            )
            _CLEANUPS[cleanup] = state
            state["root_finalized_receipt"] = (
                root._finalize_native_leverage_configuration_cleanup_v1(
                    state["closed_handle"], cleanup, key=_ROOT_SETTLEMENT_KEY_V1
                )
            )
        else:
            state["cleanup_cap"] = None
            state["root_finalized_receipt"] = (
                "ctcc.native_leverage_configuration.root_not_created.v1",
                state["intent_id"],
                sha(state["verified_receipt_json"]),
                sha(state["source_completion_view"].receipt_json),
            )
        retirement = source.retire_flat_leverage_source_v1(
            completed, key=_SOURCE_KEY_V1
        )
        state["source_retirement"] = retirement
        state["source_retirement_receipt_json"] = (
            source._replay_flat_leverage_retirement_v1(retirement, key=_SOURCE_KEY_V1)
        )
        _RETIREMENTS[retirement] = state
        if state["closed_handle"] is not None:
            state["root_retired_receipt"] = (
                root._retire_native_leverage_configuration_after_source_v1(
                    state["closed_handle"], retirement, key=_ROOT_SETTLEMENT_KEY_V1
                )
            )
        else:
            state["root_retired_receipt"] = (
                "ctcc.native_leverage_configuration.root_not_created.v1",
                state["intent_id"],
                sha(state["verified_receipt_json"]),
                sha(state["source_retirement_receipt_json"]),
            )
        _negative_stage(state, "FINAL_NEGATIVE_PERSISTENCE", 5)
        classification = (
            "settled" if state["closed_handle"] is not None else "no_change"
        )
        final = _result_document_v1(
            state,
            classification,
            {
                "source_cleanup_receipt_json": state[
                    "source_completion_view"
                ].receipt_json.decode("ascii"),
                "source_retirement_receipt_json": state[
                    "source_retirement_receipt_json"
                ].decode("ascii"),
                "root_cleanup_receipt_json": canonical(
                    state["root_retired_receipt"]
                ).decode("ascii"),
                "root_finalized_receipt": state["root_finalized_receipt"],
                "root_retirement_receipt": state["root_retired_receipt"],
                "verified_result_id": None
                if classification == "no_change"
                else sha(state["verified_receipt_json"]),
            },
        )
        state["final_readback"] = await _append_result_v1(state, final)
        _need(state["database_phase"] == classification)
        committed = object.__new__(_ConfigurationCommittedV1)
        state["committed"] = committed
        _COMMITTED[committed] = state
        # Actual full-row new-XID proof, not classification/receipt flags,
        # authorizes Source/facade and then repository/RAM compaction.
        compacted = source.compact_flat_leverage_source_retirement_v1(
            retirement, committed, key=_SOURCE_KEY_V1
        )
        public_owner = state["controller_cap"]
        _need(public_owner not in source.facade._HANDLES)
        data = contracts.NativeLeverageConfigurationResultV1(
            state["intent_id"], sha(final), sha(final), classification
        )
        tombstone = (
            data,
            public_owner,
            compacted,
            state["root_finalized_receipt"],
            state["root_retired_receipt"],
        )
        repository._compact_configuration_commits_v1(
            state["binding"],
            tuple(state["commits"]),
            tuple(state["readbacks"]),
            key=_REPOSITORY_KEY_V1,
        )
        _CALLER_COMPLETIONS[id(data)] = tombstone
        _RESULT_DATA[id(data)] = data
        _RESULTS.pop(state["result_cap"])
        _RETIREMENTS.pop(retirement)
        _COMMITTED.pop(committed)
        _STATES.pop(state["binding"])
        _UNKNOWN.pop(state["binding"], None)
        transport = state.get("transport")
        if transport is not None:
            transport.update(
                root_record=None,
                credential_document=b"",
                received_buffers=(),
                prepared_signature=None,
                response_handle=None,
                observed=None,
                owner=None,
                view=None,
                context=None,
            )
        state.clear()
        return data
    except BaseException:
        _unknown(state)
        raise


def _take_committed_configuration_for_source_v1(cap, *, key):
    _need(key is source._SOURCE_KEY_V1)
    state = _COMMITTED.get(cap) if type(cap) is _ConfigurationCommittedV1 else None
    _need(state is not None and state["committed"] is cap)
    _negative_state(state["binding"])
    actual_binding, readback = repository._replay_configuration_readback_v1(
        state["final_readback"], key=_REPOSITORY_KEY_V1
    )
    _need(
        actual_binding is state["binding"]
        and readback is state["final_readback"]
        and readback.read_metadata[8] != readback.write_metadata[8]
        and readback.write_metadata[9] <= readback.read_metadata[9]
    )
    return (
        state["source_retirement"],
        readback.result_row_json,
        readback.read_metadata,
        readback.scope_json,
    )


def execute_owned_native_leverage_configuration_v1(owner):
    """One same-task action; no order/Arm continuation or configuration retry."""
    if type(owner) is OwnedNativeNoChangeConfigurationV1:
        return complete_owned_no_change_configuration_v1(owner)
    state = (
        _OWNERS.get(owner)
        if type(owner) is OwnedNativeLeverageConfigurationIntentV1
        else None
    )
    _need(
        state is not None
        and state["owner"] is owner
        and state.get("execution_started") is None
    )
    _check(state["binding"])
    state["execution_started"] = object()
    return _execute_configuration_v1(state, owner)


async def _execute_configuration_v1(state, owner):
    from app.exchange.okx import native_demo_write_v2 as root

    try:
        try:
            observed = await root.submit_owned_native_demo_leverage_configuration_v1(
                owner
            )
        except asyncio.CancelledError:
            _unknown(state)
            raise
        except Exception:  # noqa: BLE001 -- Reconcile only retained exchange evidence after transport failure.
            observed = state["transport"]["observed"]
            if observed is None:
                _unknown(state)
                raise NativeLeverageConfigurationError(
                    "native_leverage_configuration_outcome_unknown"
                ) from None
        return await reconcile_owned_native_leverage_configuration_v1(observed)
    except BaseException:
        if type(state) is dict and state.get("binding") is not None:
            _unknown(state)
            try:
                await _inhibit_native_leverage_configuration_v1(
                    state["binding"], key=root._LEVERAGE_CONFIGURATION_KEY_V1
                )
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001, S110 -- Original state and SQL interlock remain unknown.
                pass  # Full original state and SQL interlock remain UNKNOWN.
        raise


def _take_native_leverage_configuration_caller_completion_v1(value, controller, *, key):
    from app.demo_automation import native_caller_v2 as caller

    _need(key is caller._CONFIGURATION_COMPLETION_KEY_V1)
    record = _CALLER_COMPLETIONS.pop(id(value), None)
    _need(
        record is not None
        and record[0] is value
        and record[1] is controller
        and _RESULT_DATA.get(id(value)) is value
        and controller not in source.facade._HANDLES
    )
    _RESULT_DATA.pop(id(value))
    return record[2]


# Same existing source-compiled continuation wrapper, installed by the genuine
# Source bootstrap before any GET lease/Arm. No caller supplies this table.
def _install_configuration_kernel_v1():
    return continuation.install_native_guards_v1(
        sys.modules[__name__],
        (
            "issue_owned_native_leverage_configuration_v1",
            "_replay_native_leverage_configuration_repository_v1",
            "_take_native_leverage_configuration_for_transport_v1",
            "_native_leverage_configuration_transport_state_v1",
            "_replay_native_leverage_configuration_transport_v1",
            "_lock_native_leverage_configuration_v1",
            "_check_native_leverage_configuration_physical_v1",
            "_retain_native_leverage_configuration_transport_buffers_v1",
            "_inhibit_native_leverage_configuration_v1",
            "_retained_native_leverage_configuration_buffers_v1",
            "execute_owned_native_leverage_configuration_v1",
            "reconcile_owned_native_leverage_configuration_v1",
            "complete_owned_no_change_configuration_v1",
            "_replay_configuration_cleanup_input_for_source_v1",
            "_take_native_leverage_configuration_cleanup_for_root_v1",
            "_replay_configuration_root_finalization_for_source_v1",
            "_replay_native_leverage_configuration_retirement_for_root_v1",
            "_take_committed_configuration_for_source_v1",
            "_take_native_leverage_configuration_caller_completion_v1",
        ),
        dependencies=(
            source,
            source.planner,
            repository,
            repository.schema,
            contracts,
            ledger,
            account,
            private_rest,
        ),
    )


_NATIVE_GUARD = _install_configuration_kernel_v1()
