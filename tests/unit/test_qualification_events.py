"""Reproducible OHLC-only fixtures; no downloaded data or runtime calls."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta, timezone, tzinfo
from decimal import Decimal, localcontext

import pytest

from app.analysis.service import analyze_timeframe
from app.domain.analysis import MultiTimeframeAnalysis
from app.domain.market import (
    Candle,
    DataQualityReport,
    MarketDataIssue,
    MarketSnapshot,
    OrderBook,
    Ticker,
)
from app.indicators.core import macd
from app.market.quality.candles import BAR_SECONDS, candle_closed_at
from app.trade_qualification.events import STRATEGIES, extract_trigger

D = Decimal
OBSERVED = datetime(2024, 2, 20, 10, 10, tzinfo=UTC)


def _row(time, close, *, open_=None, high=None, low=None):
    return Candle(
        timestamp=time,
        open=close if open_ is None else open_,
        high=close + D("0.05") if high is None else high,
        low=close - D("0.05") if low is None else low,
        close=close,
        volume_contracts=D(100),
        volume_currency=D(100),
        volume_quote=D(10000),
        confirmed=True,
    )


def _rows(timeframe, count=240, *, falling=False):
    seconds = BAR_SECONDS[timeframe]
    end = datetime.fromtimestamp(int(OBSERVED.timestamp()) // seconds * seconds, UTC)
    first = end - timedelta(seconds=count * seconds)
    return [
        _row(
            first + timedelta(seconds=index * seconds),
            D(120) - D(index) * D("0.08") if falling else D(100),
        )
        for index in range(count)
    ]


def _set(rows, offset, close, high, low, open_=None):
    rows[offset] = _row(
        rows[offset].timestamp,
        D(close),
        high=D(high),
        low=D(low),
        open_=None if open_ is None else D(open_),
    )


def source(strategy, direction="long"):
    frames = {timeframe: _rows(timeframe) for timeframe in BAR_SECONDS}
    native = "1H" if strategy == "structure_reversal" else "15m"
    rows = _rows(
        native, falling=strategy in {"structure_reversal", "liquidity_sweep_reversal"}
    )
    if strategy in {
        "breakout_continuation",
        "structure_reversal",
        "liquidity_sweep_reversal",
    }:
        # Both anchors have their two right-side confirmation bars before the break.
        _set(rows, -7, "100.3", "100.5", "100.1")
        _set(rows, -6, "100.2", "100.4", "99.0")
        _set(rows, -5, "100.4", "101.0", "100.0")
        _set(rows, -4, "100.3", "100.6", "100.1")
        _set(rows, -3, "100.4", "100.7", "100.2")
        _set(rows, -2, "100.5", "100.8", "100.3")
        _set(
            rows,
            -1,
            "101.5",
            "101.8",
            "98.5" if strategy == "liquidity_sweep_reversal" else "100.4",
        )
    elif strategy == "trend_pullback":
        _set(rows, -2, "102", "102.1", "101.9")
        _set(rows, -1, "100.2", "100.4", "100.0")
    elif strategy == "fvg_return":
        _set(rows, -4, "99.9", "100", "99.8")
        _set(rows, -3, "100.5", "100.8", "100.2")
        _set(rows, -2, "101.2", "101.4", "101.0")
        _set(rows, -1, "100.75", "101.2", "100.5")
    elif strategy == "order_block_return":
        _set(rows, -3, "100", "100.6", "99.8", "100.5")
        _set(rows, -2, "102", "102.2", "100.6", "100.7")
        _set(rows, -1, "100.4", "100.8", "100.2")
    elif strategy == "range_reversal":
        _set(rows, -6, "100.4", "100.6", "100.2")
        _set(rows, -5, "100.2", "100.4", "99.9")
        _set(rows, -4, "100.3", "100.5", "100.1")
        _set(rows, -3, "100.5", "100.7", "100.2")
        _set(rows, -2, "101.5", "101.7", "101.3")
        _set(rows, -1, "100.1", "100.3", "99.95")
    else:
        _set(rows, -5, "100", "100.1", "99.95")
        _set(rows, -4, "100", "100.04", "99.96")
        _set(rows, -3, "100", "100.04", "99.96")
        _set(rows, -2, "100", "100.04", "99.96")
        _set(rows, -1, "100.5", "115", "99")
    frames[native] = rows
    five = frames["5m"]
    for index, row in enumerate(five):
        close = D(102) + D(index % 8 - 4) * D("0.05")
        five[index] = _row(
            row.timestamp, close, high=close + D("0.01"), low=close - D("0.01")
        )
    _set(five, -2, "101.9", "101.91", "101.89")
    _set(five, -1, "102.1", "102.11", "102.09")
    if direction == "short":
        for key, values in frames.items():
            frames[key] = [
                row.model_copy(
                    update={
                        "open": D(200) - row.open,
                        "close": D(200) - row.close,
                        "low": D(200) - row.high,
                        "high": D(200) - row.low,
                    }
                )
                for row in values
            ]
    return _snapshot(frames)


def _snapshot(frames):
    qualities = {
        tf: DataQualityReport(
            ok=True,
            candle_count=len(rows),
            confirmed_count=sum(row.confirmed for row in rows),
            expected_interval_seconds=BAR_SECONDS[tf],
            issues=[],
        )
        for tf, rows in frames.items()
    }
    views = {
        tf: analyze_timeframe(tf, rows, qualities[tf]) for tf, rows in frames.items()
    }
    price = views["5m"].close
    analysis = MultiTimeframeAnalysis(
        symbol="BTC/USDT:USDT",
        instrument_id="BTC-USDT-SWAP",
        price=price,
        regime="synthetic_event_fixture",
        overall_bias="neutral",
        alignment_score=0,
        trade_ready=False,
        timeframe_analyses=views,
        generated_at=OBSERVED,
    )
    ticker = Ticker(
        instrument_id=analysis.instrument_id,
        last=price,
        bid=price - D("0.01"),
        ask=price + D("0.01"),
        bid_size=D(1),
        ask_size=D(1),
        open_24h=price,
        high_24h=price + D(1),
        low_24h=price - D(1),
        volume_24h=D(1),
        volume_quote_24h=D(1),
        timestamp=OBSERVED,
    )
    market = MarketSnapshot(
        symbol=analysis.symbol,
        instrument_id=analysis.instrument_id,
        ticker=ticker,
        mark_price=price,
        funding_rate=D(0),
        next_funding_time=None,
        open_interest_contracts=D(1),
        open_interest_currency=D(1),
        order_book=OrderBook(
            instrument_id=analysis.instrument_id, bids=[], asks=[], timestamp=OBSERVED
        ),
        candles=frames,
        quality=qualities,
        received_at=OBSERVED,
    )
    return market, analysis


def detect(market, analysis, strategy, direction="long", observed_at=OBSERVED):
    return extract_trigger(
        market,
        analysis,
        report_id="synthetic_events",
        strategy=strategy,
        direction=direction,
        observed_at=observed_at,
        trigger_ttl_seconds=300,
    )


@pytest.mark.parametrize("strategy", sorted(STRATEGIES))
@pytest.mark.parametrize("direction", ["long", "short"])
def test_each_strategy_has_a_reproducible_source_bound_closed_event(
    strategy, direction
):
    market, analysis = source(strategy, direction)
    result = detect(market, analysis, strategy, direction)
    assert result.fail_codes == (), result
    assert result.trigger is not None
    assert result.trigger.trigger_time == OBSERVED
    assert result.trigger.trigger_time == candle_closed_at(
        market.candles["5m"][-1], "5m"
    )
    assert result.trigger.trigger_time != market.candles["5m"][-1].timestamp
    assert result.setup_time <= market.candles["5m"][-1].timestamp
    assert result.trigger.expires_at == OBSERVED + timedelta(seconds=300)
    assert result.source_sha256 is not None and result.source_timeframe == "5m"
    assert {"setup_timeframe", "zone_low", "zone_high", "atr"} <= dict(
        result.setup_basis
    ).keys()
    assert analysis.trade_ready is False  # Supplied boolean is not event authority.


@pytest.mark.parametrize(
    "defect",
    [
        "duplicate",
        "unordered",
        "gap",
        "future",
        "nan",
        "unconfirmed_inside",
        "analysis_close",
        "analysis_time",
        "quality",
        "missing_tf",
        "naive",
        "ticker_identity",
        "book_identity",
        "capture_before_close",
        "analysis_before_capture",
        "quality_critical",
        "analysis_quality_issue",
        "quote_after_capture",
        "price_precision",
        "decimal_exponent",
    ],
)
def test_invalid_or_unaligned_source_never_emits_trigger(defect):
    market, analysis = source("fvg_return")
    rows = market.candles["5m"]
    if defect == "duplicate":
        rows[-2] = rows[-2].model_copy(update={"timestamp": rows[-3].timestamp})
    elif defect == "unordered":
        rows[-3], rows[-2] = rows[-2], rows[-3]
    elif defect == "gap":
        del rows[-3]
    elif defect == "future":
        rows.append(rows[-1].model_copy(update={"timestamp": OBSERVED}))
    elif defect == "nan":
        rows[-1] = rows[-1].model_copy(update={"low": D("NaN")})
    elif defect == "unconfirmed_inside":
        rows[-3] = rows[-3].model_copy(update={"confirmed": False})
    elif defect == "analysis_close":
        analysis.timeframe_analyses["5m"].close += D(1)
    elif defect == "analysis_time":
        analysis.timeframe_analyses["5m"].last_closed_at = rows[-1].timestamp
    elif defect == "quality":
        market.quality["5m"].ok = False
    elif defect == "missing_tf":
        del market.candles["4H"]
    elif defect == "naive":
        rows[-1] = rows[-1].model_copy(
            update={"timestamp": rows[-1].timestamp.replace(tzinfo=None)}
        )
    elif defect == "ticker_identity":
        market.ticker.instrument_id = "ETH-USDT-SWAP"
    elif defect == "book_identity":
        market.order_book.instrument_id = "ETH-USDT-SWAP"
    elif defect == "capture_before_close":
        market.received_at = OBSERVED - timedelta(seconds=1)
        market.ticker.timestamp = market.order_book.timestamp = market.received_at
    elif defect == "analysis_before_capture":
        analysis.generated_at = OBSERVED - timedelta(seconds=1)
    elif defect == "quality_critical":
        market.quality["5m"].issues.append(
            MarketDataIssue(
                code="FAULT", severity="critical", detail="synthetic defect"
            )
        )
    elif defect == "analysis_quality_issue":
        analysis.timeframe_analyses["5m"].data_quality_issues.append("FAULT")
    elif defect == "quote_after_capture":
        market.order_book.timestamp = OBSERVED + timedelta(seconds=1)
    elif defect == "price_precision":
        rows[-1] = rows[-1].model_copy(update={"low": D("102.090000000000000000001")})
    else:
        market.funding_rate = D("1e-1000")
    result = detect(market, analysis, "fvg_return")
    assert result.fail_codes and result.trigger is None and result.source_sha256 is None


def test_insufficient_previous_macd_cannot_be_treated_as_false():
    market, _ = source("fvg_return")
    market.candles["5m"] = market.candles["5m"][-34:]
    market, analysis = _snapshot(market.candles)
    assert macd([row.close for row in market.candles["5m"]][:-1])[2] is None
    result = detect(market, analysis, "fvg_return")
    assert result.trigger is None
    assert result.fail_codes == ("trigger_missing",)
    assert dict(result.setup_basis)["missing_reason"] == "insufficient_momentum_history"


def test_unconfirmed_extreme_future_close_cannot_change_past_event():
    market, analysis = source("fvg_return")
    before = detect(market, analysis, "fvg_return")
    market.candles["5m"].append(
        _row(OBSERVED, D(500)).model_copy(update={"confirmed": False})
    )
    market.quality["5m"].candle_count += 1
    after = detect(market, analysis, "fvg_return")
    assert after.trigger == before.trigger
    assert after.setup_time == before.setup_time
    assert (
        after.source_sha256 != before.source_sha256
    )  # Full snapshot provenance changes.


def test_source_hash_binds_analysis_without_trusting_its_boolean_states():
    market, analysis = source("order_block_return")
    before = detect(market, analysis, "order_block_return")
    changed = analysis.model_copy(update={"regime": "invented", "trade_ready": True})
    after = detect(market, changed, "order_block_return")
    assert after.trigger == before.trigger
    assert after.source_sha256 != before.source_sha256


def test_repeated_observation_does_not_renew_event_and_decimal_context_is_isolated():
    market, analysis = source("trend_pullback")
    before = detect(market, analysis, "trend_pullback")
    later = detect(
        market,
        analysis,
        "trend_pullback",
        observed_at=OBSERVED + timedelta(seconds=600),
    )
    assert later.trigger == before.trigger
    with localcontext() as ctx:
        ctx.prec = 6
        low_precision = detect(market, analysis, "trend_pullback")
    assert low_precision == before


def test_fvg_presence_without_a_later_return_is_not_an_event():
    market, _ = source("fvg_return")
    rows = market.candles["15m"]
    _set(rows, -1, "102", "102.1", "101.9")
    market, analysis = _snapshot(market.candles)
    assert analysis.timeframe_analyses["15m"].structure.fair_value_gaps
    assert detect(market, analysis, "fvg_return").trigger is None


def test_order_block_source_candle_does_not_establish_formation_time():
    market, analysis = source("order_block_return")
    result = detect(market, analysis, "order_block_return")
    basis = dict(result.setup_basis)
    assert (
        basis["formation_closed_at"]
        == candle_closed_at(market.candles["15m"][-2], "15m").isoformat()
    )
    assert datetime.fromisoformat(
        basis["formation_closed_at"]
    ) > datetime.fromisoformat(basis["source_open_at"])


def test_sweep_requires_observed_wick_beyond_level_not_only_close_and_choch():
    market, _ = source("liquidity_sweep_reversal")
    rows = market.candles["15m"]
    _set(rows, -1, "101.5", "101.8", "100.4")
    market, analysis = _snapshot(market.candles)
    assert analysis.timeframe_analyses["15m"].structure.choch == "up"
    assert detect(market, analysis, "liquidity_sweep_reversal").trigger is None


def test_expansion_requires_observed_compression_not_normal_volatility():
    market, _ = source("volatility_expansion")
    rows = market.candles["15m"]
    for index in range(len(rows) - 8):
        row = rows[index]
        rows[index] = row.model_copy(
            update={"high": row.close + D("0.3"), "low": row.close - D("0.3")}
        )
    market, analysis = _snapshot(market.candles)
    assert detect(market, analysis, "volatility_expansion").trigger is None


def test_post_setup_invalidation_is_retained_without_silent_rearming():
    market, _ = source("fvg_return")
    row = market.candles["5m"][-2]
    market.candles["5m"][-2] = row.model_copy(update={"low": D(99)})
    market, analysis = _snapshot(market.candles)
    result = detect(market, analysis, "fvg_return")
    assert result.trigger is not None
    assert result.trigger.invalidation_reason == "trigger_invalidated"
    assert result.fail_codes == ("trigger_invalidated",)


def test_inputs_are_not_mutated():
    market, analysis = source("range_reversal")
    before = deepcopy((market, analysis))
    detect(market, analysis, "range_reversal")
    assert (market, analysis) == before


@pytest.mark.parametrize("strategy", ["fvg_return", "liquidity_sweep_reversal"])
def test_same_close_momentum_cannot_prove_a_post_setup_trigger(strategy):
    market, _ = source(strategy)
    market.candles["5m"] = [
        row.model_copy(update={"timestamp": row.timestamp - timedelta(minutes=10)})
        for row in market.candles["5m"]
    ]
    market, analysis = _snapshot(market.candles)
    result = detect(market, analysis, strategy)
    assert result.setup_time == candle_closed_at(market.candles["5m"][-1], "5m")
    assert result.trigger is None and result.fail_codes == ("trigger_missing",)


def test_first_complete_five_minute_bar_after_setup_can_trigger():
    market, _ = source("fvg_return")
    market.candles["5m"] = [
        row.model_copy(update={"timestamp": row.timestamp - timedelta(minutes=5)})
        for row in market.candles["5m"]
    ]
    market, analysis = _snapshot(market.candles)
    result = detect(market, analysis, "fvg_return")
    assert result.fail_codes == ()
    assert result.setup_time == market.candles["5m"][-1].timestamp
    assert result.trigger.trigger_time == OBSERVED - timedelta(minutes=5)


def test_true_to_true_momentum_does_not_refresh_previous_trigger():
    market, _ = source("fvg_return")
    rows = market.candles["5m"]
    _set(rows, -3, "101.8", "101.81", "101.79")
    _set(rows, -2, "102.1", "102.11", "102.09")
    _set(rows, -1, "102.12", "102.13", "102.11")
    market, analysis = _snapshot(market.candles)
    result = detect(market, analysis, "fvg_return")
    assert result.fail_codes == ()
    assert result.trigger.trigger_time == OBSERVED - timedelta(minutes=5)


def test_swing_anchor_needs_two_completed_right_hand_bars():
    market, _ = source("breakout_continuation")
    rows = market.candles["15m"]
    _set(rows, -2, "100.5", "103", "100.3")
    market, analysis = _snapshot(market.candles)
    result = detect(market, analysis, "breakout_continuation")
    assert result.fail_codes == ()
    assert dict(result.setup_basis)["level"] == "101.0"
    assert (
        datetime.fromisoformat(dict(result.setup_basis)["pivot_known_at"])
        < result.setup_time
    )


@pytest.mark.parametrize("strategy", ["fvg_return", "order_block_return"])
@pytest.mark.parametrize("direction", ["long", "short"])
def test_fully_mitigated_zone_cannot_be_reintroduced_by_return(strategy, direction):
    market, _ = source(strategy, direction)
    rows = market.candles["15m"]
    row = rows[-1]
    rows[-1] = row.model_copy(
        update={"low": D(99)} if direction == "long" else {"high": D(101)}
    )
    market, analysis = _snapshot(market.candles)
    result = detect(market, analysis, strategy, direction)
    assert result.trigger is None
    assert result.fail_codes == ("setup_missing",)


@pytest.mark.parametrize("direction", ["long", "short"])
def test_price_contract_rounding_cannot_widen_invalidation_risk(direction):
    market, analysis = source("trend_pullback", direction)
    result = detect(market, analysis, "trend_pullback", direction)
    basis = dict(result.setup_basis)
    original = D(basis["invalidation_unrounded"])
    encoded = result.invalidation_price
    assert encoded >= original if direction == "long" else encoded <= original
    assert encoded.as_tuple().exponent >= -20
    assert abs(encoded - original) < D("1e-20")


@pytest.mark.parametrize("ttl", [True, 0, -1, 3601, 300.0, "300"])
def test_invalid_ttl_is_not_coerced_into_an_event_window(ttl):
    market, analysis = source("fvg_return")
    with pytest.raises(ValueError, match="TTL"):
        extract_trigger(
            market,
            analysis,
            report_id="synthetic_events",
            strategy="fvg_return",
            direction="long",
            observed_at=OBSERVED,
            trigger_ttl_seconds=ttl,
        )


@pytest.mark.parametrize("timeframe", ["15m", "1H", "4H"])
@pytest.mark.parametrize("direction", ["long", "short"])
def test_any_confirmed_post_setup_timeframe_can_invalidate_thesis(timeframe, direction):
    market, analysis = source("fvg_return", direction)
    before = detect(market, analysis, "fvg_return", direction)
    rows = market.candles[timeframe]
    next_open = candle_closed_at(rows[-1], timeframe)
    # 4H's first next interval spans setup; even its wick cannot establish
    # order. The following entire interval is known to occur after setup.
    while next_open < before.setup_time:
        rows.append(_row(next_open, D(101), low=D(99), high=D(101)))
        next_open = candle_closed_at(rows[-1], timeframe)
    rows.append(_row(next_open, D(101), low=D(99), high=D(101)))
    observed = candle_closed_at(rows[-1], timeframe)
    market, analysis = _snapshot(market.candles)
    market.received_at = observed
    analysis.generated_at = observed
    result = extract_trigger(
        market,
        analysis,
        report_id="synthetic_events",
        strategy="fvg_return",
        direction=direction,
        observed_at=observed,
        trigger_ttl_seconds=600,
    )
    assert result.fail_codes == ("trigger_invalidated",)
    assert result.trigger.trigger_time == before.trigger.trigger_time
    assert result.trigger.invalidation_reason == "trigger_invalidated"
    basis = dict(result.setup_basis)
    assert basis["invalidation_timeframe"] == timeframe
    assert datetime.fromisoformat(basis["invalidation_known_at"]) == observed
    # The 5m source alone has not observed the crossing.
    five_after = [
        row for row in market.candles["5m"] if row.timestamp >= result.setup_time
    ]
    assert all(
        row.low > result.invalidation_price
        if direction == "long"
        else row.high < result.invalidation_price
        for row in five_after
    )


@pytest.mark.parametrize("direction", ["long", "short"])
def test_large_bar_spanning_setup_cannot_claim_invalidation_order(direction):
    market, _ = source("fvg_return", direction)
    rows = market.candles["4H"]
    rows.append(_row(candle_closed_at(rows[-1], "4H"), D(101), low=D(99), high=D(101)))
    observed = candle_closed_at(rows[-1], "4H")
    market, analysis = _snapshot(market.candles)
    market.received_at = observed
    analysis.generated_at = observed
    result = extract_trigger(
        market,
        analysis,
        report_id="synthetic_events",
        strategy="fvg_return",
        direction=direction,
        observed_at=observed,
        trigger_ttl_seconds=600,
    )
    assert rows[-1].timestamp < result.setup_time < observed
    assert result.fail_codes == () and result.trigger.invalidation_reason is None
    assert "invalidation_known_at" not in dict(result.setup_basis)


def test_first_known_invalidation_uses_close_time_not_timeframe_iteration_order():
    market, _ = source("fvg_return")
    for timeframe in ("15m", "1H"):
        rows = market.candles[timeframe]
        rows.append(
            _row(candle_closed_at(rows[-1], timeframe), D(101), low=D(99), high=D(101))
        )
    observed = candle_closed_at(market.candles["1H"][-1], "1H")
    market, analysis = _snapshot(market.candles)
    market.received_at = observed
    analysis.generated_at = observed
    result = detect(market, analysis, "fvg_return", observed_at=observed)
    assert result.fail_codes == ("trigger_invalidated",)
    assert dict(result.setup_basis)["invalidation_timeframe"] == "15m"
    assert datetime.fromisoformat(
        dict(result.setup_basis)["invalidation_known_at"]
    ) == candle_closed_at(market.candles["15m"][-1], "15m")


class _FoldOffset(tzinfo):
    """Deterministic UTC-4/UTC-5 fold fixture; no installed timezone database."""

    def utcoffset(self, value):
        return timedelta(hours=-5 if value.fold else -4)

    def dst(self, value):
        return timedelta(0)

    def tzname(self, value):
        return "synthetic_fall_back"


def _shift_times(value, target):
    if isinstance(value, datetime):
        return value.astimezone(target)
    if isinstance(value, dict):
        return {key: _shift_times(item, target) for key, item in value.items()}
    if isinstance(value, list):
        return [_shift_times(item, target) for item in value]
    if isinstance(value, tuple):
        return tuple(_shift_times(item, target) for item in value)
    return value


@pytest.mark.parametrize("hours", [-5, 8])
def test_equivalent_non_utc_sources_have_identical_events_and_full_source_hash(hours):
    market, analysis = source("fvg_return")
    before = detect(market, analysis, "fvg_return")
    target = timezone(timedelta(hours=hours))
    shifted_market = MarketSnapshot.model_validate(
        _shift_times(market.model_dump(), target), strict=True
    )
    shifted_analysis = MultiTimeframeAnalysis.model_validate(
        _shift_times(analysis.model_dump(), target), strict=True
    )
    after = detect(
        shifted_market,
        shifted_analysis,
        "fvg_return",
        observed_at=OBSERVED.astimezone(target),
    )
    assert after == before
    assert after.observed_at.tzinfo is UTC


def test_fold_wall_clock_order_cannot_hide_an_absolute_source_gap():
    market, _ = source("fvg_return")
    zone = _FoldOffset()
    for rows in market.candles.values():
        for index, row in enumerate(rows):
            local = (row.timestamp - timedelta(hours=4)).replace(tzinfo=zone, fold=0)
            rows[index] = row.model_copy(update={"timestamp": local})
    rows = market.candles["5m"]
    rows[-1] = rows[-1].model_copy(
        update={"timestamp": rows[-1].timestamp.replace(fold=1)}
    )
    assert rows[-1].timestamp - rows[-2].timestamp == timedelta(minutes=5)
    assert rows[-1].timestamp.astimezone(UTC) - rows[-2].timestamp.astimezone(
        UTC
    ) == timedelta(minutes=65)
    # The final price may be syntactically valid and all local wall clocks align,
    # but absolute elapsed time is missing 12 bars. Do not sort or fill the gap.
    market, analysis = _snapshot(market.candles)
    observed = OBSERVED + timedelta(hours=1)
    market.received_at = observed
    analysis.generated_at = observed
    result = detect(market, analysis, "fvg_return", observed_at=observed)
    assert result.trigger is None
    assert result.source_sha256 is None
    assert result.fail_codes == ("candle_gap",)


def test_valid_fold_bars_use_absolute_open_close_intervals_and_same_hash():
    market, analysis = source("fvg_return")
    before = detect(market, analysis, "fvg_return")
    zone = _FoldOffset()
    rows = market.candles["5m"]
    for offset, hours, fold in ((-3, 4, 0), (-2, 5, 1), (-1, 5, 1)):
        row = rows[offset]
        local = (row.timestamp - timedelta(hours=hours)).replace(tzinfo=zone, fold=fold)
        rows[offset] = row.model_copy(update={"timestamp": local})
    assert rows[-2].timestamp < rows[-3].timestamp  # Local clock went backwards.
    assert rows[-2].timestamp.astimezone(UTC) > rows[-3].timestamp.astimezone(UTC)
    local_observation = (OBSERVED - timedelta(hours=5)).replace(tzinfo=zone, fold=1)
    result = detect(market, analysis, "fvg_return", observed_at=local_observation)
    assert result == before
    assert result.trigger.trigger_time == OBSERVED
    assert result.source_sha256 == before.source_sha256
