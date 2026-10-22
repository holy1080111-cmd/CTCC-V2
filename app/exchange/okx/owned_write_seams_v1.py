"""Owned A/B native write seams; exchange authority remains unavailable.

The positive diagnostic is restricted to one credential-free localhost probe.
It exercises the actual pinned HTTPX/httpcore/AnyIO/TLS stack, not qualification.
No API in this module issues a Demo or Live transport permit.
"""

from __future__ import annotations

import asyncio
import importlib.metadata
import os
import ssl
import sys
import threading
import time
from dataclasses import dataclass, field

import anyio
import h11
import httpcore
import httpx
from anyio._backends._asyncio import SocketStream
from anyio.streams.tls import TLSStream
from httpcore._backends.anyio import AnyIOBackend, AnyIOStream

from app.trade_qualification.dispatch_ownership import DispatchOwnership, DispatchSlot
from app.trade_qualification.private_write_authority import (
    require_owned_private_write_authority,
)

_BODY = b'{"kind":"ctcc.loopback_owned_write_probe.v1"}'
_PATH = b"/ctcc-owned-write-seam-probe"
_VERSIONS = {"httpx": "0.28.1", "httpcore": "1.0.9", "anyio": "4.15.1", "h11": "0.16.0"}
_TLS_CALL = TLSStream._call_sslobject_method
_SOCKET_SEND = SocketStream.send
_SSL_WRITE = ssl.SSLObject.write
_LOOPBACK_KEY = object()


class OwnedWriteSeamError(ValueError):
    """Fixed public code; never contains request, TLS, or credential values."""


def _require(value, code):
    if not value:
        raise OwnedWriteSeamError(code)


def _no_method_shadows(instance, names):
    _require(
        not set(getattr(instance, "__dict__", {})).intersection(names),
        "owned_seam_native_method_shadowed",
    )


def require_qualified_order_transport_v1(owner, slot):
    """Production boundary: even a truthy replacement cannot grant a permit."""
    _require(
        type(owner) is DispatchOwnership and type(slot) is DispatchSlot,
        "owned_order_source_authority_unavailable",
    )
    require_owned_private_write_authority(
        "POST", "/api/v5/trade/order", owner=owner, slot=slot
    )
    raise OwnedWriteSeamError("owned_order_source_authority_unavailable")


@dataclass(frozen=True, slots=True)
class LoopbackWriteObservation:
    state: str
    attempted: bool
    ssl_write_calls: int
    raw_write_calls: int
    plaintext_accepted: int
    execution_authority: bool = field(default=False, init=False)
    qualification_passed: bool = field(default=False, init=False)


class _Gate:
    __slots__ = (
        "_accepted",
        "_attempted",
        "_claimed",
        "_deadline",
        "_generation",
        "_lock",
        "_loop",
        "_pid",
        "_port",
        "_raw_calls",
        "_request_generation",
        "_ssl_calls",
        "_state",
        "_task",
        "_thread",
        "_tls_ready",
        "_transport_taken",
        "_wire",
    )

    def __init__(self, key, port, deadline_seconds):
        _require(key is _LOOPBACK_KEY, "owned_seam_constructor_denied")
        _require(type(port) is int and 1024 <= port <= 65535, "owned_seam_port_invalid")
        _require(
            type(deadline_seconds) in (int, float) and 0 < deadline_seconds <= 10,
            "owned_seam_deadline_invalid",
        )
        self._lock = threading.RLock()
        self._pid, self._thread = os.getpid(), threading.get_ident()
        self._task = self._loop = None
        self._deadline = time.monotonic() + deadline_seconds
        self._state, self._attempted = "new", False
        self._ssl_calls = self._raw_calls = self._accepted = 0
        self._wire, self._port = b"", port
        self._tls_ready = self._claimed = False
        self._transport_taken = False
        self._generation = self._request_generation = 0

    def __reduce__(self):
        raise TypeError("owned_seam_not_transferable")

    def __copy__(self):
        raise TypeError("owned_seam_not_transferable")

    def __deepcopy__(self, memo):
        raise TypeError("owned_seam_not_transferable")

    def revoke_now(self):
        with self._lock:
            self._generation += 1
            self._state = "revoked"

    def snapshot(self):
        with self._lock:
            return LoopbackWriteObservation(
                self._state,
                self._attempted,
                self._ssl_calls,
                self._raw_calls,
                self._accepted,
            )

    def _current(self):
        _require(
            self._pid == os.getpid()
            and self._thread == threading.get_ident()
            and self._task is asyncio.current_task()
            and self._loop is asyncio.get_running_loop(),
            "owned_seam_context_changed",
        )
        _require(
            self._state in ("prepared", "attempted")
            and self._request_generation == self._generation,
            "owned_seam_revoked_or_terminal",
        )
        task = self._task
        if task.cancelling() or anyio.current_effective_deadline() <= time.monotonic():
            self._state = "cancelled"
            raise asyncio.CancelledError
        if time.monotonic() >= self._deadline:
            self._state = "expired"
            raise OwnedWriteSeamError("owned_seam_expired")

    def begin(self, request):
        with self._lock:
            _require(self._state == "new" and not self._claimed, "owned_seam_duplicate")
            self._claimed = True
            self._task, self._loop = asyncio.current_task(), asyncio.get_running_loop()
            self._state = "prepared"
            try:
                self._current()
                _require(
                    type(request) is httpx.Request
                    and request.method == "POST"
                    and request.url.scheme == "https"
                    and request.url.host == "127.0.0.1"
                    and request.url.port == self._port
                    and request.url.raw_path == _PATH
                    and str(request.url)
                    == f"https://127.0.0.1:{self._port}" + _PATH.decode()
                    and type(request.stream) is httpx.ByteStream
                    and request.content == _BODY
                    and not request.extensions.keys() - {"timeout"},
                    "owned_seam_exact_probe_required",
                )
                allowed = {b"host", b"content-length", b"content-type", b"connection"}
                _require(
                    len(request.headers.raw) == 4
                    and {key.lower() for key, _ in request.headers.raw} == allowed
                    and request.headers["connection"] == "close"
                    and request.headers["host"] == f"127.0.0.1:{self._port}"
                    and request.headers["content-type"] == "application/json"
                    and request.headers["content-length"] == str(len(_BODY)),
                    "owned_seam_headers_rejected",
                )
                encoder = h11.Connection(h11.CLIENT)
                self._wire = (
                    encoder.send(
                        h11.Request(
                            method=b"POST", target=_PATH, headers=request.headers.raw
                        )
                    )
                    + encoder.send(h11.Data(data=_BODY))
                    + encoder.send(h11.EndOfMessage())
                )
            except BaseException:
                self._state = "denied"
                raise

    def take_transport(self, request):
        with self._lock:
            self._current()
            _require(not self._transport_taken, "owned_seam_duplicate_transport")
            self._transport_taken = True
            _require(
                type(request) is httpx.Request
                and type(request.stream) is httpx.ByteStream,
                "owned_seam_prepared_request_changed",
            )
            encoder = h11.Connection(h11.CLIENT)
            wire = (
                encoder.send(
                    h11.Request(
                        method=request.method.encode(),
                        target=request.url.raw_path,
                        headers=request.headers.raw,
                    )
                )
                + encoder.send(h11.Data(data=request.content))
                + encoder.send(h11.EndOfMessage())
            )
            _require(
                type(request) is httpx.Request
                and type(request.stream) is httpx.ByteStream
                and str(request.url)
                == f"https://127.0.0.1:{self._port}" + _PATH.decode()
                and wire == self._wire
                and not request.extensions.keys() - {"timeout"},
                "owned_seam_prepared_request_changed",
            )

    def tls_ready(self):
        with self._lock:
            self._current()
            _require(not self._tls_ready, "owned_seam_duplicate_tls")
            self._tls_ready = True

    def ssl_write(self, native_ssl, data):
        """A: called for every native SSL write, including TLS retry after await."""
        with self._lock:
            self._current()
            _require(
                self._tls_ready and type(data) is bytes and data,
                "owned_seam_tls_not_ready",
            )
            _require(
                self._wire[self._accepted : self._accepted + len(data)] == data,
                "owned_seam_wire_changed",
            )
            self._attempted, self._state = True, "attempted"
            self._ssl_calls += 1
            # The claim is terminal even if SSL raises/wants IO. A later call is
            # the same invocation's checked continuation, never a new permit.
            accepted = native_ssl.write(data)
            _require(
                type(accepted) is int and 0 < accepted <= len(data),
                "owned_seam_ssl_result_unknown",
            )
            self._accepted += accepted
            return accepted

    def raw_write(self, native_transport, data):
        """B: invoked by native SocketStream.send after its cancellation checkpoint."""
        with self._lock:
            # Handshake bytes contain no HTTP request. The native TLS adapter
            # cannot enter application write before tls_ready returns.
            self._current()
            if self._tls_ready:
                _require(self._attempted, "owned_seam_raw_without_claim")
                self._raw_calls += 1
            return native_transport.write(data)

    def finish(self, success):
        with self._lock:
            if self._state == "completed":
                return
            if success:
                _require(
                    self._accepted == len(self._wire) and self._raw_calls > 0,
                    "owned_seam_complete_write_unproven",
                )
                self._state = "completed"
            elif self._attempted:
                self._state = "unknown"
            elif self._state == "prepared":
                self._state = "denied"


class _RawTransport:
    __slots__ = ("_gate", "_native")

    def __init__(self, native, gate):
        from asyncio.proactor_events import _ProactorSocketTransport
        from asyncio.selector_events import _SelectorSocketTransport

        _require(
            type(native) in (_ProactorSocketTransport, _SelectorSocketTransport),
            "owned_seam_native_transport_required",
        )
        _no_method_shadows(native, {"write", "writelines", "abort", "close"})
        self._native, self._gate = native, gate

    def write(self, data):
        return self._gate.raw_write(self._native, data)

    def __getattr__(self, name):
        return getattr(self._native, name)


class _TLSBytes(anyio.abc.ByteStream):
    __slots__ = ("_gate", "_native")

    def __init__(self, native, gate):
        _require(
            type(native) is TLSStream and type(native._ssl_object) is ssl.SSLObject,
            "owned_seam_native_tls_required",
        )
        _no_method_shadows(
            native, {"send", "receive", "aclose", "_call_sslobject_method"}
        )
        _no_method_shadows(native._ssl_object, {"write", "read"})
        _require(ssl.SSLObject.write is _SSL_WRITE, "owned_seam_native_ssl_changed")
        self._native, self._gate = native, gate

    async def send(self, item):
        # Reuse native TLS WantRead/WantWrite/error/MemoryBIO mechanics.
        def write(data):
            return self._gate.ssl_write(self._native._ssl_object, data)

        await _TLS_CALL(self._native, write, item)

    async def receive(self, max_bytes=65536):
        return await self._native.receive(max_bytes)

    async def send_eof(self):
        raise OwnedWriteSeamError("owned_seam_eof_denied")

    async def aclose(self):
        # Abort instead of a TLS close-handshake/flush after a rejected write.
        self._native.transport_stream._transport.abort()

    @property
    def extra_attributes(self):
        return self._native.extra_attributes


class _ConnectionStream(httpcore.AsyncNetworkStream):
    __slots__ = ("_gate", "_native", "_started")

    def __init__(self, native, gate):
        _require(
            type(native) is AnyIOStream and type(native._stream) is SocketStream,
            "owned_seam_native_stream_required",
        )
        _no_method_shadows(native, {"read", "write", "start_tls", "aclose"})
        _no_method_shadows(native._stream, {"send", "receive", "aclose"})
        self._native, self._gate, self._started = native, gate, False
        raw = native._stream
        _require(type(raw).send is _SOCKET_SEND, "owned_seam_native_send_changed")
        raw._transport = _RawTransport(raw._transport, gate)

    async def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        _require(
            not self._started
            and server_hostname == "127.0.0.1"
            and type(ssl_context) is ssl.SSLContext
            and ssl_context.check_hostname
            and ssl_context.verify_mode == ssl.CERT_REQUIRED
            and ssl_context.keylog_filename is None
            and getattr(ssl_context, "_msg_callback", None) is None,
            "owned_seam_tls_target_rejected",
        )
        self._started = True
        try:
            native = await self._native.start_tls(ssl_context, server_hostname, timeout)
            _require(
                type(native) is AnyIOStream and type(native._stream) is TLSStream,
                "owned_seam_native_tls_required",
            )
            _require(
                native._stream._ssl_object.selected_alpn_protocol()
                in (None, "http/1.1"),
                "owned_seam_alpn_rejected",
            )
            self._gate.tls_ready()
            return AnyIOStream(_TLSBytes(native._stream, self._gate))
        except BaseException:
            # httpcore's AnyIOStream.start_tls only catches Exception. Cancellation is
            # BaseException; abort this owned raw connection even in that path.
            self._native._stream._transport.abort()
            raise

    async def read(self, max_bytes, timeout=None):
        raise OwnedWriteSeamError("owned_seam_cleartext_denied")

    async def write(self, buffer, timeout=None):
        raise OwnedWriteSeamError("owned_seam_cleartext_denied")

    async def aclose(self):
        self._native._stream._transport.abort()

    def get_extra_info(self, info):
        return self._native.get_extra_info(info)


class _Backend(httpcore.AsyncNetworkBackend):
    __slots__ = ("_connected", "_gate")

    def __init__(self, gate):
        self._gate, self._connected = gate, False

    async def connect_tcp(
        self, host, port, timeout=None, local_address=None, socket_options=None
    ):
        _require(
            not self._connected
            and host == "127.0.0.1"
            and port == self._gate._port
            and local_address is None,
            "owned_seam_connect_rejected",
        )
        self._connected = True
        self._gate._current()
        native = await AnyIOBackend().connect_tcp(
            host, port, timeout, local_address, socket_options
        )
        try:
            self._gate._current()
            return _ConnectionStream(native, self._gate)
        except BaseException:
            await native.aclose()
            raise

    async def connect_unix_socket(self, *args, **kwargs):
        raise OwnedWriteSeamError("owned_seam_unix_denied")

    async def sleep(self, seconds):
        raise OwnedWriteSeamError("owned_seam_retry_denied")


class _Transport(httpx.AsyncBaseTransport):
    def __init__(self, gate, context):
        self._gate = gate
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=context,
            network_backend=_Backend(gate),
            retries=0,
            http1=True,
            http2=False,
            max_connections=1,
            max_keepalive_connections=0,
        )

    async def handle_async_request(self, request):
        self._gate.take_transport(request)
        core = httpcore.Request(
            method=request.method,
            url=httpcore.URL(
                scheme=request.url.raw_scheme,
                host=request.url.raw_host,
                port=request.url.port,
                target=request.url.raw_path,
            ),
            headers=tuple(request.headers.raw),
            content=_BODY,
            extensions=dict(request.extensions),
        )
        success = False
        try:
            response = await self._pool.handle_async_request(core)
            body = bytearray()
            try:
                async for chunk in response.aiter_stream():
                    _require(
                        len(body) + len(chunk) <= 1024, "owned_seam_response_too_large"
                    )
                    body.extend(chunk)
            finally:
                await response.aclose()
            success = True
            return httpx.Response(
                response.status, headers=response.headers, content=bytes(body)
            )
        finally:
            self._gate.finish(success)

    async def aclose(self):
        await self._pool.aclose()


class LoopbackOwnedWriteProbe:
    """A synthetic localhost transport exercise; cannot target an exchange."""

    __slots__ = ("_context", "_gate")

    def __init__(self, *, port, trusted_ca_pem, deadline_seconds=3):
        _require(
            sys.implementation.name == "cpython"
            and sys.version_info[:2] == (3, 12)
            and all(importlib.metadata.version(k) == v for k, v in _VERSIONS.items()),
            "owned_seam_dependency_profile_rejected",
        )
        _require(
            type(trusted_ca_pem) is bytes
            and 0 < len(trusted_ca_pem) <= 65536
            and trusted_ca_pem.startswith(b"-----BEGIN CERTIFICATE-----")
            and b"PRIVATE KEY" not in trusted_ca_pem,
            "owned_seam_public_ca_required",
        )
        # No caller-owned mutable context survives an await. No environment CA,
        # keylog callback, caller hook, or certificate-store mutation is used.
        ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ssl_context.minimum_version = ssl.TLSVersion.TLSv1_2
        ssl_context.set_alpn_protocols(["http/1.1"])
        try:
            ssl_context.load_verify_locations(cadata=trusted_ca_pem.decode("ascii"))
        except (UnicodeError, ssl.SSLError):
            raise OwnedWriteSeamError("owned_seam_public_ca_invalid") from None
        self._gate, self._context = (
            _Gate(_LOOPBACK_KEY, port, deadline_seconds),
            ssl_context,
        )

    def revoke_now(self):
        self._gate.revoke_now()

    @property
    def observation(self):
        return self._gate.snapshot()

    async def send(self):
        request = httpx.Request(
            "POST",
            f"https://127.0.0.1:{self._gate._port}" + _PATH.decode(),
            headers={"content-type": "application/json", "connection": "close"},
            content=_BODY,
            extensions={
                "timeout": {name: 2.0 for name in ("connect", "read", "write", "pool")}
            },
        )
        # Claim before the first await; cancellation cannot recycle an invocation
        # which already entered this method, even before HTTPX reaches transport.
        self._gate.begin(request)
        transport = _Transport(self._gate, self._context)
        try:
            async with httpx.AsyncClient(
                transport=transport, trust_env=False, follow_redirects=False
            ) as client:
                return await client.send(request, auth=None, follow_redirects=False)
        except BaseException:
            self._gate.finish(False)
            raise
