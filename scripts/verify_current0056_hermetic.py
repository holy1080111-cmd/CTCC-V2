"""Exact Git-archive Docker acceptance for the installed DB0056 contract."""

import argparse
import io
import json
import re
import tarfile
from contextlib import ExitStack
from pathlib import Path
from uuid import uuid4

from scripts import current0056_hermetic_contract as contract
from scripts import verify_final_hermetic as shared
from scripts.current0055_catalog_receipt import verify_current55_catalog_report
from scripts.devval_hermetic_tls import devval_tls
from scripts.evidence_json import read_evidence_json
from scripts.verify_current0055_hermetic import (
    _COPIED_SOURCE,
    _PG_READY,
    _POSTGRES,
    _PYTEST,
    _REDIS,
    _REDIS_READY,
    _RESERVATION_UNIQUE_QUERY,
    _cases_to_nodes,
    _isolated_client,
    _write,
)
from scripts.verify_linux_shard import (
    DEVVAL_CUSTODY_TEST_PATH,
    POSTGRES_PROGRESS_OPTION,
    postgres_paths,
)

_EXACT_HOST_HELPERS = (
    "current0055_catalog_receipt.py",
    "current0055_hermetic_contract.py",
    "current0056_hermetic_contract.py",
    "current0056_migration_pin.py",
    "devval_hermetic_tls.py",
    "evidence_json.py",
    "verify_current0055_hermetic.py",
    "verify_final_hermetic.py",
    "verify_linux_shard.py",
)
_PARENT_HEAD_SQL = "SELECT version_num FROM public.alembic_version"


def _test_command(mode, index):
    junit, collection = contract.component_report_paths(mode, index)
    junit = "/validation-results/" + Path(junit).name
    collection = "/validation-results/" + Path(collection).name
    if mode == "full":
        return (
            *_PYTEST,
            "-p",
            "scripts.current0056_hermetic_contract",
            f"--ctcc-current56-full-collection={collection}",
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
        *((POSTGRES_PROGRESS_OPTION,) if mode == "postgres" else ()),
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
        raise ValueError("current56_runner_arguments_invalid")
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
            raise ValueError("current56_archive_unavailable")
        archive_bytes = args.archive.read_bytes()
    if (
        type(archive_bytes) is not bytes
        or not 0 < len(archive_bytes) <= 128 * 1024 * 1024
    ):
        raise ValueError("current56_archive_unavailable")
    raw = archive_bytes
    if contract.digest(raw) != args.archive_sha256:
        raise ValueError("current56_archive_sha256_mismatch")
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
        if (
            archive.pax_headers.get("comment") != args.commit
            or shared.archive_tree(archive) != args.tree
        ):
            raise ValueError("current56_archive_git_identity_mismatch")
        files = shared.archive_files(archive)
    expected_runner = files[contract.RUNNER_PATH]["sha256"]
    if contract.digest(Path(__file__).read_bytes()) != expected_runner:
        raise ValueError("current56_runner_does_not_match_archive")
    for name in _EXACT_HOST_HELPERS:
        source = Path(__file__).with_name(name)
        if contract.digest(source.read_bytes()) != files[f"scripts/{name}"]["sha256"]:
            raise ValueError("current56_host_helper_does_not_match_archive")
    contract.migration_digest(files)
    return raw, files, expected_runner


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
    needs_devval_tls = (
        args.suite != "shard" and DEVVAL_CUSTODY_TEST_PATH in postgres_paths(index)
    )
    tls_resources = ExitStack()

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

    def parent_head(stage):
        command(
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
            _PARENT_HEAD_SQL,
        )
        if (root / f"{stage}.log").read_bytes().strip() != contract.PARENT.encode(
            "ascii"
        ):
            raise ValueError("current56_parent_database_head_changed")

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
            raise RuntimeError("current56_docker_network_not_internal")
        resources.append(("container", postgres))
        tls = tls_resources.enter_context(devval_tls(needs_devval_tls, postgres))
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
            *(tls.postgres_mount if tls else ()),
            _POSTGRES,
            *(tls.postgres_command if tls else ()),
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
            container("migration-upgrade-parent", "alembic", "upgrade", contract.PARENT)
            parent_head("migration-parent-readback")
        container("migration-upgrade", "alembic", "upgrade", "head")
        container(
            "migration-identity", "python", "-m", "scripts.verify_migration_identity"
        )
        identity["migration"] = contract.migration_readback(
            (root / "migration-identity.log").read_bytes(),
            contract.migration_digest(files),
        )
        if anchor:
            container(
                "migration-empty-downgrade", "alembic", "downgrade", contract.PARENT
            )
            parent_head("migration-empty-downgrade-readback")
            container("migration-empty-reupgrade", "alembic", "upgrade", "head")
            container(
                "migration-empty-reupgrade-identity",
                "python",
                "-m",
                "scripts.verify_migration_identity",
            )
            if (
                contract.migration_readback(
                    (root / "migration-empty-reupgrade-identity.log").read_bytes(),
                    contract.migration_digest(files),
                )
                != identity["migration"]
            ):
                raise ValueError("current56_reupgrade_database_identity_changed")
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
            mounts=(*test_mount, *(tls.pytest_mount if tls else ())),
            extra_environment=(
                "-e",
                "CTCC_RUN_B3_PREFIX_33=1",
                "-e",
                "CTCC_B3_SCALE_RECEIPT_DIR=/validation-results",
                *(tls.pytest_environment if tls else ()),
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
        try:
            cleanup = shared.cleanup_resources(resources, run_id)
        finally:
            tls_resources.close()
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
        contract.verify_current56_component(
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
