"""Source58-only owner routing/workflow codecs; no PostgreSQL execution."""

import ast
import copy
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
import yaml

from scripts import current0058_full_hermetic_contract_v1 as full

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/db0058-retained-owner-regression.yml"
BRANCH = "work/db0058-retained-owner-regression"
NEW_CASES = full.OWNER0057_NULL_CASES + tuple(
    case
    for case in full.OWNER_UPGRADE_CASES
    if "null_bitmap" in case[1]
    or case[1].startswith("test_actual0057_shared_snapshot_")
)


def _actual_owner_cases():
    result = []
    for path in sorted(full.collection.CANONICAL_OWNER_PATHS):
        module = path[:-3].replace("/", ".")
        for node in ast.parse((ROOT / path).read_text(encoding="utf-8")).body:
            if not isinstance(node, ast.AsyncFunctionDef) or not node.name.startswith(
                "test_"
            ):
                continue
            values = None
            for decorator in node.decorator_list:
                if (
                    isinstance(decorator, ast.Call)
                    and isinstance(decorator.func, ast.Attribute)
                    and decorator.func.attr == "parametrize"
                ):
                    assert values is None and len(decorator.args) == 2
                    assert not decorator.keywords
                    values = ast.literal_eval(decorator.args[1])
                    assert type(values) is list and all(
                        type(value) in (bool, int, str) for value in values
                    )
            names = (
                [node.name + "[" + str(value) + "]" for value in values]
                if values is not None
                else [node.name]
            )
            result.extend((module, name) for name in names)
    return result


def test_actual_owner_cases_all_run_once_on_source58_component3():
    expected = _actual_owner_cases()
    assert len(expected) == len(set(expected)) == 28
    assert set(full.CANONICAL_OWNER_CASES) == set(expected)
    assert len(NEW_CASES) == 6 and set(NEW_CASES) <= set(expected)
    assert set(expected).isdisjoint(full.SUCCESSOR_CASES)
    for case in expected:
        assert full.REQUIRED_CASES.count(case) == 1
        node = case[0].replace(".", "/") + ".py::" + case[1]
        assert [
            index
            for index in range(5)
            if full.collection.selected_for("postgres", index, node)
        ] == [3]
        assert not any(
            full.collection.selected_for("shard", index, node) for index in range(8)
        )
        assert case in full.required_cases("postgres", 3)
    assert len(full.collection.previous.POSTGRES_TEST_PATHS) == 108
    assert [len(full.collection.previous.postgres_paths(i)) for i in range(2)] == [
        48,
        60,
    ]
    assert full.base.collection.POSTGRES_COMPONENT_COUNT == 3
    assert all(case not in full.base.REQUIRED_CASES for case in NEW_CASES)


def test_source58_all_storage_callback_cases_are_mandatory_once_on_owner3():
    cases = full.OWNER_STORAGE_CASES
    assert len(cases) == len(set(cases)) == 8
    assert len(full.OWNER_STORAGE_CANONICAL_CASES) == 7
    for case in cases:
        assert full.REQUIRED_CASES.count(case) == 1
        assert case not in full.base.REQUIRED_CASES
        node = case[0].replace(".", "/") + ".py::" + case[1]
        assert [
            index
            for index in range(5)
            if full.collection.selected_for("postgres", index, node)
        ] == [3]
        assert not any(
            full.collection.selected_for("shard", index, node) for index in range(8)
        )


@pytest.mark.parametrize("missing", full.OWNER_STORAGE_CASES)
def test_source58_required_report_refuses_each_storage_callback_case_omission(
    tmp_path, missing
):
    root = ET.Element("testsuites")
    cases = [case for case in full.OWNER_STORAGE_CASES if case != missing]
    suite = ET.SubElement(
        root, "testsuite", tests=str(len(cases)), failures="0", errors="0", skipped="0"
    )
    for module, name in cases:
        ET.SubElement(suite, "testcase", classname=module, name=name)
    path = tmp_path / "declared-synthetic-owner-storage-required-report.xml"
    path.write_bytes(ET.tostring(root))
    with pytest.raises(ValueError, match="pytest_required_execution_not_verified"):
        full.base.shared.verify_pytest_report(
            path, required_cases=full.OWNER_STORAGE_CASES
        )


@pytest.mark.parametrize("missing", NEW_CASES)
def test_source58_mandatory_report_rejects_each_new_owner_fault_omission(
    tmp_path, missing
):
    root = ET.Element("testsuites")
    cases = [case for case in full.CANONICAL_OWNER_CASES if case != missing]
    suite = ET.SubElement(
        root, "testsuite", tests=str(len(cases)), failures="0", errors="0", skipped="0"
    )
    for module, name in cases:
        ET.SubElement(suite, "testcase", classname=module, name=name)
    path = tmp_path / "declared-synthetic-owner-required-report.xml"
    path.write_bytes(ET.tostring(root))
    with pytest.raises(ValueError, match="pytest_required_execution_not_verified"):
        full.base.shared.verify_pytest_report(
            path, required_cases=full.CANONICAL_OWNER_CASES
        )


def test_source58_preserves_the_existing_genuine_cli_once_on_historical_owner1():
    case = (
        "tests.integration.test_gate3_fitted_candidate_custody_pg",
        "test_actual0056_non_superuser_owner_upgrades_0055_once_with_fresh_readback",
    )
    assert full.REQUIRED_CASES.count(case) == 1
    node = case[0].replace(".", "/") + ".py::" + case[1]
    assert [
        index
        for index in range(5)
        if full.collection.selected_for("postgres", index, node)
    ] == [1]
    assert len(full.OWNER0056_V2_CASES) == len(set(full.OWNER0056_V2_CASES)) == 9
    assert all(case not in full.base.REQUIRED_CASES for case in full.OWNER0056_V2_CASES)


@pytest.mark.parametrize(
    "missing", (*full.OWNER0056_V2_CASES, full.OWNER0056_EXISTING_CLI_CASE)
)
def test_source58_mandatory_report_rejects_each_new_owner55_case_omission(
    tmp_path, missing
):
    root = ET.Element("testsuites")
    required = (*full.CANONICAL_OWNER_CASES, full.OWNER0056_EXISTING_CLI_CASE)
    cases = [case for case in required if case != missing]
    suite = ET.SubElement(
        root, "testsuite", tests=str(len(cases)), failures="0", errors="0", skipped="0"
    )
    for module, name in cases:
        ET.SubElement(suite, "testcase", classname=module, name=name)
    path = tmp_path / "declared-synthetic-owner55-required-report.xml"
    path.write_bytes(ET.tostring(root))
    with pytest.raises(ValueError, match="pytest_required_execution_not_verified"):
        full.base.shared.verify_pytest_report(path, required_cases=required)


def _workflow_contract(workflow):
    assert set(workflow) == {"name", "on", "permissions", "jobs"}
    assert workflow["on"] == {"push": {"branches": [BRANCH]}, "workflow_dispatch": ""}
    assert workflow["permissions"] == {"contents": "read"}
    assert set(workflow["jobs"]) == {"retained-owner-regression"}
    job = workflow["jobs"]["retained-owner-regression"]
    assert job["if"] == "github.ref == 'refs/heads/" + BRANCH + "'"
    assert job["runs-on"] == "ubuntu-24.04" and job["timeout-minutes"] == "180"
    steps = job["steps"]
    assert len(steps) == 3
    assert (
        steps[0]["uses"] == "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"
    )
    assert steps[0]["with"] == {"persist-credentials": "false"}
    assert (
        steps[2]["uses"]
        == "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02"
    )
    assert steps[2]["if"] == "always()"
    assert steps[2]["with"]["if-no-files-found"] == "error"
    run = steps[1]["run"]
    assert steps[1]["shell"] == "bash"
    assert 'test "$commit" = "$GITHUB_SHA"' in run
    assert "git rev-parse 'HEAD^{tree}'" in run
    assert "git -c core.autocrlf=false -c core.eol=lf archive --format=tar" in run
    assert "python3 -m scripts.verify_current0058_full_hermetic" in run
    assert '--archive "$archive" --archive-sha256 "$digest"' in run
    assert '--commit "$commit" --tree "$tree"' in run
    assert "--suite postgres --postgres-component-index 3" in run
    assert "inputs." not in str(workflow) and "secrets." not in str(workflow)
    assert "env" not in workflow and "env" not in job
    assert all("env" not in step for step in steps)


def test_fixed_retained_owner_probe_uses_exact_archive_and_original_verifier():
    _workflow_contract(
        yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    )


@pytest.mark.parametrize(
    "change",
    (
        "wrong_component",
        "unbounded_branch",
        "credential_persistence",
        "mutable_action",
        "short_timeout",
        "secret_input",
    ),
)
def test_retained_owner_probe_rejects_unreviewed_scope(change):
    original = yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    _workflow_contract(original)
    changed = copy.deepcopy(original)
    job = changed["jobs"]["retained-owner-regression"]
    if change == "wrong_component":
        job["steps"][1]["run"] = job["steps"][1]["run"].replace(
            "--postgres-component-index 3", "--postgres-component-index 2"
        )
    elif change == "unbounded_branch":
        changed["on"]["push"]["branches"] = ["**"]
    elif change == "credential_persistence":
        job["steps"][0]["with"]["persist-credentials"] = "true"
    elif change == "mutable_action":
        job["steps"][0]["uses"] = "actions/checkout@main"
    elif change == "short_timeout":
        job["timeout-minutes"] = "40"
    else:
        job["env"] = {"UNREVIEWED": "${{ secrets.UNREVIEWED }}"}
    with pytest.raises(AssertionError):
        _workflow_contract(changed)
