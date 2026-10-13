"""Synthetic native-owner sweep lineage; never a Demo/order acceptance."""

import json
from types import SimpleNamespace

import pytest

from app.domain.source_primitives import canonical, decode, sha
from app.trade_qualification import account_native_runtime as account_native
from app.trade_qualification import original_source_coordinator_v2 as original
from app.trade_qualification import owned_original_sweep_history_v9 as boundary
from app.trade_qualification.original_candidate_precursor_v2 import (
    OriginalCandidatePrecursorV2Error,
)
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
    return diagnostic, packet


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


def _synthetic_v1_consistency_handoff():
    # Direct V9 readback test only: these fixed hashes are not raw-source proof.
    pin = "a" * 64
    outer = decode(_inspected_precursor_receipt(pin).receipt_json)
    outer["precursor_code"] = "sweep_stage_C_closed"
    original_receipt = original.InitialOwnedPrecursorDiagnosticV4(canonical(outer))
    inner = {
        "schema_version": "ctcc.owned_original_sweep_history_inspection.v9",
        "precursor_receipt_sha256": outer["precursor_receipt_sha256"],
        "public_packet_sha256": outer["public_packet_sha256"],
        "account_packet_sha256": outer["account_packet_sha256"],
        "account_plan_sha256": pin,
        "g1_evaluation_sha256": outer["g1_result_sha256"],
        "g1_source_sha256": outer["g1_source_sha256"],
        "sweep_policy_sha256": POLICY_SHA256,
        "sweep_permission_sha256": "4" * 64,
        "sweep_code": "passed",
        "sweep_admitted": True,
        "event_key_sha256": "5" * 64,
        "intent_sha256": outer["precursor_intent_sha256"],
        "event_expires_at": "2026-09-12T10:01:00+00:00",
        "event_prefix_witness_sha256": "6" * 64,
        "admission": "DENY",
        **{name: False for name in boundary._FALSE_FIELDS},
    }
    handoff = original._OwnedSweepHistoryHandoffV9(original_receipt, canonical(inner))
    return handoff, SimpleNamespace(_used=True, _pin=pin)


def test_v1_owned_readback_consistency_rejects_forgery_and_stale_session():
    handoff, controlled = _synthetic_v1_consistency_handoff()
    checked = boundary._readback_owned_sweep_handoff_v9(handoff, controlled)
    assert checked.admission == "DENY" and checked.execution_authority is False
    assert decode(checked.receipt_json)["sweep_policy_sha256"] == POLICY_SHA256
    forged = dict(decode(handoff.sweep_receipt_json), execution_authority=True)
    with pytest.raises(boundary.OwnedOriginalSweepHistoryError):
        boundary._readback_owned_sweep_handoff_v9(
            original._OwnedSweepHistoryHandoffV9(handoff.original, canonical(forged)),
            controlled,
        )
    controlled._used = False
    with pytest.raises(
        boundary.OwnedOriginalSweepHistoryError, match="session_mismatch"
    ):
        boundary._readback_owned_sweep_handoff_v9(handoff, controlled)
    controlled._used = True
    controlled._pin = "0" * 64
    with pytest.raises(
        boundary.OwnedOriginalSweepHistoryError, match="session_mismatch"
    ):
        boundary._readback_owned_sweep_handoff_v9(handoff, controlled)


def test_owned_v9_cannot_inherit_v1_permission_for_swap_vwap_v2(captured):
    diagnostic, packet = captured
    with pytest.raises(
        OriginalCandidatePrecursorV2Error, match="precursor_v2_source_invalid"
    ):
        derive(diagnostic, packet, strategy="liquidity_sweep_reversal")


def test_changed_source_pin_cannot_rescue_v1_owned_sweep(captured):
    diagnostic, packet = captured
    with pytest.raises(OriginalCandidatePrecursorV2Error):
        derive(
            diagnostic,
            packet,
            strategy="liquidity_sweep_reversal",
            expected_public_bundle_sha256="0" * 64,
        )
    with pytest.raises(
        OriginalCandidatePrecursorV2Error, match="precursor_v2_source_invalid"
    ):
        derive(diagnostic, packet, strategy="liquidity_sweep_reversal")


@pytest.mark.parametrize("direction", ("long", "short"))
def test_swap_v2_owned_sweep_rejects_before_outside_entry_zone(direction, tmp_path):
    source = history_raw_source(
        "liquidity_sweep_reversal", direction, sweep_scenario="original_outside"
    )
    diagnostic = capture_raw_source(
        source,
        tmp_path,
        declared_funding=declared_history_funding_row(source),
    )
    packet = base_fixture.account_source()
    with pytest.raises(
        OriginalCandidatePrecursorV2Error, match="precursor_v2_source_invalid"
    ):
        derive(diagnostic, packet, strategy="liquidity_sweep_reversal")


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
