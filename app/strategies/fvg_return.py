from app.strategies.base import Condition, StrategyContext, common_vetoes, evaluate_conditions
from app.strategies.helpers import has_fvg, momentum_matches, trend_matches

NAME = "fvg_return"


def evaluate(ctx: StrategyContext):
    direction = ctx.analysis.overall_bias
    conditions = [
        Condition("4h_direction", "4H direction agrees", 20, trend_matches(ctx.tf("4H"), direction), "4H direction disagrees"),
        Condition("15m_fvg", "15m has an unfilled directional FVG", 30, has_fvg(ctx.tf("15m"), direction), "no suitable 15m FVG"),
        Condition("1h_context", "1H context agrees", 15, trend_matches(ctx.tf("1H"), direction), "1H context disagrees"),
        Condition("5m_trigger", "5m momentum triggers from zone", 20, momentum_matches(ctx.tf("5m"), direction), "5m trigger is missing"),
        Condition("quality", "Data and alignment pass", 15, not ctx.analysis.blockers, "analysis contains blockers", veto=True),
    ]
    return evaluate_conditions(ctx, NAME, direction, conditions, common_vetoes(ctx, direction))
