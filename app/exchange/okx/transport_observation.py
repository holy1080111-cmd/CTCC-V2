"""Private, non-authorizing observation of one prepared OKX order response.

This module never sends a request.  The caller must retain the original request,
consume the response once, and separately persist an uncertain intent when any
post-send observation is unavailable.  Raw bytes are private and never appear in
``repr`` or a log-safe diagnostic.
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

import httpx

MAX_ORDER_RESPONSE_BYTES = 65536


@dataclass(frozen=True, slots=True, repr=False)
class OrderTransportObservation:
    request_body_sha256: str
    send_invoked_at: datetime
    response_returned_at: datetime | None
    body_observed_at: datetime | None
    http_status: int | None
    raw_body: bytes | None = field(repr=False)
    transport_complete: bool = False
    execution_authority: Literal[False] = field(default=False, init=False)
    order_retry_authority: Literal[False] = field(default=False, init=False)

    def __repr__(self) -> str:
        return "<OrderTransportObservation private DENY>"


@dataclass(frozen=True, slots=True, repr=False)
class ObservedOrderResult:
    """One private request/response observation with a response digest.

    No parsed or caller-mutable response is treated as evidence.  The original
    response bytes must be independently joined to the durable intent and
    classified by the post-submit owner before any reporting.
    """

    observation: OrderTransportObservation
    response_body_sha256: str | None
    execution_authority: Literal[False] = field(default=False, init=False)
    order_retry_authority: Literal[False] = field(default=False, init=False)

    def __repr__(self) -> str:
        return "<ObservedOrderResult private DENY>"


def _utc(clock) -> datetime:
    value = clock()
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("order_observation_clock_invalid")
    return value.astimezone(UTC)


def _body_hash(request: httpx.Request) -> str:
    if type(request) is not httpx.Request or type(request.content) is not bytes:
        raise ValueError("order_observation_request_invalid")
    return hashlib.sha256(request.content).hexdigest()


async def observe_order_response(
    request: httpx.Request,
    response: httpx.Response,
    *,
    send_invoked_at: datetime,
    clock,
) -> OrderTransportObservation:
    """Read and close one streamed response; never infer ack from HTTP 200.

    A too-large, contradictory or interrupted body becomes incomplete.  Malformed
    JSON remains original raw bytes for a later classifier to reject. Cancellation
    is propagated, leaving the already committed intent unresolved for its owner.
    """

    pending_cancel = False
    try:
        # The response is already returned by send. Every later validation and
        # clock call must stay inside the close guarantee.
        if type(response) is not httpx.Response:
            raise ValueError("order_observation_response_invalid")
        body_sha256 = _body_hash(request)
        if response.request is not request:
            raise ValueError("order_observation_response_invalid")
        if type(send_invoked_at) is not datetime or send_invoked_at.tzinfo is not UTC:
            raise ValueError("order_observation_start_invalid")
        headers_at = _utc(clock)
        if headers_at < send_invoked_at:
            raise ValueError("order_observation_clock_reversed")
        raw = bytearray()
        complete = True
        if (
            len(response.headers.raw) > 128
            or sum(len(key) + len(value) for key, value in response.headers.raw) > 32768
        ):
            complete = False
        for name in ("content-length", "content-type", "content-encoding"):
            if len(response.headers.get_list(name)) > 1:
                complete = False
        length = response.headers.get("content-length")
        if length is not None and (
            not length.isascii()
            or not length.isdigit()
            or len(length) > 10
            or int(length) > MAX_ORDER_RESPONSE_BYTES
        ):
            complete = False
        if response.headers.get("content-encoding", "identity").lower() != "identity":
            complete = False
        if not complete:
            # Still close the stream.  No unbounded body is read into memory.
            return OrderTransportObservation(
                body_sha256,
                send_invoked_at,
                headers_at,
                None,
                response.status_code,
                None,
                False,
            )
        chunks = 0
        try:
            async for chunk in response.aiter_raw():
                chunks += 1
                if (
                    type(chunk) is not bytes
                    or chunks > 1024
                    or len(raw) + len(chunk) > MAX_ORDER_RESPONSE_BYTES
                ):
                    complete = False
                    break
                raw.extend(chunk)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 -- post-send faults are unknown, not rejection
            complete = False
        completed_at = _utc(clock)
        if completed_at < headers_at:
            complete = False
        if length is not None and complete and len(raw) != int(length):
            complete = False
        return OrderTransportObservation(
            body_sha256,
            send_invoked_at,
            headers_at,
            completed_at if complete else None,
            response.status_code,
            bytes(raw) if complete else None,
            complete,
        )
    except asyncio.CancelledError:
        pending_cancel = True
        raise
    finally:
        if isinstance(response, httpx.Response):
            try:
                await response.aclose()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 -- close failure cannot establish completeness
                if not pending_cancel:
                    raise ValueError("order_observation_close_unverified") from None
