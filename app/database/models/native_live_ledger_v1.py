"""Live-only ledger rows. Root migration/registry integration is explicit."""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import OID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.schema import conv

from app.database.base import Base
from app.domain import native_live_ledger_schema_v1 as spec


def _constraints(table, *, scope=False, active=False):
    items = [
        *(
            CheckConstraint(expr, name=conv(name))
            for name, expr in spec.TABLE_CHECKS[table]
        ),
        *(
            UniqueConstraint(*fields, name=conv(name))
            for name, fields in spec.TABLE_UNIQUES[table]
        ),
    ]
    if scope:
        items.append(
            ForeignKeyConstraint(
                ["environment", "account_uid"],
                [
                    "native_live_controls_v1.environment",
                    "native_live_controls_v1.account_uid",
                ],
                name=conv(f"fk_{table}_scope"),
                ondelete="RESTRICT",
            )
        )
    if active:
        items.append(
            Index(
                "native_live_active_holds_v1",
                "environment",
                "account_uid",
                postgresql_where=text("state<>'reconciled'"),
            )
        )
    return tuple(items)


class NativeLiveControlV1(Base):
    __tablename__ = "native_live_controls_v1"
    __table_args__ = _constraints(__tablename__)
    environment: Mapped[str] = mapped_column(String(4), primary_key=True)
    account_uid: Mapped[str] = mapped_column(String(40), primary_key=True)
    main_uid: Mapped[str] = mapped_column(String(40))
    credential_session_sha256: Mapped[str] = mapped_column(String(64))
    live_writer_oid: Mapped[int] = mapped_column(OID)
    revision: Mapped[int] = mapped_column(BigInteger)
    source_revision_sha256: Mapped[str] = mapped_column(String(64))
    policy_sha256: Mapped[str] = mapped_column(String(64))
    process_sha256: Mapped[str] = mapped_column(String(64))
    emergency_stop: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    armed: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    arm_id: Mapped[str | None] = mapped_column(String(64))
    arm_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_until: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    per_trade_risk_cap: Mapped[Decimal] = mapped_column(Numeric(60, 20))
    portfolio_risk_cap: Mapped[Decimal] = mapped_column(Numeric(60, 20))
    margin_cap: Mapped[Decimal] = mapped_column(Numeric(60, 20))
    micro_notional_cap: Mapped[Decimal] = mapped_column(Numeric(60, 20))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("clock_timestamp()")
    )


class NativeLiveReservationV1(Base):
    __tablename__ = "native_live_reservations_v1"
    __table_args__ = _constraints(__tablename__, scope=True, active=True)
    reservation_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    environment: Mapped[str] = mapped_column(String(4))
    account_uid: Mapped[str] = mapped_column(String(40))
    event_key: Mapped[str] = mapped_column(String(64))
    candidate_sha256: Mapped[str] = mapped_column(String(64))
    report_sha256: Mapped[str] = mapped_column(String(64))
    source_revision_sha256: Mapped[str] = mapped_column(String(64))
    control_revision: Mapped[int] = mapped_column(BigInteger)
    arm_id: Mapped[str] = mapped_column(String(64))
    risk_amount: Mapped[Decimal] = mapped_column(Numeric(60, 20))
    margin_amount: Mapped[Decimal] = mapped_column(Numeric(60, 20))
    notional_amount: Mapped[Decimal] = mapped_column(Numeric(60, 20))
    deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    state: Mapped[str] = mapped_column(String(16))
    resolution_sha256: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("clock_timestamp()")
    )


class NativeLiveIntentV1(Base):
    __tablename__ = "native_live_intents_v1"
    __table_args__ = _constraints(__tablename__, scope=True)
    reservation_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey(
            "native_live_reservations_v1.reservation_id",
            name=conv("fk_native_live_intents_v1_reservation"),
            ondelete="RESTRICT",
        ),
        primary_key=True,
    )
    environment: Mapped[str] = mapped_column(String(4))
    account_uid: Mapped[str] = mapped_column(String(40))
    client_order_id: Mapped[str] = mapped_column(String(32))
    protection_client_id: Mapped[str] = mapped_column(String(32))
    intent_json: Mapped[str] = mapped_column(Text)
    intent_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("clock_timestamp()")
    )


class NativeLiveTransitionV1(Base):
    __tablename__ = "native_live_transitions_v1"
    __table_args__ = _constraints(__tablename__)
    reservation_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey(
            "native_live_reservations_v1.reservation_id",
            name=conv("fk_native_live_transitions_v1_reservation"),
            ondelete="RESTRICT",
        ),
        primary_key=True,
    )
    sequence: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))
    previous_sha256: Mapped[str | None] = mapped_column(String(64))
    journal_json: Mapped[str] = mapped_column(Text)
    journal_sha256: Mapped[str] = mapped_column(String(64))
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("clock_timestamp()")
    )
