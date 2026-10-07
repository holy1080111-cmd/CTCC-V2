"""Independently timestamped, immutable Gate 3 pre-window plan pin."""

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class Gate3CaptureSchedulePin(Base):
    __tablename__ = "gate3_capture_schedule_pins"
    __table_args__ = (
        CheckConstraint(
            "schedule_sha256 ~ '^[a-f0-9]{64}$' "
            "AND seal_sha256 ~ '^[a-f0-9]{64}$' "
            "AND coordinate_plan_sha256 ~ '^[a-f0-9]{64}$' "
            "AND window_key ~ '^[a-f0-9]{64}$'",
            name="hashes",
        ),
        CheckConstraint(
            "window_start < window_end AND planned_at < window_start "
            "AND recorded_at >= planned_at AND recorded_at < window_start",
            name="window",
        ),
        CheckConstraint(
            "octet_length(schedule_json) BETWEEN 1 AND 8388608 "
            "AND octet_length(coordinate_plan_json) BETWEEN 1 AND 262144",
            name="payload",
        ),
    )

    schedule_sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    seal_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    coordinate_plan_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    window_key: Mapped[str] = mapped_column(String(64), nullable=False)
    holdout_id: Mapped[str] = mapped_column(String(160), nullable=False)
    window_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    window_end: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    planned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    coordinate_plan_json: Mapped[str] = mapped_column(Text, nullable=False)
    schedule_json: Mapped[str] = mapped_column(Text, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.clock_timestamp()
    )
