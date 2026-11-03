"""Inert response-parser tests; no native gate, credential, socket or authority."""

import asyncio
import sys
from types import ModuleType

import pytest

from app.exchange.okx.native_demo_transport_v2 import (
    NativeDemoTransportError,
    _validate_response_headers_v2,
)


@pytest.mark.parametrize(
    "headers",
    [
        [],
        [(b"Content-Type", b"application/json")],
        [(b"Content-Encoding", b"identity")],
    ],
)
def test_unencoded_response_data_has_no_decoder_expansion(headers):
    assert _validate_response_headers_v2(headers) is None


@pytest.mark.parametrize(
    "encoding",
    [b"gzip", b"br", b"deflate", b"gzip, identity", b"", b" identity", b"IDENTITY"],
)
def test_encoded_response_is_denied_before_httpx_decoder_selection(encoding):
    with pytest.raises(
        NativeDemoTransportError,
        match="^native_demo_transport_encoded_response_denied$",
    ):
        _validate_response_headers_v2([(b"Content-Encoding", encoding)])


def test_duplicate_content_encoding_is_not_collapsed():
    with pytest.raises(NativeDemoTransportError, match="encoded_response_denied"):
        _validate_response_headers_v2(
            [(b"Content-Encoding", b"identity"), (b"content-encoding", b"identity")]
        )


def test_native_header_count_boundary_is_enforced():
    assert _validate_response_headers_v2([(b"x", b"v")] * 64) is None
    with pytest.raises(NativeDemoTransportError, match="headers_bound"):
        _validate_response_headers_v2([(b"x", b"v")] * 65)


def test_total_byte_boundary_is_enforced():
    assert _validate_response_headers_v2([(b"x", b"v" * 16383)]) is None
    with pytest.raises(NativeDemoTransportError, match="headers_bound"):
        _validate_response_headers_v2([(b"x", b"v" * 16384)])


@pytest.mark.parametrize(
    "headers",
    [None, {}, b"raw", [(b"x",)], [[b"x", b"v"]], [("x", b"v")], [(b"x", "v")]],
)
def test_malformed_native_header_shape_is_not_repaired(headers):
    with pytest.raises(NativeDemoTransportError, match="headers_bound"):
        _validate_response_headers_v2(headers)


def test_foreign_container_cannot_run_len_or_iteration():
    called = []

    class Foreign:
        def __len__(self):
            called.append("len")
            raise AssertionError

        def __iter__(self):
            called.append("iter")
            raise AssertionError

    with pytest.raises(NativeDemoTransportError, match="headers_bound"):
        _validate_response_headers_v2(Foreign())
    assert called == []


def test_private_response_value_is_not_echoed_in_failure():
    sentinel = b"INERT_PRIVATE_RESPONSE_SENTINEL"
    with pytest.raises(NativeDemoTransportError) as failure:
        _validate_response_headers_v2([(b"Content-Encoding", sentinel)])
    assert str(failure.value) == "native_demo_transport_encoded_response_denied"
    assert sentinel.decode() not in str(failure.value)


def _data_only_stream_fixture(
    monkeypatch, producer, *, expire_on_finish=False, fail_close=False
):
    """Only DATA hooks; no gate, native owner, credential or request authority."""
    from app.exchange import okx
    from app.exchange.okx import native_demo_transport_v2 as adapter

    journal = adapter._ResponseDataJournalV2()
    journal.retain_headers(200, [(b"content-type", b"application/json")])
    held = {"phase": "attempted"}
    authority = ModuleType("app.exchange.okx.native_demo_write_v2")
    authority._retain_native_body_data_v2 = lambda gate, value, **kw: (
        journal.retain_body(value)
    )
    authority._native_response_body_limit_v1 = lambda gate, **kw: journal._body_limit
    authority._retain_native_response_eof_v2 = lambda gate, **kw: journal.retain_eof()

    def retain_fault(gate, code, **kw):
        journal.retain_fault(code)
        if code == "close":
            held["phase"] = "unknown"

    authority._retain_native_response_fault_v2 = retain_fault
    monkeypatch.setitem(sys.modules, authority.__name__, authority)
    monkeypatch.setattr(okx, "native_demo_write_v2", authority, raising=False)

    class Gate:
        def finish(self, success):
            if success and not expire_on_finish:
                held["phase"] = "completed"
            elif success:
                journal.retain_fault("live_after_response")
                held["phase"] = "unknown"

    class Response:
        def aiter_stream(self):
            return producer()

        async def aclose(self):
            if fail_close:
                raise RuntimeError("inert_close_failure")

    class Backend:
        def abort(self):
            return None

    return adapter._ResponseStream(Response(), Gate(), Backend()), journal, held


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ("final_chunk", "EOF"))
async def test_actual_received_final_bytes_survive_late_expiry_data_only(
    monkeypatch, boundary
):
    raw = b'{"inert_order_observation":"already_received"}'

    async def producer():
        if boundary == "final_chunk":
            await asyncio.sleep(0)
        yield raw
        if boundary == "EOF":
            await asyncio.sleep(0)

    stream, journal, held = _data_only_stream_fixture(
        monkeypatch, producer, expire_on_finish=True
    )
    assert [chunk async for chunk in stream] == [raw]
    assert journal.body_raw == raw and journal.body_complete is True
    assert held["phase"] == "unknown" and journal.faults == ["live_after_response"]


@pytest.mark.asyncio
async def test_complete_received_body_survives_failed_close_data_only(monkeypatch):
    raw = b'{"inert_order_observation":"known_before_close"}'

    async def producer():
        await asyncio.sleep(0)
        yield raw

    stream, journal, held = _data_only_stream_fixture(
        monkeypatch, producer, fail_close=True
    )
    assert [chunk async for chunk in stream] == [raw]
    with pytest.raises(RuntimeError, match="inert_close_failure"):
        await stream.aclose()
    assert journal.body_complete is True and journal.body_raw == raw
    assert journal.faults == ["close"]
    assert held["phase"] == "unknown"


def test_received_journal_caps_bytes_and_preserves_partial_data():
    from app.exchange.okx import native_demo_transport_v2 as adapter

    journal = adapter._ResponseDataJournalV2()
    journal.retain_headers(200, [(b"x", b"v")])
    journal.retain_body(b"a" * (adapter._MAX_BODY + 1))
    journal.retain_eof()
    assert len(journal.body_raw) == adapter._MAX_BODY
    assert journal.body_complete is False and journal.overflow is True
