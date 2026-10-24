"""Controlled loopback TLS with synthetic account responses, never OKX acceptance.

The unchanged account collector owns the HTTP client, verifies real TLS bytes and
issues its own private capture. No saved journal/provenance/packet is rewritten.
Only this fixture's exact openapi connection is redirected to its local server.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import os
import re
import shutil
import ssl
import subprocess
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from httpcore._backends.auto import AutoBackend

from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_collector as collector
from tests.unit.test_qualification_account_collector import credentials, wire

# Existing reviewed test-owned OpenSSL profile, copied without changing tools.
WINDOWS_OPENSSL_PATHS = (
    Path("C:/Program Files/Git/mingw64/bin/openssl.exe"),
    Path("C:/Program Files/Git/usr/bin/openssl.exe"),
)
WINDOWS_OPENSSL_SHA = "822034da8a01558c17cbe53f42f33985a6eaf7c89e21273779f9c6560d8c4d78"


@pytest.fixture(scope="module")
def shared_history_original_tls_contexts(tmp_path_factory, record_testsuite_property):
    """An ephemeral test key/cert; no custom crypto or system certificate store."""
    if os.name == "nt":
        binary = next((path for path in WINDOWS_OPENSSL_PATHS if path.is_file()), None)
        assert binary is not None, "native_loopback_git_openssl_required"
        assert not binary.is_symlink()
        binary = binary.resolve(strict=True)
    else:
        located = shutil.which("openssl")
        if located is None:
            pytest.skip("native loopback TLS profile incomplete: openssl CLI absent")
        binary = Path(located).resolve()
    temporary = tmp_path_factory.mktemp("account-loopback-tls")
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
            "localhost_certificate_tool_profile",
            "git-" + binary.parent.parent.name if os.name == "nt" else "native-posix",
        )
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
                "/CN=CTCC Account Loopback Test Only",
                "-addext",
                "subjectAltName=DNS:openapi.okx.com",
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
    record_testsuite_property(
        "account_tls_fixture_classification", "controlled_local_TLS_synthetic_account"
    )
    record_testsuite_property("actual_OKX_account_source_acceptance", False)
    return server, public_ca


@asynccontextmanager
async def shared_history_original_tls_harness(monkeypatch, contexts, *, change):
    """Real TCP/TLS; scripted account rows remain explicitly synthetic.

    Production client/source guards and the selected plan/origin are untouched.
    The test-only network cut refuses every destination other than its one exact
    openapi host/port and delegates real TCP only to this loopback listener.
    """
    server_context, public_ca = contexts
    client_context = ssl.create_default_context(cadata=public_ca.decode("ascii"))
    client_context.set_alpn_protocols(["http/1.1"])
    assert client_context.verify_mode == ssl.CERT_REQUIRED
    assert client_context.check_hostname is True
    guard = collector._client_guard
    connect = AutoBackend.connect_tcp
    handlers, writers, errors = set(), set(), []
    state = SimpleNamespace(
        script=[],
        clients=[],
        requests=[],
        clock=None,
        classification="controlled_local_TLS_synthetic_account",
        native_acceptance=False,
        execution_authority=False,
        accepted=asyncio.Event(),
        cleanup_checked=False,
        cleanup_assertions_passed=False,
        cleanup_errors=(),
    )
    secret = credentials()

    async def handle(reader, writer):
        task = asyncio.current_task()
        handlers.add(task)
        writers.add(writer)
        state.accepted.set()
        try:
            raw = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=5)
            assert len(raw) <= 8192
            lines = raw[:-4].split(b"\r\n")
            method, target, version = lines[0].decode("ascii").split()
            assert method == "GET" and version == "HTTP/1.1"
            assert target.startswith("/api/v5/") and not target.startswith("//")
            assert all(b":" in part for part in lines[1:])
            headers = httpx.Headers(
                [
                    (part.split(b":", 1)[0], part.split(b":", 1)[1].strip(b" \t"))
                    for part in lines[1:]
                ]
            )
            assert headers.get("host") == "openapi.okx.com"
            assert headers.get("content-length", "0") == "0"
            assert headers.get("x-simulated-trading") == "1"
            assert headers.get("ok-access-key") == secret.api_key
            assert headers.get("ok-access-passphrase") == secret.passphrase
            prehash = headers["ok-access-timestamp"].encode() + b"GET" + target.encode()
            expected = base64.b64encode(
                hmac.new(secret.api_secret.encode(), prehash, hashlib.sha256).digest()
            ).decode()
            assert hmac.compare_digest(headers["ok-access-sign"], expected)
            index = len(state.requests)
            assert index < len(state.script), "unexpected_extra_account_GET"
            stream, data = state.script[index]
            assert target.partition("?")[0] == capture._ENDPOINTS[stream]
            # Persist neither auth headers nor their synthetic private values.
            state.requests.append((stream, target))
            replacement = change(stream, index, data)
            payload = data if replacement is None else replacement
            body = payload if type(payload) is bytes else wire(payload)
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                + b"Content-Length: "
                + str(len(body)).encode()
                + b"\r\nConnection: close\r\n\r\n"
                + body
            )
            await writer.drain()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 -- test secret text must never leave the handler.
            errors.append("controlled_local_TLS_handler_failed")
        finally:
            try:
                writer.close()
                await asyncio.wait_for(writer.wait_closed(), timeout=2)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - Fixed diagnostics, never source/auth text.
                errors.append("controlled_local_TLS_cleanup_failed")
            finally:
                try:
                    # This accepted test transport cannot outlive a failed
                    # graceful close or a second cancellation during cleanup.
                    writer.transport.abort()
                except Exception:  # noqa: BLE001 - Keep fixed nonsecret failure evidence.
                    errors.append("controlled_local_TLS_transport_abort_failed")
                finally:
                    writers.discard(writer)
                    handlers.discard(task)

    def client_factory():
        assert collector._client_guard is guard
        client = httpx.AsyncClient(
            transport=httpx.AsyncHTTPTransport(
                verify=client_context, trust_env=False, retries=0
            ),
            trust_env=False,
            follow_redirects=False,
            auth=None,
        )
        guard(client)
        state.clients.append(client)
        return client

    def assert_closed():
        assert collector._client_guard is guard
        assert all(client.is_closed for client in state.clients)
        assert not errors
        assert len(state.requests) == len(state.script)
        assert state.native_acceptance is state.execution_authority is False

    state.assert_closed = assert_closed
    server = await asyncio.start_server(
        handle,
        "127.0.0.1",
        0,
        ssl=server_context,
        ssl_handshake_timeout=5,
        limit=8192,
    )
    assert len(server.sockets) == 1
    address = server.sockets[0].getsockname()
    assert address[0] == "127.0.0.1" and 0 < address[1] <= 65535
    loopback_port = address[1]
    # Diagnostic handles for controlled cleanup tests only, never account or
    # exchange authority. No listener endpoint is external or user-supplied.
    state.loopback_port, state.client_context, state.server = (
        loopback_port,
        client_context,
        server,
    )
    state.handler_tasks = lambda: tuple(handlers)

    async def local_connect(
        backend, host, port, timeout=None, local_address=None, socket_options=None
    ):
        assert host == "openapi.okx.com" and port == 443
        assert local_address is None and socket_options is None
        assert collector._client_guard is guard
        return await connect(
            backend,
            "127.0.0.1",
            loopback_port,
            timeout=timeout,
            local_address=None,
            socket_options=None,
        )

    body_error = None
    try:
        with monkeypatch.context() as patch:
            patch.setattr(AutoBackend, "connect_tcp", local_connect)
            patch.setattr(collector, "_new_client", client_factory)
            yield state
    except BaseException as error:
        # Keep the original body failure/cancellation in memory only. Cleanup
        # must not replace it with a later timeout or expose exception text.
        body_error = error
        raise
    finally:
        cleanup_errors, cleanup_fatal = [], None

        def failed(code, error):
            nonlocal cleanup_fatal
            cleanup_errors.append(code)
            if cleanup_fatal is None and isinstance(
                error, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)
            ):
                cleanup_fatal = error

        try:
            server.close()
        except BaseException as error:  # noqa: BLE001 -- retain cancellation while finishing owned cleanup.
            failed("controlled_local_TLS_listener_close_failed", error)
        finally:
            # The actual Python3.12 Server.wait_closed waits for active
            # connections. Drop those connections BEFORE that bounded wait.
            # Every later phase is in finally, so a timeout cannot skip it.
            pending, closing_writers = tuple(handlers), tuple(writers)
            for task in pending:
                task.cancel()
            for writer in closing_writers:
                try:
                    writer.close()
                except BaseException as error:  # noqa: BLE001 -- retain cancellation while finishing owned cleanup.
                    failed("controlled_local_TLS_writer_close_failed", error)
            try:
                for client in state.clients:
                    if not client.is_closed:
                        try:
                            await asyncio.wait_for(client.aclose(), timeout=2)
                        except BaseException as error:  # noqa: BLE001 -- retain cancellation while finishing owned cleanup.
                            failed("controlled_local_TLS_client_close_failed", error)
            finally:
                try:
                    if pending:
                        await asyncio.wait_for(
                            asyncio.gather(*pending, return_exceptions=True), timeout=2
                        )
                except BaseException as error:  # noqa: BLE001 -- retain cancellation while finishing owned cleanup.
                    failed("controlled_local_TLS_handler_join_failed", error)
                finally:
                    # Force-close only these accepted test transports. This
                    # runs even if graceful close or handler join timed out.
                    for writer in closing_writers:
                        try:
                            writer.transport.abort()
                        except BaseException as error:  # noqa: BLE001 -- retain cancellation while finishing owned cleanup.
                            failed("controlled_local_TLS_writer_abort_failed", error)
                    try:
                        await asyncio.wait_for(server.wait_closed(), timeout=2)
                    except BaseException as error:  # noqa: BLE001 -- retain cancellation while finishing owned cleanup.
                        failed("controlled_local_TLS_server_close_wait_failed", error)
                    finally:
                        state.cleanup_checked = True
                        try:
                            assert not handlers and not writers
                            assert all(task.done() for task in pending)
                            assert not server.is_serving()
                            assert collector._client_guard is guard
                            assert AutoBackend.connect_tcp is connect
                            assert not errors
                            assert all(client.is_closed for client in state.clients)
                            state.cleanup_assertions_passed = True
                        except BaseException as error:  # noqa: BLE001 -- retain cancellation while finishing owned cleanup.
                            failed(
                                "controlled_local_TLS_cleanup_assertion_failed", error
                            )
        state.cleanup_errors = tuple(cleanup_errors)
        if cleanup_errors:
            if body_error is not None:
                body_error.add_note(
                    "controlled_local_TLS_cleanup_incomplete; fixed diagnostic codes retained by test fixture"
                )
            elif cleanup_fatal is not None:
                raise cleanup_fatal
            else:
                raise AssertionError("controlled_local_TLS_cleanup_incomplete")
