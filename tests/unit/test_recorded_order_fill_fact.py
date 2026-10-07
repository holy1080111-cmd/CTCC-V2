"""Synthetic B1 V5 source facts only; no credentials or order writes."""

import json
from datetime import timedelta
from types import SimpleNamespace

import pytest

from app.database.repositories.submission_reporting import outcome_document
from app.trade_evidence import recorded_order_fill_fact as fact
from app.trade_evidence import submission_reporting
from app.trade_qualification import account_bootstrap_runtime as bootstrap
from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_capture_journal as journal
from app.trade_qualification import account_history_query_verifier as history
from app.trade_qualification.account_runtime import AccountRuntimeError
from tests.unit.test_account_history_query_verifier_contracts import pins
from tests.unit.test_account_ingestion_journal_contracts import setup
from tests.unit.test_qualification_account_capture import NOW, ms, row
from tests.unit.test_qualification_account_v4 import product_row, script

ORDER_ID = "99887766"
CLIENT_ID = "CTQ01234567890123456789012345678"
BODY = {
    "instId": "BTC-USDT-SWAP",
    "tdMode": "isolated",
    "clOrdId": CLIENT_ID,
    "side": "buy",
    "posSide": "net",
    "ordType": "fok",
    "sz": "2",
    "px": "100",
}
REPORT = SimpleNamespace(
    client_order_id=CLIENT_ID,
    submit_started_at=NOW - timedelta(seconds=4),
    completed_at=NOW - timedelta(seconds=2),
)


def target_order(**changes):
    return product_row(
        "orders_history_recent",
        "SWAP",
        ordId=ORDER_ID,
        clOrdId=CLIENT_ID,
        ordType="fok",
        tdMode="isolated",
        state="filled",
        sz="2",
        accFillSz="2",
        px="100",
        cTime=ms(NOW - timedelta(seconds=3)),
        uTime=ms(NOW - timedelta(seconds=1)),
        **changes,
    )


def target_fill(**changes):
    return product_row(
        "fills_history",
        "SWAP",
        billId="9191",
        ordId=ORDER_ID,
        tradeId="9192",
        fillSz="2",
        fillPx="100",
        fillTime=ms(NOW - timedelta(seconds=2)),
        ts=ms(NOW - timedelta(seconds=1)),
        **changes,
    )


async def recorded(monkeypatch, *, order=None, recent=None, archive=None, pending=None):
    session, harness, _, args, events = setup(monkeypatch, v4=True)
    selected = {
        "orders_history_recent_swap": [
            [target_order() if order is None else order],
            [],
        ],
        "fills_recent": [[target_fill() if recent is None else recent], []],
        "fills_history_swap": [[target_fill() if archive is None else archive], []],
    }
    if pending is not None:
        selected["orders_pending"] = [[pending], []]
    harness.script = script(pages=selected)
    await bootstrap.collect_bootstrap_recorded(session, **args)
    exact = pins(events)
    query = history.verify_history_query_chain(tuple(events), **exact)
    payload = next(
        item.event.packet_payload
        for item in events
        if item.event.packet_payload is not None
    )
    packet = capture.verify_demo_account_packet(
        payload,
        expected_sha256=exact["expected_packet_sha256"],
        expected_plan_sha256=exact["expected_plan_sha256"],
    )
    harness.assert_closed()
    return tuple(events), exact, query, packet


@pytest.mark.asyncio
async def test_v5_b1_records_exact_terminal_and_deduplicated_fill_quantity(monkeypatch):
    events, exact, query, packet = await recorded(monkeypatch)
    state, size, accumulated, fill_sum, orders, count, fills = fact._source_rows(
        packet,
        order_id=ORDER_ID,
        order_body=BODY,
        report=REPORT,
    )
    assert state == "filled" and size == accumulated == fill_sum == 2
    assert count == 1 and len(fills) == 2
    assert len(orders) == 1
    assert {item["stream"] for item in fills} == {"fills_recent", "fills_history_swap"}
    assert all(item["raw_sha256"] and item["page_receipt_sha256"] for item in fills)
    assert (
        json.loads(query.receipt_json)["journal_head_sha256"]
        == exact["expected_head_sha256"]
    )
    assert events[-1].event.packet_payload is None


@pytest.mark.asyncio
async def test_fok_canceled_zero_fill_is_recordable_but_not_a_release(monkeypatch):
    order = target_order()
    order.update(state="canceled", accFillSz="0")
    session, harness, _, args, events = setup(monkeypatch, v4=True)
    harness.script = script(
        pages={
            "orders_history_recent_swap": [[order], []],
            "fills_recent": [[]],
            "fills_history_swap": [[]],
        }
    )
    await bootstrap.collect_bootstrap_recorded(session, **args)
    exact = pins(events)
    history.verify_history_query_chain(tuple(events), **exact)
    payload = next(
        item.event.packet_payload for item in events if item.event.packet_payload
    )
    packet = capture.verify_demo_account_packet(
        payload,
        expected_sha256=exact["expected_packet_sha256"],
        expected_plan_sha256=exact["expected_plan_sha256"],
    )
    assert fact._source_rows(packet, order_id=ORDER_ID, order_body=BODY, report=REPORT)[
        :3
    ] == (
        "canceled",
        2,
        0,
    )
    assert fact.RecordedOrderFillFact(b"{}").reservation_releasable is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        "missing_order",
        "missing_fill",
        "wrong_client",
        "wrong_size",
        "late_update",
    ],
)
async def test_missing_conflicting_or_late_source_facts_fail_closed(
    monkeypatch, change
):
    order, fill = target_order(), target_fill()
    if change == "missing_order":
        order["ordId"] = "777777"
    elif change == "missing_fill":
        fill["ordId"] = "777777"
    elif change == "wrong_client":
        order["clOrdId"] = "CTQ99999999999999999999999999999"
    elif change == "wrong_size":
        fill["fillSz"] = "1"
    elif change == "late_update":
        order["uTime"] = ms(NOW)
    _, _, _, packet = await recorded(
        monkeypatch, order=order, recent=fill, archive=fill
    )
    with pytest.raises(fact.RecordedOrderFillFactError):
        fact._source_rows(packet, order_id=ORDER_ID, order_body=BODY, report=REPORT)


@pytest.mark.asyncio
async def test_same_bill_id_conflict_rejected_by_original_b1_verifier(monkeypatch):
    recent, archive = target_fill(), target_fill()
    archive["fillSz"] = "1"
    session, harness, _, args, events = setup(monkeypatch, v4=True)
    harness.script = script(
        pages={
            "orders_history_recent_swap": [[target_order()], []],
            "fills_recent": [[recent], []],
            "fills_history_swap": [[archive], []],
        }
    )
    with pytest.raises(AccountRuntimeError, match="account_runtime_invalid"):
        await bootstrap.collect_bootstrap_recorded(session, **args)
    assert (
        events
        and journal.checked_event(events[-1].event)["outcome"] != "complete_recorded"
    )


@pytest.mark.asyncio
async def test_same_ord_id_conflicting_order_history_rejected(monkeypatch):
    recent, archive = target_order(), target_order()
    archive["accFillSz"] = "1"
    session, harness, _, args, events = setup(monkeypatch, v4=True)
    harness.script = script(
        pages={
            "orders_history_recent_swap": [[recent], []],
            "orders_history_archive_swap": [[archive], []],
        }
    )
    with pytest.raises(AccountRuntimeError, match="account_runtime_invalid"):
        await bootstrap.collect_bootstrap_recorded(session, **args)
    assert (
        events
        and journal.checked_event(events[-1].event)["outcome"] != "complete_recorded"
    )


@pytest.mark.asyncio
async def test_same_ord_id_in_pending_and_terminal_history_fails_closed(monkeypatch):
    pending = row(
        "orders_pending",
        identifier=ORDER_ID,
        clOrdId=CLIENT_ID,
        instType="SWAP",
        posSide="net",
    )
    _, _, _, packet = await recorded(monkeypatch, pending=pending)
    with pytest.raises(
        fact.RecordedOrderFillFactError,
        match="recorded_fact_pending_order_conflict",
    ):
        fact._source_rows(packet, order_id=ORDER_ID, order_body=BODY, report=REPORT)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("side", "fill_price"),
    [("buy", "101"), ("sell", "99")],
)
async def test_fok_fill_price_outside_limit_fails_closed(monkeypatch, side, fill_price):
    order, fill = target_order(), target_fill()
    order["side"] = side
    fill["side"] = side
    fill["fillPx"] = fill_price
    _, _, _, packet = await recorded(
        monkeypatch, order=order, recent=fill, archive=fill
    )
    with pytest.raises(
        fact.RecordedOrderFillFactError,
        match="recorded_fact_fill_price_outside_limit",
    ):
        fact._source_rows(
            packet,
            order_id=ORDER_ID,
            order_body=BODY | {"side": side},
            report=REPORT,
        )


@pytest.mark.asyncio
async def test_uid_and_head_pins_are_not_replaceable_with_caller_claims(monkeypatch):
    events, exact, _, _ = await recorded(monkeypatch)
    for change in (
        {"expected_account_id": "999999"},
        {"expected_head_sha256": "0" * 64},
    ):
        with pytest.raises(history.HistoryQueryVerificationError):
            history.verify_history_query_chain(events, **(exact | change))
    assert journal.digest(events[-1].event.event_json) == exact["expected_head_sha256"]


def prepared_lineage(*, account_id="700001"):
    """Composed-verifier seam; real prepare/report replay has separate tests."""
    report = SimpleNamespace(
        client_order_id=CLIENT_ID,
        exchange_order_id=ORDER_ID,
        submit_started_at=REPORT.submit_started_at,
        completed_at=REPORT.completed_at,
        candidate=SimpleNamespace(
            account_id=account_id,
            settlement_currency="USDT",
            instrument_id="BTC-USDT-SWAP",
        ),
    )
    intent = SimpleNamespace(
        sha256="1" * 64,
        canonical_json=submission_reporting.wire(
            {
                "version": "ctcc-demo-submit-intent-v2",
                "exchange_request": {"body": BODY},
            }
        ),
    )
    prepared = SimpleNamespace(
        status="acknowledged",
        reason="exact_order_ack_observed",
        consumed=SimpleNamespace(reservation_id="2" * 64),
        intent=intent,
        capture_sha256="3" * 64,
        report_sha256="4" * 64,
        report_bytes=b"synthetic-seam",
        binding={"client_order_id": CLIENT_ID},
    )
    outcome = outcome_document(
        prepared,
        transition_id=1,
        sequence=1,
        previous=None,
        revision=4,
        recorded_at=NOW,
    )
    return prepared, report, outcome, submission_reporting.sha(outcome.encode())


@pytest.mark.asyncio
async def test_composed_receipt_records_fact_without_release_or_execution(monkeypatch):
    events, exact, _, _ = await recorded(monkeypatch)
    prepared, report, outcome, outcome_id = prepared_lineage()
    monkeypatch.setattr(
        fact.submission_reporting, "prepare_submission", lambda *a, **k: prepared
    )
    monkeypatch.setattr(fact.post_submit, "verify_submission_report", lambda *a: report)
    result = fact.verify_submission_recorded_order_fill_fact(
        events,
        history_pins=exact,
        submission_request=object(),
        intent_raw="synthetic-seam",
        intent_sha256="1" * 64,
        submission_capture=object(),
        outcome_json=outcome,
        expected_outcome_id=outcome_id,
    )
    value = json.loads(result.receipt_json)
    assert value["schema_version"] == fact.VERSION
    assert value["terminal_state"] == "filled"
    assert value["recorded_accumulated_fill_size"] == "2"
    assert value["recorded_fill_rows_sum"] == "2"
    assert value["distinct_fill_count"] == 1
    assert value["recorded_terminal_order_observed"]
    assert value["recorded_fill_quantity_matches_acc_fill"]
    assert all(item["raw_event_sequence"] for item in value["fill_locators"])
    assert all(
        value[name] is False
        for name in (
            "reservation_releasable",
            "account_complete",
            "source_authenticity_verified",
            "execution_authority",
            "order_retry_authority",
        )
    )
    assert result.admission == value["admission"] == "DENY"
    assert ORDER_ID.encode() not in result.receipt_json
    assert b"700001" not in result.receipt_json


@pytest.mark.asyncio
async def test_composed_receipt_rejects_outcome_and_uid_mismatch(monkeypatch):
    events, exact, _, _ = await recorded(monkeypatch)
    prepared, report, outcome, outcome_id = prepared_lineage()
    monkeypatch.setattr(
        fact.submission_reporting, "prepare_submission", lambda *a, **k: prepared
    )
    monkeypatch.setattr(fact.post_submit, "verify_submission_report", lambda *a: report)
    args = {
        "history_pins": exact,
        "submission_request": object(),
        "intent_raw": "synthetic-seam",
        "intent_sha256": "1" * 64,
        "submission_capture": object(),
        "outcome_json": outcome,
        "expected_outcome_id": outcome_id,
    }
    with pytest.raises(fact.RecordedOrderFillFactError, match="outcome_pin_mismatch"):
        fact.verify_submission_recorded_order_fill_fact(
            events,
            **(args | {"outcome_json": outcome + " "}),
        )
    wrong = json.loads(outcome)
    wrong["binding"]["client_order_id"] = "CTQwrong"
    altered = submission_reporting.wire(wrong)
    with pytest.raises(fact.RecordedOrderFillFactError, match="outcome_mismatch"):
        fact.verify_submission_recorded_order_fill_fact(
            events,
            **(
                args
                | {
                    "outcome_json": altered,
                    "expected_outcome_id": submission_reporting.sha(altered.encode()),
                }
            ),
        )
    _, wrong_report, _, _ = prepared_lineage(account_id="999999")
    monkeypatch.setattr(
        fact.post_submit, "verify_submission_report", lambda *a: wrong_report
    )
    with pytest.raises(fact.RecordedOrderFillFactError, match="scope_mismatch"):
        fact.verify_submission_recorded_order_fill_fact(events, **args)
