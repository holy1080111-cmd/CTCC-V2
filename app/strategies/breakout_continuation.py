from app.strategies.base import (
    Condition,
    StrategyContext,
    common_vetoes,
    evaluate_conditions,
)
from app.strategies.conditions import StrategyConditionSet
from app.strategies.helpers import (
    bos_matches,
    trend_matches,
    volume_confirmed,
)

NAME = "breakout_continuation"


def conditions(ctx: StrategyContext) -> StrategyConditionSet:
    direction = ctx.analysis.overall_bias
    conditions = [
        Condition(
            "4h_permission",
            "4H direction permits trade",
            20,
            trend_matches(ctx.tf("4H"), direction),
            "4H does not permit direction",
            required=True,
        ),
        Condition(
            "15m_bos",
            "15m confirmed BOS",
            30,
            bos_matches(ctx.tf("15m"), direction),
            "15m confirmed BOS is missing",
            required=True,
        ),
        Condition(
            "5m_trend",
            "5m trend follows breakout",
            20,
            trend_matches(ctx.tf("5m"), direction),
            "5m trend does not follow breakout",
            required=True,
        ),
        Condition(
            "5m_volume",
            "5m volume confirms breakout",
            20,
            volume_confirmed(ctx.tf("5m")),
            "5m volume ratio is below 1.0",
        ),
        Condition(
            "quality",
            "Data and MTF alignment pass",
            10,
            not ctx.analysis.blockers,
            "analysis contains blockers",
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
