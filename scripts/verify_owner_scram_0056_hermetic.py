"""Exact-source, isolated SCRAM diagnostic for DB0056 owner route preflight.

This is a hosted lab only. It has no production credential, migration actor,
API startup, account source, or trading authority. Its Docker network is
internal and the generated password never enters argv, environment, source,
container image, or the result bundle.
"""

from __future__ import annotations

import argparse
import io
import json
import re
import tarfile
from contextlib import ExitStack
from pathlib import Path
from uuid import uuid4

from scripts import current0056_hermetic_contract as contract
from scripts import verify_current0056_hermetic as current56
from scripts import verify_final_hermetic as shared
from scripts.devval_hermetic_tls import devval_tls
from scripts.devval_owner_scram_secret_v1 import owner_scram_lab_secret_v1
from scripts.evidence_json import read_evidence_json

_CASE = (
    "tests.integration.test_native_owner_scram_lab_pg_v1",
    "test_actual0056_scram_owner_good_and_wrong_password_route_preflight",
)
_JUNIT = "test-results/owner-scram.xml"
_EVIDENCE = "test-results/owner-scram-lab.json"
_READY = (
    "for n in $(seq 1 90); do "
    "pg_isready -h 127.0.0.1 -U ctcc -d ctcc >/dev/null 2>&1 && "
    "{ echo accepting; exit 0; }; sleep 1; done; exit 1"
)
_STAGES = (
    "build",
    "image-identity",
    "network-create",
    "network-inspect",
    "postgres-start",
    "postgres-ready",
    "copied-manifest",
    "copied-source-exact",
    "dependency-lock",
    "owner-scram-test",
)


def _arguments(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--archive-sha256", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--tree", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--owner-scram-lab-only", action="store_true")
    args = parser.parse_args(argv)
    if (
        not args.owner_scram_lab_only
        or re.fullmatch(r"[0-9a-f]{40}", args.commit) is None
        or re.fullmatch(r"[0-9a-f]{40}", args.tree) is None
        or re.fullmatch(r"[0-9a-f]{64}", args.archive_sha256) is None
    ):
        raise ValueError("owner_scram_lab_arguments_invalid")
    return args


def _admit(args, archive_bytes=None):
    raw, files, _ = current56._admit_archive(args, archive_bytes)
    for name in (
        "verify_owner_scram_0056_hermetic.py",
        "devval_owner_scram_secret_v1.py",
    ):
        source = Path(__file__).with_name(name)
        if contract.digest(source.read_bytes()) != files[f"scripts/{name}"]["sha256"]:
            raise ValueError("owner_scram_lab_host_source_changed")
    return raw, files


def _write(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


def _verify(root: Path, raw: bytes, files: dict, identity: dict) -> None:
    steps = read_evidence_json((root / "steps.json").read_bytes())
    cleanup = read_evidence_json((root / "cleanup.json").read_bytes())
    hashes = read_evidence_json((root / "results.sha256.json").read_bytes())
    expected_artifacts = {
        "identity.json",
        "source-files.json",
        "steps.json",
        "cleanup.json",
        _JUNIT,
        _EVIDENCE,
        *(f"{stage}.log" for stage in _STAGES),
    }
    if (
        identity.get("validation_scope") != "owner_scram_lab_diagnostic_only"
        or identity.get("execution_authority") is not False
        or identity.get("startup_admission") is not False
        or identity.get("host_user_credentials") is not False
        or identity.get("synthetic_password_file_only") is not True
        or identity.get("external_order_writes") != 0
        or identity.get("manifest_sha256") != files["MANIFEST.sha256"]["sha256"]
        or identity.get("cleanup") != "PASS"
        or identity.get("result") != "PASS"
        or tuple(step["name"] for step in steps) != _STAGES
        or any(step["exit_code"] != 0 or "failure_type" in step for step in steps)
        or any(item["status"] not in ("REMOVED", "ABSENT") for item in cleanup)
        or set(hashes) != expected_artifacts
        or any(
            contract.digest((root / name).read_bytes()) != digest
            for name, digest in hashes.items()
        )
        or contract.digest(raw) != identity["archive_sha256"]
    ):
        raise ValueError("owner_scram_lab_artifacts_invalid")
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
        if (
            archive.pax_headers.get("comment") != identity["commit"]
            or shared.archive_tree(archive) != identity["tree"]
            or shared.archive_files(archive) != files
        ):
            raise ValueError("owner_scram_lab_archive_changed")
    image = read_evidence_json((root / "image-identity.log").read_bytes())
    network = read_evidence_json((root / "network-inspect.log").read_bytes())
    lock = read_evidence_json((root / "dependency-lock.log").read_bytes())
    if (
        image["Id"] != identity["image_id"]
        or image["Config"]["Labels"]["org.opencontainers.image.revision"]
        != identity["commit"]
        or image["Config"]["Labels"]["org.ctcc.source.tree"] != identity["tree"]
        or re.fullmatch(r"sha256:[0-9a-f]{64}", image["Id"]) is None
        or network["Internal"] is not True
        or network["Name"] != identity["network"]
        or network["Labels"]["org.ctcc.validation.run"] != identity["run_id"]
        or network["Containers"] not in ({}, None)
        or lock["target"] != "linux"
        or lock["lock_sha256"]
        != files["requirements/validation-linux-py312.lock"]["sha256"]
        or (root / "copied-source-exact.log").read_bytes().strip()
        != str(len(files)).encode("ascii")
        or b"manifest check passed:" not in (root / "copied-manifest.log").read_bytes()
        or (root / "postgres-ready.log").read_bytes().strip() != b"accepting"
    ):
        raise ValueError("owner_scram_lab_runtime_changed")
    actual_source = read_evidence_json((root / "source-files.json").read_bytes())
    if (
        actual_source != files
        or contract.digest((root / "source-files.json").read_bytes())
        != identity["source_files_sha256"]
    ):
        raise ValueError("owner_scram_lab_source_changed")
    junit = shared.verify_pytest_report(root / _JUNIT, required_cases=(_CASE,))
    evidence = read_evidence_json((root / _EVIDENCE).read_bytes())
    if junit["passed"] != 1 or evidence != {
        "schema": "ctcc.native_owner_0056.scram_lab.v1",
        "status": "PASS",
        "source_manifest_sha256": identity["manifest_sha256"],
        "ca_sha256": identity["ca_sha256"],
        "auth_method": "SCRAM-SHA-256",
        "wrong_password_sqlstate": "28P01",
        "physical_preflight_connections": 2,
        "owner_nonsuperuser": True,
        "database_owner_verified": True,
        "product_route_preflight_passed": True,
        "startup_admission": False,
        "execution_authority": False,
    }:
        raise ValueError("owner_scram_lab_evidence_invalid")


def main(argv: list[str] | None = None, *, archive_bytes=None) -> int:
    args = _arguments(argv)
    raw, files = _admit(args, archive_bytes)
    args.output.mkdir(parents=True, exist_ok=False)
    root = args.output.resolve(strict=True)
    with current56._isolated_client(root), ExitStack() as resources_stack:
        return _run(args, raw, files, root, resources_stack)


def _run(args, raw, files, root, resources_stack):
    test_results = root / "test-results"
    test_results.mkdir(mode=0o777)
    test_results.chmod(0o777)
    source_map = root / "source-files.json"
    _write(source_map, files)
    source_map.chmod(0o444)
    map_sha = contract.digest(source_map.read_bytes())
    run_id = uuid4().hex[:12]
    prefix = f"ctcc-owner-scram-{run_id}-"
    network, postgres = prefix + "network", prefix + "postgres"
    image = f"ctcc-owner-scram:{args.commit[:12]}-{run_id}"
    label = f"org.ctcc.validation.run={run_id}"
    identity = {
        "schema": "ctcc.native_owner_0056.scram_lab_run.v1",
        "validation_scope": "owner_scram_lab_diagnostic_only",
        "commit": args.commit,
        "tree": args.tree,
        "archive_sha256": args.archive_sha256,
        "manifest_sha256": files["MANIFEST.sha256"]["sha256"],
        "source_files_sha256": map_sha,
        "run_id": run_id,
        "network": network,
        "host_user_credentials": False,
        "synthetic_password_file_only": True,
        "external_order_writes": 0,
        "startup_admission": False,
        "execution_authority": False,
        "result": "PENDING",
    }
    _write(root / "identity.json", identity)
    run = shared.Run(root)
    owned = []

    def command(stage, *arguments, **kwargs):
        run.command(stage, ["docker", *arguments], **kwargs)

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
        owned.append(("network", network))
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
            raise ValueError("owner_scram_lab_network_not_internal")
        tls = resources_stack.enter_context(devval_tls(True, postgres))
        secret = resources_stack.enter_context(
            owner_scram_lab_secret_v1(tls.postgres_command)
        )
        identity["ca_sha256"] = next(
            value.split("=", 1)[1]
            for value in tls.pytest_environment
            if value.startswith("CTCC_DEVVAL_CUSTODY_TEST_TLS_CA_SHA256=")
        )
        owned.append(("container", postgres))
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
            "POSTGRES_HOST_AUTH_METHOD=scram-sha-256",
            "-e",
            "POSTGRES_INITDB_ARGS=--auth-host=scram-sha-256",
            *tls.postgres_mount,
            *secret.postgres_mount,
            current56._POSTGRES,
            *secret.postgres_command,
        )
        command("postgres-ready", "exec", postgres, "sh", "-ec", _READY, timeout=120)

        def test_container(stage, *arguments, mounts=(), environment=()):
            name = prefix + stage
            owned.append(("container", name))
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
                "-e",
                "ENVIRONMENT=test",
                "-e",
                "TRADING_MODE=analysis_only",
                "-e",
                f"DATABASE_URL=postgresql+asyncpg://ctcc@{postgres}:5432/ctcc",
                *environment,
                image,
                *arguments,
                timeout=600,
            )

        test_container("copied-manifest", "python", "scripts/manifest.py", "--check")
        test_container(
            "copied-source-exact",
            "python",
            "-c",
            current56._COPIED_SOURCE,
            map_sha,
            mounts=(
                "--mount",
                f"type=bind,src={source_map},dst=/ctcc-source-files.json,readonly",
            ),
        )
        test_container(
            "dependency-lock",
            "python",
            "-m",
            "scripts.verify_dependency_lock",
            "--target",
            "linux",
        )
        name = prefix + "owner-scram-test"
        owned.append(("container", name))
        command(
            "owner-scram-test",
            "run",
            "--rm",
            "--user",
            "0:0",
            "--name",
            name,
            "--label",
            label,
            "--network",
            network,
            "--mount",
            f"type=bind,src={test_results},dst=/validation-results",
            *tls.pytest_mount,
            *secret.pytest_mount,
            "-e",
            "ENVIRONMENT=test",
            "-e",
            "TRADING_MODE=analysis_only",
            "-e",
            f"DATABASE_URL=postgresql+asyncpg://ctcc@{postgres}:5432/ctcc",
            "-e",
            f"CTCC_OWNER_SCRAM_LAB_HOST={postgres}",
            "-e",
            f"CTCC_OWNER_SCRAM_LAB_SECRET_FILE={secret.pytest_secret_file}",
            "-e",
            "CTCC_OWNER_SCRAM_LAB_CA_FILE=/ctcc-devval-test-ca.pem",
            "-e",
            f"CTCC_OWNER_SCRAM_LAB_CA_SHA256={identity['ca_sha256']}",
            "-e",
            f"CTCC_OWNER_SCRAM_LAB_MANIFEST_SHA256={identity['manifest_sha256']}",
            image,
            *current56._PYTEST,
            f"--junitxml=/validation-results/{Path(_JUNIT).name}",
            "tests/integration/test_native_owner_scram_lab_pg_v1.py::" + _CASE[1],
            timeout=600,
        )
        identity["result"] = "PASS"
    except Exception as error:
        identity["result"] = "FAIL"
        identity["failure_type"] = type(error).__name__
        raise
    finally:
        try:
            cleanup = shared.cleanup_resources(owned, run_id)
        finally:
            resources_stack.close()
        _write(root / "cleanup.json", cleanup)
        identity["cleanup"] = (
            "PASS"
            if all(item["status"] in ("ABSENT", "REMOVED") for item in cleanup)
            else "FAIL"
        )
        _write(root / "identity.json", identity)
        expected_artifacts = {
            "identity.json",
            "source-files.json",
            "steps.json",
            "cleanup.json",
            _JUNIT,
            _EVIDENCE,
            *(f"{stage}.log" for stage in _STAGES),
        }
        _write(
            root / "results.sha256.json",
            {
                name: contract.digest((root / name).read_bytes())
                for name in sorted(expected_artifacts)
                if (root / name).is_file()
            },
        )
    _verify(root, raw, files, identity)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
