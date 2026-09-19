"""Existing reversal predicates as a versioned policy, never event authority."""

from decimal import Decimal

from app.strategies.base import StrategyContext
from app.strategies.conditions import assess_conditions
from app.strategies.mathematical_confirmation import mathematical_confirmation
from app.trade_qualification.models import MarketRegime

REVERSAL_POLICY = "ctcc-reversal-history-protection-v1"


def reversal_safety_permission(analysis, route, direction):
    """Present safety vetoes only; original historical admission is separate."""
    math = mathematical_confirmation(analysis, direction)
    return (
        direction in ("long", "short")
        and route.regime
        not in (
            MarketRegime.RISK_OFF,
            MarketRegime.HIGH_VOLATILITY,
            MarketRegime.COMPRESSION,
        )
        and not any(code != "multi_timeframe_not_aligned" for code in analysis.blockers)
        and math.status not in ("unstable", "opposed")
        and math.risk_grade != "blocked"
    )


def reversal_current_permission(analysis, market, route, direction):
    """Keep 1H CHoCH, 15m follow, 5m momentum and the strong-4H veto unchanged.

    Inputs must be source-recomputed by the calling evaluator. This helper never
    creates a trigger, changes a regime, removes a diagnostic or grants authority.
    """
    assessment = assess_conditions(
        StrategyContext(analysis, market, 0, Decimal(1)), "structure_reversal"
    )
    return (
        reversal_safety_permission(analysis, route, direction)
        and assessment.direction == direction
        and not assessment.required_failures
        and not assessment.veto_failures
    )
