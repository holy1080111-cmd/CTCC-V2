"""Synthetic per-frame math contracts, not full raw-G1/native/venue acceptance.

These retained legacy per-frame tapes do not establish parent/child aggregation.
Separate raw-G1 tests exercise the profile lineage using an existing coherent
normal Expansion tape. No positive HighVol full-G1 or Demo sample is claimed.
"""

from copy import deepcopy
from datetime import timedelta
from decimal import Decimal as D

import pytest

from app.analysis.service import analyze_snapshot_at
from app.domain.source_primitives import decode
from app.strategies.regime import RouteDecision, route_regime
from app.trade_qualification import high_vol_momentum_qualification_v1 as subject
from app.trade_qualification import regime_admission
from app.trade_qualification.continuation import _prepared_market
from app.trade_qualification.events import _copy_source, extract_trigger
from app.trade_qualification.models import MarketRegime
from app.trade_qualification.timing import TIMING_POLICIES
from tests.unit.test_high_vol_momentum_observation import (
    OBSERVED,
    VERSION,
    high_vol_source,
)


def coherent_high_vol_source(direction="long"):
    """Declare a modest noisy compression followed by a non-shock price break.

    All modifications are source-fixture construction before analysis/capture.
    The old sharp-step source remains a separate expected rejection below.
    No derived label, event, mathematical score or threshold is patched.
    """
    market = high_vol_source()
    rows = market.candles["15m"]
    for index in range(-34, -5):
        close = D(100) + D(index % 4 - 2) * D(".02")
        rows[index] = rows[index].model_copy(
            update={
                "open": close,
                "close": close,
                "high": close + D(".04"),
                "low": close - D(".04"),
            }
        )
    rows[-1] = rows[-1].model_copy(update={"open": D("100.15"), "close": D("100.15")})
    final = market.candles["5m"][-1]
    market.candles["5m"][-1] = final.model_copy(
        update={
            "volume_contracts": D(200),
            "volume_currency": D(200),
            "volume_quote": final.close * D(200),
        }
    )
    if direction == "short":
        for frame, values in market.candles.items():
            market.candles[frame] = [
                row.model_copy(
                    update={
                        "open": D(200) - row.open,
                        "close": D(200) - row.close,
                        "high": D(200) - row.low,
                        "low": D(200) - row.high,
                    }
                )
                for row in values
            ]
    return _prepared_market(market, OBSERVED)


def calculated(market):
    analysis = analyze_snapshot_at(market, evaluated_at=OBSERVED, version=VERSION)
    _, _, _, source_sha = _copy_source(market, analysis, OBSERVED)
    return analysis, source_sha


def permission(market, *, strategy="volatility_expansion", direction="long"):
    analysis, source_sha = calculated(market)
    return subject.evaluate_high_vol_momentum_permission_v1(
        market,
        analysis,
        strategy=strategy,
        direction=direction,
        observed_at=OBSERVED,
        source_sha256=source_sha,
    )


@pytest.mark.parametrize("strategy", sorted(subject.STRATEGIES))
@pytest.mark.parametrize("direction", ("long", "short"))
def test_actual_four_frame_source_proves_permission_without_router_relabel(
    strategy, direction
):
    market = coherent_high_vol_source(direction)
    original = deepcopy(market)
    analysis, _ = calculated(market)
    route = route_regime(analysis)
    result = permission(market, strategy=strategy, direction=direction)
    assert result.code == "passed" and result.admitted
    assert result.execution_authority is False
    assert route.regime == MarketRegime.HIGH_VOLATILITY
    assert route.decision == RouteDecision.NO_TRADE and route.allowed_strategies == ()
    document = decode(result.receipt_json)
    assert document["profile_sha256"] == subject.PROFILE_SHA256
    assert document["actual_regime"] == MarketRegime.HIGH_VOLATILITY.value
    assert document["router_relabelled"] is False
    assert document["source_authenticity_verified"] is False
    assert market == original and route_regime(analysis) == route


def test_existing_sharp_step_source_stays_rejected_without_threshold_relaxation():
    result = permission(_prepared_market(high_vol_source(), OBSERVED))
    assert result.code == "high_vol_causal_shock_or_unknown"
    assert not result.admitted


def test_supplied_analysis_cannot_replace_raw_source_or_pin():
    market = coherent_high_vol_source()
    analysis, source_sha = calculated(market)
    forged = analysis.model_copy(update={"overall_bias": "short"})
    inputs = {
        "strategy": "volatility_expansion",
        "direction": "long",
        "observed_at": OBSERVED,
    }
    assert (
        subject.evaluate_high_vol_momentum_permission_v1(
            market, forged, source_sha256=source_sha, **inputs
        ).code
        == "high_vol_analysis_source_mismatch"
    )
    assert (
        subject.evaluate_high_vol_momentum_permission_v1(
            market, analysis, source_sha256="a" * 64, **inputs
        ).code
        == "high_vol_source_pin_mismatch"
    )


def test_fresh_source_is_recomputed_and_original_is_not_repaired():
    original = coherent_high_vol_source()
    before = deepcopy(original)
    assert permission(original).admitted
    changed = deepcopy(original)
    row = changed.candles["15m"][-1]
    changed.candles["15m"][-1] = row.model_copy(
        update={"open": D("100.5"), "close": D("100.5")}
    )
    assert not permission(changed).admitted
    assert original == before


@pytest.mark.parametrize("direction", ("long", "short"))
def test_expansion_uses_actual_prior_compression_and_original_later_momentum(direction):
    market = coherent_high_vol_source(direction)
    inputs = {
        "report_id": "synthetic-highvol-profile",
        "strategy": "volatility_expansion",
        "direction": direction,
        "observed_at": OBSERVED,
        "analysis_version": VERSION,
    }
    legacy = regime_admission.evaluate_regime_admission(market, **inputs)
    assert not legacy.admitted and legacy.code == "regime_safety_blocked"
    current = regime_admission.evaluate_regime_admission(
        market, **inputs, high_vol_profile_sha256=subject.PROFILE_SHA256
    )
    assert current.admitted and current.history_verified
    assert current.snapshot_regime == MarketRegime.HIGH_VOLATILITY.value
    assert current.detection.setup_time < current.detection.trigger.trigger_time
    assert current.detection.trigger.expires_at == legacy.detection.trigger.expires_at
    assert current.event_key == legacy.event_key
    assert (
        regime_admission.verify_regime_admission(
            current, market, **inputs, high_vol_profile_sha256=subject.PROFILE_SHA256
        )
        == current
    )
    with pytest.raises(ValueError, match="replay_mismatch"):
        regime_admission.verify_regime_admission(current, market, **inputs)


def test_missing_compression_cannot_be_replaced_by_current_high_vol_label():
    market = coherent_high_vol_source()
    rows = market.candles["15m"]
    for index in range(-20, -1):
        rows[index] = rows[index].model_copy(
            update={
                "high": rows[index].close + D(".30"),
                "low": rows[index].close - D(".30"),
            }
        )
    analysis, _ = calculated(market)
    detection = extract_trigger(
        market,
        analysis,
        report_id="synthetic-highvol-profile",
        strategy="volatility_expansion",
        direction="long",
        observed_at=OBSERVED,
        trigger_ttl_seconds=TIMING_POLICIES["volatility_expansion"].trigger_ttl_seconds,
    )
    assert detection.fail_codes == ("setup_missing",)
    result = regime_admission.evaluate_regime_admission(
        market,
        report_id="synthetic-highvol-profile",
        strategy="volatility_expansion",
        direction="long",
        observed_at=OBSERVED,
        analysis_version=VERSION,
        high_vol_profile_sha256=subject.PROFILE_SHA256,
    )
    assert not result.admitted and result.code == "setup_missing"


@pytest.mark.parametrize("value", (True, False, "a" * 64, {}, 1))
def test_profile_claims_and_unknown_versions_never_select_a_policy(value):
    with pytest.raises(ValueError, match="profile_unknown"):
        subject.profile_arguments_v1(value)


def test_default_and_explicit_profile_have_distinct_immutable_policy_ids():
    assert subject.profile_arguments_v1(None) == {}
    assert subject.prefix_policy_id_v1("fixed", None) == "fixed"
    assert (
        subject.prefix_policy_id_v1("fixed", subject.PROFILE_SHA256)
        == "fixed-highvol-v1"
    )


def current_inputs(market, strategy, direction):
    from app.trade_qualification.native_original_g1_policy_v1 import (
        fixed_native_original_g1_policy,
    )
    from app.trade_qualification.service import (
        QualificationIntent,
        QualificationPrefixPolicy,
    )

    analysis, source_sha = calculated(market)
    policy = QualificationPrefixPolicy(
        policy_id="synthetic-highvol-current-v1",
        data=fixed_native_original_g1_policy(),
        minimum_score=85,
        tick_size=D(".01"),
        max_allowed_drift_bps=D(30),
        maximum_strategy_spread_bps=D(3),
        maximum_adverse_funding_bps=D(5),
    )
    intent = QualificationIntent(
        report_id="synthetic-highvol-current",
        instrument_id=market.instrument_id,
        strategy=strategy,
        direction=direction,
        candidate_entry=market.ticker.ask if direction == "long" else market.ticker.bid,
        created_at=OBSERVED,
        expires_at=OBSERVED + timedelta(minutes=5),
    )
    values, gates = {"evaluated_at": OBSERVED}, []

    def gate(kind, code, _reason, measured):
        gates.append((kind.value, code, measured))
        return code == "passed"

    return (
        analysis,
        gates,
        {
            "intent": intent,
            "policy": policy,
            "source_sha256": source_sha,
            "quote_bundle_sha256": "a" * 64,
            "bid": market.ticker.bid,
            "ask": market.ticker.ask,
            "funding_rate": D(0),
            "values": values,
            "gate": gate,
        },
    )


@pytest.mark.parametrize("strategy", sorted(subject.STRATEGIES))
@pytest.mark.parametrize("direction", ("long", "short"))
def test_original_and_current_g2_g4_consume_same_raw_profile_without_score_boost(
    strategy, direction
):
    from app.trade_qualification import current_conditions as conditions

    market = coherent_high_vol_source(direction)
    analysis, gates, inputs = current_inputs(market, strategy, direction)
    original = deepcopy(inputs["intent"])
    detection = extract_trigger(
        market,
        analysis,
        report_id=original.report_id,
        strategy=strategy,
        direction=direction,
        observed_at=OBSERVED,
        trigger_ttl_seconds=TIMING_POLICIES[strategy].trigger_ttl_seconds,
    )
    history = None
    for _phase in ("original", "current"):
        gates.clear()
        if strategy == "breakout_continuation":
            conditions._evaluate_base_g2_g4(
                market,
                analysis,
                **inputs,
                high_vol_profile_sha256=subject.PROFILE_SHA256,
            )
        else:
            history = conditions._evaluate_owned_history_g2_g4_v2(
                market,
                analysis,
                **inputs,
                detection=detection,
                original_source_sha256=inputs["source_sha256"],
                original_history_json=history,
                high_vol_profile_sha256=subject.PROFILE_SHA256,
            )
        assert [(kind, code) for kind, code, _ in gates] == [
            ("G2", "passed"),
            ("G3", "passed"),
            ("G4", "passed"),
        ]
        assert inputs["values"]["raw_score"] == 100
        assert inputs["values"]["market_regime"] == MarketRegime.HIGH_VOLATILITY
        assert gates[0][2]["high_vol_profile_sha256"] == subject.PROFILE_SHA256
        assert gates[0][2]["router_relabelled"] is False
        assert inputs["intent"] == original
    if history is not None:
        with pytest.raises(ValueError, match="receipt_changed"):
            conditions._evaluate_owned_history_g2_g4_v2(
                market,
                analysis,
                **inputs,
                detection=detection,
                original_source_sha256=inputs["source_sha256"],
                original_history_json=history,
            )


def test_base_current_legacy_profile_and_changed_current_source_fail_closed():
    from app.trade_qualification import current_conditions as conditions

    market = coherent_high_vol_source()
    analysis, gates, inputs = current_inputs(market, "breakout_continuation", "long")
    conditions._evaluate_base_g2_g4(market, analysis, **inputs)
    assert [(kind, code) for kind, code, _ in gates] == [
        ("G2", "regime_strategy_not_allowed")
    ]
    changed = _prepared_market(high_vol_source(), OBSERVED)
    analysis, gates, inputs = current_inputs(changed, "breakout_continuation", "long")
    conditions._evaluate_base_g2_g4(
        changed, analysis, **inputs, high_vol_profile_sha256=subject.PROFILE_SHA256
    )
    assert [(kind, code) for kind, code, _ in gates] == [
        ("G2", "regime_strategy_not_allowed")
    ]
    assert gates[0][2]["high_vol_permission_code"] == "high_vol_causal_shock_or_unknown"
