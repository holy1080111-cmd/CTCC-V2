"""Exact own filled-entry/filled-close/flat DATA from complete bounded reads.

No constructor, raw dictionary, ACK or this assessment authorizes a database
release. The native owner and the existing durable journal must authenticate
the original observations, unique close intent and every NEW actual page.
"""

from dataclasses import dataclass, field
from fractions import Fraction

from app.domain.source_primitives import canonical, decode, sha
from app.trade_qualification import native_close_contracts_v1 as preflight
from app.trade_qualification import native_reconciliation_facts_v1 as facts

_ORDER_ROLES = (
    "entry_order_before",
    "close_order_before",
    "entry_order_after",
    "close_order_after",
)
_FILL_ROLES = (
    "entry_fills_recent",
    "entry_fills_history",
    "close_fills_recent",
    "close_fills_history",
)
_ALL_FILL_ROLES = ("final_fills_recent_all", "final_fills_history_all")


@dataclass(frozen=True, slots=True, repr=False)
class NativeClosedTradeDataV1:
    receipt_json: bytes = field(repr=False)
    classification: str = field(default="own_entry_and_close_filled_flat", init=False)
    execution_authority: bool = field(default=False, init=False)
    release_authority: bool = field(default=False, init=False)
    order_retry_authority: bool = field(default=False, init=False)


def need(value, reason="native_close_final_data_incomplete_or_changed"):
    facts.need(value, reason)


def _time(value, page):
    facts.source_time(value, page)
    return int(value)


def _one(observations, role, path, query):
    rows = facts.one(observations, role, path, query)
    need(len(rows) == 1)
    return rows[0]


def _source_page(observations, role, row):
    matches = [
        page
        for current, page in observations
        if current == role and row in facts.rows(page)
    ]
    need(len(matches) == 1)
    return matches[0]


def _order(observations, role, expected, *, closing):
    query = {"instId": expected["instId"], "clOrdId": expected["clOrdId"]}
    row = _one(observations, role, "/api/v5/trade/order", query)
    page = _source_page(observations, role, row)
    created, updated = _time(row.get("cTime"), page), _time(row.get("uTime"), page)
    size = facts.number(expected["sz"], positive=True)
    need(
        created <= updated
        and all(
            row.get(key) == expected[key]
            for key in ("instId", "clOrdId", "side", "posSide", "ordType", "tdMode")
        )
        and row.get("instType") == "SWAP"
        and row.get("state") == "filled"
        and type(row.get("ordId")) is str
        and facts._ID.fullmatch(row["ordId"]) is not None
        and facts.number(row.get("sz"), positive=True) == size
        and facts.number(row.get("accFillSz"), positive=True) == size
    )
    if closing:
        need(
            expected["ordType"] == "market"
            and "px" not in expected
            and row.get("px") in {"", "0"}
        )
        if expected["posSide"] == "net":
            need(expected.get("reduceOnly") is True and row.get("reduceOnly") == "true")
        else:
            # Hedge closing semantics come from the exact side/posSide pair;
            # the net-only reduceOnly parameter is not asserted as evidence.
            need(
                "reduceOnly" not in expected
                and (expected["side"], expected["posSide"])
                in {("sell", "long"), ("buy", "short")}
            )
    else:
        facts._order(row, expected)
    return row


def _terminal_pair(first, last):
    need(
        all(
            first.get(key) == last.get(key)
            for key in (
                "ordId",
                "instId",
                "instType",
                "side",
                "posSide",
                "tdMode",
                "ordType",
                "clOrdId",
                "sz",
                "accFillSz",
                "avgPx",
                "cTime",
                "state",
                "lever",
                "reduceOnly",
            )
        )
        and int(first["uTime"]) <= int(last["uTime"])
    )


def _fills(observations, prefix, order, expected, target):
    query = {"instType": "SWAP", "instId": expected["instId"], "ordId": order["ordId"]}
    merged, trades = {}, set()
    for suffix, path in (
        ("recent", "/api/v5/trade/fills"),
        ("history", "/api/v5/trade/fills-history"),
    ):
        role = prefix + "_fills_" + suffix
        for row in facts.chain(observations, role, path, query, "billId"):
            need(row["billId"] not in merged or merged[row["billId"]] == row)
            merged[row["billId"]] = row
            page = _source_page(observations, role, row)
            traded, generated = (
                _time(row.get("fillTime"), page),
                _time(row.get("ts"), page),
            )
            need(
                int(order["cTime"]) <= traded <= int(order["uTime"])
                and traded <= generated
            )
    total, weighted, fees = Fraction(0), Fraction(0), Fraction(0)
    for row in merged.values():
        need(
            all(
                row.get(key) == order.get(key)
                for key in ("ordId", "instId", "instType", "side", "posSide")
            )
            and row.get("feeCcy") == target.request.scope.settlement_currency
            and type(row.get("tradeId")) is str
            and facts._ID.fullmatch(row["tradeId"]) is not None
            and row["tradeId"] not in trades
        )
        trades.add(row["tradeId"])
        quantity, price = (
            facts.number(row.get("fillSz"), positive=True),
            facts.number(row.get("fillPx"), positive=True),
        )
        if prefix == "entry":
            limit = facts.number(expected["px"], positive=True)
            need(
                (expected["side"] == "buy" and price <= limit)
                or (expected["side"] == "sell" and price >= limit)
            )
        total, weighted = total + quantity, weighted + quantity * price
        fees += facts.number(row.get("fee"))  # Signed fee/rebate, exactly once.
    need(merged and total == facts.number(expected["sz"], positive=True))
    average = weighted / total
    need(
        facts.number(order.get("avgPx"), positive=True) == average,
        "native_reconcile_unresolved_average_precision",
    )
    return merged, average, fees


def assess_native_closed_trade_v1(
    target,
    original_observations,
    preflight_observations,
    close_intent_json,
    observations,
):
    """A finite terminal branch: one full entry, one full owned close, flat.

    Protective fills, partial closes, foreign fills, still-live protection or
    any ambiguous source remain unresolved and retain the original risk hold.
    """
    need(
        type(target) is facts.NativeReconciliationTargetV1
        and type(observations) is tuple
        and 1 <= len(observations) <= facts.read.MAX_REQUESTS
    )
    original = facts.assess_native_reconciliation_v1(target, original_observations)
    planned = preflight.assess_native_close_preflight_v1(
        target, original_observations, preflight_observations
    )
    document = decode(close_intent_json, 140000)
    need(
        document["version"] == "ctcc.native_controlled_close_intent.v1"
        and document["reservation_id"] == target.reservation_id
        and document["close_request_sha256"] == planned.exact_order_sha256
        and document["exact_close_order_json"].encode() == planned.exact_order_json
        and document["preflight_sha256"] == sha(planned.receipt_json)
    )
    close_expected = decode(planned.exact_order_json)
    inner = decode(decode(target.intent_json.encode())["intent_json"].encode())
    expected = inner["exact_order"]
    roles = {
        "config_before",
        "config_after",
        "balance",
        "positions",
        "account_risk",
        "leverage",
        "pending_orders",
        "original_protection_detail",
        "bills",
        *_ORDER_ROLES,
        *_FILL_ROLES,
        *_ALL_FILL_ROLES,
        *("algo_" + kind for kind in facts.read.ALGO_TYPES),
    }
    need(
        {role for role, _ in observations} == roles
        and tuple(role for role, _ in observations[:3])
        == ("config_before", "entry_order_before", "close_order_before")
        and tuple(role for role, _ in observations[-3:])
        == ("entry_order_after", "close_order_after", "config_after")
    )
    preflight._chronology(target, observations)
    need(
        preflight_observations[-1][1].eof_at < observations[0][1].started_at
        and preflight_observations[-1][1].eof_monotonic_ns
        < observations[0][1].started_monotonic_ns
    )
    # Existing account clocks retain documented update semantics. Empty nested
    # risk arrays need no invented per-member timestamp; top-level ts is real.
    facts._source_clocks(
        tuple(
            (role, page)
            for role, page in observations
            if role in {"balance", "positions", "account_risk"}
        )
    )
    config = _one(observations, "config_before", "/api/v5/account/config", {})
    need(
        config == _one(observations, "config_after", "/api/v5/account/config", {})
        and config.get("uid") == target.request.scope.account_id
        and config.get("mainUid") == target.main_uid
        and config.get("acctLv") == "2"
        and config.get("posMode") == inner["execution_binding"]["position_mode"]
    )
    entry_first = _order(observations, "entry_order_before", expected, closing=False)
    entry_last = _order(observations, "entry_order_after", expected, closing=False)
    closed_first = _order(
        observations, "close_order_before", close_expected, closing=True
    )
    closed_last = _order(
        observations, "close_order_after", close_expected, closing=True
    )
    _terminal_pair(entry_first, entry_last)
    _terminal_pair(closed_first, closed_last)
    old_entry = _one(
        original_observations,
        "order_after",
        "/api/v5/trade/order",
        {"instId": expected["instId"], "clOrdId": expected["clOrdId"]},
    )
    need(
        entry_last["ordId"] == old_entry["ordId"]
        and entry_last["cTime"] == old_entry["cTime"]
        and entry_last["ordId"] != closed_last["ordId"]
        and int(entry_last["uTime"]) <= int(closed_first["cTime"])
    )
    entry_fills, entry_average, entry_fees = _fills(
        observations, "entry", entry_last, expected, target
    )
    close_fills, close_average, close_fees = _fills(
        observations, "close", closed_last, close_expected, target
    )
    need(
        not set(entry_fills).intersection(close_fills)
        and not {row["tradeId"] for row in entry_fills.values()}.intersection(
            row["tradeId"] for row in close_fills.values()
        )
    )
    original_fills = {}
    for role, path in (
        ("fills_recent", "/api/v5/trade/fills"),
        ("fills_history", "/api/v5/trade/fills-history"),
    ):
        for row in facts.chain(
            original_observations,
            role,
            path,
            {
                "instType": "SWAP",
                "instId": expected["instId"],
                "ordId": entry_last["ordId"],
            },
            "billId",
        ):
            need(
                row["billId"] not in original_fills
                or original_fills[row["billId"]] == row
            )
            original_fills[row["billId"]] = row
    need(entry_fills == original_fills)
    final_trade_time = max(int(row["fillTime"]) for row in close_fills.values())
    need(not facts._balance_and_risk(observations, target.request))
    balance = _one(observations, "balance", "/api/v5/account/balance", {})
    risk = _one(
        observations, "account_risk", "/api/v5/account/account-position-risk", {}
    )
    need(
        int(balance["uTime"]) >= final_trade_time
        and int(risk["ts"]) >= final_trade_time
    )
    need(
        not facts.chain(
            observations, "pending_orders", "/api/v5/trade/orders-pending", {}, "ordId"
        )
    )
    for kind in facts.read.ALGO_TYPES:
        need(
            not facts.chain(
                observations,
                "algo_" + kind,
                "/api/v5/trade/orders-algo-pending",
                {"ordType": kind},
                "algoId",
            )
        )
    planned_doc = decode(planned.receipt_json)
    protection = _one(
        observations,
        "original_protection_detail",
        "/api/v5/trade/order-algo",
        {"algoId": planned_doc["protection_algo_id"]},
    )
    protection_page = _source_page(
        observations, "original_protection_detail", protection
    )
    bracket = expected["attachAlgoOrds"][0]
    old_protection = next(
        row
        for role, page in original_observations
        if role == "algo_oco"
        for row in facts.rows(page)
        if row.get("algoId") == planned_doc["protection_algo_id"]
    )
    need(
        protection.get("algoId") == planned_doc["protection_algo_id"]
        and protection.get("algoClOrdId") == inner["protection_client_order_id"]
        and protection.get("instId") == expected["instId"]
        and protection.get("instType") == "SWAP"
        and protection.get("tdMode") == "isolated"
        and protection.get("posSide") == expected["posSide"]
        and protection.get("side") == close_expected["side"]
        and protection.get("ordType") == "oco"
        and protection.get("state") == "canceled"
        and protection.get("failCode") == ""
        and protection.get("cTime") == old_protection.get("cTime")
        and facts.number(protection.get("sz"), positive=True)
        == facts.number(expected["sz"], positive=True)
        and facts.number(protection.get("lever"), positive=True)
        == target.request.risk_inputs.requested_leverage
        and all(
            protection.get(key) == bracket[key]
            for key in ("slOrdPx", "tpOrdPx", "slTriggerPxType", "tpTriggerPxType")
        )
        and facts.number(protection.get("slTriggerPx"), positive=True)
        == facts.number(bracket["slTriggerPx"], positive=True)
        and facts.number(protection.get("tpTriggerPx"), positive=True)
        == facts.number(bracket["tpTriggerPx"], positive=True)
        and (expected["posSide"] != "net" or protection.get("reduceOnly") == "true")
    )
    protection_created = _time(protection.get("cTime"), protection_page)
    protection_updated = _time(protection.get("uTime"), protection_page)
    need(
        protection_created <= protection_updated
        and protection_updated >= final_trade_time
    )
    leverage = facts.one(
        observations,
        "leverage",
        "/api/v5/account/leverage-info",
        {"instId": expected["instId"], "mgnMode": "isolated"},
    )
    selected = [
        row
        for row in leverage
        if row.get("instId") == expected["instId"]
        and row.get("mgnMode") == "isolated"
        and row.get("posSide") == expected["posSide"]
    ]
    need(
        len(selected) == 1
        and facts.number(selected[0].get("lever"), positive=True)
        == target.request.risk_inputs.requested_leverage
        and facts.number(closed_last.get("lever"), positive=True)
        == target.request.risk_inputs.requested_leverage
        and facts.number(entry_last.get("lever"), positive=True)
        == target.request.risk_inputs.requested_leverage
    )
    extra = [(role, page) for role, page in observations if role in _ALL_FILL_ROLES]
    need(len(extra) > 0)
    query = dict(extra[0][1].parameters)
    begin, end = query.get("begin"), query.get("end")
    need(
        type(begin) is str
        and type(end) is str
        and begin.isascii()
        and begin.isdigit()
        and end.isascii()
        and end.isdigit()
        and int(begin) == int(entry_last["cTime"]) - 1 > 0
        and final_trade_time
        <= int(end)
        <= preflight._milliseconds(extra[0][1].started_at)
    )
    window = {
        "instType": "SWAP",
        "instId": expected["instId"],
        "begin": begin,
        "end": end,
    }
    all_fills = {}
    for role, path in (
        ("final_fills_recent_all", "/api/v5/trade/fills"),
        ("final_fills_history_all", "/api/v5/trade/fills-history"),
    ):
        for row in facts.chain(observations, role, path, window, "billId"):
            page = _source_page(observations, role, row)
            generated = _time(row.get("ts"), page)
            need(
                int(begin) <= generated <= int(end)
                and _time(row.get("fillTime"), page) <= generated
                and row.get("instType") == "SWAP"
                and row.get("instId") == expected["instId"]
                and (row["billId"] not in all_fills or all_fills[row["billId"]] == row)
            )
            all_fills[row["billId"]] = row
    need(all_fills == {**entry_fills, **close_fills})
    bills = facts.chain(
        observations,
        "bills",
        "/api/v5/account/bills-archive",
        {"begin": begin, "end": end},
        "billId",
    )
    for row in bills:
        page = _source_page(observations, "bills", row)
        need(int(begin) <= _time(row.get("ts"), page) <= int(end))
    page_index = [
        {
            "role": role,
            "raw_sha256": sha(page.raw_body),
            "eof_at": page.eof_at.isoformat(),
            "eof_monotonic_ns": page.eof_monotonic_ns,
        }
        for role, page in observations
    ]
    receipt = canonical(
        {
            "version": "ctcc.native_closed_trade_data.v1",
            "classification": "own_entry_and_close_filled_flat",
            "reservation_id": target.reservation_id,
            "request_sha256": sha(target.request_json.encode()),
            "entry_intent_sha256": target.intent_sha256,
            "close_intent_sha256": sha(close_intent_json),
            "preflight_sha256": sha(planned.receipt_json),
            "original_assessment_sha256": sha(original.receipt_json),
            "entry_order_id": entry_last["ordId"],
            "close_order_id": closed_last["ordId"],
            "entry_client_order_id": expected["clOrdId"],
            "close_client_order_id": close_expected["clOrdId"],
            "protection_algo_id": protection["algoId"],
            "protection_client_order_id": protection["algoClOrdId"],
            "entry_average_ratio": facts.ratio(entry_average),
            "close_average_ratio": facts.ratio(close_average),
            "entry_signed_fees_ratio": facts.ratio(entry_fees),
            "close_signed_fees_ratio": facts.ratio(close_fees),
            "entry_fill_count": len(entry_fills),
            "entry_fills_sha256": sha(canonical(entry_fills)),
            "close_fill_count": len(close_fills),
            "close_fills_sha256": sha(canonical(close_fills)),
            "bill_count": len(bills),
            "bills_sha256": sha(canonical(bills)),
            "history_window": {
                "begin": begin,
                "end": end,
                "semantics": "ts_record_generation_or_balance_update_not_fillTime_or_funding_accrual",
            },
            "page_count": len(page_index),
            "pages_sha256": sha(canonical(page_index)),
            "source_authenticity_verified": False,
            "release_authority": False,
            "execution_authority": False,
            "order_retry_authority": False,
        }
    )
    need(len(receipt) <= 16384)
    return NativeClosedTradeDataV1(receipt)
