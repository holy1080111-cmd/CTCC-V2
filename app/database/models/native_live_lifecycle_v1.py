"""Live lifecycle custody and append-only proofs; explicit registry integration."""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import OID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.schema import conv

from app.database.base import Base
from app.domain import native_live_lifecycle_schema_v1 as spec


def _constraints(table):
    return (
        *(
            CheckConstraint(expression, name=conv(name))
            for name, expression in spec.TABLE_CHECKS[table]
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


class NativeLiveLifecycleKeyV1(Base):
    __tablename__ = spec.TABLES[0]
    __table_args__ = _constraints(__tablename__)
    environment: Mapped[str] = mapped_column(String(4), primary_key=True)
    account_uid: Mapped[str] = mapped_column(String(40), primary_key=True)
    main_uid: Mapped[str] = mapped_column(String(40))
    database_oid: Mapped[int] = mapped_column(OID)
    owner_oid: Mapped[int] = mapped_column(OID)
    reconciler_oid: Mapped[int] = mapped_column(OID)
    origin: Mapped[str] = mapped_column(String(64))
    key_id: Mapped[str] = mapped_column(String(64))
    key_material: Mapped[bytes] = mapped_column(LargeBinary)
    key_material_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("clock_timestamp()")
    )


class NativeLiveLifecycleProofV1(Base):
    __tablename__ = spec.TABLES[1]
    __table_args__ = _constraints(__tablename__)
    reservation_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    sequence: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    environment: Mapped[str] = mapped_column(String(4))
    account_uid: Mapped[str] = mapped_column(String(40))
    key_id: Mapped[str] = mapped_column(String(64))
    previous_sha256: Mapped[str | None] = mapped_column(String(64))
    proof_json: Mapped[str] = mapped_column(Text)
    proof_sha256: Mapped[str] = mapped_column(String(64))
    signature: Mapped[str] = mapped_column(String(64))
    outcome: Mapped[str] = mapped_column(String(32))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("clock_timestamp()")
    )
