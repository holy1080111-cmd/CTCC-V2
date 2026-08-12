from __future__ import annotations

from decimal import Decimal

from app.config.settings import Settings
from app.domain.demo_automation import DemoAutomationRiskTier
from app.domain.strategy import TradeCandidate
from app.strategies.mathematical_confirmation import mathematical_score_cap

DERIVATIVE_MEDIUM_CONFIDENCE = Decimal("0.35")
DERIVATIVE_HIGH_CONFIDENCE = Decimal("0.65")


def configured_score_risk_tiers(settings: Settings) -> list[DemoAutomationRiskTier]:
    minimum = settings.strategy_min_score
    medium = settings.okx_demo_score_medium_min
    high = settings.okx_demo_score_high_min
    if settings.okx_demo_structural_dynamic_leverage_enabled:
        elite = settings.okx_demo_structural_score_elite_min
        extreme = settings.okx_demo_structural_score_extreme_min
        return [
            DemoAutomationRiskTier(
                name="low",
                minimum_score=minimum,
                maximum_score=medium - 1,
                risk_pct=settings.okx_demo_structural_low_risk_pct,
                leverage=settings.okx_demo_structural_low_leverage_cap,
                margin_allocation_pct=Decimal("1"),
            ),
            DemoAutomationRiskTier(
                name="medium",
                minimum_score=medium,
                maximum_score=high - 1,
                risk_pct=settings.okx_demo_structural_medium_risk_pct,
                leverage=settings.okx_demo_structural_medium_leverage_cap,
                margin_allocation_pct=Decimal("1"),
            ),
            DemoAutomationRiskTier(
                name="high",
                minimum_score=high,
                maximum_score=elite - 1,
                risk_pct=settings.okx_demo_structural_high_risk_pct,
                leverage=settings.okx_demo_structural_high_leverage_cap,
                margin_allocation_pct=Decimal("1"),
            ),
            DemoAutomationRiskTier(
                name="elite",
                minimum_score=elite,
                maximum_score=extreme - 1,
                risk_pct=settings.okx_demo_structural_elite_risk_pct,
                leverage=settings.okx_demo_structural_elite_leverage_cap,
                margin_allocation_pct=Decimal("1"),
            ),
            DemoAutomationRiskTier(
                name="extreme",
                minimum_score=extreme,
                maximum_score=100,
                risk_pct=settings.okx_demo_structural_extreme_risk_pct,
                leverage=settings.okx_demo_structural_extreme_leverage_cap,
                margin_allocation_pct=Decimal("1"),
            ),
        ]
    return [
        DemoAutomationRiskTier(
            name="low",
            minimum_score=minimum,
            maximum_score=medium - 1,
            risk_pct=settings.okx_demo_score_low_risk_pct,
            leverage=settings.okx_demo_score_low_leverage,
            margin_allocation_pct=settings.okx_demo_score_low_margin_pct,
        ),
        DemoAutomationRiskTier(
            name="medium",
            minimum_score=medium,
            maximum_score=high - 1,
            risk_pct=settings.okx_demo_score_medium_risk_pct,
            leverage=settings.okx_demo_score_medium_leverage,
            margin_allocation_pct=settings.okx_demo_score_medium_margin_pct,
        ),
        DemoAutomationRiskTier(
            name="high",
            minimum_score=high,
            maximum_score=100,
            risk_pct=settings.okx_demo_score_high_risk_pct,
            leverage=settings.okx_demo_score_high_leverage,
            margin_allocation_pct=settings.okx_demo_score_high_margin_pct,
        ),
    ]


def score_risk_tier(score: int, settings: Settings) -> DemoAutomationRiskTier:
    for tier in configured_score_risk_tiers(settings):
        if tier.minimum_score <= score <= tier.maximum_score:
            return tier
    raise ValueError("score_outside_configured_demo_risk_tiers")


def _legacy_derivative_adjusted_score(
    candidate: TradeCandidate, settings: Settings
) -> tuple[int, str | None]:
    """Return a score that calculus evidence may only cap, never increase."""

    raw_score = candidate.score
    if not settings.okx_demo_score_risk_enabled:
        return raw_score, None

    confirmation = candidate.derivative_confirmation
    if confirmation is not None and confirmation.status == "opposed":
        return raw_score, "causal_derivative_opposes_trade_direction"

    if (
        confirmation is not None
        and confirmation.status == "confirmed"
        and confirmation.confidence >= DERIVATIVE_HIGH_CONFIDENCE
    ):
        maximum_score = 100
    elif (
        confirmation is not None
        and confirmation.status == "confirmed"
        and confirmation.confidence >= DERIVATIVE_MEDIUM_CONFIDENCE
    ):
        maximum_score = settings.okx_demo_score_high_min - 1
    else:
        maximum_score = settings.okx_demo_score_medium_min - 1
    return min(raw_score, maximum_score), None


def mathematical_adjusted_score(
    candidate: TradeCandidate, settings: Settings
) -> tuple[int, str | None]:
    """Map the shared mathematical grade to Demo tiers monotonically."""

    raw_score = candidate.score
    if not settings.okx_demo_score_risk_enabled:
        return raw_score, None

    confirmation = candidate.mathematical_confirmation
    if confirmation is None:
        return _legacy_derivative_adjusted_score(candidate, settings)
    if confirmation.status == "unstable":
        return raw_score, "mathematical_core_regime_instability"
    if confirmation.status == "opposed" or confirmation.risk_grade == "blocked":
        return raw_score, "mathematical_core_opposes_trade_direction"

    maximum_score = mathematical_score_cap(
        confirmation,
        medium_minimum=settings.okx_demo_score_medium_min,
        high_minimum=settings.okx_demo_score_high_min,
    )
    return min(raw_score, maximum_score), None


def derivative_adjusted_score(
    candidate: TradeCandidate, settings: Settings
) -> tuple[int, str | None]:
    """Backward-compatible alias for the unified mathematical gate."""

    return mathematical_adjusted_score(candidate, settings)
