"""Synthetic qualification checks; no market acquisition or broker calls."""

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from importlib import import_module

import pytest

from app.config.settings import Settings
from app.domain.analysis import (
    FairValueGap,
    IndicatorSnapshot,
    MultiTimeframeAnalysis,
    OrderBlock,
    StructureSnapshot,
    TimeframeAnalysis,
)
from app.domain.market import MarketSnapshot, OrderBook, Ticker
from app.domain.strategy import ScoreComponent, StrategyEvaluation
from app.strategies.base import Condition, StrategyContext, evaluate_conditions
from app.strategies.registry import STRATEGY_NAMES
from app.strategies.service import StrategyService

D = Decimal
NOW = datetime(2024, 1, 2, tzinfo=UTC)
REQUIRED = {
    "trend_pullback": {
        "4h_trend",
        "1h_trend",
        "15m_pullback",
        "5m_momentum",
        "quality",
    },
    "breakout_continuation": {"4h_permission", "15m_bos", "5m_trend", "quality"},
    "liquidity_sweep_reversal": {"15m_sweep", "15m_choch", "5m_momentum", "quality"},
    "fvg_return": {"4h_direction", "15m_fvg", "5m_trigger", "quality"},
    "order_block_return": {"4h_direction", "15m_order_block", "5m_trigger", "quality"},
    "range_reversal": {"range_regime", "range_edge", "5m_turn", "quality"},
    "structure_reversal": {"1h_choch", "15m_follow", "5m_trigger", "quality"},
    "volatility_expansion": {"15m_expansion", "15m_bos", "5m_momentum", "quality"},
}


def context(
    strategy: str = "trend_pullback", minimum_score: int = 72
) -> StrategyContext:
    views = {}
    for timeframe in ("4H", "1H", "15m", "5m"):
        structure = StructureSnapshot(
            trend="bullish",
            swing_structure="HH/HL",
            bos="up",
            choch="up",
            last_swing_low=D("99"),
            last_swing_high=D("105"),
            support_levels=[D("100")],
            resistance_levels=[D("105")],
            fair_value_gaps=[
                FairValueGap(
                    direction="bullish",
                    lower=D("99"),
                    upper=D("101"),
                    created_at=NOW,
                )
            ],
            order_blocks=[
                OrderBlock(
                    direction="bullish",
                    lower=D("99"),
                    upper=D("101"),
                    created_at=NOW,
                )
            ],
        )
        views[timeframe] = TimeframeAnalysis(
            timeframe=timeframe,
            candle_count=250,
            last_closed_at=NOW,
            close=D("100"),
            data_quality_ok=True,
            indicators=IndicatorSnapshot(
                ema20=D("100"),
                atr14=D("1"),
                rsi14=D("55"),
                macd_histogram=D("0.5"),
                volume_ratio20=D("1.5"),
            ),
            structure=structure,
            volatility=(
                "high"
                if strategy == "volatility_expansion" and timeframe == "15m"
                else "normal"
            ),
            directional_bias="long",
        )
    analysis = MultiTimeframeAnalysis(
        symbol="BTC/USDT:USDT",
        instrument_id="BTC-USDT-SWAP",
        price=D("100"),
        regime="range" if strategy == "range_reversal" else "bull_trend",
        overall_bias="long",
        alignment_score=100,
        trade_ready=True,
        timeframe_analyses=views,
        generated_at=NOW,
    )
    ticker = Ticker(
        instrument_id="BTC-USDT-SWAP",
        last=D("100"),
        bid=D("99.99"),
        ask=D("100.01"),
        bid_size=D("1"),
        ask_size=D("1"),
        open_24h=D("98"),
        high_24h=D("102"),
        low_24h=D("97"),
        volume_24h=D("1"),
        volume_quote_24h=D("1"),
        timestamp=NOW,
    )
    market = MarketSnapshot(
        symbol=analysis.symbol,
        instrument_id=analysis.instrument_id,
        ticker=ticker,
        mark_price=D("100"),
        funding_rate=D("0.0001"),
        next_funding_time=None,
        open_interest_contracts=D("1"),
        open_interest_currency=D("1"),
        order_book=OrderBook(
            instrument_id=analysis.instrument_id, bids=[], asks=[], timestamp=NOW
        ),
        candles={},
        quality={},
        received_at=NOW,
    )
    return StrategyContext(analysis, market, minimum_score, D("1.8"))


def evaluator(name: str):
    return import_module(f"app.strategies.{name}").evaluate


@pytest.mark.parametrize("strategy", REQUIRED)
def test_eight_strategies_publish_exact_required_mapping_and_pass(strategy) -> None:
    assert set(REQUIRED) == set(STRATEGY_NAMES)
    result = evaluator(strategy)(context(strategy))
    assert {item.code for item in result.score_components if item.required} == REQUIRED[
        strategy
    ]
    assert all(item.passed for item in result.score_components)
    assert result.score == 100
    assert result.required_failures == []
    assert result.eligible and result.candidate is not None
    assert StrategyEvaluation.model_validate_json(result.model_dump_json()) == result


@pytest.mark.parametrize(
    ("strategy", "failed_code"),
    [
        (strategy, code)
        for strategy, codes in REQUIRED.items()
        for code in sorted(codes)
    ],
)
def test_each_required_failure_blocks_despite_passing_score(
    monkeypatch, strategy, failed_code
) -> None:
    def fail_one(ctx, name, direction, conditions, extra_vetoes):
        changed = [
            replace(item, passed=False) if item.code == failed_code else item
            for item in conditions
        ]
        return evaluate_conditions(ctx, name, direction, changed, extra_vetoes)

    monkeypatch.setattr(
        import_module(f"app.strategies.{strategy}"), "evaluate_conditions", fail_one
    )
    result = evaluator(strategy)(context(strategy, minimum_score=1))
    assert result.score >= 1
    assert result.required_failures == [failed_code]
    assert not result.eligible and result.candidate is None
    if failed_code == "quality":
        assert result.vetoes  # Required status must not replace the old quality veto.


@pytest.mark.parametrize(
    ("strategy", "failed_code"),
    [
        ("trend_pullback", "15m_pullback"),
        ("breakout_continuation", "15m_bos"),
        ("liquidity_sweep_reversal", "15m_sweep"),
        ("fvg_return", "15m_fvg"),
        ("order_block_return", "15m_order_block"),
        ("range_reversal", "range_regime"),
        ("structure_reversal", "15m_follow"),
        ("volatility_expansion", "15m_expansion"),
    ],
)
def test_real_missing_setup_inputs_cannot_be_replaced_by_other_points(
    strategy, failed_code
) -> None:
    ctx = context(strategy, minimum_score=1)
    view = ctx.tf("15m")
    if strategy == "trend_pullback":
        view.indicators.ema20 = D("50")
    elif strategy == "breakout_continuation":
        view.structure.bos = None
    elif strategy == "liquidity_sweep_reversal":
        view.structure.last_swing_low = None
    elif strategy == "fvg_return":
        view.structure.fair_value_gaps = []
    elif strategy == "order_block_return":
        view.structure.order_blocks = []
    elif strategy == "range_reversal":
        ctx.analysis.regime = "bull_trend"
    elif strategy == "structure_reversal":
        view.directional_bias = "neutral"
    else:
        view.volatility = "normal"
    result = evaluator(strategy)(ctx)
    assert result.score >= ctx.minimum_score
    assert failed_code in result.required_failures
    assert not result.eligible and result.candidate is None


def test_trend_pullback_requires_one_hour_alignment_even_at_eighty_points() -> None:
    ctx = context()
    ctx.tf("1H").directional_bias = "neutral"
    result = evaluator("trend_pullback")(ctx)
    assert result.score == 80
    assert result.required_failures == ["1h_trend"]
    assert not result.eligible and result.candidate is None


@pytest.mark.parametrize("defect", ["4H", "15m", "missing_1H", "analysis_blocker"])
def test_volatility_expansion_quality_is_required_without_changing_weights(
    defect,
) -> None:
    ctx = context("volatility_expansion")
    if defect == "missing_1H":
        del ctx.analysis.timeframe_analyses["1H"]
    elif defect == "analysis_blocker":
        ctx.analysis.blockers = ["mtf_alignment_blocked"]
    else:
        ctx.tf(defect).data_quality_ok = False
    result = evaluator("volatility_expansion")(ctx)
    assert result.score == 100
    assert sum(item.maximum for item in result.score_components) == 100
    assert result.required_failures == ["quality"]
    assert result.vetoes and not result.eligible and result.candidate is None


def test_ninety_nine_points_cannot_compensate_for_one_required_point() -> None:
    result = evaluate_conditions(
        context(),
        "synthetic",
        "long",
        [
            Condition("ranking", "Ranking", 99, True, "present"),
            Condition("setup", "Setup", 1, False, "missing setup", required=True),
        ],
    )
    assert result.score == 99 and result.completion_ratio == D("0.9900")
    assert result.required_failures == ["setup"]
    assert result.vetoes == []
    assert not result.eligible and result.candidate is None


def test_nonrequired_failure_remains_a_score_only_condition() -> None:
    result = evaluate_conditions(
        context(),
        "synthetic",
        "long",
        [
            Condition("setup", "Setup", 80, True, "present", required=True),
            Condition("ranking", "Ranking", 20, False, "missing confirmation"),
        ],
    )
    assert result.score == 80
    assert result.required_failures == []
    assert result.eligible and result.candidate is not None


@pytest.mark.parametrize("blocker", ["score", "veto", "neutral", "extra_veto"])
def test_existing_entry_gates_are_preserved(blocker) -> None:
    conditions = [Condition("setup", "Setup", 70, True, "present", required=True)]
    if blocker == "score":
        conditions.append(Condition("optional", "Optional", 30, False, "missing"))
    if blocker == "veto":
        conditions.append(Condition("safety", "Safety", 0, False, "unsafe", True))
    result = evaluate_conditions(
        context(),
        "synthetic",
        "neutral" if blocker == "neutral" else "long",
        conditions,
        ["external_safety_veto"] if blocker == "extra_veto" else [],
    )
    assert result.required_failures == []
    assert not result.eligible and result.candidate is None


def test_common_spread_veto_remains_active_when_every_required_condition_passes() -> (
    None
):
    ctx = context()
    ctx.market.ticker.bid = D("99")
    ctx.market.ticker.ask = D("101")
    result = evaluator("trend_pullback")(ctx)
    assert result.required_failures == []
    assert "spread_above_0.08_percent" in result.vetoes
    assert not result.eligible and result.candidate is None


def test_legacy_defaults_and_positional_arguments_remain_compatible() -> None:
    component = ScoreComponent(
        code="legacy",
        label="Legacy",
        points=1,
        maximum=1,
        passed=True,
        detail="present",
    )
    assert component.required is False
    evaluation = StrategyEvaluation(
        strategy="legacy",
        direction="neutral",
        eligible=False,
        completion_ratio=D(1),
        score=100,
    )
    assert evaluation.required_failures == []
    condition = Condition("legacy", "Legacy", 1, False, "unsafe", True)
    assert condition.veto is True and condition.required is False


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_source", ["declared", "components", "both"])
async def test_service_rejects_forged_eligibility_even_when_failure_list_is_cleared(
    monkeypatch, failure_source
) -> None:
    ctx = context()
    original = evaluator("trend_pullback")(ctx)
    assert original.candidate is not None
    components = original.score_components
    if failure_source in {"components", "both"}:
        components = [
            item.model_copy(update={"passed": False})
            if item.code == "1h_trend"
            else item
            for item in components
        ]
    forged = original.model_copy(
        update={
            "eligible": True,
            "score": 100,
            "score_components": components,
            "required_failures": ["1h_trend"] if failure_source != "components" else [],
        }
    )

    class FakeMarket:
        async def snapshot(self, symbol, candle_limit):
            return ctx.market

    class FakeAnalysis:
        def analyze_snapshot(self, snapshot):
            return ctx.analysis

    monkeypatch.setattr("app.strategies.service.STRATEGIES", (lambda context: forged,))
    monkeypatch.setattr(
        "app.strategies.service.get_settings", lambda: Settings(_env_file=None)
    )
    result = await StrategyService(FakeMarket(), FakeAnalysis()).evaluate(
        "BTC-USDT-SWAP"
    )
    assert result.decision == "no_trade" and result.selected_candidate is None
    assert result.evaluations[0].required_failures == ["1h_trend"]
    assert not result.evaluations[0].eligible
    assert result.evaluations[0].candidate is None
