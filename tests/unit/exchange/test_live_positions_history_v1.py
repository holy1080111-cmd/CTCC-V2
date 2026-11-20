"""Synthetic Live position raw-page contracts; never account or order authority."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.domain.source_primitives import ClockStamp, canonical
from app.exchange.okx import live_account_history_chain_v1 as chain
from app.exchange.okx import live_account_history_page_v1 as page


def _row(**changes):
    value = {
        "posId": "10",
        "instId": "ETH-USDT-SWAP",
        "instType": "SWAP",
        "posSide": "net",
        "direction": "long",
        "mgnMode": "isolated",
        "type": "2",
        "cTime": "1795132700000",
        "uTime": "1795132799000",
        "ccy": "USDT",
        "realizedPnl": "-0.02",
        "pnl": "0",
        "fee": "-0.01",
        "fundingFee": "-0.01",
        "liqPenalty": "0",
        "settledPnl": "",
        "openMaxPos": "1",
        "closeTotalPos": "1",
    }
    value.update(changes)
    return value


def _parse(rows, after=None):
    return page.checked_live_account_history_rows_v1(
        canonical({"code": "0", "data": rows}),
        page.select_live_account_history_page_v1("positions_history", after=after),
    )


def test_position_history_queries_exact_official_utime_cursor_and_no_other_filter():
    first = page.select_live_account_history_page_v1("positions_history")
    assert first.path == "/api/v5/account/positions-history"
    assert first.cursor_type == "uTime" and first.query == (("limit", "100"),)
    assert page.select_live_account_history_page_v1(
        "positions_history", after="1795132799000"
    ).query == (("after", "1795132799000"), ("limit", "100"))
    for changes in (
        {"begin_ms": "1795132700000"},
        {"inst_type": "SWAP"},
        {"after": "10"},
    ):
        with pytest.raises(page.LiveHistoryPageError):
            page.select_live_account_history_page_v1("positions_history", **changes)


def test_equal_utime_rows_remain_in_source_order_and_terminal_empty_is_required():
    rows = [_row(), _row(posId="11")]
    raw, ids, count, terminal = _parse(rows)
    assert raw == canonical({"code": "0", "data": rows})
    assert ids == ("1795132799000", "1795132799000")
    assert count == 2 and terminal is False
    assert _parse([], after=ids[-1])[2:] == (0, True)


@pytest.mark.parametrize(
    "rows,after",
    (
        ([_row(), _row()], None),
        ([_row(uTime="1795132798000"), _row(posId="11")], None),
        ([_row()], "1795132799000"),
        ([_row(posId=str(i + 1)) for i in range(257)], None),
    ),
)
def test_duplicate_nonprogressing_unordered_or_unbounded_positions_fail_closed(
    rows, after
):
    with pytest.raises(page.LiveHistoryPageError):
        _parse(rows, after)


def test_official_equal_utime_boundary_may_exceed_limit_without_truncation_or_dedup():
    rows = [_row(posId=str(i + 1)) for i in range(101)]
    assert _parse(rows)[2:] == (101, False)


def test_position_financial_values_are_validated_with_actual_measured_body_cutoff():
    stamp = ClockStamp(utc_ns=1795132800000000000, monotonic_ns=100)
    value = SimpleNamespace(
        selector=page.select_live_account_history_page_v1("positions_history"),
        stamps=(stamp,) * 4,
        raw_body=canonical({"code": "0", "data": [_row()]}),
    )
    chain._check_row_times(value)
    for changes in (
        {"fundingFee": "NaN"},
        {"uTime": "1795132800001"},
        {"type": "unknown"},
    ):
        value.raw_body = canonical({"code": "0", "data": [_row(**changes)]})
        with pytest.raises(chain.LiveAccountHistoryChainError):
            chain._check_row_times(value)
    assert datetime.fromtimestamp(stamp.utc_ns // 1_000_000_000, UTC).tzinfo is UTC
