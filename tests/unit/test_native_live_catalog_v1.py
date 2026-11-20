"""Source catalog expectations, no observed DB schema or execution claims."""

from sqlalchemy import BigInteger, Boolean, DateTime, Numeric, String, Text
from sqlalchemy.dialects.postgresql import OID

from app.database.models import native_live_ledger_v1 as models
from app.database.repositories import native_live_catalog_v1 as subject


def test_exact_source_columns_agree_with_independent_declared_orm_metadata():
    declared = subject.source_columns_v1()
    for model in (
        models.NativeLiveControlV1,
        models.NativeLiveReservationV1,
        models.NativeLiveIntentV1,
        models.NativeLiveTransitionV1,
    ):
        expected = []
        for column in model.__table__.columns:
            kind = column.type
            if type(kind) is String:
                oid, modifier = 1043, kind.length + 4
            elif type(kind) is Numeric:
                oid, modifier = 1700, (kind.precision << 16) + kind.scale + 4
            else:
                oid, modifier = {
                    BigInteger: (20, -1),
                    Boolean: (16, -1),
                    DateTime: (1184, -1),
                    Text: (25, -1),
                    OID: (26, -1),
                }[type(kind)]
            default = (
                None
                if column.server_default is None
                else str(column.server_default.arg)
            )
            expected.append((column.name, oid, modifier, not column.nullable, default))
        assert declared[model.__tablename__] == tuple(expected)


def test_source_sql_split_retains_inline_columns_and_quoted_check_commas():
    parts = subject._parts(
        "CREATE TABLE public.example (a varchar(40) NOT NULL, b numeric(60,20), CHECK(a IN ('x,y','z'))) "
    )
    assert parts == (
        "a varchar(40) NOT NULL",
        "b numeric(60,20)",
        "CHECK(a IN ('x,y','z'))",
    )


def test_check_plan_normalization_preserves_security_relevant_predicate():
    left = [{"Plan": {"Filter": "(risk > 0)", "Total Cost": 10}}]
    same = [{"Plan": {"Filter": "(risk > 0)", "Total Cost": 20}}]
    changed = [{"Plan": {"Filter": "(risk >= 0)", "Total Cost": 10}}]
    assert subject._plan_shape(left) == subject._plan_shape(same)
    assert subject._plan_shape(left) != subject._plan_shape(changed)
