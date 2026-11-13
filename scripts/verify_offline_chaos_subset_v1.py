"""Run fixed offline failure checks from an independently pinned Git tar archive.

Use the platform's locked validation Python from a reviewed checkout:
    python -B -m scripts.verify_offline_chaos_subset_v1 --archive FILE.tar
        --archive-sha256 REVIEWED_SHA256 --output NEW_DIRECTORY
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import platform
import re
import subprocess
import sys
import tarfile
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts.current_hermetic_contract_dispatch_v1 import capture_hermetic_archive
from scripts.devval_campaign_source import verify_source_checkpoint
from scripts.verify_current0054_hermetic import archive_tree
from scripts.verify_final_hermetic import (
    verify_pytest_report,
    verify_selected_junit_nodes,
)

_MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
_HEX64 = re.compile(r"[a-f0-9]{64}\Z")
_COMMON = (
    "exchange/test_owned_write_seams_v1.py::test_real_pinned_stack_tls_positive_and_duplicate_no_resubmit",
    "exchange/test_owned_write_seams_v1.py::test_actual_order_bytes_then_ambiguous_response_no_second_submission",
    "research/test_owned_public_runtime_v2.py::test_final_fence_rejects_quote_that_ages_out_after_projected_math[long]",
    "research/test_owned_public_runtime_v2.py::test_final_fence_rejects_quote_that_ages_out_after_projected_math[short]",
    "test_qualification_candle_collector.py::test_real_async_deadline_closes_and_never_retries[request]",
    "test_qualification_ws_collector.py::test_receive_timeout_closes_socket_once",
    "test_orchestrator.py::test_disconnected_websocket_blocks_execution",
    "test_demo_automation.py::test_estop_during_clear_reconcile_cannot_be_cleared_by_stale_request",
    "test_demo_automation.py::test_restart_does_not_restore_persisted_automation_arm",
    "test_demo_automation.py::test_unconfirmed_protection_stops_after_ack_without_auto_close",
    "test_demo_automation.py::test_continuous_session_retains_drawdown_backstop",
    "test_notion_outbox_runtime.py::test_native_crashed_dispatch_recovers_uncertain_without_post",
    "test_notion_outbox_runtime.py::test_page_created_before_finish_journal_failure_is_not_created_again",
)
_OS_CASES = {
    "windows": (
        "test_trade_evidence_outbox.py::test_native_windows_late_winerror32_recovers_only_matching_initial_journal",
        "test_trade_evidence_outbox.py::test_native_windows_late_envelope_denial_retains_state_journal",
    ),
    "linux": (
        "test_trade_evidence_outbox.py::test_native_posix_identical_enqueue_and_conflict_never_replace_bytes",
        "test_trade_evidence_outbox.py::test_native_posix_linked_paths_are_refused_without_altering_outside_bytes",
    ),
}

# Keep the application/test process off provider endpoints. This checks Python
# socket calls used by this roster, independently of HTTP-client proxy settings.
_LOOPBACK_PYTEST = """
import ipaddress
import socket
import sys

blocked = False

def check(host):
    global blocked
    try:
        if ipaddress.ip_address(host).is_loopback:
            return
    except ValueError:
        pass
    blocked = True
    raise PermissionError("offline_chaos_non_loopback_socket")

def guard(function, host_from_args):
    def guarded(*args, **kwargs):
        check(host_from_args(args, kwargs))
        return function(*args, **kwargs)
    return guarded

for name in ("connect", "connect_ex", "bind", "sendto"):
    function = getattr(socket.socket, name)
    setattr(socket.socket, name, guard(function, lambda args, kwargs: args[-1][0]))
for name in ("getaddrinfo", "gethostbyname", "gethostbyname_ex", "gethostbyaddr"):
    function = getattr(socket, name)
    setattr(socket, name, guard(function, lambda args, kwargs: args[0] if args else kwargs["host"]))

# Pytest puts the workstation hostname in the JUnit suite attributes.
socket.gethostname = lambda: "ctcc-offline-component-chaos"

import pytest
code = pytest.main(sys.argv[1:])
raise SystemExit(91 if blocked else int(code))
"""


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical_path(path: Path) -> Path:
    path = Path(os.path.abspath(path))
    if path.resolve(strict=True) != path or any(
        item.is_symlink() or getattr(item.lstat(), "st_file_attributes", 0) & 0x400
        for item in (path, *path.parents)
    ):
        raise ValueError("offline_chaos_unsafe_path")
    return path


def _open_source(root: Path, archive: Path, digest: str) -> dict:
    if not 0 < archive.stat().st_size <= _MAX_ARCHIVE_BYTES:
        raise ValueError("offline_chaos_archive_size")
    raw = capture_hermetic_archive(archive, digest)
    if len(raw) > _MAX_ARCHIVE_BYTES:
        raise ValueError("offline_chaos_archive_size")
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as source:
        archive_tree(source)
        source.extractall(root, filter="data")
    return verify_source_checkpoint(root, archive, digest)


def _run_env(private: Path) -> dict[str, str]:
    env = dict.fromkeys(
        (
            "APPDATA",
            "HOME",
            "LOCALAPPDATA",
            "TEMP",
            "TMP",
            "TMPDIR",
            "USERPROFILE",
            "XDG_CACHE_HOME",
            "XDG_CONFIG_HOME",
            "XDG_DATA_HOME",
        ),
        str(private),
    )
    env.update(PATH=os.defpath, PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")
    if os.name == "nt":
        system_root = str(_canonical_path(Path(os.environ["SystemRoot"])))
        env.update(SystemRoot=system_root, WINDIR=system_root)
    return env


def _child(args: list[str], root: Path, env: dict, timeout: int):
    return subprocess.run(
        [sys.executable, "-I", "-B", *args],
        cwd=root,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=timeout,
        check=False,
    )


def _verify_report(path: Path, roster: list[str]) -> dict:
    result = verify_pytest_report(path)
    if result["tests"] != len(roster) or result["skipped"]:
        raise ValueError("offline_chaos_incomplete_roster")
    verify_selected_junit_nodes(path, roster, expected_sha256=result["sha256"])
    return result


def _publish(output: Path, files: dict[str, bytes]) -> str:
    output.mkdir(mode=0o700)
    _canonical_path(output)
    digests = {name: _sha(raw) for name, raw in files.items()}
    checksums = "".join(
        f"{digest}  {name}\n" for name, digest in sorted(digests.items())
    )
    receipt_files = {**files, "SHA256SUMS": checksums.encode("ascii")}
    for name, raw in receipt_files.items():
        path = output / name
        with path.open("xb") as artifact:
            artifact.write(raw)
            artifact.flush()
            os.fsync(artifact.fileno())
    for name, raw in receipt_files.items():
        if _canonical_path(output / name).read_bytes() != raw:
            raise ValueError("offline_chaos_artifact_readback")
    if os.name == "posix":
        for directory in (output, output.parent):
            descriptor = os.open(
                directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            )
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    return digests["report.json"]


def run(archive: Path, archive_sha256: str, output: Path) -> tuple[bool, str]:
    target = platform.system().lower()
    if target not in _OS_CASES or sys.version_info[:2] != (3, 12):
        raise ValueError("offline_chaos_locked_windows_or_linux_cpython312_required")
    if _HEX64.fullmatch(archive_sha256) is None:
        raise ValueError("offline_chaos_reviewed_digest_required")
    archive = _canonical_path(archive)
    output = _canonical_path(output.parent) / output.name
    if os.path.lexists(output):
        raise FileExistsError("offline_chaos_output_already_exists")

    roster = ["tests/unit/" + case for case in (*_COMMON, *_OS_CASES[target])]
    record = {
        "schema": "ctcc.offline_component_chaos.v1",
        "status": "FAIL",
        "target": target,
        "scope": "local_regression_only",
        "environment": "isolated_configuration_and_python_loopback_only",
        "authority_granted": False,
        "full_chaos_acceptance": False,
        "operator_archive_sha256": archive_sha256,
        "runner_sha256": _sha(Path(__file__).read_bytes()),
        "python": platform.python_version(),
        "selected_node_ids": roster,
        "started_utc": datetime.now(UTC).isoformat(),
    }
    files = {}
    stage = "source_checkpoint"
    try:
        with TemporaryDirectory(prefix="ctcc-chaos-") as scratch:
            scratch = _canonical_path(Path(scratch))
            source = scratch / "source"
            source.mkdir()
            record["source"] = _open_source(source, archive, archive_sha256)
            stage = "dependency_identity"
            private = scratch / "private"
            private.mkdir(mode=0o700)
            env = _run_env(private)
            verify_lock_script = (
                "import sys; sys.path.insert(0, '.'); "
                "from scripts.verify_dependency_lock import main; main()"
            )
            lock = _child(
                ["-c", verify_lock_script, "--target", target], source, env, 25
            )
            if lock.returncode or len(lock.stdout) > 128 * 1024:
                raise ValueError("offline_chaos_dependency_lock_failed")
            record["dependency_lock"] = json.loads(lock.stdout)
            stage = "pytest"
            junit = scratch / "chaos.xml"
            result = _child(
                [
                    "-c",
                    _LOOPBACK_PYTEST,
                    "-p",
                    "pytest_asyncio.plugin",
                    "-p",
                    "no:cacheprovider",
                    "-o",
                    "junit_logging=no",
                    "-o",
                    "junit_family=xunit2",
                    "--junitxml",
                    str(junit),
                    "--basetemp",
                    str(scratch / "fixtures"),
                    *roster,
                ],
                source,
                env,
                150,
            )
            record["pytest_exit"] = result.returncode
            if result.returncode:
                raise ValueError("offline_chaos_pytest_failed")
            stage = "junit_readback"
            record["tests"] = _verify_report(junit, roster)
            raw = junit.read_bytes()
            if _sha(raw) != record["tests"]["sha256"]:
                raise ValueError("offline_chaos_junit_changed")
            stage = "source_readback"
            if (
                verify_source_checkpoint(source, archive, archive_sha256)
                != record["source"]
            ):
                raise ValueError("offline_chaos_source_changed")
        files["chaos.xml"] = raw
        record["status"] = "PASS"
    except Exception as exc:  # noqa: BLE001 - Record only known stage and exception class.
        record["failed_stage"] = stage
        record["error_class"] = type(exc).__name__
    record["finished_utc"] = datetime.now(UTC).isoformat()
    files["report.json"] = (
        json.dumps(record, sort_keys=True, indent=2) + "\n"
    ).encode()
    return record["status"] == "PASS", _publish(output, files)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--archive-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        passed, digest = run(args.archive, args.archive_sha256, args.output)
    except (KeyError, OSError, ValueError):
        print("OFFLINE_COMPONENT_CHAOS=FAIL;RECEIPT_WRITTEN=0")
        return 1
    print(
        f"OFFLINE_COMPONENT_CHAOS={'PASS' if passed else 'FAIL'};REPORT_SHA256={digest}"
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
