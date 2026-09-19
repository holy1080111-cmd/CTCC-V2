"""Append-only post-submit observations and local reporting projections."""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class QualificationSubmissionOutcome(Base):
    __tablename__ = "qualification_submission_outcomes"
    __table_args__ = (
        UniqueConstraint("reservation_id", "sequence"),
        CheckConstraint("sequence > 0 AND ledger_revision > 0", name="revisions"),
        CheckConstraint(
            "status IN ('acknowledged','rejected','uncertain')", name="status"
        ),
        CheckConstraint(
            "observation_kind IN ('initial','reconciliation')", name="kind"
        ),
        CheckConstraint(
            "(sequence = 1 AND previous_sha256 IS NULL) OR (sequence > 1 AND previous_sha256 ~ '^[a-f0-9]{64}$')",
            name="chain",
        ),
        CheckConstraint(
            "outcome_id ~ '^[a-f0-9]{64}$' AND intent_sha256 ~ '^[a-f0-9]{64}$' AND exchange_request_sha256 ~ '^[a-f0-9]{64}$' AND capture_sha256 ~ '^[a-f0-9]{64}$'",
            name="digests",
        ),
        CheckConstraint(
            "octet_length(capture_json) BETWEEN 1 AND 140000 AND octet_length(outcome_json) BETWEEN 1 AND 16384",
            name="bounds",
        ),
        CheckConstraint("recorded_at >= observed_at", name="clocks"),
        Index(
            "uq_submission_initial",
            "reservation_id",
            unique=True,
            postgresql_where=text("observation_kind = 'initial'"),
        ),
    )
    outcome_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    reservation_id: Mapped[str] = mapped_column(
        ForeignKey("qualification_reservations.reservation_id", ondelete="RESTRICT"),
        nullable=False,
    )
    intent_transition_id: Mapped[int] = mapped_column(
        ForeignKey("qualification_reservation_transitions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    previous_sha256: Mapped[str | None] = mapped_column(String(64))
    observation_kind: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    intent_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    exchange_request_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    capture_json: Mapped[str] = mapped_column(Text, nullable=False)
    capture_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    outcome_json: Mapped[str] = mapped_column(Text, nullable=False)
    ledger_revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class QualificationReportSpool(Base):
    __tablename__ = "qualification_report_spool"
    __table_args__ = (
        UniqueConstraint("outcome_id"),
        CheckConstraint(
            "spool_id ~ '^[a-f0-9]{64}$' AND report_sha256 ~ '^[a-f0-9]{64}$' AND policy_sha256 ~ '^[a-f0-9]{64}$'",
            name="digests",
        ),
        CheckConstraint(
            "queue_namespace ~ '^[a-z][a-z0-9_-]{0,63}$' AND report_id ~ '^[A-Za-z0-9][A-Za-z0-9_-]{0,95}$'",
            name="names",
        ),
        CheckConstraint(
            "octet_length(report_bytes) BETWEEN 1 AND 32768 AND octet_length(policy_json) BETWEEN 1 AND 4096",
            name="bounds",
        ),
        Index(
            "uq_submission_eligible_reservation",
            "reservation_id",
            unique=True,
            postgresql_where=text("eligible"),
        ),
        Index(
            "uq_submission_eligible_report",
            "queue_namespace",
            "report_id",
            unique=True,
            postgresql_where=text("eligible"),
        ),
        Index(
            "ix_submission_pending_spool",
            "queue_namespace",
            "created_at",
            "spool_id",
            postgresql_where=text("eligible"),
        ),
    )
    spool_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    outcome_id: Mapped[str] = mapped_column(
        ForeignKey("qualification_submission_outcomes.outcome_id", ondelete="RESTRICT"),
        nullable=False,
    )
    reservation_id: Mapped[str] = mapped_column(
        ForeignKey("qualification_reservations.reservation_id", ondelete="RESTRICT"),
        nullable=False,
    )
    report_id: Mapped[str] = mapped_column(String(96), nullable=False)
    report_bytes: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    report_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_json: Mapped[str] = mapped_column(Text, nullable=False)
    policy_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    queue_namespace: Mapped[str] = mapped_column(String(64), nullable=False)
    eligible: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class QualificationReportProjectionReceipt(Base):
    __tablename__ = "qualification_report_projection_receipts"
    __table_args__ = (
        CheckConstraint(
            "report_sha256 ~ '^[a-f0-9]{64}$' AND policy_sha256 ~ '^[a-f0-9]{64}$' AND envelope_sha256 ~ '^[a-f0-9]{64}$'",
            name="digests",
        ),
    )
    spool_id: Mapped[str] = mapped_column(
        ForeignKey("qualification_report_spool.spool_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    report_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    envelope_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    queue_namespace: Mapped[str] = mapped_column(String(64), nullable=False)
    verified_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
