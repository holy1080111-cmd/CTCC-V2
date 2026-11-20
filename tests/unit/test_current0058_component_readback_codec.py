"""DECLARED SYNTHETIC parser fixtures only; never executed Docker/PG evidence."""

import io
import json
import tarfile
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from app.domain import native_protective_hold_schema_v58 as protective
from app.domain import native_submission_binding_schema_v58 as submission
from scripts import current0058_full_hermetic_contract_v1 as contract
from scripts import verify_final_hermetic as shared
from tests.historical_source_prefix_v1 import historical_migration_paths


@pytest.fixture
def declared_synthetic_component(tmp_path, monkeypatch):
    source = Path(__file__).resolve().parents[2]
    paths = [
        *historical_migration_paths(source, target="0058"),
        *(
            source / name
            for name in (
                "MANIFEST.sha256",
                contract.RUNNER_PATH,
                "scripts/current0058_profile_measurement_v1.py",
                "scripts/current0057_g12_measurement_v1.py",
                "scripts/historical0057_on_current0058_observation_v1.py",
                "requirements/validation-linux-py312.lock",
            )
        ),
    ]
    archive_bytes = io.BytesIO()
    with tarfile.open(
        fileobj=archive_bytes,
        mode="w",
        format=tarfile.PAX_FORMAT,
        pax_headers={"comment": "a" * 40},
    ) as stream:
        for path in sorted(paths):
            raw = path.read_bytes()
            info = tarfile.TarInfo(path.relative_to(source).as_posix())
            info.size = len(raw)
            stream.addfile(info, io.BytesIO(raw))
    archive = archive_bytes.getvalue()
    with tarfile.open(fileobj=io.BytesIO(archive)) as stream:
        files, tree = (
            shared.archive_files(stream),
            shared.archive_tree(stream),
        )
    # Only parser mechanics are under test. The missing Git source/runtime
    # provenance gate is deliberately mocked; no native owner is constructed.
    monkeypatch.setattr(contract, "require_host_code", lambda _files: None)
    root = tmp_path / "DECLARED_SYNTHETIC_COMPONENT"
    root.mkdir()
    mapping = contract.canonical(files)
    identity = {
        "runner_contract": contract.RUNNER_CONTRACT,
        "validation_scope": contract.SCOPE,
        "source_contract_head": "0058",
        "commit": "a" * 40,
        "tree": tree,
        "archive_sha256": contract.digest(archive),
        "build_context": "admitted_archive_bytes_v1",
        "runner_sha256": files[contract.RUNNER_PATH]["sha256"],
        "source_files_sha256": contract.digest(mapping),
        "manifest_sha256": files["MANIFEST.sha256"]["sha256"],
        "suite": "postgres",
        "shard_index": None,
        "postgres_component_index": 2,
        "run_id": "123456abcdef",
        "image_id": "sha256:" + "1" * 64,
        "host_credentials": False,
        "host_env": False,
        "external_order_writes": 0,
        "trading_acceptance": False,
        "component_validation": "PASS",
        "hermetic_regression": "PENDING_UNION",
        "cleanup": "PASS",
    }
    migration = {
        "schema": "ctcc.migration_identity.v1",
        "status": "PASS",
        "source_head": "0058",
        "database_head": "0058",
        "migration_sha256": contract.migration_digest(files),
        "execution_authority": False,
    }
    identity["migration"] = migration
    stages = contract.component_stage_names("postgres", 2)
    values = {name + ".log": b"DECLARED_SYNTHETIC_STAGE_LOG" for name in stages}
    prefix = "ctcc-final-" + identity["run_id"] + "-"
    values.update(
        {
            "source-files.json": mapping,
            "steps.json": contract.canonical(
                [
                    {"name": name, "exit_code": 0, "elapsed_seconds": 0.01}
                    for name in stages
                ]
            ),
            "image-identity.log": contract.canonical(
                {
                    "Id": identity["image_id"],
                    "Config": {
                        "Labels": {
                            "org.opencontainers.image.revision": "a" * 40,
                            "org.ctcc.source.tree": tree,
                        }
                    },
                }
            ),
            "network-inspect.log": contract.canonical(
                {
                    "Name": prefix + "network",
                    "Internal": True,
                    "Containers": {},
                    "Labels": {"org.ctcc.validation.run": identity["run_id"]},
                }
            ),
            "dependency-lock.log": contract.canonical(
                {
                    "target": "linux",
                    "lock_sha256": files["requirements/validation-linux-py312.lock"][
                        "sha256"
                    ],
                    "accepted_full_validation_baseline": False,
                }
            ),
            "copied-manifest.log": b"manifest check passed: DECLARED_SYNTHETIC\n",
            "copied-source-exact.log": str(len(files)).encode(),
            "postgres-ready.log": b"CTCC_EPHEMERAL_PG_AUTOVACUUM=off\naccepting connections\n",
            "redis-ready.log": b"PONG\n",
            "migration-reservation-unique.log": contract.previous.RESERVATION_UNIQUE,
            "migration-drift.log": b"No new upgrade operations detected",
            "migration-identity.log": contract.canonical(migration),
            "pytest-postgres-2.log": b"HERMETIC_EXECUTION_AUTHORITY=0\n",
            "cleanup.json": contract.canonical(
                [
                    {"kind": kind, "name": name, "status": "REMOVED"}
                    for kind, name in sorted(
                        contract.cleanup_inventory("postgres", 2, prefix)
                    )
                ]
            ),
            "test-results/postgres-progress.log": b"DECLARED_SYNTHETIC_PROGRESS_ONLY\n",
        }
    )
    document = ET.Element("testsuites")
    suite = ET.SubElement(
        document,
        "testsuite",
        tests=str(len(contract.SUCCESSOR_CASES)),
        failures="0",
        errors="0",
        skipped="0",
    )
    for module, name in contract.SUCCESSOR_CASES:
        ET.SubElement(suite, "testcase", classname=module, name=name)
    junit, selected = contract.component_report_paths("postgres", 2)
    values[junit] = ET.tostring(document)
    nodes = [
        module.replace(".", "/") + ".py::" + name
        for module, name in contract.SUCCESSOR_CASES
    ]
    values[selected] = contract.canonical(
        contract.collection._record("postgres", 2, nodes, nodes, nodes, 0)
    )
    for name, raw in values.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    identity["tests"] = shared.verify_pytest_report(
        root / junit, required_cases=contract.SUCCESSOR_CASES
    )
    source_identity = contract.g12.source_identity(identity, files, mapping)
    for path, case in zip(contract.g12.REPORTS, contract.g12.CASES, strict=True):
        value = {
            "schema": "ctcc.current0058.profile_measurement.v1",
            "source": source_identity,
            "case": case[1],
            "database_head": "0058",
            "server_version_num": "170011",
            "cluster_system_identifier_sha256": "2" * 64,
            "actual_tls": True,
            "role_oids": {"writer": 11, "custody": 12, "frame_reader": 13},
            "grant_statements": 44,
            "acl_shape_sha256": "3" * 64,
            "actual_g12_catalog_sha256": "4" * 64,
            "g12_definitions_sha256": "5" * 64,
            "inherited_native_definitions_sha256": "6" * 64,
            "native_closure_body_sha256": protective.CURRENT_BODY_SHA256,
            "native_submission_body_sha256": submission.CURRENT_BODY_SHA256,
            "native_protective_flat_body_sha256": protective.PREDICATE_BODY_SHA256,
            "native_protective_indexes_sha256": "7" * 64,
            "actual_protective_catalog_sha256": "8" * 64,
            "startup_admission": False,
            "execution_authority": False,
            "trading_acceptance": False,
        }
        (root / path).write_bytes(contract.canonical(value))
    from scripts import current0057_g12_measurement_v1 as old_measurement
    from scripts.historical0057_on_current0058_observation_v1 import (
        historical_source_identity,
    )

    history_source = historical_source_identity(identity, files, mapping)
    from tests.unit.test_current0057_g12_smoke_contract import _measurement

    for history_path, history_case in zip(
        contract.historical57.REPORTS, contract.historical57.CASES, strict=True
    ):
        old_value = _measurement(history_source)
        old_value["case"] = history_case[1]
        old_measurement.verify_measurement(
            contract.canonical(old_value), source=history_source, case=history_case[1]
        )
        (root / history_path).write_bytes(contract.canonical(old_value))
    (root / "identity.json").write_bytes(contract.canonical(identity))

    def rehash():
        (root / "results.sha256.json").write_bytes(
            contract.canonical(
                {
                    name: contract.digest((root / name).read_bytes())
                    for name in contract.component_artifacts("postgres", 2)
                }
            )
        )

    rehash()
    return root, archive, identity, rehash


def _verify(fixture):
    root, raw, identity, _ = fixture
    return contract.verify_component(
        root,
        raw,
        commit=identity["commit"],
        tree=identity["tree"],
        manifest_sha256=identity["manifest_sha256"],
    )


def test_declared_synthetic_component_codec_replays_own_hashes_and_receipts(
    declared_synthetic_component,
):
    row = _verify(declared_synthetic_component)
    assert (
        len(row["actual0058_profiles"]) == 1
        and row["identity"]["migration"]["database_head"] == "0058"
    )
    assert row["identity"]["trading_acceptance"] is False


@pytest.mark.parametrize(
    "change",
    [
        "projected_head",
        "missing_cleanup",
        "failed_stage",
        "stale_receipt_source",
        "extra_update_grant",
        "no_tls",
        "changed_barrier_log",
        "altered_artifact_without_rehash",
    ],
)
def test_rehashed_synthetic_claims_cannot_hide_changed_physical_source_or_failed_invariants(
    declared_synthetic_component, change
):
    root, _, _, rehash = declared_synthetic_component
    if change == "projected_head":
        path = root / "migration-identity.log"
        value = json.loads(path.read_bytes())
        value["database_head"] = "0056"
    elif change == "missing_cleanup":
        path = root / "cleanup.json"
        value = json.loads(path.read_bytes())[1:]
    elif change == "failed_stage":
        path = root / "steps.json"
        value = json.loads(path.read_bytes())
        value[-1]["exit_code"] = 1
    elif change in {"stale_receipt_source", "extra_update_grant", "no_tls"}:
        path = root / contract.g12.REPORTS[0]
        value = json.loads(path.read_bytes())
        if change == "stale_receipt_source":
            value["source"]["commit"] = "f" * 40
        elif change == "extra_update_grant":
            value["grant_statements"] = 45
        else:
            value["actual_tls"] = False
    else:
        path = root / "pytest-postgres-2.log"
        value = None
        path.write_bytes(b"HERMETIC_EXECUTION_AUTHORITY=1\n")
    if value is not None:
        path.write_bytes(contract.canonical(value))
    if change != "altered_artifact_without_rehash":
        rehash()
    with pytest.raises(ValueError):
        _verify(declared_synthetic_component)
