"""Synthetic boundary tests for the current0055 Linux evidence union.

The per-component Docker readback is tested by its own strict contract. These
fixtures exercise only the cross-component join, without starting Docker.
"""

from __future__ import annotations

import hashlib
import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from scripts import verify_current0055_linux_union as union

COMMIT = "a" * 40
TREE = "b" * 40
MANIFEST = "c" * 64
ARCHIVE = "d" * 64
MIGRATION = "e" * 64


def _json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")


def _junit(path: Path, node: str) -> None:
    source, name = node.split("::")
    module = source.removesuffix(".py").replace("/", ".")
    root = ET.Element("testsuites")
    suite = ET.SubElement(
        root, "testsuite", tests="1", failures="0", errors="0", skipped="0"
    )
    ET.SubElement(suite, "testcase", classname=module, name=name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(ET.tostring(root, encoding="utf-8"))


@pytest.fixture
def components(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    archive_files = {"MANIFEST.sha256": {"sha256": MANIFEST}}
    source_raw = json.dumps(archive_files, sort_keys=True).encode()
    source_sha = hashlib.sha256(source_raw).hexdigest()
    full = [
        f"tests/unit/test_case_{index}.py::test_case_{index}" for index in range(10)
    ]
    records = {}
    roots = []
    for serial, (mode, index, name) in enumerate(union._component_specs(COMMIT)):
        root = tmp_path / name / "ctcc-validation"
        root.mkdir(parents=True)
        (root.parent / "ctcc-source.tar").write_bytes(b"synthetic")
        (root / "source-files.json").write_bytes(source_raw)
        _json(
            root / "steps.json",
            [{"name": stage} for stage in union.component_stage_names(mode, index)],
        )
        junit_name, collection_name = union.component_report_paths(mode, index)
        _junit(root / junit_name, full[serial])
        _json(root / collection_name, {"selected_nodeids": [full[serial]]})
        report = union.verify_pytest_report(root / junit_name)
        identity = {
            "suite": mode,
            "shard_index": index if mode == "shard" else None,
            "postgres_component_index": index if mode == "postgres" else None,
            "runner_contract": union.RUNNER_CONTRACT,
            "source_contract_head": union.HEAD,
            "run_id": f"{serial:012x}",
            "trading_acceptance": False,
            "external_order_writes": 0,
            "archive_sha256": ARCHIVE,
            "source_files_sha256": source_sha,
            "migration": {
                "source_head": union.HEAD,
                "database_head": union.HEAD,
                "status": "PASS",
                "migration_sha256": MIGRATION,
            },
            "tests": report,
        }
        _json(root / "identity.json", identity)
        records[root] = {
            "identity": identity,
            "full_nodeids": full,
            "selected_nodeids": [full[serial]],
        }
        roots.append(root)

    monkeypatch.setattr(
        union,
        "verify_current55_component",
        lambda root, archive_path, **kwargs: records[root],
    )
    monkeypatch.setattr(
        union,
        "_archive_identity",
        lambda archive_path, **kwargs: (ARCHIVE, archive_files),
    )
    monkeypatch.setattr(
        union,
        "verify_linux_collection",
        lambda path, **kwargs: {
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()
        },
    )
    monkeypatch.setattr(
        union,
        "REQUIRED_CURRENT55_CASES",
        (("tests.unit.test_case_0", "test_case_0"),),
    )
    return tmp_path, roots, records, full


def test_exact_ten_component_join_is_hermetic_only(components):
    artifacts, _, _, full = components
    result = union.verify_union(
        artifacts, commit=COMMIT, tree=TREE, manifest_sha256=MANIFEST
    )
    assert result["hermetic_regression"] == "PASS"
    assert result["trading_acceptance"] is False
    assert result["full_collected_cases"] == len(full)
    assert result["selected_cases"] == len(full)


def test_missing_component_is_rejected(components):
    artifacts, roots, _, _ = components
    roots[-1].parent.rename(artifacts / "unexpected-component")
    with pytest.raises(ValueError, match="current55_union_component_set_incomplete"):
        union.verify_union(
            artifacts, commit=COMMIT, tree=TREE, manifest_sha256=MANIFEST
        )


def test_duplicate_run_id_is_rejected(components):
    artifacts, roots, records, _ = components
    duplicate = records[roots[0]]["identity"]["run_id"]
    records[roots[1]]["identity"]["run_id"] = duplicate
    _json(roots[1] / "identity.json", records[roots[1]]["identity"])
    with pytest.raises(ValueError, match="current55_union_component_identity_invalid"):
        union.verify_union(
            artifacts, commit=COMMIT, tree=TREE, manifest_sha256=MANIFEST
        )


@pytest.mark.parametrize("required", [(), (("tests.unit.missing", "test_missing"),)])
def test_required_case_missing_is_rejected(components, monkeypatch, required):
    artifacts, _, _, _ = components
    monkeypatch.setattr(union, "REQUIRED_CURRENT55_CASES", required)
    with pytest.raises(
        ValueError,
        match="current55_union_required_case_contract_invalid|current55_union_required_coverage_incomplete",
    ):
        union.verify_union(
            artifacts, commit=COMMIT, tree=TREE, manifest_sha256=MANIFEST
        )


def test_selected_collection_overlap_is_rejected(components):
    artifacts, roots, records, full = components
    records[roots[1]]["selected_nodeids"] = [full[0]]
    mode, index = "postgres", 1
    junit_name, _ = union.component_report_paths(mode, index)
    _junit(roots[1] / junit_name, full[0])
    records[roots[1]]["identity"]["tests"] = union.verify_pytest_report(
        roots[1] / junit_name
    )
    _json(roots[1] / "identity.json", records[roots[1]]["identity"])
    with pytest.raises(
        ValueError, match="current55_union_collection_or_report_diverged"
    ):
        union.verify_union(
            artifacts, commit=COMMIT, tree=TREE, manifest_sha256=MANIFEST
        )


def test_incomplete_union_is_rejected(components):
    artifacts, _, records, _ = components
    extra = "tests/unit/test_extra.py::test_extra"
    for record in records.values():
        record["full_nodeids"] = [*record["full_nodeids"], extra]
    with pytest.raises(
        ValueError, match="current55_union_required_coverage_incomplete"
    ):
        union.verify_union(
            artifacts, commit=COMMIT, tree=TREE, manifest_sha256=MANIFEST
        )
