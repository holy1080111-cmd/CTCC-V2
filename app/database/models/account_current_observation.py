"""Append-only V7 current-account diagnostics, separate from B3 history."""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class DemoAccountCurrentObservation(Base):
    __tablename__ = "demo_account_current_observations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["capture_id", "source_sequence"],
            [
                "demo_account_capture_events.capture_id",
                "demo_account_capture_events.sequence",
            ],
            ondelete="RESTRICT",
            name="fk_account_current_observation_source",
        ),
        UniqueConstraint("capture_id", name="uq_account_current_observation_capture"),
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
