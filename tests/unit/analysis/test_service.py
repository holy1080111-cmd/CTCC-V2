from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.analysis.service import analyze_timeframe
from app.domain.market import Candle, DataQualityReport


def test_timeframe_analysis_uptrend():
    start=datetime(2026,1,1,tzinfo=timezone.utc)
    rows=[]
    for i in range(250):
        close=Decimal(100)+Decimal(i)
        rows.append(Candle(timestamp=start+timedelta(minutes=5*i),open=close-1,high=close+2,low=close-2,close=close,volume_contracts=Decimal(100+i),volume_currency=Decimal(100+i),volume_quote=Decimal(10000+i),confirmed=True))
    quality=DataQualityReport(ok=True,candle_count=250,confirmed_count=250,expected_interval_seconds=300,issues=[])
    result=analyze_timeframe('5m',rows,quality)
    assert result.directional_bias == 'long'
    assert result.indicators.ema200 is not None
    assert result.data_quality_ok is True
