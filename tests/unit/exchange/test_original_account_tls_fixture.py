"""Real controlled loopback TLS cleanup, never an OKX/account acceptance.

Uses the same reviewed ephemeral certificate/OpenSSL fixture and its existing
platform profiles. No external peer, system trust store, API key or order IO.
"""

from __future__ import annotations

import asyncio
import ssl

import pytest

from tests.original_account_tls_fixture import (
    shared_history_original_tls_contexts as shared_history_original_tls_contexts,  # noqa: PLC0414 - explicit reviewed fixture.
)
from tests.original_account_tls_fixture import (
    shared_history_original_tls_harness,
)

pytestmark = pytest.mark.asyncio


async def _connect_stalled_accepted_peer(state):
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(
            "127.0.0.1",
            state.loopback_port,
            ssl=state.client_context,
            server_hostname="openapi.okx.com",
            ssl_handshake_timeout=5,
        ),
        timeout=5,
    )
    await asyncio.wait_for(state.accepted.wait(), timeout=2)
    # The TLS handshake is real and complete, but no HTTP header is sent.
    # The fixture's actual accepted handler is now waiting on readuntil().
    tasks = state.handler_tasks()
    assert tasks and all(not task.done() for task in tasks)
    assert state.client_context.verify_mode == ssl.CERT_REQUIRED
    assert state.client_context.check_hostname is True
    assert state.server.is_serving()
    return reader, writer, tasks


async def _assert_actual_peer_closed(reader):
    try:
        assert await asyncio.wait_for(reader.read(1), timeout=2) == b""
    except (ConnectionError, ssl.SSLError):
        # A bounded forced abort is also an observed transport close, not an
        # inferred Boolean from the fixture. No further request is attempted.
        pass


async def _close_test_peer(writer):
    if writer is None:
        return
    writer.close()
    try:
        await asyncio.wait_for(writer.wait_closed(), timeout=2)
    except (ConnectionError, ssl.SSLError, TimeoutError):
        writer.transport.abort()


async def test_stalled_accepted_tls_handler_is_cancelled_joined_and_peer_closed(
    monkeypatch, shared_history_original_tls_contexts
):
    writer = None
    try:
        with pytest.raises(RuntimeError, match="^synthetic_body_failure$"):
            async with shared_history_original_tls_harness(
                monkeypatch,
                shared_history_original_tls_contexts,
                change=lambda stream, index, data: None,
            ) as state:
                reader, writer, tasks = await _connect_stalled_accepted_peer(state)
                raise RuntimeError("synthetic_body_failure")
        await _assert_actual_peer_closed(reader)
        assert all(task.done() for task in tasks)
        assert state.handler_tasks() == () and not state.server.is_serving()
        assert state.cleanup_checked and state.cleanup_assertions_passed
        assert state.cleanup_errors == ()
        assert state.native_acceptance is state.execution_authority is False
    finally:
        await _close_test_peer(writer)


@pytest.mark.parametrize("fail_wait", (False, True))
async def test_cancelled_body_preserves_cancelled_error_and_closes_accepted_tls_peer(
    monkeypatch, shared_history_original_tls_contexts, fail_wait
):
    writer = None
    try:
        with pytest.raises(asyncio.CancelledError) as cancelled:
            async with shared_history_original_tls_harness(
                monkeypatch,
                shared_history_original_tls_contexts,
                change=lambda stream, index, data: None,
            ) as state:
                reader, writer, tasks = await _connect_stalled_accepted_peer(state)
                actual_wait_closed = state.server.wait_closed
                if fail_wait:

                    async def injected_wait_failure():
                        raise TimeoutError("synthetic_wait_failure_not_exported")

                    monkeypatch.setattr(
                        state.server, "wait_closed", injected_wait_failure
                    )
                # This is an actual task cancellation at an await boundary,
                # rather than a fabricated cleanup-state or error report.
                asyncio.current_task().cancel()
                await asyncio.sleep(0)
        await _assert_actual_peer_closed(reader)
        assert all(task.done() for task in tasks)
        assert state.handler_tasks() == () and not state.server.is_serving()
        assert state.cleanup_checked and state.cleanup_assertions_passed
        if fail_wait:
            await asyncio.wait_for(actual_wait_closed(), timeout=2)
            assert state.cleanup_errors == (
                "controlled_local_TLS_server_close_wait_failed",
            )
            assert cancelled.value.__notes__ == [
                "controlled_local_TLS_cleanup_incomplete; fixed diagnostic codes retained by test fixture"
            ]
        else:
            assert state.cleanup_errors == ()
    finally:
        await _close_test_peer(writer)


async def test_server_wait_failure_cannot_skip_handler_cleanup_or_final_assertions(
    monkeypatch, shared_history_original_tls_contexts
):
    writer = None
    try:
        with pytest.raises(
            AssertionError, match="^controlled_local_TLS_cleanup_incomplete$"
        ):
            async with shared_history_original_tls_harness(
                monkeypatch,
                shared_history_original_tls_contexts,
                change=lambda stream, index, data: None,
            ) as state:
                reader, writer, tasks = await _connect_stalled_accepted_peer(state)
                actual_wait_closed = state.server.wait_closed

                async def injected_wait_failure():
                    raise TimeoutError("synthetic_wait_failure_not_exported")

                # Only the wait observation is failed. Real cancellation,
                # handler joining, accepted writers and socket closure still run.
                monkeypatch.setattr(state.server, "wait_closed", injected_wait_failure)
        await _assert_actual_peer_closed(reader)
        await asyncio.wait_for(actual_wait_closed(), timeout=2)
        assert all(task.done() for task in tasks)
        assert state.handler_tasks() == () and not state.server.is_serving()
        assert state.cleanup_checked and state.cleanup_assertions_passed
        assert state.cleanup_errors == (
            "controlled_local_TLS_server_close_wait_failed",
        )
    finally:
        await _close_test_peer(writer)
