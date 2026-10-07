"""Synthetic raw-page evidence only; these fixtures are not Demo account data."""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from app.trade_qualification import settled_funding_history_source as funding

NOW = datetime(2026, 10, 8, 12, tzinfo=UTC)
INSTRUMENT = "BTC-USDT-SWAP"
ORIGIN = "https://openapi.okx.com"


def event(days_before, rate="0.0001", *, instrument=INSTRUMENT):
    at = NOW - timedelta(days=days_before)
    return {
        "instType": "SWAP",
        "instId": instrument,
        "fundingTime": str(int(at.timestamp() * 1000)),
        "fundingRate": rate,
        "realizedRate": rate,
        "formulaType": "withRate",
        "method": "current_period",
    }


def page(rows, *, after=None, index=0, query=None, origin=ORIGIN, raw=None):
    started = NOW + timedelta(seconds=index * 5)
    expected_query = (("instId", INSTRUMENT), ("limit", "400"))
    if after is not None:
        expected_query += (("after", after),)
    return funding.RawFundingHistoryPage(
        method="GET",
        origin=origin,
        endpoint=funding.ENDPOINT,
        request_query=expected_query if query is None else query,
        simulated_trading_header="1",
        request_started_at=started,
        response_received_at=started + timedelta(milliseconds=120),
        request_started_monotonic_ns=1_000_000_000 + index * 5_000_000_000,
        response_received_monotonic_ns=1_120_000_000 + index * 5_000_000_000,
        http_status=200,
        raw_body=(
            json.dumps(
                {"code": "0", "msg": "", "data": rows}, separators=(",", ":")
            ).encode()
            if raw is None
            else raw
        ),
    )


def chain():
    first = [event(1, "-0.0002"), event(2, "0")]
    second = [event(3, "0.0003")]
    return (
        page(first),
        page(second, after=first[-1]["fundingTime"], index=1),
        page([], after=second[-1]["fundingTime"], index=2),
    )


def scope(*, required_from_at=None, region="global", instrument=INSTRUMENT):
    return funding.FundingHistoryScope(
        region, instrument, required_from_at or NOW - timedelta(days=7)
    )


def replay(pages, *, source_scope=None, source_pin=None, policy=None):
    source_scope = source_scope or scope()
    return funding.replay_settled_funding_history_pages(
        source_scope,
        pages,
        expected_source_pages_sha256=(
            funding.source_pages_sha256(source_scope, pages)
            if source_pin is None
            else source_pin
        ),
        expected_policy_sha256=funding.POLICY_SHA256 if policy is None else policy,
    )


def test_original_raw_pages_settlement_times_and_denial_replay_deterministically():
    pages = chain()
    result = replay(pages)
    data = json.loads(result.receipt_json)
    assert result.receipt_json == replay(pages).receipt_json
    assert result.raw_pages == tuple(item.raw_body for item in pages)
    assert data["policy_sha256"] == funding.POLICY_SHA256
    assert data["source_pages_sha256"] == funding.source_pages_sha256(scope(), pages)
    assert [item["row_count"] for item in data["pages"]] == [2, 1, 0]
    assert data["presented_terminal_empty_page"] is True
    assert [item["realized_rate"] for item in data["settled_public_events"]] == [
        "-0.0002",
        "0",
        "0.0003",
    ]
    assert data["settled_public_events"][1]["locator"] == {
        "page_index": 0,
        "row_ordinal": 1,
        "page_raw_sha256": data["pages"][0]["raw_sha256"],
    }
    assert data["pages"][1]["request_query"][-1] == [
        "after",
        event(2)["fundingTime"],
    ]
    assert (
        data["pages"][0]["request_started_at"]
        == pages[0].request_started_at.isoformat()
    )
    assert (
        data["pages"][0]["response_received_at"]
        == pages[0].response_received_at.isoformat()
    )
    assert data["retention_status"] == "documented_limit_not_source_finality"
    assert (
        data["newest_presented_settlement_at"] == (NOW - timedelta(days=1)).isoformat()
    )
    assert (
        data["oldest_presented_settlement_at"] == (NOW - timedelta(days=3)).isoformat()
    )
    assert data["settled_schedule_complete"] is False
    assert data["no_applicable_funding_proven"] is False
    assert data["applicable_account_funding_amount"] is None
    assert data["source_authenticity_verified"] is False
    assert data["account_region_authenticated"] is False
    assert data["account_complete"] is False and data["snapshot"] is None
    assert result.admission == data["admission"] == "DENY"
    assert result.execution_authority is data["execution_authority"] is False


@pytest.mark.parametrize(
    ("changed", "code"),
    [
        (lambda pages: pages[:2], "funding_history_terminal_missing"),
        (
            lambda pages: (
                pages[0],
                page([], after=event(2)["fundingTime"], index=1),
                pages[2],
            ),
            "funding_history_terminal_not_final",
        ),
        (
            lambda pages: (
                pages[0],
                replace(
                    pages[1],
                    request_query=(
                        ("instId", INSTRUMENT),
                        ("limit", "400"),
                        ("before", event(2)["fundingTime"]),
                    ),
                ),
                pages[2],
            ),
            "funding_history_query_invalid",
        ),
        (
            lambda pages: (
                pages[0],
                replace(pages[1], origin="https://us.okx.com"),
                pages[2],
            ),
            "funding_history_query_invalid",
        ),
        (
            lambda pages: (
                pages[0],
                replace(pages[1], request_started_monotonic_ns=1),
                pages[2],
            ),
            "funding_history_clock_invalid",
        ),
        (
            lambda pages: (
                pages[0],
                replace(pages[1], method="POST"),
                pages[2],
            ),
            "funding_history_page_invalid",
        ),
        (
            lambda pages: (
                pages[0],
                replace(pages[1], simulated_trading_header="0"),
                pages[2],
            ),
            "funding_history_page_invalid",
        ),
    ],
)
def test_page_chain_fails_closed(changed, code):
    with pytest.raises(funding.SettledFundingHistoryError, match=code):
        replay(changed(chain()))


@pytest.mark.parametrize(
    ("duplicate", "code"),
    [
        (event(2, "0"), "funding_history_duplicate_event"),
        (event(2, "0.9"), "funding_history_conflicting_event"),
    ],
)
def test_cursor_boundary_duplicate_or_conflict_is_not_silently_deduplicated(
    duplicate, code
):
    pages = chain()
    changed = (
        pages[0],
        page([duplicate], after=event(2)["fundingTime"], index=1),
    )
    with pytest.raises(funding.SettledFundingHistoryError, match=code):
        replay(changed)


def test_wrong_instrument_future_event_and_duplicate_json_key_deny():
    pages = chain()
    bad_rows = (
        [event(4, instrument="ETH-USDT-SWAP")],
        [event(-1)],
    )
    for rows, code in zip(
        bad_rows,
        ("funding_history_event_scope_invalid", "funding_history_unsettled_event"),
        strict=True,
    ):
        changed = (page(rows), page([], after=rows[0]["fundingTime"], index=1))
        with pytest.raises(funding.SettledFundingHistoryError, match=code):
            replay(changed)
    bad_json = (page([], raw=b'{"code":"0","msg":"","data":[],"code":"0"}'),)
    with pytest.raises(
        funding.SettledFundingHistoryError, match="funding_history_duplicate_json_key"
    ):
        replay(bad_json)
    assert pages[0].raw_body != b""


def test_old_required_start_is_retention_gap_not_no_funding_claim():
    result = replay(
        chain(), source_scope=scope(required_from_at=NOW - timedelta(days=120))
    )
    data = json.loads(result.receipt_json)
    assert (
        data["retention_status"] == "required_start_outside_max_three_calendar_months"
    )
    assert "required_start_outside_documented_retention" in data["blocking_reasons"]
    assert data["no_applicable_funding_proven"] is False


def test_empty_terminal_first_page_does_not_prove_zero_funding():
    data = json.loads(replay((page([]),)).receipt_json)
    assert data["settled_public_events"] == []
    assert data["oldest_presented_settlement_at"] is None
    assert data["presented_terminal_empty_page"] is True
    assert data["settled_schedule_complete"] is False
    assert data["no_applicable_funding_proven"] is False


def test_unreviewed_region_and_future_required_start_deny():
    with pytest.raises(
        funding.SettledFundingHistoryError, match="funding_history_region_unreviewed"
    ):
        replay(chain(), source_scope=scope(region="tr"))
    with pytest.raises(
        funding.SettledFundingHistoryError,
        match="funding_history_required_start_invalid",
    ):
        replay(chain(), source_scope=scope(required_from_at=NOW + timedelta(days=1)))


def test_policy_and_original_source_pins_cannot_be_silently_reinterpreted():
    pages = chain()
    with pytest.raises(
        funding.SettledFundingHistoryError, match="funding_history_policy_mismatch"
    ):
        replay(pages, policy="0" * 64)
    source_pin = funding.source_pages_sha256(scope(), pages)
    changed = (replace(pages[0], raw_body=pages[0].raw_body + b" "), *pages[1:])
    with pytest.raises(
        funding.SettledFundingHistoryError, match="funding_history_source_pin_mismatch"
    ):
        replay(changed, source_pin=source_pin)
