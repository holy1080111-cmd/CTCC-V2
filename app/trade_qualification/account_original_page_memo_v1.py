"""Private, one-RPC verified-result reuse; never source or execution authority.

The original native fence owns every deadline. This module never computes a
new expiry. Public query/packet verifiers do not read this memo. No callback,
passed flag, saved receipt or caller-supplied verified DTO is accepted.
"""

import asyncio
import copy
import hashlib
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime

_ACTIVE = ContextVar("ctcc_original_page_verified_memo", default=None)
_ISSUER = object()
_COPY_PACKET = copy.deepcopy
_SHA256 = hashlib.sha256
# This bounds retained serialized-source/receipt weights, not Python RSS.
_MAX_BYTES = 64 * 1024 * 1024
_MAX_MEMBERS = 32


class OriginalPageMemoError(ValueError):
    """Closed local errors, without input values or exception interpolation."""


def _deny():
    raise OriginalPageMemoError("original_page_memo_context_invalid") from None


def _utc(value):
    return type(value) is datetime and value.tzinfo is UTC and value.fold == 0


def _digest_tokens(values):
    result = _SHA256()
    for value in values:
        if value is None:
            result.update(b"N")
            continue
        if type(value) is bytes:
            raw, tag = value, b"B"
        elif type(value) is str:
            try:
                raw, tag = value.encode("utf-8"), b"S"
            except UnicodeEncodeError:
                return None
        elif type(value) is int:
            raw, tag = str(value).encode("ascii"), b"I"
        elif _utc(value):
            raw, tag = value.isoformat().encode("ascii"), b"T"
        else:
            return None
        result.update(tag)
        result.update(len(raw).to_bytes(8, "big"))
        result.update(raw)
    return result.digest()


def _request_key(request):
    return _digest_tokens(
        (
            request.scope.environment,
            request.scope.account_id,
            request.scope.settlement_currency,
            request.through,
            request.head,
            request.after,
            request.previous,
            request.control,
            request.account,
            request.ledger,
            request.expires_at,
        )
    )


_TOKEN_DIGEST_FUNCTION = _digest_tokens
_REQUEST_KEY_FUNCTION = _request_key


class _OriginalPageContext:
    __slots__ = (
        "_packets",
        "_query_receipts",
        "closed",
        "deadlines",
        "fence",
        "request",
        "request_key",
        "retained_bytes",
        "rpc",
        "task",
    )

    def __init__(self, issuer, fence, request, started, received):
        from app.database.repositories import account_history_original_frame_v1 as wire
        from app.database.repositories import (
            account_history_original_native_read_v1 as native,
        )

        if (
            issuer is not _ISSUER
            or type(fence) is not native._Fence
            or type(request) is not wire.OriginalFrameRequestV1
        ):
            _deny()
        request.__post_init__()
        if (
            not _utc(started)
            or not _utc(received)
            or not started <= received < request.expires_at
        ):
            _deny()
        self.fence, self.request, self.task = fence, request, asyncio.current_task()
        self.request_key = _request_key(request)
        self.deadlines = (
            fence.expires_at,
            fence.monotonic_deadline,
            fence.loop_deadline,
        )
        if fence.task is not self.task or request.expires_at != fence.expires_at:
            _deny()
        self.rpc = (started, received, None)
        self._query_receipts, self._packets, self.retained_bytes = {}, {}, 0
        self.closed = False
        self.check()

    def check(self):
        if (
            self.closed
            or copy.deepcopy is not _COPY_PACKET
            or hashlib.sha256 is not _SHA256
            or _digest_tokens is not _TOKEN_DIGEST_FUNCTION
            or _request_key is not _REQUEST_KEY_FUNCTION
            or asyncio.current_task() is not self.task
            or self.task is None
            or (
                self.fence.expires_at,
                self.fence.monotonic_deadline,
                self.fence.loop_deadline,
            )
            != self.deadlines
            or _request_key(self.request) != self.request_key
        ):
            _deny()
        # The real outer fence checks its original wall/monotonic deadlines,
        # pending cancellation and exact task identity. No new TTL is minted.
        self.fence.check()

    def _remember_query(self, namespace, key, raw):
        self.check()
        if type(raw) is not bytes:
            _deny()
        cache_key = (namespace, key)
        old = self._query_receipts.get(cache_key)
        if old is not None:
            if old != raw:
                _deny()
            return
        size = len(raw) + 64
        if (
            len(self._query_receipts) < _MAX_MEMBERS
            and self.retained_bytes + size <= _MAX_BYTES
        ):
            self._query_receipts[cache_key] = raw
            self.retained_bytes += size

    def _remember_packet(self, namespace, key, packet, source_bytes):
        self.check()
        size = source_bytes + 64
        cache_key = (namespace, key)
        if (
            cache_key not in self._packets
            and len(self._packets) < _MAX_MEMBERS
            and self.retained_bytes + size <= _MAX_BYTES
        ):
            # The cache copy is never returned. Every hit makes another copy.
            self._packets[cache_key] = _COPY_PACKET(packet)
            self.retained_bytes += size
        self.check()


@contextmanager
def _native_page_context(fence, request, started, received):
    if _ACTIVE.get() is not None:
        _deny()
    context = _OriginalPageContext(_ISSUER, fence, request, started, received)
    token = _ACTIVE.set(context)
    body_error = cleanup_error = None
    try:
        yield
    except BaseException as error:
        body_error = error
        raise
    finally:
        try:
            # This boundary is outside the query/capture/wire error closers.
            context.check()
        except BaseException as error:  # noqa: BLE001 -- retain cancellation/GeneratorExit while closing the page; never discard cleanup.
            cleanup_error = error
        finally:
            # Clear and close BEFORE reset, even on cross-Context aclose.
            context._query_receipts.clear()
            context._packets.clear()
            context.retained_bytes = 0
            context.closed = True
            try:
                _ACTIVE.reset(token)
            except (ValueError, RuntimeError):
                if _ACTIVE.get() is context:
                    _ACTIVE.set(None)
                if cleanup_error is None:
                    cleanup_error = OriginalPageMemoError(
                        "original_page_memo_context_invalid"
                    )
        # A failed token reset must not hide an original cancellation. Other
        # expiry/ownership failures remain fixed errors at the native boundary.
        if cleanup_error is not None and not isinstance(
            body_error, asyncio.CancelledError
        ):
            raise cleanup_error from None


def _bind_validated_rpc(db_observed_at, backend_pid, transaction_id):
    context = _ACTIVE.get()
    if context is None:
        return
    if type(context) is not _OriginalPageContext:
        _deny()
    context.check()
    started, received, previous = context.rpc
    if (
        previous is not None
        or not _utc(db_observed_at)
        or not started <= db_observed_at <= received
        or type(backend_pid) is not int
        or not 0 < backend_pid < 2**31
        or type(transaction_id) is not str
        or not transaction_id.isascii()
        or not 1 <= len(transaction_id) <= 20
        or not transaction_id.isdigit()
    ):
        _deny()
    context.rpc = (
        started,
        received,
        _digest_tokens((db_observed_at, backend_pid, transaction_id)),
    )


def _native_current_context():
    context = _ACTIVE.get()
    if context is None:
        return None
    if type(context) is not _OriginalPageContext:
        _deny()
    context.check()
    return (
        context
        if context.rpc[2] is not None and context.request_key is not None
        else None
    )


def _query_key(context, chain, pins):
    from app.trade_qualification import account_capture_journal as journal

    context.check()
    if (
        type(chain) is not tuple
        or not 1 <= len(chain) <= journal.MAX_EVENTS
        or type(pins) is not tuple
        or len(pins) != 6
        or any(type(value) is not str for value in pins)
    ):
        return None
    tokens, total = [context.request_key, *context.rpc, *pins], 0
    for point in chain:
        if (
            type(point) is not journal.JournalReadback
            or type(point.event) is not journal._JournalEvent
        ):
            return None
        if not _utc(point.db_recorded_at) or not _utc(point.readback_at):
            return None
        for raw in (
            point.event.event_json,
            point.event.raw_body,
            point.event.packet_payload,
        ):
            if raw is not None and type(raw) is not bytes:
                return None
            total += 0 if raw is None else len(raw)
            if total > journal.MAX_CHAIN_BYTES:
                return None
            tokens.append(raw)
        tokens.extend((point.db_recorded_at, point.readback_at))
    key = _digest_tokens(tokens)
    context.check()
    return key


def _packet_key(context, payload, expected_sha256, expected_plan_sha256):
    from app.trade_qualification import account_capture as capture

    context.check()
    if (
        type(payload) is not bytes
        or not 1 <= len(payload) <= capture.MAX_PACKET_BYTES
        or type(expected_sha256) is not str
        or type(expected_plan_sha256) is not str
    ):
        return None
    key = _digest_tokens(
        (
            context.request_key,
            *context.rpc,
            payload,
            expected_sha256,
            expected_plan_sha256,
        )
    )
    context.check()
    return key


def _fresh_packet_copy(packet):
    if copy.deepcopy is not _COPY_PACKET:
        _deny()
    return _COPY_PACKET(packet)
