"""One public socket, one subscription acknowledgement, one ticker, then close.

No login, credentials, proxy, redirect, retry, reconnect or order operation.
Transport/TLS and the supplied UTC clock are runtime dependencies, not source
attestation. A serialized capture cannot prove socket ownership or grant entry
authority. This module is separate from the legacy merged-channel WS cache.
Wire format: https://app.okx.com/docs-v5/en/ (checked 2026-09-12).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import Callable
from datetime import datetime, timedelta
from decimal import Decimal
from itertools import pairwise
from typing import Annotated, Literal

from pydantic import ConfigDict, Field, TypeAdapter, field_validator, model_validator
from websockets.asyncio.client import connect

from app.trade_qualification.event_models import Digest
from app.trade_qualification.location import _revalidate
from app.trade_qualification.models import QualificationModel, ReportId, require_aware
from app.trade_qualification.ws_reference import (
    WSCaptureError,
    WSSubscriptionAck,
    WSTickerFrame,
    parse_ws_subscription_ack,
    parse_ws_ticker_frame,
    validate_ws_subscription_ack,
    validate_ws_ticker_frame,
)

PUBLIC_WS_URL = "wss://ws.okx.com:8443/ws/v5/public"
_INST = re.compile(r"[A-Z0-9]{1,16}-[A-Z0-9]{1,16}-SWAP")
_TIMES = (
    "connection_started_at",
    "connected_at",
    "subscribe_started_at",
    "subscribe_completed_at",
    "closed_at",
    "completed_at",
)
_FLAGS = (
    "execution_authority",
    "source_authenticity_verified",
    "socket_binding_verified",
)


class WSCollectionError(ValueError):
    """Only stable local codes escape; never include a remote error or raw frame."""

    @property
    def code(self):
        return str(self)


def _utc(value):
    if type(value) is not datetime:
        raise WSCollectionError("ws_clock_invalid")
    try:
        return require_aware(value)
    except ValueError:
        raise WSCollectionError("ws_clock_invalid") from None


class WSCollectionPolicy(QualificationModel):
    max_age_seconds: int = Field(ge=1, le=60)
    open_timeout_seconds: int = Field(default=3, ge=1, le=5)
    receive_timeout_seconds: int = Field(default=3, ge=1, le=5)
    batch_timeout_seconds: int = Field(default=10, ge=1, le=15)
    close_timeout_seconds: int = Field(default=1, ge=1, le=3)
    max_message_bytes: int = Field(default=32768, ge=1024, le=65536)


def _policy_copy(value):
    # Do not invoke model_dump on an injected nested serializer before checking
    # that every policy input is really an integer, not a caller-produced value.
    if (
        type(value) is not WSCollectionPolicy
        or set(value.__dict__) != set(WSCollectionPolicy.model_fields)
        or value.__pydantic_extra__
        or any(type(part) is not int for part in value.__dict__.values())
    ):
        raise WSCollectionError("ws_policy_invalid")
    return WSCollectionPolicy.model_validate(dict(value.__dict__), strict=True)


def _subscription_id(report, instrument):
    return hashlib.sha256(f"{report}:{instrument}".encode("ascii")).hexdigest()[:32]


def _subscription(subscription_id, instrument):
    return json.dumps(
        {
            "id": subscription_id,
            "op": "subscribe",
            "args": [{"channel": "tickers", "instId": instrument}],
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")


def _digest(values):
    def serial(value):
        if isinstance(value, QualificationModel):
            return {
                k: serial(v)
                for k, v in value.model_dump(mode="python", round_trip=True).items()
            }
        if type(value) is datetime:
            return value.isoformat()
        if type(value) is bytes:
            return {"wire_bytes_hex": value.hex()}
        if isinstance(value, dict):
            return {k: serial(v) for k, v in value.items()}
        if isinstance(value, (tuple, list)):
            return [serial(v) for v in value]
        # Decimal fields are already checked by the immutable wire models.
        if isinstance(value, Decimal):
            return str(value)
        return value

    return hashlib.sha256(
        json.dumps(
            serial(values),
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


class CollectedWSReference(QualificationModel):
    model_config = ConfigDict(str_strip_whitespace=False)

    report_id: ReportId
    instrument_id: Annotated[
        str, Field(pattern=r"^[A-Z0-9]{1,16}-[A-Z0-9]{1,16}-SWAP$")
    ]
    endpoint: Literal["wss://ws.okx.com:8443/ws/v5/public"] = PUBLIC_WS_URL
    policy: WSCollectionPolicy
    barrier_completed_at: datetime | None
    connection_started_at: datetime
    connected_at: datetime
    subscribe_started_at: datetime
    subscribe_completed_at: datetime
    subscription_id: Annotated[str, Field(pattern=r"^[a-f0-9]{32}$")]
    subscription_body: bytes = Field(min_length=1, max_length=256)
    subscription_sha256: Digest
    ack: WSSubscriptionAck
    ticker: WSTickerFrame
    closed_at: datetime
    completed_at: datetime
    bundle_sha256: Digest
    execution_authority: Literal[False] = False
    source_authenticity_verified: Literal[False] = False
    socket_binding_verified: Literal[False] = False

    _times = field_validator(*_TIMES)(_utc)

    @field_validator("barrier_completed_at")
    @classmethod
    def barrier_time(cls, value):
        return None if value is None else _utc(value)

    @field_validator(*_FLAGS, mode="before")
    @classmethod
    def no_authority(cls, value):
        if type(value) is not bool or value is not False:
            raise ValueError("ws_collector_cannot_grant_authority")
        return value

    @model_validator(mode="after")
    def consistent_capture(self):
        ack = validate_ws_subscription_ack(self.ack)
        ticker = validate_ws_ticker_frame(self.ticker)
        if (
            ack.instrument_id != self.instrument_id
            or ticker.instrument_id != self.instrument_id
            or ticker.report_id != self.report_id
            or ack.subscription_id != self.subscription_id
        ):
            raise ValueError("ws_capture_identity_mismatch")
        if self.subscription_id != _subscription_id(self.report_id, self.instrument_id):
            raise ValueError("ws_subscription_identity_mismatch")
        expected = _subscription(self.subscription_id, self.instrument_id)
        if (
            self.subscription_body != expected
            or self.subscription_sha256 != hashlib.sha256(expected).hexdigest()
        ):
            raise ValueError("ws_subscription_body_mismatch")
        ordered = (
            self.connection_started_at,
            self.connected_at,
            self.subscribe_started_at,
            self.subscribe_completed_at,
            ack.received_at,
            ticker.received_at,
            self.closed_at,
            self.completed_at,
        )
        if any(a > b for a, b in pairwise(ordered)):
            raise ValueError("ws_capture_clock_reversed")
        if self.barrier_completed_at is not None and (
            self.connection_started_at <= self.barrier_completed_at
            or ticker.source_time <= self.barrier_completed_at
        ):
            raise ValueError("ws_publication_barrier_not_crossed")
        if (
            self.connected_at - self.connection_started_at
            > timedelta(seconds=self.policy.open_timeout_seconds)
            or ack.received_at - self.subscribe_completed_at
            > timedelta(seconds=self.policy.receive_timeout_seconds)
            or ticker.received_at - ack.received_at
            > timedelta(seconds=self.policy.receive_timeout_seconds)
            or self.closed_at - ticker.received_at
            > timedelta(seconds=self.policy.close_timeout_seconds)
            or self.completed_at - self.connection_started_at
            > timedelta(seconds=self.policy.batch_timeout_seconds)
        ):
            raise ValueError("ws_capture_deadline_exceeded")
        if self.completed_at - ticker.source_time > timedelta(
            seconds=self.policy.max_age_seconds
        ):
            raise ValueError("ws_reference_stale")
        if (
            max(ack.frame_size_bytes, ticker.frame_size_bytes)
            > self.policy.max_message_bytes
        ):
            raise ValueError("ws_frame_too_large")
        if self.bundle_sha256 != _digest(
            {
                name: getattr(self, name)
                for name in type(self).model_fields
                if name != "bundle_sha256"
            }
        ):
            raise ValueError("ws_bundle_digest_mismatch")
        return self

    @property
    def reference(self):
        return validate_collected_ws_reference(self).ticker.reference


def validate_collected_ws_reference(value):
    """Bound/check nested instances before serialization can discard hidden keys."""
    try:
        if type(value) is not CollectedWSReference:
            raise ValueError("exact capture required")
        if (
            set(value.__dict__) != set(CollectedWSReference.model_fields)
            or value.__pydantic_extra__
        ):
            raise ValueError("capture fields invalid")
        _policy_copy(value.policy)
        validate_ws_subscription_ack(value.ack)
        validate_ws_ticker_frame(value.ticker)
        if (
            type(value.subscription_body) is not bytes
            or not 1 <= len(value.subscription_body) <= 256
        ):
            raise ValueError("subscription body invalid")
        for name in (
            "report_id",
            "instrument_id",
            "endpoint",
            "subscription_id",
            "subscription_sha256",
            "bundle_sha256",
        ):
            part = getattr(value, name)
            if type(part) is not str or len(part) > 128:
                raise ValueError("capture scalar invalid")
        for name in _TIMES:
            _utc(getattr(value, name))
        if value.barrier_completed_at is not None:
            _utc(value.barrier_completed_at)
        for name in _FLAGS:
            if getattr(value, name) is not False:
                raise ValueError("capture authority invalid")
        return _revalidate(value, CollectedWSReference)
    except (ValueError, TypeError, AttributeError, ArithmeticError, RecursionError):
        raise WSCollectionError("ws_record_invalid") from None


class _NoRedirectConnect(connect):
    # websockets 17's single-await API follows redirects unless overridden.
    # Its handshake path aborts the rejected transport before calling this hook.
    def process_redirect(self, exc):
        return exc


async def _close_socket(socket, policy, *, pending_cancel=False):
    async def close_once():
        async with asyncio.timeout(policy.close_timeout_seconds):
            await socket.close()

    task = asyncio.create_task(close_once())
    interrupted = pending_cancel
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            interrupted = True
        except Exception:  # noqa: BLE001 -- cleanup must not swallow cancellation
            break
    failed = False
    try:
        task.result()
    except asyncio.CancelledError:
        failed = True
    except Exception:  # noqa: BLE001 -- no transport exception disclosure
        failed = True
    if failed:
        try:
            socket.transport.abort()
        except Exception:  # noqa: BLE001, S110 -- preserve cancellation; no remote logs
            pass
    if interrupted:
        raise asyncio.CancelledError
    if failed:
        raise WSCollectionError("ws_cleanup_failed") from None


async def _receive(socket, policy):
    async with asyncio.timeout(policy.receive_timeout_seconds):
        raw = await socket.recv()
    # Only a UTF-8 text application message is admitted. websocket framing,
    # TLS bytes and frame fragmentation aren't claimed as captured evidence.
    if type(raw) is not str or not 1 <= len(raw) <= policy.max_message_bytes:
        raise WSCollectionError("ws_message_type_or_size_invalid")
    try:
        body = raw.encode("utf-8")
    except UnicodeError:
        raise WSCollectionError("ws_message_encoding_invalid") from None
    if len(body) > policy.max_message_bytes:
        raise WSCollectionError("ws_frame_too_large")
    return body


async def collect_ws_reference(
    *,
    clock: Callable[[], datetime],
    report_id: str,
    instrument_id: str,
    policy: WSCollectionPolicy,
    barrier_completed_at: datetime | None = None,
) -> CollectedWSReference:
    """Perform a bounded, owned, public read once. Failure never grants authority."""
    try:
        checked = _policy_copy(policy)
        report = TypeAdapter(ReportId).validate_python(report_id, strict=True)
        if type(instrument_id) is not str or _INST.fullmatch(instrument_id) is None:
            raise WSCollectionError("ws_instrument_id_invalid")
        if not callable(clock):
            raise WSCollectionError("ws_clock_invalid")
        barrier = None if barrier_completed_at is None else _utc(barrier_completed_at)
        started = _utc(clock())
        if barrier is not None and started <= barrier:
            raise WSCollectionError("ws_publication_barrier_not_crossed")
        subscription_id = _subscription_id(report, instrument_id)
        body = _subscription(subscription_id, instrument_id)
        async with asyncio.timeout(checked.batch_timeout_seconds):
            socket = await _NoRedirectConnect(
                PUBLIC_WS_URL,
                proxy=None,
                compression=None,
                additional_headers=None,
                user_agent_header="CTCC-source-ws/1",
                open_timeout=checked.open_timeout_seconds,
                ping_interval=None,
                close_timeout=checked.close_timeout_seconds,
                max_size=checked.max_message_bytes,
                max_queue=1,
            )
            pending_cancel = False
            try:
                connected = _utc(clock())
                if connected < started:
                    raise WSCollectionError("ws_capture_clock_reversed")
                subscribe_started = _utc(clock())
                if subscribe_started < connected:
                    raise WSCollectionError("ws_capture_clock_reversed")
                await socket.send(body.decode("ascii"))
                subscribe_completed = _utc(clock())
                if subscribe_completed < subscribe_started:
                    raise WSCollectionError("ws_capture_clock_reversed")
                raw_ack = await _receive(socket, checked)
                ack = parse_ws_subscription_ack(
                    raw_ack,
                    instrument_id=instrument_id,
                    subscription_id=subscription_id,
                    received_at=_utc(clock()),
                )
                if ack.received_at < subscribe_completed:
                    raise WSCollectionError("ws_capture_clock_reversed")
                raw_ticker = await _receive(socket, checked)
                ticker = parse_ws_ticker_frame(
                    raw_ticker,
                    report_id=report,
                    instrument_id=instrument_id,
                    received_at=_utc(clock()),
                )
            except asyncio.CancelledError:
                pending_cancel = True
                raise
            finally:
                await _close_socket(socket, checked, pending_cancel=pending_cancel)
            closed = _utc(clock())
        completed = _utc(clock())
        values = {
            "report_id": report,
            "instrument_id": instrument_id,
            "endpoint": PUBLIC_WS_URL,
            "policy": checked,
            "barrier_completed_at": barrier,
            "connection_started_at": started,
            "connected_at": connected,
            "subscribe_started_at": subscribe_started,
            "subscribe_completed_at": subscribe_completed,
            "subscription_id": subscription_id,
            "subscription_body": body,
            "subscription_sha256": hashlib.sha256(body).hexdigest(),
            "ack": ack,
            "ticker": ticker,
            "closed_at": closed,
            "completed_at": completed,
            "execution_authority": False,
            "source_authenticity_verified": False,
            "socket_binding_verified": False,
        }
        return CollectedWSReference(**values, bundle_sha256=_digest(values))
    except WSCollectionError:
        raise
    except WSCaptureError:
        raise WSCollectionError("ws_wire_record_rejected") from None
    except TimeoutError:
        raise WSCollectionError("ws_transport_timeout") from None
    except Exception:  # noqa: BLE001 -- remote frames/errors must not enter logs
        raise WSCollectionError("ws_public_capture_failed") from None
