from __future__ import annotations

from decimal import Decimal

from app.domain.analysis import MultiTimeframeAnalysis
from app.domain.strategy import DerivativeConfirmation

D = Decimal

TIMEFRAME_WEIGHTS: tuple[tuple[str, Decimal], ...] = (
    ("4H", D("0.35")),
    ("1H", D("0.30")),
    ("15m", D("0.25")),
    ("5m", D("0.10")),
)
MINIMUM_LOCAL_CONFIDENCE = D("0.20")
MINIMUM_FIT_R2 = D("0.35")
MINIMUM_QUALIFIED_WEIGHT = D("0.50")
CONFIRMED_ALIGNMENT = D("0.35")


def _clamp(value: Decimal, lower: Decimal, upper: Decimal) -> Decimal:
    return min(upper, max(lower, value))


def derivative_confirmation(
    analysis: MultiTimeframeAnalysis, direction: str
) -> DerivativeConfirmation:
    if direction not in {"long", "short"}:
        return DerivativeConfirmation(
            status="insufficient",
            confidence=D("0"),
            alignment_score=D("0"),
        )

    direction_sign = D("1") if direction == "long" else D("-1")
    qualified: list[str] = []
    aligned: list[str] = []
    opposed: list[str] = []
    qualified_weight = D("0")
    aggregate_confidence = D("0")
    support_numerator = D("0")
    support_weight = D("0")

    for timeframe, timeframe_weight in TIMEFRAME_WEIGHTS:
        view = analysis.timeframe_analyses.get(timeframe)
        trend = view.indicators.causal_trend if view is not None else None
        if (
            trend is None
            or trend.confidence < MINIMUM_LOCAL_CONFIDENCE
            or trend.fit_r2 < MINIMUM_FIT_R2
        ):
            continue

        qualified.append(timeframe)
        qualified_weight += timeframe_weight
        aggregate_confidence += timeframe_weight * trend.confidence

        velocity_support = _clamp(
            direction_sign * trend.velocity_to_volatility, D("-1"), D("1")
        )
        acceleration_support = _clamp(
            direction_sign * trend.acceleration_to_volatility,
            D("-1"),
            D("1"),
        )
        local_support = _clamp(
            D("0.80") * velocity_support + D("0.20") * acceleration_support,
            D("-1"),
            D("1"),
        )
        weighted_confidence = timeframe_weight * trend.confidence
        support_numerator += weighted_confidence * local_support
        support_weight += weighted_confidence
        if local_support >= D("0.10"):
            aligned.append(timeframe)
        elif local_support <= D("-0.10"):
            opposed.append(timeframe)

    alignment_score = (
        D("0")
        if support_weight == 0
        else _clamp(support_numerator / support_weight, D("-1"), D("1"))
    )
    if len(qualified) < 2 or qualified_weight < MINIMUM_QUALIFIED_WEIGHT:
        status = "insufficient"
    elif (
        alignment_score >= CONFIRMED_ALIGNMENT
        and len(aligned) >= 2
        and any(timeframe in aligned for timeframe in ("15m", "5m"))
    ):
        status = "confirmed"
    elif alignment_score <= -CONFIRMED_ALIGNMENT and len(opposed) >= 2:
        status = "opposed"
    else:
        status = "mixed"

    return DerivativeConfirmation(
        status=status,
        confidence=_clamp(aggregate_confidence, D("0"), D("1")),
        alignment_score=alignment_score,
        qualified_timeframes=qualified,
        aligned_timeframes=aligned,
        opposed_timeframes=opposed,
    )
