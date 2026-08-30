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


def test_staleness_uses_confirmed_candle_close_not_open_time() -> None:
    # OKX candle timestamps are interval-open timestamps.  A confirmed 5m bar
    # opened 17 minutes ago but closed 12 minutes ago and is still inside the
    # three-interval freshness allowance.
    opened_at = datetime.now(timezone.utc) - timedelta(minutes=17)

    report = inspect_candles([candle(opened_at)], "5m")

    assert report.ok is True
    assert not any(issue.code == "STALE_CANDLE" for issue in report.issues)
