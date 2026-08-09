from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, DateTime, Integer, Numeric, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class OkxLiveAccountConfigState(Base):
    __tablename__ = "okx_live_account_config_state"
    __table_args__ = (CheckConstraint("id = 1", name="singleton_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    uid_fingerprint: Mapped[str | None] = mapped_column(String(64))
    main_uid_fingerprint: Mapped[str | None] = mapped_column(String(64))
    is_sub_account: Mapped[bool | None] = mapped_column(Boolean)
    account_level: Mapped[str | None] = mapped_column(String(16))
    position_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    account_stp_mode: Mapped[str | None] = mapped_column(String(32))
    account_type: Mapped[str | None] = mapped_column(String(16))
    permissions: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    unknown_permissions: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    read_permission: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    trade_permission: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    withdraw_permission: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    ip_bound: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    persisted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class OkxLiveBalanceState(Base):
    __tablename__ = "okx_live_balance_state"
    __table_args__ = (CheckConstraint("id = 1", name="singleton_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    total_equity: Mapped[Decimal] = mapped_column(Numeric(28, 10), nullable=False)
    isolated_equity: Mapped[Decimal] = mapped_column(Numeric(28, 10), nullable=False)
    adjusted_equity: Mapped[Decimal] = mapped_column(Numeric(28, 10), nullable=False)
    available_equity: Mapped[Decimal] = mapped_column(Numeric(28, 10), nullable=False)
    details: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    raw: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    persisted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class OkxLiveOrderState(Base):
    __tablename__ = "okx_live_order_state"

    order_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    client_order_id: Mapped[str | None] = mapped_column(String(32), index=True)
    instrument_id: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    side: Mapped[str] = mapped_column(String(16), nullable=False)
    position_side: Mapped[str | None] = mapped_column(String(16))
    order_type: Mapped[str] = mapped_column(String(32), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    size: Mapped[Decimal] = mapped_column(Numeric(28, 10), nullable=False)
    accumulated_fill_size: Mapped[Decimal] = mapped_column(Numeric(28, 10), nullable=False)
    price: Mapped[Decimal | None] = mapped_column(Numeric(28, 10))
    average_fill_price: Mapped[Decimal | None] = mapped_column(Numeric(28, 10))
    reduce_only: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    attached_algo_orders: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    raw: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    exchange_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    exchange_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    persisted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class OkxLivePositionState(Base):
    __tablename__ = "okx_live_position_state"

    position_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    instrument_id: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    position_side: Mapped[str] = mapped_column(String(16), nullable=False)
    size: Mapped[Decimal] = mapped_column(Numeric(28, 10), nullable=False)
    available_size: Mapped[Decimal] = mapped_column(Numeric(28, 10), nullable=False)
    average_price: Mapped[Decimal | None] = mapped_column(Numeric(28, 10))
    mark_price: Mapped[Decimal | None] = mapped_column(Numeric(28, 10))
    unrealized_pnl: Mapped[Decimal] = mapped_column(Numeric(28, 10), nullable=False)
    leverage: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    margin_mode: Mapped[str | None] = mapped_column(String(16))
    liquidation_price: Mapped[Decimal | None] = mapped_column(Numeric(28, 10))
    raw: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    exchange_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    exchange_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    persisted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class OkxLiveAlgoOrderState(Base):
    __tablename__ = "okx_live_algo_order_state"

    algo_order_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    client_algo_order_id: Mapped[str | None] = mapped_column(String(32), index=True)
    instrument_id: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    order_type: Mapped[str] = mapped_column(String(32), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    side: Mapped[str | None] = mapped_column(String(16))
    position_side: Mapped[str | None] = mapped_column(String(16))
    size: Mapped[Decimal] = mapped_column(Numeric(28, 10), nullable=False)
    take_profit_trigger_price: Mapped[Decimal | None] = mapped_column(Numeric(28, 10))
    stop_loss_trigger_price: Mapped[Decimal | None] = mapped_column(Numeric(28, 10))
    raw: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    exchange_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    exchange_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    persisted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class OkxLiveSyncCheckpoint(Base):
    __tablename__ = "okx_live_sync_checkpoints"
    __table_args__ = (CheckConstraint("id = 1", name="singleton_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    order_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    position_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    algo_order_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    last_error: Mapped[str | None] = mapped_column(String(250))
    reconciled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
