"""Exact Live order/algo/fill GET DATA; never a protection or execution permit.

The original zero-retry TLS reader owns every request. Selectors admit only the
documented exact identities; fills paginate by billId, independently of tradeId.
Page constructors and saved receipts remain unauthenticated DATA.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field, replace
from itertools import pairwise
from urllib.parse import urlencode

from app.domain.source_primitives import ClockStamp, canonical, decode, sha
from app.exchange.okx.live_account_config_observation_v1 import (
    MAX_HISTORY_BODY,
    _check_no_credential_echo,
    _checked_response_headers,
    _header_digest,
    _observe_owned_live_get_v1,
    _validate_clock,
    _validate_signed_time,
)
from app.exchange.okx.live_read_lease_v1 import (
    LivePinnedGetLeaseV1,
    OkxLivePinnedGetClientV1,
)
from app.exchange.okx.private_rest import build_signature

SCHEMA = "ctcc.live_trade_read_page.v1"
_ID = re.compile(r"[1-9][0-9]{0,39}\Z")
_CLIENT = re.compile(r"[A-Za-z0-9]{1,32}\Z")
_DIGEST = re.compile(r"[a-f0-9]{64}\Z")
_INSTRUMENT = re.compile(r"[A-Z0-9]{1,20}-USDT-SWAP\Z")
_PATHS = {
    "order": "/api/v5/trade/order",
    "algo": "/api/v5/trade/order-algo",
    "fills": "/api/v5/trade/fills",
}


class LiveTradeReadPageError(ValueError):
    pass


def _need(condition, code="live_trade_read_page_unavailable"):
    if condition is not True:
        raise LiveTradeReadPageError(code) from None


@dataclass(frozen=True, slots=True, repr=False)
class LiveTradeReadSelectorV1:
    kind: str
    instrument_id: str
    order_id: str | None
    client_order_id: str | None
    algo_id: str | None
    algo_client_id: str | None
    after_bill_id: str | None
    path: str
    query: tuple[tuple[str, str], ...]


def select_live_trade_read_v1(
    kind,
    *,
    instrument_id,
    order_id=None,
    client_order_id=None,
    algo_id=None,
    algo_client_id=None,
    after_bill_id=None,
):
    _need(type(kind) is str and kind in _PATHS)
    _need(
        type(instrument_id) is str and _INSTRUMENT.fullmatch(instrument_id) is not None
    )
    for value in (order_id, algo_id, after_bill_id):
        _need(value is None or type(value) is str and _ID.fullmatch(value) is not None)
    for value in (client_order_id, algo_client_id):
        _need(
            value is None or type(value) is str and _CLIENT.fullmatch(value) is not None
        )
    if kind == "order":
        _need((order_id is None) != (client_order_id is None))
        _need(algo_id is algo_client_id is after_bill_id is None)
        query = {
            "instId": instrument_id,
            **(
                {"ordId": order_id}
                if order_id is not None
                else {"clOrdId": client_order_id}
            ),
        }
    elif kind == "algo":
        _need((algo_id is None) != (algo_client_id is None))
        _need(order_id is client_order_id is after_bill_id is None)
        query = (
            {"algoId": algo_id}
            if algo_id is not None
            else {"algoClOrdId": algo_client_id}
        )
    else:
        _need(
            order_id is not None
            and client_order_id is algo_id is algo_client_id is None
        )
        query = {
            "instType": "SWAP",
            "instId": instrument_id,
            "ordId": order_id,
            "limit": "100",
        }
        if after_bill_id is not None:
            query["after"] = after_bill_id
    return LiveTradeReadSelectorV1(
        kind,
        instrument_id,
        order_id,
        client_order_id,
        algo_id,
        algo_client_id,
        after_bill_id,
        _PATHS[kind],
        tuple(sorted(query.items())),
    )


def _checked_selector(value):
    _need(type(value) is LiveTradeReadSelectorV1)
    expected = select_live_trade_read_v1(
        value.kind,
        instrument_id=value.instrument_id,
        order_id=value.order_id,
        client_order_id=value.client_order_id,
        algo_id=value.algo_id,
        algo_client_id=value.algo_client_id,
        after_bill_id=value.after_bill_id,
    )
    _need(value == expected)
    return expected


def _request_path(selector):
    value = _checked_selector(selector)
    return value.path + "?" + urlencode(value.query)


def _body(raw, selector, *, private_tokens):
    _checked_selector(selector)
    _need(type(raw) is bytes and 0 < len(raw) <= MAX_HISTORY_BODY)
    _need(not any(token.encode("utf-8") in raw for token in private_tokens))
    value = decode(raw, maximum=MAX_HISTORY_BODY)
    _check_no_credential_echo(value, private_tokens)
    _need(type(value) is dict and set(value) == {"code", "msg", "data"})
    _need(
        type(value["code"]) is str
        and re.fullmatch(r"[0-9]{1,10}", value["code"]) is not None
    )
    _need(type(value["msg"]) is str and len(value["msg"]) <= 4096)
    rows = value["data"]
    _need(type(rows) is list and len(rows) <= (100 if selector.kind == "fills" else 1))
    _need(all(type(row) is dict for row in rows))
    # Explicit API error DATA is retained, never converted into an empty
    # successful source. Semantic order/protection checks belong to replay.
    if value["code"] != "0":
        _need(not rows)
        return canonical(value), ()
    identities = []
    for row in rows:
        identity = row.get(
            "billId"
            if selector.kind == "fills"
            else "algoId"
            if selector.kind == "algo"
            else "ordId"
        )
        _need(type(identity) is str and _ID.fullmatch(identity) is not None)
        identities.append(identity)
        _need(row.get("instId") == selector.instrument_id)
        if selector.kind in ("order", "fills") and selector.order_id is not None:
            _need(row.get("ordId") == selector.order_id)
        if selector.client_order_id is not None:
            _need(row.get("clOrdId") == selector.client_order_id)
        if selector.algo_id is not None:
            _need(row.get("algoId") == selector.algo_id)
        if selector.algo_client_id is not None:
            _need(row.get("algoClOrdId") == selector.algo_client_id)
    _need(len(set(identities)) == len(identities))
    if selector.kind == "fills":
        _need(all(int(left) > int(right) for left, right in pairwise(identities)))
        _need(
            selector.after_bill_id is None
            or all(int(item) < int(selector.after_bill_id) for item in identities)
        )
    return canonical(value), tuple(identities)


@dataclass(frozen=True, slots=True, repr=False, eq=False)
class LiveTradeReadPageV1:
    selector: LiveTradeReadSelectorV1
    raw_body: bytes = field(repr=False)
    raw_headers: tuple[tuple[bytes, bytes], ...] = field(repr=False)
    stamps: tuple[ClockStamp, ...] = field(repr=False)
    signed_at: str
    lease_binding_id: str = field(repr=False)
    origin: str
    raw_sha256: str
    canonical_sha256: str
    headers_sha256: str
    request_signature_sha256: str
    tls_certificate_sha256: str | None
    receipt_sha256: str
    synthetic_mock: bool
    execution_authority: bool = field(default=False, init=False)
    protection_verified: bool = field(default=False, init=False)
    account_complete: bool = field(default=False, init=False)
    source_authenticity_verified: bool = field(default=False, init=False)


def live_trade_read_receipt_v1(page):
    _need(type(page) is LiveTradeReadPageV1)
    return {
        "schema": SCHEMA,
        "environment": "live",
        "selector": asdict(page.selector),
        "stamps": [stamp.model_dump() for stamp in page.stamps],
        **{
            name: getattr(page, name)
            for name in (
                "signed_at",
                "lease_binding_id",
                "origin",
                "raw_sha256",
                "canonical_sha256",
                "headers_sha256",
                "request_signature_sha256",
                "tls_certificate_sha256",
                "synthetic_mock",
            )
        },
        "execution_authority": False,
        "protection_verified": False,
        "account_complete": False,
        "source_authenticity_verified": False,
    }


def verify_live_trade_read_page_v1(page, *, lease):
    _need(type(page) is LiveTradeReadPageV1 and type(lease) is LivePinnedGetLeaseV1)
    lease.verify_current()
    selector = _checked_selector(page.selector)
    _need(
        page.lease_binding_id == lease.binding_id and page.origin == lease._pin.origin
    )
    _need(
        page.execution_authority
        is page.protection_verified
        is page.account_complete
        is page.source_authenticity_verified
        is False
    )
    _need(len(page.stamps) == 6 and type(page.synthetic_mock) is bool)
    _need(page.synthetic_mock is (lease._pin.environment == "test"))
    _need((page.tls_certificate_sha256 is None) is page.synthetic_mock)
    _need(
        page.tls_certificate_sha256 is None
        or type(page.tls_certificate_sha256) is str
        and _DIGEST.fullmatch(page.tls_certificate_sha256) is not None
    )
    _validate_clock(page.stamps)
    _validate_signed_time(page.signed_at, page.stamps[0], page.stamps[1])
    signature = build_signature(
        timestamp=page.signed_at,
        method="GET",
        request_path=_request_path(selector),
        body="",
        secret=lease._pin.credentials[1],
    )
    tokens = (*lease._pin.credentials, signature)
    length = _checked_response_headers(
        page.raw_headers, private_tokens=tokens, maximum=MAX_HISTORY_BODY
    )
    canonical_body, identities = _body(page.raw_body, selector, private_tokens=tokens)
    _need(length is None or length == len(page.raw_body))
    _need(
        page.raw_sha256 == sha(page.raw_body)
        and page.canonical_sha256 == sha(canonical_body)
    )
    _need(page.headers_sha256 == _header_digest(page.raw_headers))
    _need(
        page.request_signature_sha256
        == sha(
            canonical(
                {
                    "method": "GET",
                    "url": page.origin + _request_path(selector),
                    "signature": signature,
                }
            )
        )
    )
    _need(page.receipt_sha256 == sha(canonical(live_trade_read_receipt_v1(page))))
    return {
        "schema": SCHEMA,
        "raw_sha256": page.raw_sha256,
        "identities": identities,
        "execution_authority": False,
    }


async def observe_live_trade_read_page_v1(client, lease, selector):
    _need(
        type(client) is OkxLivePinnedGetClientV1 and type(lease) is LivePinnedGetLeaseV1
    )
    selector = _checked_selector(selector)
    try:
        observed = await _observe_owned_live_get_v1(
            client,
            lease,
            path=selector.path,
            request_path=_request_path(selector),
            selector=selector,
            validate_body=lambda raw, current, signature: _body(
                raw, selector, private_tokens=(*current._pin.credentials, signature)
            )[0],
        )
        unsealed = LiveTradeReadPageV1(
            selector,
            observed.raw_body,
            observed.raw_headers,
            observed.stamps,
            observed.signed_at,
            lease.binding_id,
            lease._pin.origin,
            sha(observed.raw_body),
            sha(observed.canonical_body),
            _header_digest(observed.raw_headers),
            observed.request_signature_sha256,
            observed.tls_certificate_sha256,
            "",
            observed.synthetic_mock,
        )
        page = replace(
            unsealed,
            receipt_sha256=sha(canonical(live_trade_read_receipt_v1(unsealed))),
        )
        verify_live_trade_read_page_v1(page, lease=lease)
        return page
    except LiveTradeReadPageError:
        raise
    except Exception:  # noqa: BLE001 -- no credential, provider body or route in operational errors.
        _need(False)
