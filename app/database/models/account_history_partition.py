"""DB0050 immutable evidence models; neither table is an account capability."""

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKeyConstraint,
    PrimaryKeyConstraint,
    String,
    Table,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.schema import conv

from app.database.base import Base
from app.domain import account_history_partition_catalog_v1 as policy


def _table(name):
    spec = policy.TABLE_SPECS[name]
    columns = []
    for key, dtype, nullable in spec["columns"]:
        if dtype.startswith("character varying("):
            selected = String(
                int(dtype.removeprefix("character varying(").removesuffix(")"))
            )
        else:
            selected = {
                "text": Text(),
                "bigint": BigInteger(),
                "timestamp with time zone": DateTime(timezone=True),
            }[dtype]
        columns.append(
            Column(
                key,
                selected,
                nullable=nullable,
                server_default=text("clock_timestamp()")
                if key == "recorded_at"
                else None,
            )
        )
    constraints = [PrimaryKeyConstraint(*spec["primary_key"], name=conv("pk_" + name))]
    constraints.extend(
        CheckConstraint(expression, name=conv(key))
        for key, expression in spec["checks"].items()
    )
    constraints.extend(
        ForeignKeyConstraint(
            list(keys),
            [target + "." + key for key in target_keys],
            name=conv(key),
            ondelete="RESTRICT",
        )
        for key, keys, target, target_keys in spec["foreign_keys"]
    )
    constraints.extend(
        UniqueConstraint(*keys, name=conv(key)) for key, keys in spec["unique"]
    )
    return Table(name, Base.metadata, *columns, *constraints)


class DemoAccountHistoryNativeMember(Base):
    __tablename__ = policy.TABLES[0]
    __table__ = _table(policy.TABLES[0])


class DemoAccountHistoryPartitionCheckpoint(Base):
    __tablename__ = policy.TABLES[1]
    __table__ = _table(policy.TABLES[1])
