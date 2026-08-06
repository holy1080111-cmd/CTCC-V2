from decimal import Decimal
from app.strategies.base import Condition, StrategyContext, common_vetoes, evaluate_conditions
from app.strategies.helpers import momentum_matches

NAME = "range_reversal"
D = Decimal


def _direction(ctx: StrategyContext) -> str:
    view = ctx.tf("15m")
    supports, resistances = view.structure.support_levels, view.structure.resistance_levels
    atr = view.indicators.atr14 or ctx.price * D("0.005")
    if supports and abs(view.close - supports[0]) <= atr:
        return "long"
    if resistances and abs(view.close - resistances[0]) <= atr:
        return "short"
    return "neutral"


def evaluate(ctx: StrategyContext):
    direction = _direction(ctx)
    conditions = [
        Condition("range_regime", "Market regime is range", 30, "range" in ctx.analysis.regime, "market is not classified as range"),
        Condition("range_edge", "15m price is near a range edge", 25, direction != "neutral", "price is not near support or resistance"),
        Condition("5m_turn", "5m momentum turns from the edge", 20, momentum_matches(ctx.tf("5m"), direction), "5m reversal momentum is missing"),
        Condition("normal_vol", "15m volatility is not high/extreme", 15, ctx.tf("15m").volatility in {"low", "normal"}, "range reversal blocked by high volatility", veto=True),
        Condition("quality", "Data quality passes", 10, all(v.data_quality_ok for v in ctx.analysis.timeframe_analyses.values()), "data quality failed", veto=True),
    ]
    return evaluate_conditions(ctx, NAME, direction, conditions, common_vetoes(ctx, direction))
