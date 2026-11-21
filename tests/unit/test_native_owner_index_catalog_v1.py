"""Source artifact safety and fixed native-index admission contracts, no SQL."""

import hashlib
import json
import re
from pathlib import Path

import pytest

from scripts import native_owner_heap_am_v1 as heap
from scripts import native_owner_index_catalog_v1 as subject
from scripts import native_owner_retained_rows_v2 as rows
from scripts import native_owner_upgrade_0056_v1 as owner55
from scripts import retained0017_owner_plan_v1 as owner17


def test_source_facts_have_exact_primary_bootstrap_identity_and_no_observed_catalog():
    raw = (
        Path(subject.__file__)
        .with_name("native_owner_pg17_btree_facts_v1.json")
        .read_bytes()
    )
    assert hashlib.sha256(raw).hexdigest() == subject._FACTS_SHA256
    facts = json.loads(raw)
    assert facts["source_tag"] == "REL_17_11"
    assert set(facts["primary_sha256"]) == {
        "pg_amop",
        "pg_amproc",
        "pg_opclass",
        "pg_opfamily",
        "pg_operator",
        "pg_proc",
        "pg_type",
        "pg_type.h",
        "pg_collation",
        "primnodes.h",
        "pg_config_manual.h",
        "execExpr.c",
    }
    assert all(
        re.fullmatch("[0-9a-f]{64}", value)
        for value in facts["primary_sha256"].values()
    )
    assert len(facts["classes"]) == 17 and len(facts["amproc"]) == 77
    assert len(facts["amop"]) == 170 and len(facts["operators"]) == 205
    assert len(facts["procedures"]) == 389
    assert len(facts["types"]) == 24
    assert {47, 3805, 3806} <= {row[0] for row in facts["procedures"]}
    handlers = {row[0]: row for row in facts["procedures"] if row[0] in (3, 330)}
    assert handlers[3][1:5] == [
        "heap_tableam_handler",
        "2281",
        269,
        "heap_tableam_handler",
    ]
    assert handlers[330][1:5] == ["bthandler", "2281", 325, "bthandler"]
    assert next(row for row in facts["procedures"] if row[0] == 3214)[-1] == [
        "from_json",
        "field_name",
    ]


def test_source_name_and_unknown_constants_have_exact_type_and_internal_io_profiles():
    facts = subject._source_facts()
    types = {row[0]: row for row in facts["types"]}
    assert types[19] == [19, "name", 18, 34, 35, 2422, 2423, 0, 0, 0, 6180]
    assert types[705] == [705, "unknown", 0, 109, 110, 2416, 2417, 0, 0, 0, 0]
    assert facts["constant_only_types"] == [
        [19, 64, False, "b", "S", False, ",", "c", "p", 950, -1, 0],
        [705, -2, False, "p", "X", False, ",", "c", "p", 0, -1, 0],
    ]
    procedures = {row[0]: row for row in facts["procedures"]}
    for oid, name, argument, result, volatility in (
        (34, "namein", "2275", 19, "i"),
        (35, "nameout", "19", 2275, "i"),
        (2422, "namerecv", "2281", 19, "s"),
        (2423, "namesend", "19", 17, "s"),
        (109, "unknownin", "2275", 705, "i"),
        (110, "unknownout", "705", 2275, "i"),
        (2416, "unknownrecv", "2281", 705, "i"),
        (2417, "unknownsend", "705", 17, "i"),
        (6180, "raw_array_subscript_handler", "2281", 2281, "i"),
    ):
        assert procedures[oid] == [
            oid,
            name,
            argument,
            result,
            name,
            volatility,
            "s",
            True,
            False,
            False,
            False,
            0,
            0,
            0,
            "1",
            "0",
            None,
        ]
    sql = subject.NATIVE_BTREE_CATALOG_PREDICATE_V1
    assert "CASE WHEN t.oid=705 THEN 'p' ELSE 'b' END" in sql
    assert "t.oid IN (19,705)" in sql
    assert "t.typstorage" in sql and "t.typcollation" in sql
    # The two source CONST exceptions do not become application row types.
    assert not ({19, 705} & set(heap.NATIVE_SCALAR_OIDS_V1))
    assert 2275 not in types and 2281 not in types


def test_array_coerce_wrapper_keeps_every_original_child_and_callback_fence():
    facts = subject._source_facts()
    assert facts["predicate_node_fields"] == {
        "ARRAYCOERCEEXPR": [
            "arg",
            "elemexpr",
            "resulttype",
            "resulttypmod",
            "resultcollid",
            "coerceformat",
            "location",
        ],
        "CASETESTEXPR": ["typeId", "typeMod", "collation"],
    }
    sql = subject.NATIVE_BTREE_CATALOG_PREDICATE_V1
    assert "'ARRAYCOERCEEXPR'" in sql
    assert "'CASETESTEXPR'" in sql and "'FUNCEXPR'" not in sql
    for original in (
        "vartype|consttype|opresulttype|resulttype|array_typeid|element_typeid",
        "[:]opno ([0-9]+)",
        "[:]opfuncid ([0-9]+)",
        "varcollid|constcollid|opcollid|inputcollid|resultcollid|array_collid",
        "hashfuncid|negfuncid|varlevelsup",
        "pg_catalog.pg_depend",
    ):
        assert original in sql


def test_changed_or_additional_array_node_field_contract_is_refused(monkeypatch):
    facts = subject._source_facts()
    facts["predicate_node_fields"]["ARRAYCOERCEEXPR"].append("callback")
    monkeypatch.setattr(subject, "_source_facts", lambda: facts)
    with pytest.raises(ValueError, match="native_owner_btree_source_refused"):
        subject._build_source_predicate()


def test_case_test_context_value_has_only_source_fields_and_no_callback_slot(
    monkeypatch,
):
    facts = subject._source_facts()
    assert facts["predicate_node_fields"]["CASETESTEXPR"] == [
        "typeId",
        "typeMod",
        "collation",
    ]
    assert (
        facts["primary_sha256"]["execExpr.c"]
        == "f9cf5e2e83d5c096bd769bbde808bf3322e797ac13a1c89b31ba680600224acd"
    )
    facts["predicate_node_fields"]["CASETESTEXPR"].append("callback")
    monkeypatch.setattr(subject, "_source_facts", lambda: facts)
    with pytest.raises(ValueError, match="native_owner_btree_source_refused"):
        subject._build_source_predicate()


@pytest.mark.parametrize(
    "mutation", ["extra", "missing", "child", "duplicate", "wrong_name"]
)
def test_case_test_malformed_leaf_cannot_pass_the_actual_source_shape_filter(mutation):
    from scripts import native_owner_storage_diagnostic_v1 as diagnostic

    condition = diagnostic._index_rejection_parts_v2()[-1][0]
    pattern = re.search(r"'(\\\{CASETESTEXPR \[:\]typeId[^']+)'", condition).group(1)
    tag = re.search(r"'(\\\{CASETESTEXPR)'", condition).group(1)
    original = "{CASETESTEXPR :typeId 1043 :typeMod -1 :collation 100}"
    assert len(re.findall(tag, original)) == len(re.findall(pattern, original)) == 1
    changed = {
        "extra": original[:-1] + " :funcid 99999}",
        "missing": original.replace(" :typeMod -1", ""),
        "child": original[:-1] + " :arg {VAR :vartype 25}}",
        "duplicate": original[:-1] + " :typeId 25}",
        "wrong_name": original.replace("typeId", "typeid"),
    }[mutation]
    assert len(re.findall(tag, changed)) != len(re.findall(pattern, changed))


@pytest.mark.parametrize("oid", [19, 705, 99999])
def test_case_test_type_uses_original_index_fence_not_new_const_exceptions(oid):
    from scripts import native_owner_storage_diagnostic_v1 as diagnostic

    condition = diagnostic._index_rejection_parts_v2()[-1][1]
    pattern = re.search(r"'(\:\(vartype[^']+)'", condition).group(1)
    allowed = {
        int(value)
        for value in re.search(r"NOT IN \(([0-9,]+)\)", condition).group(1).split(",")
    }
    assert allowed == set(heap.NATIVE_SCALAR_OIDS_V1) | {1009, 1015}
    value = re.search(pattern, ":typeId " + str(oid))
    assert value is not None and int(value.group(2)) not in allowed


def test_case_test_collation_uses_original_native_collation_fence():
    from scripts import native_owner_storage_diagnostic_v1 as diagnostic

    condition = diagnostic._index_rejection_parts_v2()[-1][4]
    pattern = re.search(r"'(\:\(varcollid[^']+)'", condition).group(1)
    allowed = {
        int(value)
        for value in re.search(r"NOT IN \(([0-9,]+)\)", condition).group(1).split(",")
    }
    assert allowed == {0, 100}
    assert int(re.search(pattern, ":collation 99999").group(2)) not in allowed


@pytest.mark.parametrize(
    "value, refused",
    [
        ("-2147483648", False),
        ("2147483647", False),
        ("-1", False),
        ("0", False),
        ("-2147483649", True),
        ("2147483648", True),
        ("99999999999", True),
        ("9" * 1000, True),
        ("-" + "9" * 1000, True),
    ],
)
def test_case_test_typemod_has_source_signed_int32_bounds_before_any_cast(
    value, refused
):
    from scripts import native_owner_storage_diagnostic_v1 as diagnostic

    condition = diagnostic._index_rejection_parts_v2()[-1][0]
    pattern = re.search(r"'(\[:\]typeMod [^']+)'", condition).group(1)
    captured = re.search(pattern, ":typeMod " + value).group(1)
    limit = int(re.search(r"length\(tm.parts\[1\]\)<=([0-9]+)", condition).group(1))
    lower, upper = map(
        int, re.search(r"NOT BETWEEN (-?[0-9]+) AND ([0-9]+)", condition).groups()
    )
    assert limit == 11 and (lower, upper) == (-2147483648, 2147483647)
    assert "WHERE CASE WHEN" in condition and "ELSE true END" in condition
    cast = []
    if len(captured) <= limit:
        cast.append(True)
        actual = not lower <= int(captured) <= upper
    else:
        actual = True
    assert actual is refused
    assert cast == ([True] if len(value) <= 11 else [])


@pytest.mark.parametrize("field", ["vartype", "resulttype", "array_typeid"])
@pytest.mark.parametrize("oid", [19, 705])
def test_const_only_exceptions_remain_rejected_in_original_index_type_fields(
    field, oid
):
    from scripts import native_owner_storage_diagnostic_v1 as diagnostic

    _prefix, _parts, _partial, predicates = diagnostic._index_rejection_parts_v2()
    # Extract the actual original partial-index field regex and its independent
    # fixed limit. The facts-derived general CONST set is never used here.
    original_type_fence = predicates[1]
    pattern = re.search(r"'(\:\(vartype[^']+)'", original_type_fence).group(1)
    limits = re.search(r"NOT IN \(([0-9,]+)\)", original_type_fence).group(1)
    allowed = {int(value) for value in limits.split(",")}
    assert allowed == set(heap.NATIVE_SCALAR_OIDS_V1) | {1009, 1015}
    match = re.search(pattern, ":" + field + " " + str(oid))
    assert match is not None and match.group(1) == field
    assert int(match.group(2)) not in allowed


def test_all_version_and_shared_row_reads_bind_one_source_closed_index_predicate():
    expected = subject.NATIVE_BTREE_CATALOG_PREDICATE_V1
    for actual in (
        heap.NATIVE_MARKER_AND_HEAP_SQL_V1,
        str(owner55._SAFE_MARKER_V2),
        str(owner17.SAFE_ROW_TYPES_SQL),
        str(rows._LAYOUT_SQL),
    ):
        assert expected in actual
    for foreign_call in (
        "pg_get_expr",
        "pg_get_indexdef",
        "pg_get_functiondef",
        "pg_relation_size",
        "FROM public.",
    ):
        assert foreign_call not in expected
    assert not re.search(
        r"\b(?:bthandler|heap_tableam_handler|bttextcmp)\s*\(", expected
    )
    assert "i.indexprs IS NOT NULL" in expected
    assert "c.opcnamespace<>11" in expected and "c.opcfamily NOT IN" in expected
    assert (
        "p.prosrc" in expected and "o.oprrest" in expected and "o.oprjoin" in expected
    )
    assert "pg_catalog.pg_amproc" in expected and "pg_catalog.pg_amop" in expected
    assert (
        "pg_catalog.pg_depend" in expected and "pg_catalog.regexp_matches" in expected
    )
    assert "'FUNCEXPR'" not in expected
    from sqlalchemy import text

    assert not text(expected)._bindparams


def test_fixed_catalog_query_is_lexically_balanced_outside_sql_literals():
    # Supporting construction regression only. The actual-PG TRUE/FALSE
    # collation case is the grammar and grouping acceptance test.
    sql = subject.NATIVE_BTREE_CATALOG_PREDICATE_V1
    stack = []
    quoted = False
    index = 0
    while index < len(sql):
        value = sql[index]
        if value == "'":
            if quoted and index + 1 < len(sql) and sql[index + 1] == "'":
                index += 2
                continue
            quoted = not quoted
        elif not quoted and value == "(":
            stack.append(index)
        elif not quoted and value == ")":
            assert stack
            stack.pop()
        index += 1
    assert not quoted and not stack


@pytest.mark.parametrize("tamper", ["bytes", "link_count", "reparse"])
def test_source_fact_file_drift_or_external_alias_is_refused_before_parsing(
    monkeypatch, tamper
):
    called = []
    original = subject.os.read
    if tamper == "bytes":
        monkeypatch.setattr(subject.os, "read", lambda *args: original(*args) + b" ")
    else:
        from types import SimpleNamespace

        old = subject.Path.lstat

        def changed(path):
            actual = old(path)
            return SimpleNamespace(
                st_mode=actual.st_mode,
                st_nlink=2 if tamper == "link_count" else 1,
                st_file_attributes=0x400 if tamper == "reparse" else 0,
            )

        monkeypatch.setattr(subject.Path, "lstat", changed)
    monkeypatch.setattr(subject.json, "loads", lambda *args: called.append(True))
    with pytest.raises(ValueError, match="native_owner_btree_source_refused"):
        subject._source_facts()
    assert called == []
