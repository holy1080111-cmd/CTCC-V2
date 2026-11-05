"""Read back current0055 Docker component evidence using only host stdlib."""

import hashlib
import io
import json
import math
import re
import tarfile
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path, PurePosixPath

from scripts import verify_final_hermetic as shared
from scripts.current0055_catalog_receipt import verify_current55_catalog_report
from scripts.evidence_json import read_evidence_json
from scripts.verify_linux_shard import POSTGRES_COMPONENT_COUNT, SHARD_COUNT

HEAD = "0055"
RUNNER_CONTRACT = "ctcc.current0055.hermetic.v1"
RUNNER_PATH = "scripts/verify_current0055_hermetic.py"
CATALOG_REPORT = "test-results/current55-catalog.xml"
RECOVERY_REPORT = "test-results/current55-recovery.xml"
RESERVATION_UNIQUE = (
    b"uq_qualification_reservation_transitions_reservation_id|"
    b"UNIQUE (reservation_id, state_revision)"
)
_SHA = re.compile(r"[0-9a-f]{64}")
_GIT = re.compile(r"[0-9a-f]{40}")
_FULL_OBSERVED: set[str] = set()

# These exact Windows cases are covered by the independent Windows union.
_LINUX_PLATFORM_SKIPS = {
    (
        "tests.unit.test_highvol_installer_validator",
        "test_changed_source_bytes_reject_before_execution",
    ): "Native PowerShell required",
    (
        "tests.unit.test_highvol_installer_validator",
        "test_default_trusted_identity_rejects_an_unreviewed_package",
    ): "Native PowerShell required",
    (
        "tests.unit.test_highvol_installer_validator",
        "test_duplicate_identity_rejects_before_execution",
    ): "Native PowerShell required",
    (
        "tests.unit.test_highvol_installer_validator",
        "test_invalid_utf8_identity_rejects_before_execution",
    ): "Native PowerShell required",
    (
        "tests.unit.test_highvol_installer_validator",
        "test_invalid_utf8_installer_rejects_before_execution",
    ): "Native PowerShell required",
    (
        "tests.unit.test_highvol_installer_validator",
        "test_missing_source_rejects_before_execution",
    ): "Native PowerShell required",
    (
        "tests.unit.test_highvol_installer_validator",
        "test_native_parse_and_repeated_dry_run_do_not_execute_installer",
    ): "Native PowerShell required",
    (
        "tests.unit.test_highvol_installer_validator",
        "test_original_scope_interpolation_rejects_before_execution",
    ): "Native PowerShell required",
    (
        "tests.unit.test_highvol_installer_validator",
        "test_rewritten_identity_rejects_before_execution",
    ): "Native PowerShell required",
    (
        "tests.unit.test_highvol_installer_validator",
        "test_unlisted_package_member_rejects_before_execution",
    ): "Native PowerShell required",
    (
        "tests.unit.test_highvol_installer_validator",
        "test_utf8_bom_identity_and_installer_parse_from_pinned_bytes",
    ): "Native PowerShell required",
    (
        "tests.unit.test_qualification_evidence_gate",
        "test_native_windows_filesystem_root_is_rejected_without_writing",
    ): "Native Windows drive-root rejection only",
    (
        "tests.unit.test_qualification_evidence_gate",
        "test_native_windows_owned_root_reports_real_pin_outcome_without_fallback",
    ): "Actual Windows native publication boundary only",
    (
        "tests.unit.test_qualification_evidence_gate",
        "test_windows_unavailable_native_pin_is_not_a_success_fallback",
    ): "Windows unavailable-pin fail-closed contract only",
    (
        "tests.unit.test_trade_evidence_outbox",
        "test_native_windows_late_envelope_denial_retains_state_journal",
    ): "Native Windows late-publication evidence retention",
    (
        "tests.unit.test_trade_evidence_outbox",
        "test_native_windows_late_winerror32_recovers_only_matching_initial_journal",
    ): "Native Windows recovery acceptance",
    (
        "tests.unit.test_trade_evidence_outbox",
        "test_native_windows_owned_root_truthfully_records_pin_denial_or_real_success",
    ): "This case records native Windows ancestor-pin behavior only",
    (
        "tests.unit.test_trade_evidence_outbox",
        "test_native_windows_root_publication_delivery_and_conflict_preserve_history",
    ): "Required real Windows outbox acceptance",
    (
        "tests.unit.test_trade_evidence_outbox",
        "test_native_windows_two_workers_cannot_both_claim",
    ): "Required real Windows worker fencing",
    (
        "tests.unit.test_trade_evidence_outbox_worker",
        "test_native_windows_worker_denial_or_real_empty_root_is_fail_closed",
    ): "Records real Windows ancestor-pin behavior, without changing ACLs",
    (
        "tests.unit.test_trade_evidence_storage",
        "test_native_windows_create_new_publish_is_no_clobber_without_releasing_lease",
    ): "Native Windows root publication",
    (
        "tests.unit.test_trade_evidence_storage",
        "test_native_windows_handles_block_rename_until_receipt_completion",
    ): "Native Windows directory handle sharing",
    (
        "tests.unit.test_trade_evidence_storage",
        "test_native_windows_late_write_failure_preserves_durable_name_and_bytes",
    ): "Native Windows late-write preservation",
    (
        "tests.unit.test_trade_evidence_storage",
        "test_native_windows_long_path_publish_readback_no_clobber_and_pins",
    ): "Real Windows extended-length publication",
    (
        "tests.unit.test_trade_evidence_storage",
        "test_native_windows_owned_root_handle_enforces_publisher_and_rename_exclusion",
    ): "Native Windows owned-directory primitive",
    (
        "tests.unit.test_trade_evidence_storage",
        "test_native_windows_publish_rejects_unsafe_names_before_io",
    ): "Native Windows publication filename guard",
    (
        "tests.unit.test_trade_evidence_storage",
        "test_unc_and_device_paths_are_rejected_lexically_before_filesystem_io",
    ): "Windows UNC and device path syntax",
    (
        "tests.unit.test_trade_evidence_storage",
        "test_windows_junctions_are_rejected_without_touching_the_target",
    ): "Windows-specific junction coverage",
    (
        "tests.unit.test_verify_migration_identity",
        "test_native_powershell_parser_and_isolated_probe_readback",
    ): "Native PowerShell parser/probe requires PowerShell",
}

CATALOG_CASES = tuple(
    ("tests.integration.test_native_filled_flat_current55_pg", name)
    for name in (
        "test_actual_current55_full_model_has_no_alembic_drift",
        "test_actual_current55_owner_catalog_survives_empty_downgrade_and_reupgrade",
        "test_actual_current55_writer_login_matches_owner_and_rejects_missing_privilege",
    )
)
RECOVERY_CASES = (
    (
        "tests.integration.test_controlled_history_current55_v2_pg",
        "test_downgrade_denies_unrevoked_new_rpc_grant",
    ),
    (
        "tests.integration.test_controlled_history_current55_v2_pg",
        "test_upgrade_denies_changed_original_history_body_before_install",
    ),
    (
        "tests.integration.test_native_zero_fok_migration_current55_v1_pg",
        "test_upgrade_refuses_actual_changed0027_before_any55_install",
    ),
    (
        "tests.integration.test_submission_reporting_repository",
        "test_actual_process_crash_retains_private_outcome_without_projection",
    ),
    (
        "tests.integration.test_native_filled_flat_current55_pg",
        "test_actual_current55_core_grants_commit_and_verify_three_restricted_logins",
    ),
    (
        "tests.integration.test_qualification_ledger_restricted_role_repository",
        "test_write_guard_accepts_restricted_role_and_rejects_function_replacement",
    ),
)
REQUIRED_CURRENT55_CASES = tuple(
    dict.fromkeys(
        (
            *shared.ARCHIVE_REQUIRED_CASES,
            *CATALOG_CASES,
            *RECOVERY_CASES,
            (
                "tests.integration.test_submission_reporting_repository",
                "test_append_only_projector_skips_held_spool_lock_and_rechecks_after_release",
            ),
            (
                "tests.integration.test_native_close_intent_index_current55_pg",
                "test_real_two_session_once_index_race_does_not_block_other_capture_versions",
            ),
            *(
                (
                    "tests.integration.test_native_recovery_repository_pg_v1",
                    (
                        "test_recovery_repository_commits_and_replays_as_restricted_writer"
                        f"[synthetic{fixture_index}-{variant}]"
                    ),
                )
                # The shared fixture covers four direction and position-mode pairs.
                for fixture_index in range(4)
                for variant in ("committed", "late_rejection")
            ),
            *(
                (
                    "tests.unit.test_qualification_one_shot",
                    f"test_native_g12_disk_readback_before_owned_capture[{side}]",
                )
                for side in ("long", "short")
            ),
        )
    )
)

_SETUP = (
    "build",
    "image-identity",
    "network-create",
    "network-inspect",
    "postgres-start",
    "redis-start",
    "postgres-ready",
    "redis-ready",
    "copied-manifest",
    "copied-source-exact",
    "dependency-lock",
    "ruff",
    "format",
)
_SUPPORTED = (
    "migration-initial-supported",
    "migration-upgrade-supported",
    "migration-downgrade-supported",
    "migration-supported-readback",
)
_MIGRATION = (
    "migration-upgrade",
    "migration-identity",
    "migration-reservation-unique",
    "migration-drift",
)
_ANCHOR_TESTS = ("current55-catalog-tests", "current55-recovery-tests")
_RESTART = (
    "api-start",
    "api-before-restart",
    "api-instance-before",
    "postgres-restart",
    "postgres-ready-after-restart",
    "redis-restart",
    "redis-ready-after-restart",
    "api-restart",
    "api-after-restart",
    "api-instance-after",
    "migration-identity-after-restart",
    "migration-drift-after-restart",
)


def component_is_anchor(mode, index):
    return mode == "full" or (mode == "postgres" and index == 0)


def component_test_stage(mode, index):
    if mode == "full" and index is None:
        return "linux-full"
    if (
        mode == "postgres"
        and type(index) is int
        and 0 <= index < POSTGRES_COMPONENT_COUNT
    ):
        return "postgres-intent"
    if mode == "shard" and type(index) is int and 0 <= index < SHARD_COUNT:
        return f"linux-shard-{index}"
    raise ValueError("current55_component_selection_invalid")


def component_report_paths(mode, index):
    stage = component_test_stage(mode, index)
    collection = "postgres-collection" if mode == "postgres" else stage + "-collection"
    return f"test-results/{stage}.xml", f"test-results/{collection}.json"


def component_stage_names(mode, index):
    stage = component_test_stage(mode, index)
    if component_is_anchor(mode, index):
        return (*_SETUP, *_SUPPORTED, *_MIGRATION, *_ANCHOR_TESTS, stage, *_RESTART)
    return (*_SETUP, *_MIGRATION, stage)


def component_artifacts(mode, index):
    base = {
        "identity.json",
        "source-files.json",
        "steps.json",
        "cleanup.json",
        *component_report_paths(mode, index),
        *(f"{stage}.log" for stage in component_stage_names(mode, index)),
    }
    if component_is_anchor(mode, index):
        base.update((CATALOG_REPORT, RECOVERY_REPORT))
    return base


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def migration_digest(files):
    migration = {
        name: item["sha256"]
        for name, item in files.items()
        if name.startswith("migrations/") and name.endswith(".py")
    }
    revisions = [
        int(match.group(1))
        for name in migration
        if (match := re.fullmatch(r"migrations/versions/(\d{4})_\w+\.py", name))
    ]
    if not revisions or max(revisions) != int(HEAD) or revisions.count(int(HEAD)) != 1:
        raise ValueError("current55_archive_migration_scope_invalid")
    return digest(json.dumps(migration, sort_keys=True, separators=(",", ":")).encode())


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
        raise ValueError("current55_database_identity_mismatch")
    return actual


def _read(root, name, *, maximum=64 * 1024 * 1024):
    parts = PurePosixPath(name)
    if parts.is_absolute() or ".." in parts.parts or parts.as_posix() != name:
        raise ValueError("current55_evidence_path_invalid")
    path = root.joinpath(*parts.parts)
    if (
        any(p.is_symlink() for p in (path, *path.parents) if p.is_relative_to(root))
        or not path.is_file()
        or not 0 <= path.stat().st_size <= maximum
    ):
        raise ValueError("current55_evidence_file_unavailable")
    return path.read_bytes()


def _api_readback(raw):
    observed = read_evidence_json(raw)
    ready = observed["readiness"]
    version = observed["api/version"]
    if (
        observed["liveness"]["status"] != "alive"
        or ready["status"] != "ready"
        or ready["blockers"] != []
        or ready["database"]["ok"] is not True
        or ready["redis"]["ok"] is not True
        or version["environment"] != "test"
        or version["trading_mode"] != "analysis_only"
        or version["auto_trade"] is not False
        or version["live_trading"] is not False
    ):
        raise ValueError("current55_api_readback_invalid")


def _restart_time(raw, container_name):
    state = read_evidence_json(raw)
    if (
        state["Name"] != "/" + container_name
        or not _SHA.fullmatch(state["Id"])
        or state["State"]["Running"] is not True
        or type(state["State"]["Pid"]) is not int
        or state["State"]["Pid"] <= 0
    ):
        raise ValueError("current55_api_instance_readback_invalid")
    value = datetime.fromisoformat(state["State"]["StartedAt"])
    if value.utcoffset() is None:
        raise ValueError("current55_api_restart_clock_invalid")
    return state["Id"], value


def _cleanup(raw, prefix, *, anchor):
    rows = read_evidence_json(raw)
    if not isinstance(rows, list) or not rows:
        raise ValueError("current55_cleanup_missing")
    by_name = {}
    for item in rows:
        name = item["name"]
        if (
            not name.startswith(prefix)
            or name in by_name
            or item["kind"] not in ("container", "network")
            or item["status"] not in ("ABSENT", "REMOVED")
            or "failure_type" in item
        ):
            raise ValueError("current55_cleanup_invalid")
        by_name[name] = item
    services = {prefix + suffix for suffix in ("network", "postgres", "redis")}
    if anchor:
        services.add(prefix + "api")
    if not services <= set(by_name) or any(
        by_name[name]["status"] != "REMOVED" for name in services
    ):
        raise ValueError("current55_services_not_removed")


def _verify_platform_skips(raw, *, mode, expected_skipped):
    actual_skipped = 0
    for case in ET.fromstring(raw).iter("testcase"):
        for skipped in case.findall("skipped"):
            actual_skipped += 1
            identity = (case.attrib["classname"], case.attrib["name"].partition("[")[0])
            approved_reason = _LINUX_PLATFORM_SKIPS.get(identity)
            if (
                mode == "postgres"
                or skipped.get("type") != "pytest.skip"
                or approved_reason is None
                or approved_reason != skipped.get("message")
            ):
                raise ValueError("current55_unapproved_linux_skip")
    if actual_skipped != expected_skipped:
        raise ValueError("current55_linux_skip_count_mismatch")


def _collection(root, mode, index, identity):
    junit, selected_path = component_report_paths(mode, index)
    report = shared.verify_pytest_report(root / junit, allow_unrelated_skips=True)
    junit_raw = _read(root, junit)
    if digest(junit_raw) != report["sha256"]:
        raise ValueError("current55_report_identity_mismatch")
    _verify_platform_skips(junit_raw, mode=mode, expected_skipped=report["skipped"])
    selection = read_evidence_json(_read(root, selected_path))
    if mode == "full":
        full = selection.get("full_nodeids")
        if (
            selection.get("schema") != "ctcc.current0055.full_collection.v1"
            or not isinstance(full, list)
            or not full
            or any(type(node) is not str for node in full)
            or len(full) != len(set(full))
            or selection.get("selected_nodeids") != full
            or selection.get("observed_nodeids") != full
            or type(selection.get("pytest_exitstatus")) is not int
            or selection["pytest_exitstatus"] != 0
        ):
            raise ValueError("current55_full_collection_invalid")
        shared.verify_pytest_report(
            root / junit,
            required_cases=REQUIRED_CURRENT55_CASES,
            allow_unrelated_skips=True,
        )
    else:
        shared.verify_linux_collection(
            root / selected_path, mode=mode, index=index, report=report
        )
    shared.verify_selected_junit_nodes(
        root / junit, selection["selected_nodeids"], expected_sha256=report["sha256"]
    )
    if report != identity["tests"]:
        raise ValueError("current55_report_identity_mismatch")
    return selection


def pytest_addoption(parser):
    parser.addoption("--ctcc-current55-full-collection")


def pytest_collection_finish(session):
    destination = session.config.getoption("--ctcc-current55-full-collection")
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
        raise ValueError("current55_full_collection_not_exact")
    _FULL_OBSERVED.clear()
    session._ctcc_current55_full = (destination, nodes)


def pytest_runtest_logreport(report):
    _FULL_OBSERVED.add(report.nodeid)


def pytest_sessionfinish(session, exitstatus):
    del exitstatus
    state = getattr(session, "_ctcc_current55_full", None)
    if state is None:
        return
    destination, nodes = state
    if set(nodes) != _FULL_OBSERVED:
        session.exitstatus = 1
    record = {
        "schema": "ctcc.current0055.full_collection.v1",
        "full_nodeids": nodes,
        "selected_nodeids": nodes,
        "observed_nodeids": [node for node in nodes if node in _FULL_OBSERVED],
        "pytest_exitstatus": int(session.exitstatus),
    }
    with Path(destination).open("x", encoding="utf-8") as output:
        json.dump(record, output, sort_keys=True, separators=(",", ":"))


def verify_current55_component(
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
        raise ValueError("current55_component_input_invalid")
    if isinstance(archive_path, Path):
        if (
            archive_path.is_symlink()
            or not archive_path.is_file()
            or not 0 < archive_path.stat().st_size <= 128 * 1024 * 1024
        ):
            raise ValueError("current55_component_input_invalid")
        raw = archive_path.read_bytes()
    else:
        raw = archive_path
    if type(raw) is not bytes or not 0 < len(raw) <= 128 * 1024 * 1024:
        raise ValueError("current55_component_input_invalid")
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
        raise ValueError("current55_component_identity_invalid")
    if digest(raw) != identity["archive_sha256"]:
        raise ValueError("current55_archive_sha256_mismatch")
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
        if (
            archive.pax_headers.get("comment") != commit
            or shared.archive_tree(archive) != tree
        ):
            raise ValueError("current55_archive_git_identity_mismatch")
        files = shared.archive_files(archive)
    mapping = _read(root, "source-files.json")
    if (
        read_evidence_json(mapping) != files
        or digest(mapping) != identity["source_files_sha256"]
        or files["MANIFEST.sha256"]["sha256"] != manifest_sha256
        or files[RUNNER_PATH]["sha256"] != identity["runner_sha256"]
    ):
        raise ValueError("current55_archive_file_map_mismatch")

    hashes = read_evidence_json(_read(root, "results.sha256.json"))
    if set(hashes) != component_artifacts(mode, index) or any(
        not isinstance(value, str)
        or not _SHA.fullmatch(value)
        or digest(_read(root, name)) != value
        for name, value in hashes.items()
    ):
        raise ValueError("current55_artifact_hashes_invalid")
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
        raise ValueError("current55_component_steps_invalid")
    run_id = identity["run_id"]
    if not re.fullmatch(r"[0-9a-f]{12}", run_id):
        raise ValueError("current55_run_identity_invalid")
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
        raise ValueError("current55_component_runtime_readback_invalid")
    expected_migration = migration_digest(files)
    before = migration_readback(
        _read(root, "migration-identity.log"), expected_migration
    )
    if identity["migration"] != before:
        raise ValueError("current55_component_migration_not_bound")
    _cleanup(_read(root, "cleanup.json"), prefix, anchor=anchor)
    selection = _collection(root, mode, index, identity)
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
            or read_evidence_json(_read(root, "migration-supported-readback.log"))
            != shared.expected_migration_revision_readback("0001")
        ):
            raise ValueError("current55_anchor_runtime_readback_invalid")
    return {
        "identity": identity,
        "full_nodeids": selection["full_nodeids"],
        "selected_nodeids": selection["selected_nodeids"],
    }
