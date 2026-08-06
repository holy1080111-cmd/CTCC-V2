from collections.abc import Callable

from app.domain.strategy import StrategyEvaluation
from app.strategies.base import StrategyContext
from app.strategies.breakout_continuation import NAME as BREAKOUT_NAME
from app.strategies.breakout_continuation import evaluate as evaluate_breakout
from app.strategies.fvg_return import NAME as FVG_NAME
from app.strategies.fvg_return import evaluate as evaluate_fvg
from app.strategies.liquidity_sweep_reversal import NAME as LIQUIDITY_SWEEP_NAME
from app.strategies.liquidity_sweep_reversal import evaluate as evaluate_liquidity_sweep
from app.strategies.order_block_return import NAME as ORDER_BLOCK_NAME
from app.strategies.order_block_return import evaluate as evaluate_order_block
from app.strategies.range_reversal import NAME as RANGE_NAME
from app.strategies.range_reversal import evaluate as evaluate_range
from app.strategies.structure_reversal import NAME as STRUCTURE_REVERSAL_NAME
from app.strategies.structure_reversal import evaluate as evaluate_structure_reversal
from app.strategies.trend_pullback import NAME as TREND_PULLBACK_NAME
from app.strategies.trend_pullback import evaluate as evaluate_trend_pullback
from app.strategies.volatility_expansion import NAME as VOLATILITY_EXPANSION_NAME
from app.strategies.volatility_expansion import evaluate as evaluate_volatility_expansion

Evaluator = Callable[[StrategyContext], StrategyEvaluation]

STRATEGIES: tuple[Evaluator, ...] = (
    evaluate_trend_pullback,
    evaluate_breakout,
    evaluate_liquidity_sweep,
    evaluate_fvg,
    evaluate_order_block,
    evaluate_range,
    evaluate_structure_reversal,
    evaluate_volatility_expansion,
)

STRATEGY_NAMES: tuple[str, ...] = (
    TREND_PULLBACK_NAME,
    BREAKOUT_NAME,
    LIQUIDITY_SWEEP_NAME,
    FVG_NAME,
    ORDER_BLOCK_NAME,
    RANGE_NAME,
    STRUCTURE_REVERSAL_NAME,
    VOLATILITY_EXPANSION_NAME,
)
