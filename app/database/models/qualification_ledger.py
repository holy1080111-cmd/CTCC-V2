"""Separate Demo qualification journal; no Live or order repository ownership."""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class QualificationAccountScope(Base):
    __tablename__ = "qualification_account_scopes"
    __table_args__ = (
        CheckConstraint("environment = 'demo'", name="demo_only"),
        CheckConstraint("account_id ~ '^[0-9]{1,32}$'", name="exact_uid"),
        CheckConstraint("settlement_currency ~ '^[A-Z0-9]{1,16}$'", name="currency"),
        CheckConstraint(
            "account_revision >= 0 AND ledger_revision >= account_revision",
            name="revisions",
        ),
        CheckConstraint(
            "(account_revision = 0 AND claims_json IS NULL AND claims_sha256 IS NULL) OR "
            "(account_revision > 0 AND claims_json IS NOT NULL AND claims_sha256 ~ '^[a-f0-9]{64}$')",
            name="claims_pair",
        ),
        CheckConstraint("octet_length(claims_json) <= 8388608", name="claims_bound"),
    )

    environment: Mapped[str] = mapped_column(String(8), primary_key=True)
    account_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    settlement_currency: Mapped[str] = mapped_column(String(16), primary_key=True)
    account_revision: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    ledger_revision: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    claims_json: Mapped[str | None] = mapped_column(Text)
    claims_sha256: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class QualificationReservation(Base):
    __tablename__ = "qualification_reservations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["environment", "account_id", "settlement_currency"],
            [
                "qualification_account_scopes.environment",
                "qualification_account_scopes.account_id",
                "qualification_account_scopes.settlement_currency",
            ],
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "environment",
            "account_id",
            "settlement_currency",
            "original_event_key",
            name="uq_qualification_reservations_scope_event",
        ),
        UniqueConstraint(
            "environment",
            "account_id",
            "original_event_key",
            name="uq_qualification_reservations_uid_event",
        ),
        CheckConstraint("environment = 'demo'", name="demo_only"),
        CheckConstraint("direction IN ('long','short')", name="direction"),
        CheckConstraint(
            "state IN ('reserved','consumed','uncertain','reconciled_flat')",
            name="state",
        ),
        CheckConstraint("state_revision >= 1", name="state_revision"),
        CheckConstraint(
            "risk_amount > 0 AND margin_amount > 0 AND notional_amount > 0",
            name="amounts",
        ),
        CheckConstraint(
            "deadline > created_at AND updated_at >= created_at", name="clocks"
        ),
        CheckConstraint("octet_length(request_json) <= 16777216", name="request_bound"),
        CheckConstraint("octet_length(coverage_json) <= 16384", name="coverage_bound"),
        CheckConstraint(
            "reservation_id ~ '^[a-f0-9]{64}$' AND original_event_key ~ '^[a-f0-9]{64}$' AND request_sha256 ~ '^[a-f0-9]{64}$'",
            name="digests",
        ),
    )

    reservation_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    environment: Mapped[str] = mapped_column(String(8), nullable=False)
    account_id: Mapped[str] = mapped_column(String(32), nullable=False)
    settlement_currency: Mapped[str] = mapped_column(String(16), nullable=False)
    original_event_key: Mapped[str] = mapped_column(String(64), nullable=False)
    report_id: Mapped[str] = mapped_column(String(96), nullable=False)
    instrument_id: Mapped[str] = mapped_column(String(64), nullable=False)
    direction: Mapped[str] = mapped_column(String(5), nullable=False)
    correlation_group: Mapped[str] = mapped_column(String(128), nullable=False)
    request_json: Mapped[str] = mapped_column(Text, nullable=False)
    request_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    coverage_json: Mapped[str] = mapped_column(Text, nullable=False)
    risk_amount: Mapped[Decimal] = mapped_column(Numeric(60, 20), nullable=False)
    margin_amount: Mapped[Decimal] = mapped_column(Numeric(60, 20), nullable=False)
    notional_amount: Mapped[Decimal] = mapped_column(Numeric(60, 20), nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False)
    state_revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class QualificationReservationTransition(Base):
    __tablename__ = "qualification_reservation_transitions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["reservation_id"],
            ["qualification_reservations.reservation_id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("reservation_id", "state_revision"),
        CheckConstraint("state_revision >= 1", name="state_revision"),
        CheckConstraint(
            "to_state IN ('reserved','consumed','uncertain','reconciled_flat')",
            name="to_state",
        ),
        CheckConstraint(
            "octet_length(evidence_json) <= 8388608", name="evidence_bound"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    reservation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    state_revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    from_state: Mapped[str | None] = mapped_column(String(24))
    to_state: Mapped[str] = mapped_column(String(24), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(80), nullable=False)
    evidence_json: Mapped[str | None] = mapped_column(Text)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
