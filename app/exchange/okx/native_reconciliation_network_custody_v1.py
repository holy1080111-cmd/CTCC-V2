"""Retain actual fixed-GET network owners before TLS/response-header awaits.

HTTPX/httpcore keep signing and request/response IO through their SAME AnyIO
backend. Actual returned TCP/TLS objects are retained; negative closure uses
the existing captured native abort chain without buffered/TLS flushing. No
caller transport, signer, retry or gate is accepted. Retention is DATA, never
a successful-close or execution witness.
"""

import socket
import ssl
from asyncio.proactor_events import _ProactorSocketTransport
from asyncio.selector_events import _SelectorSocketTransport
from types import FunctionType
from urllib.parse import urlsplit

import httpcore
import httpx
from anyio._backends._asyncio import SocketStream
from anyio.streams.tls import TLSStream
from httpcore._backends.anyio import AnyIOBackend, AnyIOStream
from httpcore._backends.auto import AutoBackend


def _need(condition):
    if condition is not True:
        raise ValueError("native_reconciliation_network_custody_invalid") from None


def _vendors():
    from app.exchange.okx import native_demo_write_v2 as write

    write._require_vendor_state_v2()
    # Old25 admission lacks this exact getter's pristine-source check. It must
    # not silently become sufficient merely because an identity was captured.
    _need(
        any(
            owner is AnyIOStream
            and attribute == "get_extra_info"
            and original is vars(AnyIOStream).get("get_extra_info")
            for owner, attribute, original in write._VENDOR_ADMISSION_V2.bindings
        )
    )


def _stream(journal, native):
    _vendors()
    _need(
        type(native) is AnyIOStream
        and not {"read", "write", "start_tls", "aclose", "get_extra_info"}.intersection(
            vars(native)
        )
    )
    if native is journal.tcp_stream:
        _need(
            native._stream is journal.socket_stream
            and type(native._stream) is SocketStream
        )
    else:
        _need(
            native is journal.tls_stream
            and type(native._stream) is TLSStream
            and native._stream.transport_stream is journal.socket_stream
            and native._stream._ssl_object is journal.tls_object
            and not {
                "send",
                "receive",
                "aclose",
                "_call_sslobject_method",
            }.intersection(vars(native._stream))
        )
    _need(
        not {"send", "receive", "aclose"}.intersection(vars(journal.socket_stream))
        and journal.socket_stream._transport is journal.raw_transport_proxy
    )
    return native


def _abort_retained(journal):
    callback = journal.cleanup_abort
    if (
        type(callback) is not FunctionType
        or callback.__code__ is not journal.cleanup_abort_code
    ):
        journal.cleanup_hold["phase"] = "unknown"
        raise ValueError("native_reconciliation_cleanup_code_changed") from None
    if callback(journal.tcp_transport) is not True:
        raise ValueError("native_reconciliation_cleanup_unknown") from None


class _RawTransport:
    """GET-only delegate; cleanup uses the existing captured native abort chain."""

    __slots__ = ("_journal", "_native", "_write", "_write_code")

    def __init__(self, native, journal):
        from app.exchange.okx import native_demo_write_v2 as write

        self._native, self._journal = native, journal
        matches = [
            pin.function
            for pin in write._VENDOR_ADMISSION_V2._functions
            if pin.lookup is type(native) and pin.attribute == "write"
        ]
        _need(len(matches) == 1)
        self._write, self._write_code = matches[0], matches[0].__code__

    def write(self, data):
        _vendors()
        _need(
            self._write.__code__ is self._write_code
            and not {
                "write",
                "writelines",
                "write_eof",
                "close",
                "abort",
                "_force_close",
            }.intersection(vars(self._native))
        )
        self._write(self._native, data)

    def write_eof(self):
        # Never flush a possibly uncertain TLS buffer during negative cleanup.
        _abort_retained(self._journal)

    def close(self):
        _abort_retained(self._journal)

    def abort(self):
        _abort_retained(self._journal)

    def is_closing(self):
        _need(type(self._native._closing) is bool)
        return self._native._closing

    def __getattr__(self, name):
        return getattr(self._native, name)


def _retain_tcp_owner_v1(journal, native):
    # No await precedes full actual-owner retention after connect returns.
    journal.tcp_stream = native
    _need(type(native) is AnyIOStream and type(native._stream) is SocketStream)
    journal.socket_stream = native._stream
    journal.tcp_transport = native._stream._transport
    _need(
        type(journal.tcp_transport)
        in (_ProactorSocketTransport, _SelectorSocketTransport)
    )
    journal.raw_socket = journal.tcp_transport._sock
    _need(type(journal.raw_socket) is socket.socket)
    from app.exchange.okx import native_demo_write_v2 as write

    _vendors()
    journal.cleanup_hold = {"phase": "retained_before_tls"}
    journal.cleanup_abort = write._native_vendors.capture_native_demo_cleanup_v2(
        write._VENDOR_ADMISSION_V2, journal.cleanup_hold
    )
    journal.cleanup_abort_code = journal.cleanup_abort.__code__
    journal.raw_transport_proxy = _RawTransport(journal.tcp_transport, journal)
    journal.socket_stream._transport = journal.raw_transport_proxy
    _stream(journal, native)


class _RetainedStream(httpcore.AsyncNetworkStream):
    __slots__ = ("journal", "native")

    def __getattribute__(self, name):
        # httpcore's base provides an instance dictionary despite our slots.
        # Deny a late per-instance method shadow before SDK dispatch can invoke
        # it; class replacements are pinned by the native resume guard.
        if name in {"read", "write", "aclose", "start_tls", "get_extra_info"}:
            _need(name not in object.__getattribute__(self, "__dict__"))
        return object.__getattribute__(self, name)

    def __init__(self, journal, native):
        self.journal, self.native = journal, native

    async def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        journal = self.journal
        _stream(journal, self.native)
        _need(
            self.native is journal.tcp_stream
            and journal.tls_stream is None
            and ssl_context is journal.transport_client._transport._pool._ssl_context
            and type(ssl_context) is ssl.SSLContext
            and ssl_context.check_hostname is True
            and ssl_context.verify_mode == ssl.CERT_REQUIRED
            and ssl_context.minimum_version >= ssl.TLSVersion.TLSv1_2
            and ssl_context.keylog_filename is None
            and getattr(ssl_context, "_msg_callback", None) is None
            and server_hostname == urlsplit(journal.request_url).hostname
        )
        # The raw C socket/native transport are ALREADY retained. AnyIO may
        # raise/close here before httpcore has assigned its _connection field.
        native = await self.native.start_tls(ssl_context, server_hostname, timeout)
        journal.tls_stream = native
        _need(type(native) is AnyIOStream and type(native._stream) is TLSStream)
        journal.tls_object = native._stream._ssl_object
        _stream(journal, native)
        return _RetainedStream(journal, native)

    async def read(self, max_bytes, timeout=None):
        return await _stream(self.journal, self.native).read(max_bytes, timeout)

    async def write(self, buffer, timeout=None):
        await _stream(self.journal, self.native).write(buffer, timeout)

    async def aclose(self):
        # This is a negative abort request, NOT an observed closed proof. The
        # Root pure observer must still see fd/callback/client completion.
        _abort_retained(self.journal)

    def get_extra_info(self, info):
        return _stream(self.journal, self.native).get_extra_info(info)


class _RetainedBackend(httpcore.AsyncNetworkBackend):
    __slots__ = ("connected", "journal", "native")

    def __getattribute__(self, name):
        if name in {"connect_tcp", "connect_unix_socket", "sleep"}:
            _need(name not in object.__getattribute__(self, "__dict__"))
        return object.__getattribute__(self, name)

    def __init__(self, journal):
        self.journal, self.native, self.connected = journal, AnyIOBackend(), False

    async def connect_tcp(
        self, host, port, timeout=None, local_address=None, socket_options=None
    ):
        _vendors()
        _need(
            type(self.native) is AnyIOBackend
            and vars(self.native) == {}
            and not self.connected
            and host == urlsplit(self.journal.request_url).hostname
            and port == 443
            and local_address is None
            and socket_options is None
        )
        self.connected = True
        native = await self.native.connect_tcp(
            host, port, timeout, local_address, socket_options
        )
        journal = self.journal
        # Retain BEFORE any later await, TLS setup or late validation can fail.
        _retain_tcp_owner_v1(journal, native)
        return _RetainedStream(journal, native)

    async def connect_unix_socket(self, *args, **kwargs):
        raise ValueError("native_reconciliation_unix_route_denied")

    async def sleep(self, seconds):
        raise ValueError("native_reconciliation_network_retry_denied")


def install_fixed_read_network_custody_v1(journal):
    _vendors()
    client = journal.transport_client
    _need(
        type(client) is httpx.AsyncClient
        and type(client._transport) is httpx.AsyncHTTPTransport
    )
    pool = client._transport._pool
    _need(
        type(pool) is httpcore.AsyncConnectionPool
        and type(pool._network_backend) is AutoBackend
        and vars(pool._network_backend) == {}
        and pool._retries == 0
        and type(pool._connections) is list
        and not pool._connections
        and type(pool._requests) is list
        and not pool._requests
        and pool._uds is None
        and pool._local_address is None
    )
    journal.network_backend = _RetainedBackend(journal)
    pool._network_backend = journal.network_backend
