"""Immutable collector-issued revision rows; instances confer no source authority."""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.schema import conv

from app.database.base import Base


class DemoCollectorIssuedAccountRevision(Base):
    __tablename__ = "demo_collector_issued_account_revisions"
    __table_args__ = (
        PrimaryKeyConstraint(
            "issuer_sha256", name="demo_collector_issued_account_revisions_pkey"
        ),
        ForeignKeyConstraint(
            ["environment", "account_id", "settlement_currency"],
            [
                "qualification_account_scopes.environment",
                "qualification_account_scopes.account_id",
                "qualification_account_scopes.settlement_currency",
            ],
            ondelete="RESTRICT",
            onupdate="NO ACTION",
            deferrable=False,
            name="fk_collector_issued_scope",
        ),
        ForeignKeyConstraint(
            ["environment", "account_id", "control_revision"],
            [
                "demo_control_journal.environment",
                "demo_control_journal.account_id",
                "demo_control_journal.control_revision",
            ],
            ondelete="RESTRICT",
            onupdate="NO ACTION",
            deferrable=False,
            name="fk_collector_issued_control",
        ),
        ForeignKeyConstraint(
            ["source_lineage_sha256"],
            ["demo_collector_current_revision_lineages.lineage_sha256"],
            ondelete="RESTRICT",
            onupdate="NO ACTION",
            deferrable=False,
            name="fk_collector_issued_source_lineage",
        ),
        ForeignKeyConstraint(
            ["hwm_custody_id"],
            ["public_runtime_raw_bundles_v1.bundle_id"],
            ondelete="RESTRICT",
            onupdate="NO ACTION",
            deferrable=False,
            name="fk_collector_issued_hwm_custody",
        ),
        UniqueConstraint(
            "environment",
            "account_id",
            "settlement_currency",
            "account_revision",
            name="uq_collector_issued_scope_revision",
        ),
        UniqueConstraint(
            "source_lineage_sha256", name="uq_collector_issued_once_per_current"
        ),
        CheckConstraint(
            "environment='demo' AND account_id ~ '^[0-9]{1,32}$' AND settlement_currency='USDT'",
            name=conv("collector_issued_scope"),
        ),
        CheckConstraint(
            "previous_account_revision>=0 AND previous_ledger_revision>=previous_account_revision AND account_revision=previous_account_revision+1 AND ledger_revision=previous_ledger_revision+1",
            name=conv("collector_issued_revision_step"),
        ),
        CheckConstraint(
            "issuer_sha256 ~ '^[a-f0-9]{64}$' AND claims_sha256 ~ '^[a-f0-9]{64}$' AND source_lineage_sha256 ~ '^[a-f0-9]{64}$' AND hwm_bundle_sha256 ~ '^[a-f0-9]{64}$' AND hwm_custody_id ~ '^[a-f0-9]{32}$' AND control_event_sha256 ~ '^[a-f0-9]{64}$' AND control_state_sha256 ~ '^[a-f0-9]{64}$' AND (previous_claims_sha256 IS NULL OR previous_claims_sha256 ~ '^[a-f0-9]{64}$')",
            name=conv("collector_issued_digests"),
        ),
        CheckConstraint(
            "control_revision>0 AND octet_length(claims_json) BETWEEN 1 AND 8388608 AND octet_length(proof_json) BETWEEN 1 AND 1048576",
            name=conv("collector_issued_bounds"),
        ),
    )

    issuer_sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    environment: Mapped[str] = mapped_column(String(8), nullable=False)
    account_id: Mapped[str] = mapped_column(String(32), nullable=False)
    settlement_currency: Mapped[str] = mapped_column(String(16), nullable=False)
    previous_account_revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    previous_ledger_revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    previous_claims_sha256: Mapped[str | None] = mapped_column(String(64))
    account_revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    ledger_revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    claims_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    claims_json: Mapped[str] = mapped_column(Text, nullable=False)
    control_revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    control_event_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    control_state_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source_lineage_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    hwm_custody_id: Mapped[str] = mapped_column(String(32), nullable=False)
    hwm_bundle_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    proof_json: Mapped[str] = mapped_column(Text, nullable=False)
    original_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    db_recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.clock_timestamp()
    )
