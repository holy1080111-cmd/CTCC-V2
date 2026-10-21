"""Bounded, exact-source DB0046 diagnostics; never full validation or trading.

Only the four existing custody preflight cases run. PostgreSQL and the app
share a disposable internal network, with no host settings or credentials.
Native logs remain in runner temp; exported JUnit contains fixed categories
only, including when an unexpected fixture error would expose driver text.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import subprocess
import tarfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from uuid import uuid4

from scripts.verify_final_hermetic import (
    RUNTIME_CUSTODY_PREFLIGHT_CASES,
    Run,
    archive_files,
    archive_tree,
    cleanup_resources,
    run_runtime_bundle_custody_preflight,
)

POSTGRES_IMAGE = "postgres:17-alpine@sha256:f02121de6f74d30d8a94cd1d9584125e2178d7e6c377d8130112d4e52d867995"
LOCK_SHA256 = "a44e649cb1d21d3790e132c21699cc172e77ed87682929320cf8f6190fe014e4"
_CATEGORIES = frozenset(
    {
        "unclassified",
        "native_factory",
        "role_query",
        "role_scope",
        "function_query",
        "function_inventory",
        "function_attributes",
        "trigger_query",
        "trigger_inventory",
        "trigger_attributes",
        "column_query",
        "column_shape",
        "constraint_query",
        "constraint_inventory",
        "constraint_attributes",
        "transaction_query",
        "poisoned",
    }
)
_ROLE_CONDITIONS = frozenset(
    {
        "role_row_present",
        "direct_login",
        "nonprivileged",
        "no_other_role",
        "owner_separation",
        "no_table_access",
        "no_create",
        "ordinary_table",
        "persistent_table",
        "no_rls",
        "no_force_rls",
        "fsync",
        "full_page_writes",
        "synchronous_commit",
        "replication_origin",
        "not_recovery",
    }
)


def _write(path, record):
    raw = json.dumps(record, sort_keys=True, indent=2).encode() + b"\n"
    with path.open("xb") as stream:
        stream.write(raw)
    return hashlib.sha256(raw).hexdigest()


def redacted_report(raw):
    """Export counts and exact allowed cases, never original XML attributes/text."""
    if not 0 < len(raw) <= 1024 * 1024:
        raise ValueError("diagnostic_report_size_invalid")
    root = ET.fromstring(raw)
    if root.tag != "testsuites":
        raise ValueError("diagnostic_report_schema_invalid")
    allowed = set(RUNTIME_CUSTODY_PREFLIGHT_CASES)
    seen = set()
    records = []
    clean = ET.Element("testsuites")
    suite = ET.SubElement(clean, "testsuite", name="runtime_custody_diagnostic")
    counts = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    for case in root.findall("./testsuite/testcase"):
        key = case.get("classname"), case.get("name")
        if key not in allowed or key in seen:
            raise ValueError("diagnostic_report_case_inventory_invalid")
        seen.add(key)
        outcomes = [
            name
            for name in ("failure", "error", "skipped")
            if case.find(name) is not None
        ]
        if len(outcomes) > 1:
            raise ValueError("diagnostic_report_outcome_invalid")
        status = outcomes[0] if outcomes else "passed"
        category = None
        failed_conditions = ()
        counts["tests"] += 1
        result = ET.SubElement(suite, "testcase", classname=key[0], name=key[1])
        if outcomes:
            counts[
                {"failure": "failures", "error": "errors", "skipped": "skipped"}[status]
            ] += 1
            original = case.find(status)
            # Match only an exact fixed failure line, never save raw message text.
            text = (original.text or "").strip()
            match = re.fullmatch(
                r"(?:Failed: )?runtime_custody_diagnostic:([a-z_]+)(?:;checks=([a-z_,]+))?",
                text,
            )
            category = "unclassified"
            if match and match[1] in _CATEGORIES:
                checks = tuple(match[2].split(",")) if match[2] else ()
                if not checks or (
                    match[1] == "role_scope"
                    and len(checks) <= len(_ROLE_CONDITIONS)
                    and len(set(checks)) == len(checks)
                    and all(name in _ROLE_CONDITIONS for name in checks)
                ):
                    category = match[1]
                    failed_conditions = checks
            suffix = (
                ";checks=" + ",".join(failed_conditions) if failed_conditions else ""
            )
            ET.SubElement(
                result, status, message=f"runtime_custody_diagnostic:{category}{suffix}"
            )
        records.append(
            {
                "module": key[0],
                "case": key[1],
                "status": status,
                "category": category,
                "failed_conditions": list(failed_conditions),
            }
        )
    for key, value in counts.items():
        suite.set(key, str(value))
    complete = seen == allowed
    summary = {
        "raw_junit_sha256": hashlib.sha256(raw).hexdigest(),
        "counts": counts,
        "exact_four_cases_present": complete,
        "cases": records,
        "all_four_passed": complete
        and counts == {"tests": 4, "failures": 0, "errors": 0, "skipped": 0},
        "redacted_export_only": True,
        "full_postgresql_acceptance": False,
    }
    return summary, ET.tostring(clean, encoding="utf-8", xml_declaration=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--archive-sha256", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--tree", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if any(
        re.fullmatch(r"[a-f0-9]{40}", value) is None
        for value in (args.commit, args.tree)
    ):
        raise ValueError("exact_git_identity_required")
    archive_raw = args.archive.read_bytes()
    if hashlib.sha256(archive_raw).hexdigest() != args.archive_sha256:
        raise ValueError("archive_digest_mismatch")
    with tarfile.open(fileobj=io.BytesIO(archive_raw)) as archive:
        if (
            archive.pax_headers.get("comment") != args.commit
            or archive_tree(archive) != args.tree
        ):
            raise ValueError("archive_git_identity_mismatch")
        files = archive_files(archive)
    executed = (
        "scripts/diagnose_runtime_custody_postgres.py",
        "scripts/verify_final_hermetic.py",
        "scripts/verify_linux_shard.py",
    )
    root = Path(__file__).resolve().parents[1]
    if any(
        hashlib.sha256((root / name).read_bytes()).hexdigest() != files[name]["sha256"]
        for name in executed
    ):
        raise ValueError("executed_runner_differs_from_archive")
    if files["requirements/validation-linux-py312.lock"]["sha256"] != LOCK_SHA256:
        raise ValueError("diagnostic_dependency_identity_mismatch")
    args.output.mkdir(parents=True, exist_ok=False)
    export = args.output / "export"
    export.mkdir()
    file_map = args.output / "source-files.json"
    file_map_sha = _write(file_map, files)
    tests = args.output / "test-results"
    tests.mkdir()
    tests.chmod(0o777)
    suffix = uuid4().hex[:12]
    label = f"org.ctcc.validation.run={suffix}"
    network = f"ctcc-final-{suffix}-network"
    postgres = f"ctcc-final-{suffix}-postgres"
    image = f"ctcc-custody-diagnostic:{args.commit[:12]}-{suffix}"
    resources = []
    run = Run(args.output, budget_seconds=20 * 60, heartbeat_seconds=30)
    identity = {
        "scope": "db0046_four_case_diagnostic_only",
        "commit": args.commit,
        "tree": args.tree,
        "archive_sha256": args.archive_sha256,
        "source_files_sha256": file_map_sha,
        "manifest_sha256": files["MANIFEST.sha256"]["sha256"],
        "runner_sha256": files[executed[0]]["sha256"],
        "dependency_lock_sha256": LOCK_SHA256,
        "postgres_image": POSTGRES_IMAGE,
        "host_credentials": False,
        "host_env": False,
        "internal_runtime_network": True,
        "external_order_writes": 0,
        "execution_authority": False,
        "full_postgresql_acceptance": False,
        "status": "FAIL",
    }

    def container(name, command, *, mounts=(), timeout=300, **kwargs):
        container_name = f"ctcc-final-{suffix}-{name}"
        resources.append(("container", container_name))
        run.command(
            name,
            [
                "docker",
                "run",
                "--rm",
                "--name",
                container_name,
                "--label",
                label,
                "--network",
                network,
                *mounts,
                "-e",
                "ENVIRONMENT=test",
                "-e",
                "TRADING_MODE=analysis_only",
                "-e",
                f"DATABASE_URL=postgresql+asyncpg://ctcc@{postgres}:5432/ctcc",
                image,
                *command,
            ],
            timeout=timeout,
            **kwargs,
        )

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
            timeout=900,
        )
        run.command(
            "image-identity",
            ["docker", "image", "inspect", "--format", "{{.Id}}", image],
        )
        image_id = (args.output / "image-identity.log").read_text().strip()
        if re.fullmatch(r"sha256:[a-f0-9]{64}", image_id) is None:
            raise ValueError("diagnostic_image_identity_invalid")
        identity["image_id"] = image_id
        resources.append(("network", network))
        run.command(
            "network-create",
            ["docker", "network", "create", "--internal", "--label", label, network],
        )
        resources.append(("container", postgres))
        run.command(
            "postgres-start",
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
        )
        deadline = min(time.monotonic() + 90, run.deadline)
        while subprocess.run(
            ["docker", "exec", postgres, "pg_isready", "-U", "ctcc", "-d", "ctcc"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
        ).returncode:
            if time.monotonic() >= deadline:
                raise RuntimeError("isolated_postgres_not_ready")
            time.sleep(1)
        container("copied-manifest", ["python", "scripts/manifest.py", "--check"])
        container(
            "copied-source-exact",
            [
                "python",
                "-c",
                "from pathlib import Path; from scripts.verify_final_hermetic import verify_copied_source; import sys; print(verify_copied_source(Path('/app'),Path('/verification/source-files.json'),sys.argv[1]))",
                file_map_sha,
            ],
            mounts=(
                "--mount",
                f"type=bind,source={file_map.resolve()},target=/verification/source-files.json,readonly",
            ),
        )
        identity["copy_verified"] = True
        identity["test_report"] = run_runtime_bundle_custody_preflight(
            container,
            test_results=tests,
            mounts=(
                "--mount",
                f"type=bind,source={tests.resolve()},target=/validation-results",
            ),
        )
        identity["status"] = "PASS"
    except Exception as error:  # noqa: BLE001 - Only a type/category reaches exported evidence.
        identity["failure_type"] = type(error).__name__
    finally:
        raw_path = tests / "runtime-bundle-custody-preflight.xml"
        if raw_path.is_file() and not raw_path.is_symlink():
            try:
                summary, clean = redacted_report(raw_path.read_bytes())
                _write(export / "diagnostic-summary.json", summary)
                with (export / "diagnostic-junit.xml").open("xb") as stream:
                    stream.write(clean)
                if not summary["all_four_passed"]:
                    identity["status"] = "FAIL"
            except Exception as error:  # noqa: BLE001 - Never export malformed raw content.
                identity["status"] = "FAIL"
                identity["report_failure_type"] = type(error).__name__
        else:
            identity["status"] = "FAIL"
        cleanup = cleanup_resources(resources, suffix, budget_seconds=90)
        if any(row["status"] not in {"ABSENT", "REMOVED"} for row in cleanup):
            identity["status"] = "FAIL"
        _write(export / "cleanup.json", cleanup)
        _write(export / "steps.json", run.steps)
        _write(export / "identity.json", identity)
        hashes = {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(export.iterdir())
        }
        receipt_sha = _write(
            export / "receipt.json", {"artifacts": hashes, "status": identity["status"]}
        )
        print(
            json.dumps(
                {"diagnostic_status": identity["status"], "receipt_sha256": receipt_sha}
            ),
            flush=True,
        )
    return 0 if identity["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
