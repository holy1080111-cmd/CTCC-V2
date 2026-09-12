"""Synthetic post-barrier source extensions, NOT actual G12 or account receipts.

Quote bytes traverse the real three-endpoint collector using MockTransport.
OHLC capture records and the WS frame are explicitly fictional adapter claims;
there is no production OHLC/WS/account collector or authenticated publication.
Original confirmed bars, event identity, prices, policies and original inputs
are retained. A later latest-event extraction is never installed as the thesis.
Confirmed bars do not prove the absence of an intrabar touch after their close.
"""

import asyncio
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Context, Decimal, localcontext
from types import MappingProxyType

from app.domain.market import Candle, OrderBookLevel
from app.market.quality.candles import BAR_SECONDS, candle_closed_at
from app.trade_qualification.data import WSReferenceObservation
from app.trade_qualification.engine import PortfolioInputs, evaluate_pre_evidence
from tests.unit.qualification_engine_fixtures import engine_inputs, engine_source
from tests.unit.qualification_prefix_fixtures import SyntheticPrefixSource
from tests.unit.test_qualification_portfolio import STAMP_FIELDS
from tests.unit.test_qualification_quote_collector import Clock, _ms, capture

D = Decimal
TIMEFRAMES = ("4H", "1H", "15m", "5m")


@dataclass(frozen=True)
class SyntheticCandleObservation:
    """Fictional raw-candle capture envelope, not an exchange receipt/adapter."""

    timeframe: str
    instrument_id: str
    request_started_at: datetime
    received_at: datetime
    completed_at: datetime
    response_body: bytes
    body_sha256: str


@dataclass(frozen=True)
class SyntheticPublicRequest:
    method: str
    url: str
    parameters: tuple[tuple[str, str], ...]
    headers: tuple[tuple[str, str], ...]
    body: bytes


@dataclass(frozen=True)
class SyntheticRecheckSource:
    original_source: SyntheticPrefixSource
    original_inputs: object
    original_run: object
    scenario: str
    barrier_completed_at: datetime
    latest_source: SyntheticPrefixSource
    current_risk_inputs: PortfolioInputs
    ohlc_captures: tuple[SyntheticCandleObservation, ...]
    ws_frame_bytes: bytes
    ws_frame_sha256: str
    requests: tuple[SyntheticPublicRequest, ...]

    @property
    def original_event_key(self):
        return self.original_run.prefix.timing.event_key


def _canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode()


def _native_close(at, seconds):
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    elapsed = at - epoch
    whole = elapsed.days * 86400 + elapsed.seconds
    return epoch + timedelta(seconds=whole // seconds * seconds)


def _append_bar(timeframe, opened_at, direction):
    # Both appended examples remain above the original bullish invalidation.
    # Mirroring around 200 produces the corresponding bearish geometry.
    low = D("100.70") if timeframe == "15m" else D("100.98")
    high, close = D("101.01"), D("101.00")
    opened = D("100.75") if timeframe == "15m" else D("100.99")
    if direction == "short":
        low, high, opened, close = (
            D(200) - high,
            D(200) - low,
            D(200) - opened,
            D(200) - close,
        )
    return Candle(
        timestamp=opened_at,
        open=opened,
        high=high,
        low=low,
        close=close,
        volume_contracts=D(100),
        volume_currency=D(100),
        volume_quote=D(10000),
        confirmed=True,
    )


def _new_market(original, quote_started_at, scenario):
    market = original.market.model_copy(deep=True)
    captures = []
    with localcontext(Context(prec=100)):
        for index, timeframe in enumerate(TIMEFRAMES):
            start = quote_started_at + timedelta(milliseconds=3 * index - 12)
            received, completed = (
                start + timedelta(milliseconds=1),
                start + timedelta(milliseconds=2),
            )
            rows = market.candles[timeframe]
            should_close = _native_close(received, BAR_SECONDS[timeframe])
            while candle_closed_at(rows[-1], timeframe) < should_close:
                rows.append(
                    _append_bar(
                        timeframe,
                        candle_closed_at(rows[-1], timeframe),
                        original.direction,
                    )
                )
            body = _canonical(
                {
                    "schema": "synthetic_ohlc_capture_v1",
                    "instrument_id": market.instrument_id,
                    "timeframe": timeframe,
                    "candles": [row.model_dump(mode="json") for row in rows],
                }
            )
            captures.append(
                SyntheticCandleObservation(
                    timeframe,
                    market.instrument_id,
                    start,
                    received,
                    completed,
                    body,
                    hashlib.sha256(body).hexdigest(),
                )
            )
        reference = (
            original.market.ticker.last
            if scenario == "same_interval"
            else (D("101.00") if original.direction == "long" else D("99.00"))
        )
        bid, ask = (
            (reference - original.tick_size, reference)
            if original.direction == "long"
            else (reference, reference + original.tick_size)
        )
        source_time = quote_started_at - timedelta(milliseconds=1)
        market.ticker = market.ticker.model_copy(
            update={"last": reference, "bid": bid, "ask": ask, "timestamp": source_time}
        )
        market.order_book = market.order_book.model_copy(
            update={
                "bids": [OrderBookLevel(price=bid, size=D(2))],
                "asks": [OrderBookLevel(price=ask, size=D(3))],
                "timestamp": source_time,
            }
        )
        market.mark_price, market.funding_rate = reference, D(0)
        market.next_funding_time = quote_started_at + timedelta(hours=8)
        market.received_at = quote_started_at
        market.quality = {}
    return market, tuple(captures)


def _current_risk(original_inputs, observed_at, received_at):
    original = original_inputs["risk_inputs"]

    def refreshed(stamp, role):
        raw = _canonical(
            {
                "kind": "fictional_demo_recheck_claim_not_receipt",
                "role": role,
                "account_id": stamp.account_id,
                "observed_at": observed_at.isoformat(),
                "received_at": received_at.isoformat(),
            }
        )
        return stamp.model_copy(
            update={
                "observed_at": observed_at,
                "received_at": received_at,
                "source_sha256": hashlib.sha256(raw).hexdigest(),
            }
        )

    account = original.account.model_copy(
        update={
            **{
                field: refreshed(getattr(original.account, field), field)
                for field in STAMP_FIELDS
            },
            # Preserve the original reporting/peak window and prior empty history;
            # the new complete fictional history extends its end, never resets it.
            "history_end": observed_at,
        }
    )
    guard = original.authority.model_copy(
        update={"stamp": refreshed(original.authority.stamp, "demo_guard")}
    )
    spec_raw = _canonical(
        {
            "kind": "fictional_contract_claim_not_receipt",
            "instrument_id": original.instrument.instrument_id,
            "observed_at": observed_at.isoformat(),
            "received_at": received_at.isoformat(),
        }
    )
    spec = original.instrument.model_copy(
        update={
            "observed_at": observed_at,
            "received_at": received_at,
            "source_sha256": hashlib.sha256(spec_raw).hexdigest(),
        }
    )
    return PortfolioInputs(
        requested_contracts=original.requested_contracts,
        requested_leverage=original.requested_leverage,
        instrument=spec,
        account=account,
        authority=guard,
    )


async def capture_recheck_source(
    original_source, *, scenario="same_interval", original_lifetime_minutes=10
):
    """Extend a supplied raw engine fixture; no R1/R2/R3 or G12 result is invented.

    The original lifetime is set BEFORE the first PreEvidenceRun is evaluated.
    Five minutes intentionally yields an expired next-boundary negative case;
    ten minutes still cannot extend the original trigger's ten-minute deadline.
    """
    if scenario not in {"same_interval", "next_boundary"} or type(scenario) is not str:
        raise ValueError("unknown synthetic recheck scenario")
    if type(original_lifetime_minutes) is not int or original_lifetime_minutes not in {
        5,
        10,
    }:
        raise ValueError("synthetic original lifetime must be 5 or 10 minutes")
    inputs = engine_inputs(original_source)
    inputs["intent"] = inputs["intent"].model_copy(
        update={
            "expires_at": inputs["intent"].created_at
            + timedelta(minutes=original_lifetime_minutes)
        }
    )
    original_run = evaluate_pre_evidence(original_source.market, **inputs)
    assert original_run.pre_evidence_complete, original_run.result.fail_codes
    barrier = original_source.evaluated_at + timedelta(milliseconds=3)
    quote_start = (
        original_source.evaluated_at + timedelta(seconds=1)
        if scenario == "same_interval"
        else (
            candle_closed_at(original_source.market.candles["5m"][-1], "5m")
            + timedelta(minutes=5, milliseconds=10)
        )
    )
    market, captures = _new_market(original_source, quote_start, scenario)
    assert all(item.request_started_at > barrier for item in captures)

    def payload(role, body):
        row = body["data"][0]
        index = {"ticker": 0, "mark": 1, "funding": 2}[role]
        row["ts"] = _ms(quote_start + timedelta(milliseconds=index * 3 - 1))
        if role == "ticker":
            row.update(
                bidPx=str(market.ticker.bid),
                askPx=str(market.ticker.ask),
                bidSz="2",
                askSz="3",
            )
        elif role == "mark":
            row["markPx"] = str(market.mark_price)
        else:
            row.update(
                fundingRate="0",
                fundingTime=_ms(quote_start + timedelta(hours=8)),
                nextFundingTime=_ms(quote_start + timedelta(hours=16)),
            )

    quote, requests = await capture(
        change=payload,
        report=original_source.report_id,
        instrument=market.instrument_id,
        clock=Clock(tuple(quote_start + timedelta(milliseconds=i) for i in range(10))),
        barrier=barrier,
    )
    frame = _canonical(
        {
            "arg": {"channel": "tickers", "instId": market.instrument_id},
            "data": [
                {
                    "instId": market.instrument_id,
                    "bidPx": str(market.ticker.bid),
                    "askPx": str(market.ticker.ask),
                    "ts": _ms(quote_start + timedelta(milliseconds=1)),
                }
            ],
        }
    )
    frame_row = json.loads(frame)["data"][0]
    ws_time = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(
        milliseconds=int(frame_row["ts"])
    )
    latest = SyntheticPrefixSource(
        report_id=original_source.report_id,
        direction=original_source.direction,
        market=market,
        quote=quote,
        reference=WSReferenceObservation(
            report_id=original_source.report_id,
            instrument_id=market.instrument_id,
            bid=D(frame_row["bidPx"]),
            ask=D(frame_row["askPx"]),
            source_time=ws_time,
            received_at=quote_start + timedelta(milliseconds=10),
        ),
        policy=original_source.policy,
        evaluated_at=quote_start + timedelta(milliseconds=12),
        strategy=original_source.strategy,
        tick_size=original_source.tick_size,
        maximum_drift_bps=original_source.maximum_drift_bps,
    )
    risk = _current_risk(
        inputs,
        quote_start + timedelta(milliseconds=10),
        quote_start + timedelta(milliseconds=11),
    )
    request_records = tuple(
        SyntheticPublicRequest(
            request.method,
            str(request.url),
            tuple(request.url.params.items()),
            tuple(request.headers.items()),
            request.content,
        )
        for request in requests
    )
    return SyntheticRecheckSource(
        original_source,
        MappingProxyType(inputs),
        original_run,
        scenario,
        barrier,
        latest,
        risk,
        captures,
        frame,
        hashlib.sha256(frame).hexdigest(),
        request_records,
    )


def recheck_source(
    direction="long", *, scenario="same_interval", original_lifetime_minutes=10
):
    """Synchronous test convenience; async callers can supply an original source."""
    original = engine_source(direction)
    return asyncio.run(
        capture_recheck_source(
            original,
            scenario=scenario,
            original_lifetime_minutes=original_lifetime_minutes,
        )
    )
