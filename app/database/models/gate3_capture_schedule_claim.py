"""Immutable canonical key ownership and separate committed-claim ACKs."""

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class Gate3CaptureScheduleKeyClaim(Base):
    __tablename__ = "gate3_capture_schedule_key_claims"
    __table_args__ = (
        CheckConstraint(
            "schedule_sha256 ~ '^[a-f0-9]{64}$' "
            "AND seal_sha256 ~ '^[a-f0-9]{64}$' "
            "AND coordinate_plan_sha256 ~ '^[a-f0-9]{64}$' "
            "AND window_key ~ '^[a-f0-9]{64}$'",
            name="hashes",
        ),
        CheckConstraint(
            "schedule_recorded_at <= claimed_at "
            "AND claimed_at < window_start AND window_start < window_end",
            name="times",
        ),
    )

    schedule_sha256: Mapped[str] = mapped_column(
        String(64),
        ForeignKey(
            "gate3_capture_schedule_pins.schedule_sha256",
            name="fk_gate3_schedule_claim_pin",
        ),
        primary_key=True,
    )
    seal_sha256: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    coordinate_plan_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    window_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    holdout_id: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    window_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    window_end: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    schedule_recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    claimed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.clock_timestamp()
    )


class Gate3CaptureScheduleClaimAck(Base):
    __tablename__ = "gate3_capture_schedule_claim_acks"
    __table_args__ = (
        CheckConstraint(
            "schedule_sha256 ~ '^[a-f0-9]{64}$' "
            "AND seal_sha256 ~ '^[a-f0-9]{64}$' "
            "AND coordinate_plan_sha256 ~ '^[a-f0-9]{64}$' "
            "AND window_key ~ '^[a-f0-9]{64}$'",
            name="hashes",
        ),
        CheckConstraint(
            "schedule_recorded_at <= claim_recorded_at "
            "AND claim_recorded_at <= acknowledged_at "
            "AND acknowledged_at < window_start AND window_start < window_end",
            name="times",
        ),
    )

    schedule_sha256: Mapped[str] = mapped_column(
        String(64),
        ForeignKey(
            "gate3_capture_schedule_key_claims.schedule_sha256",
            name="fk_gate3_schedule_claim_ack_claim",
        ),
        primary_key=True,
    )
    seal_sha256: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    coordinate_plan_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    window_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    holdout_id: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    window_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    window_end: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    schedule_recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    claim_recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    acknowledged_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.clock_timestamp()
    )


class Gate3CaptureScheduleLegacyInventory(Base):
    __tablename__ = "gate3_capture_schedule_legacy_inventory"
    __table_args__ = (
        CheckConstraint(
            "classification IN ('canonical','noncanonical','unknown')",
            name="classification",
        ),
        Index(
            "uq_gate3_schedule_legacy_reserved_seal",
            "seal_sha256",
            unique=True,
            postgresql_where=text("classification <> 'noncanonical'"),
        ),
        Index(
            "uq_gate3_schedule_legacy_reserved_window",
            "window_key",
            unique=True,
            postgresql_where=text("classification <> 'noncanonical'"),
        ),
        Index(
            "uq_gate3_schedule_legacy_reserved_holdout",
            "holdout_id",
            unique=True,
            postgresql_where=text("classification <> 'noncanonical'"),
        ),
    )

    schedule_sha256: Mapped[str] = mapped_column(
        String(64),
        ForeignKey(
            "gate3_capture_schedule_pins.schedule_sha256",
            name="fk_gate3_schedule_legacy_inventory_pin",
        ),
        primary_key=True,
    )
    seal_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    window_key: Mapped[str] = mapped_column(String(64), nullable=False)
    holdout_id: Mapped[str] = mapped_column(String(160), nullable=False)
    classification: Mapped[str] = mapped_column(String(16), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.clock_timestamp()
    )
