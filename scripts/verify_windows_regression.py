"""Verify the source-bound, credential-free Windows CI regression evidence.

This job covers native Windows unit tests and explicitly reviewed integration
tests that need no PostgreSQL. It cannot establish Docker, exchange, Demo, or
Live acceptance, and a Windows platform skip never counts as a passed test.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import stat
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path, PurePosixPath

from scripts import manifest

NO_DB_INTEGRATION = (
    "test_api.py",
    "test_binance_reference_batch_flow.py",
    "test_binance_reference_probe_flow.py",
    "test_external_benchmark_acquisition_flow.py",
    "test_external_benchmark_reference_flow.py",
    "test_mie_shadow_contract_integration.py",
    "test_paper_api.py",
)
POSTGRES_INTEGRATION = (
    "test_account_bill_archive_claim_repository.py",
    "test_account_capture_crash_probe_repository.py",
    "test_account_history_query_verifier_repository.py",
    "test_account_ingestion_journal_repository.py",
    "test_account_locked_source_join_repository.py",
    "test_account_observation_index_repository.py",
    "test_account_portfolio_components_repository.py",
    "test_control_bound_ledger_repository.py",
    "test_dashboard_snapshot_audit_integration.py",
    "test_database_schema.py",
    "test_demo_adaptive_portfolio_schema_integration.py",
    "test_demo_control_durability_probe.py",
    "test_demo_control_repository.py",
    "test_durable_migration_downgrade.py",
    "test_gate3_canonical_schedule_claim.py",
    "test_gate3_capture_schedule_pin_repository.py",
    "test_gate3_schedule_publication_ack.py",
    "test_history_submission_intent_repository.py",
    "test_ledger_event_observation_repository.py",
    "test_lifecycle_repository.py",
    "test_okx_live_execution_repository_integration.py",
    "test_okx_live_repository_integration.py",
    "test_okx_live_schema_integration.py",
    "test_persistence_repository.py",
    "test_public_receipt_witness_repository.py",
    "test_qualification_bootstrap_repository.py",
    "test_qualification_ledger_repository.py",
    "test_qualification_submission_intent_repository.py",
    "test_range_v5_reservation_repository.py",
    "test_submission_reporting_repository.py",
)

# Exact approved test and reason pairs. A new skip requires source review; a
# broadly matching reason string must not turn an arbitrary skipped test green.
POSIX_SKIP_POLICY = {
    (
        "tests.unit.research.test_public_receipt_storage",
        "test_native_symlink_root_rejected",
    ): "native POSIX symlink case executed on Linux; Windows uses reparse handles",
    (
        "tests.unit.test_final_hermetic_archive",
        "test_copied_source_rejects_executable_bit_drift",
    ): "POSIX executable bits require Linux",
    (
        "tests.unit.test_qualification_evidence_gate",
        "test_native_posix_real_chain_publishes_six_files_and_keeps_report_pre_g12",
    ): "Windows full ancestor-pin publication is not verified by POSIX tests",
    (
        "tests.unit.test_qualification_evidence_gate",
        "test_native_posix_identical_retry_cannot_reuse_g12_completion",
    ): "Windows full ancestor-pin publication is not verified by POSIX tests",
    (
        "tests.unit.test_qualification_evidence_gate",
        "test_native_posix_expired_before_write_leaves_no_report_directory",
    ): "Windows full ancestor-pin publication is not verified by POSIX tests",
    (
        "tests.unit.test_qualification_evidence_gate",
        "test_native_posix_expiry_after_readback_retains_real_receipt_and_files",
    ): "Windows full ancestor-pin publication is not verified by POSIX tests",
    (
        "tests.unit.test_qualification_market_bridge",
        "test_v2_actual_g12_six_file_readback_remains_unauthorized",
    ): "POSIX native publication; no Windows ancestor-pin success claim",
    (
        "tests.unit.test_qualification_one_shot",
        "test_native_g12_disk_readback_before_owned_capture",
    ): "Actual POSIX six-file publication; Windows success not inferred",
    (
        "tests.unit.test_release_dependencies",
        "test_egg_directory_symlink_rejected",
    ): "Native symlink proof runs in Linux acceptance; no Windows ACL changes",
    (
        "tests.unit.test_release_dependencies",
        "test_pkg_info_symlink_rejected",
    ): "Native symlink proof runs in Linux acceptance; no Windows ACL changes",
    (
        "tests.unit.test_trade_evidence_notion_adapter",
        "test_native_posix_worker_adapter_and_durable_outbox_chain",
    ): "Real POSIX storage proof only; Windows ancestor-pin denial is not bypassed",
    (
        "tests.unit.test_trade_evidence_outbox",
        "test_native_posix_journal_publication_readback_and_immutability",
    ): "Native POSIX pin/flock proof requires POSIX; not Windows success evidence",
    (
        "tests.unit.test_trade_evidence_outbox",
        "test_native_posix_identical_enqueue_and_conflict_never_replace_bytes",
    ): "Native POSIX no-clobber proof requires POSIX; not Windows success evidence",
    (
        "tests.unit.test_trade_evidence_outbox",
        "test_native_posix_two_workers_cannot_both_claim",
    ): "Native POSIX concurrent root lease proof requires POSIX; not Windows success evidence",
    (
        "tests.unit.test_trade_evidence_outbox",
        "test_native_posix_linked_paths_are_refused_without_altering_outside_bytes",
    ): "Native POSIX symlink and hardlink proof requires POSIX; not Windows success evidence",
    (
        "tests.unit.test_trade_evidence_post_submit",
        "test_native_posix_post_submit_and_separate_worker_publish_and_verify_real_journal",
    ): "Genuine POSIX post-submit journal + Mock Notion proof; not Windows publication evidence",
    (
        "tests.unit.test_trade_evidence_storage",
        "test_posix_directory_symlinks_are_rejected_without_following",
    ): "POSIX symlink behavior; Windows junctions tested separately",
}
PRIVILEGE_SKIP_POLICY = {
    (
        "tests.unit.research.test_artifacts",
        "test_artifact_verification_rejects_symlinks",
    ): "Windows symlink privilege unavailable (WinError 1314); required native symlink acceptance runs in immutable Linux validation",
    (
        "tests.unit.research.test_evidence_io",
        "test_evidence_paths_reject_symlinks",
    ): "Windows symlink privilege unavailable (WinError 1314); required native symlink acceptance runs in immutable Linux validation",
    (
        "tests.unit.test_manifest",
        "test_manifest_rejects_unlisted_source_symlink",
    ): "source symlink creation requires OS privilege",
}
WINDOWS_REQUIRED_CASES = (
    (
        "tests.unit.test_highvol_installer_validator",
        "test_native_parse_and_repeated_dry_run_do_not_execute_installer",
    ),
    (
        "tests.unit.test_qualification_evidence_gate",
        "test_native_windows_owned_root_reports_real_pin_outcome_without_fallback",
    ),
    (
        "tests.unit.test_trade_evidence_outbox",
        "test_native_windows_late_envelope_denial_retains_state_journal",
    ),
    (
        "tests.unit.test_trade_evidence_outbox",
        "test_native_windows_root_publication_delivery_and_conflict_preserve_history",
    ),
    (
        "tests.unit.test_trade_evidence_outbox",
        "test_native_windows_two_workers_cannot_both_claim",
    ),
    (
        "tests.unit.test_trade_evidence_outbox_worker",
        "test_native_windows_worker_denial_or_real_empty_root_is_fail_closed",
    ),
    (
        "tests.unit.test_trade_evidence_storage",
        "test_windows_junctions_are_rejected_without_touching_the_target",
    ),
    (
        "tests.unit.test_trade_evidence_storage",
        "test_native_windows_late_write_failure_preserves_durable_name_and_bytes",
    ),
    (
        "tests.unit.test_verify_migration_identity",
        "test_native_powershell_parser_and_isolated_probe_readback",
    ),
)
HEX40 = re.compile(r"[0-9a-f]{40}\Z")
HEX64 = re.compile(r"[0-9a-f]{64}\Z")


class WindowsRegressionError(ValueError):
    """Fail-closed CI evidence rejection with a non-secret reason code."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json(path: Path) -> dict:
    if (
        path.is_symlink()
        or not path.is_file()
        or not 0 < path.stat().st_size <= 8 * 1024 * 1024
    ):
        raise WindowsRegressionError("json_evidence_missing_or_unsafe")
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise WindowsRegressionError("json_object_required")
    return value


def test_selection(root: Path) -> tuple[str, ...]:
    """Require a reviewed classification for every integration test module."""
    integration = root / "tests" / "integration"
    observed = {path.name for path in integration.glob("test_*.py") if path.is_file()}
    expected = set(NO_DB_INTEGRATION) | set(POSTGRES_INTEGRATION)
    if (
        not observed
        or observed != expected
        or len(expected) != (len(NO_DB_INTEGRATION) + len(POSTGRES_INTEGRATION))
    ):
        raise WindowsRegressionError("integration_module_classification_changed")
    if not (root / "tests" / "unit").is_dir():
        raise WindowsRegressionError("unit_test_directory_missing")
    return ("tests/unit",) + tuple(
        f"tests/integration/{name}" for name in NO_DB_INTEGRATION
    )


def _case_matches(classname: str, name: str, target: tuple[str, str]) -> bool:
    module, base = target
    return (classname == module or classname.startswith(module + ".")) and (
        name == base or name.startswith(base + "[")
    )


def _junit_key(nodeid: str) -> tuple[str, str]:
    # This is the pinned pytest JUnit plugin's exact nodeid conversion. A
    # different pytest version must first pass the dependency lock verifier.
    from _pytest.junitxml import bin_xml_escape, mangle_test_address

    names = mangle_test_address(nodeid)
    if len(names) < 2 or not names[-1]:
        raise WindowsRegressionError("windows_collection_nodeid_invalid")
    return ".".join(names[:-1]), bin_xml_escape(names[-1])


def pytest_addoption(parser) -> None:
    parser.addoption(
        "--ctcc-windows-collection-json",
        action="store",
        default=None,
        help="Write the exact case list collected by this Windows CI invocation",
    )


def pytest_collection_finish(session) -> None:
    """Capture the test set from the very invocation that executes it."""
    output_name = session.config.getoption("--ctcc-windows-collection-json")
    if output_name is None:
        return
    output = Path(output_name)
    root = Path.cwd().resolve(strict=True)
    items = session.items
    if not items:
        raise WindowsRegressionError("windows_collection_empty")
    for option_name in ("keyword", "markexpr", "ignore", "ignore_glob", "deselect"):
        if getattr(session.config.option, option_name, None) not in (
            None,
            "",
            [],
            (),
        ):
            raise WindowsRegressionError("windows_collection_filtered")
    cases = []
    nodeids = set()
    keys = set()
    for item in items:
        nodeid = item.nodeid
        key = _junit_key(nodeid)
        if nodeid in nodeids or key in keys:
            raise WindowsRegressionError("windows_collection_duplicate_case")
        nodeids.add(nodeid)
        keys.add(key)
        cases.append({"nodeid": nodeid, "classname": key[0], "name": key[1]})
    record = {
        "schema": "ctcc.windows_ci_collection.v1",
        "requested_paths": [str(path) for path in session.config.args],
        "manifest_sha256": _sha256(root / "MANIFEST.sha256"),
        "python": sys.version,
        "selection_filters_absent": True,
        "cases": cases,
    }
    with output.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(record, stream, sort_keys=True, separators=(",", ":"))
        stream.write("\n")
    if _json(output) != record:
        raise WindowsRegressionError("windows_collection_readback_changed")


def verify_collection(path: Path, root: Path) -> tuple[set[tuple[str, str]], dict]:
    """Reject subset collection, changed source, or altered nodeid mapping."""
    record = _json(path)
    if (
        record.get("schema") != "ctcc.windows_ci_collection.v1"
        or record.get("requested_paths") != list(test_selection(root))
        or record.get("manifest_sha256") != _sha256(root / "MANIFEST.sha256")
        or record.get("python") != sys.version
        or record.get("selection_filters_absent") is not True
        or not isinstance(record.get("cases"), list)
        or not record["cases"]
    ):
        raise WindowsRegressionError("windows_collection_identity_invalid")
    keys = set()
    nodeids = set()
    collected_paths = set()
    for case in record["cases"]:
        if not isinstance(case, dict) or not isinstance(case.get("nodeid"), str):
            raise WindowsRegressionError("windows_collection_case_invalid")
        nodeid = case["nodeid"]
        key = _junit_key(nodeid)
        if (
            case.get("classname") != key[0]
            or case.get("name") != key[1]
            or key in keys
            or nodeid in nodeids
        ):
            raise WindowsRegressionError("windows_collection_case_invalid")
        keys.add(key)
        nodeids.add(nodeid)
        collected_paths.add(nodeid.partition("::")[0].replace("\\", "/"))
    unit_files = {
        path.relative_to(root).as_posix()
        for path in (root / "tests" / "unit").rglob("test_*.py")
        if path.is_file()
    }
    if not unit_files or not unit_files.issubset(collected_paths):
        raise WindowsRegressionError("windows_collection_unit_module_missing")
    return keys, {"sha256": _sha256(path), "collected": len(keys)}


def classify_approved_skip(classname: str, name: str, skip: ET.Element) -> str:
    """Admit only an exact reviewed test/reason pair from a pytest JUnit case."""
    matches = [
        (kind, expected_reason)
        for kind, policy in (
            ("posix_only", POSIX_SKIP_POLICY),
            ("windows_symlink_privilege", PRIVILEGE_SKIP_POLICY),
        )
        for target, expected_reason in policy.items()
        if _case_matches(classname, name, target)
    ]
    if (
        len(matches) != 1
        or skip.get("type") != "pytest.skip"
        or skip.get("message") != matches[0][1]
    ):
        raise WindowsRegressionError("windows_junit_unapproved_skip")
    return matches[0][0]


def verify_junit(
    path: Path, expected_cases: set[tuple[str, str]] | None = None
) -> dict:
    """Count every case and allow only audited platform-specific skips."""
    if (
        path.is_symlink()
        or not path.is_file()
        or not 0 < path.stat().st_size <= 64 * 1024 * 1024
    ):
        raise WindowsRegressionError("windows_junit_missing_or_unsafe")
    raw = path.read_bytes()
    root = ET.fromstring(raw)
    if root.tag != "testsuites" or len(root) != 1 or root[0].tag != "testsuite":
        raise WindowsRegressionError("windows_junit_schema_invalid")
    suite = root[0]
    cases = suite.findall("testcase")
    totals = {
        "tests": len(cases),
        "failures": sum(case.find("failure") is not None for case in cases),
        "errors": sum(case.find("error") is not None for case in cases),
        "skipped": sum(case.find("skipped") is not None for case in cases),
    }
    if not cases or any(suite.get(key) != str(value) for key, value in totals.items()):
        raise WindowsRegressionError("windows_junit_counts_conflict")
    no_db_passed = {name: 0 for name in NO_DB_INTEGRATION}
    windows_required_passed = {target: 0 for target in WINDOWS_REQUIRED_CASES}
    allowed_skips = []
    seen_cases = set()
    for case in cases:
        classname, name = case.get("classname"), case.get("name")
        if not isinstance(classname, str) or not isinstance(name, str) or not name:
            raise WindowsRegressionError("windows_junit_case_identity_missing")
        identity = (classname, name)
        if identity in seen_cases:
            raise WindowsRegressionError("windows_junit_duplicate_case")
        seen_cases.add(identity)
        module_match = next(
            (
                module
                for module in NO_DB_INTEGRATION
                if classname == "tests.integration." + module.removesuffix(".py")
            ),
            None,
        )
        if not classname.startswith("tests.unit.") and module_match is None:
            raise WindowsRegressionError("windows_junit_unselected_module")
        if (
            len(case.findall("failure"))
            + len(case.findall("error"))
            + len(case.findall("skipped"))
            > 1
        ):
            raise WindowsRegressionError("windows_junit_conflicting_outcomes")
        skip = case.find("skipped")
        if skip is not None:
            kind = classify_approved_skip(classname, name, skip)
            allowed_skips.append(
                {
                    "classname": classname,
                    "name": name,
                    "reason": skip.get("message"),
                    "kind": kind,
                }
            )
            continue
        if module_match is not None and not any(
            case.find(tag) is not None for tag in ("failure", "error")
        ):
            no_db_passed[module_match] += 1
        for target in WINDOWS_REQUIRED_CASES:
            if _case_matches(classname, name, target) and not any(
                case.find(tag) is not None for tag in ("failure", "error")
            ):
                windows_required_passed[target] += 1
    if totals["failures"] or totals["errors"]:
        raise WindowsRegressionError("windows_junit_failures")
    if expected_cases is not None and seen_cases != expected_cases:
        raise WindowsRegressionError("windows_junit_collected_cases_differ")
    if any(count <= 0 for count in no_db_passed.values()):
        raise WindowsRegressionError("windows_junit_no_db_module_missing")
    if any(count <= 0 for count in windows_required_passed.values()):
        raise WindowsRegressionError("windows_junit_native_case_missing")
    return {
        **totals,
        "passed": totals["tests"]
        - totals["failures"]
        - totals["errors"]
        - totals["skipped"],
        "sha256": hashlib.sha256(raw).hexdigest(),
        "allowed_skips": allowed_skips,
        "no_db_integration_modules_passed": no_db_passed,
        "required_windows_cases_passed": [
            {
                "module": module,
                "name": name,
                "passed": windows_required_passed[(module, name)],
            }
            for module, name in WINDOWS_REQUIRED_CASES
        ],
    }


def verify_archive_readback(archive: Path, root: Path) -> str:
    """Re-read every exact archived file from the extracted test source."""
    if (
        archive.is_symlink()
        or not archive.is_file()
        or archive.stat().st_size > 64 * 1024 * 1024
    ):
        raise WindowsRegressionError("windows_source_archive_missing_or_unsafe")
    seen = set()
    count = 0
    total_uncompressed = 0
    with zipfile.ZipFile(archive) as zipped:
        for info in zipped.infolist():
            name = info.filename
            relative = PurePosixPath(name)
            canonical_name = relative.as_posix() + ("/" if info.is_dir() else "")
            if (
                not name
                or name.startswith("/")
                or "\\" in name
                or ":" in name
                or name != canonical_name
                or any(part in {"", ".", ".."} for part in relative.parts)
                or name in seen
                or stat.S_IFMT(info.external_attr >> 16) == stat.S_IFLNK
            ):
                raise WindowsRegressionError("windows_source_archive_entry_unsafe")
            seen.add(name)
            if info.is_dir():
                continue
            total_uncompressed += info.file_size
            if (
                info.file_size > 32 * 1024 * 1024
                or total_uncompressed > 128 * 1024 * 1024
            ):
                raise WindowsRegressionError("windows_source_archive_too_large")
            destination = root.joinpath(*relative.parts)
            if destination.is_symlink() or not destination.is_file():
                raise WindowsRegressionError("windows_source_archive_readback_missing")
            if any(
                parent.is_symlink() or parent.is_junction()
                for parent in destination.parents
            ):
                raise WindowsRegressionError("windows_source_archive_readback_linked")
            if destination.read_bytes() != zipped.read(info):
                raise WindowsRegressionError("windows_source_archive_readback_changed")
            count += 1
    if count <= 0:
        raise WindowsRegressionError("windows_source_archive_empty")
    return _sha256(archive)


def verify_evidence(
    root: Path,
    archive: Path,
    source_identity_path: Path,
    dependency_result_path: Path,
    collection_path: Path,
    junit_path: Path,
) -> dict:
    """Bind test results to one exact committed source and lock identity."""
    test_selection(root)
    source = _json(source_identity_path)
    if (
        source.get("schema") != "ctcc.windows_ci_source.v1"
        or not isinstance(source.get("commit"), str)
        or not HEX40.fullmatch(source["commit"])
        or source.get("commit") != source.get("github_sha")
        or not isinstance(source.get("tree"), str)
        or not HEX40.fullmatch(source["tree"])
        or not isinstance(source.get("archive_sha256"), str)
        or not HEX64.fullmatch(source["archive_sha256"])
        or not isinstance(source.get("manifest_sha256"), str)
        or not HEX64.fullmatch(source["manifest_sha256"])
        or source.get("runner_label") != "windows-2025"
    ):
        raise WindowsRegressionError("windows_source_identity_invalid")
    if verify_archive_readback(archive, root) != source["archive_sha256"]:
        raise WindowsRegressionError("windows_source_archive_identity_changed")
    if _sha256(root / "MANIFEST.sha256") != source["manifest_sha256"]:
        raise WindowsRegressionError("windows_manifest_identity_changed")
    if not manifest.check_manifest(root, root / "MANIFEST.sha256"):
        raise WindowsRegressionError("windows_manifest_check_failed")
    lock_path = root / "requirements" / "validation-windows-py312.lock"
    lock_manifest = _json(root / "requirements" / "validation-windows-py312.json")
    pyproject_hash = _sha256(root / "pyproject.toml")
    lock_hash = _sha256(lock_path)
    if (
        lock_manifest.get("schema") != "ctcc.validation_dependency_lock.v1"
        or lock_manifest.get("target") != "windows"
        or lock_manifest.get("source_pyproject_sha256") != pyproject_hash
        or lock_manifest.get("lock_sha256") != lock_hash
    ):
        raise WindowsRegressionError("windows_lock_source_identity_changed")
    dependency = _json(dependency_result_path)
    installed = dependency.get("installed_inventory")
    if (
        dependency.get("target") != "windows"
        or dependency.get("lock_sha256") != lock_hash
        or dependency.get("packages") != len(lock_manifest.get("packages", ()))
        or dependency.get("accepted_full_validation_baseline") is not False
        or not isinstance(installed, dict)
        or installed.get("mode")
        not in {
            "exact_inventory_match",
            "isolated_exact_with_one_source_project_metadata_record",
        }
    ):
        raise WindowsRegressionError("windows_dependency_inventory_invalid")
    expected_cases, collection = verify_collection(collection_path, root)
    junit = verify_junit(junit_path, expected_cases)
    return {
        "schema": "ctcc.windows_ci_regression.v1",
        "scope": "windows_no_postgresql_selected_full_unit_and_no_db_integration",
        "source": source,
        "pyproject_sha256": pyproject_hash,
        "dependency_lock_sha256": lock_hash,
        "dependency_inventory": installed,
        "dependency_full_validation_baseline_accepted": dependency.get(
            "accepted_full_validation_baseline"
        ),
        "python": sys.version,
        "test_selection": list(test_selection(root)),
        "collection": collection,
        "junit": junit,
        "linux_posix_counterpart_verified": False,
        "postgresql_verified": False,
        "docker_verified": False,
        "demo_verified": False,
        "live_verified": False,
        "execution_authority": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--list-tests", action="store_true")
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--source-identity", type=Path)
    parser.add_argument("--dependency-result", type=Path)
    parser.add_argument("--collection", type=Path)
    parser.add_argument("--junit", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        root = args.root.resolve(strict=True)
        if args.list_tests:
            if any(
                value is not None
                for value in (
                    args.archive,
                    args.source_identity,
                    args.dependency_result,
                    args.collection,
                    args.junit,
                    args.output,
                )
            ):
                raise WindowsRegressionError("windows_ci_arguments_conflict")
            for selection in test_selection(root):
                print(selection)
            return 0
        if not all(
            value is not None
            for value in (
                args.archive,
                args.source_identity,
                args.dependency_result,
                args.collection,
                args.junit,
                args.output,
            )
        ):
            raise WindowsRegressionError("windows_ci_arguments_missing")
        result = verify_evidence(
            root,
            args.archive,
            args.source_identity,
            args.dependency_result,
            args.collection,
            args.junit,
        )
        with args.output.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(result, stream, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
        if _json(args.output) != result:
            raise WindowsRegressionError("windows_ci_result_readback_changed")
        print(json.dumps({"status": "PASS", "identity_sha256": _sha256(args.output)}))
        return 0
    except (OSError, ValueError, zipfile.BadZipFile, ET.ParseError) as exc:
        code = (
            str(exc) if isinstance(exc, WindowsRegressionError) else type(exc).__name__
        )
        print(f"WINDOWS_CI_FAIL_CLOSED:{code}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
