"""Immutable public-minute checkpoint witness; no execution authority."""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Index,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class PublicReceiptWitnessRevision(Base):
    __tablename__ = "public_receipt_witness_revisions"
    __table_args__ = (
        CheckConstraint("revision BETWEEN 0 AND 8192", name="revision_bound"),
        CheckConstraint(
            "journal_key ~ '^[a-f0-9]{64}$' AND journal_id ~ '^[a-f0-9]{32}$' "
            "AND checkpoint_sha256 ~ '^[a-f0-9]{64}$' "
            "AND record_sha256 ~ '^[a-f0-9]{64}$' "
            "AND (previous_record_sha256 IS NULL OR previous_record_sha256 ~ '^[a-f0-9]{64}$') "
            "AND (plan_sha256 IS NULL OR plan_sha256 ~ '^[a-f0-9]{64}$') "
            "AND (operation_id IS NULL OR operation_id ~ '^[a-f0-9]{32}$')",
            name="identity",
        ),
        CheckConstraint(
            "root_device >= 0 AND root_inode >= 1 "
            "AND octet_length(checkpoint_json) BETWEEN 1 AND 4096",
            name="checkpoint_bound",
        ),
        CheckConstraint(
            "transition IN ('initialize','open_attempt','append_attempt',"
            "'append_capture','close_rejected_attempt') "
            "AND state IN ('idle','open','attempt_anchored') "
            "AND (attempt_outcome IS NULL OR attempt_outcome IN "
            "('completed_collection','rejected','incomplete'))",
            name="state",
        ),
        Index(
            "uq_public_receipt_witness_operation_start",
            "journal_key",
            "operation_id",
            unique=True,
            postgresql_where=text("transition='open_attempt'"),
        ),
    )

    journal_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    revision: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    journal_id: Mapped[str] = mapped_column(String(32), nullable=False)
    root_device: Mapped[int] = mapped_column(BigInteger, nullable=False)
    root_inode: Mapped[int] = mapped_column(BigInteger, nullable=False)
    transition: Mapped[str] = mapped_column(String(24), nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False)
    operation_id: Mapped[str | None] = mapped_column(String(32))
    plan_sha256: Mapped[str | None] = mapped_column(String(64))
    attempt_outcome: Mapped[str | None] = mapped_column(String(24))
    previous_record_sha256: Mapped[str | None] = mapped_column(String(64))
    checkpoint_json: Mapped[str] = mapped_column(Text, nullable=False)
    checkpoint_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    record_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.clock_timestamp()
    )
