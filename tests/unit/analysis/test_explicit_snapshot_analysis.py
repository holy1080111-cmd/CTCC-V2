"""Deterministic four-timeframe calculation; synthetic bars, no runtime wiring."""

from datetime import UTC, datetime, timedelta, timezone
from decimal import ROUND_UP, Context, Decimal, Inexact, localcontext
from types import SimpleNamespace

import pytest

from app.analysis import service
from app.analysis.service import AnalysisService, analyze_snapshot_at
from app.domain.market import (
    Candle,
    DataQualityReport,
    MarketSnapshot,
    OrderBook,
    Ticker,
)
from app.market.quality.candles import BAR_SECONDS
from tests.unit.test_explicit_candle_quality import FoldOffset

D = Decimal
NOW = datetime(2026, 9, 12, 12, tzinfo=UTC)
VERSION = "explicit-synthetic-v1"


def snapshot(count=90):
    frames = {}
    for bar, seconds in BAR_SECONDS.items():
        frames[bar] = [
            Candle(
                timestamp=NOW - timedelta(seconds=(count - index) * seconds),
                open=D(100) + D(index) / 10,
                high=D(102) + D(index) / 10,
                low=D(99) + D(index) / 10,
                close=D(101) + D(index) / 10,
                volume_contracts=D(10),
                volume_currency=D(10),
                volume_quote=D(1000),
                confirmed=True,
            )
            for index in range(count)
        ]
    ticker = Ticker(
        instrument_id="BTC-USDT-SWAP",
        last=D(110),
        bid=D(109),
        ask=D(111),
        bid_size=D(10),
        ask_size=D(10),
        open_24h=D(100),
        high_24h=D(120),
        low_24h=D(90),
        volume_24h=D(100),
        volume_quote_24h=D(10000),
        timestamp=NOW,
    )
    return MarketSnapshot(
        symbol="BTCUSDT",
        instrument_id=ticker.instrument_id,
        ticker=ticker,
        mark_price=D(110),
        funding_rate=D(0),
        next_funding_time=None,
        open_interest_contracts=D(100),
        open_interest_currency=D(100),
        order_book=OrderBook(
            instrument_id=ticker.instrument_id, bids=[], asks=[], timestamp=NOW
        ),
        candles=frames,
        quality={},
        received_at=NOW,
    )


def analyze(value, at=NOW, version=VERSION):
    return analyze_snapshot_at(value, evaluated_at=at, version=version)


def test_explicit_api_rebuilds_quality_and_never_reads_settings_or_wall_clock(
    monkeypatch,
):
    value = snapshot()
    before = value.model_dump()

    def forbidden(*_args, **_kwargs):
        pytest.fail(
            "explicit snapshot analysis must not access settings, clock or network"
        )

    monkeypatch.setattr(service, "get_settings", forbidden)
    monkeypatch.setattr(service, "MarketDataService", forbidden)
    monkeypatch.setattr(service, "datetime", SimpleNamespace(now=forbidden))
    result = analyze(value)
    assert result.generated_at == NOW and result.generated_at.tzinfo is UTC
    assert result.version == VERSION
    assert tuple(result.timeframe_analyses) == ("4H", "1H", "15m", "5m")
    assert all(view.data_quality_ok for view in result.timeframe_analyses.values())
    assert all(
        view.last_closed_at == NOW for view in result.timeframe_analyses.values()
    )
    assert value.model_dump() == before
    assert analyze(value) == result


def test_caller_quality_flags_and_candle_dictionary_order_are_not_authority():
    value = snapshot()
    expected = analyze(value)
    dirty = value.model_copy(
        update={
            "quality": {"invented": "not consulted"},
            "candles": dict(reversed(tuple(value.candles.items()))),
        }
    )
    assert analyze(dirty) == expected
    late = analyze(value, NOW + timedelta(minutes=16))
    assert not late.trade_ready
    assert "5m_data_quality" in late.blockers
    assert "critical:STALE_CANDLE" in late.timeframe_analyses["5m"].data_quality_issues


def test_current_unconfirmed_tail_is_excluded_but_remains_critical():
    value = snapshot()
    baseline = analyze(value)
    rows = value.candles["5m"]
    value.candles["5m"] = [
        *rows,
        rows[-1].model_copy(update={"timestamp": NOW, "confirmed": False}),
    ]
    result = analyze(value)
    view = result.timeframe_analyses["5m"]
    assert view.indicators == baseline.timeframe_analyses["5m"].indicators
    assert view.candle_count == len(rows) and view.last_closed_at == NOW
    assert "critical:UNCONFIRMED_CANDLE" in view.data_quality_issues
    assert not view.data_quality_ok and not result.trade_ready


@pytest.mark.parametrize(
    "defect",
    [
        "ohlc",
        "nan",
        "grid",
        "reverse",
        "duplicate",
        "gap",
        "future",
        "interior_open",
        "dirty_extra",
    ],
)
def test_forged_ok_cannot_make_unsafe_candles_analyzable(defect):
    value = snapshot()
    rows = value.candles["5m"]
    value.quality = {
        bar: DataQualityReport(
            ok=True,
            candle_count=90,
            confirmed_count=90,
            expected_interval_seconds=seconds,
        )
        for bar, seconds in BAR_SECONDS.items()
    }
    if defect == "reverse":
        value.candles["5m"] = list(reversed(rows))
    elif defect == "duplicate":
        rows[-1] = rows[-2]
    elif defect == "gap":
        del rows[-2]
    else:
        changes = {
            "ohlc": {"high": D(1)},
            "nan": {"close": D("NaN")},
            "grid": {"timestamp": rows[-1].timestamp + timedelta(seconds=1)},
            "future": {"timestamp": NOW},
            "interior_open": {"confirmed": False},
            "dirty_extra": {"execution_authority": True},
        }[defect]
        index = -2 if defect == "interior_open" else -1
        rows[index] = rows[index].model_copy(update=changes)
    with pytest.raises(ValueError):
        analyze(value)


def test_confirmed_close_after_capture_is_rejected_even_if_evaluation_is_later():
    value = snapshot()
    value.received_at = NOW - timedelta(microseconds=1)
    with pytest.raises(ValueError, match="closed after capture"):
        analyze(value, NOW + timedelta(minutes=1))


@pytest.mark.parametrize(
    "defect",
    [
        "naive_eval",
        "naive_capture",
        "capture_future",
        "missing_tf",
        "extra_tf",
        "short_history",
        "version_missing",
        "version_long",
        "version_type",
    ],
)
def test_explicit_required_inputs_fail_closed(defect):
    value = snapshot()
    at, version = NOW, VERSION
    if defect == "naive_eval":
        at = NOW.replace(tzinfo=None)
    elif defect == "naive_capture":
        value.received_at = NOW.replace(tzinfo=None)
    elif defect == "capture_future":
        value.received_at += timedelta(seconds=1)
    elif defect == "missing_tf":
        del value.candles["4H"]
    elif defect == "extra_tf":
        value.candles["1m"] = value.candles["5m"]
    elif defect == "short_history":
        value.candles["5m"] = value.candles["5m"][-29:]
    else:
        version = {"version_missing": "", "version_long": "x" * 65, "version_type": 1}[
            defect
        ]
    with pytest.raises(ValueError):
        analyze(value, at, version)


def test_decimal_environment_and_equivalent_timezone_do_not_change_output():
    value = snapshot()
    expected = analyze(value)
    zone = timezone(timedelta(hours=8))
    value.received_at = NOW.astimezone(zone)
    for rows in value.candles.values():
        for candle in rows:
            candle.timestamp = candle.timestamp.astimezone(zone)
    with localcontext(Context(prec=6, rounding=ROUND_UP)) as context:
        context.traps[Inexact] = True
        assert analyze(value, NOW.astimezone(zone)) == expected
        assert context.prec == 6 and context.traps[Inexact]


def test_repeated_local_capture_and_evaluation_times_compare_in_utc():
    value = snapshot()
    zone = FoldOffset()
    capture = datetime(2026, 11, 1, 1, 55, tzinfo=zone)
    evaluated = datetime(2026, 11, 1, 1, 5, tzinfo=zone, fold=1)
    offset = capture.astimezone(UTC) - NOW
    for rows in value.candles.values():
        for candle in rows:
            candle.timestamp += offset
    # Keep UTC bar grids intact while exercising same-tzinfo folded clocks.
    for bar, rows in value.candles.items():
        seconds = BAR_SECONDS[bar]
        correction = int(rows[-1].timestamp.timestamp()) % seconds
        for candle in rows:
            candle.timestamp -= timedelta(seconds=correction)
    value.received_at = capture
    result = analyze(value, evaluated)
    assert result.generated_at == evaluated.astimezone(UTC)
    with pytest.raises(ValueError, match="after evaluation"):
        analyze(value.model_copy(update={"received_at": evaluated}), capture)


def test_legacy_snapshot_wrapper_still_uses_injected_quality_settings_and_clock(
    monkeypatch,
):
    value = snapshot()
    value.quality = {
        bar: DataQualityReport(
            ok=False,
            candle_count=90,
            confirmed_count=90,
            expected_interval_seconds=seconds,
        )
        for bar, seconds in BAR_SECONDS.items()
    }
    monkeypatch.setattr(
        service, "get_settings", lambda: SimpleNamespace(app_version="legacy-config")
    )
    monkeypatch.setattr(service, "datetime", SimpleNamespace(now=lambda zone: NOW))
    legacy = AnalysisService(market_service=object()).analyze_snapshot(value)
    assert legacy.version == "legacy-config" and legacy.generated_at == NOW
    assert all(not view.data_quality_ok for view in legacy.timeframe_analyses.values())
    assert not legacy.trade_ready
