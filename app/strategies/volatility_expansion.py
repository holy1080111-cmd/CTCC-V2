from app.strategies.base import (
    Condition,
    StrategyContext,
    common_vetoes,
    evaluate_conditions,
)
from app.strategies.conditions import StrategyConditionSet
from app.strategies.helpers import bos_matches, momentum_matches, volume_confirmed

NAME = "volatility_expansion"


def conditions(ctx: StrategyContext) -> StrategyConditionSet:
    direction = ctx.analysis.overall_bias
    conditions = [
        Condition(
            "15m_expansion",
            "15m volatility expands",
            25,
            ctx.tf("15m").volatility == "high",
            "15m volatility is not in controlled expansion",
            required=True,
        ),
        Condition(
            "15m_bos",
            "15m BOS confirms expansion direction",
            25,
            bos_matches(ctx.tf("15m"), direction),
            "15m BOS is missing",
            required=True,
        ),
        Condition(
            "5m_volume",
            "5m volume confirms expansion",
            20,
            volume_confirmed(ctx.tf("5m"), __import__("decimal").Decimal("1.2")),
            "5m volume ratio is below 1.2",
        ),
        Condition(
            "5m_momentum",
            "5m momentum agrees",
            15,
            momentum_matches(ctx.tf("5m"), direction),
            "5m momentum disagrees",
            required=True,
        ),
        Condition(
            "not_extreme",
            "5m volatility is not extreme",
            15,
            ctx.tf("5m").volatility != "extreme",
            "5m volatility is extreme",
            veto=True,
        ),
        Condition(
            "quality",
            "All required timeframes and analysis quality pass",
            0,
            not ctx.analysis.blockers
            and all(
                timeframe in ctx.analysis.timeframe_analyses
                and ctx.tf(timeframe).data_quality_ok
                for timeframe in ("4H", "1H", "15m", "5m")
            ),
            "analysis blockers or required timeframe data quality failed",
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
