"""Fixed fresh-catalog probes; neither purpose grants full acceptance."""

from __future__ import annotations

import argparse
import re
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
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
PAPER58_TEST_MODULE = "tests/integration/test_paper58_catalog_reference_v1_pg.py"
OWNED_PAPER_MODULE = "tests/integration/test_paper58_owned_runtime_v1_pg.py"
OWNED_PAPER_CASE = (
    OWNED_PAPER_MODULE.removesuffix(".py").replace("/", "."),
    "test_actual58_owned_paper_http_recovers_real_restricted_state_after_commit_ack_fault",
)
OWNED_PAPER_SCHEMA = "ctcc.source58.fresh_owned_paper_runtime_identity.v1"
OWNED_PAPER_SCOPE = (
    "fresh_canonical58_postprovision_tls_actor_http_and_restart_no_venue"
)
OWNED_PAPER_SUITE = "paper58-fresh-owned-runtime"
OWNED_PAPER_REPORT = "test-results/paper58-fresh-owned-runtime.xml"
OWNED_PAPER_ACCEPTANCE = "owned-paper-acceptance.json"
AUTHORITY_SCHEMA = "ctcc.source58.retained17_direct_authority_tls_probe.v1"
AUTHORITY_SCOPE = "fresh_tls0017_direct_authority_and_readonly_three_cases"
AUTHORITY_SUITE = "retained17-direct-authority"
AUTHORITY_MODULE = "tests.integration.test_retained0017_owner_upgrade_0055_v1_pg"
AUTHORITY_CASES = (
    (
        AUTHORITY_MODULE,
        "test_actual0017_direct_confirmed_apply_refuses_with_valid_readonly_route",
    ),
    (
        AUTHORITY_MODULE,
        "test_actual0017_cli_valid_private_pins_read_only_and_confirmed_refusal",
    ),
    (
        AUTHORITY_MODULE,
        "test_actual0017_upgrade0055_unresolved_legacy_intent_is_retained_and_no_ddl_runs",
    ),
)
AUTHORITY_REPORT = "test-results/retained17-direct-authority.xml"
AUTHORITY_ARCHIVE = "/ctcc-admitted-test-source.tar"
canonical, digest = measurement.canonical, measurement.digest


def _storage_mode(args):
    value = getattr(args, "native_storage_diagnostic", False)
    need(type(value) is bool)
    return value


def _paper58_mode(args):
    value = getattr(args, "paper58_structural_reference", False)
    need(type(value) is bool and not (value and _storage_mode(args)))
    return value


def _paper58_contract(args):
    if not _paper58_mode(args):
        return None
    from scripts import paper58_catalog_reference_v1

    return paper58_catalog_reference_v1


def _owned_paper_mode(args):
    value = getattr(args, "paper58_owned_runtime", False)
    need(
        type(value) is bool
        and not (value and (_storage_mode(args) or _paper58_mode(args)))
    )
    return value


def _authority_mode(args):
    value = getattr(args, "retained17_authority", False)
    need(
        type(value) is bool
        and not (
            value
            and (_storage_mode(args) or _paper58_mode(args) or _owned_paper_mode(args))
        )
    )
    return value


def _authority_identity(state, files, source_bytes):
    need(
        type(state) is dict and type(files) is dict and source_bytes == canonical(files)
    )
    need(
        state["schema"] == state["runner_contract"] == AUTHORITY_SCHEMA
        and state["validation_scope"] == AUTHORITY_SCOPE
        and state["suite"] == AUTHORITY_SUITE
        and state["source_contract_head"] == "0058"
        and state["database_target"] == "0017"
        and state["source_files_sha256"] == digest(source_bytes)
        and state["manifest_sha256"] == files["MANIFEST.sha256"]["sha256"]
        and state["runner_sha256"] == files[RUNNER]["sha256"]
        and state["build_context"] == "admitted_archive_bytes_v1"
        and state["hermetic_regression"] == "NOT_RUN"
        and state["full_suite_acceptance"] is state["trading_acceptance"] is False
        and state["host_credentials"] is state["host_env"] is False
        and type(state["external_order_writes"]) is int
        and state["external_order_writes"] == 0
        and re.fullmatch(r"[a-f0-9]{12}", state["run_id"]) is not None
    )


def bound_authority_fixture_source_v1(identity, files, source_bytes, archive):
    """Return actual isolated test source identities without a FULL contract."""
    _authority_identity(identity, files, source_bytes)
    need(archive == Path(AUTHORITY_ARCHIVE))
    values = {key: identity[key] for key in ("commit", "tree", "archive_sha256")}
    _, admitted = admit_archive(SimpleNamespace(archive=archive, **values))
    need(admitted == files)
    return {**values, "manifest_sha256": identity["manifest_sha256"]}


def _owned_paper_acceptance(state, report):
    need(report["tests"] == report["passed"] == 1 and report["skipped"] == 0)
    return {
        "schema": "ctcc.source58.fresh_owned_paper_runtime_acceptance.v1",
        "source_commit": state["commit"],
        "source_tree": state["tree"],
        "archive_sha256": state["archive_sha256"],
        "manifest_sha256": state["manifest_sha256"],
        "junit_sha256": report["sha256"],
        "isolated_restricted_paper_case": "PASS",
        "database_head": "0058",
        "fixture_fresh_unpinned_only": True,
        "preserved_ledger_opened": False,
        "operator_installation_verified": False,
        "external_order_writes": 0,
        "execution_authority": False,
    }


def need(value):
    if value is not True:
        raise ValueError("retained0017_reference_probe_refused") from None


def admit_archive(args):
    raw, files = admit_full_source(args)
    need(digest(Path(__file__).read_bytes()) == files[RUNNER]["sha256"])
    return raw, files


def verify_probe(root, *, args, files):
    authority = _authority_mode(args)
    owned_paper = _owned_paper_mode(args)
    diagnostic = _storage_mode(args)
    paper = _paper58_contract(args)
    state = read_evidence_json((root / "identity.json").read_bytes())
    if paper is None:
        need("paper58_reference_pair" not in state)
    raw_map = (root / "source-files.json").read_bytes()
    need(raw_map == canonical(files))
    need(
        state["commit"] == args.commit
        and state["tree"] == args.tree
        and state["archive_sha256"] == args.archive_sha256
    )
    if authority:
        _authority_identity(state, files, raw_map)
        network = read_evidence_json((root / "network-inspect.log").read_bytes())
        image = read_evidence_json((root / "image-identity.log").read_bytes())
        need(network["Internal"] is True and image["Id"] == state["image_id"])
    elif owned_paper:
        need(
            state["schema"] == state["runner_contract"] == OWNED_PAPER_SCHEMA
            and state["validation_scope"] == OWNED_PAPER_SCOPE
            and state["suite"] == OWNED_PAPER_SUITE
            and state["database_target"] == state["source_contract_head"] == "0058"
            and state["source_files_sha256"] == digest(raw_map)
            and state["manifest_sha256"] == files["MANIFEST.sha256"]["sha256"]
            and state["runner_sha256"] == files[RUNNER]["sha256"]
            and state["build_context"] == "admitted_archive_bytes_v1"
            and state["hermetic_regression"] == "NOT_RUN"
            and state["full_suite_acceptance"] is state["trading_acceptance"] is False
            and state["host_credentials"] is state["host_env"] is False
            and type(state["external_order_writes"]) is int
            and state["external_order_writes"] == 0
            and "pair" not in state
        )
        network = read_evidence_json((root / "network-inspect.log").read_bytes())
        image = read_evidence_json((root / "image-identity.log").read_bytes())
        need(network["Internal"] is True and image["Id"] == state["image_id"])
        source = state
    elif diagnostic or paper is not None:
        contract = storage if diagnostic else paper
        need(
            state["schema"] == contract.SCHEMA
            and state["runner_contract"] == contract.CONTRACT
            and state["validation_scope"] == contract.SCOPE
            and state["suite"] == contract.SUITE
            and state["database_target"] == ("0055_and_0057" if diagnostic else "0058")
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
        if paper is not None:
            need(
                "native_storage_components" not in state
                and "canonical55_reference" not in state
                and "retained0017_reference_pair" not in state
            )
        source = (
            paper.source_identity(state, files, raw_map) if paper is not None else state
        )
    else:
        source = measurement.source_identity(state, files, raw_map)
    cases = (
        AUTHORITY_CASES
        if authority
        else (OWNED_PAPER_CASE,)
        if owned_paper
        else paper.CASES
        if paper is not None
        else (*storage.CASES, canonical55.CASE)
        if diagnostic
        else measurement.CASES
    )
    report_name = (
        AUTHORITY_REPORT
        if authority
        else OWNED_PAPER_REPORT
        if owned_paper
        else paper.REPORT
        if paper is not None
        else storage.REPORT
        if diagnostic
        else REPORT
    )
    report = shared.verify_pytest_report(
        root / report_name,
        required_cases=cases,
        allow_unrelated_skips=False,
    )
    need(report["tests"] == report["passed"] == len(cases) and report["skipped"] == 0)
    pair = (
        report
        if authority
        else _owned_paper_acceptance(state, report)
        if owned_paper
        else paper.verify_reference_pair_v1(root, source, files)
        if paper is not None
        else storage.verify_reports_v1(root, source)
        if diagnostic
        else measurement.verify_reference_pair(root, source=source)
    )
    if diagnostic:
        need(
            state["canonical55_reference"]
            == canonical55.verify_reference_v1(root, source, files)
        )
    if owned_paper:
        need(read_evidence_json((root / OWNED_PAPER_ACCEPTANCE).read_bytes()) == pair)
    need(
        state[
            "authority_cases"
            if authority
            else "owned_paper_runtime"
            if owned_paper
            else "paper58_reference_pair"
            if paper is not None
            else "native_storage_components"
            if diagnostic
            else "pair"
        ]
        == pair
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
    authority = _authority_mode(args)
    diagnostic = _storage_mode(args)
    paper = _paper58_contract(args)
    owned_paper = _owned_paper_mode(args)
    report_name = (
        AUTHORITY_REPORT
        if authority
        else OWNED_PAPER_REPORT
        if owned_paper
        else paper.REPORT
        if paper is not None
        else storage.REPORT
        if diagnostic
        else REPORT
    )
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
    image = (
        ("ctcc-paper58-reference:" if paper is not None else "ctcc-source17-reference:")
        + args.commit[:12]
        + "-"
        + run_id
    )
    label = "org.ctcc.validation.run=" + run_id
    state = {
        "schema": OWNED_PAPER_SCHEMA
        if owned_paper
        else paper.SCHEMA
        if paper is not None
        else storage.SCHEMA
        if diagnostic
        else "ctcc.retained0017.reference_probe_identity.v1",
        "runner_contract": OWNED_PAPER_SCHEMA
        if owned_paper
        else paper.CONTRACT
        if paper is not None
        else storage.CONTRACT
        if diagnostic
        else measurement.PROBE_CONTRACT,
        "validation_scope": OWNED_PAPER_SCOPE
        if owned_paper
        else paper.SCOPE
        if paper is not None
        else storage.SCOPE
        if diagnostic
        else measurement.PROBE_SCOPE,
        "suite": OWNED_PAPER_SUITE
        if owned_paper
        else paper.SUITE
        if paper is not None
        else storage.SUITE
        if diagnostic
        else "retained0017-reference-probe",
        "source_contract_head": "0058",
        "database_target": "0058"
        if owned_paper or paper is not None
        else "0055_and_0057"
        if diagnostic
        else "0017",
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
    if authority:
        state.update(
            schema=AUTHORITY_SCHEMA,
            runner_contract=AUTHORITY_SCHEMA,
            validation_scope=AUTHORITY_SCOPE,
            suite=AUTHORITY_SUITE,
            database_target="0017",
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
    authority_archive_mount = (
        (
            "--mount",
            f"type=bind,src={args.archive.resolve(strict=True)},dst={AUTHORITY_ARCHIVE},readonly",
        )
        if authority
        else ()
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
        container(
            "paper58-owned-runtime-tests"
            if owned_paper
            else "paper58-reference-tests"
            if paper is not None
            else "native-storage-tests"
            if diagnostic
            else "reference17-tests",
            *_PYTEST,
            "-q",
            "--junitxml=/validation-results/" + Path(report_name).name,
            *(
                tuple(
                    path.replace(".", "/") + ".py::" + case
                    for path, case in AUTHORITY_CASES
                )
                if authority
                else (OWNED_PAPER_MODULE + "::" + OWNED_PAPER_CASE[1],)
                if owned_paper
                else (PAPER58_TEST_MODULE,)
                if paper is not None
                else (storage.MODULE,)
                if diagnostic
                else (TEST_MODULE,)
            ),
            *((canonical55.MODULE,) if diagnostic else ()),
            mounts=(
                "--mount",
                f"type=bind,src={results},dst=/validation-results",
                *source_mount,
                *identity_mount,
                *tls.pytest_mount,
                *authority_archive_mount,
            ),
            extra_environment=(
                *tls.pytest_environment,
                *(
                    (
                        "-e",
                        (
                            paper.ENV
                            if paper is not None
                            else storage.ENV
                            if diagnostic
                            else measurement.ENV
                        )
                        + "=/validation-results",
                    )
                    if not owned_paper and not authority
                    else ()
                ),
            ),
            timeout=900,
        )
        cases = (
            AUTHORITY_CASES
            if authority
            else (OWNED_PAPER_CASE,)
            if owned_paper
            else paper.CASES
            if paper is not None
            else (*storage.CASES, canonical55.CASE)
            if diagnostic
            else measurement.CASES
        )
        report = shared.verify_pytest_report(
            root / report_name,
            required_cases=cases,
            allow_unrelated_skips=False,
        )
        need(
            report["tests"] == report["passed"] == len(cases) and report["skipped"] == 0
        )
        state["tests"] = report
        if authority:
            state["authority_cases"] = report
        elif owned_paper:
            acceptance = _owned_paper_acceptance(state, report)
            state["owned_paper_runtime"] = acceptance
            _write(root / OWNED_PAPER_ACCEPTANCE, acceptance)
        elif paper is not None:
            state["paper58_reference_pair"] = paper.verify_reference_pair_v1(
                root, paper.source_identity(state, files, mapping.read_bytes()), files
            )
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
    print(
        "RETAINED17_DIRECT_AUTHORITY_TLS_DIAGNOSTIC=PASS"
        if authority
        else "PAPER58_FRESH_OWNED_RUNTIME=PASS"
        if owned_paper
        else "PAPER58_FRESH_STRUCTURAL_REFERENCE_PAIR=PASS"
        if paper is not None
        else "NATIVE_STORAGE_CATALOG_DIAGNOSTIC=PASS"
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
    purposes.add_argument("--paper58-structural-reference", action="store_true")
    purposes.add_argument("--paper58-owned-runtime", action="store_true")
    purposes.add_argument(
        "--retained17-direct-authority-proof",
        dest="retained17_authority",
        action="store_true",
    )
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
        print(
            "RETAINED17_DIRECT_AUTHORITY_ARCHIVED_READBACK=PASS"
            if _authority_mode(args)
            else "PAPER58_FRESH_OWNED_RUNTIME_ARCHIVED_READBACK=PASS"
            if _owned_paper_mode(args)
            else "PAPER58_STRUCTURAL_REFERENCE_ARCHIVED_READBACK=PASS"
            if _paper58_mode(args)
            else "NATIVE_STORAGE_DIAGNOSTIC_ARCHIVED_READBACK=PASS"
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
