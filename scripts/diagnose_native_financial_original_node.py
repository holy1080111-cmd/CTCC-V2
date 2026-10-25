"""One exact original Linux unit node; never Windows or trading acceptance."""

from __future__ import annotations

import argparse
import ast
import hashlib
import io
import json
import os
import re
import stat
import subprocess
import tarfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from uuid import uuid4

from scripts.verify_final_hermetic import (
    Run,
    archive_files,
    archive_tree,
    cleanup_resources,
)

NODE_FILE = "tests/unit/test_native_account_financial_operands_v3.py"
NODE_NAME = "test_v3_full_original_history_move_core_registration_and_inert_math"
NODE = NODE_FILE + "::" + NODE_NAME
EXPECTED_REF = "refs/heads/validation/native-financial-v3-original-node-20261025"
INPUT_PINS = {
    NODE_FILE: "0879abd5ce8170203bc8c3a8b969d0f73e4e6f66701169067e53d45caba16fbf",
    "app/trade_qualification/native_account_risk_assembly_v1.py": "e74f4565d2c93b54fd85c588959493d8384fd8e2ffd6fbb3cfdb4526c31fd317",
    "app/database/repositories/account_history_partition.py": "9fb11f4daed67ee970834fa5a5e4d7d1cc1c3533dc2b69862697c6f7777d4629",
    "Dockerfile": "73d030056253ccc324ed0e0a43de9d0c1c00cf8f25087ed8665547867b1cdd4a",
    "requirements/validation-linux-py312.lock": "a44e649cb1d21d3790e132c21699cc172e77ed87682929320cf8f6190fe014e4",
    "scripts/verify_final_hermetic.py": "243d83f0f2d4d84a7543190fc6cad5404914a0578d9dd8838bfac59bd6eece42",
    "scripts/hermetic_pytest.py": "633d526de1be2916cec09833c278ff85be9aa7dc7bbdae75e45361269b315526",
    "app/trade_qualification/account_history_source_v1.py": "fb06524a1ee540e36bc3b1366acfc647edd587b06bb8bd517ef22267fbcb6e79",
    "app/trade_qualification/account_history_source_bundle_v1.py": "06a993d5b10961f302107ff2a45dc5cbfb282384793cb4b1b317563b35c460ac",
    "app/database/repositories/public_runtime_bundle_custody.py": "69cca49251e9bce5f97261a8171aeefabac208a4d1244d864380727dd1597d30",
}
EXECUTION_OFF = (
    "ENVIRONMENT=test",
    "TRADING_MODE=analysis_only",
    "AUTO_TRADE=false",
    "PAPER_AUTO_EXECUTION=false",
    "LIVE_TRADING=false",
    "OKX_DEMO_ALLOW_ORDER_WRITES=false",
    "OKX_DEMO_AUTO_EXECUTION=false",
    "OKX_DEMO_SOAK_ALLOW_EXECUTE=false",
    "OKX_LIVE_ENABLED=false",
    "OKX_LIVE_ALLOW_ORDER_WRITES=false",
    "OKX_LIVE_AUTO_EXECUTION=false",
)
CONTAINER_DEADLINE_SECONDS = 300
RUN_BUDGET_SECONDS = 21 * 60
CLEANUP_SECONDS = 60
_DENY_ALLOWLIST = None


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _owned_deny_codes() -> dict:
    """Called only after an exception; source literals, never error text."""
    global _DENY_ALLOWLIST
    if _DENY_ALLOWLIST is not None:
        return _DENY_ALLOWLIST
    root = Path(__file__).resolve().parents[1]
    allowed = {}
    for name in (
        "app/trade_qualification/native_account_risk_assembly_v1.py",
        "app/trade_qualification/account_history_source_v1.py",
        "app/trade_qualification/account_history_source_bundle_v1.py",
    ):
        raw = (root / name).read_bytes()
        if len(raw) > 4 * 1024 * 1024 or sha(raw) != INPUT_PINS[name]:
            raise ValueError("owned_deny_source_identity_changed")
        tree = ast.parse(raw)
        local_classes = {
            node.name for node in tree.body if isinstance(node, ast.ClassDef)
        }
        deny = [
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "_deny"
        ]
        if len(deny) != 1:
            continue
        raised = {
            node.exc.func.id
            for node in ast.walk(deny[0])
            if isinstance(node, ast.Raise)
            and isinstance(node.exc, ast.Call)
            and isinstance(node.exc.func, ast.Name)
            and node.exc.func.id in local_classes
        }
        if len(raised) != 1:
            continue
        literals = {
            node.args[0].value
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_deny"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and type(node.args[0].value) is str
            and re.fullmatch(r"[a-z][a-z0-9_]{0,127}", node.args[0].value)
        }
        module = name.removesuffix(".py").replace("/", ".")
        allowed[(module, next(iter(raised)))] = frozenset(literals)
    _DENY_ALLOWLIST = allowed
    return allowed


def _failure_chain_record(error, phase):
    try:
        allowed = _owned_deny_codes()
        verified = True
    except Exception:  # noqa: BLE001 -- no unsafe fallback to exception text.
        allowed, verified = {}, False
    records, seen = [], set()
    current = error
    while current is not None and len(records) < 8 and id(current) not in seen:
        seen.add(id(current))
        kind = type(current)
        module, name = kind.__module__, kind.__name__
        if (
            type(module) is not str
            or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]{0,127}", module) is None
        ):
            module = "unknown_module"
        if (
            type(name) is not str
            or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", name) is None
        ):
            name = "unknown_exception_type"
        row = {"module": module, "type": name}
        # No generic args are serialized. A value can select only a previously
        # source-extracted fixed deny literal for this exact owned error class.
        values = BaseException.args.__get__(current)
        if type(values) is tuple and len(values) == 1 and type(values[0]) is str:
            for literal in allowed.get((module, name), ()):
                if values[0] == literal:
                    row["owned_deny_code"] = literal
                    break
        cause = BaseException.__cause__.__get__(current)
        context = BaseException.__context__.__get__(current)
        next_error = cause if cause is not None else context
        row["next_link"] = (
            "cause"
            if cause is not None
            else "context"
            if context is not None
            else "none"
        )
        row["context_suppressed"] = bool(
            BaseException.__suppress_context__.__get__(current)
        )
        records.append(row)
        current = next_error
    phase = phase if phase in ("setup", "call", "teardown") else "unknown_phase"
    record = {
        "schema": "ctcc.native_financial_original_node.failure_chain.v1",
        "phase": phase,
        "chain": records,
        "truncated_or_cycle": current is not None,
        "owned_deny_literal_sources_verified": verified,
        "diagnostic_only": True,
    }
    return record


def pytest_runtest_makereport(item, call):
    """Failure diagnostics only; returning None leaves pytest reporting intact."""
    if call.excinfo is None:
        return
    error = call.excinfo.value
    error_type = type(error)
    if error_type.__module__ == "_pytest.outcomes" and error_type.__name__ in (
        "Skipped",
        "XFailed",
    ):
        return
    try:
        record = _failure_chain_record(error, call.when)
    except Exception:  # noqa: BLE001 -- reporting cannot replace an original failure.
        record = {
            "schema": "ctcc.native_financial_original_node.failure_chain.v1",
            "status": "UNAVAILABLE",
            "reason": "bounded_failure_metadata_unavailable",
            "diagnostic_only": True,
        }
    print(json.dumps(record, sort_keys=True), flush=True)
    return


def save(path: Path, raw: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    if path.read_bytes() != raw:
        raise ValueError("diagnostic_output_readback_failed")


def write_json(path: Path, value: dict) -> None:
    save(path, (json.dumps(value, sort_keys=True, indent=2) + "\n").encode())


def junit_result(path: Path) -> dict | None:
    if not path.exists():
        return None
    info = path.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or path.is_symlink()
        or not 0 < info.st_size <= 4 * 1024 * 1024
    ):
        raise ValueError("diagnostic_junit_unsafe")
    raw = path.read_bytes()
    if b"<!DOCTYPE" in raw or b"<!ENTITY" in raw:
        raise ValueError("diagnostic_junit_entity_denied")
    root = ET.fromstring(raw)
    suites = list(root.iter("testsuite"))
    cases = list(root.iter("testcase"))
    if len(suites) != 1 or len(cases) != 1:
        raise ValueError("diagnostic_original_single_node_unverified")
    case = cases[0]
    if (
        case.attrib.get("classname")
        != "tests.unit.test_native_account_financial_operands_v3"
        or case.attrib.get("name") != NODE_NAME
    ):
        raise ValueError("diagnostic_original_node_identity_mismatch")
    counts = {
        key: int(suites[0].attrib[key])
        for key in ("tests", "failures", "errors", "skipped")
    }
    if counts["tests"] != 1 or any(not 0 <= value <= 1 for value in counts.values()):
        raise ValueError("diagnostic_junit_count_invalid")
    tags = [part.tag for part in case]
    if any(
        tag not in ("failure", "error", "skipped", "system-out", "system-err")
        for tag in tags
    ):
        raise ValueError("diagnostic_junit_child_invalid")
    if any(
        tags.count(tag) != counts[key]
        for tag, key in (
            ("failure", "failures"),
            ("error", "errors"),
            ("skipped", "skipped"),
        )
    ):
        raise ValueError("diagnostic_junit_outcome_conflict")
    return {
        "counts": counts,
        "case": [case.attrib["classname"], case.attrib["name"]],
        "outcome_tags": tags,
        "junit_sha256": sha(raw),
    }


def _cleanup_deadline(state, *, clock=time.monotonic):
    if state["deadline"] is None:
        state["deadline"] = clock() + CLEANUP_SECONDS
    return state["deadline"]


def _remaining_cleanup(state, *, clock=time.monotonic):
    return max(0.0, _cleanup_deadline(state, clock=clock) - clock())


def _stop_original_and_verify_absent(
    container_name,
    run_id,
    cleanup_state,
    *,
    clock=time.monotonic,
    command=subprocess.run,
):
    """Settle one exact owned original container before any result read."""
    record = {
        "container": container_name,
        "status": "UNAVAILABLE",
        "verified_absent": False,
        "checks": [],
    }
    if (
        type(run_id) is not str
        or re.fullmatch(r"[a-f0-9]{12}", run_id) is None
        or container_name != "ctcc-final-" + run_id + "-original-node"
    ):
        record["status"] = "OWNED_NAME_INVALID"
        return record
    deadline = _cleanup_deadline(cleanup_state, clock=clock)

    def bounded(label, arguments):
        remaining = deadline - clock()
        if remaining <= 0:
            raise TimeoutError("diagnostic_shared_cleanup_deadline_exceeded")
        result = command(
            arguments,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=min(30.0, remaining),
            check=False,
        )
        if clock() >= deadline:
            raise TimeoutError("diagnostic_shared_cleanup_deadline_exceeded")
        if (
            type(result.stdout) is not bytes
            or type(result.stderr) is not bytes
            or len(result.stdout) > 64 * 1024
            or len(result.stderr) > 64 * 1024
        ):
            raise ValueError("diagnostic_container_control_output_unbounded")
        record["checks"].append(
            {
                "stage": label,
                "exit_code": result.returncode,
                "stdout_sha256": sha(result.stdout),
                "stderr_sha256": sha(result.stderr),
            }
        )
        if result.returncode != 0:
            raise ValueError("diagnostic_container_control_failed")
        return result.stdout

    def listed(label):
        raw = bounded(
            label,
            ["docker", "container", "ls", "-a", "--format", "{{.Names}}"],
        )
        return container_name in raw.decode("utf8", "strict").splitlines()

    try:
        if listed("initial-owned-presence"):
            inspected = json.loads(
                bounded(
                    "owned-label-readback",
                    ["docker", "container", "inspect", container_name],
                )
            )
            if (
                type(inspected) is not list
                or len(inspected) != 1
                or type(inspected[0]) is not dict
                or inspected[0].get("Name") != "/" + container_name
                or inspected[0]
                .get("Config", {})
                .get("Labels", {})
                .get("org.ctcc.validation.run")
                != run_id
            ):
                record["status"] = "OWNERSHIP_MISMATCH"
                return record
            bounded(
                "owned-remove",
                ["docker", "container", "rm", "-f", "-v", container_name],
            )
            if listed("post-remove-absence-readback"):
                record["status"] = "STILL_VISIBLE_AFTER_REMOVE"
                return record
            record["status"] = "REMOVED_AND_ABSENCE_VERIFIED"
        else:
            record["status"] = "ALREADY_ABSENT_VERIFIED"
        record["verified_absent"] = True
    except Exception as error:  # noqa: BLE001 -- never serialize Docker error text.
        record["failure_category"] = type(error).__name__
    return record


def _original_then_verified_read(invoke_original, settle_original, read_after, state):
    """Retain original failure; permit post-stage IO only after verified absence."""
    try:
        invoke_original()
    except Exception as error:  # noqa: BLE001 -- original failure remains failed evidence.
        state["original_failure_category"] = type(error).__name__
        state["failure_category"] = type(error).__name__
    try:
        stopped = settle_original()
    except Exception as error:  # noqa: BLE001 -- unknown container state denies all reads.
        stopped = {
            "status": "UNAVAILABLE",
            "verified_absent": False,
            "failure_category": type(error).__name__,
        }
    state["original_container_settlement"] = stopped
    if type(stopped) is not dict or stopped.get("verified_absent") is not True:
        state["failure_category"] = (
            state["failure_category"] or "OriginalContainerAbsenceUnverified"
        )
        return None
    state["post_result_reads_permitted"] = True
    return read_after()


def _verify_final_owned_container_absence(
    resources,
    run_id,
    cleanup_state,
    *,
    clock=time.monotonic,
    command=subprocess.run,
):
    """Final independent absence observation; never treats rm rc0 as absence."""
    record = {"verified_absent": False, "owned_container_count": len(resources)}
    names = {name for kind, name in resources if kind == "container"}
    if len(names) != len(resources) or any(
        not name.startswith("ctcc-final-" + run_id + "-") for name in names
    ):
        record["failure_category"] = "OwnedResourceInventoryInvalid"
        return record
    try:
        deadline = _cleanup_deadline(cleanup_state, clock=clock)
        remaining = deadline - clock()
        if remaining <= 0:
            raise TimeoutError("diagnostic_shared_cleanup_deadline_exceeded")
        result = command(
            ["docker", "container", "ls", "-a", "--format", "{{.Names}}"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=min(30.0, remaining),
            check=False,
        )
        if clock() >= deadline:
            raise TimeoutError("diagnostic_shared_cleanup_deadline_exceeded")
        if (
            result.returncode != 0
            or type(result.stdout) is not bytes
            or type(result.stderr) is not bytes
            or len(result.stdout) > 64 * 1024
            or len(result.stderr) > 64 * 1024
        ):
            raise ValueError("diagnostic_final_absence_query_unverified")
        record["stdout_sha256"] = sha(result.stdout)
        record["stderr_sha256"] = sha(result.stderr)
        visible = names.intersection(
            result.stdout.decode("utf8", "strict").splitlines()
        )
        record["owned_containers_still_visible"] = sorted(visible)
        record["verified_absent"] = not visible
    except Exception as error:  # noqa: BLE001 -- metadata only; no Docker text fallback.
        record["failure_category"] = type(error).__name__
    return record


def _evidence_inventory_after_settlement(output, *, readable):
    """Uncertain container state performs no enumeration, stat or content read."""
    if readable is not True:
        return {}
    evidence = {}
    for path in sorted(output.rglob("*")):
        if path.is_file() and not path.is_symlink():
            evidence[str(path.relative_to(output))] = {
                "sha256": sha(path.read_bytes()),
                "bytes": path.stat().st_size,
            }
    return evidence


def main() -> int:
    from scripts.native_financial_fixed_node_diagnostics import (
        NODES,
        SCOPED_REF,
        fixed_junit_result,
        read_phase_report,
        selected_node,
    )

    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--archive-sha256", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--tree", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--fixed-node", choices=tuple(NODES), default="original")
    args = parser.parse_args()
    scoped = args.fixed_node != "original"
    node = selected_node(args.fixed_node)
    expected_ref = SCOPED_REF if scoped else EXPECTED_REF
    input_pins = dict(INPUT_PINS)
    if scoped:
        input_pins["scripts/verify_final_hermetic.py"] = (
            "6b3ed4a605fbfcb6d28d5f5114fedae7bdb9613775c1cf73fcc68a4587cd9d80"
        )
        input_pins["scripts/prospective_capture_custody.py"] = (
            "50b8305a1d0ddefac32717626d1d0dc55145e2024ac65e5104890a4aefb43789"
        )
        input_pins["scripts/native_financial_fixed_node_diagnostics.py"] = (
            "a8318180305b9bfb136f6c43447b586033be6e1ed9e5a42551da93defba8916a"
        )
        input_pins["scripts/native_financial_fixed_node_plugin.py"] = (
            "658595a65d3ec85449f3572b74675f4f832ebd800caaaeb781adaef5f2506e15"
        )
    if (
        os.environ.get("GITHUB_REF") != expected_ref
        or os.environ.get("GITHUB_SHA") != args.commit
        or os.environ.get("GITHUB_EVENT_NAME") not in ("push", "workflow_dispatch")
    ):
        raise ValueError("diagnostic_explicit_feature_branch_required")
    if any(
        re.fullmatch(r"[a-f0-9]{40}", value) is None
        for value in (args.commit, args.tree)
    ):
        raise ValueError("exact_git_identity_required")
    info = args.archive.lstat()
    if not stat.S_ISREG(info.st_mode) or args.archive.is_symlink():
        raise ValueError("diagnostic_archive_unsafe")
    archive_raw = args.archive.read_bytes()
    if sha(archive_raw) != args.archive_sha256:
        raise ValueError("diagnostic_archive_digest_mismatch")
    with tarfile.open(fileobj=io.BytesIO(archive_raw)) as archive:
        if (
            archive.pax_headers.get("comment") != args.commit
            or archive_tree(archive) != args.tree
        ):
            raise ValueError("diagnostic_archive_git_identity_mismatch")
        files = archive_files(archive)
        manifest_stream = archive.extractfile("MANIFEST.sha256")
        if manifest_stream is None:
            raise ValueError("diagnostic_manifest_missing")
        manifest_raw = manifest_stream.read()
    expected_manifest = "".join(
        files[name]["sha256"] + "  " + name + "\n"
        for name in sorted(files)
        if name != "MANIFEST.sha256"
    ).encode()
    if manifest_raw != expected_manifest:
        raise ValueError("diagnostic_manifest_inventory_mismatch")
    for name, pin in input_pins.items():
        if files.get(name, {}).get("sha256") != pin:
            raise ValueError("diagnostic_reviewed_input_changed")
    own_name = "scripts/diagnose_native_financial_original_node.py"
    if files.get(own_name, {}).get("sha256") != sha(Path(__file__).read_bytes()):
        raise ValueError("diagnostic_runner_archive_mismatch")
    args.output.mkdir(parents=True, exist_ok=False)
    map_raw = json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
    file_map = args.output / "source-files.json"
    save(file_map, map_raw)
    result_root = args.output / "test-results"
    result_root.mkdir()
    result_root.chmod(0o777)
    identity = {
        "schema": "ctcc.native_financial_original_node_linux_diagnostic.v1",
        "commit": args.commit,
        "tree": args.tree,
        "archive_sha256": sha(archive_raw),
        "manifest_sha256": sha(manifest_raw),
        "source_file_map_sha256": sha(map_raw),
        "source_count": len(files),
        "runner_sha256": sha(Path(__file__).read_bytes()),
        "reviewed_input_pins": input_pins,
        "node": node,
        "workflow_ref_checked": expected_ref,
        "fixed_node_choice": args.fixed_node,
        "runtime_functions_or_timers_modified": False,
        "original_node_docker_CLI_timeout_seconds": CONTAINER_DEADLINE_SECONDS,
        "shared_cleanup_deadline_budget_seconds": CLEANUP_SECONDS,
        "hard300second_container_termination_deadline_claimed": False,
        "no_profiler_or_external_copied_test": True,
        "runtime_network": "none",
        "host_env_or_credentials_mounted": False,
        "postgresql_service": False,
        "execution_authority": False,
        "external_order_writes": 0,
        "scope": "one_original_Linux_synthetic_IO_unit_node_only",
        "Windows_performance_unresolved": True,
        "production_acquisition_and_receipt_limits_unchanged": True,
        "full_CI_or_Demo_Live_acceptance": False,
    }
    write_json(args.output / "identity-start.json", identity)
    run = Run(args.output, budget_seconds=RUN_BUDGET_SECONDS)
    suffix = uuid4().hex[:12]
    image = f"ctcc-financial-node:{args.commit[:12]}-{suffix}"
    resources = []
    failure = None
    result = None
    cleanup_state = {"deadline": None}
    diagnostic_state = {
        "original_failure_category": None,
        "failure_category": None,
        "post_result_reads_permitted": False,
        "original_container_settlement": None,
    }

    def container(name, command, *, mounts=(), timeout=60):
        container_name = f"ctcc-final-{suffix}-{name}"
        resources.append(("container", container_name))
        environment = [value for item in EXECUTION_OFF for value in ("-e", item)]
        run.command(
            name,
            [
                "docker",
                "run",
                "--rm",
                "--name",
                container_name,
                "--label",
                f"org.ctcc.validation.run={suffix}",
                "--network",
                "none",
                "--cap-drop",
                "ALL",
                "--read-only",
                "--security-opt",
                "no-new-privileges:true",
                "--pids-limit",
                "64",
                "--memory",
                "1g",
                "--cpus",
                "1",
                "--tmpfs",
                "/tmp:rw,nosuid,nodev,size=256m",
                *mounts,
                *environment,
                "--entrypoint",
                "python",
                image,
                *command,
            ],
            timeout=timeout,
        )

    copy_mount = (
        "--mount",
        f"type=bind,src={file_map.resolve()},dst=/verification/source-files.json,readonly",
    )
    copy_code = (
        "from pathlib import Path; "
        "from scripts.verify_final_hermetic import verify_copied_source; "
        "import sys; print(verify_copied_source(Path('/app'),"
        "Path('/verification/source-files.json'),sys.argv[1]))"
    )
    try:
        run.command(
            "build",
            [
                "docker",
                "build",
                "--no-cache",
                "--build-arg",
                f"CTCC_SOURCE_COMMIT={args.commit}",
                "--build-arg",
                f"CTCC_SOURCE_TREE={args.tree}",
                "-t",
                image,
                "-",
            ],
            timeout=900,
            input_bytes=archive_raw,
        )
        run.command(
            "image-identity",
            [
                "docker",
                "image",
                "inspect",
                "--format",
                '{"Id":{{json .Id}},"Labels":{{json .Config.Labels}}}',
                image,
            ],
            timeout=30,
        )
        image_info = json.loads((args.output / "image-identity.log").read_bytes())
        labels = image_info["Labels"]
        if (
            labels.get("org.opencontainers.image.revision") != args.commit
            or labels.get("org.ctcc.source.tree") != args.tree
            or re.fullmatch(r"sha256:[a-f0-9]{64}", image_info["Id"]) is None
        ):
            raise ValueError("diagnostic_image_identity_mismatch")
        identity["image_id"] = image_info["Id"]
        identity["image_configuration_digest"] = image_info["Id"]
        identity["registry_manifest_digest"] = None
        identity["registry_manifest_digest_status"] = "UNKNOWN_NOT_COLLECTED"
        container("copied-manifest", ["-B", "scripts/manifest.py", "--check"])
        container(
            "copied-source-before",
            ["-B", "-c", copy_code, sha(map_raw)],
            mounts=copy_mount,
        )
        container(
            "dependency-lock",
            ["-B", "-m", "scripts.verify_dependency_lock", "--target", "linux"],
        )

        def invoke_original():
            container(
                "original-node",
                [
                    "-B",
                    "-m",
                    "scripts.hermetic_pytest",
                    node,
                    "-o",
                    "addopts=",
                    "-p",
                    "scripts.native_financial_fixed_node_plugin"
                    if scoped
                    else "scripts.diagnose_native_financial_original_node",
                    *(
                        [
                            "--ctcc-financial-fixed-node",
                            args.fixed_node,
                            "--ctcc-financial-phase-json",
                            "/validation-results/fixed-node-phases.json",
                        ]
                        if scoped
                        else []
                    ),
                    "-vv",
                    "-s",
                    "--tb=short",
                    "--durations=1",
                    "-p",
                    "no:cacheprovider",
                    "-p",
                    "no:faulthandler",
                    "--basetemp",
                    "/tmp/ctcc-original-node",
                    "--junitxml",
                    "/validation-results/original-node.xml",
                ],
                mounts=(
                    "--mount",
                    f"type=bind,src={result_root.resolve()},dst=/validation-results",
                ),
                timeout=CONTAINER_DEADLINE_SECONDS,
            )

        def settle_original():
            return _stop_original_and_verify_absent(
                "ctcc-final-" + suffix + "-original-node", suffix, cleanup_state
            )

        def read_after_original():
            container(
                "copied-source-after",
                ["-B", "-c", copy_code, sha(map_raw)],
                mounts=copy_mount,
            )
            original_result = (
                fixed_junit_result(result_root / "original-node.xml", args.fixed_node)
                if scoped
                else junit_result(result_root / "original-node.xml")
            )
            if scoped:
                identity["actual_original_phase_report"] = read_phase_report(
                    result_root / "fixed-node-phases.json", args.fixed_node
                )
            log = (args.output / "original-node.log").read_bytes()
            if log.count(b"HERMETIC_EXECUTION_AUTHORITY=0") != 1:
                raise ValueError("diagnostic_execution_disabled_preflight_unverified")
            if original_result is None or original_result["counts"] != {
                "tests": 1,
                "failures": 0,
                "errors": 0,
                "skipped": 0,
            }:
                diagnostic_state["failure_category"] = (
                    diagnostic_state["failure_category"] or "OriginalNodeNotPassed"
                )
            return original_result

        result = _original_then_verified_read(
            invoke_original, settle_original, read_after_original, diagnostic_state
        )
        failure = diagnostic_state["failure_category"]
    except Exception as error:  # noqa: BLE001 -- preserve original failure without error text.
        failure = (
            diagnostic_state["failure_category"] or failure or type(error).__name__
        )
    finally:
        try:
            cleanup = cleanup_resources(
                resources, suffix, budget_seconds=_remaining_cleanup(cleanup_state)
            )
        except Exception as error:  # noqa: BLE001 -- cleanup uncertainty never permits reads.
            cleanup = [{"status": "FAIL", "failure_category": type(error).__name__}]
        final_absence = _verify_final_owned_container_absence(
            resources, suffix, cleanup_state
        )
        cleanup_verified = (
            all(row["status"] in ("ABSENT", "REMOVED") for row in cleanup)
            and final_absence["verified_absent"] is True
        )
        if not cleanup_verified:
            failure = failure or "CleanupIncomplete"
        readable = (
            diagnostic_state["post_result_reads_permitted"] is True and cleanup_verified
        )
        if not readable:
            failure = failure or "ResultEvidenceUnreadContainerStateUncertain"
        identity.update(
            status="PASS_LINUX_DIAGNOSTIC_ONLY" if failure is None else "FAIL",
            failure_category=failure,
            original_failure_category=diagnostic_state["original_failure_category"],
            original_container_settlement=diagnostic_state[
                "original_container_settlement"
            ],
            post_result_reads_permitted=diagnostic_state["post_result_reads_permitted"],
            actual_original_node_result=result,
            cleanup=cleanup,
            final_owned_container_absence=final_absence,
            shared_cleanup_deadline=cleanup_state["deadline"],
            actual_stages=run.steps,
        )
        evidence = _evidence_inventory_after_settlement(args.output, readable=readable)
        identity["evidence_files"] = evidence
        identity["writable_result_evidence_read_and_hashed"] = readable
        identity["unaccepted_unread_evidence_paths"] = (
            [] if readable else ["original-node.log", "test-results/original-node.xml"]
        )
        write_json(args.output / "identity.json", identity)
    print(
        json.dumps(
            {
                "status": identity["status"],
                "failure_category": failure,
                "Windows_performance_unresolved": True,
            }
        ),
        flush=True,
    )
    return 0 if failure is None else 1


if __name__ != "__main__":
    # Pluggy's marker sets hook priority and returns the same hook function.
    # It neither wraps nor replaces an application function. The host CLI has
    # only the existing stdlib-based runner dependencies.
    from pytest import hookimpl

    hookimpl(tryfirst=True)(pytest_runtest_makereport)


if __name__ == "__main__":
    raise SystemExit(main())
