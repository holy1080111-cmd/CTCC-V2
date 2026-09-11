"""Synthetic snapshot routing only; no evaluator, transport or trade is run."""

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from decimal import (
    ROUND_DOWN,
    ROUND_UP,
    Context,
    Decimal,
    InvalidOperation,
    localcontext,
)

import pytest

from app.domain.analysis import (
    CausalStateSnapshot,
    IndicatorSnapshot,
    MultiTimeframeAnalysis,
    StructureSnapshot,
    TimeframeAnalysis,
)
from app.regime.classifier import classify_regime
from app.strategies.regime import (
    REQUIRED_TIMEFRAMES,
    TREND_STRATEGIES,
    RouteDecision,
    route_regime,
)
from app.trade_qualification.models import MarketRegime

D = Decimal
NOW = datetime(2024, 1, 2, tzinfo=UTC)


def view(timeframe: str, *, trend="bullish", bias="long") -> TimeframeAnalysis:
    return TimeframeAnalysis(
        timeframe=timeframe,
        candle_count=250,
        last_closed_at=NOW - timedelta(minutes=1),
        close=D("100"),
        data_quality_ok=True,
        indicators=IndicatorSnapshot(
            ema20=D("100"),
            atr14=D("1"),
            atr_pct=D("0.5"),
            rsi14=D("55"),
            macd_histogram=D("0.5"),
            volume_ratio20=D("1.2"),
        ),
        structure=StructureSnapshot(
            trend=trend,
            swing_structure="synthetic structure",
            support_levels=[D("98"), D("99")],
            resistance_levels=[D("102"), D("101")],
        ),
        volatility="normal",
        directional_bias=bias,
    )


def snapshot(*, range_context=False) -> MultiTimeframeAnalysis:
    views = {timeframe: view(timeframe) for timeframe in REQUIRED_TIMEFRAMES}
    if range_context:
        for timeframe in ("4H", "1H"):
            views[timeframe].structure.trend = "neutral"
            views[timeframe].directional_bias = "neutral"
    return MultiTimeframeAnalysis(
        symbol="BTC/USDT:USDT",
        instrument_id="BTC-USDT-SWAP",
        price=D("100"),
        regime=classify_regime(views),
        overall_bias="neutral" if range_context else "long",
        alignment_score=50 if range_context else 100,
        trade_ready=not range_context,
        blockers=["multi_timeframe_not_aligned"] if range_context else [],
        timeframe_analyses=views,
        generated_at=NOW,
        version="synthetic.v1",
    )


def refresh_regime(analysis):
    analysis.regime = classify_regime(analysis.timeframe_analyses)
    return analysis


def test_trend_only_allows_the_frozen_trend_families_to_be_scored():
    route = route_regime(snapshot())
    assert route.regime == MarketRegime.TREND
    assert route.decision == RouteDecision.ALLOW_SCORING
    assert route.allowed_strategies == TREND_STRATEGIES
    assert route.execution_authority is False
    assert len(route.snapshot_sha256) == 64
    assert route.reasons
    assert "range_reversal" not in route.allowed_strategies
    assert "volatility_expansion" not in route.allowed_strategies


def test_bear_trend_is_direction_symmetric():
    analysis = snapshot()
    for timeframe in REQUIRED_TIMEFRAMES:
        analysis.timeframe_analyses[timeframe].structure.trend = "bearish"
        analysis.timeframe_analyses[timeframe].directional_bias = "short"
    analysis.overall_bias = "short"
    route = route_regime(refresh_regime(analysis))
    assert route.regime == MarketRegime.TREND
    assert route.allowed_strategies == TREND_STRATEGIES


def test_trend_htf_permission_is_strategy_specific_not_all_four_timeframes_equal():
    analysis = snapshot()
    analysis.timeframe_analyses["1H"].directional_bias = "short"
    route = route_regime(analysis)
    assert route.regime == MarketRegime.TREND
    assert route.allowed_strategies == (
        "breakout_continuation",
        "fvg_return",
        "order_block_return",
    )
    assert "trend_pullback_1h_permission_missing" in route.fail_codes
    analysis.timeframe_analyses["4H"].directional_bias = "short"
    assert route_regime(analysis).decision == RouteDecision.NO_TRADE


def test_range_requires_neutral_htfs_and_an_intact_price_bracket():
    analysis = snapshot(range_context=True)
    assert analysis.regime == "range_or_transition"
    route = route_regime(analysis)
    assert route.regime == MarketRegime.RANGE
    assert route.allowed_strategies == ("range_reversal",)
    assert ("range.support", "99") in route.snapshot_basis
    assert ("range.resistance", "101") in route.snapshot_basis
    assert ("blockers", "multi_timeframe_not_aligned") in route.snapshot_basis
    assert analysis.blockers == ["multi_timeframe_not_aligned"]
    assert "sweep_transition_history_missing" in route.fail_codes


def test_range_never_promotes_an_unreachable_neutral_htf_choch_to_reversal():
    analysis = snapshot(range_context=True)
    analysis.timeframe_analyses["1H"].structure.choch = "up"
    route = route_regime(analysis)
    assert route.allowed_strategies == ("range_reversal",)
    assert analysis.timeframe_analyses["4H"].directional_bias == "neutral"
    assert "structural_reversal_history_missing" in route.fail_codes


@pytest.mark.parametrize(
    "missing", ["neutral_trend", "neutral_bias", "support", "resistance"]
)
def test_range_or_transition_label_alone_never_proves_a_range(missing):
    analysis = snapshot(range_context=True)
    if missing == "neutral_trend":
        analysis.timeframe_analyses["4H"].structure.trend = "bullish"
    elif missing == "neutral_bias":
        analysis.timeframe_analyses["4H"].directional_bias = "long"
    elif missing == "support":
        analysis.timeframe_analyses["15m"].structure.support_levels = []
    else:
        analysis.timeframe_analyses["15m"].structure.resistance_levels = []
    route = route_regime(refresh_regime(analysis))
    assert route.regime == MarketRegime.UNKNOWN
    assert route.decision == RouteDecision.NO_TRADE
    assert route.allowed_strategies == ()


@pytest.mark.parametrize("event", ["bos", "choch"])
def test_current_break_does_not_fabricate_range_or_sweep_chronology(event):
    analysis = snapshot(range_context=True)
    setattr(analysis.timeframe_analyses["15m"].structure, event, "up")
    route = route_regime(analysis)
    assert route.regime == MarketRegime.UNKNOWN
    assert route.allowed_strategies == ()
    assert "range_transition_history_missing" in route.fail_codes


def expansion_snapshot():
    analysis = snapshot()
    analysis.timeframe_analyses["1H"].structure.trend = "neutral"
    analysis.timeframe_analyses["1H"].directional_bias = "neutral"
    analysis.timeframe_analyses["15m"].volatility = "high"
    analysis.timeframe_analyses["15m"].structure.bos = "up"
    return refresh_regime(analysis)


def test_current_expansion_routes_breakout_but_does_not_invent_compression_history():
    route = route_regime(expansion_snapshot())
    assert route.regime == MarketRegime.EXPANSION
    assert route.allowed_strategies == ("breakout_continuation",)
    assert "compression_history_missing" in route.fail_codes
    assert "volatility_expansion" not in route.allowed_strategies


@pytest.mark.parametrize("invalid", ["missing_bos", "4h_opposed", "1h_opposed"])
def test_expansion_requires_current_bos_and_nonopposed_htf_permission(invalid):
    analysis = expansion_snapshot()
    if invalid == "missing_bos":
        analysis.timeframe_analyses["15m"].structure.bos = None
    else:
        timeframe = "4H" if invalid == "4h_opposed" else "1H"
        analysis.timeframe_analyses[timeframe].directional_bias = "short"
    route = route_regime(refresh_regime(analysis))
    assert route.decision == RouteDecision.NO_TRADE
    assert route.allowed_strategies == ()


def test_compression_is_wait_not_an_active_entry_even_when_structure_is_trending():
    analysis = snapshot()
    for timeframe in ("4H", "1H"):
        analysis.timeframe_analyses[timeframe].volatility = "low"
    route = route_regime(refresh_regime(analysis))
    assert route.regime == MarketRegime.COMPRESSION
    assert route.decision == RouteDecision.NO_TRADE
    assert route.allowed_strategies == ()


@pytest.mark.parametrize("timeframe", REQUIRED_TIMEFRAMES)
def test_extreme_volatility_on_any_required_timeframe_is_no_trade(timeframe):
    analysis = snapshot()
    analysis.timeframe_analyses[timeframe].volatility = "extreme"
    route = route_regime(refresh_regime(analysis))
    assert route.regime == MarketRegime.HIGH_VOLATILITY
    assert route.allowed_strategies == ()


def test_existing_causal_state_instability_is_risk_off_not_a_new_trading_class():
    analysis = snapshot()
    analysis.timeframe_analyses["4H"].indicators.causal_state = CausalStateSnapshot(
        window=20,
        log_velocity_per_bar=D(0),
        log_acceleration_per_bar2=D(0),
        velocity_std=D(0),
        acceleration_std=D(0),
        velocity_z=D(0),
        acceleration_z=D(0),
        innovation_z=D(0),
        shock_score=D("0.7"),
        confidence=D("0.8"),
        direction="flat",
        outlier_count=0,
    )
    route = route_regime(refresh_regime(analysis))
    assert route.regime == MarketRegime.RISK_OFF
    assert route.decision == RouteDecision.NO_TRADE
    assert route.allowed_strategies == ()


def test_existing_mathematical_instability_blocker_remains_a_risk_veto():
    analysis = snapshot()
    analysis.blockers = ["mathematical_core_regime_instability"]
    assert route_regime(analysis).regime == MarketRegime.RISK_OFF


@pytest.mark.parametrize("timeframe", REQUIRED_TIMEFRAMES)
def test_missing_or_bad_quality_required_snapshot_fails_closed(timeframe):
    analysis = snapshot()
    analysis.timeframe_analyses.pop(timeframe)
    route = route_regime(analysis)
    assert route.regime == MarketRegime.UNKNOWN
    assert route.allowed_strategies == ()
    assert f"missing_timeframe:{timeframe}" in route.fail_codes
    analysis = snapshot()
    analysis.timeframe_analyses[timeframe].data_quality_ok = False
    assert route_regime(analysis).regime == MarketRegime.UNKNOWN


@pytest.mark.parametrize("timeframe", REQUIRED_TIMEFRAMES)
def test_positive_quality_flag_cannot_override_reported_quality_issues(timeframe):
    analysis = snapshot()
    target = analysis.timeframe_analyses[timeframe]
    assert target.data_quality_ok is True
    target.data_quality_issues = ["error:missing_candle"]
    route = route_regime(analysis)
    assert route.regime == MarketRegime.UNKNOWN
    assert route.decision == RouteDecision.NO_TRADE
    assert route.fail_codes == ("data_quality_blocked",)
    assert route.allowed_strategies == ()


@pytest.mark.parametrize(
    "blocker",
    ["4H_data_quality", "future_source_blocker", "mathematical_core_opposes_alignment"],
)
def test_existing_data_and_unknown_analysis_blockers_cannot_be_ignored(blocker):
    analysis = snapshot()
    analysis.blockers = [blocker]
    route = route_regime(analysis)
    assert route.regime == MarketRegime.UNKNOWN
    assert route.allowed_strategies == ()


@pytest.mark.parametrize(
    "invalid",
    [
        "nan_price",
        "negative_price",
        "bool_score",
        "float_close",
        "negative_close",
        "nan_indicator",
        "negative_atr",
        "rsi_outside_range",
        "wrong_timeframe",
        "empty_candles",
        "nonboolean_quality",
        "invalid_bias",
        "invalid_volatility",
        "invalid_structure",
        "naive_timestamp",
        "future_candle",
        "negative_level",
        "nan_unused_level",
    ],
)
def test_invalid_or_nonfinite_snapshot_fields_return_unknown_without_coercion(invalid):
    analysis = snapshot()
    target = analysis.timeframe_analyses["15m"]
    if invalid == "nan_price":
        analysis.price = D("NaN")
    elif invalid == "negative_price":
        analysis.price = D("-1")
    elif invalid == "bool_score":
        analysis.alignment_score = True
    elif invalid == "float_close":
        target.close = 100.0
    elif invalid == "negative_close":
        target.close = D("-1")
    elif invalid == "nan_indicator":
        target.indicators.macd = D("NaN")
    elif invalid == "negative_atr":
        target.indicators.atr14 = D("-1")
    elif invalid == "rsi_outside_range":
        target.indicators.rsi14 = D("101")
    elif invalid == "wrong_timeframe":
        target.timeframe = "1m"
    elif invalid == "empty_candles":
        target.candle_count = 0
    elif invalid == "nonboolean_quality":
        target.data_quality_ok = 1
    elif invalid == "invalid_bias":
        target.directional_bias = "maybe"
    elif invalid == "invalid_volatility":
        target.volatility = "unknown"
    elif invalid == "invalid_structure":
        target.structure.trend = "missing"
    elif invalid == "naive_timestamp":
        analysis.generated_at = NOW.replace(tzinfo=None)
    elif invalid == "future_candle":
        target.last_closed_at = NOW + timedelta(seconds=1)
    elif invalid == "negative_level":
        target.structure.support_levels = [D("-1")]
    else:
        target.structure.resistance_levels = [D("NaN")]
    route = route_regime(analysis)
    assert route.regime == MarketRegime.UNKNOWN
    assert route.decision == RouteDecision.NO_TRADE
    assert route.allowed_strategies == ()
    assert route.fail_codes == ("invalid_analysis_snapshot",)


@pytest.mark.parametrize("measurement", ["NaN", "sNaN", "Infinity", "-Infinity"])
@pytest.mark.parametrize("field", ["price", "close", "indicator", "level"])
def test_all_nonfinite_decimal_forms_are_closed_before_comparisons(measurement, field):
    analysis = snapshot()
    target = analysis.timeframe_analyses["15m"]
    value = D(measurement)
    if field == "price":
        analysis.price = value
    elif field == "close":
        target.close = value
    elif field == "indicator":
        target.indicators.macd_histogram = value
    else:
        target.structure.support_levels = [value]
    route = route_regime(analysis)
    assert route.regime == MarketRegime.UNKNOWN
    assert route.decision == RouteDecision.NO_TRADE
    assert route.fail_codes == ("invalid_analysis_snapshot",)
    assert route.allowed_strategies == ()


def test_decimal_validation_exception_is_a_closed_route_not_an_unhandled_error(
    monkeypatch,
):
    def invalid_dump(*_args, **_kwargs):
        raise InvalidOperation("synthetic decimal serialization failure")

    analysis = snapshot()
    monkeypatch.setattr(MultiTimeframeAnalysis, "model_dump", invalid_dump)
    route = route_regime(analysis)
    assert route.regime == MarketRegime.UNKNOWN
    assert route.decision == RouteDecision.NO_TRADE
    assert route.fail_codes == ("invalid_analysis_snapshot",)


@pytest.mark.parametrize(
    "claimed", ["Unknown", "Expansion", "Risk-Off", "unrecognized", "bear_trend"]
)
def test_caller_labels_do_not_override_the_existing_snapshot_evidence(claimed):
    analysis = snapshot()
    analysis.regime = claimed
    route = route_regime(analysis)
    assert route.regime == MarketRegime.UNKNOWN
    assert route.allowed_strategies == ()


def test_routes_are_deterministic_immutable_and_detached_from_mutable_input():
    analysis = snapshot(range_context=True)
    route = route_regime(analysis)
    for precision in (9, 28, 50):
        for rounding in (ROUND_UP, ROUND_DOWN):
            with localcontext(Context(prec=precision, rounding=rounding)):
                assert route_regime(analysis) == route
    analysis.timeframe_analyses = dict(
        reversed(tuple(analysis.timeframe_analyses.items()))
    )
    assert route_regime(analysis) == route
    with pytest.raises(FrozenInstanceError):
        route.allowed_strategies = ()
    previous_basis = route.snapshot_basis
    analysis.timeframe_analyses["15m"].structure.support_levels.clear()
    assert route.snapshot_basis == previous_basis
    assert route_regime(analysis).snapshot_sha256 != route.snapshot_sha256
    assert route_regime(analysis).allowed_strategies == ()


def test_no_supported_snapshot_routes_all_eight_strategies_to_compete():
    for analysis in (snapshot(), snapshot(range_context=True), expansion_snapshot()):
        route = route_regime(analysis)
        assert 0 < len(route.allowed_strategies) <= 4
        assert route.execution_authority is False
