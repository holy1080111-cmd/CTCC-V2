from decimal import Decimal
from typing import Sequence

from app.domain.market import Candle

D = Decimal


def _values(candles: Sequence[Candle], field: str) -> list[Decimal]:
    return [getattr(c, field) for c in candles]


def sma(values: Sequence[Decimal], period: int) -> Decimal | None:
    if period <= 0 or len(values) < period:
        return None
    return sum(values[-period:], D("0")) / D(period)


def ema(values: Sequence[Decimal], period: int) -> Decimal | None:
    if period <= 0 or len(values) < period:
        return None
    seed = sum(values[:period], D("0")) / D(period)
    multiplier = D("2") / D(period + 1)
    result = seed
    for value in values[period:]:
        result = (value - result) * multiplier + result
    return result


def ema_series(values: Sequence[Decimal], period: int) -> list[Decimal | None]:
    output: list[Decimal | None] = [None] * len(values)
    if period <= 0 or len(values) < period:
        return output
    result = sum(values[:period], D("0")) / D(period)
    output[period - 1] = result
    multiplier = D("2") / D(period + 1)
    for i in range(period, len(values)):
        result = (values[i] - result) * multiplier + result
        output[i] = result
    return output


def true_ranges(candles: Sequence[Candle]) -> list[Decimal]:
    if not candles:
        return []
    result = [candles[0].high - candles[0].low]
    for previous, current in zip(candles, candles[1:]):
        result.append(max(
            current.high - current.low,
            abs(current.high - previous.close),
            abs(current.low - previous.close),
        ))
    return result


def wilder_average(values: Sequence[Decimal], period: int) -> Decimal | None:
    if len(values) < period:
        return None
    result = sum(values[:period], D("0")) / D(period)
    for value in values[period:]:
        result = (result * D(period - 1) + value) / D(period)
    return result


def atr(candles: Sequence[Candle], period: int = 14) -> Decimal | None:
    return wilder_average(true_ranges(candles), period)


def rsi(closes: Sequence[Decimal], period: int = 14) -> Decimal | None:
    if len(closes) < period + 1:
        return None
    changes = [current - previous for previous, current in zip(closes, closes[1:])]
    gains = [max(change, D("0")) for change in changes]
    losses = [max(-change, D("0")) for change in changes]
    avg_gain = sum(gains[:period], D("0")) / D(period)
    avg_loss = sum(losses[:period], D("0")) / D(period)
    for gain, loss in zip(gains[period:], losses[period:]):
        avg_gain = (avg_gain * D(period - 1) + gain) / D(period)
        avg_loss = (avg_loss * D(period - 1) + loss) / D(period)
    if avg_loss == 0:
        return D("100") if avg_gain > 0 else D("50")
    rs = avg_gain / avg_loss
    return D("100") - D("100") / (D("1") + rs)


def macd(closes: Sequence[Decimal], fast: int = 12, slow: int = 26, signal: int = 9) -> tuple[Decimal | None, Decimal | None, Decimal | None]:
    fast_series = ema_series(closes, fast)
    slow_series = ema_series(closes, slow)
    macd_values: list[Decimal] = []
    for fast_value, slow_value in zip(fast_series, slow_series):
        if fast_value is not None and slow_value is not None:
            macd_values.append(fast_value - slow_value)
    if not macd_values:
        return None, None, None
    macd_value = macd_values[-1]
    signal_value = ema(macd_values, signal)
    histogram = None if signal_value is None else macd_value - signal_value
    return macd_value, signal_value, histogram


def adx(candles: Sequence[Candle], period: int = 14) -> Decimal | None:
    if len(candles) < period * 2:
        return None
    trs: list[Decimal] = []
    plus_dm: list[Decimal] = []
    minus_dm: list[Decimal] = []
    for previous, current in zip(candles, candles[1:]):
        up = current.high - previous.high
        down = previous.low - current.low
        plus_dm.append(up if up > down and up > 0 else D("0"))
        minus_dm.append(down if down > up and down > 0 else D("0"))
        trs.append(max(current.high-current.low, abs(current.high-previous.close), abs(current.low-previous.close)))
    tr_avg = wilder_average(trs, period)
    plus_avg = wilder_average(plus_dm, period)
    minus_avg = wilder_average(minus_dm, period)
    if not tr_avg or plus_avg is None or minus_avg is None or tr_avg == 0:
        return None
    plus_di = D("100") * plus_avg / tr_avg
    minus_di = D("100") * minus_avg / tr_avg
    denominator = plus_di + minus_di
    if denominator == 0:
        return D("0")
    # Stable final DX approximation; avoids implying unavailable full historical ADX series.
    return D("100") * abs(plus_di - minus_di) / denominator


def vwap(candles: Sequence[Candle], period: int = 20) -> Decimal | None:
    if len(candles) < period:
        return None
    subset = candles[-period:]
    total_volume = sum((c.volume_quote for c in subset), D("0"))
    if total_volume <= 0:
        total_volume = sum((c.volume_contracts for c in subset), D("0"))
        volume_getter = lambda c: c.volume_contracts
    else:
        volume_getter = lambda c: c.volume_quote
    if total_volume <= 0:
        return None
    weighted = sum((((c.high+c.low+c.close)/D("3"))*volume_getter(c) for c in subset), D("0"))
    return weighted / total_volume


def volume_ratio(candles: Sequence[Candle], period: int = 20) -> Decimal | None:
    if len(candles) < period + 1:
        return None
    baseline = sum((c.volume_contracts for c in candles[-period-1:-1]), D("0")) / D(period)
    return None if baseline <= 0 else candles[-1].volume_contracts / baseline
