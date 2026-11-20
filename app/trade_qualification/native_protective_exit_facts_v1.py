"""Original OCO terminal DATA, not authority or an invented local close.

Only a single full original mark-price OCO exit is supported. Splits, partials,
foreign activity, missing funding history and changed protection stay UNKNOWN.
The separately guarded SQL predicate and independent readback resolve risk.
Official vocabulary: https://www.okx.com/docs-v5/en/#order-book-trading-algo-trading-get-algo-order-details
"""

from dataclasses import dataclass, field
from fractions import Fraction

from app.domain.source_primitives import canonical, decode, sha
from app.trade_qualification import native_close_contracts_v1 as chronology
from app.trade_qualification import native_reconciliation_facts_v1 as facts


@dataclass(frozen=True, slots=True, repr=False)
class NativeProtectiveExitAssessmentV1:
    receipt_json: bytes = field(repr=False)
    execution_authority: bool = field(default=False, init=False)
    order_retry_authority: bool = field(default=False, init=False)
    release_authority: bool = field(default=False, init=False)


def _need(value):
    facts.need(value, "native_original_protective_exit_incomplete_or_changed")


def original_protection(target, original):
    assessment = facts.assess_native_reconciliation_v1(target, original)
    _need(assessment.filled_and_protected is True)
    outer = decode(target.intent_json.encode(), facts.wire.MAX_NATIVE_BYTES)
    inner = decode(outer["intent_json"].encode(), facts.wire.MAX_NATIVE_BYTES)
    order = inner["exact_order"]
    rows = facts.chain(
        original,
        "algo_oco",
        "/api/v5/trade/orders-algo-pending",
        {"ordType": "oco"},
        "algoId",
    )
    _need(len(rows) == 1)
    return order, inner, rows[0]


def _one(observations, role, path, query):
    rows = facts.one(observations, role, path, query)
    _need(len(rows) == 1)
    return rows[0]


def _fill_set(observations, prefix, expected, order_id):
    query = {"instType": "SWAP", "instId": expected["instId"], "ordId": order_id}
    retained = {}
    bills = set()
    for suffix, path in (
        ("recent", "/api/v5/trade/fills"),
        ("history", "/api/v5/trade/fills-history"),
    ):
        for row in facts.chain(
            observations, prefix + "_fills_" + suffix, path, query, "billId"
        ):
            _need(
                row.get("ordId") == order_id
                and row.get("instId") == expected["instId"]
                and row.get("instType") == "SWAP"
                and row.get("posSide") == expected["posSide"]
                and row.get("side") == expected["side"]
                and row.get("feeCcy") == "USDT"
                and type(row.get("tradeId")) is str
                and row["tradeId"].isdigit()
                and int(row["tradeId"]) > 0
            )
            previous = retained.get(row["tradeId"])
            _need(previous is None or previous == row)
            if previous is None:
                _need(row["billId"] not in bills)
                bills.add(row["billId"])
                retained[row["tradeId"]] = row
    _need(bool(retained))
    quantity = sum(
        (facts.number(row.get("fillSz"), positive=True) for row in retained.values()),
        Fraction(0),
    )
    average = (
        sum(
            (
                facts.number(row.get("fillSz"), positive=True)
                * facts.number(row.get("fillPx"), positive=True)
                for row in retained.values()
            ),
            Fraction(0),
        )
        / quantity
    )
    fees = sum((facts.number(row.get("fee")) for row in retained.values()), Fraction(0))
    return retained, quantity, average, fees


def assess_original_protective_exit_v1(target, original, observations):
    """Recompute complete original entry + original triggered OCO + full flat exit."""
    expected, inner, old_algo = original_protection(target, original)
    _need(type(observations) is tuple and bool(observations))
    chronology._chronology(target, observations)
    _need(original[-1][1].eof_at < observations[0][1].started_at)
    required_roles = {
        "config_before",
        "config_after",
        "protection_before",
        "protection_after",
        "entry_order_before",
        "entry_order_after",
        "exit_order_before",
        "exit_order_after",
        "entry_fills_recent",
        "entry_fills_history",
        "exit_fills_recent",
        "exit_fills_history",
        "balance",
        "positions",
        "account_risk",
        "pending_orders",
        "leverage",
        "exit_bills_recent",
        "exit_bills_archive",
        "exit_all_fills_recent",
        "exit_all_fills_history",
        *("algo_" + kind for kind in facts.read.ALGO_TYPES),
    }
    _need({role for role, _ in observations} == required_roles)
    facts._source_clocks(
        tuple(
            (role, page)
            for role, page in observations
            if role in {"balance", "positions", "account_risk"}
        )
    )
    config = _one(observations, "config_before", "/api/v5/account/config", {})
    _need(config == _one(observations, "config_after", "/api/v5/account/config", {}))
    _need(
        config.get("uid") == target.request.scope.account_id
        and config.get("mainUid") == target.main_uid
        and config.get("acctLv") == "2"
        and config.get("posMode") == inner["execution_binding"]["position_mode"]
    )
    algo_query = {"algoId": old_algo["algoId"]}
    algo = _one(
        observations, "protection_before", "/api/v5/trade/order-algo", algo_query
    )
    _need(
        algo
        == _one(
            observations, "protection_after", "/api/v5/trade/order-algo", algo_query
        )
    )
    fixed = (
        "algoId",
        "algoClOrdId",
        "instId",
        "instType",
        "tdMode",
        "posSide",
        "side",
        "ordType",
        "sz",
        "slOrdPx",
        "tpOrdPx",
        "slTriggerPx",
        "tpTriggerPx",
        "slTriggerPxType",
        "tpTriggerPxType",
        "cTime",
    )
    _need(all(algo.get(key) == old_algo.get(key) for key in fixed))
    _need(
        algo.get("state") == "effective"
        and algo.get("actualSide") in {"tp", "sl"}
        and algo.get("failCode") in {"", "0"}
        and algo.get("slTriggerPxType") == algo.get("tpTriggerPxType") == "mark"
        and algo.get("algoClOrdId") == inner["protection_client_order_id"]
        and facts.number(algo.get("lever"), positive=True)
        == target.request.risk_inputs.requested_leverage
        and (expected["posSide"] != "net" or algo.get("reduceOnly") == "true")
        and type(algo.get("ordIdList")) is list
        and len(algo["ordIdList"]) == 1
        and type(algo["ordIdList"][0]) is str
        and algo["ordIdList"][0].isdigit()
        and int(algo["ordIdList"][0]) > 0
        and algo.get("subAlgoIdList") == []
        and (algo.get("ordId") in ("", algo["ordIdList"][0]))
    )
    child_id = algo["ordIdList"][0]
    query = {"instId": expected["instId"], "ordId": child_id}
    child = _one(observations, "exit_order_before", "/api/v5/trade/order", query)
    _need(child == _one(observations, "exit_order_after", "/api/v5/trade/order", query))
    opposite = "sell" if expected["side"] == "buy" else "buy"
    _need(
        all(
            child.get(k) == v
            for k, v in {
                "ordId": child_id,
                "instId": expected["instId"],
                "instType": "SWAP",
                "tdMode": "isolated",
                "posSide": expected["posSide"],
                "side": opposite,
                "ordType": "market",
                "state": "filled",
                "algoId": old_algo["algoId"],
            }.items()
        )
        and (expected["posSide"] != "net" or child.get("reduceOnly") == "true")
        and facts.number(child.get("lever"), positive=True)
        == target.request.risk_inputs.requested_leverage
    )
    size = facts.number(expected["sz"], positive=True)
    _need(
        facts.number(algo.get("actualSz"), positive=True)
        == size
        == facts.number(child.get("sz"), positive=True)
        == facts.number(child.get("accFillSz"), positive=True)
    )
    entry_query = {"instId": expected["instId"], "clOrdId": expected["clOrdId"]}
    entry = _one(observations, "entry_order_before", "/api/v5/trade/order", entry_query)
    _need(
        entry
        == _one(observations, "entry_order_after", "/api/v5/trade/order", entry_query)
    )
    entry_expected = _one(original, "order_after", "/api/v5/trade/order", entry_query)
    _need(entry == entry_expected)
    # The original assessor independently validates its fills. Compare the NEW
    # complete entry population, rather than trusting an old average or count.
    original_fills = {}
    for suffix, path in (
        ("recent", "/api/v5/trade/fills"),
        ("history", "/api/v5/trade/fills-history"),
    ):
        for row in facts.chain(
            original,
            "fills_" + suffix,
            path,
            {"instType": "SWAP", "instId": expected["instId"], "ordId": entry["ordId"]},
            "billId",
        ):
            previous = original_fills.setdefault(row["tradeId"], row)
            _need(previous == row)
    new_entry, entry_size, entry_average, entry_fees = _fill_set(
        observations, "entry", expected, entry["ordId"]
    )
    _need(new_entry == original_fills and entry_size == size)
    exit_expected = {**expected, "side": opposite}
    exits, exit_size, exit_average, exit_fees = _fill_set(
        observations, "exit", exit_expected, child_id
    )
    _need(exit_size == size and not set(exits) & set(new_entry))
    _need(
        facts.number(child.get("avgPx"), positive=True)
        == exit_average
        == facts.number(algo.get("actualPx"), positive=True)
    )
    entry_time = min(int(row["fillTime"]) for row in new_entry.values())
    exit_time = max(int(row["fillTime"]) for row in exits.values())
    trigger_time = facts.source_time(
        algo.get("triggerTime"),
        next(page for role, page in observations if role == "protection_before"),
    )
    _need(
        entry_time
        <= int(algo["triggerTime"])
        <= min(int(row["fillTime"]) for row in exits.values())
        <= exit_time
    )
    _need(
        int(child["cTime"]) >= int(algo["triggerTime"])
        and int(child["uTime"]) >= exit_time
    )
    _need(trigger_time is not None)
    _need(not facts._balance_and_risk(observations, target.request))
    leverage_rows = facts.one(
        observations,
        "leverage",
        "/api/v5/account/leverage-info",
        {"instId": expected["instId"], "mgnMode": "isolated"},
    )
    selected = [
        row
        for row in leverage_rows
        if row.get("instId") == expected["instId"]
        and row.get("mgnMode") == "isolated"
        and row.get("posSide") == expected["posSide"]
    ]
    _need(
        len(selected) == 1
        and facts.number(selected[0].get("lever"), positive=True)
        == target.request.risk_inputs.requested_leverage
    )
    _need(
        not facts.chain(
            observations, "pending_orders", "/api/v5/trade/orders-pending", {}, "ordId"
        )
    )
    for kind in facts.read.ALGO_TYPES:
        _need(
            not facts.chain(
                observations,
                "algo_" + kind,
                "/api/v5/trade/orders-algo-pending",
                {"ordType": kind},
                "algoId",
            )
        )
    funding = {}
    cutoff = chronology._milliseconds(observations[0][1].started_at)
    bill_query = {
        "begin": str(entry_time),
        "end": str(cutoff),
    }
    population = {}
    for suffix, path in (
        ("recent", "/api/v5/trade/fills"),
        ("history", "/api/v5/trade/fills-history"),
    ):
        for row in facts.chain(
            observations,
            "exit_all_fills_" + suffix,
            path,
            {"instType": "SWAP", "begin": str(entry_time), "end": str(cutoff)},
            "billId",
        ):
            _need(type(row.get("tradeId")) is str)
            previous = population.setdefault(row["tradeId"], row)
            _need(previous == row)
    _need(population == {**new_entry, **exits})
    for suffix, path in (
        ("recent", "/api/v5/account/bills"),
        ("archive", "/api/v5/account/bills-archive"),
    ):
        for row in facts.chain(
            observations, "exit_bills_" + suffix, path, bill_query, "billId"
        ):
            _need(
                row.get("instId") == expected["instId"]
                and row.get("ccy") == "USDT"
                and entry_time <= int(row["ts"]) <= cutoff
            )
            previous = funding.setdefault(row["billId"], row)
            _need(previous == row)
            _need(row.get("type") in {"2", "8"})
            facts.source_time(
                row.get("ts"),
                next(
                    page
                    for role, page in observations
                    if role == "exit_bills_" + suffix and row in facts.rows(page)
                ),
            )
            if row["type"] == "2":
                _need(row.get("ordId") in {entry["ordId"], child_id})
            else:
                _need(int(row["ts"]) <= exit_time)
    funding_cash = sum(
        (
            facts.number(row.get("balChg"))
            for row in funding.values()
            if row["type"] == "8"
        ),
        Fraction(0),
    )
    return NativeProtectiveExitAssessmentV1(
        canonical(
            {
                "version": "ctcc.native_original_protective_exit_data.v1",
                "classification": "original_oco_full_exit_flat",
                "reservation_id": target.reservation_id,
                "request_sha256": sha(target.request_json.encode()),
                "intent_sha256": target.intent_sha256,
                "protection_algo_id": old_algo["algoId"],
                "protection_client_order_id": inner["protection_client_order_id"],
                "trigger_side": algo["actualSide"],
                "child_order_id": child_id,
                "entry_order_id": entry["ordId"],
                "contracts": facts.ratio(size),
                "entry_price": facts.ratio(entry_average),
                "exit_price": facts.ratio(exit_average),
                "entry_fees_signed": facts.ratio(entry_fees),
                "exit_fees_signed": facts.ratio(exit_fees),
                "funding_cash_signed": facts.ratio(funding_cash),
                "funding_semantics": "bill_generation_cashflow_not_accrual_time",
                "mfe": None,
                "mae": None,
                "metrics_complete": False,
                "source_authenticity_verified": False,
                "release_authority": False,
                "execution_authority": False,
                "order_retry_authority": False,
            }
        )
    )
