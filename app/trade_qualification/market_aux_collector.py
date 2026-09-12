"""Bounded public books5 and SWAP open-interest records, never order authority.

OKX REST documentation checked 2026-09-12: https://app.okx.com/docs-v5/en/ .
Books ``ts`` is book generation time; open-interest ``ts`` is data-return time.
Book size and ``oi`` are contracts, ``oiCcy`` is currency, ``oiUsd`` is USD.
Books normally omit response identity: identity is the exact request, not an
invented response field. Single seqId observations do not establish continuity.

The clock and transport are dependencies, not authenticated evidence. Known
HTTPX proxy configurations, environment defaults, credentials and hooks are
rejected. MockTransport is supported for explicit synthetic tests only. This
module cannot attest an injected transport's behavior or TLS configuration.
Crossed/locked or incomplete books fail this conservative qualification policy;
that is not a claim that such books cannot occur during exchange pre-open.
There is no import-time IO, retry, redirect, fallback, or runtime wiring.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Annotated, Literal

import httpcore
import httpx
from pydantic import (
    BaseModel,
    Field,
    StringConstraints,
    TypeAdapter,
    field_validator,
    model_validator,
)

from app.domain.market import OrderBook, OrderBookLevel
from app.trade_qualification.event_models import Digest
from app.trade_qualification.models import Price, QualificationModel, ReportId
from app.trade_qualification.quote_collector import (
    BASE_URL,
    MAX_RESPONSE_BYTES,
    QuoteCollectionError,
    _canonical,
    _close_response,
    _decimal,
    _parse,
    _public_client,
    _sha,
    _timestamp,
    _utc,
)

ENDPOINTS = (
    ("books", "/api/v5/market/books"),
    ("open_interest", "/api/v5/public/open-interest"),
)
_INST = re.compile(r"[A-Z0-9]{1,16}-[A-Z0-9]{1,16}-SWAP")
_COUNT = re.compile(r"[1-9][0-9]{0,18}")
Instrument = Annotated[str, Field(pattern=r"^[A-Z0-9]{1,16}-[A-Z0-9]{1,16}-SWAP$")]
Amount = Annotated[Decimal, Field(ge=0, max_digits=40, decimal_places=20)]
RawText = Annotated[
    str, StringConstraints(strip_whitespace=False), Field(max_length=MAX_RESPONSE_BYTES)
]


class MarketAuxCollectionError(ValueError):
    """Stable codes without bodies, credentials or transport exception details."""


class MarketAuxCollectionPolicy(QualificationModel):
    max_age_seconds: int = Field(ge=1, le=60)
    request_timeout_seconds: int = Field(default=2, ge=1, le=5)
    batch_timeout_seconds: int = Field(default=4, ge=1, le=15)
    max_response_bytes: int = Field(default=32768, ge=1024, le=MAX_RESPONSE_BYTES)


class CapturedBookLevel(QualificationModel):
    price: Price
    size_contracts: Price
    deprecated_liquidated_orders: Literal[0] = 0
    order_count: int = Field(ge=1, le=10**19 - 1)

    @field_validator("deprecated_liquidated_orders", mode="before")
    @classmethod
    def exact_zero(cls, value):
        if type(value) is not int or value != 0:
            raise ValueError("deprecated_book_field_invalid")
        return value


class CapturedOrderBook(QualificationModel):
    instrument_id: Instrument
    bids: tuple[CapturedBookLevel, ...] = Field(min_length=1, max_length=5)
    asks: tuple[CapturedBookLevel, ...] = Field(min_length=1, max_length=5)
    generation_at: datetime
    seq_id: int | None = Field(default=None, ge=0, le=2**63 - 1)
    identity_binding: Literal["request"] = "request"
    size_unit: Literal["contracts"] = "contracts"

    _time = field_validator("generation_at")(_utc)

    @model_validator(mode="after")
    def geometry(self):
        if any(
            a.price <= b.price for a, b in zip(self.bids, self.bids[1:], strict=False)
        ):
            raise ValueError("bids_not_strictly_descending")
        if any(
            a.price >= b.price for a, b in zip(self.asks, self.asks[1:], strict=False)
        ):
            raise ValueError("asks_not_strictly_ascending")
        if self.bids[0].price >= self.asks[0].price:
            raise ValueError("crossed_or_locked_book")
        return self

    @property
    def order_book(self) -> OrderBook:
        """Fresh mutable legacy view; never store a domain object in this DTO."""
        checked = _copy(self, CapturedOrderBook)

        def levels(items):
            return [
                OrderBookLevel(
                    price=x.price,
                    size=x.size_contracts,
                    deprecated_liquidated_orders=x.deprecated_liquidated_orders,
                    order_count=x.order_count,
                )
                for x in items
            ]

        return OrderBook(
            instrument_id=checked.instrument_id,
            bids=levels(checked.bids),
            asks=levels(checked.asks),
            timestamp=checked.generation_at,
        )


class CapturedOpenInterest(QualificationModel):
    instrument_id: Instrument
    contracts: Amount
    currency: Amount
    usd: Amount | None
    returned_at: datetime
    identity_binding: Literal["request_and_response"] = "request_and_response"

    _time = field_validator("returned_at")(_utc)


class AuxEndpointObservation(QualificationModel):
    role: Literal["books", "open_interest"]
    method: Literal["GET"] = "GET"
    origin: Literal["https://www.okx.com"] = "https://www.okx.com"
    endpoint: Literal["/api/v5/market/books", "/api/v5/public/open-interest"]
    parameters: tuple[
        tuple[
            Annotated[str, Field(max_length=64)], Annotated[str, Field(max_length=64)]
        ],
        ...,
    ] = Field(min_length=2, max_length=2)
    instrument_id: Instrument
    request_started_at: datetime
    received_at: datetime  # headers, not another component's or batch clock
    completed_at: datetime  # body consumed and the sole response close completed
    ts_raw: Annotated[
        str,
        StringConstraints(strip_whitespace=False),
        Field(pattern=r"^[1-9][0-9]{0,14}$"),
    ]
    source_time: datetime
    timestamp_semantics: Literal["book_generation", "exchange_data_return"]
    identity_binding: Literal["request", "request_and_response"]
    response_body: bytes = Field(min_length=1, max_length=MAX_RESPONSE_BYTES)
    body_sha256: Digest
    body_size_bytes: int = Field(gt=0, le=MAX_RESPONSE_BYTES)
    canonical_json: RawText
    canonical_sha256: Digest

    _times = field_validator(
        "request_started_at", "received_at", "completed_at", "source_time"
    )(_utc)

    @model_validator(mode="after")
    def consistent_source(self):
        if (self.role, self.endpoint) not in ENDPOINTS:
            raise ValueError("endpoint_role_mismatch")
        if self.parameters != _parameters(self.role, self.instrument_id):
            raise ValueError("request_parameters_mismatch")
        if not self.request_started_at <= self.received_at <= self.completed_at:
            raise ValueError("request_clock_reversed")
        if self.source_time > self.received_at:
            raise ValueError("future_component_timestamp")
        if self.timestamp_semantics != _semantics(
            self.role
        ) or self.identity_binding != _binding(self.role):
            raise ValueError("source_semantics_mismatch")
        payload, canonical = _parse(self.response_body)
        row = _row(payload, self.role, self.instrument_id)
        _record(self.role, row, self.instrument_id)
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


class CollectedMarketAux(QualificationModel):
    report_id: ReportId
    instrument_id: Instrument
    book: CapturedOrderBook
    open_interest: CapturedOpenInterest
    provenance: tuple[AuxEndpointObservation, ...] = Field(min_length=2, max_length=2)
    policy: MarketAuxCollectionPolicy
    barrier_completed_at: datetime | None
    completed_at: datetime
    bundle_sha256: Digest
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
        if tuple(item.role for item in self.provenance) != ("books", "open_interest"):
            raise ValueError("component_order_mismatch")
        previous = None
        for item in self.provenance:
            if item.instrument_id != self.instrument_id:
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
        expected = tuple(
            _record(
                item.role,
                _row(_parse(item.response_body)[0], item.role, self.instrument_id),
                self.instrument_id,
            )
            for item in self.provenance
        )
        if (self.book, self.open_interest) != expected:
            raise ValueError("auxiliary_source_mismatch")
        if self.bundle_sha256 != _bundle_sha(
            self.report_id,
            self.instrument_id,
            self.book,
            self.open_interest,
            self.provenance,
            self.policy,
            self.barrier_completed_at,
            self.completed_at,
        ):
            raise ValueError("bundle_digest_mismatch")
        return self

    @property
    def order_book(self) -> OrderBook:
        return validate_collected_market_aux(self).book.order_book


def _parameters(role, instrument):
    return (
        (("instId", instrument), ("sz", "5"))
        if role == "books"
        else (("instId", instrument), ("instType", "SWAP"))
    )


def _semantics(role):
    return "book_generation" if role == "books" else "exchange_data_return"


def _binding(role):
    return "request" if role == "books" else "request_and_response"


def _row(payload, role, instrument):
    if (
        type(payload) is not dict
        or payload.get("code") != "0"
        or type(payload.get("msg")) is not str
    ):
        raise MarketAuxCollectionError("response_status_invalid")
    rows = payload.get("data")
    if type(rows) is not list or len(rows) != 1 or type(rows[0]) is not dict:
        raise MarketAuxCollectionError("response_cardinality_invalid")
    row = rows[0]
    for key, expected in (("instId", instrument), ("instType", "SWAP")):
        if (role == "open_interest" or key in row) and row.get(key) != expected:
            raise MarketAuxCollectionError("response_identity_mismatch")
    _timestamp(row.get("ts"))
    return row


def _levels(rows):
    if type(rows) is not list or not 1 <= len(rows) <= 5:
        raise MarketAuxCollectionError("book_level_count_invalid")
    result = []
    for row in rows:
        if type(row) is not list or len(row) != 4:
            raise MarketAuxCollectionError("book_level_shape_invalid")
        if type(row[2]) is not str or row[2] != "0":
            raise MarketAuxCollectionError("deprecated_book_field_invalid")
        if type(row[3]) is not str or _COUNT.fullmatch(row[3]) is None:
            raise MarketAuxCollectionError("book_order_count_invalid")
        result.append(
            CapturedBookLevel(
                price=_decimal({"v": row[0]}, "v", positive=True),
                size_contracts=_decimal({"v": row[1]}, "v", positive=True),
                order_count=int(row[3]),
            )
        )
    return tuple(result)


def _record(role, row, instrument):
    if role == "books":
        seq = row.get("seqId")
        if "seqId" in row and (type(seq) is not int or not 0 <= seq <= 2**63 - 1):
            raise MarketAuxCollectionError("book_sequence_invalid")
        return CapturedOrderBook(
            instrument_id=instrument,
            bids=_levels(row.get("bids")),
            asks=_levels(row.get("asks")),
            generation_at=_timestamp(row["ts"]),
            seq_id=seq,
        )
    values = {key: _decimal(row, key, positive=False) for key in ("oi", "oiCcy")}
    if "oiUsd" in row:
        values["oiUsd"] = _decimal(row, "oiUsd", positive=False)
    if any(value < 0 for value in values.values()):
        raise MarketAuxCollectionError("open_interest_negative")
    return CapturedOpenInterest(
        instrument_id=instrument,
        contracts=values["oi"],
        currency=values["oiCcy"],
        usd=values.get("oiUsd"),
        returned_at=_timestamp(row["ts"]),
    )


def _bundle_sha(
    report, instrument, book, interest, provenance, policy, barrier, completed
):
    return _sha(
        _canonical(
            {
                "report_id": report,
                "instrument_id": instrument,
                "book": book.model_dump(mode="json"),
                "open_interest": interest.model_dump(mode="json"),
                "provenance": [x.model_dump(mode="json") for x in provenance],
                "policy": policy.model_dump(mode="json"),
                "barrier_completed_at": None
                if barrier is None
                else barrier.isoformat(),
                "completed_at": completed.isoformat(),
            }
        ).encode("utf-8")
    )


_MODELS = (
    MarketAuxCollectionPolicy,
    CapturedBookLevel,
    CapturedOrderBook,
    CapturedOpenInterest,
    AuxEndpointObservation,
    CollectedMarketAux,
)


def _input(value, depth=0):
    """Bound dirty copies before any serializer or iterator can run."""
    if depth > 8:
        raise MarketAuxCollectionError("record_nesting_invalid")
    if isinstance(value, BaseModel):
        kind = type(value)
        if (
            kind not in _MODELS
            or set(value.__dict__) != set(kind.model_fields)
            or value.__pydantic_extra__
        ):
            raise MarketAuxCollectionError("record_model_invalid")
        return {key: _input(item, depth + 1) for key, item in value.__dict__.items()}
    if type(value) is tuple and len(value) <= 5:
        return tuple(_input(item, depth + 1) for item in value)
    if type(value) is str and len(value) <= MAX_RESPONSE_BYTES:
        return value
    if type(value) is bytes and len(value) <= MAX_RESPONSE_BYTES:
        return value
    if value is None or type(value) in (int, bool, Decimal, datetime):
        return value
    raise MarketAuxCollectionError("record_value_invalid")


def _copy(value, kind):
    if type(value) is not kind:
        raise MarketAuxCollectionError("record_type_invalid")
    return kind.model_validate(_input(value), strict=True)


def validate_collected_market_aux(value: CollectedMarketAux) -> CollectedMarketAux:
    """Rebuild all values from both raw bodies. Digests are not authentication."""
    try:
        return _copy(value, CollectedMarketAux)
    except (
        ValueError,
        TypeError,
        AttributeError,
        KeyError,
        ArithmeticError,
        RecursionError,
    ):
        raise MarketAuxCollectionError("collected_market_aux_invalid") from None


def _isolated_client(client, *, require_empty_cookies=True):
    _public_client(client, require_empty_cookies=require_empty_cookies)
    # The normal HTTPX pool class excludes HTTP/SOCKS proxies. Reject mounted
    # transports as well; do not inspect or disclose credential-bearing URLs.
    transport = client._transport
    if any(value is not None for value in client._mounts.values()) or type(
        transport
    ) not in (httpx.AsyncHTTPTransport, httpx.MockTransport):
        raise MarketAuxCollectionError("public_client_transport_not_isolated")
    if type(transport) is httpx.AsyncHTTPTransport:
        if type(transport._pool) is not httpcore.AsyncConnectionPool:
            raise MarketAuxCollectionError("public_client_transport_not_isolated")
        if type(transport._pool._retries) is not int or transport._pool._retries != 0:
            raise MarketAuxCollectionError("public_client_transport_retries_forbidden")


async def _collect_one(
    client, clock, role, path, instrument, policy, barrier, previous
):
    _isolated_client(client, require_empty_cookies=False)
    started = _utc(clock())
    if previous is not None and started < previous:
        raise MarketAuxCollectionError("batch_clock_reversed")
    if barrier is not None and started <= barrier:
        raise MarketAuxCollectionError("publication_barrier_not_crossed")
    params = _parameters(role, instrument)
    request = httpx.Request(
        "GET",
        BASE_URL + path,
        params=params,
        headers={
            "Accept": "application/json",
            "Accept-Encoding": "identity",
            "User-Agent": "CTCC-source-market-aux/1",
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
                raise MarketAuxCollectionError("request_clock_reversed")
            if (
                response.status_code != 200
                or response.url != request.url
                or response.history
                or response.is_closed
                or response.is_stream_consumed
            ):
                raise MarketAuxCollectionError("http_response_rejected")
            if (
                response.headers.get("content-type", "")
                .split(";", 1)[0]
                .strip()
                .lower()
                != "application/json"
            ):
                raise MarketAuxCollectionError("response_media_type_invalid")
            if (
                response.headers.get("content-encoding", "identity").lower()
                != "identity"
            ):
                raise MarketAuxCollectionError("response_encoding_rejected")
            length = response.headers.get("content-length")
            if length is not None and (
                not length.isascii()
                or not length.isdigit()
                or len(length) > 10
                or int(length) > policy.max_response_bytes
            ):
                raise MarketAuxCollectionError("response_too_large")
            body = bytearray()
            async for chunk in response.stream:
                if (
                    type(chunk) is not bytes
                    or len(body) + len(chunk) > policy.max_response_bytes
                ):
                    raise MarketAuxCollectionError("response_too_large")
                body.extend(chunk)
        except asyncio.CancelledError:
            pending_cancel = True
            raise
        finally:
            await _close_response(response, pending_cancel=pending_cancel)
    completed = _utc(clock())
    if length is not None and len(body) != int(length):
        raise MarketAuxCollectionError("response_length_mismatch")
    payload, canonical = _parse(bytes(body))
    row = _row(payload, role, instrument)
    observation = AuxEndpointObservation(
        role=role,
        endpoint=path,
        parameters=params,
        instrument_id=instrument,
        request_started_at=started,
        received_at=received,
        completed_at=completed,
        ts_raw=row["ts"],
        source_time=_timestamp(row["ts"]),
        timestamp_semantics=_semantics(role),
        identity_binding=_binding(role),
        response_body=bytes(body),
        body_sha256=_sha(bytes(body)),
        body_size_bytes=len(body),
        canonical_json=canonical,
        canonical_sha256=_sha(canonical.encode("utf-8")),
    )
    if completed - started > timedelta(seconds=policy.request_timeout_seconds):
        raise MarketAuxCollectionError("request_deadline_exceeded")
    if completed - observation.source_time > timedelta(seconds=policy.max_age_seconds):
        raise MarketAuxCollectionError("component_stale")
    return observation


async def collect_market_aux(
    *,
    client: httpx.AsyncClient,
    clock: Callable[[], datetime],
    report_id: str,
    instrument_id: str,
    policy: MarketAuxCollectionPolicy,
    barrier_completed_at: datetime | None = None,
) -> CollectedMarketAux:
    """Two fixed public GETs, once each, after the optional publication barrier."""
    try:
        checked_policy = _copy(policy, MarketAuxCollectionPolicy)
        report = TypeAdapter(ReportId).validate_python(report_id, strict=True)
        if type(instrument_id) is not str or _INST.fullmatch(instrument_id) is None:
            raise MarketAuxCollectionError("instrument_id_invalid")
        if not callable(clock):
            raise MarketAuxCollectionError("clock_invalid")
        _isolated_client(client)
        barrier = None if barrier_completed_at is None else _utc(barrier_completed_at)
        observations = []
        async with asyncio.timeout(checked_policy.batch_timeout_seconds):
            for role, path in ENDPOINTS:
                observations.append(
                    await _collect_one(
                        client,
                        clock,
                        role,
                        path,
                        instrument_id,
                        checked_policy,
                        barrier,
                        observations[-1].completed_at if observations else None,
                    )
                )
        completed = _utc(clock())
        provenance = tuple(observations)
        book, interest = tuple(
            _record(
                item.role,
                _row(_parse(item.response_body)[0], item.role, instrument_id),
                instrument_id,
            )
            for item in provenance
        )
        return CollectedMarketAux(
            report_id=report,
            instrument_id=instrument_id,
            book=book,
            open_interest=interest,
            provenance=provenance,
            policy=checked_policy,
            barrier_completed_at=barrier,
            completed_at=completed,
            bundle_sha256=_bundle_sha(
                report,
                instrument_id,
                book,
                interest,
                provenance,
                checked_policy,
                barrier,
                completed,
            ),
        )
    except MarketAuxCollectionError:
        raise
    except QuoteCollectionError as error:
        raise MarketAuxCollectionError(str(error)) from None
    except (httpx.HTTPError, TimeoutError):
        raise MarketAuxCollectionError("public_market_aux_transport_failed") from None
    except (
        ValueError,
        TypeError,
        AttributeError,
        KeyError,
        ArithmeticError,
        RecursionError,
        RuntimeError,
    ):
        raise MarketAuxCollectionError("public_market_aux_capture_invalid") from None
