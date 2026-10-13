"""Synthetic fee-source replay only; no credentials, network or order writes."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_capture_journal as journal
from app.trade_qualification import account_swap_fee_source_v1 as fees
from tests.unit.test_qualification_account_capture import INSTRUMENT, NOW, row, wire
from tests.unit.test_qualification_account_capture_v7 import v7_observations, v7_plan


def account_packet(*, instrument_changes=None):
    selected = v7_plan()
    values = {
        "tickSz": "0.1",
        "groupId": "2",
        "instFamily": "BTC-USDT",
        **({} if instrument_changes is None else instrument_changes),
    }
    packet = capture.verify_demo_account_records(
        v7_observations(
            selected,
            pages={"account_instruments": [[row("account_instruments", **values)]]},
        ),
        plan=selected,
        expected_plan_sha256=capture.plan_sha256(selected),
        barrier_completed_at=NOW - timedelta(seconds=2),
    )
    return capture.freeze_demo_account_packet(
        packet, expected_plan_sha256=packet.plan_sha256
    ), packet


def fee_row(*, group=None, **changes):
    return {
        "instType": "SWAP",
        "ts": str(int((NOW + timedelta(seconds=1, milliseconds=1)).timestamp() * 1000)),
        "feeGroup": [
            {"groupId": "1", "maker": "-0.0002", "taker": "-0.0005"},
            {"groupId": "2", "maker": "0.0001", "taker": "-0.0006"},
        ]
        if group is None
        else group,
        # These deprecated fields must never override the selected feeGroup.
        "makerU": "-0.008",
        "takerU": "-0.009",
        **changes,
    }


def observation(*, body=None, **changes):
    start = NOW + timedelta(seconds=1)
    return fees.SuppliedSwapFeeObservation(
        **{
            "raw_body": wire([fee_row()]) if body is None else body,
            "origin": "https://openapi.okx.com",
            "method": "GET",
            "endpoint": fees.FEE_ENDPOINT,
            "parameters": (("instFamily", "BTC-USDT"), ("instType", "SWAP")),
            "request_started_at": start,
            "headers_received_at": start + timedelta(milliseconds=2),
            "body_completed_at": start + timedelta(milliseconds=3),
            "barrier_completed_at": NOW - timedelta(seconds=2),
            "status_code": 200,
            "content_type": "application/json",
            "content_encoding": "identity",
            "redirected": False,
            **changes,
        }
    )


def derive(*, frozen=None, packet=None, observed=None, **changes):
    if frozen is None or packet is None:
        frozen, packet = account_packet()
    if observed is None:
        observed = observation()
    pins = {
        "instrument_id": INSTRUMENT,
        "expected_account_packet_sha256": frozen.sha256,
        "expected_account_plan_sha256": packet.plan_sha256,
        "expected_fee_raw_sha256": journal.digest(observed.raw_body),
        "expected_fee_observation_sha256": fees.supplied_observation_sha256(observed),
        **changes,
    }
    return fees.derive_supplied_swap_fee_source(frozen.payload, observed, **pins)


def test_raw_fee_group_matches_original_instrument_and_receipt_replays_exactly():
    frozen, packet = account_packet()
    supplied = observation()
    first = derive(frozen=frozen, packet=packet, observed=supplied)
    receipt = json.loads(first.receipt_json)
    assert first == derive(frozen=frozen, packet=packet, observed=supplied)
    assert first.raw_fee_body == supplied.raw_body
    assert first.signed_maker_rate == Decimal("0.0001")
    assert first.signed_taker_rate == Decimal("-0.0006")
    assert receipt["instrument_group_id"] == "2"
    assert receipt["signed_maker_rate"] == {
        "raw": "0.0001",
        "exact": {"numerator": "1", "denominator": "10000"},
        "meaning": "rebate",
    }
    assert receipt["signed_taker_rate"]["meaning"] == "commission"
    assert receipt["fee_ts"]["semantics"] == "data_return_time_not_trade_match_or_asof"
    assert receipt["account_instruments_row_sha256"] == journal.digest(
        next(
            page.rows[0].canonical_json.encode()
            for page in packet.observations
            if page.request.stream == "account_instruments"
        )
    )
    assert receipt["fee_raw_body_sha256"] == journal.digest(supplied.raw_body)
    assert receipt["fee_observation_sha256"] == fees.supplied_observation_sha256(
        supplied
    )
    assert receipt["admission"] == "DENY"
    assert not any(
        (
            first.registered_site_proven,
            first.source_authenticity_verified,
            first.account_complete,
            first.execution_authority,
        )
    )
    assert (
        fees.verify_supplied_swap_fee_receipt(
            first.receipt_json,
            expected_receipt_sha256=first.receipt_sha256,
            original_account_packet=frozen.payload,
            fee_observation=supplied,
            instrument_id=INSTRUMENT,
            expected_account_packet_sha256=frozen.sha256,
            expected_account_plan_sha256=packet.plan_sha256,
            expected_fee_raw_sha256=journal.digest(supplied.raw_body),
            expected_fee_observation_sha256=fees.supplied_observation_sha256(supplied),
        )
        == first
    )


def test_documented_large_instrument_code_does_not_break_replayed_mapping():
    frozen, packet = account_packet(instrument_changes={"instIdCode": 2**50})
    result = derive(frozen=frozen, packet=packet)
    assert result.group_id == "2"
    assert result.execution_authority is False


@pytest.mark.parametrize(
    "instrument_changes",
    [
        {"groupId": None},
        {"groupId": ""},
        {"groupId": "0"},
        {"groupId": "2.0"},
        {"instFamily": None},
        {"instFamily": ""},
        {"instFamily": "BTC-USD"},
        {"instFamily": "ETH-USDT"},
    ],
)
def test_missing_or_conflicting_original_instrument_mapping_denies(instrument_changes):
    frozen, packet = account_packet(instrument_changes=instrument_changes)
    with pytest.raises(fees.SwapFeeSourceError):
        derive(frozen=frozen, packet=packet)


@pytest.mark.parametrize(
    "groups",
    [
        [],
        [{"groupId": "1", "maker": "-0.0002", "taker": "-0.0005"}],
        [
            {"groupId": "2", "maker": "0.0001", "taker": "-0.0006"},
            {"groupId": "2", "maker": "0.0002", "taker": "-0.0006"},
        ],
        [{"groupId": "2", "maker": "0.0001"}],
        [{"groupId": "2", "maker": "0.0001", "taker": "-0.0006", "unreviewed": "x"}],
        [{"groupId": "2", "maker": "0.0001", "taker": "-0.0006", "elpMaker": None}],
    ],
)
def test_missing_duplicate_or_unreviewed_fee_group_denies(groups):
    with pytest.raises(fees.SwapFeeSourceError):
        derive(observed=observation(body=wire([fee_row(group=groups)])))


@pytest.mark.parametrize(
    "rate",
    [
        None,
        "",
        "-0",
        "-0.0",
        "0.02",
        "-0.02",
        "1e-3",
        "NaN",
        "+0.001",
        " 0.001",
        "01",
        0.001,
        True,
    ],
)
def test_malformed_or_out_of_bound_rate_denies(rate):
    groups = [{"groupId": "2", "maker": rate, "taker": "-0.0006"}]
    with pytest.raises(fees.SwapFeeSourceError):
        derive(observed=observation(body=wire([fee_row(group=groups)])))


@pytest.mark.parametrize(
    "changes",
    [
        {
            "parameters": (
                ("groupId", "2"),
                ("instFamily", "BTC-USDT"),
                ("instType", "SWAP"),
            )
        },
        {"parameters": (("groupId", "2"), ("instType", "SWAP"))},
        {"parameters": (("instFamily", "ETH-USDT"), ("instType", "SWAP"))},
        {"origin": "https://www.okx.com"},
        {"status_code": 301},
        {"redirected": True},
        {"content_type": "text/html"},
        {"content_encoding": "gzip"},
        {"request_started_at": NOW - timedelta(seconds=1)},
        {"headers_received_at": NOW + timedelta(milliseconds=1)},
        {"body_completed_at": NOW + timedelta(seconds=10)},
    ],
)
def test_wrong_query_origin_http_or_causal_order_denies(changes):
    with pytest.raises(fees.SwapFeeSourceError):
        derive(observed=observation(**changes))


@pytest.mark.parametrize(
    "body",
    [
        wire([]),
        wire([fee_row(), fee_row()]),
        wire([fee_row(instType="SPOT")]),
        wire([fee_row(ts="")]),
        wire([fee_row(ts="1597026383085")]),
        wire([fee_row(ts="1762970000000")]),
        wire(
            [
                fee_row(
                    ts=str(
                        int(
                            (NOW + timedelta(seconds=1, milliseconds=5)).timestamp()
                            * 1000
                        )
                    )
                )
            ]
        ),
        wire([fee_row(extra="unreviewed")]),
        b'{"code":"0","msg":"","data":[],"code":"0"}',
        b'{"code":"0","msg":"","data":1.0}',
    ],
)
def test_bad_envelope_timestamp_duplicate_or_unknown_data_denies(body):
    with pytest.raises(fees.SwapFeeSourceError):
        derive(observed=observation(body=body))


def test_correctly_reported_zero_is_not_evidence_of_exchange_zero_fee_exception():
    groups = [{"groupId": "2", "maker": "0", "taker": "0"}]
    receipt = json.loads(
        derive(observed=observation(body=wire([fee_row(group=groups)]))).receipt_json
    )
    assert receipt["signed_taker_rate"]["meaning"] == "zero_reported_unverified"
    assert "zero_fee_override" in receipt["unverified"]
    assert receipt["execution_authority"] is False


def test_fee_response_too_late_for_original_instrument_receipt_denies():
    frozen, packet = account_packet()
    start = packet.completed_at + timedelta(seconds=31)
    supplied = observation(
        request_started_at=start,
        headers_received_at=start + timedelta(milliseconds=2),
        body_completed_at=start + timedelta(milliseconds=3),
        body=wire(
            [
                fee_row(
                    ts=str(int((start + timedelta(milliseconds=1)).timestamp() * 1000))
                )
            ]
        ),
    )
    with pytest.raises(fees.SwapFeeSourceError, match="causal_clock_invalid"):
        derive(frozen=frozen, packet=packet, observed=supplied)


def test_changed_raw_or_metadata_and_rewritten_receipt_cannot_reuse_pins():
    frozen, packet = account_packet()
    supplied = observation()
    original = derive(frozen=frozen, packet=packet, observed=supplied)
    with pytest.raises(fees.SwapFeeSourceError, match="raw_pin_mismatch"):
        derive(
            frozen=frozen,
            packet=packet,
            observed=replace(supplied, raw_body=wire([fee_row(level="Lv2")])),
            expected_fee_observation_sha256=fees.supplied_observation_sha256(
                replace(supplied, raw_body=wire([fee_row(level="Lv2")]))
            ),
            expected_fee_raw_sha256=journal.digest(supplied.raw_body),
        )
    with pytest.raises(fees.SwapFeeSourceError, match="observation_pin_mismatch"):
        derive(
            frozen=frozen,
            packet=packet,
            observed=replace(supplied, content_type="text/plain"),
            expected_fee_observation_sha256=fees.supplied_observation_sha256(supplied),
        )
    with pytest.raises(fees.SwapFeeSourceError, match="receipt_mismatch"):
        fees.verify_supplied_swap_fee_receipt(
            original.receipt_json + b" ",
            expected_receipt_sha256=original.receipt_sha256,
            original_account_packet=frozen.payload,
            fee_observation=supplied,
            instrument_id=INSTRUMENT,
            expected_account_packet_sha256=frozen.sha256,
            expected_account_plan_sha256=packet.plan_sha256,
            expected_fee_raw_sha256=journal.digest(supplied.raw_body),
            expected_fee_observation_sha256=fees.supplied_observation_sha256(supplied),
        )


def test_caller_supplied_rate_or_pass_flag_has_no_interface():
    frozen, packet = account_packet()
    supplied = observation()
    with pytest.raises(TypeError):
        fees.derive_supplied_swap_fee_source(
            frozen.payload,
            supplied,
            instrument_id=INSTRUMENT,
            expected_account_packet_sha256=frozen.sha256,
            expected_account_plan_sha256=packet.plan_sha256,
            expected_fee_raw_sha256=journal.digest(supplied.raw_body),
            expected_fee_observation_sha256=fees.supplied_observation_sha256(supplied),
            trusted_rate=Decimal(0),
            passed=True,
        )
