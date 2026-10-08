"""Bounded negative tests for exact-source Windows CI evidence admission."""

from __future__ import annotations

import hashlib
import json
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import manifest
from scripts import verify_windows_regression as verifier


def _case(module: str, name: str, *, skip: str | None = None) -> ET.Element:
    case = ET.Element("testcase", classname=module, name=name, time="0")
    if skip is not None:
        ET.SubElement(case, "skipped", type="pytest.skip", message=skip)
    return case


def _valid_cases() -> list[ET.Element]:
    cases = [_case("tests.unit.test_safe", "test_synthetic")]
    cases.extend(
        _case("tests.integration." + name.removesuffix(".py"), "test_synthetic")
        for name in verifier.NO_DB_INTEGRATION
    )
    cases.extend(
        _case(module, name) for module, name in verifier.WINDOWS_REQUIRED_CASES
    )
    module, name = next(iter(verifier.POSIX_SKIP_POLICY))
    cases.append(_case(module, name, skip=verifier.POSIX_SKIP_POLICY[(module, name)]))
    return cases


def _junit(path: Path, cases: list[ET.Element]) -> None:
    suite = ET.Element(
        "testsuite",
        name="pytest",
        tests=str(len(cases)),
        failures=str(sum(case.find("failure") is not None for case in cases)),
        errors=str(sum(case.find("error") is not None for case in cases)),
        skipped=str(sum(case.find("skipped") is not None for case in cases)),
    )
    suite.extend(cases)
    suites = ET.Element("testsuites", name="pytest tests")
    suites.append(suite)
    path.write_bytes(ET.tostring(suites, encoding="utf-8"))


def test_junit_accepts_actual_windows_cases_and_records_posix_skip(tmp_path: Path):
    path = tmp_path / "windows.xml"
    _junit(path, _valid_cases())

    result = verifier.verify_junit(path)

    assert result["passed"] == len(_valid_cases()) - 1
    assert result["skipped"] == 1
    assert result["allowed_skips"][0]["kind"] == "posix_only"
    assert result["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()


def test_junit_rejects_missing_or_extra_collected_case(tmp_path: Path):
    cases = _valid_cases()
    path = tmp_path / "windows.xml"
    _junit(path, cases)
    expected = {(case.get("classname"), case.get("name")) for case in cases}
    assert verifier.verify_junit(path, expected)["tests"] == len(expected)

    with pytest.raises(
        verifier.WindowsRegressionError, match="windows_junit_collected_cases_differ"
    ):
        verifier.verify_junit(path, expected - {next(iter(expected))})
    with pytest.raises(
        verifier.WindowsRegressionError, match="windows_junit_collected_cases_differ"
    ):
        verifier.verify_junit(
            path, expected | {("tests.unit.test_missing", "test_missing")}
        )


@pytest.mark.parametrize(
    ("damage", "reason"),
    [
        ("unknown_skip", "windows_junit_unapproved_skip"),
        ("native_skip", "windows_junit_unapproved_skip"),
        ("module_missing", "windows_junit_no_db_module_missing"),
        ("native_missing", "windows_junit_native_case_missing"),
        ("failure", "windows_junit_failures"),
        ("duplicate", "windows_junit_duplicate_case"),
        ("foreign_module", "windows_junit_unselected_module"),
        ("count_conflict", "windows_junit_counts_conflict"),
    ],
)
def test_junit_rejects_unsafe_or_incomplete_evidence(
    tmp_path: Path, damage: str, reason: str
):
    cases = _valid_cases()
    if damage == "unknown_skip":
        cases.append(_case("tests.unit.test_safe", "test_new", skip="POSIX proof"))
    elif damage == "native_skip":
        module, name = verifier.WINDOWS_REQUIRED_CASES[0]
        target = next(
            case
            for case in cases
            if case.get("classname") == module and case.get("name") == name
        )
        ET.SubElement(
            target, "skipped", type="pytest.skip", message="missing PowerShell"
        )
    elif damage == "module_missing":
        excluded = "tests.integration." + verifier.NO_DB_INTEGRATION[0].removesuffix(
            ".py"
        )
        cases = [case for case in cases if case.get("classname") != excluded]
    elif damage == "native_missing":
        module, name = verifier.WINDOWS_REQUIRED_CASES[0]
        cases = [
            case
            for case in cases
            if (case.get("classname"), case.get("name")) != (module, name)
        ]
    elif damage == "failure":
        ET.SubElement(cases[0], "failure", message="synthetic failure")
    elif damage == "duplicate":
        cases.append(_case(cases[0].get("classname"), cases[0].get("name")))
    elif damage == "foreign_module":
        cases.append(_case("tests.integration.test_unreviewed", "test_new"))
    path = tmp_path / "windows.xml"
    _junit(path, cases)
    if damage == "count_conflict":
        tree = ET.parse(path)
        tree.getroot()[0].set("tests", "999")
        tree.write(path, encoding="utf-8")

    with pytest.raises(verifier.WindowsRegressionError, match=reason):
        verifier.verify_junit(path)


def test_integration_module_classification_fails_on_new_module(tmp_path: Path):
    assert (
        "test_complete_consumed_event_ledger_repository.py"
        in verifier.POSTGRES_INTEGRATION
    )
    assert (
        "test_complete_consumed_event_ledger_repository.py"
        not in verifier.NO_DB_INTEGRATION
    )
    assert "test_gate3_schedule_publication_ack.py" in verifier.POSTGRES_INTEGRATION
    assert "test_gate3_schedule_publication_ack.py" not in verifier.NO_DB_INTEGRATION
    assert (
        "test_gate3_committed_preregistration_seal.py" in verifier.POSTGRES_INTEGRATION
    )
    assert (
        "test_gate3_committed_preregistration_seal.py" not in verifier.NO_DB_INTEGRATION
    )
    integration = tmp_path / "tests" / "integration"
    integration.mkdir(parents=True)
    (tmp_path / "tests" / "unit").mkdir()
    for name in (*verifier.NO_DB_INTEGRATION, *verifier.POSTGRES_INTEGRATION):
        (integration / name).write_text("pass\n", encoding="utf-8")
    assert verifier.test_selection(tmp_path)[0] == "tests/unit"

    (integration / "test_new_unclassified.py").write_text("pass\n", encoding="utf-8")
    with pytest.raises(
        verifier.WindowsRegressionError,
        match="integration_module_classification_changed",
    ):
        verifier.test_selection(tmp_path)


def _source(tmp_path: Path) -> tuple[Path, Path, Path, Path, Path]:
    root = tmp_path / "source"
    integration = root / "tests" / "integration"
    integration.mkdir(parents=True)
    (root / "tests" / "unit").mkdir()
    (root / "tests" / "unit" / "test_safe.py").write_text("pass\n")
    for name in (*verifier.NO_DB_INTEGRATION, *verifier.POSTGRES_INTEGRATION):
        (integration / name).write_text("pass\n")
    (root / "requirements").mkdir()
    (root / "pyproject.toml").write_text('[project]\nname="ctcc-v2"\nversion="1"\n')
    lock = root / "requirements" / "validation-windows-py312.lock"
    lock.write_text("synthetic-only\n")
    (root / "requirements" / "validation-windows-py312.json").write_text(
        json.dumps(
            {
                "schema": "ctcc.validation_dependency_lock.v1",
                "target": "windows",
                "source_pyproject_sha256": hashlib.sha256(
                    (root / "pyproject.toml").read_bytes()
                ).hexdigest(),
                "lock_sha256": hashlib.sha256(lock.read_bytes()).hexdigest(),
                "packages": [{"name": "synthetic"}],
            }
        )
    )
    manifest.write_manifest(root, root / "MANIFEST.sha256")
    archive = tmp_path / "source.zip"
    with zipfile.ZipFile(archive, "w") as zipped:
        for path in sorted(root.rglob("*")):
            if path.is_file():
                zipped.write(path, path.relative_to(root).as_posix())
    source_identity = tmp_path / "source-identity.json"
    source_identity.write_text(
        json.dumps(
            {
                "schema": "ctcc.windows_ci_source.v1",
                "commit": "a" * 40,
                "github_sha": "a" * 40,
                "tree": "b" * 40,
                "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
                "manifest_sha256": hashlib.sha256(
                    (root / "MANIFEST.sha256").read_bytes()
                ).hexdigest(),
                "runner_label": "windows-2025",
            }
        )
    )
    dependency = tmp_path / "dependency.json"
    dependency.write_text(
        json.dumps(
            {
                "target": "windows",
                "lock_sha256": hashlib.sha256(lock.read_bytes()).hexdigest(),
                "packages": 1,
                "installed_inventory": {"mode": "exact_inventory_match"},
                "accepted_full_validation_baseline": False,
            }
        )
    )
    junit = tmp_path / "windows.xml"
    _junit(junit, _valid_cases())
    collection = tmp_path / "windows-collection.json"
    collection.write_text(
        json.dumps(
            {
                "schema": "ctcc.windows_ci_collection.v1",
                "requested_paths": list(verifier.test_selection(root)),
                "manifest_sha256": hashlib.sha256(
                    (root / "MANIFEST.sha256").read_bytes()
                ).hexdigest(),
                "python": sys.version,
                "selection_filters_absent": True,
                "cases": [
                    {
                        "nodeid": case.get("classname").replace(".", "/")
                        + ".py::"
                        + case.get("name"),
                        "classname": case.get("classname"),
                        "name": case.get("name"),
                    }
                    for case in _valid_cases()
                ],
            }
        )
    )
    return root, archive, source_identity, dependency, collection


def test_exact_source_archive_and_manifest_reject_late_change(tmp_path: Path):
    root, archive, source_identity, dependency, collection = _source(tmp_path)
    junit = tmp_path / "windows.xml"
    result = verifier.verify_evidence(
        root, archive, source_identity, dependency, collection, junit
    )
    assert (
        result["scope"]
        == "windows_no_postgresql_selected_full_unit_and_no_db_integration"
    )
    assert result["postgresql_verified"] is False
    assert result["linux_posix_counterpart_verified"] is False

    (root / "tests" / "unit" / "test_safe.py").write_text("changed\n")
    with pytest.raises(
        verifier.WindowsRegressionError, match="windows_source_archive_readback_changed"
    ):
        verifier.verify_evidence(
            root, archive, source_identity, dependency, collection, junit
        )


@pytest.mark.parametrize("damage", ["selection", "mapping", "duplicate", "filter"])
def test_collection_rejects_subset_or_mapped_identity_change(
    tmp_path: Path, damage: str
):
    root, _, _, _, collection = _source(tmp_path)
    keys, result = verifier.verify_collection(collection, root)
    assert len(keys) == result["collected"] == len(_valid_cases())
    payload = json.loads(collection.read_text())
    if damage == "selection":
        payload["requested_paths"] = ["tests/unit/test_one_file.py"]
    elif damage == "mapping":
        payload["cases"][0]["name"] = "test_forged"
    elif damage == "duplicate":
        payload["cases"].append(payload["cases"][0])
    else:
        payload["selection_filters_absent"] = False
    collection.write_text(json.dumps(payload))
    with pytest.raises(verifier.WindowsRegressionError, match="windows_collection_"):
        verifier.verify_collection(collection, root)


def test_collection_rejects_omitted_unit_test_module(tmp_path: Path):
    root, _, _, _, collection = _source(tmp_path)
    (root / "tests" / "unit" / "test_uncollected.py").write_text(
        "def test_was_not_collected(): pass\n"
    )
    manifest.write_manifest(root, root / "MANIFEST.sha256")
    payload = json.loads(collection.read_text())
    payload["manifest_sha256"] = hashlib.sha256(
        (root / "MANIFEST.sha256").read_bytes()
    ).hexdigest()
    collection.write_text(json.dumps(payload))

    with pytest.raises(
        verifier.WindowsRegressionError,
        match="windows_collection_unit_module_missing",
    ):
        verifier.verify_collection(collection, root)


def test_collection_plugin_refuses_pytest_keyword_filter(tmp_path: Path):
    output = tmp_path / "collection.json"
    config = SimpleNamespace(
        getoption=lambda _: str(output),
        option=SimpleNamespace(
            keyword="only_one", markexpr="", ignore=[], ignore_glob=[], deselect=[]
        ),
    )
    session = SimpleNamespace(
        config=config, items=[SimpleNamespace(nodeid="test.py::test_one")]
    )

    with pytest.raises(
        verifier.WindowsRegressionError, match="windows_collection_filtered"
    ):
        verifier.pytest_collection_finish(session)
    assert not output.exists()


def test_exact_source_archive_rejects_traversal(tmp_path: Path):
    root = tmp_path / "source"
    root.mkdir()
    archive = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr("../outside.txt", "no")

    with pytest.raises(
        verifier.WindowsRegressionError, match="windows_source_archive_entry_unsafe"
    ):
        verifier.verify_archive_readback(archive, root)
