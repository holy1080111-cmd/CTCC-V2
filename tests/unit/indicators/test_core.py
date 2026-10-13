from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.domain.market import Candle
from app.indicators import atr, ema, macd, rsi, swap_vwap_v2, volume_ratio, vwap


def candles(count: int, step: Decimal = Decimal(1)) -> list[Candle]:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    rows = []
    for i in range(count):
        close = Decimal(100) + step * i
        rows.append(
            Candle(
                timestamp=start + timedelta(minutes=5 * i),
                open=close - 1,
                high=close + 2,
                low=close - 2,
                close=close,
                volume_contracts=Decimal(100 + i),
                volume_currency=Decimal(100 + i),
                volume_quote=Decimal(10000 + i),
                confirmed=True,
            )
        )
    return rows


def swap_candles(count: int) -> list[Candle]:
    rows = candles(count)
    for row in rows:
        row.volume_quote = row.volume_currency * row.close
    return rows


def test_ema_and_rsi_uptrend():
    rows = candles(250)
    closes = [r.close for r in rows]
    assert ema(closes, 200) is not None
    assert rsi(closes, 14) == Decimal(100)


def test_atr_macd_vwap_volume():
    rows = candles(80)
    assert atr(rows, 14) is not None
    m, s, h = macd([r.close for r in rows])
    assert m is not None and s is not None and h is not None
    assert vwap(rows, 20) is not None
    assert volume_ratio(rows, 20) is not None


def test_swap_vwap_v2_uses_reported_quote_over_base_and_skips_zero_trade_bars():
    rows = candles(20)
    for row in rows:
        row.volume_contracts = Decimal(0)
        row.volume_currency = Decimal(0)
        row.volume_quote = Decimal(0)
    for row, price in ((rows[-2], Decimal(100)), (rows[-1], Decimal(200))):
        row.open = row.high = row.low = row.close = price
        row.volume_contracts = Decimal(1)
        row.volume_currency = Decimal(1)
        row.volume_quote = price
    assert swap_vwap_v2(rows) == Decimal(150)
    assert vwap(rows) == Decimal(500) / Decimal(3)


@pytest.mark.parametrize(
    "changes",
    [
        {"volume_currency": Decimal(0)},
        {"volume_quote": Decimal(0)},
        {"volume_contracts": Decimal(0)},
        {"volume_currency": None},
        {"volume_quote": Decimal(-1)},
        {"volume_currency": Decimal("NaN")},
        {"volume_quote": Decimal("Infinity")},
    ],
)
def test_swap_vwap_v2_rejects_missing_or_contradictory_units(changes):
    rows = swap_candles(20)
    rows[-1] = rows[-1].model_copy(update=changes)
    with pytest.raises(ValueError, match="swap_vwap_v2_volume_"):
        swap_vwap_v2(rows)


@pytest.mark.parametrize("outside", ("below", "above"))
def test_swap_vwap_v2_rejects_turnover_price_outside_source_ohlc(outside):
    rows = swap_candles(20)
    last = rows[-1]
    impossible_price = (
        last.low - Decimal("0.00000001")
        if outside == "below"
        else last.high + Decimal("0.00000001")
    )
    rows[-1] = last.model_copy(
        update={"volume_quote": last.volume_currency * impossible_price}
    )
    with pytest.raises(ValueError, match="swap_vwap_v2_turnover_price_outside_ohlc"):
        swap_vwap_v2(rows)


@pytest.mark.parametrize("bad_price", (Decimal("NaN"), Decimal("Infinity")))
def test_swap_vwap_v2_rejects_nonfinite_source_ohlc(bad_price):
    rows = swap_candles(20)
    rows[-1] = rows[-1].model_copy(update={"high": bad_price})
    with pytest.raises(ValueError, match="swap_vwap_v2_ohlc_invalid"):
        swap_vwap_v2(rows)


def test_swap_vwap_v2_rejects_all_zero_volume_without_legacy_fallback():
    rows = candles(20)
    rows = [
        row.model_copy(
            update={
                "volume_contracts": Decimal(0),
                "volume_currency": Decimal(0),
                "volume_quote": Decimal(0),
            }
        )
        for row in rows
    ]
    with pytest.raises(ValueError, match="swap_vwap_v2_total_base_missing"):
        swap_vwap_v2(rows)
