from decimal import Decimal

from app.domain.analysis import TimeframeAnalysis


def classify_regime(views: dict[str, TimeframeAnalysis]) -> str:
    h4 = views.get("4H")
    h1 = views.get("1H")
    if not h4 or not h1:
        return "insufficient_data"
    h4_state = h4.indicators.causal_state
    h1_state = h1.indicators.causal_state
    if (
        (h4_state is not None and h4_state.shock_score >= Decimal("0.65"))
        or (h1_state is not None and h1_state.shock_score >= Decimal("0.65"))
    ):
        return "unstable_transition"
    if h4.volatility in {"high", "extreme"} or h1.volatility == "extreme":
        return "high_volatility"
    if h4.structure.trend in {"strong_bullish", "bullish"} and h1.structure.trend in {"strong_bullish", "bullish"}:
        return "bull_trend"
    if h4.structure.trend in {"strong_bearish", "bearish"} and h1.structure.trend in {"strong_bearish", "bearish"}:
        return "bear_trend"
    if h4.volatility == "low" and h1.volatility == "low":
        return "compression"
    return "range_or_transition"
