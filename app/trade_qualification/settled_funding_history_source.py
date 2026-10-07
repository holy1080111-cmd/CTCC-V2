"""Pure raw-page replay for public settled SWAP funding history.

Presented request clocks, region and response bytes are evidence to inspect, not
native acquisition or proof that a public event applied to a Demo account.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation

from app.domain.source_primitives import canonical, demo_public_v2_route, sha

ENDPOINT = "/api/v5/public/funding-rate-history"
MAX_PAGES = 16
MAX_PAGE_BYTES = 262_144
MAX_ROWS_PER_PAGE = 400
MAX_RECEIPT_BYTES = 4_194_304
MAX_REQUEST_NS = 30_000_000_000
MAX_CLOCK_DELTA_NS = 2_000_000_000
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_INSTRUMENT = re.compile(r"[A-Z0-9]{1,16}-[A-Z0-9]{1,16}-SWAP\Z")
_TIMESTAMP = re.compile(r"[1-9][0-9]{0,15}\Z")
_RATE = re.compile(r"-?(?:0|[1-9][0-9]{0,19})(?:\.[0-9]{1,100})?\Z")

POLICY_BYTES = canonical(
    {
        "version": "ctcc.settled_funding_history_raw_diagnostic.v1",
        "endpoint": ENDPOINT,
        "scope": "exact_reviewed_region_and_single_SWAP_instrument_claim",
        "query": "instId_limit_400_after_previous_oldest_fundingTime",
        "ordering": "strict_newest_first_exclusive_cursor_terminal_empty_required",
        "source": "original_raw_page_bytes_and_supplied_request_receive_clock_pairs",
        "retention": "documented_three_month_limit_never_complete_account_proof",
        "economic_meaning": "public_realizedRate_at_fundingTime_not_Demo_payment",
        "account_complete": False,
        "execution_authority": False,
        "admission": "DENY",
    }
)
POLICY_SHA256 = sha(POLICY_BYTES)


class SettledFundingHistoryError(ValueError):
    """Fixed public-source diagnostic rejection codes."""


def _deny(code):
    raise SettledFundingHistoryError(code)


@dataclass(frozen=True, slots=True)
class FundingHistoryScope:
    registration_region: str
    instrument_id: str
    required_from_at: datetime


@dataclass(frozen=True, slots=True, repr=False)
class RawFundingHistoryPage:
    method: str
    origin: str
    endpoint: str
    request_query: tuple[tuple[str, str], ...]
    simulated_trading_header: str
    request_started_at: datetime
    response_received_at: datetime
    request_started_monotonic_ns: int
    response_received_monotonic_ns: int
    http_status: int
    raw_body: bytes


@dataclass(frozen=True, slots=True, repr=False)
class SettledFundingHistoryReplay:
    receipt_json: bytes
    raw_pages: tuple[bytes, ...]

    @property
    def receipt_sha256(self):
        return sha(self.receipt_json)

    @property
    def account_complete(self):
        return False

    @property
    def execution_authority(self):
        return False

    @property
    def admission(self):
        return "DENY"


def _utc(value):
    if type(value) is not datetime or value.tzinfo is not UTC:
        _deny("funding_history_exact_UTC_required")
    return value


def _scope(scope):
    if type(scope) is not FundingHistoryScope:
        _deny("funding_history_scope_invalid")
    try:
        origin = demo_public_v2_route(scope.registration_region)[0]
    except ValueError:
        _deny("funding_history_region_unreviewed")
    if (
        type(scope.instrument_id) is not str
        or _INSTRUMENT.fullmatch(scope.instrument_id) is None
    ):
        _deny("funding_history_instrument_invalid")
    _utc(scope.required_from_at)
    if scope.required_from_at < _EPOCH:
        _deny("funding_history_required_start_invalid")
    return origin


def _elapsed_ns(later, earlier):
    span = later - earlier
    return (
        span.days * 86_400 + span.seconds
    ) * 1_000_000_000 + span.microseconds * 1000


def _page_document(page):
    if type(page) is not RawFundingHistoryPage:
        _deny("funding_history_page_invalid")
    started, received = _utc(page.request_started_at), _utc(page.response_received_at)
    if (
        type(page.method) is not str
        or page.method != "GET"
        or type(page.origin) is not str
        or type(page.endpoint) is not str
        or type(page.request_query) is not tuple
        or any(
            type(pair) is not tuple
            or len(pair) != 2
            or any(type(value) is not str for value in pair)
            for pair in page.request_query
        )
        or type(page.simulated_trading_header) is not str
        or page.simulated_trading_header != "1"
        or type(page.request_started_monotonic_ns) is not int
        or type(page.response_received_monotonic_ns) is not int
        or type(page.http_status) is not int
        or type(page.raw_body) is not bytes
        or not 0 < len(page.raw_body) <= MAX_PAGE_BYTES
    ):
        _deny("funding_history_page_invalid")
    monotonic_elapsed = (
        page.response_received_monotonic_ns - page.request_started_monotonic_ns
    )
    wall_elapsed = _elapsed_ns(received, started)
    if (
        page.request_started_monotonic_ns < 0
        or not 0 <= monotonic_elapsed <= MAX_REQUEST_NS
        or not 0 <= wall_elapsed <= MAX_REQUEST_NS
        or abs(wall_elapsed - monotonic_elapsed) > MAX_CLOCK_DELTA_NS
    ):
        _deny("funding_history_clock_invalid")
    if page.http_status != 200:
        _deny("funding_history_http_status_invalid")
    return {
        "method": page.method,
        "origin": page.origin,
        "endpoint": page.endpoint,
        "request_query": [list(pair) for pair in page.request_query],
        "simulated_trading_header": page.simulated_trading_header,
        "request_started_at": started.isoformat(),
        "response_received_at": received.isoformat(),
        "request_started_monotonic_ns": page.request_started_monotonic_ns,
        "response_received_monotonic_ns": page.response_received_monotonic_ns,
        "http_status": page.http_status,
        "raw_sha256": sha(page.raw_body),
        "raw_bytes": len(page.raw_body),
    }


def source_pages_sha256(scope, pages):
    """Bind original bytes and supplied clocks; this hash is not authenticity."""
    _scope(scope)
    if type(pages) is not tuple or not 1 <= len(pages) <= MAX_PAGES:
        _deny("funding_history_page_bound")
    documents = [_page_document(page) for page in pages]
    return sha(
        canonical(
            {
                "scope": [
                    scope.registration_region,
                    scope.instrument_id,
                    scope.required_from_at.isoformat(),
                ],
                "pages": documents,
            }
        )
    )


def _unique_object(pairs):
    value = {}
    for key, member in pairs:
        if key in value:
            _deny("funding_history_duplicate_json_key")
        value[key] = member
    return value


def _reject_constant(_):
    _deny("funding_history_response_invalid")


def _rows(raw):
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except SettledFundingHistoryError:
        raise
    except (UnicodeDecodeError, ValueError, TypeError):
        _deny("funding_history_response_invalid")
    if (
        type(value) is not dict
        or set(value) != {"code", "msg", "data"}
        or value["code"] != "0"
        or value["msg"] != ""
        or type(value["data"]) is not list
        or len(value["data"]) > MAX_ROWS_PER_PAGE
    ):
        _deny("funding_history_response_invalid")
    return value["data"]


def _event(row, instrument_id):
    if (
        type(row) is not dict
        or row.get("instType") != "SWAP"
        or row.get("instId") != instrument_id
    ):
        _deny("funding_history_event_scope_invalid")
    instant, rate = row.get("fundingTime"), row.get("realizedRate")
    if (
        type(instant) is not str
        or _TIMESTAMP.fullmatch(instant) is None
        or type(rate) is not str
        or _RATE.fullmatch(rate) is None
    ):
        _deny("funding_history_event_invalid")
    try:
        number = Decimal(rate)
    except InvalidOperation:
        _deny("funding_history_event_invalid")
    if not number.is_finite() or abs(number.as_tuple().exponent) > 100:
        _deny("funding_history_event_invalid")
    try:
        stamp = _EPOCH + timedelta(milliseconds=int(instant))
        row_sha = sha(canonical(row))
    except (OverflowError, ValueError, TypeError):
        _deny("funding_history_event_invalid")
    return instant, rate, stamp, row_sha


def replay_settled_funding_history_pages(
    scope,
    pages,
    *,
    expected_source_pages_sha256,
    expected_policy_sha256=POLICY_SHA256,
):
    """Inspect a presented complete page chain, with no account or risk upgrade."""
    origin = _scope(scope)
    if (
        type(expected_policy_sha256) is not str
        or expected_policy_sha256 != POLICY_SHA256
    ):
        _deny("funding_history_policy_mismatch")
    pin = source_pages_sha256(scope, pages)
    if (
        type(expected_source_pages_sha256) is not str
        or pin != expected_source_pages_sha256
    ):
        _deny("funding_history_source_pin_mismatch")
    if scope.required_from_at > pages[0].request_started_at:
        _deny("funding_history_required_start_invalid")

    documents, events, seen = [], [], {}
    expected_after = None
    prior_oldest = None
    previous_page = None
    for page_index, page in enumerate(pages):
        document = _page_document(page)
        query = (("instId", scope.instrument_id), ("limit", "400"))
        if expected_after is not None:
            query += (("after", expected_after),)
        if (
            page.origin != origin
            or page.endpoint != ENDPOINT
            or page.request_query != query
        ):
            _deny("funding_history_query_invalid")
        if previous_page is not None and (
            page.request_started_at < previous_page.response_received_at
            or page.request_started_monotonic_ns
            < previous_page.response_received_monotonic_ns
        ):
            _deny("funding_history_clock_invalid")
        rows = _rows(page.raw_body)
        document["row_count"] = len(rows)
        document["terminal_empty"] = not rows
        documents.append(document)
        if not rows:
            if page_index != len(pages) - 1:
                _deny("funding_history_terminal_not_final")
            break
        last_time = None
        for row_ordinal, row in enumerate(rows):
            funding_time, rate, settlement_at, row_sha = _event(
                row, scope.instrument_id
            )
            if settlement_at > page.response_received_at:
                _deny("funding_history_unsettled_event")
            prior_sha = seen.get(funding_time)
            if prior_sha is not None:
                _deny(
                    "funding_history_duplicate_event"
                    if prior_sha == row_sha
                    else "funding_history_conflicting_event"
                )
            seen[funding_time] = row_sha
            timestamp = int(funding_time)
            if last_time is not None and timestamp >= last_time:
                _deny("funding_history_order_invalid")
            if prior_oldest is not None and timestamp >= prior_oldest:
                _deny("funding_history_cursor_not_exclusive")
            last_time = timestamp
            events.append(
                {
                    "funding_time_ms": funding_time,
                    "settlement_at": settlement_at.isoformat(),
                    "realized_rate": rate,
                    "first_presented_observed_at": page.response_received_at.isoformat(),
                    "row_sha256": row_sha,
                    "locator": {
                        "page_index": page_index,
                        "row_ordinal": row_ordinal,
                        "page_raw_sha256": document["raw_sha256"],
                    },
                }
            )
        expected_after = rows[-1]["fundingTime"]
        prior_oldest = int(expected_after)
        previous_page = page
    if not documents[-1]["terminal_empty"]:
        _deny("funding_history_terminal_missing")

    # Any required start older than 93 days is beyond every possible three
    # consecutive calendar-month span. A newer start still proves no retention.
    outside_retention = scope.required_from_at < (
        pages[-1].response_received_at - timedelta(days=93)
    )
    output = {
        "schema_version": "ctcc.settled_funding_history_raw_diagnostic.v1",
        "policy_sha256": POLICY_SHA256,
        "source_pages_sha256": pin,
        "environment": "demo_claim_unverified",
        "registration_region": scope.registration_region,
        "instrument_id": scope.instrument_id,
        "endpoint": ENDPOINT,
        "required_from_at": scope.required_from_at.isoformat(),
        "documented_retention": "last_three_months",
        "retention_status": (
            "required_start_outside_max_three_calendar_months"
            if outside_retention
            else "documented_limit_not_source_finality"
        ),
        "presented_terminal_empty_page": True,
        "pages": documents,
        "settled_public_events": events,
        "newest_presented_settlement_at": events[0]["settlement_at"]
        if events
        else None,
        "oldest_presented_settlement_at": events[-1]["settlement_at"]
        if events
        else None,
        "settled_schedule_complete": False,
        "applicable_account_funding_amount": None,
        "no_applicable_funding_proven": False,
        "account_region_authenticated": False,
        "source_authenticity_verified": False,
        "account_complete": False,
        "snapshot": None,
        "execution_authority": False,
        "admission": "DENY",
        "blocking_reasons": [
            "account_funding_payment_and_held_inventory_unjoined",
            "public_history_retention_not_account_finality",
            "source_clock_and_region_provenance_unverified",
            *(
                ["required_start_outside_documented_retention"]
                if outside_retention
                else []
            ),
        ],
    }
    receipt = canonical(output)
    if len(receipt) > MAX_RECEIPT_BYTES:
        _deny("funding_history_receipt_bound")
    return SettledFundingHistoryReplay(receipt, tuple(page.raw_body for page in pages))
