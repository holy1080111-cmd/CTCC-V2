"""Live-only financial ORM; registration and owner migration are explicit."""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.schema import conv

from app.database.base import Base
from app.domain import native_live_financial_epoch_schema_v1 as spec


def _constraints(table):
    return (
        *(
            CheckConstraint(expression, name=conv(name))
            for name, expression in spec.TABLE_CHECKS[table]
        ),
        *(
            UniqueConstraint(*columns, name=conv(name))
            for name, columns in spec.TABLE_UNIQUES[table]
        ),
    )


class NativeLiveFinancialEpochV1(Base):
    __tablename__ = spec.TABLES[0]
    __table_args__ = _constraints(__tablename__)
    epoch_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    environment: Mapped[str] = mapped_column(String(4))
    account_uid: Mapped[str] = mapped_column(String(40))
    main_uid: Mapped[str] = mapped_column(String(40))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    policy_sha256: Mapped[str] = mapped_column(String(64))
    epoch_json: Mapped[str] = mapped_column(Text)
    epoch_sha256: Mapped[str] = mapped_column(String(64))


class NativeLiveFinancialSampleV1(Base):
    __tablename__ = spec.TABLES[1]
    __table_args__ = _constraints(__tablename__)
    epoch_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey(
            "native_live_financial_epochs_v1.epoch_id",
            name=conv("fk_native_live_financial_samples_v1_epoch"),
            ondelete="RESTRICT",
        ),
        primary_key=True,
    )
    sequence: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    previous_sha256: Mapped[str | None] = mapped_column(String(64))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source_revision_sha256: Mapped[str] = mapped_column(String(64))
    equity: Mapped[Decimal] = mapped_column(Numeric(60, 20))
    sample_json: Mapped[str] = mapped_column(Text)
    sample_sha256: Mapped[str] = mapped_column(String(64))
