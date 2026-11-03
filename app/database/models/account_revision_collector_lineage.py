"""Immutable current-owner lineage; it never authenticates complete claims."""

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


class DemoCollectorCurrentRevisionLineage(Base):
    __tablename__ = "demo_collector_current_revision_lineages"
    __table_args__ = (
        PrimaryKeyConstraint(
            "lineage_sha256", name="demo_collector_current_revision_lineages_pkey"
        ),
        ForeignKeyConstraint(
            ["environment", "account_id", "settlement_currency"],
            [
                "qualification_account_scopes.environment",
                "qualification_account_scopes.account_id",
                "qualification_account_scopes.settlement_currency",
            ],
            ondelete="RESTRICT",
            name="fk_collector_current_scope",
        ),
        ForeignKeyConstraint(
            ["environment", "account_id", "control_revision"],
            [
                "demo_control_journal.environment",
                "demo_control_journal.account_id",
                "demo_control_journal.control_revision",
            ],
            ondelete="RESTRICT",
            name="fk_collector_current_control",
        ),
        ForeignKeyConstraint(
            ["current_capture_id", "current_terminal_sequence"],
            [
                "demo_account_capture_events.capture_id",
                "demo_account_capture_events.sequence",
            ],
            ondelete="RESTRICT",
            name="fk_collector_current_source",
        ),
        ForeignKeyConstraint(
            ["custody_id"],
            ["public_runtime_raw_bundles_v1.bundle_id"],
            ondelete="RESTRICT",
            name="fk_collector_current_custody",
        ),
        UniqueConstraint(
            "environment",
            "account_id",
            "settlement_currency",
            "account_revision",
            "control_revision",
            "current_capture_id",
            name="uq_collector_current_lineage_binding",
        ),
        CheckConstraint(
            "environment='demo' AND account_id ~ '^[0-9]{1,32}$' AND settlement_currency ~ '^[A-Z0-9]{1,16}$'",
            name=conv("collector_current_scope"),
        ),
        CheckConstraint(
            "account_revision >= 0 AND ledger_revision >= account_revision AND control_revision > 0 AND current_terminal_sequence > 0",
            name=conv("collector_current_revisions"),
        ),
        CheckConstraint(
            "(account_revision=0 AND claims_json IS NULL AND claims_sha256 IS NULL) OR (account_revision>0 AND claims_json IS NOT NULL AND claims_sha256 ~ '^[a-f0-9]{64}$')",
            name=conv("collector_current_claims_pair"),
        ),
        CheckConstraint(
            "lineage_sha256 ~ '^[a-f0-9]{64}$' AND current_head_sha256 ~ '^[a-f0-9]{64}$' AND current_capture_id ~ '^[a-f0-9]{32}$' AND custody_id ~ '^[a-f0-9]{32}$' AND control_event_sha256 ~ '^[a-f0-9]{64}$' AND control_state_sha256 ~ '^[a-f0-9]{64}$'",
            name=conv("collector_current_digests"),
        ),
        CheckConstraint(
            "octet_length(lineage_json) BETWEEN 1 AND 262144 AND (claims_json IS NULL OR octet_length(claims_json) BETWEEN 1 AND 8388608)",
            name=conv("collector_current_bounds"),
        ),
    )
    lineage_sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    environment: Mapped[str] = mapped_column(String(8), nullable=False)
    account_id: Mapped[str] = mapped_column(String(32), nullable=False)
    settlement_currency: Mapped[str] = mapped_column(String(16), nullable=False)
    account_revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    ledger_revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    claims_sha256: Mapped[str | None] = mapped_column(String(64))
    claims_json: Mapped[str | None] = mapped_column(Text)
    control_revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    control_event_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    control_state_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    current_capture_id: Mapped[str] = mapped_column(String(32), nullable=False)
    current_terminal_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    current_head_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    custody_id: Mapped[str] = mapped_column(String(32), nullable=False)
    lineage_json: Mapped[str] = mapped_column(Text, nullable=False)
    db_recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.clock_timestamp()
    )
