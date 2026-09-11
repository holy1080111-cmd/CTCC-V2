from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.domain.analysis import (
    IndicatorSnapshot,
    MultiTimeframeAnalysis,
    StructureSnapshot,
    TimeframeAnalysis,
)
from app.domain.strategy import (
    MathematicalConfirmation,
    StrategyEvaluation,
    TradeCandidate,
)
from app.strategies.service import StrategyService

D = Decimal


class FakeMarketService:
    async def snapshot(self, symbol: str, candle_limit: int):
        return object()


class FakeAnalysisService:
    def analyze_snapshot(self, snapshot) -> MultiTimeframeAnalysis:
        now = datetime.now(UTC)
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
                    last_closed_at=now - timedelta(minutes=5),
                    close=D("100"),
                    data_quality_ok=True,
                    indicators=IndicatorSnapshot(ema20=D("100")),
                    structure=StructureSnapshot(
                        trend="bullish", swing_structure="HH/HL"
                    ),
                    volatility="normal",
                    directional_bias="long",
                )
                for tf in ("4H", "1H", "15m", "5m")
            },
            generated_at=now,
        )


def _evaluation(
    status: str,
    risk_grade: str,
    *,
    score: int = 95,
    strategy: str = "trend_pullback",
    auxiliary_bonus: int = 0,
) -> StrategyEvaluation:
    candidate = TradeCandidate(
        strategy=strategy,
        direction="long",
        score=score,
        entry=D("100"),
        stop_loss=D("95"),
        take_profit=D("110"),
        risk_reward=D("2"),
        invalidation="stop",
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
        mathematical_confirmation=MathematicalConfirmation(
            status=status,
            risk_grade=risk_grade,
            confidence=D("0.8"),
            directional_support=(D("-0.8") if status == "opposed" else D("0.8")),
            reliability=D("0.8"),
            coverage=D("0.9"),
            consensus=D("0.9"),
            instability=(D("0.9") if status == "unstable" else D("0.1")),
            auxiliary_bonus=auxiliary_bonus,
            auxiliary_directional_support=(D("0.8") if auxiliary_bonus > 0 else D("0")),
            auxiliary_component_codes=(["structure"] if auxiliary_bonus > 0 else []),
            component_codes=["derivative", "state", "conformal"],
        ),
    )
    return StrategyEvaluation(
        strategy=strategy,
        direction="long",
        eligible=True,
        completion_ratio=D("0.95"),
        score=score,
        candidate=candidate,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "risk_grade", "veto"),
    [
        ("opposed", "blocked", "mathematical_core_opposes_trade_direction"),
        ("unstable", "blocked", "mathematical_core_regime_instability"),
    ],
)
async def test_strategy_selection_applies_mathematical_veto(
    monkeypatch, status: str, risk_grade: str, veto: str
) -> None:
    monkeypatch.setattr(
        "app.strategies.service.STRATEGY_EVALUATORS",
        {"trend_pullback": lambda context: _evaluation(status, risk_grade)},
    )
    service = StrategyService(
        market_service=FakeMarketService(),
        analysis_service=FakeAnalysisService(),
    )

    result = await service.evaluate("BTC-USDT-SWAP")

    assert result.decision == "no_trade"
    assert result.selected_candidate is None
    assert veto in result.evaluations[0].vetoes


@pytest.mark.asyncio
async def test_strategy_selection_preserves_confirmed_candidate(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.strategies.service.STRATEGY_EVALUATORS",
        {"trend_pullback": lambda context: _evaluation("confirmed", "high")},
    )
    service = StrategyService(
        market_service=FakeMarketService(),
        analysis_service=FakeAnalysisService(),
    )

    result = await service.evaluate("BTC-USDT-SWAP")

    assert result.decision == "long"
    assert result.selected_candidate is not None
    assert result.selected_candidate.score == 95


@pytest.mark.asyncio
async def test_selection_uses_downward_mathematical_cap_before_raw_score(
    monkeypatch,
) -> None:
    low_raw_high = _evaluation("confirmed", "low", score=95, strategy="trend_pullback")
    high_raw_lower = _evaluation(
        "confirmed", "high", score=90, strategy="breakout_continuation"
    )
    monkeypatch.setattr(
        "app.strategies.service.STRATEGY_EVALUATORS",
        {
            "trend_pullback": lambda context: low_raw_high,
            "breakout_continuation": lambda context: high_raw_lower,
        },
    )
    service = StrategyService(
        market_service=FakeMarketService(),
        analysis_service=FakeAnalysisService(),
    )

    result = await service.evaluate("BTC-USDT-SWAP")

    assert result.selected_strategy == "breakout_continuation"
    assert result.selected_candidate is not None
    assert result.selected_candidate.score == 90


@pytest.mark.asyncio
async def test_auxiliary_bonus_breaks_only_a_true_validated_tie(
    monkeypatch,
) -> None:
    no_bonus = _evaluation("confirmed", "high", score=90, strategy="trend_pullback")
    auxiliary = _evaluation(
        "confirmed",
        "high",
        score=90,
        strategy="breakout_continuation",
        auxiliary_bonus=3,
    )
    monkeypatch.setattr(
        "app.strategies.service.STRATEGY_EVALUATORS",
        {
            "trend_pullback": lambda context: no_bonus,
            "breakout_continuation": lambda context: auxiliary,
        },
    )
    service = StrategyService(
        market_service=FakeMarketService(),
        analysis_service=FakeAnalysisService(),
    )

    result = await service.evaluate("BTC-USDT-SWAP")

    assert result.selected_strategy == "breakout_continuation"


@pytest.mark.asyncio
async def test_auxiliary_bonus_cannot_overcome_a_lower_execution_score(
    monkeypatch,
) -> None:
    higher_score = _evaluation("confirmed", "high", score=91, strategy="trend_pullback")
    lower_with_bonus = _evaluation(
        "confirmed",
        "high",
        score=90,
        strategy="breakout_continuation",
        auxiliary_bonus=5,
    )
    monkeypatch.setattr(
        "app.strategies.service.STRATEGY_EVALUATORS",
        {
            "trend_pullback": lambda context: higher_score,
            "breakout_continuation": lambda context: lower_with_bonus,
        },
    )
    service = StrategyService(
        market_service=FakeMarketService(),
        analysis_service=FakeAnalysisService(),
    )

    result = await service.evaluate("BTC-USDT-SWAP")

    assert result.selected_strategy == "trend_pullback"
