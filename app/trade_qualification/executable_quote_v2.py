"""Explicit v2 quote observations and pure CTCC age-policy diagnostics.

No native source owner, gate callback, clock capability or execution issuer is
provided. Passing these checks leaves admission DENY. Existing v1 quote types,
inspectors and wire formats are not imported or changed.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field, fields
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal

from app.trade_qualification.funding_observation_v2 import (
    FundingObservationError,
    FundingObservationV2,
    verify_funding_observation_v2,
)

_DIGEST = re.compile(r"[a-f0-9]{64}")
_INSTRUMENT = re.compile(r"[A-Z0-9]{1,16}-[A-Z0-9]{1,16}-SWAP")
_REPORT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")


def _canonical(value) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode()


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


POLICY_BYTES = _canonical(
    {
        "version": "ctcc.executable_quote_diagnostic_policy.v2",
        "policy_owner": "CTCC_local_admission_choice_not_exchange_SLA",
        "region": "global",
        "environments": ["analysis_only", "demo"],
        "product": "SWAP",
        "ticker_source_age_seconds": 5,
        "mark_return_age_seconds": 5,
        "each_receipt_age_seconds": 5,
        "funding_return_age_seconds": 90,
        "future_timestamp_tolerance_seconds": 0,
        "funding_settlement_guard_seconds": 95,
        "funding_mechanism": "current_period",
        "funding_settlement_state": "settled",
        "funding_rate_generation_time": "unknown",
        "historical_first_availability": "unknown",
        "latest_exchange_generation_verified": False,
        "execution_authority": False,
        "admission": "DENY",
    }
)
POLICY_SHA256 = _sha(POLICY_BYTES)


class ExecutableQuoteV2Error(ValueError):
    """Stable diagnostic code only."""


def _deny(code):
    raise ExecutableQuoteV2Error(code)


def _exact(value, expected):
    if type(value) is not expected or any(
        not hasattr(value, item.name) for item in fields(expected)
    ):
        _deny("quote_component_exact_type_required")


def _utc(value):
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        _deny("quote_clock_invalid")
    return value.astimezone(UTC)


def _price(value):
    if (
        type(value) is not Decimal
        or not value.is_finite()
        or not Decimal(0) < value < Decimal("1e20")
        or len(value.as_tuple().digits) > 40
        or not -20 <= value.as_tuple().exponent <= 20
    ):
        _deny("quote_price_or_size_invalid")
    return str(value)


@dataclass(frozen=True, slots=True)
class RestTickerObservationV2:
    instrument_id: str
    bid: Decimal
    ask: Decimal
    bid_size_contracts: Decimal
    ask_size_contracts: Decimal
    source_generated_at: datetime
    request_started_at: datetime
    headers_received_at: datetime
    body_completed_at: datetime
    raw_body_sha256: str


@dataclass(frozen=True, slots=True)
class RestMarkObservationV2:
    instrument_id: str
    price: Decimal
    exchange_data_return_at: datetime
    request_started_at: datetime
    headers_received_at: datetime
    body_completed_at: datetime
    raw_body_sha256: str


@dataclass(frozen=True, slots=True)
class ExecutableQuoteV2:
    report_id: str
    instrument_id: str
    environment: Literal["analysis_only", "demo"]
    capture_started_at: datetime
    capture_completed_at: datetime
    ticker: RestTickerObservationV2
    mark: RestMarkObservationV2
    funding: FundingObservationV2
    schema_version: str = field(default="ctcc.executable_quote.v2", init=False)
    region: str = field(default="global", init=False)
    source_authenticity_verified: Literal[False] = field(default=False, init=False)
    execution_authority: Literal[False] = field(default=False, init=False)
    admission: Literal["DENY"] = field(default="DENY", init=False)


@dataclass(frozen=True, slots=True)
class ExecutableQuoteInspectionV2:
    receipt_json: bytes

    @property
    def receipt_sha256(self):
        return _sha(self.receipt_json)

    @property
    def execution_authority(self):
        return False

    @property
    def admission(self):
        return "DENY"


def _component(value, instrument, start, completed, now, *, role):
    expected = RestTickerObservationV2 if role == "ticker" else RestMarkObservationV2
    _exact(value, expected)
    if type(value.instrument_id) is not str or value.instrument_id != instrument:
        _deny("quote_component_identity_mismatch")
    if type(value.raw_body_sha256) is not str or not _DIGEST.fullmatch(
        value.raw_body_sha256
    ):
        _deny("quote_component_source_digest_invalid")
    source = _utc(
        value.source_generated_at if role == "ticker" else value.exchange_data_return_at
    )
    request, headers, body = map(
        _utc,
        (
            value.request_started_at,
            value.headers_received_at,
            value.body_completed_at,
        ),
    )
    if not start <= request <= headers <= body <= completed <= now or source > headers:
        _deny("quote_component_causal_order_invalid")
    if now - source > timedelta(seconds=5):
        _deny(f"{role}_source_stale")
    if any(now - stamp > timedelta(seconds=5) for stamp in (headers, body)):
        _deny(f"{role}_receipt_stale")
    prices = (
        {
            "bid": _price(value.bid),
            "ask": _price(value.ask),
            "bid_size_contracts": _price(value.bid_size_contracts),
            "ask_size_contracts": _price(value.ask_size_contracts),
        }
        if role == "ticker"
        else {"mark_price": _price(value.price)}
    )
    if role == "ticker" and value.bid > value.ask:
        _deny("quote_crossed_bid_ask")
    return {
        **prices,
        "instrument_id": instrument,
        "source_time": source.isoformat(),
        "timestamp_semantics": "ticker_generation"
        if role == "ticker"
        else "exchange_data_return",
        "request_started_at": request.isoformat(),
        "headers_received_at": headers.isoformat(),
        "body_completed_at": body.isoformat(),
        "raw_body_sha256": value.raw_body_sha256,
    }


def _inspect(value, current_time, expected_policy_sha256):
    _exact(value, ExecutableQuoteV2)
    if (
        type(expected_policy_sha256) is not str
        or expected_policy_sha256 != POLICY_SHA256
    ):
        _deny("quote_profile_identity_mismatch")
    for name, expected in (
        ("schema_version", "ctcc.executable_quote.v2"),
        ("region", "global"),
        ("source_authenticity_verified", False),
        ("execution_authority", False),
        ("admission", "DENY"),
    ):
        actual = getattr(value, name)
        if type(actual) is not type(expected) or actual != expected:
            _deny("quote_unowned_flags_invalid")
    if (
        type(value.report_id) is not str
        or not _REPORT.fullmatch(value.report_id)
        or type(value.instrument_id) is not str
        or not _INSTRUMENT.fullmatch(value.instrument_id)
        or type(value.environment) is not str
        or value.environment not in ("analysis_only", "demo")
    ):
        _deny("quote_scope_invalid")
    now, start, completed = map(
        _utc,
        (
            current_time,
            value.capture_started_at,
            value.capture_completed_at,
        ),
    )
    if not start <= completed <= now:
        _deny("quote_capture_causal_order_invalid")
    if now - completed > timedelta(seconds=5):
        _deny("quote_capture_receipt_stale")
    ticker = _component(
        value.ticker, value.instrument_id, start, completed, now, role="ticker"
    )
    mark = _component(
        value.mark, value.instrument_id, start, completed, now, role="mark"
    )
    funding = verify_funding_observation_v2(value.funding)
    if funding.instrument_id != value.instrument_id:
        _deny("funding_quote_identity_mismatch")
    if (
        not start
        <= funding.acquisition_started_at
        <= funding.received_at
        <= funding.completed_at
        <= completed
    ):
        _deny("funding_capture_causal_order_invalid")
    if any(
        now - stamp > timedelta(seconds=5)
        for stamp in (funding.received_at, funding.completed_at)
    ):
        _deny("funding_receipt_stale")
    if now - funding.exchange_data_return_at > timedelta(seconds=90):
        _deny("funding_return_stale")
    if funding.settlement_state != "settled":
        _deny("funding_settlement_processing")
    if funding.forecast.settlement_at <= now:
        _deny("funding_settlement_reached")
    if funding.forecast.settlement_at <= now + timedelta(seconds=95):
        _deny("funding_settlement_transition_guard")
    document = {
        "schema_version": value.schema_version,
        "report_id": value.report_id,
        "instrument_id": value.instrument_id,
        "environment": value.environment,
        "region": value.region,
        "policy_sha256": POLICY_SHA256,
        "capture_started_at": start.isoformat(),
        "capture_completed_at": completed.isoformat(),
        "ticker": ticker,
        "mark": mark,
        "funding": {
            "source_kind": funding.source_kind,
            "body_sha256": funding.body_sha256,
            "canonical_sha256": funding.canonical_sha256,
            "acquisition_started_at": funding.acquisition_started_at.isoformat(),
            "received_at": funding.received_at.isoformat(),
            "completed_at": funding.completed_at.isoformat(),
            "exchange_data_return_at": funding.exchange_data_return_at.isoformat(),
            "rate": str(funding.forecast.rate),
            "upcoming_settlement_at": funding.forecast.settlement_at.isoformat(),
            "following_settlement_forecast_at": funding.following_settlement_forecast_at.isoformat(),
            "formula_type": funding.formula_type,
            "mechanism": funding.mechanism,
            "settlement_state": funding.settlement_state,
        },
    }
    return now, document


def inspect_executable_quote_v2(
    value: ExecutableQuoteV2,
    *,
    current_time: datetime,
    expected_policy_sha256: str = POLICY_SHA256,
) -> ExecutableQuoteInspectionV2:
    """Recompute fixed-policy diagnostics. A valid result still cannot trade."""
    now, document = None, None
    try:
        now = _utc(current_time)
        now, document = _inspect(value, now, expected_policy_sha256)
        code = "profile_checks_satisfied"
    except ExecutableQuoteV2Error as exc:
        code = str(exc)
    except FundingObservationError:
        code = "funding_observation_invalid"
    except (AttributeError, TypeError, ValueError, OverflowError):
        code = "quote_observation_invalid"
    receipt = {
        "schema_version": "ctcc.executable_quote_inspection.v2",
        "policy_sha256": POLICY_SHA256,
        "profile_satisfied": document is not None,
        "code": code,
        "validated_at": None if now is None else now.isoformat(),
        "quote_document": document,
        "quote_sha256": None if document is None else _sha(_canonical(document)),
        "unverified": [
            "native_source_and_receipt_authenticity",
            "latest_exchange_rate_generation",
            "historical_first_availability",
            "point_in_time_predictive_claim",
            "owned_invocation_and_publication_barrier",
            "independent_ws_quote_comparison",
            "current_account_and_portfolio",
            "candidate_and_gates",
            "reservation",
            "durable_intent",
            "final_native_submit_fence",
        ],
        "source_authenticity_verified": False,
        "point_in_time_verified": False,
        "account_complete": False,
        "execution_authority": False,
        "admission": "DENY",
    }
    return ExecutableQuoteInspectionV2(_canonical(receipt))
