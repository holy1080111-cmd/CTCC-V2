"""Two fresh source17 catalog references, never full regression acceptance."""

from __future__ import annotations

import argparse
import re
from contextlib import ExitStack
from pathlib import Path
from uuid import uuid4

from scripts import retained0017_reference_measurement_v1 as measurement
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
from scripts.verify_current0058_full_hermetic import admit_archive as admit_full_source

RUNNER = measurement.PROBE_RUNNER
TEST_MODULE = "tests/integration/test_retained0017_catalog_references_v1_pg.py"
REPORT = "test-results/retained0017-reference-probe.xml"
canonical, digest = measurement.canonical, measurement.digest


def need(value):
    if value is not True:
        raise ValueError("retained0017_reference_probe_refused") from None


def admit_archive(args):
    raw, files = admit_full_source(args)
    need(digest(Path(__file__).read_bytes()) == files[RUNNER]["sha256"])
    return raw, files


def verify_probe(root, *, args, files):
    state = read_evidence_json((root / "identity.json").read_bytes())
    raw_map = (root / "source-files.json").read_bytes()
    need(raw_map == canonical(files))
    need(
        state["commit"] == args.commit
        and state["tree"] == args.tree
        and state["archive_sha256"] == args.archive_sha256
    )
    source = measurement.source_identity(state, files, raw_map)
    report = shared.verify_pytest_report(
        root / REPORT, required_cases=measurement.CASES, allow_unrelated_skips=False
    )
    need(report["tests"] == report["passed"] == 2 and report["skipped"] == 0)
    pair = measurement.verify_reference_pair(root, source=source)
    need(
        state["pair"] == pair
        and state["tests"] == report
        and state["probe"] == state["cleanup"] == "PASS"
    )
    results = read_evidence_json((root / "results.sha256.json").read_bytes())
    actual = {
        path.relative_to(root).as_posix(): digest(path.read_bytes())
        for path in root.rglob("*")
        if path.is_file()
        and not path.is_symlink()
        and "docker-client" not in path.parts
        and path.name != "results.sha256.json"
    }
    need(results == actual)
    return pair


def run_probe(args, raw, files, root):
    results = root / "test-results"
    results.mkdir(mode=0o777)
    results.chmod(0o777)  # Only disposable, credential-free reference artifacts.
    mapping = root / "source-files.json"
    mapping.write_bytes(canonical(files))
    mapping.chmod(0o444)
    run_id = uuid4().hex[:12]
    prefix = "ctcc-final-" + run_id + "-"
    network, postgres, redis = (
        prefix + name for name in ("network", "postgres", "redis")
    )
    image = "ctcc-source17-reference:" + args.commit[:12] + "-" + run_id
    label = "org.ctcc.validation.run=" + run_id
    state = {
        "schema": "ctcc.retained0017.reference_probe_identity.v1",
        "runner_contract": measurement.PROBE_CONTRACT,
        "validation_scope": measurement.PROBE_SCOPE,
        "suite": "retained0017-reference-probe",
        "source_contract_head": "0058",
        "database_target": "0017",
        "commit": args.commit,
        "tree": args.tree,
        "archive_sha256": digest(raw),
        "source_files_sha256": digest(mapping.read_bytes()),
        "manifest_sha256": files["MANIFEST.sha256"]["sha256"],
        "runner_sha256": files[RUNNER]["sha256"],
        "build_context": "admitted_archive_bytes_v1",
        "run_id": run_id,
        "probe": "FAIL",
        "hermetic_regression": "NOT_RUN",
        "full_suite_acceptance": False,
        "trading_acceptance": False,
        "host_env": False,
        "host_credentials": False,
        "external_order_writes": 0,
    }
    _write(root / "identity.json", state)
    run, resources, tls_stack = shared.Run(root, budget_seconds=1800), [], ExitStack()

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
    identity_mount = (
        "--mount",
        f"type=bind,src={root / 'identity.json'},dst=/ctcc-run-identity.json,readonly",
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

    try:
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
        docker("image-identity", "image", "inspect", "--format", "{{json .}}", image)
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
            "network-inspect", "network", "inspect", "--format", "{{json .}}", network
        )
        need(
            read_evidence_json((root / "network-inspect.log").read_bytes())["Internal"]
            is True
        )
        tls = tls_stack.enter_context(
            devval_tls(True, postgres, disable_autovacuum=True)
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
            "postgres-ready", "exec", postgres, "sh", "-ec", _PG_TLS_READY, timeout=120
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
        # No upgrade of the admin database to58 is implied. Each required case
        # independently creates its own template0 DB and genuine0017 prefix.
        container(
            "reference17-tests",
            *_PYTEST,
            "-q",
            "--junitxml=/validation-results/" + Path(REPORT).name,
            TEST_MODULE,
            mounts=(
                "--mount",
                f"type=bind,src={results},dst=/validation-results",
                *source_mount,
                *identity_mount,
                *tls.pytest_mount,
            ),
            extra_environment=(
                *tls.pytest_environment,
                "-e",
                measurement.ENV + "=/validation-results",
            ),
            timeout=900,
        )
        report = shared.verify_pytest_report(
            root / REPORT, required_cases=measurement.CASES, allow_unrelated_skips=False
        )
        need(report["tests"] == report["passed"] == 2 and report["skipped"] == 0)
        state["tests"] = report
        state["pair"] = measurement.verify_reference_pair(
            root, source=measurement.source_identity(state, files, mapping.read_bytes())
        )
        state["probe"] = "PASS"
    except Exception as error:
        state["failure_type"] = type(error).__name__
        raise
    finally:
        cleanup = shared.cleanup_resources(resources, run_id)
        _write(root / "cleanup.json", cleanup)
        tls_stack.close()
        state["cleanup"] = (
            "PASS" if all(row["status"] != "FAIL" for row in cleanup) else "FAIL"
        )
        if state["cleanup"] != "PASS":
            state["probe"] = "FAIL"
        _write(root / "identity.json", state)
        _write(
            root / "results.sha256.json",
            {
                path.relative_to(root).as_posix(): digest(path.read_bytes())
                for path in root.rglob("*")
                if path.is_file()
                and not path.is_symlink()
                and "docker-client" not in path.parts
                and path.name != "results.sha256.json"
            },
        )
    verify_probe(root, args=args, files=files)
    print("RETAINED0017_FRESH_REFERENCE_PAIR=PASS", flush=True)
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
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    for value, pattern in (
        (args.commit, r"[a-f0-9]{40}"),
        (args.tree, r"[a-f0-9]{40}"),
        (args.archive_sha256, r"[a-f0-9]{64}"),
    ):
        need(type(value) is str and re.fullmatch(pattern, value, re.ASCII) is not None)
    raw, files = admit_archive(args)
    if args.verify_only:
        verify_probe(args.output, args=args, files=files)
        print("RETAINED0017_REFERENCE_PROBE_ARCHIVED_READBACK=PASS", flush=True)
        return 0
    args.output.mkdir(parents=True, exist_ok=False)
    root = args.output.resolve(strict=True)
    with _isolated_client(root):
        return run_probe(args, raw, files, root)


if __name__ == "__main__":
    raise SystemExit(main())
