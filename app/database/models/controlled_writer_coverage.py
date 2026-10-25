"""Prospective controlled Demo coverage pointer and immutable journal."""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.schema import conv

from app.database.base import Base


class DemoControlledWriterCoverageScope(Base):
    __tablename__ = "demo_controlled_writer_coverage_scopes"
    __table_args__ = (
        PrimaryKeyConstraint(
            "environment",
            "account_id",
            name="pk_demo_controlled_writer_coverage_scopes",
        ),
        ForeignKeyConstraint(
            ["environment", "account_id"],
            ["demo_account_controls.environment", "demo_account_controls.account_id"],
            name="fk_cwc_scope_control",
            ondelete="RESTRICT",
            deferrable=False,
        ),
        CheckConstraint(
            "environment='demo' AND account_id ~ '^[0-9]{1,32}$' AND epoch_id ~ '^[a-f0-9]{32}$'",
            name=conv("ck_cwc_scope_identity"),
        ),
        CheckConstraint(
            "generation>0 AND journal_sequence>=generation",
            name=conv("ck_cwc_scope_revisions"),
        ),
        CheckConstraint(
            "state IN ('prepared','active','revoked')", name=conv("ck_cwc_scope_state")
        ),
        CheckConstraint(
            "state_sha256 ~ '^[a-f0-9]{64}$' AND journal_sha256 ~ '^[a-f0-9]{64}$' AND encode(sha256(convert_to(state_json,'UTF8')),'hex')=state_sha256",
            name=conv("ck_cwc_scope_hashes"),
        ),
        CheckConstraint(
            "octet_length(state_json) BETWEEN 1 AND 65536",
            name=conv("ck_cwc_scope_bounds"),
        ),
        CheckConstraint("created_at<=updated_at", name=conv("ck_cwc_scope_clocks")),
    )
    environment: Mapped[str] = mapped_column(String(8), nullable=False)
    account_id: Mapped[str] = mapped_column(String(32), nullable=False)
    generation: Mapped[int] = mapped_column(BigInteger, nullable=False)
    epoch_id: Mapped[str] = mapped_column(String(32), nullable=False)
    journal_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    state: Mapped[str] = mapped_column(String(8), nullable=False)
    state_json: Mapped[str] = mapped_column(Text, nullable=False)
    state_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    journal_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class DemoControlledWriterCoverageJournal(Base):
    __tablename__ = "demo_controlled_writer_coverage_journal"
    __table_args__ = (
        PrimaryKeyConstraint(
            "environment",
            "account_id",
            "journal_sequence",
            name="pk_demo_controlled_writer_coverage_journal",
        ),
        ForeignKeyConstraint(
            ["environment", "account_id"],
            [
                "demo_controlled_writer_coverage_scopes.environment",
                "demo_controlled_writer_coverage_scopes.account_id",
            ],
            name="fk_cwc_journal_scope",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        UniqueConstraint(
            "environment", "account_id", "command_id", name="uq_cwc_command"
        ),
        UniqueConstraint(
            "environment",
            "account_id",
            "generation",
            "action",
            name="uq_cwc_generation_action",
        ),
        UniqueConstraint(
            "environment",
            "account_id",
            "epoch_id",
            "action",
            name="uq_cwc_epoch_action",
        ),
        UniqueConstraint("event_sha256", name="uq_cwc_event_sha256"),
        Index(
            "ix_cwc_scope_generation_sequence",
            "environment",
            "account_id",
            "generation",
            "journal_sequence",
        ),
        CheckConstraint(
            "environment='demo' AND account_id ~ '^[0-9]{1,32}$' AND epoch_id ~ '^[a-f0-9]{32}$' AND command_id ~ '^[a-f0-9]{64}$'",
            name=conv("ck_cwc_journal_identity"),
        ),
        CheckConstraint(
            "generation>0 AND journal_sequence>=generation",
            name=conv("ck_cwc_journal_revisions"),
        ),
        CheckConstraint(
            "action IN ('prepared','active','revoked')",
            name=conv("ck_cwc_journal_action"),
        ),
        CheckConstraint(
            "(journal_sequence=1 AND previous_sha256 IS NULL) OR (journal_sequence>1 AND previous_sha256 IS NOT NULL AND previous_sha256 ~ '^[a-f0-9]{64}$')",
            name=conv("ck_cwc_journal_previous"),
        ),
        CheckConstraint(
            "state_sha256 ~ '^[a-f0-9]{64}$' AND event_sha256 ~ '^[a-f0-9]{64}$' AND audit_sha256 ~ '^[a-f0-9]{64}$' AND encode(sha256(convert_to(state_json,'UTF8')),'hex')=state_sha256 AND encode(sha256(convert_to(event_json,'UTF8')),'hex')=event_sha256 AND encode(sha256(convert_to(audit_json,'UTF8')),'hex')=audit_sha256",
            name=conv("ck_cwc_journal_hashes"),
        ),
        CheckConstraint(
            "octet_length(state_json) BETWEEN 1 AND 65536 AND octet_length(event_json) BETWEEN 1 AND 32768 AND octet_length(audit_json) BETWEEN 1 AND 262144",
            name=conv("ck_cwc_journal_bounds"),
        ),
    )
    environment: Mapped[str] = mapped_column(String(8), nullable=False)
    account_id: Mapped[str] = mapped_column(String(32), nullable=False)
    journal_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    generation: Mapped[int] = mapped_column(BigInteger, nullable=False)
    epoch_id: Mapped[str] = mapped_column(String(32), nullable=False)
    command_id: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(8), nullable=False)
    previous_sha256: Mapped[str | None] = mapped_column(String(64))
    state_json: Mapped[str] = mapped_column(Text, nullable=False)
    state_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    event_json: Mapped[str] = mapped_column(Text, nullable=False)
    event_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    audit_json: Mapped[str] = mapped_column(Text, nullable=False)
    audit_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("clock_timestamp()"),
    )


class DemoControlledWriterCoverageCutover(Base):
    """Immutable start marker for one existing active coverage epoch."""

    __tablename__ = "demo_controlled_writer_coverage_cutovers"
    __table_args__ = (
        PrimaryKeyConstraint(
            "environment", "account_id", "epoch_id", name="pk_cwc_cutover"
        ),
        UniqueConstraint("cutover_id", name="uq_cwc_cutover_id"),
        ForeignKeyConstraint(
            ["environment", "account_id", "active_journal_sequence"],
            [
                "demo_controlled_writer_coverage_journal.environment",
                "demo_controlled_writer_coverage_journal.account_id",
                "demo_controlled_writer_coverage_journal.journal_sequence",
            ],
            name="fk_cwc_cutover_active",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["environment", "account_id", "settlement_currency"],
            [
                "qualification_account_scopes.environment",
                "qualification_account_scopes.account_id",
                "qualification_account_scopes.settlement_currency",
            ],
            name="fk_cwc_cutover_scope",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "environment='demo' AND account_id ~ '^[0-9]{1,32}$' AND settlement_currency='USDT' AND epoch_id ~ '^[a-f0-9]{32}$' AND active_journal_sequence>1",
            name=conv("ck_cwc_cutover_identity"),
        ),
        CheckConstraint(
            "active_event_sha256 ~ '^[a-f0-9]{64}$' AND cutover_id ~ '^[a-f0-9]{64}$' AND record_sha256 ~ '^[a-f0-9]{64}$' AND encode(sha256(convert_to(record_json,'UTF8')),'hex')=record_sha256",
            name=conv("ck_cwc_cutover_hashes"),
        ),
        CheckConstraint(
            "octet_length(record_json) BETWEEN 1 AND 16384",
            name=conv("ck_cwc_cutover_bytes"),
        ),
        CheckConstraint(
            "coverage_start_at<=recorded_at", name=conv("ck_cwc_cutover_clock")
        ),
    )
    environment: Mapped[str] = mapped_column(String(8), nullable=False)
    account_id: Mapped[str] = mapped_column(String(32), nullable=False)
    settlement_currency: Mapped[str] = mapped_column(String(16), nullable=False)
    epoch_id: Mapped[str] = mapped_column(String(32), nullable=False)
    active_journal_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    active_event_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    cutover_id: Mapped[str] = mapped_column(String(64), nullable=False)
    record_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    record_json: Mapped[str] = mapped_column(Text, nullable=False)
    coverage_start_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("clock_timestamp()"),
    )
