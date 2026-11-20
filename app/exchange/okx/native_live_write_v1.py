"""Owned one-shot Live FOK boundary; legacy Live paths remain denied.

No constructor, DTO, saved receipt, settings switch or caller boolean grants
permission. The fixed owned qualification coordinator must replay and spend its
original registry entry. Actual SQL fences remain held through native SSL and
socket writes. There are no order retries, automatic close or capital scaling.
"""

from __future__ import annotations

import asyncio
import importlib
import os
import ssl
import threading
from contextvars import ContextVar
from fractions import Fraction
from pathlib import Path
from types import FunctionType, ModuleType
from urllib.parse import urlsplit
from weakref import WeakKeyDictionary, ref

import h11
import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, AsyncSessionTransaction

from app.database.repositories import native_live_ledger_v1 as ledger
from app.domain.native_live_intent_v1 import (
    checked_live_intent_data_v1,
    exact_live_intent_document_v1,
    live_account_lock_key_v1,
)
from app.domain.native_live_legacy_safety_v1 import (
    require_legacy_live_negative_fence_v1,
)
from app.domain.source_primitives import (
    ClockStamp,
    canonical,
    decode,
    sha,
    utc_from_ns,
    validate_stamps,
)
from app.exchange.okx import native_live_transport_v1 as transport
from app.exchange.okx.live_account_config_observation_v1 import (
    _check_no_credential_echo,
    _checked_response_headers,
    _stamp,
)
from app.exchange.okx.private_rest import build_signature
from app.okx_live import trusted_source_bridge_v1 as source
from app.trade_qualification.native_continuation_guard_v1 import (
    check_saved_native_guard_identity_v2,
    save_native_guard_v1,
)

_QUALIFICATION_SOURCE_KEY_V1 = object()
_CREDENTIAL_SOURCE_KEY_V1 = object()
_INTENT_KEY = object()
_GATE_KEY = object()
_GATES = WeakKeyDictionary()
_HELD = WeakKeyDictionary()
_NEGATIVE_TASKS = set()
_ACTIVE = ContextVar("ctcc_owned_live_physical_v1", default=None)
_ORDER_PATH = "/api/v5/trade/order"
_MAX_ENTRY_WIRE_V2 = 16 * 1024
_MAX_ENTRY_WIRE_CHUNKS_V2 = 16
_MAX_RESPONSE_READ_NS_V2 = 10_000_000_000
_COORDINATOR = "app.okx_live.owned_qualification_v1"
# This existing API-integrity cache has one source-owned mutable slot. Its
# namespace binding never changes and therefore remains fully source guarded.
# It contains no qualification, clock, risk or execution result.
_COORDINATOR_BINDINGS = [None]
_COORDINATOR_CELL_IDENTITY = _COORDINATOR_BINDINGS
_COORDINATOR_PATH = (
    Path(__file__).resolve().parents[3] / "app/okx_live/owned_qualification_v1.py"
)
MICRO_NOTIONAL_HARD_CAP_USDT = Fraction(25)
MICRO_STOP_RISK_HARD_CAP_USDT = Fraction(1, 4)
_API = (
    "_replay_owned_live_qualification_v1",
    "_consume_owned_live_qualification_v1",
    "_verify_spent_live_final_dispatch_v1",
    "_inhibit_owned_live_qualification_v1",
)


class NativeLiveWriteError(ValueError):
    pass


def _need(condition, code="native_live_physical_authority_unavailable"):
    if condition is not True:
        raise NativeLiveWriteError(code) from None


def _coordinator_api():
    _need(
        type(_COORDINATOR_BINDINGS) is list
        and _COORDINATOR_BINDINGS is _COORDINATOR_CELL_IDENTITY
        and len(_COORDINATOR_BINDINGS) == 1,
        "native_live_coordinator_cell_changed",
    )
    try:
        module = importlib.import_module(_COORDINATOR)
    except Exception:  # noqa: BLE001 -- no optional fallback can authorize a write.
        _need(False)
    _need(type(module) is ModuleType and module.__name__ == _COORDINATOR)
    calls = tuple(getattr(module, name, None) for name in _API)
    _need(
        all(
            type(call) is FunctionType
            and type(call.__module__) is str
            and call.__module__ == _COORDINATOR
            for call in calls
        )
        and type(getattr(module, "LiveQualifiedExecutionFactsV1", None)) is type
    )
    _need(
        type(getattr(module, "__file__", None)) is str
        and Path(module.__file__).resolve() == _COORDINATOR_PATH
        and _COORDINATOR_PATH.is_file()
    )
    raw = _COORDINATOR_PATH.read_bytes()
    _need(0 < len(raw) <= 2 * 1024 * 1024)
    digest = sha(raw)
    cached = _COORDINATOR_BINDINGS[0]
    if cached is not None:
        _need(
            type(cached) is tuple and len(cached) == 2,
            "native_live_coordinator_cache_changed",
        )
        original, actual_digest = cached
        _need(
            type(original) is tuple
            and len(original) == 4
            and type(original[1]) is tuple
            and len(original[1]) == len(_API)
            and type(original[2]) is tuple
            and len(original[2]) == len(_API)
            and type(actual_digest) is str,
            "native_live_coordinator_cache_changed",
        )
        _need(
            actual_digest == digest
            and original[0] is module
            and original[3] is module.LiveQualifiedExecutionFactsV1
            and all(a is b for a, b in zip(original[1], calls, strict=True))
        )
        return original
    from app.trade_qualification.native_continuation_guard_v1 import _source_code

    for call in calls:
        _need(Path(call.__code__.co_filename).resolve() == _COORDINATOR_PATH)
        _source_code(call, raw, call.__code__.co_filename)
    pins = tuple(
        (
            call.__code__,
            call.__defaults__,
            call.__kwdefaults__,
            call.__closure__,
            tuple(cell.cell_contents for cell in call.__closure__ or ()),
            call.__builtins__,
            tuple(
                (name, call.__globals__[name])
                for name in call.__code__.co_names
                if name in call.__globals__
            ),
        )
        for call in calls
    )
    original = module, calls, pins, module.LiveQualifiedExecutionFactsV1
    _COORDINATOR_BINDINGS[0] = original, digest
    return original


def _require_api_integrity_v2(api):
    _need(type(api) is tuple and len(api) == 4)
    module, calls, pins, kind = api
    _need(
        type(module) is ModuleType
        and type(calls) is tuple
        and len(calls) == len(_API)
        and type(pins) is tuple
        and len(pins) == len(_API)
    )
    actual, current, _, actual_kind = _coordinator_api()
    _need(
        actual is module
        and actual_kind is kind
        and all(a is b for a, b in zip(calls, current, strict=True))
    )
    for call, pin in zip(calls, pins, strict=True):
        _need(type(pin) is tuple and len(pin) == 7)
        code, defaults, kwdefaults, closure, cells, builtins, namespace = pin
        _need(
            type(cells) is tuple
            and type(namespace) is tuple
            and all(
                type(item) is tuple and len(item) == 2 and type(item[0]) is str
                for item in namespace
            )
        )
        _need(
            call.__code__ is code
            and call.__defaults__ is defaults
            and call.__kwdefaults__ is kwdefaults
            and call.__closure__ is closure
            and call.__builtins__ is builtins
            and all(
                cell.cell_contents is value
                for cell, value in zip(call.__closure__ or (), cells, strict=True)
            )
            and all(call.__globals__.get(name) is value for name, value in namespace)
        )
    return module, calls


def _api_call(api, index, owner, **kwargs):
    _module, calls = _require_api_integrity_v2(api)
    return calls[index](owner, key=_QUALIFICATION_SOURCE_KEY_V1, **kwargs)


def _same_facts(value, other):
    names = (
        "intent_data",
        "intent_readback",
        "reservation_id",
        "request_json",
        "request_sha256",
        "session_factory",
        "role_pins",
        "source_owner",
        "public_owner",
        "deadline_utc",
        "expiry_monotonic_ns",
        "credential_session_sha256",
        "account_uid",
        "main_uid",
        "arm_id",
    )
    _need(
        type(value) is type(other)
        and all(
            getattr(value, name) is getattr(other, name)
            or (
                name
                not in ("session_factory", "role_pins", "source_owner", "public_owner")
                and getattr(value, name) == getattr(other, name)
            )
            for name in names
        )
    )


def _facts(owner, api, *, spent=False):
    module = api[0]
    value = _api_call(api, 2 if spent else 0, owner)
    _need(type(value) is module.LiveQualifiedExecutionFactsV1)
    intent = checked_live_intent_data_v1(value.intent_data)
    rid, raw, _ = exact_live_intent_document_v1(intent)
    doc = decode(raw, maximum=65536)
    _need(
        value.reservation_id == rid
        and type(value.request_json) is bytes
        and value.request_json == doc["request_json"].encode("ascii")
        and value.request_sha256 == doc["request_sha256"] == sha(value.request_json)
        and type(value.intent_readback) is bytes
        and canonical(decode(value.intent_readback, maximum=8192))
        == value.intent_readback
        and (
            value.account_uid,
            value.main_uid,
            value.credential_session_sha256,
            value.arm_id,
        )
        == (
            intent.account_uid,
            intent.main_uid,
            intent.credential_session_sha256,
            intent.arm_id,
        )
        and type(value.expiry_monotonic_ns) is int
        and type(value.deadline_utc) is type(intent.deadline)
        and value.deadline_utc.tzinfo is intent.deadline.tzinfo
        and value.deadline_utc <= intent.deadline
    )
    now = _stamp()
    _need(
        now.monotonic_ns < value.expiry_monotonic_ns
        and utc_from_ns(now.utc_ns) < value.deadline_utc,
        "native_live_physical_expired",
    )
    view = ledger._source(intent, value.source_owner)
    _hard_micro(intent, view)
    return value


def _number(row, name):
    from decimal import Decimal

    text_value = row.get(name)
    _need(type(text_value) is str and text_value and len(text_value) <= 80)
    try:
        value = Decimal(text_value)
    except Exception:  # noqa: BLE001 -- provider numbers are private.
        _need(False)
    _need(value.is_finite() and value > 0)
    return Fraction(value)


def _hard_micro(value, view):
    _need(
        value.margin_mode == "isolated"
        and 1 <= value.leverage <= 3
        and Fraction(value.notional_amount) <= MICRO_NOTIONAL_HARD_CAP_USDT
        and Fraction(max(value.risk_samples)) <= MICRO_STOP_RISK_HARD_CAP_USDT
    )
    specs = []
    leverage = []
    for page in view["current_pages"]:
        selector = page.selector
        if (
            selector.stream == "account_instruments"
            and selector.instrument_id == value.instrument_id
        ):
            specs.extend(source.current._rows(page.raw_body))
        elif selector.stream == "leverage_isolated":
            leverage.extend(source.current._rows(page.raw_body))
    _need(len(specs) == 1)
    row = specs[0]
    _need(
        row.get("instId") == value.instrument_id
        and row.get("instType") == "SWAP"
        and row.get("ctType") == "linear"
        and row.get("settleCcy") == "USDT"
        and row.get("state") == "live"
        and _number(row, "ctMult") == 1
    )
    quantity = Fraction(value.contracts)
    lot, minimum, maximum, tick = (
        _number(row, name) for name in ("lotSz", "minSz", "maxLmtSz", "tickSz")
    )
    price = Fraction(value.executable_price_bound)
    _need(
        minimum <= quantity <= maximum
        and (quantity / lot).denominator == 1
        and (price / tick).denominator == 1
    )
    currency = row.get("ctValCcy")
    base = value.instrument_id.split("-", 1)[0]
    contract = _number(row, "ctVal")
    if currency == base:
        base_quantity = quantity * contract
        notional = base_quantity * price
    elif currency == "USDT":
        notional = quantity * contract
        base_quantity = notional / price
    else:
        _need(False)
    stop = Fraction(value.stop_loss)
    _need(
        (stop < price if value.direction == "long" else stop > price)
        and notional <= Fraction(value.notional_amount) <= MICRO_NOTIONAL_HARD_CAP_USDT
        and notional / value.leverage <= Fraction(value.margin_amount)
        and base_quantity * abs(price - stop) <= Fraction(max(value.risk_samples))
    )
    matching = [
        item
        for item in leverage
        if item.get("instId") == value.instrument_id
        and item.get("mgnMode") == "isolated"
        and item.get("posSide")
        in (("net", "") if value.position_side == "net" else (value.position_side,))
    ]
    _need(
        len(matching) == 1 and _number(matching[0], "lever") == value.leverage,
        "native_live_exact_leverage_unverified",
    )


def _require_vendor_state_v1():
    _need(
        all(
            getattr(owner, name) is original
            for owner, name, original in transport._VENDOR_BINDINGS_V1
        ),
        "native_live_transport_vendor_changed",
    )
    for (
        call,
        code,
        defaults,
        kwdefaults,
        closure,
        cells,
        builtins,
        namespace,
    ) in transport._VENDOR_CODE_BINDINGS_V1:
        _need(
            call.__code__ is code
            and call.__defaults__ is defaults
            and call.__kwdefaults__ is kwdefaults
            and call.__closure__ is closure
            and call.__builtins__ is builtins
            and all(
                cell.cell_contents is value
                for cell, value in zip(call.__closure__ or (), cells, strict=True)
            )
            and all(call.__globals__.get(name) is value for name, value in namespace),
            "native_live_transport_vendor_changed",
        )


def _record(gate):
    _need(type(gate) is _PhysicalLiveWriteGateV1)
    record = _GATES.get(gate)
    _need(
        type(record) is dict
        and record["gate"]() is gate
        and record["pid"] == os.getpid()
        and record["thread"] == threading.get_ident()
        and record["task"] is asyncio.current_task()
        and record["loop"] is asyncio.get_running_loop()
    )
    return record


def _require_held_gate_scope_v2(gate, *, key):
    """Structural ownership only; never an execution qualification result."""
    _need(key is transport._TRANSPORT_KEY)
    record = _record(gate)
    _need(
        _ACTIVE.get() is gate
        and record["phase"] in ("prepared", "attempted")
        and type(record["session"]) is AsyncSession
        and type(record["transaction"]) is AsyncSessionTransaction
        and record["session"].in_transaction()
        and record["session"].sync_session.get_transaction()
        is record["transaction"].sync_transaction
        and _HELD.get(record["session"]) is record
        and record.get("legacy_fence_held") is True
        and record.get("consumed") is True
    )
    _require_vendor_state_v1()
    return record


def _require_preparation_scope_v2(gate, *, key):
    """Current owner/configuration liveness for non-dispatch preparation DATA.

    This never reuses a positive mathematical qualification. Every plaintext
    and native socket write still invokes the full, current authority below.
    """
    record = _require_held_gate_scope_v2(gate, key=key)
    module, _calls = _require_api_integrity_v2(record["api"])
    _need(module._NATIVE_GUARD is record["native_source_guard"])
    check_saved_native_guard_identity_v2(record)
    _require_preparation_prefix_v2(record, module)
    facts = record["facts"]
    qualification = module._QUALIFIED.get(record["owner"])
    _need(
        type(qualification) is dict
        and qualification is record["preparation_qualification"]
        and qualification["facts"] is facts
        and qualification["spent"] is True
        and qualification["revoked"] is False
        and qualification["session"] is record["preparation_session"]
    )
    selected_state = module._OWNED.get(qualification["session"])
    _need(
        type(selected_state) is dict
        and selected_state["source_owner"] is facts.source_owner
        and selected_state["current_public_owner"] is facts.public_owner
    )
    state = module._session(qualification["session"], current=True)
    _need(
        state is selected_state
        and state["active"] is True
        and "inhibit_code" not in state
        and state["source_owner"] is facts.source_owner
        and state["current_public_owner"] is facts.public_owner
    )
    cfg = module.runtime._replay_live_runtime_v1(
        state["runtime"], key=module.runtime._KEY
    )
    _need(
        cfg["writer_factory"] is facts.session_factory
        and cfg["writer_pins"] is facts.role_pins
    )
    view = source._SOURCES.get(facts.source_owner)
    _need(
        type(view) is dict
        and (
            view["uid"],
            view["main_uid"],
            view["credential_session_sha256"],
            view["source_revision_sha256"],
        )
        == (
            facts.account_uid,
            facts.main_uid,
            facts.credential_session_sha256,
            facts.intent_data.source_revision_sha256,
        )
    )
    public = module.public._OWNERS.get(facts.public_owner)
    _need(type(public) is dict)
    module.public._owned(public)
    params = public["params"]
    _need(
        params.invocation is state["invocation"]
        and params.report_id == state["report_id"]
        and params.instrument_id == facts.intent_data.instrument_id
        and params.stage == "post_g12"
    )
    rid, raw, digest = exact_live_intent_document_v1(facts.intent_data)
    _need(
        (
            rid,
            raw,
            digest,
            facts.intent_readback,
            facts.request_json,
            facts.request_sha256,
        )
        == record["preparation_lineage"]
        and state["control"] == record["control"]
        and module._decode_control(state["control_readback"]) == state["control"]
    )
    ledger._control(record["control"], facts.intent_data, utc_from_ns(_stamp().utc_ns))
    return record


def _capture_preparation_prefix_v2(module):
    """Fixed canonical function/global bindings only, never eligibility DATA."""
    pins = []
    for name in ("_session", "_task", "_need", "_NATIVE_GUARD"):
        call = vars(module)[name]
        _need(type(call) is FunctionType)
        saved = {}
        save_native_guard_v1(saved, call)
        names = call.__code__.co_names
        globals_pin = tuple(
            (key, key in call.__globals__, call.__globals__.get(key)) for key in names
        )
        builtins_pin = tuple(
            (key, call.__builtins__[key]) for key in names if key in call.__builtins__
        )
        pins.append(
            (name, call, saved["native_source_guard_pin"], globals_pin, builtins_pin)
        )
    return tuple(pins)


def _require_retained_response_identity_v2(gate, *, key):
    """Original negative owner identity only; no current execution eligibility.

    A complete already-submitted request may receive DATA after disarm, EStop,
    revocation or qualification expiry. The original task/context, held database
    transaction, source/configuration/credential/API/vendor and immutable intent
    lineage must still match. This scope cannot reach any actual write method.
    """
    record = _require_held_gate_scope_v2(gate, key=key)
    module, _calls = _require_api_integrity_v2(record["api"])
    _need(module._NATIVE_GUARD is record["native_source_guard"])
    check_saved_native_guard_identity_v2(record)
    _require_preparation_prefix_v2(record, module)
    facts = record["facts"]
    qualification = module._QUALIFIED.get(record["owner"])
    _need(
        type(qualification) is dict
        and qualification is record["preparation_qualification"]
        and qualification["facts"] is facts
        and qualification["spent"] is True
        and type(qualification["revoked"]) is bool
        and qualification["session"] is record["preparation_session"]
    )
    selected_state = module._OWNED.get(qualification["session"])
    _need(
        type(selected_state) is dict
        and selected_state["source_owner"] is facts.source_owner
        and selected_state["current_public_owner"] is facts.public_owner
    )
    # The ORIGINAL non-current session path checks task/cancellation, runtime,
    # settings, credential lease, configured source and causal clocks. It does
    # not replay math or require the old Arm/quote/deadline to remain eligible.
    state = module._session(qualification["session"])
    _need(state is selected_state)
    cfg = module.runtime._replay_live_runtime_v1(
        state["runtime"], key=module.runtime._KEY
    )
    _need(
        cfg["writer_factory"] is facts.session_factory
        and cfg["writer_pins"] is facts.role_pins
    )
    view = source._SOURCES.get(facts.source_owner)
    _need(
        type(view) is dict
        and (
            view["uid"],
            view["main_uid"],
            view["credential_session_sha256"],
            view["source_revision_sha256"],
        )
        == (
            facts.account_uid,
            facts.main_uid,
            facts.credential_session_sha256,
            facts.intent_data.source_revision_sha256,
        )
    )
    public = module.public._OWNERS.get(facts.public_owner)
    _need(type(public) is dict)
    module.public._owned(public)
    params = public["params"]
    _need(
        params.invocation is state["invocation"]
        and params.report_id == state["report_id"]
        and params.instrument_id == facts.intent_data.instrument_id
        and params.stage == "post_g12"
    )
    rid, raw, digest = exact_live_intent_document_v1(facts.intent_data)
    _need(
        (
            rid,
            raw,
            digest,
            facts.intent_readback,
            facts.request_json,
            facts.request_sha256,
        )
        == record["preparation_lineage"]
    )
    return record


def _require_preparation_prefix_v2(record, module):
    # Validate the exact original prefix BEFORE invoking _session, which then
    # executes the genuine source guard itself. A foreign _session/_task/_need
    # must not run even though the identity-only helper grants no authority.
    entries = record["preparation_prefix"]
    names = ("_session", "_task", "_need", "_NATIVE_GUARD")
    _need(type(entries) is tuple and len(entries) == len(names))
    for expected_name, entry in zip(names, entries, strict=True):
        _need(type(entry) is tuple and len(entry) == 5)
        name, call, pin, globals_pin, builtins_pin = entry
        _need(
            type(name) is str
            and name == expected_name
            and type(call) is FunctionType
            and type(globals_pin) is tuple
            and type(builtins_pin) is tuple
            and all(
                type(item) is tuple
                and len(item) == 3
                and type(item[0]) is str
                and type(item[1]) is bool
                for item in globals_pin
            )
            and all(
                type(item) is tuple and len(item) == 2 and type(item[0]) is str
                for item in builtins_pin
            )
        )
        _need(vars(module).get(name) is call)
        check_saved_native_guard_identity_v2(
            {"native_source_guard": call, "native_source_guard_pin": pin}
        )
        _need(
            all(
                (key in call.__globals__) is present
                and call.__globals__.get(key) is value
                for key, present, value in globals_pin
            )
            and all(call.__builtins__.get(key) is value for key, value in builtins_pin)
        )


def _require_physical_gate_v1(gate, *, key):
    record = _require_held_gate_scope_v2(gate, key=key)
    _same_facts(record["facts"], _facts(record["owner"], record["api"], spent=True))
    now = utc_from_ns(_stamp().utc_ns)
    ledger._control(record["control"], record["facts"].intent_data, now)
    from app.okx_live.entry_owned_seal_v1 import require_native_final_clock_v1

    require_native_final_clock_v1(record["owner"], key=_QUALIFICATION_SOURCE_KEY_V1)
    return record


def _require_live_source_access_v1(owner, *, key):
    _need(key is _CREDENTIAL_SOURCE_KEY_V1 and _ACTIVE.get() is not None)
    record = _require_physical_gate_v1(_ACTIVE.get(), key=transport._TRANSPORT_KEY)
    _need(
        record["facts"].source_owner is owner
        and record["session"].in_transaction()
        and record["session"].sync_session.get_transaction()
        is record["transaction"].sync_transaction
        and record["phase"] in ("prepared", "attempted")
    )


def _retained(gate, key):
    _need(key is transport._TRANSPORT_KEY)
    return _record(gate)


def _capture_abort_pins_v1(gate):
    record = _require_preparation_scope_v2(gate, key=transport._TRANSPORT_KEY)
    types = tuple(
        (kind, call)
        for kind, name, call in transport._VENDOR_BINDINGS_V1
        if name == "abort"
    )

    def abort(native):
        for kind, call in types:
            if type(native) is kind:
                try:
                    call(native)
                    return True
                except BaseException:  # noqa: BLE001 -- negative-only socket abort never grants permission.
                    record["phase"] = "unknown"
                    record["cleanup_corrupt_socket"] = native
                    return False
        record["phase"] = "unknown"
        record["cleanup_corrupt_socket"] = native
        return False

    return abort, record


def _retain_native_socket_owners_v1(gate, backend, native, *, key):
    record = _retained(gate, key)
    _need(record.get("native_socket") is None)
    record["native_socket"], record["backend"] = native, backend
    record["socket_transport"] = native._stream._transport


def _retain_native_tls_data_v1(gate, value, *, key):
    _retained(gate, key)["received"].retain_tls(value)


def _retain_native_body_data_v1(gate, value, *, key):
    _retained(gate, key)["received"].retain_body(value)


def _retain_native_response_headers_v1(gate, status, headers, *, key):
    record = _retained(gate, key)
    record["received"].retain_headers(status, headers)
    record["headers_at"] = _stamp()


def _retain_native_response_eof_v1(gate, *, key):
    record = _retained(gate, key)
    record["received"].retain_eof()
    record["completed_at"] = _stamp()


def _retain_native_response_fault_v1(gate, code, *, key):
    _retained(gate, key)["received"].retain_fault(code)


def _native_response_body_limit_v1(gate, *, key):
    return _retained(gate, key)["received"]._body_limit


async def _held_readback(record):
    session = record["session"]
    facts = record["facts"]
    _need(
        type(session) is AsyncSession
        and session.bind is facts.session_factory.kw["bind"]
        and not set(session.__dict__).intersection(
            {"execute", "scalar", "connection", "get_transaction", "begin"}
        )
    )
    _same_facts(
        facts,
        _facts(record["owner"], record["api"], spent=record.get("consumed") is True),
    )
    value = facts.intent_data
    await ledger._profile(session, facts.role_pins)
    await require_legacy_live_negative_fence_v1(session)
    rows = (
        (
            await session.execute(
                text("SELECT * FROM public.native_live_control_fence_v1('live',:uid)"),
                {"uid": facts.account_uid},
            )
        )
        .mappings()
        .all()
    )
    _need(len(rows) == 1)
    control = dict(rows[0])
    ledger._control(
        control, value, await session.scalar(text("SELECT clock_timestamp()"))
    )
    view = ledger._source(value, facts.source_owner)
    ledger._hard_caps(control, view)
    rid, raw, digest = exact_live_intent_document_v1(value)
    row = (
        await session.execute(
            text("""SELECT r.environment,r.account_uid,r.event_key,r.candidate_sha256,r.report_sha256,
      r.source_revision_sha256,r.control_revision,r.arm_id,r.risk_amount,r.margin_amount,r.notional_amount,r.deadline,r.state,
      i.intent_json,i.intent_sha256,t.journal_json,t.journal_sha256 FROM public.native_live_reservations_v1 r
      JOIN public.native_live_intents_v1 i USING(reservation_id) JOIN public.native_live_transitions_v1 t USING(reservation_id)
      WHERE r.reservation_id=:rid AND t.sequence=1"""),
            {"rid": rid},
        )
    ).one()
    _need(
        tuple(row[:13])
        == (
            "live",
            facts.account_uid,
            value.event_key,
            value.candidate_sha256,
            value.report_sha256,
            value.source_revision_sha256,
            value.expected_control_revision,
            value.arm_id,
            max(value.risk_samples),
            value.margin_amount,
            value.notional_amount,
            value.deadline,
            "reserved",
        )
        and row[13] == raw.decode("ascii")
        and row[14] == digest
        and sha(row[15].encode("ascii")) == row[16]
    )
    expected_receipt = {
        "schema": "ctcc.native_live.intent_readback_data.v1",
        "reservation_id": rid,
        "intent_sha256": digest,
        "state": "reserved",
        "journal_sha256": row[16],
        "qualification_verified": False,
        "execution_authority": False,
        "admission": "DENY",
    }
    _need(canonical(expected_receipt) == facts.intent_readback)
    sums = tuple(
        (
            await session.execute(
                text(
                    "SELECT COALESCE(sum(risk_amount),0),COALESCE(sum(margin_amount),0),COALESCE(sum(notional_amount),0) FROM public.native_live_reservations_v1 WHERE environment='live' AND account_uid=:uid AND state<>'reconciled'"
                ),
                {"uid": facts.account_uid},
            )
        ).one()
    )
    _need(
        Fraction(sums[0]) <= Fraction(control["portfolio_risk_cap"])
        and Fraction(sums[1]) <= Fraction(control["margin_cap"])
        and Fraction(sums[2]) <= Fraction(control["micro_notional_cap"])
    )
    record["control"] = control
    record["legacy_fence_held"] = True
    _HELD[session] = record


class _PhysicalLiveWriteGateV1:
    __slots__ = ("__weakref__",)

    def __new__(cls, *, key):
        _need(cls is _PhysicalLiveWriteGateV1 and key is _GATE_KEY)
        return object.__new__(cls)

    def __copy__(self):
        _need(False)

    def __deepcopy__(self, memo):
        _need(False)

    def __reduce_ex__(self, protocol):
        _need(False)

    @property
    def host(self):
        return urlsplit(
            _require_preparation_scope_v2(self, key=transport._TRANSPORT_KEY)["origin"]
        ).hostname

    @property
    def tls_context(self):
        return _require_preparation_scope_v2(self, key=transport._TRANSPORT_KEY)[
            "tls_context"
        ]

    def take_transport(self, request):
        record = _require_preparation_scope_v2(self, key=transport._TRANSPORT_KEY)
        _need(not record["transport_taken"] and request is record["request"])
        _bind_request(record, request)
        record["transport_taken"] = True

    def bind_tls_peer(self, native_ssl):
        record = _require_preparation_scope_v2(self, key=transport._TRANSPORT_KEY)
        _need(
            type(native_ssl) is ssl.SSLObject
            and native_ssl.context is record["tls_context"]
            and record.get("ssl_object") is None
        )
        certificate = native_ssl.getpeercert(binary_form=True)
        _need(type(certificate) is bytes and bool(certificate))
        record["ssl_object"], record["tls_certificate_sha256"] = (
            native_ssl,
            sha(certificate),
        )

    async def tls_ready_after_locked_readback(self):
        record = _require_preparation_scope_v2(self, key=transport._TRANSPORT_KEY)
        _need(not record["tls_ready"])
        await _held_readback(record)
        _require_physical_gate_v1(self, key=transport._TRANSPORT_KEY)
        record["tls_ready"] = True

    def ssl_write(self, native_ssl, data):
        record = _require_physical_gate_v1(self, key=transport._TRANSPORT_KEY)
        _need(
            record["tls_ready"]
            and record["transport_taken"]
            and type(native_ssl) is ssl.SSLObject
            and native_ssl is record.get("ssl_object")
            and type(data) is bytes
            and data
            and record["wire"][record["accepted"] : record["accepted"] + len(data)]
            == data
        )
        record["attempted"], record["phase"] = True, "attempted"
        result = ssl.SSLObject.write(native_ssl, data)
        _need(type(result) is int and 0 < result <= len(data))
        record["accepted"] += result
        return result

    def raw_write(self, native, data):
        record = _require_physical_gate_v1(self, key=transport._TRANSPORT_KEY)
        _need(type(data) is bytes and bool(data))
        _need(native is record.get("socket_transport"))
        if record["tls_ready"]:
            _need(record["attempted"])
        for kind, name, call in transport._VENDOR_BINDINGS_V1:
            if name == "write" and type(native) is kind:
                return call(native, data)
        _need(False)

    def finish(self, success):
        record = _record(self)
        if success and record["received"].body_complete:
            record["phase"] = "spent"
        elif record["phase"] != "spent":
            record["phase"] = "unknown"


def _bind_request(record, request):
    facts = record["facts"]
    _need(
        type(request) is httpx.Request
        and request.method == "POST"
        and str(request.url) == record["origin"] + _ORDER_PATH
        and request.content == facts.request_json
        and sha(request.content) == facts.request_sha256
    )
    headers = tuple(request.headers.raw)
    names = [name.lower() for name, _ in headers]
    expected = {
        b"host",
        b"accept",
        b"content-type",
        b"content-length",
        b"user-agent",
        b"accept-encoding",
        b"ok-access-key",
        b"ok-access-sign",
        b"ok-access-timestamp",
        b"ok-access-passphrase",
        b"exptime",
    }
    _need(
        len(names) == len(set(names))
        and set(names) == expected
        and b"x-simulated-trading" not in names
        and request.headers["host"] == urlsplit(record["origin"]).hostname
        and request.headers["content-length"] == str(len(facts.request_json))
        and request.headers["accept-encoding"] == "identity"
        and request.headers["exptime"]
        == str(source._utc_ns(facts.deadline_utc) // 1_000_000)
    )
    connection = h11.Connection(h11.CLIENT)
    wire = connection.send(
        h11.Request(method="POST", target=_ORDER_PATH, headers=headers)
    )
    wire += connection.send(h11.Data(data=request.content)) + connection.send(
        h11.EndOfMessage()
    )
    _need(0 < len(wire) <= _MAX_ENTRY_WIRE_V2, "native_live_entry_wire_bound")
    if record["wire"]:
        _need(record["wire"] == wire)
    else:
        record["wire"] = wire


def _collect_entry_wire_v2(gate, data, *, key):
    """Validate a bounded exact prefix; buffering creates no write permission.

    No caller bytes are retained. Only an offset into the already owned complete
    h11 request advances. The final chunk consumes this one-use collection state
    before the first SSL callback; all actual writes still require full proof.
    """
    _need(key is transport._TRANSPORT_KEY)
    record = _require_preparation_scope_v2(gate, key=key)
    valid = (
        record["entry_wire_state"] == "collecting"
        and record["tls_ready"] is True
        and record["transport_taken"] is True
        and record["accepted"] == 0
        and record["attempted"] is False
        and type(record["wire"]) is bytes
        and 0 < len(record["wire"]) <= _MAX_ENTRY_WIRE_V2
        and type(record["entry_chunk_offset"]) is int
        and 0 <= record["entry_chunk_offset"] < len(record["wire"])
        and type(record["entry_chunk_count"]) is int
        and 0 <= record["entry_chunk_count"] < _MAX_ENTRY_WIRE_CHUNKS_V2
        and type(data) is bytes
        and bool(data)
        and record["wire"][
            record["entry_chunk_offset"] : record["entry_chunk_offset"] + len(data)
        ]
        == data
    )
    if not valid:
        record["entry_wire_state"], record["phase"] = "aborted", "unknown"
        record["entry_chunk_offset"] = 0
        _need(False, "native_live_entry_wire_prefix_denied")
    record["entry_chunk_offset"] += len(data)
    record["entry_chunk_count"] += 1
    complete = record["entry_chunk_offset"] == len(record["wire"])
    if complete:
        record["entry_wire_state"] = "sending"
    return record, record["wire"] if complete else None


def _complete_entry_wire_v2(gate, *, key):
    """Record successful vendor flushing, not a permit or exchange result."""
    _need(key is transport._TRANSPORT_KEY)
    record = _require_retained_response_identity_v2(gate, key=key)
    _need(
        record["entry_wire_state"] == "sending"
        and record["entry_chunk_offset"] == len(record["wire"])
        and record["accepted"] == len(record["wire"]) > 0
        and record["attempted"] is True,
        "native_live_entry_wire_incomplete",
    )
    record["entry_response_started_at"] = _stamp()
    record["entry_wire_state"] = "complete"


def _require_entry_response_ready_v2(gate, *, key):
    """Bounded read DATA after successful full-wire flushing; never authority."""
    _need(key is transport._TRANSPORT_KEY)
    record = _require_retained_response_identity_v2(gate, key=key)
    _need(
        record["entry_wire_state"] == "complete"
        and record["accepted"] == len(record["wire"]) > 0,
        "native_live_entry_wire_incomplete",
    )
    started = record["entry_response_started_at"]
    _need(type(started) is ClockStamp, "native_live_entry_response_clock_invalid")
    now = _stamp()
    validate_stamps((started.model_dump(), now.model_dump()))
    _need(
        now.utc_ns - started.utc_ns <= _MAX_RESPONSE_READ_NS_V2
        and now.monotonic_ns - started.monotonic_ns <= _MAX_RESPONSE_READ_NS_V2,
        "native_live_entry_response_read_bound",
    )
    return record


def _new_gate(record):
    _need(len(_GATES) < 16, "native_live_retained_response_capacity")
    _need(_HELD.get(record["session"]) is record and record.get("consumed") is True)
    _same_facts(record["facts"], _facts(record["owner"], record["api"], spent=True))
    module, _calls = _require_api_integrity_v2(record["api"])
    qualified = module._QUALIFIED.get(record["owner"])
    _need(
        type(qualified) is dict
        and qualified["facts"] is record["facts"]
        and qualified["spent"] is True
        and qualified["revoked"] is False
    )
    # Only source-integrity and immutable lineage are pinned. No mathematical
    # PASS, clock freshness, risk eligibility or write permission is cached.
    save_native_guard_v1(record, module._NATIVE_GUARD)
    record["preparation_prefix"] = _capture_preparation_prefix_v2(module)
    rid, raw, digest = exact_live_intent_document_v1(record["facts"].intent_data)
    record["preparation_qualification"] = qualified
    record["preparation_session"] = qualified["session"]
    record["preparation_lineage"] = (
        rid,
        raw,
        digest,
        record["facts"].intent_readback,
        record["facts"].request_json,
        record["facts"].request_sha256,
    )
    _need(
        not os.environ.get("SSL_CERT_FILE")
        and not os.environ.get("SSL_CERT_DIR")
        and not os.environ.get("SSLKEYLOGFILE")
    )
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.set_alpn_protocols(["http/1.1"])
    context.load_default_certs(ssl.Purpose.SERVER_AUTH)
    _need(
        context.check_hostname
        and context.verify_mode == ssl.CERT_REQUIRED
        and context.keylog_filename is None
    )
    gate = _PhysicalLiveWriteGateV1(key=_GATE_KEY)
    record.update(
        gate=ref(gate),
        tls_context=context,
        phase="prepared",
        pid=os.getpid(),
        thread=threading.get_ident(),
        task=asyncio.current_task(),
        loop=asyncio.get_running_loop(),
        request=None,
        wire=b"",
        accepted=0,
        entry_wire_state="collecting",
        entry_chunk_offset=0,
        entry_chunk_count=0,
        entry_response_started_at=None,
        transport_taken=False,
        tls_ready=False,
        attempted=False,
        received=transport._ResponseDataJournalV1(body_limit=65536),
    )
    _GATES[gate] = record
    return gate


async def _negative_control(facts, *, stop):
    before = None
    async with facts.session_factory() as session, session.begin():
        for sql in ledger._SETTINGS:
            await session.execute(text(sql))
        await ledger._profile(session, facts.role_pins)
        if stop:
            revision = await session.scalar(
                text("SELECT public.native_live_inhibit_v1('live',:uid)"),
                {"uid": facts.account_uid},
            )
        else:
            # This exact locked pre-state distinguishes our required disarm
            # from an earlier external disarm with superficially equal flags.
            before = dict(
                (
                    await session.execute(
                        text(
                            "SELECT * FROM public.native_live_control_fence_v1('live',:uid)"
                        ),
                        {"uid": facts.account_uid},
                    )
                )
                .mappings()
                .one()
            )
            revision = await session.scalar(
                text("SELECT public.native_live_disarm_v1('live',:uid,:arm)"),
                {"uid": facts.account_uid, "arm": facts.arm_id},
            )
        _need(
            type(revision) is int
            and revision >= facts.intent_data.expected_control_revision
        )
    # An acknowledged procedure call is not its durable independent readback.
    async with facts.session_factory() as session, session.begin():
        for sql in ledger._SETTINGS:
            await session.execute(text(sql))
        await ledger._profile(session, facts.role_pins)
        row = (
            await session.execute(
                text("""SELECT main_uid,process_sha256,armed,arm_id,arm_expires_at,
 emergency_stop,revision,credential_session_sha256,policy_sha256 FROM public.native_live_controls_v1
 WHERE environment='live' AND account_uid=:uid"""),
                {"uid": facts.account_uid},
            )
        ).one()
        _need(
            tuple(row[:5])
            == (
                facts.main_uid,
                facts.intent_data.process_sha256,
                False,
                None,
                None,
            )
            and (not stop or row[5] is True)
            and type(row[6]) is int
            and row[6] >= revision
        )
    if not stop and before is not None:
        expected = facts.intent_data
        original = (
            facts.account_uid,
            facts.main_uid,
            expected.process_sha256,
            expected.credential_session_sha256,
            expected.policy_sha256,
            expected.expected_control_revision,
            True,
            facts.arm_id,
            False,
        )
        observed = tuple(
            before.get(name)
            for name in (
                "account_uid",
                "main_uid",
                "process_sha256",
                "credential_session_sha256",
                "policy_sha256",
                "revision",
                "armed",
                "arm_id",
                "emergency_stop",
            )
        )
        if (
            observed == original
            and revision == expected.expected_control_revision + 1
            and tuple(row)
            == (
                facts.main_uid,
                expected.process_sha256,
                False,
                None,
                None,
                False,
                revision,
                expected.credential_session_sha256,
                expected.policy_sha256,
            )
        ):
            from app.domain import native_live_ledger_schema_v1 as schema

            statements = tuple(
                statement
                for statement in schema.UPGRADE_SQL
                if statement.startswith("CREATE FUNCTION public.native_live_disarm_v1(")
            )
            _need(len(statements) == 1)
            return {
                "schema": "ctcc.native_live.exact_disarm_successor_data.v1",
                "before": original,
                "after_revision": revision,
                "source_statement_sha256": sha(statements[0].encode("utf8")),
            }
    return None


async def _settle_negative_control(facts, record, *, stop):
    """Track one bounded negative write even if the caller is cancelled again.

    This worker can only disarm or latch EStop. It cannot acquire an Arm, create
    an intent, transmit an exchange request or retry an uncertain submission.
    A cancelled caller never treats the background task as confirmed durable.
    """
    _need(len(_NEGATIVE_TASKS) < 16, "native_live_negative_control_capacity")

    async def bounded():
        async with asyncio.timeout(15):
            return await _negative_control(facts, stop=stop)

    worker = asyncio.create_task(bounded())
    _NEGATIVE_TASKS.add(worker)
    if record is not None:
        record["negative_control_pending"] = True

    def completed(task):
        _NEGATIVE_TASKS.discard(task)
        confirmed = not task.cancelled() and task.exception() is None
        if record is not None:
            record["negative_control_pending"] = False
            if not confirmed:
                record["durable_inhibit_unconfirmed"] = True

    worker.add_done_callback(completed)
    settled = await asyncio.shield(worker)
    if record is not None and not stop:
        record["settled_disarm_successor"] = settled


def _replay_owned_live_disarm_successor_v1(handle, *, key):
    """Only a genuine settled same-task response can prove the one-step disarm."""
    from app.domain import native_live_ledger_schema_v1 as schema
    from app.okx_live import owned_close_v1 as close

    _need(key is close._PHYSICAL_SUCCESSOR_KEY_V1)
    record = _record(handle)
    _need(not record["session"].in_transaction())
    _need(
        record.get("negative_control_pending") is False
        and record.get("durable_inhibit_unconfirmed") is not True
    )
    value = record.get("settled_disarm_successor")
    _need(type(value) is dict)
    statements = tuple(
        statement
        for statement in schema.UPGRADE_SQL
        if statement.startswith("CREATE FUNCTION public.native_live_disarm_v1(")
    )
    _need(len(statements) == 1)
    _need(value["source_statement_sha256"] == sha(statements[0].encode("utf8")))
    intent = record["facts"].intent_data
    _need(value["after_revision"] == intent.expected_control_revision + 1)
    _need(
        value["before"]
        == (
            intent.account_uid,
            intent.main_uid,
            intent.process_sha256,
            intent.credential_session_sha256,
            intent.policy_sha256,
            intent.expected_control_revision,
            True,
            intent.arm_id,
            False,
        )
    )
    return value.copy()


def _validate_known_response_data(record):
    """Late rejection never erases a complete already-received safe journal."""
    received = record["received"]
    _need(
        received.body_complete
        and record["attempted"] is True
        and record["accepted"] == len(record["wire"]) > 0
        and type(record.get("tls_certificate_sha256")) is str
        and len(record["tls_certificate_sha256"]) == 64
        and record.get("headers_at") is not None
        and record.get("completed_at") is not None
    )
    _checked_response_headers(
        received.headers, maximum=65536, private_tokens=record["private_tokens"]
    )
    body = decode(received.body_raw, maximum=65536)
    _check_no_credential_echo(body, record["private_tokens"])
    validate_stamps(
        tuple(
            record[name].model_dump()
            for name in ("started_at", "headers_at", "completed_at")
        )
    )
    record["response_checked"] = True


def _request(record, material):
    facts = record["facts"]
    now = _stamp()
    timestamp = (
        utc_from_ns(now.utc_ns)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )
    signature = build_signature(
        timestamp=timestamp,
        method="POST",
        request_path=_ORDER_PATH,
        body=facts.request_json.decode("ascii"),
        secret=material.credentials[1],
    )
    expires = source._utc_ns(facts.deadline_utc) // 1_000_000
    headers = {
        "Host": urlsplit(material.origin).hostname,
        "Accept": "application/json",
        "Accept-Encoding": "identity",
        "Content-Type": "application/json",
        "Content-Length": str(len(facts.request_json)),
        "User-Agent": material.user_agent,
        "OK-ACCESS-KEY": material.credentials[0],
        "OK-ACCESS-SIGN": signature,
        "OK-ACCESS-TIMESTAMP": timestamp,
        "OK-ACCESS-PASSPHRASE": material.credentials[2],
        "expTime": str(expires),
    }
    record["private_tokens"] = (*material.credentials, signature)
    record["started_at"] = now
    request = httpx.Request(
        "POST",
        material.origin + _ORDER_PATH,
        headers=headers,
        content=facts.request_json,
    )
    _bind_request(record, request)
    record["request"] = request
    return request


async def submit_owned_live_fok_v1(owner):
    """Only the original owned coordinator may reach the new Live transport."""
    gate = facts = None
    api = None
    success = False
    try:
        api = _coordinator_api()
        facts = _facts(owner, api)
        ledger._factory(facts.session_factory, facts.role_pins)
        async with facts.session_factory() as session:
            transaction = await session.begin()
            try:
                for sql in ledger._SETTINGS:
                    await session.execute(text(sql))
                await session.execute(
                    text("SELECT pg_advisory_xact_lock(:key)"),
                    {"key": live_account_lock_key_v1(facts.account_uid)},
                )
                record = {
                    "owner": owner,
                    "facts": facts,
                    "api": api,
                    "session": session,
                    "transaction": transaction,
                }
                await _held_readback(record)
                consumed = _api_call(api, 1, owner)
                _same_facts(facts, consumed)
                record["consumed"] = True
                view = source._replay_owned_live_source_v1(
                    facts.source_owner, key=source._SOURCE_KEY
                )
                record["origin"] = source._SOURCES[facts.source_owner][
                    "lease"
                ]._pin.origin
                _need(
                    record["origin"]
                    in ("https://openapi.okx.com", "https://eea.okx.com")
                )
                gate = _new_gate(record)
                token = _ACTIVE.set(gate)
                try:
                    material = source._owned_live_credential_material_v1(
                        facts.source_owner, key=_CREDENTIAL_SOURCE_KEY_V1
                    )
                    _need(
                        material.origin == record["origin"]
                        and len(view["current_pages"]) > 0
                    )
                    request = _request(record, material)
                    owned_transport = transport._create_transport_v1(
                        gate, key=_INTENT_KEY
                    )
                    timeout = min(float(material.timeout_seconds), 10.0)
                    async with (
                        asyncio.timeout(timeout),
                        httpx.AsyncClient(
                            transport=owned_transport,
                            trust_env=False,
                            follow_redirects=False,
                            timeout=timeout,
                        ) as client,
                    ):
                        response = await client.send(
                            request, stream=True, follow_redirects=False
                        )
                        async for _chunk in response.aiter_raw():
                            pass
                        await response.aclose()
                    _validate_known_response_data(record)
                    success = True
                finally:
                    _ACTIVE.reset(token)
            finally:
                try:
                    await transaction.rollback()
                finally:
                    _HELD.pop(session, None)
        await _settle_negative_control(facts, _record(gate), stop=not success)
        return gate
    except BaseException as failure:
        if gate is not None:
            retained = _record(gate)
            try:
                _validate_known_response_data(retained)
            except Exception:  # noqa: BLE001 -- preserve raw in-memory DATA; no unsafe artifact publication.
                retained["response_unvalidated"] = True
        if api is not None:
            try:
                _api_call(api, 3, owner, code="native_live_physical_attempt_unresolved")
            except BaseException:  # noqa: BLE001 -- callback failure remains unknown, never permission.
                if gate is not None:
                    _record(gate)["local_inhibit_unconfirmed"] = True
        if facts is not None:
            try:
                await _settle_negative_control(
                    facts, _record(gate) if gate is not None else None, stop=True
                )
            except BaseException:  # noqa: BLE001 -- durable stop uncertainty must remain recorded.
                if gate is not None:
                    _record(gate)["durable_inhibit_unconfirmed"] = True
        if not isinstance(failure, Exception):
            failure._owned_live_response_v1 = gate
            raise failure from None
        error = NativeLiveWriteError(
            "native_live_physical_attempt_unresolved_do_not_retry"
        )
        error._owned_live_response_v1 = gate
        raise error from None


def _replay_owned_live_response_v1(handle, *, key):
    _need(key is _QUALIFICATION_SOURCE_KEY_V1)
    record = _record(handle)
    _need(not record["session"].in_transaction())
    received = record["received"]
    raw = received.body_raw
    return {
        "schema": "ctcc.native_live.physical_response_data.v1",
        "environment": "live",
        "reservation_id": record["facts"].reservation_id,
        "request_sha256": record["facts"].request_sha256,
        "request_wire_sha256": sha(record["wire"]),
        "credential_session_sha256": record["facts"].credential_session_sha256,
        "source_revision_sha256": record["facts"].intent_data.source_revision_sha256,
        "origin": record["origin"],
        "started_at": record["started_at"].model_dump()
        if record.get("started_at") is not None
        else None,
        "headers_at": record["headers_at"].model_dump()
        if record.get("headers_at") is not None
        else None,
        "completed_at": record["completed_at"].model_dump()
        if record.get("completed_at") is not None
        else None,
        "status": received.status,
        "raw_body": raw if record.get("response_checked") is True else None,
        "raw_headers": received.headers
        if record.get("response_checked") is True
        else None,
        "raw_withheld_unvalidated": record.get("response_checked") is not True,
        "body_sha256": sha(raw),
        "request_attempted": record["attempted"],
        "complete": received.body_complete,
        "late_faults": tuple(received.faults),
        "negative_control_pending": record.get("negative_control_pending") is True,
        "durable_inhibit_unconfirmed": record.get("durable_inhibit_unconfirmed")
        is True,
        "tls_certificate_sha256": record.get("tls_certificate_sha256"),
        "qualification_verified": False,
        "execution_authority": False,
        "admission": "DENY",
    }
