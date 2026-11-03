"""Exact native post-submit computations over supplied private raw GET DATA.

This is not a source owner, an ACK classifier or reservation release API. The
native consumer must authenticate every actual observation and durable target.
The only flat computation here is a terminal, zero-fill FOK with complete fresh
whole-account inventories. Filled/protected positions keep their durable hold.
"""

from __future__ import annotations

import itertools
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from fractions import Fraction
from urllib.parse import parse_qsl, urlsplit

from app.domain.source_primitives import canonical, decode, sha
from app.exchange.okx import native_reconciliation_observation_v1 as read
from app.trade_qualification import account_materializer as materializer
from app.trade_qualification import native_ledger_contracts_v2 as wire

_DECIMAL = re.compile(r"-?(?:0|[1-9][0-9]{0,23})(?:\.[0-9]{1,24})?\Z")
_ID = re.compile(r"[1-9][0-9]{0,39}\Z")
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class NativeReconciliationFactsError(ValueError):
    pass


def need(condition, code):
    if condition is not True:
        raise NativeReconciliationFactsError(code) from None


def number(value, *, positive=False):
    need(
        type(value) is str and _DECIMAL.fullmatch(value) is not None,
        "native_reconcile_number_invalid",
    )
    result = Fraction(value)
    need(not positive or result > 0, "native_reconcile_number_invalid")
    return result


def ratio(value):
    return {"numerator": str(value.numerator), "denominator": str(value.denominator)}


def source_time(value, page):
    """One documented source event/update/generation clock, never restamped."""
    need(
        type(value) is str
        and 1 <= len(value) <= 16
        and value.isascii()
        and value.isdigit()
        and int(value) > 0,
        "native_reconcile_source_clock_missing_or_invalid",
    )
    try:
        actual = _EPOCH + timedelta(milliseconds=int(value))
    except (ValueError, OverflowError):
        need(False, "native_reconcile_source_clock_missing_or_invalid")
    need(actual <= page.eof_at, "native_reconcile_future_source_clock")
    return actual


def _source_clocks(observations):
    """Keep source-update clocks distinct from fill record-generation clocks.

    An unchanged balance/position update can be old. Fresh signed GET receipt
    proves the new read; no arbitrary update-age or future-arrival watermark is
    introduced. Fills.ts is generation and must not precede actual fillTime.
    Account-position-risk ts is an account-information UPDATE/snapshot cutoff,
    not a clock that must refresh for every GET; nested timestamps are optional.
    """
    for role, page in observations:
        current = rows(page)
        for item in current:
            required = (
                ("cTime", "uTime")
                if role in {"order_before", "order_after", "positions"}
                else (
                    ("fillTime", "ts")
                    if role in {"fills_recent", "fills_history"}
                    else ("uTime",)
                    if role == "balance"
                    else ("ts",)
                    if role == "account_risk"
                    else ("cTime",)
                    if role.startswith("algo_")
                    else ()
                )
            )
            for name in required:
                source_time(item.get(name), page)
            pending = [item]
            while pending:
                part = pending.pop()
                if type(part) is dict:
                    for name, value in part.items():
                        if name in {
                            "cTime",
                            "uTime",
                            "fillTime",
                            "ts",
                        } and value not in (None, ""):
                            source_time(value, page)
                        elif type(value) in (dict, list):
                            pending.append(value)
                elif type(part) is list:
                    pending.extend(part)
            if role in {"order_before", "order_after", "positions"}:
                need(
                    int(item["cTime"]) <= int(item["uTime"]),
                    "native_reconcile_source_lifecycle_reversed",
                )
            if role in {"fills_recent", "fills_history"}:
                need(
                    int(item["fillTime"]) <= int(item["ts"]),
                    "native_reconcile_fill_generation_precedes_trade",
                )
            if role == "balance":
                need(
                    type(item.get("details")) is list,
                    "native_reconcile_full_account_missing",
                )
                for detail in item["details"]:
                    source_time(detail.get("uTime"), page)


def _single_page(observations, role):
    result = [page for name, page in observations if name == role]
    need(len(result) == 1, "native_reconcile_exact_query_missing")
    return result[0]


@dataclass(frozen=True, slots=True, repr=False)
class NativeReconciliationTargetV1:
    request: wire.NativeReservationRequestV4 = field(repr=False)
    request_json: str = field(repr=False)
    intent_json: str = field(repr=False)
    intent_sha256: str = field(repr=False)
    reservation_id: str = field(repr=False)
    main_uid: str = field(repr=False)
    origin: str = field(repr=False)
    original_credential_session_sha256: str = field(repr=False)
    durable_lineage_sha256: str = field(repr=False)


@dataclass(frozen=True, slots=True, repr=False)
class NativeReconciliationAssessmentV1:
    receipt_json: bytes = field(repr=False)
    classification: str
    # DATA computation only: the repository accepts an owned witness, not this.
    terminal_zero_fill_flat: bool
    filled_and_protected: bool
    execution_authority: bool = field(default=False, init=False)
    order_retry_authority: bool = field(default=False, init=False)


def rows(observation):
    need(
        type(observation) is read.DemoReconciliationObservationV1
        and observation.http_status == 200
        and observation.account_complete is False
        and observation.execution_authority is False,
        "native_reconcile_observation_type_invalid",
    )
    payload = decode(observation.raw_body, read.MAX_BODY)
    need(
        type(payload) is dict
        and payload.get("code") == "0"
        and type(payload.get("data")) is list
        and all(type(item) is dict for item in payload["data"]),
        "native_reconcile_response_invalid",
    )
    return payload["data"]


def one(observations, role, path, query):
    group = [item for name, item in observations if name == role]
    need(
        len(group) == 1
        and group[0].path == path
        and group[0].parameters == read.checked_query(path, query),
        "native_reconcile_exact_query_missing",
    )
    value = rows(group[0])
    return value


def chain(observations, role, path, query, cursor):
    pages = [item for name, item in observations if name == role]
    need(1 <= len(pages) <= 16, "native_reconcile_terminal_chain_missing")
    collected, seen, after = [], set(), None
    for index, page in enumerate(pages):
        current = {**query, "limit": "100"}
        if after is not None:
            current["after"] = after
        need(
            page.path == path and page.parameters == read.checked_query(path, current),
            "native_reconcile_cursor_query_mismatch",
        )
        group = rows(page)
        need(len(group) <= 100, "native_reconcile_page_limit")
        if not group:
            need(index == len(pages) - 1, "native_reconcile_rows_after_terminal")
            return collected
        identifiers = [item.get(cursor) for item in group]
        need(
            all(
                type(item) is str and _ID.fullmatch(item) is not None
                for item in identifiers
            )
            and len(set(identifiers)) == len(identifiers)
            and not seen.intersection(identifiers)
            and all(
                int(left) > int(right)
                for left, right in itertools.pairwise(identifiers)
            )
            and (after is None or int(identifiers[0]) < int(after)),
            "native_reconcile_page_identity_conflict",
        )
        seen.update(identifiers)
        after = identifiers[-1]
        collected.extend(group)
    need(False, "native_reconcile_terminal_chain_missing")


def _order(raw, expected):
    need(type(raw) is dict, "native_reconcile_exact_order_missing")
    fixed = {
        key: expected[key]
        for key in ("instId", "tdMode", "side", "posSide", "ordType", "clOrdId")
    }
    need(
        all(raw.get(key) == value for key, value in fixed.items())
        and raw.get("instType") == "SWAP"
        and type(raw.get("ordId")) is str
        and _ID.fullmatch(raw["ordId"]) is not None
        and raw.get("state") in {"filled", "canceled"}
        and number(raw.get("sz"), positive=True)
        == number(expected["sz"], positive=True)
        and number(raw.get("px"), positive=True)
        == number(expected["px"], positive=True)
        and raw.get("reduceOnly") == "false",
        "native_reconcile_order_binding_mismatch",
    )
    created, updated = raw.get("cTime"), raw.get("uTime")
    need(
        type(created) is str
        and type(updated) is str
        and created.isascii()
        and updated.isascii()
        and created.isdigit()
        and updated.isdigit()
        and 1 <= len(created) <= 16
        and 1 <= len(updated) <= 16
        and 0 < int(created) <= int(updated),
        "native_reconcile_order_clock_invalid",
    )
    return number(raw.get("accFillSz")), created, updated


def _balance_and_risk(observations, request):
    balance = one(observations, "balance", "/api/v5/account/balance", {})
    risk = one(
        observations, "account_risk", "/api/v5/account/account-position-risk", {}
    )
    need(len(balance) == len(risk) == 1, "native_reconcile_full_account_missing")
    equity, available, gap = materializer._balance(
        balance[0], request.scope.settlement_currency
    )
    need(
        gap is None and equity is not None and available is not None,
        "native_reconcile_liability_or_balance_unknown",
    )
    anchor = risk[0]
    need(
        type(anchor.get("balData")) is list
        and len(anchor["balData"]) == 1
        and anchor["balData"][0].get("ccy") == request.scope.settlement_currency
        and type(anchor.get("posData")) is list,
        "native_reconcile_account_risk_scope_unknown",
    )
    number(anchor["balData"][0].get("eq"), positive=True)
    anchor_at = int(anchor["ts"])
    need(
        int(balance[0]["uTime"]) <= anchor_at
        and all(int(item["uTime"]) <= anchor_at for item in balance[0]["details"]),
        "native_reconcile_account_snapshot_cutoff_conflict",
    )
    positions = one(observations, "positions", "/api/v5/account/positions", {})
    need(
        all(int(item["uTime"]) <= anchor_at for item in positions),
        "native_reconcile_account_snapshot_cutoff_conflict",
    )
    # Zero-size retained rows are still checked and included in the receipt.
    active = [item for item in positions if number(item.get("pos")) != 0]
    anchors = [item for item in anchor["posData"] if number(item.get("pos")) != 0]
    need(len(active) == len(anchors), "native_reconcile_position_anchor_conflict")
    for item in active:
        matches = [part for part in anchors if part.get("posId") == item.get("posId")]
        need(
            len(matches) == 1
            and all(
                matches[0].get(key) == item.get(key)
                for key in ("instId", "instType", "mgnMode", "posSide")
            )
            and number(matches[0].get("pos")) == number(item.get("pos")),
            "native_reconcile_position_anchor_conflict",
        )
    return active


def assess_native_reconciliation_v1(target, observations):
    need(
        type(target) is NativeReconciliationTargetV1
        and type(observations) is tuple
        and 1 <= len(observations) <= read.MAX_REQUESTS
        and all(
            type(item) is tuple
            and len(item) == 2
            and type(item[0]) is str
            and type(item[1]) is read.DemoReconciliationObservationV1
            for item in observations
        ),
        "native_reconcile_exact_data_required",
    )
    request = wire.checked_request(target.request)
    need(
        wire.canonical_request(request) == target.request_json
        and target.original_credential_session_sha256
        == request.credential_session_sha256,
        "native_reconcile_original_target_changed",
    )
    from app.trade_qualification import control_bound_ledger as bound

    outer, consumed = bound.replay_control_bound_intent(
        target.intent_json, request, expected_sha256=target.intent_sha256
    )
    need(
        consumed.reservation_id == target.reservation_id,
        "native_reconcile_reservation_mismatch",
    )
    inner = decode(
        decode(outer.canonical_json.encode())["intent_json"].encode(),
        wire.MAX_NATIVE_BYTES,
    )
    expected = inner["exact_order"]
    roles = {
        "config_before",
        "config_after",
        "order_before",
        "order_after",
        "balance",
        "account_risk",
        "positions",
        "pending_orders",
        "leverage",
        "fills_recent",
        "fills_history",
        *("algo_" + item for item in read.ALGO_TYPES),
    }
    need(
        {name for name, _ in observations} == roles,
        "native_reconcile_source_roles_missing",
    )
    need(
        tuple(name for name, _ in observations[:2]) == ("config_before", "order_before")
        and tuple(name for name, _ in observations[-2:])
        == ("order_after", "config_after"),
        "native_reconcile_required_capture_order_invalid",
    )
    prior = None
    for _, item in observations:
        url = urlsplit(item.request_url)
        need(
            url.scheme + "://" + url.netloc == target.origin
            and url.path == item.path
            and not url.fragment
            and tuple(sorted(parse_qsl(url.query, keep_blank_values=True)))
            == item.parameters
            and item.tls_hostname == url.hostname
            and item.started_at <= item.headers_at <= item.eof_at
            and item.started_monotonic_ns
            <= item.headers_monotonic_ns
            <= item.eof_monotonic_ns
            and (
                prior is None
                or (
                    prior.eof_at < item.started_at
                    and prior.eof_monotonic_ns < item.started_monotonic_ns
                )
            ),
            "native_reconcile_fresh_chronology_invalid",
        )
        prior = item
    _source_clocks(observations)
    before = one(observations, "config_before", "/api/v5/account/config", {})
    after = one(observations, "config_after", "/api/v5/account/config", {})
    need(
        len(before) == len(after) == 1
        and before[0] == after[0]
        and before[0].get("uid") == request.scope.account_id
        and before[0].get("mainUid") == target.main_uid
        and before[0].get("acctLv") == "2"
        and before[0].get("posMode") == inner["execution_binding"]["position_mode"],
        "native_reconcile_account_identity_or_mode_changed",
    )
    query = {"instId": expected["instId"], "clOrdId": expected["clOrdId"]}
    first = one(observations, "order_before", "/api/v5/trade/order", query)
    last = one(observations, "order_after", "/api/v5/trade/order", query)
    need(len(first) == len(last) == 1, "native_reconcile_exact_order_missing")
    accumulated, created, updated = _order(first[0], expected)
    final_accumulated, final_created, final_updated = _order(last[0], expected)
    need(
        accumulated == final_accumulated
        and created == final_created
        and int(updated) <= int(final_updated)
        and all(
            first[0].get(key) == last[0].get(key)
            for key in ("ordId", "state", "avgPx", "lever")
        ),
        "native_reconcile_terminal_order_conflict",
    )
    size = number(expected["sz"], positive=True)
    need(
        (first[0]["state"] == "filled" and accumulated == size)
        or (first[0]["state"] == "canceled" and accumulated == 0),
        "native_reconcile_fok_partial_or_ambiguous",
    )
    fill_query = {
        "instType": "SWAP",
        "instId": expected["instId"],
        "ordId": first[0]["ordId"],
    }
    fills = {}
    for role, endpoint in (
        ("fills_recent", "/api/v5/trade/fills"),
        ("fills_history", "/api/v5/trade/fills-history"),
    ):
        for item in chain(observations, role, endpoint, fill_query, "billId"):
            identity = item["billId"]
            need(
                identity not in fills or fills[identity] == item,
                "native_reconcile_fill_overlap_conflict",
            )
            fills[identity] = item
    total, weighted, paid_fees = Fraction(0), Fraction(0), Fraction(0)
    trade_ids = set()
    for item in fills.values():
        need(
            all(
                item.get(key) == first[0].get(key)
                for key in ("ordId", "instId", "instType", "side", "posSide")
            )
            and type(item.get("tradeId")) is str
            and _ID.fullmatch(item["tradeId"]) is not None,
            "native_reconcile_fill_identity_mismatch",
        )
        need(
            item["tradeId"] not in trade_ids
            and item.get("feeCcy") == request.scope.settlement_currency,
            "native_reconcile_fill_fee_or_trade_identity_unknown",
        )
        trade_ids.add(item["tradeId"])
        paid_fees += max(Fraction(0), -number(item.get("fee")))
        quantity, price = (
            number(item.get("fillSz"), positive=True),
            number(item.get("fillPx"), positive=True),
        )
        limit = number(expected["px"], positive=True)
        need(
            (expected["side"] == "buy" and price <= limit)
            or (expected["side"] == "sell" and price >= limit),
            "native_reconcile_fill_outside_fixed_limit",
        )
        fill_time = item.get("fillTime")
        need(
            type(fill_time) is str
            and fill_time.isascii()
            and fill_time.isdigit()
            and 1 <= len(fill_time) <= 16
            and int(created) <= int(fill_time) <= int(final_updated),
            "native_reconcile_fill_clock_invalid",
        )
        total += quantity
        weighted += quantity * price
    need(total == accumulated, "native_reconcile_fill_quantity_mismatch")
    pending = chain(
        observations, "pending_orders", "/api/v5/trade/orders-pending", {}, "ordId"
    )
    need(not pending, "native_reconcile_unexpected_pending_order")
    algos, algo_ids = [], set()
    for kind in read.ALGO_TYPES:
        current = chain(
            observations,
            "algo_" + kind,
            "/api/v5/trade/orders-algo-pending",
            {"ordType": kind},
            "algoId",
        )
        for item in current:
            need(
                item.get("ordType") == kind and item["algoId"] not in algo_ids,
                "native_reconcile_algo_scope_or_identity_conflict",
            )
            algo_ids.add(item["algoId"])
        algos.extend(current)
    positions = _balance_and_risk(observations, request)
    leverage = one(
        observations,
        "leverage",
        "/api/v5/account/leverage-info",
        {"instId": expected["instId"], "mgnMode": "isolated"},
    )
    selected = [
        item
        for item in leverage
        if item.get("instId") == expected["instId"]
        and item.get("mgnMode") == "isolated"
        and item.get("posSide") == expected["posSide"]
    ]
    need(
        len(selected) == 1
        and number(selected[0].get("lever"), positive=True)
        == request.risk_inputs.requested_leverage,
        "native_reconcile_actual_leverage_unknown_or_changed",
    )
    details = {}
    if accumulated == 0:
        average_raw = first[0].get("avgPx")
        need(
            not fills
            and (average_raw == "" or number(average_raw) == 0)
            and not positions
            and not algos,
            "native_reconcile_zero_fill_not_flat",
        )
        classification = "terminal_zero_fill_flat"
    else:
        average = weighted / total
        need(
            number(first[0].get("avgPx"), positive=True) == average,
            "native_reconcile_unresolved_average_precision",
        )
        need(
            number(first[0].get("lever"), positive=True)
            == request.risk_inputs.requested_leverage,
            "native_reconcile_order_leverage_changed",
        )
        need(len(positions) == 1, "native_reconcile_filled_position_missing_or_extra")
        position = positions[0]
        need(
            number(position.get("avgPx"), positive=True) == average,
            "native_reconcile_unresolved_average_precision",
        )
        expected_position = (
            -size
            if expected["posSide"] == "net" and expected["side"] == "sell"
            else size
        )
        need(
            all(
                position.get(key) == value
                for key, value in {
                    "instType": "SWAP",
                    "instId": expected["instId"],
                    "mgnMode": "isolated",
                    "posSide": expected["posSide"],
                    "ccy": request.scope.settlement_currency,
                }.items()
            )
            and number(position.get("pos")) == expected_position
            and number(position.get("lever"), positive=True)
            == request.risk_inputs.requested_leverage
            and 0
            < number(position.get("margin"))
            <= Fraction(request.coverage.margin_amount),
            "native_reconcile_position_binding_mismatch",
        )
        need(
            int(position["uTime"])
            >= max(int(item["fillTime"]) for item in fills.values()),
            "native_reconcile_position_update_precedes_trade",
        )
        protection = expected["attachAlgoOrds"][0]
        need(len(algos) == 1, "native_reconcile_protection_missing_duplicate_or_extra")
        algo = algos[0]
        required = {
            "instType": "SWAP",
            "instId": expected["instId"],
            "tdMode": "isolated",
            "posSide": expected["posSide"],
            "side": "sell" if expected["side"] == "buy" else "buy",
            "ordType": "oco",
            "state": "live",
            "algoClOrdId": protection["attachAlgoClOrdId"],
            "slOrdPx": "-1",
            "tpOrdPx": "-1",
            "slTriggerPxType": "mark",
            "tpTriggerPxType": "mark",
        }
        need(
            all(algo.get(key) == value for key, value in required.items())
            and number(algo.get("sz"), positive=True) == size
            and number(algo.get("slTriggerPx"), positive=True)
            == number(protection["slTriggerPx"], positive=True)
            and number(algo.get("tpTriggerPx"), positive=True)
            == number(protection["tpTriggerPx"], positive=True)
            and (expected["posSide"] != "net" or algo.get("reduceOnly") == "true"),
            "native_reconcile_protection_geometry_or_identity_mismatch",
        )
        sample = request.coverage.execution
        stop, target_price, cost = (
            Fraction(sample.stop_loss),
            number(protection["tpTriggerPx"], positive=True),
            Fraction(sample.cost_per_base),
        )
        need(
            (stop < average < target_price)
            if expected["side"] == "buy"
            else (target_price < average < stop),
            "native_reconcile_fill_outside_fixed_bracket",
        )
        actual_rr = (abs(target_price - average) - cost) / (abs(average - stop) + cost)
        captured_rr = (abs(target_price - Fraction(sample.entry)) - cost) / (
            abs(Fraction(sample.entry) - stop) + cost
        )
        need(
            actual_rr >= captured_rr > 0,
            "native_reconcile_original_rr_policy_not_preserved",
        )
        base_quantity = size * Fraction(sample.contract_value)
        need(
            paid_fees <= base_quantity * cost,
            "native_reconcile_actual_entry_fee_exceeds_captured_cost",
        )
        actual = {
            "risk_amount": base_quantity * (abs(average - stop) + cost),
            "margin_amount": base_quantity * average / sample.leverage,
            "notional_amount": base_quantity * average,
        }
        need(
            all(
                value <= Fraction(getattr(request.coverage, name))
                for name, value in actual.items()
            ),
            "native_reconcile_fill_exceeds_reserved_coverage",
        )
        classification = "filled_and_protected_hold_retained"
        details = {
            "actual_entry": ratio(average),
            "actual_net_rr": ratio(actual_rr),
            "captured_execution_net_rr": ratio(captured_rr),
            "fixed_cost_per_base": str(sample.cost_per_base),
            "actual_entry_fees": ratio(paid_fees),
            "actual_isolated_margin": ratio(number(position["margin"])),
            "rr_rule": "original_policy_preserved_by_exact_monotone_dominance",
            "actual_coverage": {name: ratio(value) for name, value in actual.items()},
        }
    receipt = canonical(
        {
            "version": "ctcc.native_postsubmit_reconciliation_computation.v1",
            "reservation_id": target.reservation_id,
            "request_sha256": sha(target.request_json.encode()),
            "intent_sha256": target.intent_sha256,
            "durable_lineage_sha256": target.durable_lineage_sha256,
            "classification": classification,
            "order_id": first[0]["ordId"],
            "client_order_id": expected["clOrdId"],
            "protection_client_order_id": inner["protection_client_order_id"],
            "accumulated_fill": ratio(accumulated),
            "requested_size": ratio(size),
            "fill_count": len(fills),
            "read_pages": [
                {
                    "role": role,
                    "raw_sha256": sha(item.raw_body),
                    "observation_sha256": sha(
                        canonical(
                            {
                                "path": item.path,
                                "query": list(item.parameters),
                                "request_url": item.request_url,
                                "signature_sha256": item.request_signature_sha256,
                                "read_session_id": item.read_session_id,
                                "started_at": item.started_at.isoformat(),
                                "eof_at": item.eof_at.isoformat(),
                                "started_monotonic_ns": item.started_monotonic_ns,
                                "eof_monotonic_ns": item.eof_monotonic_ns,
                                "certificate_sha256": item.certificate_sha256,
                                "raw_sha256": sha(item.raw_body),
                            }
                        )
                    ),
                }
                for role, item in observations
            ],
            "details": details,
            "execution_authority": False,
            "order_retry_authority": False,
        }
    )
    return NativeReconciliationAssessmentV1(
        receipt, classification, accumulated == 0, accumulated > 0
    )


def assess_previously_closed_zero_v1(
    target, observations, *, closure_outcome_id, historical_assessment
):
    """Fresh exact old-order DATA; never assert the whole account is still flat.

    The native repository must first independently replay the immutable SQL
    closure and its original full-account observations. Later unrelated orders,
    account modes or inventory cannot resurrect this spent event or be released
    by checking the old order again. No historical credential/session is used.
    """
    need(
        type(target) is NativeReconciliationTargetV1
        and type(observations) is tuple
        and 6 <= len(observations) <= read.MAX_REQUESTS
        and all(
            type(item) is tuple
            and len(item) == 2
            and type(item[0]) is str
            and type(item[1]) is read.DemoReconciliationObservationV1
            for item in observations
        )
        and type(historical_assessment) is NativeReconciliationAssessmentV1
        and historical_assessment.classification == "terminal_zero_fill_flat",
        "native_reconcile_closed_data_required",
    )
    request = wire.checked_request(target.request)
    need(
        wire.canonical_request(request) == target.request_json,
        "native_reconcile_original_target_changed",
    )
    inner = decode(
        decode(target.intent_json.encode())["intent_json"].encode(),
        wire.MAX_NATIVE_BYTES,
    )
    expected = inner["exact_order"]
    roles = {
        "config_before",
        "order_before",
        "fills_recent",
        "fills_history",
        "order_after",
        "config_after",
    }
    need(
        {name for name, _ in observations} == roles
        and tuple(name for name, _ in observations[:2])
        == ("config_before", "order_before")
        and tuple(name for name, _ in observations[-2:])
        == ("order_after", "config_after"),
        "native_reconcile_required_capture_order_invalid",
    )
    previous = None
    for _, page in observations:
        url = urlsplit(page.request_url)
        need(
            url.scheme + "://" + url.netloc == target.origin
            and url.path == page.path
            and not url.fragment
            and tuple(sorted(parse_qsl(url.query, keep_blank_values=True)))
            == page.parameters
            and page.tls_hostname == url.hostname
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
            ),
            "native_reconcile_fresh_chronology_invalid",
        )
        previous = page
    _source_clocks(observations)
    before = one(observations, "config_before", "/api/v5/account/config", {})
    after = one(observations, "config_after", "/api/v5/account/config", {})
    need(
        len(before) == len(after) == 1
        and before == after
        and before[0].get("uid") == request.scope.account_id
        and before[0].get("mainUid") == target.main_uid,
        "native_reconcile_account_identity_or_mode_changed",
    )
    query = {"instId": expected["instId"], "clOrdId": expected["clOrdId"]}
    first = one(observations, "order_before", "/api/v5/trade/order", query)
    last = one(observations, "order_after", "/api/v5/trade/order", query)
    need(len(first) == len(last) == 1, "native_reconcile_exact_order_missing")
    initial, created, updated = _order(first[0], expected)
    final, final_created, final_updated = _order(last[0], expected)
    need(
        initial == final == 0
        and created == final_created
        and int(updated) <= int(final_updated)
        and first[0]["state"] == last[0]["state"] == "canceled"
        and first[0]["ordId"] == last[0]["ordId"]
        and all(
            item.get("avgPx") == "" or number(item.get("avgPx")) == 0
            for item in (first[0], last[0])
        ),
        "native_reconcile_zero_fill_not_flat",
    )
    fill_query = {
        "instType": "SWAP",
        "instId": expected["instId"],
        "ordId": first[0]["ordId"],
    }
    need(
        not chain(
            observations, "fills_recent", "/api/v5/trade/fills", fill_query, "billId"
        )
        and not chain(
            observations,
            "fills_history",
            "/api/v5/trade/fills-history",
            fill_query,
            "billId",
        ),
        "native_reconcile_zero_fill_not_flat",
    )
    return canonical(
        {
            "version": "ctcc.native_zero_fok_restart_verification.v1",
            "reservation_id": target.reservation_id,
            "closure_outcome_id": closure_outcome_id,
            "historical_assessment_sha256": sha(historical_assessment.receipt_json),
            "client_order_id": expected["clOrdId"],
            "order_id": first[0]["ordId"],
            "fresh_pages": [
                {
                    "role": role,
                    "raw_sha256": sha(page.raw_body),
                    "read_session_id": page.read_session_id,
                    "actual_eof_at": page.eof_at.isoformat(),
                }
                for role, page in observations
            ],
            "current_whole_account_flat_asserted": False,
            "execution_authority": False,
            "order_retry_authority": False,
        }
    )
