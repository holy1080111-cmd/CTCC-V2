"""Immutable, DENY-only binding of a ledger revision to recorded account sources.

This row is a forensic prerequisite, not the future collector-owned revision
issuer. In particular it never converts a caller-written ``reconcile_scope``
revision into an authenticated portfolio or order permission.
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    false,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class DemoAccountRevisionSourceWitness(Base):
    __tablename__ = "demo_account_revision_source_witnesses"
    __table_args__ = (
        ForeignKeyConstraint(
            ["environment", "account_id", "settlement_currency"],
            [
                "qualification_account_scopes.environment",
                "qualification_account_scopes.account_id",
                "qualification_account_scopes.settlement_currency",
            ],
            ondelete="RESTRICT",
            name="fk_account_revision_source_scope",
        ),
        ForeignKeyConstraint(
            ["history_capture_id", "history_terminal_sequence"],
            [
                "demo_account_capture_events.capture_id",
                "demo_account_capture_events.sequence",
            ],
            ondelete="RESTRICT",
            name="fk_account_revision_source_history_terminal",
        ),
        ForeignKeyConstraint(
            ["current_capture_id", "current_terminal_sequence"],
            [
                "demo_account_capture_events.capture_id",
                "demo_account_capture_events.sequence",
            ],
            ondelete="RESTRICT",
            name="fk_account_revision_source_current_terminal",
        ),
        ForeignKeyConstraint(
            ["environment", "account_id", "settlement_currency", "typed_sequence"],
            [
                "demo_account_observation_typed_batches.environment",
                "demo_account_observation_typed_batches.account_id",
                "demo_account_observation_typed_batches.settlement_currency",
                "demo_account_observation_typed_batches.sequence",
            ],
            ondelete="RESTRICT",
            name="fk_account_revision_source_typed",
        ),
        ForeignKeyConstraint(
            ["environment", "account_id", "settlement_currency", "current_sequence"],
            [
                "demo_account_current_observations.environment",
                "demo_account_current_observations.account_id",
                "demo_account_current_observations.settlement_currency",
                "demo_account_current_observations.sequence",
            ],
            ondelete="RESTRICT",
            name="fk_account_revision_source_current",
        ),
        UniqueConstraint(
            "environment",
            "account_id",
            "settlement_currency",
            "account_revision",
            name="uq_account_revision_source_exact_revision",
        ),
        CheckConstraint(
            "environment='demo' AND account_id ~ '^[0-9]{1,32}$' "
            "AND settlement_currency ~ '^[A-Z0-9]{1,16}$'",
            name="scope",
        ),
        CheckConstraint(
            "account_revision >= 1 AND ledger_revision >= account_revision "
            "AND typed_sequence >= 1 AND current_sequence >= 1 "
            "AND history_terminal_sequence >= 1 AND current_terminal_sequence >= 1",
            name="revisions",
        ),
        CheckConstraint(
            "claims_sha256 ~ '^[a-f0-9]{64}$' "
            "AND history_head_sha256 ~ '^[a-f0-9]{64}$' "
            "AND current_head_sha256 ~ '^[a-f0-9]{64}$' "
            "AND typed_head_sha256 ~ '^[a-f0-9]{64}$' "
            "AND current_index_sha256 ~ '^[a-f0-9]{64}$' "
            "AND witness_sha256 ~ '^[a-f0-9]{64}$'",
            name="digests",
        ),
        CheckConstraint(
            "history_capture_id ~ '^[a-f0-9]{32}$' "
            "AND current_capture_id ~ '^[a-f0-9]{32}$' "
            "AND history_capture_id <> current_capture_id",
            name="capture_ids",
        ),
        CheckConstraint(
            "account_complete=false AND source_authenticity_verified=false "
            "AND execution_authority=false AND admission='DENY'",
            name="no_authority",
        ),
        CheckConstraint(
            "octet_length(witness_json) BETWEEN 1 AND 262144",
            name="witness_bound",
        ),
        CheckConstraint(
            "octet_length(claims_json) BETWEEN 1 AND 8388608",
            name="claims_bound",
        ),
    )

    witness_sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    environment: Mapped[str] = mapped_column(String(8), nullable=False)
    account_id: Mapped[str] = mapped_column(String(32), nullable=False)
    settlement_currency: Mapped[str] = mapped_column(String(16), nullable=False)
    account_revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    ledger_revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    claims_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    claims_json: Mapped[str] = mapped_column(Text, nullable=False)
    history_capture_id: Mapped[str] = mapped_column(String(32), nullable=False)
    history_terminal_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    history_head_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    typed_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    typed_head_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    current_capture_id: Mapped[str] = mapped_column(String(32), nullable=False)
    current_terminal_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    current_head_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    current_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    current_index_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    witness_json: Mapped[str] = mapped_column(Text, nullable=False)
    account_complete: Mapped[bool] = mapped_column(
        nullable=False, server_default=false()
    )
    source_authenticity_verified: Mapped[bool] = mapped_column(
        nullable=False, server_default=false()
    )
    execution_authority: Mapped[bool] = mapped_column(
        nullable=False, server_default=false()
    )
    admission: Mapped[str] = mapped_column(
        String(8), nullable=False, server_default=text("'DENY'")
    )
    db_recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.clock_timestamp()
    )
