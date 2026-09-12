"""Bounded public OHLC pages; request claims are not source authentication.

OKX primary docs checked 2026-09-12:
https://app.okx.com/docs-v5/en/#order-book-trading-market-data-get-candlesticks
GET /api/v5/market/candles returns at most 300 rows per page, within its
latest 1440 records. ``after`` requests strictly older opening timestamps.
Rows are [ts,o,h,l,c,vol,volCcy,volCcyQuote,confirm]. For SWAP, volumes retain
contracts/base/quote units. Row ts is OPEN time, never response freshness.

Each first-page header receipt freezes that frame's expected closed tail;
batch completion does NOT advance it. No history is sorted, deduplicated,
filled, silently trimmed or relabelled as confirmed. Consumers must recheck
tail coverage at their actual decision clock and preserve original history.
The injected clock/client transport remain trusted dependencies, not attestations.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from itertools import pairwise
from typing import Annotated, Literal

import httpcore
import httpx
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    TypeAdapter,
    field_validator,
    model_validator,
)

from app.domain.market import Candle
from app.market.quality.candles import BAR_SECONDS
from app.trade_qualification.event_models import Digest
from app.trade_qualification.models import Price, QualificationModel, ReportId
from app.trade_qualification.quote_collector import (
    _INST,
    BASE_URL,
    QuoteCollectionError,
    _canonical,
    _close_response,
    _decimal,
    _public_client,
    _sha,
    _timestamp,
    _utc,
)

ENDPOINT = "/api/v5/market/candles"
TIMEFRAMES = ("4H", "1H", "15m", "5m")
MAX_RESPONSE_BYTES = 262144
Timeframe = Literal["4H", "1H", "15m", "5m"]
Instrument = Annotated[str, Field(pattern=r"^[A-Z0-9]{1,16}-[A-Z0-9]{1,16}-SWAP$")]
Count = Annotated[int, Field(ge=200, le=1024)]
Amount = Annotated[Decimal, Field(ge=0, max_digits=40, decimal_places=20)]
RawText = Annotated[
    str, StringConstraints(strip_whitespace=False), Field(max_length=MAX_RESPONSE_BYTES)
]
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class CandleCollectionError(ValueError):
    """A stable local code, without HTTP exception details or response bodies."""


def _plain(value):
    if isinstance(value, BaseModel):
        return {key: _plain(item) for key, item in value.__dict__.items()}
    if type(value) is tuple:
        return tuple(_plain(item) for item in value)
    if type(value) is dict:
        return {key: _plain(item) for key, item in value.items()}
    return value


def _guard(value, depth=0, budget=None):
    if budget is None:
        budget = [120000]
    budget[0] -= 1
    if depth > 18 or budget[0] < 0:
        raise CandleCollectionError("capture_traversal_limit")
    if isinstance(value, BaseModel):
        if type(value) not in {
            FrameRequest,
            CandleCollectionPolicy,
            CapturedCandle,
            CandlePage,
            FrameCapture,
            CollectedCandles,
        }:
            raise CandleCollectionError("capture_model_type_invalid")
        if (
            set(value.__dict__) != set(type(value).model_fields)
            or value.__pydantic_extra__
        ):
            raise CandleCollectionError("capture_model_fields_invalid")
        for name, part in value.__dict__.items():
            pattern = None
            if name.endswith("_sha256"):
                pattern = r"[a-f0-9]{64}"
            elif name == "report_id":
                pattern = r"[A-Za-z0-9][A-Za-z0-9_-]{0,95}"
            elif name == "instrument_id":
                pattern = r"[A-Z0-9]{1,16}-[A-Z0-9]{1,16}-SWAP"
            if pattern is not None and (
                type(part) is not str or re.fullmatch(pattern, part) is None
            ):
                raise CandleCollectionError("capture_raw_pin_invalid")
        bounds = {
            CandleCollectionPolicy: (("requests", 4, 4),),
            FrameCapture: (("pages", 1, 12), ("confirmed", 200, 1024)),
            CollectedCandles: (("frames", 4, 4), ("volume_units", 3, 3)),
            CandlePage: (("parameters", 3, 4),),
        }
        for name, minimum, maximum in bounds.get(type(value), ()):
            sequence = value.__dict__[name]
            if type(sequence) is not tuple or not minimum <= len(sequence) <= maximum:
                raise CandleCollectionError("capture_sequence_invalid")
        if type(value) is CandlePage and any(
            type(pair) is not tuple
            or len(pair) != 2
            or any(type(part) is not str or len(part) > 64 for part in pair)
            for pair in value.parameters
        ):
            raise CandleCollectionError("capture_parameters_invalid")
        for item in value.__dict__.values():
            _guard(item, depth + 1, budget)
    elif type(value) is dict:
        if len(value) > 64 or any(
            type(key) is not str or len(key) > 64 for key in value
        ):
            raise CandleCollectionError("capture_mapping_invalid")
        for item in value.values():
            _guard(item, depth + 1, budget)
    elif type(value) is tuple:
        if len(value) > 1024:
            raise CandleCollectionError("capture_sequence_invalid")
        for item in value:
            _guard(item, depth + 1, budget)
    elif type(value) in (str, bytes):
        if len(value) > MAX_RESPONSE_BYTES:
            raise CandleCollectionError("capture_string_invalid")
    elif type(value) is Decimal:
        if (
            not value.is_finite()
            or len(value.as_tuple().digits) > 40
            or abs(value.as_tuple().exponent) > 20
        ):
            raise CandleCollectionError("capture_decimal_invalid")
    elif type(value) is datetime:
        _utc(value)
    elif (
        value is None
        or type(value) is bool
        or (type(value) is int and abs(value) <= 10**15)
    ):
        pass
    else:
        raise CandleCollectionError("capture_value_invalid")


def _copy(value, expected):
    if type(value) is not expected:
        raise CandleCollectionError("capture_model_type_invalid")
    _guard(value)
    return expected.model_validate(_plain(value), strict=True)


def _hash(value, *, omit):
    _guard(value)
    raw = _canonical(value.model_dump(mode="json", round_trip=True, exclude={omit}))
    if len(raw.encode()) > 32 * 1024 * 1024:
        raise CandleCollectionError("capture_bundle_too_large")
    return _sha(raw.encode())


class FrameRequest(QualificationModel):
    timeframe: Timeframe
    requested_confirmed_bars: Count


class CandleCollectionPolicy(QualificationModel):
    requests: tuple[FrameRequest, ...] = Field(min_length=4, max_length=4)
    page_size: int = Field(default=300, ge=100, le=300)
    max_pages_per_frame: int = Field(default=4, ge=1, le=12)
    request_timeout_seconds: int = Field(default=2, ge=1, le=5)
    batch_timeout_seconds: int = Field(default=30, ge=1, le=60)
    max_response_bytes: int = Field(default=196608, ge=1024, le=MAX_RESPONSE_BYTES)

    @model_validator(mode="after")
    def bounded_requests(self):
        _guard(self)
        if tuple(item.timeframe for item in self.requests) != TIMEFRAMES:
            raise ValueError("exact_ordered_frame_requests_required")
        if any(
            item.requested_confirmed_bars > self.page_size * self.max_pages_per_frame
            for item in self.requests
        ):
            raise ValueError("page_budget_cannot_cover_request")
        return self


class CapturedCandle(QualificationModel):
    """Frozen storage DTO; never mutate the legacy Candle class globally."""

    timestamp: datetime
    open: Price
    high: Price
    low: Price
    close: Price
    volume_contracts: Amount
    volume_currency: Amount
    volume_quote: Amount
    confirmed: bool

    _time = field_validator("timestamp")(_utc)

    @model_validator(mode="after")
    def geometry(self):
        _guard(self)
        if self.high < max(self.open, self.close) or self.low > min(
            self.open, self.close
        ):
            raise ValueError("candle_ohlc_invalid")
        return self


class _NoAuthority(QualificationModel):
    model_config = ConfigDict(str_strip_whitespace=False)
    execution_authority: Literal[False] = False
    source_authenticity_verified: Literal[False] = False

    @field_validator(
        "execution_authority", "source_authenticity_verified", mode="before"
    )
    @classmethod
    def no_authority(cls, value):
        if type(value) is not bool or value is not False:
            raise ValueError("candle_capture_cannot_grant_authority")
        return value


class CandlePage(_NoAuthority):
    method: Literal["GET"] = "GET"
    origin: Literal["https://www.okx.com"] = "https://www.okx.com"
    endpoint: Literal["/api/v5/market/candles"] = "/api/v5/market/candles"
    instrument_id: Instrument
    timeframe: Timeframe
    page_index: int = Field(ge=0, le=11)
    after: Annotated[str, Field(pattern=r"^[1-9][0-9]{0,14}$")] | None
    limit: int = Field(ge=1, le=300)
    parameters: tuple[
        tuple[
            Annotated[str, Field(max_length=64)], Annotated[str, Field(max_length=64)]
        ],
        ...,
    ] = Field(min_length=3, max_length=4)
    request_started_at: datetime
    received_at: datetime
    completed_at: datetime
    response_body: bytes = Field(min_length=1, max_length=MAX_RESPONSE_BYTES)
    body_size_bytes: int = Field(ge=1, le=MAX_RESPONSE_BYTES)
    body_sha256: Digest
    canonical_json: RawText
    canonical_sha256: Digest
    row_count: int = Field(ge=1, le=300)
    timestamp_semantics: Literal["candle_open_not_response_freshness"] = (
        "candle_open_not_response_freshness"
    )
    response_identity_semantics: Literal["request_bound_no_identity_echo"] = (
        "request_bound_no_identity_echo"
    )

    _times = field_validator("request_started_at", "received_at", "completed_at")(_utc)

    @model_validator(mode="after")
    def source_consistency(self):
        _guard(self)
        if self.parameters != _parameters(
            self.instrument_id, self.timeframe, self.limit, self.after
        ):
            raise ValueError("candle_request_identity_mismatch")
        if not self.request_started_at <= self.received_at <= self.completed_at:
            raise ValueError("request_clock_reversed")
        rows, canonical = _parse(self.response_body)
        candles = _rows(rows, self.timeframe, self.received_at)
        if len(candles) != self.row_count or len(candles) > self.limit:
            raise ValueError("candle_page_cardinality_invalid")
        if self.after is not None and any(
            c.timestamp >= _timestamp(self.after) for c in candles
        ):
            raise ValueError("candle_after_cursor_violated")
        if (
            self.body_size_bytes != len(self.response_body)
            or self.body_sha256 != _sha(self.response_body)
            or self.canonical_json != canonical
            or self.canonical_sha256 != _sha(canonical.encode())
        ):
            raise ValueError("candle_page_digest_mismatch")
        return self


class FrameCapture(_NoAuthority):
    instrument_id: Instrument
    timeframe: Timeframe
    requested_count: Count
    policy: CandleCollectionPolicy
    pages: tuple[CandlePage, ...] = Field(min_length=1, max_length=12)
    confirmed: tuple[CapturedCandle, ...] = Field(min_length=200, max_length=1024)
    collected_until: datetime
    verified_through: datetime
    completed_at: datetime
    frame_sha256: Digest
    coverage_basis: Literal["first_page_headers_received"] = (
        "first_page_headers_received"
    )

    _times = field_validator("collected_until", "verified_through", "completed_at")(
        _utc
    )

    @model_validator(mode="after")
    def exact_coverage(self):
        _guard(self)
        requested = next(
            r.requested_confirmed_bars
            for r in self.policy.requests
            if r.timeframe == self.timeframe
        )
        if self.requested_count != requested:
            raise ValueError("frame_requested_count_mismatch")
        confirmed, until, through, completed = _reconstruct(
            self.pages, self.policy, self.timeframe, self.instrument_id, requested
        )
        if (
            self.confirmed,
            self.collected_until,
            self.verified_through,
            self.completed_at,
        ) != (confirmed, until, through, completed):
            raise ValueError("frame_source_reconstruction_mismatch")
        if self.frame_sha256 != _hash(self, omit="frame_sha256"):
            raise ValueError("frame_digest_mismatch")
        return self

    @property
    def candles(self) -> tuple[Candle, ...]:
        """Fresh exact-domain copies; callers cannot mutate the stored proof."""
        checked = _copy(self, FrameCapture)
        return tuple(
            Candle.model_validate(_plain(row), strict=True) for row in checked.confirmed
        )


class CollectedCandles(_NoAuthority):
    report_id: ReportId
    instrument_id: Instrument
    policy: CandleCollectionPolicy
    frames: tuple[FrameCapture, ...] = Field(min_length=4, max_length=4)
    barrier_completed_at: datetime | None
    completed_at: datetime
    bundle_sha256: Digest
    volume_units: tuple[
        Literal["contracts"], Literal["base_currency"], Literal["quote_currency"]
    ] = ("contracts", "base_currency", "quote_currency")
    complete_path_verified: Literal[False] = False
    execution_recheck_performed: Literal[False] = False

    _time = field_validator("completed_at")(_utc)

    @field_validator("barrier_completed_at")
    @classmethod
    def optional_clock(cls, value):
        return None if value is None else _utc(value)

    @field_validator(
        "complete_path_verified", "execution_recheck_performed", mode="before"
    )
    @classmethod
    def no_path_or_recheck(cls, value):
        return _NoAuthority.no_authority(value)

    @model_validator(mode="after")
    def exact_capture(self):
        _guard(self)
        if tuple(frame.timeframe for frame in self.frames) != TIMEFRAMES:
            raise ValueError("frame_order_invalid")
        previous = None
        for frame in self.frames:
            if frame.instrument_id != self.instrument_id or frame.policy != self.policy:
                raise ValueError("frame_identity_or_policy_mismatch")
            for page in frame.pages:
                if previous is not None and page.request_started_at < previous:
                    raise ValueError("batch_clock_reversed")
                if (
                    self.barrier_completed_at is not None
                    and page.request_started_at <= self.barrier_completed_at
                ):
                    raise ValueError("publication_barrier_not_crossed")
                previous = page.completed_at
        if self.completed_at < previous:
            raise ValueError("batch_clock_reversed")
        if self.completed_at - self.frames[0].pages[0].request_started_at > timedelta(
            seconds=self.policy.batch_timeout_seconds
        ):
            raise ValueError("batch_deadline_exceeded")
        if self.bundle_sha256 != _hash(self, omit="bundle_sha256"):
            raise ValueError("candle_bundle_digest_mismatch")
        return self


def _parameters(instrument, timeframe, limit, after):
    params = {"instId": instrument, "bar": timeframe, "limit": str(limit)}
    if after is not None:
        params["after"] = after
    return tuple(sorted(params.items()))


def _parse(body):
    if type(body) is not bytes or not 0 < len(body) <= MAX_RESPONSE_BYTES:
        raise CandleCollectionError("response_too_large")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise CandleCollectionError("duplicate_response_key")
            result[key] = value
        return result

    def invalid(value):
        raise CandleCollectionError("nonfinite_response_number")

    try:
        payload = json.loads(
            body.decode("utf-8"), object_pairs_hook=unique, parse_constant=invalid
        )
        if (
            type(payload) is not dict
            or set(payload) != {"code", "msg", "data"}
            or payload["code"] != "0"
            or type(payload["msg"]) is not str
            or len(payload["msg"]) > 512
        ):
            raise CandleCollectionError("response_schema_invalid")
        rows = payload["data"]
        if type(rows) is not list or not 1 <= len(rows) <= 300:
            raise CandleCollectionError("candle_page_cardinality_invalid")
        for row in rows:
            if (
                type(row) is not list
                or len(row) != 9
                or any(type(value) is not str or len(value) > 64 for value in row)
            ):
                raise CandleCollectionError("candle_row_schema_invalid")
        canonical = _canonical(payload)
        if len(canonical.encode()) > MAX_RESPONSE_BYTES:
            raise CandleCollectionError("canonical_response_too_large")
        return rows, canonical
    except CandleCollectionError:
        raise
    except (ValueError, UnicodeError, RecursionError, OverflowError):
        raise CandleCollectionError("response_json_invalid") from None


def _floor(at, timeframe):
    elapsed = at - _EPOCH
    seconds = elapsed.days * 86400 + elapsed.seconds
    return _EPOCH + timedelta(
        seconds=seconds // BAR_SECONDS[timeframe] * BAR_SECONDS[timeframe]
    )


def _rows(rows, timeframe, received):
    candles = []
    interval = timedelta(seconds=BAR_SECONDS[timeframe])
    for row in rows:
        opened = _timestamp(row[0])
        if opened != _floor(opened, timeframe):
            raise CandleCollectionError("candle_off_grid")
        if row[8] not in {"0", "1"}:
            raise CandleCollectionError("candle_confirmation_invalid")
        confirmed = row[8] == "1"
        if opened > received or (confirmed and opened + interval > received):
            raise CandleCollectionError("future_candle")
        if not confirmed and not opened <= received < opened + interval:
            raise CandleCollectionError("unconfirmed_candle_not_current")
        values = [
            _decimal({"value": value}, "value", positive=index < 4)
            for index, value in enumerate(row[1:8])
        ]
        candles.append(
            CapturedCandle(
                timestamp=opened,
                open=values[0],
                high=values[1],
                low=values[2],
                close=values[3],
                volume_contracts=values[4],
                volume_currency=values[5],
                volume_quote=values[6],
                confirmed=confirmed,
            )
        )
    if any(a.timestamp - b.timestamp != interval for a, b in pairwise(candles)):
        raise CandleCollectionError("candle_page_order_or_gap")
    return tuple(candles)


def _reconstruct(
    pages, policy, timeframe, instrument, requested, *, require_complete=True
):
    if not 1 <= len(pages) <= policy.max_pages_per_frame:
        raise CandleCollectionError("candle_page_budget_exceeded")
    result, previous_open, previous_completed = [], None, None
    until = pages[0].received_at
    through = _floor(until, timeframe)
    after = None
    for index, page in enumerate(pages):
        if (
            page.page_index != index
            or page.instrument_id != instrument
            or page.timeframe != timeframe
            or page.after != after
            or page.limit != min(policy.page_size, requested - len(result))
        ):
            raise CandleCollectionError("candle_page_chain_invalid")
        if (
            previous_completed is not None
            and page.request_started_at < previous_completed
        ):
            raise CandleCollectionError("page_clock_reversed")
        if (
            page.body_size_bytes > policy.max_response_bytes
            or page.completed_at - page.request_started_at
            > timedelta(seconds=policy.request_timeout_seconds)
        ):
            raise CandleCollectionError("page_capture_policy_exceeded")
        rows, _ = _parse(page.response_body)
        candles = _rows(rows, timeframe, page.received_at)
        for row_index, candle in enumerate(candles):
            if (
                previous_open is not None
                and previous_open - candle.timestamp
                != timedelta(seconds=BAR_SECONDS[timeframe])
            ):
                raise CandleCollectionError("candle_cross_page_gap_or_overlap")
            previous_open = candle.timestamp
            if not candle.confirmed:
                if index != 0 or row_index != 0:
                    raise CandleCollectionError("unconfirmed_candle_not_first")
            else:
                if (
                    candle.timestamp + timedelta(seconds=BAR_SECONDS[timeframe])
                    > through
                ):
                    raise CandleCollectionError(
                        "confirmed_candle_exceeds_capture_cutoff"
                    )
                result.append(candle)
        after = rows[-1][0]
        previous_completed = page.completed_at
    if len(result) > requested or (require_complete and len(result) != requested):
        raise CandleCollectionError("confirmed_coverage_incomplete")
    if (
        not result
        or result[0].timestamp + timedelta(seconds=BAR_SECONDS[timeframe]) != through
    ):
        raise CandleCollectionError("required_closed_tail_missing")
    return tuple(reversed(result)), until, through, previous_completed


def validate_collected_candles(value: CollectedCandles) -> CollectedCandles:
    """Strict bounded copy and raw-page replay, not authentication of actual IO."""
    try:
        return _copy(value, CollectedCandles)
    except (
        ValueError,
        TypeError,
        AttributeError,
        KeyError,
        ArithmeticError,
        RecursionError,
    ):
        raise CandleCollectionError("collected_candles_invalid") from None


def _isolated_client(client, *, require_empty_cookies=True):
    _public_client(client, require_empty_cookies=require_empty_cookies)
    # Reject known proxy/retry pools and mounted transports without reading or
    # disclosing any credential URLs. An injected MockTransport is still a
    # dependency, not attestation of actual TLS or network behavior.
    transport = client._transport
    if any(value is not None for value in client._mounts.values()) or type(
        transport
    ) not in (httpx.AsyncHTTPTransport, httpx.MockTransport):
        raise CandleCollectionError("public_client_transport_not_isolated")
    if type(transport) is httpx.AsyncHTTPTransport:
        if type(transport._pool) is not httpcore.AsyncConnectionPool:
            raise CandleCollectionError("public_client_transport_not_isolated")
        if type(transport._pool._retries) is not int or transport._pool._retries != 0:
            raise CandleCollectionError("public_client_transport_retries_forbidden")


async def _fetch(
    client, clock, instrument, timeframe, index, limit, after, policy, barrier, previous
):
    _isolated_client(client, require_empty_cookies=False)
    started = _utc(clock())
    if previous is not None and started < previous:
        raise CandleCollectionError("batch_clock_reversed")
    if barrier is not None and started <= barrier:
        raise CandleCollectionError("publication_barrier_not_crossed")
    parameters = _parameters(instrument, timeframe, limit, after)
    request = httpx.Request(
        "GET",
        BASE_URL + ENDPOINT,
        params=parameters,
        headers={
            "Accept": "application/json",
            "Accept-Encoding": "identity",
            "User-Agent": "CTCC-source-candles/1",
        },
        extensions={"timeout": httpx.Timeout(policy.request_timeout_seconds).as_dict()},
    )
    async with asyncio.timeout(policy.request_timeout_seconds):
        response = await client.send(
            request, auth=None, follow_redirects=False, stream=True
        )
        cancelled = False
        try:
            received = _utc(clock())
            if received < started:
                raise CandleCollectionError("request_clock_reversed")
            if (
                response.status_code != 200
                or response.url != request.url
                or response.history
                or response.is_closed
                or response.is_stream_consumed
            ):
                raise CandleCollectionError("http_response_rejected")
            if (
                response.headers.get("content-type", "")
                .split(";", 1)[0]
                .strip()
                .lower()
                != "application/json"
            ):
                raise CandleCollectionError("response_media_type_invalid")
            if (
                response.headers.get("content-encoding", "identity").lower()
                != "identity"
            ):
                raise CandleCollectionError("response_encoding_rejected")
            length = response.headers.get("content-length")
            if length is not None and (
                not length.isascii()
                or not length.isdigit()
                or len(length) > 10
                or int(length) > policy.max_response_bytes
            ):
                raise CandleCollectionError("response_too_large")
            body = bytearray()
            async for chunk in response.stream:
                if (
                    type(chunk) is not bytes
                    or len(body) + len(chunk) > policy.max_response_bytes
                ):
                    raise CandleCollectionError("response_too_large")
                body.extend(chunk)
        except asyncio.CancelledError:
            cancelled = True
            raise
        finally:
            await _close_response(response, pending_cancel=cancelled)
    completed = _utc(clock())
    if length is not None and int(length) != len(body):
        raise CandleCollectionError("response_length_mismatch")
    rows, canonical = _parse(bytes(body))
    return CandlePage(
        instrument_id=instrument,
        timeframe=timeframe,
        page_index=index,
        after=after,
        limit=limit,
        parameters=parameters,
        request_started_at=started,
        received_at=received,
        completed_at=completed,
        response_body=bytes(body),
        body_size_bytes=len(body),
        body_sha256=_sha(bytes(body)),
        canonical_json=canonical,
        canonical_sha256=_sha(canonical.encode()),
        row_count=len(rows),
    )


async def collect_candles(
    *,
    client: httpx.AsyncClient,
    clock: Callable[[], datetime],
    report_id: str,
    instrument_id: str,
    policy: CandleCollectionPolicy,
    barrier_completed_at: datetime | None = None,
) -> CollectedCandles:
    """GET only, no retry/fallback/repair; return all four complete frame proofs."""
    try:
        checked = _copy(policy, CandleCollectionPolicy)
        if (
            type(report_id) is not str
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,95}", report_id) is None
        ):
            raise CandleCollectionError("report_id_invalid")
        report = TypeAdapter(ReportId).validate_python(report_id, strict=True)
        if type(instrument_id) is not str or _INST.fullmatch(instrument_id) is None:
            raise CandleCollectionError("instrument_id_invalid")
        if not callable(clock):
            raise CandleCollectionError("clock_invalid")
        _isolated_client(client)
        barrier = None if barrier_completed_at is None else _utc(barrier_completed_at)
        frames, previous = [], None
        async with asyncio.timeout(checked.batch_timeout_seconds):
            for request in checked.requests:
                pages, count, after = [], 0, None
                while count < request.requested_confirmed_bars:
                    if len(pages) >= checked.max_pages_per_frame:
                        raise CandleCollectionError("candle_page_budget_exceeded")
                    page = await _fetch(
                        client,
                        clock,
                        instrument_id,
                        request.timeframe,
                        len(pages),
                        min(
                            checked.page_size, request.requested_confirmed_bars - count
                        ),
                        after,
                        checked,
                        barrier,
                        previous,
                    )
                    pages.append(page)
                    rows, _ = _parse(page.response_body)
                    prefix, _, _, _ = _reconstruct(
                        tuple(pages),
                        checked,
                        request.timeframe,
                        instrument_id,
                        request.requested_confirmed_bars,
                        require_complete=False,
                    )
                    count = len(prefix)
                    after, previous = rows[-1][0], page.completed_at
                confirmed, until, through, completed = _reconstruct(
                    tuple(pages),
                    checked,
                    request.timeframe,
                    instrument_id,
                    request.requested_confirmed_bars,
                )
                values = {
                    "instrument_id": instrument_id,
                    "timeframe": request.timeframe,
                    "requested_count": request.requested_confirmed_bars,
                    "policy": checked,
                    "pages": tuple(pages),
                    "confirmed": confirmed,
                    "collected_until": until,
                    "verified_through": through,
                    "completed_at": completed,
                }
                draft = FrameCapture.model_construct(**values, frame_sha256="0" * 64)
                frame = FrameCapture.model_validate(
                    _plain(
                        {**values, "frame_sha256": _hash(draft, omit="frame_sha256")}
                    ),
                    strict=True,
                )
                frames.append(frame)
        completed = _utc(clock())
        values = {
            "report_id": report,
            "instrument_id": instrument_id,
            "policy": checked,
            "frames": tuple(frames),
            "barrier_completed_at": barrier,
            "completed_at": completed,
        }
        draft = CollectedCandles.model_construct(**values, bundle_sha256="0" * 64)
        return CollectedCandles.model_validate(
            _plain({**values, "bundle_sha256": _hash(draft, omit="bundle_sha256")}),
            strict=True,
        )
    except CandleCollectionError:
        raise
    except QuoteCollectionError as exc:
        raise CandleCollectionError(str(exc)) from None
    except (httpx.HTTPError, TimeoutError):
        raise CandleCollectionError("public_candle_transport_failed") from None
    except (
        ValueError,
        TypeError,
        AttributeError,
        KeyError,
        ArithmeticError,
        RecursionError,
        RuntimeError,
    ):
        raise CandleCollectionError("public_candle_capture_invalid") from None
