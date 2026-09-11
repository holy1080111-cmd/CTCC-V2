"""Pre-score routing integration; all market values and evaluators are synthetic."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from app.config.settings import Settings
from app.domain.analysis import (
    IndicatorSnapshot,
    MultiTimeframeAnalysis,
    StructureSnapshot,
    TimeframeAnalysis,
)
from app.domain.strategy import StrategyDecision, StrategyEvaluation, TradeCandidate
from app.regime.classifier import classify_regime
from app.strategies.registry import STRATEGIES, STRATEGY_EVALUATORS, STRATEGY_NAMES
from app.strategies.service import StrategyService
from app.trade_qualification.models import MarketRegime

D = Decimal
NOW = datetime(2024, 1, 2, tzinfo=UTC)
TREND_NAMES = {
    "trend_pullback",
    "breakout_continuation",
    "fvg_return",
    "order_block_return",
}


def analysis_snapshot() -> MultiTimeframeAnalysis:
    return MultiTimeframeAnalysis(
        symbol="BTC/USDT:USDT",
        instrument_id="BTC-USDT-SWAP",
        price=D("100"),
        regime="bull_trend",
        overall_bias="long",
        alignment_score=100,
        trade_ready=True,
        timeframe_analyses={
            tf: TimeframeAnalysis(
                timeframe=tf,
                candle_count=250,
                last_closed_at=NOW - timedelta(minutes=5),
                close=D("100"),
                data_quality_ok=True,
                indicators=IndicatorSnapshot(ema20=D("100")),
                structure=StructureSnapshot(
                    trend="bullish",
                    swing_structure="HH/HL",
                    support_levels=[D("99")],
                    resistance_levels=[D("101")],
                ),
                volatility="normal",
                directional_bias="long",
            )
            for tf in ("4H", "1H", "15m", "5m")
        },
        generated_at=NOW,
    )


def evaluation(name: str) -> StrategyEvaluation:
    return StrategyEvaluation(
        strategy=name,
        direction="long",
        eligible=True,
        completion_ratio=D(1),
        score=100,
        candidate=TradeCandidate(
            strategy=name,
            direction="long",
            score=100,
            entry=D("100"),
            stop_loss=D("95"),
            take_profit=D("110"),
            risk_reward=D(2),
            invalidation="synthetic only",
            expires_at=NOW + timedelta(minutes=5),
        ),
    )


def service_with_spies(monkeypatch, analysis):
    class FakeMarket:
        async def snapshot(self, symbol, candle_limit):
            return object()

    class FakeAnalysis:
        def analyze_snapshot(self, snapshot):
            return analysis

    spies = {name: Mock(return_value=evaluation(name)) for name in STRATEGY_NAMES}
    monkeypatch.setattr("app.strategies.service.STRATEGY_EVALUATORS", spies)
    monkeypatch.setattr(
        "app.strategies.service.get_settings", lambda: Settings(_env_file=None)
    )
    return StrategyService(FakeMarket(), FakeAnalysis()), spies


def test_registry_is_named_complete_and_immutable():
    assert tuple(STRATEGY_EVALUATORS) == STRATEGY_NAMES
    assert tuple(STRATEGY_EVALUATORS.values()) == STRATEGIES
    assert len(STRATEGY_EVALUATORS) == 8
    with pytest.raises(TypeError):
        STRATEGY_EVALUATORS["new"] = lambda context: None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("execution_authority", True),
        ("allowed_strategies", ("volatility_expansion",)),
        ("decision", "allow_scoring"),
    ],
)
async def test_api_routing_audit_remains_frozen_after_serialization(
    monkeypatch, field, value
):
    service, _ = service_with_spies(monkeypatch, analysis_snapshot())
    result = await service.evaluate("BTC-USDT-SWAP")
    for audit in (
        result.regime_route,
        StrategyDecision.model_validate_json(result.model_dump_json()).regime_route,
    ):
        with pytest.raises(ValidationError, match="frozen_instance"):
            setattr(audit, field, value)
        assert audit.execution_authority is False


@pytest.mark.asyncio
async def test_trend_only_calls_compatible_scorers_and_keeps_unscored_audit(
    monkeypatch,
):
    analysis = analysis_snapshot()
    original = analysis.model_dump_json()
    service, spies = service_with_spies(monkeypatch, analysis)
    result = await service.evaluate("BTC-USDT-SWAP")
    assert {name for name, spy in spies.items() if spy.called} == TREND_NAMES
    assert all(spy.call_count == (name in TREND_NAMES) for name, spy in spies.items())
    assert len(result.evaluations) == 8
    for item in result.evaluations:
        assert item.scoring_performed == (item.strategy in TREND_NAMES)
        if not item.scoring_performed:
            assert not item.eligible and item.candidate is None and item.score == 0
            assert item.vetoes == ["regime_not_permitted"]
    assert result.selected_strategy in TREND_NAMES
    assert result.regime_route.regime == MarketRegime.TREND
    assert result.regime_route.execution_authority is False
    assert len(result.regime_route.snapshot_sha256) == 64
    assert analysis.model_dump_json() == original
    rebuilt = StrategyDecision.model_validate_json(result.model_dump_json())
    assert rebuilt == result


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault", ["missing", "quality", "mismatch", "risk_off", "extreme"]
)
async def test_bad_regimes_never_call_any_scorer_even_if_all_would_score_100(
    monkeypatch, fault
):
    analysis = analysis_snapshot()
    if fault == "missing":
        del analysis.timeframe_analyses["5m"]
    elif fault == "quality":
        analysis.timeframe_analyses["1H"].data_quality_ok = False
    elif fault == "mismatch":
        analysis.regime = "range_or_transition"
    elif fault == "risk_off":
        analysis.blockers = ["mathematical_core_regime_instability"]
    else:
        analysis.timeframe_analyses["5m"].volatility = "extreme"
    service, spies = service_with_spies(monkeypatch, analysis)
    result = await service.evaluate("BTC-USDT-SWAP")
    assert not any(spy.called for spy in spies.values())
    assert result.decision == "no_trade" and result.selected_candidate is None
    assert all(not item.scoring_performed for item in result.evaluations)
    assert result.regime_route.decision == "no_trade"
    assert result.regime_route.fail_codes
    assert set(result.regime_route.fail_codes) <= set(result.blockers)


@pytest.mark.asyncio
async def test_range_is_not_forced_to_have_identical_directional_htfs(monkeypatch):
    analysis = analysis_snapshot()
    for tf in ("4H", "1H"):
        analysis.timeframe_analyses[tf].structure.trend = "neutral"
        analysis.timeframe_analyses[tf].directional_bias = "neutral"
    analysis.regime = classify_regime(analysis.timeframe_analyses)
    analysis.overall_bias = "neutral"
    analysis.trade_ready = False
    analysis.blockers = ["multi_timeframe_not_aligned"]
    service, spies = service_with_spies(monkeypatch, analysis)
    result = await service.evaluate("BTC-USDT-SWAP")
    assert {name for name, spy in spies.items() if spy.called} == {"range_reversal"}
    assert result.regime_route.regime == MarketRegime.RANGE
    assert "multi_timeframe_not_aligned" in result.blockers
    assert result.regime_route.execution_authority is False


@pytest.mark.asyncio
async def test_expansion_missing_history_excludes_family_not_valid_breakout_scoring(
    monkeypatch,
):
    analysis = analysis_snapshot()
    analysis.timeframe_analyses["15m"].volatility = "high"
    analysis.timeframe_analyses["15m"].structure.bos = "up"
    service, spies = service_with_spies(monkeypatch, analysis)
    result = await service.evaluate("BTC-USDT-SWAP")
    assert {name for name, spy in spies.items() if spy.called} == {
        "breakout_continuation"
    }
    assert result.selected_strategy == "breakout_continuation"
    assert "compression_history_missing" in result.regime_route.fail_codes
    assert "compression_history_missing" not in result.blockers
    assert not spies["volatility_expansion"].called


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault", ["evaluation_name", "candidate_name", "direction", "veto", "not_scored"]
)
async def test_evaluator_output_cannot_spoof_routed_identity_or_override_veto(
    monkeypatch, fault
):
    service, spies = service_with_spies(monkeypatch, analysis_snapshot())
    forged = evaluation("trend_pullback")
    if fault == "evaluation_name":
        forged.strategy = "range_reversal"
    elif fault == "candidate_name":
        forged.candidate.strategy = "range_reversal"
    elif fault == "direction":
        forged.direction = "short"
    elif fault == "veto":
        forged.vetoes = ["existing_veto"]
    else:
        forged.scoring_performed = False
    for name, spy in spies.items():
        spy.return_value = (
            forged
            if name == "trend_pullback"
            else evaluation(name).model_copy(
                update={"eligible": False, "candidate": None}
            )
        )
    result = await service.evaluate("BTC-USDT-SWAP")
    assert result.decision == "no_trade" and result.selected_candidate is None
    if fault in {"evaluation_name", "candidate_name", "direction"}:
        assert "strategy_identity_mismatch" in result.evaluations[0].vetoes
        assert result.evaluations[0].strategy == "trend_pullback"
        assert result.evaluations[0].candidate is None


@pytest.mark.asyncio
async def test_operator_disable_remains_a_veto_after_valid_routing(monkeypatch):
    service, _ = service_with_spies(monkeypatch, analysis_snapshot())
    result = await service.evaluate("BTC-USDT-SWAP", disabled_strategies=TREND_NAMES)
    assert result.decision == "no_trade" and result.selected_candidate is None
    assert all(
        "strategy_disabled_by_operator" in item.vetoes
        for item in result.evaluations
        if item.strategy in TREND_NAMES
    )
