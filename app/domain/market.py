from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field


class InstrumentInfo(BaseModel):
    symbol: str
    instrument_id: str
    instrument_type: str
    state: str
    tick_size: Decimal
    lot_size: Decimal
    minimum_size: Decimal
    contract_value: Decimal | None = None
    contract_currency: str | None = None
    settlement_currency: str | None = None


class Candle(BaseModel):
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume_contracts: Decimal
    volume_currency: Decimal
    volume_quote: Decimal
    confirmed: bool


class Ticker(BaseModel):
    instrument_id: str
    last: Decimal
    bid: Decimal
    ask: Decimal
    bid_size: Decimal
    ask_size: Decimal
    open_24h: Decimal
    high_24h: Decimal
    low_24h: Decimal
    volume_24h: Decimal
    volume_quote_24h: Decimal
    timestamp: datetime

    @property
    def spread(self) -> Decimal:
        return self.ask - self.bid

    @property
    def spread_pct(self) -> Decimal:
        midpoint = (self.ask + self.bid) / Decimal("2")
        return Decimal("0") if midpoint <= 0 else self.spread / midpoint * Decimal("100")


class OrderBookLevel(BaseModel):
    price: Decimal
    size: Decimal
    deprecated_liquidated_orders: int = 0
    order_count: int = 0


class OrderBook(BaseModel):
    instrument_id: str
    bids: list[OrderBookLevel]
    asks: list[OrderBookLevel]
    timestamp: datetime


class MarketDataIssue(BaseModel):
    code: str
    severity: Literal["warning", "critical"]
    detail: str


class DataQualityReport(BaseModel):
    ok: bool
    candle_count: int
    confirmed_count: int
    expected_interval_seconds: int
    issues: list[MarketDataIssue] = Field(default_factory=list)


class MarketSnapshot(BaseModel):
    symbol: str
    instrument_id: str
    ticker: Ticker
    mark_price: Decimal
    funding_rate: Decimal
    next_funding_time: datetime | None
    open_interest_contracts: Decimal
    open_interest_currency: Decimal
    order_book: OrderBook
    candles: dict[str, list[Candle]]
    quality: dict[str, DataQualityReport]
    received_at: datetime
