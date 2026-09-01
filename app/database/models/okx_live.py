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
    __table_args__ = (
        CheckConstraint("id = 1", name="singleton_id"),
        CheckConstraint(
            "(NOT safety_latched AND safety_latch_code IS NULL AND "
            "safety_latched_at IS NULL) OR "
            "(safety_latched AND safety_latch_code IS NOT NULL AND "
            "safety_latched_at IS NOT NULL)",
            name="safety_latch_pair",
        ),
        CheckConstraint(
            "safety_latch_version >= 0", name="safety_latch_version_nonnegative"
        ),
        CheckConstraint(
            "safety_latch_code IS NULL OR "
            "safety_latch_code ~ '^[a-z0-9_]{1,80}$'",
            name="safety_latch_code_safe",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    order_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    position_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    algo_order_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    safety_latched: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    safety_latch_code: Mapped[str | None] = mapped_column(String(80))
    safety_latch_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    safety_latched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    last_error: Mapped[str | None] = mapped_column(String(250))
    reconciled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class OkxLiveExecutionIntent(Base):
    """Durable fail-closed idempotency record for a production write attempt."""

    __tablename__ = "okx_live_execution_intents"
    __table_args__ = (
        CheckConstraint(
            "action IN ('place_order','cancel_order','close_position','set_leverage')",
            name="action_allowed",
        ),
        CheckConstraint(
            "status IN ('reserved','acknowledged','confirmed','ambiguous','rejected')",
            name="status_allowed",
        ),
        CheckConstraint(
            "(operator_reconciled_at IS NULL AND operator_resolution_code IS NULL) "
            "OR (operator_reconciled_at IS NOT NULL AND "
            "operator_resolution_code IS NOT NULL)",
            name="operator_resolution_pair",
        ),
        CheckConstraint(
            "operator_resolution_code IS NULL OR "
            "(status IN ('reserved','acknowledged','ambiguous') AND "
            "operator_resolution_code = "
            "'operator_confirmed_flat_exchange_state')",
            name="operator_resolution_allowed",
        ),
        CheckConstraint(
            "(protection_client_order_id IS NULL AND "
            "expected_protection_size IS NULL AND expected_stop_loss IS NULL "
            "AND expected_take_profit IS NULL AND "
            "expected_trigger_price_type IS NULL) OR "
            "(action = 'place_order' AND protection_client_order_id IS NOT NULL "
            "AND expected_protection_size IS NOT NULL "
            "AND expected_stop_loss IS NOT NULL "
            "AND expected_take_profit IS NOT NULL "
            "AND expected_trigger_price_type IS NOT NULL)",
            name="protection_expectation_complete",
        ),
    )

    idempotency_key: Mapped[str] = mapped_column(String(32), primary_key=True)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    instrument_id: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    client_order_id: Mapped[str | None] = mapped_column(String(32), index=True)
    exchange_order_id: Mapped[str | None] = mapped_column(String(100), index=True)
    protection_client_order_id: Mapped[str | None] = mapped_column(
        String(32), unique=True
    )
    expected_protection_size: Mapped[Decimal | None] = mapped_column(
        Numeric(28, 10)
    )
    expected_stop_loss: Mapped[Decimal | None] = mapped_column(Numeric(28, 10))
    expected_take_profit: Mapped[Decimal | None] = mapped_column(Numeric(28, 10))
    expected_trigger_price_type: Mapped[str | None] = mapped_column(String(16))
    detail_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    operator_reconciled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    operator_resolution_code: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
