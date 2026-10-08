"""Synthetic G1-rebuilt failed-G2 sidecars; no installer or trading runtime."""

import json
from datetime import timedelta
from decimal import Decimal as D

import pytest

from app.domain.analysis import MultiTimeframeAnalysis
from app.domain.market import MarketSnapshot
from app.trade_qualification.events import extract_trigger
from app.trade_qualification.high_vol_g2_sidecar import (
    HighVolFailedG2Evaluation,
    evaluate_high_vol_failed_g2,
    verify_high_vol_failed_g2,
)
from app.trade_qualification.high_vol_momentum_observation import (
    ORIGINAL_CANDIDATE_POLICY_SHA256,
    PROFILE_SHA256,
    HighVolOriginalSourcePin,
    timing_policy_sha256,
)
from app.trade_qualification.service import evaluate_qualification_prefix
from app.trade_qualification.timing import TIMING_POLICIES, event_identity
from tests.unit.qualification_prefix_fixtures import prefix_source
from tests.unit.test_qualification_events import source as event_source
from tests.unit.test_qualification_prefix import inputs


def _high_vol_breakout(direction):
    source = prefix_source(direction)
    market = source.market.model_copy(deep=True)
    event, _ = event_source("breakout_continuation", direction)
    # Copy OHLC, never analysis labels. Preserve the captured clock and quote.
    for frame in ("15m", "5m"):
        market.candles[frame] = [
            row.model_copy(update={"timestamp": original.timestamp})
            for row, original in zip(
                event.candles[frame], market.candles[frame], strict=True
            )
        ]
    market.candles["4H"] = [
        row.model_copy(
            update={"high": row.close + D(".65"), "low": row.close - D(".65")}
        )
        for row in market.candles["4H"]
    ]
    arguments = inputs(source)
    arguments["intent"] = arguments["intent"].model_copy(
        update={"strategy": "breakout_continuation"}
    )
    return market, arguments


def _pin_from_g1(prefix):
    raw = json.loads(prefix.data_result.source_json)
    market = MarketSnapshot.model_validate_json(json.dumps(raw["market"]), strict=True)
    analysis = MultiTimeframeAnalysis.model_validate_json(
        json.dumps(raw["analysis"]), strict=True
    )
    intent = prefix.intent
    timing = TIMING_POLICIES[intent.strategy]
    detection = extract_trigger(
        market,
        analysis,
        report_id=intent.report_id,
        strategy=intent.strategy,
        direction=intent.direction,
        observed_at=prefix.result.evaluated_at,
        trigger_ttl_seconds=timing.trigger_ttl_seconds,
    )
    assert not detection.fail_codes and detection.trigger is not None
    assert detection.source_sha256 == prefix.data_result.source_sha256
    return HighVolOriginalSourcePin(
        report_id=intent.report_id,
        instrument_id=intent.instrument_id,
        strategy=intent.strategy,
        direction=intent.direction,
        source_sha256=detection.source_sha256,
        event_key=event_identity(detection),
        candidate_policy_sha256=ORIGINAL_CANDIDATE_POLICY_SHA256,
        profile_sha256=PROFILE_SHA256,
        timing_policy_sha256=timing_policy_sha256(timing),
        trigger_ttl_seconds=timing.trigger_ttl_seconds,
        max_setup_to_trigger_seconds=timing.max_setup_to_trigger_seconds,
        original_setup_time=detection.setup_time,
        original_trigger_time=detection.trigger.trigger_time,
        original_trigger_expires_at=detection.trigger.expires_at,
        original_candidate_expires_at=min(
            intent.expires_at, detection.trigger.expires_at
        ),
    )


@pytest.mark.parametrize("direction", ("long", "short"))
def test_failed_g2_sidecar_replays_same_g1_without_changing_no_trade(direction):
    market, arguments = _high_vol_breakout(direction)
    baseline = evaluate_qualification_prefix(market, **arguments)
    assert [(gate.gate.value, gate.code) for gate in baseline.result.gates] == [
        ("G1", "passed"),
        ("G2", "regime_strategy_not_allowed"),
    ]
    assert baseline.result.gates[1].measured_values["route_decision"] == "no_trade"
    assert baseline.result.gates[1].measured_values["allowed_strategies"] == "none"
    pin = _pin_from_g1(baseline)
    wrapped = evaluate_high_vol_failed_g2(
        market, original_pin=pin, enabled=True, **arguments
    )
    assert wrapped.prefix == baseline
    assert wrapped.sidecar is not None
    assert wrapped.sidecar.g1_source_sha256 == baseline.data_result.source_sha256
    assert wrapped.sidecar.observation.recomputed_source_sha256 == pin.source_sha256
    assert wrapped.sidecar.observation.recomputed_event_key == pin.event_key
    assert (
        wrapped.sidecar.observation.effective_expires_at
        <= pin.original_candidate_expires_at
    )
    assert wrapped.sidecar.admission == "DENY"
    assert wrapped.sidecar.execution_authority is False
    assert (
        wrapped.prefix.detection
        is wrapped.prefix.timing
        is wrapped.prefix.location
        is None
    )
    assert wrapped.prefix.result.trigger is None
    assert wrapped.prefix.result.stop_loss is wrapped.prefix.result.take_profit is None
    assert not wrapped.prefix.prefix_complete
    assert (
        verify_high_vol_failed_g2(
            wrapped, market, original_pin=pin, enabled=True, **arguments
        )
        == wrapped
    )
    restored = HighVolFailedG2Evaluation.model_validate_json(
        wrapped.model_dump_json(round_trip=True)
    )
    assert restored == wrapped
    assert restored.sidecar.receipt_sha256 == wrapped.sidecar.receipt_sha256


def test_default_off_returns_exact_unmodified_prefix_and_no_sidecar():
    market, arguments = _high_vol_breakout("long")
    baseline = evaluate_qualification_prefix(market, **arguments)
    wrapped = evaluate_high_vol_failed_g2(market, **arguments)
    assert wrapped.prefix == baseline
    assert wrapped.sidecar is None
    assert wrapped.execution_authority is False


def test_non_high_vol_or_failed_g1_never_receives_sidecar():
    source = prefix_source("long")
    arguments = inputs(source)
    allowed = evaluate_high_vol_failed_g2(source.market, enabled=True, **arguments)
    assert allowed.sidecar is None
    missing_reference = evaluate_high_vol_failed_g2(
        source.market, enabled=True, **{**arguments, "reference": None}
    )
    assert missing_reference.sidecar is None
    assert len(missing_reference.prefix.result.gates) == 1


def test_original_expiry_only_shortens_and_stale_observation_stays_denied():
    market, arguments = _high_vol_breakout("long")
    baseline = evaluate_qualification_prefix(market, **arguments)
    pin = _pin_from_g1(baseline)
    shortened = HighVolOriginalSourcePin.model_validate(
        {
            **pin.model_dump(round_trip=True),
            "original_candidate_expires_at": pin.original_trigger_time
            + timedelta(seconds=1),
        },
        strict=True,
    )
    later = {
        **arguments,
        "evaluated_at": arguments["evaluated_at"] + timedelta(seconds=2),
    }
    wrapped = evaluate_high_vol_failed_g2(
        market, original_pin=shortened, enabled=True, **later
    )
    assert wrapped.prefix.result.gates[-1].code == "regime_strategy_not_allowed"
    assert wrapped.sidecar.observation.code == "original_event_expired_or_invalid"
    assert (
        wrapped.sidecar.observation.effective_expires_at
        == shortened.original_candidate_expires_at
    )
    assert (
        wrapped.sidecar.observation.effective_expires_at
        < pin.original_trigger_expires_at
    )
    assert wrapped.sidecar.admission == "DENY"


def test_changed_source_or_saved_digest_cannot_replay_as_same_sidecar():
    market, arguments = _high_vol_breakout("long")
    baseline = evaluate_qualification_prefix(market, **arguments)
    pin = _pin_from_g1(baseline)
    wrapped = evaluate_high_vol_failed_g2(
        market, original_pin=pin, enabled=True, **arguments
    )
    altered = market.model_copy(deep=True)
    row = altered.candles["4H"][0]
    altered.candles["4H"][0] = row.model_copy(update={"high": row.high + D(".01")})
    with pytest.raises(ValueError, match="replay mismatch"):
        verify_high_vol_failed_g2(
            wrapped, altered, original_pin=pin, enabled=True, **arguments
        )
    forged = wrapped.model_copy(
        update={
            "sidecar": wrapped.sidecar.model_copy(update={"g2_gate_sha256": "a" * 64})
        }
    )
    with pytest.raises(ValueError):
        verify_high_vol_failed_g2(
            forged, market, original_pin=pin, enabled=True, **arguments
        )


def test_missing_pin_and_non_boolean_switch_reject_without_granting_authority():
    market, arguments = _high_vol_breakout("long")
    with pytest.raises(ValueError, match="exact original source pin"):
        evaluate_high_vol_failed_g2(market, enabled=True, **arguments)
    with pytest.raises(ValueError, match="exact boolean"):
        evaluate_high_vol_failed_g2(market, enabled=1, **arguments)
