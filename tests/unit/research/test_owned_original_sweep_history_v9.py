"""Synthetic native-owner sweep lineage; never a Demo/order acceptance."""

import json
from types import SimpleNamespace

import pytest

from app.domain.source_primitives import canonical, decode, sha
from app.trade_qualification import account_native_runtime as account_native
from app.trade_qualification import original_source_coordinator_v2 as original
from app.trade_qualification import owned_original_sweep_history_v9 as boundary
from app.trade_qualification.sweep_history_permission import POLICY_SHA256
from tests.unit import test_original_candidate_policy as base_fixture
from tests.unit.research.test_owned_original_candidate_boundary_v5 import (
    _inspected_precursor_receipt,
)
from tests.unit.research.test_owned_original_source_coordinator_v2 import session
from tests.unit.research.test_owned_public_runtime_v2 import policy
from tests.unit.test_original_candidate_precursor_v2 import (
    capture_raw_source,
    declared_history_funding_row,
    derive,
    history_raw_source,
    inputs,
)


@pytest.fixture(scope="module", params=["long", "short"])
def captured(request, tmp_path_factory):
    source = history_raw_source("liquidity_sweep_reversal", request.param)
    diagnostic = capture_raw_source(
        source,
        tmp_path_factory.mktemp("owned-sweep-v9"),
        declared_funding=declared_history_funding_row(source),
    )
    packet = base_fixture.account_source()
    derived = derive(diagnostic, packet, strategy="liquidity_sweep_reversal")
    assert derived.intent is not None
    return diagnostic, packet, derived


def _inspect(diagnostic, packet, derived, **changes):
    arguments = inputs(diagnostic, packet, strategy="liquidity_sweep_reversal")
    return original._evaluate_owned_sweep_history_v9(
        diagnostic.packet,
        derived,
        **{
            "public_pin": arguments["expected_public_bundle_sha256"],
            "account_pin": arguments["expected_account_packet_sha256"],
            "plan_pin": arguments["expected_account_plan_sha256"],
            "data_policy": arguments["data_policy"],
            "created_at": arguments["created_at"],
            **changes,
        },
    )


def _handoff(diagnostic, packet, derived, inner):
    arguments = inputs(diagnostic, packet, strategy="liquidity_sweep_reversal")
    value = decode(
        _inspected_precursor_receipt(
            arguments["expected_account_plan_sha256"]
        ).receipt_json
    )
    source = json.loads(derived.receipt_json)
    value.update(
        public_packet_sha256=arguments["expected_public_bundle_sha256"],
        g1_source_sha256=arguments["expected_public_bundle_sha256"],
        account_packet_sha256=arguments["expected_account_packet_sha256"],
        precursor_receipt_sha256=derived.receipt_sha256,
        precursor_intent_sha256=None
        if source["intent"] is None
        else sha(canonical(source["intent"])),
        precursor_code=source["code"],
        precursor_action=source["action"],
        precursor_intent_derived=source["intent"] is not None,
    )
    return (
        original._OwnedSweepHistoryHandoffV9(
            original.InitialOwnedPrecursorDiagnosticV4(canonical(value)), inner
        ),
        SimpleNamespace(_used=True, _pin=arguments["expected_account_plan_sha256"]),
    )


def test_owned_replay_pins_v6_permission_original_event_and_unknown_pit(captured):
    diagnostic, packet, derived = captured
    first = _inspect(diagnostic, packet, derived)
    assert first == _inspect(diagnostic, packet, derived)
    inner = decode(first)
    source = json.loads(derived.receipt_json)
    assert inner["sweep_policy_sha256"] == POLICY_SHA256
    assert inner["sweep_permission_sha256"] == sha(
        canonical(source["history_admission"])
    )
    assert inner["event_key_sha256"] == source["event_key"]
    assert inner["intent_sha256"] == sha(canonical(source["intent"]))
    assert inner["sweep_admitted"] is True
    assert inner["historical_first_availability_verified"] is False
    assert inner["admission"] == "DENY"
    assert inner["execution_authority"] is False
    handoff, controlled = _handoff(diagnostic, packet, derived, first)
    result = boundary._readback_owned_sweep_handoff_v9(handoff, controlled)
    outer = decode(result.receipt_json)
    assert outer["code"] == "sweep_history_observed_wait_pit"
    assert outer["sweep_inspection_sha256"] == sha(first)
    assert outer["intent_sha256"] == inner["intent_sha256"]
    assert outer["historical_first_availability_verified"] is False
    assert result.admission == "DENY" and result.execution_authority is False


def test_changed_source_pin_and_late_forged_permission_fail_closed(captured):
    diagnostic, packet, derived = captured
    with pytest.raises(original.OriginalSourceCoordinatorError):
        _inspect(diagnostic, packet, derived, public_pin="0" * 64)
    first = _inspect(diagnostic, packet, derived)
    handoff, controlled = _handoff(diagnostic, packet, derived, first)
    changed = dict(decode(first), execution_authority=True)
    with pytest.raises(boundary.OwnedOriginalSweepHistoryError):
        boundary._readback_owned_sweep_handoff_v9(
            original._OwnedSweepHistoryHandoffV9(handoff.original, canonical(changed)),
            controlled,
        )
    controlled._used = False
    with pytest.raises(boundary.OwnedOriginalSweepHistoryError):
        boundary._readback_owned_sweep_handoff_v9(handoff, controlled)


@pytest.mark.parametrize("direction", ("long", "short"))
def test_real_history_cannot_rescue_outside_original_entry_zone(direction, tmp_path):
    source = history_raw_source(
        "liquidity_sweep_reversal", direction, sweep_scenario="original_outside"
    )
    diagnostic = capture_raw_source(
        source,
        tmp_path,
        declared_funding=declared_history_funding_row(source),
    )
    packet = base_fixture.account_source()
    derived = derive(diagnostic, packet, strategy="liquidity_sweep_reversal")
    precursor = json.loads(derived.receipt_json)
    assert precursor["action"] == "CANCEL"
    assert precursor["code"] == "fixed_initial_entry_outside_original_zone"
    inner = _inspect(diagnostic, packet, derived)
    assert decode(inner)["sweep_admitted"] is True
    assert decode(inner)["intent_sha256"] is None
    handoff, controlled = _handoff(diagnostic, packet, derived, inner)
    result = boundary._readback_owned_sweep_handoff_v9(handoff, controlled)
    record = decode(result.receipt_json)
    assert record["code"] == "sweep_history_observed_no_intent"
    assert record["intent_sha256"] is None
    assert result.admission == "DENY" and result.execution_authority is False


@pytest.mark.asyncio
async def test_native_preflight_keeps_actual_public_refusal_and_no_account_io(
    tmp_path, monkeypatch
):
    controlled = session()
    monkeypatch.setattr(account_native, "_configured_factory", lambda _: True)

    async def account_forbidden(*_args, **_kwargs):
        pytest.fail("account source cannot be queried before public acceptance")

    monkeypatch.setattr(
        account_native, "capture_initial_native_account", account_forbidden
    )
    result = await boundary.preflight_owned_sweep_history_v9(
        tmp_path / "public",
        tmp_path / "account",
        instrument_id="BTC-USDT-SWAP",
        market_policy=policy(),
        account_session=controlled,
        session_factory=object(),
    )
    record = decode(result.receipt_json)
    assert record["code"] == "native_precursor_unavailable"
    assert record["history_evidence_replayed"] is False
    assert record["admission"] == "DENY"
    assert controlled._used is True
