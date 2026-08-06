from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field


class RealtimeBookLevel(BaseModel):
    price: Decimal
    size: Decimal
    order_count: int | None = None


class RealtimeSnapshot(BaseModel):
    symbol: str
    last: Decimal | None = None
    bid: Decimal | None = None
    ask: Decimal | None = None
    mark_price: Decimal | None = None
    funding_rate: Decimal | None = None
    next_funding_time: datetime | None = None
    open_interest: Decimal | None = None
    open_interest_currency: Decimal | None = None
    last_trade_side: Literal["buy", "sell"] | None = None
    last_trade_size: Decimal | None = None
    best_bids: list[RealtimeBookLevel] = Field(default_factory=list)
    best_asks: list[RealtimeBookLevel] = Field(default_factory=list)
    exchange_timestamp: datetime | None = None
    received_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    sequence: int = 0


class RealtimeStatus(BaseModel):
    enabled: bool
    running: bool
    connected: bool
    endpoint: str
    symbols: list[str]
    connection_count: int = 0
    reconnect_count: int = 0
    message_count: int = 0
    parse_error_count: int = 0
    last_connected_at: datetime | None = None
    last_message_at: datetime | None = None
    last_error: str | None = None
    paper_auto_ticks: bool
