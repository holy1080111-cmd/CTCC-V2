"""Nonvacuous synthetic raw-G1 → HighVol precursor/G2–G4, never venue acceptance."""

import json
from datetime import datetime
from decimal import Decimal

import pytest

from app.domain.analysis import MultiTimeframeAnalysis
from app.domain.source_primitives import decode, sha
from app.strategies.regime import RouteDecision, route_regime
from app.trade_qualification import account_capture, data_v2
from app.trade_qualification import account_current_source_v4 as current
from app.trade_qualification import high_vol_momentum_qualification_v1 as profile
from app.trade_qualification import original_candidate_precursor_v2 as precursor
from app.trade_qualification import owned_current_original_prefix_v1 as local
from app.trade_qualification.account_observation_index import CaptureReference
from app.trade_qualification.models import MarketRegime
from app.trade_qualification.native_original_g1_policy_v1 import (
    DATA_POLICY_SHA256,
    fixed_native_original_g1_policy,
)
from tests.unit import test_original_candidate_policy as account_fixture
from tests.unit import test_original_candidate_precursor_v2 as raw
from tests.unit.high_vol_common_raw_fixture_v1 import common_raw_high_vol_source_v1


@pytest.mark.parametrize("strategy", ("breakout_continuation", "volatility_expansion"))
@pytest.mark.parametrize("direction", ("long", "short"))
def test_complete_common_raw_g1_reaches_versioned_high_vol_qualification(
    tmp_path, strategy, direction
):
    source = common_raw_high_vol_source_v1(strategy, direction)
    diagnostic = raw.capture_raw_source(
        source,
        tmp_path / "raw",
        declared_funding=raw.declared_history_funding_row(source),
    )
    packet = account_fixture.account_source()
    policy = fixed_native_original_g1_policy()
    args = raw.inputs(
        diagnostic,
        packet,
        strategy=strategy,
        data_policy=policy,
        expected_data_policy_sha256=DATA_POLICY_SHA256,
    )
    g1 = data_v2.evaluate_public_market_data_v2(
        diagnostic.packet,
        expected_bundle_sha256=diagnostic.packet.bundle_sha256,
        policy=policy,
        evaluated_at=args["created_at"],
    )
    g1 = data_v2.verify_public_market_data_v2(
        g1,
        diagnostic.packet,
        expected_bundle_sha256=diagnostic.packet.bundle_sha256,
        policy=policy,
        evaluated_at=args["created_at"],
    )
    assert g1.passed, g1.gate.code
    original_bytes = diagnostic.packet.packet_json
    doc = json.loads(g1.source_json)
    analysis = MultiTimeframeAnalysis.model_validate_json(
        json.dumps(doc["analysis"]), strict=True
    )
    route = route_regime(analysis)
    assert route.regime == MarketRegime.HIGH_VOLATILITY
    assert route.decision == RouteDecision.NO_TRADE and route.allowed_strategies == ()
    assert analysis.timeframe_analyses["4H"].volatility == "high"
    assert not analysis.blockers
    assert all(
        view.indicators.causal_state.shock_score < Decimal(".65")
        for view in analysis.timeframe_analyses.values()
    )

    owned_family = strategy == "volatility_expansion"
    derive = (
        precursor.derive_owned_original_candidate_precursor_v3
        if owned_family
        else precursor.derive_original_candidate_precursor_v2
    )
    verify = (
        precursor.verify_owned_original_candidate_precursor_v3
        if owned_family
        else precursor.verify_original_candidate_precursor_v2
    )
    denied = derive(diagnostic.packet, packet, **args)
    denied_doc = decode(denied.receipt_json)
    assert denied.intent is None
    assert denied_doc["route"]["regime"] == MarketRegime.HIGH_VOLATILITY.value
    assert denied_doc["code"] == (
        "regime_safety_blocked"
        if owned_family
        else "source_regime_strategy_not_allowed"
    )
    selected = {
        **args,
        "high_vol_profile_sha256": profile.PROFILE_SHA256,
        "expected_policy_sha256": (
            precursor.OWNED_HIGH_VOL_POLICY_SHA256
            if owned_family
            else precursor.HIGH_VOL_POLICY_SHA256
        ),
    }
    actual = derive(diagnostic.packet, packet, **selected)
    actual_doc = decode(actual.receipt_json)
    assert actual.intent is not None, actual_doc["code"]
    assert actual_doc["high_vol_profile_sha256"] == profile.PROFILE_SHA256
    proof = decode(actual_doc["high_vol_permission_json"].encode())
    assert (
        proof["admitted"] is True
        and proof["actual_regime"] == MarketRegime.HIGH_VOLATILITY.value
    )
    assert proof["source_sha256"] == g1.source_sha256
    assert proof["router_relabelled"] is False
    assert actual_doc["conditions"]["score"] == 100
    assert (
        actual_doc["conditions"]["required_failures"]
        == actual_doc["conditions"]["veto_failures"]
        == []
    )
    assert datetime.fromisoformat(
        actual_doc["detection"]["setup_time"]
    ) == raw.NOW.replace(minute=0)
    assert (
        datetime.fromisoformat(actual_doc["detection"]["trigger"]["trigger_time"])
        == raw.NOW
    )
    assert actual.intent.candidate_entry == (
        diagnostic.context.quote.ticker.ask
        if direction == "long"
        else diagnostic.context.quote.ticker.bid
    )
    assert (
        verify(actual, diagnostic.packet, packet, **selected).receipt_json
        == actual.receipt_json
    )
    with pytest.raises(ValueError, match="replay"):
        verify(denied, diagnostic.packet, packet, **selected)

    frozen = account_capture.freeze_demo_account_packet(
        packet, expected_plan_sha256=packet.plan_sha256
    )
    private = current.RecordedCurrentAccountBundleV4(
        packet,
        CaptureReference(
            capture_id="1" * 32,
            head_sha256="2" * 64,
            plan_sha256=packet.plan_sha256,
            packet_sha256=frozen.sha256,
            session_binding_sha256="3" * 64,
        ),
        b"{}",
        "4" * 64,
        "5" * 64,
        "6" * 64,
        args["service_deadline"],
        1,
    )
    replay = (
        diagnostic.packet,
        private,
        diagnostic.context,
        g1,
        actual,
        args["created_at"],
        args["service_deadline"],
    )
    computed = (local._compute_owned_family_v2 if owned_family else local._compute)(
        replay
    )
    assert all(item.passed for item in computed.result.gates)
    assert computed.result.market_regime == MarketRegime.HIGH_VOLATILITY
    assert [(gate.gate.value, gate.code) for gate in computed.result.gates[:4]] == [
        ("G1", "passed"),
        ("G2", "passed"),
        ("G3", "passed"),
        ("G4", "passed"),
    ]
    assert computed.result.raw_score == 100
    assert computed.result.gates[1].measured_values[
        "high_vol_permission_sha256"
    ] == sha(actual_doc["high_vol_permission_json"].encode())
    assert computed.result.gates[1].measured_values["router_relabelled"] is False
    assert computed.detection.model_dump(mode="json") == actual_doc["detection"]
    assert computed.original_zone.model_dump(mode="json") == actual_doc["zone"]
    assert computed.intent == actual.intent
    assert (
        computed.execution_authority is False and private.execution_authority is False
    )
    assert diagnostic.packet.packet_json == original_bytes


def test_inherited_initial_quote_remains_outside_fixed_original_zone(tmp_path):
    source = common_raw_high_vol_source_v1(
        "breakout_continuation", "long", quote_profile="inherited_outside_zone"
    )
    diagnostic = raw.capture_raw_source(
        source,
        tmp_path / "rejected-raw",
        declared_funding=raw.declared_history_funding_row(source),
    )
    packet = account_fixture.account_source()
    args = raw.inputs(
        diagnostic,
        packet,
        strategy="breakout_continuation",
        data_policy=fixed_native_original_g1_policy(),
        expected_data_policy_sha256=DATA_POLICY_SHA256,
        high_vol_profile_sha256=profile.PROFILE_SHA256,
        expected_policy_sha256=precursor.HIGH_VOL_POLICY_SHA256,
    )
    actual = precursor.derive_original_candidate_precursor_v2(
        diagnostic.packet, packet, **args
    )
    doc = decode(actual.receipt_json)
    assert actual.intent is None
    assert doc["g1"]["passed"] is True
    assert decode(doc["high_vol_permission_json"].encode())["admitted"] is True
    assert doc["code"] == "fixed_initial_entry_outside_original_zone"
    assert doc["initial_entry"] == "100.41"
    assert doc["zone"]["zone_low"] == doc["zone"]["zone_high"] == "100.36"
