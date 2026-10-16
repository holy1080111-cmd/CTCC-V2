"""DENY-only Demo event-history epoch journal; no G6 or order authority."""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class DemoEventHistoryEpochEvent(Base):
    __tablename__ = "demo_event_history_epoch_events"
    __table_args__ = (
        UniqueConstraint(
            "environment",
            "account_id",
            "credential_session_binding_sha256",
            "claimed_source_sha256",
            "policy_sha256",
            "sequence",
            name="uq_demo_event_history_epoch_uid_binding_sequence",
        ),
        Index(
            "ix_demo_event_history_epoch_uid",
            "environment",
            "account_id",
            "epoch_id",
            "sequence",
        ),
        CheckConstraint(
            "environment='demo' AND account_id ~ '^[0-9]{1,32}$'",
            name="scope",
        ),
        CheckConstraint(
            "epoch_id ~ '^[a-f0-9]{64}$' AND "
            "credential_session_binding_sha256 ~ '^[a-f0-9]{64}$' AND "
            "claimed_source_sha256 ~ '^[a-f0-9]{64}$' AND "
            "policy_sha256 ~ '^[a-f0-9]{64}$' AND "
            "evidence_sha256 ~ '^[a-f0-9]{64}$' AND "
            "event_sha256 ~ '^[a-f0-9]{64}$'",
            name="hashes",
        ),
        CheckConstraint(
            "(sequence=1 AND kind='anchor_unverified' AND "
            "reason_code='source_history_unverified' AND previous_sha256 IS NULL) "
            "OR (sequence BETWEEN 2 AND 1024 AND kind='invalidated' AND "
            "reason_code ~ '^[a-z][a-z0-9_]{1,63}$' AND "
            "previous_sha256 ~ '^[a-f0-9]{64}$')",
            name="chain",
        ),
        CheckConstraint(
            "octet_length(event_json) BETWEEN 1 AND 4096",
            name="bounded",
        ),
    )

    epoch_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    sequence: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    environment: Mapped[str] = mapped_column(String(8), nullable=False)
    account_id: Mapped[str] = mapped_column(String(32), nullable=False)
    credential_session_binding_sha256: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    claimed_source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    previous_sha256: Mapped[str | None] = mapped_column(String(64))
    event_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    event_json: Mapped[str] = mapped_column(Text, nullable=False)
    db_recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("clock_timestamp()"),
    )
