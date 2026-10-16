"""Offline, DENY-only OKX position-history page-chain observation.

The official positions-history endpoint pages by *uTime*, not posId. OKX says
all rows sharing a uTime are returned in the same page. This adapter retains
the exact GET query and raw response for independent authenticated acquisition;
it neither performs IO nor certifies exchange retention, lifecycle completeness,
financial balances, account identity, or permission to trade.

Source: https://www.okx.com/docs-v5/en/#trading-account-rest-api-get-positions-history
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from itertools import pairwise

from app.trade_qualification import account_capture as capture

VERSION = "ctcc.demo_positions_history_page_chain.v1"
ENDPOINT = "/api/v5/account/positions-history"
LIMIT = 100
MAX_PAGE_ROWS = 256  # The documented equal-uTime boundary can exceed limit=100.
MAX_PAGE_BYTES = 262144
MAX_PAGES = 16
MAX_CHAIN_BYTES = 4 * 1024 * 1024
_REGION_ORIGINS = {
    "global": "https://openapi.okx.com",
    "us_au": "https://us.okx.com",
    "eea": "https://eea.okx.com",
    "tr": "https://tr.okx.com",
}
_DECIMAL = re.compile(r"-?(?:0|[1-9][0-9]{0,19})(?:\.[0-9]{1,20})?")
_ID = re.compile(r"[1-9][0-9]{0,39}")
_MS = re.compile(r"[1-9][0-9]{0,14}")
_SHA = re.compile(r"[a-f0-9]{64}")


class PositionHistoryError(ValueError):
    """Stable codes only; never include source rows or private identifiers."""


def _deny(code: str):
    raise PositionHistoryError(code)


def _hash(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, ensure_ascii=True, separators=(",", ":")
    ).encode("ascii")


def _utc(value: datetime) -> datetime:
    if type(value) is not datetime or value.tzinfo is not UTC:
        _deny("clock_invalid")
    return value


def _milliseconds(value: datetime) -> int:
    delta = value - datetime(1970, 1, 1, tzinfo=UTC)
    return (delta.days * 86400 + delta.seconds) * 1000 + delta.microseconds // 1000


@dataclass(frozen=True, slots=True, repr=False)
class PositionHistoryPlanV1:
    registration_region: str
    origin: str
    account_binding_sha256: str
    session_binding_sha256: str
    created_at: datetime
    cutoff_u_time_ms: int

    def __post_init__(self):
        if (
            type(self.registration_region) is not str
            or self.registration_region not in _REGION_ORIGINS
            or type(self.origin) is not str
            or self.origin != _REGION_ORIGINS[self.registration_region]
            or type(self.account_binding_sha256) is not str
            or _SHA.fullmatch(self.account_binding_sha256) is None
            or type(self.session_binding_sha256) is not str
            or _SHA.fullmatch(self.session_binding_sha256) is None
            or type(self.cutoff_u_time_ms) is not int
            or self.cutoff_u_time_ms < 1
            or self.cutoff_u_time_ms > _milliseconds(_utc(self.created_at))
        ):
            _deny("plan_invalid")

    @property
    def sha256(self) -> str:
        return _hash(
            _canonical(
                {
                    "version": VERSION,
                    "environment": "demo",
                    "registration_region": self.registration_region,
                    "origin": self.origin,
                    "account_binding_sha256": self.account_binding_sha256,
                    "session_binding_sha256": self.session_binding_sha256,
                    "created_at": self.created_at.isoformat(),
                    "cutoff_u_time_ms": self.cutoff_u_time_ms,
                    "limit": LIMIT,
                    "endpoint": ENDPOINT,
                }
            )
        )


def request_for_page(plan: PositionHistoryPlanV1, after: str | None = None):
    """Return the exact GET origin/path/query; the caller owns all signing/IO."""
    if type(plan) is not PositionHistoryPlanV1:
        _deny("plan_invalid")
    plan.__post_init__()
    if after is not None and (type(after) is not str or _MS.fullmatch(after) is None):
        _deny("cursor_invalid")
    query = (
        (("limit", str(LIMIT)),)
        if after is None
        else (
            ("after", after),
            ("limit", str(LIMIT)),
        )
    )
    return ("GET", plan.origin, ENDPOINT, query)


@dataclass(frozen=True, slots=True, repr=False)
class PositionHistoryRowV1:
    identity_sha256: str
    row_sha256: str
    u_time_ms: int
    c_time_ms: int
    missing_financial_fields: tuple[str, ...]


@dataclass(frozen=True, slots=True, repr=False)
class PositionHistoryPageV1:
    plan_sha256: str
    page_index: int
    after: str | None
    previous_page_sha256: str | None
    request_query: tuple[tuple[str, str], ...]
    request_started_at: datetime
    headers_received_at: datetime
    body_completed_at: datetime
    raw_body: bytes = field(repr=False)
    raw_sha256: str
    canonical_sha256: str
    rows: tuple[PositionHistoryRowV1, ...]
    terminal: bool
    next_after: str | None
    page_sha256: str

    @property
    def account_complete(self) -> bool:
        return False

    @property
    def execution_authority(self) -> bool:
        return False

    @property
    def source_authenticity_verified(self) -> bool:
        return False


def _row(source: object, completed_at: datetime) -> PositionHistoryRowV1:
    if type(source) is not dict:
        _deny("row_invalid")
    for key in ("posId", "instId", "instType", "posSide", "mgnMode", "type"):
        value = source.get(key)
        if (
            type(value) is not str
            or not 1 <= len(value) <= 96
            or value != value.strip()
        ):
            _deny("row_invalid")
    if _ID.fullmatch(source["posId"]) is None:
        _deny("row_invalid")
    if source["instType"] not in {"MARGIN", "SWAP", "FUTURES", "OPTION", "EVENTS"}:
        _deny("row_invalid")
    if source["posSide"] not in {"net", "long", "short"}:
        _deny("row_invalid")
    if source["mgnMode"] not in {"cross", "isolated"}:
        _deny("row_invalid")
    if source["type"] not in {"1", "2", "3", "4", "5", "6"}:
        _deny("row_invalid")
    for key in ("cTime", "uTime"):
        if type(source.get(key)) is not str or _MS.fullmatch(source[key]) is None:
            _deny("row_invalid")
    created, updated = int(source["cTime"]), int(source["uTime"])
    if created > updated or updated > _milliseconds(completed_at):
        _deny("row_clock_invalid")
    financial = ("realizedPnl", "fee", "fundingFee", "liqPenalty", "pnl")
    missing = []
    for key in financial:
        value = source.get(key)
        if value is None or value == "":
            missing.append(key)
        elif type(value) is not str or _DECIMAL.fullmatch(value) is None:
            _deny("row_financial_invalid")
    identity = _canonical({"posId": source["posId"], "uTime": source["uTime"]})
    return PositionHistoryRowV1(
        identity_sha256=_hash(identity),
        row_sha256=_hash(_canonical(source)),
        u_time_ms=updated,
        c_time_ms=created,
        missing_financial_fields=tuple(missing),
    )


def parse_page(
    plan: PositionHistoryPlanV1,
    raw_body: bytes,
    *,
    page_index: int,
    after: str | None,
    previous_page_sha256: str | None,
    request_started_at: datetime,
    headers_received_at: datetime,
    body_completed_at: datetime,
) -> PositionHistoryPageV1:
    method, origin, endpoint, query = request_for_page(plan, after)
    if (
        type(page_index) is not int
        or not 0 <= page_index < MAX_PAGES
        or (page_index == 0) != (after is None and previous_page_sha256 is None)
        or (page_index > 0 and after is None)
        or (
            page_index > 0
            and (
                type(previous_page_sha256) is not str
                or _SHA.fullmatch(previous_page_sha256) is None
            )
        )
    ):
        _deny("page_chain_metadata_invalid")
    started, received, completed = map(
        _utc, (request_started_at, headers_received_at, body_completed_at)
    )
    if not plan.created_at < started <= received <= completed:
        _deny("page_clock_invalid")
    if completed - started > timedelta(seconds=15):
        _deny("page_clock_invalid")
    try:
        payload, canonical = capture._decode_json(
            raw_body, limit=MAX_PAGE_BYTES, wire=True
        )
    except capture.AccountCaptureError:
        _deny("page_json_invalid")
    if (
        type(payload) is not dict
        or set(payload) - {"code", "msg", "data", "inTime", "outTime"}
        or payload.get("code") != "0"
        or type(payload.get("msg")) is not str
        or type(payload.get("data")) is not list
    ):
        _deny("page_envelope_invalid")
    if ("inTime" in payload) != ("outTime" in payload):
        _deny("page_envelope_invalid")
    gateway_out = None
    if "inTime" in payload:
        try:
            gateway_in = capture._gateway_time(payload["inTime"])
            gateway_out = capture._gateway_time(payload["outTime"])
        except capture.AccountCaptureError:
            _deny("page_gateway_clock_invalid")
        if not started <= gateway_in <= gateway_out <= received:
            _deny("page_gateway_clock_invalid")
    source_rows = payload["data"]
    if len(source_rows) > MAX_PAGE_ROWS:
        _deny("page_row_limit")
    rows = tuple(_row(source, completed) for source in source_rows)
    if gateway_out is not None and any(
        row.u_time_ms > _milliseconds(gateway_out) for row in rows
    ):
        _deny("row_after_gateway_response")
    times = tuple(row.u_time_ms for row in rows)
    if any(left < right for left, right in pairwise(times)):
        _deny("page_time_order_invalid")
    if after is not None and any(value >= int(after) for value in times):
        _deny("page_cursor_not_exclusive")
    if len(rows) > LIMIT and any(value != times[LIMIT - 1] for value in times[LIMIT:]):
        _deny("page_collision_overflow_invalid")
    identities = [row.identity_sha256 for row in rows]
    seen_rows: dict[str, str] = {}
    for row in rows:
        previous_hash = seen_rows.get(row.identity_sha256)
        if previous_hash is not None:
            _deny(
                "page_conflicting_identity"
                if previous_hash != row.row_sha256
                else "page_duplicate_identity"
            )
        seen_rows[row.identity_sha256] = row.row_sha256
    raw_sha = _hash(raw_body)
    canonical_sha = _hash(canonical.encode("utf-8"))
    next_after = str(times[-1]) if times else None
    metadata = {
        "version": VERSION,
        "plan_sha256": plan.sha256,
        "page_index": page_index,
        "after": after,
        "previous_page_sha256": previous_page_sha256,
        "method": method,
        "origin": origin,
        "endpoint": endpoint,
        "request_query": query,
        "request_started_at": started.isoformat(),
        "headers_received_at": received.isoformat(),
        "body_completed_at": completed.isoformat(),
        "raw_sha256": raw_sha,
        "canonical_sha256": canonical_sha,
        "row_identities": identities,
        "row_hashes": [row.row_sha256 for row in rows],
        "terminal": not rows,
        "next_after": next_after,
    }
    return PositionHistoryPageV1(
        plan_sha256=plan.sha256,
        page_index=page_index,
        after=after,
        previous_page_sha256=previous_page_sha256,
        request_query=query,
        request_started_at=started,
        headers_received_at=received,
        body_completed_at=completed,
        raw_body=raw_body,
        raw_sha256=raw_sha,
        canonical_sha256=canonical_sha,
        rows=rows,
        terminal=not rows,
        next_after=next_after,
        page_sha256=_hash(_canonical(metadata)),
    )


@dataclass(frozen=True, slots=True, repr=False)
class PositionHistoryChainV1:
    plan_sha256: str
    page_hashes: tuple[str, ...]
    row_count: int
    oldest_u_time_ms: int | None
    cutoff_u_time_ms: int
    cutoff_observed: bool
    terminal_page_sha256: str
    chain_sha256: str

    @property
    def account_complete(self) -> bool:
        return False

    @property
    def execution_authority(self) -> bool:
        return False

    @property
    def source_authenticity_verified(self) -> bool:
        return False

    @property
    def admission(self) -> str:
        return "DENY"


def verify_chain(
    plan: PositionHistoryPlanV1, pages: tuple[PositionHistoryPageV1, ...]
) -> PositionHistoryChainV1:
    if type(plan) is not PositionHistoryPlanV1 or type(pages) is not tuple:
        _deny("chain_invalid")
    plan.__post_init__()
    if not 1 <= len(pages) <= MAX_PAGES or any(
        type(page) is not PositionHistoryPageV1 for page in pages
    ):
        _deny("chain_page_limit")
    if sum(len(page.raw_body) for page in pages) > MAX_CHAIN_BYTES:
        _deny("chain_byte_limit")
    if not pages[-1].terminal or any(page.terminal for page in pages[:-1]):
        _deny("empty_terminal_page_required")
    previous = None
    identities: dict[str, str] = {}
    all_times = []
    hashes = []
    for index, page in enumerate(pages):
        replay = parse_page(
            plan,
            page.raw_body,
            page_index=index,
            after=None if previous is None else previous.next_after,
            previous_page_sha256=None if previous is None else previous.page_sha256,
            request_started_at=page.request_started_at,
            headers_received_at=page.headers_received_at,
            body_completed_at=page.body_completed_at,
        )
        if replay != page:
            _deny("chain_replay_mismatch")
        if (
            previous is not None
            and page.request_started_at < previous.body_completed_at
        ):
            _deny("chain_clock_reversed")
        for row in page.rows:
            earlier = identities.get(row.identity_sha256)
            if earlier is not None:
                _deny(
                    "chain_conflicting_identity"
                    if earlier != row.row_sha256
                    else "chain_duplicate_identity"
                )
            identities[row.identity_sha256] = row.row_sha256
            all_times.append(row.u_time_ms)
        hashes.append(page.page_sha256)
        previous = page
    if len(identities) > MAX_PAGES * MAX_PAGE_ROWS:
        _deny("chain_row_limit")
    oldest = min(all_times) if all_times else None
    return PositionHistoryChainV1(
        plan_sha256=plan.sha256,
        page_hashes=tuple(hashes),
        row_count=len(identities),
        oldest_u_time_ms=oldest,
        cutoff_u_time_ms=plan.cutoff_u_time_ms,
        cutoff_observed=oldest is not None and oldest <= plan.cutoff_u_time_ms,
        terminal_page_sha256=pages[-1].page_sha256,
        chain_sha256=_hash(
            _canonical(
                {"version": VERSION, "plan_sha256": plan.sha256, "pages": hashes}
            )
        ),
    )
