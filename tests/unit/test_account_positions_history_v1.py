"""Synthetic uTime-page tests; no credentials, network, or trading authority."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from app.trade_qualification import account_positions_history_v1 as source

NOW = datetime(2026, 10, 16, 0, 0, tzinfo=UTC)
NOW_MS = source._milliseconds(NOW)


def test_archive_readback_replays_exact_raw_chain_and_remains_deny_only():
    first = page([row(1, NOW_MS - 6000)])
    terminal = page([], index=1, prior=first, after=first.next_after, elapsed_ms=4)
    payload = source.freeze_chain(plan(), (first, terminal))
    result = source.verify_archived_chain(
        plan(), payload, expected_sha256=hashlib.sha256(payload).hexdigest()
    )
    assert result == source.verify_chain(plan(), (first, terminal))
    assert result.admission == "DENY"
    assert not result.source_authenticity_verified
    assert not result.execution_authority
    assert not result.account_complete
    with pytest.raises(source.PositionHistoryError, match="archive_pin_mismatch"):
        source.verify_archived_chain(
            plan(), payload + b" ", expected_sha256=hashlib.sha256(payload).hexdigest()
        )


@pytest.mark.parametrize(
    "mutation", ["plan", "raw", "clock", "chain", "extra", "terminal"]
)
def test_rehashed_archive_tampering_fails_closed(mutation):
    first = page([row(1, NOW_MS - 6000)])
    terminal = page([], index=1, prior=first, after=first.next_after, elapsed_ms=4)
    value = json.loads(source.freeze_chain(plan(), (first, terminal)))
    if mutation == "plan":
        value["plan_sha256"] = "0" * 64
    elif mutation == "raw":
        value["pages"][0]["raw_body_base64"] = "invalid!"
    elif mutation == "clock":
        value["pages"][1]["request_started_at"] = NOW.isoformat()
    elif mutation == "chain":
        value["chain_sha256"] = "0" * 64
    elif mutation == "extra":
        value["execution_authority"] = True
    else:
        value["pages"].pop()
    payload = source._canonical(value)
    with pytest.raises(source.PositionHistoryError):
        source.verify_archived_chain(
            plan(), payload, expected_sha256=hashlib.sha256(payload).hexdigest()
        )


def plan():
    return source.PositionHistoryPlanV1(
        registration_region="global",
        origin="https://openapi.okx.com",
        account_binding_sha256="a" * 64,
        session_binding_sha256="b" * 64,
        created_at=NOW,
        cutoff_u_time_ms=NOW_MS - 5000,
    )


def row(identifier, updated, **changes):
    return {
        "posId": str(identifier),
        "instId": "BTC-USDT-SWAP",
        "instType": "SWAP",
        "posSide": "net",
        "mgnMode": "isolated",
        "type": "2",
        "cTime": str(updated - 500),
        "uTime": str(updated),
        "realizedPnl": "0.01",
        "fee": "-0.01",
        "fundingFee": "",
        "liqPenalty": "0",
        "pnl": "0.02",
        **changes,
    }


def wire(rows):
    return json.dumps({"code": "0", "msg": "", "data": rows}).encode("utf-8")


def page(rows, *, index=0, prior=None, after=None, elapsed_ms=0):
    started = NOW + timedelta(milliseconds=1 + elapsed_ms)
    return source.parse_page(
        plan(),
        wire(rows),
        page_index=index,
        after=after,
        previous_page_sha256=None if prior is None else prior.page_sha256,
        request_started_at=started,
        headers_received_at=started + timedelta(milliseconds=1),
        body_completed_at=started + timedelta(milliseconds=2),
    )


def test_exact_query_and_tied_u_time_boundary_are_collision_safe():
    selected = plan()
    assert source.request_for_page(selected) == (
        "GET",
        "https://openapi.okx.com",
        "/api/v5/account/positions-history",
        (("limit", "100"),),
    )
    newer = NOW_MS - 1000
    tied = NOW_MS - 2000
    first = page([row(999, newer)] + [row(100 + index, tied) for index in range(101)])
    assert len(first.rows) == 102
    assert first.next_after == str(tied)
    assert source.request_for_page(selected, first.next_after)[-1] == (
        ("after", str(tied)),
        ("limit", "100"),
    )
    second = page(
        [row(5, NOW_MS - 6000)],
        index=1,
        prior=first,
        after=first.next_after,
        elapsed_ms=4,
    )
    terminal = page([], index=2, prior=second, after=second.next_after, elapsed_ms=8)
    chain = source.verify_chain(selected, (first, second, terminal))
    assert chain.row_count == 103
    assert chain.cutoff_observed is True
    assert chain.admission == "DENY"
    assert chain.account_complete is False
    assert chain.execution_authority is False
    assert chain.source_authenticity_verified is False
    assert first.rows[0].missing_financial_fields == ("fundingFee",)
    assert first.raw_sha256 != first.canonical_sha256


@pytest.mark.parametrize(
    "bad,code",
    [
        ([row(1, NOW_MS - 1000), row(1, NOW_MS - 1000)], "page_duplicate_identity"),
        (
            [row(1, NOW_MS - 1000), row(1, NOW_MS - 1000, fee="-0.02")],
            "page_conflicting_identity",
        ),
        ([row(1, NOW_MS - 2000), row(2, NOW_MS - 1000)], "page_time_order_invalid"),
        ([row(1, NOW_MS - 1000, uTime="")], "row_invalid"),
        ([row(1, NOW_MS - 1000, fee="NaN")], "row_financial_invalid"),
    ],
)
def test_malformed_duplicate_and_conflicting_rows_fail_closed(bad, code):
    with pytest.raises(source.PositionHistoryError, match=f"^{code}$"):
        page(bad)


def test_stagnant_or_nonexclusive_cursor_and_replayed_page_are_denied():
    first = page([row(10, NOW_MS - 1000)])
    with pytest.raises(source.PositionHistoryError, match="page_cursor_not_exclusive"):
        page(
            [row(11, NOW_MS - 1000)],
            index=1,
            prior=first,
            after=first.next_after,
            elapsed_ms=4,
        )
    second = page([], index=1, prior=first, after=first.next_after, elapsed_ms=4)
    with pytest.raises(
        source.PositionHistoryError, match="page_chain_metadata_invalid"
    ):
        page([], index=1, prior=first, after=None, elapsed_ms=4)
    with pytest.raises(source.PositionHistoryError, match="chain_replay_mismatch"):
        source.verify_chain(
            plan(),
            (first, replace(second, previous_page_sha256="0" * 64)),
        )
    skipped_index = source.parse_page(
        plan(),
        wire([]),
        page_index=2,
        after=first.next_after,
        previous_page_sha256=first.page_sha256,
        request_started_at=NOW + timedelta(milliseconds=5),
        headers_received_at=NOW + timedelta(milliseconds=6),
        body_completed_at=NOW + timedelta(milliseconds=7),
    )
    with pytest.raises(source.PositionHistoryError, match="chain_replay_mismatch"):
        source.verify_chain(plan(), (first, skipped_index))


def test_truncation_and_unexplained_over_limit_page_fail_closed():
    first = page([row(10, NOW_MS - 1000)])
    with pytest.raises(
        source.PositionHistoryError, match="empty_terminal_page_required"
    ):
        source.verify_chain(plan(), (first,))
    times = [NOW_MS - index for index in range(1, 102)]
    with pytest.raises(
        source.PositionHistoryError, match="page_collision_overflow_invalid"
    ):
        page([row(index + 1, value) for index, value in enumerate(times)])
    with pytest.raises(source.PositionHistoryError, match="page_row_limit"):
        page([row(index + 1, NOW_MS - 1000) for index in range(257)])


def test_false_cutoff_does_not_become_complete_or_authoritative():
    first = page([row(1, NOW_MS - 1000)])
    terminal = page([], index=1, prior=first, after=first.next_after, elapsed_ms=4)
    result = source.verify_chain(plan(), (first, terminal))
    assert result.cutoff_observed is False
    assert result.account_complete is False
    assert result.execution_authority is False


def test_tampered_body_and_wrong_time_or_query_do_not_replay():
    first = page([row(1, NOW_MS - 1000)])
    terminal = page([], index=1, prior=first, after=first.next_after, elapsed_ms=4)
    for mutation in (
        replace(first, raw_body=wire([row(1, NOW_MS - 1000, fee="-0.02")])),
        replace(first, request_query=(("limit", "1"),)),
    ):
        with pytest.raises(source.PositionHistoryError, match="chain_replay_mismatch"):
            source.verify_chain(plan(), (mutation, terminal))
    overlapping = page([], index=1, prior=first, after=first.next_after, elapsed_ms=0)
    with pytest.raises(source.PositionHistoryError, match="chain_clock_reversed"):
        source.verify_chain(plan(), (first, overlapping))


def test_row_update_after_exchange_gateway_out_time_is_denied():
    started = NOW + timedelta(milliseconds=1)
    body = json.dumps(
        {
            "code": "0",
            "msg": "",
            "inTime": str(NOW_MS * 1000 + 2000),
            "outTime": str(NOW_MS * 1000 + 3000),
            "data": [row(1, NOW_MS + 4)],
        }
    ).encode("utf-8")
    with pytest.raises(source.PositionHistoryError, match="row_after_gateway_response"):
        source.parse_page(
            plan(),
            body,
            page_index=0,
            after=None,
            previous_page_sha256=None,
            request_started_at=started,
            headers_received_at=started + timedelta(milliseconds=5),
            body_completed_at=started + timedelta(milliseconds=7),
        )


def test_row_at_exchange_gateway_out_time_remains_non_authoritative():
    started = NOW + timedelta(milliseconds=1)
    body = json.dumps(
        {
            "code": "0",
            "msg": "",
            "inTime": str(NOW_MS * 1000 + 2000),
            "outTime": str(NOW_MS * 1000 + 3000),
            "data": [row(1, NOW_MS + 3)],
        }
    ).encode("utf-8")
    observed = source.parse_page(
        plan(),
        body,
        page_index=0,
        after=None,
        previous_page_sha256=None,
        request_started_at=started,
        headers_received_at=started + timedelta(milliseconds=5),
        body_completed_at=started + timedelta(milliseconds=7),
    )
    assert observed.rows[0].u_time_ms == NOW_MS + 3
    assert observed.account_complete is False
    assert observed.source_authenticity_verified is False
    assert observed.execution_authority is False
