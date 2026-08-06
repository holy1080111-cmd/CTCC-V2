from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class PaperOrderRequest(BaseModel):
    symbol: str = Field(min_length=3, max_length=40)
    side: Literal["long", "short"]
    quantity: Decimal = Field(gt=0)
    reference_price: Decimal = Field(gt=0)
    stop_loss: Decimal = Field(gt=0)
    take_profit: Decimal = Field(gt=0)
    order_type: Literal["market", "limit"] = "market"
    limit_price: Decimal | None = Field(default=None, gt=0)
    risk_decision: Literal["approved", "rejected"] = "approved"
    strategy: str = Field(default="manual", min_length=2, max_length=100)
    score: int = Field(default=0, ge=0, le=100)
    reasons: list[str] = Field(default_factory=list)
    client_order_id: str | None = Field(default=None, min_length=4, max_length=80)

    @model_validator(mode="after")
    def validate_order(self) -> "PaperOrderRequest":
        if self.order_type == "limit" and self.limit_price is None:
            raise ValueError("limit_price is required for limit orders")
        if self.side == "long" and not self.stop_loss < self.reference_price < self.take_profit:
            raise ValueError("long requires stop_loss < reference_price < take_profit")
        if self.side == "short" and not self.take_profit < self.reference_price < self.stop_loss:
            raise ValueError("short requires take_profit < reference_price < stop_loss")
        return self


class PaperOrderView(BaseModel):
    id: UUID
    client_order_id: str
    symbol: str
    side: Literal["long", "short"]
    order_type: Literal["market", "limit"]
    status: Literal["pending", "filled", "cancelled", "rejected"]
    quantity: Decimal
    reference_price: Decimal
    limit_price: Decimal | None
    average_fill_price: Decimal | None
    stop_loss: Decimal
    take_profit: Decimal
    fee: Decimal
    strategy: str
    score: int
    reasons: list[str]
    created_at: datetime
    filled_at: datetime | None = None


class PaperPositionView(BaseModel):
    id: UUID
    order_id: UUID
    symbol: str
    side: Literal["long", "short"]
    status: Literal["open", "closed"]
    quantity: Decimal
    entry_price: Decimal
    mark_price: Decimal
    stop_loss: Decimal
    take_profit: Decimal
    unrealized_pnl: Decimal
    realized_pnl: Decimal
    fees: Decimal
    opened_at: datetime
    closed_at: datetime | None = None
    close_reason: str | None = None


class MarketTickRequest(BaseModel):
    symbol: str = Field(min_length=3, max_length=40)
    price: Decimal = Field(gt=0)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ManualCloseRequest(BaseModel):
    price: Decimal = Field(gt=0)
    reason: str = Field(default="manual", min_length=2, max_length=100)


class PaperAccountView(BaseModel):
    starting_balance: Decimal
    cash_balance: Decimal
    equity: Decimal
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    fees_paid: Decimal
    open_positions: int
    pending_orders: int
    closed_trades: int


class PaperTickResult(BaseModel):
    symbol: str
    price: Decimal
    filled_order_ids: list[UUID] = Field(default_factory=list)
    closed_position_ids: list[UUID] = Field(default_factory=list)
    account: PaperAccountView


class PaperStateView(BaseModel):
    account: PaperAccountView
    orders: list[PaperOrderView]
    positions: list[PaperPositionView]
