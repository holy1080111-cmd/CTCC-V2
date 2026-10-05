"""Pure interpretation of one retained OKX funding payload; never an issuer.

``ts`` is exchange data-return time, not rate-generation or first availability.
The forecast applies to ``fundingTime``; ``nextFundingTime`` is the following
forecast settlement. No network, wall clock, freshness policy or trading gate
is invoked. The supplied receipt times remain unverified caller observations.
Official semantics checked 2026-09-28:
https://my.okx.com/docs-v5/en/#public-data-rest-api-get-funding-rate
https://app.okx.com/docs-v5/en/#public-data-websocket-funding-rate-channel
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field, fields
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal, NoReturn

SCHEMA_VERSION = "ctcc.funding_observation.v2"
MAX_BODY_BYTES = 65536
_INSTRUMENT = re.compile(r"[A-Z0-9]{1,16}-[A-Z0-9]{1,16}-SWAP")
_DECIMAL = re.compile(r"-?(?:0|[1-9][0-9]{0,19})(?:\.[0-9]{1,20})?")
_MILLISECONDS = re.compile(r"[1-9][0-9]{0,14}")
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class FundingObservationError(ValueError):
    """A bounded local code; raw response values are never error text."""


def _deny(code: str) -> NoReturn:
    raise FundingObservationError(code)


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _utc(value: datetime) -> datetime:
    if type(value) is not datetime or value.tzinfo is None:
        _deny("funding_observation_clock_invalid")
    try:
        if value.utcoffset() is None:
            _deny("funding_observation_clock_invalid")
        return value.astimezone(UTC)
    except (ValueError, OverflowError):
        _deny("funding_observation_clock_invalid")


def _timestamp(value: object) -> datetime:
    if type(value) is not str or _MILLISECONDS.fullmatch(value) is None:
        _deny("funding_source_timestamp_invalid")
    try:
        return _EPOCH + timedelta(milliseconds=int(value))
    except (ValueError, OverflowError):
        _deny("funding_source_timestamp_invalid")


def _decimal(row: dict, name: str) -> Decimal:
    value = row.get(name)
    if type(value) is not str or _DECIMAL.fullmatch(value) is None:
        _deny("funding_source_decimal_invalid")
    return Decimal(value)


def _pairs(pairs: list[tuple[str, object]]) -> dict:
    value = {}
    for key, item in pairs:
        if key in value:
            _deny("funding_duplicate_json_key")
        value[key] = item
    return value


def _constant(_: str) -> NoReturn:
    _deny("funding_nonfinite_json")


def _payload(raw: bytes) -> tuple[dict, bytes]:
    if type(raw) is not bytes or not 0 < len(raw) <= MAX_BODY_BYTES:
        _deny("funding_body_invalid")
    try:
        payload = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_constant
        )
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (ValueError, UnicodeError, RecursionError, OverflowError):
        _deny("funding_json_invalid")
    if type(payload) is not dict:
        _deny("funding_envelope_invalid")
    return payload, canonical


@dataclass(frozen=True, slots=True)
class FundingForecastV2:
    """One inseparable source rate/settlement pair, not realized cashflow."""

    rate: Decimal
    settlement_at: datetime


@dataclass(frozen=True, slots=True)
class FundingObservationV2:
    source_kind: Literal["rest", "ws"]
    instrument_id: str
    acquisition_started_at: datetime
    received_at: datetime
    completed_at: datetime
    exchange_data_return_at: datetime
    forecast: FundingForecastV2
    following_settlement_forecast_at: datetime
    formula_type: Literal["noRate", "withRate"]
    mechanism: Literal["current_period"]
    minimum_rate: Decimal
    maximum_rate: Decimal
    settlement_state: Literal["processing", "settled"]
    settlement_reference_rate: Decimal
    settlement_reference_kind: Literal["current_processing", "previous_settled"]
    raw_body: bytes
    body_sha256: str
    canonical_json: bytes
    canonical_sha256: str
    schema_version: str = field(default=SCHEMA_VERSION, init=False)
    rate_generated_at: None = field(default=None, init=False)
    historical_first_available_at: None = field(default=None, init=False)
    source_authenticity_verified: Literal[False] = field(default=False, init=False)
    receipt_authenticity_verified: Literal[False] = field(default=False, init=False)
    point_in_time_verified: Literal[False] = field(default=False, init=False)
    account_complete: Literal[False] = field(default=False, init=False)
    execution_authority: Literal[False] = field(default=False, init=False)
    admission: Literal["DENY"] = field(default="DENY", init=False)


def parse_funding_observation_v2(
    raw_body: bytes,
    *,
    source_kind: Literal["rest", "ws"],
    instrument_id: str,
    acquisition_started_at: datetime,
    received_at: datetime,
    completed_at: datetime,
) -> FundingObservationV2:
    """Decode one exact SWAP row, retaining all raw and canonical payload bytes.

    REST receipt denotes observed response headers; WS receipt denotes observed
    complete message receipt. Acquisition starts before the HTTP request or WS
    subscription. These inputs are not authenticated by this pure function.
    Processing or old records can be represented; admission remains DENY.
    """
    if type(source_kind) is not str or source_kind not in ("rest", "ws"):
        _deny("funding_source_kind_invalid")
    if type(instrument_id) is not str or _INSTRUMENT.fullmatch(instrument_id) is None:
        _deny("funding_instrument_invalid")
    start, received, completed = map(
        _utc, (acquisition_started_at, received_at, completed_at)
    )
    if not start <= received <= completed:
        _deny("funding_receipt_order_invalid")
    payload, canonical = _payload(raw_body)
    if source_kind == "rest":
        if payload.get("code") != "0" or type(payload.get("msg")) is not str:
            _deny("funding_rest_envelope_invalid")
    elif (
        "event" in payload
        or "code" in payload
        or type(payload.get("arg")) is not dict
        or payload["arg"] != {"channel": "funding-rate", "instId": instrument_id}
    ):
        _deny("funding_ws_envelope_invalid")
    rows = payload.get("data")
    if type(rows) is not list or len(rows) != 1 or type(rows[0]) is not dict:
        _deny("funding_exact_row_required")
    row = rows[0]
    if row.get("instId") != instrument_id or row.get("instType") != "SWAP":
        _deny("funding_source_identity_mismatch")
    if row.get("method") != "current_period":
        _deny("funding_mechanism_unsupported")
    if row.get("formulaType") not in ("noRate", "withRate"):
        _deny("funding_formula_unsupported")
    if row.get("settState") not in ("processing", "settled"):
        _deny("funding_settlement_state_invalid")
    if "nextFundingRate" in row and row["nextFundingRate"] != "":
        _deny("funding_deprecated_forecast_unsupported")
    returned = _timestamp(row.get("ts"))
    upcoming = _timestamp(row.get("fundingTime"))
    following = _timestamp(row.get("nextFundingTime"))
    if returned > received:
        _deny("funding_future_return_timestamp")
    if following <= upcoming:
        _deny("funding_settlement_sequence_invalid")
    rate, lower, upper, settled = (
        _decimal(row, name)
        for name in (
            "fundingRate",
            "minFundingRate",
            "maxFundingRate",
            "settFundingRate",
        )
    )
    if not lower <= rate <= upper:
        _deny("funding_rate_bounds_invalid")
    return FundingObservationV2(
        source_kind=source_kind,
        instrument_id=instrument_id,
        acquisition_started_at=start,
        received_at=received,
        completed_at=completed,
        exchange_data_return_at=returned,
        forecast=FundingForecastV2(rate=rate, settlement_at=upcoming),
        following_settlement_forecast_at=following,
        formula_type=row["formulaType"],
        mechanism=row["method"],
        minimum_rate=lower,
        maximum_rate=upper,
        settlement_state=row["settState"],
        settlement_reference_rate=settled,
        settlement_reference_kind=(
            "current_processing"
            if row["settState"] == "processing"
            else "previous_settled"
        ),
        raw_body=raw_body,
        body_sha256=_sha(raw_body),
        canonical_json=canonical,
        canonical_sha256=_sha(canonical),
    )


def verify_funding_observation_v2(value: FundingObservationV2) -> FundingObservationV2:
    """Replay exact fields from raw bytes; never upgrade caller data to authority."""
    if type(value) is not FundingObservationV2:
        _deny("funding_observation_exact_type_required")
    if any(not hasattr(value, item.name) for item in fields(FundingObservationV2)):
        _deny("funding_observation_fields_missing")
    if type(value.forecast) is not FundingForecastV2:
        _deny("funding_forecast_exact_type_required")
    if any(
        not hasattr(value.forecast, item.name) for item in fields(FundingForecastV2)
    ):
        _deny("funding_forecast_fields_missing")
    replay = parse_funding_observation_v2(
        value.raw_body,
        source_kind=value.source_kind,
        instrument_id=value.instrument_id,
        acquisition_started_at=value.acquisition_started_at,
        received_at=value.received_at,
        completed_at=value.completed_at,
    )
    for item in fields(replay):
        original, expected = getattr(value, item.name), getattr(replay, item.name)
        if type(original) is not type(expected) or original != expected:
            _deny("funding_observation_replay_mismatch")
    for item in fields(replay.forecast):
        original, expected = (
            getattr(value.forecast, item.name),
            getattr(replay.forecast, item.name),
        )
        if type(original) is not type(expected) or original != expected:
            _deny("funding_forecast_replay_mismatch")
    return replay
