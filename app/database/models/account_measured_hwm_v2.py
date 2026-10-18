"""Immutable sampled-balance continuation; never an account-risk authority."""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    String,
    Text,
    false,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class DemoAccountMeasuredHWMV2(Base):
    __tablename__ = "demo_account_measured_hwm_v2"
    __table_args__ = (
        ForeignKeyConstraint(
            ["environment", "account_id", "settlement_currency", "sequence"],
            [
                "demo_account_observation_batches.environment",
                "demo_account_observation_batches.account_id",
                "demo_account_observation_batches.settlement_currency",
                "demo_account_observation_batches.sequence",
            ],
            ondelete="RESTRICT",
            name="fk_hwm_v2_observation",
        ),
        ForeignKeyConstraint(
            ["capture_id", "source_sequence"],
            [
                "demo_account_capture_events.capture_id",
                "demo_account_capture_events.sequence",
            ],
            ondelete="RESTRICT",
            name="fk_hwm_v2_source",
        ),
        CheckConstraint(
            "environment='demo' AND account_id ~ '^[0-9]{1,32}$' "
            "AND settlement_currency ~ '^[A-Z0-9]{1,16}$'",
            name="scope",
        ),
        CheckConstraint(
            "sequence BETWEEN 1 AND 9223372036854775807 "
            "AND source_sequence BETWEEN 1 AND 8192 AND genesis_sequence=1 "
            "AND peak_sequence BETWEEN 1 AND sequence",
            name="sequence",
        ),
        CheckConstraint(
            "event_sha256 ~ '^[a-f0-9]{64}$' "
            "AND measurement_sha256 ~ '^[a-f0-9]{64}$' "
            "AND projection_sha256 ~ '^[a-f0-9]{64}$' "
            "AND ((sequence=1 AND previous_event_sha256 IS NULL "
            "AND previous_projection_sha256 IS NULL) OR "
            "(sequence>1 AND previous_event_sha256 ~ '^[a-f0-9]{64}$' "
            "AND previous_projection_sha256 ~ '^[a-f0-9]{64}$'))",
            name="digests",
        ),
        CheckConstraint(
            "octet_length(measurement_json) BETWEEN 1 AND 1048576 "
            "AND octet_length(projection_json) BETWEEN 1 AND 1048576",
            name="bytes",
        ),
        CheckConstraint(
            "account_complete=false AND execution_authority=false AND admission='DENY'",
            name="no_authority",
        ),
    )

    environment: Mapped[str] = mapped_column(String(8), primary_key=True)
    account_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    settlement_currency: Mapped[str] = mapped_column(String(16), primary_key=True)
    sequence: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    capture_id: Mapped[str] = mapped_column(String(32), nullable=False)
    source_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    event_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    previous_event_sha256: Mapped[str | None] = mapped_column(String(64))
    measurement_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    measurement_json: Mapped[str] = mapped_column(Text, nullable=False)
    genesis_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    peak_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    previous_projection_sha256: Mapped[str | None] = mapped_column(String(64))
    projection_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    projection_json: Mapped[str] = mapped_column(Text, nullable=False)
    account_complete: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=false()
    )
    execution_authority: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=false()
    )
    admission: Mapped[str] = mapped_column(
        String(8), nullable=False, server_default=text("'DENY'")
    )
    db_recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.clock_timestamp()
    )
