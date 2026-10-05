"""Private DB0022 one-attempt Demo quarterly archive diagnostic tombstone."""

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class DemoAccountBillArchiveClaim(Base):
    __tablename__ = "demo_account_bill_archive_claims"
    __table_args__ = (
        ForeignKeyConstraint(
            ["environment", "account_id", "settlement_currency"],
            [
                "qualification_account_scopes.environment",
                "qualification_account_scopes.account_id",
                "qualification_account_scopes.settlement_currency",
            ],
            ondelete="RESTRICT",
            name="fk_bill_archive_claim_scope",
        ),
        CheckConstraint(
            "environment='demo' AND account_id ~ '^[0-9]{1,32}$' AND settlement_currency ~ '^[A-Z0-9]{1,16}$' AND expected_main_uid ~ '^[0-9]{1,32}$'",
            name="scope",
        ),
        CheckConstraint(
            "year BETWEEN 2021 AND 9998 AND quarter BETWEEN 1 AND 4 AND bill_types='all'",
            name="quarter_and_all_types",
        ),
        CheckConstraint(
            "registration_region IN ('global','us_au','eea','tr') AND session_binding_id ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}$'",
            name="registration_and_session",
        ),
        CheckConstraint(
            "registration_evidence_sha256 ~ '^[a-f0-9]{64}$' AND plan_sha256 ~ '^[a-f0-9]{64}$' AND apply_scope_sha256 ~ '^[a-f0-9]{64}$' AND claim_sha256 ~ '^[a-f0-9]{64}$'",
            name="digests",
        ),
        CheckConstraint(
            "octet_length(claim_json) BETWEEN 1 AND 32768", name="claim_bound"
        ),
    )

    # Uniqueness deliberately excludes settlement currency and credential
    # session: neither can mint a second apply for the same account-quarter.
    environment: Mapped[str] = mapped_column(String(8), primary_key=True)
    account_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    year: Mapped[int] = mapped_column(primary_key=True)
    quarter: Mapped[int] = mapped_column(primary_key=True)
    settlement_currency: Mapped[str] = mapped_column(String(16), nullable=False)
    bill_types: Mapped[str] = mapped_column(String(3), nullable=False)
    expected_main_uid: Mapped[str] = mapped_column(String(32), nullable=False)
    session_binding_id: Mapped[str] = mapped_column(String(96), nullable=False)
    registration_region: Mapped[str] = mapped_column(String(16), nullable=False)
    origin: Mapped[str] = mapped_column(String(128), nullable=False)
    registration_evidence_sha256: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    plan_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    apply_scope_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    claim_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    claim_json: Mapped[str] = mapped_column(Text, nullable=False)
    db_recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.clock_timestamp()
    )
