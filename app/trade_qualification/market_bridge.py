"""Pure public-capture → versioned market → G1 bridge; no trading authority.

The external pin binds the selected packet, not the authenticity of its source.
Callers must retain the complete packet alongside the resulting G1 evidence.
Missing 24-hour volume/price fields are rejected, never filled with zero or WS.
"""

import re
from datetime import datetime

from app.domain.market import MarketSnapshot
from app.exchange.okx.parsers import parse_swap_ticker_v2
from app.exchange.okx.symbols import to_canonical_symbol
from app.trade_qualification.data import (
    DataQualificationPolicy,
    DataQualificationResult,
    WSReferenceObservation,
    evaluate_data,
)
from app.trade_qualification.public_market_collector import (
    CollectedPublicMarket,
    validate_collected_public_market,
)
from app.trade_qualification.quote_collector import _decimal, _parse, _row, _timestamp


def _checked(packet, pin):
    if type(pin) is not str or re.fullmatch(r"[0-9a-f]{64}", pin) is None:
        raise ValueError("public_market_pin_invalid")
    # Exact types, hidden extras and callbacks are rejected before serialization.
    checked = validate_collected_public_market(packet)
    if checked.bundle_sha256 != pin:
        raise ValueError("public_market_pin_mismatch")
    return checked


def _snapshot(packet):
    # The quote currency in an arbitrary SWAP ID does not prove its settlement
    # currency (e.g. inverse contracts). Only the reviewed USDT universe maps.
    symbol = to_canonical_symbol(packet.instrument_id)
    rows = {
        observation.role: _row(
            _parse(observation.response_body)[0], packet.instrument_id
        )
        for observation in packet.quote.provenance
    }
    return MarketSnapshot(
        symbol=symbol,
        instrument_id=packet.instrument_id,
        ticker=parse_swap_ticker_v2(rows["ticker"]),
        mark_price=_decimal(rows["mark"], "markPx", positive=True),
        funding_rate=_decimal(rows["funding"], "fundingRate", positive=False),
        next_funding_time=_timestamp(rows["funding"].get("nextFundingTime")),
        open_interest_contracts=packet.market_aux.open_interest.contracts,
        open_interest_currency=packet.market_aux.open_interest.currency,
        order_book=packet.market_aux.book.order_book,
        candles={frame.timeframe: frame.candles for frame in packet.candles.frames},
        quality={},
        received_at=packet.completed_at,
    )


def public_market_snapshot(
    packet: CollectedPublicMarket, *, expected_bundle_sha256: str
) -> MarketSnapshot:
    """Return a new, mutable domain copy from a fully replayed immutable packet."""
    return _snapshot(_checked(packet, expected_bundle_sha256))


def qualify_public_market(
    packet: CollectedPublicMarket,
    *,
    expected_bundle_sha256: str,
    policy: DataQualificationPolicy,
    evaluated_at: datetime,
) -> DataQualificationResult:
    """Run the real G1 evaluator; no supplied analysis or precomputed PASS."""
    checked = _checked(packet, expected_bundle_sha256)
    ticker = checked.ws.ticker
    reference = WSReferenceObservation(
        report_id=checked.report_id,
        instrument_id=checked.instrument_id,
        bid=ticker.bid,
        ask=ticker.ask,
        source_time=ticker.source_time,
        received_at=ticker.received_at,
    )
    return evaluate_data(
        _snapshot(checked),
        report_id=checked.report_id,
        instrument_id=checked.instrument_id,
        quote=checked.quote,
        reference=reference,
        policy=policy,
        evaluated_at=evaluated_at,
    )
