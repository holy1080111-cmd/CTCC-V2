"""Owned POST transport closure after genuine committed zero-FOK reconciliation.

This observes closure already initiated by the original request scope. It never
submits, retries, flushes TLS, invents a close time or releases a database hold.
The separate source/ledger completion witness is required before RAM retirement.
"""

from __future__ import annotations

import _socket
import asyncio
import os
import socket
import threading
from dataclasses import dataclass, field
from types import FunctionType, ModuleType

from app.domain.native_clock import native_stamp
from app.trade_qualification import native_continuation_guard_v1 as guard

_SOURCE_KEY_V1 = object()
_HANDLE_KEY_V1 = object()
_CLOSED = {}
_UNKNOWN = {}
_MAX = 16
_SOCKET_CLASS = socket.socket
_RAW_SOCKET_FILENO = _socket.socket.fileno
_API_NAMES = (
    "_take_native_flat_result_for_root_settlement_v1",
    "_replay_native_flat_result_for_root_settlement_v1",
    "_take_native_flat_cleanup_completion_for_root_v1",
)


class NativeTransportSettlementError(ValueError):
    pass


def _need(value, code="native_demo_transport_settlement_unconfirmed"):
    if value is not True:
        raise NativeTransportSettlementError(code) from None


@dataclass(frozen=True, slots=True, repr=False)
class NativeSocketOwnersV1:
    backend: object = field(repr=False)
    network_stream: object = field(repr=False)
    socket_stream: object = field(repr=False)
    native_transport: object = field(repr=False)
    raw_socket: object = field(repr=False)


@dataclass(frozen=True, slots=True, repr=False)
class NativeClosedTransportViewV1:
    flat_result_view: object = field(repr=False)
    response_handle: object = field(repr=False)
    observed_result: object = field(repr=False)
    closure_outcome_id: str = field(repr=False)
    closure_receipt_sha256: str = field(repr=False)
    socket_observation: tuple = field(repr=False)
    closed_transport_handle: object = field(repr=False)
    execution_authority: bool = field(default=False, init=False)
    order_retry_authority: bool = field(default=False, init=False)


class OwnedNativeClosedTransportV1:
    __slots__ = ()

    def __new__(cls, *, key=None):
        _need(
            cls is OwnedNativeClosedTransportV1 and key is _HANDLE_KEY_V1,
            "native_demo_closed_transport_not_constructible",
        )
        return object.__new__(cls)

    def __reduce_ex__(self, protocol):
        raise TypeError("native_demo_closed_transport_not_serializable")

    def __copy__(self):
        raise TypeError("native_demo_closed_transport_not_copyable")

    def __deepcopy__(self, memo):
        raise TypeError("native_demo_closed_transport_not_copyable")


def capture_postsubmit_api_v1(record):
    """Called only at actual locked gate creation, after genuine source replay."""
    from app.exchange.okx import native_demo_write_v2 as write
    from app.trade_qualification import native_postsubmit_reconciliation_v1 as post

    _need(
        type(record) is dict
        and write._GATES.get(record["gate"]) is record
        and type(post) is ModuleType
    )
    write._live_facts(record)
    post._NATIVE_GUARD()
    functions = []
    for name in _API_NAMES:
        fn = vars(post).get(name)
        _need(
            type(fn) is FunctionType, "native_demo_transport_settlement_source_missing"
        )
        # Native exports are guarded wrappers defined in the guard module;
        # their genuine globals are not the postsubmit module dictionary.
        # Authenticate the actual defining source/code/namespace and closure
        # contents instead of trusting a claimed __module__ or a clone.
        pin = guard._function_pin(fn, {})
        guard._check_function(pin)
        functions.append((name, fn, pin))
    saved = {}
    guard.save_native_guard_v1(saved, post._NATIVE_GUARD)
    record["settlement_api"] = (
        post,
        vars(post).get("NativeReconciliationResultV1"),
        vars(post).get("NativeFlatResultForRootSettlementV1"),
        vars(post).get("_ROOT_TRANSPORT_SETTLEMENT_KEY_V1"),
        tuple(functions),
        saved,
    )
    record["native_socket_owners"] = None
    record["cleanup_corrupt_socket"] = None


def _api(record):
    from app.exchange.okx import native_demo_write_v2 as write

    write._NATIVE_GUARD()
    actual = record["settlement_api"]
    _need(type(actual) is tuple and len(actual) == 6)
    post, result_type, view_type, key, functions, saved = actual
    _need(type(post) is ModuleType)
    # Authenticate the captured original namespace before any module lookup.
    # vars() also prevents a missing class/key from invoking __getattr__.
    guard.check_saved_native_guard_v1(saved)
    _need(
        vars(post).get("NativeReconciliationResultV1") is result_type
        and vars(post).get("NativeFlatResultForRootSettlementV1") is view_type
        and vars(post).get("_ROOT_TRANSPORT_SETTLEMENT_KEY_V1") is key
    )
    for name, fn, pin in functions:
        _need(vars(post).get(name) is fn)
        guard._check_function(pin)
    return actual


def retain_native_socket_owners_v1(gate, backend, native, *, key):
    """Retain actual exposed TCP owners BEFORE TLS, including late-expiry data."""
    from app.exchange.okx import native_demo_transport_v2 as transport
    from app.exchange.okx import native_demo_write_v2 as write

    _need(key is transport._TRANSPORT_KEY)
    record = write._received_record_v2(gate, key)
    write._NATIVE_GUARD()
    write._require_vendor_state_v2()
    owned = record["transport"]
    _need(
        type(owned) is transport._Transport
        and type(backend) is transport._Backend
        and owned._backend is backend
        and backend._gate is gate
        and backend._native is native
        and type(native) is transport.AnyIOStream
        and type(native._stream) is transport.SocketStream
        and record["native_socket_owners"] is None
    )
    stream = native._stream
    underlying = stream._transport
    _need(
        type(underlying)
        in (transport._ProactorSocketTransport, transport._SelectorSocketTransport)
    )
    members = object.__getattribute__(underlying, "__dict__")
    _need(type(members) is dict and type(members.get("_extra")) is dict)
    raw = members.get("_sock")
    if raw is None:
        # The transport's original standard-library wrapper retains the actual
        # socket even if a peer-close callback ran before this task resumed.
        from asyncio.trsock import TransportSocket

        exposed = members["_extra"].get("socket")
        _need(type(exposed) is TransportSocket)
        raw = object.__getattribute__(exposed, "_sock")
    _need(type(raw) is _SOCKET_CLASS and _socket.socket.fileno is _RAW_SOCKET_FILENO)
    record["native_socket_owners"] = NativeSocketOwnersV1(
        backend, native, stream, underlying, raw
    )


def _socket_closed(record):
    value = record["native_socket_owners"]
    _need(
        type(value) is NativeSocketOwnersV1
        and type(value.raw_socket) is _SOCKET_CLASS
        and _socket.socket.fileno is _RAW_SOCKET_FILENO
        and record["transport"]._backend is value.backend
        and value.backend._native is value.network_stream
        and value.network_stream._stream is value.socket_stream
        and type(record["cleanup_unknown_sockets"]) is list
        and not record["cleanup_unknown_sockets"]
        and record["cleanup_corrupt_socket"] is None
    )
    return _closed_socket_data_v1(value.native_transport, value.raw_socket)


def _closed_socket_data_v1(native, raw_socket):
    _need(
        type(raw_socket) is _SOCKET_CLASS
        and _socket.socket.fileno is _RAW_SOCKET_FILENO
        and native is not None
    )
    from app.exchange.okx import native_demo_transport_v2 as transport

    _need(
        type(native)
        in (transport._ProactorSocketTransport, transport._SelectorSocketTransport)
    )
    members = object.__getattribute__(native, "__dict__")
    _need(
        type(members) is dict
        and "_sock" in members
        and "_closing" in members
        and "_server" in members
    )
    if _RAW_SOCKET_FILENO(raw_socket) != -1 or members["_sock"] is not None:
        return False
    _need(members["_closing"] is True and members["_server"] is None)
    if type(native) is transport._ProactorSocketTransport:
        _need(
            all(
                name in members
                for name in (
                    "_called_connection_lost",
                    "_read_fut",
                    "_write_fut",
                    "_pending_write",
                    "_buffer",
                )
            )
            and type(members["_called_connection_lost"]) is bool
            and type(members["_pending_write"]) is int
        )
        return (
            members["_called_connection_lost"] is True
            and members["_read_fut"] is None
            and members["_write_fut"] is None
            and members["_pending_write"] == 0
            and members["_buffer"] is None
        )
    _need(type(native) is transport._SelectorSocketTransport)
    _need(
        all(
            name in members
            for name in ("_protocol", "_loop", "_write_ready", "_buffer")
        )
    )
    return (
        members["_protocol"] is None
        and members["_loop"] is None
        and members["_write_ready"] is None
        and not members["_buffer"]
    )


def _sdk_closed(record):
    return _closed_sdk_data_v1(record["httpx_client"], record["transport"])


def _closed_sdk_data_v1(client, owned):
    import httpcore
    import httpx
    from httpx._client import ClientState

    from app.exchange.okx import native_demo_transport_v2 as transport

    _need(
        type(client) is httpx.AsyncClient
        and type(owned) in (transport._Transport, httpx.AsyncHTTPTransport)
    )
    values = object.__getattribute__(client, "__dict__")
    _need(
        type(values) is dict
        and values.get("_transport") is owned
        and type(values.get("_mounts")) is dict
        and not values["_mounts"]
        and "_state" in values
        and type(values["_state"]) is ClientState
    )
    pool = owned._pool
    _need(type(pool) is httpcore.AsyncConnectionPool)
    state = object.__getattribute__(pool, "__dict__")
    _need(
        type(state) is dict
        and type(state.get("_connections")) is list
        and type(state.get("_requests")) is list
    )
    return (
        values.get("_state") is ClientState.CLOSED
        and not state["_connections"]
        and not state["_requests"]
    )


def observe_native_closed_httpx_tcp_data_v1(
    client, sdk_transport, native_transport, raw_socket
):
    """Pure DATA over actual objects; never a source, hold or release authority.

    The calling source owner must independently authenticate these exact object
    identities. Missing/unexposed owners raise unknown rather than prove closed.
    """
    if not (
        _closed_socket_data_v1(native_transport, raw_socket)
        and _closed_sdk_data_v1(client, sdk_transport)
    ):
        return None
    stamp = native_stamp()
    return (
        "ctcc.native_socket_closed_observation.v1",
        stamp["utc_ns"],
        stamp["monotonic_ns"],
        -1,
        "native_callback_tail_and_sdk_pool_closed",
    )


def _replay(state):
    from app.exchange.okx import native_demo_write_v2 as write

    _need(
        type(state) is dict
        and _UNKNOWN.get(state["handle"]) is state
        and state["pid"] == os.getpid()
        and state["thread"] == threading.get_ident()
        and state["task"] is asyncio.current_task()
        and state["loop"] is asyncio.get_running_loop()
    )
    record = state["record"]
    post, _, view_type, _, functions, _ = _api(record)
    replay = functions[1][1]
    _need(
        replay(state["flat"], key=_SOURCE_KEY_V1) is state["flat"]
        and type(state["flat"]) is view_type
    )
    actual = write._native_reconciliation_record_v1(
        record["gate"], key=post._SOURCE_KEY_V1
    )
    _need(
        actual is record
        and state["flat"].response_handle is record["gate"]
        and state["flat"].response_data is record["reconciliation_view"]
        and state["flat"].observed_result is record["observed_result"]
    )
    return record


async def close_owned_native_demo_transport_after_flat_v1(result):
    """Observe already-started close within5s; no close callback is retried."""
    from app.exchange.okx import native_demo_write_v2 as write

    write._NATIVE_GUARD()
    _need(len(_UNKNOWN) < _MAX)
    matches = []
    for record in write._GATES.values():
        if (
            type(record) is dict
            and record["task"] is asyncio.current_task()
            and "settlement_api" in record
            and type(result) is record["settlement_api"][1]
            and result.reservation_id
            == record["original_transport_facts"].consumed.reservation_id
        ):
            matches.append(record)
    _need(len(matches) == 1)
    record = matches[0]
    _, _, view_type, _, functions, _ = _api(record)
    flat = functions[0][1](result, key=_SOURCE_KEY_V1)
    _need(
        type(flat) is view_type
        and flat.result is result
        and flat.response_handle is record["gate"]
    )
    handle = OwnedNativeClosedTransportV1(key=_HANDLE_KEY_V1)
    started = native_stamp()
    state = {
        "handle": handle,
        "record": record,
        "flat": flat,
        "pid": os.getpid(),
        "thread": threading.get_ident(),
        "task": asyncio.current_task(),
        "loop": asyncio.get_running_loop(),
        "started": started,
        "last": started,
        "taken": False,
        "view": None,
    }
    _UNKNOWN[handle] = state
    try:
        async with asyncio.timeout(5):
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
                if _socket_closed(record) and _sdk_closed(record):
                    # Measured observation time; exact physical close time is
                    # unknown and is never replaced with this local timestamp.
                    observation = (
                        "ctcc.native_socket_closed_observation.v1",
                        stamp["utc_ns"],
                        stamp["monotonic_ns"],
                        -1,
                        "native_callback_tail_and_sdk_pool_closed",
                    )
                    view = NativeClosedTransportViewV1(
                        flat,
                        record["gate"],
                        record["observed_result"],
                        flat.closure_outcome_id,
                        flat.closure_receipt_sha256,
                        observation,
                        handle,
                    )
                    state["view"] = view
                    _CLOSED[handle] = state
                    return handle
                await asyncio.sleep(0.01)
    except BaseException as error:  # retain actual objects on unknown/cancellation.
        state["unconfirmed"] = True
        if isinstance(error, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
            raise
        raise NativeTransportSettlementError(
            "native_demo_transport_settlement_unconfirmed"
        ) from None


def _take_native_closed_transport_for_settlement_v1(handle, *, key):
    _need(type(handle) is OwnedNativeClosedTransportV1 and handle in _CLOSED)
    state = _CLOSED[handle]
    record = _replay(state)
    _need(key is record["settlement_api"][3] and state["taken"] is False)
    state["taken"] = True
    return state["view"]


def _replay_native_closed_transport_for_settlement_v1(handle, *, key):
    _need(type(handle) is OwnedNativeClosedTransportV1 and handle in _CLOSED)
    state = _CLOSED[handle]
    record = _replay(state)
    _need(
        key is record["settlement_api"][3]
        and state["taken"] is True
        and state["view"].closed_transport_handle is handle
    )
    return state["view"]


def _finalize_native_closed_transport_settlement_v1(handle, completion, *, key):
    from app.exchange.okx import native_demo_write_v2 as write

    view = _replay_native_closed_transport_for_settlement_v1(handle, key=key)
    state = _CLOSED[handle]
    record = state["record"]
    _, _, _, _, functions, _ = _api(record)
    completed = functions[2][1](completion, key=_SOURCE_KEY_V1)
    _need(
        completed.flat_result_view is view.flat_result_view
        and completed.closed_transport_handle is handle
        and _socket_closed(record)
        and _sdk_closed(record)
    )
    _need(
        write._GATES.get(record["gate"]) is record
        and write._CLIENT_GATES.get(record["client"]) is record["gate"]
    )
    # Durable events, intents, outcomes and accepted evidence remain intact.
    write._CLIENT_GATES.pop(record["client"])
    write._GATES.pop(record["gate"])
    _CLOSED.pop(handle)
    _UNKNOWN.pop(handle)
    return (
        "ctcc.native_transport_resources_finalized.v1",
        view.closure_outcome_id,
        view.closure_receipt_sha256,
    )
