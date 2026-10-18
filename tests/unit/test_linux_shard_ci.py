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
    DOWNGRADE_DENIAL,
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
from scripts.verify_windows_regression import POSTGRES_INTEGRATION

COMMIT = "a" * 40
TREE = "b" * 40
ARCHIVE = b"reviewed-git-archive"
MANIFEST = b"reviewed-manifest\n"
SOURCE_FILES = b"{}"


def _revision_readback(revision: str) -> dict:
    result = {
        "schema": "ctcc.migration_revision_readback.v2",
        "expected": revision,
        "observed": revision,
        "execution_authority": False,
    }
    if revision in ("0001", "0042", "0043", "0044", "0045"):
        witness = revision in ("0043", "0044", "0045")
        result["account_revision_witness_table_present"] = witness
        result["account_revision_witness_guard_present"] = witness
        result["account_revision_witness_trigger_count"] = 3 if witness else 0
        result["account_revision_witness_trigger_catalog"] = (
            [list(row) for row in ACCOUNT_REVISION_WITNESS_TRIGGERS] if witness else []
        )
        hwm = revision in ("0044", "0045")
        result["account_measured_hwm_v2_table_present"] = hwm
        result["account_measured_hwm_v2_guard_present"] = hwm
        result["account_measured_hwm_v2_trigger_count"] = 3 if hwm else 0
        result["account_measured_hwm_v2_trigger_catalog"] = (
            [
                list(row)
                for row in (
                    ACCOUNT_MEASURED_HWM_V2_TRIGGERS_0045
                    if revision == "0045"
                    else ACCOUNT_MEASURED_HWM_V2_TRIGGERS
                )
            ]
            if hwm
            else []
        )
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
    migration = {
        "schema": "ctcc.migration_identity.v1",
        "status": "PASS",
        "source_head": "0045",
        "database_head": "0045",
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
                "source_head": "0045",
                "guard_down_revision": "0044",
                "guard_down_readback": _revision_readback("0044"),
                "hwm_down_revision": "0043",
                "hwm_down_readback": _revision_readback("0043"),
                "down_revision": "0042",
                "down_readback": _revision_readback("0042"),
                "reupgrade_readback": _revision_readback("0045"),
                "reupgrade_identity": "PASS",
                "schema_drift": "PASS",
            },
            "migration_downgrade_policy": {
                "source_head": "0045",
                "target": "0041",
                "result": "DENIED",
                "denied_at": "0042",
                "policy_refusal": DOWNGRADE_DENIAL,
                "database_head_after": "0045",
                "rollback_readback": _revision_readback("0045"),
                "schema_drift": "PASS",
            },
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
                    {
                        "name": stage_name,
                        "exit_code": (
                            1 if stage_name == "migration-downgrade-denial" else 0
                        ),
                        **(
                            {"policy_refusal": DOWNGRADE_DENIAL}
                            if stage_name == "migration-downgrade-denial"
                            else {}
                        ),
                    }
                    for stage_name in (*COMMON_STAGES, stage)
                ]
            )
        )
        (root / "source-files.json").write_bytes(SOURCE_FILES)
        supported_log = root / "migration-supported-readback.log"
        supported_log.write_text(json.dumps(identity["migration_supported_roundtrip"]))
        migration_log = root / "migration-identity.log"
        migration_log.write_text(json.dumps(migration))
        guard_empty_log = root / "migration-guard-empty-readback.log"
        guard_empty_log.write_text(json.dumps(_revision_readback("0044")))
        hwm_empty_log = root / "migration-hwm-empty-readback.log"
        hwm_empty_log.write_text(json.dumps(_revision_readback("0043")))
        empty_log = root / "migration-empty-readback.log"
        empty_log.write_text(json.dumps(_revision_readback("0042")))
        reupgrade_identity_log = root / "migration-reupgrade-identity.log"
        reupgrade_identity_log.write_text(json.dumps(migration))
        reupgrade_readback_log = root / "migration-reupgrade-readback.log"
        reupgrade_readback_log.write_text(json.dumps(_revision_readback("0045")))
        reidentity_log = root / "migration-reidentity.log"
        reidentity_log.write_text(json.dumps(migration))
        denial_readback_log = root / "migration-denial-readback.log"
        denial_readback_log.write_text(json.dumps(_revision_readback("0045")))
        denial_log = root / "migration-downgrade-denial.log"
        denial_log.write_text(
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
            empty_log,
            reupgrade_identity_log,
            reupgrade_readback_log,
            reidentity_log,
            denial_readback_log,
            denial_log,
            *stage_logs,
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
    "name,field,value",
    (
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
        "migration-guard-empty-readback",
        "migration-hwm-empty-readback",
        "migration-empty-readback",
        "migration-reupgrade-drift",
        "migration-denial-readback",
    ),
)
def test_union_requires_all_db0045_transition_stages(tmp_path, stage):
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
