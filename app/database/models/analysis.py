from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class AnalysisRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "analysis_runs"

    symbol: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="started")
    input_timeframes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    data_quality: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_detail: Mapped[str | None] = mapped_column(Text)


class TimeframeAnalysis(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "timeframe_analyses"

    analysis_run_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("analysis_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    timeframe: Mapped[str] = mapped_column(String(10), nullable=False)
    trend: Mapped[str] = mapped_column(String(32), nullable=False, default="neutral")
    volatility: Mapped[str] = mapped_column(String(32), nullable=False, default="unknown")
    indicators: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    structure: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    quality: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class StrategyEvaluation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "strategy_evaluations"
    __table_args__ = (
        CheckConstraint("score >= 0 AND score <= 100", name="score_range"),
        CheckConstraint("completion_ratio >= 0 AND completion_ratio <= 1", name="completion_ratio_range"),
    )

    analysis_run_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("analysis_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    strategy_name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    direction: Mapped[str] = mapped_column(String(16), nullable=False, default="neutral")
    eligible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_ratio: Mapped[Decimal] = mapped_column(Numeric(6, 5), nullable=False, default=Decimal("0"))
    passed_conditions: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    failed_conditions: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    vetoes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    score_breakdown: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    config_version: Mapped[str] = mapped_column(String(64), nullable=False, default="unversioned")
