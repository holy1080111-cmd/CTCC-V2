from app.strategies.base import Condition, StrategyContext, common_vetoes, evaluate_conditions
from app.strategies.helpers import choch_matches, momentum_matches

NAME = "structure_reversal"


def evaluate(ctx: StrategyContext):
    direction = "long" if ctx.tf("1H").structure.choch == "up" else "short" if ctx.tf("1H").structure.choch == "down" else "neutral"
    conditions = [
        Condition("1h_choch", "1H CHoCH confirms structural reversal", 35, choch_matches(ctx.tf("1H"), direction), "1H CHoCH is missing"),
        Condition("15m_follow", "15m bias follows reversal", 25, ctx.tf("15m").directional_bias == direction, "15m does not follow reversal"),
        Condition("5m_trigger", "5m momentum confirms entry", 20, momentum_matches(ctx.tf("5m"), direction), "5m trigger is missing"),
        Condition("4h_not_opposed", "4H is not strongly opposed", 10, not ((direction == "long" and ctx.tf("4H").structure.trend == "strong_bearish") or (direction == "short" and ctx.tf("4H").structure.trend == "strong_bullish")), "4H strongly opposes reversal", veto=True),
        Condition("quality", "Data quality passes", 10, all(v.data_quality_ok for v in ctx.analysis.timeframe_analyses.values()), "data quality failed", veto=True),
    ]
    return evaluate_conditions(ctx, NAME, direction, conditions, common_vetoes(ctx, direction))
