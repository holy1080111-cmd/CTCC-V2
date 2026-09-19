"""Validate a Git archive in new, credential-free, isolated Docker services.

Never reads a host .env or uses the deployment's Compose project or volumes.
The archive's actual Git tree is reconstructed before building. Results identify
this run only; a green result is not trading, OOS or production acceptance.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import stat
import subprocess
import tarfile
import time
from pathlib import Path, PurePosixPath
from uuid import uuid4


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


def verify_pytest_report(path: Path, *, required_modules=()):
    """Require executed tests, preserve platform skips, reject empty/false greens."""
    import xml.etree.ElementTree as ET

    if path.is_symlink() or not 0 < path.stat().st_size <= 64 * 1024 * 1024:
        raise ValueError("pytest_report_missing_or_unsafe")
    root = ET.fromstring(path.read_bytes())
    if root.tag != "testsuites" or not len(root):
        raise ValueError("pytest_report_schema_invalid")
    total = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    executed_modules = set()
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
            if all(case.find(tag) is None for tag in ("failure", "error", "skipped")):
                executed_modules.add(case.get("classname"))
    passed = total["tests"] - total["failures"] - total["errors"] - total["skipped"]
    if (
        passed <= 0
        or total["failures"]
        or total["errors"]
        or (required_modules and total["skipped"])
        or not set(required_modules) <= executed_modules
    ):
        raise ValueError("pytest_required_execution_not_verified")
    return {
        **total,
        "passed": passed,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


class Run:
    def __init__(self, output: Path):
        self.output = output
        self.steps = []

    def command(self, name, args, *, timeout=3600):
        start = time.monotonic()
        record = {"name": name, "exit_code": None}
        try:
            with (self.output / f"{name}.log").open("wb") as log:
                result = subprocess.run(
                    args,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    timeout=timeout,
                    check=False,
                )
                record["exit_code"] = result.returncode
        except (OSError, subprocess.TimeoutExpired) as error:
            record["failure_type"] = type(error).__name__
            raise
        finally:
            record["elapsed_seconds"] = time.monotonic() - start
            self.steps.append(record)
            self.save()
        if result.returncode:
            raise RuntimeError(f"validation_step_failed:{name}")

    def save(self):
        (self.output / "steps.json").write_text(json.dumps(self.steps, indent=2))


def cleanup_resources(resources: list[tuple[str, str]], run_id: str) -> list[dict]:
    """Reconcile attempted creates, including timeouts, without touching others."""
    results = []
    for kind, name in reversed(resources):
        result = {"kind": kind, "name": name, "status": "FAIL"}
        try:
            listing = subprocess.run(
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
                inspected = subprocess.run(
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
                subprocess.run(
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
                or body.get("schema") != "ctcc.synthetic.qualification.crash-probe.v1"
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
    args = parser.parse_args()
    for identity in (args.commit, args.tree):
        if not re.fullmatch("[a-f0-9]{40}", identity):
            raise ValueError("exact_git_identity_required")
    if hashlib.sha256(args.archive.read_bytes()).hexdigest() != args.archive_sha256:
        raise ValueError("archive_digest_mismatch")
    args.output.mkdir(parents=True, exist_ok=False)
    source = args.output / "source"
    source.mkdir()
    with tarfile.open(args.archive) as archive:
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
        "source_files_sha256": file_map_sha256,
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
    def container(name, command, *, mounts=(), extra_environment=()):
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
                str(source),
            ],
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
        container(
            "postgres-intent",
            [
                "python",
                "-m",
                "scripts.hermetic_pytest",
                "-p",
                "no:cacheprovider",
                "-ra",
                "--junitxml=/validation-results/postgres-intent.xml",
                "tests/integration/test_qualification_ledger_repository.py",
                "tests/integration/test_qualification_submission_intent_repository.py",
                "tests/integration/test_history_submission_intent_repository.py",
                "tests/integration/test_submission_reporting_repository.py",
            ],
            mounts=test_results_mount,
        )
        identity["postgres_tests"] = verify_pytest_report(
            test_results / "postgres-intent.xml",
            required_modules=(
                "tests.integration.test_qualification_ledger_repository",
                "tests.integration.test_qualification_submission_intent_repository",
                "tests.integration.test_history_submission_intent_repository",
                "tests.integration.test_submission_reporting_repository",
            ),
        )
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
        )
        identity["linux_tests"] = verify_pytest_report(test_results / "linux-full.xml")
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
        identity["hermetic_regression"] = "PASS"
    except Exception as error:
        identity["hermetic_regression"] = "FAIL"
        identity["failure_type"] = type(error).__name__
        raise
    finally:
        cleanup = cleanup_resources(resources, suffix)
        (args.output / "cleanup.json").write_text(json.dumps(cleanup, indent=2))
        cleanup_failed = any(item["status"] == "FAIL" for item in cleanup)
        identity["cleanup"] = "FAIL" if cleanup_failed else "PASS"
        if cleanup_failed:
            identity["hermetic_regression"] = "FAIL"
        (args.output / "identity.json").write_text(json.dumps(identity, indent=2))
        evidence_files = (
            *args.output.iterdir(),
            *test_results.glob("*.xml"),
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
