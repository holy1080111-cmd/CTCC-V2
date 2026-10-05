"""Raw full-public v2 replay to disposable domain data; no G1 or source permit."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal

from app.domain.market import MarketSnapshot
from app.domain.source_primitives import decode
from app.exchange.okx.parsers import parse_swap_ticker_v2
from app.exchange.okx.symbols import to_canonical_symbol
from app.trade_qualification import candle_collector as candles
from app.trade_qualification import public_market_collector_v2 as public
from app.trade_qualification import quote_collector as wire
from app.trade_qualification.data import WSReferenceObservation
from app.trade_qualification.executable_quote_v2 import (
    ExecutableQuoteV2,
    inspect_executable_quote_v2,
)


@dataclass(frozen=True, slots=True)
class PublicMarketContextV2:
    packet_sha256: str
    market: MarketSnapshot
    quote: ExecutableQuoteV2
    reference: WSReferenceObservation
    evaluated_at: datetime
    quote_inspection_json: bytes
    schema_version: str = field(default="ctcc.public_market_context.v2", init=False)
    original_source_verified: Literal[False] = field(default=False, init=False)
    qualification_performed: Literal[False] = field(default=False, init=False)
    execution_authority: Literal[False] = field(default=False, init=False)
    admission: Literal["DENY"] = field(default="DENY", init=False)


def public_market_context_v2(packet, *, expected_bundle_sha256, evaluated_at):
    """Recompute current component checks; no caller-produced PASS is accepted.

    The domain projection retains raw nextFundingTime as the following forecast.
    V2 funding economics must use quote.funding.forecast, whose immutable rate is
    paired with the actual upcoming fundingTime. No old executable quote is built.
    """
    if type(expected_bundle_sha256) is not str:
        raise public.PublicMarketV2Error("public_v2_context_pin_invalid")
    checked, (policy, quote, provenance, candle_packet, auxiliary, ws) = public._parts(
        packet
    )
    if checked.bundle_sha256 != expected_bundle_sha256:
        raise public.PublicMarketV2Error("public_v2_context_pin_mismatch")
    now = wire._utc(evaluated_at)
    inspection = inspect_executable_quote_v2(quote, current_time=now)
    if decode(inspection.receipt_json)["profile_satisfied"] is not True:
        raise public.PublicMarketV2Error("public_v2_context_quote_rejected")
    document = decode(checked.packet_json, public.MAX_PACKET_BYTES)
    if (
        public._time(document["completed_at"]) > now
        or any(
            now - row.source_time > timedelta(seconds=policy.market_aux.max_age_seconds)
            for row in auxiliary.provenance
        )
        or now - ws.ticker.source_time > timedelta(seconds=policy.ws.max_age_seconds)
    ):
        raise public.PublicMarketV2Error("public_v2_context_not_current")
    if any(
        frame.verified_through != candles._floor(now, frame.timeframe)
        for frame in candle_packet.frames
    ):
        raise public.PublicMarketV2Error("public_v2_context_candle_tail_missing")
    ticker_row = wire._row(
        wire._parse(provenance[-1].response_body)[0], quote.instrument_id
    )
    market = MarketSnapshot(
        symbol=to_canonical_symbol(quote.instrument_id),
        instrument_id=quote.instrument_id,
        ticker=parse_swap_ticker_v2(ticker_row),
        mark_price=quote.mark.price,
        funding_rate=quote.funding.forecast.rate,
        next_funding_time=quote.funding.following_settlement_forecast_at,
        open_interest_contracts=auxiliary.open_interest.contracts,
        open_interest_currency=auxiliary.open_interest.currency,
        order_book=auxiliary.book.order_book,
        candles={frame.timeframe: frame.candles for frame in candle_packet.frames},
        quality={},
        received_at=public._time(document["completed_at"]),
    )
    reference = WSReferenceObservation(
        report_id=quote.report_id,
        instrument_id=quote.instrument_id,
        bid=ws.ticker.bid,
        ask=ws.ticker.ask,
        source_time=ws.ticker.source_time,
        received_at=ws.ticker.received_at,
    )
    return PublicMarketContextV2(
        checked.bundle_sha256, market, quote, reference, now, inspection.receipt_json
    )
