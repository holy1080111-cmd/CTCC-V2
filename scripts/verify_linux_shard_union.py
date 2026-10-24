"""Verify that independently hermetic Linux jobs executed the entire suite.

The old all-in-one runner timed out after a second PostgreSQL run. This gate
admits only one exact-source PostgreSQL component and every disjoint full-suite
shard, with read-back test reports and identical full collection inventories.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

from scripts.evidence_json import read_evidence_json
from scripts.verify_final_hermetic import (
    ARCHIVE_REQUIRED_CASES,
    COVERAGE_PREFLIGHT_CASES,
    DOWNGRADE_DENIAL,
    GATE3_PREFLIGHT_CASES,
    LEDGER_SCHEMA_PREFLIGHT_CASES,
    NATIVE_IDENTITY_DOWNGRADE_DENIAL,
    PAIR_GUARD_DOWNGRADE_DENIAL,
    PREDECESSOR_0047_CASES,
    RUNTIME_CUSTODY_PREFLIGHT_CASES,
    coverage_nonempty_downgrade_readback,
    expected_coverage_absent_readback,
    expected_migration_revision_readback,
    expected_native_identity_empty_downgrade_readback,
    native_identity_empty_before_readback,
    native_identity_nonempty_downgrade_readback,
    native_migration_json_equal,
    verify_coverage_migration_receipt,
    verify_coverage_native_catalog_capture,
    verify_current_0049_downgrade_denial_log,
    verify_durable_seed_marker,
    verify_linux_collection,
    verify_native_identity_downgrade_denial_log,
    verify_pair_guard_downgrade_denial_log,
    verify_pair_guard_migration_receipt,
    verify_pytest_report,
)
from scripts.verify_linux_shard import POSTGRES_TEST_PATHS, SHARD_COUNT
from scripts.verify_native_witness_identity_schema import (
    require_native_witness_identity_baseline,
)

COMMON_STAGES = (
    "build",
    "image-identity",
    "network-create",
    "postgres-start",
    "redis-start",
    "copied-manifest",
    "copied-source-exact",
    "dependencies",
    "dependency-lock",
    "ruff",
    "format",
    "migration-initial-supported",
    "migration-upgrade-supported",
    "migration-downgrade-supported",
    "migration-supported-readback",
    "migration-upgrade",
    "migration-coverage-native-catalog",
    "migration-identity",
    "migration-drift",
    "migration-pair-guard-empty-before",
    "migration-pair-guard-empty-downgrade",
    "migration-pair-guard-empty-readback",
    "migration-coverage-empty-before",
    "migration-coverage-empty-downgrade",
    "migration-coverage-empty-readback",
    "migration-native-identity-empty-before",
    "migration-native-identity-empty-downgrade",
    "migration-native-identity-empty-readback",
    "migration-bundle-empty-downgrade",
    "migration-bundle-empty-readback",
    "migration-guard-empty-downgrade",
    "migration-guard-empty-readback",
    "migration-hwm-empty-downgrade",
    "migration-hwm-empty-readback",
    "migration-empty-downgrade",
    "migration-empty-readback",
    "migration-reupgrade",
    "migration-reupgrade-identity",
    "migration-reupgrade-readback",
    "migration-reupgrade-drift",
    "migration-downgrade-denial",
    "migration-reidentity",
    "migration-denial-readback",
    "migration-redrift",
)
POSTGRES_RESTART_STAGES = (
    "crash-probe-start",
    "crash-seed-readback-log",
    "process-sigkill",
    "postgres-restart",
    "redis-restart",
    "crash-restart-independent-readback",
    "api-start",
    "api-health",
    "api-restart",
    "api-restart-health",
    "api-logs",
)
POSTGRES_PREFLIGHT_STAGES = (
    "controlled-writer-coverage-preflight",
    "runtime-bundle-custody-preflight",
    "event-journal-schema-preflight",
    "gate3-postgres-preflight",
)
POSTGRES_COVERAGE_STAGES = (
    "migration-coverage-nonempty-before",
    "migration-coverage-nonempty-denial",
    "migration-coverage-nonempty-after",
)
PREDECESSOR_0047_STAGES = (
    "predecessor-0047-parent-identity",
    "predecessor-0047-create",
    "predecessor-0047-upgrade",
    "predecessor-0047-identity",
    "predecessor-0047-two-cases",
    "migration-native-identity-nonempty-before",
    "migration-native-identity-nonempty-denial",
    "migration-native-identity-nonempty-after",
    "predecessor-0047-drop",
)


def _json(path: Path):
    if (
        path.is_symlink()
        or not path.is_file()
        or not 0 < path.stat().st_size <= 16 * 1024 * 1024
    ):
        raise ValueError(f"linux_union_evidence_missing_or_unsafe:{path.name}")
    return read_evidence_json(path.read_bytes())


def _verify_hashes(root: Path):
    recorded = _json(root / "results.sha256.json")
    if not isinstance(recorded, dict):
        raise TypeError("linux_union_hash_map_invalid")
    for name in (
        "identity.json",
        "steps.json",
        "source-files.json",
        "test-results/coverage-native-catalog.json",
        "migration-supported-readback.log",
        "migration-identity.log",
        "migration-native-identity-empty-before.log",
        "migration-native-identity-empty-readback.log",
        "migration-bundle-empty-readback.log",
        "migration-guard-empty-readback.log",
        "migration-hwm-empty-readback.log",
        "migration-empty-readback.log",
        "migration-reupgrade-identity.log",
        "migration-reupgrade-readback.log",
        "migration-reidentity.log",
        "migration-denial-readback.log",
        "migration-downgrade-denial.log",
        *(f"{stage}.log" for stage in COMMON_STAGES),
    ):
        if name not in recorded:
            raise ValueError("linux_union_core_hash_missing")
    for name, digest in recorded.items():
        if (
            type(name) is not str
            or type(digest) is not str
            or not re.fullmatch("[a-f0-9]{64}", digest)
            or Path(name).is_absolute()
            or ".." in Path(name).parts
        ):
            raise ValueError("linux_union_hash_map_invalid")
        path = root / name
        if (
            path.is_symlink()
            or not path.is_file()
            or hashlib.sha256(path.read_bytes()).hexdigest() != digest
        ):
            raise ValueError(f"linux_union_hash_mismatch:{name}")
    return recorded


def _verify_stage_records(stages: object) -> None:
    """Reject any failed, aliased or repeated native stage, even if rehashed.

    A successful sibling record cannot erase a late failure or a second
    execution. The two migration refusals still require their separate exact
    driver-log checks; a nonzero code alone never establishes either refusal.
    """
    if type(stages) is not list or not 1 <= len(stages) <= 256:
        raise ValueError("linux_union_stage_inventory_invalid")
    refusals = {
        "migration-downgrade-denial": DOWNGRADE_DENIAL,
        "migration-coverage-nonempty-denial": PAIR_GUARD_DOWNGRADE_DENIAL,
        "migration-native-identity-nonempty-denial": NATIVE_IDENTITY_DOWNGRADE_DENIAL,
    }
    names = set()
    for step in stages:
        if (
            type(step) is not dict
            or not set(step)
            <= {
                "name",
                "exit_code",
                "elapsed_seconds",
                "timeout_scope",
                "policy_refusal",
            }
            or type(step.get("name")) is not str
            or not 1 <= len(step["name"]) <= 128
            or re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", step["name"]) is None
            or step["name"] in names
            or type(step.get("exit_code")) is not int
            or not -(2**31) <= step["exit_code"] < 2**31
            or "timeout_scope" in step
            and step["timeout_scope"] not in ("stage", "validation")
            or "elapsed_seconds" in step
            and (
                type(step["elapsed_seconds"]) not in (int, float)
                or not 0 <= step["elapsed_seconds"] <= 2**31
            )
        ):
            raise ValueError("linux_union_stage_record_invalid")
        name = step["name"]
        names.add(name)
        if name in refusals:
            if step["exit_code"] == 0 or step.get("policy_refusal") != refusals[name]:
                raise ValueError("linux_union_stage_policy_refusal_invalid")
        elif step["exit_code"] != 0 or "policy_refusal" in step:
            raise ValueError("linux_union_stage_unsuccessful")
    positions = [
        index for index, step in enumerate(stages) if step["name"] in COMMON_STAGES
    ]
    observed = [stages[index]["name"] for index in positions]
    expected = [name for name in COMMON_STAGES if name in names]
    if observed != expected:
        raise ValueError("linux_union_stage_order_invalid")


def _verify_restart_stages(root, stages, hashes, identity):
    """Require the actual crash/restart commands after the PostgreSQL suite.

    These remain synthetic crash-recovery evidence, not source or trading
    acceptance. Identity PASS strings cannot replace their ordered commands,
    hashed logs and retained durable seed marker.
    """
    names = [step["name"] for step in stages]
    required = (
        "migration-native-identity-nonempty-after",
        *POSTGRES_RESTART_STAGES,
    )
    if any(name not in names for name in required):
        raise ValueError("linux_union_restart_stage_missing")
    positions = [names.index(name) for name in required]
    if positions != sorted(positions):
        raise ValueError("linux_union_restart_stage_order_invalid")
    for name in POSTGRES_RESTART_STAGES:
        if f"{name}.log" not in hashes:
            raise ValueError("linux_union_restart_stage_log_missing")
    marker = root / "crash-probe" / "durable.json"
    if (
        "crash-probe/durable.json" not in hashes
        or marker.parent.is_symlink()
        or marker.is_symlink()
        or not marker.is_file()
        or not 0 < marker.stat().st_size <= 32768
        or hashlib.sha256(marker.read_bytes()).hexdigest()
        != identity.get("crash_probe_marker_sha256")
    ):
        raise ValueError("linux_union_restart_marker_missing_or_changed")
    try:
        verify_durable_seed_marker(marker.read_bytes())
    except RuntimeError:
        raise ValueError("linux_union_restart_marker_invalid") from None
    expected_lines = (
        "ACCOUNT_CAPTURE_RESTART_RAW_AND_UNKNOWN_PREFIX=PASS",
        "DEMO_CONTROL_RESTART_NO_ARM_ESTOP_PERSISTENCE=PASS",
        "PROCESS_KILL_POSTGRES_REDIS_RESTART_DURABILITY=PASS",
        "SYNTHETIC_CLAIMS=1;SOURCE_AUTHORITY=0;ORDER_WRITES=0",
    )
    lines = (root / "crash-restart-independent-readback.log").read_text().splitlines()
    if tuple(lines) != expected_lines:
        raise ValueError("linux_union_restart_independent_readback_incomplete")
    ready = (root / "crash-seed-readback-log.log").read_text().splitlines()
    if ready.count("SYNTHETIC_DURABLE_COMMIT_READBACK_READY=1") != 1:
        raise ValueError("linux_union_restart_seed_readback_incomplete")
    for name in ("api-health", "api-restart-health"):
        values = _json(root / f"{name}.log")
        if type(values) is not dict or set(values) != {
            "liveness",
            "readiness",
            "api/version",
        }:
            raise ValueError("linux_union_restart_api_health_invalid")
        live, ready, version = (
            values[key] for key in ("liveness", "readiness", "api/version")
        )
        if (
            any(type(value) is not dict for value in (live, ready, version))
            or live.get("status") != "alive"
            or ready.get("status") != "ready"
            or ready.get("blockers") != []
            or type(ready.get("database")) is not dict
            or ready["database"].get("ok") is not True
            or type(ready.get("redis")) is not dict
            or ready["redis"].get("ok") is not True
            or version.get("environment") != "test"
            or version.get("trading_mode") != "analysis_only"
            or version.get("auto_trade") is not False
            or version.get("live_trading") is not False
        ):
            raise ValueError("linux_union_restart_api_health_invalid")


def _verify_preflight_reports(root, hashes, identity):
    for stage, field, cases in (
        (
            "controlled-writer-coverage-preflight",
            "controlled_writer_coverage_preflight",
            COVERAGE_PREFLIGHT_CASES,
        ),
        (
            "event-journal-schema-preflight",
            "event_journal_schema_preflight",
            LEDGER_SCHEMA_PREFLIGHT_CASES,
        ),
        ("gate3-postgres-preflight", "gate3_preflight", GATE3_PREFLIGHT_CASES),
    ):
        relative = f"test-results/{stage}.xml"
        if relative not in hashes or f"{stage}.log" not in hashes:
            raise ValueError("linux_union_preflight_report_missing")
        report = verify_pytest_report(
            root / relative,
            required_cases=cases,
            required_modules=(cases[0][0],)
            if stage == "controlled-writer-coverage-preflight"
            else (),
        )
        if (
            not native_migration_json_equal(report, identity.get(field))
            or report["tests"] != len(cases)
            or report["skipped"]
        ):
            raise ValueError("linux_union_preflight_report_invalid")


def _verify_complete_stage_sequence(stages, mode, index):
    expected = (
        *COMMON_STAGES,
        *(
            (
                *POSTGRES_PREFLIGHT_STAGES,
                "postgres-intent",
                *POSTGRES_COVERAGE_STAGES,
                *PREDECESSOR_0047_STAGES,
                *POSTGRES_RESTART_STAGES,
            )
            if mode == "postgres"
            else (f"linux-shard-{index}",)
        ),
    )
    if tuple(step["name"] for step in stages) != expected:
        raise ValueError("linux_union_stage_inventory_or_order_invalid")


def verify_union(
    artifacts: Path, *, commit: str, tree: str, manifest_sha256: str
) -> dict:
    require_native_witness_identity_baseline()
    if any(not re.fullmatch("[a-f0-9]{40}", value) for value in (commit, tree)):
        raise ValueError("linux_union_git_identity_invalid")
    if not re.fullmatch("[a-f0-9]{64}", manifest_sha256):
        raise ValueError("linux_union_manifest_invalid")
    names = {f"ctcc-linux-postgres-{commit}"} | {
        f"ctcc-linux-shard-{i}-{commit}" for i in range(SHARD_COUNT)
    }
    actual = {path.name for path in artifacts.iterdir() if path.is_dir()}
    if actual != names:
        raise ValueError("linux_union_component_set_incomplete")
    full: list[str] | None = None
    selected_union: set[str] = set()
    common_identity: tuple | None = None
    components = []
    for mode, index in (
        ("postgres", None),
        *(("shard", i) for i in range(SHARD_COUNT)),
    ):
        name = (
            f"ctcc-linux-postgres-{commit}"
            if mode == "postgres"
            else f"ctcc-linux-shard-{index}-{commit}"
        )
        attachment = artifacts / name
        root = attachment / "ctcc-validation"
        if attachment.is_symlink() or root.is_symlink():
            raise ValueError("linux_union_artifact_symlink")
        hashes = _verify_hashes(root)
        identity = _json(root / "identity.json")
        stages = _json(root / "steps.json")
        _verify_stage_records(stages)
        native_before = _json(root / "migration-native-identity-empty-before.log")
        if not isinstance(native_before, dict) or not native_migration_json_equal(
            native_before,
            native_identity_empty_before_readback(
                native_before.get("revision_readback"),
                native_before.get("object_custody_sha256"),
            ),
        ):
            raise ValueError("linux_union_native_identity_empty_before_invalid")
        native_custody = native_before["object_custody_sha256"]
        if (
            identity.get("commit") != commit
            or identity.get("tree") != tree
            or identity.get("manifest_sha256") != manifest_sha256
            or identity.get("source_files_sha256")
            != hashlib.sha256((root / "source-files.json").read_bytes()).hexdigest()
            or identity.get("suite") != mode
            or (mode == "shard" and type(identity.get("shard_index")) is not int)
            or identity.get("shard_index") != index
            or type(identity.get("shard_count")) is not int
            or identity.get("shard_count") != SHARD_COUNT
            or identity.get("host_credentials") is not False
            or identity.get("host_env") is not False
            or type(identity.get("external_order_writes")) is not int
            or identity.get("external_order_writes") != 0
            or identity.get("trading_acceptance") is not False
            or identity.get("component_validation") != "PASS"
            or identity.get("hermetic_regression") != "PENDING_UNION"
            or identity.get("cleanup") != "PASS"
            or not isinstance(identity.get("migration"), dict)
            or identity["migration"].get("source_head") != "0049"
            or identity["migration"].get("database_head") != "0049"
            or identity["migration"].get("status") != "PASS"
            or not native_migration_json_equal(
                identity.get("migration_supported_roundtrip"),
                expected_migration_revision_readback("0001"),
            )
            or not native_migration_json_equal(
                identity.get("migration_empty_roundtrip"),
                {
                    "source_head": "0049",
                    "pair_guard_before_readback": expected_migration_revision_readback(
                        "0049"
                    ),
                    "pair_guard_down_revision": "0048",
                    "pair_guard_down_readback": expected_migration_revision_readback(
                        "0048"
                    ),
                    "coverage_before_readback": expected_migration_revision_readback(
                        "0048"
                    ),
                    "coverage_down_revision": "0047",
                    "coverage_down_readback": expected_coverage_absent_readback(),
                    "native_identity_before_readback": native_before,
                    "native_identity_down_revision": "0046",
                    "native_identity_down_readback": expected_native_identity_empty_downgrade_readback(
                        native_custody
                    ),
                    "bundle_down_revision": "0045",
                    "bundle_down_readback": expected_migration_revision_readback(
                        "0045"
                    ),
                    "guard_down_revision": "0044",
                    "guard_down_readback": expected_migration_revision_readback("0044"),
                    "hwm_down_revision": "0043",
                    "hwm_down_readback": expected_migration_revision_readback("0043"),
                    "down_revision": "0042",
                    "down_readback": expected_migration_revision_readback("0042"),
                    "reupgrade_readback": expected_migration_revision_readback("0049"),
                    "reupgrade_identity": "PASS",
                    "schema_drift": "PASS",
                },
            )
            or not native_migration_json_equal(
                identity.get("migration_downgrade_policy"),
                {
                    "source_head": "0049",
                    "target": "0041",
                    "result": "DENIED",
                    "denied_at": "0042",
                    "policy_refusal": DOWNGRADE_DENIAL,
                    "database_head_after": "0049",
                    "rollback_readback": expected_migration_revision_readback("0049"),
                    "schema_drift": "PASS",
                },
            )
        ):
            raise ValueError(f"linux_union_component_identity_rejected:{name}")
        capture_path = root / "test-results/coverage-native-catalog.json"
        if not 0 < capture_path.stat().st_size <= 2 * 1024 * 1024:
            raise ValueError("linux_union_coverage_native_catalog_size_invalid")
        capture = _json(capture_path)
        coverage_schema = verify_coverage_native_catalog_capture(
            capture, migration_sha256=identity["migration"].get("migration_sha256")
        )
        if not native_migration_json_equal(
            coverage_schema,
            expected_migration_revision_readback("0049")[
                "controlled_writer_coverage_schema"
            ],
        ):
            raise ValueError("linux_union_coverage_native_catalog_changed")
        capture_log = _json(root / "migration-coverage-native-catalog.log")
        if not native_migration_json_equal(
            capture_log,
            {
                "artifact": "test-results/coverage-native-catalog.json",
                "sha256": hashes["test-results/coverage-native-catalog.json"],
                "native_baseline_admission": False,
                "execution_authority": False,
            },
        ):
            raise ValueError("linux_union_coverage_capture_receipt_changed")
        if not native_migration_json_equal(
            _json(root / "migration-native-identity-empty-readback.log"),
            expected_native_identity_empty_downgrade_readback(native_custody),
        ):
            raise ValueError(
                f"linux_union_native_identity_legacy_readback_changed:{name}"
            )
        migration_logs = (
            (
                "migration-pair-guard-empty-before",
                expected_migration_revision_readback("0049"),
            ),
            (
                "migration-pair-guard-empty-readback",
                expected_migration_revision_readback("0048"),
            ),
            ("migration-supported-readback", identity["migration_supported_roundtrip"]),
            ("migration-identity", identity["migration"]),
            (
                "migration-coverage-empty-before",
                identity["migration_empty_roundtrip"]["coverage_before_readback"],
            ),
            ("migration-coverage-empty-readback", expected_coverage_absent_readback()),
            (
                "migration-native-identity-empty-readback",
                identity["migration_empty_roundtrip"]["native_identity_down_readback"],
            ),
            (
                "migration-bundle-empty-readback",
                identity["migration_empty_roundtrip"]["bundle_down_readback"],
            ),
            (
                "migration-guard-empty-readback",
                identity["migration_empty_roundtrip"]["guard_down_readback"],
            ),
            (
                "migration-hwm-empty-readback",
                identity["migration_empty_roundtrip"]["hwm_down_readback"],
            ),
            (
                "migration-empty-readback",
                identity["migration_empty_roundtrip"]["down_readback"],
            ),
            ("migration-reupgrade-identity", identity["migration"]),
            (
                "migration-reupgrade-readback",
                identity["migration_empty_roundtrip"]["reupgrade_readback"],
            ),
            ("migration-reidentity", identity["migration"]),
            (
                "migration-denial-readback",
                identity["migration_downgrade_policy"]["rollback_readback"],
            ),
        )
        if any(
            not native_migration_json_equal(_json(root / f"{stage}.log"), expected)
            for stage, expected in migration_logs
        ):
            raise ValueError(f"linux_union_migration_readback_changed:{name}")
        archive = attachment / "ctcc-source.tar"
        if (
            archive.is_symlink()
            or not archive.is_file()
            or hashlib.sha256(archive.read_bytes()).hexdigest()
            != identity.get("archive_sha256")
        ):
            raise ValueError(f"linux_union_archive_readback_changed:{name}")
        shared = (
            identity.get("archive_sha256"),
            identity.get("source_files_sha256"),
            identity.get("runner_sha256"),
            identity.get("migration"),
        )
        if common_identity is None:
            common_identity = shared
        elif not native_migration_json_equal(shared, common_identity):
            raise ValueError("linux_union_source_or_migration_drift")
        if not isinstance(stages, list) or any(
            not any(
                step.get("name") == required
                and (
                    type(step.get("exit_code")) is int
                    and step["exit_code"] != 0
                    and step.get("policy_refusal") == DOWNGRADE_DENIAL
                    if required == "migration-downgrade-denial"
                    else step.get("exit_code") == 0
                )
                for step in stages
            )
            for required in COMMON_STAGES
        ):
            raise ValueError(f"linux_union_common_stage_missing:{name}")
        empty_positions = []
        for required in (
            "migration-pair-guard-empty-before",
            "migration-pair-guard-empty-downgrade",
            "migration-pair-guard-empty-readback",
            "migration-coverage-empty-before",
            "migration-coverage-empty-downgrade",
            "migration-coverage-empty-readback",
            "migration-native-identity-empty-before",
            "migration-native-identity-empty-downgrade",
            "migration-native-identity-empty-readback",
            "migration-bundle-empty-downgrade",
        ):
            matches = [
                (i, step)
                for i, step in enumerate(stages)
                if step.get("name") == required
            ]
            if (
                len(matches) != 1
                or type(matches[0][1].get("exit_code")) is not int
                or matches[0][1]["exit_code"] != 0
            ):
                raise ValueError("linux_union_native_identity_empty_stage_invalid")
            empty_positions.append(matches[0][0])
        if empty_positions != sorted(empty_positions):
            raise ValueError("linux_union_native_identity_empty_stage_order")
        verify_current_0049_downgrade_denial_log(
            root / "migration-downgrade-denial.log"
        )
        stage = "postgres-intent" if mode == "postgres" else f"linux-shard-{index}"
        if not any(
            step.get("name") == stage and step.get("exit_code") == 0 for step in stages
        ):
            raise ValueError(f"linux_union_suite_stage_failed:{name}")
        if mode == "postgres":
            receipt_name = "test-results/controlled-writer-coverage-migration.json"
            if receipt_name not in hashes:
                raise ValueError("linux_union_coverage_migration_receipt_missing")
            if not 0 < (root / receipt_name).stat().st_size <= 65536:
                raise ValueError("linux_union_coverage_migration_receipt_size_invalid")
            source_files = _json(root / "source-files.json")
            required_sources = (
                "app/database/repositories/controlled_writer_coverage.py",
                "migrations/versions/0048_controlled_writer_coverage.py",
            )
            if type(source_files) is not dict or any(
                type(source_files.get(path)) is not dict
                or type(source_files[path].get("sha256")) is not str
                for path in required_sources
            ):
                raise ValueError("linux_union_coverage_migration_source_pins_missing")
            receipt = verify_coverage_migration_receipt(
                _json(root / receipt_name),
                manifest_sha256=manifest_sha256,
                repository_sha256=source_files[required_sources[0]]["sha256"],
                migration_sha256=source_files[required_sources[1]]["sha256"],
            )
            if not native_migration_json_equal(
                identity.get("coverage_migration_acceptance"), receipt
            ):
                raise ValueError("linux_union_coverage_migration_receipt_changed")
            pair_name = "test-results/controlled-writer-pair-guard-migration.json"
            pair_source = (
                "migrations/versions/0049_controlled_writer_pair_guard_definer.py"
            )
            if (
                pair_name not in hashes
                or pair_source not in source_files
                or type(source_files[pair_source]) is not dict
                or type(source_files[pair_source].get("sha256")) is not str
                or not 0 < (root / pair_name).stat().st_size <= 65536
            ):
                raise ValueError("linux_union_pair_guard_migration_evidence_missing")
            pair_receipt = verify_pair_guard_migration_receipt(
                _json(root / pair_name),
                manifest_sha256=manifest_sha256,
                repository_sha256=source_files[required_sources[0]]["sha256"],
                migration_sha256=source_files[pair_source]["sha256"],
            )
            if not native_migration_json_equal(
                identity.get("pair_guard_migration_acceptance"), pair_receipt
            ):
                raise ValueError("linux_union_pair_guard_migration_receipt_changed")
            for required in (
                *POSTGRES_COVERAGE_STAGES,
                *(
                    name
                    for name in PREDECESSOR_0047_STAGES
                    if not name.startswith("migration-native-identity-")
                ),
            ):
                if (
                    f"{required}.log" not in hashes
                    or sum(step["name"] == required for step in stages) != 1
                ):
                    raise ValueError(
                        "linux_union_coverage_or_predecessor_stage_or_hash_missing"
                    )
            verify_pair_guard_downgrade_denial_log(
                root / "migration-coverage-nonempty-denial.log"
            )
            coverage_retention = coverage_nonempty_downgrade_readback(
                _json(root / "migration-coverage-nonempty-before.log"),
                _json(root / "migration-coverage-nonempty-after.log"),
            )
            if (
                coverage_retention["source_head"] != "0049"
                or coverage_retention["target"] != "0048"
                or coverage_retention["policy_refusal"] != PAIR_GUARD_DOWNGRADE_DENIAL
                or not native_migration_json_equal(
                    identity.get("coverage_nonempty_downgrade_policy"),
                    coverage_retention,
                )
            ):
                raise ValueError("linux_union_coverage_nonempty_readback_changed")
            if (
                root / "predecessor-0047-parent-identity.log"
            ).read_bytes() != b"ctcc\n":
                raise ValueError("linux_union_predecessor_parent_identity_changed")
            predecessor_identity = _json(root / "predecessor-0047-identity.log")
            if not native_migration_json_equal(
                predecessor_identity,
                {
                    "schema": "ctcc.migration_predecessor_identity.v1",
                    "status": "PASS",
                    "source_head": "0049",
                    "database_head": "0047",
                    "target_revision": "0047",
                    "migration_sha256": identity["migration"].get("migration_sha256"),
                    "execution_authority": False,
                },
            ):
                raise ValueError("linux_union_predecessor_source_or_target_changed")
            predecessor_xml = "test-results/predecessor-0047-two-cases.xml"
            if predecessor_xml not in hashes:
                raise ValueError("linux_union_predecessor_two_case_junit_missing")
            predecessor_report = verify_pytest_report(
                root / predecessor_xml,
                required_cases=PREDECESSOR_0047_CASES,
                required_modules=(PREDECESSOR_0047_CASES[0][0],),
            )
            if (
                predecessor_report["tests"] != 2
                or predecessor_report["passed"] != 2
                or predecessor_report["skipped"] != 0
            ):
                raise ValueError("linux_union_predecessor_two_case_inventory_invalid")
            retention_stages = (
                stage,
                "migration-native-identity-nonempty-before",
                "migration-native-identity-nonempty-denial",
                "migration-native-identity-nonempty-after",
            )
            positions = []
            for required in retention_stages:
                matches = [
                    (i, step)
                    for i, step in enumerate(stages)
                    if step.get("name") == required
                ]
                if len(matches) != 1:
                    raise ValueError(
                        "linux_union_native_identity_nonempty_stage_missing"
                    )
                position, step_record = matches[0]
                code = step_record.get("exit_code")
                if type(code) is not int or (
                    (
                        code == 0
                        or step_record.get("policy_refusal")
                        != NATIVE_IDENTITY_DOWNGRADE_DENIAL
                    )
                    if required.endswith("-denial")
                    else code != 0
                ):
                    raise ValueError(
                        "linux_union_native_identity_nonempty_stage_failed"
                    )
                positions.append(position)
            if positions != sorted(positions):
                raise ValueError("linux_union_native_identity_nonempty_stage_order")
            for required in retention_stages[1:]:
                if f"{required}.log" not in hashes:
                    raise ValueError(
                        "linux_union_native_identity_nonempty_hash_missing"
                    )
            verify_native_identity_downgrade_denial_log(
                root / f"{retention_stages[2]}.log"
            )
            retention = native_identity_nonempty_downgrade_readback(
                _json(root / f"{retention_stages[1]}.log"),
                _json(root / f"{retention_stages[3]}.log"),
            )
            if not native_migration_json_equal(
                identity.get("native_identity_nonempty_downgrade_policy"), retention
            ):
                raise ValueError(
                    "linux_union_native_identity_nonempty_readback_changed"
                )
            if not native_migration_json_equal(
                identity.get("predecessor_0047_validation"),
                {
                    "schema": "ctcc.isolated_0047_predecessor_validation.v1",
                    "source_head": "0049",
                    "database_target": "0047",
                    "separate_from_full_suite_unique_count": True,
                    "test_report": predecessor_report,
                    "nonempty_downgrade_policy": retention,
                    "execution_authority": False,
                },
            ):
                raise ValueError("linux_union_predecessor_validation_changed")
            preflight_stage = "runtime-bundle-custody-preflight"
            observed = [
                (i, step)
                for i, step in enumerate(stages)
                if step.get("name") == preflight_stage
            ]
            if (
                len(observed) != 1
                or type(observed[0][1].get("exit_code")) is not int
                or observed[0][1]["exit_code"] != 0
                or any(
                    i <= observed[0][0]
                    for i, step in enumerate(stages)
                    if step.get("name") == stage
                )
            ):
                raise ValueError("linux_union_runtime_custody_preflight_stage_missing")
            preflight_xml = root / "test-results" / f"{preflight_stage}.xml"
            for relative in (
                f"{preflight_stage}.log",
                preflight_xml.relative_to(root).as_posix(),
            ):
                if relative not in hashes:
                    raise ValueError(
                        "linux_union_runtime_custody_preflight_hash_missing"
                    )
            preflight = verify_pytest_report(
                preflight_xml,
                required_modules=tuple(
                    sorted({module for module, _ in RUNTIME_CUSTODY_PREFLIGHT_CASES})
                ),
                required_cases=RUNTIME_CUSTODY_PREFLIGHT_CASES,
            )
            if not native_migration_json_equal(
                preflight, identity.get("runtime_bundle_custody_preflight")
            ):
                raise ValueError(
                    "linux_union_runtime_custody_preflight_readback_changed"
                )
            _verify_restart_stages(root, stages, hashes, identity)
            _verify_preflight_reports(root, hashes, identity)
        _verify_complete_stage_sequence(stages, mode, index)
        if mode == "postgres" and (
            identity.get("synthetic_process_pg_redis_crash_durability") != "PASS"
            or identity.get("api_service_restart") != "PASS"
        ):
            raise ValueError("linux_union_restart_acceptance_missing")
        xml = root / "test-results" / f"{stage}.xml"
        collection_path = (
            root
            / "test-results"
            / (
                "postgres-collection.json"
                if mode == "postgres"
                else f"{stage}-collection.json"
            )
        )
        for relative in (
            xml.relative_to(root).as_posix(),
            collection_path.relative_to(root).as_posix(),
        ):
            if relative not in hashes:
                raise ValueError("linux_union_test_hash_missing")
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
        recorded_report = identity.get(
            "postgres_tests" if mode == "postgres" else "linux_tests"
        )
        if not native_migration_json_equal(report, recorded_report):
            raise ValueError("linux_union_test_report_readback_changed")
        collection = verify_linux_collection(
            collection_path,
            mode=mode,
            index=index,
            report=report,
        )
        if not native_migration_json_equal(identity.get("collection"), collection):
            raise ValueError("linux_union_collection_readback_changed")
        record = _json(collection_path)
        if full is None:
            full = record["full_nodeids"]
        elif record["full_nodeids"] != full:
            raise ValueError("linux_union_full_collection_drift")
        for nodeid in record["selected_nodeids"]:
            if nodeid in selected_union:
                raise ValueError("linux_union_duplicate_execution")
            selected_union.add(nodeid)
        components.append(
            {
                "component": name,
                "selected": collection["selected_count"],
                "passed": report["passed"],
                "skipped": report["skipped"],
                "junit_sha256": report["sha256"],
                "collection_sha256": collection["sha256"],
            }
        )
    assert full is not None
    if selected_union != set(full):
        raise ValueError("linux_union_missing_full_suite_cases")
    return {
        "schema": "ctcc.linux_hermetic_union.v1",
        "commit": commit,
        "tree": tree,
        "manifest_sha256": manifest_sha256,
        "full_collected_cases": len(full),
        "selected_cases": sum(item["selected"] for item in components),
        "passed_cases": sum(item["passed"] for item in components),
        "skipped_cases": sum(item["skipped"] for item in components),
        "components": components,
        "hermetic_regression": "PASS",
        "trading_acceptance": False,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--tree", required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = verify_union(
        args.artifacts,
        commit=args.commit,
        tree=args.tree,
        manifest_sha256=hashlib.sha256(args.manifest.read_bytes()).hexdigest(),
    )
    args.output.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n")
    print(
        json.dumps(
            {
                key: result[key]
                for key in (
                    "commit",
                    "tree",
                    "full_collected_cases",
                    "selected_cases",
                    "passed_cases",
                    "skipped_cases",
                    "hermetic_regression",
                )
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
