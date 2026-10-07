"""Validate a Git archive in new, credential-free, isolated Docker services.

Never reads a host .env or uses the deployment's Compose project or volumes.
The archive's actual Git tree is reconstructed before building. Results identify
this run only; a green result is not trading, OOS or production acceptance.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import stat
import subprocess
import tarfile
import threading
import time
from pathlib import Path, PurePosixPath
from uuid import uuid4

from scripts.verify_linux_shard import POSTGRES_TEST_PATHS, SHARD_COUNT, selected_for

# The two full PostgreSQL-bearing test runs can exceed 90 minutes together.
# Leave time for bounded cleanup and `if: always()` evidence upload before the
# 180-minute GitHub job cutoff; a budget failure is never a validation pass.
VALIDATION_BUDGET_SECONDS = 165 * 60
CLEANUP_BUDGET_SECONDS = 5 * 60
HEARTBEAT_INTERVAL_SECONDS = 60
POSTGRES_TEST_TIMEOUT_SECONDS = 75 * 60
FULL_TEST_TIMEOUT_SECONDS = 100 * 60

ARCHIVE_REQUIRED_CASES = (
    (
        "tests.integration.test_account_bill_archive_claim_repository",
        "test_claim_is_one_across_sessions_and_survives_repository_restart",
    ),
    (
        "tests.integration.test_account_bill_archive_claim_repository",
        "test_future_host_clock_denies_readback_but_preserves_one_attempt_claim",
    ),
    (
        "tests.integration.test_durable_migration_downgrade",
        "test_nonempty_downgrade_retains_exact_durable_records[0022-archive_claim]",
    ),
    (
        "tests.integration.test_durable_migration_downgrade",
        "test_uid_event_upgrade_refuses_legacy_cross_currency_collision[0022]",
    ),
    (
        "tests.integration.test_durable_migration_downgrade",
        "test_uid_event_upgrade_downgrade_and_truncate_guards_are_atomic[0022]",
    ),
)


def git_object(kind: str, raw: bytes) -> bytes:
    return hashlib.sha1(f"{kind} {len(raw)}\0".encode() + raw).digest()


def archive_tree(archive: tarfile.TarFile) -> str:
    """Hash exact archived bytes/modes, not normalized manifest representations."""
    root: dict = {}
    for item in archive.getmembers():
        path = PurePosixPath(item.name)
        if path.is_absolute() or ".." in path.parts or not path.parts:
            raise ValueError("unsafe_archive_path")
        if item.isdir():
            continue
        if (
            not item.isfile()
            or path.name.casefold().startswith(".env")
            and path.name != ".env.example"
            or path.suffix.casefold() == ".token"
        ):
            raise ValueError("archive_non_source_entry")
        if any(
            part.casefold() in {".git", "reports", "backups", ".venv", "private-notion"}
            for part in path.parts
        ):
            raise ValueError("archive_runtime_or_git_entry")
        node = root
        for part in path.parts[:-1]:
            node = node.setdefault(part, {})
            if not isinstance(node, dict):
                raise TypeError("archive_path_conflict")
        if path.name in node:
            raise ValueError("archive_duplicate_entry")
        stream = archive.extractfile(item)
        assert stream is not None
        raw = stream.read()
        node[path.name] = (
            b"100755" if item.mode & 0o111 else b"100644",
            git_object("blob", raw),
        )

    def tree(node):
        records = []
        for name, value in node.items():
            directory = isinstance(value, dict)
            mode, digest = (b"40000", tree(value)) if directory else value
            records.append(
                (
                    name.encode() + (b"/" if directory else b""),
                    mode + b" " + name.encode() + b"\0" + digest,
                )
            )
        return git_object("tree", b"".join(raw for _, raw in sorted(records)))

    return tree(root).hex()


def archive_files(archive: tarfile.TarFile) -> dict:
    """Independent expected COPY bytes; called only after archive admission."""
    archive_tree(archive)
    files = {}
    for item in archive.getmembers():
        if item.isfile():
            stream = archive.extractfile(item)
            assert stream is not None
            files[item.name] = {
                "sha256": hashlib.sha256(stream.read()).hexdigest(),
                "executable": bool(item.mode & 0o111),
            }
    return files


def verify_copied_source(root: Path, manifest: Path, expected_sha256: str) -> int:
    """Check exact archived files inside the image, including executable bits."""
    raw = manifest.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError("source_file_map_digest_mismatch")
    files = json.loads(raw)
    for name, expected in files.items():
        relative = PurePosixPath(name)
        if relative.is_absolute() or ".." in relative.parts or not relative.parts:
            raise ValueError("source_file_map_unsafe_path")
        path = root.joinpath(*relative.parts)
        if any(parent.is_symlink() for parent in (path, *path.parents)):
            raise ValueError(f"copied_source_symlink:{name}")
        mode = path.stat().st_mode
        if (
            not stat.S_ISREG(mode)
            or hashlib.sha256(path.read_bytes()).hexdigest() != expected["sha256"]
            or bool(mode & 0o111) != expected["executable"]
        ):
            raise ValueError(f"copied_source_mismatch:{name}")
    return len(files)


def verify_pytest_report(
    path: Path, *, required_modules=(), required_cases=(), allow_unrelated_skips=False
):
    """Verify execution, with explicit full-suite platform-skip handling.

    Required modules must have passed cases and no skipped cases. A targeted
    PostgreSQL report still rejects every skip by default. Full-suite callers
    may retain skips outside those modules without claiming they passed.
    """
    import xml.etree.ElementTree as ET

    if path.is_symlink() or not 0 < path.stat().st_size <= 64 * 1024 * 1024:
        raise ValueError("pytest_report_missing_or_unsafe")
    raw = path.read_bytes()
    root = ET.fromstring(raw)
    if root.tag != "testsuites" or not len(root):
        raise ValueError("pytest_report_schema_invalid")
    total = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    required = set(required_modules)
    required_counts = {module: 0 for module in sorted(required)}
    case_counts = {tuple(case): 0 for case in required_cases}
    required_skipped = False
    for suite in root:
        if suite.tag != "testsuite":
            raise ValueError("pytest_report_schema_invalid")
        cases = suite.findall("testcase")
        counts = {"tests": len(cases)}
        for key, child in (
            ("failures", "failure"),
            ("errors", "error"),
            ("skipped", "skipped"),
        ):
            counts[key] = sum(case.find(child) is not None for case in cases)
        if any(str(value) != suite.get(key) for key, value in counts.items()):
            raise ValueError("pytest_report_counts_conflict")
        for key, value in counts.items():
            total[key] += value
        for case in cases:
            module = case.get("classname")
            exact_case = (module, case.get("name"))
            matched_modules = (
                {
                    target
                    for target in required
                    if module == target or module.startswith(target + ".")
                }
                if module
                else set()
            )
            if matched_modules and case.find("skipped") is not None:
                required_skipped = True
            if all(case.find(tag) is None for tag in ("failure", "error", "skipped")):
                for target in matched_modules:
                    required_counts[target] += 1
                if exact_case in case_counts:
                    case_counts[exact_case] += 1
    passed = total["tests"] - total["failures"] - total["errors"] - total["skipped"]
    if (
        passed <= 0
        or total["failures"]
        or total["errors"]
        or required_skipped
        or (required and total["skipped"] and not allow_unrelated_skips)
        or any(count <= 0 for count in required_counts.values())
        or any(count != 1 for count in case_counts.values())
    ):
        raise ValueError("pytest_required_execution_not_verified")
    return {
        **total,
        "passed": passed,
        "required_module_passed": required_counts,
        "required_case_passed": {
            f"{module}::{name}": count
            for (module, name), count in sorted(case_counts.items())
        },
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def verify_linux_collection(path: Path, *, mode: str, index: int | None, report: dict):
    """Read back an executed shard against its complete collected test list."""
    if path.is_symlink() or not 0 < path.stat().st_size <= 16 * 1024 * 1024:
        raise ValueError("linux_collection_missing_or_unsafe")
    raw = path.read_bytes()
    record = json.loads(raw)
    full = record.get("full_nodeids")
    selected = record.get("selected_nodeids")
    observed = record.get("observed_nodeids")
    if (
        record.get("schema") != "ctcc.linux_full_collection_shard.v1"
        or record.get("mode") != mode
        or record.get("shard_index") != index
        or record.get("shard_count") != SHARD_COUNT
        or record.get("pytest_exitstatus") != 0
        or not isinstance(full, list)
        or not full
        or any(type(node) is not str for node in full)
        or len(full) != len(set(full))
        or not isinstance(selected, list)
        or not selected
        or selected != [node for node in full if selected_for(mode, index, node)]
        or observed != selected
        or report["tests"] != len(selected)
    ):
        raise ValueError("linux_collection_execution_incomplete")
    return {
        "full_count": len(full),
        "selected_count": len(selected),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "full_sha256": hashlib.sha256(
            json.dumps(full, separators=(",", ":")).encode()
        ).hexdigest(),
    }


class Run:
    def __init__(
        self,
        output: Path,
        *,
        budget_seconds: float = VALIDATION_BUDGET_SECONDS,
        heartbeat_seconds: float = HEARTBEAT_INTERVAL_SECONDS,
        clock=time.monotonic,
    ):
        if budget_seconds <= 0 or heartbeat_seconds <= 0:
            raise ValueError("validation_timing_budget_invalid")
        self.output = output
        self.steps = []
        self.clock = clock
        self.deadline = clock() + budget_seconds
        self.heartbeat_seconds = heartbeat_seconds

    def command(self, name, args, *, timeout=3600, input_bytes=None):
        start = self.clock()
        record = {"name": name, "exit_code": None}
        print(f"HERMETIC_STAGE_START={name}", flush=True)
        heartbeat_stop = threading.Event()

        def heartbeat():
            while not heartbeat_stop.wait(self.heartbeat_seconds):
                if heartbeat_stop.is_set():
                    return
                print(
                    f"HERMETIC_STAGE_ALIVE={name}:{int(self.clock() - start)}",
                    flush=True,
                )

        heartbeat_thread = threading.Thread(
            target=heartbeat, name="ctcc-hermetic-heartbeat", daemon=True
        )
        heartbeat_started = False
        try:
            with (self.output / f"{name}.log").open("wb") as log:
                remaining = self.deadline - self.clock()
                record["timeout_scope"] = (
                    "validation" if remaining < timeout else "stage"
                )
                if remaining <= 0:
                    raise TimeoutError("validation_deadline_exceeded")
                effective_timeout = min(timeout, remaining)
                heartbeat_thread.start()
                heartbeat_started = True
                result = subprocess.run(
                    args,
                    input=input_bytes,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    timeout=effective_timeout,
                    check=False,
                )
                record["exit_code"] = result.returncode
        except Exception as error:
            record["failure_type"] = type(error).__name__
            raise
        finally:
            heartbeat_stop.set()
            if heartbeat_started:
                heartbeat_thread.join(timeout=1)
            record["elapsed_seconds"] = self.clock() - start
            self.steps.append(record)
            self.save()
            print(
                f"HERMETIC_STAGE_END={name}:"
                f"{record['exit_code'] if record['exit_code'] is not None else record.get('failure_type', 'unknown')}",
                flush=True,
            )
        if result.returncode:
            raise RuntimeError(f"validation_step_failed:{name}")

    def save(self):
        (self.output / "steps.json").write_text(json.dumps(self.steps, indent=2))


def cleanup_resources(
    resources: list[tuple[str, str]],
    run_id: str,
    *,
    budget_seconds: float = CLEANUP_BUDGET_SECONDS,
    clock=time.monotonic,
) -> list[dict]:
    """Reconcile attempted creates, including timeouts, without touching others."""
    if budget_seconds < 0:
        raise ValueError("cleanup_timing_budget_invalid")
    deadline = clock() + budget_seconds

    def bounded_command(args, *, timeout, check, **kwargs):
        remaining = deadline - clock()
        if remaining <= 0:
            raise TimeoutError("cleanup_deadline_exceeded")
        return subprocess.run(
            args, timeout=min(timeout, remaining), check=check, **kwargs
        )

    results = []
    for kind, name in reversed(resources):
        result = {"kind": kind, "name": name, "status": "FAIL"}
        try:
            listing = bounded_command(
                [
                    "docker",
                    kind,
                    "ls",
                    *(["-a"] if kind == "container" else []),
                    "--format",
                    "{{.Name}}" if kind == "network" else "{{.Names}}",
                ],
                capture_output=True,
                text=True,
                check=True,
                timeout=30,
            )
            if name not in listing.stdout.splitlines():
                result["status"] = "ABSENT"
            else:
                inspected = bounded_command(
                    ["docker", kind, "inspect", name],
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=30,
                )
                details = json.loads(inspected.stdout)[0]
                labels = (
                    details.get("Config", {}) if kind == "container" else details
                ).get("Labels") or {}
                if labels.get("org.ctcc.validation.run") != run_id:
                    raise ValueError("cleanup_resource_ownership_mismatch")
                bounded_command(
                    [
                        "docker",
                        kind,
                        "rm",
                        *(["-f", "-v"] if kind == "container" else []),
                        name,
                    ],
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=60,
                )
                result["status"] = "REMOVED"
        except (
            OSError,
            subprocess.SubprocessError,
            ValueError,
            KeyError,
            IndexError,
        ) as error:
            result["failure_type"] = type(error).__name__
        results.append(result)
    return results


def wait_for_durable_seed(run: Run, container: str, marker: Path, *, timeout=120):
    """Require a live seed process and a complete durable readback marker."""
    deadline = time.monotonic() + timeout
    while True:
        inspected = subprocess.run(
            ["docker", "inspect", "--format", "{{json .State}}", container],
            capture_output=True,
            text=True,
            check=True,
            timeout=15,
        )
        state = json.loads(inspected.stdout)
        if state.get("Running") is not True:
            run.command("crash-seed-exited-log", ["docker", "logs", container])
            run.command("crash-seed-exited-state", ["docker", "inspect", container])
            raise RuntimeError("durable_seed_process_exited")
        if marker.is_file():
            raw = marker.read_bytes()
            if len(raw) > 32768:
                raise RuntimeError("durable_seed_marker_oversize")
            envelope = json.loads(raw)
            body = envelope["body"]
            canonical = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
            if (
                envelope["sha256"] != hashlib.sha256(canonical).hexdigest()
                or body.get("schema") != "ctcc.synthetic.qualification.crash-probe.v3"
                or type(body.get("account_captures")) is not list
                or len(body["account_captures"]) != 2
                or any(type(item) is not dict for item in body["account_captures"])
                or {item.get("scenario") for item in body["account_captures"]}
                != {"safe_failed_capture", "unfinished_ram_prefix"}
                or type(body.get("controls")) is not list
                or len(body["controls"]) != 3
                or any(type(item) is not dict for item in body["controls"])
                or {item.get("scenario") for item in body["controls"]}
                != {"arm_intent", "estop", "cold_estop"}
                or body.get("control_arm_intent_observed_before_publish") is not True
                or body.get("intent_version") != "ctcc-demo-submit-intent-v2"
                or type(body.get("exchange_request_sha256")) is not str
                or len(body["exchange_request_sha256"]) != 64
                or any(
                    c not in "0123456789abcdef" for c in body["exchange_request_sha256"]
                )
                or body.get("execution_authority") is not False
                or type(body.get("order_writes")) is not int
                or body["order_writes"] != 0
            ):
                raise RuntimeError("durable_seed_marker_invalid")
            return
        if time.monotonic() >= deadline:
            run.command("crash-seed-timeout-log", ["docker", "logs", container])
            raise RuntimeError("durable_seed_marker_missing")
        time.sleep(0.25)


API_PROBE = """
import json, time, urllib.request
deadline = time.monotonic() + 60
while True:
    try:
        values = {}
        for path in ('liveness', 'readiness', 'api/version'):
            with urllib.request.urlopen('http://127.0.0.1:8000/' + path, timeout=5) as response:
                values[path] = json.load(response)
        assert values['liveness']['status'] == 'alive'
        ready = values['readiness']
        assert ready['status'] == 'ready' and not ready['blockers']
        assert ready['database']['ok'] is True and ready['redis']['ok'] is True
        version = values['api/version']
        assert version['environment'] == 'test' and version['trading_mode'] == 'analysis_only'
        assert version['auto_trade'] is False and version['live_trading'] is False
        print(json.dumps(values, sort_keys=True))
        break
    except Exception:
        if time.monotonic() >= deadline:
            raise
        time.sleep(1)
"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--archive-sha256", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--tree", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--suite", choices=("full", "postgres", "shard"), default="full"
    )
    parser.add_argument("--shard-index", type=int)
    args = parser.parse_args()
    if (args.suite == "shard") != (args.shard_index is not None) or (
        args.shard_index is not None and not 0 <= args.shard_index < SHARD_COUNT
    ):
        raise ValueError("linux_suite_selection_invalid")
    for identity in (args.commit, args.tree):
        if not re.fullmatch("[a-f0-9]{40}", identity):
            raise ValueError("exact_git_identity_required")
    # Pin one byte buffer for admission and build. A Windows directory context
    # does not retain archived POSIX executable bits, and rereading the archive
    # after admission could submit different bytes to the builder.
    archive_bytes = args.archive.read_bytes()
    if hashlib.sha256(archive_bytes).hexdigest() != args.archive_sha256:
        raise ValueError("archive_digest_mismatch")
    args.output.mkdir(parents=True, exist_ok=False)
    source = args.output / "source"
    source.mkdir()
    with tarfile.open(fileobj=io.BytesIO(archive_bytes)) as archive:
        if (
            archive.pax_headers.get("comment") != args.commit
            or archive_tree(archive) != args.tree
        ):
            raise ValueError("archive_git_identity_mismatch")
        files = archive_files(archive)
        archive.extractall(source, filter="data")
    file_map = args.output / "source-files.json"
    file_map.write_text(json.dumps(files, sort_keys=True, separators=(",", ":")))
    file_map_sha256 = hashlib.sha256(file_map.read_bytes()).hexdigest()
    identity = {
        "commit": args.commit,
        "tree": args.tree,
        "archive_sha256": args.archive_sha256,
        "build_context": "admitted_archive_bytes_v1",
        "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "source_files_sha256": file_map_sha256,
        "manifest_sha256": (
            hashlib.sha256((source / "MANIFEST.sha256").read_bytes()).hexdigest()
            if (source / "MANIFEST.sha256").is_file()
            else None
        ),
        "suite": args.suite,
        "shard_index": args.shard_index,
        "shard_count": SHARD_COUNT,
        "host_credentials": False,
        "host_env": False,
        "external_order_writes": 0,
        "trading_acceptance": False,
    }
    (args.output / "identity.json").write_text(json.dumps(identity, indent=2))
    run = Run(args.output)
    test_results = args.output / "test-results"
    test_results.mkdir()
    # Dedicated synthetic test output only; neither source nor credentials mount.
    test_results.chmod(0o777)
    test_results_mount = (
        "--mount",
        f"type=bind,src={test_results.resolve()},dst=/validation-results",
    )
    suffix = uuid4().hex[:12]
    label = f"org.ctcc.validation.run={suffix}"
    network, postgres, redis = (
        f"ctcc-final-{suffix}-{part}" for part in ("network", "postgres", "redis")
    )
    image = f"ctcc-final-validation:{args.commit[:12]}-{suffix}"
    resources = []
    environment = [
        "-e",
        "ENVIRONMENT=test",
        "-e",
        "TRADING_MODE=analysis_only",
        "-e",
        f"DATABASE_URL=postgresql+asyncpg://ctcc@{postgres}:5432/ctcc",
        "-e",
        f"REDIS_URL=redis://{redis}:6379/0",
    ]

    # No exchange credential or write switch is passed. Settings defaults and the
    # hermetic pytest preflight both deny execution; the network has no egress.
    def container(name, command, *, mounts=(), extra_environment=(), timeout=3600):
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
                *environment,
                *extra_environment,
                image,
                *command,
            ],
            timeout=timeout,
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
            input_bytes=archive_bytes,
        )
        run.command(
            "image-identity",
            [
                "docker",
                "image",
                "inspect",
                "--format",
                "{{.Id}} {{json .RepoDigests}} {{json .Config.Labels}}",
                image,
            ],
        )
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
                "postgres:17-alpine@sha256:f02121de6f74d30d8a94cd1d9584125e2178d7e6c377d8130112d4e52d867995",
            ],
        )
        resources.append(("container", redis))
        run.command(
            "redis-start",
            [
                "docker",
                "run",
                "-d",
                "--name",
                redis,
                "--label",
                label,
                "--network",
                network,
                "redis:8-alpine@sha256:bd999b5cfee25fb24b8320a31fddbd69f462df44c8138c66e369582937beebc0",
            ],
        )
        deadline = time.monotonic() + 120
        while subprocess.run(
            ["docker", "exec", postgres, "pg_isready", "-U", "ctcc", "-d", "ctcc"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=15,
        ).returncode:
            if time.monotonic() > deadline:
                raise RuntimeError("isolated_postgres_not_ready")
            time.sleep(1)
        container("copied-manifest", ["python", "scripts/manifest.py", "--check"])
        container(
            "copied-source-exact",
            [
                "python",
                "-c",
                "from pathlib import Path; from scripts.verify_final_hermetic import verify_copied_source; import sys; print(verify_copied_source(Path('/app'),Path('/verification/source-files.json'),sys.argv[1]))",
                file_map_sha256,
            ],
            mounts=(
                "--mount",
                f"type=bind,source={file_map.resolve()},target=/verification/source-files.json,readonly",
            ),
        )
        container("dependencies", ["python", "-m", "pip", "freeze", "--all"])
        container(
            "dependency-lock",
            ["python", "-m", "scripts.verify_dependency_lock", "--target", "linux"],
        )
        container("ruff", ["python", "-m", "ruff", "check", "--no-cache", "."])
        container(
            "format",
            ["python", "-m", "ruff", "format", "--check", "--no-cache", "."],
        )
        container("migration-initial-supported", ["alembic", "upgrade", "0001"])
        container("migration-upgrade", ["alembic", "upgrade", "head"])
        container(
            "migration-identity", ["python", "-m", "scripts.verify_migration_identity"]
        )
        identity["migration"] = json.loads(
            (args.output / "migration-identity.log").read_text()
        )
        container("migration-drift", ["alembic", "check"])
        container("migration-downgrade", ["alembic", "downgrade", "0016"])
        container("migration-reupgrade", ["alembic", "upgrade", "head"])
        container(
            "migration-reidentity",
            ["python", "-m", "scripts.verify_migration_identity"],
        )
        if identity["migration"] != json.loads(
            (args.output / "migration-reidentity.log").read_text()
        ):
            raise RuntimeError("migration_reupgrade_identity_changed")
        container("migration-redrift", ["alembic", "check"])
        if args.suite in ("full", "postgres"):
            postgres_command = [
                "python",
                "-m",
                "scripts.hermetic_pytest",
                "-p",
                "no:cacheprovider",
                "-ra",
                "--junitxml=/validation-results/postgres-intent.xml",
            ]
            if args.suite == "postgres":
                postgres_command += [
                    "-p",
                    "scripts.verify_linux_shard",
                    "--ctcc-linux-suite=postgres",
                    "--ctcc-linux-collection-json=/validation-results/postgres-collection.json",
                    "tests",
                ]
            else:
                postgres_command += sorted(POSTGRES_TEST_PATHS)
            container(
                "postgres-intent",
                postgres_command,
                mounts=test_results_mount,
                timeout=POSTGRES_TEST_TIMEOUT_SECONDS,
            )
            identity["postgres_tests"] = verify_pytest_report(
                test_results / "postgres-intent.xml",
                required_cases=ARCHIVE_REQUIRED_CASES,
                required_modules=tuple(
                    sorted(
                        path.removesuffix(".py").replace("/", ".")
                        for path in POSTGRES_TEST_PATHS
                    )
                ),
            )
            if args.suite == "postgres":
                identity["collection"] = verify_linux_collection(
                    test_results / "postgres-collection.json",
                    mode="postgres",
                    index=None,
                    report=identity["postgres_tests"],
                )
        if args.suite == "full":
            container(
                "linux-full",
                [
                    "python",
                    "-m",
                    "scripts.hermetic_pytest",
                    "-p",
                    "no:cacheprovider",
                    "-ra",
                    "--junitxml=/validation-results/linux-full.xml",
                    "tests",
                ],
                mounts=test_results_mount,
                timeout=FULL_TEST_TIMEOUT_SECONDS,
            )
            identity["linux_tests"] = verify_pytest_report(
                test_results / "linux-full.xml",
                required_cases=ARCHIVE_REQUIRED_CASES,
                required_modules=tuple(
                    identity["postgres_tests"]["required_module_passed"]
                ),
                allow_unrelated_skips=True,
            )
        if args.suite == "shard":
            stage = f"linux-shard-{args.shard_index}"
            container(
                stage,
                [
                    "python",
                    "-m",
                    "scripts.hermetic_pytest",
                    "-p",
                    "no:cacheprovider",
                    "-p",
                    "scripts.verify_linux_shard",
                    "-ra",
                    f"--junitxml=/validation-results/{stage}.xml",
                    "--ctcc-linux-suite=shard",
                    f"--ctcc-linux-shard-index={args.shard_index}",
                    f"--ctcc-linux-collection-json=/validation-results/{stage}-collection.json",
                    "tests",
                ],
                mounts=test_results_mount,
                timeout=FULL_TEST_TIMEOUT_SECONDS,
            )
            identity["linux_tests"] = verify_pytest_report(
                test_results / f"{stage}.xml",
                allow_unrelated_skips=True,
            )
            identity["collection"] = verify_linux_collection(
                test_results / f"{stage}-collection.json",
                mode="shard",
                index=args.shard_index,
                report=identity["linux_tests"],
            )
            identity["component_validation"] = "PASS"
            identity["hermetic_regression"] = "PENDING_UNION"
            return
        # Actual process death and server restarts, using explicitly synthetic
        # claims. This does not establish source, Demo or Live acceptance.
        probe_dir = args.output / "crash-probe"
        probe_dir.mkdir()
        probe_dir.chmod(0o777)  # Dedicated Linux validation output; no secrets.
        probe_marker = probe_dir / "durable.json"
        probe_container = f"ctcc-final-{suffix}-crash-seed"
        resources.append(("container", probe_container))
        probe_mount = (
            "--mount",
            f"type=bind,source={probe_dir.resolve()},target=/probe",
        )
        run.command(
            "crash-probe-start",
            [
                "docker",
                "run",
                "-d",
                "--name",
                probe_container,
                "--label",
                label,
                "--network",
                network,
                *probe_mount,
                *environment,
                "-e",
                "CTCC_HERMETIC_DURABILITY=1",
                image,
                "python",
                "-m",
                "scripts.qualification_durability_probe",
                "seed",
                "--marker",
                "/probe/durable.json",
            ],
        )
        wait_for_durable_seed(run, probe_container, probe_marker)
        run.command("crash-seed-readback-log", ["docker", "logs", probe_container])
        run.command(
            "process-sigkill", ["docker", "kill", "--signal=KILL", probe_container]
        )
        run.command("postgres-restart", ["docker", "restart", "--time", "10", postgres])
        run.command("redis-restart", ["docker", "restart", "--time", "10", redis])
        deadline = time.monotonic() + 120
        while subprocess.run(
            ["docker", "exec", postgres, "pg_isready", "-U", "ctcc", "-d", "ctcc"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=15,
        ).returncode:
            if time.monotonic() >= deadline:
                raise RuntimeError("restarted_postgres_not_ready")
            time.sleep(0.25)
        container(
            "crash-restart-independent-readback",
            [
                "python",
                "-m",
                "scripts.qualification_durability_probe",
                "verify",
                "--marker",
                "/probe/durable.json",
            ],
            mounts=(probe_mount[0], probe_mount[1] + ",readonly"),
            extra_environment=("-e", "CTCC_HERMETIC_DURABILITY=1"),
        )
        identity["synthetic_process_pg_redis_crash_durability"] = "PASS"
        identity["crash_probe_marker_sha256"] = hashlib.sha256(
            probe_marker.read_bytes()
        ).hexdigest()
        api = f"ctcc-final-{suffix}-api"
        resources.append(("container", api))
        run.command(
            "api-start",
            [
                "docker",
                "run",
                "-d",
                "--name",
                api,
                "--label",
                label,
                "--network",
                network,
                *environment,
                image,
            ],
        )
        run.command(
            "api-health", ["docker", "exec", api, "python", "-c", API_PROBE], timeout=90
        )
        run.command(
            "api-restart", ["docker", "restart", "--time", "10", api], timeout=60
        )
        run.command(
            "api-restart-health",
            ["docker", "exec", api, "python", "-c", API_PROBE],
            timeout=90,
        )
        run.command("api-logs", ["docker", "logs", api], timeout=30)
        identity["api_service_restart"] = "PASS"
        identity["live_restart_acceptance"] = False
        identity["component_validation"] = "PASS"
        identity["hermetic_regression"] = (
            "PASS" if args.suite == "full" else "PENDING_UNION"
        )
    except Exception as error:
        identity["component_validation"] = "FAIL"
        identity["hermetic_regression"] = "FAIL"
        identity["failure_type"] = type(error).__name__
        raise
    finally:
        cleanup = cleanup_resources(resources, suffix)
        (args.output / "cleanup.json").write_text(json.dumps(cleanup, indent=2))
        cleanup_failed = any(item["status"] == "FAIL" for item in cleanup)
        identity["cleanup"] = "FAIL" if cleanup_failed else "PASS"
        if cleanup_failed:
            identity["component_validation"] = "FAIL"
            identity["hermetic_regression"] = "FAIL"
        (args.output / "identity.json").write_text(json.dumps(identity, indent=2))
        evidence_files = (
            *args.output.iterdir(),
            *test_results.glob("*.xml"),
            *test_results.glob("*-collection.json"),
            *(args.output / "crash-probe").glob("*.json"),
        )
        hashes = {
            p.relative_to(args.output).as_posix(): hashlib.sha256(
                p.read_bytes()
            ).hexdigest()
            for p in evidence_files
            if p.is_file() and not p.is_symlink()
        }
        (args.output / "results.sha256.json").write_text(json.dumps(hashes, indent=2))
        if cleanup_failed:
            raise RuntimeError("validation_resource_cleanup_unconfirmed")


if __name__ == "__main__":
    main()
