from app.strategies.base import (
    Condition,
    StrategyContext,
    common_vetoes,
    evaluate_conditions,
)
from app.strategies.conditions import StrategyConditionSet
from app.strategies.helpers import momentum_matches, near_ema20, trend_matches

NAME = "trend_pullback"


def conditions(ctx: StrategyContext) -> StrategyConditionSet:
    direction = ctx.analysis.overall_bias
    conditions = [
        Condition(
            "4h_trend",
            "4H trend agrees",
            25,
            trend_matches(ctx.tf("4H"), direction),
            "4H trend does not agree",
            required=True,
        ),
        Condition(
            "1h_trend",
            "1H trend agrees",
            20,
            trend_matches(ctx.tf("1H"), direction),
            "1H trend does not agree",
            required=True,
        ),
        Condition(
            "15m_pullback",
            "15m price is near EMA20",
            20,
            near_ema20(ctx.tf("15m")),
            "15m is not at a controlled pullback",
            required=True,
        ),
        Condition(
            "5m_momentum",
            "5m momentum resumes",
            20,
            momentum_matches(ctx.tf("5m"), direction),
            "5m momentum has not resumed",
            required=True,
        ),
        Condition(
            "quality",
            "All timeframe data quality passes",
            15,
            not ctx.analysis.blockers,
            "market data quality or alignment is blocked",
            veto=True,
            required=True,
        ),
    ]
    return StrategyConditionSet(NAME, direction, tuple(conditions))


def evaluate(ctx: StrategyContext):
    assessment = conditions(ctx)
    return evaluate_conditions(
        ctx,
        NAME,
        assessment.direction,
        list(assessment.items),
        common_vetoes(ctx, assessment.direction),
    )
