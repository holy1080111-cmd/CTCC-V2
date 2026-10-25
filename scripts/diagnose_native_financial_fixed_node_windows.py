"""One exact Windows original unit node in an owned, bounded process Job.

No application function, TTL or regression gate is changed. The source is a
fresh Git archive and the fresh CPython3.12 environment must match all41 locked
Windows dependencies. This program is diagnostic evidence, never acceptance.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import zipfile
from pathlib import Path, PurePosixPath

from scripts.diagnose_native_financial_original_node import EXECUTION_OFF, write_json
from scripts.native_financial_fixed_node_diagnostics import (
    NODE_FILE,
    SCOPED_CHOICES,
    SCOPED_REF,
    SOURCE,
    SOURCE_SHA,
    fixed_junit_result,
    read_phase_report,
    selected_node,
)
from scripts.verify_dependency_lock import verify_lock
from scripts.verify_windows_regression import verify_archive_readback

NODE_SECONDS = 300
CLEANUP_SECONDS = 60
TEST_SHA = "0879abd5ce8170203bc8c3a8b969d0f73e4e6f66701169067e53d45caba16fbf"


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _git_tree(files):
    nodes = {}
    for name, (mode, raw) in files.items():
        value = nodes
        parts = PurePosixPath(name).parts
        for part in parts[:-1]:
            value = value.setdefault(part, {})
        value[parts[-1]] = (
            mode,
            hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).digest(),
        )

    def encode(value):
        raw = b""
        for name, child in sorted(
            value.items(),
            key=lambda item: (
                item[0].encode() + (b"/" if type(item[1]) is dict else b"")
            ),
        ):
            mode, oid = ("40000", encode(child)) if type(child) is dict else child
            raw += mode.encode() + b" " + name.encode() + b"\0" + oid
        return hashlib.sha1(b"tree " + str(len(raw)).encode() + b"\0" + raw).digest()

    return encode(nodes).hex()


def source_identity(archive, root, commit, tree):
    if any(re.fullmatch("[a-f0-9]{40}", value) is None for value in (commit, tree)):
        raise ValueError("windows_fixed_exact_commit_tree_required")
    archive_sha = verify_archive_readback(archive, root)
    files = {}
    with zipfile.ZipFile(archive) as stream:
        if stream.comment != commit.encode():
            raise ValueError("windows_fixed_git_archive_commit_unverified")
        for item in stream.infolist():
            if item.is_dir():
                continue
            name = item.filename
            if any(
                part.casefold().startswith(".env") and part != ".env.example"
                for part in PurePosixPath(name).parts
            ):
                raise ValueError("windows_fixed_private_environment_in_source")
            mode = "100755" if item.external_attr >> 16 & 0o111 else "100644"
            files[name] = mode, stream.read(item)
    if _git_tree(files) != tree:
        raise ValueError("windows_fixed_git_archive_tree_unverified")
    manifest = "".join(
        _sha(raw) + "  " + name + "\n"
        for name, (_mode, raw) in sorted(files.items())
        if name != "MANIFEST.sha256"
    ).encode()
    if files.get("MANIFEST.sha256", (None, None))[1] != manifest:
        raise ValueError("windows_fixed_manifest_unverified")
    if _sha(files[SOURCE][1]) != SOURCE_SHA or _sha(files[NODE_FILE][1]) != TEST_SHA:
        raise ValueError("windows_fixed_original_source_changed")
    own = "scripts/diagnose_native_financial_fixed_node_windows.py"
    if files.get(own, (None, None))[1] != Path(__file__).read_bytes():
        raise ValueError("windows_fixed_producer_archive_mismatch")
    return {
        "commit": commit,
        "tree": tree,
        "archive_sha256": archive_sha,
        "manifest_sha256": _sha(manifest),
        "source_count": len(files),
        "source_map_sha256": _sha(
            json.dumps(
                {
                    name: [mode, _sha(raw)]
                    for name, (mode, raw) in sorted(files.items())
                },
                separators=(",", ":"),
            ).encode()
        ),
    }


class WindowsOwnedJob:
    """Create suspended, assign to kill-on-close Job, then resume exactly once."""

    def __init__(self):
        if os.name != "nt" or ctypes.sizeof(ctypes.c_void_p) != 8:
            raise ValueError("windows_fixed_native_x64_required")
        from ctypes import wintypes as w

        self.w, self.kernel = w, ctypes.WinDLL("kernel32", use_last_error=True)
        self.job = self.process = self.thread = None
        self.stream = self.null = None
        self.attribute = None
        self.attribute_initialized = False
        U64, SIZE = ctypes.c_ulonglong, ctypes.c_size_t

        class Basic(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", w.DWORD),
                ("MinimumWorkingSetSize", SIZE),
                ("MaximumWorkingSetSize", SIZE),
                ("ActiveProcessLimit", w.DWORD),
                ("Affinity", SIZE),
                ("PriorityClass", w.DWORD),
                ("SchedulingClass", w.DWORD),
            ]

        class Counters(ctypes.Structure):
            _fields_ = [
                (name, U64)
                for name in (
                    "ReadOperationCount",
                    "WriteOperationCount",
                    "OtherOperationCount",
                    "ReadTransferCount",
                    "WriteTransferCount",
                    "OtherTransferCount",
                )
            ]

        class Extended(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", Basic),
                ("IoInfo", Counters),
                ("ProcessMemoryLimit", SIZE),
                ("JobMemoryLimit", SIZE),
                ("PeakProcessMemoryUsed", SIZE),
                ("PeakJobMemoryUsed", SIZE),
            ]

        class Accounting(ctypes.Structure):
            _fields_ = [
                (name, ctypes.c_longlong)
                for name in (
                    "TotalUserTime",
                    "TotalKernelTime",
                    "ThisPeriodTotalUserTime",
                    "ThisPeriodTotalKernelTime",
                )
            ] + [
                (name, w.DWORD)
                for name in (
                    "TotalPageFaultCount",
                    "TotalProcesses",
                    "ActiveProcesses",
                    "TotalTerminatedProcesses",
                )
            ]

        class Startup(ctypes.Structure):
            _fields_ = [
                ("cb", w.DWORD),
                ("lpReserved", w.LPWSTR),
                ("lpDesktop", w.LPWSTR),
                ("lpTitle", w.LPWSTR),
                ("dwX", w.DWORD),
                ("dwY", w.DWORD),
                ("dwXSize", w.DWORD),
                ("dwYSize", w.DWORD),
                ("dwXCountChars", w.DWORD),
                ("dwYCountChars", w.DWORD),
                ("dwFillAttribute", w.DWORD),
                ("dwFlags", w.DWORD),
                ("wShowWindow", w.WORD),
                ("cbReserved2", w.WORD),
                ("lpReserved2", ctypes.POINTER(ctypes.c_byte)),
                ("hStdInput", w.HANDLE),
                ("hStdOutput", w.HANDLE),
                ("hStdError", w.HANDLE),
            ]

        class StartupEx(ctypes.Structure):
            _fields_ = [("StartupInfo", Startup), ("lpAttributeList", ctypes.c_void_p)]

        class Process(ctypes.Structure):
            _fields_ = [
                ("hProcess", w.HANDLE),
                ("hThread", w.HANDLE),
                ("dwProcessId", w.DWORD),
                ("dwThreadId", w.DWORD),
            ]

        self.Extended, self.Accounting, self.StartupEx, self.Process = (
            Extended,
            Accounting,
            StartupEx,
            Process,
        )
        signatures = {
            "CreateJobObjectW": ([ctypes.c_void_p, w.LPCWSTR], w.HANDLE),
            "SetInformationJobObject": (
                [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD],
                w.BOOL,
            ),
            "QueryInformationJobObject": (
                [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD, ctypes.c_void_p],
                w.BOOL,
            ),
            "AssignProcessToJobObject": ([w.HANDLE, w.HANDLE], w.BOOL),
            "TerminateJobObject": ([w.HANDLE, w.UINT], w.BOOL),
            "TerminateProcess": ([w.HANDLE, w.UINT], w.BOOL),
            "WaitForSingleObject": ([w.HANDLE, w.DWORD], w.DWORD),
            "GetExitCodeProcess": ([w.HANDLE, ctypes.POINTER(w.DWORD)], w.BOOL),
            "CloseHandle": ([w.HANDLE], w.BOOL),
            "ResumeThread": ([w.HANDLE], w.DWORD),
            "InitializeProcThreadAttributeList": (
                [ctypes.c_void_p, w.DWORD, w.DWORD, ctypes.POINTER(SIZE)],
                w.BOOL,
            ),
            "UpdateProcThreadAttribute": (
                [
                    ctypes.c_void_p,
                    w.DWORD,
                    SIZE,
                    ctypes.c_void_p,
                    SIZE,
                    ctypes.c_void_p,
                    ctypes.c_void_p,
                ],
                w.BOOL,
            ),
            "DeleteProcThreadAttributeList": ([ctypes.c_void_p], None),
            "CreateProcessW": (
                [
                    w.LPCWSTR,
                    w.LPWSTR,
                    ctypes.c_void_p,
                    ctypes.c_void_p,
                    w.BOOL,
                    w.DWORD,
                    ctypes.c_void_p,
                    w.LPCWSTR,
                    ctypes.c_void_p,
                    ctypes.POINTER(Process),
                ],
                w.BOOL,
            ),
        }
        for name, (arguments, result) in signatures.items():
            function = getattr(self.kernel, name)
            function.argtypes, function.restype = arguments, result

    def start(self, command, cwd, environment, log_path):
        import msvcrt

        k, w = self.kernel, self.w
        self.job = k.CreateJobObjectW(None, None)
        if not self.job:
            raise OSError("windows_fixed_job_create_failed")
        limits = self.Extended()
        limits.BasicLimitInformation.LimitFlags = 0x2000
        if not k.SetInformationJobObject(
            self.job, 9, ctypes.byref(limits), ctypes.sizeof(limits)
        ):
            raise OSError("windows_fixed_job_limits_failed")
        # Owned streams stay open until the process termination barrier.
        self.stream, self.null = log_path.open("xb"), open(os.devnull, "rb")  # noqa: SIM115
        output, stdin = (
            msvcrt.get_osfhandle(self.stream.fileno()),
            msvcrt.get_osfhandle(self.null.fileno()),
        )
        os.set_handle_inheritable(output, True)
        os.set_handle_inheritable(stdin, True)
        handles = (w.HANDLE * 2)(stdin, output)
        size = ctypes.c_size_t()
        k.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(size))
        if not size.value:
            raise OSError("windows_fixed_attribute_size_failed")
        self.attribute = ctypes.create_string_buffer(size.value)
        if not k.InitializeProcThreadAttributeList(
            self.attribute, 1, 0, ctypes.byref(size)
        ):
            raise OSError("windows_fixed_handle_allowlist_initialization_failed")
        self.attribute_initialized = True
        if not k.UpdateProcThreadAttribute(
            self.attribute,
            0,
            0x00020002,
            ctypes.byref(handles),
            ctypes.sizeof(handles),
            None,
            None,
        ):
            raise OSError("windows_fixed_handle_allowlist_failed")
        startup, process = self.StartupEx(), self.Process()
        startup.StartupInfo.cb = ctypes.sizeof(startup)
        startup.StartupInfo.dwFlags = 0x100
        (
            startup.StartupInfo.hStdInput,
            startup.StartupInfo.hStdOutput,
            startup.StartupInfo.hStdError,
        ) = stdin, output, output
        startup.lpAttributeList = ctypes.cast(self.attribute, ctypes.c_void_p)
        block = ctypes.create_unicode_buffer(
            "\0".join(name + "=" + value for name, value in sorted(environment.items()))
            + "\0\0"
        )
        arguments = ctypes.create_unicode_buffer(subprocess.list2cmdline(command))
        if not k.CreateProcessW(
            command[0],
            arguments,
            None,
            None,
            True,
            0x08080404,
            block,
            str(cwd),
            ctypes.byref(startup),
            ctypes.byref(process),
        ):
            raise OSError("windows_fixed_suspended_process_create_failed")
        self.process, self.thread = process.hProcess, process.hThread
        if not k.AssignProcessToJobObject(self.job, self.process):
            raise OSError("windows_fixed_process_assignment_failed")
        if k.ResumeThread(self.thread) == 0xFFFFFFFF:
            raise OSError("windows_fixed_process_resume_failed")

    def wait(self, milliseconds):
        if self.process is None:
            return True
        value = self.kernel.WaitForSingleObject(self.process, milliseconds)
        if value not in (0, 258):
            raise OSError("windows_fixed_process_wait_unknown")
        return value == 0

    def terminate(self):
        # A failed assignment can leave the suspended primary outside this Job.
        # Always attempt both exact owned handles before reporting uncertainty.
        failure = False
        if self.job:
            try:
                if not self.kernel.TerminateJobObject(self.job, 0xCECC0001):
                    failure = True
            except Exception:  # noqa: BLE001 -- still attempt the owned primary.
                failure = True
        if self.process:
            try:
                exited = self.wait(0)
            except Exception:  # noqa: BLE001 -- uncertain wait still requires kill.
                exited, failure = False, True
            if not exited:
                try:
                    if not self.kernel.TerminateProcess(self.process, 0xCECC0001):
                        failure = True
                except Exception:  # noqa: BLE001 -- preserve fixed unknown state.
                    failure = True
        if failure:
            raise OSError("windows_fixed_owned_termination_unknown")

    def active(self):
        if not self.job:
            return 0 if self.process is None else None
        value = self.Accounting()
        if not self.kernel.QueryInformationJobObject(
            self.job, 1, ctypes.byref(value), ctypes.sizeof(value), None
        ):
            raise OSError("windows_fixed_job_accounting_unknown")
        return value.ActiveProcesses

    def exit_code(self):
        value = self.w.DWORD()
        if not self.process or not self.kernel.GetExitCodeProcess(
            self.process, ctypes.byref(value)
        ):
            return None
        return value.value

    def close(self):
        failure = False
        if self.attribute_initialized:
            self.kernel.DeleteProcThreadAttributeList(self.attribute)
            self.attribute_initialized = False
        self.attribute = None
        for name in ("thread", "process", "job"):
            value = getattr(self, name)
            if value and not self.kernel.CloseHandle(value):
                failure = True
            setattr(self, name, None)
        for name in ("stream", "null"):
            value = getattr(self, name)
            if value is not None:
                value.close()
                setattr(self, name, None)
        if failure:
            raise OSError("windows_fixed_owned_handle_close_unknown")


def run_owned_node(
    command, cwd, environment, log_path, *, native=None, clock=time.monotonic
):
    native = WindowsOwnedJob() if native is None else native
    record = {
        "verified_terminated": False,
        "node_cap_seconds": NODE_SECONDS,
        "cleanup_budget_seconds": CLEANUP_SECONDS,
        "exit_code": None,
        "timed_out": False,
        "failure_category": None,
    }
    try:
        start = clock()
        native.start(command, cwd, environment, log_path)
        deadline = start + NODE_SECONDS
        if clock() >= deadline:
            record["timed_out"] = True
        record["real_node_start_monotonic"] = start
        record["real_node_deadline_monotonic"] = deadline
        while not record["timed_out"] and not native.wait(0):
            remaining = deadline - clock()
            if remaining <= 0:
                record["timed_out"] = True
                break
            native.wait(max(1, min(100, int(remaining * 1000))))
        if clock() >= deadline:
            record["timed_out"] = True
        record["exit_code"] = native.exit_code()
    except Exception as error:  # noqa: BLE001 -- no Windows error text is serialized.
        record["failure_category"] = type(error).__name__
    finally:
        cleanup_deadline = clock() + CLEANUP_SECONDS
        record["cleanup_deadline_monotonic"] = cleanup_deadline
        try:
            if clock() >= cleanup_deadline:
                raise TimeoutError("windows_fixed_termination_deadline")
            try:
                native.terminate()
            except Exception as error:  # noqa: BLE001 -- still observe both handles.
                record["failure_category"] = (
                    record["failure_category"] or type(error).__name__
                )
            if clock() >= cleanup_deadline:
                raise TimeoutError("windows_fixed_termination_deadline")
            while True:
                if clock() >= cleanup_deadline:
                    raise TimeoutError("windows_fixed_termination_deadline")
                primary_exited = native.wait(0)
                if clock() >= cleanup_deadline:
                    raise TimeoutError("windows_fixed_termination_deadline")
                active = native.active()
                if clock() >= cleanup_deadline:
                    raise TimeoutError("windows_fixed_termination_deadline")
                if primary_exited and type(active) is int and active == 0:
                    record["verified_terminated"] = record["failure_category"] is None
                    break
                native.wait(max(1, min(100, int((cleanup_deadline - clock()) * 1000))))
        except Exception as error:  # noqa: BLE001 -- unknown Job state forbids evidence reads.
            record["failure_category"] = (
                record["failure_category"] or type(error).__name__
            )
        try:
            native.close()
            if clock() >= cleanup_deadline:
                record["verified_terminated"] = False
                raise TimeoutError("windows_fixed_handle_close_deadline")
        except Exception as error:  # noqa: BLE001 -- closed output handles are part of the barrier.
            record["verified_terminated"] = False
            record["failure_category"] = (
                record["failure_category"] or type(error).__name__
            )
    return record


def original_run_passed(settlement, result):
    return (
        settlement.get("verified_terminated") is True
        and settlement.get("timed_out") is False
        and settlement.get("failure_category") is None
        and settlement.get("exit_code") == 0
        and result is not None
        and result.get("counts")
        == {"tests": 1, "failures": 0, "errors": 0, "skipped": 0}
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--tree", required=True)
    parser.add_argument("--fixed-node", choices=SCOPED_CHOICES, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if (
        os.name != "nt"
        or sys.version_info[:2] != (3, 12)
        or os.environ.get("GITHUB_REF") != SCOPED_REF
        or os.environ.get("GITHUB_SHA") != args.commit
        or os.environ.get("GITHUB_EVENT_NAME") not in ("push", "workflow_dispatch")
    ):
        raise ValueError("windows_fixed_scoped_native_workflow_required")
    root = Path(__file__).resolve().parents[1]
    expected_venv = args.archive.parent / "venv"
    if (
        Path(sys.prefix).resolve() != expected_venv.resolve()
        or sys.prefix == sys.base_prefix
        or Path(sys.executable).resolve()
        != (expected_venv / "Scripts/python.exe").resolve()
    ):
        raise ValueError("windows_fixed_fresh_owned_venv_required")
    before = source_identity(args.archive, root, args.commit, args.tree)
    dependency = verify_lock(root, "windows")
    if (
        dependency["packages"] != 41
        or dependency["installed_inventory"]["expected_count"] != 42
    ):
        raise ValueError("windows_fixed_exact41_dependencies_required")
    args.output.mkdir(parents=True, exist_ok=False)
    temporary = args.output / "tmp"
    temporary.mkdir()
    node = selected_node(args.fixed_node)
    environment = {
        name: os.environ[name]
        for name in ("SystemRoot", "WINDIR", "ComSpec")
        if name in os.environ
    }
    environment.update(
        {
            "PATH": str(Path(sys.executable).parent),
            "TEMP": str(temporary.resolve()),
            "TMP": str(temporary.resolve()),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
            **dict(item.split("=", 1) for item in EXECUTION_OFF),
        }
    )
    identity = {
        "schema": "ctcc.native_financial_fixed_node.windows_diagnostic.v1",
        "source": before,
        "node": node,
        "platform": "native_windows_x64_cpython312",
        "python_executable_sha256": _sha(Path(sys.executable).read_bytes()),
        "dependency": dependency,
        "sterile_environment_keys": sorted(environment),
        "host_credentials_or_env_loaded": False,
        "Linux_results_substituted": False,
        "runtime_functions_or_deadlines_modified": False,
        "execution_authority": False,
        "external_order_writes": 0,
        "full_CI_or_Demo_Live_acceptance": False,
        "status": "FAIL",
        "trusted_result_artifacts": None,
    }
    write_json(args.output / "identity-start.json", identity)
    log_path = args.output / "original-node.log"
    command = [
        sys.executable,
        "-B",
        "-m",
        "scripts.hermetic_pytest",
        node,
        "-o",
        "addopts=",
        "-p",
        "scripts.native_financial_fixed_node_plugin",
        "--ctcc-financial-fixed-node",
        args.fixed_node,
        "--ctcc-financial-phase-json",
        str((args.output / "fixed-node-phases.json").resolve()),
        "-vv",
        "-s",
        "--tb=short",
        "--durations=1",
        "-p",
        "no:cacheprovider",
        "-p",
        "no:faulthandler",
        "--basetemp",
        str((temporary / "pytest").resolve()),
        "--junitxml",
        str((args.output / "original-node.xml").resolve()),
    ]
    settlement = run_owned_node(command, root, environment, log_path)
    identity["owned_process_settlement"] = settlement
    if settlement["verified_terminated"] is True:
        try:
            if source_identity(args.archive, root, args.commit, args.tree) != before:
                raise ValueError("windows_fixed_source_after_process_changed")
            result = fixed_junit_result(
                args.output / "original-node.xml", args.fixed_node
            )
            log = log_path.read_bytes()
            if log.count(b"HERMETIC_EXECUTION_AUTHORITY=0") != 1:
                raise ValueError("windows_fixed_execution_disabled_unverified")
            phases = args.output / "fixed-node-phases.json"
            identity["actual_original_phase_report"] = read_phase_report(
                phases, args.fixed_node
            )
            identity["actual_original_node_result"] = result
            identity["trusted_result_artifacts"] = {
                path.name: _sha(path.read_bytes())
                for path in (log_path, args.output / "original-node.xml", phases)
            }
            if original_run_passed(settlement, result):
                identity["status"] = "PASS_WINDOWS_DIAGNOSTIC_ONLY"
        except Exception as error:  # noqa: BLE001 -- fixed category only; failed raw evidence is retained.
            identity["failure_category"] = type(error).__name__
    else:
        identity["unaccepted_unread_result_paths"] = [
            "original-node.log",
            "original-node.xml",
            "fixed-node-phases.json",
        ]
    write_json(args.output / "identity.json", identity)
    print(
        json.dumps(
            {"status": identity["status"], "execution_authority": False}, sort_keys=True
        )
    )
    return 0 if identity["status"] == "PASS_WINDOWS_DIAGNOSTIC_ONLY" else 1


if __name__ == "__main__":
    raise SystemExit(main())
