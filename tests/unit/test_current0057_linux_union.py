"""Synthetic eleven-component join; component DB/runtime proof is mocked out."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from scripts import current0057_full_hermetic_contract_v1 as contract
from scripts import versioned_hermetic_engine_v1 as engine
from scripts import versioned_hermetic_union_v1 as union

COMMIT, TREE, MANIFEST, ARCHIVE = "a" * 40, "b" * 40, "c" * 64, "d" * 64


def _json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(contract.canonical(value))


def _junit(path, nodes):
    document = ET.Element("testsuites")
    suite = ET.SubElement(
        document,
        "testsuite",
        tests=str(len(nodes)),
        failures="0",
        errors="0",
        skipped="0",
    )
    for node in nodes:
        source, name = node.split("::", 1)
        ET.SubElement(
            suite,
            "testcase",
            classname=source.removesuffix(".py").replace("/", "."),
            name=name,
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(ET.tostring(document))


@pytest.fixture
def components(tmp_path, monkeypatch):
    source = Path(__file__).resolve().parents[2]
    files = {
        path.relative_to(source).as_posix(): {
            "sha256": contract.digest(path.read_bytes()),
            "size": path.stat().st_size,
        }
        for path in (source / "migrations").rglob("*.py")
    }
    files["MANIFEST.sha256"] = {"sha256": MANIFEST}
    mapping = contract.canonical(files)
    full = [
        module.replace(".", "/") + ".py::" + name
        for module, name in contract.REQUIRED_CASES
    ]
    for shard in range(8):
        ordinal = 0
        while True:
            path = f"tests/unit/test_DECLARED_SYNTHETIC_UNION_{ordinal}.py"
            if contract.collection.previous.module_shard(path) == shard:
                full.append(path + "::test_synthetic_join_only")
                break
            ordinal += 1
    assert len(full) == len(set(full))
    records, roots = {}, []
    for serial, (mode, index, name) in enumerate(union.specs_for(contract, COMMIT)):
        root = tmp_path / name / "ctcc-validation"
        root.mkdir(parents=True)
        (root.parent / "ctcc-source.tar").write_bytes(
            b"DECLARED_SYNTHETIC_ARCHIVE_JOIN_ONLY"
        )
        (root / "source-files.json").write_bytes(mapping)
        selected = [
            node for node in full if contract.collection.selected_for(mode, index, node)
        ]
        assert selected
        junit, collection_path = contract.component_report_paths(mode, index)
        _junit(root / junit, selected)
        _json(
            root / collection_path,
            contract.collection._record(mode, index, full, selected, selected, 0),
        )
        report = union.shared.verify_pytest_report(
            root / junit,
            required_cases=contract.required_cases(mode, index),
            allow_unrelated_skips=mode != "postgres",
        )
        identity = {
            "suite": mode,
            "shard_index": index if mode == "shard" else None,
            "postgres_component_index": index if mode == "postgres" else None,
            "runner_contract": contract.RUNNER_CONTRACT,
            "source_contract_head": "0057",
            "run_id": f"{serial:012x}",
            "archive_sha256": ARCHIVE,
            "source_files_sha256": contract.digest(mapping),
            "migration": {"source_head": "0057", "database_head": "0057"},
            "tests": report,
        }
        _json(root / "identity.json", identity)
        fake_observation = {
            "database_head": "0057",
            "cluster_system_identifier_sha256": "1" * 64,
            "inherited_native_definitions_sha256": "2" * 64,
            "g12_definitions_sha256": "3" * 64,
        }
        records[root] = {
            "identity": identity,
            "full_nodeids": full,
            "selected_nodeids": selected,
            "historical0056": {"DECLARED_SYNTHETIC_JOIN_ONLY": True}
            if contract.requires_historical(mode, index)
            else None,
            "actual0057_g12": [dict(fake_observation) for _ in range(4)]
            if contract.requires_g12(mode, index)
            else [],
        }
        roots.append(root)
    monkeypatch.setattr(
        contract, "verify_component", lambda root, *_args, **_kw: records[root]
    )
    monkeypatch.setattr(
        union, "_archive_identity", lambda *_args, **_kw: (ARCHIVE, files)
    )
    return tmp_path, roots, records, full


def _verify(path):
    return union.verify_union(
        engine.HermeticProfileV1.CURRENT0057,
        path,
        commit=COMMIT,
        tree=TREE,
        manifest_sha256=MANIFEST,
    )


def test_synthetic_join_counts_its_own_eleven_reports_without_borrowing56(components):
    path, _, _, full = components
    result = _verify(path)
    assert (
        result["full_collected_cases"]
        == result["selected_cases"]
        == result["passed_cases"]
        == len(full)
    )
    assert (
        result["source_contract_head"]
        == result["actual0057_g12"]["database_head"]
        == "0057"
    )
    assert result["postgres_component_count"] == 3 and result["shard_count"] == 8
    assert result["execution_authority"] is result["trading_acceptance"] is False


@pytest.mark.parametrize(
    "change",
    [
        "missing",
        "duplicate_run",
        "wrong_head",
        "wrong_db_head",
        "missing_actual57",
        "extra_component",
        "changed_collection",
        "changed_source_map",
    ],
)
def test_union_denies_missing_mixed_duplicated_or_relabelled_components(
    components, change
):
    path, roots, records, full = components
    root = roots[2]
    record = records[root]
    if change == "missing":
        roots[-1].parent.rename(path / "unexpected_component")
    elif change == "extra_component":
        (path / "EXTRA_DECLARED_SYNTHETIC_COMPONENT").mkdir()
    elif change == "duplicate_run":
        record["identity"]["run_id"] = records[roots[0]]["identity"]["run_id"]
        _json(root / "identity.json", record["identity"])
    elif change == "wrong_head":
        record["identity"]["source_contract_head"] = "0056"
        _json(root / "identity.json", record["identity"])
    elif change == "wrong_db_head":
        record["identity"]["migration"]["database_head"] = "0056"
        _json(root / "identity.json", record["identity"])
    elif change == "missing_actual57":
        record["actual0057_g12"] = []
    elif change == "changed_collection":
        _, name = contract.component_report_paths("postgres", 2)
        raw = json.loads((root / name).read_bytes())
        raw["full_nodeids"] = list(reversed(full))
        _json(root / name, raw)
    else:
        (root / "source-files.json").write_bytes(b"{}")
    with pytest.raises(ValueError):
        _verify(path)


def test_union_requires_each_reviewed_case_in_executed_junit_even_if_full_nodes_look_complete(
    components, monkeypatch
):
    path, _, _, _ = components
    monkeypatch.setattr(
        contract,
        "REQUIRED_CASES",
        (
            *contract.REQUIRED_CASES,
            ("tests.unit.test_missing_source_case", "test_missing"),
        ),
    )
    with pytest.raises(ValueError):
        _verify(path)
