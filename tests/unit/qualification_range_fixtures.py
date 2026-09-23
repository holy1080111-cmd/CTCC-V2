"""Synthetic original range source; real evaluators, no exchange authority."""

import asyncio
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

from app.trade_qualification.data import WSReferenceObservation
from app.trade_qualification.history_engine import HistoryPreEvidencePolicyV4
from app.trade_qualification.history_prefix import HistoryQualificationPrefixPolicyV4
from tests.unit.history_qualification_fixtures import history_inputs, history_source
from tests.unit.test_qualification_quote_collector import Clock, _ms, capture


def range_source(direction="long"):
    source = history_source("range_reversal", direction, bracket=True)
    shift = Decimal(-2) if direction == "long" else Decimal(2)
    market = source.market.model_copy(deep=True)
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
    now = source.reference.received_at

    def change(role, body):
        row = body["data"][0]
        row["ts"] = _ms(now - timedelta(seconds=1))
        if role == "ticker":
            row.update(bidPx=str(market.ticker.bid), askPx=str(market.ticker.ask))
        elif role == "mark":
            row["markPx"] = str(market.mark_price)
        else:
            row.update(
                fundingRate="0",
                fundingTime=_ms(now + timedelta(hours=1)),
                nextFundingTime=_ms(now + timedelta(hours=9)),
            )

    quote, _ = asyncio.run(
        capture(
            change=change,
            report=source.report_id,
            clock=Clock(tuple(now + timedelta(milliseconds=i) for i in range(10))),
        )
    )
    return replace(
        source,
        market=market,
        quote=quote,
        reference=WSReferenceObservation(
            report_id=source.report_id,
            instrument_id=market.instrument_id,
            bid=market.ticker.bid,
            ask=market.ticker.ask,
            source_time=now - timedelta(seconds=1),
            received_at=now,
        ),
    )


def v4_inputs(source):
    args = history_inputs(source)
    old = args["policy"]
    prefix = old.prefix.model_dump(mode="python", round_trip=True)
    prefix.update(
        contract_version="ctcc-history-qualification-prefix-v4",
        range_anchor_policy="ctcc-original-range-anchor-v1",
    )
    args["policy"] = HistoryPreEvidencePolicyV4(
        contract_version="ctcc-history-pre-evidence-v4",
        policy_id="synthetic-original-range-v4",
        prefix=HistoryQualificationPrefixPolicyV4(**prefix),
        protection=old.protection,
        economics=old.economics,
        portfolio=old.portfolio,
    )
    return args
