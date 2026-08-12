from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


DEMO_CONFIRMATION_PHRASE = "OKX_DEMO_ONLY"


class OkxDemoStatus(BaseModel):
    enabled: bool
    trading_mode: str
    credentials_configured: bool
    writes_enabled: bool
    simulated_trading_header: Literal["1"] = "1"
    base_url: str
    allowed_symbols: list[str]
    max_order_size_contracts: Decimal
    max_open_positions: int
    max_leverage: int
    require_protection: bool
    local_mirror_available: bool
    mirrored_order_count: int = 0
    mirrored_position_count: int = 0
    mirrored_algo_order_count: int = 0
    last_reconciled_at: datetime | None = None
    last_exchange_ok_at: datetime | None = None
    last_error: str | None = None
    blockers: list[str] = Field(default_factory=list)


class OkxDemoAccountConfig(BaseModel):
    uid: str | None = None
    account_level: str | None = None
    position_mode: Literal["net_mode", "long_short_mode"] | str
    account_stp_mode: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class OkxDemoBalanceDetail(BaseModel):
    currency: str
    equity: Decimal
    available_equity: Decimal
    cash_balance: Decimal
    available_balance: Decimal
    frozen_balance: Decimal
    unrealized_pnl: Decimal


class OkxDemoBalanceSnapshot(BaseModel):
    total_equity: Decimal
    isolated_equity: Decimal
    adjusted_equity: Decimal
    available_equity: Decimal
    details: list[OkxDemoBalanceDetail] = Field(default_factory=list)
    captured_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    raw: dict[str, Any] = Field(default_factory=dict)


class OkxDemoPositionView(BaseModel):
    instrument_id: str
    position_side: str
    size: Decimal
    available_size: Decimal
    average_price: Decimal | None = None
    mark_price: Decimal | None = None
    unrealized_pnl: Decimal
    leverage: Decimal | None = None
    margin_mode: str | None = None
    liquidation_price: Decimal | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    raw: dict[str, Any] = Field(default_factory=dict)

    @property
    def position_key(self) -> str:
        return f"{self.instrument_id}:{self.position_side}"


class OkxDemoOrderView(BaseModel):
    order_id: str
    client_order_id: str | None = None
    instrument_id: str
    side: str
    position_side: str | None = None
    order_type: str
    state: str
    size: Decimal
    accumulated_fill_size: Decimal
    price: Decimal | None = None
    average_fill_price: Decimal | None = None
    reduce_only: bool = False
    created_at: datetime | None = None
    updated_at: datetime | None = None
    attached_algo_orders: list[dict[str, Any]] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)


class OkxDemoAlgoOrderView(BaseModel):
    algo_order_id: str
    client_algo_order_id: str | None = None
    instrument_id: str
    order_type: str
    state: str
    side: str | None = None
    position_side: str | None = None
    size: Decimal
    take_profit_trigger_price: Decimal | None = None
    stop_loss_trigger_price: Decimal | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class OkxDemoOrderAcknowledgement(BaseModel):
    order_id: str
    client_order_id: str | None = None
    exchange_code: str = "0"
    exchange_message: str = ""


class OkxDemoWriteResult(BaseModel):
    action: Literal["place_order", "cancel_order", "close_position", "set_leverage"]
    acknowledged: bool
    acknowledgement: OkxDemoOrderAcknowledgement | None = None
    order: OkxDemoOrderView | None = None
    exchange_data: list[dict[str, Any]] = Field(default_factory=list)
    reconciled: bool = False
    warnings: list[str] = Field(default_factory=list)
    completed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class OkxDemoReconcileResult(BaseModel):
    account_config: OkxDemoAccountConfig
    balance: OkxDemoBalanceSnapshot
    positions: list[OkxDemoPositionView]
    pending_orders: list[OkxDemoOrderView]
    recent_orders: list[OkxDemoOrderView]
    pending_algo_orders: list[OkxDemoAlgoOrderView]
    persisted: bool
    reconciled_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class OkxDemoOrderRequest(BaseModel):
    instrument_id: str = Field(pattern=r"^[A-Z0-9]+-[A-Z0-9]+-SWAP$", max_length=40)
    direction: Literal["long", "short"]
    size: Decimal = Field(gt=0)
    margin_mode: Literal["cross", "isolated"] = "cross"
    order_type: Literal["market", "limit"] = "market"
    price: Decimal | None = Field(default=None, gt=0)
    stop_loss: Decimal | None = Field(default=None, gt=0)
    take_profit: Decimal | None = Field(default=None, gt=0)
    trigger_price_type: Literal["last", "mark", "index"] = "mark"
    client_order_id: str | None = Field(
        default=None,
        min_length=4,
        max_length=32,
        pattern=r"^[A-Za-z0-9]+$",
    )
    confirmation: Literal[DEMO_CONFIRMATION_PHRASE]

    @model_validator(mode="after")
    def validate_order_shape(self) -> "OkxDemoOrderRequest":
        if self.order_type == "limit" and self.price is None:
            raise ValueError("price is required for limit orders")
        if self.order_type == "market" and self.price is not None:
            raise ValueError("price must be omitted for market orders")
        if (self.stop_loss is None) != (self.take_profit is None):
            raise ValueError("stop_loss and take_profit must be supplied together")
        return self


class OkxDemoCancelRequest(BaseModel):
    instrument_id: str = Field(pattern=r"^[A-Z0-9]+-[A-Z0-9]+-SWAP$", max_length=40)
    order_id: str | None = Field(default=None, min_length=1, max_length=100)
    client_order_id: str | None = Field(
        default=None,
        min_length=4,
        max_length=32,
        pattern=r"^[A-Za-z0-9]+$",
    )
    confirmation: Literal[DEMO_CONFIRMATION_PHRASE]

    @model_validator(mode="after")
    def validate_identifier(self) -> "OkxDemoCancelRequest":
        if bool(self.order_id) == bool(self.client_order_id):
            raise ValueError("provide exactly one of order_id or client_order_id")
        return self


class OkxDemoCloseRequest(BaseModel):
    instrument_id: str = Field(pattern=r"^[A-Z0-9]+-[A-Z0-9]+-SWAP$", max_length=40)
    direction: Literal["long", "short"] | None = None
    margin_mode: Literal["cross", "isolated"] = "cross"
    confirmation: Literal[DEMO_CONFIRMATION_PHRASE]


class OkxDemoLeverageRequest(BaseModel):
    instrument_id: str = Field(pattern=r"^[A-Z0-9]+-[A-Z0-9]+-SWAP$", max_length=40)
    leverage: int = Field(ge=1, le=125)
    margin_mode: Literal["cross", "isolated"] = "cross"
    direction: Literal["long", "short"] | None = None
    confirmation: Literal[DEMO_CONFIRMATION_PHRASE]


class OkxDemoMirrorStatus(BaseModel):
    available: bool
    order_count: int = 0
    position_count: int = 0
    algo_order_count: int = 0
    last_reconciled_at: datetime | None = None
    last_error: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
