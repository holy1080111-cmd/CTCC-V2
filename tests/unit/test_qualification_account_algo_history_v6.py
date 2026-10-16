"""Synthetic algo-history GET chains; never evidence of complete Demo history."""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_collector as collector
from tests.unit import test_qualification_account_capture as old
from tests.unit.test_qualification_account_collector import Harness
from tests.unit.test_qualification_account_runtime import regional
from tests.unit.test_qualification_account_v4 import plan as v5_plan

STREAM = "algo_history_conditional_effective"


def plan():
    fields = capture._plain(regional())
    fields.update(
        contract_version="ctcc.demo_account_plan.v6",
        capture_scope="all_standard_products_v6_algo_history",
    )
    return capture.AllProductDemoAccountCapturePlanV6(**fields)


def history_row(stream=STREAM, identifier="9900", **changes):
    kind, state = capture._ALGO_HISTORY_QUERIES[stream]
    fields = {"ordType": kind, "state": state, "uTime": old.ms()}
    fields.update(changes)
    return old.row(
        f"algo_{kind}",
        identifier=identifier,
        **fields,
    )


def scripted_pages(*, extra=None):
    pages = {} if extra is None else extra
    result = []
    for stream in capture.V6_ALL_PRODUCT_STREAMS:
        if stream in pages:
            selected = pages[stream]
        elif stream == STREAM:
            selected = [[history_row()], []]
        elif stream in capture._CURSOR_FIELDS or stream == "positions":
            selected = [[]]
        else:
            selected = [[old.row(stream)]]
        result.extend((stream, rows) for rows in selected)
    return result


def observations(selected, *, extra=None):
    items = []
    identity = None
    stream_before = None
    index, after, prior = 0, None, None
    for stream, rows in scripted_pages(extra=extra):
        if stream != stream_before:
            index, after, prior = 0, None, None
        at = old.NOW + timedelta(milliseconds=10 + 3 * len(items))
        item = capture.parse_demo_account_observation(
            old.wire(rows),
            plan=selected,
            expected_plan_sha256=capture.plan_sha256(selected),
            stream=stream,
            request_started_at=at,
            headers_received_at=at + timedelta(milliseconds=1),
            body_completed_at=at + timedelta(milliseconds=2),
            barrier_completed_at=old.BARRIER,
            page_index=index,
            after=after,
            previous_page_sha256=prior,
            identity_receipt_sha256=identity,
        )
        items.append(item)
        if stream == "config_before":
            identity = item.receipt_sha256
        if rows and stream in capture._CURSOR_FIELDS:
            after = rows[-1][capture._CURSOR_FIELDS[stream]]
        prior = item.receipt_sha256
        index += 1
        stream_before = stream
    return tuple(items)


def parse_history(rows, *, stream=STREAM, **changes):
    selected = plan()
    return old.observe(
        old.wire(rows),
        selected=selected,
        stream=stream,
        identity_receipt_sha256="f" * 64,
        **changes,
    )


def test_versioned_inventory_uses_all_state_filters_and_algo_id_cursor():
    selected = plan()
    assert len(capture._ALGO_HISTORY_QUERIES) == 24
    assert len(capture.V6_ALL_PRODUCT_STREAMS) == 62
    assert capture.streams_for_plan(selected) == capture.V6_ALL_PRODUCT_STREAMS
    assert capture.streams_for_plan(selected)[-1] == "config_after"
    assert capture.V5_STREAMS != capture.V6_ALL_PRODUCT_STREAMS
    with pytest.raises(capture.AccountCaptureError, match="stream_invalid"):
        capture.account_request(v5_plan(), STREAM)
    for stream, (kind, state) in capture._ALGO_HISTORY_QUERIES.items():
        request = capture.account_request(selected, stream, after="123")
        assert request.method == "GET"
        assert request.origin == "https://openapi.okx.com"
        assert request.endpoint == "/api/v5/trade/orders-algo-history"
        assert dict(request.parameters) == {
            "after": "123",
            "limit": "100",
            "ordType": kind,
            "state": state,
        }
        assert "begin" not in dict(request.parameters)
        assert "end" not in dict(request.parameters)
        assert "algoId" not in dict(request.parameters)


def test_algo_history_requires_empty_terminal_and_replays_incomplete_packet():
    selected = plan()
    observed = observations(selected)
    packet = capture.verify_demo_account_records(
        observed,
        plan=selected,
        expected_plan_sha256=capture.plan_sha256(selected),
        barrier_completed_at=old.BARRIER,
    )
    chain = [item for item in observed if item.request.stream == STREAM]
    assert len(chain) == 2
    assert chain[0].terminal is False
    assert chain[1].terminal is True
    assert chain[1].after == chain[0].rows[-1].row_id == "9900"
    assert chain[1].previous_page_sha256 == chain[0].receipt_sha256
    assert packet.schema_version == "ctcc.demo_account_capture.v6"
    assert packet.account_complete is False
    assert packet.source_authenticity_verified is False
    assert packet.execution_authority is False
    assert "history_retention_unverified" in packet.incomplete_reasons
    assert "algo_history_terminal_partition_unverified" in packet.incomplete_reasons
    assert "algo_history_epoch_coverage_unverified" in packet.incomplete_reasons
    frozen = capture.freeze_demo_account_packet(
        packet, expected_plan_sha256=packet.plan_sha256
    )
    assert (
        capture.verify_demo_account_packet(
            frozen.payload,
            expected_sha256=frozen.sha256,
            expected_plan_sha256=packet.plan_sha256,
        )
        == packet
    )
    with pytest.raises(
        capture.AccountCaptureError, match="empty_terminal_page_required"
    ):
        capture.verify_demo_account_records(
            tuple(item for item in observed if item is not chain[1]),
            plan=selected,
            expected_plan_sha256=packet.plan_sha256,
            barrier_completed_at=old.BARRIER,
        )


@pytest.mark.parametrize(
    ("row", "code"),
    [
        ({"ordType": "oco"}, "algo_query_scope_mismatch"),
        ({"state": "canceled"}, "algo_history_state_mismatch"),
        ({"state": "partially_effective"}, "algo_history_state_mismatch"),
        ({"state": "partially_failed"}, "algo_history_state_mismatch"),
        ({"algoId": ""}, "source_identity_field_invalid"),
        ({"uTime": old.ms(old.NOW + timedelta(seconds=1))}, "future_source_timestamp"),
    ],
)
def test_algo_history_wrong_query_identity_or_time_fails_closed(row, code):
    with pytest.raises(capture.AccountCaptureError, match=code):
        parse_history([history_row(**row)])


def test_algo_history_cursor_is_exclusive_descending_and_not_an_order_id():
    with pytest.raises(
        capture.AccountCaptureError, match="source_cursor_order_invalid"
    ):
        parse_history([history_row(identifier="99"), history_row(identifier="100")])
    with pytest.raises(
        capture.AccountCaptureError, match="source_cursor_not_exclusive"
    ):
        parse_history(
            [history_row(identifier="101")],
            page_index=1,
            after="100",
            previous_page_sha256="e" * 64,
        )
    with pytest.raises(capture.AccountCaptureError, match="duplicate_source_identity"):
        parse_history([history_row(identifier="100"), history_row(identifier="100")])


@pytest.mark.parametrize(
    "conflict_stream",
    ["algo_conditional", "algo_history_conditional_canceled"],
)
def test_algo_history_cross_state_and_pending_conflict_deny_packet(conflict_stream):
    selected = plan()
    duplicate = (
        old.row("algo_conditional", identifier="9900")
        if conflict_stream == "algo_conditional"
        else history_row(stream=conflict_stream)
    )
    observed = observations(
        selected,
        extra={conflict_stream: [[duplicate], []]},
    )
    with pytest.raises(
        capture.AccountCaptureError, match="conflicting_algo_type_identity"
    ):
        capture.verify_demo_account_records(
            observed,
            plan=selected,
            expected_plan_sha256=capture.plan_sha256(selected),
            barrier_completed_at=old.BARRIER,
        )


@pytest.mark.asyncio
async def test_owned_collector_issues_only_pinned_gets_and_rejects_duplicate(
    monkeypatch,
):
    selected = plan()
    harness = Harness(monkeypatch)
    harness.script = scripted_pages()
    packet = await harness.collect(selected=selected)
    assert packet.schema_version == "ctcc.demo_account_capture.v6"
    assert packet.execution_authority is False
    assert len(packet.observations) == len(harness.script) == len(harness.requests)
    assert all(request.method == "GET" for request in harness.requests)
    assert all(
        request.headers["x-simulated-trading"] == "1" for request in harness.requests
    )
    history = [
        request
        for request in harness.requests
        if request.url.path == "/api/v5/trade/orders-algo-history"
    ]
    assert len(history) == 25  # 24 state/type scopes plus one terminal follow-up.
    assert [
        request.url.params.get("after")
        for request in history
        if request.url.params.get("state") == "effective"
        and request.url.params.get("ordType") == "conditional"
    ] == [None, "9900"]
    harness.assert_closed()

    duplicate = Harness(monkeypatch)
    duplicate.script = scripted_pages(
        extra={
            "algo_conditional": [[old.row("algo_conditional", identifier="9900")], []]
        }
    )
    with pytest.raises(
        collector.AccountCollectionError, match="conflicting_algo_identity"
    ):
        await duplicate.collect(selected=selected)
    duplicate.assert_closed()
