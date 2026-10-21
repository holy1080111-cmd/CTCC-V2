"""Bounded, exact-source PostgreSQL diagnostics; never full validation or trading.

The default runs the four existing DB0046 custody preflight cases. The explicit
DB0047 identity profile migrates head and runs the reviewed witness module.
The separate migration profile also checks the empty roundtrip and a nonempty
downgrade refusal using the full runner's fixed probes and strict validators.
PostgreSQL and the app share an internal network, without host credentials.
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
    MIGRATION_REVISION_PROBE,
    NATIVE_IDENTITY_DOWNGRADE_DENIAL,
    NATIVE_IDENTITY_RETENTION_PROBE,
    RUNTIME_CUSTODY_PREFLIGHT_CASES,
    Run,
    archive_files,
    archive_tree,
    cleanup_resources,
    expected_migration_revision_readback,
    expected_native_identity_empty_downgrade_readback,
    native_identity_empty_before_readback,
    native_identity_nonempty_downgrade_readback,
    native_migration_json_equal,
    run_runtime_bundle_custody_preflight,
    verify_pytest_report,
)

POSTGRES_IMAGE = "postgres:17-alpine@sha256:f02121de6f74d30d8a94cd1d9584125e2178d7e6c377d8130112d4e52d867995"
LOCK_SHA256 = "a44e649cb1d21d3790e132c21699cc172e77ed87682929320cf8f6190fe014e4"
DEFAULT_PROFILE = "runtime-custody-0046"
NATIVE_WITNESS_PROFILE = "native-witness-identity-0047"
NATIVE_WITNESS_MIGRATION_PROFILE = "native-witness-migration-0047"
NATIVE_WITNESS_PROFILES = (NATIVE_WITNESS_PROFILE, NATIVE_WITNESS_MIGRATION_PROFILE)
NATIVE_WITNESS_MODULE = "tests.integration.test_public_receipt_witness_repository"
NATIVE_WITNESS_CASES = tuple(
    (NATIVE_WITNESS_MODULE, name)
    for name in (
        "test_restricted_function_only_role_and_monotonic_cas",
        "test_restricted_role_rejects_direct_column_and_table_grants",
        "test_restricted_role_rejects_other_role_membership",
        "test_native_identity_round_trips_without_signed_conversion[unsigned-windows-identity-width]",
        "test_native_identity_round_trips_without_signed_conversion[full-native-identity-width]",
        "test_native_identity_round_trips_without_signed_conversion[zero-device-and-wide-file-id]",
        "test_restricted_function_rejects_identity_token_coercion[fraction-must-not-round]",
        "test_restricted_function_rejects_identity_token_coercion[decimal-must-not-coerce]",
        "test_restricted_function_rejects_identity_token_coercion[boolean-must-not-coerce]",
        "test_restricted_function_rejects_identity_token_coercion[string-must-not-coerce]",
        "test_restricted_function_rejects_identity_token_coercion[volume-overflow]",
        "test_restricted_function_rejects_identity_token_coercion[file-id-fraction-must-not-round]",
        "test_restricted_function_rejects_identity_token_coercion[file-id-zero]",
        "test_restricted_function_rejects_identity_token_coercion[file-id-overflow]",
        "test_restricted_function_rejects_identity_token_coercion[checkpoint-exponent-lexeme]",
        "test_restricted_function_rejects_identity_token_coercion[checkpoint-decimal-lexeme]",
        "test_restricted_function_rejects_identity_token_coercion[checkpoint-string-identity]",
    )
)
NATIVE_WITNESS_REPORT = "native-witness-identity-diagnostic.xml"
_NATIVE_CATEGORIES = {
    "failure": "witness_test_failed",
    "error": "witness_fixture_failed",
    "skipped": "witness_test_skipped",
}
NATIVE_WITNESS_CATALOG_SQL = """
SELECT json_build_object(
  'constraint_definition', (SELECT pg_catalog.pg_get_constraintdef(oid,true)
    FROM pg_catalog.pg_constraint
    WHERE conrelid='public.public_receipt_witness_revisions'::pg_catalog.regclass
      AND conname='ck_public_receipt_witness_revisions_native_identity_bounds'),
  'columns', (SELECT json_agg(json_build_object(
    'name',attname,'type',pg_catalog.format_type(atttypid,atttypmod),
    'typmod',atttypmod) ORDER BY attname)
    FROM pg_catalog.pg_attribute
    WHERE attrelid='public.public_receipt_witness_revisions'::pg_catalog.regclass
      AND attname IN ('root_device','root_inode') AND NOT attisdropped),
  'functions', json_build_array(
    json_build_object('name','public_receipt_witness_append(jsonb)',
      'prosrc_sha256',(SELECT encode(pg_catalog.sha256(
        pg_catalog.convert_to(prosrc,'UTF8')),'hex') FROM pg_catalog.pg_proc
        WHERE oid='public.public_receipt_witness_append(jsonb)'::pg_catalog.regprocedure)),
    json_build_object('name','public_receipt_witness_insert_guard()',
      'prosrc_sha256',(SELECT encode(pg_catalog.sha256(
        pg_catalog.convert_to(prosrc,'UTF8')),'hex') FROM pg_catalog.pg_proc
        WHERE oid='public.public_receipt_witness_insert_guard()'::pg_catalog.regprocedure))))
"""
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


def required_cases(profile):
    if profile == DEFAULT_PROFILE:
        return RUNTIME_CUSTODY_PREFLIGHT_CASES
    if profile in NATIVE_WITNESS_PROFILES:
        return NATIVE_WITNESS_CASES
    raise ValueError("diagnostic_profile_invalid")


def redacted_report(raw, *, profile=DEFAULT_PROFILE):
    """Export counts and exact allowed cases, never original XML attributes/text."""
    if not 0 < len(raw) <= 1024 * 1024:
        raise ValueError("diagnostic_report_size_invalid")
    root = ET.fromstring(raw)
    if root.tag != "testsuites":
        raise ValueError("diagnostic_report_schema_invalid")
    if profile in NATIVE_WITNESS_PROFILES:
        if not len(root) or any(suite.tag != "testsuite" for suite in root):
            raise ValueError("diagnostic_report_schema_invalid")
        for source_suite in root:
            if any(
                item.tag not in {"testcase", "properties", "system-out", "system-err"}
                for item in source_suite
            ):
                raise ValueError("diagnostic_report_schema_invalid")
            actual = {"tests": len(source_suite.findall("testcase"))}
            actual.update(
                (name, len(source_suite.findall(f"testcase/{tag}")))
                for name, tag in (
                    ("failures", "failure"),
                    ("errors", "error"),
                    ("skipped", "skipped"),
                )
            )
            if any(
                source_suite.get(name) != str(count) for name, count in actual.items()
            ):
                raise ValueError("diagnostic_report_counts_conflict")
    allowed = set(required_cases(profile))
    seen = set()
    records = []
    clean = ET.Element("testsuites")
    suite = ET.SubElement(
        clean,
        "testsuite",
        name=(
            "runtime_custody_diagnostic"
            if profile == DEFAULT_PROFILE
            else "native_witness_identity_diagnostic"
        ),
    )
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
            if profile == DEFAULT_PROFILE and match and match[1] in _CATEGORIES:
                checks = tuple(match[2].split(",")) if match[2] else ()
                if not checks or (
                    match[1] == "role_scope"
                    and len(checks) <= len(_ROLE_CONDITIONS)
                    and len(set(checks)) == len(checks)
                    and all(name in _ROLE_CONDITIONS for name in checks)
                ):
                    category = match[1]
                    failed_conditions = checks
            if profile in NATIVE_WITNESS_PROFILES:
                category = _NATIVE_CATEGORIES[status]
            suffix = (
                ";checks=" + ",".join(failed_conditions) if failed_conditions else ""
            )
            prefix = (
                "runtime_custody_diagnostic"
                if profile == DEFAULT_PROFILE
                else "native_witness_identity_diagnostic"
            )
            ET.SubElement(result, status, message=f"{prefix}:{category}{suffix}")
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
        "cases": records,
        "redacted_export_only": True,
        "full_postgresql_acceptance": False,
    }
    passed = complete and counts == {
        "tests": len(allowed),
        "failures": 0,
        "errors": 0,
        "skipped": 0,
    }
    if profile == DEFAULT_PROFILE:
        summary.update(exact_four_cases_present=complete, all_four_passed=passed)
    else:
        summary.update(
            profile=profile,
            expected_case_count=len(allowed),
            exact_required_cases_present=complete,
            all_required_passed=passed,
            category_counts={
                value: sum(item["category"] == value for item in records)
                for value in _NATIVE_CATEGORIES.values()
            },
        )
    return summary, ET.tostring(clean, encoding="utf-8", xml_declaration=True)


def run_native_witness_identity_diagnostic(container, *, test_results: Path, mounts):
    """Run the entire source module; all seventeen exact cases must pass once."""
    container(
        "native-witness-identity-tests",
        [
            "python",
            "-m",
            "scripts.hermetic_pytest",
            "-p",
            "no:cacheprovider",
            "--tb=no",
            "--show-capture=no",
            "-o",
            "junit_logging=no",
            f"--junitxml=/validation-results/{NATIVE_WITNESS_REPORT}",
            NATIVE_WITNESS_MODULE.replace(".", "/") + ".py",
        ],
        mounts=mounts,
        timeout=5 * 60,
    )
    path = test_results / NATIVE_WITNESS_REPORT
    report = verify_pytest_report(
        path,
        required_modules=(NATIVE_WITNESS_MODULE,),
        required_cases=NATIVE_WITNESS_CASES,
    )
    summary, _ = redacted_report(path.read_bytes(), profile=NATIVE_WITNESS_PROFILE)
    if not summary["all_required_passed"]:
        raise ValueError("diagnostic_required_execution_incomplete")
    return report


def _migration_object(path: Path) -> tuple[dict, str]:
    if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= 65536:
        raise ValueError("diagnostic_migration_observation_unsafe")

    def unique(pairs):
        record = {}
        for name, value in pairs:
            if name in record:
                raise ValueError("diagnostic_migration_duplicate_key")
            record[name] = value
        return record

    raw = path.read_bytes()
    record = json.loads(raw, object_pairs_hook=unique)
    if type(record) is not dict:
        raise ValueError("diagnostic_migration_observation_invalid")
    return record, hashlib.sha256(raw).hexdigest()


def run_native_witness_migration_diagnostic(
    container, *, output: Path, test_results: Path, mounts, progress: dict
):
    """One empty roundtrip, seventeen cases, then one nonempty refusal.

    Only fixed, validated observations enter progress. Raw logs remain private
    runner-temp artifacts even when a SQL/driver failure interrupts the probe.
    """

    def observe(phase, command):
        progress["phase"] = phase
        container(phase, command, timeout=60)
        return _migration_object(output / f"{phase}.log")

    def retain(phase, record, digest):
        progress["observations"][phase] = record
        progress["observation_sha256"][phase] = digest

    before, digest = observe(
        "native-witness-migration-before",
        [
            "python",
            "-c",
            MIGRATION_REVISION_PROBE,
            "0047",
            "native-identity-0047-empty-before",
        ],
    )
    expected = native_identity_empty_before_readback(
        before.get("revision_readback"), before.get("object_custody_sha256")
    )
    if not native_migration_json_equal(before, expected):
        raise ValueError("diagnostic_migration_empty_before_invalid")
    retain(progress["phase"], expected, digest)
    progress["phase"] = "native-witness-empty-downgrade"
    container(progress["phase"], ["alembic", "downgrade", "0046"], timeout=60)
    down, digest = observe(
        "native-witness-empty-readback",
        [
            "python",
            "-c",
            MIGRATION_REVISION_PROBE,
            "0046",
            "native-identity-0047-empty-down",
        ],
    )
    expected = expected_native_identity_empty_downgrade_readback(
        before["object_custody_sha256"]
    )
    if not native_migration_json_equal(down, expected):
        raise ValueError("diagnostic_migration_empty_down_invalid")
    retain(progress["phase"], expected, digest)
    progress["phase"] = "native-witness-reupgrade"
    container(progress["phase"], ["alembic", "upgrade", "head"], timeout=60)
    current, digest = observe(
        "native-witness-reupgrade-readback",
        ["python", "-c", MIGRATION_REVISION_PROBE, "0047"],
    )
    expected = expected_migration_revision_readback("0047")
    if not native_migration_json_equal(current, expected):
        raise ValueError("diagnostic_migration_reupgrade_invalid")
    retain(progress["phase"], expected, digest)
    progress["phase"] = "native-witness-reupgrade-drift"
    container(progress["phase"], ["alembic", "check"], timeout=60)
    progress["phase"] = "native-witness-identity-tests"
    report = run_native_witness_identity_diagnostic(
        container, test_results=test_results, mounts=mounts
    )
    progress["phase"] = "native-witness-nonempty-before"
    nonempty_before, digest = observe(
        progress["phase"], ["python", "-c", NATIVE_IDENTITY_RETENTION_PROBE]
    )
    # This validates the exact snapshot contract and both small/wide counts.
    native_identity_nonempty_downgrade_readback(nonempty_before, nonempty_before)
    retain(progress["phase"], nonempty_before, digest)
    progress["phase"] = "native-witness-nonempty-denial"
    container(
        progress["phase"],
        ["alembic", "downgrade", "0046"],
        timeout=60,
        expected_refusal=NATIVE_IDENTITY_DOWNGRADE_DENIAL,
    )
    nonempty_after, digest = observe(
        "native-witness-nonempty-after",
        ["python", "-c", NATIVE_IDENTITY_RETENTION_PROBE],
    )
    policy = native_identity_nonempty_downgrade_readback(
        nonempty_before, nonempty_after
    )
    retain(progress["phase"], nonempty_after, digest)
    progress["nonempty_downgrade_policy"] = policy
    progress["required_case_count"] = len(NATIVE_WITNESS_CASES)
    progress["required_junit_sha256"] = report["sha256"]
    progress["phase"] = "COMPLETE"
    progress["status"] = "PASS"
    return report


def native_witness_catalog(raw):
    """Allow fixed public DDL metadata, never rows, roles or driver text.

    Pretty=true matches the formal DB0047 catalog query. Preserve its exact
    definition/whitespace; this observation never seals a baseline by itself.
    """
    if not 0 < len(raw) <= 8192:
        raise ValueError("diagnostic_catalog_size_invalid")

    def unique_object(pairs):
        record = {}
        for name, value in pairs:
            if name in record:
                raise ValueError("diagnostic_catalog_duplicate_key")
            record[name] = value
        return record

    record = json.loads(raw, object_pairs_hook=unique_object)
    if type(record) is not dict or set(record) != {
        "constraint_definition",
        "columns",
        "functions",
    }:
        raise ValueError("diagnostic_catalog_schema_invalid")
    definition = record["constraint_definition"]
    if (
        type(definition) is not str
        or not 0 < len(definition) <= 2048
        or re.fullmatch(r"[A-Za-z_0-9 \t\r\n():<>=.'+\-]+", definition) is None
        or set(re.findall(r"[A-Za-z_][A-Za-z_0-9]*", definition))
        - {
            "CHECK",
            "AND",
            "BETWEEN",
            "root_device",
            "root_inode",
            "numeric",
            "trunc",
            "pg_catalog",
        }
        or set(re.findall(r"[0-9]+", definition))
        - {
            "0",
            "1",
            "18446744073709551615",
            "340282366920938463463374607431768211455",
        }
        or not all(name in definition for name in ("root_device", "root_inode"))
    ):
        raise ValueError("diagnostic_catalog_constraint_invalid")
    columns = record["columns"]
    if columns != [
        {"name": "root_device", "type": "numeric", "typmod": -1},
        {"name": "root_inode", "type": "numeric", "typmod": -1},
    ] or any(type(row["typmod"]) is not int for row in columns):
        raise ValueError("diagnostic_catalog_columns_invalid")
    functions = record["functions"]
    expected = (
        "public_receipt_witness_append(jsonb)",
        "public_receipt_witness_insert_guard()",
    )
    if type(functions) is not list or len(functions) != 2:
        raise ValueError("diagnostic_catalog_functions_invalid")
    for row, name in zip(functions, expected, strict=True):
        if (
            type(row) is not dict
            or set(row) != {"name", "prosrc_sha256"}
            or row["name"] != name
            or type(row["prosrc_sha256"]) is not str
            or re.fullmatch(r"[a-f0-9]{64}", row["prosrc_sha256"]) is None
        ):
            raise ValueError("diagnostic_catalog_functions_invalid")
    return record


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--profile",
        choices=(DEFAULT_PROFILE, *NATIVE_WITNESS_PROFILES),
        default=DEFAULT_PROFILE,
    )
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
        *(
            ("scripts/verify_native_witness_identity_schema.py",)
            if args.profile == NATIVE_WITNESS_MIGRATION_PROFILE
            else ()
        ),
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
        "scope": (
            "db0046_four_case_diagnostic_only"
            if args.profile == DEFAULT_PROFILE
            else "db0047_seventeen_case_migration_diagnostic_only"
            if args.profile == NATIVE_WITNESS_MIGRATION_PROFILE
            else "db0047_seventeen_case_native_identity_diagnostic_only"
        ),
        "profile": args.profile,
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
    migration_progress = {
        "schema": "ctcc.db0047_native_migration_diagnostic.v1",
        "profile": NATIVE_WITNESS_MIGRATION_PROFILE,
        "status": "FAIL",
        "phase": "NOT_STARTED",
        "observations": {},
        "observation_sha256": {},
        "source": {
            key: identity[key]
            for key in (
                "commit",
                "tree",
                "archive_sha256",
                "source_files_sha256",
                "manifest_sha256",
                "runner_sha256",
                "dependency_lock_sha256",
            )
        },
        "executed_source_sha256": {name: files[name]["sha256"] for name in executed},
        "full_postgresql_acceptance": False,
        "execution_authority": False,
        "external_order_writes": 0,
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
        mounts = (
            "--mount",
            f"type=bind,source={tests.resolve()},target=/validation-results",
        )
        if args.profile in NATIVE_WITNESS_PROFILES:
            container("migration-upgrade", ["alembic", "upgrade", "head"])
            container(
                "migration-identity",
                ["python", "-m", "scripts.verify_migration_identity"],
            )
            migration = json.loads(
                (args.output / "migration-identity.log").read_bytes()
            )
            if (
                type(migration) is not dict
                or migration.get("status") != "PASS"
                or migration.get("source_head") != "0047"
                or migration.get("database_head") != "0047"
                or migration.get("execution_authority") is not False
                or type(migration.get("migration_sha256")) is not str
                or re.fullmatch(r"[a-f0-9]{64}", migration["migration_sha256"]) is None
            ):
                raise ValueError("diagnostic_migration_identity_invalid")
            # Export only this fixed subset, not arbitrary fields from stdout.
            identity["migration"] = {
                key: migration[key]
                for key in ("source_head", "database_head", "migration_sha256")
            }
            if args.profile == NATIVE_WITNESS_MIGRATION_PROFILE:
                identity["test_report"] = run_native_witness_migration_diagnostic(
                    container,
                    output=args.output,
                    test_results=tests,
                    mounts=mounts,
                    progress=migration_progress,
                )
            else:
                identity["test_report"] = run_native_witness_identity_diagnostic(
                    container, test_results=tests, mounts=mounts
                )
        else:
            identity["test_report"] = run_runtime_bundle_custody_preflight(
                container, test_results=tests, mounts=mounts
            )
        identity["status"] = "PASS"
    except Exception as error:  # noqa: BLE001 - Only a type/category reaches exported evidence.
        identity["failure_type"] = type(error).__name__
    finally:
        raw_path = tests / (
            "runtime-bundle-custody-preflight.xml"
            if args.profile == DEFAULT_PROFILE
            else NATIVE_WITNESS_REPORT
        )
        if raw_path.is_file() and not raw_path.is_symlink():
            try:
                summary, clean = redacted_report(
                    raw_path.read_bytes(), profile=args.profile
                )
                _write(export / "diagnostic-summary.json", summary)
                with (export / "diagnostic-junit.xml").open("xb") as stream:
                    stream.write(clean)
                if not summary[
                    "all_four_passed"
                    if args.profile == DEFAULT_PROFILE
                    else "all_required_passed"
                ]:
                    identity["status"] = "FAIL"
            except Exception as error:  # noqa: BLE001 - Never export malformed raw content.
                identity["status"] = "FAIL"
                identity["report_failure_type"] = type(error).__name__
        else:
            identity["status"] = "FAIL"
        if args.profile in NATIVE_WITNESS_PROFILES and "migration" in identity:
            try:
                run.command(
                    "native-witness-public-catalog",
                    [
                        "docker",
                        "exec",
                        postgres,
                        "psql",
                        "--no-psqlrc",
                        "--no-align",
                        "--tuples-only",
                        "--set=ON_ERROR_STOP=1",
                        "-U",
                        "ctcc",
                        "-d",
                        "ctcc",
                        "-c",
                        NATIVE_WITNESS_CATALOG_SQL,
                    ],
                    timeout=30,
                )
                catalog = native_witness_catalog(
                    (args.output / "native-witness-public-catalog.log").read_bytes()
                )
                identity["public_catalog_sha256"] = _write(
                    export / "native-witness-public-catalog.json", catalog
                )
                identity["catalog_is_observation_not_admission"] = True
            except Exception as error:  # noqa: BLE001 - Native errors never leave runner temp.
                identity["status"] = "FAIL"
                identity["catalog_failure_type"] = type(error).__name__
        cleanup = cleanup_resources(resources, suffix, budget_seconds=90)
        if any(row["status"] not in {"ABSENT", "REMOVED"} for row in cleanup):
            identity["status"] = "FAIL"
        _write(export / "cleanup.json", cleanup)
        if args.profile == NATIVE_WITNESS_MIGRATION_PROFILE:
            if identity["status"] != "PASS":
                migration_progress["status"] = "FAIL"
            if len(json.dumps(migration_progress, sort_keys=True).encode()) > 65536:
                raise ValueError("diagnostic_migration_receipt_oversized")
            identity["migration_diagnostic_receipt_sha256"] = _write(
                export / "native-witness-migration-receipt.json", migration_progress
            )
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
