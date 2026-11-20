"""Closed-profile exact-source Docker runner; no host credentials or venue writes."""

from __future__ import annotations

import json
from contextlib import ExitStack
from enum import Enum
from pathlib import Path
from uuid import uuid4

from scripts import verify_final_hermetic as shared
from scripts.devval_hermetic_tls import devval_tls
from scripts.evidence_json import read_evidence_json
from scripts.verify_current0055_hermetic import (
    _COPIED_SOURCE,
    _POSTGRES,
    _PYTEST,
    _REDIS,
    _REDIS_READY,
    _RESERVATION_UNIQUE_QUERY,
    _cases_to_nodes,
    _isolated_client,
    _write,
)
from scripts.verify_current0056_hermetic import _PG_TLS_READY


class HermeticProfileV1(Enum):
    CURRENT0057 = "0057"


def contract_for(profile):
    if profile is not HermeticProfileV1.CURRENT0057:
        raise ValueError("hermetic_closed_profile_required") from None
    from scripts import current0057_full_hermetic_contract_v1

    return current0057_full_hermetic_contract_v1


def test_command(contract, mode, index):
    junit, selected = contract.component_report_paths(mode, index)
    return (
        *_PYTEST,
        "-p",
        "scripts.current0057_collection_v1",
        "--ctcc-current57-suite=" + mode,
        *(("--ctcc-current57-index=" + str(index),) if index is not None else ()),
        "--ctcc-current57-collection=/validation-results/" + Path(selected).name,
        *(("--ctcc-current57-progress",) if mode == "postgres" else ()),
        "--junitxml=/validation-results/" + Path(junit).name,
        "tests",
    )


def run_component(profile, args, raw, files):
    contract = contract_for(profile)
    index = (
        args.postgres_component_index if args.suite == "postgres" else args.shard_index
    )
    contract.collection._mode(args.suite, index)
    args.output.mkdir(parents=True, exist_ok=False)
    root = args.output.resolve(strict=True)
    with _isolated_client(root):
        return _run(contract, args, index, raw, files, root)


def _run(contract, args, index, raw, files, root):
    test_results = root / "test-results"
    test_results.mkdir(mode=0o777)
    test_results.chmod(0o777)  # Only synthetic disposable evidence is writable.
    source_map = root / "source-files.json"
    source_map.write_bytes(contract.canonical(files))
    source_map.chmod(0o444)
    retained = root / "admitted-test-source.tar"
    with retained.open("xb") as output:
        output.write(raw)
    retained.chmod(0o444)
    run_id = uuid4().hex[:12]
    prefix, label = "ctcc-final-" + run_id + "-", "org.ctcc.validation.run=" + run_id
    network, postgres, redis, api = (
        prefix + name for name in ("network", "postgres", "redis", "api")
    )
    image = "ctcc-final-validation:" + args.commit[:12] + "-" + run_id
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
    identity_mount = (
        "--mount",
        f"type=bind,src={root / 'identity.json'},dst=/ctcc-run-identity.json,readonly",
    )
    archive_mount = (
        "--mount",
        f"type=bind,src={retained},dst=/ctcc-admitted-test-source.tar,readonly",
    )
    identity = {
        "runner_contract": contract.RUNNER_CONTRACT,
        "source_contract_head": contract.HEAD,
        "validation_scope": contract.SCOPE,
        "commit": args.commit,
        "tree": args.tree,
        "archive_sha256": args.archive_sha256,
        "build_context": "admitted_archive_bytes_v1",
        "runner_sha256": files[contract.RUNNER_PATH]["sha256"],
        "source_files_sha256": contract.digest(source_map.read_bytes()),
        "manifest_sha256": files["MANIFEST.sha256"]["sha256"],
        "run_id": run_id,
        "suite": args.suite,
        "shard_index": args.shard_index,
        "postgres_component_index": args.postgres_component_index,
        "host_credentials": False,
        "host_env": False,
        "external_order_writes": 0,
        "trading_acceptance": False,
        "component_validation": "FAIL",
        "hermetic_regression": "FAIL",
    }
    _write(root / "identity.json", identity)
    run = shared.Run(root)
    resources, tls_resources = [], ExitStack()
    anchor = contract.component_is_anchor(args.suite, index)

    def command(stage, *arguments, **options):
        run.command(stage, ["docker", *arguments], **options)

    def container(stage, *arguments, mounts=(), extra_environment=(), timeout=3600):
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
        if (root / (stage + ".log")).read_bytes().strip() != contract.PARENT.encode(
            "ascii"
        ):
            raise ValueError("hermetic_actual_parent_head_changed")

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
            raise ValueError("hermetic_internal_network_required")
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
        command("redis-ready", "exec", redis, "sh", "-ec", _REDIS_READY, timeout=120)
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
            parent("migration-parent-readback")
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
            parent("migration-empty-downgrade-readback")
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
                raise ValueError("hermetic_reupgrade_head_changed")
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
        if contract.previous.requires_root_trust(args.suite, index):
            stage = contract.ROOT_TRUST_STAGE
            resources.append(("container", prefix + stage))
            command(
                stage,
                "run",
                "--rm",
                "--name",
                prefix + stage,
                "--label",
                label,
                "--network=none",
                "--user=0:0",
                "--read-only",
                "--tmpfs=/tmp:rw,nosuid,nodev,mode=1777",
                "--security-opt=no-new-privileges:true",
                "--cap-drop=ALL",
                "--cap-add=SYS_CHROOT",
                "--cap-add=SETUID",
                "--cap-add=SETGID",
                "--pids-limit=64",
                *test_mount,
                identity["image_id"],
                "sh",
                "-ec",
                'umask 022; exec "$@"',
                stage,
                *_PYTEST,
                "--junitxml=/validation-results/"
                + Path(contract.ROOT_TRUST_REPORT).name,
                *_cases_to_nodes(contract.previous.root_trust_cases(args.suite, index)),
                timeout=120,
            )
            contract.previous.verify_root_trust(root, args.suite, index)
        from scripts.current0057_g12_measurement_v1 import ENV
        from scripts.devval_campaign_physical_catalog_v1 import HERMETIC_CATALOG_ENV

        extra = (
            "-e",
            "CTCC_RUN_B3_PREFIX_33=1",
            "-e",
            "CTCC_B3_SCALE_RECEIPT_DIR=/validation-results",
            *tls.pytest_environment,
            "-e",
            "CTCC_DEVVAL_CUSTODY_TEST_SOURCE_MANIFEST_SHA256="
            + identity["manifest_sha256"],
            *(
                ("-e", HERMETIC_CATALOG_ENV + "=1")
                if contract.requires_historical(args.suite, index)
                else ()
            ),
            *(
                ("-e", ENV + "=/validation-results")
                if contract.requires_g12(args.suite, index)
                else ()
            ),
        )
        container(
            contract.component_test_stage(args.suite, index),
            *test_command(contract, args.suite, index),
            mounts=(
                *test_mount,
                *source_mount,
                *identity_mount,
                *archive_mount,
                *tls.pytest_mount,
            ),
            extra_environment=extra,
            timeout=shared.POSTGRES_TEST_TIMEOUT_SECONDS
            if args.suite == "postgres"
            else shared.FULL_TEST_TIMEOUT_SECONDS,
        )
        junit, _ = contract.component_report_paths(args.suite, index)
        identity["tests"] = shared.verify_pytest_report(
            root / junit,
            required_cases=contract.required_cases(args.suite, index),
            allow_unrelated_skips=args.suite != "postgres",
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
            command(
                "postgres-ready-after-restart",
                "exec",
                postgres,
                "sh",
                "-ec",
                _PG_TLS_READY,
                timeout=120,
            )
            command("redis-restart", "restart", "--time", "10", redis, timeout=90)
            command(
                "redis-ready-after-restart",
                "exec",
                redis,
                "sh",
                "-ec",
                _REDIS_READY,
                timeout=120,
            )
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
            "FAIL" if any(row["status"] == "FAIL" for row in cleanup) else "PASS"
        )
        if identity["cleanup"] == "FAIL":
            identity.update(component_validation="FAIL", hermetic_regression="FAIL")
        _write(root / "identity.json", identity)
        hashes = {
            name: contract.digest((root / name).read_bytes())
            for name in sorted(contract.component_artifacts(args.suite, index))
            if (root / name).is_file()
        }
        _write(root / "results.sha256.json", hashes)
    try:
        contract.verify_component(
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
    print(
        json.dumps(
            {
                key: identity[key]
                for key in (
                    "commit",
                    "tree",
                    "source_contract_head",
                    "suite",
                    "component_validation",
                    "hermetic_regression",
                )
            }
        )
    )
    return 0
