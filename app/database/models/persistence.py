from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, Numeric, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class PaperAccountState(Base):
    __tablename__ = "paper_account_state"
    __table_args__ = (
        CheckConstraint("id = 1", name="singleton_id"),
        CheckConstraint("starting_balance > 0", name="starting_balance_positive"),
        CheckConstraint("revision >= 1", name="revision_positive"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    starting_balance: Mapped[Decimal] = mapped_column(Numeric(28, 10), nullable=False)
    cash_balance: Mapped[Decimal] = mapped_column(Numeric(28, 10), nullable=False)
    equity: Mapped[Decimal] = mapped_column(Numeric(28, 10), nullable=False)
    realized_pnl: Mapped[Decimal] = mapped_column(Numeric(28, 10), nullable=False)
    unrealized_pnl: Mapped[Decimal] = mapped_column(Numeric(28, 10), nullable=False)
    fees_paid: Mapped[Decimal] = mapped_column(Numeric(28, 10), nullable=False)
    open_positions: Mapped[int] = mapped_column(Integer, nullable=False)
    pending_orders: Mapped[int] = mapped_column(Integer, nullable=False)
    closed_trades: Mapped[int] = mapped_column(Integer, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    state_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    persisted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class PaperOrderState(Base):
    __tablename__ = "paper_order_state"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint("reference_price > 0", name="reference_price_positive"),
        CheckConstraint("stop_loss > 0", name="stop_loss_positive"),
        CheckConstraint("take_profit > 0", name="take_profit_positive"),
        CheckConstraint("fee >= 0", name="fee_nonnegative"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    client_order_id: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    symbol: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    side: Mapped[str] = mapped_column(String(16), nullable=False)
    order_type: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    quantity: Mapped[Decimal] = mapped_column(Numeric(28, 10), nullable=False)
    reference_price: Mapped[Decimal] = mapped_column(Numeric(28, 10), nullable=False)
    limit_price: Mapped[Decimal | None] = mapped_column(Numeric(28, 10))
    average_fill_price: Mapped[Decimal | None] = mapped_column(Numeric(28, 10))
    stop_loss: Mapped[Decimal] = mapped_column(Numeric(28, 10), nullable=False)
    take_profit: Mapped[Decimal] = mapped_column(Numeric(28, 10), nullable=False)
    fee: Mapped[Decimal] = mapped_column(Numeric(28, 10), nullable=False)
    strategy: Mapped[str] = mapped_column(String(100), nullable=False)
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    reasons: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    filled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    persisted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class PaperPositionState(Base):
    __tablename__ = "paper_position_state"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint("entry_price > 0", name="entry_price_positive"),
        CheckConstraint("mark_price > 0", name="mark_price_positive"),
        CheckConstraint("stop_loss > 0", name="stop_loss_positive"),
        CheckConstraint("take_profit > 0", name="take_profit_positive"),
        CheckConstraint("fees >= 0", name="fees_nonnegative"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    order_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("paper_order_state.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    symbol: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    side: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    quantity: Mapped[Decimal] = mapped_column(Numeric(28, 10), nullable=False)
    entry_price: Mapped[Decimal] = mapped_column(Numeric(28, 10), nullable=False)
    mark_price: Mapped[Decimal] = mapped_column(Numeric(28, 10), nullable=False)
    stop_loss: Mapped[Decimal] = mapped_column(Numeric(28, 10), nullable=False)
    take_profit: Mapped[Decimal] = mapped_column(Numeric(28, 10), nullable=False)
    unrealized_pnl: Mapped[Decimal] = mapped_column(Numeric(28, 10), nullable=False)
    realized_pnl: Mapped[Decimal] = mapped_column(Numeric(28, 10), nullable=False)
    fees: Mapped[Decimal] = mapped_column(Numeric(28, 10), nullable=False)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    close_reason: Mapped[str | None] = mapped_column(String(100))
    persisted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class OrchestratorRunState(Base):
    __tablename__ = "orchestrator_run_state"

    run_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    trigger: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    execute: Mapped[bool] = mapped_column(Boolean, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)


class OrchestratorFingerprintState(Base):
    __tablename__ = "orchestrator_fingerprint_state"

    fingerprint: Mapped[str] = mapped_column(String(64), primary_key=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class RecoveryCheckpoint(Base):
    __tablename__ = "recovery_checkpoints"
    __table_args__ = (
        CheckConstraint("id = 1", name="singleton_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    state_checksum: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    recovered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    persisted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
