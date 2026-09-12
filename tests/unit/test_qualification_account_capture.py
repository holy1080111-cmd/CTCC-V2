"""Synthetic private-account envelopes only: no credentials, HTTP or account IO.

Successful capture is bounded record consistency, never authenticated account
completeness, a portfolio approval, or authority to place an order.
"""

from __future__ import annotations

import ast
import hashlib
import inspect
import json
from datetime import UTC, datetime, timedelta, timezone, tzinfo
from decimal import Decimal

import pytest
from pydantic import BaseModel, ValidationError, create_model, model_serializer

from app.trade_qualification import account_capture as module

NOW = datetime(2026, 9, 12, 10, tzinfo=UTC)
BARRIER = NOW - timedelta(seconds=2)
UID = "700001"
MAIN_UID = "700000"
INSTRUMENT = "BTC-USDT-SWAP"
STREAMS = (
    "config_before",
    "account_position_risk",
    "balance",
    "positions",
    "orders_pending",
    "algo_conditional",
    "algo_oco",
    "algo_trigger",
    "algo_move_order_stop",
    "fills_history",
    "bills_archive",
    "orders_history_archive",
    "config_after",
)
CURSORS = {
    "orders_pending": "ordId",
    "algo_conditional": "algoId",
    "algo_oco": "algoId",
    "algo_trigger": "algoId",
    "algo_move_order_stop": "algoId",
    "fills_history": "billId",
    "bills_archive": "billId",
    "orders_history_archive": "ordId",
}


def ms(moment=NOW - timedelta(seconds=1)):
    delta = moment - datetime(1970, 1, 1, tzinfo=UTC)
    return str(
        delta.days * 86400000 + delta.seconds * 1000 + delta.microseconds // 1000
    )


def plan(**changes):
    return module.DemoAccountCapturePlan(
        **{
            "plan_id": "synthetic-demo-account-capture",
            "created_at": NOW,
            "expected_uid": UID,
            "expected_main_uid": MAIN_UID,
            "session_binding_id": "synthetic-private-session",
            "settlement_currency": "USDT",
            "history_start": NOW - timedelta(days=7),
            "history_end": NOW - timedelta(seconds=1),
            **changes,
        }
    )


def wire(data, **changes):
    return json.dumps({"code": "0", "msg": "", "data": data, **changes}).encode()


def config(**changes):
    return {
        "uid": UID,
        "mainUid": MAIN_UID,
        "acctLv": "2",
        "posMode": "net_mode",
        **changes,
    }


def row(stream, identifier=None, **changes):
    """Documented shapes, deliberately synthetic and never exchange samples."""
    if identifier is None:
        identifier = {
            "algo_conditional": "910",
            "algo_oco": "920",
            "algo_trigger": "930",
            "algo_move_order_stop": "940",
        }.get(stream, "900")
    common = {"instId": INSTRUMENT, "instType": "SWAP", "posSide": "net"}
    if stream in {"config_before", "config_after"}:
        value = config()
    elif stream == "account_position_risk":
        value = {"ts": ms(), "balData": [], "posData": []}
    elif stream == "balance":
        value = {"uTime": ms(), "totalEq": "1000", "availEq": "800", "details": []}
    elif stream == "positions":
        value = {
            **common,
            "posId": identifier,
            "ccy": "USDT",
            "mgnMode": "cross",
            "pos": "2",
            "avgPx": "100",
            "lever": "3",
            "markPx": "101",
            "cTime": ms(NOW - timedelta(hours=1)),
            "uTime": ms(),
        }
    elif stream in {"orders_pending", "orders_history_archive"}:
        value = {
            **common,
            "ordId": identifier,
            "side": "buy",
            "tdMode": "cross",
            "state": "live" if stream == "orders_pending" else "filled",
            "sz": "2",
            "accFillSz": "0" if stream == "orders_pending" else "2",
            "px": "100",
            "avgPx": "" if stream == "orders_pending" else "100",
            "cTime": ms(NOW - timedelta(hours=1)),
            "uTime": ms(),
        }
    elif stream.startswith("algo_"):
        value = {
            **common,
            "algoId": identifier,
            "ordType": stream.removeprefix("algo_"),
            "side": "sell",
            "state": "live",
            "sz": "2",
            "tdMode": "cross",
            "cTime": ms(NOW - timedelta(hours=1)),
        }
    elif stream == "fills_history":
        value = {
            **common,
            "billId": identifier,
            "ordId": "800",
            "tradeId": "700",
            "side": "buy",
            "fillPx": "100.25",
            "fillSz": "2",
            "fee": "-0.01",
            "feeCcy": "USDT",
            "fillTime": ms(NOW - timedelta(seconds=3)),
            "ts": ms(),
        }
    elif stream == "bills_archive":
        value = {
            "billId": identifier,
            "ccy": "USDT",
            "type": "8",
            "subType": "173",
            "bal": "1000",
            "balChg": "-0.03",
            "fee": "-0.01",
            "pnl": "0",
            "ts": ms(),
            "instId": "",
        }
    else:
        raise AssertionError(f"unknown synthetic stream: {stream}")
    return {**value, **changes}


def observe(body=None, *, selected=None, stream="config_before", **changes):
    selected = plan() if selected is None else selected
    return module.parse_demo_account_observation(
        wire([config()]) if body is None else body,
        **{
            "plan": selected,
            "expected_plan_sha256": module.plan_sha256(selected),
            "stream": stream,
            "request_started_at": NOW + timedelta(milliseconds=10),
            "headers_received_at": NOW + timedelta(milliseconds=11),
            "body_completed_at": NOW + timedelta(milliseconds=12),
            "barrier_completed_at": BARRIER,
            **changes,
        },
    )


def stream_observation(stream, data=None, *, selected=None, **changes):
    selected = plan() if selected is None else selected
    receipt = observe(selected=selected).receipt_sha256
    return observe(
        wire([row(stream)] if data is None else data),
        selected=selected,
        stream=stream,
        identity_receipt_sha256=receipt,
        **changes,
    )


def records(*, selected=None, pages=None, empty=False, stamp_shift=timedelta(0)):
    selected = plan() if selected is None else selected
    pages = {} if pages is None else pages
    result = []
    identity = None
    for stream in STREAMS:
        if stream in pages:
            stream_pages = pages[stream]
        elif stream in CURSORS:
            stream_pages = [[]] if empty else [[row(stream)], []]
        else:
            stream_pages = [[row(stream)]]
        after = None
        previous = None
        for index, data in enumerate(stream_pages):
            at = NOW + timedelta(milliseconds=10 + 3 * len(result)) + stamp_shift
            observation = observe(
                wire(data),
                selected=selected,
                stream=stream,
                page_index=index,
                after=after,
                previous_page_sha256=previous,
                identity_receipt_sha256=identity,
                request_started_at=at,
                headers_received_at=at + timedelta(milliseconds=1),
                body_completed_at=at + timedelta(milliseconds=2),
            )
            result.append(observation)
            if stream == "config_before":
                identity = observation.receipt_sha256
            previous = observation.receipt_sha256
            if data and stream in CURSORS:
                after = data[-1][CURSORS[stream]]
    return selected, tuple(result)


def verify(selected, observations, **changes):
    return module.verify_demo_account_records(
        observations,
        **{
            "plan": selected,
            "expected_plan_sha256": module.plan_sha256(selected),
            "barrier_completed_at": BARRIER,
            **changes,
        },
    )


@pytest.fixture(scope="module")
def packet():
    return verify(*records())


def resign_record(record, field, **changes):
    altered = record.model_copy(update=changes)
    return altered.model_copy(
        update={field: module._digest_record(altered.__dict__, excluded=(field,))}
    )


def test_exact_ordered_stream_contract_and_pinned_plan():
    selected = plan()
    assert module.STREAMS == STREAMS
    assert selected.environment == "demo"
    assert module.plan_sha256(selected) == module.plan_sha256(plan())
    assert module.plan_sha256(selected) != module.plan_sha256(
        plan(expected_uid="700002")
    )
    restored = module.DemoAccountCapturePlan.model_validate_json(
        selected.model_dump_json(round_trip=True), strict=True
    )
    assert restored == selected
    assert module.plan_sha256(restored) == module.plan_sha256(selected)
    with pytest.raises(ValidationError):
        selected.expected_uid = "700002"


@pytest.mark.parametrize("stream", STREAMS)
def test_requests_are_fixed_gets_and_current_account_requests_are_unfiltered(stream):
    request = module.account_request(plan(), stream)
    assert request.method == "GET"
    assert request.origin == "https://www.okx.com"
    assert request.parameters == tuple(sorted(request.parameters))
    params = dict(request.parameters)
    assert "instId" not in params and "ccy" not in params
    assert "before" not in params
    if stream in {
        "config_before",
        "config_after",
        "account_position_risk",
        "balance",
        "positions",
    }:
        assert not params
    elif stream in {"fills_history", "orders_history_archive"}:
        assert params["instType"] == "SWAP"
        assert params["begin"] == ms(plan().history_start)
        assert params["end"] == ms(plan().history_end)
    elif stream == "bills_archive":
        assert "instType" not in params
        assert params["begin"] == ms(plan().history_start)
        assert params["end"] == ms(plan().history_end)
    else:
        assert "instType" not in params
    if stream.startswith("algo_"):
        assert params["ordType"] == stream.removeprefix("algo_")


@pytest.mark.parametrize(
    "stream", ["balance?ccy=USDT", "account_config", "orders", "", None, True]
)
def test_unknown_request_stream_cannot_select_arbitrary_private_endpoint(stream):
    with pytest.raises((ValueError, TypeError)):
        module.account_request(plan(), stream)


@pytest.mark.parametrize(
    "stream",
    ["config_before", "config_after", "balance", "positions", "account_position_risk"],
)
def test_nonpaginated_requests_reject_after_cursor(stream):
    with pytest.raises(ValueError):
        module.account_request(plan(), stream, after="123")


@pytest.mark.parametrize("cursor", ["", "123&instId=ETH-USDT-SWAP", "123\n", 123, True])
def test_cursor_shape_cannot_inject_or_coerce_query(cursor):
    with pytest.raises((ValueError, TypeError)):
        module.account_request(plan(), "fills_history", after=cursor)


@pytest.mark.parametrize(
    "key,value",
    [
        ("environment", "live"),
        ("expected_uid", ""),
        ("expected_uid", 700001),
        ("expected_main_uid", True),
        ("session_binding_id", ""),
        ("settlement_currency", "usdt"),
        ("created_at", NOW.replace(tzinfo=None)),
        ("history_start", NOW),
        ("history_end", NOW + timedelta(seconds=1)),
    ],
)
def test_invalid_plan_is_not_coerced(key, value):
    with pytest.raises(ValueError):
        plan(**{key: value})


@pytest.mark.parametrize("field", ["uid", "mainUid", "acctLv", "posMode"])
@pytest.mark.parametrize("value", [None, "", 1, True])
def test_config_identity_and_modes_require_real_nonempty_strings(field, value):
    with pytest.raises(ValueError):
        observe(wire([config(**{field: value})]))


@pytest.mark.parametrize("field", ["uid", "mainUid", "acctLv", "posMode"])
def test_config_cannot_fabricate_missing_identity_field(field):
    row = config()
    del row[field]
    with pytest.raises(ValueError):
        observe(wire([row]))


@pytest.mark.parametrize(
    "changes",
    [
        {"uid": "700002"},
        {"mainUid": "700003"},
        {"posMode": "unknown"},
        {"acctLv": "unknown"},
    ],
)
def test_wrong_config_account_or_unknown_mode_rejected(changes):
    with pytest.raises(ValueError):
        observe(wire([config(**changes)]))


@pytest.mark.parametrize(
    "body",
    [
        b'{"code":"0","code":"0","msg":"","data":[]}',
        b'{"code":"0","msg":"","data":[],"number":NaN}',
        b'{"code":"0","msg":"","data":[],"number":Infinity}',
        b'{"code":"0","msg":"","data":[],"number":-Infinity}',
        b'{"code":"0","msg":"","data":[',
        b"\xff",
        b"[]",
        b"null",
        b'{"code":0,"msg":"","data":[]}',
        b'{"code":"1","msg":"sensitive upstream detail","data":[]}',
    ],
)
def test_malformed_raw_envelope_never_becomes_account_evidence(body):
    with pytest.raises(ValueError) as caught:
        observe(body)
    assert "sensitive upstream detail" not in str(caught.value)


@pytest.mark.parametrize(
    "key", ["apiKey", "apiSecret", "passphrase", "OK-ACCESS-KEY", "authorization"]
)
def test_secret_fields_are_rejected_without_echoing_value(key):
    secret = "synthetic-never-a-real-secret-value"
    with pytest.raises(ValueError) as caught:
        observe(wire([config(**{key: secret})]))
    assert secret not in str(caught.value)


def test_duplicate_nested_identity_keys_are_not_last_value_wins():
    body = wire([config()]).replace(
        b'"uid": "700001"', b'"uid":"700002","uid":"700001"'
    )
    with pytest.raises(ValueError):
        observe(body)


def test_deep_raw_json_fails_boundedly_instead_of_python_recursion_error():
    body = (
        b'{"code":"0","msg":"","data":[],"nested":'
        + b"[" * 1500
        + b"0"
        + b"]" * 1500
        + b"}"
    )
    with pytest.raises(ValueError):
        observe(body)


@pytest.mark.parametrize(
    "changes",
    [
        {"expected_plan_sha256": "0" * 64},
        {"request_started_at": BARRIER},
        {"request_started_at": BARRIER - timedelta(microseconds=1)},
        {"headers_received_at": NOW + timedelta(milliseconds=9)},
        {"body_completed_at": NOW + timedelta(milliseconds=10)},
        {"request_started_at": NOW.replace(tzinfo=None)},
        {"headers_received_at": NOW.replace(tzinfo=None)},
        {"body_completed_at": NOW.replace(tzinfo=None)},
        {"barrier_completed_at": BARRIER.replace(tzinfo=None)},
        {"page_index": True},
        {"page_index": -1},
        {"page_index": 1},
        {"previous_page_sha256": "0" * 64},
    ],
)
def test_observation_plan_and_all_causal_time_boundaries_are_checked(changes):
    with pytest.raises(ValueError):
        observe(**changes)


@pytest.mark.parametrize("stream", STREAMS[1:])
def test_noninitial_stream_requires_config_before_receipt(stream):
    with pytest.raises(ValueError):
        observe(
            wire([config()]) if stream == "config_after" else wire([]), stream=stream
        )


def test_incomplete_stream_collection_is_not_a_complete_packet():
    selected = plan()
    with pytest.raises(ValueError):
        module.verify_demo_account_records(
            (observe(selected=selected),),
            plan=selected,
            expected_plan_sha256=module.plan_sha256(selected),
            barrier_completed_at=BARRIER,
        )


def test_full_thirteen_stream_capture_is_pinned_but_not_complete_account(packet):
    assert (
        tuple(dict.fromkeys(item.request.stream for item in packet.observations))
        == STREAMS
    )
    assert len(packet.observations) == len(STREAMS) + len(CURSORS)
    assert packet.state == "records_verified_incomplete_account"
    assert packet.account_complete is False
    assert packet.execution_authority is False
    assert packet.source_authenticity_verified is False
    assert packet.plan_sha256 == module.plan_sha256(packet.plan)
    assert packet.barrier_completed_at == BARRIER
    assert packet.completed_at == packet.observations[-1].body_completed_at
    assert packet.row_count == sum(len(part.rows) for part in packet.observations)
    assert {
        "source_authenticity_unverified",
        "cross_source_atomicity_unverified",
        "history_retention_unverified",
        "history_ingestion_watermark_unverified",
        "history_seed_missing",
        "peak_window_evidence_missing",
        "local_uncertain_ledger_missing",
        "instrument_and_correlation_mapping_missing",
        "non_swap_history_not_requested",
    } <= set(packet.incomplete_reasons)
    identity = packet.observations[0]
    assert identity.identity_receipt_sha256 is None
    assert identity.identity_binding == "config_response"
    for observation in packet.observations:
        assert (
            observation.body_sha256
            == hashlib.sha256(observation.response_body).hexdigest()
        )
        assert observation.body_size_bytes == len(observation.response_body)
        assert (
            observation.canonical_sha256
            == hashlib.sha256(observation.canonical_json.encode()).hexdigest()
        )
        assert observation.request_started_at > BARRIER
        if observation is not identity:
            assert observation.identity_receipt_sha256 == identity.receipt_sha256
    for stream in CURSORS:
        pages = [part for part in packet.observations if part.request.stream == stream]
        assert [part.page_index for part in pages] == [0, 1]
        assert not pages[0].terminal and pages[1].terminal
        assert not pages[1].rows
        assert pages[1].after == row(stream)[CURSORS[stream]]
        assert pages[1].previous_page_sha256 == pages[0].receipt_sha256
    with pytest.raises(ValidationError):
        packet.account_complete = True


def test_explicit_first_empty_page_is_a_valid_chain_not_a_complete_account():
    result = verify(*records(empty=True))
    assert len(result.observations) == 13
    assert all(
        part.terminal and not part.rows and part.after is None
        for part in result.observations
        if part.request.stream in CURSORS
    )
    assert result.account_complete is False
    assert "history_retention_unverified" in result.incomplete_reasons


def test_real_multiple_page_receipts_allow_nonconsecutive_ids_not_missing_history_fabrication():
    result = verify(
        *records(
            pages={
                "fills_history": [
                    [row("fills_history", "900"), row("fills_history", "450")],
                    [row("fills_history", "3")],
                    [],
                ],
            }
        )
    )
    pages = [
        part for part in result.observations if part.request.stream == "fills_history"
    ]
    assert [part.after for part in pages] == [None, "450", "3"]
    assert [part.terminal for part in pages] == [False, False, True]
    assert [r.row_id for part in pages for r in part.rows] == ["900", "450", "3"]
    assert result.account_complete is False


def test_raw_json_whitespace_and_order_equivalence_changes_raw_pin_only():
    body = wire([config()])
    equivalent = (
        b" \n" * 100
        + json.dumps(
            {"data": [dict(reversed(tuple(config().items())))], "msg": "", "code": "0"},
            indent=3,
        ).encode()
        + b"\t " * 100
    )
    first = observe(body)
    second = observe(equivalent)
    assert first.rows == second.rows
    assert first.canonical_json == second.canonical_json
    assert first.canonical_sha256 == second.canonical_sha256
    assert first.body_sha256 != second.body_sha256
    assert first.receipt_sha256 != second.receipt_sha256
    assert second.response_body == equivalent


def test_config_source_clock_is_absent_not_receipt_time_and_raw_identity_is_retained():
    record = observe()
    assert json.loads(record.rows[0].canonical_json) == config()
    assert not record.rows[0].source_times
    assert all("ts" not in json.loads(part.canonical_json) for part in record.rows)


def test_fill_match_and_bill_generation_times_are_not_conflated():
    fill = stream_observation("fills_history").rows[0]
    source_times = {item.path.rsplit(".", 1)[-1]: item for item in fill.source_times}
    assert source_times["fillTime"].value == NOW - timedelta(seconds=3)
    assert source_times["fillTime"].semantics == "trade_match"
    assert source_times["ts"].value == NOW - timedelta(seconds=1)
    assert source_times["ts"].semantics == "record_generation"
    assert source_times["fillTime"].raw == ms(NOW - timedelta(seconds=3))
    assert source_times["ts"].raw == ms()


@pytest.mark.parametrize("missing", [None, ""])
def test_optional_numbers_and_source_times_stay_unknown_not_zero_or_now(missing):
    value = row("positions")
    for key in ("pos", "avgPx", "cTime", "uTime"):
        if missing is None:
            del value[key]
        else:
            value[key] = missing
    captured = stream_observation("positions", [value]).rows[0]
    numbers = {item.path.rsplit(".", 1)[-1]: item for item in captured.numbers}
    moments = {item.path.rsplit(".", 1)[-1]: item for item in captured.source_times}
    assert numbers["pos"].value is None and numbers["avgPx"].value is None
    assert moments["cTime"].value is None and moments["uTime"].value is None
    gaps = {name.rsplit(".", 1)[-1] for name in captured.missing_fields}
    assert {"pos", "avgPx", "cTime", "uTime"} <= gaps
    assert json.loads(captured.canonical_json) == value


def test_explicit_zero_fee_and_pnl_are_not_missing():
    captured = stream_observation(
        "bills_archive", [row("bills_archive", fee="0", pnl="0")]
    ).rows[0]
    numbers = {item.path.rsplit(".", 1)[-1]: item for item in captured.numbers}
    assert numbers["fee"].value == numbers["pnl"].value == Decimal(0)
    assert numbers["fee"].raw == numbers["pnl"].raw == "0"
    assert not {"fee", "pnl"} & {
        path.rsplit(".", 1)[-1] for path in captured.missing_fields
    }


@pytest.mark.parametrize(
    "value",
    [
        True,
        False,
        0.5,
        1,
        "NaN",
        "Infinity",
        "1e5",
        " 1",
        "1\n",
        "0.000000000000000000001",
    ],
)
def test_known_numeric_raw_fields_are_exact_bounded_decimal_strings(value):
    with pytest.raises(ValueError):
        stream_observation("positions", [row("positions", pos=value)])


@pytest.mark.parametrize(
    "value", [True, 1.0, 1, "NaN", "1.5", "-1", "0", "999999999999999", " 123"]
)
def test_known_source_timestamps_do_not_coerce_invalid_values(value):
    with pytest.raises(ValueError):
        stream_observation("positions", [row("positions", uTime=value)])


@pytest.mark.parametrize("stream", ("positions", *CURSORS))
@pytest.mark.parametrize("value", [None, "", True, 900])
def test_real_row_identifiers_are_mandatory_not_receipt_generated(stream, value):
    key = "posId" if stream == "positions" else CURSORS[stream]
    with pytest.raises(ValueError):
        stream_observation(stream, [row(stream, **{key: value})])


@pytest.mark.parametrize(
    "field,value",
    [
        ("instId", ""),
        ("instType", ""),
        ("ccy", ""),
        ("posSide", "unknown"),
        ("mgnMode", "unknown"),
    ],
)
def test_current_position_required_identity_and_mode_cannot_be_missing(field, value):
    with pytest.raises(ValueError):
        stream_observation("positions", [row("positions", **{field: value})])


@pytest.mark.parametrize("stream", ["fills_history", "orders_history_archive"])
def test_swap_history_cannot_be_labeled_as_other_product(stream):
    with pytest.raises(ValueError):
        stream_observation(stream, [row(stream, instType="FUTURES")])


def test_freeze_replay_requires_both_external_pins_and_preserves_full_wire_bytes(
    packet,
):
    frozen = module.freeze_demo_account_packet(
        packet, expected_plan_sha256=packet.plan_sha256
    )
    assert type(frozen.payload) is bytes
    assert frozen.sha256 == hashlib.sha256(frozen.payload).hexdigest()
    restored = module.verify_demo_account_packet(
        frozen.payload,
        expected_sha256=frozen.sha256,
        expected_plan_sha256=packet.plan_sha256,
    )
    assert restored == packet
    assert (
        restored.observations[0].response_body == packet.observations[0].response_body
    )
    assert not restored.account_complete and not restored.execution_authority
    assert (
        module.DemoAccountPacket.model_validate_json(
            packet.model_dump_json(round_trip=True), strict=True
        )
        == packet
    )
    with pytest.raises(ValueError):
        module.freeze_demo_account_packet(packet, expected_plan_sha256="0" * 64)
    with pytest.raises(ValueError):
        module.verify_demo_account_packet(
            frozen.payload,
            expected_sha256="0" * 64,
            expected_plan_sha256=packet.plan_sha256,
        )
    with pytest.raises(ValueError):
        module.verify_demo_account_packet(
            frozen.payload, expected_sha256=frozen.sha256, expected_plan_sha256="0" * 64
        )
    with pytest.raises(ValueError):
        module.verify_demo_account_packet(
            frozen.payload + b" ",
            expected_sha256=frozen.sha256,
            expected_plan_sha256=packet.plan_sha256,
        )


@pytest.mark.parametrize(
    "field", ["account_complete", "execution_authority", "source_authenticity_verified"]
)
@pytest.mark.parametrize("value", [True, 0, 1, "false"])
def test_model_copy_authority_claims_are_rejected_before_freeze(packet, field, value):
    with pytest.raises(ValueError):
        module.freeze_demo_account_packet(
            packet.model_copy(update={field: value}),
            expected_plan_sha256=packet.plan_sha256,
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("session_binding_id", "other-session"),
        ("expected_uid", "700002"),
        ("expected_main_uid", "700003"),
    ],
)
def test_foreign_plan_or_same_layout_session_cannot_reuse_receipts(
    packet, field, value
):
    selected = plan(**{field: value})
    with pytest.raises(ValueError):
        verify(selected, packet.observations)


@pytest.mark.parametrize(
    "field,value",
    [
        ("uid", "700002"),
        ("mainUid", "700003"),
        ("acctLv", "3"),
        ("posMode", "long_short_mode"),
    ],
)
def test_config_after_must_match_initial_account_identity_and_modes(field, value):
    with pytest.raises(ValueError):
        verify(*records(pages={"config_after": [[config(**{field: value})]]}))


@pytest.mark.parametrize("stream", STREAMS)
def test_every_required_stream_must_be_present(packet, stream):
    changed = tuple(
        part for part in packet.observations if part.request.stream != stream
    )
    with pytest.raises(ValueError):
        verify(packet.plan, changed)


@pytest.mark.parametrize("stream", tuple(CURSORS))
def test_short_nonempty_page_does_not_prove_terminal_history_or_current_orders(
    packet, stream
):
    changed = tuple(
        part
        for part in packet.observations
        if not (part.request.stream == stream and part.terminal)
    )
    with pytest.raises(ValueError):
        verify(packet.plan, changed)


@pytest.mark.parametrize(
    "defect",
    [
        "reordered",
        "repeated_stream",
        "repeated_terminal",
        "gap_page_index",
        "wrong_previous",
        "wrong_after",
        "foreign_identity",
        "foreign_session",
        "overlapping_clock",
        "wrong_barrier",
        "unknown_query",
    ],
)
def test_receipt_chain_cannot_be_reordered_reused_or_rehashed_to_hide_binding_defects(
    packet, defect
):
    observations = list(packet.observations)
    index = next(
        i
        for i, item in enumerate(observations)
        if item.request.stream == "fills_history" and item.terminal
    )
    part = observations[index]
    if defect == "reordered":
        observations[1], observations[2] = observations[2], observations[1]
    elif defect == "repeated_stream":
        observations.insert(2, observations[1])
    elif defect == "repeated_terminal":
        observations.insert(index + 1, part)
    else:
        changes = {
            "gap_page_index": {"page_index": 2},
            "wrong_previous": {"previous_page_sha256": "0" * 64},
            "wrong_after": {"after": "899"},
            "foreign_identity": {"identity_receipt_sha256": "0" * 64},
            "foreign_session": {"session_binding_id": "other-session"},
            "overlapping_clock": {
                "request_started_at": observations[index - 1].body_completed_at
                - timedelta(microseconds=1)
            },
            "wrong_barrier": {"barrier_completed_at": BARRIER - timedelta(seconds=1)},
            "unknown_query": {
                "request": part.request.model_copy(
                    update={"parameters": (("ccy", "USDT"),)}
                )
            },
        }[defect]
        observations[index] = resign_record(part, "receipt_sha256", **changes)
    with pytest.raises(ValueError):
        verify(packet.plan, tuple(observations))


@pytest.mark.parametrize("second", ["900", "901"])
def test_pagination_after_cursor_must_strictly_advance_without_duplicates(second):
    with pytest.raises(ValueError):
        verify(
            *records(
                pages={
                    "fills_history": [
                        [row("fills_history", "900")],
                        [row("fills_history", second)],
                        [],
                    ]
                }
            )
        )


@pytest.mark.parametrize("changed", [False, True])
def test_duplicate_row_ids_fail_even_when_one_copy_has_conflicting_values(changed):
    duplicate = row("fills_history", fee="-0.02" if changed else "-0.01")
    with pytest.raises(ValueError):
        stream_observation("fills_history", [row("fills_history"), duplicate])


def test_no_record_or_raw_data_changes_are_laundered_by_top_packet_rehash(packet):
    part = packet.observations[2]
    changed_row = part.rows[0].model_copy(update={"canonical_json": "{}"})
    changed_part = resign_record(part, "receipt_sha256", rows=(changed_row,))
    altered = resign_record(
        packet,
        "packet_sha256",
        observations=(*packet.observations[:2], changed_part, *packet.observations[3:]),
    )
    with pytest.raises(ValueError):
        module.freeze_demo_account_packet(
            altered, expected_plan_sha256=packet.plan_sha256
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("max_pages_per_stream", 1),
        ("max_total_pages", 13),
        ("max_total_rows", 1),
        ("max_total_bytes", 1024),
    ],
)
def test_total_resource_budget_exhaustion_is_not_partial_success(field, value):
    with pytest.raises(ValueError):
        verify(*records(selected=plan(**{field: value})))


def test_page_row_budget_is_enforced_before_terminal_receipt():
    selected = plan(page_size=1)
    with pytest.raises(ValueError):
        stream_observation(
            "fills_history",
            [row("fills_history", "900"), row("fills_history", "899")],
            selected=selected,
        )


def test_response_byte_budget_and_request_duration_are_enforced():
    with pytest.raises(ValueError):
        observe(b" " * 70000 + wire([config()]))
    with pytest.raises(ValueError):
        observe(body_completed_at=NOW + timedelta(seconds=6))


@pytest.mark.parametrize(
    "field",
    [
        "page_size",
        "max_pages_per_stream",
        "max_total_pages",
        "max_total_rows",
        "max_response_bytes",
        "max_total_bytes",
        "max_request_seconds",
        "max_batch_seconds",
    ],
)
def test_budget_integer_fields_reject_boolean_model_copy(field):
    with pytest.raises(ValueError):
        module.plan_sha256(plan().model_copy(update={field: True}))


def test_equivalent_utc_offset_is_normalized_without_changing_request_window():
    offset = timezone(timedelta(hours=8))
    selected = plan(
        created_at=NOW.astimezone(offset),
        history_start=(NOW - timedelta(days=7)).astimezone(offset),
        history_end=(NOW - timedelta(seconds=1)).astimezone(offset),
    )
    assert selected == plan()
    assert module.plan_sha256(selected) == module.plan_sha256(plan())
    assert module.account_request(selected, "fills_history") == module.account_request(
        plan(), "fills_history"
    )


def test_history_query_boundaries_are_inclusive_and_order_creation_is_not_update_time():
    selected = plan()
    for moment in (selected.history_start, selected.history_end):
        captured = stream_observation(
            "bills_archive", [row("bills_archive", ts=ms(moment))], selected=selected
        )
        assert captured.rows[0].source_times[0].value == moment
    # Archive begin/end binds cTime; a later valid update does not move the order
    # into another history window, nor fabricate a different creation timestamp.
    captured = stream_observation(
        "orders_history_archive",
        [
            row(
                "orders_history_archive",
                cTime=ms(selected.history_start),
                uTime=ms(NOW),
            )
        ],
        selected=selected,
    )
    source_times = {part.path: part for part in captured.rows[0].source_times}
    assert source_times["cTime"].value == selected.history_start
    assert source_times["cTime"].semantics == "record_creation"
    assert source_times["uTime"].value == NOW
    assert source_times["uTime"].semantics == "source_update"


@pytest.mark.parametrize(
    "stream,key",
    [
        ("fills_history", "ts"),
        ("bills_archive", "ts"),
        ("orders_history_archive", "cTime"),
    ],
)
@pytest.mark.parametrize("which", ["before", "after"])
def test_history_rows_outside_the_exact_requested_window_are_rejected(
    stream, key, which
):
    selected = plan()
    moment = (
        selected.history_start - timedelta(milliseconds=1)
        if which == "before"
        else selected.history_end + timedelta(milliseconds=1)
    )
    with pytest.raises(ValueError):
        stream_observation(
            stream, [row(stream, **{key: ms(moment)})], selected=selected
        )


def test_source_clock_lifecycle_and_nested_future_clock_are_checked():
    with pytest.raises(ValueError):
        stream_observation(
            "positions",
            [row("positions", cTime=ms(NOW), uTime=ms(NOW - timedelta(seconds=1)))],
        )
    with pytest.raises(ValueError):
        stream_observation(
            "balance",
            [
                row(
                    "balance",
                    details=[
                        {
                            "ccy": "USDT",
                            "uTime": ms(NOW + timedelta(seconds=1)),
                            "eq": "1000",
                        }
                    ],
                )
            ],
        )


def test_equal_header_body_clocks_and_adjacent_request_boundary_are_valid():
    same = NOW + timedelta(milliseconds=10)
    captured = observe(
        request_started_at=same, headers_received_at=same, body_completed_at=same
    )
    assert captured.request_started_at == captured.body_completed_at
    selected, observations = records(empty=True)
    parts = list(observations)
    previous = parts[-2]
    current = parts[-1]
    parts[-1] = observe(
        current.response_body,
        selected=selected,
        stream="config_after",
        identity_receipt_sha256=parts[0].receipt_sha256,
        request_started_at=previous.body_completed_at,
        headers_received_at=previous.body_completed_at,
        body_completed_at=previous.body_completed_at,
    )
    assert verify(selected, tuple(parts)).completed_at == previous.body_completed_at


def test_batch_deadline_checks_full_capture_not_each_individual_request():
    selected, observations = records(selected=plan(max_batch_seconds=1), empty=True)
    parts = list(observations)
    part = parts[-1]
    late = NOW + timedelta(seconds=2)
    parts[-1] = observe(
        part.response_body,
        selected=selected,
        stream="config_after",
        identity_receipt_sha256=parts[0].receipt_sha256,
        request_started_at=late,
        headers_received_at=late,
        body_completed_at=late,
    )
    with pytest.raises(ValueError):
        verify(selected, tuple(parts))


@pytest.mark.parametrize(
    "bad",
    [
        "bool_authority",
        "float",
        "nonfinite",
        "duplicate_key",
        "unknown_field",
        "noncanonical",
    ],
)
def test_new_external_hash_does_not_make_malformed_serialized_packet_valid(packet, bad):
    frozen = module.freeze_demo_account_packet(
        packet, expected_plan_sha256=packet.plan_sha256
    )
    value = json.loads(frozen.payload)
    if bad == "bool_authority":
        value["execution_authority"] = 0
    elif bad == "float":
        value["row_count"] = 13.0
    elif bad == "nonfinite":
        value["row_count"] = float("nan")
    elif bad == "unknown_field":
        value["undeclared"] = "payload"
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    if bad == "duplicate_key":
        payload = payload.replace(b'"row_count":', b'"row_count":1,"row_count":', 1)
    elif bad == "noncanonical":
        payload += b" "
    with pytest.raises(ValueError):
        module.verify_demo_account_packet(
            payload,
            expected_sha256=hashlib.sha256(payload).hexdigest(),
            expected_plan_sha256=packet.plan_sha256,
        )


@pytest.mark.parametrize(
    "kind",
    [
        "plan",
        "request",
        "row",
        "number",
        "subclass",
        "tuple_subclass",
        "iterator",
        "extra",
        "raw_scalar",
    ],
)
def test_nested_dirty_records_are_denied_without_calling_custom_serializers_or_iterators(
    packet, kind
):
    touched = []

    class Trap(BaseModel):
        @model_serializer
        def serialize(self):
            touched.append("serializer")
            return 1

    class TrapTuple(tuple):
        def __iter__(self):
            touched.append("iterator")
            raise AssertionError("untrusted iterator ran")

    class TrapBytes(bytes):
        def __bytes__(self):
            touched.append("bytes")
            raise AssertionError("untrusted byte conversion ran")

    if kind == "plan":
        altered = packet.model_copy(
            update={"plan": packet.plan.model_copy(update={"page_size": Trap()})}
        )
    elif kind == "subclass":
        subclass = create_model(
            "DerivedAccountPacket", __base__=module.DemoAccountPacket
        )
        altered = subclass.model_construct(**packet.__dict__)
    elif kind == "tuple_subclass":
        altered = packet.model_copy(
            update={"observations": TrapTuple(packet.observations)}
        )
    elif kind == "iterator":

        def generate():
            touched.append("generator")
            yield packet.observations[0]

        altered = packet.model_copy(update={"observations": generate()})
    elif kind == "extra":
        altered = packet.model_copy(update={"unexpected": Trap()})
    else:
        part = packet.observations[2]
        if kind == "request":
            part = part.model_copy(
                update={
                    "request": part.request.model_copy(
                        update={"parameters": (("limit", Trap()),)}
                    )
                }
            )
        elif kind == "row":
            part = part.model_copy(update={"rows": (Trap(),)})
        elif kind == "number":
            number = part.rows[0].numbers[0].model_copy(update={"value": Trap()})
            part = part.model_copy(
                update={
                    "rows": (part.rows[0].model_copy(update={"numbers": (number,)}),)
                }
            )
        else:
            part = part.model_copy(
                update={"response_body": TrapBytes(part.response_body)}
            )
        altered = packet.model_copy(
            update={
                "observations": (
                    *packet.observations[:2],
                    part,
                    *packet.observations[3:],
                )
            }
        )
    with pytest.raises(ValueError):
        module.freeze_demo_account_packet(
            altered, expected_plan_sha256=packet.plan_sha256
        )
    assert touched == []


def test_parser_rejects_custom_byte_scalar_before_conversion():
    touched = []

    class CustomBytes(bytes):
        def decode(self, *args, **kwargs):
            touched.append(True)
            raise AssertionError("custom decode must not execute")

    with pytest.raises(ValueError):
        observe(CustomBytes(wire([config()])))
    assert not touched


@pytest.mark.parametrize("field", ["uid", "mainUid"])
@pytest.mark.parametrize("nested", [False, True])
def test_any_present_raw_account_identity_must_match_even_inside_balance_details(
    field, nested
):
    value = row("balance")
    if nested:
        value["details"] = [{"ccy": "USDT", "eq": "1000", field: "999999"}]
    else:
        value[field] = "999999"
    with pytest.raises(module.AccountCaptureError):
        stream_observation("balance", [value])


def test_correct_present_nested_identity_is_retained_without_inventing_missing_identity():
    value = row(
        "balance",
        uid=UID,
        mainUid=MAIN_UID,
        details=[{"ccy": "USDT", "eq": "1000", "uid": UID}],
    )
    captured = stream_observation("balance", [value])
    assert json.loads(captured.rows[0].canonical_json) == value
    assert captured.identity_binding == "request_context"


def test_same_algo_id_in_distinct_type_queries_cannot_claim_disjoint_inventory():
    with pytest.raises(
        module.AccountCaptureError, match="conflicting_algo_type_identity"
    ):
        verify(*records(pages={"algo_oco": [[row("algo_oco", "910")], []]}))


@pytest.mark.parametrize(
    "location", ["extra", "private", "nested_extra", "nested_private", "scalar"]
)
def test_hidden_metadata_and_opaque_scalar_never_run_bool_or_attribute_access(
    packet, location
):
    touched = []

    class Opaque:
        def __bool__(self):
            touched.append("bool")
            raise AssertionError("untrusted truthiness ran")

        def __getattribute__(self, name):
            touched.append(name)
            raise AssertionError("untrusted attribute lookup ran")

    altered = packet.model_copy()
    if location == "scalar":
        altered = packet.model_copy(
            update={"plan": packet.plan.model_copy(update={"page_size": Opaque()})}
        )
    else:
        owner = altered
        if location.startswith("nested_"):
            owner = packet.plan.model_copy()
            altered = packet.model_copy(update={"plan": owner})
        slot = (
            "__pydantic_private__"
            if location.endswith("private")
            else "__pydantic_extra__"
        )
        object.__setattr__(owner, slot, Opaque())
    with pytest.raises(module.AccountCaptureError):
        module.freeze_demo_account_packet(
            altered, expected_plan_sha256=packet.plan_sha256
        )
    assert touched == []


def test_custom_scalar_metaclass_hash_and_equality_are_never_called(packet):
    touched = []

    class UnsafeMeta(type):
        def __hash__(cls):
            touched.append("class_hash")
            raise AssertionError("untrusted class hash ran")

        def __eq__(cls, other):
            touched.append("class_equality")
            raise AssertionError("untrusted class equality ran")

    class Opaque(metaclass=UnsafeMeta):
        pass

    altered = packet.model_copy(
        update={"plan": packet.plan.model_copy(update={"page_size": Opaque()})}
    )
    with pytest.raises(module.AccountCaptureError):
        module.freeze_demo_account_packet(
            altered, expected_plan_sha256=packet.plan_sha256
        )
    assert touched == []


def test_custom_timezone_metaclass_is_rejected_before_hash_equality_or_offset():
    touched = []

    class UnsafeMeta(type):
        def __hash__(cls):
            touched.append("class_hash")
            raise AssertionError("untrusted timezone class hash ran")

        def __eq__(cls, other):
            touched.append("class_equality")
            raise AssertionError("untrusted timezone class equality ran")

    class UnsafeTimezone(tzinfo, metaclass=UnsafeMeta):
        def utcoffset(self, value):
            touched.append("offset")
            raise AssertionError("untrusted timezone offset ran")

    altered = plan().model_copy(
        update={"created_at": NOW.replace(tzinfo=UnsafeTimezone())}
    )
    with pytest.raises(module.AccountCaptureError):
        module.plan_sha256(altered)
    assert touched == []


@pytest.mark.parametrize("missing", (False, True))
def test_bill_timestamp_is_balance_update_not_record_generation(missing):
    value = row("bills_archive")
    if missing:
        value.pop("ts")
    observation = stream_observation("bills_archive", [value])
    stamp = next(part for part in observation.rows[0].source_times if part.path == "ts")
    assert stamp.semantics == "source_update"
    assert stamp.value == (None if missing else NOW - timedelta(seconds=1))


def test_module_has_no_transport_credentials_settings_database_or_execution_wiring():
    syntax = ast.parse(inspect.getsource(module))
    imports = {
        (node.module or "").split(".")[0]
        for node in ast.walk(syntax)
        if isinstance(node, ast.ImportFrom)
    }
    imports.update(
        alias.name.split(".")[0]
        for node in ast.walk(syntax)
        if isinstance(node, ast.Import)
        for alias in node.names
    )
    assert not imports & {
        "os",
        "socket",
        "httpx",
        "requests",
        "subprocess",
        "sqlalchemy",
    }
    names = {node.id for node in ast.walk(syntax) if isinstance(node, ast.Name)}
    assert not names & {
        "get_settings",
        "OkxDemoService",
        "AsyncSessionFactory",
        "PortfolioRiskSnapshot",
        "place_order",
    }
