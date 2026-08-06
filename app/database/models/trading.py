from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class TradeCandidate(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "trade_candidates"
    __table_args__ = (
        CheckConstraint("score >= 0 AND score <= 100", name="score_range"),
        CheckConstraint("entry_price > 0", name="entry_positive"),
        CheckConstraint("stop_loss > 0", name="stop_positive"),
        CheckConstraint("take_profit > 0", name="take_profit_positive"),
        CheckConstraint("risk_reward > 0", name="risk_reward_positive"),
    )

    strategy_evaluation_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("strategy_evaluations.id", ondelete="RESTRICT"), nullable=False, unique=True
    )
    client_candidate_id: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    symbol: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    side: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="created", index=True)
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    entry_price: Mapped[Decimal] = mapped_column(Numeric(28, 10), nullable=False)
    stop_loss: Mapped[Decimal] = mapped_column(Numeric(28, 10), nullable=False)
    take_profit: Mapped[Decimal] = mapped_column(Numeric(28, 10), nullable=False)
    risk_reward: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False)
    reasons: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RiskDecision(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "risk_decisions"
    __table_args__ = (
        CheckConstraint("requested_risk_pct >= 0", name="requested_risk_nonnegative"),
        CheckConstraint("approved_risk_pct >= 0", name="approved_risk_nonnegative"),
        CheckConstraint("approved_quantity >= 0", name="approved_quantity_nonnegative"),
    )

    candidate_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("trade_candidates.id", ondelete="CASCADE"), nullable=False, index=True
    )
    decision: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    reason_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    requested_risk_pct: Mapped[Decimal] = mapped_column(Numeric(8, 5), nullable=False)
    approved_risk_pct: Mapped[Decimal] = mapped_column(Numeric(8, 5), nullable=False, default=Decimal("0"))
    approved_quantity: Mapped[Decimal] = mapped_column(Numeric(28, 10), nullable=False, default=Decimal("0"))
    account_equity: Mapped[Decimal | None] = mapped_column(Numeric(28, 10))
    max_loss_amount: Mapped[Decimal | None] = mapped_column(Numeric(28, 10))
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class TradeLifecycle(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "trade_lifecycles"
    __table_args__ = (CheckConstraint("version >= 1", name="version_positive"),)

    candidate_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("trade_candidates.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="candidate", index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    failure_code: Mapped[str | None] = mapped_column(String(100))
    last_event_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class Order(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "orders"
    __table_args__ = (
        CheckConstraint("requested_quantity > 0", name="requested_quantity_positive"),
        CheckConstraint("filled_quantity >= 0", name="filled_quantity_nonnegative"),
    )

    lifecycle_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("trade_lifecycles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    candidate_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("trade_candidates.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    client_order_id: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    exchange_order_id: Mapped[str | None] = mapped_column(String(100), unique=True)
    broker: Mapped[str] = mapped_column(String(20), nullable=False)
    symbol: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    side: Mapped[str] = mapped_column(String(16), nullable=False)
    order_type: Mapped[str] = mapped_column(String(32), nullable=False)
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="created", index=True)
    requested_quantity: Mapped[Decimal] = mapped_column(Numeric(28, 10), nullable=False)
    filled_quantity: Mapped[Decimal] = mapped_column(Numeric(28, 10), nullable=False, default=Decimal("0"))
    price: Mapped[Decimal | None] = mapped_column(Numeric(28, 10))
    average_fill_price: Mapped[Decimal | None] = mapped_column(Numeric(28, 10))
    reduce_only: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Fill(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "fills"
    __table_args__ = (
        CheckConstraint("price > 0", name="price_positive"),
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint("fee >= 0", name="fee_nonnegative"),
    )

    order_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    exchange_fill_id: Mapped[str | None] = mapped_column(String(100), unique=True)
    price: Mapped[Decimal] = mapped_column(Numeric(28, 10), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(28, 10), nullable=False)
    fee: Mapped[Decimal] = mapped_column(Numeric(28, 10), nullable=False, default=Decimal("0"))
    fee_currency: Mapped[str | None] = mapped_column(String(20))
    filled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class Position(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "positions"
    __table_args__ = (
        CheckConstraint("quantity >= 0", name="quantity_nonnegative"),
        CheckConstraint("version >= 1", name="version_positive"),
    )

    lifecycle_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("trade_lifecycles.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    symbol: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    side: Mapped[str] = mapped_column(String(16), nullable=False)
    broker: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="opening", index=True)
    quantity: Mapped[Decimal] = mapped_column(Numeric(28, 10), nullable=False)
    entry_price: Mapped[Decimal | None] = mapped_column(Numeric(28, 10))
    mark_price: Mapped[Decimal | None] = mapped_column(Numeric(28, 10))
    stop_loss: Mapped[Decimal | None] = mapped_column(Numeric(28, 10))
    take_profit: Mapped[Decimal | None] = mapped_column(Numeric(28, 10))
    realized_pnl: Mapped[Decimal] = mapped_column(Numeric(28, 10), nullable=False, default=Decimal("0"))
    unrealized_pnl: Mapped[Decimal] = mapped_column(Numeric(28, 10), nullable=False, default=Decimal("0"))
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class ProtectiveOrder(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "protective_orders"
    __table_args__ = (CheckConstraint("quantity > 0", name="quantity_positive"),)

    position_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("positions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    order_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("orders.id", ondelete="RESTRICT"), nullable=False, unique=True
    )
    protection_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    trigger_price: Mapped[Decimal] = mapped_column(Numeric(28, 10), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(28, 10), nullable=False)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Trade(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "trades"

    lifecycle_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("trade_lifecycles.id", ondelete="RESTRICT"), nullable=False, unique=True
    )
    candidate_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("trade_candidates.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    position_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("positions.id", ondelete="RESTRICT"), nullable=False, unique=True
    )
    symbol: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    side: Mapped[str] = mapped_column(String(16), nullable=False)
    broker: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active", index=True)
    entry_price: Mapped[Decimal | None] = mapped_column(Numeric(28, 10))
    exit_price: Mapped[Decimal | None] = mapped_column(Numeric(28, 10))
    quantity: Mapped[Decimal] = mapped_column(Numeric(28, 10), nullable=False)
    gross_pnl: Mapped[Decimal] = mapped_column(Numeric(28, 10), nullable=False, default=Decimal("0"))
    net_pnl: Mapped[Decimal] = mapped_column(Numeric(28, 10), nullable=False, default=Decimal("0"))
    fees: Mapped[Decimal] = mapped_column(Numeric(28, 10), nullable=False, default=Decimal("0"))
    funding: Mapped[Decimal] = mapped_column(Numeric(28, 10), nullable=False, default=Decimal("0"))
    r_multiple: Mapped[Decimal | None] = mapped_column(Numeric(16, 8))
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    close_reason: Mapped[str | None] = mapped_column(String(100))
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
