"""Closed producer typing contracts; actual PG SQL/type evidence is separate."""

import copy
import re

import pytest

from scripts import retained0017_catalog_baseline_v1 as subject
from scripts import retained0017_catalog_sql_types_v1 as types
from tests.unit import test_native_retained0017_catalog_data_v1 as data

data_pair = data.data_pair


@pytest.mark.parametrize("catalog", tuple(types.SCALAR_OIDS))
def test_sql_projection_uses_only_fixed_native_casts_and_explicit_dimensions(catalog):
    sql = types.catalog_row_json_v1(catalog, "fixed")
    assert sql.startswith(
        "(pg_catalog.to_jsonb(fixed) || pg_catalog.jsonb_build_object("
    )
    for field in types.SCALAR_OIDS[catalog]:
        assert (
            "'" + field + "',fixed." + field + "::pg_catalog.oid::pg_catalog.int8"
            in sql
        )
    for field in types.OID_ARRAYS.get(catalog, ()):
        vector = "::pg_catalog.oid[]" if (catalog, field) in types._VECTORS else ""
        assert "'" + field + "',fixed." + field + vector + "::pg_catalog.int8[]" in sql
    for field in types.ARRAYS[catalog]:
        assert "'" + field + "',pg_catalog.array_dims(fixed." + field in sql
    assert "COALESCE" not in sql and "CASE " not in sql and "CAST(:" not in sql


@pytest.mark.parametrize(
    ("catalog", "alias"),
    (
        ("caller_table", "c"),
        ("pg_class", "c); SELECT private"),
        ("pg_class", "c.field"),
        ("pg_class", "c\n--"),
        ("pg_class", True),
        (None, "c"),
    ),
)
def test_unknown_catalog_or_alias_can_never_select_a_user_field(catalog, alias):
    with pytest.raises(ValueError, match="^retained0017_catalog_typed_sql_refused$"):
        types.catalog_row_json_v1(catalog, alias)


def test_every_capture_placeholder_is_bound_before_sql_can_execute():
    assert re.search(r"__[A-Z_]+__", subject.CATALOG_SQL) is None
    assert (
        "parent_oid::pg_catalog.int8,toast_oid::pg_catalog.int8" in subject.CATALOG_SQL
    )
    assert subject.CATALOG_SQL.count("'" + types.ARRAY_DIMS_FIELD + "'") == 19


@pytest.mark.parametrize("field", ("proargtypes", "proallargtypes", "protrftypes"))
@pytest.mark.parametrize("value", ("26", 26.0, True, -1))
def test_source_oid_arrays_refuse_strings_floats_booleans_and_negative_values(
    data_pair, field, value
):
    first, _ = data_pair
    changed = copy.deepcopy(first)
    row = next(row for kind, row, _extra in changed["entries"] if kind == "function")
    row[field] = [value]
    row[types.ARRAY_DIMS_FIELD][field] = "[0:0]" if field == "proargtypes" else "[1:1]"
    with pytest.raises(subject.Retained0017CatalogRefused):
        subject.normalize_catalog17_v1(changed)


def test_scalar_oid_json_strings_remain_refused_instead_of_coerced(data_pair):
    first, _ = data_pair
    changed = copy.deepcopy(first)
    data._row(changed, "relation", name="declared_fixture")["oid"] = "20001"
    with pytest.raises(subject.Retained0017CatalogRefused):
        subject.normalize_catalog17_v1(changed)


def test_nondefault_array_bounds_are_retained_in_normalized_definition(data_pair):
    first, _ = data_pair
    changed = copy.deepcopy(first)
    row = data._row(changed, "constraint")
    row[types.ARRAY_DIMS_FIELD]["conkey"] = "[0:0]"
    assert subject.normalize_catalog17_v1(changed) != subject.normalize_catalog17_v1(
        first
    )


@pytest.mark.parametrize(
    "fault",
    ("missing_map", "missing_field", "extra_field", "invalid_bound", "missing_value"),
)
def test_every_array_dimension_field_is_explicit_and_cannot_disappear(data_pair, fault):
    first, _ = data_pair
    changed = copy.deepcopy(first)
    row = data._row(changed, "relation", name="declared_fixture")
    if fault == "missing_map":
        row.pop(types.ARRAY_DIMS_FIELD)
    elif fault == "missing_field":
        row[types.ARRAY_DIMS_FIELD].pop("reloptions")
    elif fault == "extra_field":
        row[types.ARRAY_DIMS_FIELD]["unreviewed"] = None
    elif fault == "invalid_bound":
        row[types.ARRAY_DIMS_FIELD]["relacl"] = "one-based"
    else:
        row.pop("reloptions")
    with pytest.raises(subject.Retained0017CatalogRefused):
        subject.normalize_catalog17_v1(changed)
