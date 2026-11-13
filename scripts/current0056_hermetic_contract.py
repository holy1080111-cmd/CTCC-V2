"""Read back exact DB0056 Docker components and their historical DB0055 anchors."""

import io
import json
import math
import re
import tarfile
from pathlib import Path

from scripts import current0055_hermetic_contract as previous
from scripts import current0056_fitted_catalog_measurement_v1 as fitted_measurement
from scripts import current0056_postgrant_acl_measurement_v1 as postgrant_acl
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
    DB0056_CONTROL_MONITOR_TEST_PATH,
    DB0056_REPORT_RESUME_TEST_PATH,
    DEVVAL_CUSTODY_TEST_PATH,
    FITTED_CUSTODY_TEST_PATH,
    module_shard,
    postgres_paths,
)

RUNNER_CONTRACT = "ctcc.current0056.hermetic.v1"
RUNNER_PATH = "scripts/verify_current0056_hermetic.py"
CATALOG_REPORT = previous.CATALOG_REPORT
RECOVERY_REPORT = previous.RECOVERY_REPORT
CATALOG_CASES = previous.CATALOG_CASES
RECOVERY_CASES = previous.RECOVERY_CASES
RESERVATION_UNIQUE = previous.RESERVATION_UNIQUE
ROOT_TRUST_STAGE = "linux-root-trust-install"
ROOT_TRUST_REPORT = "test-results/linux-root-trust-install.xml"
_ROOT_TRUST_SPECS = (
    (
        "tests/unit/test_native_startup_trust_0056_v1.py",
        "test_real_linux_directory_descriptors_with_nonroot_child",
        "requires Linux with an administrator to prepare a disposable filesystem",
    ),
    (
        "tests/unit/test_install_native_startup_trust_0056_v1.py",
        "test_linux_install_is_single_link_and_reopens_as_nonroot_without_replacement",
        "requires Linux and administrator to prepare a disposable filesystem",
    ),
)
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
        "test_actual0056_verified_ca_rejects_wrong_server_hostname",
        "test_actual0056_full_native_role_observation_tls",
        "test_actual0056_full_native_role_rejects_swapped_roles_and_extra_grant",
        "test_actual0056_native_lifespan_denies_before_recovery_cas",
        "test_actual0056_owner_grant_lock_and_rollback",
        "test_actual0056_restricted_account_control_read_replays_exact_scope",
        "test_actual0056_restricted_ledger_replays_retained_control_bound_intent",
        "test_actual0056_controlled_partition_uses_inherited_armed_append",
    )
)
CONTROL_MONITOR_CASE = (
    DB0056_CONTROL_MONITOR_TEST_PATH.removesuffix(".py").replace("/", "."),
    "test_actual0056_control_monitor_joins_exact_release_and_restricted_tls",
)
CONTROL_LIFECYCLE_CASE = (
    DB0056_CONTROL_MONITOR_TEST_PATH.removesuffix(".py").replace("/", "."),
    "test_actual0056_restricted_control_repository_reads_back_lifecycle",
)
COVERAGE_CHECKPOINT_CASE = (
    DB0056_CONTROL_MONITOR_TEST_PATH.removesuffix(".py").replace("/", "."),
    "test_actual0056_coverage_replays_held_checkpoint_without_authority",
)
LEGACY_CONTROL_GENESIS_CASE = (
    DB0056_CONTROL_MONITOR_TEST_PATH.removesuffix(".py").replace("/", "."),
    "test_actual0056_owner_initializes_legacy_singleton_only_as_stopped",
)
REPORT_RESUME_CASE = (
    DB0056_REPORT_RESUME_TEST_PATH.removesuffix(".py").replace("/", "."),
    "test_actual0056_report_resume_refuses_missing_cleanup_no_new_rows",
)
INITIAL_TRANSPORT_CASE = (
    DB0056_REPORT_RESUME_TEST_PATH.removesuffix(".py").replace("/", "."),
    "test_actual0056_report_initial_transport_appends_atomically_without_eligibility[long-net]",
)
CURRENT56_PG1_REQUIRED_CASES = (
    *CURRENT56_CUSTODY_CASES,
    *CURRENT56_NATIVE_ROLE_CASES,
    CONTROL_MONITOR_CASE,
    CONTROL_LIFECYCLE_CASE,
    COVERAGE_CHECKPOINT_CASE,
    LEGACY_CONTROL_GENESIS_CASE,
    REPORT_RESUME_CASE,
    INITIAL_TRANSPORT_CASE,
)
PREDICTION_CUSTODY_CASE = (
    "tests.integration.test_public_runtime_bundle_custody_repository",
    "test_validation_prediction_precursor_uses_actual_db0046_commit_and_readback",
)
REQUIRED_CURRENT56_CASES = (
    *previous.REQUIRED_CURRENT55_CASES,
    *CURRENT56_PG1_REQUIRED_CASES,
    PREDICTION_CUSTODY_CASE,
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
_EPHEMERAL_AUTOVACUUM = b"CTCC_EPHEMERAL_PG_AUTOVACUUM="
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


def _root_trust_specs(mode, index):
    return tuple(
        (path, name, reason)
        for path, name, reason in _ROOT_TRUST_SPECS
        if mode == "full" or mode == "shard" and index == module_shard(path)
    )


def root_trust_cases(mode, index):
    return tuple(
        (path.removesuffix(".py").replace("/", "."), name)
        for path, name, _ in _root_trust_specs(mode, index)
    )


def _linux_platform_skips(root_trust_verified):
    return (
        *_LINUX_PLATFORM_SKIPS_0056,
        *(
            ((path.removesuffix(".py").replace("/", "."), name), reason)
            for path, name, reason in _ROOT_TRUST_SPECS
            if (path.removesuffix(".py").replace("/", "."), name) in root_trust_verified
        ),
    )


def _verify_platform_skips(raw, *, mode, expected_skipped, root_trust_verified=()):
    previous._verify_platform_skips(
        raw,
        mode=mode,
        expected_skipped=expected_skipped,
        additional_exact_skips=_linux_platform_skips(root_trust_verified),
    )


def requires_root_trust(mode, index):
    return bool(root_trust_cases(mode, index))


def component_stage_names(mode, index):
    original = previous.component_stage_names(mode, index)
    if requires_root_trust(mode, index):
        report_index = original.index(component_test_stage(mode, index))
        original = (
            *original[:report_index],
            ROOT_TRUST_STAGE,
            *original[report_index:],
        )
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


def postgres_ready_readback(raw, *, ephemeral_tls):
    settings = [
        line for line in raw.splitlines() if line.startswith(_EPHEMERAL_AUTOVACUUM)
    ]
    return b"accepting connections" in raw and settings == (
        [_EPHEMERAL_AUTOVACUUM + b"off"] if ephemeral_tls else []
    )


def component_artifacts(mode, index):
    artifacts = previous.component_artifacts(mode, index)
    if requires_root_trust(mode, index):
        artifacts.update((f"{ROOT_TRUST_STAGE}.log", ROOT_TRUST_REPORT))
    if requires_devval_catalog(mode, index):
        artifacts.add(f"test-results/{devval_catalog.HERMETIC_CATALOG_FILE}")
        artifacts.add(f"test-results/{fitted_measurement.FILE}")
        artifacts.add(f"test-results/{fitted_measurement.INHERITED_FILE}")
        artifacts.add(f"test-results/{postgrant_acl.FILE}")
    if component_is_anchor(mode, index):
        artifacts.update(
            f"{stage}.log" for stage in (*_PARENT_UPGRADE, *_EMPTY_ROUNDTRIP)
        )
    return artifacts


def verify_root_trust(root, mode, index):
    expected = root_trust_cases(mode, index)
    if not expected:
        raise ValueError("current56_root_trust_evidence_invalid")
    report = shared.verify_pytest_report(
        root / ROOT_TRUST_REPORT, required_cases=expected
    )
    if (
        report["tests"] != len(expected)
        or report["passed"] != len(expected)
        or _read(root, f"{ROOT_TRUST_STAGE}.log")
        .splitlines()
        .count(b"HERMETIC_EXECUTION_AUTHORITY=0")
        != 1
    ):
        raise ValueError("current56_root_trust_evidence_invalid")
    return expected


def _verify_devval_catalog_evidence(root, mode, index, identity, files):
    junit, _ = component_report_paths(mode, index)
    checked = shared.verify_pytest_report(
        root / junit,
        required_cases=(
            devval_catalog.HERMETIC_CATALOG_CASE,
            fitted_measurement.CASE,
            fitted_measurement.INHERITED_CASE,
        ),
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
    verify_fitted_and_inherited_catalogs(root, identity, files)


def verify_fitted_and_inherited_catalogs(root, identity, files):
    """Replay both receipts against the exact archive and one actual cluster."""
    fitted = fitted_measurement.verify_measurement(
        _read(root, f"test-results/{fitted_measurement.FILE}", maximum=1_200_000),
        identity=identity,
        source_files=files,
    )
    inherited = fitted_measurement.verify_inherited_measurement(
        _read(root, f"test-results/{fitted_measurement.INHERITED_FILE}", maximum=8_192),
        identity=identity,
        source_files=files,
    )
    postgrant = postgrant_acl.verify_measurement(
        _read(root, f"test-results/{postgrant_acl.FILE}", maximum=8_192),
        identity=identity,
        source_files=files,
    )
    if (
        inherited["cluster_system_identifier_sha256"]
        != fitted["cluster_system_identifier_sha256"]
        or postgrant["cluster_system_identifier_sha256"]
        != fitted["cluster_system_identifier_sha256"]
        or postgrant["inherited_native_definitions_sha256"]
        != inherited["definitions"]["postgrant_readonly_inherited_sha256"]
    ):
        raise ValueError("current56_fitted_inherited_cluster_mismatch")
    return fitted


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


def _collection(root, mode, index, identity, *, root_trust_verified=()):
    junit, selected_path = component_report_paths(mode, index)
    if mode == "shard":
        return previous._collection(
            root,
            mode,
            index,
            identity,
            additional_exact_skips=_linux_platform_skips(root_trust_verified),
        )
    if mode != "full":
        selection = previous._collection(root, mode, index, identity)
        if mode == "postgres":
            cases = (
                CURRENT56_PG1_REQUIRED_CASES
                if index == 1
                else (PREDICTION_CUSTODY_CASE,)
            )
            checked = shared.verify_pytest_report(root / junit, required_cases=cases)
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
    _verify_platform_skips(
        junit_raw,
        mode=mode,
        expected_skipped=report["skipped"],
        root_trust_verified=root_trust_verified,
    )
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
        or not postgres_ready_readback(
            _read(root, "postgres-ready.log"),
            ephemeral_tls=requires_devval_catalog(mode, index),
        )
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
    root_trust_verified = ()
    if requires_root_trust(mode, index):
        root_trust_verified = verify_root_trust(root, mode, index)
    selection = _collection(
        root, mode, index, identity, root_trust_verified=root_trust_verified
    )
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
            or not postgres_ready_readback(
                _read(root, "postgres-ready-after-restart.log"),
                ephemeral_tls=requires_devval_catalog(mode, index),
            )
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
