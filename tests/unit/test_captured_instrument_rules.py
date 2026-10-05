"""Exact synthetic source rules; never native source or candidate authority."""

import json
from datetime import timedelta
from decimal import Decimal, Inexact, Rounded, localcontext
from fractions import Fraction

import pytest

from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_capture_journal as journal
from app.trade_qualification import captured_instrument_rules as rules
from tests.unit.test_qualification_account_capture import INSTRUMENT, NOW, UID, ms, row
from tests.unit.test_qualification_account_materializer import instrument
from tests.unit.test_qualification_account_metadata import source
from tests.unit.test_qualification_account_runtime import regional


def derive(packet=None, **changes):
    packet = source(selected=regional()) if packet is None else packet
    frozen = capture.freeze_demo_account_packet(
        packet, expected_plan_sha256=packet.plan_sha256
    )
    return rules.derive_captured_instrument_rules(
        packet,
        **{
            "instrument_id": INSTRUMENT,
            "expected_plan_sha256": packet.plan_sha256,
            "expected_packet_sha256": frozen.sha256,
            **changes,
        },
    )


def with_row(value):
    return source(selected=regional(), pages={"account_instruments": [[value]]})


def test_exact_tick_lot_and_contract_units_bind_original_row_page_packet():
    packet = with_row(
        row(
            "account_instruments",
            tickSz="0.0100",
            lotSz="0.100",
            minSz="0.150",
            ctVal="0.010",
        )
    )
    before = capture.freeze_demo_account_packet(
        packet, expected_plan_sha256=packet.plan_sha256
    )
    result = derive(packet)
    value = json.loads(result.receipt_json)
    assert result.tick_size == Decimal("0.01") and result.lot_size == Decimal("0.1")
    assert result.min_contracts == Decimal("0.15")  # not forced onto the lot grid
    assert Fraction(result.min_contracts) / Fraction(result.lot_size) == Fraction(3, 2)
    assert result.contract_value == Decimal("0.01") and result.contract_multiplier == 1
    assert value["rules"]["tickSz"]["raw"] == "0.0100"
    assert value["rules"]["ctVal"]["raw"] == "0.010"
    assert value["units"] == {
        "tickSz": "USDT_per_BTC",
        "lotSz": "contracts",
        "minSz": "contracts",
        "ctVal": "BTC_per_contract",
        "ctMult": "dimensionless",
    }
    index = value["source_binding"]["request_index"]
    page = packet.observations[index]
    assert page.request.stream == "account_instruments"
    assert result.raw_row_json == page.rows[0].canonical_json.encode()
    assert value["source_binding"]["row_sha256"] == journal.digest(result.raw_row_json)
    assert value["source_binding"]["page_receipt_sha256"] == page.receipt_sha256
    assert value["source_binding"]["response_body_sha256"] == page.body_sha256
    assert value["packet_sha256"] == before.sha256
    assert value["account_identity"]["uid"] == UID
    assert value["account_identity"]["environment"] == "demo"
    assert value["account_identity"]["session_binding_sha256"] == journal.digest(
        packet.plan.session_binding_id.encode()
    )
    assert value["source_ts"] == {
        "field_present": False,
        "raw": None,
        "semantics": "unknown_for_this_endpoint",
        "exchange_asof": None,
    }
    assert (
        not result.source_authenticity_verified
        and not result.current_owned_session_authority
        and not result.execution_authority
    )
    assert (
        capture.freeze_demo_account_packet(
            packet, expected_plan_sha256=packet.plan_sha256
        )
        == before
    )
    assert derive(packet) == result


@pytest.mark.parametrize("timestamp", [None, "", ms(NOW - timedelta(days=30))])
def test_optional_ts_is_retained_without_assigning_asof_or_receipt_timestamp(timestamp):
    result = derive(with_row(row("account_instruments", tickSz="0.1", ts=timestamp)))
    value = json.loads(result.receipt_json)
    assert value["source_ts"]["field_present"] is True
    assert value["source_ts"]["raw"] == timestamp
    assert value["source_ts"]["exchange_asof"] is None
    assert value["source_ts"]["semantics"] == "unknown_for_this_endpoint"
    assert value["measured_receipt"]["body_completed_at"] != timestamp


@pytest.mark.parametrize(
    "bad",
    [
        None,
        "",
        "0",
        "-1",
        "1e-2",
        "NaN",
        "Infinity",
        " 1",
        "+1",
        "01",
        True,
        0.1,
        "1" * 21,
        "0." + "1" * 21,
    ],
)
def test_malformed_decimal_syntax_rejects_at_source_or_binding(bad):
    with pytest.raises(
        (capture.AccountCaptureError, rules.CapturedInstrumentRulesError)
    ):
        derive(with_row(row("account_instruments", tickSz=bad)))


@pytest.mark.parametrize("field", ["tickSz", "lotSz", "minSz", "ctVal", "ctMult"])
def test_every_numeric_rule_requires_positive_source_value(field):
    value = row("account_instruments", tickSz="0.1")
    value[field] = "0"
    with pytest.raises(
        (capture.AccountCaptureError, rules.CapturedInstrumentRulesError)
    ):
        derive(with_row(value))


@pytest.mark.parametrize("field", ["tickSz", "lotSz", "minSz", "ctVal", "ctMult"])
def test_absent_required_field_is_never_filled(field):
    value = row("account_instruments", tickSz="0.1")
    del value[field]
    with pytest.raises(
        (capture.AccountCaptureError, rules.CapturedInstrumentRulesError)
    ):
        derive(with_row(value))


@pytest.mark.parametrize(
    "changes",
    [
        {"ctType": "inverse"},
        {"settleCcy": "BTC"},
        {"ctValCcy": "USDT"},
        {"ctValCcy": "ETH"},
        {"ctMult": "2"},
        {"state": "suspend"},
        {"instType": "SPOT"},
        {"baseCcy": "ETH"},
        {"quoteCcy": "BTC"},
    ],
)
def test_unreviewed_contract_semantics_are_rejected(changes):
    with pytest.raises(
        (capture.AccountCaptureError, rules.CapturedInstrumentRulesError)
    ):
        derive(with_row(row("account_instruments", tickSz="0.1", **changes)))


def test_non_target_row_does_not_substitute_for_missing_instrument():
    packet = with_row(
        row(
            "account_instruments",
            instId="ETH-USDT-SWAP",
            ctValCcy="ETH",
            baseCcy="ETH",
            tickSz="0.1",
        )
    )
    with pytest.raises(rules.CapturedInstrumentRulesError, match="exact_row_missing"):
        derive(packet)


def test_duplicate_identity_in_one_page_and_duplicate_page_are_rejected():
    raw = row("account_instruments", tickSz="0.1")
    with pytest.raises(capture.AccountCaptureError):
        source(pages={"account_instruments": [[raw, raw]]})
    packet = source()
    page = next(
        item
        for item in packet.observations
        if item.request.stream == "account_instruments"
    )
    forged = packet.model_copy(update={"observations": (*packet.observations, page)})
    with pytest.raises(
        (capture.AccountCaptureError, rules.CapturedInstrumentRulesError)
    ):
        derive(forged)


@pytest.mark.parametrize(
    "field",
    ["expected_plan_sha256", "expected_packet_sha256", "expected_policy_sha256"],
)
def test_wrong_external_pin_rejects(field):
    with pytest.raises(rules.CapturedInstrumentRulesError):
        derive(**{field: "0" * 64})


def test_caller_metadata_or_diagnostic_cannot_replace_original_packet():
    packet = source()
    pins = {
        "instrument_id": INSTRUMENT,
        "expected_plan_sha256": packet.plan_sha256,
        "expected_packet_sha256": capture.freeze_demo_account_packet(
            packet, expected_plan_sha256=packet.plan_sha256
        ).sha256,
    }
    for counterfeit in (
        instrument(),
        derive(packet),
        {"tickSz": "0.1", "passed": True},
    ):
        with pytest.raises(rules.CapturedInstrumentRulesError):
            rules.derive_captured_instrument_rules(counterfeit, **pins)
    with pytest.raises(TypeError):
        rules.derive_captured_instrument_rules(
            packet, **pins, supplied_spec=instrument()
        )


def test_decimal_context_never_rounds_rules():
    packet = with_row(
        row(
            "account_instruments",
            tickSz="0.00000000000000000001",
            lotSz="0.00000000000000000002",
            minSz="0.00000000000000000003",
        )
    )
    expected = derive(packet)
    with localcontext() as context:
        context.prec = 2
        context.traps[Inexact] = True
        context.traps[Rounded] = True
        assert derive(packet) == expected
