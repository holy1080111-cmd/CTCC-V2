"""Exact Git-archive Docker acceptance for the installed DB0055 contract."""

import argparse
import io
import json
import os
import re
import tarfile
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from scripts import current0055_hermetic_contract as contract
from scripts import verify_final_hermetic as shared
from scripts.current0055_catalog_receipt import verify_current55_catalog_report
from scripts.evidence_json import read_evidence_json

_POSTGRES = "postgres:17-alpine@sha256:f02121de6f74d30d8a94cd1d9584125e2178d7e6c377d8130112d4e52d867995"
_REDIS = "redis:8-alpine@sha256:bd999b5cfee25fb24b8320a31fddbd69f462df44c8138c66e369582937beebc0"
_PYTEST = ("python", "-m", "scripts.hermetic_pytest", "-p", "no:cacheprovider", "-ra")
_COPIED_SOURCE = """
import sys
from pathlib import Path
from scripts.verify_final_hermetic import verify_copied_source
print(verify_copied_source(Path('/app'), Path('/ctcc-source-files.json'), sys.argv[1]))
"""
_PG_READY = "for n in $(seq 1 90); do pg_isready -U ctcc -d ctcc && exit 0; sleep 1; done; exit 1"
_REDIS_READY = 'for n in $(seq 1 45); do [ "$(redis-cli ping)" = PONG ] && { echo PONG; exit 0; }; sleep 1; done; exit 1'
_RESERVATION_UNIQUE_QUERY = (
    "SELECT conname, pg_catalog.pg_get_constraintdef(oid) FROM pg_catalog.pg_constraint "
    "WHERE conrelid='public.qualification_reservation_transitions'::regclass AND contype='u'"
)


def _write(path, value):
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")


def _cases_to_nodes(cases):
    return tuple(module.replace(".", "/") + ".py::" + name for module, name in cases)


def _test_command(mode, index):
    junit, collection = contract.component_report_paths(mode, index)
    junit = "/validation-results/" + Path(junit).name
    collection = "/validation-results/" + Path(collection).name
    if mode == "full":
        return (
            *_PYTEST,
            "-p",
            "scripts.current0055_hermetic_contract",
            f"--ctcc-current55-full-collection={collection}",
            f"--junitxml={junit}",
            "tests",
        )
    option = "postgres-component-index" if mode == "postgres" else "shard-index"
    return (
        *_PYTEST,
        "-p",
        "scripts.verify_linux_shard",
        f"--ctcc-linux-suite={mode}",
        f"--ctcc-linux-{option}={index}",
        f"--ctcc-linux-collection-json={collection}",
        f"--junitxml={junit}",
        "tests",
    )


def _arguments():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--archive-sha256", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--tree", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--suite", choices=("full", "postgres", "shard"), default="full"
    )
    parser.add_argument("--shard-index", type=int)
    parser.add_argument("--postgres-component-index", type=int)
    args = parser.parse_args()
    if (
        (args.shard_index is not None and args.suite != "shard")
        or (args.postgres_component_index is not None and args.suite != "postgres")
        or not re.fullmatch(r"[0-9a-f]{40}", args.commit)
        or not re.fullmatch(r"[0-9a-f]{40}", args.tree)
        or not re.fullmatch(r"[0-9a-f]{64}", args.archive_sha256)
    ):
        raise ValueError("current55_runner_arguments_invalid")
    index = (
        args.postgres_component_index if args.suite == "postgres" else args.shard_index
    )
    contract.component_test_stage(args.suite, index)
    return args, index


def _admit_archive(args, archive_bytes=None):
    if archive_bytes is None:
        if (
            args.archive.is_symlink()
            or not args.archive.is_file()
            or not 0 < args.archive.stat().st_size <= 128 * 1024 * 1024
        ):
            raise ValueError("current55_archive_unavailable")
        archive_bytes = args.archive.read_bytes()
    if (
        type(archive_bytes) is not bytes
        or not 0 < len(archive_bytes) <= 128 * 1024 * 1024
    ):
        raise ValueError("current55_archive_unavailable")
    raw = archive_bytes
    if contract.digest(raw) != args.archive_sha256:
        raise ValueError("current55_archive_sha256_mismatch")
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
        if (
            archive.pax_headers.get("comment") != args.commit
            or shared.archive_tree(archive) != args.tree
        ):
            raise ValueError("current55_archive_git_identity_mismatch")
        files = shared.archive_files(archive)
    expected_runner = files[contract.RUNNER_PATH]["sha256"]
    if contract.digest(Path(__file__).read_bytes()) != expected_runner:
        raise ValueError("current55_runner_does_not_match_archive")
    contract.migration_digest(files)
    return raw, files, expected_runner


@contextmanager
def _isolated_client(root):
    endpoint = os.environ.get("DOCKER_HOST")
    if os.environ.get("DOCKER_CONTEXT") or (
        endpoint and not endpoint.startswith("unix:///")
    ):
        raise ValueError("current55_local_docker_socket_required")
    client = root / "docker-client"
    client.mkdir(mode=0o700)
    previous = os.environ.get("DOCKER_CONFIG")
    os.environ["DOCKER_CONFIG"] = str(client)
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("DOCKER_CONFIG", None)
        else:
            os.environ["DOCKER_CONFIG"] = previous


def main(*, archive_bytes=None):
    args, index = _arguments()
    raw, files, runner_sha = _admit_archive(args, archive_bytes)
    args.output.mkdir(parents=True, exist_ok=False)
    root = args.output.resolve(strict=True)
    with _isolated_client(root):
        return _run(args, index, raw, files, runner_sha, root)


def _run(args, index, raw, files, runner_sha, root):
    test_results = root / "test-results"
    test_results.mkdir(mode=0o777)
    test_results.chmod(
        0o777
    )  # Writable only synthetic evidence, never source or credentials.
    source_map = root / "source-files.json"
    source_map.write_text(
        json.dumps(files, sort_keys=True, separators=(",", ":")), encoding="utf-8"
    )
    source_map.chmod(0o444)
    map_sha = contract.digest(source_map.read_bytes())
    run_id = uuid4().hex[:12]
    prefix = f"ctcc-final-{run_id}-"
    label = f"org.ctcc.validation.run={run_id}"
    network, postgres, redis, api = (
        prefix + suffix for suffix in ("network", "postgres", "redis", "api")
    )
    image = f"ctcc-final-validation:{args.commit[:12]}-{run_id}"
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
    test_mount = ("--mount", f"type=bind,src={test_results},dst=/validation-results")
    source_mount = (
        "--mount",
        f"type=bind,src={source_map},dst=/ctcc-source-files.json,readonly",
    )
    identity = {
        "commit": args.commit,
        "tree": args.tree,
        "archive_sha256": args.archive_sha256,
        "build_context": "admitted_archive_bytes_v1",
        "runner_sha256": runner_sha,
        "runner_contract": contract.RUNNER_CONTRACT,
        "source_contract_head": contract.HEAD,
        "source_files_sha256": map_sha,
        "manifest_sha256": files["MANIFEST.sha256"]["sha256"],
        "run_id": run_id,
        "suite": args.suite,
        "shard_index": args.shard_index,
        "postgres_component_index": args.postgres_component_index,
        "host_credentials": False,
        "host_env": False,
        "external_order_writes": 0,
        "trading_acceptance": False,
    }
    _write(root / "identity.json", identity)
    run = shared.Run(root)
    resources = []
    anchor = contract.component_is_anchor(args.suite, index)

    def command(stage, *command_args, **options):
        run.command(stage, ["docker", *command_args], **options)

    def container(stage, *command_args, mounts=(), extra_environment=(), timeout=3600):
        name = prefix + stage
        resources.append(("container", name))
        command(
            stage,
            "run",
            "--rm",
            "--name",
            name,
            "--label",
            label,
            "--network",
            network,
            *mounts,
            *environment,
            *extra_environment,
            image,
            *command_args,
            timeout=timeout,
        )

    def ready(stage, service, probe):
        command(stage, "exec", service, "sh", "-ec", probe, timeout=120)

    def pytest_stage(stage, report, cases):
        container(
            stage,
            *_PYTEST,
            f"--junitxml=/validation-results/{Path(report).name}",
            *_cases_to_nodes(cases),
            mounts=test_mount,
        )

    try:
        command(
            "build",
            "build",
            "--no-cache",
            "--build-arg",
            f"CTCC_SOURCE_COMMIT={args.commit}",
            "--build-arg",
            f"CTCC_SOURCE_TREE={args.tree}",
            "-t",
            image,
            "-",
            input_bytes=raw,
        )
        command("image-identity", "image", "inspect", "--format", "{{json .}}", image)
        identity["image_id"] = read_evidence_json(
            (root / "image-identity.log").read_bytes()
        )["Id"]
        resources.append(("network", network))
        command(
            "network-create",
            "network",
            "create",
            "--internal",
            "--label",
            label,
            network,
        )
        command(
            "network-inspect", "network", "inspect", "--format", "{{json .}}", network
        )
        if (
            read_evidence_json((root / "network-inspect.log").read_bytes())["Internal"]
            is not True
        ):
            raise RuntimeError("current55_docker_network_not_internal")
        resources.append(("container", postgres))
        command(
            "postgres-start",
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
            _POSTGRES,
        )
        resources.append(("container", redis))
        command(
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
        ready("postgres-ready", postgres, _PG_READY)
        ready("redis-ready", redis, _REDIS_READY)
        container("copied-manifest", "python", "scripts/manifest.py", "--check")
        container(
            "copied-source-exact",
            "python",
            "-c",
            _COPIED_SOURCE,
            map_sha,
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
        container("ruff", "python", "-m", "ruff", "check", "--no-cache", ".")
        container(
            "format", "python", "-m", "ruff", "format", "--check", "--no-cache", "."
        )
        if anchor:
            container("migration-initial-supported", "alembic", "upgrade", "0001")
            container("migration-upgrade-supported", "alembic", "upgrade", "0016")
            container("migration-downgrade-supported", "alembic", "downgrade", "0001")
            container(
                "migration-supported-readback",
                "python",
                "-c",
                shared.MIGRATION_REVISION_PROBE,
                "0001",
            )
        container("migration-upgrade", "alembic", "upgrade", "head")
        container(
            "migration-identity", "python", "-m", "scripts.verify_migration_identity"
        )
        identity["migration"] = contract.migration_readback(
            (root / "migration-identity.log").read_bytes(),
            contract.migration_digest(files),
        )
        command(
            "migration-reservation-unique",
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
            _RESERVATION_UNIQUE_QUERY,
        )
        container("migration-drift", "alembic", "check")
        if anchor:
            pytest_stage(
                "current55-catalog-tests",
                contract.CATALOG_REPORT,
                contract.CATALOG_CASES,
            )
            identity["catalog"] = verify_current55_catalog_report(
                root / contract.CATALOG_REPORT
            )
            pytest_stage(
                "current55-recovery-tests",
                contract.RECOVERY_REPORT,
                contract.RECOVERY_CASES,
            )
            identity["recovery"] = shared.verify_pytest_report(
                root / contract.RECOVERY_REPORT, required_cases=contract.RECOVERY_CASES
            )
        stage = contract.component_test_stage(args.suite, index)
        junit, _ = contract.component_report_paths(args.suite, index)
        container(
            stage,
            *_test_command(args.suite, index),
            mounts=test_mount,
            extra_environment=(
                "-e",
                "CTCC_RUN_B3_PREFIX_33=1",
                "-e",
                "CTCC_B3_SCALE_RECEIPT_DIR=/validation-results",
            ),
            timeout=shared.FULL_TEST_TIMEOUT_SECONDS,
        )
        identity["tests"] = shared.verify_pytest_report(
            root / junit, allow_unrelated_skips=True
        )
        if anchor:
            resources.append(("container", api))
            command(
                "api-start",
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
                "uvicorn",
                "app.main:app",
                "--host",
                "0.0.0.0",
                "--port",
                "8000",
                "--workers",
                "1",
            )
            command(
                "api-before-restart",
                "exec",
                api,
                "python",
                "-c",
                shared.API_PROBE,
                timeout=90,
            )
            command(
                "api-instance-before",
                "container",
                "inspect",
                "--format",
                "{{json .}}",
                api,
            )
            command("postgres-restart", "restart", "--time", "10", postgres, timeout=90)
            ready("postgres-ready-after-restart", postgres, _PG_READY)
            command("redis-restart", "restart", "--time", "10", redis, timeout=90)
            ready("redis-ready-after-restart", redis, _REDIS_READY)
            command("api-restart", "restart", "--time", "10", api, timeout=90)
            command(
                "api-after-restart",
                "exec",
                api,
                "python",
                "-c",
                shared.API_PROBE,
                timeout=90,
            )
            command(
                "api-instance-after",
                "container",
                "inspect",
                "--format",
                "{{json .}}",
                api,
            )
            container(
                "migration-identity-after-restart",
                "python",
                "-m",
                "scripts.verify_migration_identity",
            )
            container("migration-drift-after-restart", "alembic", "check")
        identity["component_validation"] = "PASS"
        identity["hermetic_regression"] = (
            "PASS" if args.suite == "full" else "PENDING_UNION"
        )
    except Exception as error:
        identity.update(
            component_validation="FAIL",
            hermetic_regression="FAIL",
            failure_type=type(error).__name__,
        )
        raise
    finally:
        cleanup = shared.cleanup_resources(resources, run_id)
        _write(root / "cleanup.json", cleanup)
        identity["cleanup"] = (
            "PASS" if all(item["status"] != "FAIL" for item in cleanup) else "FAIL"
        )
        if identity["cleanup"] == "FAIL":
            identity.update(component_validation="FAIL", hermetic_regression="FAIL")
        _write(root / "identity.json", identity)
        expected = contract.component_artifacts(args.suite, index)
        hashes = {
            name: contract.digest((root / name).read_bytes())
            for name in sorted(expected)
            if (root / name).is_file()
        }
        _write(root / "results.sha256.json", hashes)
    try:
        contract.verify_current55_component(
            root,
            raw,
            commit=args.commit,
            tree=args.tree,
            manifest_sha256=identity["manifest_sha256"],
        )
    except Exception as error:
        identity.update(
            component_validation="FAIL",
            hermetic_regression="FAIL",
            failure_type=type(error).__name__,
        )
        _write(root / "identity.json", identity)
        hashes["identity.json"] = contract.digest((root / "identity.json").read_bytes())
        _write(root / "results.sha256.json", hashes)
        raise
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
