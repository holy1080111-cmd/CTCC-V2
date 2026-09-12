"""Synthetic raw-OHLC G1–G7 fixtures, not observed samples or trading evidence.

Every evaluator runs normally. There are no supplied analysis flags, fake PASS
records, lowered gate thresholds, or runtime strategy/quote adapters. Quotes use
the existing MockTransport raw-response fixture. The prices describe independent
timeframe examples, not a claimed reconstruction of one real exchange tape.
"""

import asyncio
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Context, Decimal, localcontext

from app.domain.analysis import MultiTimeframeAnalysis
from app.domain.market import Candle, MarketSnapshot, OrderBook, OrderBookLevel, Ticker
from app.market.quality.candles import BAR_SECONDS
from app.strategies.base import StrategyContext
from app.strategies.conditions import StrategyConditionSet, assess_conditions
from app.strategies.regime import RegimeRoute, RouteDecision, route_regime
from app.trade_qualification.data import (
    DataQualificationPolicy,
    DataQualificationResult,
    WSReferenceObservation,
    evaluate_data,
)
from app.trade_qualification.event_models import TriggerDetection
from app.trade_qualification.events import extract_trigger
from app.trade_qualification.location import (
    LocationResult,
    build_entry_zone,
    evaluate_location,
)
from app.trade_qualification.models import EntryZone
from app.trade_qualification.quote_collector import CollectedQuote
from app.trade_qualification.timing import (
    TIMING_POLICIES,
    TimingResult,
    evaluate_timing,
)
from tests.unit.test_qualification_quote_collector import Clock, _ms, capture

D = Decimal
STRATEGY = "fvg_return"
INSTRUMENT = "BTC-USDT-SWAP"
SYMBOL = "BTC/USDT:USDT"
CAPTURED_AT = datetime(2026, 9, 12, 1, 10, tzinfo=UTC)
EVALUATED_AT = CAPTURED_AT + timedelta(milliseconds=10)
TICK_SIZE = D("0.01")
MAX_ALLOWED_DRIFT_BPS = D(30)
MINIMUM_SCORE = 85
MINIMUM_RISK_REWARD = D(2)
DATA_POLICY = DataQualificationPolicy(
    policy_id="synthetic-prefix-g1-policy",
    analysis_version="synthetic-prefix-source-v1",
    minimum_confirmed_bars=200,
    maximum_snapshot_age_seconds=5,
    maximum_quote_age_seconds=10,
    maximum_reference_age_seconds=10,
    maximum_candle_age_intervals=2,
    maximum_reference_conflict_bps=D(10),
    maximum_mark_dislocation_bps=D(10),
    maximum_spread_bps=D(5),
    maximum_absolute_funding_bps=D(10),
)


@dataclass(frozen=True)
class SyntheticPrefixSource:
    """Raw inputs; nested legacy MarketSnapshot is intentionally not authority."""

    report_id: str
    direction: str
    market: MarketSnapshot
    quote: CollectedQuote
    reference: WSReferenceObservation
    policy: DataQualificationPolicy = DATA_POLICY
    evaluated_at: datetime = EVALUATED_AT
    strategy: str = STRATEGY
    tick_size: Decimal = TICK_SIZE
    maximum_drift_bps: Decimal = MAX_ALLOWED_DRIFT_BPS


@dataclass(frozen=True)
class SyntheticPrefixTrace:
    """Results of real evaluators, not a forged EntryQualificationResult."""

    source: SyntheticPrefixSource
    data: DataQualificationResult
    market: MarketSnapshot
    analysis: MultiTimeframeAnalysis
    route: RegimeRoute
    strategy: StrategyConditionSet
    detection: TriggerDetection
    timing: TimingResult
    zone: EntryZone
    location: LocationResult


def _candle(at, close, *, high=None, low=None):
    return Candle(
        timestamp=at,
        open=close,
        high=close + D("0.05") if high is None else high,
        low=close - D("0.05") if low is None else low,
        close=close,
        volume_contracts=D(100),
        volume_currency=D(100),
        volume_quote=D(10000),
        confirmed=True,
    )


def _set(rows, offset, close, high, low):
    rows[offset] = _candle(rows[offset].timestamp, D(close), high=D(high), low=D(low))


def _frames(direction):
    if type(direction) is not str or direction not in {"long", "short"}:
        raise ValueError("synthetic fixture direction must be long or short")
    frames = {}
    for timeframe, seconds in BAR_SECONDS.items():
        elapsed = CAPTURED_AT - datetime(1970, 1, 1, tzinfo=UTC)
        epoch_seconds = elapsed.days * 86400 + elapsed.seconds
        end = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(
            seconds=epoch_seconds // seconds * seconds
        )
        frames[timeframe] = [
            _candle(end - timedelta(seconds=(240 - index) * seconds), D(100))
            for index in range(240)
        ]
    # Smooth HTF trends with positive, normal ATR. This is neither a hand-set
    # regime nor high-volatility evidence; both derive from these actual bars.
    for timeframe in ("4H", "1H"):
        for index, row in enumerate(frames[timeframe]):
            close = D(93) + D(index) * D("0.03")
            frames[timeframe][index] = _candle(
                row.timestamp, close, high=close + D("0.16"), low=close - D("0.16")
            )
    # Actual 15m bullish FVG [100,101], followed by its first return, known at
    # 01:00. The mirrored short setup has the corresponding bearish geometry.
    rows = frames["15m"]
    _set(rows, -4, "99.9", "100", "99.8")
    _set(rows, -3, "100.5", "100.8", "100.2")
    _set(rows, -2, "101.2", "101.4", "101.0")
    _set(rows, -1, "100.75", "101.2", "100.5")
    # A real closed-bar false→true momentum change at 01:10. Its price sits
    # inside the source-derived tick zone, without editing a trigger or zone.
    rows = frames["5m"]
    for index, row in enumerate(rows):
        close = D("100.89") + D(index % 8 - 4) * D("0.05")
        rows[index] = _candle(
            row.timestamp, close, high=close + D("0.01"), low=close - D("0.01")
        )
    _set(rows, -2, "100.79", "100.80", "100.78")
    _set(rows, -1, "100.99", "101.00", "100.98")
    if direction == "short":
        frames = {
            timeframe: [
                _candle(
                    row.timestamp,
                    D(200) - row.close,
                    high=D(200) - row.low,
                    low=D(200) - row.high,
                )
                for row in rows
            ]
            for timeframe, rows in frames.items()
        }
    return frames


def prefix_market(direction="long"):
    """Return fresh raw OHLC only, with no analysis or caller quality assertions."""
    with localcontext(Context(prec=100)):
        frames = _frames(direction)
        price = frames["5m"][-1].close
        bid, ask = (
            (price - TICK_SIZE, price)
            if direction == "long"
            else (price, price + TICK_SIZE)
        )
        source_at = CAPTURED_AT - timedelta(seconds=1)
        return MarketSnapshot(
            symbol=SYMBOL,
            instrument_id=INSTRUMENT,
            ticker=Ticker(
                instrument_id=INSTRUMENT,
                last=price,
                bid=bid,
                ask=ask,
                bid_size=D(2),
                ask_size=D(3),
                open_24h=D(100),
                high_24h=D(102),
                low_24h=D(98),
                volume_24h=D(100),
                volume_quote_24h=D(10000),
                timestamp=source_at,
            ),
            order_book=OrderBook(
                instrument_id=INSTRUMENT,
                bids=[OrderBookLevel(price=bid, size=D(2))],
                asks=[OrderBookLevel(price=ask, size=D(3))],
                timestamp=source_at,
            ),
            mark_price=price,
            funding_rate=D(0),
            next_funding_time=CAPTURED_AT + timedelta(hours=8),
            open_interest_contracts=D(100),
            open_interest_currency=D(100),
            candles=frames,
            quality={},
            received_at=CAPTURED_AT,
        )


async def capture_prefix_source(direction="long", *, report_id=None):
    """Capture synthetic raw public payloads through MockTransport, never IO."""
    market = prefix_market(direction)
    report_id = report_id or f"synthetic-prefix-fvg-{direction}"
    bid, ask, price = market.ticker.bid, market.ticker.ask, market.ticker.last

    def change(role, body):
        row = body["data"][0]
        row["ts"] = _ms(market.ticker.timestamp)
        if role == "ticker":
            row.update(bidPx=str(bid), askPx=str(ask))
        elif role == "mark":
            row["markPx"] = str(price)
        else:
            row.update(
                fundingRate="0",
                fundingTime=_ms(CAPTURED_AT + timedelta(hours=1)),
                nextFundingTime=_ms(CAPTURED_AT + timedelta(hours=9)),
            )

    collected, _ = await capture(
        change=change,
        report=report_id,
        clock=Clock(tuple(CAPTURED_AT + timedelta(milliseconds=i) for i in range(10))),
    )
    return SyntheticPrefixSource(
        report_id=report_id,
        direction=direction,
        market=market,
        quote=collected,
        reference=WSReferenceObservation(
            report_id=report_id,
            instrument_id=INSTRUMENT,
            bid=bid,
            ask=ask,
            source_time=market.ticker.timestamp,
            received_at=CAPTURED_AT,
        ),
    )


def prefix_source(direction="long", *, report_id=None):
    """Synchronous convenience for pure tests; async tests can await capture_* ."""
    return asyncio.run(capture_prefix_source(direction, report_id=report_id))


def qualify_data(source):
    return evaluate_data(
        source.market,
        report_id=source.report_id,
        instrument_id=INSTRUMENT,
        quote=source.quote,
        reference=source.reference,
        policy=source.policy,
        evaluated_at=source.evaluated_at,
    )


def restore_source(data):
    """Only restore the G1 evaluator's own source; never construct analysis flags."""
    assert data.passed, data.gate
    raw = json.loads(data.source_json)
    return (
        MarketSnapshot.model_validate_json(json.dumps(raw["market"]), strict=True),
        MultiTimeframeAnalysis.model_validate_json(
            json.dumps(raw["analysis"]), strict=True
        ),
    )


def run_prefix_source(source):
    """Exercise actual G1, route, strategy, event, timing and location evaluators.

    This is a test trace, not the production coordinator or a seven-gate permit.
    Pure strategy predicates never build a legacy candidate or read its clock;
    timing uses only the explicit fixture clock and the real event's capped TTL.
    """
    data = qualify_data(source)
    market, analysis = restore_source(data)
    route = route_regime(analysis)
    assert route.decision == RouteDecision.ALLOW_SCORING, route
    assert source.strategy in route.allowed_strategies
    with localcontext(Context(prec=100)):
        strategy = assess_conditions(
            StrategyContext(analysis, market, MINIMUM_SCORE, MINIMUM_RISK_REWARD),
            source.strategy,
        )
    assert strategy.score >= MINIMUM_SCORE, strategy
    assert not strategy.required_failures and not strategy.veto_failures, strategy
    assert strategy.direction == source.direction
    detection = extract_trigger(
        market,
        analysis,
        report_id=source.report_id,
        strategy=source.strategy,
        direction=source.direction,
        observed_at=source.evaluated_at,
        trigger_ttl_seconds=TIMING_POLICIES[source.strategy].trigger_ttl_seconds,
    )
    assert not detection.fail_codes and detection.trigger is not None, detection
    reference = (
        source.quote.quote.ask if source.direction == "long" else source.quote.quote.bid
    )
    timing = evaluate_timing(
        detection,
        current_time=source.evaluated_at,
        candidate_created_at=source.evaluated_at,
        candidate_expires_at=detection.trigger.expires_at,
        reference_price=reference,
    )
    assert timing.timing_valid, timing
    zone, code = build_entry_zone(
        detection,
        tick_size=source.tick_size,
        max_allowed_drift_bps=source.maximum_drift_bps,
        expires_at=detection.trigger.expires_at,
    )
    assert code == "passed", code
    location = evaluate_location(
        report_id=source.report_id,
        instrument_id=INSTRUMENT,
        direction=source.direction,
        zone=zone,
        candidate_entry=detection.trigger.trigger_price,
        quote=source.quote.quote,
        current_time=source.evaluated_at,
        max_quote_age_seconds=source.policy.maximum_quote_age_seconds,
    )
    assert location.passed, location
    return SyntheticPrefixTrace(
        source,
        data,
        market,
        analysis,
        route,
        strategy,
        detection,
        timing,
        zone,
        location,
    )
