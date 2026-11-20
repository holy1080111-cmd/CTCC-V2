"""Exact source-owned reduce-only close/cancel physical purpose, never entry.

Both actions require their independently read-back durable HMAC claim and the
same original human close consent. Account and Control locks remain held across
TLS and every native write. A claim is never retried after any uncertainty.
"""

import asyncio
import os
import ssl
import threading
from contextvars import ContextVar
from urllib.parse import urlsplit
from weakref import WeakKeyDictionary, ref

import h11
import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.source_primitives import decode, sha, utc_from_ns
from app.exchange.okx import native_live_transport_v1 as transport
from app.exchange.okx import native_live_write_v1 as entry
from app.exchange.okx.live_account_config_observation_v1 import _stamp
from app.exchange.okx.private_rest import build_signature

_ACTOR_SOURCE_KEY_V1 = object()
_INTENT_KEY = object()
_GATE_KEY = object()
_GATES = WeakKeyDictionary()
_ACTIVE = ContextVar("ctcc_live_controlled_close_physical_v1", default=None)
_PATHS = {
    "close": "/api/v5/trade/order",
    "cancel_original_protection": "/api/v5/trade/cancel-algos",
}


def _need(value):
    if value is not True:
        raise ValueError("native_live_controlled_action_unavailable") from None


def _record(gate):
    _need(type(gate) is _PhysicalLiveCloseGateV1)
    state = _GATES.get(gate)
    _need(type(state) is dict and state["gate"]() is gate)
    _need(state["pid"] == os.getpid() and state["thread"] == threading.get_ident())
    _need(
        state["task"] is asyncio.current_task()
        and state["loop"] is asyncio.get_running_loop()
    )
    return state


def _actor(owner, *, consume=False):
    from app.okx_live import owned_close_v1 as actor

    return actor._replay_owned_live_close_dispatch_v1(
        owner, key=_ACTOR_SOURCE_KEY_V1, consume=consume
    )


def _require_vendor_state_v1():
    entry._require_vendor_state_v1()


def _require_physical_gate_v1(gate, *, key):
    from app.okx_live import owned_close_v1 as actor

    _need(key is transport._TRANSPORT_KEY)
    record = _record(gate)
    _need(_ACTIVE.get() is gate and record["phase"] in ("prepared", "attempted"))
    session = record["session"]
    _need(type(session) is AsyncSession and session.in_transaction())
    _need(
        session.sync_session.get_transaction() is record["transaction"].sync_transaction
    )
    _need(record["legacy_fence_held"] is True)
    state = _actor(record["owner"])
    _need(state is record["state"] and state["action"] == record["action"])
    actor._validate_control(state, record["control"], utc_from_ns(_stamp().utc_ns))
    _require_vendor_state_v1()
    return record


def _retained(gate, key):
    _need(key is transport._TRANSPORT_KEY)
    return _record(gate)


def _capture_abort_pins_v1(gate):
    record = _require_physical_gate_v1(gate, key=transport._TRANSPORT_KEY)
    calls = tuple(
        (kind, call)
        for kind, name, call in transport._VENDOR_BINDINGS_V1
        if name == "abort"
    )

    def abort(native):
        for kind, call in calls:
            if type(native) is kind:
                try:
                    call(native)
                    return True
                except BaseException:  # noqa: BLE001 -- cleanup remains negative and retains uncertainty.
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


def _bind_request(state, request):
    action, raw = state["action"], state["request_json"]
    path = _PATHS[action]
    _need(type(request) is httpx.Request and request.method == "POST")
    _need(
        str(request.url) == state["lease"]._pin.origin + path and request.content == raw
    )
    headers = tuple(request.headers.raw)
    names = tuple(name.lower() for name, _ in headers)
    _need(
        len(names) == len(set(names))
        and set(names)
        == {
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
    )
    _need(request.headers["host"] == urlsplit(state["lease"]._pin.origin).hostname)
    _need(
        request.headers["content-length"] == str(len(raw))
        and request.headers["accept-encoding"] == "identity"
    )
    from app.okx_live.owned_close_v1 import _utc_ns

    _need(
        request.headers["exptime"]
        == str(_utc_ns(state["request_deadline"]) // 1_000_000)
    )
    connection = h11.Connection(h11.CLIENT)
    wire = connection.send(h11.Request(method="POST", target=path, headers=headers))
    wire += connection.send(h11.Data(data=raw)) + connection.send(h11.EndOfMessage())
    return wire


def prepare_owned_live_controlled_request_v1(owner, *, key):
    from app.okx_live import owned_close_v1 as actor

    _need(key is _ACTOR_SOURCE_KEY_V1)
    state = actor._record(owner)
    _need(state["phase"] in ("intent_spent", "dispatch_spent"))
    actor._source_checks(state)
    lease = state["lease"]
    lease.verify_current()
    pin, now = lease._pin, _stamp()
    timestamp = (
        utc_from_ns(now.utc_ns)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )
    path = _PATHS[state["action"]]
    signature = build_signature(
        timestamp=timestamp,
        method="POST",
        request_path=path,
        body=state["request_json"].decode("ascii"),
        secret=pin.credentials[1],
    )
    request = httpx.Request(
        "POST",
        pin.origin + path,
        content=state["request_json"],
        headers={
            "Host": urlsplit(pin.origin).hostname,
            "Accept": "application/json",
            "Accept-Encoding": "identity",
            "Content-Type": "application/json",
            "Content-Length": str(len(state["request_json"])),
            "User-Agent": pin.user_agent,
            "OK-ACCESS-KEY": pin.credentials[0],
            "OK-ACCESS-SIGN": signature,
            "OK-ACCESS-TIMESTAMP": timestamp,
            "OK-ACCESS-PASSPHRASE": pin.credentials[2],
            "expTime": str(actor._utc_ns(state["request_deadline"]) // 1_000_000),
        },
    )
    wire = _bind_request(state, request)
    state.update(
        prepared_request=request,
        prepared_wire=wire,
        request_wire_sha256=sha(wire),
        private_tokens=(*pin.credentials, signature),
    )


async def _held_readback(record):
    from app.database.repositories import native_live_lifecycle_v1 as repo
    from app.domain.native_live_legacy_safety_v1 import (
        require_legacy_live_negative_fence_v1,
    )
    from app.okx_live import owned_close_v1 as actor

    state, session = record["state"], record["session"]
    _need(type(session) is AsyncSession and session.bind is state["factory"].kw["bind"])
    _need(
        not set(session.__dict__).intersection(
            {"execute", "scalar", "connection", "get_transaction", "begin"}
        )
    )
    _need(_actor(record["owner"]) is state)
    await repo._profile(session, state["pins"])
    await require_legacy_live_negative_fence_v1(session)
    control = dict(
        (
            await session.execute(
                text(
                    "SELECT * FROM public.native_live_lifecycle_control_fence_v1(:uid)"
                ),
                {"uid": state["intent"].account_uid},
            )
        )
        .mappings()
        .one()
    )
    actor._validate_control(
        state, control, await session.scalar(text("SELECT clock_timestamp()"))
    )
    row = (
        await session.execute(
            text("""SELECT i.close_intent_json,i.close_intent_sha256,i.signature,
 c.claim_json,c.claim_sha256,c.signature,c.request_wire_sha256,r.state
 FROM public.native_live_controlled_close_intents_v1 i
 JOIN public.native_live_controlled_close_claims_v1 c USING(close_intent_id)
 JOIN public.native_live_reservations_v1 r USING(reservation_id)
 WHERE i.close_intent_id=:id AND c.action=:action AND i.environment='live' AND i.account_uid=:uid"""),
            {
                "id": state["close_intent_id"],
                "action": state["action"],
                "uid": state["intent"].account_uid,
            },
        )
    ).one()
    _need(
        tuple(row[:7])
        == (
            state["intent_json"].decode("ascii"),
            sha(state["intent_json"]),
            state["intent_readback"].signature,
            state["claim_json"].decode("ascii"),
            sha(state["claim_json"]),
            state["claim_readback"].signature,
            state["request_wire_sha256"],
        )
        and row[7] in ("reserved", "uncertain")
    )
    if state["action"] == "close":
        latest = (
            await session.execute(
                text("""SELECT proof_sha256,outcome,observed_at
 FROM public.native_live_lifecycle_proofs_v1 WHERE reservation_id=:id ORDER BY sequence DESC LIMIT 1"""),
                {"id": state["consent"].reservation_id},
            )
        ).one()
        now = await session.scalar(text("SELECT clock_timestamp()"))
        _need(
            latest[0] == sha(state["protected_journal"].proofs[-1])
            and latest[1] == "PROTECTED_HOLD"
        )
        _need(0 <= (now - latest[2]).total_seconds() <= 5)
    record["control"], record["legacy_fence_held"] = control, True


class _PhysicalLiveCloseGateV1:
    __slots__ = ("__weakref__",)

    def __new__(cls, *, key=None):
        _need(cls is _PhysicalLiveCloseGateV1 and key is _GATE_KEY)
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
            _require_physical_gate_v1(self, key=transport._TRANSPORT_KEY)["origin"]
        ).hostname

    @property
    def tls_context(self):
        return _require_physical_gate_v1(self, key=transport._TRANSPORT_KEY)[
            "tls_context"
        ]

    def take_transport(self, request):
        record = _require_physical_gate_v1(self, key=transport._TRANSPORT_KEY)
        _need(not record["transport_taken"] and request is record["request"])
        _need(_bind_request(record["state"], request) == record["wire"])
        record["transport_taken"] = True

    def bind_tls_peer(self, native_ssl):
        record = _require_physical_gate_v1(self, key=transport._TRANSPORT_KEY)
        _need(
            type(native_ssl) is ssl.SSLObject
            and native_ssl.context is record["tls_context"]
        )
        _need(record.get("ssl_object") is None)
        certificate = native_ssl.getpeercert(binary_form=True)
        _need(type(certificate) is bytes and bool(certificate))
        record["ssl_object"], record["tls_certificate_sha256"] = (
            native_ssl,
            sha(certificate),
        )

    async def tls_ready_after_locked_readback(self):
        record = _require_physical_gate_v1(self, key=transport._TRANSPORT_KEY)
        _need(not record["tls_ready"])
        await _held_readback(record)
        _require_physical_gate_v1(self, key=transport._TRANSPORT_KEY)
        record["tls_ready"] = True

    def ssl_write(self, native_ssl, data):
        record = _require_physical_gate_v1(self, key=transport._TRANSPORT_KEY)
        _need(
            record["tls_ready"]
            and record["transport_taken"]
            and native_ssl is record.get("ssl_object")
        )
        _need(type(native_ssl) is ssl.SSLObject and type(data) is bytes and bool(data))
        _need(
            record["wire"][record["accepted"] : record["accepted"] + len(data)] == data
        )
        record["attempted"], record["phase"] = True, "attempted"
        value = ssl.SSLObject.write(native_ssl, data)
        _need(type(value) is int and 0 < value <= len(data))
        record["accepted"] += value
        return value

    def raw_write(self, native, data):
        record = _require_physical_gate_v1(self, key=transport._TRANSPORT_KEY)
        _need(
            type(data) is bytes
            and bool(data)
            and native is record.get("socket_transport")
        )
        _need(not record["tls_ready"] or record["attempted"])
        for kind, name, call in transport._VENDOR_BINDINGS_V1:
            if name == "write" and type(native) is kind:
                return call(native, data)
        _need(False)

    def finish(self, success):
        record = _record(self)
        record["phase"] = (
            "spent" if success and record["received"].body_complete else "unknown"
        )


async def submit_owned_live_controlled_action_v1(owner):
    """Burn the exact source action before await; no response permits a retry."""
    from app.database.repositories import native_live_lifecycle_v1 as repo
    from app.domain.native_live_intent_v1 import live_account_lock_key_v1

    state = _actor(owner, consume=True)
    _need(
        not any(
            os.environ.get(name)
            for name in ("SSL_CERT_FILE", "SSL_CERT_DIR", "SSLKEYLOGFILE")
        )
    )
    gate = None
    try:
        async with state["factory"]() as session:
            transaction = await session.begin()
            try:
                for statement in repo._SETTINGS:
                    await session.execute(text(statement))
                await session.execute(
                    text("SELECT pg_advisory_xact_lock(:key)"),
                    {"key": live_account_lock_key_v1(state["intent"].account_uid)},
                )
                record = {
                    "owner": owner,
                    "state": state,
                    "action": state["action"],
                    "session": session,
                    "transaction": transaction,
                    "legacy_fence_held": False,
                }
                await _held_readback(record)
                _need(len(_GATES) < 16)
                context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                context.minimum_version = ssl.TLSVersion.TLSv1_2
                context.set_alpn_protocols(["http/1.1"])
                context.load_default_certs(ssl.Purpose.SERVER_AUTH)
                _need(
                    context.check_hostname
                    and context.verify_mode == ssl.CERT_REQUIRED
                    and context.keylog_filename is None
                )
                gate = _PhysicalLiveCloseGateV1(key=_GATE_KEY)
                record.update(
                    gate=ref(gate),
                    phase="prepared",
                    pid=os.getpid(),
                    thread=threading.get_ident(),
                    task=asyncio.current_task(),
                    loop=asyncio.get_running_loop(),
                    tls_context=context,
                    origin=state["lease"]._pin.origin,
                    request=state["prepared_request"],
                    wire=state["prepared_wire"],
                    accepted=0,
                    transport_taken=False,
                    tls_ready=False,
                    attempted=False,
                    received=transport._ResponseDataJournalV1(body_limit=65536),
                    private_tokens=state["private_tokens"],
                    started_at=_stamp(),
                )
                _GATES[gate] = record
                token = _ACTIVE.set(gate)
                try:
                    _require_physical_gate_v1(gate, key=transport._TRANSPORT_KEY)
                    owned_transport = transport._create_transport_v1(
                        gate, key=_INTENT_KEY
                    )
                    remaining = (
                        state["request_deadline_monotonic_ns"] - _stamp().monotonic_ns
                    ) / 1e9
                    _need(remaining > 0)
                    async with (
                        asyncio.timeout(remaining),
                        httpx.AsyncClient(
                            transport=owned_transport,
                            trust_env=False,
                            follow_redirects=False,
                            timeout=remaining,
                        ) as client,
                    ):
                        response = await client.send(
                            record["request"], stream=True, follow_redirects=False
                        )
                        async for _ in response.aiter_raw():
                            pass
                        await response.aclose()
                    entry._validate_known_response_data(record)
                finally:
                    _ACTIVE.reset(token)
            finally:
                await transaction.rollback()
        value = _replay_owned_live_controlled_response_v1(
            gate, key=_ACTOR_SOURCE_KEY_V1
        )
        _need(value["status"] == 200 and value["complete"] is True)
        document = decode(value["raw_body"], maximum=65536)
        _need(
            document.get("code") == "0"
            and type(document.get("data")) is list
            and len(document["data"]) == 1
        )
        row = document["data"][0]
        _need(row.get("sCode") == "0")
        if state["action"] == "close":
            _need(row.get("clOrdId") == state["close_client_order_id"])
            _need(
                type(row.get("ordId")) is str
                and row["ordId"].isascii()
                and row["ordId"].isdecimal()
                and int(row["ordId"]) > 0
            )
        else:
            _need(row.get("algoId") == state["protected_facts"].protection_algo_id)
        return gate
    except BaseException as error:
        if gate is not None:
            try:
                entry._validate_known_response_data(_record(gate))
            except Exception:  # noqa: BLE001 -- unknown safe response bytes stay withheld, never retried.
                _record(gate)["response_unvalidated"] = True
            state.setdefault("responses", {})[state["action"]] = gate
        # The outer original actor persists stop; a claim is already durable.
        if not isinstance(error, Exception):
            raise
        raise ValueError("native_live_controlled_action_unknown_no_retry") from None


def _replay_owned_live_controlled_response_v1(handle, *, key):
    _need(key is _ACTOR_SOURCE_KEY_V1)
    record = _record(handle)
    _need(not record["session"].in_transaction())
    received = record["received"]
    checked = record.get("response_checked") is True
    return {
        "schema": "ctcc.native_live.controlled_response_data.v1",
        "action": record["action"],
        "request_sha256": sha(record["request"].content),
        "request_wire_sha256": sha(record["wire"]),
        "started_at": record["started_at"].model_dump(),
        "headers_at": record["headers_at"].model_dump()
        if record.get("headers_at") is not None
        else None,
        "completed_at": record["completed_at"].model_dump()
        if record.get("completed_at") is not None
        else None,
        "status": received.status,
        "raw_body": received.body_raw if checked else None,
        "raw_headers": received.headers if checked else None,
        "body_sha256": sha(received.body_raw),
        "complete": received.body_complete,
        "request_attempted": record["attempted"],
        "raw_withheld_unvalidated": not checked,
        "late_faults": tuple(received.faults),
        "tls_certificate_sha256": record.get("tls_certificate_sha256"),
        "execution_authority": False,
    }
