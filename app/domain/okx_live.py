from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field


class OkxLiveApiKeyCapability(BaseModel):
    permissions: list[str] = Field(default_factory=list)
    unknown_permissions: list[str] = Field(default_factory=list)
    read_permission: bool = False
    trade_permission: bool = False
    withdraw_permission: bool = False
    ip_bound: bool = False


class OkxLiveAccountConfig(BaseModel):
    uid: str | None = None
    main_uid: str | None = None
    is_sub_account: bool | None = None
    account_level: str | None = None
    position_mode: str = Field(min_length=1)
    account_stp_mode: str | None = None
    account_type: str | None = None
    capability: OkxLiveApiKeyCapability


class OkxLiveBalanceDetail(BaseModel):
    currency: str
    equity: Decimal
    cash_balance: Decimal
    available_balance: Decimal
    frozen_balance: Decimal
    unrealized_pnl: Decimal


class OkxLiveBalanceSnapshot(BaseModel):
    total_equity: Decimal
    isolated_equity: Decimal
    adjusted_equity: Decimal
    available_equity: Decimal
    details: list[OkxLiveBalanceDetail] = Field(default_factory=list)
    captured_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    raw: dict[str, Any] = Field(default_factory=dict)


class OkxLivePositionView(BaseModel):
    position_id: str = Field(min_length=1, max_length=100)
    instrument_id: str = Field(min_length=1, max_length=40)
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
        return self.position_id


class OkxLiveOrderView(BaseModel):
    order_id: str = Field(min_length=1, max_length=100)
    client_order_id: str | None = None
    instrument_id: str = Field(min_length=1, max_length=40)
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


class OkxLiveAlgoOrderView(BaseModel):
    algo_order_id: str = Field(min_length=1, max_length=100)
    client_algo_order_id: str | None = None
    instrument_id: str = Field(min_length=1, max_length=40)
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


class OkxLiveReconcileResult(BaseModel):
    account_config: OkxLiveAccountConfig
    balance: OkxLiveBalanceSnapshot
    positions: list[OkxLivePositionView]
    pending_orders: list[OkxLiveOrderView]
    recent_orders: list[OkxLiveOrderView]
    pending_algo_orders: list[OkxLiveAlgoOrderView]
    persisted: bool
    reconciled_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class OkxLiveMirrorStatus(BaseModel):
    available: bool
    order_count: int = 0
    position_count: int = 0
    algo_order_count: int = 0
    last_reconciled_at: datetime | None = None
    last_error: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
