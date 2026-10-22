"""Two bounded CI timing experiments. This is never PostgreSQL/full acceptance."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import subprocess
import tarfile
import time
from itertools import pairwise
from pathlib import Path
from uuid import uuid4

from scripts.diagnose_runtime_custody_postgres import LOCK_SHA256, POSTGRES_IMAGE
from scripts.pg_phase_policy_v1 import (
    EXPERIMENTS,
    LAST_TWO,
    NEW_MODULE,
    decode,
    read_phase_prefix,
    redacted_junit,
    required,
)
from scripts.pg_wait_redaction_v1 import validate_formatted_sample
from scripts.verify_final_hermetic import (
    Run,
    archive_files,
    archive_tree,
    cleanup_resources,
)

BASELINE = "4f21e104f505a89d50f0257d80b37f7cc480daba"
ADDED_PATHS = frozenset(
    {
        ".github/workflows/pg-stage-budget-diagnostic.yml",
        "scripts/diagnose_pg_stage_budget_v1.py",
        "scripts/pg_phase_policy_v1.py",
        "scripts/pg_phase_plugin_v1.py",
        "scripts/pg_wait_observer_v1.py",
        "scripts/pg_wait_redaction_v1.py",
        "tests/unit/test_pg_phase_diagnostic_v1.py",
        "tests/unit/test_pg_wait_redaction_v1.py",
    }
)
EXECUTED = (
    "scripts/diagnose_pg_stage_budget_v1.py",
    "scripts/pg_phase_policy_v1.py",
    "scripts/pg_phase_plugin_v1.py",
    "scripts/pg_wait_observer_v1.py",
    "scripts/pg_wait_redaction_v1.py",
    "scripts/diagnose_runtime_custody_postgres.py",
    "scripts/verify_final_hermetic.py",
    "scripts/verify_linux_shard.py",
    "scripts/hermetic_pytest.py",
)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def write(path, value):
    raw = (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
    with path.open("xb") as file:
        file.write(raw)
        file.flush()
    return sha(raw)


def bounded(path, cap):
    required(path.is_file() and not path.is_symlink())
    with path.open("rb") as file:
        raw = file.read(cap + 1)
    required(0 < len(raw) <= cap)
    return raw


def source_guard(root, archive_raw, *, commit, tree):
    with tarfile.open(fileobj=io.BytesIO(archive_raw)) as archive:
        required(
            archive.pax_headers.get("comment") == commit
            and archive_tree(archive) == tree
        )
        files = archive_files(archive)
    required(files["requirements/validation-linux-py312.lock"]["sha256"] == LOCK_SHA256)
    for name in EXECUTED:
        required(sha(bounded(root / name, 2 * 1024 * 1024)) == files[name]["sha256"])
    return files


def validate_observer_result(raw, *, experiment, sample_count):
    value = decode(raw)
    required(
        type(value) is dict
        and set(value)
        == {
            "schema",
            "status",
            "measured_sample_count",
            "observations_complete",
            "reason",
            "raw_sql_or_identity_exported",
            "execution_authority",
            "sampling_elapsed_monotonic_ns",
            "sampling_budget_ns",
            "connection_close_grace_seconds",
        }
    )
    required(
        value["schema"] == "ctcc.pg_wait_observer_result.v1"
        and value["status"] in ("PASS", "FAIL")
        and type(value["measured_sample_count"]) is int
        and value["measured_sample_count"] == sample_count
        and type(value["observations_complete"]) is bool
        and value["reason"]
        in ("STOP_REQUEST_OBSERVED", "OBSERVER_INCOMPLETE_OR_DENIED")
        and value["raw_sql_or_identity_exported"] is False
        and value["execution_authority"] is False
    )
    required(value["observations_complete"] is (value["status"] == "PASS"))
    required(
        type(value["sampling_elapsed_monotonic_ns"]) is int
        and 0 <= value["sampling_elapsed_monotonic_ns"] <= 640_000_000_000
        and type(value["sampling_budget_ns"]) is int
        and value["sampling_budget_ns"]
        == (210 if experiment == "A" else 630) * 1_000_000_000
        and type(value["connection_close_grace_seconds"]) is int
        and value["connection_close_grace_seconds"] == 3
    )
    required(
        value["status"] != "PASS"
        or value["sampling_elapsed_monotonic_ns"] < value["sampling_budget_ns"]
    )
    return value


def export_measured_prefix(raw_root, export, experiment):
    """Only schema-validated, rewritten safe events/XML; missing means unknown."""
    result = {
        "status": "FAIL",
        "phase_prefix": "UNKNOWN",
        "wait_prefix": "UNKNOWN",
        "junit": "UNKNOWN",
        "raw_logs_exported": False,
        "execution_authority": False,
    }
    phase = raw_root / "phase-events.jsonl"
    if phase.exists():
        try:
            prefix = read_phase_prefix(
                bounded(phase, 4 * 1024 * 1024), experiment=experiment
            )
            result["phase_prefix"] = write(export / f"{experiment}-phases.json", prefix)
            result["phases_complete"] = prefix["complete"]
            result["all_expected_phases_passed"] = prefix["all_expected_phases_passed"]
            result["test_helper_instrumentation_complete"] = prefix[
                "test_helper_instrumentation_complete"
            ]
        except Exception:  # noqa: BLE001 - No malformed source or exception text exported.
            result["phase_prefix"] = "DENIED"
    waits = raw_root / "wait-samples.jsonl"
    observer = raw_root / "observer-result.json"
    if waits.exists():
        try:
            raw = bounded(waits, 4 * 1024 * 1024)
            required(len(raw) < 3 * 1024 * 1024)
            tail = None
            if not raw.endswith(b"\n"):
                complete, separator, fragment = raw.rpartition(b"\n")
                required(separator and complete)
                tail = {"sha256": sha(fragment), "bytes": len(fragment)}
                raw = complete + b"\n"
            samples = [validate_formatted_sample(line) for line in raw.splitlines()]
            required(1 <= len(samples) <= 64)
            required(
                [sample["sample_index"] for sample in samples]
                == list(range(len(samples)))
            )
            required(
                all(
                    a["elapsed_monotonic_ns"] <= b["elapsed_monotonic_ns"]
                    for a, b in pairwise(samples)
                )
            )
            value = "UNKNOWN"
            if observer.exists():
                try:
                    value = validate_observer_result(
                        bounded(observer, 4096),
                        experiment=experiment,
                        sample_count=len(samples),
                    )
                except Exception:  # noqa: BLE001 - Preserve validated samples even if the late marker is denied.
                    value = "DENIED"
            result["wait_prefix"] = write(
                export / f"{experiment}-wait-samples.json",
                {
                    "samples": samples,
                    "observer": value,
                    "measured_prefix": True,
                    "truncated_tail": tail,
                },
            )
            result["observer_complete"] = (
                value["observations_complete"]
                if type(value) is dict and tail is None
                else "UNKNOWN"
            )
            result["all_wait_samples_valid"] = all(
                sample["status"] == "PASS" for sample in samples
            )
        except Exception:  # noqa: BLE001
            result["wait_prefix"] = "DENIED"
    junit = raw_root / "native-junit.xml"
    if junit.exists():
        try:
            summary, clean = redacted_junit(
                bounded(junit, 1024 * 1024), experiment=experiment
            )
            result["junit"] = write(
                export / f"{experiment}-junit-summary.json", summary
            )
            with (export / f"{experiment}-junit.xml").open("xb") as file:
                file.write(clean)
            result["all_junit_cases_passed"] = summary["all_passed"]
        except Exception:  # noqa: BLE001
            result["junit"] = "DENIED"
    result["status"] = (
        "PASS"
        if all(
            result.get(key) is True
            for key in (
                "phases_complete",
                "all_expected_phases_passed",
                "observer_complete",
                "all_wait_samples_valid",
                "all_junit_cases_passed",
                "test_helper_instrumentation_complete",
            )
        )
        else "FAIL"
    )
    return result


def main():
    parser = argparse.ArgumentParser()
    for name in ("archive", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    for name in ("archive-sha256", "commit", "tree"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args()
    required(
        re.fullmatch("[a-f0-9]{40}", args.commit)
        and re.fullmatch("[a-f0-9]{40}", args.tree)
        and re.fullmatch("[a-f0-9]{64}", args.archive_sha256)
    )
    archive_raw = bounded(args.archive, 128 * 1024 * 1024)
    required(sha(archive_raw) == args.archive_sha256)
    files = source_guard(
        Path(__file__).resolve().parents[1],
        archive_raw,
        commit=args.commit,
        tree=args.tree,
    )
    args.output.mkdir(parents=True, exist_ok=False)
    export = args.output / "export"
    export.mkdir()
    file_map = args.output / "source-files.json"
    file_map_sha = write(file_map, files)
    run = Run(args.output, budget_seconds=18 * 60, heartbeat_seconds=30)
    image_suffix = uuid4().hex[:12]
    image = f"ctcc-pg-phase:{args.commit[:12]}-{image_suffix}"
    report = {
        "schema": "ctcc.pg_stage_budget_diagnostic.v1",
        "status": "FAIL",
        "scope": "A_fresh_last2_B_fresh_new12_then_last2_same_pytest_process",
        "commit": args.commit,
        "tree": args.tree,
        "archive_sha256": args.archive_sha256,
        "manifest_sha256": files["MANIFEST.sha256"]["sha256"],
        "source_files_sha256": file_map_sha,
        "baseline_parent_required_by_workflow": BASELINE,
        "executed_source_sha256": {name: files[name]["sha256"] for name in EXECUTED},
        "dependency_lock_sha256": LOCK_SHA256,
        "postgres_image": POSTGRES_IMAGE,
        "expected_executions": 16,
        "expected_unique_cases": 14,
        "preregistered_budgets_seconds": {
            "runner": 1080,
            "A_pytest": 180,
            "A_observer": 210,
            "B_pytest": 600,
            "B_observer": 630,
            "each_cleanup": 45,
        },
        "B_last_two_may_be_unobserved_if_budget_exhausts": True,
        "carryover_comparison_requires_all_B14_complete": True,
        "fixed_modules_only_not_full_collection_or_original_prefix": True,
        "even_diagnostic_PASS_cannot_identify_whole_CI_cause_or_rule_out_other_prefix_effects": True,
        "experiments": {
            name: {
                "status": "NOT_STARTED",
                "expected_case_count": len(EXPERIMENTS[name]),
                "phase_prefix": "UNKNOWN",
                "wait_prefix": "UNKNOWN",
                "junit": "UNKNOWN",
            }
            for name in ("A", "B")
        },
        "test_helper_wrapper_implementation_preserves_task_await": True,
        "native_factory_or_engine_listener_modified": False,
        "instrumented_diagnostic_overhead_includes_fsync_helper_wrappers_and_observer": True,
        "not_uninstrumented_full_suite_timing_or_acceptance": True,
        "empty_wait_sample_cannot_prove_no_blocking_between_samples": True,
        "host_environment_or_credentials": False,
        "internal_network_only": True,
        "raw_logs_or_private_payload_exported": False,
        "full_suite_timeout_modified": False,
        "full_postgresql_acceptance": False,
        "execution_authority": False,
        "external_order_writes": 0,
    }
    try:
        run.command(
            "build",
            [
                "docker",
                "build",
                "--no-cache",
                "--build-arg",
                f"CTCC_SOURCE_COMMIT={args.commit}",
                "--build-arg",
                f"CTCC_SOURCE_TREE={args.tree}",
                "-t",
                image,
                "-",
            ],
            input_bytes=archive_raw,
            timeout=600,
        )
        run.command(
            "image-identity",
            ["docker", "image", "inspect", "--format", "{{.Id}}", image],
            timeout=10,
        )
        image_id = bounded(args.output / "image-identity.log", 1024).decode().strip()
        required(re.fullmatch("sha256:[a-f0-9]{64}", image_id))
        report["image_id"] = image_id
        for experiment in ("A", "B"):
            suffix = uuid4().hex[:12]
            network, postgres = (
                f"ctcc-final-{suffix}-network",
                f"ctcc-final-{suffix}-postgres",
            )
            label = f"org.ctcc.validation.run={suffix}"
            raw_root = args.output / ("private-" + experiment)
            raw_root.mkdir()
            raw_root.chmod(0o777)
            mounts = [
                "--mount",
                f"type=bind,source={raw_root.resolve()},target=/validation-results",
            ]
            resources = []
            observer_attempted = False
            observer_name = f"ctcc-final-{suffix}-observer"
            result = {
                "status": "FAIL",
                "expected_case_count": len(EXPERIMENTS[experiment]),
                "pytest_exit_verified": False,
                "observer_exit_verified": False,
            }

            def container(
                stage,
                command,
                *,
                extra_mounts=(),
                timeout=120,
                detached=False,
                fixed_observer_name=observer_name,
                fixed_suffix=suffix,
                fixed_resources=resources,
                fixed_experiment=experiment,
                fixed_label=label,
                fixed_network=network,
                fixed_postgres=postgres,
            ):
                nonlocal observer_attempted
                if stage == "observer":
                    observer_attempted = True
                name = (
                    fixed_observer_name
                    if stage == "observer"
                    else f"ctcc-final-{fixed_suffix}-{stage}"
                )
                fixed_resources.append(("container", name))
                run.command(
                    f"{fixed_experiment}-{stage}",
                    [
                        "docker",
                        "run",
                        *(["-d"] if detached else ["--rm"]),
                        "--name",
                        name,
                        "--label",
                        fixed_label,
                        "--network",
                        fixed_network,
                        *extra_mounts,
                        "-e",
                        "ENVIRONMENT=test",
                        "-e",
                        "TRADING_MODE=analysis_only",
                        "-e",
                        f"CTCC_PG_PHASE_EXPERIMENT={fixed_experiment}",
                        "-e",
                        f"DATABASE_URL=postgresql+asyncpg://ctcc@{fixed_postgres}:5432/ctcc",
                        image,
                        *command,
                    ],
                    timeout=timeout,
                )

            try:
                resources.append(("network", network))
                run.command(
                    f"{experiment}-network",
                    [
                        "docker",
                        "network",
                        "create",
                        "--internal",
                        "--label",
                        label,
                        network,
                    ],
                    timeout=15,
                )
                resources.append(("container", postgres))
                run.command(
                    f"{experiment}-postgres",
                    [
                        "docker",
                        "run",
                        "-d",
                        "--name",
                        postgres,
                        "--label",
                        label,
                        "--network",
                        network,
                        "-e",
                        "POSTGRES_USER=ctcc",
                        "-e",
                        "POSTGRES_DB=ctcc",
                        "-e",
                        "POSTGRES_HOST_AUTH_METHOD=trust",
                        POSTGRES_IMAGE,
                    ],
                    timeout=30,
                )
                ready_deadline = min(time.monotonic() + 45, run.deadline)
                while subprocess.run(
                    [
                        "docker",
                        "exec",
                        postgres,
                        "pg_isready",
                        "-U",
                        "ctcc",
                        "-d",
                        "ctcc",
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                    check=False,
                ).returncode:
                    required(time.monotonic() < ready_deadline)
                    time.sleep(0.5)
                container(
                    "manifest", ["python", "scripts/manifest.py", "--check"], timeout=30
                )
                container(
                    "copy",
                    [
                        "python",
                        "-c",
                        "from pathlib import Path; from scripts.verify_final_hermetic import verify_copied_source; import sys; print(verify_copied_source(Path('/app'),Path('/verification/source-files.json'),sys.argv[1]))",
                        file_map_sha,
                    ],
                    extra_mounts=[
                        "--mount",
                        f"type=bind,source={file_map.resolve()},target=/verification/source-files.json,readonly",
                    ],
                    timeout=30,
                )
                container("migration", ["alembic", "upgrade", "head"], timeout=60)
                container(
                    "observer",
                    ["python", "-m", "scripts.pg_wait_observer_v1"],
                    extra_mounts=mounts,
                    detached=True,
                    timeout=15,
                )
                selection = (
                    list(LAST_TWO) if experiment == "A" else [NEW_MODULE, *LAST_TWO]
                )
                container(
                    "pytest",
                    [
                        "python",
                        "-m",
                        "scripts.hermetic_pytest",
                        "-p",
                        "no:cacheprovider",
                        "-p",
                        "scripts.pg_phase_plugin_v1",
                        "--ctcc-pg-phase-experiment=" + experiment,
                        "-vv",
                        "--junitxml=/validation-results/native-junit.xml",
                        *selection,
                    ],
                    extra_mounts=mounts,
                    timeout=180 if experiment == "A" else 600,
                )
                result["pytest_exit_verified"] = True
            except Exception as error:  # noqa: BLE001 - Fixed type only; no raw native payload.
                result["failure_type"] = type(error).__name__
            finally:
                try:
                    with (raw_root / "observer-stop").open("xb") as file:
                        file.write(b"STOP\n")
                except OSError:
                    result["observer_stop_signal"] = "DENIED"
                try:
                    # Separate bounded wait: even when run's budget has expired,
                    # no discovery, restart or signal is used to claim success.
                    if observer_attempted:
                        waited = subprocess.run(
                            ["docker", "wait", observer_name],
                            capture_output=True,
                            timeout=10,
                            check=False,
                        )
                        result["observer_exit_verified"] = (
                            waited.returncode == 0 and waited.stdout.strip() == b"0"
                        )
                except (OSError, subprocess.SubprocessError):
                    result["observer_exit_verified"] = False
                cleanup = cleanup_resources(resources, suffix, budget_seconds=45)
                result["cleanup_sha256"] = write(
                    export / f"{experiment}-cleanup.json", cleanup
                )
                result["owned_cleanup_complete"] = all(
                    row["status"] in ("ABSENT", "REMOVED") for row in cleanup
                )
                result["measured_evidence"] = export_measured_prefix(
                    raw_root, export, experiment
                )
                result["status"] = (
                    "PASS"
                    if (
                        result["pytest_exit_verified"]
                        and result["observer_exit_verified"]
                        and result["owned_cleanup_complete"]
                        and result["measured_evidence"]["status"] == "PASS"
                    )
                    else "FAIL"
                )
                report["experiments"][experiment] = result
        report["status"] = (
            "PASS_DIAGNOSTIC_ONLY"
            if all(
                report["experiments"][name]["status"] == "PASS" for name in ("A", "B")
            )
            else "FAIL_OR_INCOMPLETE_DIAGNOSTIC"
        )
    except Exception as error:  # noqa: BLE001
        report["failure_type"] = type(error).__name__
    finally:
        write(export / "steps.json", run.steps)
        write(export / "identity.json", report)
        digest = write(
            export / "receipt.json",
            {
                "status": report["status"],
                "artifacts": {
                    path.name: sha(path.read_bytes())
                    for path in sorted(export.iterdir())
                },
            },
        )
        print(
            json.dumps(
                {
                    "status": report["status"],
                    "receipt_sha256": digest,
                    "execution_authority": False,
                }
            ),
            flush=True,
        )
    return 0 if report["status"] == "PASS_DIAGNOSTIC_ONLY" else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:  # noqa: BLE001 - Native payload must never become a public traceback.
        print('{"status":"PG_DIAGNOSTIC_FAILED_CLOSED","raw_payload_exported":false}')
        raise SystemExit(2) from None
