"""Append-only Live close custody; root model-registry/migration binding is explicit."""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.schema import conv

from app.database.base import Base
from app.domain import native_live_controlled_close_schema_v1 as spec


def _constraints(table):
    return (
        *(
            CheckConstraint(expr, name=conv(name))
            for name, expr in spec.TABLE_CHECKS[table]
        ),
        *(
            UniqueConstraint(*fields, name=conv(name))
            for name, fields in spec.TABLE_UNIQUES[table]
        ),
        *(
            ForeignKeyConstraint(
                fields,
                [parent + "." + column for column in parent_fields],
                name=conv(name),
                ondelete="RESTRICT",
            )
            for (child, name), (
                fields,
                parent,
                parent_fields,
            ) in spec.SOURCE_FOREIGN_KEYS.items()
            if child == table
        ),
    )


class NativeLiveControlledCloseIntentV1(Base):
    __tablename__ = spec.TABLES[0]
    __table_args__ = _constraints(__tablename__)
    close_intent_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    reservation_id: Mapped[str] = mapped_column(String(64))
    environment: Mapped[str] = mapped_column(String(4))
    account_uid: Mapped[str] = mapped_column(String(40))
    key_id: Mapped[str] = mapped_column(String(64))
    close_client_order_id: Mapped[str] = mapped_column(String(32))
    original_intent_sha256: Mapped[str] = mapped_column(String(64))
    consent_sha256: Mapped[str] = mapped_column(String(64))
    signature: Mapped[str] = mapped_column(String(64))
    expected_control_revision: Mapped[int] = mapped_column(BigInteger)
    consent_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    close_intent_json: Mapped[str] = mapped_column(Text)
    close_intent_sha256: Mapped[str] = mapped_column(String(64))
    request_json: Mapped[str] = mapped_column(Text)
    request_sha256: Mapped[str] = mapped_column(String(64))
    close_deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("clock_timestamp()")
    )


class NativeLiveControlledCloseClaimV1(Base):
    __tablename__ = spec.TABLES[1]
    __table_args__ = _constraints(__tablename__)
    close_intent_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    action: Mapped[str] = mapped_column(String(32), primary_key=True)
    signature: Mapped[str] = mapped_column(String(64))
    claim_json: Mapped[str] = mapped_column(Text)
    request_json: Mapped[str] = mapped_column(Text)
    request_sha256: Mapped[str] = mapped_column(String(64))
    request_deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    claim_sha256: Mapped[str] = mapped_column(String(64))
    request_wire_sha256: Mapped[str] = mapped_column(String(64))
    claimed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("clock_timestamp()")
    )
