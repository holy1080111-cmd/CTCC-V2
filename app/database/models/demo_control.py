"""Account-scoped Demo control and immutable journal; no trading permit."""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class DemoAccountControl(Base):
    __tablename__ = "demo_account_controls"
    __table_args__ = (
        CheckConstraint(
            "environment = 'demo' AND account_id ~ '^[0-9]{1,32}$'", name="identity"
        ),
        CheckConstraint(
            "control_revision > 0 AND owner_epoch >= 0 AND owner_epoch <= control_revision",
            name="revisions",
        ),
        CheckConstraint(
            "state_sha256 ~ '^[a-f0-9]{64}$'",
            name="digests",
        ),
        CheckConstraint(
            "(owner_epoch=0 AND owner_sha256 IS NULL AND emergency_stop AND arm_request_id IS NULL AND lease_until=updated_at) OR (owner_epoch>0 AND owner_sha256 IS NOT NULL AND owner_sha256 ~ '^[a-f0-9]{64}$')",
            name="owner_pair",
        ),
        CheckConstraint(
            "created_at <= updated_at AND updated_at <= lease_until", name="clocks"
        ),
        CheckConstraint(
            "octet_length(state_json) BETWEEN 1 AND 8192", name="state_bound"
        ),
        CheckConstraint(
            "(arm_request_id IS NULL AND arm_expires_at IS NULL) OR (arm_request_id IS NOT NULL AND arm_request_id ~ '^[a-f0-9]{64}$' AND arm_expires_at IS NOT NULL AND NOT emergency_stop AND created_at < arm_expires_at AND arm_expires_at <= lease_until)",
            name="arm_pair",
        ),
    )
    environment: Mapped[str] = mapped_column(String(8), primary_key=True)
    account_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    control_revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    owner_sha256: Mapped[str | None] = mapped_column(String(64))
    owner_epoch: Mapped[int] = mapped_column(BigInteger, nullable=False)
    lease_until: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    emergency_stop: Mapped[bool] = mapped_column(Boolean, nullable=False)
    arm_request_id: Mapped[str | None] = mapped_column(String(64))
    arm_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    state_json: Mapped[str] = mapped_column(Text, nullable=False)
    state_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class DemoControlJournal(Base):
    __tablename__ = "demo_control_journal"
    __table_args__ = (
        ForeignKeyConstraint(
            ["environment", "account_id"],
            ["demo_account_controls.environment", "demo_account_controls.account_id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("environment", "account_id", "command_id"),
        CheckConstraint("control_revision > 0", name="revision"),
        CheckConstraint(
            "command_id ~ '^[a-f0-9]{64}$' AND event_sha256 ~ '^[a-f0-9]{64}$' AND state_sha256 ~ '^[a-f0-9]{64}$'",
            name="digests",
        ),
        CheckConstraint(
            "(control_revision = 1 AND previous_sha256 IS NULL) OR (control_revision > 1 AND previous_sha256 IS NOT NULL AND previous_sha256 ~ '^[a-f0-9]{64}$')",
            name="chain",
        ),
        CheckConstraint(
            "action IN ('acquire','renew','arm_requested','disarm','estop','rebind','release')",
            name="action",
        ),
        CheckConstraint(
            "octet_length(event_json) BETWEEN 1 AND 12288", name="event_bound"
        ),
    )
    environment: Mapped[str] = mapped_column(String(8), primary_key=True)
    account_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    control_revision: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    command_id: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(24), nullable=False)
    previous_sha256: Mapped[str | None] = mapped_column(String(64))
    event_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    state_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    event_json: Mapped[str] = mapped_column(Text, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
