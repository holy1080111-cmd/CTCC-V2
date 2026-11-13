"""Exact Git-archive Docker acceptance for the installed DB0056 contract."""

import argparse
import io
import json
import re
import tarfile
from contextlib import ExitStack
from pathlib import Path
from uuid import uuid4

from scripts import current0056_fitted_catalog_measurement_v1 as fitted_measurement
from scripts import current0056_hermetic_contract as contract
from scripts import verify_final_hermetic as shared
from scripts.current0055_catalog_receipt import verify_current55_catalog_report
from scripts.devval_campaign_physical_catalog_v1 import HERMETIC_CATALOG_ENV
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
from scripts.verify_linux_shard import POSTGRES_PROGRESS_OPTION

_EXACT_HOST_HELPERS = (
    "current0055_catalog_receipt.py",
    "current0055_hermetic_contract.py",
    "current0056_hermetic_contract.py",
    "current0056_fitted_delta_catalog_v1.py",
    "current0056_fitted_catalog_measurement_v1.py",
    "current0056_fitted_catalog_pair_v1.py",
    "current0056_migration_pin.py",
    "devval_campaign_physical_catalog_v1.py",
    "devval_hermetic_tls.py",
    "evidence_json.py",
    "verify_current0055_hermetic.py",
    "verify_final_hermetic.py",
    "verify_linux_shard.py",
)
_PARENT_HEAD_SQL = "SELECT version_num FROM public.alembic_version"
_FITTED_JUNIT = "test-results/fitted-catalog-diagnostic.xml"
PREDICTION_DIAGNOSTIC_CASE = (
    "tests.integration.test_devval_prediction_custody_diagnostic_pg_v1",
    "test_native_tls_validation_prediction_schedule_slot_and_independent_ack",
)
_FITTED_CASES = (
    contract.devval_catalog.HERMETIC_CATALOG_CASE,
    fitted_measurement.CASE,
    fitted_measurement.INHERITED_CASE,
    (
        fitted_measurement.CASE[0],
        "test_actual0056_full_native_role_rejects_swapped_roles_and_extra_grant",
    ),
    (
        fitted_measurement.CASE[0],
        "test_actual0056_between_snapshot_grant_denies_fresh_writer_recheck",
    ),
    (
        fitted_measurement.CASE[0],
        "test_actual0056_native_lifespan_denies_before_recovery_cas",
    ),
    (
        fitted_measurement.CASE[0],
        "test_actual0056_owner_grant_lock_and_rollback",
    ),
    (
        fitted_measurement.CASE[0],
        "test_actual0056_restricted_account_control_read_replays_exact_scope",
    ),
    (
        "tests.integration.test_qualification_submission_intent_repository",
        "test_uncertain_and_expired_intent_read_is_audit_only_and_hold_remains",
    ),
    contract.PREDICTION_CUSTODY_CASE,
    PREDICTION_DIAGNOSTIC_CASE,
)


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
    parser.add_argument("--fitted-catalog-diagnostic", action="store_true")
    args = parser.parse_args()
    if (
        (args.shard_index is not None and args.suite != "shard")
        or (args.postgres_component_index is not None and args.suite != "postgres")
        or (
            args.fitted_catalog_diagnostic
            and (args.suite != "postgres" or args.postgres_component_index != 1)
        )
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
    diagnostic = args.fitted_catalog_diagnostic
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
    identity_mount = (
        "--mount",
        f"type=bind,src={root / 'identity.json'},dst=/ctcc-run-identity.json,readonly",
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
    if diagnostic:
        identity["validation_scope"] = "fitted_catalog_diagnostic_only"
    _write(root / "identity.json", identity)
    run = shared.Run(root)
    resources = []
    anchor = contract.component_is_anchor(args.suite, index)
    needs_devval_tls = contract.requires_devval_catalog(args.suite, index)
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
        if diagnostic:
            stage, junit = "fitted-catalog-diagnostic", _FITTED_JUNIT
            test_command = (
                *_PYTEST,
                f"--junitxml=/validation-results/{Path(junit).name}",
                *_cases_to_nodes(_FITTED_CASES),
            )
        else:
            stage = contract.component_test_stage(args.suite, index)
            junit, _ = contract.component_report_paths(args.suite, index)
            test_command = _test_command(args.suite, index)
        container(
            stage,
            *test_command,
            mounts=(
                *test_mount,
                *(source_mount if needs_devval_tls else ()),
                *(identity_mount if needs_devval_tls else ()),
                *(tls.pytest_mount if tls else ()),
            ),
            extra_environment=(
                "-e",
                "CTCC_RUN_B3_PREFIX_33=1",
                "-e",
                "CTCC_B3_SCALE_RECEIPT_DIR=/validation-results",
                *(tls.pytest_environment if tls else ()),
                *(("-e", f"{HERMETIC_CATALOG_ENV}=1") if needs_devval_tls else ()),
            ),
            timeout=(
                shared.POSTGRES_TEST_TIMEOUT_SECONDS
                if args.suite == "postgres" and not diagnostic
                else shared.FULL_TEST_TIMEOUT_SECONDS
            ),
        )
        identity["tests"] = shared.verify_pytest_report(
            root / junit,
            required_cases=_FITTED_CASES if diagnostic else (),
            allow_unrelated_skips=not diagnostic,
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
        identity["component_validation"] = "NOT_RUN" if diagnostic else "PASS"
        identity["hermetic_regression"] = (
            "NOT_RUN"
            if diagnostic
            else ("PASS" if args.suite == "full" else "PENDING_UNION")
        )
    except Exception as error:
        identity.update(
            component_validation="NOT_RUN" if diagnostic else "FAIL",
            hermetic_regression="NOT_RUN" if diagnostic else "FAIL",
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
        if identity["cleanup"] == "FAIL" and not diagnostic:
            identity.update(component_validation="FAIL", hermetic_regression="FAIL")
        _write(root / "identity.json", identity)
        expected = (
            _diagnostic_artifacts(run.steps)
            if diagnostic
            else contract.component_artifacts(args.suite, index)
        )
        hashes = {
            name: contract.digest((root / name).read_bytes())
            for name in sorted(expected)
            if (root / name).is_file()
        }
        _write(root / "results.sha256.json", hashes)
    try:
        if diagnostic:
            verify_fitted_catalog_diagnostic(root, raw, files)
        else:
            contract.verify_current56_component(
                root,
                raw,
                commit=args.commit,
                tree=args.tree,
                manifest_sha256=identity["manifest_sha256"],
            )
    except Exception as error:
        identity.update(
            component_validation="NOT_RUN" if diagnostic else "FAIL",
            hermetic_regression="NOT_RUN" if diagnostic else "FAIL",
            failure_type=type(error).__name__,
        )
        _write(root / "identity.json", identity)
        hashes["identity.json"] = contract.digest((root / "identity.json").read_bytes())
        _write(root / "results.sha256.json", hashes)
        raise
    return 0


def _diagnostic_artifacts(steps):
    return {
        "identity.json",
        "source-files.json",
        "steps.json",
        "cleanup.json",
        _FITTED_JUNIT,
        f"test-results/{contract.devval_catalog.HERMETIC_CATALOG_FILE}",
        f"test-results/{fitted_measurement.FILE}",
        f"test-results/{fitted_measurement.INHERITED_FILE}",
        *(f"{step['name']}.log" for step in steps),
    }


def verify_fitted_catalog_diagnostic(root, raw, files):
    identity = read_evidence_json(contract._read(root, "identity.json"))
    steps = read_evidence_json(contract._read(root, "steps.json"))
    hashes = read_evidence_json(contract._read(root, "results.sha256.json"))
    stage = "fitted-catalog-diagnostic"
    required = (*contract.component_stage_names("postgres", 1)[:-1], stage)
    if (
        identity.get("validation_scope") != "fitted_catalog_diagnostic_only"
        or identity.get("suite") != "postgres"
        or identity.get("postgres_component_index") != 1
        or identity.get("shard_index") is not None
        or identity.get("component_validation") != "NOT_RUN"
        or identity.get("hermetic_regression") != "NOT_RUN"
        or identity.get("cleanup") != "PASS"
        or "failure_type" in identity
        or identity.get("host_credentials") is not False
        or identity.get("host_env") is not False
        or identity.get("trading_acceptance") is not False
        or type(identity.get("external_order_writes")) is not int
        or identity["external_order_writes"] != 0
        or contract.digest(raw) != identity["archive_sha256"]
        or tuple(step["name"] for step in steps) != required
        or any(
            type(step["exit_code"]) is not int
            or step["exit_code"] != 0
            or "failure_type" in step
            or "policy_refusal" in step
            for step in steps
        )
        or set(hashes) != _diagnostic_artifacts(steps)
        or any(
            contract.digest(contract._read(root, name)) != digest
            for name, digest in hashes.items()
        )
    ):
        raise ValueError("fitted_catalog_diagnostic_identity_invalid")
    mapping = contract._read(root, "source-files.json")
    if (
        read_evidence_json(mapping) != files
        or contract.digest(mapping) != identity["source_files_sha256"]
    ):
        raise ValueError("fitted_catalog_diagnostic_source_changed")
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
        if (
            archive.pax_headers.get("comment") != identity["commit"]
            or shared.archive_tree(archive) != identity["tree"]
            or shared.archive_files(archive) != files
        ):
            raise ValueError("fitted_catalog_diagnostic_source_changed")
    network = read_evidence_json(contract._read(root, "network-inspect.log"))
    image = read_evidence_json(contract._read(root, "image-identity.log"))
    lock = read_evidence_json(contract._read(root, "dependency-lock.log"))
    run_id = identity["run_id"]
    prefix = f"ctcc-final-{run_id}-"
    if (
        network["Name"] != prefix + "network"
        or network["Internal"] is not True
        or network["Containers"] not in ({}, None)
        or network["Labels"]["org.ctcc.validation.run"] != run_id
        or image["Id"] != identity["image_id"]
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", image["Id"])
        or image["Config"]["Labels"]["org.opencontainers.image.revision"]
        != identity["commit"]
        or image["Config"]["Labels"]["org.ctcc.source.tree"] != identity["tree"]
        or lock["target"] != "linux"
        or lock["lock_sha256"]
        != files["requirements/validation-linux-py312.lock"]["sha256"]
        or lock["accepted_full_validation_baseline"] is not False
        or contract._read(root, "copied-source-exact.log").strip()
        != str(len(files)).encode()
        or b"manifest check passed:" not in contract._read(root, "copied-manifest.log")
        or contract._read(root, "redis-ready.log").strip() != b"PONG"
        or b"accepting connections" not in contract._read(root, "postgres-ready.log")
        or contract._read(root, "migration-reservation-unique.log").strip()
        != contract.RESERVATION_UNIQUE
        or b"No new upgrade operations detected"
        not in contract._read(root, "migration-drift.log")
        or contract._read(root, stage + ".log")
        .splitlines()
        .count(b"HERMETIC_EXECUTION_AUTHORITY=0")
        != 1
        or contract.migration_readback(
            contract._read(root, "migration-identity.log"),
            contract.migration_digest(files),
        )
        != identity["migration"]
    ):
        raise ValueError("fitted_catalog_diagnostic_runtime_changed")
    contract._cleanup(contract._read(root, "cleanup.json"), prefix, anchor=False)
    junit = shared.verify_pytest_report(
        root / _FITTED_JUNIT, required_cases=_FITTED_CASES
    )
    if junit != identity["tests"] or junit["passed"] != len(_FITTED_CASES):
        raise ValueError("fitted_catalog_diagnostic_test_scope_changed")
    contract.devval_catalog.verify_hermetic_linux_catalogs(
        contract._read(
            root,
            f"test-results/{contract.devval_catalog.HERMETIC_CATALOG_FILE}",
            maximum=contract.devval_catalog.MAX_HERMETIC_CATALOG_BYTES,
        )
    )
    return contract.verify_fitted_and_inherited_catalogs(root, identity, files)


if __name__ == "__main__":
    raise SystemExit(main())
