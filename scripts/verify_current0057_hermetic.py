"""Run the explicit four-case DB0057 G12 smoke on exact archived source only."""

from __future__ import annotations

import argparse
import io
import re
import tarfile
from contextlib import ExitStack
from pathlib import Path
from uuid import uuid4

from scripts import current0057_g12_measurement_v1 as measurement
from scripts import current0057_hermetic_contract as contract
from scripts import verify_final_hermetic as shared
from scripts.devval_hermetic_tls import devval_tls
from scripts.evidence_json import read_evidence_json
from scripts.verify_current0055_hermetic import (
    _COPIED_SOURCE,
    _POSTGRES,
    _PYTEST,
    _REDIS,
    _REDIS_READY,
    _cases_to_nodes,
    _isolated_client,
    _write,
)
from scripts.verify_current0056_hermetic import _PG_TLS_READY

_HELPERS = (
    "current0057_hermetic_contract.py",
    "current0057_g12_measurement_v1.py",
    "current0057_migration_pin.py",
    "verify_current0057_g12_smoke_pair.py",
    "current0056_hermetic_contract.py",
    "current0056_migration_pin.py",
    "current0055_hermetic_contract.py",
    "verify_current0055_hermetic.py",
    "verify_current0056_hermetic.py",
    "verify_final_hermetic.py",
    "devval_hermetic_tls.py",
    "evidence_json.py",
    "verify_linux_shard.py",
)


def _arguments():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--archive-sha256", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--tree", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--g12-successor-smoke", action="store_true")
    parser.add_argument("--suite", choices=("postgres",), default="postgres")
    parser.add_argument("--postgres-component-index", type=int, default=1)
    args = parser.parse_args()
    if (
        not args.g12_successor_smoke
        or args.postgres_component_index != 1
        or any(
            re.fullmatch(pattern, value) is None
            for pattern, value in (
                (r"[a-f0-9]{40}", args.commit),
                (r"[a-f0-9]{40}", args.tree),
                (r"[a-f0-9]{64}", args.archive_sha256),
            )
        )
    ):
        raise ValueError("current57_explicit_g12_smoke_arguments_required")
    return args


def _admit_archive(args, archive_bytes=None):
    if archive_bytes is None:
        if (
            args.archive.is_symlink()
            or not args.archive.is_file()
            or not 0 < args.archive.stat().st_size <= 128 * 1024 * 1024
        ):
            raise ValueError("current57_smoke_archive_unavailable")
        archive_bytes = args.archive.read_bytes()
    if (
        type(archive_bytes) is not bytes
        or not 0 < len(archive_bytes) <= 128 * 1024 * 1024
        or contract.digest(archive_bytes) != args.archive_sha256
    ):
        raise ValueError("current57_smoke_archive_identity_changed")
    with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:") as archive:
        if (
            archive.pax_headers.get("comment") != args.commit
            or shared.archive_tree(archive) != args.tree
        ):
            raise ValueError("current57_smoke_git_identity_changed")
        files = shared.archive_files(archive)
    if any(Path(name).name == ".env" for name in files):
        raise ValueError("current57_smoke_private_env_in_archive")
    for path in (
        Path(__file__),
        *(Path(__file__).with_name(name) for name in _HELPERS),
    ):
        if (
            path.is_symlink()
            or contract.digest(path.read_bytes())
            != files["scripts/" + path.name]["sha256"]
        ):
            raise ValueError("current57_smoke_runner_or_helper_different_from_archive")
    contract.migration_digest(files)
    return archive_bytes, files


def main(*, archive_bytes=None):
    args = _arguments()
    raw, files = _admit_archive(args, archive_bytes)
    args.output.mkdir(parents=True, exist_ok=False)
    root = args.output.resolve(strict=True)
    with _isolated_client(root):
        return _run(args, raw, files, root)


def _run(args, raw, files, root):
    test_results = root / "test-results"
    test_results.mkdir(mode=0o777)
    test_results.chmod(0o777)  # Disposable synthetic evidence only.
    source_map = root / "source-files.json"
    source_map.write_bytes(contract.canonical(files))
    source_map.chmod(0o444)
    run_id = uuid4().hex[:12]
    prefix = "ctcc-final-" + run_id + "-"
    label = "org.ctcc.validation.run=" + run_id
    network, postgres, redis = (
        prefix + name for name in ("network", "postgres", "redis")
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
    source_mount = (
        "--mount",
        f"type=bind,src={source_map},dst=/ctcc-source-files.json,readonly",
    )
    identity_mount = (
        "--mount",
        f"type=bind,src={root / 'identity.json'},dst=/ctcc-run-identity.json,readonly",
    )
    test_mount = ("--mount", f"type=bind,src={test_results},dst=/validation-results")
    identity = {
        "runner_contract": contract.RUNNER_CONTRACT,
        "validation_scope": contract.SCOPE,
        "source_contract_head": "0057",
        "commit": args.commit,
        "tree": args.tree,
        "archive_sha256": args.archive_sha256,
        "runner_sha256": files[contract.RUNNER_PATH]["sha256"],
        "build_context": "admitted_archive_bytes_v1",
        "source_files_sha256": contract.digest(source_map.read_bytes()),
        "manifest_sha256": files["MANIFEST.sha256"]["sha256"],
        "run_id": run_id,
        "host_credentials": False,
        "host_env": False,
        "external_order_writes": 0,
        "trading_acceptance": False,
        "component_validation": "NOT_RUN",
        "hermetic_regression": "NOT_RUN",
        "smoke_validation": "FAIL",
    }
    _write(root / "identity.json", identity)
    run = shared.Run(root, budget_seconds=2400)
    resources = []
    tls_resources = ExitStack()

    def command(stage, *arguments, **options):
        run.command(stage, ["docker", *arguments], **options)

    def container(stage, *arguments, mounts=(), extra_environment=(), timeout=300):
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
            *arguments,
            timeout=timeout,
        )

    def parent(stage):
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
            "SELECT version_num FROM public.alembic_version",
        )
        if (root / (stage + ".log")).read_bytes().strip() != b"0056":
            raise ValueError("current57_smoke_parent_head_changed")

    try:
        command(
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
            raise ValueError("current57_smoke_internal_network_required")
        tls = tls_resources.enter_context(
            devval_tls(True, postgres, disable_autovacuum=True)
        )
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
        command(
            "postgres-ready", "exec", postgres, "sh", "-ec", _PG_TLS_READY, timeout=120
        )
        command("redis-ready", "exec", redis, "sh", "-ec", _REDIS_READY, timeout=90)
        container("copied-manifest", "python", "scripts/manifest.py", "--check")
        container(
            "copied-source-exact",
            "python",
            "-c",
            _COPIED_SOURCE,
            identity["source_files_sha256"],
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
        container("migration-upgrade-parent", "alembic", "upgrade", "0056")
        parent("migration-parent-readback")
        container("migration-upgrade", "alembic", "upgrade", "head")
        container(
            "migration-identity", "python", "-m", "scripts.verify_migration_identity"
        )
        identity["migration"] = contract.migration_readback(
            (root / "migration-identity.log").read_bytes(),
            contract.migration_digest(files),
        )
        container("migration-empty-downgrade", "alembic", "downgrade", "0056")
        parent("migration-empty-downgrade-readback")
        container("migration-empty-reupgrade", "alembic", "upgrade", "head")
        container(
            "migration-empty-reupgrade-identity",
            "python",
            "-m",
            "scripts.verify_migration_identity",
        )
        contract.migration_readback(
            (root / "migration-empty-reupgrade-identity.log").read_bytes(),
            contract.migration_digest(files),
        )
        container("migration-drift", "alembic", "check")
        container(
            "g12-successor-smoke",
            *_PYTEST,
            "--maxfail=1",
            f"--junitxml=/validation-results/{Path(contract.JUNIT).name}",
            *_cases_to_nodes(contract.CASES),
            mounts=(*test_mount, *source_mount, *identity_mount, *tls.pytest_mount),
            extra_environment=(
                *tls.pytest_environment,
                "-e",
                measurement.ENV + "=/validation-results",
            ),
            timeout=900,
        )
        identity["tests"] = shared.verify_pytest_report(
            root / contract.JUNIT, required_cases=contract.CASES
        )
        if identity["tests"]["tests"] != 4 or identity["tests"]["passed"] != 4:
            raise ValueError("current57_smoke_exact_four_cases_required")
        identity["smoke_validation"] = "PASS"
    except Exception as error:
        identity["failure_type"] = type(error).__name__
        identity["smoke_validation"] = "FAIL"
        raise
    finally:
        try:
            cleanup = shared.cleanup_resources(resources, run_id)
        finally:
            tls_resources.close()
        _write(root / "cleanup.json", cleanup)
        identity["cleanup"] = (
            "FAIL" if any(row["status"] == "FAIL" for row in cleanup) else "PASS"
        )
        if identity["cleanup"] != "PASS":
            identity["smoke_validation"] = "FAIL"
        _write(root / "identity.json", identity)
        hashes = {
            name: contract.digest((root / name).read_bytes())
            for name in sorted(contract.artifacts(run.steps))
            if (root / name).is_file()
        }
        _write(root / "results.sha256.json", hashes)
    try:
        readback = contract.verify_smoke(root, raw, commit=args.commit, tree=args.tree)
    except Exception as error:
        identity["failure_type"] = type(error).__name__
        identity["smoke_validation"] = "FAIL"
        _write(root / "identity.json", identity)
        hashes["identity.json"] = contract.digest((root / "identity.json").read_bytes())
        _write(root / "results.sha256.json", hashes)
        raise
    _write(root / "smoke-readback.json", readback)
    _write(
        root / "readback.sha256.json",
        {
            "smoke-readback.json": contract.digest(
                (root / "smoke-readback.json").read_bytes()
            )
        },
    )
    print("DB0057_G12_SMOKE=PASS", flush=True)
    print("HERMETIC_REGRESSION=NOT_RUN", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
