"""Restricted application-role custody; no historical or trading authority."""

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Integer,
    LargeBinary,
    String,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class PublicRuntimeRawBundleV1(Base):
    __tablename__ = "public_runtime_raw_bundles_v1"
    __table_args__ = (
        CheckConstraint(
            "bundle_id ~ '^[a-f0-9]{32}$' AND bundle_sha256 ~ '^[a-f0-9]{64}$' "
            "AND proof_envelope_sha256 ~ '^[a-f0-9]{64}$'",
            name="identity",
        ),
        CheckConstraint(
            "contract_version=1 AND octet_length(bundle_raw) BETWEEN 1 AND 67108864 "
            "AND encode(sha256(bundle_raw),'hex')=bundle_sha256",
            name="content",
        ),
    )
    bundle_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    bundle_sha256: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    proof_envelope_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    bundle_raw: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    contract_version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1")
    )
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.clock_timestamp()
    )
