"""Fixed fresh-catalog probes; neither purpose grants full acceptance."""

from __future__ import annotations

import argparse
import re
from contextlib import ExitStack
from pathlib import Path
from uuid import uuid4

from scripts import canonical0055_catalog_reference_v1 as canonical55
from scripts import native_owner_storage_diagnostic_v1 as storage
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
READER_CASE = (
    "tests.integration.test_source58_owner_reader_isolation_compatibility_pg",
    "test_actual_source58_rc_owner_writer_and_explicit_owner_rr_match_frozen_readers",
)
READER_NODE = READER_CASE[0].replace(".", "/") + ".py::" + READER_CASE[1]
READER_REPORT = "test-results/owner-reader-isolation.xml"
READER_MEASUREMENT = "test-results/reader-isolation-compatibility.json"
READER_SCHEMA = "ctcc.source58.owner_reader_isolation_probe.v1"
READER_SCOPE = (
    "disposable0055_0056_tls_legacy_current_comparison_no_execution_authority"
)
READER_SUBJECT = "90f2a714bc31a7833579ee112b64619c31bea5d8"
FROZEN_READER_SOURCE = "93e648fafbd79fa64bad98f6ab8ebede8653c417"
FROZEN_READERS = {
    "collector_current_0055_catalog_v1.py": "fa7e80105102a9eca72176c7c9243c604d275020fae37dd4fbd12f7a81d28652",
    "collector_current_0056_inherited_catalog_v1.py": "d2a489a59510e684b8b2efeea84929aacdd4f3b328a9678fffca78d9b94ea1ca",
}
ACTUAL_READERS = {
    "collector_current_0055_catalog_v1.py": "e27ea4f7ff2577b81d3f6d920436f8054266629846549e074fed9ef1668d9a04",
    "collector_current_0056_inherited_catalog_v1.py": "7ef3e52af3eefd006a16ef0bc9d274ebf8d8e81917ad90c48994a78d39f1c3f1",
}


def _storage_mode(args):
    value = getattr(args, "native_storage_diagnostic", False)
    need(type(value) is bool)
    return value


def _reader_mode(args):
    value = getattr(args, "owner_reader_isolation_compatibility", False)
    need(type(value) is bool and not (value and _storage_mode(args)))
    return value


def _reader_subject(files):
    inputs = {"app/domain/" + name: pin for name, pin in ACTUAL_READERS.items()}
    inputs.update(
        {
            "tests/fixtures/source58_legacy_owner_reader/" + name + ".txt": pin
            for name, pin in FROZEN_READERS.items()
        }
    )
    need(all(files[name]["sha256"] == expected for name, expected in inputs.items()))
    return {
        "commit": READER_SUBJECT,
        "frozen_commit": FROZEN_READER_SOURCE,
        "file_sha256": inputs,
    }


def _reader_measurement(root):
    raw = (root / READER_MEASUREMENT).read_bytes()
    need(0 < len(raw) <= 8192)
    seen = read_evidence_json(raw)
    expected = {
        "schema": "ctcc.source58.owner_reader_isolation_compatibility.v1",
        "observed_tls_sessions": 8,
        "frozen_source_commit": FROZEN_READER_SOURCE,
        "frozen_reader_sha256": FROZEN_READERS,
        "actual_reader_sha256": ACTUAL_READERS,
        "execution_authority": False,
    }
    need(
        type(seen) is dict
        and set(seen) == set(expected) | {"heads"}
        and raw == canonical(seen)
    )
    need(
        all(
            type(seen[key]) is type(value) and seen[key] == value
            for key, value in expected.items()
        )
    )
    heads = seen["heads"]
    need(type(heads) is dict and set(heads) == {"0055", "0056"})
    for row in heads.values():
        flags = {
            "legacy_rc_full_observation_exact",
            "legacy_and_default_rr_refused",
            "explicit_owner_rr_refuses_rc",
        }
        hashes = {
            "owner_rc_definitions_sha256",
            "owner_rr_definitions_sha256",
            "writer_rc_definitions_sha256",
        }
        need(
            type(row) is dict
            and set(row) == flags | hashes
            and all(row[name] is True for name in flags)
        )
        need(
            all(
                type(row[name]) is str and re.fullmatch(r"[a-f0-9]{64}", row[name])
                for name in hashes
            )
        )
        need(row["owner_rc_definitions_sha256"] == row["owner_rr_definitions_sha256"])
    return {"measurement_sha256": digest(raw), **seen}


def _test_scope(args):
    if _reader_mode(args):
        return (READER_CASE,), (READER_NODE,), READER_REPORT
    if _storage_mode(args):
        return (
            (*storage.CASES, canonical55.CASE),
            (storage.MODULE, canonical55.MODULE),
            storage.REPORT,
        )
    return measurement.CASES, (TEST_MODULE,), REPORT


def need(value):
    if value is not True:
        raise ValueError("retained0017_reference_probe_refused") from None


def admit_archive(args):
    raw, files = admit_full_source(args)
    need(digest(Path(__file__).read_bytes()) == files[RUNNER]["sha256"])
    return raw, files


def verify_probe(root, *, args, files):
    reader = _reader_mode(args)
    diagnostic = _storage_mode(args)
    state = read_evidence_json((root / "identity.json").read_bytes())
    raw_map = (root / "source-files.json").read_bytes()
    need(raw_map == canonical(files))
    need(
        state["commit"] == args.commit
        and state["tree"] == args.tree
        and state["archive_sha256"] == args.archive_sha256
    )
    if reader:
        cleanup = read_evidence_json((root / "cleanup.json").read_bytes())
        prefix = "ctcc-final-" + state["run_id"] + "-"
        need(
            state["schema"] == READER_SCHEMA
            and state["suite"] == "owner-reader-isolation-compatibility"
            and state["runner_contract"] == READER_SCHEMA
            and state["validation_scope"] == READER_SCOPE
            and state["database_target"] == "0055_and_0056"
            and state["source_contract_head"] == "0058"
            and state["source_files_sha256"] == digest(raw_map)
            and state["manifest_sha256"] == files["MANIFEST.sha256"]["sha256"]
            and state["runner_sha256"] == files[RUNNER]["sha256"]
            and state["validation_subject"] == _reader_subject(files)
            and state["build_context"] == "admitted_archive_bytes_v1"
            and state["hermetic_regression"] == "NOT_RUN"
            and state["full_suite_acceptance"] is False
            and state["trading_acceptance"] is False
            and state["execution_authority"] is False
            and state["host_env"] is False
            and state["host_credentials"] is False
            and type(state["external_order_writes"]) is int
            and state["external_order_writes"] == 0
            and re.fullmatch(r"sha256:[a-f0-9]{64}", state["image_id"]) is not None
            and re.fullmatch(r"[a-f0-9]{12}", state["run_id"]) is not None
            and type(cleanup) is list
            and all(row["status"] in {"ABSENT", "REMOVED"} for row in cleanup)
            and {(row["kind"], row["name"]) for row in cleanup}
            >= {("network", prefix + "network"), ("container", prefix + "postgres")}
            and not (
                {"pair", "canonical55_reference", "native_storage_components"}
                & state.keys()
            )
        )
    elif diagnostic:
        need(
            state["schema"] == storage.SCHEMA
            and state["runner_contract"] == storage.CONTRACT
            and state["validation_scope"] == storage.SCOPE
            and state["suite"] == storage.SUITE
            and state["database_target"] == "0055_and_0057"
            and state["source_contract_head"] == "0058"
            and state["source_files_sha256"] == digest(raw_map)
            and state["manifest_sha256"] == files["MANIFEST.sha256"]["sha256"]
            and state["runner_sha256"] == files[RUNNER]["sha256"]
            and state["build_context"] == "admitted_archive_bytes_v1"
            and state["hermetic_regression"] == "NOT_RUN"
            and state["full_suite_acceptance"] is False
            and state["trading_acceptance"] is False
            and state["host_credentials"] is False
            and state["host_env"] is False
            and type(state["external_order_writes"]) is int
            and state["external_order_writes"] == 0
            and "pair" not in state
        )
        source = state
    else:
        source = measurement.source_identity(state, files, raw_map)
    cases, _, report_path = _test_scope(args)
    report = shared.verify_pytest_report(
        root / report_path,
        required_cases=cases,
        allow_unrelated_skips=False,
    )
    need(report["tests"] == report["passed"] == len(cases) and report["skipped"] == 0)
    if reader:
        result = _reader_measurement(root)
        need(state["reader_compatibility"] == result)
    elif diagnostic:
        result = storage.verify_reports_v1(root, source)
        need(
            state["canonical55_reference"]
            == canonical55.verify_reference_v1(root, source, files)
        )
        need(state["native_storage_components"] == result)
    else:
        result = measurement.verify_reference_pair(root, source=source)
        need(state["pair"] == result)
    need(state["tests"] == report and state["probe"] == state["cleanup"] == "PASS")
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
    return result


def run_probe(args, raw, files, root):
    reader = _reader_mode(args)
    diagnostic = _storage_mode(args)
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
        "schema": storage.SCHEMA
        if diagnostic
        else "ctcc.retained0017.reference_probe_identity.v1",
        "runner_contract": storage.CONTRACT
        if diagnostic
        else measurement.PROBE_CONTRACT,
        "validation_scope": storage.SCOPE if diagnostic else measurement.PROBE_SCOPE,
        "suite": storage.SUITE if diagnostic else "retained0017-reference-probe",
        "source_contract_head": "0058",
        "database_target": "0055_and_0057" if diagnostic else "0017",
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
    if reader:
        state.update(
            schema=READER_SCHEMA,
            suite="owner-reader-isolation-compatibility",
            runner_contract=READER_SCHEMA,
            validation_scope=READER_SCOPE,
            database_target="0055_and_0056",
            validation_subject=_reader_subject(files),
            execution_authority=False,
        )
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
        # No upgrade of the admin database to58 is implied. Required cases
        # independently create their own disposable canonical-source DBs.
        cases, nodes, report_path = _test_scope(args)
        if reader:
            test_stage = "owner-reader-isolation-tests"
            measurement_environment = (
                "-e",
                "CTCC_OWNER_READER_ISOLATION_COMPATIBILITY=/validation-results/reader-isolation-compatibility.json",
            )
        else:
            test_stage = "native-storage-tests" if diagnostic else "reference17-tests"
            measurement_environment = (
                "-e",
                (storage.ENV if diagnostic else measurement.ENV)
                + "=/validation-results",
            )
        container(
            test_stage,
            *_PYTEST,
            "-q",
            "--junitxml=/validation-results/" + Path(report_path).name,
            *nodes,
            mounts=(
                "--mount",
                f"type=bind,src={results},dst=/validation-results",
                *source_mount,
                *identity_mount,
                *tls.pytest_mount,
            ),
            extra_environment=(
                *tls.pytest_environment,
                *measurement_environment,
            ),
            timeout=900,
        )
        report = shared.verify_pytest_report(
            root / report_path,
            required_cases=cases,
            allow_unrelated_skips=False,
        )
        need(
            report["tests"] == report["passed"] == len(cases) and report["skipped"] == 0
        )
        state["tests"] = report
        if reader:
            state["reader_compatibility"] = _reader_measurement(root)
        elif diagnostic:
            state["native_storage_components"] = storage.verify_reports_v1(root, state)
            state["canonical55_reference"] = canonical55.verify_reference_v1(
                root, state, files
            )
        else:
            state["pair"] = measurement.verify_reference_pair(
                root,
                source=measurement.source_identity(state, files, mapping.read_bytes()),
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
    if reader:
        print("SOURCE58_OWNER_READER_ISOLATION_TLS=PASS", flush=True)
    else:
        print(
            "NATIVE_STORAGE_CATALOG_DIAGNOSTIC=PASS"
            if diagnostic
            else "RETAINED0017_FRESH_REFERENCE_PAIR=PASS",
            flush=True,
        )
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
    purposes = parser.add_mutually_exclusive_group()
    purposes.add_argument("--native-storage-diagnostic", action="store_true")
    purposes.add_argument("--owner-reader-isolation-compatibility", action="store_true")
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
        if _reader_mode(args):
            print("SOURCE58_OWNER_READER_ISOLATION_ARCHIVED_READBACK=PASS", flush=True)
        else:
            print(
                "NATIVE_STORAGE_DIAGNOSTIC_ARCHIVED_READBACK=PASS"
                if _storage_mode(args)
                else "RETAINED0017_REFERENCE_PROBE_ARCHIVED_READBACK=PASS",
                flush=True,
            )
        return 0
    args.output.mkdir(parents=True, exist_ok=False)
    root = args.output.resolve(strict=True)
    with _isolated_client(root):
        return run_probe(args, raw, files, root)


if __name__ == "__main__":
    raise SystemExit(main())
