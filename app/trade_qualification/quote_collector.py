"""Isolated OKX public GET capture; no account credentials or order integration.

OKX documents ticker ``ts`` as ticker generation time; REST mark ``ts`` and
funding ``ts`` are exchange data-return observations, NOT proof of when a value
last changed. Funding settlement/next-settlement times are never freshness.
Sources checked 2026-09-12:
https://app.okx.com/docs-v5/en/#public-data-rest-api-get-mark-price
https://www.okx.com/docs-v5/log_en/#2023-11-22
https://app.okx.com/docs-v5/en/#public-data-websocket-funding-rate-channel

Client transport/TLS and the injected UTC clock are trusted runtime dependencies,
not externally verifiable attestations. Known HTTPX proxy, mount and transport
retry configurations are rejected. An injected transport's actual behavior and
TLS configuration are still dependencies, not authenticated evidence.
No network call occurs on import. The
collector sends three fixed GETs only; tests use httpx.MockTransport exclusively.
An initially cookie-free client may receive anonymous server cookies. They are
never sent by these explicit requests; a later capture must use a fresh client.
SWAP bid/ask sizes retain native CONTRACT units, not base units or fill promises.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Literal

import httpcore
import httpx
from pydantic import (
    Field,
    StringConstraints,
    TypeAdapter,
    field_validator,
    model_validator,
)

from app.trade_qualification.event_models import Digest
from app.trade_qualification.location import ExecutableQuote
from app.trade_qualification.models import QualificationModel, ReportId, require_aware

BASE_URL = "https://www.okx.com"
ENDPOINTS = (
    ("ticker", "/api/v5/market/ticker"),
    ("mark", "/api/v5/public/mark-price"),
    ("funding", "/api/v5/public/funding-rate"),
)
MAX_RESPONSE_BYTES = 65536
_INST = re.compile(r"[A-Z0-9]{1,16}-[A-Z0-9]{1,16}-SWAP")
_DECIMAL = re.compile(r"-?(?:0|[1-9][0-9]{0,19})(?:\.[0-9]{1,20})?")
_TS = re.compile(r"[1-9][0-9]{0,14}")
_JSON_TEXT = Annotated[
    str, StringConstraints(strip_whitespace=False), Field(max_length=MAX_RESPONSE_BYTES)
]


class QuoteCollectionError(ValueError):
    """Stable local code only; never expose response bodies or HTTP exceptions."""


def _utc(value):
    if type(value) is not datetime:
        raise QuoteCollectionError("clock_invalid")
    try:
        return require_aware(value)
    except ValueError:
        raise QuoteCollectionError("clock_invalid") from None


class QuoteCollectionPolicy(QualificationModel):
    max_age_seconds: int = Field(ge=1, le=60)
    request_timeout_seconds: int = Field(default=2, ge=1, le=5)
    batch_timeout_seconds: int = Field(default=6, ge=1, le=15)
    max_response_bytes: int = Field(default=32768, ge=1024, le=MAX_RESPONSE_BYTES)


class EndpointObservation(QualificationModel):
    role: Literal["ticker", "mark", "funding"]
    method: Literal["GET"] = "GET"
    origin: Literal["https://www.okx.com"] = "https://www.okx.com"
    parameters: tuple[
        tuple[
            Annotated[str, Field(max_length=64)], Annotated[str, Field(max_length=64)]
        ],
        ...,
    ] = Field(min_length=1, max_length=2)
    endpoint: Literal[
        "/api/v5/market/ticker",
        "/api/v5/public/mark-price",
        "/api/v5/public/funding-rate",
    ]
    instrument_id: Annotated[
        str, Field(pattern=r"^[A-Z0-9]{1,16}-[A-Z0-9]{1,16}-SWAP$")
    ]
    request_started_at: datetime
    received_at: datetime  # response headers received; not a merged batch clock
    completed_at: datetime  # bounded body consumed and response closed
    ts_raw: Annotated[
        str,
        StringConstraints(strip_whitespace=False),
        Field(pattern=r"^[1-9][0-9]{0,14}$"),
    ]
    source_time: datetime
    timestamp_semantics: Literal["ticker_generation", "exchange_data_return"]
    response_body: bytes = Field(min_length=1, max_length=MAX_RESPONSE_BYTES)
    body_sha256: Digest
    body_size_bytes: int = Field(gt=0, le=MAX_RESPONSE_BYTES)
    canonical_json: _JSON_TEXT
    canonical_sha256: Digest

    _times = field_validator(
        "request_started_at", "received_at", "completed_at", "source_time"
    )(_utc)

    @model_validator(mode="after")
    def consistent_source(self):
        _observation_preflight(self)
        if (self.role, self.endpoint) not in ENDPOINTS:
            raise ValueError("endpoint_role_mismatch")
        expected_params = (("instId", self.instrument_id),)
        if self.role == "mark":
            expected_params += (("instType", "SWAP"),)
        if self.parameters != expected_params:
            raise ValueError("request_parameters_mismatch")
        if not self.request_started_at <= self.received_at <= self.completed_at:
            raise ValueError("request_clock_reversed")
        if self.source_time > self.received_at:
            raise ValueError("future_component_timestamp")
        expected = (
            "ticker_generation" if self.role == "ticker" else "exchange_data_return"
        )
        if self.timestamp_semantics != expected:
            raise ValueError("timestamp_semantics_mismatch")
        payload, canonical = _parse(self.response_body)
        row = _row(payload, self.instrument_id)
        _values(self.role, row)
        if row["ts"] != self.ts_raw or _timestamp(self.ts_raw) != self.source_time:
            raise ValueError("timestamp_source_mismatch")
        if self.body_size_bytes != len(self.response_body) or self.body_sha256 != _sha(
            self.response_body
        ):
            raise ValueError("wire_body_digest_mismatch")
        if self.canonical_json != canonical or self.canonical_sha256 != _sha(
            canonical.encode("utf-8")
        ):
            raise ValueError("canonical_body_digest_mismatch")
        return self


class CollectedQuote(QualificationModel):
    quote: ExecutableQuote
    provenance: tuple[EndpointObservation, ...] = Field(min_length=3, max_length=3)
    policy: QuoteCollectionPolicy
    barrier_completed_at: datetime | None
    completed_at: datetime
    bundle_sha256: Digest
    size_unit: Literal["contracts"] = "contracts"
    execution_authority: Literal[False] = False
    source_authenticity_verified: Literal[False] = False

    _time = field_validator("completed_at")(_utc)

    @field_validator("barrier_completed_at")
    @classmethod
    def barrier_time(cls, value):
        return None if value is None else _utc(value)

    @field_validator(
        "execution_authority", "source_authenticity_verified", mode="before"
    )
    @classmethod
    def no_authority(cls, value):
        if type(value) is not bool or value is not False:
            raise ValueError("collector_cannot_grant_authority")
        return value

    @model_validator(mode="after")
    def consistent_capture(self):
        _capture_preflight(self)
        if tuple(item.role for item in self.provenance) != (
            "ticker",
            "mark",
            "funding",
        ):
            raise ValueError("component_order_mismatch")
        previous = None
        for item in self.provenance:
            if item.instrument_id != self.quote.instrument_id:
                raise ValueError("component_identity_mismatch")
            if previous is not None and item.request_started_at < previous:
                raise ValueError("batch_clock_reversed")
            previous = item.completed_at
            if (
                self.barrier_completed_at is not None
                and item.request_started_at <= self.barrier_completed_at
            ):
                raise ValueError("publication_barrier_not_crossed")
            if item.body_size_bytes > self.policy.max_response_bytes:
                raise ValueError("response_too_large")
            if item.completed_at - item.request_started_at > timedelta(
                seconds=self.policy.request_timeout_seconds
            ):
                raise ValueError("request_deadline_exceeded")
            if self.completed_at - item.source_time > timedelta(
                seconds=self.policy.max_age_seconds
            ):
                raise ValueError("component_stale")
        if previous is None or self.completed_at < previous:
            raise ValueError("batch_clock_reversed")
        if self.completed_at - self.provenance[0].request_started_at > timedelta(
            seconds=self.policy.batch_timeout_seconds
        ):
            raise ValueError("batch_deadline_exceeded")
        if self.quote != _quote(
            self.quote.report_id, self.quote.instrument_id, self.provenance
        ):
            raise ValueError("quote_source_mismatch")
        if self.bundle_sha256 != _bundle_sha(
            self.quote,
            self.provenance,
            self.policy,
            self.barrier_completed_at,
            self.completed_at,
        ):
            raise ValueError("bundle_digest_mismatch")
        return self


def _sha(body):
    return hashlib.sha256(body).hexdigest()


def _canonical(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _tree(value, depth=0):
    if depth > 8:
        raise QuoteCollectionError("response_nesting_exceeded")
    if isinstance(value, dict):
        if len(value) > 128 or any(
            type(key) is not str or len(key) > 128 for key in value
        ):
            raise QuoteCollectionError("response_object_too_large")
        for item in value.values():
            _tree(item, depth + 1)
    elif isinstance(value, list):
        if len(value) > 128:
            raise QuoteCollectionError("response_array_too_large")
        for item in value:
            _tree(item, depth + 1)
    elif isinstance(value, str) and len(value) > 4096:
        raise QuoteCollectionError("response_string_too_large")
    elif (
        isinstance(value, float) and (not math.isfinite(value) or abs(value) > 1e40)
    ) or (type(value) is int and abs(value) > 10**40):
        raise QuoteCollectionError("response_number_invalid")


def _parse(body):
    if type(body) is not bytes or not 0 < len(body) <= MAX_RESPONSE_BYTES:
        raise QuoteCollectionError("response_too_large")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise QuoteCollectionError("duplicate_response_key")
            result[key] = value
        return result

    def invalid(_value):
        raise QuoteCollectionError("nonfinite_response_number")

    try:
        payload = json.loads(
            body.decode("utf-8"), object_pairs_hook=unique, parse_constant=invalid
        )
        _tree(payload)
        canonical = _canonical(payload)
        if len(canonical.encode("utf-8")) > MAX_RESPONSE_BYTES:
            raise QuoteCollectionError("canonical_response_too_large")
        return payload, canonical
    except QuoteCollectionError:
        raise
    except (ValueError, UnicodeError, RecursionError, OverflowError):
        raise QuoteCollectionError("response_json_invalid") from None


def _timestamp(raw):
    if type(raw) is not str or _TS.fullmatch(raw) is None:
        raise QuoteCollectionError("source_timestamp_invalid")
    try:
        return datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=int(raw))
    except (ValueError, OverflowError):
        raise QuoteCollectionError("source_timestamp_invalid") from None


def _decimal(row, field, *, positive):
    raw = row.get(field)
    if type(raw) is not str or _DECIMAL.fullmatch(raw) is None:
        raise QuoteCollectionError("source_decimal_invalid")
    value = Decimal(raw)
    if positive and value <= 0:
        raise QuoteCollectionError("source_decimal_nonpositive")
    return value


def _row(payload, instrument_id):
    if (
        type(payload) is not dict
        or payload.get("code") != "0"
        or type(payload.get("msg")) is not str
    ):
        raise QuoteCollectionError("response_status_invalid")
    rows = payload.get("data")
    if type(rows) is not list or len(rows) != 1 or type(rows[0]) is not dict:
        raise QuoteCollectionError("response_cardinality_invalid")
    row = rows[0]
    if row.get("instId") != instrument_id or row.get("instType") != "SWAP":
        raise QuoteCollectionError("response_identity_mismatch")
    _timestamp(row.get("ts"))
    return row


def _values(role, row):
    fields = {
        "ticker": ("bidPx", "askPx", "bidSz", "askSz"),
        "mark": ("markPx",),
        "funding": ("fundingRate",),
    }[role]
    values = tuple(_decimal(row, field, positive=role != "funding") for field in fields)
    if role == "ticker" and values[0] > values[1]:
        raise QuoteCollectionError("crossed_executable_quote")
    return values


def _quote(report_id, instrument_id, provenance):
    ticker, mark, funding = provenance
    values = [
        _values(item.role, _row(_parse(item.response_body)[0], instrument_id))
        for item in provenance
    ]
    return ExecutableQuote(
        report_id=report_id,
        instrument_id=instrument_id,
        source="rest",
        bid=values[0][0],
        ask=values[0][1],
        bid_size=values[0][2],
        ask_size=values[0][3],
        mark_price=values[1][0],
        funding_rate=values[2][0],
        quote_time=ticker.source_time,
        mark_time=mark.source_time,
        funding_time=funding.source_time,
        received_at=max(item.completed_at for item in provenance),
        request_started_at=min(item.request_started_at for item in provenance),
    )


def _bundle_sha(quote, provenance, policy, barrier, completed):
    return _sha(
        _canonical(
            {
                "quote": quote.model_dump(mode="json", round_trip=True),
                "provenance": [
                    item.model_dump(mode="json", round_trip=True) for item in provenance
                ],
                "policy": policy.model_dump(mode="json", round_trip=True),
                "barrier_completed_at": barrier.isoformat() if barrier else None,
                "completed_at": completed.isoformat(),
            }
        ).encode("utf-8")
    )


def validate_collected_quote(value: CollectedQuote) -> CollectedQuote:
    """Revalidate every byte-derived value; hashes cannot authenticate a client."""
    try:
        _capture_preflight(value)
        # No serializer is allowed to turn an invalid raw value into a valid
        # field before validation. The complete, bounded tree is checked above.
        raw = dict(value.__dict__)
        raw["quote"] = dict(value.quote.__dict__)
        raw["policy"] = dict(value.policy.__dict__)
        raw["provenance"] = tuple(dict(item.__dict__) for item in value.provenance)
        return CollectedQuote.model_validate(raw, strict=True)
    except (
        ValueError,
        TypeError,
        AttributeError,
        KeyError,
        ArithmeticError,
        RecursionError,
    ):
        raise QuoteCollectionError("collected_quote_invalid") from None


def _model_preflight(value, expected):
    if type(value) is not expected:
        raise ValueError("quote_record_type_invalid")
    if set(value.__dict__) != set(expected.model_fields) or value.__pydantic_extra__:
        raise ValueError("quote_record_fields_invalid")


def _text_preflight(value, limit):
    if type(value) is not str or not 0 < len(value) <= limit:
        raise ValueError("quote_record_text_invalid")


def _int_preflight(value, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError("quote_record_integer_invalid")


def _decimal_preflight(value):
    if type(value) is not Decimal or not value.is_finite():
        raise ValueError("quote_record_decimal_invalid")
    parts = value.as_tuple()
    if len(parts.digits) > 40 or not -20 <= parts.exponent <= 40:
        raise ValueError("quote_record_decimal_bounds_invalid")


def _policy_preflight(value):
    _model_preflight(value, QuoteCollectionPolicy)
    for name, minimum, maximum in (
        ("max_age_seconds", 1, 60),
        ("request_timeout_seconds", 1, 5),
        ("batch_timeout_seconds", 1, 15),
        ("max_response_bytes", 1024, MAX_RESPONSE_BYTES),
    ):
        _int_preflight(value.__dict__[name], minimum, maximum)


def _quote_preflight(value):
    _model_preflight(value, ExecutableQuote)
    for name, limit in (("report_id", 96), ("instrument_id", 512), ("source", 4)):
        _text_preflight(value.__dict__[name], limit)
    for name in ("bid", "ask", "mark_price", "bid_size", "ask_size", "funding_rate"):
        _decimal_preflight(value.__dict__[name])
    for name in ("quote_time", "mark_time", "funding_time", "received_at"):
        _utc(value.__dict__[name])
    if value.request_started_at is not None:
        _utc(value.request_started_at)


def _capture_preflight(value):
    _model_preflight(value, CollectedQuote)
    _text_preflight(value.bundle_sha256, 64)
    _text_preflight(value.size_unit, 16)
    for name in ("execution_authority", "source_authenticity_verified"):
        part = value.__dict__[name]
        if type(part) is not bool or part is not False:
            raise ValueError("collector_cannot_grant_authority")
    _utc(value.completed_at)
    if value.barrier_completed_at is not None:
        _utc(value.barrier_completed_at)
    _policy_preflight(value.policy)
    _quote_preflight(value.quote)
    if type(value.provenance) is not tuple or len(value.provenance) != 3:
        raise QuoteCollectionError("provenance_cardinality_invalid")
    for item in value.provenance:
        _observation_preflight(item)


def _observation_preflight(item):
    # Reject dirty model_copy payloads before model_dump can consume an iterator
    # or amplify arbitrarily large nested values. Schema validation still follows.
    _model_preflight(item, EndpointObservation)
    if type(item.parameters) is not tuple or not 1 <= len(item.parameters) <= 2:
        raise QuoteCollectionError("parameters_shape_invalid")
    for pair in item.parameters:
        if type(pair) is not tuple or len(pair) != 2:
            raise QuoteCollectionError("parameters_shape_invalid")
        if any(type(part) is not str or len(part) > 64 for part in pair):
            raise QuoteCollectionError("parameters_shape_invalid")
    if (
        type(item.response_body) is not bytes
        or not 0 < len(item.response_body) <= MAX_RESPONSE_BYTES
        or type(item.canonical_json) is not str
        or not 0 < len(item.canonical_json) <= MAX_RESPONSE_BYTES
    ):
        raise QuoteCollectionError("observation_body_shape_invalid")
    _int_preflight(item.body_size_bytes, 1, MAX_RESPONSE_BYTES)
    for name in ("request_started_at", "received_at", "completed_at", "source_time"):
        _utc(item.__dict__[name])
    for name in (
        "role",
        "method",
        "origin",
        "endpoint",
        "instrument_id",
        "ts_raw",
        "timestamp_semantics",
        "body_sha256",
        "canonical_sha256",
    ):
        part = getattr(item, name)
        if type(part) is not str or len(part) > 64:
            raise QuoteCollectionError("observation_text_shape_invalid")


def _public_client(client, *, require_empty_cookies=True):
    if type(client) is not httpx.AsyncClient or client.is_closed:
        raise QuoteCollectionError("public_client_invalid")
    # Explicit Request objects below avoid merging headers, cookies or query
    # defaults. Reject credential-bearing/shared clients anyway, before any IO.
    # HTTPX send(Request) sends as-is, unlike build_request. Its response handler
    # may populate the jar, but no request here merges or clears that jar.
    allowed = {"accept", "accept-encoding", "connection", "user-agent"}
    if (
        client.trust_env
        or client.auth is not None
        or (require_empty_cookies and client.cookies)
        or client.params
        or any(key.lower() not in allowed for key in client.headers)
        or any(client.event_hooks.values())
    ):
        raise QuoteCollectionError("public_client_not_isolated")
    # A caller-supplied HTTPX transport can otherwise retry or use a proxy even
    # with trust_env=False. Check configuration before any send, without reading
    # credential-bearing URLs. MockTransport remains an explicit test dependency.
    transport = client._transport
    if any(value is not None for value in client._mounts.values()) or type(
        transport
    ) not in (httpx.AsyncHTTPTransport, httpx.MockTransport):
        raise QuoteCollectionError("public_client_transport_not_isolated")
    if type(transport) is httpx.AsyncHTTPTransport:
        if type(transport._pool) is not httpcore.AsyncConnectionPool:
            raise QuoteCollectionError("public_client_transport_not_isolated")
        if type(transport._pool._retries) is not int or transport._pool._retries != 0:
            raise QuoteCollectionError("public_client_transport_retries_forbidden")


async def _close_response(response, *, pending_cancel=False):
    """Complete a single shielded close, preserving cancellation after cleanup.

    HTTPX sets is_closed before awaiting its stream; retrying response.aclose after
    interruption can therefore be a no-op. The caller reads the raw stream directly
    and gives this sole close task at most one additional second. A broken transport
    that cannot close within that bound fails capture; it never produces a quote.
    """

    async def close_once():
        async with asyncio.timeout(1):
            await response.aclose()

    task = asyncio.create_task(close_once())
    interrupted = pending_cancel
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            interrupted = True
        except Exception:  # noqa: BLE001 -- isolate arbitrary transport cleanup failure
            break
    failure = False
    try:
        task.result()
    except asyncio.CancelledError:
        interrupted = True
    except Exception:  # noqa: BLE001 -- cancellation must survive any cleanup failure
        failure = True
    if interrupted:
        raise asyncio.CancelledError
    if failure:
        raise QuoteCollectionError("response_cleanup_failed") from None


async def _collect_one(
    client, clock, role, path, instrument, policy, barrier, previous
):
    _public_client(client, require_empty_cookies=False)
    started = _utc(clock())
    if previous is not None and started < previous:
        raise QuoteCollectionError("batch_clock_reversed")
    if barrier is not None and started <= barrier:
        raise QuoteCollectionError("publication_barrier_not_crossed")
    params = {"instId": instrument}
    if role == "mark":
        params["instType"] = "SWAP"
    request = httpx.Request(
        "GET",
        BASE_URL + path,
        params=params,
        headers={
            "Accept": "application/json",
            "Accept-Encoding": "identity",
            "User-Agent": "CTCC-source-quote/1",
        },
        extensions={"timeout": httpx.Timeout(policy.request_timeout_seconds).as_dict()},
    )
    async with asyncio.timeout(policy.request_timeout_seconds):
        response = await client.send(
            request, auth=None, follow_redirects=False, stream=True
        )
        pending_cancel = False
        try:
            received = _utc(clock())
            if received < started:
                raise QuoteCollectionError("request_clock_reversed")
            if (
                response.status_code != 200
                or response.url != request.url
                or response.history
                or response.is_closed
                or response.is_stream_consumed
            ):
                raise QuoteCollectionError("http_response_rejected")
            if (
                response.headers.get("content-type", "")
                .split(";", 1)[0]
                .strip()
                .lower()
                != "application/json"
            ):
                raise QuoteCollectionError("response_media_type_invalid")
            if (
                response.headers.get("content-encoding", "identity").lower()
                != "identity"
            ):
                raise QuoteCollectionError("response_encoding_rejected")
            length = response.headers.get("content-length")
            if length is not None and (
                not length.isascii()
                or not length.isdigit()
                or len(length) > 10
                or int(length) > policy.max_response_bytes
            ):
                raise QuoteCollectionError("response_too_large")
            body = bytearray()
            # Do not use aiter_raw's automatic EOF close: cancellation inside
            # that close would set is_closed without finishing stream cleanup.
            async for chunk in response.stream:
                if len(body) + len(chunk) > policy.max_response_bytes:
                    raise QuoteCollectionError("response_too_large")
                body.extend(chunk)
        except asyncio.CancelledError:
            # Cancellation already delivered during body IO will not be raised
            # again inside cleanup. Carry it explicitly so even a failed close
            # cannot replace the caller's cancellation with an ordinary error.
            pending_cancel = True
            raise
        finally:
            await _close_response(response, pending_cancel=pending_cancel)
    completed = _utc(clock())
    if length is not None and len(body) != int(length):
        raise QuoteCollectionError("response_length_mismatch")
    payload, canonical = _parse(bytes(body))
    row = _row(payload, instrument)
    _values(role, row)
    return EndpointObservation(
        role=role,
        endpoint=path,
        parameters=tuple(sorted(params.items())),
        instrument_id=instrument,
        request_started_at=started,
        received_at=received,
        completed_at=completed,
        ts_raw=row["ts"],
        source_time=_timestamp(row["ts"]),
        timestamp_semantics="ticker_generation"
        if role == "ticker"
        else "exchange_data_return",
        response_body=bytes(body),
        body_sha256=_sha(bytes(body)),
        body_size_bytes=len(body),
        canonical_json=canonical,
        canonical_sha256=_sha(canonical.encode("utf-8")),
    )


async def collect_executable_quote(
    *,
    client: httpx.AsyncClient,
    clock: Callable[[], datetime],
    report_id: str,
    instrument_id: str,
    policy: QuoteCollectionPolicy,
    barrier_completed_at: datetime | None = None,
) -> CollectedQuote:
    """Capture three public observations once. No retry, fallback, or authority."""
    try:
        _policy_preflight(policy)
        checked_policy = QuoteCollectionPolicy.model_validate(
            dict(policy.__dict__), strict=True
        )
        if type(report_id) is not str:
            raise ValueError("report_id_invalid")
        report = TypeAdapter(ReportId).validate_python(report_id, strict=True)
        if type(instrument_id) is not str or _INST.fullmatch(instrument_id) is None:
            raise QuoteCollectionError("instrument_id_invalid")
        if not callable(clock):
            raise QuoteCollectionError("clock_invalid")
        _public_client(client)
        barrier = None if barrier_completed_at is None else _utc(barrier_completed_at)
        observations = []
        async with asyncio.timeout(checked_policy.batch_timeout_seconds):
            for role, path in ENDPOINTS:
                item = await _collect_one(
                    client,
                    clock,
                    role,
                    path,
                    instrument_id,
                    checked_policy,
                    barrier,
                    observations[-1].completed_at if observations else None,
                )
                observations.append(item)
        completed = _utc(clock())
        provenance = tuple(observations)
        quote = _quote(report, instrument_id, provenance)
        return CollectedQuote(
            quote=quote,
            provenance=provenance,
            policy=checked_policy,
            barrier_completed_at=barrier,
            completed_at=completed,
            bundle_sha256=_bundle_sha(
                quote, provenance, checked_policy, barrier, completed
            ),
        )
    except QuoteCollectionError:
        raise
    except (httpx.HTTPError, TimeoutError):
        raise QuoteCollectionError("public_quote_transport_failed") from None
    except (
        ValueError,
        TypeError,
        AttributeError,
        KeyError,
        ArithmeticError,
        RecursionError,
        RuntimeError,
    ):
        raise QuoteCollectionError("public_quote_capture_invalid") from None
