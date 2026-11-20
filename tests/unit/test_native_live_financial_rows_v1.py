"""Declared synthetic row arithmetic. No exchange/account/Arm acceptance claim."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from fractions import Fraction
from types import SimpleNamespace

import pytest

from app.domain.source_primitives import ClockStamp, canonical, decode, sha
from app.okx_live import financial_rows_v1 as subject
from app.okx_live import trusted_source_bridge_v1 as bridge

NOW = datetime(2026, 11, 20, 0, 10, tzinfo=UTC)


def _ms(seconds):
    return str(bridge._utc_ns(NOW + timedelta(seconds=seconds)) // 1_000_000)


def _view(*, mutation=None):
    plan = bridge.LiveSourcePlanV1(
        "a" * 32,
        "global",
        NOW - timedelta(days=8),
        NOW - timedelta(seconds=60),
        ClockStamp(utc_ns=bridge._utc_ns(NOW - timedelta(seconds=600)), monotonic_ns=1),
    )
    tail = {
        "environment": "live",
        "partition_count": 7,
        "begin_ms": _ms(-60),
        "end_ms": _ms(-3),
    }
    selectors = (
        *bridge._selectors(plan),
        *(
            bridge.select_live_account_history_page_v1(
                "fills_recent",
                begin_ms=tail["begin_ms"],
                end_ms=tail["end_ms"],
                inst_type=product,
            )
            for product in bridge.HISTORY_INST_TYPES
        ),
        bridge.select_live_account_history_page_v1(
            "bills_recent", begin_ms=tail["begin_ms"], end_ms=tail["end_ms"]
        ),
    )
    fills = [
        {
            "billId": str(101 + index),
            "ordId": str(201 + index),
            "tradeId": str(301 + index),
            "instType": "SWAP",
            "instId": "ETH-USDT-SWAP",
            "posSide": "net",
            "side": "buy" if index == 0 else "sell",
            "subType": "3" if index == 0 else "5",
            "fillTime": _ms(-120 if index == 0 else -100),
            "ts": _ms(-119 if index == 0 else -99),
            "fillPnl": "0" if index == 0 else "1",
            "fee": "-0.01",
            "feeCcy": "USDT",
            "fillSz": "1",
            "fillPx": "100" if index == 0 else "110",
        }
        for index in range(2)
    ]
    bills = [
        {**row, "type": "2", "ccy": "USDT", "pnl": row["fillPnl"], "sz": row["fillSz"]}
        for row in fills
    ]
    bills.append(
        {
            "billId": "103",
            "type": "8",
            "subType": "173",
            "instType": "SWAP",
            "instId": "ETH-USDT-SWAP",
            "ccy": "USDT",
            "ts": _ms(-105),
            "pnl": "-0.01",
            "fee": "0",
            "balChg": "-0.01",
        }
    )
    position = {
        "posId": "401",
        "instType": "SWAP",
        "instId": "ETH-USDT-SWAP",
        "posSide": "net",
        "direction": "long",
        "mgnMode": "isolated",
        "ccy": "USDT",
        "type": "2",
        "cTime": _ms(-120),
        "uTime": _ms(-90),
        "pnl": "1",
        "fee": "-0.02",
        "fundingFee": "-0.01",
        "liqPenalty": "0",
        "realizedPnl": "0.97",
        "settledPnl": "",
    }
    if mutation is not None:
        mutation(fills, bills, position)
    pages = []
    for selector in selectors:
        is_tail = selector.begin_ms == tail["begin_ms"]
        rows = []
        if (
            selector.kind.startswith("fills_")
            and selector.inst_type == "SWAP"
            and not is_tail
        ):
            rows = list(reversed(fills))
        elif selector.kind.startswith("bills_") and not is_tail:
            rows = list(reversed(bills))
        elif selector.kind == "positions_history":
            rows = [position]
        for index in range(2 if rows else 1):
            request = (
                selector
                if index == 0
                else bridge.select_live_account_history_page_v1(
                    selector.kind,
                    begin_ms=selector.begin_ms,
                    end_ms=selector.end_ms,
                    inst_type=selector.inst_type,
                    ord_type=selector.ord_type,
                    state=selector.state,
                    after=str(rows[-1][selector.cursor_type]),
                )
            )
            raw = canonical({"code": "0", "data": rows if index == 0 else []})
            digest = sha(canonical([request.query, len(pages)]))
            pages.append(
                bridge.LiveSourcePageDataV1(
                    request,
                    raw,
                    (),
                    (),
                    sha(raw),
                    sha(raw),
                    "c" * 64,
                    digest,
                    "d" * 64,
                    "https://openapi.okx.com",
                    "e" * 64,
                    "synthetic",
                    "f" * 64,
                    "1" * 64,
                )
            )
    balance = canonical(
        {
            "code": "0",
            "data": [
                {
                    "uTime": _ms(-90),
                    "details": [
                        {
                            "ccy": "USDT",
                            "eq": "100",
                            "availEq": "100",
                            "liab": "0",
                            "borrowFroz": "0",
                            "uTime": _ms(-90),
                        }
                    ],
                }
            ],
        }
    )
    return {
        "plan": plan,
        "history_pages": tuple(pages),
        "current_pages": (
            SimpleNamespace(
                selector=SimpleNamespace(stream="balance"), raw_body=balance
            ),
        ),
        "current_readback": SimpleNamespace(
            receipt={"no_open_positions_or_pending_orders_observed": True}
        ),
        "current_completed": ClockStamp(utc_ns=bridge._utc_ns(NOW), monotonic_ns=1000),
        "financial_tail_json": canonical(tail),
        "source_revision_sha256": "1" * 64,
        "raw_bank_sha256": "2" * 64,
        "uid": "100",
        "main_uid": "100",
        "credential_session_sha256": "3" * 64,
    }


def test_raw_financial_rows_reconcile_fill_bill_mirrors_signed_fee_and_funding_without_double_counting():
    value = subject._derive_original_financial_rows_v1(_view())
    assert value.equity == Decimal(100) and value.available_margin == Decimal(100)
    assert sum(item.gross_pnl + item.signed_fee for item in value.fills) == Fraction(
        98, 100
    )
    assert sum(item.signed_payment for item in value.funding_payments) == Fraction(
        -1, 100
    )
    assert value.closed_positions[0].net_pnl == Fraction(97, 100)
    assert all(len(item.source_locators) == 2 for item in value.fills)
    data = decode(value.receipt_json, maximum=65536)
    assert data["funding_time_basis"] == "actual_booked_payment_not_accrual"
    assert data["loss_streak_seed"] is None
    assert data["account_complete"] is data["execution_authority"] is False


@pytest.mark.parametrize(
    "mutation",
    (
        lambda fills, bills, position: bills.pop(0),
        lambda fills, bills, position: bills[0].pop("fee"),
        lambda fills, bills, position: bills[0].update(pnl="1"),
        lambda fills, bills, position: fills[0].update(feeCcy="ETH"),
        lambda fills, bills, position: fills[0].update(fillTime=_ms(1)),
        lambda fills, bills, position: position.pop("fundingFee"),
        lambda fills, bills, position: position.update(realizedPnl="1"),
        lambda fills, bills, position: position.update(type="3"),
        lambda fills, bills, position: bills[-1].update(type="unknown"),
    ),
)
def test_missing_conflicting_unsupported_or_future_financial_rows_never_become_zero(
    mutation,
):
    with pytest.raises((subject.LiveFinancialRowsError, ValueError)):
        subject._derive_original_financial_rows_v1(_view(mutation=mutation))


def test_terminal_empty_partition_and_balance_liability_are_not_optional():
    value = _view()
    value["history_pages"] = value["history_pages"][:-1]
    with pytest.raises(subject.LiveFinancialRowsError, match="partition_incomplete"):
        subject._derive_original_financial_rows_v1(value)
    value = _view()
    balance = decode(value["current_pages"][0].raw_body, maximum=65536)
    balance["data"][0]["details"][0]["borrowFroz"] = ""
    value["current_pages"][0].raw_body = canonical(balance)
    with pytest.raises(subject.LiveFinancialRowsError):
        subject._derive_original_financial_rows_v1(value)


def test_balance_tail_requires_same_explicit_update_revision_and_rejects_post_cutoff():
    pages = _view()["current_pages"]
    before = bridge._balance_tail_anchor_v1(pages, cutoff_ms=int(_ms(-3)))
    balance = decode(pages[0].raw_body, maximum=65536)
    balance["data"][0]["details"][0]["eq"] = "99"
    pages[0].raw_body = canonical(balance)
    assert bridge._balance_tail_anchor_v1(pages, cutoff_ms=int(_ms(-3))) != before
    balance["data"][0]["uTime"] = _ms(-2)
    pages[0].raw_body = canonical(balance)
    with pytest.raises(bridge.LiveSourceBridgeError):
        bridge._balance_tail_anchor_v1(pages, cutoff_ms=int(_ms(-3)))


def test_source_bank_budget_refuses_before_retaining_the_next_raw_partition():
    from dataclasses import replace

    page = replace(_view()["history_pages"][0], raw_body=b"x" * 1048576)
    bank = []
    with pytest.raises(bridge.LiveSourceBridgeError):
        bridge._extend_original_history_bank_v1(bank, (page,) * 65)
    assert bank == []
