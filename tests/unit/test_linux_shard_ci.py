"""Small source-bound proofs for the Linux CI suite union gate."""

from __future__ import annotations

import hashlib
import json
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from scripts.verify_final_hermetic import (
    ARCHIVE_REQUIRED_CASES,
    verify_linux_collection,
    verify_pytest_report,
)
from scripts.verify_linux_shard import (
    POSTGRES_TEST_PATHS,
    SHARD_COUNT,
    module_shard,
    selected_for,
)
from scripts.verify_linux_shard_union import COMMON_STAGES, verify_union

COMMIT = "a" * 40
TREE = "b" * 40
ARCHIVE = b"reviewed-git-archive"
MANIFEST = b"reviewed-manifest\n"
SOURCE_FILES = b"{}"


def test_gate3_ack_in_postgres_suite_only():
    path = "tests/integration/test_gate3_schedule_publication_ack.py"
    nodeid = path + "::test_ack_requires_committed_pin_and_replays_after_restart"
    assert path in POSTGRES_TEST_PATHS
    assert selected_for("postgres", None, nodeid)
    assert all(not selected_for("shard", index, nodeid) for index in range(SHARD_COUNT))


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _full_nodeids() -> list[str]:
    postgres = [f"{path}::test_module_smoke" for path in sorted(POSTGRES_TEST_PATHS)]
    postgres += [
        f"{module.replace('.', '/')}.py::{case}"
        for module, case in ARCHIVE_REQUIRED_CASES
    ]
    modules = []
    candidate = 0
    while len(modules) < SHARD_COUNT:
        path = f"tests/unit/test_synthetic_{candidate}.py"
        if module_shard(path) not in {module_shard(item) for item in modules}:
            modules.append(path)
        candidate += 1
    return postgres + [f"{path}::test_shard_smoke" for path in modules]


def _junit(path: Path, nodeids: list[str]):
    suites = ET.Element("testsuites")
    suite = ET.SubElement(
        suites,
        "testsuite",
        tests=str(len(nodeids)),
        failures="0",
        errors="0",
        skipped="0",
    )
    for nodeid in nodeids:
        module, name = nodeid.split("::", 1)
        ET.SubElement(
            suite,
            "testcase",
            classname=module.removesuffix(".py").replace("/", "."),
            name=name,
        )
    path.write_bytes(ET.tostring(suites))


def _artifacts(tmp_path: Path) -> tuple[Path, list[str]]:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    full = _full_nodeids()
    migration = {"schema": "ctcc.migration_identity.v1", "status": "PASS"}
    for mode, index in (
        ("postgres", None),
        *(("shard", i) for i in range(SHARD_COUNT)),
    ):
        name = (
            f"ctcc-linux-postgres-{COMMIT}"
            if mode == "postgres"
            else f"ctcc-linux-shard-{index}-{COMMIT}"
        )
        attachment = artifacts / name
        root = attachment / "ctcc-validation"
        results = root / "test-results"
        results.mkdir(parents=True)
        (attachment / "ctcc-source.tar").write_bytes(ARCHIVE)
        selected = [nodeid for nodeid in full if selected_for(mode, index, nodeid)]
        stage = "postgres-intent" if mode == "postgres" else f"linux-shard-{index}"
        xml = results / f"{stage}.xml"
        _junit(xml, selected)
        report = verify_pytest_report(
            xml,
            required_cases=ARCHIVE_REQUIRED_CASES if mode == "postgres" else (),
            required_modules=(
                tuple(
                    sorted(
                        path.removesuffix(".py").replace("/", ".")
                        for path in POSTGRES_TEST_PATHS
                    )
                )
                if mode == "postgres"
                else ()
            ),
            allow_unrelated_skips=mode == "shard",
        )
        collection_file = results / (
            "postgres-collection.json"
            if mode == "postgres"
            else f"{stage}-collection.json"
        )
        collection_file.write_text(
            json.dumps(
                {
                    "schema": "ctcc.linux_full_collection_shard.v1",
                    "mode": mode,
                    "shard_index": index,
                    "shard_count": SHARD_COUNT,
                    "full_nodeids": full,
                    "selected_nodeids": selected,
                    "observed_nodeids": selected,
                    "pytest_exitstatus": 0,
                }
            )
        )
        collection = verify_linux_collection(
            collection_file,
            mode=mode,
            index=index,
            report=report,
        )
        identity = {
            "commit": COMMIT,
            "tree": TREE,
            "manifest_sha256": _sha(MANIFEST),
            "archive_sha256": _sha(ARCHIVE),
            "source_files_sha256": _sha(SOURCE_FILES),
            "runner_sha256": "c" * 64,
            "suite": mode,
            "shard_index": index,
            "shard_count": SHARD_COUNT,
            "host_credentials": False,
            "host_env": False,
            "external_order_writes": 0,
            "trading_acceptance": False,
            "component_validation": "PASS",
            "hermetic_regression": "PENDING_UNION",
            "cleanup": "PASS",
            "migration": migration,
            "collection": collection,
            "postgres_tests" if mode == "postgres" else "linux_tests": report,
        }
        if mode == "postgres":
            identity["synthetic_process_pg_redis_crash_durability"] = "PASS"
            identity["api_service_restart"] = "PASS"
        (root / "identity.json").write_text(json.dumps(identity))
        (root / "steps.json").write_text(
            json.dumps(
                [
                    {"name": stage_name, "exit_code": 0}
                    for stage_name in (*COMMON_STAGES, stage)
                ]
            )
        )
        (root / "source-files.json").write_bytes(SOURCE_FILES)
        files = (
            root / "identity.json",
            root / "steps.json",
            root / "source-files.json",
            xml,
            collection_file,
        )
        (root / "results.sha256.json").write_text(
            json.dumps(
                {
                    path.relative_to(root).as_posix(): _sha(path.read_bytes())
                    for path in files
                }
            )
        )
    return artifacts, full


def test_every_case_has_one_deterministic_owner():
    full = _full_nodeids()
    assert len(full) == len(set(full))
    for nodeid in full:
        assert (
            int(selected_for("postgres", None, nodeid))
            + sum(selected_for("shard", index, nodeid) for index in range(SHARD_COUNT))
            == 1
        )


def test_exact_union_passes_and_missing_shard_fails(tmp_path):
    artifacts, full = _artifacts(tmp_path)
    result = verify_union(
        artifacts, commit=COMMIT, tree=TREE, manifest_sha256=_sha(MANIFEST)
    )
    assert result["hermetic_regression"] == "PASS"
    assert result["selected_cases"] == len(full)
    shutil.rmtree(artifacts / f"ctcc-linux-shard-7-{COMMIT}")
    with pytest.raises(ValueError, match="linux_union_component_set_incomplete"):
        verify_union(
            artifacts, commit=COMMIT, tree=TREE, manifest_sha256=_sha(MANIFEST)
        )


def test_duplicate_shard_artifact_and_modified_collection_fail(tmp_path):
    artifacts, _ = _artifacts(tmp_path)
    duplicate = artifacts / f"ctcc-linux-shard-0-copy-{COMMIT}"
    shutil.copytree(artifacts / f"ctcc-linux-shard-0-{COMMIT}", duplicate)
    with pytest.raises(ValueError, match="linux_union_component_set_incomplete"):
        verify_union(
            artifacts, commit=COMMIT, tree=TREE, manifest_sha256=_sha(MANIFEST)
        )
    shutil.rmtree(duplicate)
    collection = (
        artifacts
        / f"ctcc-linux-shard-0-{COMMIT}"
        / "ctcc-validation"
        / "test-results"
        / "linux-shard-0-collection.json"
    )
    collection.write_bytes(collection.read_bytes() + b" ")
    with pytest.raises(ValueError, match="linux_union_hash_mismatch"):
        verify_union(
            artifacts, commit=COMMIT, tree=TREE, manifest_sha256=_sha(MANIFEST)
        )


def test_tampered_result_hash_and_identity_drift_fail(tmp_path):
    artifacts, _ = _artifacts(tmp_path)
    root = artifacts / f"ctcc-linux-shard-0-{COMMIT}" / "ctcc-validation"
    hash_map = root / "results.sha256.json"
    hashes = json.loads(hash_map.read_text())
    hashes["identity.json"] = "0" * 64
    hash_map.write_text(json.dumps(hashes))
    with pytest.raises(ValueError, match="linux_union_hash_mismatch"):
        verify_union(
            artifacts, commit=COMMIT, tree=TREE, manifest_sha256=_sha(MANIFEST)
        )
