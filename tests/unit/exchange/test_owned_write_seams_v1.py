"""Real localhost TLS and explicit await cuts; no qualification or exchange IO."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import os
import re
import shutil
import ssl
import subprocess
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import anyio
import httpcore
import httpx
import pytest
from anyio._backends._asyncio import AsyncIOBackend

from app.exchange.okx import owned_write_seams_v1 as seams
from app.trade_qualification.dispatch_ownership import DispatchOwnership, DispatchSlot

WINDOWS_OPENSSL = Path("C:/Program Files/Git/mingw64/bin/openssl.exe")
WINDOWS_OPENSSL_SHA = "822034da8a01558c17cbe53f42f33985a6eaf7c89e21273779f9c6560d8c4d78"


@pytest.fixture(scope="module")
def tls_contexts(tmp_path_factory, record_testsuite_property):
    """An ephemeral test key/cert; no custom crypto or system certificate store."""
    if os.name == "nt":
        binary = WINDOWS_OPENSSL
    else:
        located = shutil.which("openssl")
        if located is None:
            pytest.skip("native loopback TLS profile incomplete: openssl CLI absent")
        binary = Path(located).resolve()
    temporary = tmp_path_factory.mktemp("owned-local-tls")
    key, cert = temporary / "ephemeral-test-only.key", temporary / "cert.pem"
    environment = {
        name: os.environ[name]
        for name in ("SystemRoot", "WINDIR")
        if name in os.environ
    }
    environment.update(PATH=str(binary.parent), TEMP=str(temporary), TMP=str(temporary))
    assert key.resolve(strict=False).parent == temporary.resolve(strict=True)
    try:
        version = subprocess.run(
            [str(binary), "version"],
            cwd=binary.parent,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=3,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        assert version.returncode == 0 and 0 < len(version.stdout) <= 512
        safe_version = version.stdout.decode("ascii").strip()
        assert re.fullmatch(
            r"OpenSSL [3-9]\.[0-9]+\.[0-9]+ [A-Za-z0-9 ]+(?: \(Library: OpenSSL [A-Za-z0-9. ]+\))?",
            safe_version,
        )
        tool_sha = hashlib.sha256(binary.read_bytes()).hexdigest()
        record_testsuite_property("localhost_certificate_tool_sha256", tool_sha)
        record_testsuite_property("localhost_certificate_tool_version", safe_version)
        record_testsuite_property(
            "reviewed_local_Windows_tool_profile",
            os.name == "nt" and tool_sha == WINDOWS_OPENSSL_SHA,
        )
        result = subprocess.run(
            [
                str(binary),
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-keyout",
                str(key),
                "-out",
                str(cert),
                "-sha256",
                "-days",
                "1",
                "-subj",
                "/CN=CTCC Localhost Test Only",
                "-addext",
                "subjectAltName=IP:127.0.0.1",
            ],
            cwd=binary.parent,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        assert result.returncode == 0, "ephemeral_local_certificate_generation_failed"
        server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        server.load_cert_chain(cert, key)
        server.set_alpn_protocols(["http/1.1"])
        public_ca = cert.read_bytes()
    finally:
        # Only this fixture's fixed key leaf; never recursive or another path.
        assert key.resolve(strict=False).parent == temporary.resolve(strict=True)
        assert not key.is_symlink()
        key.unlink(missing_ok=True)
    record_testsuite_property("temporary_localhost_key_removed", not key.exists())
    return server, public_ca


@asynccontextmanager
async def local_server(
    context, *, ambiguous=False, plaintext_stall=False, stall_response=False
):
    requests, handlers = [], set()
    received = asyncio.Event()

    async def handle(reader, writer):
        task = asyncio.current_task()
        handlers.add(task)
        try:
            if plaintext_stall:
                await reader.read(4096)
                received.set()
                await asyncio.Future()
            header = await reader.readuntil(b"\r\n\r\n")
            length = next(
                int(line.split(b":", 1)[1])
                for line in header.split(b"\r\n")
                if line.lower().startswith(b"content-length:")
            )
            assert length == len(seams._BODY)
            body = await reader.readexactly(length)
            requests.append((header, body))
            received.set()
            if stall_response:
                await asyncio.Future()
            if ambiguous:
                writer.transport.abort()
            else:
                writer.write(
                    b"HTTP/1.1 200 OK\r\nContent-Length: 8\r\nConnection: close\r\n\r\naccepted"
                )
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError, ssl.SSLError):
            pass
        finally:
            writer.transport.abort()
            try:
                await asyncio.wait_for(writer.wait_closed(), 0.5)
            except (ConnectionError, ssl.SSLError, TimeoutError):
                pass
            handlers.discard(task)

    server = await asyncio.start_server(
        handle, "127.0.0.1", 0, ssl=None if plaintext_stall else context, limit=8192
    )
    state = SimpleNamespace(
        port=server.sockets[0].getsockname()[1], requests=requests, received=received
    )
    try:
        yield state
    finally:
        server.close()
        pending = tuple(handlers)
        for task in pending:
            task.cancel()
        await asyncio.wait_for(asyncio.gather(*pending, return_exceptions=True), 2)
        await asyncio.wait_for(server.wait_closed(), 2)


@pytest.mark.asyncio
async def test_real_pinned_stack_tls_positive_and_duplicate_no_resubmit(tls_contexts):
    server_context, context = tls_contexts
    async with local_server(server_context) as server:
        probe = seams.LoopbackOwnedWriteProbe(port=server.port, trusted_ca_pem=context)
        response = await probe.send()
        assert response.status_code == 200 and response.content == b"accepted"
        assert [body for _, body in server.requests] == [seams._BODY]
        observed = probe.observation
        assert observed.state == "completed" and observed.attempted is True
        assert observed.ssl_write_calls == 2 and observed.raw_write_calls >= 2
        assert observed.plaintext_accepted > len(seams._BODY)
        assert (
            observed.execution_authority is False
            and observed.qualification_passed is False
        )
        with pytest.raises(seams.OwnedWriteSeamError, match="owned_seam_duplicate"):
            await probe.send()
        assert len(server.requests) == 1
        assert probe.observation.state == "completed"


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["revoke", "expire"])
async def test_native_pool_wait_then_revocation_emits_no_order_bytes(
    tls_contexts, monkeypatch, outcome
):
    server_context, context = tls_contexts
    captured = []
    original = seams._Transport.__init__

    def init(transport, gate, context):
        original(transport, gate, context)
        transport._pool._max_connections = 0
        captured.append(transport)

    monkeypatch.setattr(seams._Transport, "__init__", init)
    async with local_server(server_context) as server:
        probe = seams.LoopbackOwnedWriteProbe(port=server.port, trusted_ca_pem=context)
        task = asyncio.create_task(probe.send())
        async with asyncio.timeout(2):
            while not captured or not captured[0]._pool._requests:
                await asyncio.sleep(0)
        pool = captured[0]._pool
        assert pool._requests[0].connection is None
        if outcome == "revoke":
            probe.revoke_now()
        else:
            probe._gate._deadline = 0  # Synthetic deadline cut, not a clock proof.
        pool._max_connections = 1
        assert pool._assign_requests_to_connections() == []
        with pytest.raises(seams.OwnedWriteSeamError):
            await task
        assert (
            probe.observation.ssl_write_calls == probe.observation.raw_write_calls == 0
        )
        assert server.requests == []


@pytest.mark.asyncio
async def test_revoke_after_actual_tls_handshake_before_A(tls_contexts, monkeypatch):
    server_context, context = tls_contexts
    entered, release = asyncio.Event(), asyncio.Event()
    original = seams._ConnectionStream.start_tls

    async def cut(stream, *args, **kwargs):
        connected = await original(stream, *args, **kwargs)
        entered.set()
        await release.wait()
        return connected

    monkeypatch.setattr(seams._ConnectionStream, "start_tls", cut)
    async with local_server(server_context) as server:
        probe = seams.LoopbackOwnedWriteProbe(port=server.port, trusted_ca_pem=context)
        task = asyncio.create_task(probe.send())
        await asyncio.wait_for(entered.wait(), 2)
        probe.revoke_now()
        release.set()
        with pytest.raises(seams.OwnedWriteSeamError):
            await task
        assert probe.observation.ssl_write_calls == 0 and server.requests == []
        with pytest.raises(seams.OwnedWriteSeamError):
            await probe.send()


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["revoke", "cancel"])
async def test_native_raw_checkpoint_after_A_before_B(
    tls_contexts, monkeypatch, action
):
    server_context, context = tls_contexts
    entered, release = asyncio.Event(), asyncio.Event()
    native_checkpoint = AsyncIOBackend.checkpoint
    selected = probe = None

    async def checkpoint(cls):
        await native_checkpoint()
        if (
            asyncio.current_task() is selected
            and probe.observation.attempted
            and not entered.is_set()
        ):
            entered.set()
            await release.wait()

    monkeypatch.setattr(AsyncIOBackend, "checkpoint", classmethod(checkpoint))
    async with local_server(server_context) as server:
        probe = seams.LoopbackOwnedWriteProbe(port=server.port, trusted_ca_pem=context)
        selected = asyncio.create_task(probe.send())
        await asyncio.wait_for(entered.wait(), 2)
        assert probe.observation.ssl_write_calls == 1
        assert (
            probe.observation.plaintext_accepted > 0
            and probe.observation.raw_write_calls == 0
        )
        if action == "revoke":
            probe.revoke_now()
        else:
            selected.cancel()
        release.set()
        with pytest.raises(
            seams.OwnedWriteSeamError if action == "revoke" else asyncio.CancelledError
        ):
            await selected
        assert probe.observation.attempted is True
        assert probe.observation.state == "unknown"
        assert probe.observation.raw_write_calls == 0 and server.requests == []
        with pytest.raises(seams.OwnedWriteSeamError):
            await probe.send()


@pytest.mark.asyncio
async def test_cancel_during_actual_tls_handshake(tls_contexts):
    _, context = tls_contexts
    async with local_server(None, plaintext_stall=True) as server:
        probe = seams.LoopbackOwnedWriteProbe(port=server.port, trusted_ca_pem=context)
        task = asyncio.create_task(probe.send())
        await asyncio.wait_for(server.received.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 2)
        assert probe.observation.attempted is False
        assert probe.observation.ssl_write_calls == 0 and server.requests == []


@pytest.mark.asyncio
async def test_actual_order_bytes_then_ambiguous_response_no_second_submission(
    tls_contexts,
):
    server_context, context = tls_contexts
    async with local_server(server_context, ambiguous=True) as server:
        probe = seams.LoopbackOwnedWriteProbe(port=server.port, trusted_ca_pem=context)
        with pytest.raises((httpcore.RemoteProtocolError, httpcore.ReadError)):
            await probe.send()
        assert (
            probe.observation.state == "unknown" and probe.observation.attempted is True
        )
        assert len(server.requests) == 1
        with pytest.raises(seams.OwnedWriteSeamError):
            await probe.send()
        assert len(server.requests) == 1


@pytest.mark.asyncio
async def test_cancel_after_B_actual_application_bytes_retains_unknown(tls_contexts):
    server_context, context = tls_contexts
    async with local_server(server_context, stall_response=True) as server:
        probe = seams.LoopbackOwnedWriteProbe(port=server.port, trusted_ca_pem=context)
        task = asyncio.create_task(probe.send())
        await asyncio.wait_for(server.received.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 2)
        assert probe.observation.state == "unknown"
        assert probe.observation.raw_write_calls >= 2
        assert len(server.requests) == 1
        with pytest.raises(seams.OwnedWriteSeamError):
            await probe.send()
        assert len(server.requests) == 1


@pytest.mark.asyncio
async def test_anyio_cancelled_scope_is_not_a_new_write_permit(tls_contexts):
    server_context, context = tls_contexts
    async with local_server(server_context) as server:
        probe = seams.LoopbackOwnedWriteProbe(port=server.port, trusted_ca_pem=context)
        with anyio.CancelScope() as scope:
            scope.cancel()
            with pytest.raises(asyncio.CancelledError):
                await probe.send()
        assert probe.observation.ssl_write_calls == 0 and server.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("want", ["read", "write"])
async def test_tls_want_io_retry_rechecks_A_not_only_outer_send(want):
    """Pinned native TLS retry loop with an explicitly synthetic SSL IO cut."""
    gate = seams._Gate(seams._LOOPBACK_KEY, 34567, 3)
    request = httpx.Request(
        "POST",
        "https://127.0.0.1:34567" + seams._PATH.decode(),
        headers={"content-type": "application/json", "connection": "close"},
        content=seams._BODY,
    )
    gate.begin(request)
    gate.tls_ready()

    class SSL:
        calls = 0

        def write(self, data):
            self.calls += 1
            raise ssl.SSLWantReadError() if want == "read" else ssl.SSLWantWriteError()

    class Raw:
        async def receive(self):
            await asyncio.sleep(0)
            gate.revoke_now()
            return b"synthetic TLS retry cut"

        async def send(self, item):
            await asyncio.sleep(0)
            gate.revoke_now()

    native_ssl = SSL()
    native = seams.TLSStream(Raw(), False, native_ssl, ssl.MemoryBIO(), ssl.MemoryBIO())
    with pytest.raises(seams.OwnedWriteSeamError, match="native_tls_required"):
        seams._TLSBytes(native, gate)
    with pytest.raises(seams.OwnedWriteSeamError):
        await seams._TLS_CALL(
            native, lambda data: gate.ssl_write(native_ssl, data), gate._wire
        )
    assert native_ssl.calls == 1 and gate.snapshot().attempted is True


@pytest.mark.asyncio
async def test_real_monotonic_expiry_after_tls_before_A(tls_contexts, monkeypatch):
    server_context, context = tls_contexts
    original = seams._ConnectionStream.start_tls

    async def delay(stream, *args, **kwargs):
        connected = await original(stream, *args, **kwargs)
        await asyncio.sleep(0.08)
        return connected

    monkeypatch.setattr(seams._ConnectionStream, "start_tls", delay)
    async with local_server(server_context) as server:
        probe = seams.LoopbackOwnedWriteProbe(
            port=server.port, trusted_ca_pem=context, deadline_seconds=0.05
        )
        with pytest.raises(seams.OwnedWriteSeamError, match="expired"):
            await probe.send()
        assert probe.observation.attempted is False and server.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change", ["body", "host", "userinfo", "fragment", "headers", "trace"]
)
async def test_changed_probe_is_denied_before_native_connect(change):
    gate = seams._Gate(seams._LOOPBACK_KEY, 34567, 3)
    url = "https://127.0.0.1:34567" + seams._PATH.decode()
    headers = {"content-type": "application/json", "connection": "close"}
    if change == "host":
        url = url.replace("127.0.0.1", "example.invalid")
    elif change == "userinfo":
        url = url.replace("https://", "https://synthetic-user:synthetic-password@")
    elif change == "fragment":
        url += "#unbound"
    elif change == "headers":
        headers["OK-ACCESS-KEY"] = "synthetic-rejected"
    request = httpx.Request(
        "POST",
        url,
        headers=headers,
        content=b"changed" if change == "body" else seams._BODY,
    )
    if change == "trace":
        request.extensions["trace"] = lambda *_: True
    with pytest.raises(seams.OwnedWriteSeamError):
        gate.begin(request)
    assert gate.snapshot().attempted is False
    assert gate.snapshot().ssl_write_calls == gate.snapshot().raw_write_calls == 0


@pytest.mark.asyncio
async def test_mutation_after_claim_is_not_repaired_or_retried():
    gate = seams._Gate(seams._LOOPBACK_KEY, 34567, 3)
    request = httpx.Request(
        "POST",
        "https://127.0.0.1:34567" + seams._PATH.decode(),
        headers={"content-type": "application/json", "connection": "close"},
        content=seams._BODY,
    )
    gate.begin(request)
    request.headers["connection"] = "keep-alive"
    with pytest.raises(seams.OwnedWriteSeamError, match="prepared_request_changed"):
        gate.take_transport(request)
    request.headers["connection"] = "close"
    with pytest.raises(seams.OwnedWriteSeamError, match="duplicate_transport"):
        gate.take_transport(request)
    assert gate.snapshot().ssl_write_calls == gate.snapshot().raw_write_calls == 0


@pytest.mark.asyncio
async def test_two_workers_same_probe_cannot_send_twice(tls_contexts, monkeypatch):
    server_context, context = tls_contexts
    entered, release = asyncio.Event(), asyncio.Event()
    original = seams._ConnectionStream.start_tls

    async def delay(stream, *args, **kwargs):
        native = await original(stream, *args, **kwargs)
        entered.set()
        await release.wait()
        return native

    monkeypatch.setattr(seams._ConnectionStream, "start_tls", delay)
    async with local_server(server_context) as server:
        probe = seams.LoopbackOwnedWriteProbe(port=server.port, trusted_ca_pem=context)
        first = asyncio.create_task(probe.send())
        await asyncio.wait_for(entered.wait(), 2)
        with pytest.raises(seams.OwnedWriteSeamError, match="duplicate"):
            await probe.send()
        release.set()
        assert (await first).status_code == 200
        assert len(server.requests) == 1


def test_production_issuer_remains_DENY_even_truthy_replacement(monkeypatch):
    with pytest.raises(seams.OwnedWriteSeamError):
        seams.require_qualified_order_transport_v1({"passed": True}, {"ready": True})
    owner = object.__new__(DispatchOwnership)
    slot = object.__new__(DispatchSlot)
    monkeypatch.setattr(
        seams, "require_owned_private_write_authority", lambda *_a, **_k: True
    )
    with pytest.raises(
        seams.OwnedWriteSeamError, match="owned_order_source_authority_unavailable"
    ):
        seams.require_qualified_order_transport_v1(owner, slot)


def test_nontransferable_no_caller_constructor_or_unverified_tls():
    with pytest.raises(seams.OwnedWriteSeamError):
        seams._Gate(True, 34567, 3)
    gate = seams._Gate(seams._LOOPBACK_KEY, 34567, 3)
    for copier in (copy.copy, copy.deepcopy):
        with pytest.raises(TypeError, match="not_transferable"):
            copier(gate)
    with pytest.raises(TypeError):
        seams.LoopbackOwnedWriteProbe(
            port=34567, ssl_context=ssl._create_unverified_context()
        )


def test_caller_context_and_mutable_ca_cannot_cross_native_await(tls_contexts):
    _, ca = tls_contexts
    caller = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    caller.load_verify_locations(cadata=ca.decode())
    with pytest.raises(TypeError):
        seams.LoopbackOwnedWriteProbe(port=34567, ssl_context=caller)
    with pytest.raises(seams.OwnedWriteSeamError, match="public_ca_required"):
        seams.LoopbackOwnedWriteProbe(port=34567, trusted_ca_pem=bytearray(ca))
    probe = seams.LoopbackOwnedWriteProbe(port=34567, trusted_ca_pem=ca)
    caller.check_hostname = False
    caller.verify_mode = ssl.CERT_NONE
    caller._msg_callback = lambda *_: None
    assert probe._context is not caller
    assert (
        probe._context.check_hostname
        and probe._context.verify_mode == ssl.CERT_REQUIRED
    )
    assert (
        probe._context.keylog_filename is None and probe._context._msg_callback is None
    )


def test_exact_native_carrier_rejects_instance_write_hook():
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    native_ssl = context.wrap_bio(
        ssl.MemoryBIO(), ssl.MemoryBIO(), server_hostname="127.0.0.1"
    )
    native_ssl.write = lambda _: 1
    native_tls = seams.TLSStream(
        None, False, native_ssl, ssl.MemoryBIO(), ssl.MemoryBIO()
    )
    with pytest.raises(seams.OwnedWriteSeamError, match="native_method_shadowed"):
        seams._TLSBytes(native_tls, seams._Gate(seams._LOOPBACK_KEY, 34567, 3))


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["hostname", "port", "unix", "retry"])
async def test_backend_has_no_alternate_connection_or_retry_route(target):
    gate = seams._Gate(seams._LOOPBACK_KEY, 34567, 3)
    backend = seams._Backend(gate)
    with pytest.raises(seams.OwnedWriteSeamError):
        if target == "hostname":
            await backend.connect_tcp("example.invalid", 34567)
        elif target == "port":
            await backend.connect_tcp("127.0.0.1", 443)
        elif target == "unix":
            await backend.connect_unix_socket("synthetic-rejected")
        else:
            await backend.sleep(0)
