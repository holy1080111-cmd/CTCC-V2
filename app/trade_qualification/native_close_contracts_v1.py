"""Exact own-position close preflight DATA, never close/write eligibility.

Reuse the existing native entry/protection arithmetic and raw page vocabulary.
The native owner must authenticate both the original committed observations and
every NEW observation. No DTO, true flag, price or size override grants a close.
"""

from dataclasses import dataclass, field
from fractions import Fraction
from urllib.parse import parse_qsl, urlsplit

from app.domain.source_primitives import canonical, decode, sha
from app.trade_qualification import native_reconciliation_facts_v1 as facts

_EXTRA = frozenset({"close_fills_recent_all", "close_fills_history_all"})


@dataclass(frozen=True, slots=True, repr=False)
class NativeClosePreflightDataV1:
    receipt_json: bytes = field(repr=False)
    exact_order_json: bytes = field(repr=False)
    exact_order_sha256: str = field(repr=False)
    execution_authority: bool = field(default=False, init=False)
    order_retry_authority: bool = field(default=False, init=False)


def _need(value):
    facts.need(value, "native_close_preflight_incomplete_or_changed")


def _milliseconds(value):
    delta = value - facts._EPOCH
    return (delta.days * 86400 + delta.seconds) * 1000 + delta.microseconds // 1000


def _chronology(target, observations):
    previous = None
    for _, page in observations:
        _need(type(page) is facts.read.DemoReconciliationObservationV1)
        url = urlsplit(page.request_url)
        _need(
            url.scheme + "://" + url.netloc == target.origin
            and url.path == page.path
            and not url.fragment
            and page.tls_hostname == url.hostname
            and tuple(sorted(parse_qsl(url.query, keep_blank_values=True)))
            == page.parameters
            and page.started_at <= page.headers_at <= page.eof_at
            and page.started_monotonic_ns
            <= page.headers_monotonic_ns
            <= page.eof_monotonic_ns
            and (
                previous is None
                or (
                    previous.eof_at < page.started_at
                    and previous.eof_monotonic_ns < page.started_monotonic_ns
                )
            )
        )
        previous = page


def assess_native_close_preflight_v1(target, original_observations, observations):
    """Only an unchanged, actually owned fully protected position is planned.

    Other exit/foreign/manual activity is retained and unresolved. This finite
    first-close branch does not relabel a partial or already closed position.
    """
    _need(
        type(target) is facts.NativeReconciliationTargetV1
        and type(original_observations) is tuple
        and type(observations) is tuple
        and 1 <= len(observations) <= facts.read.MAX_REQUESTS
    )
    initial = facts.assess_native_reconciliation_v1(target, original_observations)
    _need(
        initial.filled_and_protected is True
        and initial.terminal_zero_fill_flat is False
    )
    _chronology(target, observations)
    _need(
        original_observations[-1][1].eof_at < observations[0][1].started_at
        and original_observations[-1][1].eof_monotonic_ns
        < observations[0][1].started_monotonic_ns
    )
    core = tuple((role, page) for role, page in observations if role not in _EXTRA)
    current = facts.assess_native_reconciliation_v1(target, core)
    _need(current.filled_and_protected is True)
    outer = decode(target.intent_json.encode(), facts.wire.MAX_NATIVE_BYTES)
    intent = decode(outer["intent_json"].encode(), facts.wire.MAX_NATIVE_BYTES)
    expected = intent["exact_order"]
    order_query = {"instId": expected["instId"], "clOrdId": expected["clOrdId"]}
    old_order = facts.one(
        original_observations, "order_after", "/api/v5/trade/order", order_query
    )[0]
    order = facts.one(core, "order_after", "/api/v5/trade/order", order_query)[0]
    _need(
        old_order["ordId"] == order["ordId"]
        and old_order["cTime"] == order["cTime"]
        and facts.number(old_order["accFillSz"]) == facts.number(order["accFillSz"])
    )
    previous = facts._balance_and_risk(original_observations, target.request)[0]
    position = facts._balance_and_risk(core, target.request)[0]
    _need(
        type(previous.get("posId")) is str
        and facts._ID.fullmatch(previous["posId"]) is not None
        and position.get("posId") == previous["posId"]
    )
    extra = [(role, page) for role, page in observations if role in _EXTRA]
    _need(extra and {role for role, _ in extra} == _EXTRA)
    first_query = dict(extra[0][1].parameters)
    begin, end = first_query.get("begin"), first_query.get("end")
    _need(
        type(begin) is str
        and type(end) is str
        and begin.isascii()
        and end.isascii()
        and begin.isdigit()
        and end.isdigit()
        and int(begin) == int(order["cTime"]) - 1 > 0
        and _milliseconds(original_observations[-1][1].eof_at) < int(end)
        and int(end) <= _milliseconds(extra[0][1].started_at)
        and int(position["uTime"]) < int(end)
    )
    # The one-ms lower query padding handles unspecified API bound inclusion;
    # no source clock is changed or tolerated. Preserve all returned boundaries.
    query = {
        "instType": "SWAP",
        "instId": expected["instId"],
        "begin": begin,
        "end": end,
    }
    all_fills = {}
    for role, path in (
        ("close_fills_recent_all", "/api/v5/trade/fills"),
        ("close_fills_history_all", "/api/v5/trade/fills-history"),
    ):
        for item in facts.chain(observations, role, path, query, "billId"):
            page = next(
                page
                for current_role, page in observations
                if current_role == role and item in facts.rows(page)
            )
            matched, generated = (
                facts.source_time(item.get("fillTime"), page),
                facts.source_time(item.get("ts"), page),
            )
            _need(
                matched <= generated
                and int(begin) <= int(item["ts"]) <= int(end)
                and item.get("instType") == "SWAP"
                and item.get("instId") == expected["instId"]
            )
            identity = item["billId"]
            _need(identity not in all_fills or all_fills[identity] == item)
            all_fills[identity] = item
    relevant = {
        identity: item
        for identity, item in all_fills.items()
        if int(item["ts"]) >= int(order["cTime"])
    }
    _need(
        relevant
        and all(item.get("ordId") == order["ordId"] for item in relevant.values())
    )
    owned = {}
    fills_query = {
        "instType": "SWAP",
        "instId": expected["instId"],
        "ordId": order["ordId"],
    }
    for role, path in (
        ("fills_recent", "/api/v5/trade/fills"),
        ("fills_history", "/api/v5/trade/fills-history"),
    ):
        for item in facts.chain(core, role, path, fills_query, "billId"):
            _need(item["billId"] not in owned or owned[item["billId"]] == item)
            owned[item["billId"]] = item
    _need(relevant == owned)
    mode = intent["execution_binding"]["position_mode"]
    side = "sell" if expected["side"] == "buy" else "buy"
    payload = {
        "instId": expected["instId"],
        "tdMode": "isolated",
        "ordType": "market",
        "side": side,
        "posSide": expected["posSide"],
        "sz": expected["sz"],
        "clOrdId": "CTC" + target.reservation_id[:29],
    }
    if mode == "net_mode":
        _need(expected["posSide"] == "net")
        payload["reduceOnly"] = True
        semantics = "net_explicit_reduce_only"
    else:
        _need(
            mode == "long_short_mode"
            and ((side, expected["posSide"]) in {("sell", "long"), ("buy", "short")})
        )
        semantics = "hedge_exact_closing_side_and_position_side"
    remaining = abs(facts.number(position["pos"]))
    _need(
        remaining == facts.number(payload["sz"], positive=True)
        and remaining == Fraction(target.request.risk_inputs.requested_contracts)
        and payload["clOrdId"] != expected["clOrdId"]
        and payload["clOrdId"] != intent["protection_client_order_id"]
    )
    protection = next(
        item
        for kind in facts.read.ALGO_TYPES
        for item in facts.chain(
            core,
            "algo_" + kind,
            "/api/v5/trade/orders-algo-pending",
            {"ordType": kind},
            "algoId",
        )
    )
    raw = canonical(payload)
    receipt = canonical(
        {
            "version": "ctcc.native_close_preflight_data.v1",
            "reservation_id": target.reservation_id,
            "original_event_key": target.request.origin.original_event_key,
            "request_sha256": sha(target.request_json.encode()),
            "entry_intent_sha256": target.intent_sha256,
            "entry_order_id": order["ordId"],
            "entry_client_order_id": expected["clOrdId"],
            "position_id": position["posId"],
            "position_mode": mode,
            "position_side": position["posSide"],
            "protection_algo_id": protection["algoId"],
            "protection_client_order_id": intent["protection_client_order_id"],
            "remaining_contracts": facts.ratio(remaining),
            "close_client_order_id": payload["clOrdId"],
            "exact_close_order_sha256": sha(raw),
            "closing_semantics": semantics,
            "fill_generation_window": {
                "query_begin_ms": begin,
                "query_end_ms": end,
                "semantics": "ts_record_generation_not_fillTime",
                "returned_boundary_rows_retained": True,
            },
            "original_assessment_sha256": sha(initial.receipt_json),
            "fresh_assessment_sha256": sha(current.receipt_json),
            "fresh_pages": [
                {
                    "role": role,
                    "raw_sha256": sha(page.raw_body),
                    "eof_at": page.eof_at.isoformat(),
                    "started_monotonic_ns": page.started_monotonic_ns,
                    "eof_monotonic_ns": page.eof_monotonic_ns,
                }
                for role, page in observations
            ],
            "source_authenticity_verified": False,
            "execution_authority": False,
            "order_retry_authority": False,
        }
    )
    return NativeClosePreflightDataV1(receipt, raw, sha(raw))
