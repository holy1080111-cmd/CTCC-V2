"""Credential-free runtime/profile measurement; does not grant clock admission.

Run on Linux with the reviewed C probe path as its sole argument. For Docker,
run the same executable inside the exact pinned base, with no network, host
environment, credentials, additional capabilities, or clock/service writes.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys

BASE_IMAGE = "python:3.12-slim@sha256:2f17fc044b579bab302c2e8054d3a686e2cb9a83de48e70534b94cd8ebbe06a9"


class _DlInfo(ctypes.Structure):
    _fields_ = (
        ("filename", ctypes.c_char_p),
        ("base", ctypes.c_void_p),
        ("symbol", ctypes.c_char_p),
        ("address", ctypes.c_void_p),
    )


def main() -> None:
    if sys.platform != "linux" or len(sys.argv) != 2:
        raise SystemExit("Linux and one exact C probe executable required")
    # Force ctypes/libffi mappings into the measured Python process.
    _ = ctypes.c_long()
    maps = Path("/proc/self/maps").read_text(encoding="ascii")
    selected = {str(Path(sys.executable).resolve())}
    for line in maps.splitlines():
        parts = line.split(maxsplit=5)
        if (
            len(parts) == 6
            and parts[5].startswith("/")
            and any(
                marker in Path(parts[5]).name
                for marker in (
                    "libc.so",
                    "ld-linux",
                    "libpython",
                    "_ctypes.",
                    "libffi.so",
                )
            )
        ):
            selected.add(str(Path(parts[5]).resolve(strict=True)))
    files = []
    for name in sorted(selected):
        path = Path(name)
        data = path.read_bytes()
        info = path.stat()
        if len(data) > 64 * 1024 * 1024:
            raise SystemExit("runtime file budget exceeded")
        files.append(
            {
                "path": name,
                "sha256": hashlib.sha256(data).hexdigest(),
                "size": len(data),
                "uid": info.st_uid,
                "mode": info.st_mode & 0o7777,
            }
        )
    libc_paths = [
        item["path"] for item in files if Path(item["path"]).name == "libc.so.6"
    ]
    if len(libc_paths) != 1:
        raise SystemExit("exact mapped glibc required")
    library = ctypes.CDLL(libc_paths[0])
    symbols = {}
    resolver = library.dladdr
    resolver.argtypes = (ctypes.c_void_p, ctypes.POINTER(_DlInfo))
    resolver.restype = ctypes.c_int
    for name in ("clock_gettime", "clock_getres", "adjtimex", "dladdr"):
        function = getattr(library, name)
        info = _DlInfo()
        pointer = ctypes.cast(function, ctypes.c_void_p)
        if resolver(pointer, ctypes.byref(info)) != 1:
            raise SystemExit("native symbol origin unavailable")
        symbols[name] = {
            "path": str(Path(info.filename.decode("ascii")).resolve(strict=True)),
            "symbol": info.symbol.decode("ascii"),
            "exact_symbol_address": info.address == pointer.value,
        }
    result = subprocess.run(
        [str(Path(sys.argv[1]).resolve(strict=True))],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=3,
        check=True,
        env={"PATH": "/usr/bin:/bin", "LANG": "C"},
    )
    if len(result.stdout) > 32768 or result.stderr:
        raise SystemExit("C probe output rejected")
    value = {
        "schema_version": "ctcc.linux_clock_runtime_profile_probe.v1",
        "scope": "measurement_not_clock_admission",
        "pinned_candidate_base": BASE_IMAGE,
        "machine": platform.machine(),
        "byteorder": sys.byteorder,
        "python": list(sys.version_info[:3]),
        "kernel_release": os.uname().release,
        "runtime_files": files,
        "native_symbols": symbols,
        "localtime_path": str(Path("/etc/localtime").resolve(strict=True)),
        "localtime_sha256": hashlib.sha256(
            Path("/etc/localtime").read_bytes()
        ).hexdigest(),
        "timens_offsets": Path("/proc/self/timens_offsets").read_text(encoding="ascii"),
        "c_probe": json.loads(result.stdout),
    }
    print(json.dumps(value, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
