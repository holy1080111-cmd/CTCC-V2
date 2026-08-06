from decimal import Decimal
from typing import Sequence

from app.domain.analysis import FairValueGap, OrderBlock, StructureSnapshot, SwingPoint
from app.domain.market import Candle

D = Decimal


def find_swings(candles: Sequence[Candle], window: int = 2) -> list[SwingPoint]:
    swings: list[SwingPoint] = []
    if len(candles) < window * 2 + 1:
        return swings
    for index in range(window, len(candles) - window):
        candle = candles[index]
        neighborhood = candles[index-window:index+window+1]
        if candle.high == max(item.high for item in neighborhood) and sum(item.high == candle.high for item in neighborhood) == 1:
            swings.append(SwingPoint(kind="high", timestamp=candle.timestamp, price=candle.high, index=index))
        if candle.low == min(item.low for item in neighborhood) and sum(item.low == candle.low for item in neighborhood) == 1:
            swings.append(SwingPoint(kind="low", timestamp=candle.timestamp, price=candle.low, index=index))
    return swings


def _structure_label(swings: Sequence[SwingPoint]) -> str:
    highs = [s.price for s in swings if s.kind == "high"]
    lows = [s.price for s in swings if s.kind == "low"]
    high_label = "HH" if len(highs) >= 2 and highs[-1] > highs[-2] else "LH" if len(highs) >= 2 else "NA"
    low_label = "HL" if len(lows) >= 2 and lows[-1] > lows[-2] else "LL" if len(lows) >= 2 else "NA"
    return f"{high_label}/{low_label}"


def detect_fvg(candles: Sequence[Candle], lookback: int = 30) -> list[FairValueGap]:
    gaps: list[FairValueGap] = []
    start = max(2, len(candles) - lookback)
    for i in range(start, len(candles)):
        first, third = candles[i-2], candles[i]
        if third.low > first.high:
            lower, upper, direction = first.high, third.low, "bullish"
        elif third.high < first.low:
            lower, upper, direction = third.high, first.low, "bearish"
        else:
            continue
        width = upper - lower
        if width <= 0:
            continue
        later = candles[i+1:]
        if direction == "bullish":
            deepest = min((c.low for c in later), default=upper)
            filled = max(D("0"), min(D("1"), (upper - deepest) / width))
        else:
            highest = max((c.high for c in later), default=lower)
            filled = max(D("0"), min(D("1"), (highest - lower) / width))
        if filled < D("1"):
            gaps.append(FairValueGap(direction=direction, lower=lower, upper=upper, created_at=third.timestamp, filled_ratio=filled))
    return gaps[-5:]



def detect_order_blocks(candles: Sequence[Candle], lookback: int = 50) -> list[OrderBlock]:
    """Detect conservative displacement-backed order blocks.

    A bullish block is the final bearish candle immediately before a bullish
    displacement that closes above the prior 3-candle high. Bearish is mirrored.
    The zone uses the source candle high/low and is marked mitigated when later
    price fully trades through the opposite boundary.
    """
    blocks: list[OrderBlock] = []
    start = max(4, len(candles) - lookback)
    for i in range(start, len(candles)):
        source = candles[i - 1]
        impulse = candles[i]
        prior = candles[i - 4:i - 1]
        if not prior:
            continue
        bullish = source.close < source.open and impulse.close > max(c.high for c in prior) and impulse.close > impulse.open
        bearish = source.close > source.open and impulse.close < min(c.low for c in prior) and impulse.close < impulse.open
        if not bullish and not bearish:
            continue
        direction = "bullish" if bullish else "bearish"
        later = candles[i + 1:]
        mitigated = (
            any(c.low <= source.low for c in later)
            if bullish
            else any(c.high >= source.high for c in later)
        )
        if not mitigated:
            blocks.append(OrderBlock(
                direction=direction,
                lower=source.low,
                upper=source.high,
                created_at=source.timestamp,
                mitigated=False,
            ))
    return blocks[-5:]

def analyze_structure(candles: Sequence[Candle], ema20: Decimal | None, ema50: Decimal | None, ema200: Decimal | None) -> StructureSnapshot:
    close = candles[-1].close
    if ema20 is not None and ema50 is not None and ema200 is not None:
        if close > ema200 and ema20 > ema50 > ema200:
            trend = "strong_bullish"
        elif close > ema200 and ema20 > ema50:
            trend = "bullish"
        elif close < ema200 and ema20 < ema50 < ema200:
            trend = "strong_bearish"
        elif close < ema200 and ema20 < ema50:
            trend = "bearish"
        else:
            trend = "neutral"
    else:
        trend = "neutral"

    swings = find_swings(candles)
    highs = [s for s in swings if s.kind == "high"]
    lows = [s for s in swings if s.kind == "low"]
    last_high = highs[-1].price if highs else None
    last_low = lows[-1].price if lows else None
    bos = "up" if last_high is not None and close > last_high else "down" if last_low is not None and close < last_low else None
    choch = None
    if bos == "down" and trend in {"bullish", "strong_bullish"}:
        choch = "down"
    elif bos == "up" and trend in {"bearish", "strong_bearish"}:
        choch = "up"

    support = sorted({s.price for s in lows[-3:]}, reverse=True)
    resistance = sorted({s.price for s in highs[-3:]})
    return StructureSnapshot(
        trend=trend,
        swing_structure=_structure_label(swings),
        bos=bos,
        choch=choch,
        last_swing_high=last_high,
        last_swing_low=last_low,
        fair_value_gaps=detect_fvg(candles),
        order_blocks=detect_order_blocks(candles),
        support_levels=support,
        resistance_levels=resistance,
    )
