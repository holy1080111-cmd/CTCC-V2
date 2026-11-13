"""Read back exact DB0056 Docker components and their historical DB0055 anchors."""

import io
import json
import math
import re
import tarfile
from pathlib import Path

from scripts import current0055_hermetic_contract as previous
from scripts import current0056_fitted_catalog_measurement_v1 as fitted_measurement
from scripts import devval_campaign_physical_catalog_v1 as devval_catalog
from scripts import verify_final_hermetic as shared
from scripts.current0055_catalog_receipt import verify_current55_catalog_report
from scripts.current0056_migration_pin import (
    HEAD,
    MIGRATION_INVENTORY_COUNT,
    MIGRATION_INVENTORY_SHA256,
    MIGRATION_PATH,
    MIGRATION_SHA256,
    PARENT,
    PARENT_INVENTORY_SHA256,
)
from scripts.evidence_json import read_evidence_json
from scripts.verify_linux_shard import (
    DEVVAL_CUSTODY_TEST_PATH,
    FITTED_CUSTODY_TEST_PATH,
    postgres_paths,
)

RUNNER_CONTRACT = "ctcc.current0056.hermetic.v1"
RUNNER_PATH = "scripts/verify_current0056_hermetic.py"
CATALOG_REPORT = previous.CATALOG_REPORT
RECOVERY_REPORT = previous.RECOVERY_REPORT
CATALOG_CASES = previous.CATALOG_CASES
RECOVERY_CASES = previous.RECOVERY_CASES
RESERVATION_UNIQUE = previous.RESERVATION_UNIQUE
FITTED_CUSTODY_CASES = tuple(
    (FITTED_CUSTODY_TEST_PATH.removesuffix(".py").replace("/", "."), name)
    for name in (
        "test_fitted_binding_role_separation_and_immutable_readback",
        "test_fitted_evaluation_is_once_and_after_permitted_access",
        "test_fitted_binding_refuses_changed_bytes_and_empty_downgrade",
    )
)
CURRENT56_CUSTODY_CASES = (
    *FITTED_CUSTODY_CASES,
    (
        FITTED_CUSTODY_TEST_PATH.removesuffix(".py").replace("/", "."),
        "test_actual56_runtime_raw_custody_seals_and_reads_under_tls",
    ),
)
CURRENT56_NATIVE_ROLE_CASES = tuple(
    (FITTED_CUSTODY_TEST_PATH.removesuffix(".py").replace("/", "."), name)
    for name in (
        "test_actual0056_full_native_role_observation_tls",
        "test_actual0056_full_native_role_rejects_swapped_roles_and_extra_grant",
    )
)
CURRENT56_PG1_REQUIRED_CASES = (*CURRENT56_CUSTODY_CASES, *CURRENT56_NATIVE_ROLE_CASES)
REQUIRED_CURRENT56_CASES = (
    *previous.REQUIRED_CURRENT55_CASES,
    *CURRENT56_PG1_REQUIRED_CASES,
)
_PARENT_UPGRADE = ("migration-upgrade-parent", "migration-parent-readback")
_EMPTY_ROUNDTRIP = (
    "migration-empty-downgrade",
    "migration-empty-downgrade-readback",
    "migration-empty-reupgrade",
    "migration-empty-reupgrade-identity",
)
_SHA = re.compile(r"[0-9a-f]{64}")
_GIT = re.compile(r"[0-9a-f]{40}")
_FULL_OBSERVED: set[str] = set()

digest = previous.digest
component_is_anchor = previous.component_is_anchor
component_test_stage = previous.component_test_stage
component_report_paths = previous.component_report_paths
_read = previous._read
_api_readback = previous._api_readback
_restart_time = previous._restart_time
_cleanup = previous._cleanup
_LINUX_PLATFORM_SKIPS_0056 = (
    (
        (
            "tests.unit.exchange.test_live_account_config_custody_v1",
            "test_windows_private_file_is_checked_before_write_and_remains_reserved",
        ),
        "requires native Windows ACLs",
    ),
)


def _verify_platform_skips(raw, *, mode, expected_skipped):
    previous._verify_platform_skips(
        raw,
        mode=mode,
        expected_skipped=expected_skipped,
        additional_exact_skips=_LINUX_PLATFORM_SKIPS_0056,
    )


def component_stage_names(mode, index):
    original = previous.component_stage_names(mode, index)
    if not component_is_anchor(mode, index):
        return original
    upgrade_index = original.index("migration-upgrade")
    reservation_index = original.index("migration-reservation-unique")
    return (
        *original[:upgrade_index],
        *_PARENT_UPGRADE,
        *original[upgrade_index:reservation_index],
        *_EMPTY_ROUNDTRIP,
        *original[reservation_index:],
    )


def requires_devval_catalog(mode, index):
    return mode in {"postgres", "full"} and DEVVAL_CUSTODY_TEST_PATH in postgres_paths(
        index
    )


def component_artifacts(mode, index):
    artifacts = previous.component_artifacts(mode, index)
    if requires_devval_catalog(mode, index):
        artifacts.add(f"test-results/{devval_catalog.HERMETIC_CATALOG_FILE}")
        artifacts.add(f"test-results/{fitted_measurement.FILE}")
    if component_is_anchor(mode, index):
        artifacts.update(
            f"{stage}.log" for stage in (*_PARENT_UPGRADE, *_EMPTY_ROUNDTRIP)
        )
    return artifacts


def _verify_devval_catalog_evidence(root, mode, index, identity, files):
    junit, _ = component_report_paths(mode, index)
    checked = shared.verify_pytest_report(
        root / junit,
        required_cases=(devval_catalog.HERMETIC_CATALOG_CASE,),
        allow_unrelated_skips=mode == "full",
    )
    if checked["sha256"] != identity["tests"]["sha256"] or any(
        files.get(path, {}).get("sha256") != pin
        for path, pin in devval_catalog.SQL_PINS.items()
    ):
        raise ValueError("current56_devval_catalog_source_or_test_changed")
    devval_catalog.verify_hermetic_linux_catalogs(
        _read(
            root,
            f"test-results/{devval_catalog.HERMETIC_CATALOG_FILE}",
            maximum=devval_catalog.MAX_HERMETIC_CATALOG_BYTES,
        )
    )
    shared.verify_pytest_report(
        root / junit,
        required_cases=(fitted_measurement.CASE,),
        allow_unrelated_skips=mode == "full",
    )
    fitted_measurement.verify_measurement(
        _read(root, f"test-results/{fitted_measurement.FILE}", maximum=1_200_000),
        identity=identity,
        source_files=files,
    )


def migration_digest(files):
    migration = {
        name: item["sha256"]
        for name, item in files.items()
        if name.startswith("migrations/") and name.endswith(".py")
    }
    parent = {name: item for name, item in files.items() if name != MIGRATION_PATH}
    inventory = digest(
        json.dumps(migration, sort_keys=True, separators=(",", ":")).encode()
    )
    if (
        migration.get(MIGRATION_PATH) != MIGRATION_SHA256
        or len(migration) != MIGRATION_INVENTORY_COUNT
        or inventory != MIGRATION_INVENTORY_SHA256
        or previous.migration_digest(parent) != PARENT_INVENTORY_SHA256
    ):
        raise ValueError("current56_archive_migration_scope_invalid")
    return inventory


def migration_readback(raw, expected_digest):
    actual = read_evidence_json(raw)
    if (
        actual
        != {
            "schema": "ctcc.migration_identity.v1",
            "status": "PASS",
            "source_head": HEAD,
            "database_head": HEAD,
            "migration_sha256": expected_digest,
            "execution_authority": False,
        }
        or actual.get("execution_authority") is not False
    ):
        raise ValueError("current56_database_identity_mismatch")
    return actual


def _collection(root, mode, index, identity):
    junit, selected_path = component_report_paths(mode, index)
    if mode != "full":
        selection = previous._collection(root, mode, index, identity)
        if mode == "postgres" and index == 1:
            checked = shared.verify_pytest_report(
                root / junit, required_cases=CURRENT56_PG1_REQUIRED_CASES
            )
            if (
                checked["sha256"] != identity["tests"]["sha256"]
                or checked["skipped"] != 0
            ):
                raise ValueError("current56_postgres_custody_execution_missing")
        return selection

    report = shared.verify_pytest_report(root / junit, allow_unrelated_skips=True)
    junit_raw = _read(root, junit)
    if digest(junit_raw) != report["sha256"]:
        raise ValueError("current56_report_identity_mismatch")
    _verify_platform_skips(junit_raw, mode=mode, expected_skipped=report["skipped"])
    selection = read_evidence_json(_read(root, selected_path))
    full = selection.get("full_nodeids")
    if (
        selection.get("schema") != "ctcc.current0056.full_collection.v1"
        or not isinstance(full, list)
        or not full
        or any(type(node) is not str for node in full)
        or len(full) != len(set(full))
        or selection.get("selected_nodeids") != full
        or selection.get("observed_nodeids") != full
        or type(selection.get("pytest_exitstatus")) is not int
        or selection["pytest_exitstatus"] != 0
    ):
        raise ValueError("current56_full_collection_invalid")
    required = shared.verify_pytest_report(
        root / junit,
        required_cases=REQUIRED_CURRENT56_CASES,
        allow_unrelated_skips=True,
    )
    if required["sha256"] != report["sha256"]:
        raise ValueError("current56_report_identity_mismatch")
    shared.verify_selected_junit_nodes(
        root / junit, selection["selected_nodeids"], expected_sha256=report["sha256"]
    )
    if report != identity["tests"]:
        raise ValueError("current56_report_identity_mismatch")
    return selection


def pytest_addoption(parser):
    parser.addoption("--ctcc-current56-full-collection")


def pytest_collection_finish(session):
    destination = session.config.getoption("--ctcc-current56-full-collection")
    if destination is None:
        return
    nodes = [item.nodeid for item in session.items]
    if (
        not nodes
        or len(nodes) != len(set(nodes))
        or any(
            item.nodeid.split("::", 1)[0]
            != item.path.relative_to(session.config.rootpath).as_posix()
            for item in session.items
        )
    ):
        raise ValueError("current56_full_collection_not_exact")
    _FULL_OBSERVED.clear()
    session._ctcc_current56_full = (destination, nodes)


def pytest_runtest_logreport(report):
    _FULL_OBSERVED.add(report.nodeid)


def pytest_sessionfinish(session, exitstatus):
    del exitstatus
    state = getattr(session, "_ctcc_current56_full", None)
    if state is None:
        return
    destination, nodes = state
    if set(nodes) != _FULL_OBSERVED:
        session.exitstatus = 1
    record = {
        "schema": "ctcc.current0056.full_collection.v1",
        "full_nodeids": nodes,
        "selected_nodeids": nodes,
        "observed_nodeids": [node for node in nodes if node in _FULL_OBSERVED],
        "pytest_exitstatus": int(session.exitstatus),
    }
    with Path(destination).open("x", encoding="utf-8") as output:
        json.dump(record, output, sort_keys=True, separators=(",", ":"))


def verify_current56_component(
    root: Path, archive_path: Path | bytes, *, commit, tree, manifest_sha256
):
    """Read a retained union archive or revalidate the runner's captured bytes."""
    if (
        root.is_symlink()
        or not root.is_dir()
        or not _GIT.fullmatch(commit)
        or not _GIT.fullmatch(tree)
        or not _SHA.fullmatch(manifest_sha256)
    ):
        raise ValueError("current56_component_input_invalid")
    if isinstance(archive_path, Path):
        if (
            archive_path.is_symlink()
            or not archive_path.is_file()
            or not 0 < archive_path.stat().st_size <= 128 * 1024 * 1024
        ):
            raise ValueError("current56_component_input_invalid")
        raw = archive_path.read_bytes()
    else:
        raw = archive_path
    if type(raw) is not bytes or not 0 < len(raw) <= 128 * 1024 * 1024:
        raise ValueError("current56_component_input_invalid")
    identity = read_evidence_json(_read(root, "identity.json"))
    mode = identity["suite"]
    index = (
        identity["postgres_component_index"]
        if mode == "postgres"
        else identity["shard_index"]
    )
    anchor = component_is_anchor(mode, index)
    stages = component_stage_names(mode, index)
    if (
        identity["commit"] != commit
        or identity["tree"] != tree
        or identity["manifest_sha256"] != manifest_sha256
        or identity["runner_contract"] != RUNNER_CONTRACT
        or identity["source_contract_head"] != HEAD
        or identity["build_context"] != "admitted_archive_bytes_v1"
        or (mode == "postgres" and identity["shard_index"] is not None)
        or (mode != "postgres" and identity["postgres_component_index"] is not None)
        or identity["component_validation"] != "PASS"
        or identity["hermetic_regression"]
        != ("PASS" if mode == "full" else "PENDING_UNION")
        or identity["cleanup"] != "PASS"
        or identity["host_credentials"] is not False
        or identity["host_env"] is not False
        or identity["trading_acceptance"] is not False
        or type(identity["external_order_writes"]) is not int
        or identity["external_order_writes"] != 0
    ):
        raise ValueError("current56_component_identity_invalid")
    if digest(raw) != identity["archive_sha256"]:
        raise ValueError("current56_archive_sha256_mismatch")
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
        if (
            archive.pax_headers.get("comment") != commit
            or shared.archive_tree(archive) != tree
        ):
            raise ValueError("current56_archive_git_identity_mismatch")
        files = shared.archive_files(archive)
    mapping = _read(root, "source-files.json")
    if (
        read_evidence_json(mapping) != files
        or digest(mapping) != identity["source_files_sha256"]
        or files["MANIFEST.sha256"]["sha256"] != manifest_sha256
        or files[RUNNER_PATH]["sha256"] != identity["runner_sha256"]
    ):
        raise ValueError("current56_archive_file_map_mismatch")

    hashes = read_evidence_json(_read(root, "results.sha256.json"))
    if set(hashes) != component_artifacts(mode, index) or any(
        not isinstance(value, str)
        or not _SHA.fullmatch(value)
        or digest(_read(root, name)) != value
        for name, value in hashes.items()
    ):
        raise ValueError("current56_artifact_hashes_invalid")
    rows = read_evidence_json(_read(root, "steps.json"))
    if tuple(row["name"] for row in rows) != stages or any(
        type(row["exit_code"]) is not int
        or row["exit_code"] != 0
        or type(row["elapsed_seconds"]) not in (float, int)
        or not math.isfinite(row["elapsed_seconds"])
        or row["elapsed_seconds"] < 0
        or "failure_type" in row
        or "policy_refusal" in row
        for row in rows
    ):
        raise ValueError("current56_component_steps_invalid")
    run_id = identity["run_id"]
    if not re.fullmatch(r"[0-9a-f]{12}", run_id):
        raise ValueError("current56_run_identity_invalid")
    prefix = f"ctcc-final-{run_id}-"
    network = read_evidence_json(_read(root, "network-inspect.log"))
    image = read_evidence_json(_read(root, "image-identity.log"))
    lock = read_evidence_json(_read(root, "dependency-lock.log"))
    report_stage = component_test_stage(mode, index)
    if (
        network["Name"] != prefix + "network"
        or network["Internal"] is not True
        or network["Labels"]["org.ctcc.validation.run"] != run_id
        or network["Containers"] not in ({}, None)
        or image["Id"] != identity["image_id"]
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", image["Id"])
        or image["Config"]["Labels"]["org.opencontainers.image.revision"] != commit
        or image["Config"]["Labels"]["org.ctcc.source.tree"] != tree
        or lock["target"] != "linux"
        or lock["lock_sha256"]
        != files["requirements/validation-linux-py312.lock"]["sha256"]
        or lock["accepted_full_validation_baseline"] is not False
        or _read(root, "copied-source-exact.log").strip() != str(len(files)).encode()
        or b"manifest check passed:" not in _read(root, "copied-manifest.log")
        or _read(root, "redis-ready.log").strip() != b"PONG"
        or b"accepting connections" not in _read(root, "postgres-ready.log")
        or _read(root, "migration-reservation-unique.log").strip() != RESERVATION_UNIQUE
        or _read(root, report_stage + ".log")
        .splitlines()
        .count(b"HERMETIC_EXECUTION_AUTHORITY=0")
        != 1
        or b"No new upgrade operations detected"
        not in _read(root, "migration-drift.log")
    ):
        raise ValueError("current56_component_runtime_readback_invalid")
    expected_migration = migration_digest(files)
    before = migration_readback(
        _read(root, "migration-identity.log"), expected_migration
    )
    if identity["migration"] != before:
        raise ValueError("current56_component_migration_not_bound")
    _cleanup(_read(root, "cleanup.json"), prefix, anchor=anchor)
    selection = _collection(root, mode, index, identity)
    if requires_devval_catalog(mode, index):
        _verify_devval_catalog_evidence(root, mode, index, identity, files)
    if anchor:
        catalog = verify_current55_catalog_report(root / CATALOG_REPORT)
        recovery = shared.verify_pytest_report(
            root / RECOVERY_REPORT, required_cases=RECOVERY_CASES
        )
        first_id, first_time = _restart_time(
            _read(root, "api-instance-before.log"), prefix + "api"
        )
        last_id, last_time = _restart_time(
            _read(root, "api-instance-after.log"), prefix + "api"
        )
        _api_readback(_read(root, "api-before-restart.log"))
        _api_readback(_read(root, "api-after-restart.log"))
        if (
            identity["catalog"] != catalog
            or identity["recovery"] != recovery
            or first_id != last_id
            or first_time >= last_time
            or _read(root, "redis-ready-after-restart.log").strip() != b"PONG"
            or b"accepting connections"
            not in _read(root, "postgres-ready-after-restart.log")
            or migration_readback(
                _read(root, "migration-identity-after-restart.log"), expected_migration
            )
            != before
            or b"No new upgrade operations detected"
            not in _read(root, "migration-drift-after-restart.log")
            or _read(root, "migration-parent-readback.log").strip()
            != PARENT.encode("ascii")
            or _read(root, "migration-empty-downgrade-readback.log").strip()
            != PARENT.encode("ascii")
            or migration_readback(
                _read(root, "migration-empty-reupgrade-identity.log"),
                expected_migration,
            )
            != before
            or read_evidence_json(_read(root, "migration-supported-readback.log"))
            != shared.expected_migration_revision_readback("0001")
        ):
            raise ValueError("current56_anchor_runtime_readback_invalid")
    return {
        "identity": identity,
        "full_nodeids": selection["full_nodeids"],
        "selected_nodeids": selection["selected_nodeids"],
    }
