"""Versioned read-only Linux kernel-clock evidence, separate from Windows.

This building block verifies a measured runtime profile and kernel discipline;
it cannot certify an upstream NTP server, last synchronization age, historical
availability, or trading authority. Existing clock dispatch is unchanged.
"""

from __future__ import annotations

import _ctypes
import base64
import ctypes
import inspect
import os
import re
import stat
import sys
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from threading import Lock, get_ident
from types import MappingProxyType
from weakref import WeakKeyDictionary

from app.domain.source_primitives import (
    MAX_CLOCK_DEVIATION_NS,
    MAX_TIMESTAMP_NS,
    PublicReceiptError,
    canonical,
    decode,
    sha,
    validate_stamps,
)

SCHEMA_VERSION = "ctcc.linux_kernel_clock_observation.v1"
MAX_PAYLOAD_BYTES = 64 * 1024
MAX_OBSERVATION_NS = 5_000_000_000
MAX_SAMPLE_BRACKET_NS = 1_000_000
MAX_RESOLUTION_NS = 1000
_MAX_RUNTIME_FILE = 64 * 1024 * 1024
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_SHA = re.compile(r"[0-9a-f]{64}\Z")

_ABI = {
    "machine": "x86_64",
    "byteorder": "little",
    "long_size": 8,
    "pointer_size": 8,
    "timex_size": 208,
    "timex_alignment": 8,
    "timespec_size": 16,
    "timeval_size": 16,
    "offsets": {
        "modes": 0,
        "offset": 8,
        "freq": 16,
        "maxerror": 24,
        "esterror": 32,
        "status": 40,
        "constant": 48,
        "precision": 56,
        "tolerance": 64,
        "time": 72,
        "tick": 88,
        "ppsfreq": 96,
        "jitter": 104,
        "shift": 112,
        "stabil": 120,
        "jitcnt": 128,
        "calcnt": 136,
        "errcnt": 144,
        "stbcnt": 152,
        "tai": 160,
    },
    "clock_ids": {"realtime": 0, "monotonic": 1, "boottime": 7},
}


# Reviewed fixed observations and primary LP64 declaration comparison only.
# No runtime auto-learning, caller allow flag or version-only fallback.
_PROFILES = MappingProxyType(
    {
        "ctcc.linux.x86_64.glibc239.py3123.probe21bcabc.v1": {
            "abi": {
                "byteorder": "little",
                "clock_ids": {"boottime": 7, "monotonic": 1, "realtime": 0},
                "long_size": 8,
                "machine": "x86_64",
                "offsets": {
                    "calcnt": 136,
                    "constant": 48,
                    "errcnt": 144,
                    "esterror": 32,
                    "freq": 16,
                    "jitcnt": 128,
                    "jitter": 104,
                    "maxerror": 24,
                    "modes": 0,
                    "offset": 8,
                    "ppsfreq": 96,
                    "precision": 56,
                    "shift": 112,
                    "stabil": 120,
                    "status": 40,
                    "stbcnt": 152,
                    "tai": 160,
                    "tick": 88,
                    "time": 72,
                    "tolerance": 64,
                },
                "pointer_size": 8,
                "timespec_size": 16,
                "timeval_size": 16,
                "timex_alignment": 8,
                "timex_size": 208,
            },
            "libc_path": "/usr/lib/x86_64-linux-gnu/libc.so.6",
            "localtime_path": "/usr/share/zoneinfo/Etc/UTC",
            "localtime_sha256": "8b85846791ab2c8a5463c83a5be3c043e2570d7448434d41398969ed47e3e6f2",
            "native_symbols": {
                "adjtimex": {
                    "exact_symbol_address": True,
                    "path": "/usr/lib/x86_64-linux-gnu/libc.so.6",
                    "symbol": "adjtimex",
                },
                "clock_getres": {
                    "exact_symbol_address": True,
                    "path": "/usr/lib/x86_64-linux-gnu/libc.so.6",
                    "symbol": "clock_getres",
                },
                "clock_gettime": {
                    "exact_symbol_address": True,
                    "path": "/usr/lib/x86_64-linux-gnu/libc.so.6",
                    "symbol": "clock_gettime",
                },
                "dladdr": {
                    "exact_symbol_address": True,
                    "path": "/usr/lib/x86_64-linux-gnu/libc.so.6",
                    "symbol": "dladdr",
                },
            },
            "profile_id": "ctcc.linux.x86_64.glibc239.py3123.probe21bcabc.v1",
            "provenance": {
                "compiled_header_glibc": "2.39",
                "container_glibc241_headers_compiled": False,
                "header_comparison_sha256": "38468eb24fbb8070b67e4a200eabfdf02015c928dc7cb715bbd818a907306661",
                "lp64_branch_exact_equal": True,
                "measurement_kernel_release": "6.17.0-1022-azure",
                "measurement_scope": "host",
                "pinned_container_base": None,
                "primary_headers": {
                    "2.39": {
                        "http_status": 200,
                        "lp64_branch_sha256": "648ab2266b5657ccfd486ac25bd14cff87d3648965558bd9ce2ce1a737f385d0",
                        "primary_url": "https://raw.githubusercontent.com/bminor/glibc/glibc-2.39/sysdeps/unix/sysv/linux/bits/timex.h",
                        "raw_bytes": 6198,
                        "raw_sha256": "1a214847fbda711b4a14f211961593fcb2f5879b16f2f7115b2069dcca83acd3",
                    },
                    "2.41": {
                        "http_status": 200,
                        "lp64_branch_sha256": "648ab2266b5657ccfd486ac25bd14cff87d3648965558bd9ce2ce1a737f385d0",
                        "primary_url": "https://raw.githubusercontent.com/bminor/glibc/glibc-2.41/sysdeps/unix/sysv/linux/bits/timex.h",
                        "raw_bytes": 6203,
                        "raw_sha256": "feb7e3f78aadcf5aa89233175df74e74366b0a7e79bf70f29edd24b45e817a4b",
                    },
                },
                "probe_artifact_id": 12372357313,
                "probe_artifact_sha256": "72c0f6d19f18a29361c719dbda8877a4d6dc48e1cf8f0b4b6749ae4a4dea6a83",
                "probe_c_sha256": "ab41d2b1580ac727dfd56124e2a99fb808558e459578888c08560f22dcd07e4b",
                "probe_python_sha256": "49d79ccb0334575979998aa2aaae03ec11af5c96d7d0d81904f4a200e49b5471",
                "probe_readback_sha256": "6ddda19acde6b5939f7c3585cb16c7c5e963526d3e14e9662a813f434ac0f424",
                "probe_receipt_sha256": "288579a433f2d0c33c889df574262b9d7b733cf64df209d99123a34d0c22a7a0",
                "probe_run_id": 39657125200,
                "probe_source_head": "21bcabcdc412d0528d52a120b3cf50dd57050560",
                "probe_source_tree": "1874a71bb463aeb5a0d609b5738bf2c68a4542db",
                "profile_scope": "read_only_kernel_observation_only",
                "python_version": [3, 12, 3],
                "runtime_glibc": "2.39",
                "schema_version": "ctcc.linux_clock_profile_provenance.v1",
                "source_or_execution_authority": False,
                "utc_or_upstream_freshness_authority": False,
            },
            "python_executable": "/usr/bin/python3.12",
            "runtime_files": [
                {
                    "path": "/usr/bin/python3.12",
                    "sha256": "e50d468e8b0adfb05733f5b87b3cff34829c4a8c1aea50c865aa8bdfe4bb150f",
                    "size": 8020928,
                },
                {
                    "path": "/usr/lib/python3.12/lib-dynload/_ctypes.cpython-312-x86_64-linux-gnu.so",
                    "sha256": "aad3e24f186e6c0a1c7efa8f16004f951f527db86f6451d30118657384a456c1",
                    "size": 137968,
                },
                {
                    "path": "/usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2",
                    "sha256": "c20a2dc8917c755f02b94049356320fe1f62ac7d9f8994731f807d9df39302da",
                    "size": 236616,
                },
                {
                    "path": "/usr/lib/x86_64-linux-gnu/libc.so.6",
                    "sha256": "3a15d66867d83762c7f2f1e37359cb8f6c5743edb369c65285cb0b1c4f7498bf",
                    "size": 2129424,
                },
                {
                    "path": "/usr/lib/x86_64-linux-gnu/libffi.so.8.1.4",
                    "sha256": "00f593fe192f2851b8ce23b25cec2488d769beb5a8f63e8c9e563071e1075153",
                    "size": 47672,
                },
            ],
            "runtime_roles": {
                "ctypes": "/usr/lib/python3.12/lib-dynload/_ctypes.cpython-312-x86_64-linux-gnu.so",
                "executable": "/usr/bin/python3.12",
                "libc": "/usr/lib/x86_64-linux-gnu/libc.so.6",
                "libffi": "/usr/lib/x86_64-linux-gnu/libffi.so.8.1.4",
                "loader": "/usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2",
            },
        },
        "ctcc.linux.x86_64.glibc241.py31214.base2f17fc0.v1": {
            "abi": {
                "byteorder": "little",
                "clock_ids": {"boottime": 7, "monotonic": 1, "realtime": 0},
                "long_size": 8,
                "machine": "x86_64",
                "offsets": {
                    "calcnt": 136,
                    "constant": 48,
                    "errcnt": 144,
                    "esterror": 32,
                    "freq": 16,
                    "jitcnt": 128,
                    "jitter": 104,
                    "maxerror": 24,
                    "modes": 0,
                    "offset": 8,
                    "ppsfreq": 96,
                    "precision": 56,
                    "shift": 112,
                    "stabil": 120,
                    "status": 40,
                    "stbcnt": 152,
                    "tai": 160,
                    "tick": 88,
                    "time": 72,
                    "tolerance": 64,
                },
                "pointer_size": 8,
                "timespec_size": 16,
                "timeval_size": 16,
                "timex_alignment": 8,
                "timex_size": 208,
            },
            "libc_path": "/usr/lib/x86_64-linux-gnu/libc.so.6",
            "localtime_path": "/usr/share/zoneinfo/Etc/UTC",
            "localtime_sha256": "8b85846791ab2c8a5463c83a5be3c043e2570d7448434d41398969ed47e3e6f2",
            "native_symbols": {
                "adjtimex": {
                    "exact_symbol_address": True,
                    "path": "/usr/lib/x86_64-linux-gnu/libc.so.6",
                    "symbol": "adjtimex",
                },
                "clock_getres": {
                    "exact_symbol_address": True,
                    "path": "/usr/lib/x86_64-linux-gnu/libc.so.6",
                    "symbol": "clock_getres",
                },
                "clock_gettime": {
                    "exact_symbol_address": True,
                    "path": "/usr/lib/x86_64-linux-gnu/libc.so.6",
                    "symbol": "clock_gettime",
                },
                "dladdr": {
                    "exact_symbol_address": True,
                    "path": "/usr/lib/x86_64-linux-gnu/libc.so.6",
                    "symbol": "dladdr",
                },
            },
            "profile_id": "ctcc.linux.x86_64.glibc241.py31214.base2f17fc0.v1",
            "provenance": {
                "compiled_header_glibc": "2.39",
                "container_glibc241_headers_compiled": False,
                "header_comparison_sha256": "38468eb24fbb8070b67e4a200eabfdf02015c928dc7cb715bbd818a907306661",
                "lp64_branch_exact_equal": True,
                "measurement_kernel_release": "6.17.0-1022-azure",
                "measurement_scope": "container",
                "pinned_container_base": "python:3.12-slim@sha256:2f17fc044b579bab302c2e8054d3a686e2cb9a83de48e70534b94cd8ebbe06a9",
                "primary_headers": {
                    "2.39": {
                        "http_status": 200,
                        "lp64_branch_sha256": "648ab2266b5657ccfd486ac25bd14cff87d3648965558bd9ce2ce1a737f385d0",
                        "primary_url": "https://raw.githubusercontent.com/bminor/glibc/glibc-2.39/sysdeps/unix/sysv/linux/bits/timex.h",
                        "raw_bytes": 6198,
                        "raw_sha256": "1a214847fbda711b4a14f211961593fcb2f5879b16f2f7115b2069dcca83acd3",
                    },
                    "2.41": {
                        "http_status": 200,
                        "lp64_branch_sha256": "648ab2266b5657ccfd486ac25bd14cff87d3648965558bd9ce2ce1a737f385d0",
                        "primary_url": "https://raw.githubusercontent.com/bminor/glibc/glibc-2.41/sysdeps/unix/sysv/linux/bits/timex.h",
                        "raw_bytes": 6203,
                        "raw_sha256": "feb7e3f78aadcf5aa89233175df74e74366b0a7e79bf70f29edd24b45e817a4b",
                    },
                },
                "probe_artifact_id": 12372357313,
                "probe_artifact_sha256": "72c0f6d19f18a29361c719dbda8877a4d6dc48e1cf8f0b4b6749ae4a4dea6a83",
                "probe_c_sha256": "ab41d2b1580ac727dfd56124e2a99fb808558e459578888c08560f22dcd07e4b",
                "probe_python_sha256": "49d79ccb0334575979998aa2aaae03ec11af5c96d7d0d81904f4a200e49b5471",
                "probe_readback_sha256": "cf6b4f68f11cf1d72d450c91780c9cf0a08b3bf3ecc7f68b6b8d18a305a70157",
                "probe_receipt_sha256": "288579a433f2d0c33c889df574262b9d7b733cf64df209d99123a34d0c22a7a0",
                "probe_run_id": 39657125200,
                "probe_source_head": "21bcabcdc412d0528d52a120b3cf50dd57050560",
                "probe_source_tree": "1874a71bb463aeb5a0d609b5738bf2c68a4542db",
                "profile_scope": "read_only_kernel_observation_only",
                "python_version": [3, 12, 14],
                "runtime_glibc": "2.41",
                "schema_version": "ctcc.linux_clock_profile_provenance.v1",
                "source_or_execution_authority": False,
                "utc_or_upstream_freshness_authority": False,
            },
            "python_executable": "/usr/local/bin/python3.12",
            "runtime_files": [
                {
                    "path": "/usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2",
                    "sha256": "c8438e4fde1934e61c88311633f00949ff645d5c04cdb8671fa3d78164d2f307",
                    "size": 225672,
                },
                {
                    "path": "/usr/lib/x86_64-linux-gnu/libc.so.6",
                    "sha256": "9792e3cbb541c8f44c7acf5f14f4022ea62998ecc787d326bed4d8b6547dfd92",
                    "size": 1995216,
                },
                {
                    "path": "/usr/lib/x86_64-linux-gnu/libffi.so.8.1.4",
                    "sha256": "922024ec5a23f7de1095b1cff2f6cbfb377b5d58cd51fd0ec2e8fe35427653e2",
                    "size": 47576,
                },
                {
                    "path": "/usr/local/bin/python3.12",
                    "sha256": "0e6475dfda68a9b2d93501449fc47593ca169010e8f4881577b97463fd0c1263",
                    "size": 14472,
                },
                {
                    "path": "/usr/local/lib/libpython3.12.so.1.0",
                    "sha256": "60badb35175743f87894f8f44ace744ab56fde25e71c5d551741f66f645bfa47",
                    "size": 6630672,
                },
                {
                    "path": "/usr/local/lib/python3.12/lib-dynload/_ctypes.cpython-312-x86_64-linux-gnu.so",
                    "sha256": "c4e932d98dd779c09aee18533e04b5c6e5b6214878f3bc0ba867e5559fa5d5d1",
                    "size": 129536,
                },
            ],
            "runtime_roles": {
                "ctypes": "/usr/local/lib/python3.12/lib-dynload/_ctypes.cpython-312-x86_64-linux-gnu.so",
                "executable": "/usr/local/bin/python3.12",
                "libc": "/usr/lib/x86_64-linux-gnu/libc.so.6",
                "libffi": "/usr/lib/x86_64-linux-gnu/libffi.so.8.1.4",
                "libpython": "/usr/local/lib/libpython3.12.so.1.0",
                "loader": "/usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2",
            },
        },
    }
)


class LinuxClockError(PublicReceiptError):
    """A fixed code; no environment, path, credential or exception text leaks."""


class LinuxKernelStatusError(LinuxClockError):
    """Retain an unsuccessful read-only status without issuing a healthy token."""

    def __init__(self, code, record):
        super().__init__(code)
        self.observation_bytes = canonical(record)


class _Timeval(ctypes.LittleEndianStructure):
    _fields_ = (("tv_sec", ctypes.c_int64), ("tv_usec", ctypes.c_int64))


class _Timespec(ctypes.LittleEndianStructure):
    _fields_ = (("tv_sec", ctypes.c_int64), ("tv_nsec", ctypes.c_int64))


class _Timex(ctypes.LittleEndianStructure):
    _fields_ = (
        ("modes", ctypes.c_uint32),
        ("_pad0", ctypes.c_uint32),
        ("offset", ctypes.c_int64),
        ("freq", ctypes.c_int64),
        ("maxerror", ctypes.c_int64),
        ("esterror", ctypes.c_int64),
        ("status", ctypes.c_int32),
        ("_pad1", ctypes.c_uint32),
        ("constant", ctypes.c_int64),
        ("precision", ctypes.c_int64),
        ("tolerance", ctypes.c_int64),
        ("time", _Timeval),
        ("tick", ctypes.c_int64),
        ("ppsfreq", ctypes.c_int64),
        ("jitter", ctypes.c_int64),
        ("shift", ctypes.c_int32),
        ("_pad2", ctypes.c_uint32),
        ("stabil", ctypes.c_int64),
        ("jitcnt", ctypes.c_int64),
        ("calcnt", ctypes.c_int64),
        ("errcnt", ctypes.c_int64),
        ("stbcnt", ctypes.c_int64),
        ("tai", ctypes.c_int32),
        ("_reserved", ctypes.c_uint32 * 11),
    )


class _DlInfo(ctypes.Structure):
    _fields_ = (
        ("filename", ctypes.c_char_p),
        ("base", ctypes.c_void_p),
        ("symbol", ctypes.c_char_p),
        ("address", ctypes.c_void_p),
    )


def _abi_layout():
    return {
        **_ABI,
        "long_size": ctypes.sizeof(ctypes.c_long),
        "pointer_size": ctypes.sizeof(ctypes.c_void_p),
        "timex_size": ctypes.sizeof(_Timex),
        "timex_alignment": ctypes.alignment(_Timex),
        "timespec_size": ctypes.sizeof(_Timespec),
        "timeval_size": ctypes.sizeof(_Timeval),
        "offsets": {name: getattr(_Timex, name).offset for name in _ABI["offsets"]},
    }


def _keys(value, expected, code="linux_clock_evidence_invalid"):
    if type(value) is not dict or set(value) != set(expected):
        raise LinuxClockError(code)


def _integer(value, low, high, code="linux_clock_evidence_invalid"):
    if type(value) is not int or not low <= value <= high:
        raise LinuxClockError(code)
    return value


def _read_fixed(path, limit, *, regular=False):
    """Fixed service paths only; last component cannot be a symlink."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        try:
            before = os.fstat(fd)
            if regular and (
                not stat.S_ISREG(before.st_mode)
                or before.st_uid != 0
                or before.st_mode & 0o022
            ):
                raise LinuxClockError("linux_clock_runtime_file_unsafe")
            parts, size = [], 0
            while part := os.read(fd, min(65536, limit + 1 - size)):
                parts.append(part)
                size += len(part)
                if size > limit:
                    raise LinuxClockError("linux_clock_read_budget")
            after = os.fstat(fd)
            if (
                before.st_dev,
                before.st_ino,
                before.st_size,
                before.st_mtime_ns,
                before.st_ctime_ns,
            ) != (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            ):
                raise LinuxClockError("linux_clock_runtime_changed")
            return b"".join(parts), before
        finally:
            os.close(fd)
    except LinuxClockError:
        raise
    except (OSError, ValueError):
        raise LinuxClockError("linux_clock_native_read_failed") from None


def _environment_guard():
    if sys.platform != "linux" or os.name != "posix":
        raise LinuxClockError("linux_clock_platform_unsupported")
    if (
        sys.byteorder != "little"
        or os.uname().machine != "x86_64"
        or _abi_layout() != _ABI
    ):
        raise LinuxClockError("linux_clock_abi_unsupported")
    if any(
        name.startswith("LD_") or name in {"GLIBC_TUNABLES", "TZ", "LIBC_FATAL_STDERR_"}
        for name in os.environ
    ):
        raise LinuxClockError("linux_clock_environment_override")
    if sys.gettrace() is not None or sys.getprofile() is not None:
        raise LinuxClockError("linux_clock_python_hook")
    if not all(
        inspect.isbuiltin(function)
        for function in (
            os.open,
            os.read,
            os.fstat,
            os.close,
            os.getpid,
            os.readlink,
            os.uname,
        )
    ):
        raise LinuxClockError("linux_clock_python_hook")


def _runtime_snapshot(profile):
    _environment_guard()
    _keys(
        profile,
        {
            "profile_id",
            "runtime_files",
            "runtime_roles",
            "python_executable",
            "libc_path",
            "native_symbols",
            "localtime_path",
            "localtime_sha256",
            "abi",
            "provenance",
        },
        "linux_clock_profile_invalid",
    )
    if canonical(profile["abi"]) != canonical(_ABI):
        raise LinuxClockError("linux_clock_profile_invalid")
    if (
        type(profile["runtime_files"]) is not list
        or type(profile["runtime_roles"]) is not dict
        or type(profile["native_symbols"]) is not dict
        or set(profile["native_symbols"])
        != {"clock_gettime", "clock_getres", "adjtimex", "dladdr"}
    ):
        raise LinuxClockError("linux_clock_profile_invalid")
    roles = profile["runtime_roles"]
    required_roles = {"executable", "libc", "loader", "ctypes", "libffi"}
    if (
        set(roles) not in (required_roles, required_roles | {"libpython"})
        or any(type(path) is not str for path in roles.values())
        or len(set(roles.values())) != len(roles)
        or roles["executable"] != profile["python_executable"]
        or roles["libc"] != profile["libc_path"]
    ):
        raise LinuxClockError("linux_clock_profile_roles_invalid")
    paths = [
        item.get("path") for item in profile["runtime_files"] if type(item) is dict
    ]
    if (
        len(paths) != len(profile["runtime_files"])
        or len(set(paths)) != len(paths)
        or set(paths) != set(roles.values())
        or str(Path(f"/proc/{os.getpid()}/exe").resolve(strict=True))
        != profile["python_executable"]
        or str(Path(sys.executable).resolve(strict=True))
        != profile["python_executable"]
        or str(Path(_ctypes.__file__).resolve(strict=True)) != roles["ctypes"]
    ):
        raise LinuxClockError("linux_clock_profile_invalid")
    identities = []
    maps_raw, _ = _read_fixed(f"/proc/{os.getpid()}/maps", 1024 * 1024)
    try:
        maps = maps_raw.decode("ascii")
    except UnicodeError:
        raise LinuxClockError("linux_clock_mapping_invalid") from None
    for item in profile["runtime_files"]:
        _keys(item, {"path", "sha256", "size"}, "linux_clock_profile_invalid")
        path = item["path"]
        if (
            type(path) is not str
            or not path.startswith("/")
            or Path(path).as_posix() != path
            or _SHA.fullmatch(item["sha256"]) is None
        ):
            raise LinuxClockError("linux_clock_profile_invalid")
        raw, info = _read_fixed(path, _MAX_RUNTIME_FILE, regular=True)
        if len(raw) != item["size"] or sha(raw) != item["sha256"]:
            raise LinuxClockError("linux_clock_runtime_profile_mismatch")
        matching = []
        for line in maps.splitlines():
            parts = line.split(maxsplit=5)
            if len(parts) == 6 and parts[5] == path:
                try:
                    major, minor = (int(value, 16) for value in parts[3].split(":"))
                    identity = (os.makedev(major, minor), int(parts[4]))
                except (ValueError, OSError):
                    raise LinuxClockError("linux_clock_mapping_invalid") from None
                if (
                    identity != (info.st_dev, info.st_ino)
                    or "w" in parts[1]
                    and "x" in parts[1]
                ):
                    raise LinuxClockError("linux_clock_mapping_invalid")
                matching.append(parts[1])
        if not matching or not any("x" in value for value in matching):
            raise LinuxClockError("linux_clock_mapping_missing")
        identities.append(
            {
                "path": path,
                "sha256": item["sha256"],
                "size": len(raw),
                "device": info.st_dev,
                "inode": info.st_ino,
            }
        )
    # The profile pins the resolved, root-owned UTC zone file, not caller TZ.
    timezone, _ = _read_fixed(profile["localtime_path"], 4096, regular=True)
    try:
        localtime = str(Path("/etc/localtime").resolve(strict=True))
    except OSError:
        raise LinuxClockError("linux_clock_timezone_unknown") from None
    if (
        localtime != profile["localtime_path"]
        or sha(timezone) != profile["localtime_sha256"]
    ):
        raise LinuxClockError("linux_clock_timezone_unknown")
    boot, _ = _read_fixed("/proc/sys/kernel/random/boot_id", 64)
    if re.fullmatch(rb"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\n", boot) is None:
        raise LinuxClockError("linux_clock_boot_identity_invalid")
    pid_root = f"/proc/{os.getpid()}"
    offsets, _ = _read_fixed(pid_root + "/timens_offsets", 256)
    if re.fullmatch(rb"monotonic\s+0\s+0\nboottime\s+0\s+0\n", offsets) is None:
        raise LinuxClockError("linux_clock_namespace_offset")
    try:
        namespace = os.readlink(pid_root + "/ns/time")
    except OSError:
        raise LinuxClockError("linux_clock_namespace_unknown") from None
    if re.fullmatch(r"time:\[[1-9][0-9]{0,19}\]", namespace) is None:
        raise LinuxClockError("linux_clock_namespace_unknown")
    kernel = os.uname()
    if (
        kernel.machine != "x86_64"
        or re.fullmatch(r"[0-9A-Za-z._+\-]{1,128}", kernel.release) is None
    ):
        raise LinuxClockError("linux_clock_kernel_identity_invalid")
    return {
        "runtime_files": identities,
        "boot_id_sha256": sha(boot),
        "time_namespace": namespace,
        "namespace_offsets_ascii": offsets.decode("ascii"),
        "namespace_offsets_sha256": sha(offsets),
        "timezone_id": "Etc/UTC",
        "utc_offset_minutes": 0,
        "kernel_release": kernel.release,
        "machine": kernel.machine,
    }


class _NativeReader:
    """No supplied path, clock, status provider, callback, or trust flag."""

    def __init__(self):
        _environment_guard()
        selected = None
        # Profile mismatch is not repaired by learning currently installed bytes.
        for candidate in _PROFILES.values():
            try:
                snapshot = _runtime_snapshot(candidate)
            except LinuxClockError:
                continue
            if selected is not None:
                raise LinuxClockError("linux_clock_profile_ambiguous")
            selected = (candidate, snapshot)
        if selected is None:
            raise LinuxClockError("linux_clock_profile_unsupported")
        self.profile, self.before = selected
        try:
            self.library = ctypes.CDLL(self.profile["libc_path"], use_errno=True)
            self.clock_gettime = self.library.clock_gettime
            self.clock_getres = self.library.clock_getres
            self.adjtimex = self.library.adjtimex
            self.dladdr = self.library.dladdr
            self.clock_gettime.argtypes = (ctypes.c_int, ctypes.POINTER(_Timespec))
            self.clock_getres.argtypes = (ctypes.c_int, ctypes.POINTER(_Timespec))
            self.adjtimex.argtypes = (ctypes.POINTER(_Timex),)
            self.dladdr.argtypes = (ctypes.c_void_p, ctypes.POINTER(_DlInfo))
            for function in (
                self.clock_gettime,
                self.clock_getres,
                self.adjtimex,
                self.dladdr,
            ):
                function.restype = ctypes.c_int
        except (OSError, AttributeError):
            raise LinuxClockError("linux_clock_native_api_unavailable") from None
        self.guard()

    def guard(self):
        if _runtime_snapshot(self.profile) != self.before:
            raise LinuxClockError("linux_clock_runtime_changed")
        for name in ("clock_gettime", "clock_getres", "adjtimex", "dladdr"):
            function = getattr(self, name)
            if not isinstance(function, ctypes._CFuncPtr):
                raise LinuxClockError("linux_clock_native_hook")
            value = _DlInfo()
            if (
                self.dladdr(ctypes.cast(function, ctypes.c_void_p), ctypes.byref(value))
                != 1
            ):
                raise LinuxClockError("linux_clock_native_hook")
            try:
                origin = str(Path(value.filename.decode("ascii")).resolve(strict=True))
            except (OSError, AttributeError, UnicodeError):
                raise LinuxClockError("linux_clock_native_hook") from None
            pinned = self.profile["native_symbols"].get(name)
            if (
                pinned is None
                or origin != self.profile["libc_path"]
                or origin != pinned["path"]
                or value.symbol != pinned["symbol"].encode("ascii")
                or value.address != ctypes.cast(function, ctypes.c_void_p).value
                or pinned["exact_symbol_address"] is not True
            ):
                raise LinuxClockError("linux_clock_native_hook")

    def clock(self, clock_id, *, resolution=False):
        value = _Timespec()
        function = self.clock_getres if resolution else self.clock_gettime
        if (
            function(clock_id, ctypes.byref(value)) != 0
            or not 0 <= value.tv_nsec < 1_000_000_000
            or value.tv_sec < 0
        ):
            raise LinuxClockError("linux_clock_native_api_failed")
        return _integer(
            value.tv_sec * 1_000_000_000 + value.tv_nsec, 0, MAX_TIMESTAMP_NS
        )

    def sample(self):
        before = self.clock(1)
        wall = self.clock(0)
        boot = self.clock(7)
        after = self.clock(1)
        value = {
            "utc_ns": wall,
            "monotonic_ns": (before + after) // 2,
            "monotonic_before_ns": before,
            "monotonic_after_ns": after,
            "boottime_ns": boot,
        }
        _sample(value)
        return value

    def status(self):
        value = _Timex()  # Every input byte, including modes, is zero.
        ctypes.set_errno(0)
        result = self.adjtimex(ctypes.byref(value))
        error = ctypes.get_errno()
        return result, error, bytes(value)


def _sample(value):
    _keys(
        value,
        {
            "utc_ns",
            "monotonic_ns",
            "monotonic_before_ns",
            "monotonic_after_ns",
            "boottime_ns",
        },
    )
    for number in value.values():
        _integer(number, 0, MAX_TIMESTAMP_NS)
    before, after = value["monotonic_before_ns"], value["monotonic_after_ns"]
    if (
        after < before
        or after - before > MAX_SAMPLE_BRACKET_NS
        or value["monotonic_ns"] != (before + after) // 2
        or value["boottime_ns"] < before
    ):
        raise LinuxClockError("linux_clock_sample_unbounded")
    # Integer UTC conversion, no float timestamp or caller timezone correction.
    wall = _EPOCH + timedelta(microseconds=value["utc_ns"] // 1000)
    if wall.utcoffset() != timedelta(0) or wall.tzinfo is not UTC:
        raise LinuxClockError("linux_clock_utc_conversion_failed")
    return {key: value[key] for key in ("utc_ns", "monotonic_ns")}


def _sample_sequence(samples):
    """The same complete bracket, boot, jump and lease rules at every read."""
    if type(samples) is not list or not 2 <= len(samples) <= 4:
        raise LinuxClockError("linux_clock_evidence_invalid")
    checked = [_sample(value) for value in samples]
    validate_stamps(checked)
    first, last = samples[0], samples[-1]
    elapsed = last["monotonic_after_ns"] - first["monotonic_before_ns"]
    if elapsed < 0 or elapsed > MAX_OBSERVATION_NS:
        raise LinuxClockError("linux_clock_probe_expired")
    first_suspend = first["boottime_ns"] - first["monotonic_ns"]
    for previous, sample in pairwise(samples):
        if (
            sample["monotonic_before_ns"] < previous["monotonic_after_ns"]
            or sample["boottime_ns"] < previous["boottime_ns"]
            or abs((sample["boottime_ns"] - sample["monotonic_ns"]) - first_suspend)
            > MAX_CLOCK_DEVIATION_NS
        ):
            raise LinuxClockError("linux_clock_suspend_or_reversed")
    return elapsed


def _raw_status(record):
    _keys(record, {"return_state", "errno", "raw_base64", "raw_sha256", "raw_length"})
    _integer(record["return_state"], -1, 5)
    _integer(record["errno"], 0, 4095)
    if (
        record["raw_length"] != 208
        or type(record["raw_length"]) is not int
        or type(record["raw_base64"]) is not str
        or len(record["raw_base64"]) != 280
    ):
        raise LinuxClockError("linux_clock_status_invalid")
    try:
        raw = base64.b64decode(record["raw_base64"], validate=True)
    except ValueError:
        raise LinuxClockError("linux_clock_status_invalid") from None
    if (
        len(raw) != 208
        or base64.b64encode(raw).decode("ascii") != record["raw_base64"]
        or sha(raw) != record["raw_sha256"]
    ):
        raise LinuxClockError("linux_clock_status_invalid")
    value = _Timex.from_buffer_copy(raw)
    if record["return_state"] == -1 or record["errno"] != 0:
        raise LinuxKernelStatusError("linux_clock_native_api_failed", record)
    # STA_PLL controls PLL updates; absence is not an UNSYNC declaration.
    # TIME_OK remains necessary. All leap, hold, FLL/PPS, UNSYNC/CLOCKERR,
    # unknown and error bits deny; STA_NANO changes only representation units.
    if (
        record["return_state"] != 0
        or value.modes != 0
        or value.status not in (0x0000, 0x0001, 0x2000, 0x2001)
    ):
        raise LinuxKernelStatusError("linux_clock_kernel_unsynchronized", record)
    if (
        not 0 <= value.maxerror <= 5000
        or not 0 <= value.esterror <= value.maxerror
        or not 0 <= value.precision <= 5000
        or not 0 < value.tolerance <= 32_768_000
    ):
        raise LinuxClockError("linux_clock_uncertainty_unbounded")
    unit = 1 if value.status & 0x2000 else 1000
    if value.time.tv_sec < 0 or not 0 <= value.time.tv_usec < (
        1_000_000_000 if unit == 1 else 1_000_000
    ):
        raise LinuxClockError("linux_clock_status_time_invalid")
    return value, unit


def replay_linux_clock_observation_v1(payload: bytes):
    """Strict portable replay proves matching bytes, never native issuance."""
    if type(payload) is not bytes or not 0 < len(payload) <= MAX_PAYLOAD_BYTES:
        raise LinuxClockError("linux_clock_evidence_invalid")
    data = decode(payload, MAX_PAYLOAD_BYTES)
    if canonical(data) != payload:
        raise LinuxClockError("linux_clock_evidence_noncanonical")
    _keys(
        data,
        {
            "schema_version",
            "profile_id",
            "profile_sha256",
            "profile_provenance",
            "abi",
            "host_before",
            "host_after",
            "samples",
            "resolutions_ns",
            "status",
            "uncertainty_bound_ns",
            "pll_updates_enabled",
            "upstream_server",
            "last_upstream_sync_at",
            "utc_certified",
            "execution_authority",
        },
    )
    profile = (
        _PROFILES.get(data["profile_id"]) if type(data["profile_id"]) is str else None
    )
    if (
        profile is None
        or data["schema_version"] != SCHEMA_VERSION
        or data["profile_sha256"] != sha(canonical(profile))
        or canonical(data["profile_provenance"]) != canonical(profile["provenance"])
        or canonical(data["abi"]) != canonical(_ABI)
    ):
        raise LinuxClockError("linux_clock_profile_unsupported")
    if (
        data["upstream_server"] is not None
        or data["last_upstream_sync_at"] is not None
        or data["utc_certified"] is not False
        or data["execution_authority"] is not False
    ):
        raise LinuxClockError("linux_clock_authority_forbidden")
    before, after = data["host_before"], data["host_after"]
    expected_host_keys = {
        "runtime_files",
        "boot_id_sha256",
        "time_namespace",
        "namespace_offsets_ascii",
        "namespace_offsets_sha256",
        "timezone_id",
        "utc_offset_minutes",
        "kernel_release",
        "machine",
    }
    for host in (before, after):
        _keys(host, expected_host_keys)
        if (
            host["timezone_id"] != "Etc/UTC"
            or type(host["utc_offset_minutes"]) is not int
            or host["utc_offset_minutes"] != 0
            or type(host["boot_id_sha256"]) is not str
            or _SHA.fullmatch(host["boot_id_sha256"]) is None
            or type(host["namespace_offsets_sha256"]) is not str
            or _SHA.fullmatch(host["namespace_offsets_sha256"]) is None
            or type(host["time_namespace"]) is not str
            or re.fullmatch(r"time:\[[1-9][0-9]{0,19}\]", host["time_namespace"])
            is None
            or host["machine"] != "x86_64"
            or type(host["kernel_release"]) is not str
            or re.fullmatch(r"[0-9A-Za-z._+\-]{1,128}", host["kernel_release"]) is None
        ):
            raise LinuxClockError("linux_clock_host_invalid")
        if (
            type(host["namespace_offsets_ascii"]) is not str
            or re.fullmatch(
                r"monotonic[ \t]+0[ \t]+0\nboottime[ \t]+0[ \t]+0\n",
                host["namespace_offsets_ascii"],
            )
            is None
            or sha(host["namespace_offsets_ascii"].encode("ascii"))
            != host["namespace_offsets_sha256"]
        ):
            raise LinuxClockError("linux_clock_namespace_offset")
        if type(host["runtime_files"]) is not list or len(host["runtime_files"]) != len(
            profile["runtime_files"]
        ):
            raise LinuxClockError("linux_clock_host_invalid")
        for actual, pinned in zip(
            host["runtime_files"], profile["runtime_files"], strict=True
        ):
            _keys(actual, {"path", "sha256", "size", "device", "inode"})
            if canonical(
                {key: actual[key] for key in ("path", "sha256", "size")}
            ) != canonical(pinned):
                raise LinuxClockError("linux_clock_runtime_profile_mismatch")
            _integer(actual["device"], 0, 2**64 - 1)
            _integer(actual["inode"], 1, 2**64 - 1)
    if before != after:
        raise LinuxClockError("linux_clock_runtime_changed")
    samples = data["samples"]
    if type(samples) is not list or len(samples) != 3:
        raise LinuxClockError("linux_clock_evidence_invalid")
    elapsed = _sample_sequence(samples)
    resolutions = data["resolutions_ns"]
    _keys(resolutions, {"realtime", "monotonic", "boottime"})
    for resolution in resolutions.values():
        _integer(resolution, 1, MAX_RESOLUTION_NS, "linux_clock_resolution_unsupported")
    value, unit = _raw_status(data["status"])
    if type(data["pll_updates_enabled"]) is not bool or data[
        "pll_updates_enabled"
    ] != bool(value.status & 0x0001):
        raise LinuxClockError("linux_clock_pll_mode_mismatch")
    status_start = value.time.tv_sec * 1_000_000_000 + value.time.tv_usec * unit
    # A microsecond timestamp represents an interval, not a fabricated later ns.
    if (
        status_start + unit - 1 < samples[0]["utc_ns"]
        or status_start > samples[1]["utc_ns"]
    ):
        raise LinuxClockError("linux_clock_status_causal_order")
    # maxerror is the kernel estimate, not an independent UTC certificate.
    # Count magnitude of correction, reported precision, full sample brackets and
    # worst reported frequency tolerance throughout this observation. Round up.
    drift = (elapsed * value.tolerance + 65_536_000_000 - 1) // 65_536_000_000
    bound = (
        value.maxerror * 1000
        + abs(value.offset) * unit
        + value.precision * 1000
        + max(resolutions.values())
        + sum(
            item["monotonic_after_ns"] - item["monotonic_before_ns"] for item in samples
        )
        + drift
    )
    if (
        type(data["uncertainty_bound_ns"]) is not int
        or data["uncertainty_bound_ns"] != bound
        or bound > MAX_CLOCK_DEVIATION_NS
    ):
        raise LinuxClockError("linux_clock_uncertainty_unbounded")
    return data


def _observe(reader):
    first = reader.sample()
    state, error, raw = reader.status()
    second = reader.sample()
    resolutions = {
        name: reader.clock(clock_id, resolution=True)
        for name, clock_id in _ABI["clock_ids"].items()
    }
    reader.guard()
    last = reader.sample()
    record = {
        "return_state": state,
        "errno": error,
        "raw_base64": base64.b64encode(raw).decode("ascii"),
        "raw_sha256": sha(raw),
        "raw_length": len(raw),
    }
    value, unit = _raw_status(record)
    elapsed = last["monotonic_after_ns"] - first["monotonic_before_ns"]
    drift = (elapsed * value.tolerance + 65_536_000_000 - 1) // 65_536_000_000
    samples = [first, second, last]
    bound = (
        value.maxerror * 1000
        + abs(value.offset) * unit
        + value.precision * 1000
        + max(resolutions.values())
        + sum(
            item["monotonic_after_ns"] - item["monotonic_before_ns"] for item in samples
        )
        + drift
    )
    data = {
        "schema_version": SCHEMA_VERSION,
        "profile_id": reader.profile["profile_id"],
        "profile_sha256": sha(canonical(reader.profile)),
        "profile_provenance": reader.profile["provenance"],
        "abi": _ABI,
        "host_before": reader.before,
        "host_after": reader.before,
        "samples": samples,
        "resolutions_ns": resolutions,
        "status": record,
        "uncertainty_bound_ns": bound,
        "pll_updates_enabled": bool(value.status & 0x0001),
        "upstream_server": None,
        "last_upstream_sync_at": None,
        "utc_certified": False,
        "execution_authority": False,
    }
    payload = canonical(data)
    replay_linux_clock_observation_v1(payload)
    return payload


_ISSUER = object()
_OWNED = WeakKeyDictionary()
_LOCK = Lock()


class OwnedLinuxClockObservationV1:
    __slots__ = ("__weakref__",)

    def __init__(self, issuer):
        if issuer is not _ISSUER:
            raise LinuxClockError("linux_clock_owned_observation_required")

    def __copy__(self):
        raise LinuxClockError("linux_clock_owned_observation_not_transferable")

    def __deepcopy__(self, memo):
        raise LinuxClockError("linux_clock_owned_observation_not_transferable")

    def __reduce_ex__(self, protocol):
        raise LinuxClockError("linux_clock_owned_observation_not_transferable")


def observe_linux_clock_v1() -> OwnedLinuxClockObservationV1:
    """Fresh native acquisition only; no inputs and no default-dispatch wiring."""
    reader = _NativeReader()
    payload = _observe(reader)
    token = OwnedLinuxClockObservationV1(_ISSUER)
    with _LOCK:
        _OWNED[token] = (os.getpid(), get_ident(), reader, payload)
    return token


def consume_owned_linux_clock_v1(token: OwnedLinuxClockObservationV1) -> bytes:
    """One use on the issuing process/thread, within the original 5s lease."""
    if type(token) is not OwnedLinuxClockObservationV1:
        raise LinuxClockError("linux_clock_owned_observation_required")
    with _LOCK:
        retained = _OWNED.pop(token, None)
    if retained is None or retained[:2] != (os.getpid(), get_ident()):
        raise LinuxClockError("linux_clock_owned_observation_required")
    _, _, reader, payload = retained
    reader.guard()
    current = reader.sample()
    data = replay_linux_clock_observation_v1(payload)
    _sample_sequence([*data["samples"], current])
    return payload


__all__ = (
    "LinuxClockError",
    "OwnedLinuxClockObservationV1",
    "consume_owned_linux_clock_v1",
    "observe_linux_clock_v1",
    "replay_linux_clock_observation_v1",
)
