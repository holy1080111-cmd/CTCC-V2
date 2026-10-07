"""Server observation of a separately committed Gate 3 schedule pin."""

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class Gate3CaptureSchedulePublicationAck(Base):
    __tablename__ = "gate3_capture_schedule_publication_acks"
    __table_args__ = (
        CheckConstraint(
            "schedule_sha256 ~ '^[a-f0-9]{64}$' "
            "AND seal_sha256 ~ '^[a-f0-9]{64}$' "
            "AND coordinate_plan_sha256 ~ '^[a-f0-9]{64}$' "
            "AND window_key ~ '^[a-f0-9]{64}$'",
            name="hashes",
        ),
        CheckConstraint(
            "schedule_recorded_at <= acknowledged_at "
            "AND acknowledged_at < window_start AND window_start < window_end",
            name="time",
        ),
    )

    schedule_sha256: Mapped[str] = mapped_column(
        String(64),
        ForeignKey(
            "gate3_capture_schedule_pins.schedule_sha256",
            name="fk_gate3_schedule_publication_ack_pin",
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
    acknowledged_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.clock_timestamp()
    )
