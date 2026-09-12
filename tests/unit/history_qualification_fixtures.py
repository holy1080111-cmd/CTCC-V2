"""Synthetic OHLC, MockTransport quotes and fictional risk claims only."""

import asyncio
from datetime import timedelta
from decimal import Decimal

from app.trade_qualification.data import WSReferenceObservation
from app.trade_qualification.history_engine import HistoryPreEvidencePolicy
from app.trade_qualification.history_prefix import HistoryQualificationPrefixPolicy
from tests.unit.qualification_engine_fixtures import engine_inputs
from tests.unit.qualification_prefix_fixtures import DATA_POLICY, SyntheticPrefixSource
from tests.unit.test_qualification_quote_collector import Clock, _ms, capture
from tests.unit.test_qualification_regime_admission import OBSERVED, fixture

D = Decimal


async def capture_history_source(
    strategy="structure_reversal", direction="long", *, bracket=False
):
    market = fixture(strategy, direction)
    if strategy == "structure_reversal":
        # Shift the actual entire momentum tape inside the real 1H entry zone;
        # do not replace an event/zone/quote result or loosen the policy limits.
        shift = D("-.65") if direction == "long" else D(".65")
        market.candles["5m"] = [
            row.model_copy(
                update={
                    field: getattr(row, field) + shift
                    for field in ("open", "high", "low", "close")
                }
            )
            for row in market.candles["5m"]
        ]
        market.ticker = market.ticker.model_copy(
            update={
                field: getattr(market.ticker, field) + shift
                for field in ("last", "bid", "ask", "open_24h", "high_24h", "low_24h")
            }
        )
        market.order_book.bids = [
            row.model_copy(update={"price": row.price + shift})
            for row in market.order_book.bids
        ]
        market.order_book.asks = [
            row.model_copy(update={"price": row.price + shift})
            for row in market.order_book.asks
        ]
        market.mark_price += shift
    if bracket:
        rows = market.candles["4H"]
        for offset, field, price in (
            (-30, "high", D("110.17")),
            (-25, "low", D("97.2")),
        ):
            if direction == "short":
                field = "low" if field == "high" else "high"
                price = D(200) - price
            rows[offset] = rows[offset].model_copy(update={field: price})
    report = f"synthetic-history-{strategy}-{direction}"
    price, bid, ask = market.ticker.last, market.ticker.bid, market.ticker.ask

    def change(role, body):
        row = body["data"][0]
        row["ts"] = _ms(OBSERVED - timedelta(seconds=1))
        if role == "ticker":
            row.update(bidPx=str(bid), askPx=str(ask))
        elif role == "mark":
            row["markPx"] = str(price)
        else:
            row.update(
                fundingRate="0",
                fundingTime=_ms(OBSERVED + timedelta(hours=1)),
                nextFundingTime=_ms(OBSERVED + timedelta(hours=9)),
            )

    quote, _ = await capture(
        change=change,
        report=report,
        clock=Clock(tuple(OBSERVED + timedelta(milliseconds=i) for i in range(10))),
    )
    return SyntheticPrefixSource(
        report_id=report,
        direction=direction,
        market=market,
        quote=quote,
        reference=WSReferenceObservation(
            report_id=report,
            instrument_id=market.instrument_id,
            bid=bid,
            ask=ask,
            source_time=OBSERVED - timedelta(seconds=1),
            received_at=OBSERVED,
        ),
        policy=DATA_POLICY,
        evaluated_at=OBSERVED + timedelta(milliseconds=10),
        strategy=strategy,
    )


def history_source(strategy="structure_reversal", direction="long", *, bracket=False):
    return asyncio.run(capture_history_source(strategy, direction, bracket=bracket))


def history_inputs(source):
    args = engine_inputs(source)
    old = args["policy"]
    args["policy"] = HistoryPreEvidencePolicy(
        policy_id="synthetic-history-engine-policy",
        prefix=HistoryQualificationPrefixPolicy(
            **old.prefix.model_dump(round_trip=True)
        ),
        protection=old.protection,
        economics=old.economics,
        portfolio=old.portfolio,
    )
    return args
