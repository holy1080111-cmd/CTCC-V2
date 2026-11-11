"""Fixed Live historical GET pages with raw custody facts, never history authority."""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from itertools import pairwise
from urllib.parse import urlencode

from app.domain.source_primitives import ClockStamp, canonical, decode, sha
from app.exchange.okx.live_account_config_observation_v1 import (
    MAX_HISTORY_BODY,
    LiveConfigObservationError,
    _check_no_credential_echo,
    _checked_response_headers,
    _header_digest,
    _LivePageBodyError,
    _observe_owned_live_get_v1,
    _validate_clock,
    _validate_signed_time,
)
from app.exchange.okx.live_read_lease_v1 import (
    _LIVE_HOSTS,
    LivePinnedGetLeaseV1,
    OkxLivePinnedGetClientV1,
)
from app.exchange.okx.private_rest import build_signature

SCHEMA = "ctcc.live_account_history_raw_page.v1"
HISTORY_INST_TYPES = ("SPOT", "MARGIN", "SWAP", "FUTURES", "OPTION", "EVENTS")
HISTORY_ALGO_TYPES = (
    "conditional",
    "oco",
    "chase",
    "trigger",
    "move_order_stop",
    "iceberg",
    "twap",
    "smart_iceberg",
)
HISTORY_ALGO_STATES = ("effective", "canceled", "order_failed")
_PATHS = {
    "fills_recent": "/api/v5/trade/fills",
    "fills_history": "/api/v5/trade/fills-history",
    "bills_recent": "/api/v5/account/bills",
    "bills_archive": "/api/v5/account/bills-archive",
    "orders_recent": "/api/v5/trade/orders-history",
    "orders_archive": "/api/v5/trade/orders-history-archive",
    "algo_history": "/api/v5/trade/orders-algo-history",
}
_ID = re.compile(r"[1-9][0-9]{0,39}\Z")
_MS = re.compile(r"[1-9][0-9]{12}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_MAX_WINDOW_MS = 28 * 24 * 60 * 60 * 1000
_LIVE_ORIGINS = frozenset("https://" + host for host in _LIVE_HOSTS)


class LiveHistoryPageError(_LivePageBodyError):
    """Fixed historical page refusal without source, identity, or secret text."""


def _deny(code: str) -> None:
    raise LiveHistoryPageError(code) from None


@dataclass(frozen=True, slots=True, repr=False)
class LiveAccountHistorySelectorV1:
    kind: str
    path: str
    query: tuple[tuple[str, str], ...]
    cursor_type: str
    after: str | None
    begin_ms: str | None
    end_ms: str | None
    inst_type: str | None
    ord_type: str | None
    state: str | None


def select_live_account_history_page_v1(
    kind: str,
    *,
    begin_ms: str | None = None,
    end_ms: str | None = None,
    inst_type: str | None = None,
    ord_type: str | None = None,
    state: str | None = None,
    after: str | None = None,
) -> LiveAccountHistorySelectorV1:
    """Fix exact endpoint, product/state partition, time filter, and ID cursor."""
    if type(kind) is not str or kind not in _PATHS:
        _deny("live_history_kind_invalid")
    if after is not None and (type(after) is not str or _ID.fullmatch(after) is None):
        _deny("live_history_cursor_invalid")
    algo = kind == "algo_history"
    product = kind.startswith(("fills_", "orders_"))
    if algo:
        if (
            begin_ms is not None
            or end_ms is not None
            or inst_type is not None
            or type(ord_type) is not str
            or ord_type not in HISTORY_ALGO_TYPES
            or type(state) is not str
            or state not in HISTORY_ALGO_STATES
        ):
            _deny("live_history_partition_invalid")
    else:
        if (
            type(begin_ms) is not str
            or _MS.fullmatch(begin_ms) is None
            or type(end_ms) is not str
            or _MS.fullmatch(end_ms) is None
            or int(begin_ms) > int(end_ms)
            or int(end_ms) - int(begin_ms) > _MAX_WINDOW_MS
            or ord_type is not None
            or state is not None
            or (
                product
                and (type(inst_type) is not str or inst_type not in HISTORY_INST_TYPES)
            )
            or (not product and inst_type is not None)
        ):
            _deny("live_history_partition_invalid")
    cursor_type = (
        "algoId" if algo else "ordId" if kind.startswith("orders_") else "billId"
    )
    query: dict[str, str] = {"limit": "100"}
    if after is not None:
        query["after"] = after
    if algo:
        query.update(ordType=ord_type, state=state)
    else:
        query.update(begin=begin_ms, end=end_ms)
        if product:
            query["instType"] = inst_type
    return LiveAccountHistorySelectorV1(
        kind=kind,
        path=_PATHS[kind],
        query=tuple(sorted(query.items())),
        cursor_type=cursor_type,
        after=after,
        begin_ms=begin_ms,
        end_ms=end_ms,
        inst_type=inst_type,
        ord_type=ord_type,
        state=state,
    )


def _checked_selector(value: object) -> LiveAccountHistorySelectorV1:
    if type(value) is not LiveAccountHistorySelectorV1:
        _deny("live_history_selector_invalid")
    expected = select_live_account_history_page_v1(
        value.kind,
        begin_ms=value.begin_ms,
        end_ms=value.end_ms,
        inst_type=value.inst_type,
        ord_type=value.ord_type,
        state=value.state,
        after=value.after,
    )
    if value != expected:
        _deny("live_history_selector_invalid")
    return expected


def _request_path(selector: LiveAccountHistorySelectorV1) -> str:
    return selector.path + "?" + urlencode(selector.query)


def checked_live_account_history_rows_v1(
    raw: bytes,
    selector: LiveAccountHistorySelectorV1,
    *,
    private_tokens: tuple[str, ...] = (),
) -> tuple[bytes, tuple[str, ...], int, bool]:
    """Replay one source page, not the completeness of a history window."""
    selector = _checked_selector(selector)
    try:
        payload = decode(raw, maximum=MAX_HISTORY_BODY)
        if type(payload) is not dict or payload.get("code") != "0":
            _deny("live_history_response_invalid")
        rows = payload.get("data")
        if (
            type(rows) is not list
            or len(rows) > 100
            or any(type(row) is not dict for row in rows)
        ):
            _deny("live_history_rows_invalid")
        row_ids: list[str] = []
        for row in rows:
            identifier = row.get(selector.cursor_type)
            if type(identifier) is not str or _ID.fullmatch(identifier) is None:
                _deny("live_history_identity_invalid")
            if (
                selector.inst_type is not None
                and row.get("instType") != selector.inst_type
            ):
                _deny("live_history_product_mismatch")
            if selector.ord_type is not None and (
                row.get("ordType") != selector.ord_type
                or row.get("state") != selector.state
            ):
                _deny("live_history_algo_partition_mismatch")
            row_ids.append(identifier)
        numeric_ids = tuple(int(value) for value in row_ids)
        if any(left <= right for left, right in pairwise(numeric_ids)):
            _deny("live_history_cursor_order_invalid")
        if selector.after is not None and any(
            value >= int(selector.after) for value in numeric_ids
        ):
            _deny("live_history_cursor_not_exclusive")
        if any(token.encode("utf-8") in raw for token in private_tokens):
            _deny("live_history_credential_echo_rejected")
        _check_no_credential_echo(payload, private_tokens)
        return canonical(payload), tuple(row_ids), len(rows), not rows
    except LiveConfigObservationError:
        _deny("live_history_credential_echo_rejected")
    except LiveHistoryPageError:
        raise
    except Exception:  # noqa: BLE001 - no remote fields in errors
        _deny("live_history_response_invalid")


@dataclass(frozen=True, slots=True, repr=False, eq=False)
class LiveAccountHistoryPageV1:
    selector: LiveAccountHistorySelectorV1
    raw_body: bytes = field(repr=False)
    raw_headers: tuple[tuple[bytes, bytes], ...] = field(repr=False)
    row_ids: tuple[str, ...] = field(repr=False)
    row_count: int
    empty_page: bool
    signed_at: str
    stamps: tuple[ClockStamp, ...] = field(repr=False)
    lease_binding_id: str = field(repr=False)
    origin: str
    raw_sha256: str
    canonical_sha256: str
    headers_sha256: str
    request_signature_sha256: str
    tls_certificate_sha256: str | None
    receipt_sha256: str
    synthetic_mock: bool
    account_complete: bool = field(default=False, init=False)
    history_complete: bool = field(default=False, init=False)
    source_authenticity_verified: bool = field(default=False, init=False)
    execution_authority: bool = field(default=False, init=False)


def live_account_history_page_receipt_document_v1(
    page: LiveAccountHistoryPageV1,
) -> dict:
    """Canonical metadata with hashed identifiers; no raw rows or headers."""
    if type(page) is not LiveAccountHistoryPageV1:
        _deny("live_history_receipt_invalid")
    return {
        "schema_version": SCHEMA,
        "selector": {
            "kind": page.selector.kind,
            "path": page.selector.path,
            "query": [
                [key, sha(value.encode()) if key == "after" else value]
                for key, value in page.selector.query
            ],
            "cursor_type": page.selector.cursor_type,
            "after_sha256": None
            if page.selector.after is None
            else sha(page.selector.after.encode()),
            "begin_ms": page.selector.begin_ms,
            "end_ms": page.selector.end_ms,
            "inst_type": page.selector.inst_type,
            "ord_type": page.selector.ord_type,
            "state": page.selector.state,
        },
        "row_ids_sha256": sha(canonical(list(page.row_ids))),
        "row_count": page.row_count,
        "empty_page": page.empty_page,
        "signed_at": page.signed_at,
        "stamps": [item.model_dump() for item in page.stamps],
        "lease_binding_id": page.lease_binding_id,
        "origin": page.origin,
        "raw_sha256": page.raw_sha256,
        "canonical_sha256": page.canonical_sha256,
        "headers_sha256": page.headers_sha256,
        "request_signature_sha256": page.request_signature_sha256,
        "tls_certificate_sha256": page.tls_certificate_sha256,
        "synthetic_mock": page.synthetic_mock,
        "account_complete": False,
        "history_complete": False,
        "source_authenticity_verified": False,
        "execution_authority": False,
    }


def _check_pure_page(page: LiveAccountHistoryPageV1) -> None:
    selector = _checked_selector(page.selector)
    canonical_body, ids, count, empty = checked_live_account_history_rows_v1(
        page.raw_body, selector
    )
    declared = _checked_response_headers(
        page.raw_headers, private_tokens=(), maximum=MAX_HISTORY_BODY
    )
    if (
        page.account_complete is not False
        or page.history_complete is not False
        or page.source_authenticity_verified is not False
        or page.execution_authority is not False
        or type(page.origin) is not str
        or page.origin not in _LIVE_ORIGINS
        or type(page.lease_binding_id) is not str
        or _DIGEST.fullmatch(page.lease_binding_id) is None
        or len(page.stamps) != 6
        or (page.tls_certificate_sha256 is None) is not page.synthetic_mock
        or (
            page.tls_certificate_sha256 is not None
            and _DIGEST.fullmatch(page.tls_certificate_sha256) is None
        )
        or _DIGEST.fullmatch(page.request_signature_sha256) is None
        or (declared is not None and declared != len(page.raw_body))
        or sha(page.raw_body) != page.raw_sha256
        or sha(canonical_body) != page.canonical_sha256
        or _header_digest(page.raw_headers) != page.headers_sha256
        or ids != page.row_ids
        or count != page.row_count
        or empty is not page.empty_page
        or sha(canonical(live_account_history_page_receipt_document_v1(page)))
        != page.receipt_sha256
    ):
        _deny("live_history_readback_invalid")
    _validate_clock(page.stamps)
    _validate_signed_time(page.signed_at, page.stamps[0], page.stamps[1])


def reconstruct_live_account_history_page_v1(
    raw_body: bytes,
    raw_headers: tuple[tuple[bytes, bytes], ...],
    source_metadata: dict,
    selector: LiveAccountHistorySelectorV1,
) -> LiveAccountHistoryPageV1:
    """Pure archived byte replay; it does not authenticate an exchange peer."""
    try:
        selector = _checked_selector(selector)
        _, ids, count, empty = checked_live_account_history_rows_v1(raw_body, selector)
        page = LiveAccountHistoryPageV1(
            selector=selector,
            raw_body=raw_body,
            raw_headers=raw_headers,
            row_ids=ids,
            row_count=count,
            empty_page=empty,
            signed_at=source_metadata["signed_at"],
            stamps=tuple(
                ClockStamp.model_validate(value) for value in source_metadata["stamps"]
            ),
            lease_binding_id=source_metadata["lease_binding_id"],
            origin=source_metadata["origin"],
            raw_sha256=sha(raw_body),
            canonical_sha256=sha(
                checked_live_account_history_rows_v1(raw_body, selector)[0]
            ),
            headers_sha256=_header_digest(raw_headers),
            request_signature_sha256=source_metadata["request_signature_sha256"],
            tls_certificate_sha256=source_metadata["tls_certificate_sha256"],
            receipt_sha256=sha(canonical(source_metadata)),
            synthetic_mock=source_metadata["synthetic_mock"],
        )
        _check_pure_page(page)
        if source_metadata != live_account_history_page_receipt_document_v1(page):
            _deny("live_history_readback_invalid")
        return page
    except LiveHistoryPageError:
        raise
    except Exception:  # noqa: BLE001 - no private archive bytes in errors
        _deny("live_history_readback_invalid")


def verify_live_account_history_page_readback_v1(
    page: LiveAccountHistoryPageV1, *, lease: LivePinnedGetLeaseV1
) -> dict:
    """Current-lease replay also recomputes the signed request digest."""
    if (
        type(page) is not LiveAccountHistoryPageV1
        or type(lease) is not LivePinnedGetLeaseV1
    ):
        _deny("live_history_readback_invalid")
    try:
        lease.verify_current()
        _check_pure_page(page)
        if (
            page.lease_binding_id != lease.binding_id
            or page.origin != lease._pin.origin
        ):
            _deny("live_history_readback_invalid")
        signature = build_signature(
            timestamp=page.signed_at,
            method="GET",
            request_path=_request_path(page.selector),
            body="",
            secret=lease._pin.credentials[1],
        )
        _checked_response_headers(
            page.raw_headers,
            private_tokens=(*lease._pin.credentials, signature),
            maximum=MAX_HISTORY_BODY,
        )
        checked_live_account_history_rows_v1(
            page.raw_body,
            page.selector,
            private_tokens=(*lease._pin.credentials, signature),
        )
        if (
            sha(
                canonical(
                    {
                        "method": "GET",
                        "url": page.origin + _request_path(page.selector),
                        "signature": signature,
                    }
                )
            )
            != page.request_signature_sha256
        ):
            _deny("live_history_readback_invalid")
        return {
            "schema_version": SCHEMA,
            "receipt_sha256": page.receipt_sha256,
            "raw_sha256": page.raw_sha256,
            "synthetic_mock": page.synthetic_mock,
            "account_complete": False,
            "history_complete": False,
            "source_authenticity_verified": False,
            "execution_authority": False,
        }
    except LiveHistoryPageError:
        raise
    except Exception:  # noqa: BLE001 - no private source text in errors
        _deny("live_history_readback_invalid")


async def observe_live_account_history_page_v1(
    client: OkxLivePinnedGetClientV1,
    lease: LivePinnedGetLeaseV1,
    selector: LiveAccountHistorySelectorV1,
) -> LiveAccountHistoryPageV1:
    """One GET, one raw page; no chain or retention completion claim."""
    selector = _checked_selector(selector)
    request_path = _request_path(selector)
    try:
        owned = await _observe_owned_live_get_v1(
            client,
            lease,
            path=selector.path,
            request_path=request_path,
            selector=selector,
            validate_body=lambda raw, current, signature: (
                checked_live_account_history_rows_v1(
                    raw,
                    selector,
                    private_tokens=(*current._pin.credentials, signature),
                )[0]
            ),
        )
        _, ids, count, empty = checked_live_account_history_rows_v1(
            owned.raw_body, selector
        )
        unsealed = LiveAccountHistoryPageV1(
            selector=selector,
            raw_body=owned.raw_body,
            raw_headers=owned.raw_headers,
            row_ids=ids,
            row_count=count,
            empty_page=empty,
            signed_at=owned.signed_at,
            stamps=owned.stamps,
            lease_binding_id=lease.binding_id,
            origin=lease._pin.origin,
            raw_sha256=sha(owned.raw_body),
            canonical_sha256=sha(owned.canonical_body),
            headers_sha256=_header_digest(owned.raw_headers),
            request_signature_sha256=owned.request_signature_sha256,
            tls_certificate_sha256=owned.tls_certificate_sha256,
            receipt_sha256="",
            synthetic_mock=owned.synthetic_mock,
        )
        page = replace(
            unsealed,
            receipt_sha256=sha(
                canonical(live_account_history_page_receipt_document_v1(unsealed))
            ),
        )
        verify_live_account_history_page_readback_v1(page, lease=lease)
        return page
    except LiveHistoryPageError:
        raise
    except Exception:  # noqa: BLE001 - redact HTTP, source, and account errors
        _deny("live_history_observation_failed")
