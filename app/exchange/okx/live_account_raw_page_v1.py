"""Closed, bounded Live current-account raw GET pages; never account authority."""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from itertools import pairwise
from urllib.parse import urlencode

from app.domain.source_primitives import ClockStamp, canonical, decode, sha
from app.exchange.okx.live_account_config_observation_v1 import (
    MAX_BODY,
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
    LivePinnedGetLeaseV1,
    OkxLivePinnedGetClientV1,
)
from app.exchange.okx.private_rest import build_signature

SCHEMA = "ctcc.live_account_raw_page.v1"
_ID = re.compile(r"[1-9][0-9]{0,39}\Z")
_SWAP = re.compile(r"[A-Z0-9]+-[A-Z0-9]+-SWAP\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
ALGO_TYPES = (
    "conditional",
    "oco",
    "chase",
    "trigger",
    "move_order_stop",
    "iceberg",
    "twap",
    "smart_iceberg",
)
_ENDPOINTS = {
    "account_position_risk": "/api/v5/account/account-position-risk",
    "balance": "/api/v5/account/balance",
    "positions": "/api/v5/account/positions",
    "orders_pending": "/api/v5/trade/orders-pending",
    "account_instruments": "/api/v5/account/instruments",
    "leverage_cross": "/api/v5/account/leverage-info",
    "leverage_isolated": "/api/v5/account/leverage-info",
    **{f"algo_{kind}": "/api/v5/trade/orders-algo-pending" for kind in ALGO_TYPES},
}


class LiveRawPageError(_LivePageBodyError):
    """A fixed error code, never a remote body, credential, or account identity."""


def _deny(code: str) -> None:
    raise LiveRawPageError(code) from None


@dataclass(frozen=True, slots=True, repr=False)
class LiveAccountRawPageSelectorV1:
    stream: str
    path: str
    query: tuple[tuple[str, str], ...]
    cursor_type: str | None
    after: str | None
    instrument_id: str | None
    leverage_instrument_ids: tuple[str, ...]


def select_live_account_raw_page_v1(
    stream: str,
    *,
    after: str | None = None,
    instrument_id: str | None = None,
    leverage_instrument_ids: tuple[str, ...] = (),
) -> LiveAccountRawPageSelectorV1:
    """Build only one of the reviewed current-account GET requests."""
    if type(stream) is not str or stream not in _ENDPOINTS:
        _deny("live_page_stream_invalid")
    cursor_type = (
        "ordId"
        if stream == "orders_pending"
        else "algoId"
        if stream.startswith("algo_")
        else None
    )
    if after is not None and (
        cursor_type is None or type(after) is not str or _ID.fullmatch(after) is None
    ):
        _deny("live_page_cursor_invalid")
    if (instrument_id is not None) is not (stream == "account_instruments") or (
        instrument_id is not None
        and (type(instrument_id) is not str or _SWAP.fullmatch(instrument_id) is None)
    ):
        _deny("live_page_instrument_scope_invalid")
    if type(leverage_instrument_ids) is not tuple or (
        bool(leverage_instrument_ids) != stream.startswith("leverage_")
    ):
        _deny("live_page_instrument_scope_invalid")
    if leverage_instrument_ids and (
        not 1 <= len(leverage_instrument_ids) <= 20
        or any(
            type(item) is not str or _SWAP.fullmatch(item) is None
            for item in leverage_instrument_ids
        )
        or tuple(sorted(set(leverage_instrument_ids))) != leverage_instrument_ids
    ):
        _deny("live_page_instrument_scope_invalid")
    query: dict[str, str] = {}
    if cursor_type is not None:
        query["limit"] = "100"
        if after is not None:
            query["after"] = after
    if stream.startswith("algo_"):
        query["ordType"] = stream.removeprefix("algo_")
    if stream == "account_instruments":
        query["instId"] = instrument_id
        query["instType"] = "SWAP"
    if stream.startswith("leverage_"):
        query["instId"] = ",".join(leverage_instrument_ids)
        query["mgnMode"] = stream.removeprefix("leverage_")
    return LiveAccountRawPageSelectorV1(
        stream=stream,
        path=_ENDPOINTS[stream],
        query=tuple(sorted(query.items())),
        cursor_type=cursor_type,
        after=after,
        instrument_id=instrument_id,
        leverage_instrument_ids=leverage_instrument_ids,
    )


def _checked_selector(
    value: LiveAccountRawPageSelectorV1,
) -> LiveAccountRawPageSelectorV1:
    if type(value) is not LiveAccountRawPageSelectorV1:
        _deny("live_page_selector_invalid")
    expected = select_live_account_raw_page_v1(
        value.stream,
        after=value.after,
        instrument_id=value.instrument_id,
        leverage_instrument_ids=value.leverage_instrument_ids,
    )
    if value != expected:
        _deny("live_page_selector_invalid")
    return expected


def _request_path(selector: LiveAccountRawPageSelectorV1) -> str:
    query = urlencode(selector.query)
    return selector.path + ("?" + query if query else "")


def _checked_rows(
    raw: bytes,
    selector: LiveAccountRawPageSelectorV1,
    *,
    private_tokens: tuple[str, ...],
) -> tuple[bytes, tuple[str, ...], int, bool]:
    try:
        payload = decode(raw, maximum=MAX_BODY)
        if type(payload) is not dict or payload.get("code") != "0":
            _deny("live_page_response_invalid")
        rows = payload.get("data")
        if (
            type(rows) is not list
            or len(rows) > (100 if selector.cursor_type is not None else 2048)
            or any(type(row) is not dict for row in rows)
        ):
            _deny("live_page_rows_invalid")
        if selector.stream in {"balance", "account_position_risk"} and len(rows) != 1:
            _deny("live_page_rows_invalid")
        if selector.stream == "account_instruments" and (
            len(rows) != 1
            or rows[0].get("instType") != "SWAP"
            or rows[0].get("instId") != selector.instrument_id
        ):
            _deny("live_page_instrument_scope_invalid")
        identifiers: list[str] = []
        if selector.cursor_type is not None:
            for row in rows:
                identifier = row.get(selector.cursor_type)
                if type(identifier) is not str or _ID.fullmatch(identifier) is None:
                    _deny("live_page_identity_invalid")
                identifiers.append(identifier)
            numeric = tuple(int(item) for item in identifiers)
            if any(left <= right for left, right in pairwise(numeric)):
                _deny("live_page_cursor_order_invalid")
            if selector.after is not None and any(
                item >= int(selector.after) for item in numeric
            ):
                _deny("live_page_cursor_not_exclusive")
        if any(token.encode("utf-8") in raw for token in private_tokens):
            _deny("live_page_credential_echo_rejected")
        _check_no_credential_echo(payload, private_tokens)
        return (
            canonical(payload),
            tuple(identifiers),
            len(rows),
            bool(selector.cursor_type is not None and not rows),
        )
    except LiveConfigObservationError:
        _deny("live_page_credential_echo_rejected")
    except LiveRawPageError:
        raise
    except Exception:  # noqa: BLE001 - raw exchange content remains private
        _deny("live_page_response_invalid")


@dataclass(frozen=True, slots=True, repr=False, eq=False)
class LiveAccountRawPageV1:
    selector: LiveAccountRawPageSelectorV1
    raw_body: bytes = field(repr=False)
    raw_headers: tuple[tuple[bytes, bytes], ...] = field(repr=False)
    row_ids: tuple[str, ...]
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
    source_authenticity_verified: bool = field(default=False, init=False)
    execution_authority: bool = field(default=False, init=False)


def live_account_raw_page_receipt_document_v1(page: LiveAccountRawPageV1) -> dict:
    """Canonicalizable metadata only; private raw body and headers remain separate."""
    if type(page) is not LiveAccountRawPageV1:
        _deny("live_page_receipt_invalid")
    return {
        "schema_version": SCHEMA,
        "selector": {
            "stream": page.selector.stream,
            "path": page.selector.path,
            "query": [list(item) for item in page.selector.query],
            "cursor_type": page.selector.cursor_type,
            "after": page.selector.after,
            "instrument_id": page.selector.instrument_id,
            "leverage_instrument_ids": list(page.selector.leverage_instrument_ids),
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
        "source_authenticity_verified": False,
        "execution_authority": False,
    }


def verify_live_account_raw_page_readback_v1(
    page: LiveAccountRawPageV1, *, lease: LivePinnedGetLeaseV1
) -> dict:
    """Replay signed request, raw entity, headers, identity order, and clocks."""
    if (
        type(page) is not LiveAccountRawPageV1
        or type(lease) is not LivePinnedGetLeaseV1
    ):
        _deny("live_page_readback_invalid")
    try:
        lease.verify_current()
        selector = _checked_selector(page.selector)
        if (
            page.lease_binding_id != lease.binding_id
            or page.origin != lease._pin.origin
            or page.account_complete is not False
            or page.source_authenticity_verified is not False
            or page.execution_authority is not False
            or len(page.stamps) != 6
            or (page.tls_certificate_sha256 is None) is not page.synthetic_mock
            or (
                page.tls_certificate_sha256 is not None
                and _DIGEST.fullmatch(page.tls_certificate_sha256) is None
            )
        ):
            _deny("live_page_readback_invalid")
        _validate_clock(page.stamps)
        _validate_signed_time(page.signed_at, page.stamps[0], page.stamps[1])
        request_path = _request_path(selector)
        signature = build_signature(
            timestamp=page.signed_at,
            method="GET",
            request_path=request_path,
            body="",
            secret=lease._pin.credentials[1],
        )
        length = _checked_response_headers(
            page.raw_headers, private_tokens=(*lease._pin.credentials, signature)
        )
        canonical_body, row_ids, row_count, empty_page = _checked_rows(
            page.raw_body,
            selector,
            private_tokens=(*lease._pin.credentials, signature),
        )
        if (
            length is not None
            and length != len(page.raw_body)
            or sha(page.raw_body) != page.raw_sha256
            or sha(canonical_body) != page.canonical_sha256
            or _header_digest(page.raw_headers) != page.headers_sha256
            or row_ids != page.row_ids
            or row_count != page.row_count
            or empty_page is not page.empty_page
            or sha(
                canonical(
                    {
                        "method": "GET",
                        "url": page.origin + request_path,
                        "signature": signature,
                    }
                )
            )
            != page.request_signature_sha256
            or sha(canonical(live_account_raw_page_receipt_document_v1(page)))
            != page.receipt_sha256
        ):
            _deny("live_page_readback_invalid")
        return {
            "schema_version": SCHEMA,
            "receipt_sha256": page.receipt_sha256,
            "raw_sha256": page.raw_sha256,
            "synthetic_mock": page.synthetic_mock,
            "account_complete": False,
            "source_authenticity_verified": False,
            "execution_authority": False,
        }
    except LiveRawPageError:
        raise
    except Exception:  # noqa: BLE001 - no private source content in errors
        _deny("live_page_readback_invalid")


async def observe_live_account_raw_page_v1(
    client: OkxLivePinnedGetClientV1,
    lease: LivePinnedGetLeaseV1,
    selector: LiveAccountRawPageSelectorV1,
) -> LiveAccountRawPageV1:
    """Capture one fixed page; chain completion belongs to a separate owner."""
    selector = _checked_selector(selector)
    request_path = _request_path(selector)
    try:
        owned = await _observe_owned_live_get_v1(
            client,
            lease,
            path=selector.path,
            request_path=request_path,
            selector=selector,
            validate_body=lambda raw, current, signature: _checked_rows(
                raw,
                selector,
                private_tokens=(*current._pin.credentials, signature),
            )[0],
        )
        _, row_ids, row_count, empty_page = _checked_rows(
            owned.raw_body,
            selector,
            private_tokens=(
                *lease._pin.credentials,
                build_signature(
                    timestamp=owned.signed_at,
                    method="GET",
                    request_path=request_path,
                    body="",
                    secret=lease._pin.credentials[1],
                ),
            ),
        )
        unsealed = LiveAccountRawPageV1(
            selector=selector,
            raw_body=owned.raw_body,
            raw_headers=owned.raw_headers,
            row_ids=row_ids,
            row_count=row_count,
            empty_page=empty_page,
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
                canonical(live_account_raw_page_receipt_document_v1(unsealed))
            ),
        )
        verify_live_account_raw_page_readback_v1(page, lease=lease)
        return page
    except LiveRawPageError:
        raise
    except Exception:  # noqa: BLE001 - transport and account data remain private
        _deny("live_page_observation_failed")
