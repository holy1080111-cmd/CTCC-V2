"""A restricted observer's committed-witness observation; no Gate 3 authority."""

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


class PublicReceiptPublicationAck(Base):
    __tablename__ = "public_receipt_publication_acks"
    __table_args__ = (
        UniqueConstraint(
            "journal_key",
            "capture_sequence",
            name="uq_public_receipt_publication_ack_capture_sequence",
        ),
        ForeignKeyConstraint(
            ("journal_key", "witness_revision"),
            (
                "public_receipt_witness_revisions.journal_key",
                "public_receipt_witness_revisions.revision",
            ),
            name="fk_public_receipt_publication_ack_witness",
        ),
        CheckConstraint(
            "journal_key ~ '^[a-f0-9]{64}$' "
            "AND witness_record_sha256 ~ '^[a-f0-9]{64}$' "
            "AND checkpoint_sha256 ~ '^[a-f0-9]{64}$' "
            "AND capture_head_sha256 ~ '^[a-f0-9]{64}$'",
            name="hashes",
        ),
        CheckConstraint(
            "witness_revision BETWEEN 1 AND 8192 "
            "AND capture_sequence BETWEEN 1 AND 1024",
            name="bounds",
        ),
        CheckConstraint("witness_recorded_at <= acknowledged_at", name="time"),
    )

    journal_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    witness_revision: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    witness_record_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    checkpoint_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    capture_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    capture_head_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    witness_recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    acknowledged_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
