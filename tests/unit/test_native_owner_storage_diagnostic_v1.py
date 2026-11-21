"""Source lexer/report tests only; no SQL, provider or runtime acceptance."""

import ast
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from sqlalchemy import text

from scripts import current0058_collection_v1 as collection
from scripts import current0058_full_hermetic_contract_v1 as full
from scripts import native_owner_storage_diagnostic_v1 as diagnostic
from scripts import run_retained0017_reference_probe_v1 as runner
from scripts import verify_final_hermetic as reports
from scripts import verify_windows_regression as windows

ROOT = Path(__file__).resolve().parents[2]


def test_source_components_reconstruct_original_combined_predicate_exactly():
    queries = dict(diagnostic.queries_v1())
    assert len(queries) == 20
    assert queries["combined"] == diagnostic.heap.NATIVE_MARKER_AND_HEAP_SQL_V1
    assert (
        queries["btree_combined"]
        == "SELECT " + diagnostic.index.NATIVE_BTREE_CATALOG_PREDICATE_V1.strip()
    )
    original = diagnostic.heap.ALL_PUBLIC_TABLES_HEAP_V1
    assert "AND".join(diagnostic._pieces(original)) == original
    assert all(statement.startswith("SELECT ") for statement in queries.values())
    # The observed marker is never read; the only marker query is metadata.
    assert all("FROM public." not in statement for statement in queries.values())


def test_lexer_preserves_quoted_and_nested_and_without_treating_them_as_boundaries():
    source = " a='AND''AND' AND (b AND c) AND \"AND\"='x' "
    parts = diagnostic._pieces(source)
    assert parts == (" a='AND''AND' ", " (b AND c) ", " \"AND\"='x' ")
    assert "AND".join(parts) == source


def test_secondary_queries_preserve_source_rejection_filters_and_catalog_scope():
    number_sql, categories = diagnostic.detail_queries_v2()
    assert number_sql.lstrip().startswith("SELECT DISTINCT m.parts[1]::pg_catalog.int8")
    assert "NOT IN (" in number_sql and number_sql.endswith(" ORDER BY 1")
    assert len(categories) == 10 and len(dict(categories)) == 10
    assert all(statement.startswith("SELECT EXISTS(") for _, statement in categories)
    assert all("FROM public." not in statement for _, statement in categories)
    source = "a='OR''AND' OR (b OR c) OR ORDERED"
    parts = diagnostic._pieces(source, "OR")
    assert parts == ("a='OR''AND' ", " (b OR c) ", " ORDERED")
    assert "OR".join(parts) == source


def test_secondary_source_queries_have_no_accidental_sqlalchemy_bind_parameters():
    number_sql, categories = diagnostic.detail_queries_v2()
    for statement in (number_sql, *(sql for _, sql in categories)):
        diagnostic._scan(statement)
        assert not text(statement)._bindparams


@pytest.mark.parametrize(
    "source",
    ["a AND", "(a AND b", "a AND 'b", "a -- AND b", "a /* x */ AND b", ") AND a"],
)
def test_source_lexer_refuses_ambiguous_or_unbalanced_input(source):
    with pytest.raises(ValueError, match="native_storage_diagnostic_refused"):
        diagnostic._pieces(source)


def _write_reports(root, change=None):
    source = {"commit": "a" * 40, "tree": "b" * 40, "manifest_sha256": "c" * 64}
    folder = root / "test-results"
    folder.mkdir()
    for target in ("0055", "0057"):
        document = {
            "schema": diagnostic.COMPONENT_SCHEMA,
            "target": target,
            "source_commit": source["commit"],
            "source_tree": source["tree"],
            "source_manifest_sha256": source["manifest_sha256"],
            "predicate_sha256": diagnostic.predicate_sha256_v1(),
            "groups": {name: True for name, _ in diagnostic.queries_v1()},
            "startup_admission": False,
            "schema_admission": False,
            "execution_authority": False,
            "original_combined_predicate_used": True,
            "component_conjunction_matches_original": True,
            "rejected_constant_type_ids": [],
            "index_rejection_categories": {
                name: False for name, _ in diagnostic.detail_queries_v2()[1]
            },
            "detail_matches_original_groups": True,
        }
        if target == "0055" and change:
            change(document)
        (folder / ("native-storage-" + target + ".json")).write_text(
            json.dumps(document), encoding="ascii"
        )
    return source


def test_all_true_report_remains_deny_and_never_becomes_schema_or_reference_admission(
    tmp_path,
):
    source = _write_reports(tmp_path)
    result = diagnostic.verify_reports_v1(tmp_path, source)
    assert result == {
        "targets": ["0055", "0057"],
        "all_original_predicates_true": True,
        "admission": "DENY",
    }


@pytest.mark.parametrize("value", [False, None, 1, "true"])
def test_original_false_unknown_or_coerced_value_never_passes_even_with_other_groups_true(
    tmp_path, value
):
    source = _write_reports(
        tmp_path, lambda document: document["groups"].update(combined=value)
    )
    with pytest.raises(ValueError, match="native_storage_diagnostic_refused"):
        diagnostic.verify_reports_v1(tmp_path, source)


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_commit", "d" * 40),
        ("predicate_sha256", "e" * 64),
        ("execution_authority", True),
        ("original_combined_predicate_used", False),
        ("component_conjunction_matches_original", False),
        ("detail_matches_original_groups", False),
        ("rejected_constant_type_ids", [1007]),
        ("rejected_constant_type_ids", ["1007"]),
    ],
)
def test_rehashed_wrong_source_or_admission_claim_is_refused(tmp_path, field, value):
    source = _write_reports(tmp_path, lambda document: document.update({field: value}))
    with pytest.raises(ValueError):
        diagnostic.verify_reports_v1(tmp_path, source)


def test_both_actual_nodes_owned_once_by_current58_component3_and_windows_postgres():
    tree = ast.parse((ROOT / diagnostic.MODULE).read_text("utf-8"))
    actual = tuple(
        (diagnostic.CASES[0][0], node.name)
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name.startswith("test_")
    )
    assert actual == diagnostic.CASES
    assert diagnostic.MODULE in collection.postgres_paths(3)
    assert diagnostic.MODULE not in collection.postgres_paths(2)
    assert Path(diagnostic.MODULE).name in windows.POSTGRES_INTEGRATION
    for case in diagnostic.CASES:
        assert full.REQUIRED_CASES.count(case) == 1
        assert full.required_cases("postgres", 3).count(case) == 1
        assert all(
            case not in full.required_cases("postgres", index) for index in (0, 1, 2, 4)
        )


@pytest.mark.parametrize("omitted", diagnostic.CASES)
def test_each_new_actual_case_is_mandatory_in_junit(tmp_path, omitted):
    path = tmp_path / "missing.xml"
    remaining = [case for case in diagnostic.CASES if case != omitted]
    path.write_text(
        '<testsuites><testsuite tests="1" failures="0" errors="0" skipped="0">'
        + "".join(
            '<testcase classname="' + module + '" name="' + name + '"/>'
            for module, name in remaining
        )
        + "</testsuite></testsuites>",
        encoding="ascii",
    )
    with pytest.raises(ValueError, match="pytest_required_execution_not_verified"):
        reports.verify_pytest_report(
            path, required_cases=diagnostic.CASES, allow_unrelated_skips=False
        )


def test_fixed_purpose_and_workflow_cannot_select_caller_node_or_become_reference_default():
    assert runner._storage_mode(SimpleNamespace()) is False
    assert runner._storage_mode(SimpleNamespace(native_storage_diagnostic=True)) is True
    with pytest.raises(ValueError):
        runner._storage_mode(SimpleNamespace(native_storage_diagnostic="true"))
    workflow = yaml.load(
        (ROOT / ".github/workflows/db0058-native-storage-diagnostic.yml").read_text(
            "utf-8"
        ),
        Loader=yaml.BaseLoader,
    )
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["on"]["push"]["branches"] == [
        "work/db0058-native-storage-diagnostic"
    ]
    job = workflow["jobs"]["native-storage"]
    assert job["timeout-minutes"] == "35"
    script = job["steps"][1]["run"]
    assert "--native-storage-diagnostic" in script
    assert "scripts.run_retained0017_reference_probe_v1" in script
    assert "--node" not in script and "--module" not in script
    original = yaml.load(
        (ROOT / ".github/workflows/db0058-retained0017-reference-probe.yml").read_text(
            "utf-8"
        ),
        Loader=yaml.BaseLoader,
    )
    assert (
        "--native-storage-diagnostic"
        not in original["jobs"]["reference-probe"]["steps"][1]["run"]
    )
