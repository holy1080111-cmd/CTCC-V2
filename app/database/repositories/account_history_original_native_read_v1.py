"""Actual PostgreSQL original-prefix read and replay; never a source owner.

This additive IO slice deliberately accepts untrusted request/role/factory
inputs and verifies what its actual database connection can observe. It does
not authenticate the upstream Control lease, factory custody, credential
session or account identity. Those missing owners cannot be replaced by this
read observation, its request DTO, a successful query or a replay receipt.
"""

import asyncio
import hashlib
import time
from contextlib import aclosing
from dataclasses import dataclass, replace
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.repositories import account_history_original_frame_v1 as wire
from app.domain import account_history_original_frame_v1 as frame_policy
from app.domain import account_history_partition_catalog_v1 as parent_policy
from app.domain import account_history_partition_v1 as records
from app.trade_qualification import account_capture_journal as journal
from app.trade_qualification import account_history_original_stream_v1 as stream

# Cleanup cannot make a late acquisition eligible. This bounds one final
# close attempt after a failed/cancelled read; it never retries commit or RPC.
FAILED_READ_CLOSE_SECONDS = 5.0
ERROR_CODES = frozenset(
    {
        "original_pg_request_invalid",
        "original_pg_factory_invalid",
        "original_pg_session_invalid",
        "original_pg_clock_or_task_changed",
        "original_pg_original_expiry",
        "original_pg_backend_changed",
        "original_pg_request_causality",
        "original_pg_catalog_changed",
        "original_pg_incomplete",
        "original_pg_read_uncertain",
        "original_pg_cleanup_uncertain",
    }
)


class OriginalHistoryPgReadError(ValueError):
    """Closed source-safe error; no SQL, account, URL or row text."""


def _deny(code):
    if code not in ERROR_CODES:
        code = "original_pg_read_uncertain"
    raise OriginalHistoryPgReadError(code) from None


@dataclass(frozen=True, slots=True, repr=False)
class OriginalHistoryPgReadObservationV1:
    replay: stream.OriginalHistoryStreamReplayV1
    observed_frames: int
    acquisition_membership_sha256: str
    catalog_sha256: str
    first_request_started_at: datetime
    last_rpc_received_at: datetime
    session_closed_at: datetime

    @property
    def native_history_prefix_verified(self):
        return False

    @property
    def account_complete(self):
        return False

    @property
    def account_revision_published(self):
        return False

    @property
    def execution_authority(self):
        return False


class _Fence:
    def __init__(self, expires_at):
        self.task = asyncio.current_task()
        if self.task is not None and self.task.cancelling():
            raise asyncio.CancelledError() from None
        self.expires_at = expires_at
        self.wall = datetime.now(UTC)
        self.monotonic_ns = time.monotonic_ns()
        remaining = (expires_at - self.wall).total_seconds()
        if self.task is None or remaining <= 0:
            _deny("original_pg_original_expiry")
        self.monotonic_deadline = self.monotonic_ns + int(remaining * 1_000_000_000)
        self.loop_deadline = asyncio.get_running_loop().time() + remaining

    def check(self):
        if self.task.cancelling():
            raise asyncio.CancelledError() from None
        wall, monotonic_ns = datetime.now(UTC), time.monotonic_ns()
        if (
            asyncio.current_task() is not self.task
            or wall < self.wall
            or monotonic_ns < self.monotonic_ns
        ):
            _deny("original_pg_clock_or_task_changed")
        if wall >= self.expires_at or monotonic_ns >= self.monotonic_deadline:
            _deny("original_pg_original_expiry")
        self.wall, self.monotonic_ns = wall, monotonic_ns
        return wall


def _check_request(genesis_request, checkpoint, approved_role):
    try:
        if type(genesis_request) is not wire.OriginalFrameRequestV1:
            _deny("original_pg_request_invalid")
        genesis_request.__post_init__()
        if genesis_request.after != 0 or genesis_request.previous is not None:
            _deny("original_pg_request_invalid")
        if genesis_request.through < records.PARTITION_SPAN:
            if checkpoint is not None:
                _deny("original_pg_request_invalid")
        else:
            records._hash(checkpoint)
        if type(approved_role) is not int or not 0 < approved_role < 2**32:
            _deny("original_pg_request_invalid")
    except OriginalHistoryPgReadError:
        raise
    except Exception:  # noqa: BLE001 -- request values are never disclosed
        _deny("original_pg_request_invalid")


async def _execute_one(session, statement, fence, parameters=None):
    fence.check()
    result = await session.execute(text(statement), parameters or {})
    fence.check()
    return result.scalar_one()


async def _catalog(session, fence, approved_role):
    previous = await _execute_one(session, parent_policy.CATALOG_SQL, fence)
    # Both strict defaults stay enabled. The legacy metadata runtime-role
    # verifier expects append/read grants and must not be applied here.
    parent_policy.verify_account_history_partition_catalog(previous)
    actual = await _execute_one(session, frame_policy.CATALOG_SQL, fence)
    frame_policy.verify_catalog(
        actual, previous, approved_collector_role_oid=approved_role
    )
    fence.check()
    return journal.canonical({"partition": previous, "frame": actual})


async def _backend(session, fence):
    fence.check()
    result = await session.execute(
        text("SELECT pg_backend_pid(), pg_current_xact_id()::text")
    )
    fence.check()
    row = result.one()
    backend = tuple(row)
    if (
        len(backend) != 2
        or type(backend[0]) is not int
        or not 0 < backend[0] < 2**31
        or type(backend[1]) is not str
        or not 1 <= len(backend[1]) <= 20
        or not backend[1].isascii()
        or not backend[1].isdigit()
    ):
        _deny("original_pg_backend_changed")
    return backend


def _parameters(request):
    return {
        "environment": request.scope.environment,
        "account_id": request.scope.account_id,
        "currency": request.scope.settlement_currency,
        "through": request.through,
        "head": request.head,
        "after": request.after,
        "previous": request.previous,
        "control": request.control,
        "account": request.account,
        "ledger": request.ledger,
        "expires": request.expires_at,
    }


async def _pages(session, genesis_request, fence, state):
    after, previous, previous_page = 0, None, None
    while after < genesis_request.through:
        request = replace(genesis_request, after=after, previous=previous)
        started = fence.check()
        value = await _execute_one(session, wire.SQL, fence, _parameters(request))
        received = fence.check()
        from app.trade_qualification import account_original_page_memo_v1 as page_memo

        with page_memo._native_page_context(fence, request, started, received):
            decoded = wire.decode_original_frame_wire_v1(
                value, request=request, client_received_at=received
            )
            # Frozen DB0052 uses clock_timestamp() AFTER exporting the original
            # rows, not transaction_timestamp(). Never rewrite any timestamp.
            if not started <= decoded.db_observed_at <= received:
                _deny("original_pg_request_causality")
            backend = (decoded.backend_pid, decoded.transaction_id)
            if state["first_started"] is None:
                state["first_started"] = started
            if state["backend"] != backend:
                _deny("original_pg_backend_changed")
            page = wire.original_frame_width1_page_v1(
                decoded, page_index=after, previous_page_sha256=previous_page
            )
            page_sha256 = stream.original_history_page_sha256_v1(page)
            state["membership"].update(
                journal.canonical(
                    {
                        "sequence": decoded.frame.batch.sequence,
                        "event_sha256": decoded.frame.batch.event_sha256,
                        "page_sha256": page_sha256,
                        "request_started_at": started.isoformat(),
                        "db_observed_at": decoded.db_observed_at.isoformat(),
                        "client_received_at": received.isoformat(),
                        "backend_pid": decoded.backend_pid,
                        "transaction_id": decoded.transaction_id,
                    }
                )
            )
            fence.check()
            yield page
            # A yielded page is accepted by the in-task pure replay before this
            # cursor advances. B3 predecessor and page predecessor stay distinct.
            fence.check()
        after = decoded.frame.batch.sequence
        previous = decoded.frame.batch.event_sha256
        previous_page = page_sha256
        state["frames"], state["last_received"] = after, received
        # Do not accumulate raw frames or decoded observations across pages.
        del value, decoded, page


async def replay_original_history_from_pg_v1(
    session_factory,
    *,
    genesis_request,
    expected_checkpoint_sha256,
    approved_collector_role_oid,
):
    """Read every original frame from genesis, replay, commit and close.

    A caller-supplied factory/request/role pin is NOT an authenticated owner.
    Successful output is an inert actual-database read observation. It cannot
    remove any missing acquisition/economic/tail blocker from pure replay.
    No RPC, commit or uncertain cleanup is retried and no lease is renewed.
    """
    _check_request(
        genesis_request, expected_checkpoint_sha256, approved_collector_role_oid
    )
    if (
        type(session_factory) is not async_sessionmaker
        or session_factory.class_ is not AsyncSession
    ):
        _deny("original_pg_factory_invalid")
    fence = _Fence(genesis_request.expires_at)
    state = {
        "backend": None,
        "first_started": None,
        "last_received": None,
        "frames": 0,
        "membership": hashlib.sha256(),
    }
    state["membership"].update(
        journal.canonical(
            {
                "scope": records._scope(genesis_request.scope),
                "through_sequence": genesis_request.through,
                "original_head_sha256": genesis_request.head,
                "original_checkpoint_sha256": expected_checkpoint_sha256,
                "control_revision": genesis_request.control,
                "account_revision": genesis_request.account,
                "ledger_revision": genesis_request.ledger,
                "original_expires_at": genesis_request.expires_at.isoformat(),
                "approved_collector_role_oid": approved_collector_role_oid,
            }
        )
    )
    session = None
    close_attempted = False
    try:
        async with asyncio.timeout_at(fence.loop_deadline):
            session = session_factory()
            if (
                type(session) is not AsyncSession
                or session.get_bind().dialect.name != "postgresql"
            ):
                _deny("original_pg_session_invalid")
            bind = session.get_bind()
            fence.check()
            await session.begin()
            fence.check()
            for statement in (
                "SET TRANSACTION ISOLATION LEVEL READ COMMITTED",
                "SET LOCAL search_path TO pg_catalog, public, pg_temp",
                "SET LOCAL TIME ZONE 'UTC'",
                "SET LOCAL lock_timeout='1000ms'",
                "SET LOCAL statement_timeout='10000ms'",
            ):
                await session.execute(text(statement))
                fence.check()
            before = await _catalog(session, fence, approved_collector_role_oid)
            state["backend"] = await _backend(session, fence)
            producer = _pages(session, genesis_request, fence, state)
            # The existing pure consumer closes only its internal engine.
            # This wrapper explicitly closes and joins the original producer.
            async with aclosing(producer):
                replay = await stream.replay_original_history_stream_async_v1(
                    producer,
                    scope=genesis_request.scope,
                    expected_through_sequence=genesis_request.through,
                    expected_b3_head_sha256=genesis_request.head,
                    expected_checkpoint_sha256=expected_checkpoint_sha256,
                )
                fence.check()
            fence.check()
            if (
                state["frames"] != genesis_request.through
                or state["backend"] is None
                or session.get_bind() is not bind
            ):
                _deny("original_pg_incomplete")
            after = await _catalog(session, fence, approved_collector_role_oid)
            if (
                before != after
                or state["backend"] != await _backend(session, fence)
                or session.get_bind() is not bind
            ):
                _deny("original_pg_catalog_changed")
            fence.check()
            await session.commit()
            fence.check()
            close_attempted = True
            await session.close()
            closed_at = fence.check()
            observation = OriginalHistoryPgReadObservationV1(
                replay,
                state["frames"],
                state["membership"].hexdigest(),
                journal.digest(before),
                state["first_started"],
                state["last_received"],
                closed_at,
            )
        fence.check()
        return observation
    except asyncio.CancelledError:
        raise
    except OriginalHistoryPgReadError:
        raise
    except Exception:  # noqa: BLE001 -- no private SQL/source exception text
        _deny("original_pg_read_uncertain")
    finally:
        if session is not None and not close_attempted:
            # Cancellation/expiry never returns a receipt. Close is attempted
            # once, joined in this task, under a separate bounded cleanup cap.
            close_attempted = True
            try:
                async with asyncio.timeout(FAILED_READ_CLOSE_SECONDS):
                    await session.close()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 -- uncertainty remains fail closed
                _deny("original_pg_cleanup_uncertain")
