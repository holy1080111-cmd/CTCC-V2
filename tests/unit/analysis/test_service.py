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
    assert result.indicators.causal_trend is not None
    assert result.indicators.causal_trend.direction == "rising"
    assert result.indicators.causal_trend.confidence > Decimal("0.90")
    assert result.indicators.causal_state is not None
    assert result.indicators.causal_state.direction == "rising"
    assert result.indicators.causal_state.confidence > Decimal("0.90")
    assert result.indicators.return_interval is not None
    assert result.indicators.return_interval.direction == "rising"
    assert result.data_quality_ok is True


def test_timeframe_analysis_excludes_unconfirmed_candle_from_derivative():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = []
    for i in range(250):
        close = Decimal("100") * Decimal("1.001") ** i
        rows.append(
            Candle(
                timestamp=start + timedelta(minutes=5 * i),
                open=close,
                high=close + 1,
                low=close - 1,
                close=close,
                volume_contracts=Decimal("100"),
                volume_currency=Decimal("100"),
                volume_quote=Decimal("10000"),
                confirmed=True,
            )
        )
    rows.append(
        rows[-1].model_copy(
            update={
                "timestamp": start + timedelta(minutes=5 * 250),
                "close": Decimal("1"),
                "low": Decimal("1"),
                "confirmed": False,
            }
        )
    )
    quality = DataQualityReport(
        ok=True,
        candle_count=251,
        confirmed_count=250,
        expected_interval_seconds=300,
        issues=[],
    )

    result = analyze_timeframe("5m", rows, quality)

    assert result.last_closed_at == rows[-2].timestamp
    assert result.indicators.causal_trend is not None
    assert result.indicators.causal_trend.direction == "rising"
    assert result.indicators.causal_state is not None
    assert result.indicators.causal_state.direction == "rising"
    assert result.indicators.causal_state.shock_score == Decimal("0")
    assert result.indicators.return_interval is not None
    assert result.indicators.return_interval.direction == "rising"
