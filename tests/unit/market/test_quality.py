from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.domain.market import Candle
from app.market.quality.candles import inspect_candles


def candle(timestamp: datetime, confirmed: bool = True) -> Candle:
    return Candle(
        timestamp=timestamp,
        open=Decimal("100"), high=Decimal("105"), low=Decimal("95"), close=Decimal("101"),
        volume_contracts=Decimal("1"), volume_currency=Decimal("1"), volume_quote=Decimal("100"),
        confirmed=confirmed,
    )


def test_quality_accepts_continuous_closed_candles() -> None:
    end = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    end -= timedelta(minutes=end.minute % 5)
    rows = [candle(end - timedelta(minutes=5 * i)) for i in reversed(range(5))]
    report = inspect_candles(rows, "5m")
    assert report.ok is True


def test_quality_rejects_gap() -> None:
    now = datetime.now(timezone.utc)
    rows = [candle(now - timedelta(minutes=15)), candle(now - timedelta(minutes=5))]
    report = inspect_candles(rows, "5m")
    assert report.ok is False
    assert any(issue.code == "CANDLE_GAP" for issue in report.issues)
