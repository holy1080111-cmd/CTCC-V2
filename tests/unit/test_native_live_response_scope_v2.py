"""Late response DATA with synthetic original ingress; zero DB/venue/Arm."""

import ssl
from datetime import timedelta
from types import SimpleNamespace as Data

import pytest

from app.domain.source_primitives import ClockStamp
from app.exchange.okx import native_live_transport_v1 as transport
from app.exchange.okx import native_live_write_v1 as write
from tests.unit.test_native_live_preparation_scope_v2 import _scope
from tests.unit.test_native_live_whole_wire_v2 import _buffer


def _flushed_late(scope):
    """Declare measured flush before expiry; ACK read begins after expiry."""
    deadline = scope.record["facts"].deadline_utc
    scope.time["now"] = deadline + timedelta(milliseconds=500)
    previous = scope.state["last"]
    scope.time["mono"] = (
        previous.monotonic_ns
        + write.source._utc_ns(scope.time["now"])
        - previous.utc_ns
    )
    scope.record.update(
        wire=b"synthetic-original-wire",
        accepted=len(b"synthetic-original-wire"),
        attempted=True,
        phase="attempted",
        entry_wire_state="complete",
        entry_response_started_at=ClockStamp(
            utc_ns=write.source._utc_ns(deadline - timedelta(milliseconds=500)),
            monotonic_ns=scope.time["mono"] - 1_000_000_000,
        ),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("stopped", [False, True])
async def test_late_data_is_allowed_after_expiry_or_stop_but_never_a_write(
    monkeypatch, stopped
):
    async with _scope(monkeypatch) as scope:
        _flushed_late(scope)
        if stopped:
            scope.qualification["revoked"] = True
            scope.state["active"] = False
            scope.state["inhibit_code"] = "synthetic_estop"
            scope.record["control"]["emergency_stop"] = True
        assert (
            write._require_entry_response_ready_v2(
                scope.gate, key=transport._TRANSPORT_KEY
            )
            is scope.record
        )
        with pytest.raises(ValueError):
            write._require_preparation_scope_v2(
                scope.gate, key=transport._TRANSPORT_KEY
            )
        # Retaining DATA cannot authorize either original full write boundary.
        for actual_write in (scope.gate.ssl_write, scope.gate.raw_write):
            with pytest.raises((ValueError, TypeError)):
                actual_write(object(), b"no execution authority")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation", ["partial", "cancelled", "failed_flush", "unflushed"]
)
async def test_only_full_successful_flush_can_open_response_data(monkeypatch, mutation):
    async with _scope(monkeypatch) as scope:
        _flushed_late(scope)
        if mutation == "partial":
            scope.record["accepted"] -= 1
        else:
            scope.record["entry_wire_state"] = {
                "cancelled": "aborted",
                "failed_flush": "sending",
                "unflushed": "collecting",
            }[mutation]
        with pytest.raises(write.NativeLiveWriteError, match="incomplete"):
            write._require_entry_response_ready_v2(
                scope.gate, key=transport._TRANSPORT_KEY
            )


@pytest.mark.asyncio
async def test_measured_response_read_cap_is_independent_of_order_expiry(monkeypatch):
    async with _scope(monkeypatch) as scope:
        _flushed_late(scope)
        started = scope.record["entry_response_started_at"]
        scope.time["now"] += timedelta(seconds=10)
        scope.time["mono"] = started.monotonic_ns + 11_000_000_000
        with pytest.raises(write.NativeLiveWriteError, match="response_read_bound"):
            write._require_entry_response_ready_v2(
                scope.gate, key=transport._TRANSPORT_KEY
            )


@pytest.mark.asyncio
async def test_completed_vendor_flush_records_data_even_if_execution_expired(
    monkeypatch,
):
    async with _scope(monkeypatch) as scope:
        _flushed_late(scope)
        scope.record["entry_wire_state"] = "sending"
        scope.record["entry_chunk_offset"] = len(scope.record["wire"])
        scope.record["entry_response_started_at"] = None
        scope.qualification["revoked"] = True
        scope.state["active"] = False
        write._complete_entry_wire_v2(scope.gate, key=transport._TRANSPORT_KEY)
        assert scope.record["entry_wire_state"] == "complete"
        assert scope.record["entry_response_started_at"].utc_ns == write.source._utc_ns(
            scope.time["now"]
        )
        with pytest.raises(ValueError):
            write._require_preparation_scope_v2(
                scope.gate, key=transport._TRANSPORT_KEY
            )


@pytest.mark.asyncio
async def test_missing_measured_flush_clock_is_not_assumed(monkeypatch):
    async with _scope(monkeypatch) as scope:
        _flushed_late(scope)
        scope.record["entry_response_started_at"] = None
        with pytest.raises(write.NativeLiveWriteError, match="response_clock_invalid"):
            write._require_entry_response_ready_v2(
                scope.gate, key=transport._TRANSPORT_KEY
            )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation",
    [
        "closed",
        "unspent",
        "wrong_revocation_type",
        "source_uid",
        "credential",
        "source_revision",
        "writer_factory",
        "writer_pins",
        "held",
        "transaction",
        "context",
        "lineage",
        "source_guard",
        "canonical_session",
        "vendor",
    ],
)
async def test_response_data_still_rejects_owner_source_and_vendor_mutations(
    monkeypatch, mutation
):
    async with _scope(monkeypatch) as scope:
        _flushed_late(scope)
        foreign_calls = []

        def foreign(*_args, **_kwargs):
            foreign_calls.append(True)

        token = None
        if mutation == "closed":
            scope.state["closed"] = True
        elif mutation == "unspent":
            scope.qualification["spent"] = False
        elif mutation == "wrong_revocation_type":
            scope.qualification["revoked"] = "yes"
        elif mutation == "source_uid":
            scope.view["uid"] = "foreign"
        elif mutation == "credential":
            scope.view["credential_session_sha256"] = "f" * 64
        elif mutation == "source_revision":
            scope.view["source_revision_sha256"] = "f" * 64
        elif mutation == "writer_factory":
            scope.cfg["writer_factory"] = object()
        elif mutation == "writer_pins":
            scope.cfg["writer_pins"] = object()
        elif mutation == "held":
            write._HELD.pop(scope.record["session"])
        elif mutation == "transaction":
            await scope.record["transaction"].rollback()
        elif mutation == "context":
            token = write._ACTIVE.set(None)
        elif mutation == "lineage":
            scope.record["preparation_lineage"] = ()
        elif mutation == "source_guard":
            monkeypatch.setattr(write._coordinator_api()[0], "_NATIVE_GUARD", foreign)
        elif mutation == "canonical_session":
            monkeypatch.setattr(write._coordinator_api()[0], "_session", foreign)
        else:
            monkeypatch.setattr(transport.TLSStream, "receive", foreign)
        try:
            with pytest.raises((ValueError, TypeError)):
                write._require_entry_response_ready_v2(
                    scope.gate, key=transport._TRANSPORT_KEY
                )
            assert foreign_calls == []
        finally:
            if token is not None:
                write._ACTIVE.reset(token)


@pytest.mark.asyncio
async def test_transport_retains_late_ack_without_calling_full_write_eligibility(
    monkeypatch,
):
    value = _buffer(monkeypatch)
    await value.stream.send(value.record["wire"])
    retained = []
    monkeypatch.setattr(
        transport, "_require_entry_tls_response_binding_v2", lambda *_args: None
    )
    monkeypatch.setattr(
        write, "_require_retained_response_identity_v2", lambda *_a, **_k: value.record
    )

    async def late_ack(_native, _maximum):
        value.state["fault"] = ValueError("synthetic_quote_and_arm_expired")
        return b"synthetic-late-ack"

    monkeypatch.setattr(transport.TLSStream, "receive", late_ack)
    monkeypatch.setattr(
        write, "_retain_native_tls_data_v1", lambda _g, raw, **_k: retained.append(raw)
    )
    assert await value.stream.receive() == b"synthetic-late-ack"
    assert retained == [b"synthetic-late-ack"]
    with pytest.raises(ValueError, match="expired"):
        await value.stream.send(value.record["wire"])
    assert len([event for event in value.events if event[0] == "ssl"]) == 1


@pytest.mark.asyncio
async def test_transport_checks_identity_again_after_response_await(monkeypatch):
    value = _buffer(monkeypatch)
    await value.stream.send(value.record["wire"])
    monkeypatch.setattr(
        transport, "_require_entry_tls_response_binding_v2", lambda *_args: None
    )
    retained = []

    async def changed_source(_native, _maximum):
        value.state["fault"] = ValueError("synthetic_changed_source")
        return b"untrusted-response-data"

    monkeypatch.setattr(transport.TLSStream, "receive", changed_source)
    monkeypatch.setattr(
        write, "_retain_native_tls_data_v1", lambda _g, raw, **_k: retained.append(raw)
    )
    with pytest.raises(ValueError, match="changed_source"):
        await value.stream.receive()
    assert retained == []


def test_response_binding_rejects_foreign_tls_or_socket_before_callback():
    with pytest.raises(transport.NativeLiveTransportError, match="owner_changed"):
        transport._require_entry_tls_response_binding_v2(Data(), object(), {})


def _native_data_binding():
    """Native types, inert unconnected SocketStream and unhandshaken SSL DATA."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    incoming, outgoing = ssl.MemoryBIO(), ssl.MemoryBIO()
    native_ssl = context.wrap_bio(
        incoming, outgoing, server_hostname="synthetic.invalid"
    )
    gate, native_socket = object(), object()
    socket_stream = object.__new__(transport.SocketStream)
    raw = object.__new__(transport._RawTransport)
    raw._gate, raw._native = gate, native_socket
    socket_stream._transport = raw
    native = transport.TLSStream(socket_stream, True, native_ssl, incoming, outgoing)
    record = {
        "ssl_object": native_ssl,
        "tls_context": context,
        "native_socket": transport.AnyIOStream(socket_stream),
        "socket_transport": native_socket,
    }
    return native, gate, record


def test_original_native_response_binding_is_data_only():
    native, gate, record = _native_data_binding()
    assert (
        transport._require_entry_tls_response_binding_v2(native, gate, record) is None
    )


@pytest.mark.parametrize(
    "mutation",
    ["tls_shadow", "ssl_shadow", "socket_shadow", "tls_context", "socket_owner"],
)
def test_response_owner_mutations_deny_without_foreign_callbacks(mutation):
    native, gate, record = _native_data_binding()
    callbacks = []

    def foreign(*_args):
        callbacks.append(True)

    if mutation == "tls_shadow":
        native.receive = foreign
    elif mutation == "ssl_shadow":
        native._ssl_object.read = foreign
    elif mutation == "socket_shadow":
        native.transport_stream.receive = foreign
    elif mutation == "tls_context":
        record["tls_context"].check_hostname = False
    else:
        native.transport_stream._transport._gate = object()
    with pytest.raises(transport.NativeLiveTransportError):
        transport._require_entry_tls_response_binding_v2(native, gate, record)
    assert callbacks == []
