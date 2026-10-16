"""Adversarial proofs that Windows sharding preserves the full suite gate."""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import manifest
from scripts import verify_windows_shard as shard_plugin
from scripts.verify_windows_regression import (
    NO_DB_INTEGRATION,
    POSIX_SKIP_POLICY,
    POSTGRES_INTEGRATION,
    WINDOWS_REQUIRED_CASES,
    WindowsRegressionError,
    _junit_key,
)
from scripts.verify_windows_shard import SHARD_COUNT, module_shard, selected_for
from scripts.verify_windows_shard_union import verify_union

COMMIT = "a" * 40
TREE = "b" * 40


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _nodeid(module: str, name: str) -> str:
    return module.replace(".", "/") + ".py::" + name


def _fixture(tmp_path: Path) -> tuple[Path, Path, str, list[dict]]:
    root = tmp_path / "source"
    modules = {"tests/unit/test_safe.py"}
    cases = [_nodeid("tests.unit.test_safe", "test_synthetic")]
    for name in NO_DB_INTEGRATION:
        module = f"tests.integration.{name.removesuffix('.py')}"
        modules.add(f"tests/integration/{name}")
        cases.append(_nodeid(module, "test_synthetic"))
    for module, name in WINDOWS_REQUIRED_CASES:
        modules.add(module.replace(".", "/") + ".py")
        cases.append(_nodeid(module, name))
    skip_target = next(iter(POSIX_SKIP_POLICY))
    modules.add(skip_target[0].replace(".", "/") + ".py")
    cases.append(_nodeid(*skip_target))
    candidate = 0
    while {module_shard(path) for path in modules} != set(range(SHARD_COUNT)):
        path = f"tests/unit/test_shard_synthetic_{candidate}.py"
        if path not in modules:
            modules.add(path)
            cases.append(path + "::test_synthetic")
        candidate += 1
    for path in modules:
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("def test_synthetic(): pass\n", encoding="utf-8")
    for name in set(NO_DB_INTEGRATION) | set(POSTGRES_INTEGRATION):
        target = root / "tests/integration" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            target.write_text("def test_synthetic(): pass\n", encoding="utf-8")
    (root / "requirements").mkdir()
    pyproject = root / "pyproject.toml"
    pyproject.write_text('[project]\nname="ctcc-v2"\nversion="1"\n')
    lock = root / "requirements/validation-windows-py312.lock"
    lock.write_text("synthetic-only\n")
    lock_hash = _sha(lock.read_bytes())
    (root / "requirements/validation-windows-py312.json").write_text(
        json.dumps(
            {
                "schema": "ctcc.validation_dependency_lock.v1",
                "target": "windows",
                "source_pyproject_sha256": _sha(pyproject.read_bytes()),
                "lock_sha256": lock_hash,
                "packages": [{"name": "synthetic"}],
            }
        )
    )
    manifest.write_manifest(root, root / "MANIFEST.sha256")
    manifest_hash = _sha((root / "MANIFEST.sha256").read_bytes())
    archive = tmp_path / "source.zip"
    with zipfile.ZipFile(archive, "w") as zipped:
        for path in sorted(root.rglob("*")):
            if path.is_file():
                zipped.write(path, path.relative_to(root).as_posix())
    source_identity = {
        "schema": "ctcc.windows_ci_source.v1",
        "commit": COMMIT,
        "github_sha": COMMIT,
        "tree": TREE,
        "archive_sha256": _sha(archive.read_bytes()),
        "manifest_sha256": manifest_hash,
        "runner_label": "windows-2025",
        "runner_image_version": "synthetic-image",
    }
    dependency = {
        "target": "windows",
        "lock_sha256": lock_hash,
        "packages": 1,
        "installed_inventory": {"mode": "exact_inventory_match"},
        "accepted_full_validation_baseline": False,
    }
    full = [
        {"nodeid": nodeid, "classname": key[0], "name": key[1]}
        for nodeid in cases
        for key in (_junit_key(nodeid),)
    ]
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    for index in range(SHARD_COUNT):
        component = artifacts / f"ctcc-windows-shard-{index}-{COMMIT}"
        component.mkdir()
        shutil.copyfile(archive, component / "source.zip")
        (component / "source-identity.json").write_text(json.dumps(source_identity))
        (component / "dependency-result.json").write_text(json.dumps(dependency))
        selected = [case for case in full if selected_for(index, case["nodeid"])]
        record = {
            "schema": "ctcc.windows_full_collection_shard.v1",
            "shard_index": index,
            "shard_count": SHARD_COUNT,
            "requested_paths": ["tests/unit"]
            + [f"tests/integration/{name}" for name in NO_DB_INTEGRATION],
            "manifest_sha256": manifest_hash,
            "python": sys.version,
            "selection_filters_absent": True,
            "full_cases": full,
            "selected_nodeids": [case["nodeid"] for case in selected],
            "observed_nodeids": [case["nodeid"] for case in selected],
            "pytest_exitstatus": 0,
        }
        (component / "windows-collection.json").write_text(json.dumps(record))
        suites = ET.Element("testsuites")
        suite = ET.SubElement(
            suites,
            "testsuite",
            tests=str(len(selected)),
            failures="0",
            errors="0",
            skipped=str(
                sum(case["nodeid"] == _nodeid(*skip_target) for case in selected)
            ),
        )
        for case in selected:
            item = ET.SubElement(
                suite,
                "testcase",
                classname=case["classname"],
                name=case["name"],
            )
            if case["nodeid"] == _nodeid(*skip_target):
                ET.SubElement(
                    item,
                    "skipped",
                    type="pytest.skip",
                    message=POSIX_SKIP_POLICY[skip_target],
                )
        (component / "windows-junit.xml").write_bytes(ET.tostring(suites))
    return artifacts, root, manifest_hash, full


def _verify(tmp_path: Path, artifacts: Path, root: Path, manifest_hash: str):
    return verify_union(
        artifacts,
        root=root,
        commit=COMMIT,
        tree=TREE,
        manifest_sha256=manifest_hash,
        output=tmp_path / "union",
    )


def test_whole_module_partition_and_exact_full_suite_union(tmp_path: Path):
    artifacts, root, manifest_hash, full = _fixture(tmp_path)
    assert all(
        sum(selected_for(index, case["nodeid"]) for index in range(SHARD_COUNT)) == 1
        for case in full
    )
    result = _verify(tmp_path, artifacts, root, manifest_hash)
    assert result["full_suite_union"] == "PASS"
    assert result["full_collected_cases"] == result["selected_cases"] == len(full)
    assert result["regression"]["junit"]["skipped"] == 1
    assert len(result["regression"]["junit"]["required_windows_cases_passed"]) == len(
        WINDOWS_REQUIRED_CASES
    )


@pytest.mark.parametrize(
    ("damage", "reason"),
    [
        ("missing_artifact", "component_set_incomplete"),
        ("subset", "shard_selection_or_observation"),
        ("missing_junit_case", "junit_cases_or_outcomes_changed"),
        ("source_drift", "source_or_lock_identity_invalid"),
        ("lock_drift", "source_or_lock_drift"),
        ("inventory_drift", "source_or_lock_drift"),
        ("full_collection_drift", "full_collection_drift"),
        ("unapproved_skip", "junit_unapproved_skip"),
    ],
)
def test_union_rejects_missing_or_changed_evidence(
    tmp_path: Path, damage: str, reason: str
):
    artifacts, root, manifest_hash, _ = _fixture(tmp_path)
    component = artifacts / f"ctcc-windows-shard-0-{COMMIT}"
    if damage == "missing_artifact":
        shutil.rmtree(component)
    elif damage == "subset":
        record_path = component / "windows-collection.json"
        record = json.loads(record_path.read_text())
        record["selected_nodeids"].pop()
        record["observed_nodeids"].pop()
        record_path.write_text(json.dumps(record))
    elif damage == "missing_junit_case":
        path = component / "windows-junit.xml"
        xml = ET.parse(path)
        xml.getroot()[0].remove(xml.getroot()[0].find("testcase"))
        xml.write(path)
    elif damage == "source_drift":
        path = component / "source-identity.json"
        value = json.loads(path.read_text())
        value["tree"] = "c" * 40
        path.write_text(json.dumps(value))
    elif damage == "lock_drift":
        path = component / "dependency-result.json"
        value = json.loads(path.read_text())
        value["lock_sha256"] = "d" * 64
        path.write_text(json.dumps(value))
    elif damage == "inventory_drift":
        path = component / "dependency-result.json"
        value = json.loads(path.read_text())
        value["installed_inventory"]["current_count"] = 999
        path.write_text(json.dumps(value))
    elif damage == "full_collection_drift":
        path = component / "windows-collection.json"
        value = json.loads(path.read_text())
        value["full_cases"].reverse()
        value["selected_nodeids"] = [
            case["nodeid"]
            for case in value["full_cases"]
            if selected_for(0, case["nodeid"])
        ]
        value["observed_nodeids"] = value["selected_nodeids"]
        path.write_text(json.dumps(value))
    elif damage == "unapproved_skip":
        path = component / "windows-junit.xml"
        xml = ET.parse(path)
        case = next(
            item
            for item in xml.getroot()[0].findall("testcase")
            if item.find("skipped") is None
        )
        ET.SubElement(case, "skipped", type="pytest.skip", message="not reviewed")
        xml.getroot()[0].set(
            "skipped",
            str(sum(item.find("skipped") is not None for item in xml.getroot()[0])),
        )
        xml.write(path)
    with pytest.raises(WindowsRegressionError, match=reason):
        _verify(tmp_path, artifacts, root, manifest_hash)


def test_module_shard_rejects_foreign_or_traversal_paths():
    for path in (
        "tests/integration/test_database_schema.py",
        "tests/unit/../integration/test_api.py",
        "../tests/unit/test_safe.py",
        "tests\\unit\\test_safe.py",
    ):
        with pytest.raises(WindowsRegressionError, match="module_path_invalid"):
            module_shard(path)


def test_pytest_hook_records_full_collection_before_module_deselection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _, root, _, full = _fixture(tmp_path)
    monkeypatch.chdir(root)
    output = tmp_path / "actual-shard.json"
    deselected = []
    index = 0
    config = SimpleNamespace(
        rootpath=root,
        args=["tests/unit"]
        + [f"tests/integration/{name}" for name in NO_DB_INTEGRATION],
        getoption=lambda name: {
            "--ctcc-windows-shard-index": index,
            "--ctcc-windows-collection-json": str(output),
        }[name],
        option=SimpleNamespace(
            keyword="",
            markexpr="",
            ignore=[],
            ignore_glob=[],
            deselect=[],
            lf=False,
            ff=False,
            stepwise=False,
        ),
        hook=SimpleNamespace(pytest_deselected=lambda items: deselected.extend(items)),
    )
    items = [
        SimpleNamespace(
            nodeid=case["nodeid"], path=root / case["nodeid"].split("::")[0]
        )
        for case in full
    ]
    session = SimpleNamespace(config=config, items=items, exitstatus=0)
    shard_plugin.pytest_collection_modifyitems(session, config, items)
    shard_plugin.pytest_collection_finish(session)
    for item in items:
        shard_plugin.pytest_runtest_logreport(SimpleNamespace(nodeid=item.nodeid))
    shard_plugin.pytest_sessionfinish(session, 0)
    record = json.loads(output.read_text())
    assert [case["nodeid"] for case in record["full_cases"]] == [
        case["nodeid"] for case in full
    ]
    assert record["selected_nodeids"] == [
        case["nodeid"] for case in full if selected_for(index, case["nodeid"])
    ]
    assert record["observed_nodeids"] == record["selected_nodeids"]
    assert len(items) + len(deselected) == len(full)
    assert session.exitstatus == 0
