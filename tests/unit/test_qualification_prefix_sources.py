"""Actual evaluator proofs for synthetic sources, not a runtime gate coordinator."""

from dataclasses import replace
from datetime import timedelta
from decimal import ROUND_DOWN, Context, Decimal, Inexact, Rounded, localcontext

import pytest

from app.market.quality.candles import BAR_SECONDS, candle_closed_at
from app.strategies.base import StrategyContext
from app.strategies.conditions import assess_conditions
from app.strategies.regime import RouteDecision, route_regime
from app.trade_qualification.events import _copy_source, extract_trigger
from app.trade_qualification.location import evaluate_location, quote_fingerprint
from app.trade_qualification.models import MarketRegime
from app.trade_qualification.quote_collector import validate_collected_quote
from app.trade_qualification.timing import (
    TIMING_POLICIES,
    evaluate_timing,
    event_identity,
)
from tests.unit.qualification_prefix_fixtures import (
    CAPTURED_AT,
    DATA_POLICY,
    EVALUATED_AT,
    INSTRUMENT,
    MINIMUM_RISK_REWARD,
    MINIMUM_SCORE,
    STRATEGY,
    capture_prefix_source,
    prefix_market,
    prefix_source,
    qualify_data,
    restore_source,
    run_prefix_source,
)

D = Decimal


@pytest.fixture(scope="module", params=["long", "short"])
def trace(request):
    return run_prefix_source(prefix_source(request.param))


def test_raw_source_passes_real_g1_without_any_supplied_quality_or_analysis(trace):
    assert trace.source.market.quality == {}
    assert trace.data.passed and trace.data.gate.gate.value == "G1"
    assert trace.data.gate.measured_values["quality_recomputed"] is True
    assert trace.data.gate.measured_values["analysis_recomputed"] is True
    assert (
        trace.data.source_sha256
        == _copy_source(trace.market, trace.analysis, EVALUATED_AT)[3]
    )
    assert trace.analysis.generated_at == EVALUATED_AT
    assert trace.analysis.version == DATA_POLICY.analysis_version
    assert trace.source.policy.minimum_confirmed_bars == 200
    for timeframe, rows in trace.market.candles.items():
        assert len(rows) == 240 and all(row.confirmed for row in rows)
        assert trace.market.quality[timeframe].ok
        assert trace.market.quality[timeframe].issues == []
        assert all(candle_closed_at(row, timeframe) <= CAPTURED_AT for row in rows)
        assert trace.analysis.timeframe_analyses[
            timeframe
        ].last_closed_at == candle_closed_at(rows[-1], timeframe)
        assert (
            trace.analysis.timeframe_analyses[timeframe].indicators.ema200 is not None
        )
        assert EVALUATED_AT - candle_closed_at(rows[-1], timeframe) <= timedelta(
            seconds=BAR_SECONDS[timeframe] * 2
        )


def test_real_regime_and_strategy_conditions_allow_only_the_observed_family(trace):
    assert trace.analysis.overall_bias == trace.source.direction
    assert not trace.analysis.blockers
    assert trace.route == route_regime(trace.analysis)
    assert trace.route.regime == MarketRegime.TREND
    assert trace.route.decision == RouteDecision.ALLOW_SCORING
    assert STRATEGY in trace.route.allowed_strategies
    assert len(trace.route.allowed_strategies) < 8
    assert trace.strategy.score == 100
    assert trace.strategy.required_failures == trace.strategy.veto_failures == ()
    assert all(
        component.passed for component in trace.strategy.items if component.required
    )
    assert {
        component.code for component in trace.strategy.items if component.required
    } == {"4h_direction", "15m_fvg", "5m_trigger", "quality"}
    for timeframe in ("4H", "1H"):
        assert (
            trace.analysis.timeframe_analyses[timeframe].directional_bias
            == trace.source.direction
        )
        assert trace.analysis.timeframe_analyses[timeframe].volatility == "normal"


def test_raw_fvg_return_then_closed_momentum_produces_source_bound_trigger(trace):
    event = trace.detection
    assert event.fail_codes == () and event.trigger is not None
    assert event.source_sha256 == trace.data.source_sha256 == trace.zone.source_sha256
    assert event.source_timeframe == "5m"
    assert event.setup_time == CAPTURED_AT - timedelta(minutes=10)
    assert event.trigger.trigger_time == CAPTURED_AT
    assert event.setup_time <= trace.market.candles["5m"][-1].timestamp
    assert event.trigger.trigger_time == candle_closed_at(
        trace.market.candles["5m"][-1], "5m"
    )
    assert event.trigger.trigger_price == trace.market.candles["5m"][-1].close
    assert event.trigger.trigger_price == (
        D("100.99") if event.direction == "long" else D("99.01")
    )
    assert dict(event.setup_basis)["setup_timeframe"] == "15m"
    assert event.setup_type == "fvg_first_return"
    assert event.trigger.expires_at == CAPTURED_AT + timedelta(
        seconds=TIMING_POLICIES[STRATEGY].trigger_ttl_seconds
    )


def test_g6_and_g7_use_original_event_and_native_quote_without_repricing(trace):
    event, zone, source = trace.detection, trace.zone, trace.source
    reference = (
        source.quote.quote.ask if source.direction == "long" else source.quote.quote.bid
    )
    assert trace.timing.timing_valid and trace.timing.action == "CONTINUE"
    assert trace.timing.latest_valid_entry_time == event.trigger.expires_at
    assert trace.location.passed and trace.location.reference_price == reference
    assert zone.zone_low <= event.trigger.trigger_price == reference <= zone.zone_high
    assert zone.zone_low % source.tick_size == zone.zone_high % source.tick_size == 0
    assert trace.location.quote_sha256 == quote_fingerprint(source.quote.quote)
    assert trace.location.drift_bps == 0
    assert (
        not trace.location.execution_authority and not trace.route.execution_authority
    )
    assert (
        not trace.data.execution_authority
        and not trace.data.source_authenticity_verified
    )


def test_collected_source_uses_real_strict_public_parser_with_mock_wire_bytes(trace):
    collected = trace.source.quote
    assert validate_collected_quote(collected) == collected
    assert collected.size_unit == "contracts"
    assert collected.barrier_completed_at is None
    assert (
        not collected.execution_authority and not collected.source_authenticity_verified
    )
    assert tuple(item.role for item in collected.provenance) == (
        "ticker",
        "mark",
        "funding",
    )
    assert all(
        item.method == "GET" and item.response_body for item in collected.provenance
    )
    assert all(
        item.source_time <= item.received_at <= item.completed_at <= EVALUATED_AT
        for item in collected.provenance
    )
    assert (
        trace.source.reference.instrument_id
        == collected.quote.instrument_id
        == INSTRUMENT
    )
    assert (
        trace.source.reference.report_id
        == collected.quote.report_id
        == trace.source.report_id
    )
    assert trace.source.reference.bid == collected.quote.bid
    assert trace.source.reference.ask == collected.quote.ask


def test_repeat_source_chain_is_deterministic_and_does_not_modify_raw_input(trace):
    source = prefix_source(trace.source.direction)
    before = source.market.model_dump_json(round_trip=True)
    again = run_prefix_source(source)
    assert source.market.model_dump_json(round_trip=True) == before
    assert again.data == trace.data
    assert again.analysis == trace.analysis and again.route == trace.route
    assert again.detection == trace.detection and again.zone == trace.zone
    assert again.timing == trace.timing and again.location == trace.location
    assert again.strategy == trace.strategy


def test_real_source_chain_ignores_ambient_decimal_rounding_and_traps(trace):
    with localcontext(Context(prec=6, rounding=ROUND_DOWN)) as context:
        context.traps[Inexact] = True
        context.traps[Rounded] = True
        again = run_prefix_source(prefix_source(trace.source.direction))
    assert again.data == trace.data and again.detection == trace.detection
    assert again.strategy == trace.strategy and again.timing == trace.timing
    assert again.zone == trace.zone and again.location == trace.location


@pytest.mark.parametrize("direction", ["long", "short"])
def test_score_one_hundred_cannot_replace_a_missing_new_closed_event(direction):
    source = prefix_source(direction)
    rows = source.market.candles["5m"]
    price = rows[-1].close
    # Momentum is already true before the setup, then stays true. Current
    # predicates still agree, but no post-setup false→true event took place.
    for index in range(-3, 0):
        rows[index] = rows[index].model_copy(
            update={
                "open": price,
                "close": price,
                "high": price + D("0.01"),
                "low": price - D("0.01"),
            }
        )
    market, analysis = restore_source(qualify_data(source))
    assert STRATEGY in route_regime(analysis).allowed_strategies
    with localcontext(Context(prec=100)):
        conditions = assess_conditions(
            StrategyContext(analysis, market, MINIMUM_SCORE, MINIMUM_RISK_REWARD),
            STRATEGY,
        )
    assert conditions.score == 100
    assert conditions.required_failures == conditions.veto_failures == ()
    event = extract_trigger(
        market,
        analysis,
        report_id=source.report_id,
        strategy=STRATEGY,
        direction=direction,
        observed_at=source.evaluated_at,
        trigger_ttl_seconds=TIMING_POLICIES[STRATEGY].trigger_ttl_seconds,
    )
    assert event.trigger is None and event.fail_codes == ("trigger_missing",)


@pytest.mark.parametrize("direction", ["long", "short"])
def test_removing_native_gap_really_fails_required_strategy_condition(direction):
    source = prefix_source(direction)
    rows = source.market.candles["15m"]
    for index, row in enumerate(rows):
        rows[index] = row.model_copy(
            update={
                "open": D(100),
                "close": D(100),
                "high": D("100.05"),
                "low": D("99.95"),
            }
        )
    data = qualify_data(source)
    assert data.passed, data.gate
    market, analysis = restore_source(data)
    with localcontext(Context(prec=100)):
        evaluation = assess_conditions(
            StrategyContext(analysis, market, MINIMUM_SCORE, MINIMUM_RISK_REWARD),
            STRATEGY,
        )
    assert "15m_fvg" in evaluation.required_failures
    assert not analysis.timeframe_analyses["15m"].structure.fair_value_gaps


@pytest.mark.parametrize("direction", ["long", "short"])
def test_removing_closed_momentum_cannot_keep_trigger_or_required_pass(direction):
    source = prefix_source(direction)
    rows = source.market.candles["5m"]
    rows[-1] = rows[-2].model_copy(update={"timestamp": rows[-1].timestamp})
    data = qualify_data(source)
    assert data.passed, data.gate
    market, analysis = restore_source(data)
    event = extract_trigger(
        market,
        analysis,
        report_id=source.report_id,
        strategy=STRATEGY,
        direction=direction,
        observed_at=source.evaluated_at,
        trigger_ttl_seconds=TIMING_POLICIES[STRATEGY].trigger_ttl_seconds,
    )
    assert event.trigger is None and "trigger_missing" in event.fail_codes
    with localcontext(Context(prec=100)):
        evaluation = assess_conditions(
            StrategyContext(analysis, market, MINIMUM_SCORE, MINIMUM_RISK_REWARD),
            STRATEGY,
        )
    assert "5m_trigger" in evaluation.required_failures


def test_raw_source_missing_timeframe_fails_g1_without_repairing_analysis(trace):
    source = prefix_source(trace.source.direction)
    source.market.candles.pop("4H")
    result = qualify_data(source)
    assert not result.passed and result.gate.code == "missing_timeframe"


def test_real_location_rejects_quote_outside_original_zone(trace):
    source, event, zone = trace.source, trace.detection, trace.zone
    original = event.model_dump_json(round_trip=True)
    outside = (
        zone.zone_high + D("0.01")
        if source.direction == "long"
        else zone.zone_low - D("0.01")
    )
    quoted = source.quote.quote.model_copy(
        update={
            "bid": outside - D("0.01") if source.direction == "long" else outside,
            "ask": outside if source.direction == "long" else outside + D("0.01"),
        }
    )
    result = evaluate_location(
        report_id=source.report_id,
        instrument_id=INSTRUMENT,
        direction=source.direction,
        zone=zone,
        candidate_entry=event.trigger.trigger_price,
        quote=quoted,
        current_time=source.evaluated_at,
        max_quote_age_seconds=source.policy.maximum_quote_age_seconds,
    )
    assert not result.passed and result.code in {
        "reference_outside_entry_zone",
        "entry_zone_missed",
    }
    assert event.model_dump_json(round_trip=True) == original


def test_consumed_event_and_expired_source_do_not_repeat_positive_prefix(trace):
    source, event = trace.source, trace.detection
    timing = evaluate_timing(
        event,
        current_time=source.evaluated_at,
        candidate_created_at=source.evaluated_at,
        candidate_expires_at=event.trigger.expires_at,
        reference_price=event.trigger.trigger_price,
        consumed_event_keys=frozenset({event_identity(event)}),
    )
    assert not timing.timing_valid and timing.code == "stale_candidate"
    old = qualify_data(
        replace(source, evaluated_at=EVALUATED_AT + timedelta(seconds=11))
    )
    assert not old.passed and old.gate.code == "stale_market_data"


@pytest.mark.parametrize("direction", ["long", "short"])
async def test_async_fixture_can_supply_source_inside_existing_event_loop(direction):
    source = await capture_prefix_source(
        direction, report_id=f"async-prefix-{direction}"
    )
    assert qualify_data(source).passed
    assert (
        source.quote.quote.report_id == source.reference.report_id == source.report_id
    )


@pytest.mark.parametrize("value", ["neutral", "LONG", None, True])
def test_fixture_does_not_invent_direction_or_fallback_strategy(value):
    with pytest.raises(ValueError):
        prefix_market(value)
