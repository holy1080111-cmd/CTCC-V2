from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from app.domain.market import Candle, InstrumentInfo, OrderBook, OrderBookLevel, Ticker


def utc_from_ms(value: str | int) -> datetime:
    return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc)


def decimal_value(value: Any, default: str = "0") -> Decimal:
    if value in (None, ""):
        return Decimal(default)
    return Decimal(str(value))


def parse_instrument(row: dict[str, Any]) -> InstrumentInfo:
    return InstrumentInfo(
        symbol=row["instId"],
        instrument_id=row["instId"],
        instrument_type=row["instType"],
        state=row["state"],
        tick_size=decimal_value(row["tickSz"]),
        lot_size=decimal_value(row["lotSz"]),
        minimum_size=decimal_value(row["minSz"]),
        contract_value=decimal_value(row.get("ctVal")) if row.get("ctVal") else None,
        contract_currency=row.get("ctValCcy") or None,
    )


def parse_candle(row: list[str]) -> Candle:
    if len(row) < 9:
        raise ValueError(f"invalid OKX candle length: {len(row)}")
    return Candle(
        timestamp=utc_from_ms(row[0]),
        open=decimal_value(row[1]),
        high=decimal_value(row[2]),
        low=decimal_value(row[3]),
        close=decimal_value(row[4]),
        volume_contracts=decimal_value(row[5]),
        volume_currency=decimal_value(row[6]),
        volume_quote=decimal_value(row[7]),
        confirmed=row[8] == "1",
    )


def parse_ticker(row: dict[str, Any]) -> Ticker:
    return Ticker(
        instrument_id=row["instId"],
        last=decimal_value(row["last"]),
        bid=decimal_value(row["bidPx"]),
        ask=decimal_value(row["askPx"]),
        bid_size=decimal_value(row["bidSz"]),
        ask_size=decimal_value(row["askSz"]),
        open_24h=decimal_value(row["open24h"]),
        high_24h=decimal_value(row["high24h"]),
        low_24h=decimal_value(row["low24h"]),
        volume_24h=decimal_value(row["vol24h"]),
        volume_quote_24h=decimal_value(row["volCcy24h"]),
        timestamp=utc_from_ms(row["ts"]),
    )


def _parse_level(row: list[str]) -> OrderBookLevel:
    return OrderBookLevel(
        price=decimal_value(row[0]),
        size=decimal_value(row[1]),
        deprecated_liquidated_orders=int(row[2]),
        order_count=int(row[3]),
    )


def parse_order_book(instrument_id: str, row: dict[str, Any]) -> OrderBook:
    return OrderBook(
        instrument_id=instrument_id,
        bids=[_parse_level(level) for level in row.get("bids", [])],
        asks=[_parse_level(level) for level in row.get("asks", [])],
        timestamp=utc_from_ms(row["ts"]),
    )
