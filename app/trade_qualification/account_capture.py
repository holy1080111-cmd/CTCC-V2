"""Offline Demo account receipts, not a trusted collector or portfolio snapshot.

Only supplied bytes are parsed. No transport, credentials, settings, wall clock,
database or execution imports exist. OKX primary documentation checked 2026-10-05:
https://www.okx.com/docs-v5/en/ and https://www.okx.com/docs-v5/trick_en/#pagination.
Current v5 exposure queries are unfiltered. Historical v2/v3 packets retain
their original stream contract; immutable v4 packets retain the eight formerly
requested algo filters for replay only. Current v5 uses four currently
documented algo types and six standard-product history chains. Recent fills are
unfiltered. Instrument/leverage metadata remains scoped to SWAP.
An empty terminal page proves only the supplied query chain,
not exchange retention, ingestion completeness, atomicity or account-wide risk.
Source update/event times are preserved, never replaced by receipt time.
Limits below are uncalibrated engineering budgets, not trading-risk policy.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from functools import wraps
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from pydantic import ConfigDict, Field, field_validator, model_validator
from pydantic_core import TzInfo

from app.trade_qualification.models import QualificationModel

MAX_RESPONSE_BYTES = 262144
MAX_PACKET_BYTES = 16777216
MAX_TOTAL_PAGES = 256
ALGO_ORDER_TYPES = (
    "conditional",
    "oco",
    "trigger",
    "move_order_stop",
)
LEGACY_ALGO_ORDER_TYPES_V4 = (
    *ALGO_ORDER_TYPES,
    "iceberg",
    "twap",
    "chase",
    "smart_iceberg",
)
STREAMS = (
    "config_before",
    "account_position_risk",
    "balance",
    "positions",
    "orders_pending",
    "algo_conditional",
    "algo_oco",
    "algo_trigger",
    "algo_move_order_stop",
    "algo_iceberg",
    "algo_twap",
    "algo_chase",
    "algo_smart_iceberg",
    "fills_recent",
    "fills_history",
    "bills_recent",
    "bills_archive",
    "orders_history_recent",
    "orders_history_archive",
    "account_instruments",
    "leverage_cross",
    "leverage_isolated",
    "config_after",
)
Stream = Literal[
    "fills_history_spot",
    "fills_history_margin",
    "fills_history_swap",
    "fills_history_futures",
    "fills_history_option",
    "fills_history_events",
    "orders_history_recent_spot",
    "orders_history_recent_margin",
    "orders_history_recent_swap",
    "orders_history_recent_futures",
    "orders_history_recent_option",
    "orders_history_recent_events",
    "orders_history_archive_spot",
    "orders_history_archive_margin",
    "orders_history_archive_swap",
    "orders_history_archive_futures",
    "orders_history_archive_option",
    "orders_history_archive_events",
    "config_before",
    "account_position_risk",
    "balance",
    "positions",
    "orders_pending",
    "algo_conditional",
    "algo_oco",
    "algo_trigger",
    "algo_move_order_stop",
    "algo_iceberg",
    "algo_twap",
    "algo_chase",
    "algo_smart_iceberg",
    "fills_recent",
    "fills_history",
    "bills_recent",
    "bills_archive",
    "orders_history_recent",
    "orders_history_archive",
    "account_instruments",
    "leverage_cross",
    "leverage_isolated",
    "config_after",
]
Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Name = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}$")]
Identifier = Annotated[str, Field(pattern=r"^[1-9][0-9]{0,39}$")]
Currency = Annotated[str, Field(pattern=r"^[A-Z0-9]{1,20}$")]
RawText = Annotated[str, Field(max_length=MAX_RESPONSE_BYTES)]
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_DECIMAL = re.compile(r"-?(?:0|[1-9][0-9]{0,19})(?:\.[0-9]{1,20})?")
_ID = re.compile(r"[1-9][0-9]{0,39}")
_ENDPOINTS = {
    "config_before": "/api/v5/account/config",
    "config_after": "/api/v5/account/config",
    "account_position_risk": "/api/v5/account/account-position-risk",
    "balance": "/api/v5/account/balance",
    "positions": "/api/v5/account/positions",
    "orders_pending": "/api/v5/trade/orders-pending",
    "fills_recent": "/api/v5/trade/fills",
    "fills_history": "/api/v5/trade/fills-history",
    "bills_recent": "/api/v5/account/bills",
    "bills_archive": "/api/v5/account/bills-archive",
    "orders_history_recent": "/api/v5/trade/orders-history",
    "orders_history_archive": "/api/v5/trade/orders-history-archive",
    "account_instruments": "/api/v5/account/instruments",
    "leverage_cross": "/api/v5/account/leverage-info",
    "leverage_isolated": "/api/v5/account/leverage-info",
    **{
        f"algo_{kind}": "/api/v5/trade/orders-algo-pending"
        for kind in LEGACY_ALGO_ORDER_TYPES_V4
    },
}
_CURSOR_FIELDS = {
    "orders_pending": "ordId",
    "orders_history_recent": "ordId",
    "orders_history_archive": "ordId",
    "fills_recent": "billId",
    "fills_history": "billId",
    "bills_recent": "billId",
    "bills_archive": "billId",
    **{stream: "algoId" for stream in STREAMS if stream.startswith("algo_")},
}
_FILL_STREAMS = frozenset({"fills_recent", "fills_history"})
_BILL_STREAMS = frozenset({"bills_recent", "bills_archive"})
_ORDER_HISTORY_STREAMS = frozenset({"orders_history_recent", "orders_history_archive"})
INSTRUMENT_TYPES = ("SPOT", "MARGIN", "SWAP", "FUTURES", "OPTION", "EVENTS")
_HISTORY_VARIANTS = {
    f"{family}_{kind.lower()}": (family, kind)
    for family in ("fills_history", "orders_history_recent", "orders_history_archive")
    for kind in INSTRUMENT_TYPES
}
V4_STREAMS = tuple(
    item
    for stream in STREAMS
    for item in (
        tuple(f"{stream}_{kind.lower()}" for kind in INSTRUMENT_TYPES)
        if stream
        in {"fills_history", "orders_history_recent", "orders_history_archive"}
        else (stream,)
    )
)
V5_BASE_STREAMS = tuple(
    stream for stream in STREAMS if not stream.startswith("algo_")
) + tuple(f"algo_{kind}" for kind in ALGO_ORDER_TYPES)
V5_STREAMS = tuple(
    item
    for stream in V5_BASE_STREAMS
    for item in (
        tuple(f"{stream}_{kind.lower()}" for kind in INSTRUMENT_TYPES)
        if stream
        in {"fills_history", "orders_history_recent", "orders_history_archive"}
        else (stream,)
    )
)
V6_CURRENT_STREAMS = (
    "config_before",
    "account_position_risk",
    "balance",
    "positions",
    "orders_pending",
    *(f"algo_{kind}" for kind in ALGO_ORDER_TYPES),
    "account_instruments",
    "leverage_cross",
    "leverage_isolated",
    "config_after",
)
ALL_STREAMS = frozenset(STREAMS) | frozenset(V4_STREAMS) | frozenset(V5_STREAMS)
_ENDPOINTS.update(
    {name: _ENDPOINTS[family] for name, (family, _) in _HISTORY_VARIANTS.items()}
)
_CURSOR_FIELDS.update(
    {name: _CURSOR_FIELDS[family] for name, (family, _) in _HISTORY_VARIANTS.items()}
)
_FILL_STREAMS |= frozenset(
    name for name, (family, _) in _HISTORY_VARIANTS.items() if family == "fills_history"
)
_ORDER_HISTORY_STREAMS |= frozenset(
    name
    for name, (family, _) in _HISTORY_VARIANTS.items()
    if family.startswith("orders_history_")
)
_HISTORY_STREAMS = _FILL_STREAMS | _BILL_STREAMS | _ORDER_HISTORY_STREAMS


def stream_family(stream):
    """Preserve exact query identity while grouping documented endpoint families."""
    return _HISTORY_VARIANTS.get(stream, (stream, None))[0]


_BASE_GAPS = (
    "advanced_product_scope_unverified",
    "cross_source_atomicity_unverified",
    "history_ingestion_watermark_unverified",
    "history_retention_unverified",
    "history_seed_missing",
    "instrument_and_correlation_mapping_missing",
    "local_uncertain_ledger_missing",
    "non_swap_history_not_requested",
    "peak_window_evidence_missing",
    "protection_and_cost_mapping_missing",
    "source_authenticity_unverified",
)


class AccountCaptureError(ValueError):
    """Stable local error codes; never include supplied bodies or secrets."""


def _fail(code):
    raise AccountCaptureError(code)


def _bounded_api(function):
    @wraps(function)
    def checked(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except AccountCaptureError:
            raise
        except (
            ValueError,
            TypeError,
            ArithmeticError,
            RecursionError,
            AttributeError,
            KeyError,
        ):
            _fail("account_capture_invalid")

    return checked


def _utc(value):
    if type(value) is not datetime or not any(
        type(value.tzinfo) is trusted for trusted in (timezone, ZoneInfo, TzInfo)
    ):
        _fail("clock_invalid")
    return value.astimezone(UTC)


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical(value) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


class _Record(QualificationModel):
    model_config = ConfigDict(
        str_strip_whitespace=False, ser_json_bytes="hex", val_json_bytes="hex"
    )

    @model_validator(mode="after")
    def bounded_raw_fields(self):
        _guard(self)
        return self


class DemoAccountCapturePlan(_Record):
    plan_id: Name
    created_at: datetime
    expected_uid: Identifier
    expected_main_uid: Identifier
    session_binding_id: Name
    settlement_currency: Currency
    leverage_instrument_ids: tuple[Name, ...] = Field(min_length=1, max_length=20)
    history_start: datetime
    history_end: datetime
    page_size: int = Field(default=100, ge=1, le=100)
    max_pages_per_stream: int = Field(default=16, ge=1, le=64)
    max_total_pages: int = Field(default=128, ge=len(STREAMS), le=MAX_TOTAL_PAGES)
    # Top-level data rows; nested inventory/JSON nodes have independent bounds.
    max_total_rows: int = Field(default=2048, ge=1, le=8192)
    max_response_bytes: int = Field(default=65536, ge=256, le=MAX_RESPONSE_BYTES)
    max_total_bytes: int = Field(default=2097152, ge=1024, le=4194304)
    max_request_seconds: int = Field(default=5, ge=1, le=30)
    max_batch_seconds: int = Field(default=120, ge=1, le=3600)
    environment: Literal["demo"] = "demo"
    # A new mandatory query inventory must not silently reuse an old plan pin.
    capture_scope: Literal["all_current_algos_v2_and_swap_history"] = (
        "all_current_algos_v2_and_swap_history"
    )

    _times = field_validator("created_at", "history_start", "history_end")(_utc)

    @model_validator(mode="after")
    def windows(self):
        if len(set(self.leverage_instrument_ids)) != len(
            self.leverage_instrument_ids
        ) or any(
            re.fullmatch(r"[A-Z0-9]+-[A-Z0-9]+-SWAP", item) is None
            for item in self.leverage_instrument_ids
        ):
            _fail("plan_leverage_scope_invalid")
        if not _EPOCH < self.history_start <= self.history_end <= self.created_at:
            _fail("plan_history_window_invalid")
        if any(
            value.microsecond % 1000 for value in (self.history_start, self.history_end)
        ):
            _fail("history_window_requires_milliseconds")
        return self


class RegionalDemoAccountCapturePlan(DemoAccountCapturePlan):
    """Explicit registration routing; the evidence pin is not authentication.

    Kept separate so historical v2 plans and packets retain their exact hashes.
    Registration must be established outside capture, never inferred from locale.
    Official regional overview reviewed 2026-10-05; no redirects or alternate hosts.
    """

    registration_region: Literal["global", "us_au", "eea", "tr"]
    origin: Literal[
        "https://openapi.okx.com",
        "https://us.okx.com",
        "https://eea.okx.com",
        "https://tr.okx.com",
    ]
    registration_evidence_sha256: Digest

    @model_validator(mode="after")
    def regional_origin(self):
        if (
            self.origin
            != {
                "global": "https://openapi.okx.com",
                "us_au": "https://us.okx.com",
                "eea": "https://eea.okx.com",
                "tr": "https://tr.okx.com",
            }[self.registration_region]
        ):
            _fail("registration_origin_mismatch")
        return self


class AllProductDemoAccountCapturePlan(RegionalDemoAccountCapturePlan):
    """Current documented standard-product scope, never account authority."""

    contract_version: Literal["ctcc.demo_account_plan.v5"]
    capture_scope: Literal["all_standard_products_v5_documented_algos"] = (
        "all_standard_products_v5_documented_algos"
    )

    @model_validator(mode="after")
    def inventory_budget(self):
        if self.max_total_pages < len(V5_STREAMS):
            _fail("plan_inventory_budget_invalid")
        return self


class CurrentDemoAccountCapturePlanV6(RegionalDemoAccountCapturePlan):
    """Fresh current inventory only; historical cashflows need a separate join."""

    contract_version: Literal["ctcc.demo_current_account_plan.v6"]
    capture_scope: Literal["all_current_standard_products_v6"] = (
        "all_current_standard_products_v6"
    )

    @model_validator(mode="after")
    def inventory_budget(self):
        if self.max_total_pages < len(V6_CURRENT_STREAMS):
            _fail("plan_inventory_budget_invalid")
        return self


class HistoricalAllProductDemoAccountCapturePlanV4(RegionalDemoAccountCapturePlan):
    """Read-only replay identity for immutable v4 packets; collection is disabled."""

    contract_version: Literal["ctcc.demo_account_plan.v4"]
    capture_scope: Literal["all_standard_products_v4_and_current_algos"] = (
        "all_standard_products_v4_and_current_algos"
    )

    @model_validator(mode="after")
    def inventory_budget(self):
        if self.max_total_pages < len(V4_STREAMS):
            _fail("plan_inventory_budget_invalid")
        return self


def streams_for_plan(plan):
    if type(plan) is CurrentDemoAccountCapturePlanV6:
        return V6_CURRENT_STREAMS
    if type(plan) is AllProductDemoAccountCapturePlan:
        return V5_STREAMS
    if type(plan) is HistoricalAllProductDemoAccountCapturePlanV4:
        return V4_STREAMS
    return STREAMS


def is_all_product_plan(plan):
    return type(plan) in {
        CurrentDemoAccountCapturePlanV6,
        AllProductDemoAccountCapturePlan,
        HistoricalAllProductDemoAccountCapturePlanV4,
    }


class AccountRequest(_Record):
    stream: Stream
    method: Literal["GET"] = "GET"
    origin: Literal[
        "https://www.okx.com",
        "https://openapi.okx.com",
        "https://us.okx.com",
        "https://eea.okx.com",
        "https://tr.okx.com",
    ] = "https://www.okx.com"
    endpoint: Annotated[str, Field(max_length=96)]
    parameters: tuple[
        tuple[
            Annotated[str, Field(max_length=20)], Annotated[str, Field(max_length=1940)]
        ],
        ...,
    ] = Field(max_length=5)


class AccountNumber(_Record):
    path: Annotated[str, Field(min_length=1, max_length=160)]
    raw: Annotated[str, Field(max_length=42)] | None
    value: Decimal | None = Field(max_digits=40, decimal_places=20)


class AccountSourceTime(_Record):
    path: Annotated[str, Field(min_length=1, max_length=160)]
    raw: Annotated[str, Field(max_length=15)] | None
    value: datetime | None
    semantics: Literal[
        "source_update", "record_creation", "trade_match", "record_generation"
    ]


class AccountRow(_Record):
    row_id: Annotated[str, Field(min_length=1, max_length=160)]
    instrument_id: Annotated[str, Field(min_length=1, max_length=96)] | None
    canonical_json: RawText
    numbers: tuple[AccountNumber, ...] = Field(max_length=4096)
    source_times: tuple[AccountSourceTime, ...] = Field(max_length=4096)
    missing_fields: tuple[Annotated[str, Field(max_length=160)], ...] = Field(
        max_length=4096
    )


class DemoAccountObservation(_Record):
    request: AccountRequest
    plan_sha256: Digest
    environment: Literal["demo"] = "demo"
    session_binding_id: Name
    identity_receipt_sha256: Digest | None
    identity_binding: Literal["config_response", "request_context"]
    page_index: int = Field(ge=0, le=63)
    after: Identifier | None
    previous_page_sha256: Digest | None
    request_started_at: datetime
    headers_received_at: datetime
    body_completed_at: datetime
    barrier_completed_at: datetime
    response_body: bytes = Field(min_length=1, max_length=MAX_RESPONSE_BYTES)
    body_sha256: Digest
    body_size_bytes: int = Field(ge=1, le=MAX_RESPONSE_BYTES)
    canonical_json: RawText
    canonical_sha256: Digest
    rows: tuple[AccountRow, ...] = Field(max_length=2048)
    terminal: bool
    receipt_sha256: Digest

    _times = field_validator(
        "request_started_at",
        "headers_received_at",
        "body_completed_at",
        "barrier_completed_at",
    )(_utc)


class DemoAccountPacket(_Record):
    schema_version: Literal[
        "ctcc.demo_account_capture.v2",
        "ctcc.demo_account_capture.v3",
        "ctcc.demo_account_capture.v4",
        "ctcc.demo_account_capture.v5",
        "ctcc.demo_current_account_capture.v6",
    ] = "ctcc.demo_account_capture.v2"
    plan: (
        CurrentDemoAccountCapturePlanV6
        | AllProductDemoAccountCapturePlan
        | HistoricalAllProductDemoAccountCapturePlanV4
        | RegionalDemoAccountCapturePlan
        | DemoAccountCapturePlan
    )
    plan_sha256: Digest
    observations: tuple[DemoAccountObservation, ...] = Field(
        min_length=len(V6_CURRENT_STREAMS), max_length=256
    )
    barrier_completed_at: datetime
    completed_at: datetime
    row_count: int = Field(ge=0, le=8192)
    packet_sha256: Digest
    state: Literal["records_verified_incomplete_account"] = (
        "records_verified_incomplete_account"
    )
    incomplete_reasons: tuple[Name, ...] = Field(min_length=1, max_length=64)
    account_complete: Literal[False] = False
    execution_authority: Literal[False] = False
    source_authenticity_verified: Literal[False] = False

    _times = field_validator("barrier_completed_at", "completed_at")(_utc)

    @field_validator(
        "account_complete",
        "execution_authority",
        "source_authenticity_verified",
        mode="before",
    )
    @classmethod
    def no_authority(cls, value):
        if type(value) is not bool or value is not False:
            _fail("account_capture_cannot_grant_authority")
        return value


@dataclass(frozen=True)
class FrozenAccountPacket:
    payload: bytes
    sha256: str


_MODELS = {
    DemoAccountCapturePlan,
    RegionalDemoAccountCapturePlan,
    CurrentDemoAccountCapturePlanV6,
    AllProductDemoAccountCapturePlan,
    HistoricalAllProductDemoAccountCapturePlanV4,
    AccountRequest,
    AccountNumber,
    AccountSourceTime,
    AccountRow,
    DemoAccountObservation,
    DemoAccountPacket,
}


def _guard(value, depth=0, budget=None):
    """No serializer, subclass, iterator or duck-typed scalar before validation."""
    if budget is None:
        budget = [250000]
    budget[0] -= 1
    if depth > 20 or budget[0] < 0:
        _fail("record_traversal_limit")
    kind = type(value)
    if any(kind is model for model in _MODELS):
        raw = value.__dict__
        extra = value.__pydantic_extra__
        private = value.__pydantic_private__
        if (
            type(raw) is not dict
            or any(type(key) is not str for key in raw)
            or set(raw) != set(kind.model_fields)
            or (extra is not None and (type(extra) is not dict or len(extra) != 0))
            or (
                private is not None and (type(private) is not dict or len(private) != 0)
            )
        ):
            _fail("record_fields_invalid")
        for item in raw.values():
            _guard(item, depth + 1, budget)
    elif kind is tuple:
        if len(value) > 8192:
            _fail("record_sequence_limit")
        for item in value:
            _guard(item, depth + 1, budget)
    elif kind is str:
        if len(value) > MAX_RESPONSE_BYTES:
            _fail("record_text_limit")
    elif kind is bytes:
        if not 1 <= len(value) <= MAX_RESPONSE_BYTES:
            _fail("record_bytes_limit")
    elif kind is datetime:
        _utc(value)
    elif kind is Decimal:
        parts = value.as_tuple()
        if (
            not value.is_finite()
            or len(parts.digits) > 40
            or not -20 <= parts.exponent <= 20
        ):
            _fail("record_decimal_invalid")
    elif kind is int:
        if abs(value) > MAX_PACKET_BYTES:
            _fail("record_integer_limit")
    elif value is not None and kind is not bool:
        _fail("record_scalar_invalid")


def _plain(value):
    if any(type(value) is model for model in _MODELS):
        return {key: _plain(item) for key, item in value.__dict__.items()}
    if type(value) is tuple:
        return tuple(_plain(item) for item in value)
    return value


def _copy(value, expected):
    if type(value) is not expected:
        _fail("record_type_invalid")
    _guard(value)
    try:
        return expected.model_validate(_plain(value), strict=True)
    except (ValueError, TypeError, ArithmeticError, RecursionError):
        _fail("record_invalid")


def _json_value(value):
    if any(type(value) is model for model in _MODELS):
        return {key: _json_value(item) for key, item in value.__dict__.items()}
    if type(value) is dict:
        return {key: _json_value(item) for key, item in value.items()}
    if type(value) is tuple:
        return [_json_value(item) for item in value]
    if type(value) is bytes:
        return value.hex()
    if type(value) is datetime:
        return _utc(value).isoformat()
    if type(value) is Decimal:
        return str(value)
    return value


def _digest_record(value, excluded=()):
    return _sha(
        _canonical(
            {
                key: _json_value(item)
                for key, item in value.items()
                if key not in excluded
            }
        ).encode("utf-8")
    )


@_bounded_api
def plan_sha256(plan: DemoAccountCapturePlan) -> str:
    return _digest_record(_copy_plan(plan).__dict__)


def _copy_plan(plan):
    if type(plan) is CurrentDemoAccountCapturePlanV6:
        return _copy(plan, CurrentDemoAccountCapturePlanV6)
    if type(plan) is AllProductDemoAccountCapturePlan:
        return _copy(plan, AllProductDemoAccountCapturePlan)
    if type(plan) is HistoricalAllProductDemoAccountCapturePlanV4:
        return _copy(plan, HistoricalAllProductDemoAccountCapturePlanV4)
    if type(plan) is RegionalDemoAccountCapturePlan:
        return _copy(plan, RegionalDemoAccountCapturePlan)
    return _copy(plan, DemoAccountCapturePlan)


def _checked_plan(plan, expected):
    plan = _copy_plan(plan)
    if type(expected) is not str or re.fullmatch(r"[a-f0-9]{64}", expected) is None:
        _fail("external_plan_pin_invalid")
    if _digest_record(plan.__dict__) != expected:
        _fail("external_plan_pin_mismatch")
    return plan


def _milliseconds(value):
    delta = value - _EPOCH
    return str((delta.days * 86400 + delta.seconds) * 1000 + delta.microseconds // 1000)


@_bounded_api
def account_request(
    plan: DemoAccountCapturePlan, stream: str, after: str | None = None
) -> AccountRequest:
    plan = _copy_plan(plan)
    if type(stream) is not str or stream not in streams_for_plan(plan):
        _fail("stream_invalid")
    if after is not None and (type(after) is not str or _ID.fullmatch(after) is None):
        _fail("cursor_invalid")
    if after is not None and stream not in _CURSOR_FIELDS:
        _fail("cursor_not_supported")
    parameters = {}
    if stream in _CURSOR_FIELDS:
        parameters["limit"] = str(plan.page_size)
        if after is not None:
            parameters["after"] = after
    if stream.startswith("algo_"):
        parameters["ordType"] = stream.removeprefix("algo_")
    if stream in _HISTORY_VARIANTS:
        parameters["instType"] = _HISTORY_VARIANTS[stream][1]
    elif stream in _FILL_STREAMS | _ORDER_HISTORY_STREAMS | {
        "account_instruments"
    } and not (is_all_product_plan(plan) and stream == "fills_recent"):
        parameters["instType"] = "SWAP"
    if stream in _HISTORY_STREAMS:
        parameters["begin"] = _milliseconds(plan.history_start)
        parameters["end"] = _milliseconds(plan.history_end)
    if stream.startswith("leverage_"):
        parameters["instId"] = ",".join(plan.leverage_instrument_ids)
        parameters["mgnMode"] = stream.removeprefix("leverage_")
    return AccountRequest(
        stream=stream,
        origin=(
            plan.origin
            if type(plan)
            in {
                CurrentDemoAccountCapturePlanV6,
                RegionalDemoAccountCapturePlan,
                AllProductDemoAccountCapturePlan,
                HistoricalAllProductDemoAccountCapturePlanV4,
            }
            else "https://www.okx.com"
        ),
        endpoint=_ENDPOINTS[stream],
        parameters=tuple(sorted(parameters.items())),
    )


_NUMBERS = frozenset(
    {
        "eq",
        "totalEq",
        "availEq",
        "adjEq",
        "isoEq",
        "eqUsd",
        "cashBal",
        "availBal",
        "frozenBal",
        "upl",
        "disEq",
        "pos",
        "availPos",
        "avgPx",
        "markPx",
        "margin",
        "imr",
        "mmr",
        "lever",
        "notionalUsd",
        "notionalCcy",
        "baseBal",
        "quoteBal",
        "sz",
        "accFillSz",
        "fillPx",
        "fillSz",
        "fee",
        "pnl",
        "fillPnl",
        "bal",
        "balChg",
        "posBal",
        "posBalChg",
        "interest",
        "px",
        "ordPx",
        "slOrdPx",
        "slTriggerPx",
        "tpOrdPx",
        "tpTriggerPx",
        "triggerPx",
        "activePx",
        "actualSz",
        "actualPx",
        "callbackRatio",
        "callbackSpread",
        "closeFraction",
        "liab",
        "borrowFroz",
        "ordFroz",
        "ctVal",
        "ctMult",
        "lotSz",
        "minSz",
        "maxLmtSz",
        "tickSz",
    }
)
_CLOCKS = {
    "uTime": "source_update",
    "cTime": "record_creation",
    "fillTime": "trade_match",
    "ts": "record_generation",
}
_SECRET_KEYS = frozenset(
    {
        "apikey",
        "apisecret",
        "secret",
        "secretkey",
        "passphrase",
        "signature",
        "authorization",
        "proxyauthorization",
        "headers",
        "requestheaders",
        "responseheaders",
        "cookie",
        "setcookie",
        "credentials",
        "credential",
        "token",
        "accesstoken",
        "refreshtoken",
    }
)


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            _fail("duplicate_json_key")
        result[key] = value
    return result


def _invalid_json_number(_):
    _fail("json_number_invalid")


def _json_tree(value, *, wire, depth=0, budget=None):
    if budget is None:
        budget = [250000 if not wire else 50000]
    budget[0] -= 1
    if depth > (20 if not wire else 12) or budget[0] < 0:
        _fail("json_traversal_limit")
    kind = type(value)
    if kind is dict:
        if len(value) > 256:
            _fail("json_object_limit")
        for key, item in value.items():
            if len(key) > 96:
                _fail("json_key_limit")
            normalized = re.sub(r"[^a-z0-9]", "", key.lower())
            if normalized in _SECRET_KEYS or normalized.startswith("okaccess"):
                _fail("secret_field_forbidden")
            _json_tree(item, wire=wire, depth=depth + 1, budget=budget)
    elif kind is list:
        if len(value) > 8192:
            _fail("json_array_limit")
        for item in value:
            _json_tree(item, wire=wire, depth=depth + 1, budget=budget)
    elif kind is str:
        if len(value) > (4096 if wire else MAX_RESPONSE_BYTES * 2):
            _fail("json_text_limit")
        try:
            value.encode("utf-8")
        except UnicodeError:
            _fail("json_unicode_invalid")
    elif kind is int:
        if wire or abs(value) > MAX_PACKET_BYTES:
            _fail("json_number_invalid")
    elif kind is not bool and value is not None:
        _fail("json_scalar_invalid")


def _decode_json(raw, *, limit, wire):
    if type(raw) is not bytes or not 1 <= len(raw) <= limit:
        _fail("response_bytes_invalid" if wire else "packet_bytes_invalid")
    try:
        payload = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_pairs,
            parse_float=_invalid_json_number,
            parse_constant=_invalid_json_number,
        )
        _json_tree(payload, wire=wire)
        canonical = _canonical(payload)
        if len(canonical.encode("utf-8")) > limit:
            _fail("canonical_bytes_limit")
        return payload, canonical
    except AccountCaptureError:
        raise
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        _fail("response_json_invalid" if wire else "packet_json_invalid")


def _required_text(row, key, *, identifier=False):
    value = row.get(key)
    if type(value) is not str or not 1 <= len(value) <= 96 or value != value.strip():
        _fail("source_identity_field_invalid")
    if identifier and _ID.fullmatch(value) is None:
        _fail("source_identifier_invalid")
    return value


def _fill_identifiers(row):
    # Fills use billId for row identity and pagination. Exceptional trade/order
    # IDs remain exact source strings, never synthetic lineage or cursor IDs.
    trade_id = _required_text(row, "tradeId")
    if trade_id.startswith("-"):
        if _ID.fullmatch(trade_id[1:]) is None:
            _fail("source_identifier_invalid")
        if _required_text(row, "subType") not in {
            "100",
            "101",
            "102",
            "103",
            "104",
            "105",
            "106",
            "107",
            "125",
            "126",
            "127",
            "128",
        }:
            _fail("fill_identifier_context_invalid")
    elif _ID.fullmatch(trade_id) is None:
        _fail("source_identifier_invalid")
    if type(row.get("ordId")) is str and row["ordId"] == "":
        if _required_text(row, "subType") not in {
            "204",
            "205",
            "206",
            "207",
            "208",
            "209",
        }:
            _fail("fill_identifier_context_invalid")
    else:
        _required_text(row, "ordId", identifier=True)


def _time_record(path, raw, semantics):
    if raw is None or raw == "":
        return AccountSourceTime(path=path, raw=raw, value=None, semantics=semantics)
    if type(raw) is not str or re.fullmatch(r"[1-9][0-9]{0,14}", raw) is None:
        _fail("source_timestamp_invalid")
    try:
        value = _EPOCH + timedelta(milliseconds=int(raw))
    except (OverflowError, ValueError):
        _fail("source_timestamp_invalid")
    return AccountSourceTime(path=path, raw=raw, value=value, semantics=semantics)


def _gateway_time(raw):
    # OKX REST envelope inTime/outTime are Unix microseconds, unlike row
    # timestamps. Never coerce milliseconds, floats, or malformed strings.
    if type(raw) is not str or re.fullmatch(r"[1-9][0-9]{0,16}", raw) is None:
        _fail("response_gateway_time_invalid")
    try:
        return _EPOCH + timedelta(microseconds=int(raw))
    except (OverflowError, ValueError):
        _fail("response_gateway_time_invalid")


def _number_record(path, raw):
    if raw is None or raw == "":
        return AccountNumber(path=path, raw=raw, value=None)
    if type(raw) is not str or _DECIMAL.fullmatch(raw) is None:
        _fail("source_decimal_invalid")
    return AccountNumber(path=path, raw=raw, value=Decimal(raw))


def _row_record(row, stream, plan, received):
    if type(row) is not dict:
        _fail("source_row_invalid")
    instrument = None
    required_numbers = set()
    required_clocks = set()
    if stream.startswith("config_"):
        uid = _required_text(row, "uid", identifier=True)
        main_uid = _required_text(row, "mainUid", identifier=True)
        if uid != plan.expected_uid or main_uid != plan.expected_main_uid:
            _fail("account_identity_mismatch")
        if _required_text(row, "acctLv") not in {"1", "2", "3", "4"} or _required_text(
            row, "posMode"
        ) not in {"net_mode", "long_short_mode"}:
            _fail("account_mode_unsupported")
        row_id = uid
    elif stream in {"balance", "account_position_risk"}:
        row_id = stream
        arrays = ("details",) if stream == "balance" else ("balData", "posData")
        for name in arrays:
            if type(row.get(name)) is not list or len(row[name]) > 2048:
                _fail("source_inventory_invalid")
            identities = set()
            for item in row[name]:
                if type(item) is not dict:
                    _fail("source_inventory_invalid")
                key = "posId" if name == "posData" else "ccy"
                identity = _required_text(item, key, identifier=key == "posId")
                if identity in identities:
                    _fail("duplicate_source_identity")
                identities.add(identity)
                if name == "posData":
                    _required_text(item, "instId")
        required_numbers = {"totalEq", "availEq"} if stream == "balance" else {"adjEq"}
        required_clocks = {"uTime"} if stream == "balance" else {"ts"}
    elif stream == "account_instruments":
        instrument = _required_text(row, "instId")
        row_id = instrument
        if _required_text(row, "instType") != "SWAP":
            _fail("history_instrument_scope_mismatch")
        for field in ("ctType", "settleCcy", "ctValCcy", "state"):
            _required_text(row, field)
        required_numbers = {"ctVal", "ctMult", "lotSz", "minSz", "maxLmtSz", "lever"}
    elif stream.startswith("leverage_"):
        instrument = _required_text(row, "instId")
        if instrument not in plan.leverage_instrument_ids:
            _fail("leverage_instrument_scope_mismatch")
        if _required_text(row, "mgnMode") != stream.removeprefix("leverage_"):
            _fail("margin_mode_invalid")
        position_side = _required_text(row, "posSide")
        if position_side not in {"net", "long", "short"}:
            _fail("position_side_invalid")
        row_id = instrument + ":" + position_side
        required_numbers = {"lever"}
    else:
        row_id = _required_text(
            row, _CURSOR_FIELDS.get(stream, "posId"), identifier=True
        )
        if stream not in _BILL_STREAMS:
            instrument = _required_text(row, "instId")
            kind = _required_text(row, "instType")
            if is_all_product_plan(plan):
                if kind not in INSTRUMENT_TYPES:
                    _fail("source_instrument_type_invalid")
                if stream == "positions" and kind == "SPOT":
                    _fail("source_instrument_type_invalid")
                if stream == "algo_chase" and kind not in {"SWAP", "FUTURES"}:
                    _fail("source_instrument_type_invalid")
                if stream.startswith("algo_") and kind not in {
                    "SPOT",
                    "MARGIN",
                    "SWAP",
                    "FUTURES",
                }:
                    _fail("source_instrument_type_invalid")
                expected_kind = _HISTORY_VARIANTS.get(stream, (None, None))[1]
                if expected_kind is not None and kind != expected_kind:
                    _fail("history_instrument_scope_mismatch")
                # Non-derivative responses can explicitly carry an empty side;
                # preserve it, never turn it into a net/long/short risk claim.
                position_side = row.get("posSide")
                allowed_sides = {"net", "long", "short"}
                if kind not in {"SWAP", "FUTURES"} and stream != "positions":
                    allowed_sides.add("")
                if type(position_side) is not str or position_side not in allowed_sides:
                    _fail("position_side_invalid")
            else:
                if stream in _FILL_STREAMS | _ORDER_HISTORY_STREAMS and kind != "SWAP":
                    _fail("history_instrument_scope_mismatch")
                if _required_text(row, "posSide") not in {"net", "long", "short"}:
                    _fail("position_side_invalid")
        elif row.get("instId") not in (None, ""):
            instrument = _required_text(row, "instId")
        if stream == "positions":
            # This parser does not infer a missing margin currency from instId.
            _required_text(row, "ccy")
            if _required_text(row, "mgnMode") not in {"cross", "isolated"}:
                _fail("margin_mode_invalid")
            required_numbers = {"pos", "margin", "avgPx", "markPx", "notionalUsd"}
            required_clocks = {"cTime", "uTime"}
        elif stream in {"orders_pending"} | _ORDER_HISTORY_STREAMS:
            if _required_text(row, "side") not in {"buy", "sell"}:
                _fail("order_side_invalid")
            state = _required_text(row, "state")
            allowed = (
                {"live", "partially_filled"}
                if stream == "orders_pending"
                else {"filled", "canceled", "mmp_canceled"}
            )
            if state not in allowed:
                _fail("order_state_invalid")
            required_numbers = {"sz", "accFillSz"}
            required_clocks = {"cTime", "uTime"}
        elif stream.startswith("algo_"):
            if _required_text(row, "ordType") != stream.removeprefix("algo_"):
                _fail("algo_query_scope_mismatch")
            if _required_text(row, "side") not in {"buy", "sell"}:
                _fail("order_side_invalid")
            if _required_text(row, "state") not in {"live", "pause"}:
                _fail("algo_state_invalid")
            required_numbers = {"sz"}
            if stream in {
                "algo_conditional",
                "algo_oco",
                "algo_trigger",
                "algo_move_order_stop",
            }:
                required_numbers.update(
                    {"slTriggerPx", "slOrdPx", "tpTriggerPx", "tpOrdPx"}
                )
            required_clocks = {"cTime"}
        elif stream in _FILL_STREAMS:
            _fill_identifiers(row)
            _required_text(row, "feeCcy")
            if _required_text(row, "side") not in {"buy", "sell"}:
                _fail("order_side_invalid")
            required_numbers = {"fillPx", "fillSz", "fee", "fillPnl"}
            required_clocks = {"fillTime", "ts"}
        else:
            for field in ("ccy", "type", "subType"):
                _required_text(row, field)
            required_numbers = {"bal", "balChg", "fee", "pnl"}
            required_clocks = {"ts"}
    numbers, times, missing = [], [], []

    def walk(value, prefix=""):
        if type(value) is dict:
            for key, expected in (
                ("uid", plan.expected_uid),
                ("mainUid", plan.expected_main_uid),
            ):
                if key in value and value[key] != expected:
                    _fail("account_identity_mismatch")
            for key, part in sorted(value.items()):
                path = f"{prefix}.{key}" if prefix else key
                if key in _NUMBERS:
                    numbers.append(_number_record(path, part))
                elif key in _CLOCKS:
                    semantics = (
                        "source_update"
                        if key == "ts"
                        and stream in {"account_position_risk"} | _BILL_STREAMS
                        else _CLOCKS[key]
                    )
                    times.append(_time_record(path, part, semantics))
                elif type(part) in {dict, list}:
                    walk(part, path)
        elif type(value) is list:
            for index, part in enumerate(value):
                walk(part, f"{prefix}[{index}]")

    walk(row)
    for field in sorted(required_numbers - set(row)):
        numbers.append(_number_record(field, None))
    for field in sorted(required_clocks - set(row)):
        semantics = (
            "source_update"
            if field == "ts" and stream in {"account_position_risk"} | _BILL_STREAMS
            else _CLOCKS[field]
        )
        times.append(_time_record(field, None, semantics))
    for item in (*numbers, *times):
        if item.value is None:
            missing.append(item.path)
    for item in times:
        if item.value is not None and item.value > received:
            _fail("future_source_timestamp")
    roots = {item.path: item.value for item in times}
    if (
        roots.get("cTime") is not None
        and roots.get("uTime") is not None
        and roots["cTime"] > roots["uTime"]
    ):
        _fail("source_lifecycle_reversed")
    for earlier, later in (("cTime", "fillTime"), ("fillTime", "uTime")):
        if (
            roots.get(earlier) is not None
            and roots.get(later) is not None
            and roots[earlier] > roots[later]
        ):
            _fail("source_lifecycle_reversed")
    if (
        stream in _FILL_STREAMS
        and roots.get("fillTime") is not None
        and roots.get("ts") is not None
        and roots["fillTime"] > roots["ts"]
    ):
        _fail("source_lifecycle_reversed")
    amounts = {item.path: item.value for item in numbers}
    spot_market_order = (
        is_all_product_plan(plan)
        and row.get("instType") == "SPOT"
        and row.get("ordType") == "market"
        and stream in {"orders_pending"} | _ORDER_HISTORY_STREAMS
    )
    if spot_market_order:
        if row.get("tgtCcy") not in {None, "", "base_ccy", "quote_ccy"}:
            _fail("source_quantity_unit_invalid")
        if row.get("tgtCcy") in {None, ""}:
            missing.append("tgtCcy")
    for name in ("sz", "accFillSz", "fillSz"):
        if amounts.get(name) is not None and amounts[name] < 0:
            _fail("source_quantity_negative")
    if (
        amounts.get("sz") is not None
        and amounts.get("accFillSz") is not None
        and amounts["accFillSz"] > amounts["sz"]
        and not (spot_market_order and row.get("tgtCcy") != "base_ccy")
    ):
        _fail("filled_quantity_exceeds_order")
    if stream in _HISTORY_STREAMS:
        key = "cTime" if stream in _ORDER_HISTORY_STREAMS else "ts"
        if (
            roots.get(key) is not None
            and not plan.history_start <= roots[key] <= plan.history_end
        ):
            _fail("history_row_outside_query")
    return AccountRow(
        row_id=row_id,
        instrument_id=instrument,
        canonical_json=_canonical(row),
        numbers=tuple(sorted(numbers, key=lambda item: item.path)),
        source_times=tuple(sorted(times, key=lambda item: item.path)),
        missing_fields=tuple(sorted(set(missing))),
    )


@_bounded_api
def parse_demo_account_observation(
    response_body: bytes,
    *,
    plan: DemoAccountCapturePlan,
    expected_plan_sha256: str,
    stream: str,
    request_started_at: datetime,
    headers_received_at: datetime,
    body_completed_at: datetime,
    barrier_completed_at: datetime,
    page_index: int = 0,
    after: str | None = None,
    previous_page_sha256: str | None = None,
    identity_receipt_sha256: str | None = None,
) -> DemoAccountObservation:
    """Parse a supplied GET receipt; no collector or authenticity is implied."""
    plan = _checked_plan(plan, expected_plan_sha256)
    request = account_request(plan, stream, after)
    if type(page_index) is not int or not 0 <= page_index < plan.max_pages_per_stream:
        _fail("page_index_invalid")
    for digest in (previous_page_sha256, identity_receipt_sha256):
        if digest is not None and (
            type(digest) is not str or re.fullmatch(r"[a-f0-9]{64}", digest) is None
        ):
            _fail("receipt_pin_invalid")
    if (page_index == 0) != (after is None and previous_page_sha256 is None):
        _fail("page_chain_metadata_invalid")
    if page_index > 0 and (after is None or previous_page_sha256 is None):
        _fail("page_chain_metadata_invalid")
    if stream not in _CURSOR_FIELDS and page_index != 0:
        _fail("nonpaginated_page_invalid")
    if (stream == "config_before") != (identity_receipt_sha256 is None):
        _fail("identity_receipt_required")
    started, received, completed, barrier = map(
        _utc,
        (
            request_started_at,
            headers_received_at,
            body_completed_at,
            barrier_completed_at,
        ),
    )
    if not barrier < started <= received <= completed or started < plan.created_at:
        _fail("request_clock_invalid")
    if completed - started > timedelta(seconds=plan.max_request_seconds):
        _fail("request_deadline_exceeded")
    payload, canonical = _decode_json(
        response_body, limit=plan.max_response_bytes, wire=True
    )
    if (
        type(payload) is not dict
        or type(payload.get("code")) is not str
        or payload["code"] != "0"
        or type(payload.get("msg")) is not str
        or type(payload.get("data")) is not list
    ):
        _fail("response_envelope_invalid")
    if set(payload) - {"code", "msg", "data", "inTime", "outTime"}:
        _fail("response_envelope_fields_invalid")
    if ("inTime" in payload) != ("outTime" in payload):
        _fail("response_gateway_time_invalid")
    if "inTime" in payload:
        gateway_in = _gateway_time(payload["inTime"])
        gateway_out = _gateway_time(payload["outTime"])
        if not started <= gateway_in <= gateway_out <= received:
            _fail("response_gateway_time_invalid")
    raw_rows = payload["data"]
    maximum = plan.page_size if stream in _CURSOR_FIELDS else 2048
    if len(raw_rows) > maximum:
        _fail("response_row_limit")
    if (
        stream in {"config_before", "config_after", "account_position_risk", "balance"}
        and len(raw_rows) != 1
    ):
        _fail("response_cardinality_invalid")
    rows = tuple(_row_record(row, stream, plan, received) for row in raw_rows)
    ids = tuple(row.row_id for row in rows)
    if len(set(ids)) != len(ids):
        _fail("duplicate_source_identity")
    if stream in _CURSOR_FIELDS:
        numeric_ids = tuple(int(value) for value in ids)
        if any(left <= right for left, right in itertools.pairwise(numeric_ids)):
            _fail("source_cursor_order_invalid")
        if after is not None and any(value >= int(after) for value in numeric_ids):
            _fail("source_cursor_not_exclusive")
    fields = {
        "request": request,
        "plan_sha256": expected_plan_sha256,
        "environment": "demo",
        "session_binding_id": plan.session_binding_id,
        "identity_receipt_sha256": identity_receipt_sha256,
        "identity_binding": "config_response"
        if stream.startswith("config_")
        else "request_context",
        "page_index": page_index,
        "after": after,
        "previous_page_sha256": previous_page_sha256,
        "request_started_at": started,
        "headers_received_at": received,
        "body_completed_at": completed,
        "barrier_completed_at": barrier,
        "response_body": response_body,
        "body_sha256": _sha(response_body),
        "body_size_bytes": len(response_body),
        "canonical_json": canonical,
        "canonical_sha256": _sha(canonical.encode("utf-8")),
        "rows": rows,
        "terminal": not rows if stream in _CURSOR_FIELDS else True,
    }
    return DemoAccountObservation(**fields, receipt_sha256=_digest_record(fields))


def _replay_observation(item, plan, pin, barrier):
    item = _copy(item, DemoAccountObservation)
    replay = parse_demo_account_observation(
        item.response_body,
        plan=plan,
        expected_plan_sha256=pin,
        stream=item.request.stream,
        page_index=item.page_index,
        after=item.after,
        previous_page_sha256=item.previous_page_sha256,
        identity_receipt_sha256=item.identity_receipt_sha256,
        request_started_at=item.request_started_at,
        headers_received_at=item.headers_received_at,
        body_completed_at=item.body_completed_at,
        barrier_completed_at=barrier,
    )
    if item != replay:
        _fail("observation_replay_mismatch")
    return replay


@_bounded_api
def verify_demo_account_records(
    observations: tuple[DemoAccountObservation, ...],
    *,
    plan: DemoAccountCapturePlan,
    expected_plan_sha256: str,
    barrier_completed_at: datetime,
) -> DemoAccountPacket:
    """Require the entire bounded requested inventory, still account-incomplete."""
    plan = _checked_plan(plan, expected_plan_sha256)
    barrier = _utc(barrier_completed_at)
    streams = streams_for_plan(plan)
    if (
        type(observations) is not tuple
        or not len(streams) <= len(observations) <= plan.max_total_pages
    ):
        _fail("inventory_page_count_invalid")
    _guard(observations)
    if any(type(item) is not DemoAccountObservation for item in observations):
        _fail("record_type_invalid")
    if sum(len(item.response_body) for item in observations) > plan.max_total_bytes:
        _fail("inventory_bytes_limit")
    verified = tuple(
        _replay_observation(item, plan, expected_plan_sha256, barrier)
        for item in observations
    )
    if verified[0].request.stream != "config_before":
        _fail("inventory_order_invalid")
    identity = verified[0].receipt_sha256
    groups = {stream: [] for stream in streams}
    prior = None
    for item in verified:
        if prior is not None:
            if item.request_started_at < prior.body_completed_at:
                _fail("cross_request_clock_reversed")
            if item.identity_receipt_sha256 != identity:
                _fail("identity_chain_mismatch")
            if streams.index(item.request.stream) < streams.index(prior.request.stream):
                _fail("inventory_order_invalid")
        groups[item.request.stream].append(item)
        prior = item
    if verified[-1].body_completed_at - verified[0].request_started_at > timedelta(
        seconds=plan.max_batch_seconds
    ):
        _fail("batch_deadline_exceeded")
    algo_ids = set()
    product_history_ids = set()
    for stream, pages in groups.items():
        if not pages or len(pages) > plan.max_pages_per_stream:
            _fail("inventory_stream_missing_or_limit")
        if stream not in _CURSOR_FIELDS:
            if len(pages) != 1:
                _fail("nonpaginated_inventory_duplicate")
            continue
        if not pages[-1].terminal:
            _fail("empty_terminal_page_required")
        seen = set()
        for index, page in enumerate(pages):
            if page.page_index != index:
                _fail("page_index_gap")
            if index:
                previous = pages[index - 1]
                if previous.terminal:
                    _fail("page_after_terminal")
                if (
                    page.previous_page_sha256 != previous.receipt_sha256
                    or page.after != previous.rows[-1].row_id
                ):
                    _fail("page_chain_mismatch")
            for row in page.rows:
                if row.row_id in seen:
                    _fail("duplicate_or_conflicting_page_identity")
                seen.add(row.row_id)
                if stream in _HISTORY_VARIANTS:
                    identity_key = (stream_family(stream), row.row_id)
                    if identity_key in product_history_ids:
                        _fail("conflicting_history_product_identity")
                    product_history_ids.add(identity_key)
                if stream.startswith("algo_"):
                    if row.row_id in algo_ids:
                        _fail("conflicting_algo_type_identity")
                    algo_ids.add(row.row_id)
    before, after_config = (
        json.loads(groups[name][0].rows[0].canonical_json)
        for name in ("config_before", "config_after")
    )
    if any(
        before[key] != after_config[key]
        for key in ("uid", "mainUid", "acctLv", "posMode")
    ):
        _fail("account_mode_changed")
    row_count = sum(len(item.rows) for item in verified)
    if row_count > plan.max_total_rows:
        _fail("inventory_rows_limit")
    gaps = set(_BASE_GAPS)
    if is_all_product_plan(plan):
        gaps.remove("non_swap_history_not_requested")
        gaps.add("all_product_metadata_coverage_unverified")
    if type(plan) is CurrentDemoAccountCapturePlanV6:
        gaps.add("separate_history_source_join_required")
    if any(row.missing_fields for item in verified for row in item.rows):
        gaps.add("source_fields_missing")
    if any(
        not row.source_times or any(time.value is None for time in row.source_times)
        for item in verified
        for row in item.rows
    ):
        gaps.add("source_clock_coverage_incomplete")
    fields = {
        "schema_version": (
            "ctcc.demo_current_account_capture.v6"
            if type(plan) is CurrentDemoAccountCapturePlanV6
            else "ctcc.demo_account_capture.v5"
            if type(plan) is AllProductDemoAccountCapturePlan
            else "ctcc.demo_account_capture.v4"
            if type(plan) is HistoricalAllProductDemoAccountCapturePlanV4
            else "ctcc.demo_account_capture.v3"
            if type(plan) is RegionalDemoAccountCapturePlan
            else "ctcc.demo_account_capture.v2"
        ),
        "plan": plan,
        "plan_sha256": expected_plan_sha256,
        "observations": verified,
        "barrier_completed_at": barrier,
        "completed_at": verified[-1].body_completed_at,
        "row_count": row_count,
        "state": "records_verified_incomplete_account",
        "incomplete_reasons": tuple(sorted(gaps)),
        "account_complete": False,
        "execution_authority": False,
        "source_authenticity_verified": False,
    }
    return DemoAccountPacket(**fields, packet_sha256=_digest_record(fields))


def _replay_packet(packet, expected_plan_sha256):
    packet = _copy(packet, DemoAccountPacket)
    replay = verify_demo_account_records(
        packet.observations,
        plan=packet.plan,
        expected_plan_sha256=expected_plan_sha256,
        barrier_completed_at=packet.barrier_completed_at,
    )
    if packet != replay:
        _fail("packet_replay_mismatch")
    return replay


@_bounded_api
def freeze_demo_account_packet(
    packet: DemoAccountPacket, *, expected_plan_sha256: str
) -> FrozenAccountPacket:
    checked = _replay_packet(packet, expected_plan_sha256)
    payload = _canonical(_json_value(checked)).encode("utf-8")
    if len(payload) > MAX_PACKET_BYTES:
        _fail("packet_bytes_limit")
    return FrozenAccountPacket(payload=payload, sha256=_sha(payload))


@_bounded_api
def verify_demo_account_packet(
    payload: bytes, *, expected_sha256: str, expected_plan_sha256: str
) -> DemoAccountPacket:
    if (
        type(expected_sha256) is not str
        or re.fullmatch(r"[a-f0-9]{64}", expected_sha256) is None
    ):
        _fail("external_packet_pin_invalid")
    if type(payload) is not bytes or not 1 <= len(payload) <= MAX_PACKET_BYTES:
        _fail("packet_bytes_invalid")
    if _sha(payload) != expected_sha256:
        _fail("external_packet_pin_mismatch")
    _, canonical = _decode_json(payload, limit=MAX_PACKET_BYTES, wire=False)
    if canonical.encode("utf-8") != payload:
        _fail("packet_noncanonical")
    try:
        packet = DemoAccountPacket.model_validate_json(payload, strict=True)
    except (ValueError, TypeError, ArithmeticError, RecursionError):
        _fail("packet_schema_invalid")
    return _replay_packet(packet, expected_plan_sha256)
