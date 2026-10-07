"""Restricted immutable prospective seal and committed-observation metadata."""

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class Gate3PreregistrationSeal(Base):
    __tablename__ = "gate3_preregistration_seals"
    __table_args__ = (
        CheckConstraint(
            "seal_sha256 ~ '^[a-f0-9]{64}$' "
            "AND coordinate_plan_sha256 ~ '^[a-f0-9]{64}$' "
            "AND window_key ~ '^[a-f0-9]{64}$'",
            name="hashes",
        ),
        CheckConstraint(
            "created_at <= recorded_at AND recorded_at < window_start "
            "AND window_start < window_end",
            name="times",
        ),
        CheckConstraint(
            "pg_catalog.octet_length(seal_json) BETWEEN 1 AND 1048576",
            name="payload",
        ),
    )

    seal_sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    preregistration_id: Mapped[str] = mapped_column(String(160), nullable=False)
    coordinate_plan_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    window_key: Mapped[str] = mapped_column(String(64), nullable=False)
    holdout_id: Mapped[str] = mapped_column(String(160), nullable=False)
    window_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    window_end: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    seal_json: Mapped[str] = mapped_column(Text, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.clock_timestamp()
    )


class Gate3PreregistrationSealAck(Base):
    __tablename__ = "gate3_preregistration_seal_acks"
    __table_args__ = (
        CheckConstraint(
            "seal_sha256 ~ '^[a-f0-9]{64}$' "
            "AND coordinate_plan_sha256 ~ '^[a-f0-9]{64}$' "
            "AND window_key ~ '^[a-f0-9]{64}$'",
            name="hashes",
        ),
        CheckConstraint(
            "seal_recorded_at <= acknowledged_at "
            "AND acknowledged_at < window_start "
            "AND window_start < window_end",
            name="times",
        ),
    )

    seal_sha256: Mapped[str] = mapped_column(
        String(64),
        ForeignKey(
            "gate3_preregistration_seals.seal_sha256",
            name="fk_gate3_prereg_seal_ack_seal",
        ),
        primary_key=True,
    )
    preregistration_id: Mapped[str] = mapped_column(
        String(160), nullable=False, unique=True
    )
    coordinate_plan_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    window_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    holdout_id: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    window_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    window_end: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    seal_recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    acknowledged_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.clock_timestamp()
    )
