from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, Date, DateTime, Integer, Numeric, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, UUIDPrimaryKeyMixin


class DemoAutomationState(Base):
    __tablename__ = "demo_automation_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    armed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    emergency_stop: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    locked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    lock_reasons: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    session_date: Mapped[date] = mapped_column(Date, nullable=False)
    equity_basis: Mapped[str | None] = mapped_column(String(40))
    baseline_equity: Mapped[Decimal | None] = mapped_column(Numeric(28, 10))
    peak_equity: Mapped[Decimal | None] = mapped_column(Numeric(28, 10))
    risk_peak_equity: Mapped[Decimal | None] = mapped_column(Numeric(28, 10))
    daily_pnl: Mapped[Decimal] = mapped_column(Numeric(28, 10), nullable=False, default=Decimal("0"))
    trades_today: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    consecutive_losses: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    active_instrument_id: Mapped[str | None] = mapped_column(String(40))
    active_client_order_id: Mapped[str | None] = mapped_column(String(32))
    active_start_equity: Mapped[Decimal | None] = mapped_column(Numeric(28, 10))
    active_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    active_trades: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    symbol_cooldowns: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    realized_pnl_events: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    last_trade_closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(250))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class DemoAutomationRun(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "demo_automation_runs"

    trigger: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    execute: Mapped[bool] = mapped_column(Boolean, nullable=False)
    result: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)


class DemoAutomationFingerprint(Base):
    __tablename__ = "demo_automation_fingerprints"

    fingerprint: Mapped[str] = mapped_column(String(64), primary_key=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
