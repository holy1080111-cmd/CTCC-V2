"""Synthetic OHLC tests for an evidence-only, default-off HighVol profile."""

from copy import deepcopy
from datetime import timedelta
from decimal import Decimal as D

import pytest

from app.analysis.service import analyze_snapshot_at
from app.strategies.regime import route_regime
from app.trade_qualification import high_vol_momentum_observation as subject
from app.trade_qualification.continuation import _prepared_market
from app.trade_qualification.events import extract_trigger
from app.trade_qualification.high_vol_momentum_observation import (
    ORIGINAL_CANDIDATE_POLICY_SHA256,
    PROFILE_SHA256,
    HighVolOriginalSourcePin,
    observe_high_vol_momentum,
    timing_policy_sha256,
)
from app.trade_qualification.timing import TIMING_POLICIES, event_identity
from tests.unit.test_qualification_regime_admission import OBSERVED, fixture

VERSION = "synthetic-high-vol-v1"


def high_vol_source(strategy="volatility_expansion", direction="long"):
    market = fixture(strategy, direction)
    # Make 4H high from actual bars while retaining the original 15m setup and
    # 5m false-to-true momentum event. A derived shock may still veto the profile.
    market.candles["4H"] = [
        row.model_copy(
            update={"high": row.close + D(".65"), "low": row.close - D(".65")}
        )
        for row in market.candles["4H"]
    ]
    return market


def original_pin(market, strategy="volatility_expansion", direction="long"):
    checked = _prepared_market(market, OBSERVED)
    analysis = analyze_snapshot_at(checked, evaluated_at=OBSERVED, version=VERSION)
    detection = extract_trigger(
        checked,
        analysis,
        report_id="synthetic-high-vol",
        strategy=strategy,
        direction=direction,
        observed_at=OBSERVED,
        trigger_ttl_seconds=TIMING_POLICIES[strategy].trigger_ttl_seconds,
    )
    assert detection.trigger is not None and not detection.fail_codes
    timing = TIMING_POLICIES[strategy]
    return HighVolOriginalSourcePin(
        report_id="synthetic-high-vol",
        instrument_id=checked.instrument_id,
        strategy=strategy,
        direction=direction,
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
        original_candidate_expires_at=detection.trigger.expires_at,
    )


@pytest.mark.parametrize("strategy", ("breakout_continuation", "volatility_expansion"))
@pytest.mark.parametrize("direction", ("long", "short"))
def test_default_off_does_not_replay_or_change_legacy_route(strategy, direction):
    market = high_vol_source(strategy, direction)
    pin = original_pin(market, strategy, direction)
    original = deepcopy(market)
    route = route_regime(
        analyze_snapshot_at(market, evaluated_at=OBSERVED, version=VERSION)
    )
    result = observe_high_vol_momentum(
        market, original=pin, observed_at=OBSERVED, analysis_version=VERSION
    )
    assert result.code == "profile_disabled"
    assert result.analysis_version == VERSION
    assert not result.source_conditions_met
    assert result.recomputed_source_sha256 is None
    assert route.allowed_strategies == ()
    assert market == original
    assert result.admission == "DENY"
    assert result.execution_authority is False
    assert result.qualification_performed is False


@pytest.mark.parametrize("strategy", ("breakout_continuation", "volatility_expansion"))
@pytest.mark.parametrize("direction", ("long", "short"))
def test_source_chronology_and_policy_are_replayed_without_authority(
    strategy, direction
):
    market = high_vol_source(strategy, direction)
    pin = original_pin(market, strategy, direction)
    result = observe_high_vol_momentum(
        market,
        original=pin,
        observed_at=OBSERVED,
        analysis_version=VERSION,
        enabled=True,
    )
    assert result.recomputed_source_sha256 == pin.source_sha256
    assert result.recomputed_event_key == pin.event_key
    assert result.setup_time < result.trigger_time < result.trigger_expires_at
    assert result.effective_expires_at == pin.original_trigger_expires_at
    assert result.current_timing_policy_sha256 == pin.timing_policy_sha256
    assert (
        result.code
        == {
            "breakout_continuation": "source_htf_direction_missing",
            "volatility_expansion": "causal_shock_or_unknown",
        }[strategy]
    )
    assert result.admission == "DENY"
    assert result.execution_authority is False
    assert result.source_authenticity_verified is False


def test_original_policy_and_profile_pins_are_exact():
    market = high_vol_source()
    pin = original_pin(market)
    for changed in (
        {"candidate_policy_sha256": "a" * 64},
        {"profile_sha256": "b" * 64},
    ):
        result = observe_high_vol_momentum(
            market,
            original=pin.model_copy(update=changed),
            observed_at=OBSERVED,
            analysis_version=VERSION,
            enabled=True,
        )
        assert result.code == "original_policy_pin_mismatch"
        assert result.admission == "DENY"


def test_original_event_and_source_cannot_be_replaced_by_a_report_id():
    market = high_vol_source()
    pin = original_pin(market)
    for changed, expected in (
        ({"event_key": "c" * 64}, "original_event_changed"),
        ({"source_sha256": "d" * 64}, "original_source_changed"),
    ):
        result = observe_high_vol_momentum(
            market,
            original=pin.model_copy(update=changed),
            observed_at=OBSERVED,
            analysis_version=VERSION,
            enabled=True,
        )
        assert result.code == expected
        assert result.admission == "DENY"


def test_missing_prior_compression_or_future_source_fails_closed():
    market = high_vol_source()
    pin = original_pin(market)
    changed = deepcopy(market)
    for index in range(-20, -1):
        row = changed.candles["15m"][index]
        changed.candles["15m"][index] = row.model_copy(
            update={"high": row.close + D(".30"), "low": row.close - D(".30")}
        )
    no_compression = observe_high_vol_momentum(
        changed,
        original=pin,
        observed_at=OBSERVED,
        analysis_version=VERSION,
        enabled=True,
    )
    assert no_compression.code == "original_source_changed"
    checked = _prepared_market(changed, OBSERVED)
    analysis = analyze_snapshot_at(checked, evaluated_at=OBSERVED, version=VERSION)
    detection = extract_trigger(
        checked,
        analysis,
        report_id=pin.report_id,
        strategy=pin.strategy,
        direction=pin.direction,
        observed_at=OBSERVED,
        trigger_ttl_seconds=TIMING_POLICIES[pin.strategy].trigger_ttl_seconds,
    )
    assert detection.fail_codes == ("setup_missing",)
    missing_history = observe_high_vol_momentum(
        changed,
        original=pin.model_copy(update={"source_sha256": detection.source_sha256}),
        observed_at=OBSERVED,
        analysis_version=VERSION,
        enabled=True,
    )
    assert missing_history.code == "source_chronology_missing"
    future = deepcopy(market)
    row = future.candles["15m"][-1]
    future.candles["15m"][-1] = row.model_copy(
        update={"timestamp": OBSERVED + timedelta(days=1)}
    )
    invalid = observe_high_vol_momentum(
        future,
        original=pin,
        observed_at=OBSERVED,
        analysis_version=VERSION,
        enabled=True,
    )
    assert invalid.code == "source_invalid"
    assert not invalid.source_conditions_met


def test_no_switch_or_record_can_be_converted_into_execution_authority():
    market = high_vol_source()
    pin = original_pin(market)
    with pytest.raises(ValueError, match="exact boolean"):
        observe_high_vol_momentum(
            market,
            original=pin,
            observed_at=OBSERVED,
            analysis_version=VERSION,
            enabled=1,
        )
    result = observe_high_vol_momentum(
        market,
        original=pin,
        observed_at=OBSERVED,
        analysis_version=VERSION,
        enabled=True,
    )
    with pytest.raises(ValueError):
        type(result).model_validate(
            {**result.model_dump(round_trip=True), "execution_authority": True},
            strict=True,
        )


@pytest.mark.parametrize("bar_seconds", (150, 600))
def test_timing_policy_drift_cannot_extend_original_event(monkeypatch, bar_seconds):
    market = high_vol_source()
    pin = original_pin(market)
    original_timing = TIMING_POLICIES[pin.strategy]
    changed_timing = type(original_timing).model_validate(
        {**original_timing.model_dump(round_trip=True), "bar_seconds": bar_seconds},
        strict=True,
    )
    monkeypatch.setattr(subject, "TIMING_POLICIES", {pin.strategy: changed_timing})
    result = observe_high_vol_momentum(
        market,
        original=pin,
        observed_at=OBSERVED,
        analysis_version=VERSION,
        enabled=True,
    )
    assert result.code == "timing_policy_drift"
    assert result.admission == "DENY"
    assert result.execution_authority is False
    assert result.current_timing_policy_sha256 != pin.timing_policy_sha256
    assert result.effective_expires_at == min(
        pin.original_trigger_expires_at,
        pin.original_trigger_time + timedelta(seconds=bar_seconds),
    )


def test_original_candidate_deadline_can_only_shorten_event():
    market = high_vol_source()
    pin = original_pin(market)
    shortened = HighVolOriginalSourcePin.model_validate(
        {
            **pin.model_dump(round_trip=True),
            "original_candidate_expires_at": pin.original_trigger_time
            + timedelta(seconds=30),
        },
        strict=True,
    )
    result = observe_high_vol_momentum(
        market,
        original=shortened,
        observed_at=OBSERVED + timedelta(seconds=31),
        analysis_version=VERSION,
        enabled=True,
    )
    assert result.code == "original_event_expired_or_invalid"
    assert result.effective_expires_at == shortened.original_candidate_expires_at
    assert result.effective_expires_at < shortened.original_trigger_expires_at
    assert not result.source_conditions_met


def test_original_trigger_expiry_and_ttl_cannot_be_rewritten():
    pin = original_pin(high_vol_source())
    with pytest.raises(ValueError, match="original event timing pin"):
        HighVolOriginalSourcePin.model_validate(
            {
                **pin.model_dump(round_trip=True),
                "original_trigger_expires_at": pin.original_trigger_expires_at
                + timedelta(seconds=300),
            },
            strict=True,
        )


def test_coherently_shifted_original_trigger_is_not_the_same_event():
    market = high_vol_source()
    pin = original_pin(market)
    shifted = HighVolOriginalSourcePin.model_validate(
        {
            **pin.model_dump(round_trip=True),
            "original_trigger_time": pin.original_trigger_time + timedelta(seconds=30),
            "original_trigger_expires_at": pin.original_trigger_expires_at
            + timedelta(seconds=30),
            "original_candidate_expires_at": pin.original_candidate_expires_at
            + timedelta(seconds=30),
        },
        strict=True,
    )
    result = observe_high_vol_momentum(
        market,
        original=shifted,
        observed_at=OBSERVED,
        analysis_version=VERSION,
        enabled=True,
    )
    assert result.recomputed_event_key == shifted.event_key
    assert result.code == "original_event_expired_or_invalid"
    assert result.admission == "DENY"
