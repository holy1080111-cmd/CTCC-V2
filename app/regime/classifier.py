from app.domain.analysis import TimeframeAnalysis


def classify_regime(views: dict[str, TimeframeAnalysis]) -> str:
    h4 = views.get("4H")
    h1 = views.get("1H")
    if not h4 or not h1:
        return "insufficient_data"
    if h4.volatility in {"high", "extreme"} or h1.volatility == "extreme":
        return "high_volatility"
    if h4.structure.trend in {"strong_bullish", "bullish"} and h1.structure.trend in {"strong_bullish", "bullish"}:
        return "bull_trend"
    if h4.structure.trend in {"strong_bearish", "bearish"} and h1.structure.trend in {"strong_bearish", "bearish"}:
        return "bear_trend"
    if h4.volatility == "low" and h1.volatility == "low":
        return "compression"
    return "range_or_transition"
