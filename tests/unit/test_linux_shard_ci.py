"""Small source-bound proofs for the Linux CI suite union gate."""

from __future__ import annotations

import hashlib
import json
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from scripts.verify_final_hermetic import (
    ACCOUNT_MEASURED_HWM_V2_TRIGGERS,
    ACCOUNT_MEASURED_HWM_V2_TRIGGERS_0045,
    ACCOUNT_REVISION_WITNESS_TRIGGERS,
    ARCHIVE_REQUIRED_CASES,
    COVERAGE_DOWNGRADE_DENIAL,
    COVERAGE_PREFLIGHT_CASES,
    DOWNGRADE_DENIAL,
    GATE3_PREFLIGHT_CASES,
    HISTORY_PARTITION_PREFLIGHT_CASES,
    LEDGER_SCHEMA_PREFLIGHT_CASES,
    NATIVE_IDENTITY_DOWNGRADE_DENIAL,
    NATIVE_WITNESS_IDENTITY_SCHEMA,
    PAIR_GUARD_DOWNGRADE_DENIAL,
    PREDECESSOR_0047_CASES,
    PUBLIC_RUNTIME_BUNDLE_SCHEMA,
    RUNTIME_CUSTODY_PREFLIGHT_CASES,
    coverage_nonempty_downgrade_readback,
    coverage_nonempty_snapshot,
    expected_coverage_absent_readback,
    expected_migration_revision_readback,
    expected_native_identity_empty_downgrade_readback,
    native_identity_empty_before_readback,
    native_identity_nonempty_downgrade_readback,
    run_controlled_writer_coverage_preflight,
    run_history_partition_preflight,
    verify_linux_collection,
    verify_pytest_report,
)
from scripts.verify_linux_shard import (
    POSTGRES_TEST_PATHS,
    SHARD_COUNT,
    module_shard,
    selected_for,
)
from scripts.verify_linux_shard_union import (
    COMMON_STAGES,
    POSTGRES_COVERAGE_STAGES,
    POSTGRES_PREFLIGHT_STAGES,
    POSTGRES_RESTART_STAGES,
    PREDECESSOR_0047_STAGES,
    verify_union,
)
from scripts.verify_windows_regression import POSTGRES_INTEGRATION

COMMIT = "a" * 40
TREE = "b" * 40
ARCHIVE = b"reviewed-git-archive"
MANIFEST = b"reviewed-manifest\n"
SOURCE_FILES = json.dumps(
    {
        "app/database/repositories/controlled_writer_coverage.py": {"sha256": "1" * 64},
        "app/domain/account_history_partition_catalog_v1.py": {"sha256": "4" * 64},
        "app/domain/account_history_partition_v1.py": {"sha256": "5" * 64},
        "app/database/repositories/account_history_partition.py": {"sha256": "6" * 64},
        "migrations/versions/0050_account_history_partition.py": {"sha256": "7" * 64},
        **{
            name: {"sha256": "8" * 64}
            for name in (
                "app/domain/prospective_event_cutover_catalog_v2.py",
                "app/domain/prospective_event_cutover_v2.py",
                "app/database/repositories/prospective_event_cutover_v2.py",
                "app/database/repositories/qualification_ledger.py",
                "app/trade_qualification/prospective_event_cutover_v2.py",
                "app/trade_qualification/controlled_writer_coverage_v1.py",
                "app/database/models/controlled_writer_coverage.py",
                "migrations/versions/0051_prospective_event_cutover.py",
            )
        },
        "migrations/versions/0048_controlled_writer_coverage.py": {"sha256": "2" * 64},
        "migrations/versions/0049_controlled_writer_pair_guard_definer.py": {
            "sha256": "3" * 64
        },
    },
    sort_keys=True,
).encode()


@pytest.fixture(autouse=True)
def synthetic_sealed_native_identity_baseline(monkeypatch):
    # These filesystem fixtures test evidence rejection, not native PG DDL.
    # The synthetic pin is distinct from the reviewed native deparser digest.
    constraint = list(NATIVE_WITNESS_IDENTITY_SCHEMA["constraint"])
    constraint[-1] = "9" * 64
    monkeypatch.setitem(NATIVE_WITNESS_IDENTITY_SCHEMA, "constraint", constraint)
    from scripts import verify_controlled_writer_coverage_schema_v1 as coverage_schema

    def synthetic_catalog(catalog, *, require_native_baseline=True):
        assert require_native_baseline is True
        if catalog != {"synthetic_fixture_only": True}:
            raise ValueError("synthetic_coverage_catalog_mismatch")
        return expected_migration_revision_readback("0049")[
            "controlled_writer_coverage_schema"
        ]

    # Invented union filesystem artifacts cannot seal a native PG17 baseline.
    monkeypatch.setattr(coverage_schema, "verify_catalog", synthetic_catalog)
    from app.domain import account_history_partition_catalog_v1 as partition_policy

    def synthetic_partition_catalog(catalog, *, require_native_baseline=True):
        assert require_native_baseline is True
        if catalog != {"synthetic_fixture_only": True}:
            raise ValueError("synthetic_partition_catalog_mismatch")
        return expected_migration_revision_readback("0050")[
            "account_history_partition_schema"
        ]

    monkeypatch.setattr(
        partition_policy,
        "verify_account_history_partition_catalog",
        synthetic_partition_catalog,
    )

    from app.domain import prospective_event_cutover_catalog_v2 as cutover_policy

    def synthetic_cutover_catalog(catalog, *, require_native_baseline=True):
        assert require_native_baseline is True
        if catalog != {"synthetic_fixture_only": True}:
            raise ValueError("synthetic_cutover_catalog_mismatch")

    def synthetic_cutover_deparser(catalog):
        if catalog != {"synthetic_fixture_only": True}:
            raise ValueError("synthetic_cutover_catalog_mismatch")
        return "c" * 64

    monkeypatch.setattr(cutover_policy, "verify_catalog", synthetic_cutover_catalog)
    monkeypatch.setattr(cutover_policy, "deparser_sha256", synthetic_cutover_deparser)


def _revision_readback(revision: str) -> dict:
    if revision in ("0050", "0051"):
        # Filesystem evidence is parsed JSON. Preserve the producer-reader JSON
        # boundary fix when this preparation is three-way merged with root.
        return json.loads(json.dumps(expected_migration_revision_readback(revision)))
    if revision in ("0048", "0049"):
        return {
            **_revision_readback("0047"),
            "schema": "ctcc.migration_revision_readback.v6"
            if revision == "0049"
            else "ctcc.migration_revision_readback.v5",
            "expected": revision,
            "observed": revision,
            "controlled_writer_coverage_schema": expected_migration_revision_readback(
                revision
            )["controlled_writer_coverage_schema"],
        }
    result = {
        "schema": (
            "ctcc.migration_revision_readback.v4"
            if revision == "0047"
            else "ctcc.migration_revision_readback.v3"
            if revision == "0046"
            else "ctcc.migration_revision_readback.v2"
        ),
        "expected": revision,
        "observed": revision,
        "execution_authority": False,
    }
    if revision in ("0001", "0042", "0043", "0044", "0045", "0046", "0047"):
        witness = revision in ("0043", "0044", "0045", "0046", "0047")
        result["account_revision_witness_table_present"] = witness
        result["account_revision_witness_guard_present"] = witness
        result["account_revision_witness_trigger_count"] = 3 if witness else 0
        result["account_revision_witness_trigger_catalog"] = (
            [list(row) for row in ACCOUNT_REVISION_WITNESS_TRIGGERS] if witness else []
        )
        hwm = revision in ("0044", "0045", "0046", "0047")
        result["account_measured_hwm_v2_table_present"] = hwm
        result["account_measured_hwm_v2_guard_present"] = hwm
        result["account_measured_hwm_v2_trigger_count"] = 3 if hwm else 0
        result["account_measured_hwm_v2_trigger_catalog"] = (
            [
                list(row)
                for row in (
                    ACCOUNT_MEASURED_HWM_V2_TRIGGERS_0045
                    if revision in ("0045", "0046", "0047")
                    else ACCOUNT_MEASURED_HWM_V2_TRIGGERS
                )
            ]
            if hwm
            else []
        )
    if revision in ("0046", "0047"):
        result["public_runtime_bundle_schema"] = PUBLIC_RUNTIME_BUNDLE_SCHEMA
    if revision == "0047":
        result["public_witness_native_identity_schema"] = NATIVE_WITNESS_IDENTITY_SCHEMA
    return result


def test_every_windows_classified_postgres_module_runs_in_strict_linux_postgres_suite():
    assert {Path(path).name for path in POSTGRES_TEST_PATHS} == set(
        POSTGRES_INTEGRATION
    )


def test_unpacked_unit_and_integration_modules_have_distinct_pytest_names():
    root = Path(__file__).resolve().parents[2]
    unit = {path.name for path in (root / "tests/unit").glob("test_*.py")}
    integration = {path.name for path in (root / "tests/integration").glob("test_*.py")}
    assert not unit & integration


def test_reviewed_modules_in_postgres_suite_only():
    for path, case in (
        (
            "tests/integration/test_complete_consumed_event_ledger_repository.py",
            "test_missing_transition_denies_instead_of_publishing_empty",
        ),
        (
            "tests/integration/test_gate3_schedule_publication_ack.py",
            "test_ack_requires_committed_pin_and_replays_after_restart",
        ),
        (
            "tests/integration/test_gate3_committed_preregistration_seal.py",
            "test_seal_ack_must_commit_before_capture_claim",
        ),
        (
            "tests/integration/test_gate3_scheduled_public_minute_preflight.py",
            "test_exact_committed_seal_and_schedule_still_wait_before_due",
        ),
        (
            "tests/integration/test_measured_hwm_v2_postgres.py",
            "test_b5_direct_sql_forged_nonpeak_equity_cannot_enter_chain",
        ),
        (
            "tests/integration/test_measured_hwm_v2_catch_up_postgres.py",
            "test_b3_only_scope_catches_up_from_original_genesis_in_bounded_pages",
        ),
        (
            "tests/integration/test_account_portfolio_runtime_v3_postgres.py",
            "test_v3_real_postgresql_same_invocation_commits_and_replays_deny_only",
        ),
        (
            "tests/integration/test_account_portfolio_runtime_v3_postgres.py",
            "test_v3_late_v1_fold_failure_preserves_committed_b1_b3_b5_after_restart",
        ),
        (
            "tests/integration/test_account_portfolio_components_repository.py",
            "test_original_balance_pages_survive_restart_and_reject_prefix_or_head_changes",
        ),
        (
            "tests/integration/test_gate3_claim_trigger_alias_repair.py",
            "test_0032_repaired_guard_accepts_fresh_prereg_acknowledged_claim",
        ),
        (
            "tests/integration/test_public_receipt_publication_ack_repository.py",
            "test_observer_cannot_ack_own_uncommitted_witness_insert",
        ),
        (
            "tests/integration/test_post_g12_v4_real_account_chain_repository.py",
            "test_v4_reads_real_v5_v7_b1_chains_before_separate_event_observation",
        ),
        (
            "tests/integration/test_account_current_observation_repository.py",
            "test_v7_current_append_readback_is_separate_from_history_and_idempotent",
        ),
        (
            "tests/integration/test_account_current_history_observation_repository.py",
            "test_b3_and_db0038_same_uid_join_preserves_unknowns",
        ),
        (
            "tests/integration/test_account_current_history_recency_repository.py",
            "test_native_db_clock_after_locked_v7_chain_is_deny_only",
        ),
        (
            "tests/integration/test_account_locked_current_history_join_v7_repository.py",
            "test_isolated_postgresql_v7_join_has_distinct_policy_receipt_and_no_authority",
        ),
        (
            "tests/integration/test_account_observation_runtime_typed_repository.py",
            "test_one_owned_signed_b1_capture_commits_typed_b3_and_reads_it_back",
        ),
    ):
        nodeid = path + "::" + case
        assert path in POSTGRES_TEST_PATHS
        assert selected_for("postgres", None, nodeid)
        assert all(
            not selected_for("shard", index, nodeid) for index in range(SHARD_COUNT)
        )


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _full_nodeids() -> list[str]:
    postgres = [f"{path}::test_module_smoke" for path in sorted(POSTGRES_TEST_PATHS)]
    postgres += [
        f"{module.replace('.', '/')}.py::{case}"
        for module, case in (*ARCHIVE_REQUIRED_CASES, *RUNTIME_CUSTODY_PREFLIGHT_CASES)
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
    migration = {
        "schema": "ctcc.migration_identity.v1",
        "status": "PASS",
        "source_head": "0049",
        "database_head": "0049",
        "migration_sha256": "e" * 64,
    }
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
            "migration_supported_roundtrip": _revision_readback("0001"),
            "migration_empty_roundtrip": {
                "source_head": "0049",
                "pair_guard_before_readback": _revision_readback("0049"),
                "pair_guard_down_revision": "0048",
                "pair_guard_down_readback": _revision_readback("0048"),
                "coverage_before_readback": _revision_readback("0048"),
                "coverage_down_revision": "0047",
                "coverage_down_readback": expected_coverage_absent_readback(),
                "native_identity_before_readback": native_identity_empty_before_readback(
                    _revision_readback("0047"), "c" * 64
                ),
                "native_identity_down_revision": "0046",
                "native_identity_down_readback": expected_native_identity_empty_downgrade_readback(
                    "c" * 64
                ),
                "bundle_down_revision": "0045",
                "bundle_down_readback": _revision_readback("0045"),
                "guard_down_revision": "0044",
                "guard_down_readback": _revision_readback("0044"),
                "hwm_down_revision": "0043",
                "hwm_down_readback": _revision_readback("0043"),
                "down_revision": "0042",
                "down_readback": _revision_readback("0042"),
                "reupgrade_readback": _revision_readback("0049"),
                "reupgrade_identity": "PASS",
                "schema_drift": "PASS",
            },
            "migration_downgrade_policy": {
                "source_head": "0049",
                "target": "0041",
                "result": "DENIED",
                "denied_at": "0042",
                "policy_refusal": DOWNGRADE_DENIAL,
                "database_head_after": "0049",
                "rollback_readback": _revision_readback("0049"),
                "schema_drift": "PASS",
            },
            "collection": collection,
            "postgres_tests" if mode == "postgres" else "linux_tests": report,
        }
        if mode == "postgres":
            identity["synthetic_process_pg_redis_crash_durability"] = "PASS"
            identity["api_service_restart"] = "PASS"
        restart_files = []
        if mode == "postgres":
            marker = root / "crash-probe" / "durable.json"
            marker.parent.mkdir()
            body = {
                "schema": "ctcc.synthetic.qualification.crash-probe.v3",
                "execution_authority": False,
                "order_writes": 0,
                "account_captures": [
                    {"scenario": "safe_failed_capture"},
                    {"scenario": "unfinished_ram_prefix"},
                ],
                "controls": [
                    {"scenario": "arm_intent"},
                    {"scenario": "estop"},
                    {"scenario": "cold_estop"},
                ],
                "control_arm_intent_observed_before_publish": True,
                "intent_version": "ctcc-demo-submit-intent-v2",
                "exchange_request_sha256": "a" * 64,
            }
            marker.write_text(
                json.dumps(
                    {
                        "body": body,
                        "sha256": _sha(
                            json.dumps(
                                body, sort_keys=True, separators=(",", ":")
                            ).encode()
                        ),
                    }
                )
            )
            identity["crash_probe_marker_sha256"] = _sha(marker.read_bytes())
            restart_files.append(marker)
            for name in POSTGRES_RESTART_STAGES:
                log = root / f"{name}.log"
                if name == "crash-seed-readback-log":
                    log.write_text("SYNTHETIC_DURABLE_COMMIT_READBACK_READY=1\n")
                elif name == "crash-restart-independent-readback":
                    log.write_text(
                        "ACCOUNT_CAPTURE_RESTART_RAW_AND_UNKNOWN_PREFIX=PASS\n"
                        "DEMO_CONTROL_RESTART_NO_ARM_ESTOP_PERSISTENCE=PASS\n"
                        "PROCESS_KILL_POSTGRES_REDIS_RESTART_DURABILITY=PASS\n"
                        "SYNTHETIC_CLAIMS=1;SOURCE_AUTHORITY=0;ORDER_WRITES=0\n"
                    )
                elif name in ("api-health", "api-restart-health"):
                    log.write_text(
                        json.dumps(
                            {
                                "liveness": {"status": "alive"},
                                "readiness": {
                                    "status": "ready",
                                    "blockers": [],
                                    "database": {"ok": True},
                                    "redis": {"ok": True},
                                },
                                "api/version": {
                                    "environment": "test",
                                    "trading_mode": "analysis_only",
                                    "auto_trade": False,
                                    "live_trading": False,
                                },
                            }
                        )
                    )
                else:
                    log.write_text("synthetic-stage-fixture-only\n")
                restart_files.append(log)
        retention_files = []
        retention_stages = ()
        if mode == "postgres":
            retention_stages = (
                "migration-native-identity-nonempty-before",
                "migration-native-identity-nonempty-denial",
                "migration-native-identity-nonempty-after",
            )
            snapshot = {
                "schema": "ctcc.public_witness_db0047_nonempty_snapshot.v1",
                "revision_readback": _revision_readback("0047"),
                "retention": {
                    "row_count": 4,
                    "small_row_count": 1,
                    "wide_row_count": 3,
                    "records_sha256": "a" * 64,
                    "catalog_sha256": "b" * 64,
                },
                "execution_authority": False,
            }
            identity["native_identity_nonempty_downgrade_policy"] = (
                native_identity_nonempty_downgrade_readback(snapshot, snapshot)
            )
            for retained_stage in (retention_stages[0], retention_stages[2]):
                path = root / f"{retained_stage}.log"
                path.write_text(json.dumps(snapshot))
                retention_files.append(path)
            path = root / f"{retention_stages[1]}.log"
            path.write_text(
                "INFO Running downgrade 0047 -> 0046\n"
                '  File "/app/migrations/versions/0047_public_witness_native_identity.py", line 320\n'
                f"asyncpg.exceptions.RaiseError: {NATIVE_IDENTITY_DOWNGRADE_DENIAL}\n"
                f"sqlalchemy.exc.DBAPIError: (asyncpg.Error): {NATIVE_IDENTITY_DOWNGRADE_DENIAL}\n"
            )
            retention_files.append(path)
        preflight_files = []
        if mode == "postgres":
            preflight_xml = results / "runtime-bundle-custody-preflight.xml"
            _junit(
                preflight_xml,
                [
                    f"{module.replace('.', '/')}.py::{case}"
                    for module, case in RUNTIME_CUSTODY_PREFLIGHT_CASES
                ],
            )
            identity["runtime_bundle_custody_preflight"] = verify_pytest_report(
                preflight_xml,
                required_modules=tuple(
                    sorted({module for module, _ in RUNTIME_CUSTODY_PREFLIGHT_CASES})
                ),
                required_cases=RUNTIME_CUSTODY_PREFLIGHT_CASES,
            )
            preflight_log = root / "runtime-bundle-custody-preflight.log"
            preflight_log.write_text("4 passed\n")
            preflight_files = [preflight_xml, preflight_log]
            for name, field, cases in (
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
                xml = results / f"{name}.xml"
                _junit(
                    xml,
                    [
                        f"{module.replace('.', '/')}.py::{case}"
                        for module, case in cases
                    ],
                )
                if name == "controlled-writer-coverage-preflight":
                    # Exercise the actual producer's module/case report shape;
                    # the synthetic container has already authored this XML.
                    identity[field] = run_controlled_writer_coverage_preflight(
                        lambda *_args, **_options: None,
                        test_results=results,
                        mounts=(),
                    )
                else:
                    identity[field] = verify_pytest_report(xml, required_cases=cases)
                log = root / f"{name}.log"
                log.write_text("synthetic-preflight-fixture-only\n")
                preflight_files.extend((xml, log))
            xml = results / f"{stage}.xml"
        coverage_files = []
        capture_path = results / "coverage-native-catalog.json"
        capture_path.write_text(
            json.dumps(
                {
                    "schema": "ctcc.controlled_writer_coverage_native_catalog_observation.v1",
                    "source_head": "0049",
                    "source_migration_sha256": "e" * 64,
                    "database_head": "0049",
                    "session_facts": [
                        "17.11",
                        "170011",
                        "ctcc",
                        "ctcc",
                        "ctcc",
                        "public",
                    ],
                    "catalog": {"synthetic_fixture_only": True},
                    "native_baseline_admission": False,
                    "execution_authority": False,
                }
            )
        )
        capture_log = root / "migration-coverage-native-catalog.log"
        capture_log.write_text(
            json.dumps(
                {
                    "artifact": "test-results/coverage-native-catalog.json",
                    "sha256": _sha(capture_path.read_bytes()),
                    "native_baseline_admission": False,
                    "execution_authority": False,
                }
            )
        )
        coverage_files.extend((capture_path, capture_log))
        for name, body in (
            ("migration-pair-guard-empty-before", _revision_readback("0049")),
            ("migration-pair-guard-empty-readback", _revision_readback("0048")),
            ("migration-coverage-empty-before", _revision_readback("0048")),
            ("migration-coverage-empty-readback", expected_coverage_absent_readback()),
        ):
            path = root / f"{name}.log"
            path.write_text(json.dumps(body))
            coverage_files.append(path)
        if mode == "postgres":
            rows = {
                table: [{"synthetic_fixture_only": True}]
                for table in (
                    "demo_controlled_writer_coverage_scopes",
                    "demo_controlled_writer_coverage_journal",
                )
            }
            retention = {
                "schema_version": "ctcc.controlled_writer_coverage_retention.v1",
                "rows": rows,
                "counts": {table: 1 for table in rows},
                "rows_sha256": _sha(
                    json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()
                ),
            }
            snapshot = coverage_nonempty_snapshot(_revision_readback("0049"), retention)
            identity["coverage_nonempty_downgrade_policy"] = (
                coverage_nonempty_downgrade_readback(snapshot, snapshot)
            )
            receipt_snapshot = {
                "head": "0048",
                "rows_sha256": retention["rows_sha256"],
                "counts": retention["counts"],
                "catalog_sha256": "f" * 64,
            }
            migration_receipt = {
                "schema_version": "ctcc.controlled_writer_coverage_migration_acceptance.v1",
                "manifest_sha256": _sha(MANIFEST),
                "repository_sha256": "1" * 64,
                "migration_sha256": "2" * 64,
                "server_major": 17,
                "before": receipt_snapshot,
                "after": receipt_snapshot,
                "refusal_code": COVERAGE_DOWNGRADE_DENIAL,
                "downgrade_target": "0047",
                "native_baseline_admission": False,
                "execution_authority": False,
            }
            receipt_path = results / "controlled-writer-coverage-migration.json"
            receipt_path.write_text(json.dumps(migration_receipt))
            identity["coverage_migration_acceptance"] = migration_receipt
            coverage_files.append(receipt_path)
            pair_receipt = {
                **migration_receipt,
                "schema_version": "ctcc.controlled_writer_pair_guard_migration_acceptance.v1",
                "migration_sha256": "3" * 64,
                "before": {**receipt_snapshot, "head": "0049"},
                "after": {**receipt_snapshot, "head": "0049"},
                "refusal_code": PAIR_GUARD_DOWNGRADE_DENIAL,
                "downgrade_target": "0048",
            }
            pair_path = results / "controlled-writer-pair-guard-migration.json"
            pair_path.write_text(json.dumps(pair_receipt))
            identity["pair_guard_migration_acceptance"] = pair_receipt
            coverage_files.append(pair_path)
            for name in (POSTGRES_COVERAGE_STAGES[0], POSTGRES_COVERAGE_STAGES[2]):
                path = root / f"{name}.log"
                path.write_text(json.dumps(snapshot))
                coverage_files.append(path)
            denial = root / "migration-coverage-nonempty-denial.log"
            denial.write_text(
                "INFO Running downgrade 0049 -> 0048\n"
                '  File "/app/migrations/versions/0049_controlled_writer_pair_guard_definer.py", line 49\n'
                f"asyncpg.exceptions.RaiseError: {PAIR_GUARD_DOWNGRADE_DENIAL}\n"
                f"sqlalchemy.exc.DBAPIError: (asyncpg.Error): {PAIR_GUARD_DOWNGRADE_DENIAL}\n"
            )
            coverage_files.append(denial)
            predecessor_xml = results / "predecessor-0047-two-cases.xml"
            _junit(
                predecessor_xml,
                [
                    f"{module.replace('.', '/')}.py::{case}"
                    for module, case in PREDECESSOR_0047_CASES
                ],
            )
            predecessor_report = verify_pytest_report(
                predecessor_xml,
                required_cases=PREDECESSOR_0047_CASES,
                required_modules=(PREDECESSOR_0047_CASES[0][0],),
            )
            identity["predecessor_0047_validation"] = {
                "schema": "ctcc.isolated_0047_predecessor_validation.v1",
                "source_head": "0049",
                "database_target": "0047",
                "separate_from_full_suite_unique_count": True,
                "test_report": predecessor_report,
                "nonempty_downgrade_policy": identity[
                    "native_identity_nonempty_downgrade_policy"
                ],
                "execution_authority": False,
            }
            coverage_files.append(predecessor_xml)
            for name in PREDECESSOR_0047_STAGES:
                path = root / f"{name}.log"
                if name == "predecessor-0047-parent-identity":
                    path.write_bytes(b"ctcc\n")
                elif name == "predecessor-0047-identity":
                    path.write_text(
                        json.dumps(
                            {
                                "schema": "ctcc.migration_predecessor_identity.v1",
                                "status": "PASS",
                                "source_head": "0049",
                                "database_head": "0047",
                                "target_revision": "0047",
                                "migration_sha256": "e" * 64,
                                "execution_authority": False,
                            }
                        )
                    )
                elif not path.exists():
                    path.write_text("synthetic-predecessor-stage-only\n")
                coverage_files.append(path)
        (root / "identity.json").write_text(json.dumps(identity))
        (root / "steps.json").write_text(
            json.dumps(
                [
                    {
                        "name": stage_name,
                        "exit_code": (
                            1
                            if stage_name
                            in (
                                "migration-downgrade-denial",
                                "migration-native-identity-nonempty-denial",
                                "migration-coverage-nonempty-denial",
                            )
                            else 0
                        ),
                        **(
                            {"policy_refusal": DOWNGRADE_DENIAL}
                            if stage_name == "migration-downgrade-denial"
                            else {"policy_refusal": NATIVE_IDENTITY_DOWNGRADE_DENIAL}
                            if stage_name == "migration-native-identity-nonempty-denial"
                            else {"policy_refusal": PAIR_GUARD_DOWNGRADE_DENIAL}
                            if stage_name == "migration-coverage-nonempty-denial"
                            else {}
                        ),
                    }
                    for stage_name in (
                        *COMMON_STAGES,
                        *(POSTGRES_PREFLIGHT_STAGES if mode == "postgres" else ()),
                        stage,
                        *(POSTGRES_COVERAGE_STAGES if mode == "postgres" else ()),
                        *(PREDECESSOR_0047_STAGES if mode == "postgres" else ()),
                        *(POSTGRES_RESTART_STAGES if mode == "postgres" else ()),
                    )
                ]
            )
        )
        (root / "source-files.json").write_bytes(SOURCE_FILES)
        supported_log = root / "migration-supported-readback.log"
        supported_log.write_text(json.dumps(identity["migration_supported_roundtrip"]))
        migration_log = root / "migration-identity.log"
        migration_log.write_text(json.dumps(migration))
        native_before_log = root / "migration-native-identity-empty-before.log"
        native_before_log.write_text(
            json.dumps(
                identity["migration_empty_roundtrip"]["native_identity_before_readback"]
            )
        )
        native_empty_log = root / "migration-native-identity-empty-readback.log"
        native_empty_log.write_text(
            json.dumps(expected_native_identity_empty_downgrade_readback("c" * 64))
        )
        bundle_empty_log = root / "migration-bundle-empty-readback.log"
        bundle_empty_log.write_text(json.dumps(_revision_readback("0045")))
        guard_empty_log = root / "migration-guard-empty-readback.log"
        guard_empty_log.write_text(json.dumps(_revision_readback("0044")))
        hwm_empty_log = root / "migration-hwm-empty-readback.log"
        hwm_empty_log.write_text(json.dumps(_revision_readback("0043")))
        empty_log = root / "migration-empty-readback.log"
        empty_log.write_text(json.dumps(_revision_readback("0042")))
        reupgrade_identity_log = root / "migration-reupgrade-identity.log"
        reupgrade_identity_log.write_text(json.dumps(migration))
        reupgrade_readback_log = root / "migration-reupgrade-readback.log"
        reupgrade_readback_log.write_text(json.dumps(_revision_readback("0049")))
        reidentity_log = root / "migration-reidentity.log"
        reidentity_log.write_text(json.dumps(migration))
        denial_readback_log = root / "migration-denial-readback.log"
        denial_readback_log.write_text(json.dumps(_revision_readback("0049")))
        denial_log = root / "migration-downgrade-denial.log"
        denial_log.write_text(
            "INFO Running downgrade 0049 -> 0048\n"
            "INFO Running downgrade 0048 -> 0047\n"
            "INFO Running downgrade 0047 -> 0046\n"
            "INFO Running downgrade 0046 -> 0045\n"
            "INFO Running downgrade 0045 -> 0044\n"
            "INFO Running downgrade 0044 -> 0043\n"
            "INFO Running downgrade 0043 -> 0042\n"
            "INFO Running downgrade 0042 -> 0041\n"
            '  File "/app/migrations/versions/'
            '0042_submission_transport_authenticity_denial.py", line 109\n'
            f"asyncpg.exceptions.RaiseError: {DOWNGRADE_DENIAL}\n"
            f"sqlalchemy.exc.DBAPIError: (asyncpg.Error): {DOWNGRADE_DENIAL}\n"
        )
        stage_logs = []
        for stage_name in COMMON_STAGES:
            path = root / f"{stage_name}.log"
            if not path.exists():
                path.write_text(f"stage={stage_name}\n")
            stage_logs.append(path)
        files = (
            root / "identity.json",
            root / "steps.json",
            root / "source-files.json",
            supported_log,
            migration_log,
            bundle_empty_log,
            empty_log,
            reupgrade_identity_log,
            reupgrade_readback_log,
            reidentity_log,
            denial_readback_log,
            denial_log,
            *stage_logs,
            *preflight_files,
            *retention_files,
            *coverage_files,
            *restart_files,
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
    _current0050_synthetic_artifact_layer(artifacts)
    _current0051_synthetic_artifact_layer(artifacts)
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


def test_rehashed_raw_json_cannot_hide_an_earlier_duplicate_failure_code(tmp_path):
    artifacts, _ = _artifacts(tmp_path)
    root = artifacts / f"ctcc-linux-shard-0-{COMMIT}" / "ctcc-validation"
    path = root / "steps.json"
    raw = path.read_text()
    assert '"exit_code": 0' in raw
    path.write_text(raw.replace('"exit_code": 0', '"exit_code":19,"exit_code":0', 1))
    hashes_path = root / "results.sha256.json"
    hashes = json.loads(hashes_path.read_text())
    hashes[path.name] = _sha(path.read_bytes())
    hashes_path.write_text(json.dumps(hashes))
    with pytest.raises(ValueError, match="evidence_json_duplicate_key"):
        verify_union(
            artifacts, commit=COMMIT, tree=TREE, manifest_sha256=_sha(MANIFEST)
        )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("pytest_exitstatus", False),
        ("pytest_exitstatus", 0.0),
        ("shard_index", False),
        ("shard_index", 0.0),
        ("shard_count", 8.0),
    ),
)
def test_linux_collection_requires_native_integer_fields(tmp_path, field, value):
    full = _full_nodeids()
    selected = [nodeid for nodeid in full if selected_for("shard", 0, nodeid)]
    record = {
        "schema": "ctcc.linux_full_collection_shard.v1",
        "mode": "shard",
        "shard_index": 0,
        "shard_count": SHARD_COUNT,
        "full_nodeids": full,
        "selected_nodeids": selected,
        "observed_nodeids": selected,
        "pytest_exitstatus": 0,
    }
    path = tmp_path / "collection.json"
    path.write_text(json.dumps(record))
    report = {"tests": len(selected)}
    verify_linux_collection(path, mode="shard", index=0, report=report)
    record[field] = value
    path.write_text(json.dumps(record))
    with pytest.raises(ValueError, match="linux_collection_execution_incomplete"):
        verify_linux_collection(path, mode="shard", index=0, report=report)


@pytest.mark.parametrize(
    "damage",
    (
        "bool_exit",
        "float_exit",
        "late_failure",
        "duplicate_success",
        "duplicate_failure",
        "extra_failure",
        "unexpected_refusal",
        "reordered_common_stages",
        "overlarge_refusal_exit",
        "negative_elapsed",
        "boolean_elapsed",
        "unknown_record_field",
    ),
)
def test_rehashed_steps_cannot_hide_an_unsuccessful_native_stage(tmp_path, damage):
    artifacts, _ = _artifacts(tmp_path)
    root = artifacts / f"ctcc-linux-shard-0-{COMMIT}" / "ctcc-validation"
    path = root / "steps.json"
    steps = json.loads(path.read_text())
    target = next(item for item in steps if item["name"] == "ruff")
    if damage == "bool_exit":
        target["exit_code"] = False
    elif damage == "float_exit":
        target["exit_code"] = 0.0
    elif damage == "late_failure":
        target["failure_type"] = "RuntimeError"
    elif damage == "duplicate_success":
        steps.append(dict(target))
    elif damage == "duplicate_failure":
        steps.append({**target, "exit_code": 1})
    elif damage == "extra_failure":
        steps.append({"name": "extra-post-test-check", "exit_code": 1})
    elif damage == "unexpected_refusal":
        target["policy_refusal"] = DOWNGRADE_DENIAL
    elif damage == "reordered_common_stages":
        steps[0], steps[1] = steps[1], steps[0]
    elif damage == "overlarge_refusal_exit":
        next(item for item in steps if item["name"] == "migration-downgrade-denial")[
            "exit_code"
        ] = 2**32
    elif damage == "negative_elapsed":
        target["elapsed_seconds"] = -1
    elif damage == "boolean_elapsed":
        target["elapsed_seconds"] = True
    elif damage == "unknown_record_field":
        target["error"] = "unrecognized-native-error"
    path.write_text(json.dumps(steps))
    hashes_path = root / "results.sha256.json"
    hashes = json.loads(hashes_path.read_text())
    hashes[path.name] = _sha(path.read_bytes())
    hashes_path.write_text(json.dumps(hashes))
    with pytest.raises(ValueError, match="linux_union_stage_"):
        verify_union(
            artifacts, commit=COMMIT, tree=TREE, manifest_sha256=_sha(MANIFEST)
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


@pytest.mark.parametrize("drift", ("numeric", "bool_alias", "custody"))
def test_union_checks_distinct_legacy_witness_catalog_even_with_matching_old_v3(
    tmp_path, drift
):
    artifacts, _ = _artifacts(tmp_path)
    root = artifacts / f"ctcc-linux-shard-0-{COMMIT}" / "ctcc-validation"
    identity_path = root / "identity.json"
    identity = json.loads(identity_path.read_text())
    receipt = identity["migration_empty_roundtrip"]["native_identity_down_readback"]
    if drift == "numeric":
        receipt["legacy_witness_catalog"]["columns"][0][1] = "numeric"
    elif drift == "bool_alias":
        receipt["legacy_witness_catalog"]["table_present"] = 1
    else:
        receipt["object_custody_sha256"] = "d" * 64
    identity_path.write_text(json.dumps(identity))
    log = root / "migration-native-identity-empty-readback.log"
    log.write_text(json.dumps(receipt))
    _rehash(root, identity_path, log)
    with pytest.raises(
        ValueError,
        match="linux_union_(component_identity_rejected|native_identity_legacy_readback_changed)",
    ):
        verify_union(
            artifacts, commit=COMMIT, tree=TREE, manifest_sha256=_sha(MANIFEST)
        )


@pytest.mark.parametrize(
    ("wrapper", "field", "alias"),
    (
        ("native_identity_before_readback", "execution_authority", 0),
        ("native_identity_down_readback", "execution_authority", 0),
        ("native_identity_down_readback", "legacy_table_present", 1),
    ),
)
def test_union_rejects_rehashed_identity_only_native_wrapper_alias(
    tmp_path, wrapper, field, alias
):
    artifacts, _ = _artifacts(tmp_path)
    root = _pg_root(artifacts)
    path = root / "identity.json"
    identity = json.loads(path.read_text())
    receipt = identity["migration_empty_roundtrip"][wrapper]
    if field == "legacy_table_present":
        receipt["legacy_witness_catalog"]["table_present"] = alias
    else:
        receipt[field] = alias
    # Keep independently verified observation logs unchanged: only the identity
    # copy and its envelope hash are modified. Python's False==0 must not link it.
    path.write_text(json.dumps(identity))
    _rehash(root, path)
    with pytest.raises(ValueError, match="linux_union_component_identity_rejected"):
        verify_union(
            artifacts, commit=COMMIT, tree=TREE, manifest_sha256=_sha(MANIFEST)
        )


@pytest.mark.parametrize(
    "damage", ("missing", "failed", "boolean", "duplicate", "early", "wrong_policy")
)
def test_union_requires_actual_post_pg47_nonempty_denial(tmp_path, damage):
    artifacts, _ = _artifacts(tmp_path)
    root = _pg_root(artifacts)
    path = root / "steps.json"
    stages = json.loads(path.read_text())
    target = next(
        s for s in stages if s["name"] == "migration-native-identity-nonempty-denial"
    )
    if damage == "missing":
        stages.remove(target)
    elif damage == "failed":
        target["exit_code"] = 0
    elif damage == "boolean":
        target["exit_code"] = True
    elif damage == "duplicate":
        stages.append(dict(target))
    elif damage == "early":
        stages.remove(target)
        stages.insert(0, target)
    else:
        target["policy_refusal"] = DOWNGRADE_DENIAL
    path.write_text(json.dumps(stages))
    _rehash(root, path)
    # The whole-journal guard can reject the same damage before retention parsing.
    with pytest.raises(
        ValueError, match="linux_union_(native_identity_nonempty_stage|stage_)"
    ):
        verify_union(
            artifacts, commit=COMMIT, tree=TREE, manifest_sha256=_sha(MANIFEST)
        )


@pytest.mark.parametrize("damage", ("record", "catalog", "unhashed", "old42"))
def test_union_rejects_rehashed_pg47_retention_changes_or_wrong_refusal(
    tmp_path, damage
):
    artifacts, _ = _artifacts(tmp_path)
    root = _pg_root(artifacts)
    path = root / "migration-native-identity-nonempty-after.log"
    if damage in ("record", "catalog"):
        snapshot = json.loads(path.read_text())
        snapshot["retention"][
            f"{damage}s_sha256" if damage == "record" else "catalog_sha256"
        ] = "d" * 64
        path.write_text(json.dumps(snapshot))
        _rehash(root, path)
    elif damage == "old42":
        path = root / "migration-native-identity-nonempty-denial.log"
        path.write_text((root / "migration-downgrade-denial.log").read_text())
        _rehash(root, path)
    else:
        hashes_path = root / "results.sha256.json"
        hashes = json.loads(hashes_path.read_text())
        del hashes[path.name]
        hashes_path.write_text(json.dumps(hashes))
    with pytest.raises(
        (ValueError, RuntimeError), match="native_identity_(nonempty|downgrade)"
    ):
        verify_union(
            artifacts, commit=COMMIT, tree=TREE, manifest_sha256=_sha(MANIFEST)
        )


def _pg_root(artifacts):
    return artifacts / f"ctcc-linux-postgres-{COMMIT}" / "ctcc-validation"


def _rehash(root, *paths):
    hashes_path = root / "results.sha256.json"
    hashes = json.loads(hashes_path.read_text())
    for path in paths:
        hashes[path.relative_to(root).as_posix()] = _sha(path.read_bytes())
    hashes_path.write_text(json.dumps(hashes))


def test_paired_downgrade_policy_and_raw_log_alias_cannot_change_typed_baseline(
    tmp_path,
):
    artifacts, _ = _artifacts(tmp_path)
    root = _pg_root(artifacts)
    identity_path = root / "identity.json"
    identity = json.loads(identity_path.read_text())
    identity["migration_downgrade_policy"]["rollback_readback"][
        "execution_authority"
    ] = 0
    identity_path.write_text(json.dumps(identity))
    log_path = root / "migration-denial-readback.log"
    record = json.loads(log_path.read_text())
    record["execution_authority"] = 0
    log_path.write_text(json.dumps(record))
    _rehash(root, identity_path, log_path)
    with pytest.raises(ValueError, match="linux_union_component_identity_rejected"):
        verify_union(
            artifacts, commit=COMMIT, tree=TREE, manifest_sha256=_sha(MANIFEST)
        )


@pytest.mark.parametrize(
    ("field", "counter", "value", "reason"),
    (
        (
            "event_journal_schema_preflight",
            "failures",
            False,
            "preflight_report_invalid",
        ),
        (
            "runtime_bundle_custody_preflight",
            "errors",
            False,
            "runtime_custody_preflight_readback_changed",
        ),
        ("postgres_tests", "failures", False, "test_report_readback_changed"),
        ("collection", "selected_count", 1.0, "collection_readback_changed"),
    ),
)
def test_recomputed_native_summaries_require_exact_json_types(
    tmp_path, field, counter, value, reason
):
    artifacts, _ = _artifacts(tmp_path)
    root = _pg_root(artifacts)
    path = root / "identity.json"
    identity = json.loads(path.read_text())
    # A float of the real count preserves numerical equality and changes its type.
    identity[field][counter] = (
        float(identity[field][counter]) if field == "collection" else value
    )
    path.write_text(json.dumps(identity))
    _rehash(root, path)
    with pytest.raises(ValueError, match=reason):
        verify_union(
            artifacts, commit=COMMIT, tree=TREE, manifest_sha256=_sha(MANIFEST)
        )


@pytest.mark.parametrize("stage", POSTGRES_RESTART_STAGES)
def test_restart_pass_flags_do_not_replace_actual_ordered_commands(tmp_path, stage):
    artifacts, _ = _artifacts(tmp_path)
    root = _pg_root(artifacts)
    path = root / "steps.json"
    stages = json.loads(path.read_text())
    path.write_text(json.dumps([entry for entry in stages if entry["name"] != stage]))
    _rehash(root, path)
    with pytest.raises(ValueError, match="linux_union_restart_stage_missing"):
        verify_union(
            artifacts, commit=COMMIT, tree=TREE, manifest_sha256=_sha(MANIFEST)
        )


@pytest.mark.parametrize("damage", ("early", "unhashed_log", "changed_marker"))
def test_rehashed_restart_summary_cannot_replace_retained_runtime_evidence(
    tmp_path, damage
):
    artifacts, _ = _artifacts(tmp_path)
    root = _pg_root(artifacts)
    if damage == "early":
        path = root / "steps.json"
        stages = json.loads(path.read_text())
        target = next(entry for entry in stages if entry["name"] == "process-sigkill")
        stages.remove(target)
        stages.insert(0, target)
        path.write_text(json.dumps(stages))
        _rehash(root, path)
    elif damage == "unhashed_log":
        path = root / "results.sha256.json"
        hashes = json.loads(path.read_text())
        hashes.pop("crash-restart-independent-readback.log")
        path.write_text(json.dumps(hashes))
    else:
        path = root / "crash-probe" / "durable.json"
        envelope = json.loads(path.read_text())
        envelope["body"]["order_writes"] = False
        envelope["sha256"] = _sha(
            json.dumps(envelope["body"], sort_keys=True, separators=(",", ":")).encode()
        )
        path.write_text(json.dumps(envelope))
        identity_path = root / "identity.json"
        identity = json.loads(identity_path.read_text())
        identity["crash_probe_marker_sha256"] = _sha(path.read_bytes())
        identity_path.write_text(json.dumps(identity))
        _rehash(root, path, identity_path)
    with pytest.raises(ValueError, match="linux_union_restart_"):
        verify_union(
            artifacts, commit=COMMIT, tree=TREE, manifest_sha256=_sha(MANIFEST)
        )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("external_order_writes", False),
        ("external_order_writes", 0.0),
        ("shard_index", False),
        ("shard_count", 8.0),
    ),
)
def test_native_identity_counters_cannot_use_bool_or_float_aliases(
    tmp_path, field, value
):
    artifacts, _ = _artifacts(tmp_path)
    root = artifacts / f"ctcc-linux-shard-0-{COMMIT}" / "ctcc-validation"
    path = root / "identity.json"
    identity = json.loads(path.read_text())
    identity[field] = value
    path.write_text(json.dumps(identity))
    _rehash(root, path)
    with pytest.raises(ValueError, match="linux_union_component_identity_rejected"):
        verify_union(
            artifacts, commit=COMMIT, tree=TREE, manifest_sha256=_sha(MANIFEST)
        )


@pytest.mark.parametrize("mode", ("postgres", "shard"))
def test_suite_cannot_execute_before_its_build_and_migrations(tmp_path, mode):
    artifacts, _ = _artifacts(tmp_path)
    root = (
        _pg_root(artifacts)
        if mode == "postgres"
        else artifacts / f"ctcc-linux-shard-0-{COMMIT}" / "ctcc-validation"
    )
    path = root / "steps.json"
    stages = json.loads(path.read_text())
    common = [entry for entry in stages if entry["name"] in COMMON_STAGES]
    rest = [entry for entry in stages if entry["name"] not in COMMON_STAGES]
    path.write_text(json.dumps([*rest, *common]))
    _rehash(root, path)
    with pytest.raises(
        ValueError, match="linux_union_stage_inventory_or_order_invalid"
    ):
        verify_union(
            artifacts, commit=COMMIT, tree=TREE, manifest_sha256=_sha(MANIFEST)
        )


@pytest.mark.parametrize(
    "damage",
    (
        "incomplete_marker",
        "missing_readback_line",
        "seed_ready_missing",
        "api_live_enabled",
    ),
)
def test_restart_pass_requires_producer_complete_marker_and_actual_success_logs(
    tmp_path, damage
):
    artifacts, _ = _artifacts(tmp_path)
    root = _pg_root(artifacts)
    if damage == "incomplete_marker":
        path = root / "crash-probe" / "durable.json"
        envelope = json.loads(path.read_text())
        envelope["body"] = {
            key: envelope["body"][key]
            for key in ("schema", "execution_authority", "order_writes")
        }
        envelope["sha256"] = _sha(
            json.dumps(envelope["body"], sort_keys=True, separators=(",", ":")).encode()
        )
        path.write_text(json.dumps(envelope))
        identity_path = root / "identity.json"
        identity = json.loads(identity_path.read_text())
        identity["crash_probe_marker_sha256"] = _sha(path.read_bytes())
        identity_path.write_text(json.dumps(identity))
        _rehash(root, path, identity_path)
    elif damage == "missing_readback_line":
        path = root / "crash-restart-independent-readback.log"
        path.write_text("\n".join(path.read_text().splitlines()[1:]) + "\n")
        _rehash(root, path)
    elif damage == "seed_ready_missing":
        path = root / "crash-seed-readback-log.log"
        path.write_text("synthetic-seed-unconfirmed\n")
        _rehash(root, path)
    else:
        path = root / "api-restart-health.log"
        values = json.loads(path.read_text())
        values["api/version"]["live_trading"] = True
        path.write_text(json.dumps(values))
        _rehash(root, path)
    with pytest.raises(ValueError, match="linux_union_restart_"):
        verify_union(
            artifacts, commit=COMMIT, tree=TREE, manifest_sha256=_sha(MANIFEST)
        )


@pytest.mark.parametrize(
    "damage", ("missing", "failed", "boolean", "duplicate", "late")
)
def test_union_requires_actual_early_runtime_custody_preflight(tmp_path, damage):
    artifacts, _ = _artifacts(tmp_path)
    root = _pg_root(artifacts)
    path = root / "steps.json"
    stages = json.loads(path.read_text())
    target = next(
        step for step in stages if step["name"] == "runtime-bundle-custody-preflight"
    )
    if damage == "missing":
        stages.remove(target)
    elif damage == "failed":
        target["exit_code"] = 1
    elif damage == "boolean":
        target["exit_code"] = False
    elif damage == "duplicate":
        stages.append(dict(target))
    else:
        stages.remove(target)
        stages.append(target)
    path.write_text(json.dumps(stages))
    _rehash(root, path)
    # A globally malformed or failed stage is now rejected before preflight parsing.
    with pytest.raises(
        ValueError, match="runtime_custody_preflight_stage_missing|linux_union_stage_"
    ):
        verify_union(
            artifacts, commit=COMMIT, tree=TREE, manifest_sha256=_sha(MANIFEST)
        )


@pytest.mark.parametrize(
    "relative",
    (
        "runtime-bundle-custody-preflight.log",
        "test-results/runtime-bundle-custody-preflight.xml",
    ),
)
def test_union_requires_hashed_runtime_custody_preflight_evidence(tmp_path, relative):
    artifacts, _ = _artifacts(tmp_path)
    root = _pg_root(artifacts)
    path = root / "results.sha256.json"
    hashes = json.loads(path.read_text())
    hashes.pop(relative)
    path.write_text(json.dumps(hashes))
    with pytest.raises(ValueError, match="runtime_custody_preflight_hash_missing"):
        verify_union(
            artifacts, commit=COMMIT, tree=TREE, manifest_sha256=_sha(MANIFEST)
        )


@pytest.mark.parametrize("damage", ("missing_case", "changed_case", "skipped_case"))
def test_union_replays_exact_runtime_custody_cases_after_rehash(tmp_path, damage):
    artifacts, _ = _artifacts(tmp_path)
    root = _pg_root(artifacts)
    path = root / "test-results/runtime-bundle-custody-preflight.xml"
    document = ET.fromstring(path.read_bytes())
    suite = document.find("testsuite")
    case = suite.find("testcase")
    if damage == "missing_case":
        suite.remove(case)
        suite.set("tests", "3")
    elif damage == "changed_case":
        case.set("name", "test_unreviewed_replacement")
    else:
        ET.SubElement(case, "skipped")
        suite.set("skipped", "1")
    path.write_bytes(ET.tostring(document))
    _rehash(root, path)
    with pytest.raises(ValueError, match="pytest_required_execution_not_verified"):
        verify_union(
            artifacts, commit=COMMIT, tree=TREE, manifest_sha256=_sha(MANIFEST)
        )


@pytest.mark.parametrize(
    "damage", ("missing_receipt", "changed_receipt", "changed_xml")
)
def test_union_requires_exact_runtime_custody_preflight_readback(tmp_path, damage):
    artifacts, _ = _artifacts(tmp_path)
    root = _pg_root(artifacts)
    path = root / "identity.json"
    identity = json.loads(path.read_text())
    if damage == "missing_receipt":
        identity.pop("runtime_bundle_custody_preflight")
    elif damage == "changed_receipt":
        identity["runtime_bundle_custody_preflight"]["passed"] = 3
    else:
        xml = root / "test-results/runtime-bundle-custody-preflight.xml"
        xml.write_bytes(xml.read_bytes() + b"\n")
        _rehash(root, xml)
    path.write_text(json.dumps(identity))
    _rehash(root, path)
    with pytest.raises(ValueError, match="runtime_custody_preflight_readback_changed"):
        verify_union(
            artifacts, commit=COMMIT, tree=TREE, manifest_sha256=_sha(MANIFEST)
        )


@pytest.mark.parametrize(
    "damage", ("wrong_exception", "success_exit", "missing_refusal")
)
def test_union_never_accepts_arbitrary_or_successful_downgrade(tmp_path, damage):
    artifacts, _ = _artifacts(tmp_path)
    root = artifacts / f"ctcc-linux-shard-0-{COMMIT}" / "ctcc-validation"
    if damage == "wrong_exception":
        path = root / "migration-downgrade-denial.log"
        path.write_text(
            path.read_text().replace(DOWNGRADE_DENIAL, "database_unavailable")
        )
        hashes_path = root / "results.sha256.json"
        hashes = json.loads(hashes_path.read_text())
        hashes[path.name] = _sha(path.read_bytes())
        hashes_path.write_text(json.dumps(hashes))
    else:
        steps_path = root / "steps.json"
        steps = json.loads(steps_path.read_text())
        refusal = next(
            step for step in steps if step["name"] == "migration-downgrade-denial"
        )
        if damage == "success_exit":
            refusal["exit_code"] = 0
        else:
            refusal.pop("policy_refusal")
        steps_path.write_text(json.dumps(steps))
        hashes_path = root / "results.sha256.json"
        hashes = json.loads(hashes_path.read_text())
        hashes[steps_path.name] = _sha(steps_path.read_bytes())
        hashes_path.write_text(json.dumps(hashes))
    with pytest.raises(ValueError, match="policy|common_stage"):
        verify_union(
            artifacts, commit=COMMIT, tree=TREE, manifest_sha256=_sha(MANIFEST)
        )


def test_union_rejects_post_denial_database_head_change_even_with_rehashed_log(
    tmp_path,
):
    artifacts, _ = _artifacts(tmp_path)
    root = artifacts / f"ctcc-linux-shard-0-{COMMIT}" / "ctcc-validation"
    path = root / "migration-reidentity.log"
    record = json.loads(path.read_text())
    record["database_head"] = "0041"
    path.write_text(json.dumps(record))
    hashes_path = root / "results.sha256.json"
    hashes = json.loads(hashes_path.read_text())
    hashes[path.name] = _sha(path.read_bytes())
    hashes_path.write_text(json.dumps(hashes))
    with pytest.raises(ValueError, match="linux_union_migration_readback_changed"):
        verify_union(
            artifacts, commit=COMMIT, tree=TREE, manifest_sha256=_sha(MANIFEST)
        )


@pytest.mark.parametrize(
    "filename,section,field",
    (
        (
            "migration-reupgrade-readback.log",
            "migration_empty_roundtrip",
            "reupgrade_readback",
        ),
        (
            "migration-denial-readback.log",
            "migration_downgrade_policy",
            "rollback_readback",
        ),
    ),
)
def test_union_pins_db0046_catalog_when_both_identity_and_log_are_rehashed(
    tmp_path, filename, section, field
):
    artifacts, _ = _artifacts(tmp_path)
    root = artifacts / f"ctcc-linux-shard-0-{COMMIT}" / "ctcc-validation"
    record_path = root / filename
    record = json.loads(record_path.read_text())
    record["public_runtime_bundle_schema"]["functions"][0][1] = "0" * 64
    record_path.write_text(json.dumps(record))
    identity_path = root / "identity.json"
    identity = json.loads(identity_path.read_text())
    identity[section][field] = record
    identity_path.write_text(json.dumps(identity))
    hashes_path = root / "results.sha256.json"
    hashes = json.loads(hashes_path.read_text())
    for changed in (record_path, identity_path):
        hashes[changed.name] = _sha(changed.read_bytes())
    hashes_path.write_text(json.dumps(hashes))
    with pytest.raises(ValueError, match="linux_union_component_identity_rejected"):
        verify_union(
            artifacts, commit=COMMIT, tree=TREE, manifest_sha256=_sha(MANIFEST)
        )


@pytest.mark.parametrize(
    "name,field,value",
    (
        ("migration-bundle-empty-readback.log", "observed", "0044"),
        ("migration-empty-readback.log", "observed", "0043"),
        ("migration-guard-empty-readback.log", "observed", "0043"),
        ("migration-hwm-empty-readback.log", "observed", "0044"),
        (
            "migration-empty-readback.log",
            "account_revision_witness_table_present",
            True,
        ),
        (
            "migration-empty-readback.log",
            "account_revision_witness_guard_present",
            True,
        ),
        ("migration-reupgrade-readback.log", "observed", "0042"),
        (
            "migration-denial-readback.log",
            "account_revision_witness_table_present",
            False,
        ),
        ("migration-denial-readback.log", "account_revision_witness_trigger_count", 2),
        (
            "migration-denial-readback.log",
            "account_revision_witness_trigger_catalog",
            [["account_revision_source_witness_guard", "D", 7, True]],
        ),
    ),
)
def test_union_rejects_rehashed_roundtrip_or_rollback_tamper(
    tmp_path, name, field, value
):
    artifacts, _ = _artifacts(tmp_path)
    root = artifacts / f"ctcc-linux-shard-0-{COMMIT}" / "ctcc-validation"
    path = root / name
    record = json.loads(path.read_text())
    record[field] = value
    path.write_text(json.dumps(record))
    hashes_path = root / "results.sha256.json"
    hashes = json.loads(hashes_path.read_text())
    hashes[path.name] = _sha(path.read_bytes())
    hashes_path.write_text(json.dumps(hashes))
    with pytest.raises(ValueError, match="linux_union_migration_readback_changed"):
        verify_union(
            artifacts, commit=COMMIT, tree=TREE, manifest_sha256=_sha(MANIFEST)
        )


@pytest.mark.parametrize(
    "stage",
    (
        "migration-native-identity-empty-before",
        "migration-native-identity-empty-downgrade",
        "migration-native-identity-empty-readback",
        "migration-bundle-empty-downgrade",
        "migration-bundle-empty-readback",
        "migration-guard-empty-readback",
        "migration-hwm-empty-readback",
        "migration-empty-readback",
        "migration-reupgrade-drift",
        "migration-denial-readback",
    ),
)
def test_union_requires_all_db0047_transition_stages(tmp_path, stage):
    artifacts, _ = _artifacts(tmp_path)
    root = artifacts / f"ctcc-linux-shard-0-{COMMIT}" / "ctcc-validation"
    steps_path = root / "steps.json"
    steps = json.loads(steps_path.read_text())
    steps = [entry for entry in steps if entry["name"] != stage]
    steps_path.write_text(json.dumps(steps))
    hashes_path = root / "results.sha256.json"
    hashes = json.loads(hashes_path.read_text())
    hashes[steps_path.name] = _sha(steps_path.read_bytes())
    hashes_path.write_text(json.dumps(hashes))
    with pytest.raises(ValueError, match="linux_union_common_stage_missing"):
        verify_union(
            artifacts, commit=COMMIT, tree=TREE, manifest_sha256=_sha(MANIFEST)
        )


def test_union_requires_hash_for_empty_downgrade_log(tmp_path):
    artifacts, _ = _artifacts(tmp_path)
    root = artifacts / f"ctcc-linux-shard-0-{COMMIT}" / "ctcc-validation"
    hashes_path = root / "results.sha256.json"
    hashes = json.loads(hashes_path.read_text())
    hashes.pop("migration-empty-downgrade.log")
    hashes_path.write_text(json.dumps(hashes))
    with pytest.raises(ValueError, match="linux_union_core_hash_missing"):
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


@pytest.mark.parametrize(
    "stage",
    (
        "migration-coverage-native-catalog",
        "migration-pair-guard-empty-before",
        "migration-pair-guard-empty-downgrade",
        "migration-pair-guard-empty-readback",
        "migration-coverage-empty-before",
        "migration-coverage-empty-downgrade",
        "migration-coverage-empty-readback",
    ),
)
def test_current_0049_union_cannot_omit_new_transition(tmp_path, stage):
    artifacts, _ = _artifacts(tmp_path)
    root = _pg_root(artifacts)
    path = root / "steps.json"
    path.write_text(
        json.dumps(
            [row for row in json.loads(path.read_text()) if row["name"] != stage]
        )
    )
    _rehash(root, path)
    with pytest.raises(ValueError):
        verify_union(
            artifacts, commit=COMMIT, tree=TREE, manifest_sha256=_sha(MANIFEST)
        )


@pytest.mark.parametrize(
    "damage",
    (
        "missing",
        "wrong_profile",
        "changed_row",
        "extra_predecessor_case",
        "admitted_capture",
    ),
)
def test_current_0049_and_isolated_0047_evidence_cannot_substitute_each_other(
    tmp_path, damage
):
    artifacts, _ = _artifacts(tmp_path)
    root = _pg_root(artifacts)
    if damage == "missing":
        path = root / "steps.json"
        path.write_text(
            json.dumps(
                [
                    row
                    for row in json.loads(path.read_text())
                    if row["name"] != "predecessor-0047-drop"
                ]
            )
        )
    elif damage == "wrong_profile":
        path = root / "predecessor-0047-identity.log"
        value = json.loads(path.read_text())
        value["database_head"] = "0048"
        path.write_text(json.dumps(value))
    elif damage == "changed_row":
        # Own49 evidence now lives in its genuine child receipt, not a primary50 log.
        path = root / "test-results/controlled-writer-pair-guard-migration.json"
        value = json.loads(path.read_text())
        value["after"]["rows_sha256"] = "0" * 64
        path.write_text(json.dumps(value))
    elif damage == "extra_predecessor_case":
        path = root / "test-results/predecessor-0047-two-cases.xml"
        _junit(
            path,
            [
                *(
                    f"{module.replace('.', '/')}.py::{case}"
                    for module, case in PREDECESSOR_0047_CASES
                ),
                "tests/unit/test_synthetic.py::test_not_in_formal_two",
            ],
        )
    else:
        path = root / "test-results/coverage-native-catalog.json"
        value = json.loads(path.read_text())
        value["native_baseline_admission"] = True
        path.write_text(json.dumps(value))
    _rehash(root, path)
    with pytest.raises((ValueError, RuntimeError)):
        verify_union(
            artifacts, commit=COMMIT, tree=TREE, manifest_sha256=_sha(MANIFEST)
        )


@pytest.mark.parametrize(
    "damage",
    (
        "old48_receipt",
        "missing_preflight",
        "old48_current_head",
        "rehashed_wrong_refusal",
    ),
)
def test_0049_union_requires_distinct_pair_guard_receipt_and_early_commit_proof(
    tmp_path, damage
):
    """Rehashed synthetic artifacts cannot relabel0048 or omit the preflight."""
    artifacts, _ = _artifacts(tmp_path)
    root = _pg_root(artifacts)
    if damage == "old48_receipt":
        path = root / "test-results/controlled-writer-pair-guard-migration.json"
        path.write_bytes(
            (
                root / "test-results/controlled-writer-coverage-migration.json"
            ).read_bytes()
        )
    elif damage == "missing_preflight":
        path = root / "steps.json"
        steps = json.loads(path.read_text())
        path.write_text(
            json.dumps(
                [
                    row
                    for row in steps
                    if row["name"] != "controlled-writer-coverage-preflight"
                ]
            )
        )
    elif damage == "old48_current_head":
        path = root / "migration-pair-guard-empty-before.log"
        path.write_text(json.dumps(_revision_readback("0048")))
    else:
        path = root / "test-results/postgres-intent.xml"
        document = ET.fromstring(path.read_bytes())
        item = next(
            item
            for item in document.iter("property")
            if item.get("name") == "owned49_refusal"
        )
        item.set("value", COVERAGE_DOWNGRADE_DENIAL)
        path.write_bytes(ET.tostring(document))
    _rehash(root, path)
    with pytest.raises((ValueError, RuntimeError)):
        verify_union(
            artifacts, commit=COMMIT, tree=TREE, manifest_sha256=_sha(MANIFEST)
        )


def test_0049_preflight_real_producer_report_roundtrips_through_union_reader(tmp_path):
    """Actual producer/parser functions with synthetic XML only; no PG run."""
    from scripts.verify_linux_shard_union import _verify_preflight_reports

    results = tmp_path / "test-results"
    results.mkdir()
    identity, hashes, calls = {}, {}, []

    def container(stage, command, **options):
        calls.append((stage, command, options))
        assert stage == "controlled-writer-coverage-preflight"
        _junit(
            results / f"{stage}.xml",
            [
                f"{module.replace('.', '/')}.py::{case}"
                for module, case in COVERAGE_PREFLIGHT_CASES
            ],
        )
        (tmp_path / f"{stage}.log").write_text(
            "synthetic-container-produced-reviewed-XML-only\n"
        )

    identity["controlled_writer_coverage_preflight"] = (
        run_controlled_writer_coverage_preflight(
            container,
            test_results=results,
            mounts=(),
        )
    )
    partition_calls = []

    def partition_container(stage, command, **options):
        partition_calls.append((stage, command, options))
        assert stage == "account-history-partition-preflight"
        expected = [
            f"{module.replace('.', '/')}.py::{case}"
            for module, case in HISTORY_PARTITION_PREFLIGHT_CASES
        ]
        assert command[-len(expected) :] == expected
        _junit(results / f"{stage}.xml", expected)
        (tmp_path / f"{stage}.log").write_text(
            "synthetic-container-produced-reviewed-XML-only\n"
        )

    identity["account_history_partition_preflight"] = run_history_partition_preflight(
        partition_container,
        test_results=results,
        mounts=(),
    )
    partition_module = HISTORY_PARTITION_PREFLIGHT_CASES[0][0]
    assert identity["account_history_partition_preflight"][
        "required_module_passed"
    ] == {partition_module: len(HISTORY_PARTITION_PREFLIGHT_CASES)}
    assert len(partition_calls) == 1
    module = COVERAGE_PREFLIGHT_CASES[0][0]
    assert identity["controlled_writer_coverage_preflight"][
        "required_module_passed"
    ] == {module: 3}
    for stage, field, cases in (
        (
            "event-journal-schema-preflight",
            "event_journal_schema_preflight",
            LEDGER_SCHEMA_PREFLIGHT_CASES,
        ),
        ("gate3-postgres-preflight", "gate3_preflight", GATE3_PREFLIGHT_CASES),
    ):
        xml = results / f"{stage}.xml"
        _junit(
            xml, [f"{module.replace('.', '/')}.py::{case}" for module, case in cases]
        )
        identity[field] = verify_pytest_report(xml, required_cases=cases)
        assert identity[field]["required_module_passed"] == {}
        (tmp_path / f"{stage}.log").write_text("synthetic-case-only-report\n")
    for stage in (
        "account-history-partition-preflight",
        "controlled-writer-coverage-preflight",
        "event-journal-schema-preflight",
        "gate3-postgres-preflight",
    ):
        for path in (results / f"{stage}.xml", tmp_path / f"{stage}.log"):
            hashes[path.relative_to(tmp_path).as_posix()] = _sha(path.read_bytes())
    _verify_preflight_reports(tmp_path, hashes, identity)
    assert len(calls) == 1
    # One passing partition case cannot stand in for the exact two-case proof,
    # even when the altered XML is explicitly rehashed by this fixture.
    relative = "test-results/account-history-partition-preflight.xml"
    partition_xml = tmp_path / relative
    complete_xml = partition_xml.read_bytes()
    _junit(
        partition_xml,
        [
            f"{module.replace('.', '/')}.py::{case}"
            for module, case in HISTORY_PARTITION_PREFLIGHT_CASES[:1]
        ],
    )
    hashes[relative] = _sha(partition_xml.read_bytes())
    with pytest.raises(ValueError, match="pytest_required_execution_not_verified"):
        _verify_preflight_reports(tmp_path, hashes, identity)
    partition_xml.write_bytes(complete_xml)
    hashes[relative] = _sha(complete_xml)
    _verify_preflight_reports(tmp_path, hashes, identity)
    # The previous fixture's empty module mapping must no longer camouflage
    # this producer/consumer mismatch, even though all three cases still pass.
    identity["controlled_writer_coverage_preflight"]["required_module_passed"] = {}
    with pytest.raises(ValueError, match="linux_union_preflight_report_invalid"):
        _verify_preflight_reports(tmp_path, hashes, identity)


def _current0050_synthetic_artifact_layer(artifacts):
    """Only invented filesystem fixtures; never a retained or native receipt."""
    from app.domain.account_history_partition_catalog_v1 import TABLES
    from scripts import verify_final_hermetic as producer

    source = json.loads(SOURCE_FILES)
    for attachment in artifacts.iterdir():
        root = attachment / "ctcc-validation"
        results = root / "test-results"
        identity = json.loads((root / "identity.json").read_bytes())
        identity["migration"]["source_head"] = identity["migration"][
            "database_head"
        ] = "0050"
        roundtrip = identity["migration_empty_roundtrip"]
        roundtrip.update(
            source_head="0050",
            history_partition_before_readback=_revision_readback("0050"),
            history_partition_down_revision="0049",
            history_partition_down_readback=producer.expected_history_partition_absent_readback(),
            reupgrade_readback=_revision_readback("0050"),
        )
        identity["migration_downgrade_policy"].update(
            source_head="0050",
            database_head_after="0050",
            rollback_readback=_revision_readback("0050"),
        )
        for log in [
            "migration-identity",
            "migration-reupgrade-identity",
            "migration-reidentity",
        ]:
            (root / f"{log}.log").write_text(
                json.dumps(identity["migration"]), encoding="utf-8"
            )
        for log in [
            "migration-reupgrade-readback",
            "migration-denial-readback",
            "migration-history-partition-empty-before",
        ]:
            (root / f"{log}.log").write_text(
                json.dumps(_revision_readback("0050")), encoding="utf-8"
            )
        (root / "migration-history-partition-empty-readback.log").write_text(
            json.dumps(producer.expected_history_partition_absent_readback()),
            encoding="utf-8",
        )
        denial = root / "migration-downgrade-denial.log"
        denial.write_bytes(
            b"INFO Running downgrade 0050 -> 0049\n" + denial.read_bytes()
        )
        capture_path = results / "coverage-native-catalog.json"
        coverage_capture = json.loads(capture_path.read_bytes())
        coverage_capture["source_head"] = coverage_capture["database_head"] = "0050"
        capture_path.write_text(json.dumps(coverage_capture), encoding="utf-8")
        partition_capture = {
            **coverage_capture,
            "schema": "ctcc.account_history_partition_native_catalog_observation.v1",
            "source_files": {
                name: source[name]["sha256"]
                for name in producer.HISTORY_PARTITION_CAPTURE_PATHS
            },
            "manifest_sha256": _sha(MANIFEST),
        }
        path = results / "account-history-partition-native-catalog.json"
        path.write_text(json.dumps(partition_capture), encoding="utf-8")
        for label, evidence_path in [
            ("migration-history-partition-native-catalog", path),
            ("migration-coverage-native-catalog", capture_path),
        ]:
            (root / f"{label}.log").write_text(
                json.dumps(
                    {
                        "artifact": "test-results/" + evidence_path.name,
                        "sha256": _sha(evidence_path.read_bytes()),
                        "native_baseline_admission": False,
                        "execution_authority": False,
                    }
                ),
                encoding="utf-8",
            )
        for log in ["predecessor-0047-identity"]:
            path = root / f"{log}.log"
            if path.exists():
                value = json.loads(path.read_bytes())
                value["source_head"] = "0050"
                path.write_text(json.dumps(value), encoding="utf-8")
        if identity["suite"] == "postgres":
            identity["predecessor_0047_validation"]["source_head"] = "0050"
            cases = producer.HISTORY_PARTITION_PREFLIGHT_CASES
            assert type(cases) is tuple and cases
            stage = "account-history-partition-preflight"
            xml = results / f"{stage}.xml"
            _junit(
                xml,
                [f"{module.replace('.', '/')}.py::{case}" for module, case in cases],
            )
            identity["account_history_partition_preflight"] = (
                producer.run_history_partition_preflight(
                    lambda *_args, **_kwargs: None, test_results=results, mounts=()
                )
            )
            (root / f"{stage}.log").write_text(
                "synthetic-preflight-fixture-only\n", encoding="utf-8"
            )
            pair = identity["pair_guard_migration_acceptance"]
            xml = results / "postgres-intent.xml"
            tree = ET.fromstring(xml.read_bytes())
            target = next(
                c
                for c in tree.iter("testcase")
                if c.get("name")
                == "test_nonempty_0049_downgrade_refuses_and_retains_exact_catalog"
            )
            properties = ET.SubElement(target, "properties")
            values = {
                "owned49_database": "ctcc_cwc_" + "a" * 32,
                "owned49_owner": "ctcc",
                "owned49_schema": "public",
                "owned49_head_before": "0049",
                "owned49_head_after": "0049",
                "owned49_edge": "0049 -> 0048",
                "owned49_sqlstate": "P0001",
                "owned49_refusal": PAIR_GUARD_DOWNGRADE_DENIAL,
                "owned49_source_frame": "0049_controlled_writer_pair_guard_definer.py:downgrade",
                "owned49_catalog_sha256": pair["before"]["catalog_sha256"],
                "owned49_rows_sha256": pair["before"]["rows_sha256"],
            }
            for name, value in values.items():
                ET.SubElement(properties, "property", name=name, value=value)
            xml.write_bytes(ET.tostring(tree))
            identity["historical_0049_nonempty_actual_case"] = (
                producer.verify_historical_0049_nonempty_case(xml, pair)
            )
            identity["postgres_tests"] = verify_pytest_report(
                xml,
                required_cases=ARCHIVE_REQUIRED_CASES,
                required_modules=tuple(
                    sorted(
                        path.removesuffix(".py").replace("/", ".")
                        for path in POSTGRES_TEST_PATHS
                    )
                ),
            )
            rows = {TABLES[0]: [{"synthetic_fixture_only": True}], TABLES[1]: []}
            retention = {
                "rows": rows,
                "counts": {TABLES[0]: 1, TABLES[1]: 0},
                "rows_sha256": _sha(
                    json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()
                ),
            }
            snapshot = producer.history_partition_nonempty_snapshot(
                _revision_readback("0050"), retention
            )
            identity["history_partition_nonempty_downgrade_policy"] = (
                producer.history_partition_nonempty_downgrade_readback(
                    snapshot, snapshot
                )
            )
            for label in [
                "migration-history-partition-nonempty-before",
                "migration-history-partition-nonempty-after",
            ]:
                (root / f"{label}.log").write_text(
                    json.dumps(snapshot), encoding="utf-8"
                )
            (root / "migration-history-partition-nonempty-denial.log").write_text(
                "INFO Running downgrade 0050 -> 0049\n"
                '  File "/app/migrations/versions/0050_account_history_partition.py", line 49\n'
                f"asyncpg.exceptions.RaiseError: {producer.HISTORY_PARTITION_DOWNGRADE_DENIAL}\n"
                f"sqlalchemy.exc.DBAPIError: (asyncpg.Error): {producer.HISTORY_PARTITION_DOWNGRADE_DENIAL}\n",
                encoding="utf-8",
            )
            snap = {
                "head": "0050",
                "rows_sha256": retention["rows_sha256"],
                "counts": retention["counts"],
                "catalog_sha256": "f" * 64,
            }
            receipt = {
                "schema_version": "ctcc.account_history_partition_migration_acceptance.v1",
                "manifest_sha256": _sha(MANIFEST),
                "repository_sha256": "6" * 64,
                "migration_sha256": "7" * 64,
                "server_major": 17,
                "before": snap,
                "after": snap,
                "refusal_code": producer.HISTORY_PARTITION_DOWNGRADE_DENIAL,
                "downgrade_target": "0049",
                "native_baseline_admission": False,
                "execution_authority": False,
            }
            (results / "account-history-partition-migration.json").write_text(
                json.dumps(receipt), encoding="utf-8"
            )
            identity["history_partition_migration_acceptance"] = receipt
        steps = json.loads((root / "steps.json").read_bytes())
        for step in steps:
            if step["name"] == "migration-history-partition-nonempty-denial":
                step["exit_code"] = 1
                step["policy_refusal"] = producer.HISTORY_PARTITION_DOWNGRADE_DENIAL
        (root / "steps.json").write_text(json.dumps(steps), encoding="utf-8")
        (root / "identity.json").write_text(json.dumps(identity), encoding="utf-8")
        hashes = {
            path.relative_to(root).as_posix(): _sha(path.read_bytes())
            for path in root.rglob("*")
            if path.is_file() and path.name != "results.sha256.json"
        }
        (root / "results.sha256.json").write_text(json.dumps(hashes), encoding="utf-8")


def test_0050_partition_preflight_real_producer_report_roundtrips_through_union_reader(
    tmp_path,
):
    from scripts import verify_linux_shard_union as reader

    artifacts, _full = _artifacts(tmp_path)
    root = artifacts / f"ctcc-linux-postgres-{COMMIT}" / "ctcc-validation"
    identity = json.loads((root / "identity.json").read_bytes())
    hashes = json.loads((root / "results.sha256.json").read_bytes())
    reader._verify_preflight_reports(root, hashes, identity)
    report = identity["account_history_partition_preflight"]
    module = HISTORY_PARTITION_PREFLIGHT_CASES[0][0]
    assert report["required_module_passed"] == {
        module: len(HISTORY_PARTITION_PREFLIGHT_CASES)
    }
    assert report["tests"] == len(HISTORY_PARTITION_PREFLIGHT_CASES)
    assert report["skipped"] == 0
    report["required_module_passed"] = {}
    with pytest.raises(ValueError, match="linux_union_preflight_report_invalid"):
        reader._verify_preflight_reports(root, hashes, identity)


def test_0050_partition_module_is_exclusively_postgres_selected():
    path = "tests/integration/test_account_history_partition_repository.py"
    assert path in POSTGRES_TEST_PATHS
    assert selected_for("postgres", None, path + "::actual_case")
    assert not any(
        selected_for("shard", index, path + "::actual_case")
        for index in range(SHARD_COUNT)
    )


def _current0051_synthetic_artifact_layer(artifacts):
    """Invented IO fixtures only, never genuine native catalog or acceptance."""
    from app.domain import prospective_event_cutover_catalog_v2 as policy
    from scripts import verify_final_hermetic as producer

    assert (
        policy.PG17_DEPARSED_BASELINE
        == "531b56a9680701a6f1bd462c52d0c57edb883194cbf88c75b84bfa7b391b66fd"
    )
    assert policy.PG17_DEPARSED_BASELINE_STATUS == "SEALED_FROM_REVIEWED_NATIVE_PG17"
    source_files = json.loads(SOURCE_FILES)
    for attachment in artifacts.iterdir():
        root = attachment / "ctcc-validation"
        results = root / "test-results"
        identity = json.loads((root / "identity.json").read_bytes())
        identity["migration"]["source_head"] = identity["migration"][
            "database_head"
        ] = "0051"
        identity["migration_empty_roundtrip"].update(
            source_head="0051",
            prospective_cutover_before_readback=_revision_readback("0051"),
            prospective_cutover_down_revision="0050",
            prospective_cutover_down_readback=producer.expected_prospective_cutover_absent_readback(),
            reupgrade_readback=_revision_readback("0051"),
        )
        identity["migration_downgrade_policy"].update(
            source_head="0051",
            database_head_after="0051",
            rollback_readback=_revision_readback("0051"),
        )
        for label in (
            "migration-identity",
            "migration-reupgrade-identity",
            "migration-reidentity",
        ):
            (root / (label + ".log")).write_text(
                json.dumps(identity["migration"]), encoding="utf-8"
            )
        for label in (
            "migration-reupgrade-readback",
            "migration-denial-readback",
            "migration-prospective-cutover-empty-before",
        ):
            (root / (label + ".log")).write_text(
                json.dumps(_revision_readback("0051")), encoding="utf-8"
            )
        (root / "migration-prospective-cutover-empty-readback.log").write_text(
            json.dumps(producer.expected_prospective_cutover_absent_readback()),
            encoding="utf-8",
        )
        denial = root / "migration-downgrade-denial.log"
        denial.write_bytes(
            b"INFO Running downgrade 0051 -> 0050\n" + denial.read_bytes()
        )
        for filename in (
            "coverage-native-catalog.json",
            "account-history-partition-native-catalog.json",
        ):
            path = results / filename
            value = json.loads(path.read_bytes())
            value["source_head"] = value["database_head"] = "0051"
            if filename.startswith("account-history"):
                value["schema"] = (
                    "ctcc.account_history_partition_native_catalog_observation.v2"
                )
            path.write_text(json.dumps(value), encoding="utf-8")
        capture = {
            "schema": "ctcc.prospective_cutover_native_catalog_observation.v2",
            "source_head": "0051",
            "database_head": "0051",
            "source_migration_sha256": identity["migration"]["migration_sha256"],
            "session_facts": ["17.0", "170000", "ctcc", "ctcc", "ctcc", "pg_catalog"],
            "catalog": {"synthetic_fixture_only": True},
            "source_files": {
                name: source_files[name]["sha256"]
                for name in producer.PROSPECTIVE_CUTOVER_CAPTURE_PATHS
            },
            "manifest_sha256": _sha(MANIFEST),
            "effective_ddl_sha256": policy.ddl_identity(),
            "observed_deparser_sha256": "c" * 64,
            "native_baseline_admission": False,
            "g6_qualified": False,
            "execution_authority": False,
        }
        path = results / "prospective-cutover-native-catalog.json"
        path.write_text(json.dumps(capture), encoding="utf-8")
        for label, filename in (
            (
                "migration-prospective-cutover-native-catalog",
                "prospective-cutover-native-catalog.json",
            ),
            (
                "migration-history-partition-native-catalog",
                "account-history-partition-native-catalog.json",
            ),
            ("migration-coverage-native-catalog", "coverage-native-catalog.json"),
        ):
            value = {
                "artifact": "test-results/" + filename,
                "sha256": _sha((results / filename).read_bytes()),
                "native_baseline_admission": False,
                "execution_authority": False,
            }
            if filename.startswith("prospective"):
                value["g6_qualified"] = False
            (root / (label + ".log")).write_text(json.dumps(value), encoding="utf-8")
        if identity["suite"] == "postgres":
            identity["predecessor_0047_validation"]["source_head"] = "0051"
            value = json.loads((root / "predecessor-0047-identity.log").read_bytes())
            value["source_head"] = "0051"
            (root / "predecessor-0047-identity.log").write_text(
                json.dumps(value), encoding="utf-8"
            )
            before_path = root / "migration-history-partition-nonempty-before.log"
            previous = json.loads(before_path.read_bytes())
            snapshot = producer.history_partition_nonempty_snapshot_0051(
                _revision_readback("0051"), previous["retention"]
            )
            identity["history_partition_nonempty_downgrade_policy"] = (
                producer.history_partition_nonempty_downgrade_readback_0051(
                    snapshot, snapshot
                )
            )
            for label in (
                "migration-history-partition-nonempty-before",
                "migration-history-partition-nonempty-after",
            ):
                (root / (label + ".log")).write_text(
                    json.dumps(snapshot), encoding="utf-8"
                )
            path = root / "migration-history-partition-nonempty-denial.log"
            path.write_bytes(
                b"INFO Running downgrade 0051 -> 0050\n" + path.read_bytes()
            )
            receipt_path = results / "account-history-partition-migration.json"
            receipt = json.loads(receipt_path.read_bytes())
            receipt["schema_version"] = (
                "ctcc.account_history_partition_migration_acceptance.v2"
            )
            receipt["before"]["head"] = receipt["after"]["head"] = "0051"
            receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
            identity["history_partition_migration_acceptance"] = receipt
        (root / "identity.json").write_text(json.dumps(identity), encoding="utf-8")
        hashes = {
            path.relative_to(root).as_posix(): _sha(path.read_bytes())
            for path in root.rglob("*")
            if path.is_file() and path.name != "results.sha256.json"
        }
        (root / "results.sha256.json").write_text(json.dumps(hashes), encoding="utf-8")
    assert (
        policy.PG17_DEPARSED_BASELINE
        == "531b56a9680701a6f1bd462c52d0c57edb883194cbf88c75b84bfa7b391b66fd"
    )


@pytest.mark.parametrize(
    "damage",
    (
        "head",
        "source_pin",
        "g6",
        "missing_capture",
        "deparser_hash",
        "baseline_flag",
        "missing_stage",
        "wrong_cutover_absence",
    ),
)
def test_current0051_union_rejects_rehashed_cutover_fabrication(tmp_path, damage):
    artifacts, _ = _artifacts(tmp_path)
    # Prove the complete current51 synthetic artifact set reaches the genuine
    # reader before damaging it; an unrelated fixture mismatch cannot pass this.
    verify_union(artifacts, commit=COMMIT, tree=TREE, manifest_sha256=_sha(MANIFEST))
    attachment = next(artifacts.iterdir())
    root = attachment / "ctcc-validation"
    path = root / "test-results/prospective-cutover-native-catalog.json"
    value = json.loads(path.read_bytes())
    if damage == "head":
        value["source_head"] = "0050"
    elif damage == "source_pin":
        value["source_files"][next(iter(value["source_files"]))] = "0" * 64
    elif damage == "g6":
        value["g6_qualified"] = True
    elif damage == "deparser_hash":
        value["observed_deparser_sha256"] = "0" * 64
    elif damage == "baseline_flag":
        value["native_baseline_admission"] = True
    elif damage == "missing_capture":
        path.unlink()
    elif damage == "missing_stage":
        stages_path = root / "steps.json"
        stages = json.loads(stages_path.read_bytes())
        stages = [
            row
            for row in stages
            if row["name"] != "migration-prospective-cutover-empty-downgrade"
        ]
        stages_path.write_text(json.dumps(stages), encoding="utf-8")
    else:
        assert damage == "wrong_cutover_absence"
        identity_path = root / "identity.json"
        identity = json.loads(identity_path.read_bytes())
        identity["migration_empty_roundtrip"]["prospective_cutover_down_readback"][
            "table_present"
        ] = True
        identity_path.write_text(json.dumps(identity), encoding="utf-8")
    if path.exists():
        path.write_text(json.dumps(value), encoding="utf-8")
    hashes = {
        p.relative_to(root).as_posix(): _sha(p.read_bytes())
        for p in root.rglob("*")
        if p.is_file() and p.name != "results.sha256.json"
    }
    (root / "results.sha256.json").write_text(json.dumps(hashes), encoding="utf-8")
    refusal = {
        "missing_capture": "linux_union_prospective_cutover_capture_missing_or_unsafe",
        "missing_stage": "linux_union_common_stage_missing",
        "wrong_cutover_absence": "linux_union_component_identity_rejected",
    }.get(damage, "prospective_cutover_native_catalog_observation_invalid")
    with pytest.raises(ValueError, match=refusal):
        verify_union(
            artifacts, commit=COMMIT, tree=TREE, manifest_sha256=_sha(MANIFEST)
        )
