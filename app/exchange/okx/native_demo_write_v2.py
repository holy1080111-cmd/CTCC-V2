"""Same-invocation Demo physical-write gate; source integration in progress.

No DTO, historical intent, caller settings, copied credential, or passed flag
creates a gate. Only the genuine native durable-intent registry and its real
locked PostgreSQL session can create and replay one. Legacy and Live routes
remain denied. This uninstalled module does not change those routes.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import ssl
import sys as _native_sys
import threading
from collections.abc import Coroutine
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import FunctionType
from urllib.parse import urlsplit
from weakref import WeakKeyDictionary

import h11
import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.native_clock import native_stamp
from app.domain.source_primitives import utc_from_ns
from app.exchange.okx import (
    native_demo_filled_settlement_v1 as _native_filled_settlement,
)
from app.exchange.okx import (
    native_demo_leverage_configuration_transport_v1 as _native_leverage_configuration,
)
from app.exchange.okx import native_demo_settlement_v1 as _native_settlement
from app.exchange.okx import native_demo_source_identity_v2 as _native_source_identity
from app.exchange.okx import native_demo_transport_v2 as _native_transport
from app.exchange.okx import native_demo_vendor_integrity_v2 as _native_vendors
from app.exchange.okx import private_rest as _native_rest
from app.trade_qualification import native_account_risk_assembly_v1 as _native_account
from app.trade_qualification import native_continuation_guard_v1 as _native_guard
from app.trade_qualification import native_ledger_runtime_v2 as _native_ledger

_INTENT_KEY = object()
_CLOSE_INTENT_KEY_V1 = object()
_LEVERAGE_CONFIGURATION_KEY_V1 = object()
_GATE_KEY = object()
_POSTSUBMIT_SETTLEMENT_KEY_V1 = _native_settlement._SOURCE_KEY_V1
_POSTSUBMIT_FILLED_SETTLEMENT_KEY_V1 = _native_filled_settlement._SOURCE_KEY_V1
_GATES = WeakKeyDictionary()
_CLIENT_GATES = WeakKeyDictionary()
_ORDER_PATH = "/api/v5/trade/order"
_ORIGINS = frozenset(
    {
        "https://openapi.okx.com",
        "https://www.okx.com",
        "https://us.okx.com",
        "https://eea.okx.com",
        "https://tr.okx.com",
    }
)
_MAX_GATES = 16
_CLOSE_ATTEMPTS_V1 = {}
_CLOSE_API_NAMES_V1 = (
    "_peek_owned_native_close_intent_for_root_v1",
    "_take_owned_native_close_intent_for_root_v1",
    "_bind_owned_native_close_transport_v1",
    "_replay_native_close_transport_facts_v1",
    "_lock_owned_native_close_transport_v1",
    "_require_owned_native_close_transport_lock_v1",
    "_discard_owned_native_close_intent_for_root_v1",
    "_inhibit_owned_native_close_transport_v1",
    "_replay_retained_native_close_intent_for_root_v1",
)


class NativeDemoWriteError(ValueError):
    """Fixed public failure codes; never stringify private payloads or errors."""


def _need(condition, code="native_demo_write_unavailable"):
    if condition is not True:
        raise NativeDemoWriteError(code) from None


@dataclass(frozen=True, slots=True, repr=False)
class _DemoTransportContextV2:
    """Inert private transport DATA returned by the genuine RI registry.

    Constructor success is not authority. RI replays the actual settings,
    credential session, exact account, configured route and source ownership
    every time; this module retains its opaque registry binding separately.
    """

    client: object = field(repr=False)
    read_session: object = field(repr=False)
    scope: object = field(repr=False)
    session_factory: object = field(repr=False)
    origin: str = field(repr=False)
    credential_session_sha256: str = field(repr=False)
    original_expires_at: datetime
    monotonic_deadline_ns: int


@dataclass(frozen=True, slots=True, repr=False)
class _DemoCloseTransportContextV1:
    """Inert Source-created NEGATIVE context, never a renewed entry lease."""

    client: object = field(repr=False)
    read_session: object = field(repr=False)
    scope: object = field(repr=False)
    session_factory: object = field(repr=False)
    origin: str = field(repr=False)
    credential_session_sha256: str = field(repr=False)
    read_source_generation: object = field(repr=False)
    original_entry_transport_binding: object = field(repr=False)
    negative_expires_at: datetime = field(repr=False)
    monotonic_deadline_ns: int = field(repr=False)


def _live_facts(record):
    if record.get("configuration_purpose") is _LEVERAGE_CONFIGURATION_KEY_V1:
        return _native_leverage_configuration.live_v1(record)
    if record.get("close_purpose") is _CLOSE_INTENT_KEY_V1:
        return _live_close_facts_v1(record)
    from app.exchange.okx.private_rest import OkxDemoPrivateRestClient
    from app.trade_qualification import native_account_risk_assembly_v1 as account
    from app.trade_qualification import native_ledger_runtime_v2 as ledger

    _need(
        record["pid"] == os.getpid()
        and record["thread"] == threading.get_ident()
        and record["task"] is asyncio.current_task()
        and record["loop"] is asyncio.get_running_loop()
        and not record["task"].cancelling(),
        "native_demo_write_context_changed",
    )
    facts = ledger._replay_native_intent_for_transport_v2(
        record["state"], key=_INTENT_KEY
    )
    _need(type(facts) is ledger.NativeIntentTransportFactsV2)
    context = account._replay_issued_demo_transport_v2(
        facts.transport_binding, key=_INTENT_KEY
    )
    _need(
        type(context) is _DemoTransportContextV2
        and context == facts.transport_context
        and type(context.client) is OkxDemoPrivateRestClient
        and context.client is record["client"]
        and context.session_factory is facts.session_factory
        and context.scope == facts.request.scope
        and context.scope.environment == "demo"
        and context.credential_session_sha256 == facts.request.credential_session_sha256
        and type(context.origin) is str
        and context.origin in _ORIGINS
        and context.origin == record["origin"]
        and type(context.monotonic_deadline_ns) is int
        and facts.execution_authority is False
        and facts.order_retry_authority is False,
        "native_demo_write_source_binding_changed",
    )
    settings = context.client.settings
    _need(
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
        and facts.request.risk_inputs.instrument.instrument_id
        in settings.okx_demo_allowed_symbol_list
        and facts.request.risk_inputs.requested_leverage
        <= settings.okx_demo_max_leverage
        and facts.request.risk_inputs.requested_contracts
        <= settings.okx_demo_max_order_size_contracts,
        "native_demo_write_configuration_disabled_or_cap_changed",
    )
    stamp = native_stamp()
    now = utc_from_ns(stamp["utc_ns"])
    _need(
        record["opened_at"] <= now < facts.expires_at
        and now < context.original_expires_at
        and stamp["monotonic_ns"] < facts.expiry_monotonic_ns
        and stamp["monotonic_ns"] < context.monotonic_deadline_ns,
        "native_demo_write_original_expiry",
    )
    return facts, context, now


def _capture_close_api_v1(record):
    """Capture only after the original genuine entry gate/source authenticated."""
    from app.trade_qualification import native_controlled_close_v1 as close

    close._NATIVE_GUARD()
    functions = []
    for name in _CLOSE_API_NAMES_V1:
        value = vars(close).get(name)
        _need(type(value) is FunctionType)
        pin = _native_guard._function_pin(value, {})
        _native_guard._check_function(pin)
        functions.append((name, value, pin))
    saved = {}
    _native_guard.save_native_guard_v1(saved, close._NATIVE_GUARD)
    record["close_api"] = (
        close,
        close.OwnedNativeCloseIntentV1,
        close.NativeCloseTransportFactsV1,
        tuple(functions),
        saved,
        close.NativeCloseSubmissionResponseDataV1,
    )


def _close_api_v1(record):
    actual = record["close_api"]
    close, owner_type, facts_type, functions, saved, response_type = actual
    _need(
        vars(close).get("OwnedNativeCloseIntentV1") is owner_type
        and vars(close).get("NativeCloseTransportFactsV1") is facts_type
        and vars(close).get("NativeCloseSubmissionResponseDataV1") is response_type
    )
    _native_guard.check_saved_native_guard_v1(saved)
    for name, function, pin in functions:
        _need(vars(close).get(name) is function)
        _native_guard._check_function(pin)
    return actual


def _close_function_v1(record, name):
    for expected, function, _ in _close_api_v1(record)[3]:
        if name == expected:
            return function
    raise NativeDemoWriteError("native_demo_close_fixed_function_unavailable")


def _live_close_facts_v1(record):
    from app.exchange.okx.private_rest import OkxDemoPrivateRestClient

    _need(
        record["pid"] == os.getpid()
        and record["thread"] == threading.get_ident()
        and record["task"] is asyncio.current_task()
        and record["loop"] is asyncio.get_running_loop()
        and not record["task"].cancelling()
    )
    facts = _close_function_v1(record, "_replay_native_close_transport_facts_v1")(
        record["state"], key=_CLOSE_INTENT_KEY_V1
    )
    _need(type(facts) is _close_api_v1(record)[2])
    context = facts.transport_context
    _need(
        type(context) is _DemoCloseTransportContextV1
        and type(context.client) is OkxDemoPrivateRestClient
        and context.client is record["client"]
        and context.scope == facts.request.scope
        and context.scope.environment == "demo"
        and context.session_factory is facts.session_factory
        and context.origin == record["origin"]
        and context.origin in _ORIGINS
        and context.read_source_generation is record["state"]["view"].source_generation
        and facts.exact_order_sha256
        == hashlib.sha256(facts.exact_order_json).hexdigest()
        and facts.execution_authority is False
        and facts.order_retry_authority is False
    )
    settings = context.client.settings
    _need(
        settings.trading_mode == "okx_demo"
        and settings.okx_demo_enabled is True
        and settings.okx_demo_allow_order_writes is True
        and settings.okx_demo_require_protection is True
        and settings.live_trading is False
        and settings.okx_live_enabled is False
        and settings.okx_live_allow_order_writes is False
        and settings.okx_live_auto_execution is False
        and settings.paper_auto_execution is False
    )
    stamp = native_stamp()
    now = utc_from_ns(stamp["utc_ns"])
    _need(
        now < facts.expires_at
        and now < context.negative_expires_at
        and stamp["monotonic_ns"] < facts.expiry_monotonic_ns
        and stamp["monotonic_ns"] < context.monotonic_deadline_ns,
        "native_demo_close_negative_budget_expired",
    )
    return facts, context, now


def _require_record_lock_v1(record):
    if record.get("configuration_purpose") is _LEVERAGE_CONFIGURATION_KEY_V1:
        return _native_leverage_configuration.require_lock_v1(record)
    if record.get("close_purpose") is _CLOSE_INTENT_KEY_V1:
        return _close_function_v1(
            record, "_require_owned_native_close_transport_lock_v1"
        )(record["state"], session=record["session"], key=_CLOSE_INTENT_KEY_V1)
    from app.trade_qualification import native_ledger_runtime_v2 as ledger

    return ledger._require_native_transport_lock_v2(
        record["state"], session=record["session"], key=_INTENT_KEY
    )


def _require_physical_gate_v2(gate, *, key):
    from app.exchange.okx import native_demo_transport_v2 as transport

    _need(key is transport._TRANSPORT_KEY and type(gate) is _PhysicalWriteGateV2)
    record = _GATES.get(gate)
    _need(
        type(record) is dict
        and record["gate"] is gate
        and record["phase"] in ("locked", "prepared", "attempted")
        and type(record["session"]) is AsyncSession,
        "native_demo_write_gate_not_owned",
    )
    _live_facts(record)
    _require_record_lock_v1(record)
    return record


def _checked_gate(gate):
    from app.exchange.okx import native_demo_transport_v2 as transport

    return _require_physical_gate_v2(gate, key=transport._TRANSPORT_KEY)


def _require_vendor_state_v2():
    from app.exchange.okx import native_demo_vendor_integrity_v2 as vendors

    vendors.require_native_demo_vendors_v2(_VENDOR_ADMISSION_V2)


def _capture_abort_pins_v2(gate):
    record = _checked_gate(gate)
    _require_vendor_state_v2()
    return _native_vendors.capture_native_demo_cleanup_v2(
        _VENDOR_ADMISSION_V2, record
    ), record


class _PhysicalWriteGateV2:
    __slots__ = ("__weakref__",)

    def __new__(cls, *, key):
        _need(key is _GATE_KEY and cls is _PhysicalWriteGateV2)
        return object.__new__(cls)

    def __copy__(self):
        raise NativeDemoWriteError("native_demo_write_not_transferable")

    def __deepcopy__(self, memo):
        raise NativeDemoWriteError("native_demo_write_not_transferable")

    def __reduce_ex__(self, protocol):
        raise NativeDemoWriteError("native_demo_write_not_transferable")

    @property
    def host(self):
        return urlsplit(_checked_gate(self)["origin"]).hostname

    @property
    def tls_context(self):
        return _checked_gate(self)["tls_context"]

    def take_transport(self, request):
        record = _checked_gate(self)
        with record["lock"]:
            _need(
                record["phase"] == "prepared"
                and not record["transport_taken"]
                and request is record["request"],
                "native_demo_write_transport_replayed",
            )
            _bind_request(record, request, repeat=True)
            record["transport_taken"] = True

    async def tls_ready_after_locked_readback(self):
        from app.trade_qualification import native_ledger_runtime_v2 as ledger

        record = _checked_gate(self)
        _need(not record["tls_ready"], "native_demo_write_duplicate_tls")
        if record.get("configuration_purpose") is _LEVERAGE_CONFIGURATION_KEY_V1:
            await _native_leverage_configuration.lock_after_tls_v1(record)
        elif record.get("close_purpose") is _CLOSE_INTENT_KEY_V1:
            await _close_function_v1(record, "_lock_owned_native_close_transport_v1")(
                record["state"], session=record["session"], key=_CLOSE_INTENT_KEY_V1
            )
        else:
            await ledger._lock_native_intent_for_transport_v2(
                record["state"], session=record["session"], key=_INTENT_KEY
            )
        _checked_gate(self)
        record["tls_ready"] = True

    def ssl_write(self, native_ssl, data):
        record = _checked_gate(self)
        with record["lock"]:
            _need(
                record["tls_ready"]
                and record["transport_taken"]
                and type(native_ssl) is ssl.SSLObject
                and type(data) is bytes
                and bool(data)
                and record["wire"][record["accepted"] : record["accepted"] + len(data)]
                == data,
                "native_demo_write_plaintext_changed",
            )
            # Terminal submission attempt BEFORE calling SSL. An SSL error or
            # WantRead/WantWrite never returns this invocation to a ready pool.
            record["attempted"] = True
            record["phase"] = "attempted"
            accepted = native_ssl.write(data)
            _need(
                type(accepted) is int and 0 < accepted <= len(data),
                "native_demo_write_ssl_result_unknown",
            )
            record["accepted"] += accepted
            return accepted

    def raw_write(self, native_transport, data):
        record = _checked_gate(self)
        with record["lock"]:
            _need(
                type(data) is bytes and bool(data),
                "native_demo_write_socket_bytes_invalid",
            )
            if record["tls_ready"]:
                _need(record["attempted"], "native_demo_write_socket_without_claim")
                record["raw_calls"] += 1
            return native_transport.write(data)

    def finish(self, success):
        # This is bookkeeping, never an authority/retry decision. It must also
        # work after cancellation, expiry or a revoked source fails its replay.
        record = _GATES.get(self)
        _need(type(record) is dict and record["gate"] is self)
        with record["lock"]:
            if record["phase"] in ("completed", "denied", "unknown"):
                return
            if success is True:
                try:
                    _checked_gate(self)
                except BaseException:  # noqa: BLE001 - retain known DATA while authority stays unknown.
                    # This cannot grant response or retry authority. Actual
                    # received bytes/EOF already belong to the DATA journal;
                    # late expiry/revocation must not erase that observation.
                    record["received"].retain_fault("live_after_response")
                    record["phase"] = "unknown"
                    return
                _need(
                    record["accepted"] == len(record["wire"])
                    and record["raw_calls"] > 0,
                    "native_demo_write_complete_request_unproven",
                )
                record["phase"] = "completed"
            else:
                record["phase"] = "unknown" if record["attempted"] else "denied"


def _bind_request(record, request, *, repeat=False):
    from app.exchange.okx import private_rest

    facts, context, now = _live_facts(record)
    configuration = (
        record.get("configuration_purpose") is _LEVERAGE_CONFIGURATION_KEY_V1
    )
    target = _native_leverage_configuration.PATH if configuration else _ORDER_PATH
    exact_body = facts.request_json if configuration else facts.exact_order_json
    exact_sha256 = facts.request_sha256 if configuration else facts.exact_order_sha256
    _need(
        type(request) is httpx.Request
        and request.method == "POST"
        and str(request.url) == context.origin + target
        and request.url.scheme == "https"
        and request.url.port in (None, 443)
        and request.url.raw_path == target.encode("ascii")
        and not request.url.username
        and not request.url.password
        and type(request.stream) is httpx.ByteStream
        and request.content == exact_body
        and hashlib.sha256(request.content).hexdigest() == exact_sha256
        and not request.extensions.keys() - {"timeout"},
        "native_demo_write_prepared_request_changed",
    )
    rows = request.headers.raw
    names = [name.lower() for name, _ in rows]
    expected_names = {
        b"host",
        b"connection",
        b"content-length",
        b"content-type",
        b"accept",
        b"accept-encoding",
        b"user-agent",
        b"ok-access-key",
        b"ok-access-sign",
        b"ok-access-passphrase",
        b"ok-access-timestamp",
        b"x-simulated-trading",
    }
    _need(
        len(names) == len(expected_names)
        and set(names) == expected_names
        and request.headers["host"] == urlsplit(context.origin).hostname
        and request.headers["connection"] == "close"
        and request.headers["accept-encoding"] == "identity"
        and request.headers["content-type"] == "application/json"
        and request.headers["accept"] == "application/json"
        and request.headers["content-length"] == str(len(exact_body))
        and request.headers["user-agent"] == context.client._user_agent()
        and request.headers["x-simulated-trading"] == "1",
        "native_demo_write_headers_changed",
    )
    timestamp = request.headers["ok-access-timestamp"]
    try:
        issued_at = datetime.fromisoformat(timestamp)
        _need(issued_at.utcoffset() == UTC.utcoffset(issued_at))
        # Authentication timestamps are encoded to milliseconds by the fixed
        # signer. Compare that exact representation at both sampled bounds;
        # no host/source timestamp is restamped and no tolerance is added.
        _need(private_rest.utc_iso_timestamp(issued_at) == timestamp)
        lower = datetime.fromisoformat(
            private_rest.utc_iso_timestamp(record["opened_at"])
        )
        upper = datetime.fromisoformat(private_rest.utc_iso_timestamp(now))
        _need(lower <= issued_at <= upper)
        key, secret, passphrase = context.client._credentials()
        signature = private_rest.build_signature(
            timestamp=timestamp,
            method="POST",
            request_path=target,
            body=exact_body.decode("utf-8"),
            secret=secret,
        )
        _need(
            request.headers["ok-access-key"] == key
            and request.headers["ok-access-passphrase"] == passphrase
            and request.headers["ok-access-sign"] == signature,
            "native_demo_write_signature_session_changed",
        )
    except (ValueError, UnicodeError, KeyError):
        raise NativeDemoWriteError("native_demo_write_signature_invalid") from None
    encoder = h11.Connection(h11.CLIENT)
    wire = (
        encoder.send(h11.Request(method=b"POST", target=target.encode(), headers=rows))
        + encoder.send(h11.Data(data=request.content))
        + encoder.send(h11.EndOfMessage())
    )
    if repeat:
        _need(request is record["request"] and wire == record["wire"])
    else:
        _need(record["phase"] == "locked" and record["request"] is None)
        record["request"], record["wire"], record["phase"] = request, wire, "prepared"
        # Actual prepared header captured at the physical boundary. Native-only
        # terminal scanning never re-signs or substitutes an earlier GET token.
        record["actual_post_signature"] = request.headers["ok-access-sign"]
        if configuration:
            _native_leverage_configuration.prepared_v1(record)


def _create_locked_gate_v2(state, session, *, key):
    from app.trade_qualification import native_ledger_runtime_v2 as ledger

    _need(key is _INTENT_KEY and type(session) is AsyncSession)
    _need(len(_GATES) < _MAX_GATES, "native_demo_write_capacity")
    facts = ledger._replay_native_intent_for_transport_v2(state, key=_INTENT_KEY)
    context = facts.transport_context
    _need(type(context) is _DemoTransportContextV2)
    ledger._require_native_transport_lock_v2(state, session=session, key=_INTENT_KEY)
    return _create_gate_record_v1(state, session, facts, context)


def _create_close_locked_gate_v1(state, session):
    temporary = {"close_api": state["root_close_api"]}
    facts = _close_function_v1(temporary, "_replay_native_close_transport_facts_v1")(
        state, key=_CLOSE_INTENT_KEY_V1
    )
    _need(
        type(facts) is _close_api_v1(temporary)[2]
        and type(facts.transport_context) is _DemoCloseTransportContextV1
    )
    _close_function_v1(temporary, "_require_owned_native_close_transport_lock_v1")(
        state, session=session, key=_CLOSE_INTENT_KEY_V1
    )
    return _create_gate_record_v1(
        state,
        session,
        facts,
        facts.transport_context,
        close_api=state["root_close_api"],
    )


def _create_gate_record_v1(state, session, facts, context, *, close_api=None):
    _need(type(session) is AsyncSession and len(_GATES) < _MAX_GATES)
    _need(
        not os.environ.get("SSL_CERT_FILE") and not os.environ.get("SSL_CERT_DIR"),
        "native_demo_write_environment_CA_denied",
    )
    tls_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    tls_context.minimum_version = ssl.TLSVersion.TLSv1_2
    tls_context.set_alpn_protocols(["http/1.1"])
    tls_context.load_default_certs(ssl.Purpose.SERVER_AUTH)
    _need(
        tls_context.keylog_filename is None
        and tls_context.check_hostname
        and tls_context.verify_mode == ssl.CERT_REQUIRED
    )
    gate = _PhysicalWriteGateV2(key=_GATE_KEY)
    configuration = _native_leverage_configuration.is_state_v1(state)
    fields = {
        "gate": gate,
        "state": state,
        "original_transport_facts": facts,
        "session": session,
        "pid": os.getpid(),
        "thread": threading.get_ident(),
        "task": asyncio.current_task(),
        "loop": asyncio.get_running_loop(),
        "opened_at": utc_from_ns(native_stamp()["utc_ns"]),
        "client": context.client,
        "transport_context": context,
        "origin": context.origin,
        "tls_context": tls_context,
        "lock": threading.RLock(),
        "phase": "locked",
        "request": None,
        "wire": b"",
        "accepted": 0,
        "raw_calls": 0,
        "attempted": False,
        "tls_ready": False,
        "transport_taken": False,
        "received": _native_transport._ResponseDataJournalV2(),
    }
    if configuration:
        record = _native_leverage_configuration.adopt_gate_record_v1(
            state, session, facts, context
        )
        fields["received"], fields["opened_at"] = (
            record["received"],
            record["opened_at"],
        )
        record.update(fields)
    else:
        record = fields
    if close_api is not None:
        record["close_purpose"] = _CLOSE_INTENT_KEY_V1
        record["close_api"] = close_api
    # The genuine native registry's spent/unknown state strongly retains this
    # same journal after client/stream close fails. This is memory custody,
    # never a database durability or complete-fill/protection assertion.
    if not configuration:
        _need("native_transport_received_data_v2" not in state)
        state["native_transport_received_data_v2"] = record["received"]
        _need("native_root_resource_data_v1" not in state)
        # Existing ledger/close custody remains unchanged for those purposes.
        state["native_root_resource_data_v1"] = record
    _GATES[gate] = record
    _live_facts(record)
    if configuration:
        _native_leverage_configuration.charge_v1(record)
    elif close_api is None:
        _capture_close_api_v1(record)
    else:
        state["root_close_gate"] = gate
    if not configuration:
        _native_settlement.capture_postsubmit_api_v1(record)
        _native_filled_settlement.capture_postsubmit_filled_api_v1(record)
    _need(context.client not in _CLIENT_GATES, "native_demo_write_client_replayed")
    _CLIENT_GATES[context.client] = gate
    return gate


def _registered_client_gate(client):
    from app.exchange.okx.private_rest import OkxDemoPrivateRestClient

    if type(client) is not OkxDemoPrivateRestClient:
        return None
    gate = _CLIENT_GATES.get(client)
    if gate is not None:
        record = _checked_gate(gate)
        _need(record["client"] is client)
    return gate


def _native_foreign_read_context_v2(client, session):
    """Keep the original GET context intact, never relabel it as a write lease.

    Completed-response access is DATA-only. Pre-POST checks remain separately
    unused/prepared-only; every other foreign client retains the old denial.
    """
    from app.exchange.okx.private_rest import (
        DemoPinnedReadSession,
        OkxDemoPrivateRestClient,
    )

    if type(client) is not OkxDemoPrivateRestClient:
        return False
    gate = _CLIENT_GATES.get(client)
    if gate is None:
        return False
    record = _GATES.get(gate)
    _need(
        type(record) is dict and record["gate"] is gate and record["client"] is client
    )
    if record["phase"] == "completed":
        _NATIVE_GUARD()
        _need(
            record["pid"] == os.getpid()
            and record["thread"] == threading.get_ident()
            and record["task"] is asyncio.current_task()
            and record["loop"] is asyncio.get_running_loop()
        )
    else:
        _checked_gate(gate)
    context = record["transport_context"]
    _need(
        type(context)
        is (
            _native_leverage_configuration.contracts.NativeLeverageConfigurationTransportContextV1
            if record.get("configuration_purpose") is _LEVERAGE_CONFIGURATION_KEY_V1
            else _DemoCloseTransportContextV1
            if record.get("close_purpose") is _CLOSE_INTENT_KEY_V1
            else _DemoTransportContextV2
        )
        and type(session) is DemoPinnedReadSession
        and session is context.read_session
        and session._owner is not client
        and session._settings is client.settings
        and session._origin == record["origin"],
        "native_demo_write_original_read_context_changed",
    )
    if record.get("close_purpose") is not _CLOSE_INTENT_KEY_V1:
        session.verify_current()
    # Negative close uses an independently authenticated dedicated context.
    # Exact original GET identity is retained; a closed old lease is not revived.
    return True


def _retain_native_order_observation_v2(client, observed):
    """Retain response DATA before late rejection; never grant retry authority."""
    from app.exchange.okx.private_rest import (
        ObservedOrderResult,
        OkxDemoPrivateRestClient,
    )

    _need(
        type(client) is OkxDemoPrivateRestClient
        and type(observed) is ObservedOrderResult
    )
    gate = _CLIENT_GATES.get(client)
    record = _GATES.get(gate) if type(gate) is _PhysicalWriteGateV2 else None
    _need(
        type(record) is dict
        and record["client"] is client
        and record["task"] is asyncio.current_task()
        and record.get("observed_result") is None,
        "native_demo_write_observation_not_owned",
    )
    record["observed_result"] = observed


def _received_record_v2(gate, key):
    from app.exchange.okx import native_demo_transport_v2 as transport

    # DATA-only admission intentionally excludes live expiry/Control replay.
    # It cannot send, consume, retry, release a hold or create an owner.
    _need(key is transport._TRANSPORT_KEY and type(gate) is _PhysicalWriteGateV2)
    record = _GATES.get(gate)
    if (
        type(record) is dict
        and record.get("configuration_purpose") is _LEVERAGE_CONFIGURATION_KEY_V1
    ):
        _need(key is transport._TRANSPORT_KEY and type(gate) is _PhysicalWriteGateV2)
        return _native_leverage_configuration.received_record_v1(record)
    _need(
        type(record) is dict
        and record["gate"] is gate
        and record["pid"] == os.getpid()
        and record["thread"] == threading.get_ident()
        and record["task"] is asyncio.current_task()
        and record["loop"] is asyncio.get_running_loop()
        and record["state"].get("native_transport_received_data_v2")
        is record["received"],
        "native_demo_write_response_data_not_owned",
    )
    return record


def _retain_native_response_headers_v2(gate, status, headers, *, key):
    record = _received_record_v2(gate, key)
    record["received"].retain_headers(status, headers)
    if record.get("configuration_purpose") is _LEVERAGE_CONFIGURATION_KEY_V1:
        _native_leverage_configuration.charge_v1(record)


def _retain_native_tls_data_v2(gate, value, *, key):
    record = _received_record_v2(gate, key)
    record["received"].retain_tls(value)
    if record.get("configuration_purpose") is _LEVERAGE_CONFIGURATION_KEY_V1:
        _native_leverage_configuration.charge_v1(record)


def _retain_native_body_data_v2(gate, value, *, key):
    record = _received_record_v2(gate, key)
    record["received"].retain_body(value)
    if record.get("configuration_purpose") is _LEVERAGE_CONFIGURATION_KEY_V1:
        _native_leverage_configuration.charge_v1(record)


def _retain_native_response_eof_v2(gate, *, key):
    record = _received_record_v2(gate, key)
    record["received"].retain_eof()
    if record.get("configuration_purpose") is _LEVERAGE_CONFIGURATION_KEY_V1:
        _native_leverage_configuration.charge_v1(record)


def _native_response_body_limit_v1(gate, *, key):
    return _received_record_v2(gate, key)["received"]._body_limit


def _retain_native_response_fault_v2(gate, code, *, key):
    record = _received_record_v2(gate, key)
    record["received"].retain_fault(code)
    if code in {"body", "close", "cleanup_mutation"}:
        record["phase"] = "unknown"


def _native_reconciliation_record_v1(handle, *, key):
    """Read-only spent response source, deliberately independent of trade expiry."""
    from app.trade_qualification import native_ledger_runtime_v2 as ledger
    from app.trade_qualification import native_postsubmit_reconciliation_v1 as reconcile

    _need(key is reconcile._SOURCE_KEY_V1 and type(handle) is _PhysicalWriteGateV2)
    _NATIVE_GUARD()
    record = _received_record_v2(handle, _native_transport._TRANSPORT_KEY)
    _need(
        record.get("close_purpose") is not _CLOSE_INTENT_KEY_V1,
        "native_demo_close_requires_versioned_reconciliation",
    )
    _need(
        record.get("reconciliation_taken") is True
        and record["state"].get("transport_spent") is True
        and not record["session"].in_transaction(),
        "native_reconciliation_requires_spent_unlocked_submission",
    )
    # The exact already-spent UNKNOWN marker authenticates the held state.
    # This is idempotent retirement; it never replays a healthy trade lease.
    ledger._discard_native_intent_for_transport_v2(record["state"], key=_INTENT_KEY)
    retained = record["original_transport_facts"]
    _need(
        retained.request_json == record["state"]["request_json"]
        and retained.intent == record["state"]["intent"]
        and retained.reserved == record["state"]["reserved"]
        and retained.consumed == record["state"]["consumed"]
        and retained.transport_binding is record["state"]["transport_binding"]
    )
    return record


def _take_native_response_for_reconciliation_v1(observed, *, key):
    """One-use same-task MOVE by exact observed object identity, never DTO fields."""
    from app.exchange.okx.transport_observation import ObservedOrderResult
    from app.trade_qualification import native_postsubmit_reconciliation_v1 as reconcile

    _need(key is reconcile._SOURCE_KEY_V1 and type(observed) is ObservedOrderResult)
    _NATIVE_GUARD()
    matches = [
        record
        for record in _GATES.values()
        if type(record) is dict
        and record.get("observed_result") is observed
        and record.get("close_purpose") is not _CLOSE_INTENT_KEY_V1
    ]
    _need(
        len(matches) == 1 and not matches[0].get("reconciliation_taken", False),
        "native_reconciliation_response_not_owned_or_spent",
    )
    record = matches[0]
    # Spend first. A later failure cannot make this response reusable.
    record["reconciliation_taken"] = True
    _native_reconciliation_record_v1(record["gate"], key=key)
    record["reconciliation_view"] = reconcile.NativeSubmissionResponseDataV1(
        record["original_transport_facts"],
        observed,
        record["received"],
        record["state"],
    )
    return record["gate"]


def _replay_native_response_for_reconciliation_v1(handle, *, key):
    from app.trade_qualification import native_postsubmit_reconciliation_v1 as reconcile

    record = _native_reconciliation_record_v1(handle, key=key)
    value = record.get("reconciliation_view")
    _need(
        type(value) is reconcile.NativeSubmissionResponseDataV1
        and value.transport_facts is record["original_transport_facts"]
        and value.observed is record["observed_result"]
        and value.received_journal is record["received"]
        and value.transport_state is record["state"]
    )
    return value


def _native_close_reconciliation_record_v1(handle, *, key):
    """Authenticate only received negative DATA after all account locks exit."""
    _NATIVE_GUARD()
    record = _received_record_v2(handle, _native_transport._TRANSPORT_KEY)
    api = _close_api_v1(record)
    _need(
        key is vars(api[0]).get("_POSTSUBMIT_KEY_V1")
        and record.get("close_purpose") is _CLOSE_INTENT_KEY_V1
        and record.get("close_reconciliation_taken") is True
        and record["state"].get("root_close_spent") is True
        and not record["session"].in_transaction()
    )
    view = _close_function_v1(
        record, "_replay_retained_native_close_intent_for_root_v1"
    )(record["state"], key=_CLOSE_INTENT_KEY_V1)
    _need(record["state"]["view"] is view)
    entry = _GATES.get(view.filled_result_view.response_handle)
    _need(
        type(entry) is dict
        and entry["gate"] is view.filled_result_view.response_handle
        and entry.get("close_purpose") is not _CLOSE_INTENT_KEY_V1
        and entry.get("observed_result") is view.filled_result_view.observed_result
        and view.filled_result_view.response_data.transport_facts
        is entry["original_transport_facts"]
    )
    return record, view


def _take_native_close_response_for_reconciliation_v1(observed, *, key):
    from app.exchange.okx.transport_observation import ObservedOrderResult

    _NATIVE_GUARD()
    _need(type(observed) is ObservedOrderResult)
    matches = [
        record
        for record in _GATES.values()
        if type(record) is dict
        and record.get("close_purpose") is _CLOSE_INTENT_KEY_V1
        and record.get("observed_result") is observed
    ]
    _need(
        len(matches) == 1 and matches[0].get("close_reconciliation_taken") is not True
    )
    record = matches[0]
    return _take_native_close_received_record_v1(record, key=key)


def _take_native_close_attempt_for_reconciliation_v1(intent_owner, *, key):
    """Exact spent intent identity retrieves late/partial DATA after a failure.

    A response DTO is unnecessary. This cannot renew or retry the close, and
    requires a real retained gate plus the actual transaction already exited.
    """
    _NATIVE_GUARD()
    attempt = _CLOSE_ATTEMPTS_V1.get(intent_owner)
    _need(
        type(attempt) is _CloseSubmissionAttemptV1
        and attempt.state["owner"] is intent_owner
    )
    _need(
        key
        is vars(_close_api_v1({"close_api": attempt.api})[0]).get("_POSTSUBMIT_KEY_V1")
    )
    gate = attempt.state.get("root_close_gate")
    record = _GATES.get(gate)
    _need(
        type(record) is dict
        and record["state"] is attempt.state
        and record["close_api"] is attempt.api
        and record.get("close_purpose") is _CLOSE_INTENT_KEY_V1
    )
    return _take_native_close_received_record_v1(record, key=key)


def _take_native_close_received_record_v1(record, *, key):
    _need(record.get("close_reconciliation_taken") is not True)
    _need(key is vars(_close_api_v1(record)[0]).get("_POSTSUBMIT_KEY_V1"))
    # Spend before any subsequent validation; failure retains the exact graph.
    record["close_reconciliation_taken"] = True
    record, view = _native_close_reconciliation_record_v1(record["gate"], key=key)
    record["close_reconciliation_view"] = _close_api_v1(record)[5](
        record["original_transport_facts"],
        record.get("observed_result"),
        record["received"],
        record["state"],
        view.filled_result_view.response_handle,
        view.filled_result_view,
    )
    return record["gate"]


def _replay_native_close_response_for_reconciliation_v1(handle, *, key):
    record, view = _native_close_reconciliation_record_v1(handle, key=key)
    value = record.get("close_reconciliation_view")
    _need(
        type(value) is _close_api_v1(record)[5]
        and value.transport_facts is record["original_transport_facts"]
        and value.observed is record.get("observed_result")
        and value.received_journal is record["received"]
        and value.transport_state is record["state"]
        and value.entry_response_handle is view.filled_result_view.response_handle
        and value.filled_result_view is view.filled_result_view
    )
    return value


def _native_postsubmit_scan_record_v1(transport_binding, *, key):
    from app.trade_qualification import native_controlled_account_source_v6 as source

    _need(key is source._SCAN_KEY_V6)
    _NATIVE_GUARD()
    matches = [
        record
        for record in _GATES.values()
        if type(record) is dict
        and record["original_transport_facts"].transport_binding is transport_binding
    ]
    _need(len(matches) == 1)
    record = _received_record_v2(matches[0]["gate"], _native_transport._TRANSPORT_KEY)
    _need(
        (
            record["state"].get("root_close_spent") is True
            if record.get("close_purpose") is _CLOSE_INTENT_KEY_V1
            else record["state"].get("transport_spent") is True
        )
        and not record["session"].in_transaction()
    )
    if (
        record.get("close_purpose") is _CLOSE_INTENT_KEY_V1
        and record["request"] is None
    ):
        # Exact actual registry proves no signed/prepared POST existed. This
        # is absence DATA for a NEW read, never a retry/closure/write permit.
        journal = record["received"]
        _need(
            record["wire"] == b""
            and record.get("actual_post_signature") is None
            and record["attempted"] is False
            and record["transport_taken"] is False
            and record["raw_calls"] == 0
            and record["accepted"] == 0
            and record.get("observed_result") is None
            and journal.status is None
            and not journal.headers
            and not journal.tls_chunks
            and not journal.body_chunks
            and journal.tls_bytes == journal.body_bytes == 0
            and journal.eof is False
        )
    else:
        _need(
            type(record.get("actual_post_signature")) is str
            and record["request"] is not None
        )
    return record


def _native_source_memory_state_v1(binding, source):
    """Read genuine Root/Source registry relationships without replay calls."""
    close_records = vars(source).get("_CLOSE_TRANSPORT_V6")
    _need(type(close_records) is dict)
    close_record = close_records.get(binding)
    if close_record is not None:
        _need(
            type(close_record) is dict
            and close_record["token"] is binding
            and close_record["key"] is _CLOSE_INTENT_KEY_V1
            and close_record["write_module"] is _native_sys.modules[__name__]
        )
        state = close_record["intent_state"]
        _need(type(state) is dict)
        attempt = _CLOSE_ATTEMPTS_V1.get(state["owner"])
        _need(
            type(attempt) is _CloseSubmissionAttemptV1
            and attempt.state is state
            and state.get("root_close_spent") is True
            and state["root_taken"] is True
            and type(state.get("owner")) is attempt.api[1]
        )
        _close_api_v1({"close_api": attempt.api})
        view = state["view"]
        entry = _GATES.get(view.filled_result_view.response_handle)
        if entry is not None:
            _need(
                type(entry) is dict
                and entry["close_api"] is attempt.api
                and entry["observed_result"] is view.filled_result_view.observed_result
            )
            _close_api_v1(entry)
        capture = state.get("transport_source_capture")
        _need(
            type(capture) is tuple
            and len(capture) == 3
            and capture[0] is source
            and (
                state.get("transport_binding") is binding
                or (
                    state.get("transport_binding") is None
                    and close_record["phase"] == "BINDING"
                )
            )
        )
        return state, True
    records = vars(source).get("_TRANSPORT_V6")
    _need(type(records) is dict)
    original = records.get(binding)
    _need(
        type(original) is dict
        and original["key"] is _INTENT_KEY
        and original["write_module"] is _native_sys.modules[__name__]
    )
    candidates = list(_native_ledger._ATTEMPTS.values())
    candidates.extend(
        value[5]
        for value in _native_ledger._UNKNOWN.values()
        if type(value) is tuple and len(value) == 6 and type(value[5]) is dict
    )
    matches = [
        value
        for value in candidates
        if type(value) is dict
        and value.get("transport_module") is _native_sys.modules[__name__]
        and value.get("transport_key") is _INTENT_KEY
        and value["r7"]["account_binding"] is original["held"]
    ]
    _need(len(matches) == 1)
    state = matches[0]
    _need(
        state.get("transport_binding") is None
        or state.get("transport_binding") is binding
    )
    return state, False


def _native_source_retained_buffers_v1(transport_binding, *, key):
    """Actual private DATA buffers for Source accounting on EVERY pass.

    No healthy lease/clock, DB, Source callback or active write-gate replay is
    invoked. Outgoing authentication/wire is included for capacity only, never
    passed to the received-data echo scanner. A missing gate is accepted only
    for the exact genuine pre-gate attempt, not as evidence of empty resources.
    """
    from app.trade_qualification import native_controlled_account_source_v6 as source

    _NATIVE_GUARD()
    _need(type(key) is object and vars(source).get("_SCAN_KEY_V6") is key)
    state, closing = _native_source_memory_state_v1(transport_binding, source)
    _need(
        state["task"] is asyncio.current_task()
        and state["task"].get_loop() is asyncio.get_running_loop()
    )
    if closing:
        _need(
            state["pid"] == os.getpid()
            and state["thread"] == threading.get_ident()
            and state["loop"] is asyncio.get_running_loop()
        )
    records = [
        record
        for record in _GATES.values()
        if type(record) is dict and record["state"] is state
    ]
    retained = state.get("native_root_resource_data_v1")
    if records:
        _need(len(records) == 1 and records[0] is retained)
    values = []
    if retained is None:
        # Root's real MOVE is registered already, but _create_gate_record has
        # never installed a journal/client association. No send is inferred.
        _need(
            not records
            and "native_transport_received_data_v2" not in state
            and (not closing or state.get("root_close_gate") is None)
        )
        if closing:
            values.extend(state["view"].intent_readback[:2])
            values.append(state["view"].target.request_json)
        else:
            values.extend((state["request_json"], state["intent"].canonical_json))
    else:
        _need(
            type(retained) is dict
            and retained["state"] is state
            and type(retained["gate"]) is _PhysicalWriteGateV2
            and retained["pid"] == os.getpid()
            and retained["thread"] == threading.get_ident()
            and retained["task"] is state["task"]
            and retained["loop"] is asyncio.get_running_loop()
            and retained["received"] is state["native_transport_received_data_v2"]
            and retained["original_transport_facts"].transport_binding
            is transport_binding
            and ((retained.get("close_purpose") is _CLOSE_INTENT_KEY_V1) is closing)
        )
        values.extend(_native_retained_request_documents_v1(retained))
        values.append(retained["wire"])
        signature = retained.get("actual_post_signature")
        if signature is not None:
            values.append(signature)
        request = retained.get("request")
        if request is not None:
            values.extend(
                _native_rest.reconciliation_read.retained_httpx_request_data_v1(request)
            )
        journal = retained["received"]
        values.extend(journal.tls_chunks)
        values.extend(journal.body_chunks)
        values.extend(value for row in journal.headers for value in row)
        values.extend(journal.faults)
        observed = retained.get("observed_result")
        if observed is not None:
            for value in (
                observed.observation.raw_body,
                observed.observation.request_body_sha256,
                observed.response_body_sha256,
            ):
                if value is not None:
                    values.append(value)
    unique, seen = [], set()
    for value in values:
        _need(type(value) in (bytes, str))
        if id(value) not in seen:
            seen.add(id(value))
            unique.append(value)
    _need(len(unique) <= 8192)
    return tuple(unique)


def _native_postsubmit_scan_tokens_v1(transport_binding, *, key):
    """Only the native terminal scanner may read the captured actual POST token."""
    record = _native_postsubmit_scan_record_v1(transport_binding, key=key)
    if record["request"] is None:
        _need(record.get("close_purpose") is _CLOSE_INTENT_KEY_V1)
        return ()
    # Retained immutable wire, not a mutable Header lookup or fresh signer.
    signature = record["actual_post_signature"]
    actual = []
    for line in record["wire"].split(b"\r\n\r\n", 1)[0].split(b"\r\n")[1:]:
        name, separator, value = line.partition(b":")
        if separator and name.lower() == b"ok-access-sign":
            actual.append(value.lstrip(b" "))
    _need(actual == [signature.encode("ascii")])
    return (signature,)


def _native_postsubmit_received_buffers_v1(transport_binding, *, key):
    """Actual earlier Root buffers/metadata for the later native token union."""
    record = _native_postsubmit_scan_record_v1(transport_binding, key=key)
    received = record["received"]
    values = [*received.tls_chunks, *received.body_chunks]
    if received.tls_chunks:
        values.append(b"".join(received.tls_chunks))
    if received.body_chunks:
        values.append(received.body_raw)
    for name, value in received.headers:
        values.extend((name, value))
    observed = record.get("observed_result")
    if observed is not None and type(observed.observation.raw_body) is bytes:
        values.append(observed.observation.raw_body)
    _original = record["original_transport_facts"]
    values.extend(_native_retained_request_documents_v1(record))
    _need(len(values) <= 4096 and all(type(value) is bytes for value in values))
    return tuple(values)


def _native_postsubmit_complete_documents_v1(transport_binding, *, key):
    """Fixed complete JSON/received-body purpose, distinct from raw fragments."""
    record = _native_postsubmit_scan_record_v1(transport_binding, key=key)
    values = list(_native_retained_request_documents_v1(record))
    observed, received = record.get("observed_result"), record["received"]
    if received.body_complete:
        # Actual H11 EOF is a received-entity DATA fact even when the later
        # SDK observation/close check failed. It grants no TCP closure or ACK
        # authority; strict decoded secret scanning must still inspect it.
        _need(observed is None or observed.observation.raw_body == received.body_raw)
        values.append(received.body_raw)
    _need(all(type(value) is bytes and bool(value) for value in values))
    return tuple(values)


def _native_retained_request_documents_v1(record):
    original = record["original_transport_facts"]
    if record.get("close_purpose") is _CLOSE_INTENT_KEY_V1:
        _close_api_v1(record)
        return (
            original.request_json.encode("ascii"),
            original.close_intent_json,
            original.exact_order_json,
        )
    return (
        original.request_json.encode("ascii"),
        original.intent.canonical_json.encode("ascii"),
        original.current_recheck_json,
        original.exact_order_json,
    )


def _native_postsubmit_r7_scannables_v1(transport_binding, *, key):
    """Actual original/fresh public journals and renderer artifacts, DATA only."""
    from app.trade_qualification import demo_market_source_v4 as market
    from app.trade_qualification import native_original_local_current55_v2 as original

    record = _native_postsubmit_scan_record_v1(transport_binding, key=key)
    if record.get("close_purpose") is _CLOSE_INTENT_KEY_V1:
        view = _close_function_v1(
            record, "_replay_retained_native_close_intent_for_root_v1"
        )(record["state"], key=_CLOSE_INTENT_KEY_V1)
        record = _GATES.get(view.filled_result_view.response_handle)
        _need(
            type(record) is dict
            and record["observed_result"] is view.filled_result_view.observed_result
        )
    r7 = record["state"]["r7"]
    _native_guard.check_saved_native_guard_v1(r7)
    candidate = r7["published"]["candidate"]
    _need(candidate["local_kind"] == "native_current55_v2")
    local = original._HOLDS.get(candidate["local_binding"])
    _need(type(local) is dict and local["task"] is asyncio.current_task())
    _native_guard.check_saved_native_guard_v1(local)
    journals = []
    for public in (local["prefix"]["frame"]["market_state"], r7["public_state"]):
        # Historical raw/custody replay has no healthy trade-lease admission.
        directory, _, _, events = market.replay_runtime_bundle_v4(
            public["bundle_raw"], expected_sha256=public["bundle_sha256"]
        )
        journals.append((directory, events))
    artifacts = r7["published"]["packet"]
    _need(
        type(artifacts) is tuple
        and len(artifacts) == 6
        and all(
            type(item) is tuple
            and len(item) == 2
            and type(item[0]) is str
            and type(item[1]) is bytes
            for item in artifacts
        )
    )
    return tuple(journals), artifacts


def _native_route_v2(client, method, path):
    """Only an already registered native client takes this fixed route.

    Returning True is a route selection, not a transport permit. The final
    independent boundary and both physical write gates replay the same owner.
    """
    gate = _registered_client_gate(client)
    if gate is None:
        return False
    _need(
        type(method) is str
        and method == "POST"
        and path
        == (
            _native_leverage_configuration.PATH
            if _GATES[gate].get("configuration_purpose")
            is _LEVERAGE_CONFIGURATION_KEY_V1
            else _ORDER_PATH
        ),
        "native_demo_write_exact_order_route_required",
    )
    _checked_gate(gate)
    return True


def _native_request_client_v2(client, method, path):
    from app.exchange.okx import native_demo_transport_v2 as transport

    if not _native_route_v2(client, method, path):
        return None
    gate = _registered_client_gate(client)
    record = _checked_gate(gate)
    _need(
        record["phase"] == "locked" and not record.get("httpx_created", False),
        "native_demo_write_client_replayed",
    )
    facts, context, now = _live_facts(record)
    context_expiry = (
        context.negative_expires_at
        if record.get("close_purpose") is _CLOSE_INTENT_KEY_V1
        else context.original_expires_at
    )
    remaining = (
        context_expiry
        if record.get("configuration_purpose") is _LEVERAGE_CONFIGURATION_KEY_V1
        else min(facts.expires_at, context_expiry)
    ) - now
    timeout = min(2.0, remaining.total_seconds())
    _need(timeout > 0, "native_demo_write_original_expiry")
    record["httpx_created"] = True
    owned_transport = transport._create_transport_v2(
        gate,
        key=(
            _LEVERAGE_CONFIGURATION_KEY_V1
            if record.get("configuration_purpose") is _LEVERAGE_CONFIGURATION_KEY_V1
            else _INTENT_KEY
        ),
    )
    owned_client = httpx.AsyncClient(
        transport=owned_transport,
        headers={"accept-encoding": "identity", "connection": "close"},
        timeout=httpx.Timeout(timeout),
        trust_env=False,
        follow_redirects=False,
    )
    # Keep the actual socket/HTTP owners through abandoned or failed async
    # cleanup. Durable intent and event holds are retained independently.
    record["transport"], record["httpx_client"] = owned_transport, owned_client
    return owned_client


def _bind_prepared_native_request_v2(client, request):
    gate = _registered_client_gate(client)
    _need(gate is not None, "native_demo_write_client_not_owned")
    record = _checked_gate(gate)
    with record["lock"]:
        _bind_request(record, request)


def _require_native_private_write_v2(client, method, path):
    _need(_native_route_v2(client, method, path), "native_demo_write_client_not_owned")
    record = _checked_gate(_registered_client_gate(client))
    _need(
        record["phase"] == "prepared" and not record["transport_taken"],
        "native_demo_write_final_request_not_prepared",
    )
    _bind_request(record, record["request"], repeat=True)


def _native_leverage_configuration_route_v1(client, method, path):
    if type(client) is not _native_rest.OkxDemoPrivateRestClient:
        return False
    gate = _CLIENT_GATES.get(client)
    if gate is None:
        return False
    record = _GATES.get(gate)
    _need(type(record) is dict and record["client"] is client)
    if record.get("configuration_purpose") is not _LEVERAGE_CONFIGURATION_KEY_V1:
        return False
    _need(
        type(method) is str
        and method == "POST"
        and path == _native_leverage_configuration.PATH
    )
    _checked_gate(gate)
    return True


def _require_native_leverage_configuration_private_write_v1(client, method, path):
    _need(_native_leverage_configuration_route_v1(client, method, path))
    _require_native_private_write_v2(client, method, path)


def _require_native_leverage_configuration_client_v1(client, gate, payload):
    _need(type(gate) is _PhysicalWriteGateV2 and _CLIENT_GATES.get(client) is gate)
    record = _checked_gate(gate)
    _need(
        record.get("configuration_purpose") is _LEVERAGE_CONFIGURATION_KEY_V1
        and type(payload) is dict
        and payload
        == _native_leverage_configuration._request_document(
            record["original_transport_facts"]
        )
    )


def _retain_native_leverage_configuration_httpx_response_v1(client, response):
    _need(type(client) is _native_rest.OkxDemoPrivateRestClient)
    gate = _CLIENT_GATES.get(client)
    record = _GATES.get(gate) if type(gate) is _PhysicalWriteGateV2 else None
    _need(
        type(record) is dict
        and record["client"] is client
        and record.get("configuration_purpose") is _LEVERAGE_CONFIGURATION_KEY_V1
    )
    _native_leverage_configuration.retain_httpx_response_v1(record, response)


def _retire_gate(gate):
    if gate is None:
        return
    record = _GATES.get(gate)
    if type(record) is not dict or record["gate"] is not gate:
        raise NativeDemoWriteError("native_demo_write_gate_not_owned")
    # Retain the bounded native invocation and durable intent/tombstone. A
    # finalizer never deletes exchange evidence or makes the event retryable.
    if record["phase"] not in ("completed", "denied", "unknown"):
        record["phase"] = "unknown" if record["attempted"] else "denied"


async def _submit(state, absolute_deadline):
    from app.trade_qualification import native_account_risk_assembly_v1 as account
    from app.trade_qualification import native_ledger_runtime_v2 as ledger

    gate, response, failure, stop, transport_binding = None, None, None, None, None
    try:
        # One original absolute budget covers acquisition of the session and
        # locks, DNS, TLS, response read and transaction exit. It is never
        # renewed per operation or after the TLS publication/readback point.
        async with asyncio.timeout_at(absolute_deadline):
            facts = ledger._replay_native_intent_for_transport_v2(
                state, key=_INTENT_KEY
            )
            transport_binding = facts.transport_binding
            # The actual configured writer factory is replayed again inside
            # the helper; UID -> Control -> scope remain locked through IO.
            async with facts.session_factory() as session, session.begin():
                await ledger._lock_native_intent_for_transport_v2(
                    state, session=session, key=_INTENT_KEY
                )
                facts = ledger._replay_native_intent_for_transport_v2(
                    state, key=_INTENT_KEY
                )
                gate = _create_locked_gate_v2(state, session, key=_INTENT_KEY)
                context = facts.transport_context
                # Only the already sealed exact intent payload is decoded.
                payload = json.loads(facts.exact_order_json)
                response = await context.client.place_order_observed(payload)
                _need(
                    _GATES[gate]["phase"] == "completed",
                    "native_demo_write_full_transport_readback_unproven",
                )
                ledger._require_native_transport_lock_v2(
                    state, session=session, key=_INTENT_KEY
                )
    except BaseException as error:  # noqa: BLE001 - preserve unknown intent and control exceptions.
        # Preserve control exceptions after attempting conservative inhibition;
        # ordinary private exception text is never propagated or logged.
        failure = error
        attempted = gate is not None and _GATES[gate]["attempted"]
        if attempted and transport_binding is not None:
            stop = account._inhibit_issued_demo_transport_v2(
                transport_binding, key=_INTENT_KEY
            )
    finally:
        _retire_gate(gate)
        # Retiring a consumed intent keeps the durable reservation and exact
        # event tombstone. It never submits, retries, flattens or deletes state.
        ledger._discard_native_intent_for_transport_v2(state, key=_INTENT_KEY)
    if stop is not None:
        # The account/Control transaction above has exited: the safety latch
        # cannot deadlock itself by waiting on its own held account row lock.
        try:
            async with asyncio.timeout(5):
                await stop
        except BaseException:  # noqa: BLE001 - fail closed without exposing private exception text.
            raise NativeDemoWriteError(
                "native_demo_write_unknown_stop_unconfirmed"
            ) from None
    if failure is not None:
        if isinstance(failure, asyncio.CancelledError):
            raise asyncio.CancelledError() from None
        if isinstance(failure, (KeyboardInterrupt, SystemExit)):
            raise type(failure)() from None
        raise NativeDemoWriteError(
            "native_demo_write_submit_or_readback_unknown"
        ) from None
    # An ACK/response remains DATA. It does not assert a full fill, protection,
    # reconciliation, realized outcome, Demo acceptance or any retry authority.
    return response


class _SubmissionAttempt(Coroutine):
    """A synchronous MOVE must remain spent even if never awaited."""

    __slots__ = ("_body", "_closed", "_state")

    def __init__(self, state, absolute_deadline):
        self._state, self._body, self._closed = (
            state,
            _submit(state, absolute_deadline),
            False,
        )

    def __await__(self):
        return self

    def __iter__(self):
        return self

    def __next__(self):
        return self.send(None)

    def send(self, value):
        try:
            return self._body.send(value)
        except BaseException:
            self._closed = True
            _discard_attempt(self._state)
            raise

    def throw(self, *args):
        try:
            return self._body.throw(*args)
        except BaseException:
            self._closed = True
            _discard_attempt(self._state)
            raise

    def close(self):
        from app.trade_qualification import native_ledger_runtime_v2 as ledger

        if not self._closed:
            self._closed = True
            try:
                self._body.close()
            finally:
                ledger._discard_native_intent_for_transport_v2(
                    self._state, key=_INTENT_KEY
                )


def _discard_attempt(state):
    from app.trade_qualification import native_ledger_runtime_v2 as ledger

    ledger._discard_native_intent_for_transport_v2(state, key=_INTENT_KEY)


def _original_absolute_deadline(state):
    from app.trade_qualification import native_ledger_runtime_v2 as ledger

    # Sample the loop clock BEFORE the native UTC/monotonic pair. Conversion
    # therefore shortens the original budget; it cannot start a new lease.
    loop_before = asyncio.get_running_loop().time()
    stamp = native_stamp()
    facts = ledger._replay_native_intent_for_transport_v2(state, key=_INTENT_KEY)
    context = facts.transport_context
    _need(type(context) is _DemoTransportContextV2)
    utc_remaining = min(facts.expires_at, context.original_expires_at) - utc_from_ns(
        stamp["utc_ns"]
    )
    utc_ns = (
        (utc_remaining.days * 86400 + utc_remaining.seconds) * 1_000_000
        + utc_remaining.microseconds
    ) * 1000
    remaining_ns = min(
        utc_ns,
        facts.expiry_monotonic_ns - stamp["monotonic_ns"],
        context.monotonic_deadline_ns - stamp["monotonic_ns"],
    )
    _need(remaining_ns > 0, "native_demo_write_original_expiry")
    return math.nextafter(loop_before + remaining_ns / 1_000_000_000, -math.inf)


def submit_owned_native_demo_v2(owner):
    """Consume the genuine same-task native intent before the first await.

    Source closure and the shared private REST integration must be admitted
    before installation. Missing native hooks have no passing fallback.
    """
    from app.trade_qualification import native_ledger_runtime_v2 as ledger

    state = ledger._take_native_intent_for_transport_v2(owner, key=_INTENT_KEY)
    try:
        return _SubmissionAttempt(state, _original_absolute_deadline(state))
    except BaseException:
        ledger._discard_native_intent_for_transport_v2(state, key=_INTENT_KEY)
        raise


def submit_owned_native_demo_close_v1(owner):
    """One owned negative intent through the SAME shared POST/TLS gate."""
    _NATIVE_GUARD()
    candidates = [
        record
        for record in _GATES.values()
        if type(record) is dict
        and record.get("close_purpose") is not _CLOSE_INTENT_KEY_V1
        and "close_api" in record
        and type(owner) is record["close_api"][1]
    ]
    _need(len(candidates) > 0, "native_demo_close_original_entry_not_owned")
    view = _close_function_v1(
        candidates[0], "_peek_owned_native_close_intent_for_root_v1"
    )(owner, key=_CLOSE_INTENT_KEY_V1)
    entry = _GATES.get(view.filled_result_view.response_handle)
    _need(
        type(entry) is dict
        and any(actual is entry for actual in candidates)
        and entry["observed_result"] is view.filled_result_view.observed_result
    )
    api = _close_api_v1(entry)
    # Convert the already committed deadline before MOVE. Source binding is
    # performed inside the returned attempt, so its failure still reaches the
    # real negative inhibitor and bounded persistent stop after locks exit.
    loop_before = asyncio.get_running_loop().time()
    stamp = native_stamp()
    budget = view.intent_readback[4]
    remaining = min(
        (budget["expires_utc_ns"] - stamp["utc_ns"]) / 1_000_000_000,
        (budget["expires_monotonic_ns"] - stamp["monotonic_ns"]) / 1_000_000_000,
    )
    _need(
        math.isfinite(remaining) and 0 < remaining <= 10,
        "native_demo_close_committed_budget_expired",
    )
    state = _close_function_v1(entry, "_take_owned_native_close_intent_for_root_v1")(
        owner, key=_CLOSE_INTENT_KEY_V1
    )
    state["root_close_api"] = api
    holder = {"close_api": api}
    try:
        attempt = _CloseSubmissionAttemptV1(
            state, api, math.nextafter(loop_before + remaining, -math.inf)
        )
        _CLOSE_ATTEMPTS_V1[owner] = attempt
        return attempt
    except BaseException as error:  # noqa: BLE001 -- Retire the consumed close intent on any setup exit.
        _close_function_v1(holder, "_discard_owned_native_close_intent_for_root_v1")(
            state, key=_CLOSE_INTENT_KEY_V1
        )
        if isinstance(error, asyncio.CancelledError):
            raise asyncio.CancelledError() from None
        if isinstance(error, (KeyboardInterrupt, SystemExit)):
            raise type(error)() from None
        raise NativeDemoWriteError(
            "native_demo_close_source_or_budget_unknown"
        ) from None


class _CloseSubmissionAttemptV1(Coroutine):
    def __init__(self, state, api, absolute_deadline):
        self.state, self.api = state, api
        self.body = _submit_close_v1(state, api, absolute_deadline)

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
        try:
            return self.body.close()
        finally:
            _close_function_v1(
                {"close_api": self.api},
                "_discard_owned_native_close_intent_for_root_v1",
            )(self.state, key=_CLOSE_INTENT_KEY_V1)


async def _submit_close_v1(state, api, absolute_deadline):
    holder = {"close_api": api}
    gate, response, failure, stop = None, None, None, None
    try:
        async with asyncio.timeout_at(absolute_deadline):
            facts = _close_function_v1(holder, "_bind_owned_native_close_transport_v1")(
                state, key=_CLOSE_INTENT_KEY_V1
            )
            async with facts.session_factory() as session, session.begin():
                await _close_function_v1(
                    holder, "_lock_owned_native_close_transport_v1"
                )(state, session=session, key=_CLOSE_INTENT_KEY_V1)
                gate = _create_close_locked_gate_v1(state, session)
                record = _GATES[gate]
                response = await record["client"].place_order_observed(
                    json.loads(record["original_transport_facts"].exact_order_json)
                )
                _need(
                    record["phase"] == "completed",
                    "native_demo_close_transport_unknown",
                )
                _require_record_lock_v1(record)
    except BaseException as error:  # noqa: BLE001 -- retain close intent/entry/received bytes and redact private errors.
        failure = error
    finally:
        _retire_gate(gate)
        _close_function_v1(holder, "_discard_owned_native_close_intent_for_root_v1")(
            state, key=_CLOSE_INTENT_KEY_V1
        )
    if failure is not None:
        if isinstance(failure, GeneratorExit):
            # Synchronous close cannot await DB stop/rollback completion.
            # All remaining owners/actual journals are already strongly held.
            raise failure
        # All account transactions have exited before persistent safety latch.
        # Known received bytes remain in the exact Root journal before any
        # late expiry/close/error check; they are not an ACK/release permit.
        try:
            stop = _close_function_v1(
                holder, "_inhibit_owned_native_close_transport_v1"
            )(state, key=_CLOSE_INTENT_KEY_V1)
            async with asyncio.timeout(5):
                await stop
        except BaseException:  # noqa: BLE001 -- strong UNKNOWN, no second attempt or private exception text.
            raise NativeDemoWriteError(
                "native_demo_close_unknown_stop_unconfirmed"
            ) from None
        if isinstance(failure, asyncio.CancelledError):
            raise asyncio.CancelledError() from None
        if isinstance(failure, (KeyboardInterrupt, SystemExit)):
            raise type(failure)() from None
        raise NativeDemoWriteError(
            "native_demo_close_submit_or_readback_unknown"
        ) from None
    return response


def _retain_native_socket_owners_v1(gate, backend, native, *, key):
    _NATIVE_GUARD()
    return _native_settlement.retain_native_socket_owners_v1(
        gate, backend, native, key=key
    )


async def close_owned_native_demo_transport_after_flat_v1(result):
    _NATIVE_GUARD()
    value = await _native_settlement.close_owned_native_demo_transport_after_flat_v1(
        result
    )
    _NATIVE_GUARD()
    return value


def _take_native_closed_transport_for_settlement_v1(handle, *, key):
    _NATIVE_GUARD()
    return _native_settlement._take_native_closed_transport_for_settlement_v1(
        handle, key=key
    )


def _replay_native_closed_transport_for_settlement_v1(handle, *, key):
    _NATIVE_GUARD()
    return _native_settlement._replay_native_closed_transport_for_settlement_v1(
        handle, key=key
    )


def _finalize_native_closed_transport_settlement_v1(handle, completion, *, key):
    _NATIVE_GUARD()
    return _native_settlement._finalize_native_closed_transport_settlement_v1(
        handle, completion, key=key
    )


async def close_owned_native_demo_transports_after_filled_flat_v1(result):
    _NATIVE_GUARD()
    value = await _native_filled_settlement.close_owned_native_demo_transports_after_filled_flat_v1(
        result
    )
    _NATIVE_GUARD()
    return value


def _take_native_closed_transports_for_settlement_v1(handle, *, key):
    _NATIVE_GUARD()
    return _native_filled_settlement._take_native_closed_transports_for_settlement_v1(
        handle, key=key
    )


def _replay_native_closed_transports_for_settlement_v1(handle, *, key):
    _NATIVE_GUARD()
    return _native_filled_settlement._replay_native_closed_transports_for_settlement_v1(
        handle, key=key
    )


def _finalize_native_closed_transports_settlement_v1(handle, completion, *, key):
    _NATIVE_GUARD()
    return _native_filled_settlement._finalize_native_closed_transports_settlement_v1(
        handle, completion, key=key
    )


def _retire_native_closed_transports_after_source_v1(handle, retirement, *, key):
    _NATIVE_GUARD()
    return _native_filled_settlement._retire_native_closed_transports_after_source_v1(
        handle, retirement, key=key
    )


def _replay_native_finalized_closed_transports_for_source_v1(handle, *, key):
    _NATIVE_GUARD()
    return _native_filled_settlement._replay_native_finalized_closed_transports_for_source_v1(
        handle, key=key
    )


# Fixed original aliases; the helper declares the configuration purpose and
# stores its own genuine actor/response/closed registries, never order DTOs.
submit_owned_native_demo_leverage_configuration_v1 = (
    _native_leverage_configuration.submit_owned_native_demo_leverage_configuration_v1
)
_native_leverage_configuration_retained_buffers_v1 = (
    _native_leverage_configuration._native_leverage_configuration_retained_buffers_v1
)
_native_leverage_configuration_scan_tokens_v1 = (
    _native_leverage_configuration._native_leverage_configuration_scan_tokens_v1
)
_take_native_leverage_configuration_response_v1 = (
    _native_leverage_configuration._take_native_leverage_configuration_response_v1
)
_replay_native_leverage_configuration_response_v1 = (
    _native_leverage_configuration._replay_native_leverage_configuration_response_v1
)
_observe_native_closed_leverage_configuration_v1 = (
    _native_leverage_configuration._observe_native_closed_leverage_configuration_v1
)
_take_native_closed_leverage_configuration_v1 = (
    _native_leverage_configuration._take_native_closed_leverage_configuration_v1
)
_replay_native_closed_leverage_configuration_v1 = (
    _native_leverage_configuration._replay_native_closed_leverage_configuration_v1
)
_finalize_native_leverage_configuration_cleanup_v1 = (
    _native_leverage_configuration._finalize_native_leverage_configuration_cleanup_v1
)
_retire_native_leverage_configuration_after_source_v1 = (
    _native_leverage_configuration._retire_native_leverage_configuration_after_source_v1
)


# Pristine admission establishes only these finite dependency bindings. The
# real intent/Control/session registries remain independently required. The
# corrected continuation guard checks the exact bindings on every resume,
# before any post-await vendor method can be selected or invoked.
_VENDOR_ADMISSION_V2 = _native_vendors.admit_native_demo_vendors_v2()
_NATIVE_GUARD = _native_guard.install_native_guards_v1(
    _native_sys.modules[__name__],
    (
        "submit_owned_native_demo_v2",
        "submit_owned_native_demo_close_v1",
        "submit_owned_native_demo_leverage_configuration_v1",
        "_native_leverage_configuration_retained_buffers_v1",
        "_native_leverage_configuration_scan_tokens_v1",
        "_take_native_leverage_configuration_response_v1",
        "_replay_native_leverage_configuration_response_v1",
        "_observe_native_closed_leverage_configuration_v1",
        "_take_native_closed_leverage_configuration_v1",
        "_replay_native_closed_leverage_configuration_v1",
        "_finalize_native_leverage_configuration_cleanup_v1",
        "_retire_native_leverage_configuration_after_source_v1",
        "close_owned_native_demo_transport_after_flat_v1",
        "close_owned_native_demo_transports_after_filled_flat_v1",
        "_take_native_closed_transports_for_settlement_v1",
        "_replay_native_closed_transports_for_settlement_v1",
        "_finalize_native_closed_transports_settlement_v1",
        "_retire_native_closed_transports_after_source_v1",
        "_replay_native_finalized_closed_transports_for_source_v1",
        "_retain_native_socket_owners_v1",
        "_take_native_closed_transport_for_settlement_v1",
        "_replay_native_closed_transport_for_settlement_v1",
        "_finalize_native_closed_transport_settlement_v1",
        "_take_native_close_response_for_reconciliation_v1",
        "_take_native_close_attempt_for_reconciliation_v1",
        "_replay_native_close_response_for_reconciliation_v1",
        "_native_source_retained_buffers_v1",
    ),
    dependencies=(
        _native_transport,
        _native_settlement,
        _native_filled_settlement,
        _native_leverage_configuration,
        _native_leverage_configuration.contracts,
        _native_vendors,
        _native_source_identity,
        _native_rest,
        _native_rest.reconciliation_read,
        _native_rest.reconciliation_read.network_custody,
        _native_account,
        _native_ledger,
        _native_ledger._native_catalog55,
        _native_ledger._native_catalog55.policy,
        _native_ledger._native_catalog55.policy.native_close_intent,
        _native_ledger._native_catalog55.policy.native_close_index,
        _native_ledger._native_catalog55.policy.native_closure,
        _native_ledger._native_catalog55.policy.native_closure.zero,
        _native_ledger._native_catalog55.policy.native_terminal,
        _native_ledger._native_catalog55.policy.native_terminal.filled,
        _native_ledger._native_catalog55.policy.native_report,
        _native_ledger._native_catalog55.policy.native_report.schema,
        _native_ledger._native_catalog55.policy.native_report.helpers,
        _native_ledger._zero_event55,
        _native_ledger._zero_event55.schema,
        _native_ledger._zero_event55.live_schema,
    ),
    bindings=_VENDOR_ADMISSION_V2.bindings,
)
