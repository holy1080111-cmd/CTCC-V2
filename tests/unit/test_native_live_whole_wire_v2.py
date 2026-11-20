"""Exact ENTRY buffering with explicitly inert authority/SSL DATA seams.

No credentials, runtime, Arm, database, DNS or venue is used. The separate
faithful loopback test exercises the unmodified SSL/vendor/socket bodies.
"""

import asyncio
import ssl
from types import SimpleNamespace as Data

import anyio
import pytest
from anyio.streams.tls import TLSStream

from app.exchange.okx import native_live_transport_v1 as transport
from app.exchange.okx import native_live_write_v1 as write
from tests.unit.test_native_live_physical_v1 import _request_record


def _buffer(
    monkeypatch,
    *,
    wire=b"headers\r\n\r\nbody",
    partial=None,
    want_read=False,
    want_write=False,
):
    gate = write._PhysicalLiveWriteGateV1(key=write._GATE_KEY)
    record = {
        "wire": wire,
        "entry_wire_state": "collecting",
        "entry_chunk_offset": 0,
        "entry_chunk_count": 0,
        "tls_ready": True,
        "transport_taken": True,
        "accepted": 0,
        "attempted": False,
        "phase": "prepared",
    }
    events, state = [], {"fault": None, "raw_fault": None, "ssl_calls": 0}

    def preparation(current, *, key):
        assert current is gate and key is transport._TRANSPORT_KEY
        if state["fault"] is not None:
            raise state["fault"]
        if record["phase"] not in ("prepared", "attempted"):
            raise write.NativeLiveWriteError("synthetic_inactive_owner")
        return record

    def full(current):
        preparation(current, key=transport._TRANSPORT_KEY)
        events.append(("full",))
        return current

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    incoming, outgoing = ssl.MemoryBIO(), ssl.MemoryBIO()
    native_ssl = context.wrap_bio(
        incoming, outgoing, server_hostname="synthetic.invalid"
    )

    class WireData:
        _transport = Data(abort=lambda: events.append(("abort",)))

        async def send(self, value):
            await anyio.lowlevel.checkpoint()
            if state["raw_fault"] is not None:
                raise state["raw_fault"]
            events.append(("raw", value))

        async def receive(self):
            await anyio.lowlevel.checkpoint()
            events.append(("want-read",))
            return b"synthetic-handshake-data"

    native = TLSStream(WireData(), True, native_ssl, incoming, outgoing)

    def ssl_write(current, actual_ssl, data):
        assert current is gate and actual_ssl is native_ssl
        full(current)
        events.append(("ssl", data))
        state["ssl_calls"] += 1
        if (want_read or want_write) and state["ssl_calls"] == 1:
            outgoing.write(b"synthetic-control-ciphertext")
            raise ssl.SSLWantReadError if want_read else ssl.SSLWantWriteError
        count = min(partial, len(data)) if partial is not None else len(data)
        assert data == wire[record["accepted"] : record["accepted"] + len(data)]
        record["accepted"] += count
        record["attempted"], record["phase"] = True, "attempted"
        outgoing.write(b"synthetic-application-ciphertext")
        return count

    monkeypatch.setattr(write, "_require_preparation_scope_v2", preparation)
    monkeypatch.setattr(write, "_require_retained_response_identity_v2", preparation)
    monkeypatch.setattr(
        write, "_record", lambda current: record if current is gate else None
    )
    monkeypatch.setattr(
        transport, "_preparation_gate_v2", lambda current: full(current)
    )
    monkeypatch.setattr(transport, "_gate", full)
    monkeypatch.setattr(write._PhysicalLiveWriteGateV1, "ssl_write", ssl_write)
    stream = transport._TLSBytes(native, gate)
    events.clear()
    return Data(stream=stream, gate=gate, record=record, events=events, state=state)


@pytest.mark.asyncio
async def test_exact_prefix_emits_nothing_until_complete_and_only_one_whole_wire(
    monkeypatch,
):
    value = _buffer(monkeypatch)
    wire = value.record["wire"]
    await value.stream.send(wire[:5])
    assert value.events == []
    assert value.record["accepted"] == 0 and value.record["attempted"] is False
    await value.stream.send(wire[5:])
    assert [event for event in value.events if event[0] == "ssl"] == [("ssl", wire)]
    assert len([event for event in value.events if event[0] == "raw"]) == 1
    assert value.record["entry_wire_state"] == "complete"
    with pytest.raises(write.NativeLiveWriteError, match="prefix_denied"):
        await value.stream.send(wire)
    assert len([event for event in value.events if event[0] == "ssl"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation", ["reordered", "changed", "extra", "duplicate", "empty", "mutable"]
)
async def test_bad_prefix_burns_unsent_state_without_native_bytes(
    monkeypatch, mutation
):
    value = _buffer(monkeypatch)
    wire = value.record["wire"]
    if mutation == "reordered":
        bad = wire[5:]
    elif mutation == "changed":
        bad = b"X" + wire[1:5]
    elif mutation == "extra":
        bad = wire + b"extra"
    elif mutation == "empty":
        bad = b""
    elif mutation == "mutable":
        bad = bytearray(wire)
    else:
        await value.stream.send(wire[:5])
        bad = wire[:5]
    with pytest.raises(write.NativeLiveWriteError, match="prefix_denied"):
        await value.stream.send(bad)
    assert value.record["entry_wire_state"] == "aborted"
    assert value.record["entry_chunk_offset"] == 0
    assert value.record["phase"] == "unknown"
    assert not any(event[0] in ("ssl", "raw") for event in value.events)
    with pytest.raises(write.NativeLiveWriteError):
        await value.stream.send(wire)


@pytest.mark.asyncio
async def test_chunk_bound_refuses_without_any_emission(monkeypatch):
    value = _buffer(monkeypatch, wire=b"x" * 17)
    for _ in range(write._MAX_ENTRY_WIRE_CHUNKS_V2):
        await value.stream.send(b"x")
    assert value.events == []
    with pytest.raises(write.NativeLiveWriteError, match="prefix_denied"):
        await value.stream.send(b"x")
    assert value.record["phase"] == "unknown"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reason", ["emergency_stop", "expired", "revoked", "cancelled"]
)
async def test_last_prefix_liveness_failure_cannot_flush_buffer(monkeypatch, reason):
    value = _buffer(monkeypatch)
    wire = value.record["wire"]
    await value.stream.send(wire[:5])
    error = asyncio.CancelledError() if reason == "cancelled" else ValueError(reason)
    value.state["fault"] = error
    with pytest.raises(type(error)):
        await value.stream.send(wire[5:])
    assert value.record["entry_wire_state"] == "aborted"
    assert value.record["phase"] == "unknown"
    assert value.events == []


@pytest.mark.asyncio
async def test_first_chunk_cancellation_burns_the_original_unsent_owner(monkeypatch):
    value = _buffer(monkeypatch)
    value.state["fault"] = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await value.stream.send(value.record["wire"][:5])
    assert value.record["entry_wire_state"] == "aborted"
    assert value.record["phase"] == "unknown" and value.events == []
    value.state["fault"] = None
    with pytest.raises(write.NativeLiveWriteError):
        await value.stream.send(value.record["wire"])


@pytest.mark.asyncio
async def test_partial_ssl_acceptance_advances_only_original_suffix(monkeypatch):
    value = _buffer(monkeypatch, partial=5)
    wire = value.record["wire"]
    await value.stream.send(wire)
    assert [event[1] for event in value.events if event[0] == "ssl"] == [
        wire[offset:] for offset in range(0, len(wire), 5)
    ]
    assert value.record["accepted"] == len(wire)
    assert value.record["entry_wire_state"] == "complete"


@pytest.mark.asyncio
@pytest.mark.parametrize("progress", [0, True, -1, 1_000_000, None])
async def test_invalid_ssl_progress_cannot_replay_or_mark_complete(
    monkeypatch, progress
):
    value = _buffer(monkeypatch)

    async def invalid_progress(*_args):
        return progress

    monkeypatch.setattr(transport, "_TLS_CALL", invalid_progress)
    with pytest.raises(transport.NativeLiveTransportError, match="progress_invalid"):
        await value.stream.send(value.record["wire"])
    assert value.record["phase"] == "unknown"
    assert value.record["entry_wire_state"] == "aborted"
    assert value.record["accepted"] == 0


@pytest.mark.asyncio
async def test_cleanup_preserves_completed_wire_data_but_never_restores_authority(
    monkeypatch,
):
    value = _buffer(monkeypatch)
    wire = value.record["wire"]
    await value.stream.send(wire)
    value.record["phase"] = "spent"
    await value.stream.aclose()
    assert value.record["entry_wire_state"] == "complete"
    assert value.record["accepted"] == len(wire)
    assert value.record["phase"] == "spent"
    assert value.events[-1] == ("abort",)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["read", "write"])
async def test_vendor_want_resumes_full_ssl_callback_after_real_checkpoint(
    monkeypatch, kind
):
    value = _buffer(monkeypatch, want_read=kind == "read", want_write=kind == "write")
    wire = value.record["wire"]
    await value.stream.send(wire)
    assert [event for event in value.events if event[0] == "ssl"] == [
        ("ssl", wire),
        ("ssl", wire),
    ]
    assert (("want-read",) in value.events) is (kind == "read")
    assert len([event for event in value.events if event[0] == "raw"]) == 2
    assert value.record["entry_wire_state"] == "complete"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error", [ValueError("synthetic_estop_after_checkpoint"), asyncio.CancelledError()]
)
async def test_native_checkpoint_failure_keeps_unknown_and_never_resends(
    monkeypatch, error
):
    value = _buffer(monkeypatch)
    value.state["raw_fault"] = error
    with pytest.raises(type(error)):
        await value.stream.send(value.record["wire"])
    assert value.record["entry_wire_state"] == "aborted"
    assert value.record["phase"] == "unknown"
    assert not any(event[0] == "raw" for event in value.events)
    with pytest.raises(write.NativeLiveWriteError):
        await value.stream.send(value.record["wire"])


@pytest.mark.asyncio
async def test_incomplete_entry_never_starts_response_read(monkeypatch):
    value = _buffer(monkeypatch)
    await value.stream.send(value.record["wire"][:5])
    with pytest.raises(write.NativeLiveWriteError, match="incomplete"):
        await value.stream.receive()
    assert not any(event[0] == "want-read" for event in value.events)


def test_oversize_owned_request_denied_before_transport():
    record, request = _request_record()
    request.headers["user-agent"] = "x" * write._MAX_ENTRY_WIRE_V2
    with pytest.raises(write.NativeLiveWriteError, match="wire_bound"):
        write._bind_request(record, request)
    assert record["wire"] == b""


@pytest.mark.asyncio
async def test_controlled_close_family_keeps_original_chunk_behavior(monkeypatch):
    from app.exchange.okx import native_live_close_write_v1 as close

    value = _buffer(monkeypatch)
    monkeypatch.setattr(transport, "_authority", lambda _gate: close)
    calls = []

    async def old_vendor(_native, _call, item):
        calls.append(item)

    monkeypatch.setattr(transport, "_TLS_CALL", old_vendor)
    await value.stream.send(b"original-close-headers")
    await value.stream.send(b"original-close-body")
    assert calls == [b"original-close-headers", b"original-close-body"]
    assert value.record["entry_chunk_count"] == 0
