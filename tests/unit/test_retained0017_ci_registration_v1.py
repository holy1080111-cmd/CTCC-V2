"""Synthetic CI registration codecs only; collected SQL cases are not execution."""

import ast
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from scripts import current0058_full_hermetic_contract_v1 as full
from scripts import verify_linux_shard as legacy
from scripts import verify_windows_regression as windows

ROOT = Path(__file__).resolve().parents[2]
MODULE = "tests.integration.test_retained0017_owner_plan_v1_pg"
PATH = MODULE.replace(".", "/") + ".py"


def _declared_cases():
    cases = []
    for node in ast.parse((ROOT / PATH).read_text(encoding="utf-8")).body:
        if not isinstance(node, ast.AsyncFunctionDef) or not node.name.startswith(
            "test_"
        ):
            continue
        parameters = []
        for decorator in node.decorator_list:
            if (
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Attribute)
                and decorator.func.attr == "parametrize"
            ):
                assert len(decorator.args) == 2 and not decorator.keywords
                assert isinstance(ast.literal_eval(decorator.args[0]), str)
                values = ast.literal_eval(decorator.args[1])
                assert type(values) is list and values
                assert all(type(value) is str for value in values)
                parameters.append(values)
        assert len(parameters) <= 1
        names = (
            [node.name + "[" + value + "]" for value in parameters[0]]
            if parameters
            else [node.name]
        )
        cases.extend((MODULE, name) for name in names)
    return tuple(cases)


def _report(tmp_path, cases):
    root = ET.Element("testsuites")
    suite = ET.SubElement(
        root, "testsuite", tests=str(len(cases)), failures="0", errors="0", skipped="0"
    )
    for module, name in cases:
        ET.SubElement(suite, "testcase", classname=module, name=name)
    path = tmp_path / "declared-synthetic-required-case-codec.xml"
    path.write_bytes(ET.tostring(root))
    return path


def test_retained0017_all_actual_declared_cases_are_mandatory_once():
    expected = _declared_cases()
    assert len(expected) == len(set(expected)) == 13
    assert full.RETAINED0017_OWNER_CASES == expected
    assert all(full.REQUIRED_CASES.count(case) == 1 for case in expected)
    assert set(expected).isdisjoint(full.SUCCESSOR_CASES)


def test_retained0017_owner_module_runs_only_in_current58_component3():
    assert full.collection.RETAINED0017_OWNER_PATHS == {PATH}
    assert Path(PATH).name in windows.POSTGRES_INTEGRATION
    assert PATH not in legacy.POSTGRES_TEST_PATHS
    assert len(legacy.POSTGRES_TEST_PATHS) == 108
    assert [len(legacy.postgres_paths(index)) for index in range(2)] == [48, 60]
    assert all(
        case not in full.base.REQUIRED_CASES for case in full.RETAINED0017_OWNER_CASES
    )
    for module, name in full.RETAINED0017_OWNER_CASES:
        node = module.replace(".", "/") + ".py::" + name
        assert [
            index
            for index in range(5)
            if full.collection.selected_for("postgres", index, node)
        ] == [3]
        assert not any(
            full.collection.selected_for("shard", index, node) for index in range(8)
        )
        assert (module, name) in full.required_cases("postgres", 3)
        assert (module, name) in full.required_cases("full", None)


def test_retained0017_complete_synthetic_required_report_codec(tmp_path):
    cases = full.RETAINED0017_OWNER_CASES
    report = full.base.shared.verify_pytest_report(
        _report(tmp_path, cases), required_cases=cases
    )
    assert report["passed"] == 13 and report["skipped"] == 0


@pytest.mark.parametrize("missing", range(13))
def test_retained0017_required_report_refuses_each_single_omitted_case(
    tmp_path, missing
):
    cases = full.RETAINED0017_OWNER_CASES
    with pytest.raises(ValueError, match="pytest_required_execution_not_verified"):
        full.base.shared.verify_pytest_report(
            _report(tmp_path, cases[:missing] + cases[missing + 1 :]),
            required_cases=cases,
        )
