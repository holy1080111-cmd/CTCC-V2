from decimal import Decimal

from app.domain.analysis import TimeframeAnalysis

D = Decimal


def trend_matches(view: TimeframeAnalysis, direction: str) -> bool:
    return view.directional_bias == direction


def bos_matches(view: TimeframeAnalysis, direction: str) -> bool:
    return view.structure.bos == ("up" if direction == "long" else "down")


def choch_matches(view: TimeframeAnalysis, direction: str) -> bool:
    return view.structure.choch == ("up" if direction == "long" else "down")


def momentum_matches(view: TimeframeAnalysis, direction: str) -> bool:
    hist = view.indicators.macd_histogram
    rsi = view.indicators.rsi14
    if hist is None or rsi is None:
        return False
    return (hist > 0 and D("50") <= rsi < D("72")) if direction == "long" else (hist < 0 and D("28") < rsi <= D("50"))


def volume_confirmed(view: TimeframeAnalysis, minimum: Decimal = D("1.0")) -> bool:
    ratio = view.indicators.volume_ratio20
    return ratio is not None and ratio >= minimum


def near_ema20(view: TimeframeAnalysis, tolerance_pct: Decimal = D("0.6")) -> bool:
    ema20 = view.indicators.ema20
    if ema20 is None or ema20 <= 0:
        return False
    return abs(view.close - ema20) / ema20 * D("100") <= tolerance_pct


def has_fvg(view: TimeframeAnalysis, direction: str) -> bool:
    expected = "bullish" if direction == "long" else "bearish"
    return any(gap.direction == expected and gap.filled_ratio < D("1") for gap in view.structure.fair_value_gaps)
