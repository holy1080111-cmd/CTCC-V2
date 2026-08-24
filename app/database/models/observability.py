from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, DateTime, Integer, Numeric, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, UUIDPrimaryKeyMixin


class DemoSoakSession(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "demo_soak_sessions"

    state: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    execute: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    symbols: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    max_runs: Mapped[int] = mapped_column(Integer, nullable=False)
    max_submissions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    planned_end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_runs: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    submitted_runs: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    dry_run_runs: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    blocked_runs: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_runs: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    consecutive_errors: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    equity_basis: Mapped[str | None] = mapped_column(String(40))
    equity_currency: Mapped[str | None] = mapped_column(String(16))
    starting_equity: Mapped[Decimal | None] = mapped_column(Numeric(38, 18))
    latest_equity: Mapped[Decimal | None] = mapped_column(Numeric(38, 18))
    session_pnl: Mapped[Decimal] = mapped_column(
        Numeric(38, 18), nullable=False, default=Decimal("0")
    )
    max_drawdown_pct_observed: Mapped[Decimal] = mapped_column(
        Numeric(18, 12), nullable=False, default=Decimal("0")
    )
    protection_checks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    protection_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    active_position_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    active_pending_order_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    active_algo_order_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    protection_verified: Mapped[bool | None] = mapped_column(Boolean)
    auto_disarmed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_outcome: Mapped[str | None] = mapped_column(String(40))
    stop_reason: Mapped[str | None] = mapped_column(String(120))
    safety_stop_reason: Mapped[str | None] = mapped_column(String(120))
    last_error: Mapped[str | None] = mapped_column(String(250))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class DemoObservabilityEvent(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "demo_observability_events"

    severity: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    message: Mapped[str] = mapped_column(String(250), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
