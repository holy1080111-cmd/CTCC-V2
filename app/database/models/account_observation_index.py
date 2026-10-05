"""Append-only B3 observations referencing the original DB0020 terminal."""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class DemoAccountObservationBatch(Base):
    __tablename__ = "demo_account_observation_batches"
    __table_args__ = (
        ForeignKeyConstraint(
            ["capture_id", "source_sequence"],
            [
                "demo_account_capture_events.capture_id",
                "demo_account_capture_events.sequence",
            ],
            ondelete="RESTRICT",
            name="fk_account_observation_source",
        ),
        UniqueConstraint("capture_id", name="uq_account_observation_capture"),
        CheckConstraint(
            "environment='demo' AND account_id ~ '^[0-9]{1,32}$' AND settlement_currency ~ '^[A-Z0-9]{1,16}$'",
            name="scope",
        ),
        CheckConstraint(
            "sequence BETWEEN 1 AND 9223372036854775807 AND source_sequence BETWEEN 1 AND 8192",
            name="sequence_bound",
        ),
        CheckConstraint(
            "event_sha256 ~ '^[a-f0-9]{64}$' AND ((sequence=1 AND previous_sha256 IS NULL) OR (sequence>1 AND previous_sha256 ~ '^[a-f0-9]{64}$'))",
            name="chain",
        ),
        CheckConstraint(
            "octet_length(document_json) BETWEEN 1 AND 262144 AND octet_length(receipt_json) BETWEEN 1 AND 8388608",
            name="byte_bound",
        ),
    )
    environment: Mapped[str] = mapped_column(String(8), primary_key=True)
    account_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    settlement_currency: Mapped[str] = mapped_column(String(16), primary_key=True)
    sequence: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    capture_id: Mapped[str] = mapped_column(String(32), nullable=False)
    source_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    previous_sha256: Mapped[str | None] = mapped_column(String(64))
    event_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    document_json: Mapped[str] = mapped_column(Text, nullable=False)
    receipt_json: Mapped[str] = mapped_column(Text, nullable=False)
    db_recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.clock_timestamp()
    )


class DemoAccountObservationFact(Base):
    __tablename__ = "demo_account_observation_facts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["environment", "account_id", "settlement_currency", "batch_sequence"],
            [
                "demo_account_observation_batches.environment",
                "demo_account_observation_batches.account_id",
                "demo_account_observation_batches.settlement_currency",
                "demo_account_observation_batches.sequence",
            ],
            ondelete="RESTRICT",
            name="fk_observation_fact_batch",
        ),
        CheckConstraint(
            "ordinal BETWEEN 1 AND 16384 AND octet_length(fact_json) BETWEEN 1 AND 262144",
            name="bound",
        ),
        CheckConstraint(
            "identity_sha256 ~ '^[a-f0-9]{64}$' AND row_sha256 ~ '^[a-f0-9]{64}$' AND (metadata_sha256 IS NULL OR metadata_sha256 ~ '^[a-f0-9]{64}$')",
            name="digests",
        ),
        Index(
            "ix_observation_fact_identity",
            "environment",
            "account_id",
            "settlement_currency",
            "identity_sha256",
        ),
        Index(
            "ix_observation_fact_generation",
            "environment",
            "account_id",
            "settlement_currency",
            "generation_at",
        ),
    )
    environment: Mapped[str] = mapped_column(String(8), primary_key=True)
    account_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    settlement_currency: Mapped[str] = mapped_column(String(16), primary_key=True)
    batch_sequence: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ordinal: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    family: Mapped[str] = mapped_column(String(8), nullable=False)
    product: Mapped[str | None] = mapped_column(String(16))
    identity_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    row_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    metadata_sha256: Mapped[str | None] = mapped_column(String(64))
    generation_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    fill_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fact_json: Mapped[str] = mapped_column(Text, nullable=False)


class DemoAccountObservationCoverage(Base):
    __tablename__ = "demo_account_observation_coverage"
    __table_args__ = (
        ForeignKeyConstraint(
            ["environment", "account_id", "settlement_currency", "batch_sequence"],
            [
                "demo_account_observation_batches.environment",
                "demo_account_observation_batches.account_id",
                "demo_account_observation_batches.settlement_currency",
                "demo_account_observation_batches.sequence",
            ],
            ondelete="RESTRICT",
            name="fk_observation_coverage_batch",
        ),
        CheckConstraint(
            "ordinal BETWEEN 1 AND 128 AND started_at <= ended_at AND octet_length(coverage_json) BETWEEN 1 AND 262144",
            name="bound",
        ),
        Index(
            "ix_observation_coverage_domain",
            "environment",
            "account_id",
            "settlement_currency",
            "family",
            "product",
            "ended_at",
        ),
    )
    environment: Mapped[str] = mapped_column(String(8), primary_key=True)
    account_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    settlement_currency: Mapped[str] = mapped_column(String(16), primary_key=True)
    batch_sequence: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ordinal: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    family: Mapped[str] = mapped_column(String(8), nullable=False)
    product: Mapped[str] = mapped_column(String(16), nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    ended_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    coverage_json: Mapped[str] = mapped_column(Text, nullable=False)


class DemoAccountObservationFinding(Base):
    __tablename__ = "demo_account_observation_findings"
    __table_args__ = (
        ForeignKeyConstraint(
            ["environment", "account_id", "settlement_currency", "batch_sequence"],
            [
                "demo_account_observation_batches.environment",
                "demo_account_observation_batches.account_id",
                "demo_account_observation_batches.settlement_currency",
                "demo_account_observation_batches.sequence",
            ],
            ondelete="RESTRICT",
            name="fk_observation_finding_batch",
        ),
        CheckConstraint(
            "ordinal BETWEEN 1 AND 16384 AND octet_length(finding_json) BETWEEN 1 AND 262144",
            name="bound",
        ),
    )
    environment: Mapped[str] = mapped_column(String(8), primary_key=True)
    account_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    settlement_currency: Mapped[str] = mapped_column(String(16), primary_key=True)
    batch_sequence: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ordinal: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    finding_json: Mapped[str] = mapped_column(Text, nullable=False)
