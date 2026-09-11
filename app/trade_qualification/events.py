"""Closed-bar event extraction from copied OHLC sources; no runtime registration.

This conservative version waits for a *subsequent* 5m momentum transition.
It does not prove market authenticity, historical availability, or eligibility
under the other qualification gates. OHLC never establishes intrabar order.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, Context, Decimal, localcontext
from typing import Literal

from app.domain.analysis import MultiTimeframeAnalysis
from app.domain.market import MarketSnapshot
from app.indicators.core import atr, ema, macd, rsi
from app.market.quality.candles import BAR_SECONDS, candle_closed_at
from app.structure.engine import analyze_structure, find_swings
from app.trade_qualification.event_models import TriggerDetection
from app.trade_qualification.models import EntryTrigger, require_aware

D = Decimal
TIMEFRAMES = ("4H", "1H", "15m", "5m")
STRATEGIES = frozenset(
    {
        "trend_pullback",
        "breakout_continuation",
        "liquidity_sweep_reversal",
        "fvg_return",
        "order_block_return",
        "range_reversal",
        "structure_reversal",
        "volatility_expansion",
    }
)
MAX_BARS = 1024
SETUP_LOOKBACK = 50


class _InvalidSource(ValueError):
    pass


def _checked_tree(value):
    """Copy finite source values, normalizing every instant before arithmetic."""
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise _InvalidSource("nonfinite_source")
        if len(value.as_tuple().digits) > 256 or abs(value.as_tuple().exponent) > 256:
            raise _InvalidSource("source_decimal_out_of_bounds")
    if isinstance(value, float):
        raise _InvalidSource("nondecimal_source_number")
    if isinstance(value, datetime):
        return require_aware(value).astimezone(UTC)
    if isinstance(value, dict):
        return {key: _checked_tree(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return tuple(_checked_tree(item) for item in value)
    if isinstance(value, list):
        return [_checked_tree(item) for item in value]
    return value


def _copy_source(market, analysis, observed_at):
    if (
        type(market) is not MarketSnapshot
        or type(analysis) is not MultiTimeframeAnalysis
    ):
        raise _InvalidSource("invalid_source_type")
    raw_market = _checked_tree(market.model_dump(mode="python", serialize_as_any=True))
    raw_analysis = _checked_tree(
        analysis.model_dump(mode="python", serialize_as_any=True)
    )
    market = MarketSnapshot.model_validate(raw_market, strict=True)
    analysis = MultiTimeframeAnalysis.model_validate(raw_analysis, strict=True)
    if (market.symbol, market.instrument_id) != (
        analysis.symbol,
        analysis.instrument_id,
    ):
        raise _InvalidSource("source_identity_mismatch")
    if (
        market.ticker.instrument_id != market.instrument_id
        or market.order_book.instrument_id != market.instrument_id
    ):
        raise _InvalidSource("market_identity_mismatch")
    if market.received_at > observed_at or analysis.generated_at > observed_at:
        raise _InvalidSource("future_source_observation")
    if market.received_at > analysis.generated_at:
        raise _InvalidSource("analysis_precedes_source")
    if max(market.ticker.timestamp, market.order_book.timestamp) > market.received_at:
        raise _InvalidSource("quote_after_capture")
    if set(market.candles) != set(TIMEFRAMES) or set(
        analysis.timeframe_analyses
    ) != set(TIMEFRAMES):
        raise _InvalidSource("missing_or_extra_timeframe")
    confirmed = {}
    for timeframe in TIMEFRAMES:
        rows = market.candles[timeframe]
        if not 1 <= len(rows) <= MAX_BARS:
            raise _InvalidSource("candle_count_out_of_bounds")
        previous = None
        for row in rows:
            timestamp = row.timestamp
            if (
                timestamp.microsecond
                or int(timestamp.timestamp()) % BAR_SECONDS[timeframe]
            ):
                raise _InvalidSource("candle_grid_mismatch")
            if previous is not None:
                difference = (timestamp - previous).total_seconds()
                if difference == 0:
                    raise _InvalidSource("duplicate_candle")
                if difference < 0:
                    raise _InvalidSource("unordered_candles")
                if difference != BAR_SECONDS[timeframe]:
                    raise _InvalidSource("candle_gap")
            previous = timestamp
            if timestamp > market.received_at or (
                row.confirmed and candle_closed_at(row, timeframe) > market.received_at
            ):
                raise _InvalidSource("future_candle")
            if min(row.open, row.high, row.low, row.close) <= 0:
                raise _InvalidSource("nonpositive_ohlc")
            for price in (row.open, row.high, row.low, row.close):
                if price >= D("1e20") or price.as_tuple().exponent < -20:
                    raise _InvalidSource("ohlc_precision_out_of_bounds")
            if row.low > min(row.open, row.close) or row.high < max(
                row.open, row.close
            ):
                raise _InvalidSource("invalid_ohlc")
            if min(row.volume_contracts, row.volume_currency, row.volume_quote) < 0:
                raise _InvalidSource("negative_volume")
        closed = [row for row in rows if row.confirmed]
        if not closed or any(not row.confirmed for row in rows[: len(closed)]):
            raise _InvalidSource("missing_or_interleaved_confirmation")
        view = analysis.timeframe_analyses[timeframe]
        if (
            view.timeframe != timeframe
            or view.close != closed[-1].close
            or view.last_closed_at != candle_closed_at(closed[-1], timeframe)
            or view.candle_count != len(closed)
        ):
            raise _InvalidSource("analysis_candle_mismatch")
        quality = market.quality.get(timeframe)
        if (
            quality is None
            or not quality.ok
            or not view.data_quality_ok
            or view.data_quality_issues
            or any(issue.severity == "critical" for issue in quality.issues)
            or quality.candle_count != len(rows)
            or quality.confirmed_count != len(closed)
            or quality.expected_interval_seconds != BAR_SECONDS[timeframe]
        ):
            raise _InvalidSource("source_quality_rejected")
        confirmed[timeframe] = closed
    payload = json.dumps(
        {
            "market": market.model_dump(mode="json"),
            "analysis": analysis.model_dump(mode="json"),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()
    if len(payload) > 8 * 1024 * 1024:
        raise _InvalidSource("source_bytes_out_of_bounds")
    return market, analysis, confirmed, hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class _Setup:
    time: datetime
    kind: str
    invalidation: Decimal
    basis: tuple[tuple[str, str], ...]


def _setup(rows, index, timeframe, kind, low, high, invalidation, **extra):
    volatility = atr(rows[: index + 1], 14)
    if volatility is None or volatility <= 0 or invalidation <= 0:
        return None
    closed_at = candle_closed_at(rows[index], timeframe)
    values = {
        "setup_timeframe": timeframe,
        "zone_low": str(low),
        "zone_high": str(high),
        "atr": str(volatility),
        "source_closed_at": closed_at.isoformat(),
        "setup_close": str(rows[index].close),
        "ohlc_ordering": "close_confirmed_only",
        **{key: str(value) for key, value in extra.items()},
    }
    if invalidation.as_tuple().exponent < -20:
        # Contract encoding, not an instrument tick or trading buffer. Round
        # toward the setup close so representational rounding cannot widen risk.
        values["invalidation_unrounded"] = str(invalidation)
        values["invalidation_rounding"] = "decimal20_toward_setup_close"
        rounding = ROUND_CEILING if invalidation < rows[index].close else ROUND_FLOOR
        invalidation = invalidation.quantize(D("1e-20"), rounding=rounding)
    if not D(0) < invalidation < D("1e20"):
        return None
    return _Setup(closed_at, kind, invalidation, tuple(sorted(values.items())))


def _cross(rows, index, direction):
    """Cross one level already confirmed before this bar; never move its anchor."""
    swings = find_swings(rows[:index], window=2)
    wanted = "high" if direction == "long" else "low"
    matching = [swing for swing in swings if swing.kind == wanted]
    if not matching:
        return None
    swing = matching[-1]
    before, current = rows[index - 1].close, rows[index].close
    crossed = (
        before <= swing.price < current
        if direction == "long"
        else before >= swing.price > current
    )
    return swing if crossed else None


def _prior_trend(rows, index):
    closes = [row.close for row in rows[:index]]
    e20, e50, e200 = (ema(closes, period) for period in (20, 50, 200))
    if e200 is None:
        return None
    return analyze_structure(rows[:index], e20, e50, e200).trend


def _setups(rows, timeframe, strategy, direction):
    """Recognize completed native-timeframe setups; all anchors come from prefixes."""
    output = []
    long = direction == "long"
    first = max(20, len(rows) - SETUP_LOOKBACK)
    for index in range(first, len(rows)):
        previous, current = rows[index - 1], rows[index]
        value = None
        if strategy == "trend_pullback":
            level = ema([row.close for row in rows[:index]], 20)
            low, high = level * D("0.994"), level * D("1.006")
            approach = previous.close > high if long else previous.close < low
            if (
                approach
                and low <= current.close <= high
                and current.low <= high
                and current.high >= low
            ):
                value = _setup(
                    rows,
                    index,
                    timeframe,
                    "directional_ema_pullback",
                    low,
                    high,
                    min(current.low, low) if long else max(current.high, high),
                    level=level,
                    prior_side="above" if long else "below",
                )
        elif strategy in {
            "breakout_continuation",
            "structure_reversal",
            "volatility_expansion",
        }:
            swing = _cross(rows, index, direction)
            if swing is None:
                continue
            if strategy == "structure_reversal":
                prior = _prior_trend(rows, index)
                if prior not in (
                    {"bearish", "strong_bearish"}
                    if long
                    else {"bullish", "strong_bullish"}
                ):
                    continue
            if strategy == "volatility_expansion":
                prior_atr, current_atr = (
                    atr(rows[:index], 14),
                    atr(rows[: index + 1], 14),
                )
                if prior_atr is None or current_atr is None:
                    continue
                prior_pct = prior_atr / previous.close * 100
                current_pct = current_atr / current.close * 100
                if not prior_pct < D("0.20") or not D("1") <= current_pct < D("2.50"):
                    continue
            kind = {
                "breakout_continuation": "confirmed_swing_break",
                "structure_reversal": "opposed_trend_structure_break",
                "volatility_expansion": "observed_compression_break",
            }[strategy]
            value = _setup(
                rows,
                index,
                timeframe,
                kind,
                swing.price,
                swing.price,
                swing.price,
                level=swing.price,
                pivot_open_at=swing.timestamp.isoformat(),
                pivot_known_at=candle_closed_at(
                    rows[swing.index + 2], timeframe
                ).isoformat(),
                prior_close=previous.close,
            )
        elif strategy == "liquidity_sweep_reversal":
            wanted = "low" if long else "high"
            swings = [s for s in find_swings(rows[:index], 2) if s.kind == wanted]
            if not swings:
                continue
            level = swings[-1].price
            reclaimed = (
                current.low < level < current.close
                if long
                else current.high > level > current.close
            )
            crossed = _cross(rows, index, direction)
            prior = _prior_trend(rows, index)
            opposed = prior in (
                {"bearish", "strong_bearish"} if long else {"bullish", "strong_bullish"}
            )
            if reclaimed and crossed is not None and opposed:
                value = _setup(
                    rows,
                    index,
                    timeframe,
                    "sweep_reclaim_with_co_confirmed_structure",
                    level,
                    level,
                    current.low if long else current.high,
                    level=level,
                    structure_level=crossed.price,
                    intrabar_sequence="unknown",
                    pivot_known_at=candle_closed_at(
                        rows[swings[-1].index + 2], timeframe
                    ).isoformat(),
                )
        elif strategy == "range_reversal":
            wanted = "low" if long else "high"
            swings = [s for s in find_swings(rows[:index], 2) if s.kind == wanted]
            volatility = atr(rows[:index], 14)
            if not swings or volatility is None or volatility <= 0:
                continue
            level = swings[-1].price
            approached = (
                previous.close > level + volatility
                if long
                else previous.close < level - volatility
            )
            touched = (
                current.low <= level + volatility and current.close >= level
                if long
                else current.high >= level - volatility and current.close <= level
            )
            if approached and touched and abs(current.close - level) <= volatility:
                value = _setup(
                    rows,
                    index,
                    timeframe,
                    "confirmed_range_edge_reclaim",
                    level - volatility,
                    level + volatility,
                    level - volatility if long else level + volatility,
                    level=level,
                    prior_side="range_interior",
                    pivot_known_at=candle_closed_at(
                        rows[swings[-1].index + 2], timeframe
                    ).isoformat(),
                )
        if value is not None:
            output.append(value)
    if strategy in {"fvg_return", "order_block_return"}:
        for index in range(max(4, len(rows) - SETUP_LOOKBACK), len(rows)):
            current = rows[index]
            if strategy == "fvg_return":
                earlier = rows[index - 2]
                formed = (
                    current.low > earlier.high if long else current.high < earlier.low
                )
                low, high = (
                    (earlier.high, current.low) if long else (current.high, earlier.low)
                )
                source_open = earlier.timestamp
            else:
                source = rows[index - 1]
                prior = rows[index - 4 : index - 1]
                formed = (
                    (
                        source.close < source.open
                        and current.close > current.open
                        and current.close > max(row.high for row in prior)
                    )
                    if long
                    else (
                        source.close > source.open
                        and current.close < current.open
                        and current.close < min(row.low for row in prior)
                    )
                )
                low, high, source_open = source.low, source.high, source.timestamp
            if not formed or not low < high:
                continue
            for touch in range(index + 1, len(rows)):
                row = rows[touch]
                invalid = row.low <= low if long else row.high >= high
                if invalid:
                    break
                if row.low <= high and row.high >= low:
                    value = _setup(
                        rows,
                        touch,
                        timeframe,
                        "fvg_first_return"
                        if strategy == "fvg_return"
                        else "order_block_first_retest",
                        low,
                        high,
                        low if long else high,
                        source_open_at=source_open.isoformat(),
                        formation_closed_at=candle_closed_at(
                            current, timeframe
                        ).isoformat(),
                    )
                    if value is not None:
                        output.append(value)
                    break
    return sorted(output, key=lambda item: item.time)


def _momentum(rows, direction):
    states = []
    for end in range(1, len(rows) + 1):
        closes = [row.close for row in rows[:end]]
        histogram = macd(closes)[2]
        relative_strength = rsi(closes, 14)
        if histogram is None or relative_strength is None:
            states.append(None)
        else:
            states.append(
                (histogram > 0 and D("50") <= relative_strength < D("72"))
                if direction == "long"
                else (histogram < 0 and D("28") < relative_strength <= D("50"))
            )
    return states


def extract_trigger(
    market: MarketSnapshot,
    analysis: MultiTimeframeAnalysis,
    *,
    report_id: str,
    strategy: str,
    direction: Literal["long", "short"],
    observed_at: datetime,
    trigger_ttl_seconds: int,
) -> TriggerDetection:
    """Derive the latest setup-bound false→true event, never renew an old event."""
    observed_at = require_aware(observed_at).astimezone(UTC)
    if (
        type(market) is not MarketSnapshot
        or type(analysis) is not MultiTimeframeAnalysis
    ):
        raise ValueError("market and analysis must be exact source model types")
    if strategy not in STRATEGIES or direction not in {"long", "short"}:
        raise ValueError("unsupported strategy or direction")
    if type(trigger_ttl_seconds) is not int or not 1 <= trigger_ttl_seconds <= 3600:
        raise ValueError("trigger TTL must be an integer from 1 to 3600 seconds")
    envelope = {
        "report_id": report_id,
        "symbol": market.symbol,
        "instrument_id": market.instrument_id,
        "strategy": strategy,
        "direction": direction,
        "observed_at": observed_at,
        "source_timeframe": "5m",
    }
    try:
        market, analysis, frames, source_sha = _copy_source(
            market, analysis, observed_at
        )
    except (ValueError, TypeError, OverflowError) as exc:
        return TriggerDetection(
            **envelope,
            source_sha256=None,
            fail_codes=(
                str(exc) if isinstance(exc, _InvalidSource) else "invalid_source_input",
            ),
        )
    with localcontext(Context(prec=50)):
        native = "1H" if strategy == "structure_reversal" else "15m"
        setups = _setups(frames[native], native, strategy, direction)
        if not setups:
            return TriggerDetection(
                **envelope, source_sha256=source_sha, fail_codes=("setup_missing",)
            )
        rows = frames["5m"]
        if len(rows) < 35:
            setup = setups[-1]
            return TriggerDetection(
                **envelope,
                source_sha256=source_sha,
                fail_codes=("trigger_missing",),
                setup_time=setup.time,
                setup_type=setup.kind,
                invalidation_price=setup.invalidation,
                setup_basis=setup.basis
                + (("missing_reason", "insufficient_momentum_history"),),
            )
        states = _momentum(rows, direction)
        for index in range(len(rows) - 1, 0, -1):
            if states[index - 1] is not False or states[index] is not True:
                continue
            eligible_setups = [
                item
                for item in setups
                if rows[0].timestamp <= item.time <= rows[index].timestamp
            ]
            if not eligible_setups:
                continue
            setup = eligible_setups[-1]
            event_at = candle_closed_at(rows[index], "5m")
            # Independently collected timeframes can close at different times.
            # Any fully post-setup confirmed interval can already establish
            # invalidation, even while the 5m source has not caught up. A bar
            # spanning setup is excluded: its wick's intrabar order is unknown.
            invalidating = [
                (candle_closed_at(row, timeframe), timeframe)
                for timeframe, source_rows in frames.items()
                for row in source_rows
                if row.timestamp >= setup.time
                and (
                    row.low <= setup.invalidation
                    if direction == "long"
                    else row.high >= setup.invalidation
                )
            ]
            wrong_side = (
                rows[index].close <= setup.invalidation
                if direction == "long"
                else rows[index].close >= setup.invalidation
            )
            invalidation = "trigger_invalidated" if invalidating or wrong_side else None
            basis = setup.basis
            if invalidating:
                known_at, invalidating_timeframe = min(
                    invalidating, key=lambda item: (item[0], BAR_SECONDS[item[1]])
                )
                basis += (
                    ("invalidation_timeframe", invalidating_timeframe),
                    ("invalidation_known_at", known_at.isoformat()),
                )
            trigger = EntryTrigger(
                report_id=report_id,
                trigger_type="closed_5m_momentum_false_to_true",
                trigger_time=event_at,
                trigger_price=rows[index].close,
                expires_at=event_at + timedelta(seconds=trigger_ttl_seconds),
                invalidation_reason=invalidation,
            )
            return TriggerDetection(
                **envelope,
                source_sha256=source_sha,
                setup_time=setup.time,
                setup_type=setup.kind,
                setup_basis=basis,
                invalidation_price=setup.invalidation,
                trigger=trigger,
                fail_codes=(invalidation,) if invalidation else (),
            )
        setup = setups[-1]
        return TriggerDetection(
            **envelope,
            source_sha256=source_sha,
            setup_time=setup.time,
            setup_type=setup.kind,
            setup_basis=setup.basis
            + (("missing_reason", "no_new_post_setup_momentum_transition"),),
            invalidation_price=setup.invalidation,
            fail_codes=("trigger_missing",),
        )
