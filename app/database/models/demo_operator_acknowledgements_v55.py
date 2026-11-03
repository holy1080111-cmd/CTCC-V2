"""Immutable operator recovery/counter rows; no source or execution permission."""

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


class DemoControlClearAcknowledgementV2(Base):
    __tablename__ = "demo_control_clear_acknowledgements_v2"
    __table_args__ = (
        PrimaryKeyConstraint(
            "ack_sha256", name="demo_control_clear_acknowledgements_v2_pkey"
        ),
        ForeignKeyConstraint(
            ["environment", "account_id", "settlement_currency"],
            [
                "qualification_account_scopes.environment",
                "qualification_account_scopes.account_id",
                "qualification_account_scopes.settlement_currency",
            ],
            name="fk_control_clear_scope",
            ondelete="RESTRICT",
            onupdate="NO ACTION",
            deferrable=False,
        ),
        ForeignKeyConstraint(
            ["environment", "account_id", "stopped_control_revision"],
            [
                "demo_control_journal.environment",
                "demo_control_journal.account_id",
                "demo_control_journal.control_revision",
            ],
            name="fk_control_clear_stopped",
            ondelete="RESTRICT",
            onupdate="NO ACTION",
            deferrable=False,
        ),
        ForeignKeyConstraint(
            ["environment", "account_id", "clear_control_revision"],
            [
                "demo_control_journal.environment",
                "demo_control_journal.account_id",
                "demo_control_journal.control_revision",
            ],
            name="fk_control_clear_result",
            ondelete="NO ACTION",
            onupdate="NO ACTION",
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            ["source_lineage_sha256"],
            ["demo_collector_current_revision_lineages.lineage_sha256"],
            name="fk_control_clear_current",
            ondelete="RESTRICT",
            onupdate="NO ACTION",
            deferrable=False,
        ),
        UniqueConstraint(
            "environment", "account_id", "command_id", name="uq_control_clear_command"
        ),
        UniqueConstraint(
            "environment",
            "account_id",
            "stopped_control_revision",
            name="uq_control_clear_once",
        ),
        CheckConstraint(
            "environment='demo' AND account_id ~ '^[0-9]{1,32}$' AND settlement_currency='USDT'",
            name=conv("control_clear_scope"),
        ),
        CheckConstraint(
            "stopped_control_revision>0 AND stopped_control_revision<9223372036854775807 AND clear_control_revision=stopped_control_revision+1 AND owner_epoch>0 AND account_revision>=0 AND ledger_revision>=account_revision",
            name=conv("control_clear_revisions"),
        ),
        CheckConstraint(
            "(account_revision=0 AND claims_sha256 IS NULL) OR (account_revision>0 AND claims_sha256 IS NOT NULL AND claims_sha256 ~ '^[a-f0-9]{64}$')",
            name=conv("control_clear_claims_pair"),
        ),
        CheckConstraint(
            "ack_sha256 ~ '^[a-f0-9]{64}$' AND command_id ~ '^[a-f0-9]{64}$' AND stopped_event_sha256 ~ '^[a-f0-9]{64}$' AND stopped_state_sha256 ~ '^[a-f0-9]{64}$' AND clear_state_sha256 ~ '^[a-f0-9]{64}$' AND clear_event_sha256 ~ '^[a-f0-9]{64}$' AND source_lineage_sha256 ~ '^[a-f0-9]{64}$' AND owner_sha256 ~ '^[a-f0-9]{64}$'",
            name=conv("control_clear_digests"),
        ),
        CheckConstraint(
            "octet_length(ack_json) BETWEEN 1 AND 32768 AND octet_length(state_json) BETWEEN 1 AND 8192 AND octet_length(event_json) BETWEEN 1 AND 12288",
            name=conv("control_clear_bounds"),
        ),
        CheckConstraint(
            "isfinite(owner_sampled_at) AND isfinite(original_expires_at) AND isfinite(original_lease_until) AND owner_sampled_at<original_expires_at AND original_expires_at<=original_lease_until",
            name=conv("control_clear_clocks"),
        ),
    )
    ack_sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    environment: Mapped[str] = mapped_column(String(8), nullable=False)
    account_id: Mapped[str] = mapped_column(String(32), nullable=False)
    settlement_currency: Mapped[str] = mapped_column(String(16), nullable=False)
    command_id: Mapped[str] = mapped_column(String(64), nullable=False)
    stopped_control_revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    stopped_event_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    stopped_state_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    clear_control_revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    clear_state_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    clear_event_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source_lineage_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    account_revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    ledger_revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    claims_sha256: Mapped[str | None] = mapped_column(String(64))
    owner_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    owner_epoch: Mapped[int] = mapped_column(BigInteger, nullable=False)
    original_lease_until: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    owner_sampled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    original_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    ack_json: Mapped[str] = mapped_column(Text, nullable=False)
    state_json: Mapped[str] = mapped_column(Text, nullable=False)
    event_json: Mapped[str] = mapped_column(Text, nullable=False)
    db_recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.clock_timestamp()
    )


class DemoPolicyCounterInitializationV1(Base):
    __tablename__ = "demo_policy_counter_initializations_v1"
    __table_args__ = (
        PrimaryKeyConstraint(
            "initialization_sha256", name="demo_policy_counter_initializations_v1_pkey"
        ),
        ForeignKeyConstraint(
            ["environment", "account_id", "settlement_currency"],
            [
                "qualification_account_scopes.environment",
                "qualification_account_scopes.account_id",
                "qualification_account_scopes.settlement_currency",
            ],
            name="fk_policy_counter_scope",
            ondelete="RESTRICT",
            onupdate="NO ACTION",
            deferrable=False,
        ),
        ForeignKeyConstraint(
            ["environment", "account_id", "control_revision"],
            [
                "demo_control_journal.environment",
                "demo_control_journal.account_id",
                "demo_control_journal.control_revision",
            ],
            name="fk_policy_counter_control",
            ondelete="RESTRICT",
            onupdate="NO ACTION",
            deferrable=False,
        ),
        ForeignKeyConstraint(
            ["source_lineage_sha256"],
            ["demo_collector_current_revision_lineages.lineage_sha256"],
            name="fk_policy_counter_current",
            ondelete="RESTRICT",
            onupdate="NO ACTION",
            deferrable=False,
        ),
        UniqueConstraint(
            "environment",
            "account_id",
            "settlement_currency",
            name="uq_policy_counter_once",
        ),
        UniqueConstraint(
            "environment", "account_id", "command_id", name="uq_policy_counter_command"
        ),
        CheckConstraint(
            "environment='demo' AND account_id ~ '^[0-9]{1,32}$' AND settlement_currency='USDT'",
            name=conv("policy_counter_scope"),
        ),
        CheckConstraint(
            "control_revision>0 AND account_revision>=0 AND ledger_revision>=account_revision",
            name=conv("policy_counter_revisions"),
        ),
        CheckConstraint(
            "(account_revision=0 AND claims_sha256 IS NULL) OR (account_revision>0 AND claims_sha256 IS NOT NULL AND claims_sha256 ~ '^[a-f0-9]{64}$')",
            name=conv("policy_counter_claims_pair"),
        ),
        CheckConstraint(
            "initialization_sha256 ~ '^[a-f0-9]{64}$' AND command_id ~ '^[a-f0-9]{64}$' AND control_event_sha256 ~ '^[a-f0-9]{64}$' AND control_state_sha256 ~ '^[a-f0-9]{64}$' AND source_lineage_sha256 ~ '^[a-f0-9]{64}$' AND configured_policy_sha256 ~ '^[a-f0-9]{64}$' AND measured_genesis_sha256 ~ '^[a-f0-9]{64}$'",
            name=conv("policy_counter_digests"),
        ),
        CheckConstraint(
            "octet_length(ack_json) BETWEEN 1 AND 32768 AND octet_length(configured_policy_json) BETWEEN 1 AND 32768 AND octet_length(measured_genesis_json) BETWEEN 1 AND 1048576",
            name=conv("policy_counter_bounds"),
        ),
        CheckConstraint(
            "isfinite(owner_sampled_at) AND isfinite(original_expires_at) AND owner_sampled_at<original_expires_at AND initialized_at=db_recorded_at AND isfinite(initialized_at) AND owner_sampled_at<=initialized_at AND initialized_at<original_expires_at",
            name=conv("policy_counter_clocks"),
        ),
    )
    initialization_sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    environment: Mapped[str] = mapped_column(String(8), nullable=False)
    account_id: Mapped[str] = mapped_column(String(32), nullable=False)
    settlement_currency: Mapped[str] = mapped_column(String(16), nullable=False)
    command_id: Mapped[str] = mapped_column(String(64), nullable=False)
    control_revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    control_event_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    control_state_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source_lineage_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    account_revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    ledger_revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    claims_sha256: Mapped[str | None] = mapped_column(String(64))
    configured_policy_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    configured_policy_json: Mapped[str] = mapped_column(Text, nullable=False)
    measured_genesis_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    measured_genesis_json: Mapped[str] = mapped_column(Text, nullable=False)
    owner_sampled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    original_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    ack_json: Mapped[str] = mapped_column(Text, nullable=False)
    initialized_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.clock_timestamp()
    )
    db_recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.clock_timestamp()
    )
