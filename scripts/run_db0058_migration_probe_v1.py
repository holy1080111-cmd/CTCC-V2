"""Bounded exact-source0058 DDL/Live DATA probe; never full acceptance."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import sys
import tarfile
from pathlib import Path
from uuid import uuid4

from scripts import current0058_migration_pin as pins
from scripts import verify_final_hermetic as shared
from scripts.devval_hermetic_tls import devval_tls
from scripts.evidence_json import read_evidence_json
from scripts.verify_current0055_hermetic import (
    _COPIED_SOURCE,
    _POSTGRES,
    _PYTEST,
    _REDIS,
    _REDIS_READY,
    _isolated_client,
    _write,
)
from scripts.verify_current0056_hermetic import _PG_TLS_READY

RUNNER = "scripts/run_db0058_migration_probe_v1.py"
TEST_MODULE = "tests/integration/test_native_live_ledger_v1_pg.py"
REQUIRED_CASES = tuple(
    ("tests.integration.test_native_live_ledger_v1_pg", name)
    for name in (
        "test_actual58_two_workers_same_event_one_durable_intent_and_independent_readback",
        "test_actual58_forged_intent_authority_rolls_back_hold_and_journal",
        "test_actual58_postcommit_readback_failure_keeps_intent_and_never_retries",
        "test_actual58_estop_or_expiry_refuses_before_creating_any_hold",
        "test_actual58_direct_sql_cannot_make_null_armed_or_unknown_caps",
        "test_actual58_downgrade_refuses_retained_control_without_deleting_custody",
        "test_actual58_effective_maintain_drift_blocks_data_hold",
        "test_actual58_negative_procedures_cannot_arm_or_clear_estop",
        "test_actual58_writer_can_observe_real_head_but_cannot_read_demo_records",
    )
)
_MAX_ARCHIVE = 128 * 1024 * 1024


def canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("ascii")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def need(value):
    if value is not True:
        raise ValueError("db0058_migration_probe_identity_refused") from None


def admit_archive(args):
    need(
        not args.archive.is_symlink()
        and args.archive.is_file()
        and 0 < args.archive.stat().st_size <= _MAX_ARCHIVE
    )
    raw = args.archive.read_bytes()
    need(sha(raw) == args.archive_sha256)
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
        need(
            archive.pax_headers.get("comment") == args.commit
            and shared.archive_tree(archive) == args.tree
        )
        files = shared.archive_files(archive)
    need(not any(Path(name).name == ".env" for name in files))
    migrations = {
        name: row["sha256"]
        for name, row in files.items()
        if name.startswith("migrations/") and name.endswith(".py")
    }
    need(
        len(migrations) == pins.MIGRATION_INVENTORY_COUNT
        and sha(canonical(migrations)) == pins.MIGRATION_INVENTORY_SHA256
        and migrations[pins.MIGRATION_PATH] == pins.MIGRATION_SHA256
    )
    parent = {
        name: digest
        for name, digest in migrations.items()
        if name != pins.MIGRATION_PATH
    }
    need(sha(canonical(parent)) == pins.PARENT_INVENTORY_SHA256)
    for name, module in tuple(sys.modules.items()):
        if name.startswith("scripts."):
            path = Path(module.__file__)
            need(
                path.suffix == ".py"
                and path.parent == Path(__file__).parent
                and not path.is_symlink()
            )
            need(sha(path.read_bytes()) == files["scripts/" + path.name]["sha256"])
    need(sha(Path(__file__).read_bytes()) == files[RUNNER]["sha256"])
    return raw, files


def run_probe(args, raw, files, root):
    results = root / "test-results"
    results.mkdir(mode=0o777)
    results.chmod(0o777)
    mapping = root / "source-files.json"
    mapping.write_bytes(canonical(files))
    mapping.chmod(0o444)
    run_id = uuid4().hex[:12]
    prefix = "ctcc-final-" + run_id + "-"
    network, postgres, redis = (
        prefix + name for name in ("network", "postgres", "redis")
    )
    label = "org.ctcc.validation.run=" + run_id
    image = "ctcc-migration58-probe:" + args.commit[:12] + "-" + run_id
    state = {
        "schema": "ctcc.db0058.migration_live_data_probe.v1",
        "source_head": "0058",
        "database_head": "0058",
        "commit": args.commit,
        "tree": args.tree,
        "archive_sha256": sha(raw),
        "source_files_sha256": sha(mapping.read_bytes()),
        "manifest_sha256": files["MANIFEST.sha256"]["sha256"],
        "migration_sha256": pins.MIGRATION_INVENTORY_SHA256,
        "run_id": run_id,
        "probe": "FAIL",
        "hermetic_regression": "NOT_RUN",
        "trading_acceptance": False,
        "host_env": False,
        "host_credentials": False,
        "external_order_writes": 0,
    }
    _write(root / "identity.json", state)
    run = shared.Run(root, budget_seconds=1800)
    resources = []

    def docker(stage, *argv, **options):
        run.command(stage, ["docker", *argv], **options)

    environment = (
        "-e",
        "ENVIRONMENT=test",
        "-e",
        "TRADING_MODE=analysis_only",
        "-e",
        f"DATABASE_URL=postgresql+asyncpg://ctcc@{postgres}:5432/ctcc",
        "-e",
        f"REDIS_URL=redis://{redis}:6379/0",
    )
    source_mount = (
        "--mount",
        f"type=bind,src={mapping},dst=/ctcc-source-files.json,readonly",
    )

    def container(stage, *argv, mounts=(), extra_environment=(), timeout=300):
        name = prefix + stage
        resources.append(("container", name))
        docker(
            stage,
            "run",
            "--rm",
            "--name",
            name,
            "--label",
            label,
            "--network",
            network,
            *environment,
            *mounts,
            *extra_environment,
            image,
            *argv,
            timeout=timeout,
        )

    def parent(stage):
        docker(
            stage,
            "exec",
            postgres,
            "psql",
            "-XAt",
            "-U",
            "ctcc",
            "-d",
            "ctcc",
            "-v",
            "ON_ERROR_STOP=1",
            "-c",
            "SELECT version_num FROM public.alembic_version",
        )
        need((root / (stage + ".log")).read_bytes().strip() == b"0057")

    def identity(stage):
        container(stage, "python", "-m", "scripts.verify_migration_identity")
        value = read_evidence_json((root / (stage + ".log")).read_bytes())
        need(
            value
            == {
                "schema": "ctcc.migration_identity.v1",
                "status": "PASS",
                "source_head": "0058",
                "database_head": "0058",
                "migration_sha256": pins.MIGRATION_INVENTORY_SHA256,
                "execution_authority": False,
            }
        )

    try:
        with devval_tls(True, postgres, disable_autovacuum=True) as tls:
            docker(
                "build",
                "build",
                "--no-cache",
                "--build-arg",
                "CTCC_SOURCE_COMMIT=" + args.commit,
                "--build-arg",
                "CTCC_SOURCE_TREE=" + args.tree,
                "-t",
                image,
                "-",
                input_bytes=raw,
            )
            docker(
                "image-identity", "image", "inspect", "--format", "{{json .}}", image
            )
            state["image_id"] = read_evidence_json(
                (root / "image-identity.log").read_bytes()
            )["Id"]
            resources.append(("network", network))
            docker(
                "network-create",
                "network",
                "create",
                "--internal",
                "--label",
                label,
                network,
            )
            docker(
                "network-inspect",
                "network",
                "inspect",
                "--format",
                "{{json .}}",
                network,
            )
            need(
                read_evidence_json((root / "network-inspect.log").read_bytes())[
                    "Internal"
                ]
                is True
            )
            resources.append(("container", postgres))
            docker(
                "postgres-start",
                "run",
                "-d",
                "--name",
                postgres,
                "--label",
                label,
                "--network",
                network,
                "--network-alias",
                tls.wrong_hostname_alias,
                "-e",
                "POSTGRES_USER=ctcc",
                "-e",
                "POSTGRES_DB=ctcc",
                "-e",
                "POSTGRES_HOST_AUTH_METHOD=trust",
                *tls.postgres_mount,
                _POSTGRES,
                *tls.postgres_command,
            )
            resources.append(("container", redis))
            docker(
                "redis-start",
                "run",
                "-d",
                "--name",
                redis,
                "--label",
                label,
                "--network",
                network,
                _REDIS,
            )
            docker(
                "postgres-ready",
                "exec",
                postgres,
                "sh",
                "-ec",
                _PG_TLS_READY,
                timeout=120,
            )
            docker("redis-ready", "exec", redis, "sh", "-ec", _REDIS_READY, timeout=90)
            container("copied-manifest", "python", "scripts/manifest.py", "--check")
            container(
                "copied-source-exact",
                "python",
                "-c",
                _COPIED_SOURCE,
                state["source_files_sha256"],
                mounts=source_mount,
            )
            container(
                "dependency-lock",
                "python",
                "-m",
                "scripts.verify_dependency_lock",
                "--target",
                "linux",
            )
            container("migration-upgrade-parent", "alembic", "upgrade", "0057")
            parent("migration-parent-readback")
            container("migration-upgrade", "alembic", "upgrade", "head")
            identity("migration-identity")
            container("migration-drift", "alembic", "check")
            container("migration-empty-downgrade", "alembic", "downgrade", "0057")
            parent("migration-empty-downgrade-readback")
            container("migration-empty-reupgrade", "alembic", "upgrade", "head")
            identity("migration-reupgrade-identity")
            container(
                "live-ledger-data-tests",
                *_PYTEST,
                "-q",
                "--junitxml=/validation-results/live-ledger-data.xml",
                TEST_MODULE,
                mounts=(
                    "--mount",
                    f"type=bind,src={results},dst=/validation-results",
                    *tls.pytest_mount,
                ),
                extra_environment=tls.pytest_environment,
                timeout=900,
            )
            report = shared.verify_pytest_report(
                results / "live-ledger-data.xml",
                required_cases=REQUIRED_CASES,
                allow_unrelated_skips=False,
            )
            need(
                report["tests"] == 17
                and report["passed"] == report["tests"]
                and report["skipped"] == 0
            )
            state["tests"] = report
            state["probe"] = "PASS"
    except Exception as error:
        state["failure_type"] = type(error).__name__
        raise
    finally:
        cleanup = shared.cleanup_resources(resources, run_id)
        _write(root / "cleanup.json", cleanup)
        state["cleanup"] = (
            "PASS" if all(row["status"] != "FAIL" for row in cleanup) else "FAIL"
        )
        if state["cleanup"] != "PASS":
            state["probe"] = "FAIL"
        _write(root / "identity.json", state)
        hashes = {
            path.relative_to(root).as_posix(): sha(path.read_bytes())
            for path in root.rglob("*")
            if path.is_file()
            and not path.is_symlink()
            and "docker-client" not in path.parts
            and path.name != "results.sha256.json"
        }
        _write(root / "results.sha256.json", hashes)
    need(state["probe"] == state["cleanup"] == "PASS")
    print("DB0058_MIGRATION_LIVE_DATA_PROBE=PASS", flush=True)
    print("HERMETIC_REGRESSION=NOT_RUN", flush=True)
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    for name in ("archive", "archive-sha256", "commit", "tree", "output"):
        parser.add_argument(
            "--" + name,
            required=True,
            type=Path if name in ("archive", "output") else str,
        )
    args = parser.parse_args()
    need(
        all(
            re.fullmatch(pattern, value) is not None
            for pattern, value in (
                (r"[a-f0-9]{40}", args.commit),
                (r"[a-f0-9]{40}", args.tree),
                (r"[a-f0-9]{64}", args.archive_sha256),
            )
        )
    )
    raw, files = admit_archive(args)
    args.output.mkdir(parents=True, exist_ok=False)
    root = args.output.resolve(strict=True)
    with _isolated_client(root):
        return run_probe(args, raw, files, root)


if __name__ == "__main__":
    raise SystemExit(main())
