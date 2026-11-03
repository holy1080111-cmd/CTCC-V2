"""Private HTTP/1.1 transport for the native Demo intent boundary.

This adapter creates no authority. Its only gate is the exact private gate of
native_demo_write_v2, which must retain real account/Control database locks,
the original native intent and its absolute deadlines. That module is a
required dependency; no legacy/loopback gate or caller callback is accepted.
"""

from __future__ import annotations

import ssl
from asyncio.proactor_events import _ProactorSocketTransport
from asyncio.selector_events import _SelectorSocketTransport
from types import FunctionType

import anyio
import httpcore
import httpx
from anyio._backends._asyncio import SocketStream
from anyio.streams.tls import TLSStream
from httpcore._backends.anyio import AnyIOBackend, AnyIOStream

_TRANSPORT_KEY = object()
_TLS_CALL = TLSStream._call_sslobject_method
_SOCKET_SEND = SocketStream.send
_SSL_WRITE = ssl.SSLObject.write
_MAX_BODY = 1024 * 1024
_MAX_HEADERS = 16 * 1024
_VENDOR_BINDINGS_V2 = (
    (TLSStream, "_call_sslobject_method", _TLS_CALL),
    (TLSStream, "send", TLSStream.send),
    (TLSStream, "receive", TLSStream.receive),
    (TLSStream, "aclose", TLSStream.aclose),
    (SocketStream, "send", _SOCKET_SEND),
    (SocketStream, "receive", SocketStream.receive),
    (SocketStream, "aclose", SocketStream.aclose),
    (AnyIOStream, "read", AnyIOStream.read),
    (AnyIOStream, "write", AnyIOStream.write),
    (AnyIOStream, "start_tls", AnyIOStream.start_tls),
    (AnyIOStream, "aclose", AnyIOStream.aclose),
    (AnyIOBackend, "connect_tcp", AnyIOBackend.connect_tcp),
    (ssl.SSLObject, "write", _SSL_WRITE),
    (ssl.SSLObject, "read", ssl.SSLObject.read),
    (_ProactorSocketTransport, "write", _ProactorSocketTransport.write),
    (_ProactorSocketTransport, "abort", _ProactorSocketTransport.abort),
    (_ProactorSocketTransport, "close", _ProactorSocketTransport.close),
    (_SelectorSocketTransport, "write", _SelectorSocketTransport.write),
    (_SelectorSocketTransport, "abort", _SelectorSocketTransport.abort),
    (_SelectorSocketTransport, "close", _SelectorSocketTransport.close),
)


class NativeDemoTransportError(ValueError):
    """Fixed error codes only; no request, response or credential formatting."""


class _ResponseDataJournalV2:
    """Bounded private DATA, never an owner, live permit or retry decision.

    Root attaches its own instance to the genuine retained native state. This
    inert constructor alone authenticates no source or response. Known bytes
    survive expiry, parser rejection and failed close; completeness is a DATA
    fact only when the actual HTTP stream reaches its end marker.
    """

    __slots__ = (
        "_body_limit",
        "body_bytes",
        "body_chunks",
        "eof",
        "faults",
        "headers",
        "overflow",
        "status",
        "tls_bytes",
        "tls_chunks",
    )

    def __init__(self, *, body_limit=_MAX_BODY):
        _need(
            type(body_limit) is int and body_limit in (65536, _MAX_BODY),
            "native_demo_transport_data_limit_invalid",
        )
        self._body_limit = body_limit
        self.status, self.headers = None, ()
        self.tls_chunks, self.body_chunks, self.faults = [], [], []
        self.tls_bytes = self.body_bytes = 0
        self.eof = self.overflow = False

    def retain_headers(self, status, headers):
        self.status = status if type(status) is int else None
        if type(headers) not in (tuple, list):
            self.overflow = True
            return
        rows, size = [], 0
        if len(headers) > 64:
            self.overflow = True
        for pair in headers[:64]:
            if (
                type(pair) is not tuple
                or len(pair) != 2
                or type(pair[0]) is not bytes
                or type(pair[1]) is not bytes
            ):
                self.overflow = True
                break
            name, value = pair
            remaining = _MAX_HEADERS - size
            kept_name = name[:remaining]
            remaining -= len(kept_name)
            kept_value = value[:remaining]
            rows.append((kept_name, kept_value))
            size += len(kept_name) + len(kept_value)
            if len(kept_name) != len(name) or len(kept_value) != len(value):
                self.overflow = True
                break
        self.headers = tuple(rows)

    def retain_tls(self, value):
        self._retain_chunk(value, tls=True)

    def retain_body(self, value):
        self._retain_chunk(value, tls=False)

    def _retain_chunk(self, value, *, tls):
        if type(value) is not bytes:
            self.overflow = True
            return
        chunks = self.tls_chunks if tls else self.body_chunks
        count = self.tls_bytes if tls else self.body_bytes
        limit = self._body_limit + _MAX_HEADERS if tls else self._body_limit
        if len(chunks) >= 1024:
            self.overflow = True
            return
        kept = value[: max(0, limit - count)]
        if kept:
            chunks.append(kept)
        if tls:
            self.tls_bytes += len(kept)
        else:
            self.body_bytes += len(kept)
        if len(kept) != len(value):
            self.overflow = True

    def retain_eof(self):
        self.eof = True

    def retain_fault(self, code):
        if (
            type(code) is str
            and code
            in {"live_after_response", "receive", "body", "close", "cleanup_mutation"}
            and code not in self.faults
        ):
            self.faults.append(code)

    @property
    def body_raw(self):
        return b"".join(self.body_chunks)

    @property
    def body_complete(self):
        return self.eof and not self.overflow and self.status is not None


def _need(condition, code):
    if condition is not True:
        raise NativeDemoTransportError(code) from None


def _unshadowed(instance, names):
    _need(
        not set(getattr(instance, "__dict__", {})).intersection(names),
        "native_demo_transport_method_shadowed",
    )


def _validate_response_headers_v2(headers):
    """Bound response DATA before HTTPX can select a content decoder."""
    _need(
        type(headers) in (list, tuple) and len(headers) <= 64,
        "native_demo_transport_response_headers_bound",
    )
    size, encodings = 0, []
    for pair in headers:
        _need(
            type(pair) is tuple
            and len(pair) == 2
            and type(pair[0]) is bytes
            and type(pair[1]) is bytes,
            "native_demo_transport_response_headers_bound",
        )
        name, value = pair
        size += len(name) + len(value)
        _need(size <= _MAX_HEADERS, "native_demo_transport_response_headers_bound")
        if name.lower() == b"content-encoding":
            encodings.append(value)
    _need(
        not encodings or encodings == [b"identity"],
        "native_demo_transport_encoded_response_denied",
    )


def _gate(value):
    from app.exchange.okx import native_demo_write_v2 as authority

    _need(
        type(value) is authority._PhysicalWriteGateV2,
        "native_demo_transport_owned_gate_required",
    )
    # Registry membership, source closure, task, held transaction, revocation,
    # credential-session and deadlines are checked by the actual authority.
    authority._require_physical_gate_v2(value, key=_TRANSPORT_KEY)
    _require_vendor_bindings_v2()
    return value


def _require_vendor_bindings_v2():
    # The same finite bindings are also captured by the outer coroutine guard,
    # so a post-await replacement is rejected before a vendor callback runs.
    from app.exchange.okx import native_demo_write_v2 as authority

    authority._require_vendor_state_v2()


class _CapturedAbort:
    __slots__ = (
        "_builtins",
        "_call",
        "_cells",
        "_closure",
        "_code",
        "_hold",
        "_namespace",
    )

    def __init__(self, gate):
        from app.exchange.okx import native_demo_write_v2 as authority

        call, hold = authority._capture_abort_pins_v2(gate)
        _need(
            type(call) is FunctionType
            and call.__defaults__ is None
            and call.__kwdefaults__ is None,
            "native_demo_transport_cleanup_unconfirmed",
        )
        self._call, self._code = call, call.__code__
        self._namespace, self._builtins = call.__globals__, call.__builtins__
        self._hold, self._closure = hold, call.__closure__
        self._cells = tuple(cell.cell_contents for cell in call.__closure__ or ())

    def invoke(self, native):
        # This captured cleanup path grants no write permission and works after
        # source revocation. Changed code is retained as unknown, never invoked.
        call = self._call
        if not (
            type(call) is FunctionType
            and call.__code__ is self._code
            and call.__globals__ is self._namespace
            and call.__builtins__ is self._builtins
            and call.__closure__ is self._closure
            and call.__defaults__ is None
            and call.__kwdefaults__ is None
            and (
                self._closure is None
                or (
                    len(self._closure) == len(self._cells)
                    and all(
                        cell.cell_contents is expected
                        for cell, expected in zip(
                            self._closure, self._cells, strict=True
                        )
                    )
                )
            )
        ):
            # No mutable global `_need` or exception constructor is invoked.
            self._hold["cleanup_corrupt_socket"], self._hold["phase"] = (
                native,
                "unknown",
            )
            return False
        return call(native)


class _RawTransport:
    __slots__ = ("_abort", "_gate", "_native")

    def __init__(self, native, gate, abort):
        _need(
            type(native) in (_ProactorSocketTransport, _SelectorSocketTransport),
            "native_demo_transport_native_socket_required",
        )
        _unshadowed(native, {"write", "writelines", "abort", "close"})
        _need(
            type(abort) is _CapturedAbort, "native_demo_transport_cleanup_unconfirmed"
        )
        self._native, self._gate, self._abort = native, _gate(gate), abort

    def write(self, data):
        # B: SocketStream.send invokes this after its cancellation checkpoint.
        # This is a synchronous guard immediately adjacent to native IO.
        return _gate(self._gate).raw_write(self._native, data)

    def __getattr__(self, name):
        return getattr(self._native, name)

    def abort(self):
        return self._abort.invoke(self._native)

    def close(self):
        return self._abort.invoke(self._native)


class _TLSBytes(anyio.abc.ByteStream):
    __slots__ = ("_gate", "_native")

    def __init__(self, native, gate):
        _need(
            type(native) is TLSStream and type(native._ssl_object) is ssl.SSLObject,
            "native_demo_transport_native_tls_required",
        )
        _unshadowed(native, {"send", "receive", "aclose", "_call_sslobject_method"})
        _unshadowed(native._ssl_object, {"write", "read"})
        _need(ssl.SSLObject.write is _SSL_WRITE, "native_demo_transport_ssl_changed")
        self._native, self._gate = native, _gate(gate)

    async def send(self, item):
        def write(data):
            # A: every SSL write, including WantRead/WantWrite continuations,
            # replays the same one-shot invocation after any internal await.
            return _gate(self._gate).ssl_write(self._native._ssl_object, data)

        _gate(self._gate)
        await _TLS_CALL(self._native, write, item)
        _gate(self._gate)

    async def receive(self, max_bytes=65536):
        _gate(self._gate)
        value = await self._native.receive(max_bytes)
        # New reads still require the live gate BEFORE their await. Once bytes
        # have actually arrived they are DATA: retain and return them to the
        # HTTP parser, even if the source expires at this response boundary.
        # Another network read must independently pass the live gate again.
        from app.exchange.okx import native_demo_write_v2 as authority

        authority._retain_native_tls_data_v2(self._gate, value, key=_TRANSPORT_KEY)
        return value

    async def send_eof(self):
        raise NativeDemoTransportError("native_demo_transport_eof_denied")

    async def aclose(self):
        # Never flush an application request or a TLS close handshake while
        # handling an expired, revoked, cancelled or ambiguous submission.
        self._native.transport_stream._transport.abort()

    @property
    def extra_attributes(self):
        return self._native.extra_attributes


class _ConnectionStream(httpcore.AsyncNetworkStream):
    __slots__ = ("_gate", "_native", "_started")

    def __init__(self, native, gate, abort):
        _need(
            type(native) is AnyIOStream and type(native._stream) is SocketStream,
            "native_demo_transport_native_stream_required",
        )
        _unshadowed(native, {"read", "write", "start_tls", "aclose"})
        _unshadowed(native._stream, {"send", "receive", "aclose"})
        _need(
            type(native._stream).send is _SOCKET_SEND,
            "native_demo_transport_send_changed",
        )
        self._native, self._gate, self._started = native, _gate(gate), False
        native._stream._transport = _RawTransport(
            native._stream._transport, gate, abort
        )

    async def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        gate = _gate(self._gate)
        _need(
            not self._started
            and server_hostname == gate.host
            and type(ssl_context) is ssl.SSLContext
            and ssl_context is gate.tls_context
            and ssl_context.check_hostname
            and ssl_context.verify_mode == ssl.CERT_REQUIRED
            and ssl_context.minimum_version >= ssl.TLSVersion.TLSv1_2
            and ssl_context.keylog_filename is None
            and getattr(ssl_context, "_msg_callback", None) is None,
            "native_demo_transport_tls_target_rejected",
        )
        self._started = True
        try:
            native = await self._native.start_tls(ssl_context, server_hostname, timeout)
            gate = _gate(self._gate)
            _need(
                type(native) is AnyIOStream and type(native._stream) is TLSStream,
                "native_demo_transport_native_tls_required",
            )
            _need(
                native._stream._ssl_object.selected_alpn_protocol()
                in (None, "http/1.1"),
                "native_demo_transport_alpn_rejected",
            )
            # Re-read actual Control/scope/intent on the SAME locked database
            # session after the TLS network await, before any HTTP bytes.
            await gate.tls_ready_after_locked_readback()
            _gate(self._gate)
            return AnyIOStream(_TLSBytes(native._stream, gate))
        except BaseException:
            # Cancellation is BaseException. Abort rather than attempt a
            # retry, buffered flush, TLS shutdown or unguarded second write.
            self._native._stream._transport.abort()
            raise

    async def read(self, max_bytes, timeout=None):
        raise NativeDemoTransportError("native_demo_transport_cleartext_denied")

    async def write(self, buffer, timeout=None):
        raise NativeDemoTransportError("native_demo_transport_cleartext_denied")

    async def aclose(self):
        self._native._stream._transport.abort()

    def get_extra_info(self, info):
        return self._native.get_extra_info(info)


class _Backend(httpcore.AsyncNetworkBackend):
    __slots__ = ("_abort", "_connected", "_gate", "_native")

    def __init__(self, gate):
        self._gate, self._connected, self._native = _gate(gate), False, None
        self._abort = _CapturedAbort(gate)

    def abort(self):
        # Cleanup carries no request permission and must work after revocation,
        # expiry, cancellation or response rejection without another await.
        if self._native is not None:
            native = self._native._stream._transport
            if type(native) is _RawTransport:
                native = native._native
            self._abort.invoke(native)

    async def connect_tcp(
        self, host, port, timeout=None, local_address=None, socket_options=None
    ):
        gate = _gate(self._gate)
        _need(
            not self._connected
            and host == gate.host
            and port == 443
            and local_address is None
            and socket_options is None,
            "native_demo_transport_connect_rejected",
        )
        self._connected = True
        native = await AnyIOBackend().connect_tcp(
            host, port, timeout, local_address, socket_options
        )
        self._native = native
        from app.exchange.okx import native_demo_write_v2 as authority

        # Retain actual exposed TCP/C-socket owners before TLS or a late gate.
        authority._retain_native_socket_owners_v1(
            self._gate, self, native, key=_TRANSPORT_KEY
        )
        try:
            _gate(self._gate)
            return _ConnectionStream(native, self._gate, self._abort)
        except BaseException:
            # The raw native socket has no HTTP plaintext buffered here.
            # Synchronous abort also works when the enclosing Task is cancelled.
            self.abort()
            raise

    async def connect_unix_socket(self, *args, **kwargs):
        raise NativeDemoTransportError("native_demo_transport_unix_denied")

    async def sleep(self, seconds):
        raise NativeDemoTransportError("native_demo_transport_retry_denied")


class _ResponseStream(httpx.AsyncByteStream):
    __slots__ = ("_backend", "_body", "_closed", "_gate", "_response")

    def __init__(self, response, gate, backend):
        self._response, self._gate, self._backend = response, gate, backend
        self._body, self._closed = 0, False

    async def __aiter__(self):
        from app.exchange.okx import native_demo_write_v2 as authority

        success = False
        try:
            async for chunk in self._response.aiter_stream():
                authority._retain_native_body_data_v2(
                    self._gate, chunk, key=_TRANSPORT_KEY
                )
                _need(type(chunk) is bytes, "native_demo_transport_response_type")
                self._body += len(chunk)
                maximum = authority._native_response_body_limit_v1(
                    self._gate, key=_TRANSPORT_KEY
                )
                _need(self._body <= maximum, "native_demo_transport_response_bound")
                yield chunk
            authority._retain_native_response_eof_v2(self._gate, key=_TRANSPORT_KEY)
            success = True
        finally:
            self._gate.finish(success)

    async def aclose(self):
        from app.exchange.okx import native_demo_write_v2 as authority

        if not self._closed:
            self._closed = True
            try:
                self._backend.abort()
                await self._response.aclose()
            except BaseException:
                authority._retain_native_response_fault_v2(
                    self._gate, "close", key=_TRANSPORT_KEY
                )
                raise
            finally:
                self._gate.finish(False)


class _Transport(httpx.AsyncBaseTransport):
    __slots__ = ("_backend", "_gate", "_pool")

    def __init__(self, gate, *, key):
        _need(key is _TRANSPORT_KEY, "native_demo_transport_constructor_denied")
        gate = _gate(gate)
        self._gate = gate
        self._backend = _Backend(gate)
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=gate.tls_context,
            network_backend=self._backend,
            retries=0,
            http1=True,
            http2=False,
            max_connections=1,
            max_keepalive_connections=0,
        )

    async def handle_async_request(self, request):
        gate = _gate(self._gate)
        gate.take_transport(request)
        core = httpcore.Request(
            method=request.method,
            url=httpcore.URL(
                scheme=request.url.raw_scheme,
                host=request.url.raw_host,
                port=request.url.port,
                target=request.url.raw_path,
            ),
            headers=tuple(request.headers.raw),
            content=request.content,
            extensions=dict(request.extensions),
        )
        response = None
        try:
            response = await self._pool.handle_async_request(core)
            from app.exchange.okx import native_demo_write_v2 as authority

            authority._retain_native_response_headers_v2(
                self._gate, response.status, response.headers, key=_TRANSPORT_KEY
            )
            _validate_response_headers_v2(response.headers)
            return httpx.Response(
                response.status,
                headers=response.headers,
                stream=_ResponseStream(response, gate, self._backend),
                extensions=response.extensions,
            )
        except BaseException:
            try:
                gate.finish(False)
            finally:
                self._backend.abort()
                if response is not None:
                    await response.aclose()
            raise

    async def aclose(self):
        try:
            self._gate.finish(False)
        finally:
            self._backend.abort()
            await self._pool.aclose()


def _create_transport_v2(gate, *, key):
    from app.exchange.okx import native_demo_write_v2 as authority

    if key is authority._LEVERAGE_CONFIGURATION_KEY_V1:
        record = authority._require_physical_gate_v2(gate, key=_TRANSPORT_KEY)
        _need(
            record.get("configuration_purpose") is key,
            "native_demo_transport_configuration_key_required",
        )
    else:
        _need(key is authority._INTENT_KEY, "native_demo_transport_source_key_required")
    return _Transport(_gate(gate), key=_TRANSPORT_KEY)
