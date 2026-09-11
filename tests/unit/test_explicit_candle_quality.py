"""Explicit-clock OHLC quality fixtures, not observed market evidence."""

from datetime import UTC, datetime, timedelta, timezone, tzinfo
from decimal import Decimal

import pytest

from app.domain.market import Candle
from app.market.quality.candles import inspect_candles, inspect_candles_at

D = Decimal
NOW = datetime(2026, 9, 12, 12, tzinfo=UTC)


def row(opened=NOW - timedelta(minutes=5), **changes):
    candle = Candle(
        timestamp=opened,
        open=D(100),
        high=D(102),
        low=D(99),
        close=D(101),
        volume_contracts=D(10),
        volume_currency=D(10),
        volume_quote=D(1000),
        confirmed=True,
    )
    return candle.model_copy(update=changes)


def codes(candles, at=NOW):
    report = inspect_candles_at(candles, "5m", current_time=at)
    assert all(issue.severity == "critical" for issue in report.issues)
    assert report.ok == (not report.issues)
    return {issue.code for issue in report.issues}


def test_closed_time_is_open_plus_interval_and_exact_freshness_boundary():
    candles = [row(NOW - timedelta(minutes=5 * index)) for index in (3, 2, 1)]
    before = [item.model_dump() for item in candles]
    result = inspect_candles_at(candles, "5m", current_time=NOW)
    assert result.ok and result.confirmed_count == result.candle_count == 3
    assert result.expected_interval_seconds == 300
    assert not codes(candles, NOW + timedelta(minutes=15))
    assert codes(candles, NOW + timedelta(minutes=15, microseconds=1)) == {
        "STALE_CANDLE"
    }
    assert [item.model_dump() for item in candles] == before


@pytest.mark.parametrize(
    "changes,expected",
    [
        ({"confirmed": False}, "UNCONFIRMED_CANDLE"),
        ({"confirmed": 1}, "INVALID_CONFIRMATION"),
        ({"open": D(0)}, "NON_POSITIVE_PRICE"),
        ({"high": D(99)}, "INVALID_OHLC"),
        ({"low": D(103)}, "INVALID_OHLC"),
        ({"close": D("NaN")}, "NONFINITE_CANDLE_VALUE"),
        ({"open": D("sNaN")}, "NONFINITE_CANDLE_VALUE"),
        ({"volume_quote": D("Infinity")}, "NONFINITE_CANDLE_VALUE"),
        ({"volume_contracts": D(-1)}, "NEGATIVE_VOLUME"),
        ({"volume_currency": D(-1)}, "NEGATIVE_VOLUME"),
        ({"volume_quote": D(-1)}, "NEGATIVE_VOLUME"),
        ({"open": 100}, "NONFINITE_CANDLE_VALUE"),
        ({"timestamp": NOW.replace(tzinfo=None)}, "INVALID_CANDLE_TIME"),
        ({"timestamp": "2026-09-12T12:00:00Z"}, "INVALID_CANDLE_TIME"),
        (
            {"timestamp": NOW - timedelta(minutes=5) + timedelta(microseconds=1)},
            "OFF_GRID_CANDLE",
        ),
        ({"timestamp": NOW - timedelta(minutes=4)}, "OFF_GRID_CANDLE"),
        ({"timestamp": NOW}, "FUTURE_CONFIRMED_CANDLE"),
        ({"timestamp": NOW + timedelta(minutes=5)}, "FUTURE_CANDLE"),
        ({"extra_authority": True}, "INVALID_CANDLE"),
    ],
)
def test_invalid_rows_are_reproducibly_critical(changes, expected):
    candles = [row(**changes)]
    assert expected in codes(candles)
    assert inspect_candles_at(candles, "5m", current_time=NOW) == inspect_candles_at(
        candles, "5m", current_time=NOW
    )


def test_no_sorting_or_duplicate_repair_and_interior_unconfirmed_are_critical():
    rows = [row(NOW - timedelta(minutes=5 * index)) for index in (3, 2, 1)]
    assert "CANDLE_ORDER" in codes(list(reversed(rows)))
    assert "DUPLICATE_CANDLE" in codes([rows[0], rows[0], rows[-1]])
    assert "CANDLE_GAP" in codes([rows[0], rows[-1]])
    assert "UNCONFIRMED_NOT_TAIL" in codes(
        [rows[0].model_copy(update={"confirmed": False}), *rows[1:]]
    )
    assert codes([]) == {"NO_CANDLES"}
    assert "NO_CONFIRMED_CANDLES" in codes([row(confirmed=False)])


@pytest.mark.parametrize("current", [NOW.replace(tzinfo=None), "2026-09-12", None])
def test_explicit_clock_must_be_exact_and_aware(current):
    with pytest.raises(ValueError):
        inspect_candles_at([row()], "5m", current_time=current)


def test_unsupported_bar_and_unbounded_or_lazy_sequence_are_rejected():
    touched = []

    def infinite():
        touched.append(True)
        while True:
            yield row()

    for rows in (infinite(), (row(),), [row()] * 1025):
        with pytest.raises(ValueError):
            inspect_candles_at(rows, "5m", current_time=NOW)
    assert not touched
    with pytest.raises(ValueError):
        inspect_candles_at([row()], "1m", current_time=NOW)


class FoldOffset(tzinfo):
    def utcoffset(self, value):
        return timedelta(hours=-5 if value.fold else -4)

    def dst(self, value):
        return timedelta(0)


def test_timezone_equivalence_and_fold_use_utc_before_close_arithmetic():
    zone = timezone(timedelta(hours=8))
    assert inspect_candles_at([row()], "5m", current_time=NOW) == inspect_candles_at(
        [row((NOW - timedelta(minutes=5)).astimezone(zone))],
        "5m",
        current_time=NOW.astimezone(zone),
    )
    folded = FoldOffset()
    opened = datetime(2026, 11, 1, 1, 55, tzinfo=folded)
    current = datetime(2026, 11, 1, 1, 5, tzinfo=folded, fold=1)
    assert not codes([row(opened)], current)
    future_open = datetime(2026, 11, 1, 1, 0, tzinfo=folded, fold=1)
    prior_current = datetime(2026, 11, 1, 1, 55, tzinfo=folded)
    assert "FUTURE_CONFIRMED_CANDLE" in codes([row(future_open)], prior_current)


def test_legacy_quality_keeps_its_previous_wall_clock_policy():
    opened = datetime.now(UTC).replace(second=0, microsecond=0)
    opened -= timedelta(minutes=opened.minute % 5)
    # Legacy accepts an exchange-confirmed current interval; the new explicit
    # policy flags that close as future, without silently changing legacy users.
    assert inspect_candles([row(opened)], "5m").ok
    assert "FUTURE_CONFIRMED_CANDLE" in codes([row(opened)], opened)
