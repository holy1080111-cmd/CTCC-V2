"""No SQL: native row-codec/layout and saved-proof refusal contracts."""

import asyncio

import pytest

from scripts import native_owner_retained_rows_v2 as subject
from scripts import retained0017_owner_plan_v1 as original


def _expected():
    return {"alembic_version": (40, "r"), "audit_logs": (41, "r")}


def _layouts():
    return (
        (
            "alembic_version",
            40,
            "r",
            30,
            "p",
            False,
            False,
            False,
            False,
            False,
            [["version_num", 1043, 36, True, 100, "", ""]],
        ),
        (
            "audit_logs",
            41,
            "r",
            30,
            "p",
            False,
            False,
            False,
            False,
            False,
            [["before", 3802, -1, False, 0, "", ""]],
        ),
    )


def test_17_and_owner_upgrade_use_one_identical_source_defined_null_aware_codec():
    columns = ["id", "before", "after"]
    assert original._row_expression(columns) == subject.row_expression_v2(columns)
    size, digest = subject.row_multiset_sql_v2("audit_logs", columns)
    assert subject.row_expression_v2(columns) in str(size)
    assert subject.row_expression_v2(columns) in str(digest)
    assert 'ORDER BY row_sha COLLATE pg_catalog."C"' in str(digest)
    assert "DISTINCT" not in str(digest)


@pytest.mark.parametrize(
    "columns", [[], ["id", "id"], ["id;SELECT private"], [True], ("id",)]
)
def test_codec_refuses_unknown_or_ambiguous_column_shape(columns):
    with pytest.raises(subject.RetainedRowsV2Refused):
        subject.row_expression_v2(columns)


def test_codec_never_invokes_caller_name_conversion_or_iteration():
    called = []

    class Foreign:
        def __str__(self):
            called.append(True)
            return "before"

        def __iter__(self):
            called.append(True)
            return iter(["before"])

    for value in ([Foreign()], Foreign()):
        with pytest.raises(subject.RetainedRowsV2Refused):
            subject.row_expression_v2(value)
    assert called == []


@pytest.mark.parametrize(
    "name", ["audit_logs;SELECT private", None, True, "public.audit_logs"]
)
def test_table_identifier_is_not_arbitrary_sql(name):
    with pytest.raises(subject.RetainedRowsV2Refused):
        subject.row_multiset_sql_v2(name, ["before"])


@pytest.mark.parametrize(
    "relations",
    [
        (),
        (("audit_logs", 41, "r"),),
        (("alembic_version", True, "r"),),
        (("alembic_version", 40, "r"), ("audit_logs", 40, "r")),
        (("alembic_version", 40, "v"),),
        (("alembic_version", 40, "r"), ("alembic_version", 41, "r")),
    ],
)
def test_retained_relation_scope_requires_actual_marker_and_unique_native_ids(
    relations,
):
    with pytest.raises(subject.RetainedRowsV2Refused):
        subject._expected_relations(relations)


def test_original_relation_column_type_nullability_and_order_remain_in_snapshot():
    layouts = subject._checked_layouts(_layouts(), _expected(), 30)
    assert layouts[0] == ["alembic_version", 40, "r", _layouts()[0][-1]]
    assert layouts[1][3][0] == ["before", 3802, -1, False, 0, "", ""]


@pytest.mark.parametrize(
    "index,value",
    [
        (1, 99),
        (2, "p"),
        (3, 99),
        (4, "u"),
        (5, True),
        (6, True),
        (7, True),
        (8, True),
        (9, True),
        (10, []),
    ],
)
def test_native_layout_rejects_hidden_rows_changed_identity_or_missing_columns(
    index, value
):
    row = list(_layouts()[1])
    row[index] = value
    with pytest.raises(subject.RetainedRowsV2Refused):
        subject._checked_layouts((_layouts()[0], tuple(row)), _expected(), 30)


def test_saved_receipt_or_fake_connection_never_opens_a_row_read():
    class Receipt:
        def execute(self, *_):
            pytest.fail("caller receipt cannot become a SQL owner")

    with pytest.raises(subject.RetainedRowsV2Refused):
        asyncio.run(
            subject.snapshot_rows_v2(
                Receipt(),
                (),
                owner_oid=30,
                marker_scope=subject.MarkerScopeV2.EXCLUDE_UPGRADE_MARKER,
                max_rows=10,
                max_table_bytes=1024,
                max_total_bytes=2048,
            )
        )


@pytest.mark.parametrize("oid", [True, 0, 999999, 1009, 2249])
def test_unknown_domains_composites_arrays_or_custom_output_type_refuse_before_row_query(
    oid,
):
    row = list(_layouts()[1])
    columns = [list(row[-1][0])]
    columns[0][1] = oid
    row[-1] = columns
    with pytest.raises(subject.RetainedRowsV2Refused):
        subject._checked_layouts((_layouts()[0], tuple(row)), _expected(), 30)


@pytest.mark.parametrize("oid", sorted(subject._NATIVE_SCALAR_OIDS))
def test_reviewed_native_scalar_column_outputs_are_closed_and_supported(oid):
    row = list(_layouts()[1])
    columns = [list(row[-1][0])]
    columns[0][1] = oid
    row[-1] = columns
    assert subject._checked_layouts((_layouts()[0], tuple(row)), _expected(), 30)


def test_every_json_and_aggregate_call_in_shared_sql_is_explicitly_pg_catalog_bound():
    import re

    size, digest = subject.row_multiset_sql_v2("audit_logs", ["before"])
    for sql in (
        str(size),
        str(digest),
        str(subject._LAYOUT_SQL),
        str(subject._SEQUENCES_SQL),
    ):
        assert not re.search(
            r"(?<!pg_catalog\.)\b(?:to_jsonb|jsonb_build_array|jsonb_agg|string_agg|count|sum)\(",
            sql,
        )


def test_shared_sql_never_resolves_fixed_builtin_casts_through_the_public_search_path():
    import re

    size, digest = subject.row_multiset_sql_v2("audit_logs", ["before"])
    for sql in (
        str(size),
        str(digest),
        str(subject._LAYOUT_SQL),
        str(subject._SEQUENCES_SQL),
    ):
        assert not re.search(r"::(?:text|bigint|jsonb|regclass)\b", sql)
