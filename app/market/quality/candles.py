from datetime import UTC, datetime, timedelta
from decimal import Decimal
from itertools import pairwise

from app.domain.market import Candle, DataQualityReport, MarketDataIssue

BAR_SECONDS: dict[str, int] = {
    "5m": 300,
    "15m": 900,
    "1H": 3600,
    "4H": 14400,
}


def _utc_time(value: datetime) -> datetime:
    """Normalize before arithmetic, including repeated local times at DST fold."""
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("an exact timezone-aware datetime is required")
    return value.astimezone(UTC)


def inspect_candles_at(
    candles: list[Candle], bar: str, *, current_time: datetime
) -> DataQualityReport:
    """Explicit-clock quality inspection; never repair, sort or trust caller flags.

    This stricter API is separate from the legacy wrapper below. Even a valid
    unconfirmed tail is critical: it may be excluded for descriptive analysis,
    but must not be silently promoted into qualified closed-bar evidence.
    """
    if bar not in BAR_SECONDS:
        raise ValueError(f"unsupported bar: {bar}")
    current = _utc_time(current_time)
    if type(candles) is not list or len(candles) > 1024:
        raise ValueError("candles must be an exact list of at most 1024 rows")
    interval = BAR_SECONDS[bar]
    issues: dict[str, MarketDataIssue] = {}

    def critical(code: str, detail: str) -> None:
        if code not in issues:
            issues[code] = MarketDataIssue(
                code=code, severity="critical", detail=detail
            )

    if not candles:
        critical("NO_CANDLES", "no candles received")
    confirmed_count = 0
    timestamps: list[datetime] = []
    confirmed_closes: list[datetime] = []
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    for index, candle in enumerate(candles):
        if (
            type(candle) is not Candle
            or len(candle.__dict__) != len(Candle.model_fields)
            or set(candle.__dict__) != set(Candle.model_fields)
            or candle.__pydantic_extra__
        ):
            critical("INVALID_CANDLE", f"row {index} is not an exact candle contract")
            continue
        if type(candle.confirmed) is not bool:
            critical("INVALID_CONFIRMATION", f"row {index} confirmation is not boolean")
        elif candle.confirmed:
            confirmed_count += 1
        else:
            critical("UNCONFIRMED_CANDLE", f"row {index} is not confirmed closed")
            if index != len(candles) - 1:
                critical("UNCONFIRMED_NOT_TAIL", f"row {index} is an interior open bar")
        try:
            opened = _utc_time(candle.timestamp)
            closed = opened + timedelta(seconds=interval)
        except (ValueError, TypeError, OverflowError):
            critical("INVALID_CANDLE_TIME", f"row {index} time is invalid")
        else:
            timestamps.append(opened)
            since_epoch = opened - epoch
            seconds = since_epoch.days * 86400 + since_epoch.seconds
            if opened.microsecond or seconds % interval:
                critical(
                    "OFF_GRID_CANDLE", f"row {index} does not open on the UTC bar grid"
                )
            if opened > current:
                critical("FUTURE_CANDLE", f"row {index} opens after observation")
            if candle.confirmed is True:
                confirmed_closes.append(closed)
                if closed > current:
                    critical(
                        "FUTURE_CONFIRMED_CANDLE",
                        f"row {index} has not closed at observation",
                    )
        prices = (candle.open, candle.high, candle.low, candle.close)
        volumes = (candle.volume_contracts, candle.volume_currency, candle.volume_quote)
        if any(
            type(value) is not Decimal or not value.is_finite()
            for value in (*prices, *volumes)
        ):
            critical(
                "NONFINITE_CANDLE_VALUE", f"row {index} requires finite Decimal values"
            )
            continue
        if min(prices) <= 0:
            critical("NON_POSITIVE_PRICE", f"row {index} price must be positive")
        if candle.high < max(candle.open, candle.close) or candle.low > min(
            candle.open, candle.close
        ):
            critical("INVALID_OHLC", f"row {index} OHLC geometry is invalid")
        if min(volumes) < 0:
            critical("NEGATIVE_VOLUME", f"row {index} volume must be nonnegative")
    if candles and not confirmed_count:
        critical("NO_CONFIRMED_CANDLES", "no closed candle available")
    if len(set(timestamps)) != len(timestamps):
        critical("DUPLICATE_CANDLE", "duplicate UTC candle timestamp")
    for previous, following in pairwise(timestamps):
        difference = following - previous
        if difference <= timedelta(0):
            critical("CANDLE_ORDER", "candle timestamps are not strictly increasing")
        if difference != timedelta(seconds=interval):
            critical("CANDLE_GAP", f"expected consecutive {interval}s UTC intervals")
    if confirmed_closes:
        age = current - confirmed_closes[-1]
        if age > timedelta(seconds=interval * 3):
            critical(
                "STALE_CANDLE",
                f"latest confirmed candle is {int(age.total_seconds())} seconds old",
            )
    return DataQualityReport(
        ok=not issues,
        candle_count=len(candles),
        confirmed_count=confirmed_count,
        expected_interval_seconds=interval,
        issues=list(issues.values()),
    )


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
        issues.append(
            MarketDataIssue(
                code="NO_CANDLES", severity="critical", detail="no candles received"
            )
        )
    if candles and not confirmed:
        issues.append(
            MarketDataIssue(
                code="NO_CONFIRMED_CANDLES",
                severity="critical",
                detail="no closed candle available",
            )
        )

    timestamps = [int(candle.timestamp.timestamp()) for candle in candles]
    if len(timestamps) != len(set(timestamps)):
        issues.append(
            MarketDataIssue(
                code="DUPLICATE_CANDLE",
                severity="critical",
                detail="duplicate candle timestamp",
            )
        )

    ordered = sorted(timestamps)
    for previous, current in pairwise(ordered):
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
            issues.append(
                MarketDataIssue(
                    code="NON_POSITIVE_PRICE",
                    severity="critical",
                    detail="candle price must be positive",
                )
            )
            break
        if candle.high < max(candle.open, candle.close) or candle.low > min(
            candle.open, candle.close
        ):
            issues.append(
                MarketDataIssue(
                    code="INVALID_OHLC",
                    severity="critical",
                    detail="OHLC geometry is invalid",
                )
            )
            break

    if confirmed:
        age = datetime.now(UTC) - candle_closed_at(confirmed[-1], bar)
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
