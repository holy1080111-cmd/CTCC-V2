from decimal import Decimal

from app.strategies.base import Condition, StrategyContext, common_vetoes, evaluate_conditions
from app.strategies.helpers import momentum_matches, trend_matches

NAME = "order_block_return"
D = Decimal


def _at_directional_block(ctx: StrategyContext, direction: str) -> bool:
    expected = "bullish" if direction == "long" else "bearish"
    price = ctx.tf("15m").close
    atr = ctx.tf("15m").indicators.atr14 or price * D("0.005")
    for block in ctx.tf("15m").structure.order_blocks:
        if block.direction != expected or block.mitigated:
            continue
        expanded_lower = block.lower - atr * D("0.25")
        expanded_upper = block.upper + atr * D("0.25")
        if expanded_lower <= price <= expanded_upper:
            return True
    return False


def evaluate(ctx: StrategyContext):
    direction = ctx.analysis.overall_bias
    conditions = [
        Condition("4h_direction", "4H direction agrees", 20, trend_matches(ctx.tf("4H"), direction), "4H direction disagrees"),
        Condition("1h_context", "1H direction agrees", 15, trend_matches(ctx.tf("1H"), direction), "1H context disagrees"),
        Condition("15m_order_block", "15m price is retesting an unmitigated directional order block", 35, _at_directional_block(ctx, direction), "price is not retesting a valid 15m order block"),
        Condition("5m_trigger", "5m momentum confirms the return", 15, momentum_matches(ctx.tf("5m"), direction), "5m trigger is missing"),
        Condition("quality", "Data quality passes", 15, all(v.data_quality_ok for v in ctx.analysis.timeframe_analyses.values()), "data quality failed", veto=True),
    ]
    return evaluate_conditions(ctx, NAME, direction, conditions, common_vetoes(ctx, direction))
