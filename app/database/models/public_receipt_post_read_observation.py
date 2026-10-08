"""Append-only observation after a committed public witness read."""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class PublicReceiptPostReadObservation(Base):
    __tablename__ = "public_receipt_post_read_observations"
    __table_args__ = (
        UniqueConstraint(
            "journal_key",
            "capture_sequence",
            name="uq_public_receipt_post_read_capture",
        ),
        ForeignKeyConstraint(
            ("journal_key", "witness_revision"),
            (
                "public_receipt_witness_revisions.journal_key",
                "public_receipt_witness_revisions.revision",
            ),
            name="fk_public_receipt_post_read_witness",
        ),
        CheckConstraint(
            "journal_key ~ '^[a-f0-9]{64}$' "
            "AND witness_record_sha256 ~ '^[a-f0-9]{64}$' "
            "AND checkpoint_sha256 ~ '^[a-f0-9]{64}$' "
            "AND capture_head_sha256 ~ '^[a-f0-9]{64}$' "
            "AND capture_id ~ '^[a-f0-9]{32}$' "
            "AND plan_sha256 ~ '^[a-f0-9]{64}$' "
            "AND receipt_sha256 ~ '^[a-f0-9]{64}$' "
            "AND source_rows_sha256 ~ '^[a-f0-9]{64}$' "
            "AND v2_capture_sha256 ~ '^[a-f0-9]{64}$'",
            name="hashes",
        ),
        CheckConstraint(
            "witness_revision BETWEEN 3 AND 8192 "
            "AND capture_sequence BETWEEN 1 AND 1024",
            name="bounds",
        ),
        CheckConstraint("witness_recorded_at <= observed_at", name="time"),
    )

    journal_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    witness_revision: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    witness_record_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    checkpoint_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    capture_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    capture_head_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    capture_id: Mapped[str] = mapped_column(String(32), nullable=False)
    plan_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    receipt_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source_rows_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    v2_capture_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    witness_recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
