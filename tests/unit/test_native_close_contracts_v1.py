"""Synthetic exact close DATA only; no Source/close owner, SDK or DB permit."""

import copy
from datetime import timedelta
from urllib.parse import urlencode

import pytest

from app.domain.source_primitives import canonical, decode
from app.exchange.okx import native_reconciliation_observation_v1 as read
from app.trade_qualification import native_close_contracts_v1 as close
from app.trade_qualification import native_reconciliation_facts_v1 as facts
from tests.unit.test_native_ledger_contracts_v2 import (
    synthetic as synthetic,  # noqa: PLC0414 -- Pytest collects this imported fixture.
)
from tests.unit.test_native_reconciliation_facts_v1 import data
from tests.unit.test_native_reconciliation_facts_v1 import (
    target as target,  # noqa: PLC0414 -- Pytest collects this imported fixture.
)


def fresh_preflight(target):
    original = data(target, filled=True)
    at = original[-1][1].eof_at + timedelta(seconds=1)
    mono = original[-1][1].eof_monotonic_ns + 1_000_000_000
    # Predeclare new dispatch clocks and query window before constructing the
    # synthetic NEW pages. Source event/generation bytes remain the old facts.
    sequence = [
        (role, page.path, dict(page.parameters), facts.rows(page))
        for role, page in original[:-2]
    ]
    order = facts.rows(original[1][1])[0]
    end = str(close._milliseconds(at + timedelta(milliseconds=10 * len(sequence))))
    window = {
        "instType": "SWAP",
        "instId": order["instId"],
        "begin": str(int(order["cTime"]) - 1),
        "end": end,
        "limit": "100",
    }
    fill = next(
        facts.rows(page)[0]
        for role, page in original
        if role == "fills_history" and facts.rows(page)
    )
    for role, path in (
        ("close_fills_recent_all", "/api/v5/trade/fills"),
        ("close_fills_history_all", "/api/v5/trade/fills-history"),
    ):
        sequence.append((role, path, window, [fill]))
        sequence.append((role, path, {**window, "after": fill["billId"]}, []))
    sequence.extend(
        (role, page.path, dict(page.parameters), facts.rows(page))
        for role, page in original[-2:]
    )
    result = []
    for index, (role, path, query, rows) in enumerate(sequence):
        start = at + timedelta(milliseconds=10 * index)
        base = mono + 10_000_000 * index
        result.append(
            (
                role,
                read.DemoReconciliationObservationV1(
                    path,
                    read.checked_query(path, query),
                    target.origin + path + ("?" + urlencode(query) if query else ""),
                    "f" * 64,
                    "c" * 64,
                    start,
                    base,
                    start + timedelta(milliseconds=1),
                    base + 1_000_000,
                    start + timedelta(milliseconds=2),
                    base + 2_000_000,
                    200,
                    (),
                    canonical({"code": "0", "data": copy.deepcopy(rows)}),
                    "b" * 64,
                    "www.okx.com",
                ),
            )
        )
    return original, tuple(result)


def changed_extra(observations, operation):
    from dataclasses import replace

    result = []
    for role, page in observations:
        if role in close._EXTRA and facts.rows(page):
            raw = decode(page.raw_body)
            operation(raw["data"][0])
            page = replace(page, raw_body=canonical(raw))
        result.append((role, page))
    return tuple(result)


def test_real_close_side_mode_and_size_are_bound_by_raw_preflight(target):
    original, fresh = fresh_preflight(target)
    result = close.assess_native_close_preflight_v1(target, original, fresh)
    order = decode(result.exact_order_json)
    entry = facts.rows(original[1][1])[0]
    assert order["side"] == ("sell" if entry["side"] == "buy" else "buy")
    assert order["posSide"] == entry["posSide"] and order["sz"] == entry["sz"]
    assert (
        order["ordType"] == "market"
        and "px" not in order
        and "attachAlgoOrds" not in order
    )
    assert (
        order["clOrdId"] == "CTC" + target.reservation_id[:29]
        and len(order["clOrdId"]) == 32
    )
    if entry["posSide"] == "net":
        assert order["reduceOnly"] is True
    else:
        assert "reduceOnly" not in order
        assert (order["side"], order["posSide"]) in {("sell", "long"), ("buy", "short")}
    assert result.execution_authority is False and result.order_retry_authority is False


def test_foreign_fill_in_captured_generation_window_is_not_our_exposure(target):
    original, fresh = fresh_preflight(target)
    altered = changed_extra(fresh, lambda row: row.update(ordId="20002"))
    with pytest.raises(facts.NativeReconciliationFactsError):
        close.assess_native_close_preflight_v1(target, original, altered)


def test_missing_terminal_full_instrument_page_is_incomplete(target):
    original, fresh = fresh_preflight(target)
    altered = tuple(
        (role, page)
        for role, page in fresh
        if not (role == "close_fills_history_all" and not facts.rows(page))
    )
    with pytest.raises(facts.NativeReconciliationFactsError):
        close.assess_native_close_preflight_v1(target, original, altered)


def test_record_generation_is_not_replaced_by_execution_time(target):
    original, fresh = fresh_preflight(target)
    end = dict(next(page.parameters for role, page in fresh if role in close._EXTRA))[
        "end"
    ]
    altered = changed_extra(fresh, lambda row: row.update(ts=str(int(end) + 1)))
    with pytest.raises(facts.NativeReconciliationFactsError):
        close.assess_native_close_preflight_v1(target, original, altered)


def test_unbounded_or_order_mixed_new_history_query_is_refused():
    base = {
        "instType": "SWAP",
        "instId": "BTC-USDT-SWAP",
        "begin": "1793577600000",
        "end": "1793577660000",
        "limit": "100",
    }
    for altered in (
        {key: value for key, value in base.items() if key != "end"},
        {**base, "ordId": "10001"},
        {**base, "end": "0"},
        {**base, "begin": "NaN"},
    ):
        with pytest.raises(ValueError):
            read.checked_query("/api/v5/trade/fills-history", altered)
