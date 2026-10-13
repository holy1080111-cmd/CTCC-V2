"""Append-only, diagnostic custody of a G12 invocation's preclaim and publication."""

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class G12InvocationClaim(Base):
    __tablename__ = "g12_invocation_claims"
    __table_args__ = (
        UniqueConstraint(
            "environment",
            "account_uid_sha256",
            "event_key",
            name="uq_g12_claim_uid_event",
        ),
        CheckConstraint("environment = 'demo'", name="demo_only"),
        CheckConstraint(
            "invocation_id ~ '^[a-f0-9]{32}$' AND account_uid_sha256 ~ '^[a-f0-9]{64}$' AND event_key ~ '^[a-f0-9]{64}$' AND claim_sha256 ~ '^[a-f0-9]{64}$'",
            name="identity",
        ),
        CheckConstraint("octet_length(claim_json) BETWEEN 1 AND 8192", name="bounded"),
    )

    invocation_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    environment: Mapped[str] = mapped_column(String(8), nullable=False)
    account_uid_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    event_key: Mapped[str] = mapped_column(String(64), nullable=False)
    claim_json: Mapped[str] = mapped_column(Text, nullable=False)
    claim_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    db_claimed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("clock_timestamp()"),
    )


class G12InvocationPublication(Base):
    __tablename__ = "g12_invocation_publications"
    __table_args__ = (
        CheckConstraint(
            "publication_sha256 ~ '^[a-f0-9]{64}$' AND claim_sha256 ~ '^[a-f0-9]{64}$'",
            name="hashes",
        ),
        CheckConstraint(
            "octet_length(publication_json) BETWEEN 1 AND 8192", name="bounded"
        ),
    )

    invocation_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("g12_invocation_claims.invocation_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    claim_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    publication_json: Mapped[str] = mapped_column(Text, nullable=False)
    publication_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    db_recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("clock_timestamp()"),
    )
