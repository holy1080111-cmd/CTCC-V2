from app.strategies.base import Condition, StrategyContext, common_vetoes, evaluate_conditions
from app.strategies.helpers import bos_matches, momentum_matches, volume_confirmed

NAME = "volatility_expansion"


def evaluate(ctx: StrategyContext):
    direction = ctx.analysis.overall_bias
    conditions = [
        Condition("15m_expansion", "15m volatility expands", 25, ctx.tf("15m").volatility == "high", "15m volatility is not in controlled expansion"),
        Condition("15m_bos", "15m BOS confirms expansion direction", 25, bos_matches(ctx.tf("15m"), direction), "15m BOS is missing"),
        Condition("5m_volume", "5m volume confirms expansion", 20, volume_confirmed(ctx.tf("5m"), __import__('decimal').Decimal('1.2')), "5m volume ratio is below 1.2"),
        Condition("5m_momentum", "5m momentum agrees", 15, momentum_matches(ctx.tf("5m"), direction), "5m momentum disagrees"),
        Condition("not_extreme", "5m volatility is not extreme", 15, ctx.tf("5m").volatility != "extreme", "5m volatility is extreme", veto=True),
    ]
    return evaluate_conditions(ctx, NAME, direction, conditions, common_vetoes(ctx, direction))
