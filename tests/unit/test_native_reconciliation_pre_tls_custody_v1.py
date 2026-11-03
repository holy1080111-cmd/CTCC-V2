"""Actual local socket/TLS failure only; no Demo IO or native source permit."""

import asyncio
import socket
from types import SimpleNamespace

import httpcore
import httpx
import pytest
from anyio._backends._asyncio import SocketStream, StreamProtocol
from httpcore._backends.anyio import AnyIOStream

from app.exchange.okx import native_reconciliation_network_custody_v1 as custody
from app.exchange.okx import native_reconciliation_observation_v1 as read


async def local_stream():
    left, right = socket.socketpair()
    left.setblocking(False)
    transport = None
    try:
        transport, protocol = await asyncio.get_running_loop().create_connection(
            StreamProtocol, sock=left
        )
        transport.pause_reading()
        return transport, right, AnyIOStream(SocketStream(transport, protocol))
    except BaseException:
        left.close()
        right.close()
        if transport is not None:
            transport.close()
        raise


async def wait_closed(transport):
    async with asyncio.timeout(1):
        while transport._sock is not None:
            await asyncio.sleep(0)


@pytest.mark.parametrize(
    "kind,method",
    ((custody._RetainedStream, "read"), (custody._RetainedBackend, "connect_tcp")),
)
def test_instance_method_shadow_refuses_before_foreign_callback(kind, method):
    value = object.__new__(kind)
    called = []
    setattr(value, method, lambda *args, **kwargs: called.append(True))
    with pytest.raises(ValueError, match="network_custody_invalid"):
        getattr(value, method)()
    assert called == []


def test_in_place_captured_abort_code_change_is_never_invoked():
    def original(native):
        return True

    def foreign(native):
        raise AssertionError("foreign_cleanup_must_never_run")

    journal = SimpleNamespace(
        cleanup_abort=original,
        cleanup_abort_code=original.__code__,
        cleanup_hold={},
        tcp_transport=object(),
    )
    original.__code__ = foreign.__code__
    with pytest.raises(ValueError, match="cleanup_code_changed"):
        custody._abort_retained(journal)
    assert journal.cleanup_hold["phase"] == "unknown"


@pytest.mark.asyncio
async def test_late_native_cleanup_shadow_is_retained_and_never_invoked(monkeypatch):
    transport, peer, native = await local_stream()
    journal = read.ReadJournalV1(
        "/api/v5/account/config",
        (),
        "https://www.okx.com/api/v5/account/config",
        "a" * 64,
    )
    called = []
    try:
        custody._retain_tcp_owner_v1(journal, native)
        with monkeypatch.context() as change:
            change.setattr(transport, "_force_close", lambda *args: called.append(True))
            await asyncio.sleep(0)
            with pytest.raises(ValueError, match="cleanup_unknown"):
                journal.raw_transport_proxy.abort()
            assert called == [] and journal.raw_socket.fileno() >= 0
            assert journal.cleanup_hold["phase"] == "unknown"
            assert journal.cleanup_hold["cleanup_unknown_sockets"][0] is transport
    finally:
        peer.close()
        transport.close()


@pytest.mark.asyncio
async def test_raw_csocket_remains_owned_after_native_callback_clears_transport_sock():
    transport, peer, native = await local_stream()
    journal = read.ReadJournalV1(
        "/api/v5/account/config",
        (),
        "https://www.okx.com/api/v5/account/config",
        "a" * 64,
    )
    try:
        custody._retain_tcp_owner_v1(journal, native)
        original = journal.raw_socket
        assert original is transport._sock and original.fileno() >= 0
        transport.close()
        await wait_closed(transport)
        assert journal.raw_socket is original and original.fileno() == -1
        assert (
            journal.tcp_transport is transport
            and journal.socket_stream is native._stream
        )
    finally:
        peer.close()
        transport.close()


@pytest.mark.asyncio
async def test_tls_failure_before_any_http_headers_keeps_original_tcp_owner():
    transport, peer, native = await local_stream()
    async with httpx.AsyncClient(trust_env=False) as client:
        journal = read.ReadJournalV1(
            "/api/v5/account/config",
            (),
            "https://www.okx.com/api/v5/account/config",
            "a" * 64,
        )
        journal.transport_client = client
        try:
            custody._retain_tcp_owner_v1(journal, native)
            original = journal.raw_socket
            peer.sendall(b"not-a-tls-record\r\n")
            peer.close()
            wrapped = custody._RetainedStream(journal, native)
            with pytest.raises((httpcore.ConnectError, httpcore.ConnectTimeout)):
                await wrapped.start_tls(
                    client._transport._pool._ssl_context,
                    server_hostname="www.okx.com",
                    timeout=0.25,
                )
            assert journal.response is None and journal.headers_stamp is None
            assert journal.tcp_stream is native and journal.raw_socket is original
            await wait_closed(transport)
            assert original.fileno() == -1
        finally:
            peer.close()
            transport.close()
