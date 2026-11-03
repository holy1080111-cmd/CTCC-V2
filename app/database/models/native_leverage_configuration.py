"""Dedicated Demo configuration journal; never an order or account-claims model."""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    FetchedValue,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import OID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class _ConfigurationFenceColumnsV1:
    environment: Mapped[str] = mapped_column(String(8), nullable=False)
    account_id: Mapped[str] = mapped_column(String(32), nullable=False)
    settlement_currency: Mapped[str] = mapped_column(String(16), nullable=False)
    account_revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    previous_ledger_revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    ledger_revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    db_role_oid: Mapped[int] = mapped_column(
        OID, nullable=False, server_default=FetchedValue()
    )
    db_backend_pid: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=FetchedValue()
    )
    db_transaction_id: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=FetchedValue()
    )
    db_recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=FetchedValue()
    )


class NativeLeverageConfigurationIntentV1(_ConfigurationFenceColumnsV1, Base):
    __tablename__ = "qualification_leverage_configuration_intents_v1"
    __table_args__ = (
        Index(
            "qualification_leverage_configuration_active_uid_v55",
            "environment",
            "account_id",
            unique=True,
            postgresql_where=text(
                "phase IN ('prepared','attempted','verified','unknown')"
            ),
        ),
        ForeignKeyConstraint(
            ["environment", "account_id", "settlement_currency"],
            [
                "qualification_account_scopes.environment",
                "qualification_account_scopes.account_id",
                "qualification_account_scopes.settlement_currency",
            ],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["environment", "account_id"],
            ["demo_account_controls.environment", "demo_account_controls.account_id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["environment", "account_id", "control_revision"],
            [
                "demo_control_journal.environment",
                "demo_control_journal.account_id",
                "demo_control_journal.control_revision",
            ],
            ondelete="RESTRICT",
        ),
    )
    intent_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    claims_sha256: Mapped[str | None] = mapped_column(String(64))
    control_revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    control_event_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    core_json: Mapped[str] = mapped_column(Text, nullable=False)
    source_json: Mapped[str] = mapped_column(Text, nullable=False)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    plan_json: Mapped[str] = mapped_column(Text, nullable=False)
    plan_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    request_json: Mapped[str] = mapped_column(Text, nullable=False)
    request_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    original_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    phase: Mapped[str] = mapped_column(String(16), nullable=False)
    latest_result_sequence: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )


class NativeLeverageConfigurationAttemptV1(_ConfigurationFenceColumnsV1, Base):
    __tablename__ = "qualification_leverage_configuration_attempts_v1"
    __table_args__ = (
        ForeignKeyConstraint(
            ["intent_id"],
            ["qualification_leverage_configuration_intents_v1.intent_id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "attempt_id",
            name="qualification_leverage_configuration_attempts_v1_attempt_id_key",
        ),
    )
    intent_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    attempt_id: Mapped[str] = mapped_column(String(64), nullable=False)
    attempt_json: Mapped[str] = mapped_column(Text, nullable=False)


class NativeLeverageConfigurationResultV1(_ConfigurationFenceColumnsV1, Base):
    __tablename__ = "qualification_leverage_configuration_results_v1"
    __table_args__ = (
        ForeignKeyConstraint(
            ["intent_id"],
            ["qualification_leverage_configuration_intents_v1.intent_id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["attempt_id"],
            ["qualification_leverage_configuration_attempts_v1.attempt_id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "intent_id",
            "result_sequence",
            name="qualification_leverage_configurat_intent_id_result_sequence_key",
        ),
    )
    result_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    intent_id: Mapped[str] = mapped_column(String(64), nullable=False)
    attempt_id: Mapped[str | None] = mapped_column(String(64))
    result_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    previous_phase: Mapped[str] = mapped_column(String(16), nullable=False)
    phase: Mapped[str] = mapped_column(String(16), nullable=False)
    result_json: Mapped[str] = mapped_column(Text, nullable=False)
