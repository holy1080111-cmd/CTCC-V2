"""Fixed fresh-catalog probes; neither purpose grants full acceptance."""

from __future__ import annotations

import argparse
import re
from contextlib import ExitStack
from dataclasses import dataclass
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
NONSUPER_OWNER_CASES = (
    "tests/integration/test_gate3_fitted_candidate_custody_pg.py::test_actual0056_granted_nonsuper_cli_stage_first_refusal",
)
canonical, digest = measurement.canonical, measurement.digest


@dataclass(frozen=True)
class _ExactCasesProbe:
    schema: str
    scope: str
    suite: str
    database_target: str
    stage: str
    nodes: tuple[str, ...]
    report: str
    receipt: str
    state_key: str
    console: str

    @property
    def cases(self):
        return tuple(
            (path.removesuffix(".py").replace("/", "."), case)
            for path, case in (node.split("::") for node in self.nodes)
        )


_PAPER = _ExactCasesProbe(
    schema=OWNED_PAPER_SCHEMA,
    scope=OWNED_PAPER_SCOPE,
    suite=OWNED_PAPER_SUITE,
    database_target="0058",
    stage="paper58-owned-runtime-tests",
    nodes=(OWNED_PAPER_MODULE + "::" + OWNED_PAPER_CASE[1],),
    report=OWNED_PAPER_REPORT,
    receipt=OWNED_PAPER_ACCEPTANCE,
    state_key="owned_paper_runtime",
    console="PAPER58_FRESH_OWNED_RUNTIME",
)
_NONSUPER_OWNER = _ExactCasesProbe(
    schema="ctcc.source58.nonsuper_cli_stage_diagnostic_identity.v1",
    scope="disposable_tls_pg17_db0055_to_0056_nonsuper_cli_stage_diagnostic_no_retained_data_no_venue",
    suite="source58-nonsuper-cli-stage-diagnostic",
    database_target="0055_and_0056",
    stage="nonsuper-cli-stage-diagnostic-tests",
    nodes=NONSUPER_OWNER_CASES,
    report="test-results/source58-nonsuper-cli-stage-diagnostic.xml",
    receipt="nonsuper-cli-stage-diagnostic-acceptance.json",
    state_key="nonsuper_cli_stage_diagnostic",
    console="SOURCE58_NONSUPER_OWNER_CLI",
)


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


def _exact_cases_probe(args):
    granted_owner = getattr(args, "nonsuper_cli_stage_diagnostic", False)
    owned = _owned_paper_mode(args)
    need(
        type(granted_owner) is bool
        and not (
            granted_owner and (owned or _storage_mode(args) or _paper58_mode(args))
        )
    )
    if granted_owner:
        return _NONSUPER_OWNER
    return _PAPER if owned else None


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


def _exact_case_acceptance(probe, state, report):
    if probe is _PAPER:
        return _owned_paper_acceptance(state, report)
    need(
        probe is _NONSUPER_OWNER
        and report["tests"] == report["passed"] == len(probe.nodes) == 1
        and report["skipped"] == 0
    )
    return {
        "schema": "ctcc.source58.nonsuper_cli_stage_diagnostic_acceptance.v1",
        "source_commit": state["commit"],
        "source_tree": state["tree"],
        "archive_sha256": state["archive_sha256"],
        "manifest_sha256": state["manifest_sha256"],
        "junit_sha256": report["sha256"],
        "required_nodeids": list(probe.nodes),
        "database_targets": ["0055", "0056"],
        "nonsuper_cli_stage_diagnostic_canaries": "PASS",
        "retained_owner_maintenance_acceptance": False,
        "passed": len(probe.nodes),
        "skipped": 0,
        "preserved_ledger_opened": False,
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
    exact = _exact_cases_probe(args)
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
    if exact is not None:
        need(
            state["schema"] == state["runner_contract"] == exact.schema
            and state["validation_scope"] == exact.scope
            and state["suite"] == exact.suite
            and state["database_target"] == exact.database_target
            and state["source_contract_head"] == "0058"
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
        exact.cases
        if exact is not None
        else paper.CASES
        if paper is not None
        else (*storage.CASES, canonical55.CASE)
        if diagnostic
        else measurement.CASES
    )
    report_name = (
        exact.report
        if exact is not None
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
        _exact_case_acceptance(exact, state, report)
        if exact is not None
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
    if exact is not None:
        need(read_evidence_json((root / exact.receipt).read_bytes()) == pair)
    need(
        state[
            exact.state_key
            if exact is not None
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
    diagnostic = _storage_mode(args)
    paper = _paper58_contract(args)
    exact = _exact_cases_probe(args)
    report_name = (
        exact.report
        if exact is not None
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
        "schema": exact.schema
        if exact is not None
        else paper.SCHEMA
        if paper is not None
        else storage.SCHEMA
        if diagnostic
        else "ctcc.retained0017.reference_probe_identity.v1",
        "runner_contract": exact.schema
        if exact is not None
        else paper.CONTRACT
        if paper is not None
        else storage.CONTRACT
        if diagnostic
        else measurement.PROBE_CONTRACT,
        "validation_scope": exact.scope
        if exact is not None
        else paper.SCOPE
        if paper is not None
        else storage.SCOPE
        if diagnostic
        else measurement.PROBE_SCOPE,
        "suite": exact.suite
        if exact is not None
        else paper.SUITE
        if paper is not None
        else storage.SUITE
        if diagnostic
        else "retained0017-reference-probe",
        "source_contract_head": "0058",
        "database_target": exact.database_target
        if exact is not None
        else "0058"
        if paper is not None
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
        container(
            exact.stage
            if exact is not None
            else "paper58-reference-tests"
            if paper is not None
            else "native-storage-tests"
            if diagnostic
            else "reference17-tests",
            *_PYTEST,
            "-q",
            "-o",
            "junit_family=xunit1",
            "--junitxml=/validation-results/" + Path(report_name).name,
            *(
                exact.nodes
                if exact is not None
                else (PAPER58_TEST_MODULE,)
                if paper is not None
                else (storage.MODULE, canonical55.MODULE)
                if diagnostic
                else (TEST_MODULE,)
            ),
            mounts=(
                "--mount",
                f"type=bind,src={results},dst=/validation-results",
                *source_mount,
                *identity_mount,
                *tls.pytest_mount,
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
                    if exact is None
                    else ()
                ),
            ),
            timeout=900,
        )
        cases = (
            exact.cases
            if exact is not None
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
        if exact is not None:
            acceptance = _exact_case_acceptance(exact, state, report)
            state[exact.state_key] = acceptance
            _write(root / exact.receipt, acceptance)
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
        exact.console + "=PASS"
        if exact is not None
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
    purposes.add_argument("--nonsuper-cli-stage-diagnostic", action="store_true")
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
        exact = _exact_cases_probe(args)
        print(
            exact.console + "_ARCHIVED_READBACK=PASS"
            if exact is not None
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
