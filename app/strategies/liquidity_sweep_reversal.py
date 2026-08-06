from decimal import Decimal
from app.strategies.base import Condition, StrategyContext, common_vetoes, evaluate_conditions
from app.strategies.helpers import choch_matches, momentum_matches

NAME = "liquidity_sweep_reversal"
D = Decimal


def _sweep(ctx: StrategyContext, direction: str) -> bool:
    view = ctx.tf("15m")
    if direction == "long" and view.structure.last_swing_low is not None:
        return view.close > view.structure.last_swing_low and view.structure.choch == "up"
    if direction == "short" and view.structure.last_swing_high is not None:
        return view.close < view.structure.last_swing_high and view.structure.choch == "down"
    return False


def evaluate(ctx: StrategyContext):
    direction = "long" if ctx.tf("15m").structure.choch == "up" else "short" if ctx.tf("15m").structure.choch == "down" else "neutral"
    conditions = [
        Condition("15m_sweep", "15m liquidity sweep is reclaimed", 30, _sweep(ctx, direction), "no reclaim after a liquidity sweep"),
        Condition("15m_choch", "15m CHoCH confirms reversal", 25, choch_matches(ctx.tf("15m"), direction), "15m CHoCH is missing"),
        Condition("5m_momentum", "5m momentum confirms reversal", 20, momentum_matches(ctx.tf("5m"), direction), "5m momentum has not confirmed"),
        Condition("not_extreme", "5m volatility is not extreme", 15, ctx.tf("5m").volatility != "extreme", "5m volatility is extreme", veto=True),
        Condition("quality", "Data quality passes", 10, all(v.data_quality_ok for v in ctx.analysis.timeframe_analyses.values()), "data quality failed", veto=True),
    ]
    return evaluate_conditions(ctx, NAME, direction, conditions, common_vetoes(ctx, direction))
