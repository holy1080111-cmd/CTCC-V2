"""Private append-only account observations, independent of account revisions."""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    LargeBinary,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class DemoAccountCaptureEvent(Base):
    __tablename__ = "demo_account_capture_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["environment", "account_id", "settlement_currency"],
            [
                "qualification_account_scopes.environment",
                "qualification_account_scopes.account_id",
                "qualification_account_scopes.settlement_currency",
            ],
            ondelete="RESTRICT",
            name="fk_account_capture_scope",
        ),
        CheckConstraint(
            "environment = 'demo' AND account_id ~ '^[0-9]{1,32}$' AND settlement_currency ~ '^[A-Z0-9]{1,16}$'",
            name="scope",
        ),
        CheckConstraint(
            "capture_id ~ '^[a-f0-9]{32}$' AND sequence BETWEEN 1 AND 8192",
            name="identity",
        ),
        CheckConstraint(
            "event_sha256 ~ '^[a-f0-9]{64}$' AND ((sequence=1 AND previous_sha256 IS NULL) OR (sequence>1 AND previous_sha256 ~ '^[a-f0-9]{64}$'))",
            name="chain",
        ),
        CheckConstraint(
            "octet_length(event_json) BETWEEN 1 AND 262144", name="event_bound"
        ),
        CheckConstraint(
            "raw_body IS NULL OR octet_length(raw_body) BETWEEN 1 AND 262144",
            name="raw_bound",
        ),
        CheckConstraint(
            "packet_payload IS NULL OR octet_length(packet_payload) BETWEEN 1 AND 16777216",
            name="packet_bound",
        ),
        CheckConstraint("chain_bytes BETWEEN 1 AND 67108864", name="chain_byte_bound"),
    )
    capture_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    sequence: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    environment: Mapped[str] = mapped_column(String(8), nullable=False)
    account_id: Mapped[str] = mapped_column(String(32), nullable=False)
    settlement_currency: Mapped[str] = mapped_column(String(16), nullable=False)
    previous_sha256: Mapped[str | None] = mapped_column(String(64))
    chain_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    event_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    event_json: Mapped[str] = mapped_column(Text, nullable=False)
    raw_body: Mapped[bytes | None] = mapped_column(LargeBinary)
    packet_payload: Mapped[bytes | None] = mapped_column(LargeBinary)
    db_recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.clock_timestamp()
    )
