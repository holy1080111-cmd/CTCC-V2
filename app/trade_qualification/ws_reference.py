"""Bounded OKX public WS ticker/ack parsing, without opening any connection.

The ticker source timestamp is the exchange's ``ts`` generation time. Receipt
time is supplied by the caller and cannot replace a missing source timestamp.
Acknowledgements have no exchange source timestamp; pushes have no connId.
Neither a hash nor parsing a WS-shaped document proves it came from a socket.
The collector must observe acknowledgement and push ordering on its own actual
connection. Freshness and the publication/request barrier also belong there.
``raw_frame`` denotes the UTF-8 application-message payload, not WebSocket
framing, TLS wire bytes, a handshake transcript or authenticated provenance.

SWAP sizes are native contracts, not base units or guaranteed executable size.
Only the documented tickers schema is consumed. Optional ticker statistics are
retained, but they do not supply mark/funding data, account facts or authority.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, field_validator, model_validator

from app.trade_qualification.data import WSReferenceObservation
from app.trade_qualification.models import Price, QualificationModel, require_aware
from app.trade_qualification.quote_collector import (
    MAX_RESPONSE_BYTES,
    QuoteCollectionError,
    _decimal,
    _parse,
    _timestamp,
)

MAX_FRAME_BYTES = MAX_RESPONSE_BYTES
_INSTRUMENT = re.compile(r"[A-Z0-9]{1,16}-[A-Z0-9]{1,16}-SWAP")
_REPORT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,95}")
_SUBSCRIPTION = re.compile(r"[A-Za-z0-9]{1,32}")
_CONNECTION = re.compile(r"[A-Za-z0-9]{1,64}")
_REQUIRED_TICKER_FIELDS = frozenset(
    {"instType", "instId", "bidPx", "askPx", "bidSz", "askSz", "ts"}
)
OPTIONAL_TICKER_FIELDS = frozenset(
    {
        "last",
        "lastSz",
        "open24h",
        "high24h",
        "low24h",
        "volCcy24h",
        "vol24h",
        "sodUtc0",
        "sodUtc8",
    }
)
_JSON = Annotated[
    str,
    StringConstraints(strip_whitespace=False),
    Field(min_length=1, max_length=MAX_FRAME_BYTES),
]
_INSTRUMENT_ID = Annotated[
    str,
    StringConstraints(strip_whitespace=False),
    Field(pattern=r"^[A-Z0-9]{1,16}-[A-Z0-9]{1,16}-SWAP$"),
]
_EXACT_DIGEST = Annotated[
    str,
    StringConstraints(strip_whitespace=False),
    Field(pattern=r"^[a-f0-9]{64}$"),
]
_INVALID = (ValueError, TypeError, AttributeError, ArithmeticError, RecursionError)


class WSCaptureError(ValueError):
    """Stable local code only; do not expose raw frames or upstream exceptions."""

    @property
    def code(self) -> str:
        return str(self)


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _utc(value):
    if type(value) is not datetime:
        raise WSCaptureError("ws_clock_invalid")
    try:
        return require_aware(value)
    except (ValueError, OverflowError):
        raise WSCaptureError("ws_clock_invalid") from None


def _identifier(value, pattern, code):
    if type(value) is not str or pattern.fullmatch(value) is None:
        raise WSCaptureError(code)
    return value


def _parse_frame(raw):
    if type(raw) is not bytes or not 0 < len(raw) <= MAX_FRAME_BYTES:
        raise WSCaptureError("ws_frame_size_invalid")
    try:
        return _parse(raw)
    except QuoteCollectionError as exc:
        raise WSCaptureError("ws_" + str(exc)) from None


def _arg(payload, instrument_id):
    arg = payload.get("arg")
    if type(arg) is not dict or set(arg) != {"channel", "instId"}:
        raise WSCaptureError("ws_argument_schema_invalid")
    if arg["channel"] != "tickers" or arg["instId"] != instrument_id:
        raise WSCaptureError("ws_argument_identity_mismatch")


def _ticker_values(raw, instrument_id, received_at):
    payload, canonical = _parse_frame(raw)
    if type(payload) is not dict or set(payload) != {"arg", "data"}:
        raise WSCaptureError("ws_ticker_envelope_invalid")
    _arg(payload, instrument_id)
    data = payload["data"]
    if type(data) is not list or len(data) != 1 or type(data[0]) is not dict:
        raise WSCaptureError("ws_ticker_cardinality_invalid")
    row = data[0]
    if not _REQUIRED_TICKER_FIELDS <= set(row) or not set(row) <= (
        _REQUIRED_TICKER_FIELDS | OPTIONAL_TICKER_FIELDS
    ):
        raise WSCaptureError("ws_ticker_schema_invalid")
    if row["instType"] != "SWAP" or row["instId"] != instrument_id:
        raise WSCaptureError("ws_ticker_identity_mismatch")
    try:
        values = {
            name: _decimal(row, key, positive=True)
            for name, key in (
                ("bid", "bidPx"),
                ("ask", "askPx"),
                ("bid_size", "bidSz"),
                ("ask_size", "askSz"),
            )
        }
        for key in OPTIONAL_TICKER_FIELDS & row.keys():
            if _decimal(row, key, positive=False) < 0:
                raise WSCaptureError("ws_ticker_statistic_negative")
        source_time = _timestamp(row["ts"])
    except QuoteCollectionError as exc:
        raise WSCaptureError("ws_" + str(exc)) from None
    if values["bid"] >= values["ask"]:
        raise WSCaptureError("ws_ticker_spread_invalid")
    if source_time > received_at:
        raise WSCaptureError("ws_future_source_timestamp")
    return {
        **values,
        "ts_raw": row["ts"],
        "source_time": source_time,
        "canonical_json": canonical,
    }


def _ack_values(raw, instrument_id, subscription_id):
    payload, canonical = _parse_frame(raw)
    if type(payload) is not dict or set(payload) != {"id", "event", "arg", "connId"}:
        raise WSCaptureError("ws_ack_envelope_invalid")
    _arg(payload, instrument_id)
    if payload["event"] != "subscribe" or payload["id"] != subscription_id:
        raise WSCaptureError("ws_ack_subscription_mismatch")
    conn_id = _identifier(payload["connId"], _CONNECTION, "ws_connection_id_invalid")
    return {"conn_id": conn_id, "canonical_json": canonical}


def _guard_record(value, expected):
    if (
        type(value) is not expected
        or set(value.__dict__) != set(expected.model_fields)
        or value.__pydantic_extra__
    ):
        raise WSCaptureError("ws_record_type_or_fields_invalid")
    # Both receipt schemas are scalar-only. Never traverse or serialize an
    # injected nested model, iterator, callback or model_copy value.
    for item in value.__dict__.values():
        if type(item) is bytes:
            if not 0 < len(item) <= MAX_FRAME_BYTES:
                raise WSCaptureError("ws_frame_size_invalid")
        elif type(item) is str:
            if len(item) > MAX_FRAME_BYTES or len(item.encode()) > MAX_FRAME_BYTES:
                raise WSCaptureError("ws_record_text_too_large")
        elif type(item) is Decimal:
            if (
                not item.is_finite()
                or len(item.as_tuple().digits) > 40
                or abs(item.as_tuple().exponent) > 20
            ):
                raise WSCaptureError("ws_record_decimal_invalid")
        elif type(item) is datetime:
            _utc(item)
        elif type(item) is bool or type(item) is int and 0 <= item <= MAX_FRAME_BYTES:
            pass
        else:
            raise WSCaptureError("ws_record_scalar_invalid")


def _wire_fields(raw, canonical):
    return {
        "raw_frame": raw,
        "frame_size_bytes": len(raw),
        "frame_sha256": _sha(raw),
        "canonical_json": canonical,
        "canonical_sha256": _sha(canonical.encode()),
    }


def _same_source(record, expected):
    for name, value in expected.items():
        actual = getattr(record, name)
        equal = (
            actual.as_tuple() == value.as_tuple()
            if type(value) is Decimal
            else actual == value
        )
        if not equal:
            raise WSCaptureError("ws_record_source_mismatch")


class _WireReceipt(QualificationModel):
    instrument_id: _INSTRUMENT_ID
    received_at: datetime
    raw_frame: bytes = Field(min_length=1, max_length=MAX_FRAME_BYTES)
    frame_size_bytes: int = Field(gt=0, le=MAX_FRAME_BYTES)
    frame_sha256: _EXACT_DIGEST
    canonical_json: _JSON
    canonical_sha256: _EXACT_DIGEST
    source: Literal["ws"] = "ws"
    venue: Literal["OKX"] = "OKX"
    channel: Literal["tickers"] = "tickers"
    execution_authority: Literal[False] = False
    source_authenticity_verified: Literal[False] = False
    socket_binding_verified: Literal[False] = False

    _time = field_validator("received_at")(_utc)

    @field_validator(
        "execution_authority",
        "source_authenticity_verified",
        "socket_binding_verified",
        mode="before",
    )
    @classmethod
    def no_authority(cls, value):
        if type(value) is not bool or value is not False:
            raise WSCaptureError("ws_parser_cannot_grant_authority")
        return value


class WSTickerFrame(_WireReceipt):
    report_id: Annotated[
        str,
        StringConstraints(strip_whitespace=False),
        Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,95}$"),
    ]
    bid: Price
    ask: Price
    bid_size: Price
    ask_size: Price
    inst_type: Literal["SWAP"] = "SWAP"
    size_unit: Literal["contracts"] = "contracts"
    timestamp_semantics: Literal["ticker_generation"] = "ticker_generation"
    ts_raw: Annotated[
        str,
        StringConstraints(strip_whitespace=False),
        Field(pattern=r"^[1-9][0-9]{0,14}$"),
    ]
    source_time: datetime

    _source_time = field_validator("source_time")(_utc)

    @model_validator(mode="after")
    def consistent(self):
        _guard_record(self, WSTickerFrame)
        expected = _ticker_values(self.raw_frame, self.instrument_id, self.received_at)
        _same_source(
            self,
            {
                **expected,
                **_wire_fields(self.raw_frame, expected["canonical_json"]),
            },
        )
        return self

    @property
    def reference(self) -> WSReferenceObservation:
        checked = validate_ws_ticker_frame(self)
        return WSReferenceObservation(
            report_id=checked.report_id,
            instrument_id=checked.instrument_id,
            bid=checked.bid,
            ask=checked.ask,
            source_time=checked.source_time,
            received_at=checked.received_at,
        )


class WSSubscriptionAck(_WireReceipt):
    subscription_id: Annotated[
        str,
        StringConstraints(strip_whitespace=False),
        Field(pattern=r"^[A-Za-z0-9]{1,32}$"),
    ]
    conn_id: Annotated[
        str,
        StringConstraints(strip_whitespace=False),
        Field(pattern=r"^[A-Za-z0-9]{1,64}$"),
    ]
    event: Literal["subscribe"] = "subscribe"

    @model_validator(mode="after")
    def consistent(self):
        _guard_record(self, WSSubscriptionAck)
        expected = _ack_values(self.raw_frame, self.instrument_id, self.subscription_id)
        _same_source(
            self,
            {
                **expected,
                **_wire_fields(self.raw_frame, expected["canonical_json"]),
            },
        )
        return self


def parse_ws_ticker_frame(
    raw: bytes,
    *,
    report_id: str,
    instrument_id: str,
    received_at: datetime,
) -> WSTickerFrame:
    try:
        report_id = _identifier(report_id, _REPORT, "ws_report_id_invalid")
        instrument_id = _identifier(
            instrument_id, _INSTRUMENT, "ws_instrument_id_invalid"
        )
        received_at = _utc(received_at)
        values = _ticker_values(raw, instrument_id, received_at)
        return WSTickerFrame(
            report_id=report_id,
            instrument_id=instrument_id,
            received_at=received_at,
            **(values | _wire_fields(raw, values["canonical_json"])),
        )
    except WSCaptureError:
        raise
    except _INVALID:
        raise WSCaptureError("ws_ticker_record_invalid") from None


def parse_ws_subscription_ack(
    raw: bytes,
    *,
    instrument_id: str,
    subscription_id: str,
    received_at: datetime,
) -> WSSubscriptionAck:
    try:
        instrument_id = _identifier(
            instrument_id, _INSTRUMENT, "ws_instrument_id_invalid"
        )
        subscription_id = _identifier(
            subscription_id, _SUBSCRIPTION, "ws_subscription_id_invalid"
        )
        received_at = _utc(received_at)
        values = _ack_values(raw, instrument_id, subscription_id)
        return WSSubscriptionAck(
            instrument_id=instrument_id,
            subscription_id=subscription_id,
            received_at=received_at,
            **(values | _wire_fields(raw, values["canonical_json"])),
        )
    except WSCaptureError:
        raise
    except _INVALID:
        raise WSCaptureError("ws_ack_record_invalid") from None


def _validate_record(value, expected, parser):
    try:
        _guard_record(value, expected)
        # Compare byte-derived pins before Pydantic can wrap a precise local
        # mismatch as a generic ValidationError. No field is repaired here.
        derived = (
            _ticker_values(
                value.raw_frame, value.instrument_id, _utc(value.received_at)
            )
            if expected is WSTickerFrame
            else _ack_values(
                value.raw_frame, value.instrument_id, value.subscription_id
            )
        )
        _same_source(
            value,
            {
                **derived,
                **_wire_fields(value.raw_frame, derived["canonical_json"]),
            },
        )
        checked = expected.model_validate(dict(value.__dict__), strict=True)
        identity = {
            "instrument_id": checked.instrument_id,
            "received_at": checked.received_at,
        }
        if expected is WSTickerFrame:
            identity["report_id"] = checked.report_id
        else:
            identity["subscription_id"] = checked.subscription_id
        replayed = parser(checked.raw_frame, **identity)
        if replayed != checked:
            raise WSCaptureError("ws_record_replay_mismatch")
        return replayed
    except WSCaptureError:
        raise
    except _INVALID:
        raise WSCaptureError("ws_record_invalid") from None


def validate_ws_ticker_frame(frame: WSTickerFrame) -> WSTickerFrame:
    """Exact scalar reconstruction and byte replay; not socket authentication."""
    return _validate_record(frame, WSTickerFrame, parse_ws_ticker_frame)


def validate_ws_subscription_ack(ack: WSSubscriptionAck) -> WSSubscriptionAck:
    """Validate the documented ack, without inventing a push connId or timestamp."""
    return _validate_record(ack, WSSubscriptionAck, parse_ws_subscription_ack)
