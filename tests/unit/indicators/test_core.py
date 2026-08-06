from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.domain.market import Candle
from app.indicators import atr, ema, macd, rsi, volume_ratio, vwap


def candles(count: int, step: Decimal = Decimal("1")) -> list[Candle]:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows=[]
    for i in range(count):
        close=Decimal("100")+step*i
        rows.append(Candle(timestamp=start+timedelta(minutes=5*i),open=close-1,high=close+2,low=close-2,close=close,volume_contracts=Decimal(100+i),volume_currency=Decimal(100+i),volume_quote=Decimal(10000+i),confirmed=True))
    return rows


def test_ema_and_rsi_uptrend():
    rows=candles(250)
    closes=[r.close for r in rows]
    assert ema(closes, 200) is not None
    assert rsi(closes, 14) == Decimal("100")


def test_atr_macd_vwap_volume():
    rows=candles(80)
    assert atr(rows,14) is not None
    m,s,h=macd([r.close for r in rows])
    assert m is not None and s is not None and h is not None
    assert vwap(rows,20) is not None
    assert volume_ratio(rows,20) is not None
