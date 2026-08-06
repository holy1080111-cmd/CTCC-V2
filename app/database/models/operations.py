from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import DateTime, Integer, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class MarketSnapshot(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "market_snapshots"

    symbol: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    bid: Mapped[Decimal | None] = mapped_column(Numeric(28, 10))
    ask: Mapped[Decimal | None] = mapped_column(Numeric(28, 10))
    mark_price: Mapped[Decimal | None] = mapped_column(Numeric(28, 10))
    funding_rate: Mapped[Decimal | None] = mapped_column(Numeric(18, 12))
    open_interest: Mapped[Decimal | None] = mapped_column(Numeric(28, 10))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), index=True)


class AccountSnapshot(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "account_snapshots"

    broker: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    equity: Mapped[Decimal] = mapped_column(Numeric(28, 10), nullable=False)
    balance: Mapped[Decimal] = mapped_column(Numeric(28, 10), nullable=False)
    margin_used: Mapped[Decimal] = mapped_column(Numeric(28, 10), nullable=False)
    available_margin: Mapped[Decimal] = mapped_column(Numeric(28, 10), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), index=True)


class PortfolioSnapshot(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "portfolio_snapshots"

    gross_exposure: Mapped[Decimal] = mapped_column(Numeric(28, 10), nullable=False)
    net_exposure: Mapped[Decimal] = mapped_column(Numeric(28, 10), nullable=False)
    heat: Mapped[Decimal] = mapped_column(Numeric(12, 8), nullable=False)
    open_positions: Mapped[int] = mapped_column(Integer, nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), index=True)


class SystemEvent(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "system_events"

    event_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    aggregate_type: Mapped[str | None] = mapped_column(String(50), index=True)
    aggregate_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), index=True)
    severity: Mapped[str] = mapped_column(String(16), nullable=False, default="info", index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    correlation_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), index=True)
    causation_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), index=True)


class AuditLog(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "audit_logs"

    actor: Mapped[str] = mapped_column(String(100), nullable=False)
    action: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    resource_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    resource_id: Mapped[str | None] = mapped_column(String(100), index=True)
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    ip_address: Mapped[str | None] = mapped_column(INET)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), index=True)


class SafetyIncident(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "safety_incidents"

    incident_code: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="open", index=True)
    summary: Mapped[str] = mapped_column(String(250), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ConfigurationVersion(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "configuration_versions"

    version_name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    checksum: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    configuration: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_by: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
