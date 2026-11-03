"""Synthetic complete terminal DATA only; no native/SQL/close permissions."""

import copy
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from urllib.parse import urlencode

import pytest

from app.domain.source_primitives import canonical, decode
from app.exchange.okx import native_reconciliation_observation_v1 as read
from app.trade_qualification import native_close_contracts_v1 as preflight
from app.trade_qualification import native_close_final_facts_v1 as final
from app.trade_qualification import native_reconciliation_facts_v1 as facts
from tests.unit.test_native_close_contracts_v1 import fresh_preflight
from tests.unit.test_native_ledger_contracts_v2 import (
    synthetic as synthetic,  # noqa: PLC0414 -- Pytest collects this imported fixture.
)
from tests.unit.test_native_reconciliation_facts_v1 import (
    target as target,  # noqa: PLC0414 -- Pytest collects this imported fixture.
)


def terminal(target):
    original, prior = fresh_preflight(target)
    plan = preflight.assess_native_close_preflight_v1(target, original, prior)
    decode(plan.receipt_json)
    expected_close = decode(plan.exact_order_json)
    entry = copy.deepcopy(facts.rows(original[1][1])[0])
    entry_fill = copy.deepcopy(
        next(
            facts.rows(page)[0]
            for role, page in original
            if role == "fills_history" and facts.rows(page)
        )
    )
    # Declare genuine synthetic source events BEFORE constructing any NEW
    # captured pages. Original entry/protection/fill bytes are never restamped.
    closing_at = prior[-1][1].eof_at + timedelta(milliseconds=10)
    created = preflight._milliseconds(closing_at)
    traded = created + 1
    captured_at = closing_at + timedelta(seconds=1)
    mono = prior[-1][1].eof_monotonic_ns + 2_000_000_000
    closed = {
        **expected_close,
        "instType": "SWAP",
        "ordId": "10002",
        "state": "filled",
        "reduceOnly": "true" if expected_close["posSide"] == "net" else "false",
        "px": "",
        "accFillSz": expected_close["sz"],
        "avgPx": entry["avgPx"],
        "lever": entry["lever"],
        "cTime": str(created),
        "uTime": str(traded),
    }
    close_fill = {
        **entry_fill,
        "billId": "9002",
        "tradeId": "7002",
        "ordId": "10002",
        "side": expected_close["side"],
        "fillTime": str(traded),
        "ts": str(traded),
    }
    config = copy.deepcopy(facts.rows(original[0][1])[0])
    balance = copy.deepcopy(
        next(facts.rows(page)[0] for role, page in original if role == "balance")
    )
    balance["uTime"] = str(traded + 1)
    for row in balance["details"]:
        row["uTime"] = str(traded + 1)
    risk = {
        "ts": str(traded + 2),
        "balData": [{"ccy": "USDT", "eq": "10000"}],
        "posData": [],
    }
    protection = copy.deepcopy(
        next(
            facts.rows(page)[0]
            for role, page in original
            if role == "algo_oco" and facts.rows(page)
        )
    )
    protection.update(
        state="canceled", uTime=str(traded + 1), failCode="", lever=entry["lever"]
    )
    inst = entry["instId"]
    entry_query = {"instId": inst, "clOrdId": entry["clOrdId"]}
    close_query = {"instId": inst, "clOrdId": expected_close["clOrdId"]}
    sequence = [
        ("config_before", "/api/v5/account/config", {}, [config]),
        ("entry_order_before", "/api/v5/trade/order", entry_query, [entry]),
        ("close_order_before", "/api/v5/trade/order", close_query, [closed]),
    ]

    def chain(role, path, query, rows, cursor):
        query = {**query, "limit": "100"}
        if rows:
            sequence.append((role, path, query, rows))
            query = {**query, "after": rows[-1][cursor]}
        sequence.append((role, path, query, []))

    for prefix, order, fill in (
        ("entry", entry, entry_fill),
        ("close", closed, close_fill),
    ):
        query = {"instType": "SWAP", "instId": inst, "ordId": order["ordId"]}
        for suffix, path in (
            ("recent", "/api/v5/trade/fills"),
            ("history", "/api/v5/trade/fills-history"),
        ):
            chain(prefix + "_fills_" + suffix, path, query, [fill], "billId")
    sequence.extend(
        (
            ("balance", "/api/v5/account/balance", {}, [balance]),
            ("positions", "/api/v5/account/positions", {}, []),
            ("account_risk", "/api/v5/account/account-position-risk", {}, [risk]),
        )
    )
    chain("pending_orders", "/api/v5/trade/orders-pending", {}, [], "ordId")
    for kind in read.ALGO_TYPES:
        chain(
            "algo_" + kind,
            "/api/v5/trade/orders-algo-pending",
            {"ordType": kind},
            [],
            "algoId",
        )
    sequence.extend(
        (
            (
                "original_protection_detail",
                "/api/v5/trade/order-algo",
                {"algoId": "8001"},
                [protection],
            ),
            (
                "leverage",
                "/api/v5/account/leverage-info",
                {"instId": inst, "mgnMode": "isolated"},
                [
                    {
                        "instId": inst,
                        "mgnMode": "isolated",
                        "posSide": entry["posSide"],
                        "lever": entry["lever"],
                    }
                ],
            ),
        )
    )
    # ts query endpoint bounds are declared before dispatch; all returned
    # boundary rows remain, and fillTime retains its execution semantics.
    window = {
        "begin": str(int(entry["cTime"]) - 1),
        "end": str(
            preflight._milliseconds(
                captured_at + timedelta(milliseconds=10 * len(sequence))
            )
        ),
    }
    for role, path in (
        ("final_fills_recent_all", "/api/v5/trade/fills"),
        ("final_fills_history_all", "/api/v5/trade/fills-history"),
    ):
        chain(
            role,
            path,
            {"instType": "SWAP", "instId": inst, **window},
            [close_fill, entry_fill],
            "billId",
        )
    chain("bills", "/api/v5/account/bills-archive", window, [], "billId")
    sequence.extend(
        (
            ("entry_order_after", "/api/v5/trade/order", entry_query, [entry]),
            ("close_order_after", "/api/v5/trade/order", close_query, [closed]),
            ("config_after", "/api/v5/account/config", {}, [config]),
        )
    )
    observations = []
    for index, (role, path, query, rows) in enumerate(sequence):
        started = captured_at + timedelta(milliseconds=index * 10)
        base = mono + index * 10_000_000
        observations.append(
            (
                role,
                read.DemoReconciliationObservationV1(
                    path,
                    read.checked_query(path, query),
                    target.origin + path + ("?" + urlencode(query) if query else ""),
                    "f" * 64,
                    "d" * 64,
                    started,
                    base,
                    started + timedelta(milliseconds=1),
                    base + 1_000_000,
                    started + timedelta(milliseconds=2),
                    base + 2_000_000,
                    200,
                    (),
                    canonical({"code": "0", "data": copy.deepcopy(rows)}),
                    "b" * 64,
                    "www.okx.com",
                ),
            )
        )
    document = canonical(
        {
            "version": "ctcc.native_controlled_close_intent.v1",
            "reservation_id": target.reservation_id,
            "close_request_sha256": plan.exact_order_sha256,
            "exact_close_order_json": plan.exact_order_json.decode(),
            "preflight_sha256": facts.sha(plan.receipt_json),
        }
    )
    return original, prior, document, tuple(observations)


def change(observations, role, operation):
    result = []
    for current, page in observations:
        if current == role:
            body = decode(page.raw_body)
            operation(body["data"], page)
            page = replace(page, raw_body=canonical(body))
        result.append((current, page))
    return tuple(result)


def assess(target, prepared, observations=None):
    original, prior, document, captured = prepared
    return final.assess_native_closed_trade_v1(
        target,
        original,
        prior,
        document,
        captured if observations is None else observations,
    )


def test_complete_exact_entry_close_and_flat_are_only_data(target):
    result = assess(target, terminal(target))
    receipt = decode(result.receipt_json)
    assert result.classification == "own_entry_and_close_filled_flat"
    assert receipt["entry_order_id"] == "10001" and receipt["close_order_id"] == "10002"
    assert receipt["protection_algo_id"] == "8001"
    assert (
        not result.release_authority
        and not result.execution_authority
        and not result.order_retry_authority
    )


@pytest.mark.parametrize(
    "role,field,value",
    (
        ("close_order_before", "state", "live"),
        ("close_order_before", "accFillSz", "0"),
        ("close_order_after", "clOrdId", "foreign"),
        ("close_order_after", "side", "wrong"),
        ("close_order_after", "posSide", "unknown"),
        ("close_order_after", "avgPx", "0"),
        ("original_protection_detail", "state", "live"),
        ("original_protection_detail", "algoClOrdId", "foreign"),
        ("original_protection_detail", "sz", "0"),
        ("original_protection_detail", "slTriggerPx", "0"),
    ),
)
def test_partial_foreign_or_still_live_protection_is_unknown(
    target, role, field, value
):
    prepared = terminal(target)
    altered = change(
        prepared[3], role, lambda rows, page: rows[0].update({field: value})
    )
    with pytest.raises(facts.NativeReconciliationFactsError):
        assess(target, prepared, altered)


@pytest.mark.parametrize(
    "role,field",
    (
        ("close_order_before", "uTime"),
        ("close_fills_history", "fillTime"),
        ("close_fills_history", "ts"),
        ("balance", "uTime"),
        ("account_risk", "ts"),
        ("original_protection_detail", "uTime"),
    ),
)
def test_future_source_event_is_not_changed_to_receipt_time(target, role, field):
    prepared = terminal(target)
    future = str(
        preflight._milliseconds(prepared[3][-1][1].eof_at + timedelta(seconds=1))
    )
    if role == "close_fills_history":
        altered = prepared[3]
        # Keep overlapping recent/history/full-instrument rows identical so
        # the actual source-clock condition, not an overlap conflict, denies.
        for current in (*final._FILL_ROLES, *final._ALL_FILL_ROLES):
            altered = change(
                altered,
                current,
                lambda rows, page: [
                    row.update({field: future})
                    for row in rows
                    if row.get("ordId") == "10002"
                ],
            )
    else:
        altered = change(
            prepared[3],
            role,
            lambda rows, page: rows and rows[0].update({field: future}),
        )
    with pytest.raises(
        facts.NativeReconciliationFactsError, match="future_source_clock"
    ):
        assess(target, prepared, altered)


def test_old_empty_snapshot_cannot_prove_close_flat(target):
    prepared = terminal(target)
    old_time = facts.rows(prepared[0][1][1])[0]["uTime"]
    altered = change(
        prepared[3], "account_risk", lambda rows, page: rows[0].update(ts=old_time)
    )
    with pytest.raises(facts.NativeReconciliationFactsError):
        assess(target, prepared, altered)


def test_full_instrument_terminal_chain_is_mandatory(target):
    prepared = terminal(target)
    altered = tuple(
        (role, page)
        for role, page in prepared[3]
        if not (role == "final_fills_history_all" and not facts.rows(page))
    )
    with pytest.raises(facts.NativeReconciliationFactsError):
        assess(target, prepared, altered)


def test_consistent_close_fill_average_mismatch_has_no_rounding_tolerance(target):
    prepared = terminal(target)
    altered = prepared[3]
    order = next(
        facts.rows(page)[0] for role, page in altered if role == "close_order_before"
    )
    different = str(Decimal(order["avgPx"]) + 1)
    for role in (*final._FILL_ROLES, *final._ALL_FILL_ROLES):
        altered = change(
            altered,
            role,
            lambda rows, page: [
                row.update(fillPx=different)
                for row in rows
                if row.get("ordId") == "10002"
            ],
        )
    with pytest.raises(
        facts.NativeReconciliationFactsError, match="unresolved_average_precision"
    ):
        assess(target, prepared, altered)


def test_protection_detail_route_has_no_write_or_filter_override():
    assert read.checked_query("/api/v5/trade/order-algo", {"algoId": "8001"}) == (
        ("algoId", "8001"),
    )
    for query in (
        {},
        {"algoClOrdId": "CTA"},
        {"algoId": "8001", "instId": "BTC-USDT-SWAP"},
    ):
        with pytest.raises(ValueError):
            read.checked_query("/api/v5/trade/order-algo", query)
