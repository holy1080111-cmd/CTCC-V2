from datetime import datetime, timedelta, timezone

from app.domain.market import Candle, DataQualityReport, MarketDataIssue

BAR_SECONDS: dict[str, int] = {
    "5m": 300,
    "15m": 900,
    "1H": 3600,
    "4H": 14400,
}


def candle_closed_at(candle: Candle, bar: str) -> datetime:
    """Return the actual close time for an OKX candle.

    OKX supplies the interval opening timestamp in ``ts`` even when ``confirm``
    says the candle is closed.  Keeping this conversion in one place prevents
    audit fields and freshness checks from mislabeling the opening time as the
    close time.
    """

    if bar not in BAR_SECONDS:
        raise ValueError(f"unsupported bar: {bar}")
    return candle.timestamp + timedelta(seconds=BAR_SECONDS[bar])


def inspect_candles(candles: list[Candle], bar: str) -> DataQualityReport:
    if bar not in BAR_SECONDS:
        raise ValueError(f"unsupported bar: {bar}")

    interval = BAR_SECONDS[bar]
    issues: list[MarketDataIssue] = []
    confirmed = [candle for candle in candles if candle.confirmed]

    if not candles:
        issues.append(MarketDataIssue(code="NO_CANDLES", severity="critical", detail="no candles received"))
    if candles and not confirmed:
        issues.append(MarketDataIssue(code="NO_CONFIRMED_CANDLES", severity="critical", detail="no closed candle available"))

    timestamps = [int(candle.timestamp.timestamp()) for candle in candles]
    if len(timestamps) != len(set(timestamps)):
        issues.append(MarketDataIssue(code="DUPLICATE_CANDLE", severity="critical", detail="duplicate candle timestamp"))

    ordered = sorted(timestamps)
    for previous, current in zip(ordered, ordered[1:]):
        difference = current - previous
        if difference != interval:
            issues.append(
                MarketDataIssue(
                    code="CANDLE_GAP",
                    severity="critical",
                    detail=f"expected {interval}s interval, received {difference}s",
                )
            )
            break

    for candle in candles:
        if min(candle.open, candle.high, candle.low, candle.close) <= 0:
            issues.append(MarketDataIssue(code="NON_POSITIVE_PRICE", severity="critical", detail="candle price must be positive"))
            break
        if candle.high < max(candle.open, candle.close) or candle.low > min(candle.open, candle.close):
            issues.append(MarketDataIssue(code="INVALID_OHLC", severity="critical", detail="OHLC geometry is invalid"))
            break

    if confirmed:
        age = datetime.now(timezone.utc) - candle_closed_at(confirmed[-1], bar)
        if age.total_seconds() > interval * 3:
            issues.append(
                MarketDataIssue(
                    code="STALE_CANDLE",
                    severity="critical",
                    detail=f"latest confirmed candle is {int(age.total_seconds())} seconds old",
                )
            )

    return DataQualityReport(
        ok=not any(issue.severity == "critical" for issue in issues),
        candle_count=len(candles),
        confirmed_count=len(confirmed),
        expected_interval_seconds=interval,
        issues=issues,
    )
