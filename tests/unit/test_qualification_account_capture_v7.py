"""Synthetic V7 account inventories; never use credentials or live HTTP."""

from __future__ import annotations

from datetime import timedelta

import httpx
import pytest
from pydantic import ValidationError

from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_collector as collector
from tests.unit.test_qualification_account_capture import (
    BARRIER,
    INSTRUMENT,
    MAIN_UID,
    NOW,
    UID,
    row,
    wire,
)
from tests.unit.test_qualification_account_collector import Clock, Stream, credentials


def v7_plan(**changes):
    return capture.CurrentDemoAccountCapturePlanV7(
        **{
            "plan_id": "synthetic-current-account-v7",
            "created_at": NOW,
            "expected_uid": UID,
            "expected_main_uid": MAIN_UID,
            "session_binding_id": "synthetic-private-session",
            "settlement_currency": "USDT",
            "leverage_instrument_ids": (INSTRUMENT,),
            "history_start": NOW - timedelta(days=7),
            "history_end": NOW - timedelta(seconds=1),
            "contract_version": "ctcc.demo_current_account_plan.v7",
            "registration_region": "global",
            "origin": "https://openapi.okx.com",
            "registration_evidence_sha256": "a" * 64,
            **changes,
        }
    )


def v7_observations(selected, *, pages=None, omit=None):
    pages = {} if pages is None else pages
    observations = []
    identity = None
    pin = capture.plan_sha256(selected)
    for stream in capture.V7_CURRENT_STREAMS:
        if stream == omit:
            continue
        default_pages = (
            [[]]
            if stream.startswith("algo_") or stream == "orders_pending"
            else [[row(stream)]]
        )
        stream_pages = pages.get(stream, default_pages)
        cursor = previous = None
        for index, data in enumerate(stream_pages):
            started = NOW + timedelta(milliseconds=10 + 3 * len(observations))
            observed = capture.parse_demo_account_observation(
                wire(data),
                plan=selected,
                expected_plan_sha256=pin,
                stream=stream,
                request_started_at=started,
                headers_received_at=started + timedelta(milliseconds=1),
                body_completed_at=started + timedelta(milliseconds=2),
                barrier_completed_at=BARRIER,
                page_index=index,
                after=cursor,
                previous_page_sha256=previous,
                identity_receipt_sha256=identity,
            )
            observations.append(observed)
            if stream == "config_before":
                identity = observed.receipt_sha256
            previous = observed.receipt_sha256
            if data and (stream.startswith("algo_") or stream == "orders_pending"):
                cursor = data[-1]["algoId" if stream.startswith("algo_") else "ordId"]
    return tuple(observations)


def test_v7_exact_current_algo_inventory_and_separate_contract():
    selected = v7_plan()
    expected_algos = (
        "conditional",
        "oco",
        "chase",
        "trigger",
        "move_order_stop",
        "iceberg",
        "twap",
        "smart_iceberg",
    )
    assert capture.CURRENT_ALGO_ORDER_TYPES_V7 == expected_algos
    assert tuple(
        stream for stream in capture.V7_CURRENT_STREAMS if stream.startswith("algo_")
    ) == tuple(f"algo_{kind}" for kind in expected_algos)
    assert capture.ALGO_ORDER_TYPES == (
        "conditional",
        "oco",
        "trigger",
        "move_order_stop",
    )
    assert capture.streams_for_plan(selected) == capture.V7_CURRENT_STREAMS
    assert capture.streams_for_plan(selected) != capture.V6_CURRENT_STREAMS
    assert selected.capture_scope == "all_current_standard_products_v7_eight_algos"
    for kind in expected_algos:
        request = capture.account_request(selected, f"algo_{kind}")
        assert request.method == "GET"
        assert request.origin == "https://openapi.okx.com"
        assert request.endpoint == "/api/v5/trade/orders-algo-pending"
        assert dict(request.parameters) == {"limit": "100", "ordType": kind}
        after = capture.account_request(selected, f"algo_{kind}", after="123")
        assert dict(after.parameters) == {
            "after": "123",
            "limit": "100",
            "ordType": kind,
        }


def test_v7_packet_freezes_and_replays_without_granting_account_authority():
    selected = v7_plan()
    packet = capture.verify_demo_account_records(
        v7_observations(selected),
        plan=selected,
        expected_plan_sha256=capture.plan_sha256(selected),
        barrier_completed_at=BARRIER,
    )
    assert packet.schema_version == "ctcc.demo_current_account_capture.v7"
    assert type(packet.plan) is capture.CurrentDemoAccountCapturePlanV7
    assert packet.account_complete is False
    assert packet.execution_authority is False
    assert packet.source_authenticity_verified is False
    assert "separate_history_source_join_required" in packet.incomplete_reasons
    frozen = capture.freeze_demo_account_packet(
        packet, expected_plan_sha256=capture.plan_sha256(selected)
    )
    replay = capture.verify_demo_account_packet(
        frozen.payload,
        expected_sha256=frozen.sha256,
        expected_plan_sha256=capture.plan_sha256(selected),
    )
    assert replay == packet


def _instrument_observation(instrument_row):
    selected = v7_plan()
    started = NOW + timedelta(milliseconds=10)
    return capture.parse_demo_account_observation(
        wire([instrument_row]),
        plan=selected,
        expected_plan_sha256=capture.plan_sha256(selected),
        stream="account_instruments",
        request_started_at=started,
        headers_received_at=started + timedelta(milliseconds=1),
        body_completed_at=started + timedelta(milliseconds=2),
        barrier_completed_at=BARRIER,
        identity_receipt_sha256="a" * 64,
    )


def test_v7_instrument_code_accepts_only_documented_integer_at_exact_wire_path():
    # OKX's account/instruments response now emits instIdCode as an integer.
    observed = _instrument_observation(row("account_instruments", instIdCode=2**50))
    assert observed.request.stream == "account_instruments"
    assert observed.rows[0].row_id == INSTRUMENT
    assert b'"instIdCode": 1125899906842624' in observed.response_body
    _instrument_observation(row("account_instruments", instIdCode=None))
    for bad in (0, -1, 2**63, 1.5):
        with pytest.raises(capture.AccountCaptureError, match="json_number_invalid"):
            _instrument_observation(row("account_instruments", instIdCode=bad))
    with pytest.raises(
        capture.AccountCaptureError, match="source_instrument_code_invalid"
    ):
        _instrument_observation(row("account_instruments", instIdCode="123"))
    with pytest.raises(capture.AccountCaptureError, match="json_number_invalid"):
        _instrument_observation(row("account_instruments", unrelatedInteger=123))
    with pytest.raises(capture.AccountCaptureError, match="json_number_invalid"):
        _instrument_observation(
            row("account_instruments", upcChg=[{"instIdCode": 123}])
        )


def test_v7_default_bound_admits_large_account_instrument_page_without_truncation():
    selected = v7_plan()
    rows = [
        row(
            "account_instruments",
            instId=f"SYM{index}-USDT-SWAP",
            instIdCode=2**50 + index,
            descriptiveField="x" * 600,
        )
        for index in range(167)
    ]
    body = wire(rows)
    assert 65536 < len(body) < selected.max_response_bytes
    started = NOW + timedelta(milliseconds=10)
    observed = capture.parse_demo_account_observation(
        body,
        plan=selected,
        expected_plan_sha256=capture.plan_sha256(selected),
        stream="account_instruments",
        request_started_at=started,
        headers_received_at=started + timedelta(milliseconds=1),
        body_completed_at=started + timedelta(milliseconds=2),
        barrier_completed_at=BARRIER,
        identity_receipt_sha256="a" * 64,
    )
    assert observed.response_body == body
    assert len(observed.rows) == 167


def test_v7_short_algo_page_is_not_terminal_and_uses_exact_algo_cursor():
    selected = v7_plan()
    stream = "algo_smart_iceberg"
    pages = {stream: [[row(stream, "987", ordId="99", billId="88")], []]}
    observations = v7_observations(selected, pages=pages)
    chain = [item for item in observations if item.request.stream == stream]
    assert len(chain) == 2
    assert chain[0].terminal is False and chain[1].terminal is True
    assert chain[1].after == "987"
    assert chain[1].previous_page_sha256 == chain[0].receipt_sha256
    assert dict(chain[1].request.parameters)["after"] == "987"
    capture.verify_demo_account_records(
        observations,
        plan=selected,
        expected_plan_sha256=capture.plan_sha256(selected),
        barrier_completed_at=BARRIER,
    )
    with pytest.raises(
        capture.AccountCaptureError, match="empty_terminal_page_required"
    ):
        capture.verify_demo_account_records(
            tuple(item for item in observations if item is not chain[1]),
            plan=selected,
            expected_plan_sha256=capture.plan_sha256(selected),
            barrier_completed_at=BARRIER,
        )


def test_v7_missing_algo_stream_cannot_form_packet():
    selected = v7_plan()
    with pytest.raises(
        capture.AccountCaptureError,
        match="inventory_page_count_invalid|inventory_stream_missing_or_limit",
    ):
        capture.verify_demo_account_records(
            v7_observations(selected, omit="algo_chase"),
            plan=selected,
            expected_plan_sha256=capture.plan_sha256(selected),
            barrier_completed_at=BARRIER,
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"registration_region": "eea"},
        {"registration_evidence_sha256": ""},
        {"contract_version": "ctcc.demo_current_account_plan.v6"},
        {"capture_scope": "all_current_standard_products_v6"},
        {"max_total_pages": 16},
    ],
)
def test_v7_plan_rejects_unpinned_region_version_scope_or_budget(changes):
    with pytest.raises((capture.AccountCaptureError, ValidationError)):
        v7_plan(**changes)


def test_v7_algo_response_rejects_wrong_type_and_nonprogressing_algo_id():
    selected = v7_plan()
    with pytest.raises(capture.AccountCaptureError, match="algo_query_scope_mismatch"):
        v7_observations(
            selected,
            pages={"algo_chase": [[row("algo_chase", ordType="conditional")], []]},
        )
    with pytest.raises(
        capture.AccountCaptureError, match="source_cursor_not_exclusive"
    ):
        v7_observations(
            selected,
            pages={
                "algo_twap": [[row("algo_twap", "987")], [row("algo_twap", "987")], []]
            },
        )


def test_v7_same_algo_id_in_distinct_type_queries_is_conflict():
    selected = v7_plan()
    observations = v7_observations(
        selected,
        pages={
            "algo_conditional": [[row("algo_conditional", "987")], []],
            "algo_chase": [[row("algo_chase", "987")], []],
        },
    )
    with pytest.raises(
        capture.AccountCaptureError, match="conflicting_algo_type_identity"
    ):
        capture.verify_demo_account_records(
            observations,
            plan=selected,
            expected_plan_sha256=capture.plan_sha256(selected),
            barrier_completed_at=BARRIER,
        )


@pytest.mark.asyncio
async def test_v7_owned_demo_collector_queries_eight_algos_and_chase_cursor(
    monkeypatch,
):
    selected = v7_plan()
    scripted = [(stream, None) for stream in capture.V7_CURRENT_STREAMS]
    chase_index = capture.V7_CURRENT_STREAMS.index("algo_chase")
    scripted.insert(chase_index + 1, ("algo_chase", "987"))
    requests = []
    clients = []

    def factory():
        def handler(request):
            index = len(requests)
            assert index < len(scripted)
            stream, after = scripted[index]
            assert (
                request.url.path == capture.account_request(selected, stream).endpoint
            )
            assert (
                tuple(request.url.params.multi_items())
                == capture.account_request(selected, stream, after).parameters
            )
            requests.append(request)
            if stream == "algo_chase" and after is None:
                data = [row(stream, "987")]
            elif stream.startswith("algo_") or stream == "orders_pending":
                data = []
            else:
                data = [row(stream)]
            body = wire(data)
            return httpx.Response(
                200,
                headers={
                    "Content-Type": "application/json",
                    "Content-Length": str(len(body)),
                },
                stream=Stream(body),
                request=request,
            )

        client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler), trust_env=False
        )
        clients.append(client)
        return client

    monkeypatch.setattr(collector, "_new_client", factory)
    packet = await collector.collect_demo_account_records(
        credentials=credentials(),
        clock=Clock(),
        plan=selected,
        expected_plan_sha256=capture.plan_sha256(selected),
        barrier_completed_at=BARRIER,
    )
    assert len(requests) == len(capture.V7_CURRENT_STREAMS) + 1
    assert all(request.method == "GET" for request in requests)
    assert all(request.url.host == "openapi.okx.com" for request in requests)
    assert all(request.headers["x-simulated-trading"] == "1" for request in requests)
    assert all(client.is_closed for client in clients)
    assert requests[chase_index + 1].url.params["after"] == "987"
    assert packet.schema_version == "ctcc.demo_current_account_capture.v7"
    assert packet.account_complete is False and packet.execution_authority is False
