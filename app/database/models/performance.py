from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, Date, DateTime, Integer, Numeric, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, UUIDPrimaryKeyMixin


class DemoPerformanceSnapshot(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "demo_performance_snapshots"

    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    total_equity: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    available_equity: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    performance_equity: Mapped[Decimal | None] = mapped_column(Numeric(38, 18))
    performance_available_equity: Mapped[Decimal | None] = mapped_column(
        Numeric(38, 18)
    )
    equity_basis: Mapped[str | None] = mapped_column(String(40))
    equity_currency: Mapped[str | None] = mapped_column(String(16))
    unrealized_pnl: Mapped[Decimal] = mapped_column(
        Numeric(38, 18), nullable=False, default=Decimal("0")
    )
    position_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pending_order_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    algo_order_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    persisted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class DemoStrategyControl(Base):
    __tablename__ = "demo_strategy_controls"

    strategy: Mapped[str] = mapped_column(String(80), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    reason: Mapped[str | None] = mapped_column(String(250))
    updated_by: Mapped[str] = mapped_column(String(80), nullable=False, default="system")
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class DemoDailyPerformanceReport(Base):
    __tablename__ = "demo_daily_performance_reports"

    report_date: Mapped[date] = mapped_column(Date, primary_key=True)
    performance_window_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    equity_basis: Mapped[str | None] = mapped_column(String(40))
    equity_currency: Mapped[str | None] = mapped_column(String(16))
    performance_snapshot_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    excluded_snapshot_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    opening_equity: Mapped[Decimal | None] = mapped_column(Numeric(38, 18))
    closing_equity: Mapped[Decimal | None] = mapped_column(Numeric(38, 18))
    net_equity_change: Mapped[Decimal | None] = mapped_column(Numeric(38, 18))
    realized_pnl: Mapped[Decimal] = mapped_column(
        Numeric(38, 18), nullable=False, default=Decimal("0")
    )
    fees: Mapped[Decimal] = mapped_column(
        Numeric(38, 18), nullable=False, default=Decimal("0")
    )
    rebates: Mapped[Decimal] = mapped_column(
        Numeric(38, 18), nullable=False, default=Decimal("0")
    )
    funding_fees: Mapped[Decimal] = mapped_column(
        Numeric(38, 18), nullable=False, default=Decimal("0")
    )
    net_after_costs: Mapped[Decimal] = mapped_column(
        Numeric(38, 18), nullable=False, default=Decimal("0")
    )
    order_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    filled_order_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    realized_trade_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    attributed_realized_trade_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    unattributed_realized_trade_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    wins: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    losses: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    breakeven: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    win_rate: Mapped[Decimal | None] = mapped_column(Numeric(18, 12))
    profit_factor: Mapped[Decimal | None] = mapped_column(Numeric(38, 18))
    average_adverse_slippage_bps: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    max_adverse_slippage_bps: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    max_drawdown_pct: Mapped[Decimal] = mapped_column(
        Numeric(18, 12), nullable=False, default=Decimal("0")
    )
    account_opening_equity: Mapped[Decimal | None] = mapped_column(Numeric(38, 18))
    account_closing_equity: Mapped[Decimal | None] = mapped_column(Numeric(38, 18))
    account_equity_change: Mapped[Decimal | None] = mapped_column(Numeric(38, 18))
    account_max_drawdown_pct: Mapped[Decimal] = mapped_column(
        Numeric(18, 12), nullable=False, default=Decimal("0")
    )
    strategy_stats: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    alerts: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
