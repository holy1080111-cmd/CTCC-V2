"""One Linux preserved-cluster bootstrap; no receipt/listener adoption API.

The source-owned supervisor keeps its actual PG child and native directory
handles throughout a bounded maintenance operation. These private primitives
canonicalize the reviewed N400/N397 lifecycle; no validation-results file is
imported or executed. Operator apply remains inhibited until the complete
runtime provider, live lease and owner transaction wiring have been verified.
"""

from __future__ import annotations

import asyncio
import ctypes
import hashlib
import io
import json
import os
import posixpath
import re
import signal
import ssl
import stat
import subprocess
import sys
import tarfile
import threading
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PG_IMAGE = "sha256:f02121de6f74d30d8a94cd1d9584125e2178d7e6c377d8130112d4e52d867995"
PG_TREE_SHA256 = "a4413022a5e18d5c8b4e3221be80bd7d21aa13c85093ae1adb7e01423da78556"
PG_INVENTORY_SHA256 = "603f68e5f1059ea01a25ecc0a79a0f7f041b4640a24f5f16fcc3b3b40524f382"
PYTHON_TREE_SHA256 = "a601e5ccc5a8a43752448bf394ee481e18b8048b085300dc448b5192186439f4"
PYTHON_INVENTORY_SHA256 = (
    "9b79bfd725a69bbf9ae2d386c3219f5110ffbc2efa5e4b7435552043c87c0a6a"
)
VENDOR_TREE_SHA256 = "f2864cee254e795416117789eda90fd97d6d3c9492fde7090737d08f34a3ae28"
VENDOR_LOCATOR_SHA256 = (
    "3923f75fe08d42df2478a4a9d19e4225ae38ab4621a5caaf80b66982e2e6b9db"
)
DEPENDENCY_INPUT_SHA256 = (
    "cc4eec913361243481206d76a87d9f9b76296d2ab9df85ea018c4891792f2caf"
)
LOCK_SHA256 = "a44e649cb1d21d3790e132c21699cc172e77ed87682929320cf8f6190fe014e4"
PYPROJECT_SHA256 = "aa387d04aad4e5a07538a40615cb98cf7550db5e3d8df83d7e4c795f9fdbaa2a"
PYTHON_ELF_SHA256 = "0e6475dfda68a9b2d93501449fc47593ca169010e8f4881577b97463fd0c1263"
PRESERVED_MANIFEST_SHA256 = (
    "8ef08dd08ffe2e0e72295c975c068b31234e31091e3f2ad7899525f2dc632b86"
)
PRESERVED_BACKUP_SHA256 = (
    "75515c6478eb4c14ccb46b0475db4e2ba8b7d4df9855bf114e0a4e8a7b6b131d"
)
PRESERVED_CONTROL_SHA256 = (
    "f01f1ec5c3ed1d498dfc910d8488a405189046b3059bba93fa030995d1004d8f"
)
SOURCE17_PREFIX_SHA256 = (
    "7a998b64b61f5bb30d65c405a995f3d00ffc12d15344b7cbda309f1c58b17cd2"
)
PRESERVED_DOMAIN = b"ctcc.retained_pg.cold_preserved_8ef.v1\x00"
FRESH_FIXTURE_DOMAIN = b"ctcc.retained_pg.cold_canonical17_fixture.v1\x00"
BOOTSTRAP_SECONDS = 60
OWNER_SECONDS = 600  # Existing owner API budget, unrelated to trading clocks.
CLEANUP_SECONDS = 20
PORT = 15439
_HEX = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,62}\Z", re.ASCII)
_GIT = re.compile(r"[0-9a-f]{40}\Z", re.ASCII)
_ABSENT = frozenset(
    {
        "standby.signal",
        "recovery.signal",
        "postmaster.pid",
        "backup_label",
        "tablespace_map",
    }
)


class RetainedBootstrapRefused(ValueError):
    pass


class RetainedRuntimeProviderPending(RetainedBootstrapRefused):
    pass


def _need(value, code="retained_pg_bootstrap_refused"):
    if value is not True:
        raise RetainedBootstrapRefused(code) from None


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("ascii")


class _Origin(Enum):
    PRESERVED = "preserved_8ef_cold_cluster"
    FRESH_FIXTURE = "fresh_canonical17_cold_fixture"


class _Phase(Enum):
    READY = "ready"
    CLAIMED = "claimed"
    REHEARSAL = "rehearsal"
    ROLLED_BACK = "rolled_back"
    FINAL = "final"
    COMMIT_ATTEMPTED = "commit_attempted"
    READBACK_ONLY = "readback_only"
    UNKNOWN = "unknown"
    CLOSED = "closed"


@dataclass(frozen=True, slots=True)
class RetainedBootstrapFilesV1:
    """Private resource locations are DATA; this constructor grants nothing."""

    pg_rootfs: str
    pg_inventory_file: str
    python_rootfs: str
    python_inventory_file: str
    cold_directory: str
    cold_manifest_file: str
    private_output_parent: str
    ca_certificate_file: str
    server_certificate_file: str
    server_private_key_file: str
    runtime_provider_file: str
    source_archive_file: str


@dataclass(frozen=True, slots=True)
class FreshCanonical17ResourcesV1:
    """Fresh fixture resources contain no caller-selected PGDATA/manifest."""

    pg_rootfs: str
    pg_inventory_file: str
    python_rootfs: str
    python_inventory_file: str
    private_output_parent: str
    ca_certificate_file: str
    server_certificate_file: str
    server_private_key_file: str
    runtime_provider_file: str
    source_archive_file: str


class OwnedRetainedBootstrapV1:
    """No public constructor, fields, deserializer or listener adoption."""

    __slots__ = ()

    def __new__(cls, *_args, **_kwargs):
        raise RetainedBootstrapRefused(
            "retained_pg_bootstrap_not_constructible"
        ) from None

    def __repr__(self):
        return "<OwnedRetainedBootstrapV1>"


class OwnedFreshCanonical17ColdV1:
    __slots__ = ()

    def __new__(cls, *_args, **_kwargs):
        raise RetainedBootstrapRefused(
            "retained_pg_fresh_cold_not_constructible"
        ) from None


@dataclass(slots=True)
class _LiveRecord:
    owner: OwnedRetainedBootstrapV1
    origin: _Origin
    phase: _Phase
    process_id: int
    thread_id: int
    task: Any
    deadline_ns: int
    server: subprocess.Popen
    working_fd: int
    working_identity: tuple
    namespaces: dict
    cluster_name: str
    system_identifier_sha256: str
    database: str
    database_oid: int
    owner_name: str
    owner_oid: int
    cold_manifest_sha256: str
    origin_sha256: str
    ca_sha256: str
    ca_file: Path
    private_runtime: Path
    operation_id: str
    source_archive_sha256: str
    observed_provider_locator_sha256: str
    engine: Any = None
    journal_step: int = 0
    commit_attempts: int = 0
    postmaster_started: Any = None


# Only the actual fixed supervisor context may insert an owned record. There
# is deliberately no register-from-record/payload/Popen/receipt function.
_RECORDS: dict[int, _LiveRecord] = {}
_NATIVE_GUARD = None


@dataclass(slots=True)
class _FreshColdRecord:
    owner: OwnedFreshCanonical17ColdV1
    files: RetainedBootstrapFilesV1
    pins: Any
    process_id: int
    thread_id: int
    task: Any
    directory_fd: int
    directory_identity: tuple
    manifest_sha256: str
    creation_namespaces: dict
    spent: bool = False
    working_namespaces: dict | None = None


_FRESH_COLD_RECORDS: dict[int, _FreshColdRecord] = {}
_COMPLETED_DATA: dict[int, dict] = {}


def _linux():
    _need(sys.platform == "linux" and os.name == "posix")
    _need(os.uname().machine == "x86_64" and sys.byteorder == "little")
    _need(ctypes.sizeof(ctypes.c_void_p) == ctypes.sizeof(ctypes.c_long) == 8)
    _need(os.geteuid() == 0 and threading.current_thread() is threading.main_thread())


def _stable(info):
    # atime is intentionally absent: a genuine Linux read can update it.
    return tuple(
        getattr(info, name)
        for name in (
            "st_dev",
            "st_ino",
            "st_mode",
            "st_nlink",
            "st_uid",
            "st_gid",
            "st_size",
            "st_mtime_ns",
            "st_ctime_ns",
        )
    )


def _directory_identity(info):
    # A running PostgreSQL legitimately creates directories. The held native
    # directory identity, owner and protection must remain the same; nlink and
    # timestamps are not a live custody identity.
    _need(stat.S_ISDIR(info.st_mode))
    return info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid


def _relative(value, *, root=False):
    _need(
        type(value) is str
        and len(value) <= 1024
        and "\\" not in value
        and ":" not in value
    )
    _need(not any(ord(char) < 32 or ord(char) == 127 for char in value))
    if value == "":
        _need(root)
        return PurePosixPath(".")
    path = PurePosixPath(value)
    _need(
        not path.is_absolute()
        and path.as_posix() == value
        and all(part not in ("", ".", "..") for part in path.parts)
    )
    return path


def _parent_fd(root_fd, path):
    current = os.dup(root_fd)
    try:
        for part in path.parts[:-1]:
            child = os.open(
                part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current
            )
            os.close(current)
            current = child
        return current
    except BaseException:
        os.close(current)
        raise


def _private_bytes(path, maximum, *, expected=None, owner=0):
    before = path.lstat()
    _need(
        stat.S_ISREG(before.st_mode)
        and before.st_nlink == 1
        and 0 <= before.st_size <= maximum
    )
    _need(
        (before.st_uid, before.st_gid, stat.S_IMODE(before.st_mode))
        == (owner, owner, 0o600)
    )
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        opened = os.fstat(descriptor)
        _need(_stable(opened) == _stable(before))
        chunks, count = [], 0
        while chunk := os.read(descriptor, min(1024 * 1024, maximum + 1)):
            count += len(chunk)
            _need(count <= maximum)
            chunks.append(chunk)
        _need(
            _stable(opened) == _stable(os.fstat(descriptor)) and count == opened.st_size
        )
    finally:
        os.close(descriptor)
    raw = b"".join(chunks)
    _need(expected is None or _sha(raw) == expected)
    return raw


def _write_private(path, raw, *, uid=0, gid=0):
    _need(type(raw) is bytes and len(raw) <= 32 * 1024 * 1024)
    descriptor = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
    )
    try:
        view = memoryview(raw)
        while view:
            written = os.write(descriptor, view)
            _need(written > 0)
            view = view[written:]
        os.fchown(descriptor, uid, gid)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    _need(_private_bytes(path, len(raw), expected=_sha(raw), owner=uid) == raw)
    parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(parent)
    finally:
        os.close(parent)


def _cold_manifest(raw, *, origin):
    _need(type(raw) is bytes and 0 < len(raw) <= 16 * 1024 * 1024)
    _need(type(origin) is _Origin)
    if origin is _Origin.PRESERVED:
        _need(_sha(raw) == PRESERVED_MANIFEST_SHA256)
    value = json.loads(raw)
    _need(type(value) is dict and type(value.get("entries")) is list)
    if origin is _Origin.FRESH_FIXTURE:
        _need(
            set(value)
            == {
                "schema",
                "origin_nonce",
                "source17_prefix_sha256",
                "system_identifier_sha256",
                "entries",
                "regular_bytes",
            }
        )
        _need(
            value["schema"] == "ctcc.retained_pg.fresh_canonical17_cold.v1"
            and value["source17_prefix_sha256"] == SOURCE17_PREFIX_SHA256
        )
        _need(
            type(value["origin_nonce"]) is str
            and re.fullmatch(r"[0-9a-f]{32}", value["origin_nonce"], re.ASCII)
            is not None
        )
        _need(
            type(value["system_identifier_sha256"]) is str
            and _HEX.fullmatch(value["system_identifier_sha256"]) is not None
        )
    files, directories = {}, set()
    _need(0 < len(value["entries"]) <= 100000)
    for item in value["entries"]:
        _need(type(item) is dict and type(item.get("mode")) is int)
        name = item.get("path")
        _relative(name, root=True)
        _need(name not in files and name not in directories)
        if stat.S_ISDIR(item["mode"]):
            _need(stat.S_IMODE(item["mode"]) == 0o700)
            directories.add(name)
        else:
            _need(stat.S_ISREG(item["mode"]) and name != "")
            _need(stat.S_IMODE(item["mode"]) == 0o600)
            _need(type(item.get("size")) is int and 0 <= item["size"] <= 2**30)
            _need(
                type(item.get("sha256")) is str
                and _HEX.fullmatch(item["sha256"]) is not None
            )
            files[name] = (item["size"], item["sha256"])
    _need("" in directories and not _ABSENT.intersection(files.keys() | directories))
    count = sum(size for size, _ in files.values())
    _need(count == value.get("regular_bytes") and count <= 2**31)
    if origin is _Origin.PRESERVED:
        _need((len(files), len(directories), count) == (1579, 28, 136846581))
        _need(
            files.get("global/pg_control", (None, None))[1] == PRESERVED_CONTROL_SHA256
        )
    expected_children = {name: set() for name in directories}
    for name in files.keys() | directories:
        if name:
            path = _relative(name)
            parent = "" if path.parent.as_posix() == "." else path.parent.as_posix()
            _need(parent in directories)
            expected_children[parent].add(path.name)
    domain = PRESERVED_DOMAIN if origin is _Origin.PRESERVED else FRESH_FIXTURE_DOMAIN
    return files, directories, expected_children, _sha(domain + raw)


def _read_file_at(parent, name, size, expected, *, owner=70):
    before = os.stat(name, dir_fd=parent, follow_symlinks=False)
    _need(
        stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_size == size
    )
    _need(
        (before.st_uid, before.st_gid, stat.S_IMODE(before.st_mode))
        == (owner, owner, 0o600)
    )
    descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
    try:
        opened = os.fstat(descriptor)
        _need(_stable(opened) == _stable(before))
        chunks, count, hasher = [], 0, hashlib.sha256()
        while chunk := os.read(descriptor, 1024 * 1024):
            count += len(chunk)
            _need(count <= size)
            hasher.update(chunk)
            chunks.append(chunk)
        _need(
            count == size
            and hasher.hexdigest() == expected
            and _stable(opened) == _stable(os.fstat(descriptor))
        )
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _verify_cold_tree(root_fd, manifest):
    files, directories, expected_children, _ = manifest
    for name in sorted(directories):
        if name == "":
            descriptor = os.dup(root_fd)
        else:
            path = _relative(name)
            parent = _parent_fd(root_fd, path)
            try:
                descriptor = os.open(
                    path.name,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=parent,
                )
            finally:
                os.close(parent)
        try:
            info = os.fstat(descriptor)
            _need(
                (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode))
                == (70, 70, 0o700)
            )
            _need(set(os.listdir(descriptor)) == expected_children[name])
        finally:
            os.close(descriptor)
    for name, (size, expected) in sorted(files.items()):
        path = _relative(name)
        parent = _parent_fd(root_fd, path)
        try:
            _read_file_at(parent, path.name, size, expected)
        finally:
            os.close(parent)


def _copy_cold_tree(source_fd, target, manifest):
    """Only create a new leaf; no move/delete/link/overwrite source operation."""
    files, directories, _, _ = manifest
    target.mkdir(mode=0o700)
    os.chown(target, 70, 70)
    target_fd = os.open(target, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for name in sorted(directories, key=lambda item: (item.count("/"), item)):
            if name:
                path = _relative(name)
                parent = _parent_fd(target_fd, path)
                try:
                    os.mkdir(path.name, mode=0o700, dir_fd=parent)
                    os.chown(path.name, 70, 70, dir_fd=parent, follow_symlinks=False)
                    os.fsync(parent)
                finally:
                    os.close(parent)
        for name, (size, expected) in sorted(files.items()):
            path = _relative(name)
            source_parent, target_parent = (
                _parent_fd(source_fd, path),
                _parent_fd(target_fd, path),
            )
            try:
                raw = _read_file_at(source_parent, path.name, size, expected)
                descriptor = os.open(
                    path.name,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=target_parent,
                )
                try:
                    view = memoryview(raw)
                    while view:
                        written = os.write(descriptor, view)
                        _need(written > 0)
                        view = view[written:]
                    os.fchown(descriptor, 70, 70)
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
                os.fsync(target_parent)
            finally:
                os.close(source_parent)
                os.close(target_parent)
        os.fsync(target_fd)
        _verify_cold_tree(target_fd, manifest)
        return target_fd
    except BaseException:
        os.close(target_fd)
        raise


def _libc(name, arguments, *, result=ctypes.c_int):
    library = ctypes.CDLL(None, use_errno=True)
    function = getattr(library, name)
    function.argtypes, function.restype = arguments, result
    return function


def _mount(source, target, kind, flags, data=None):
    call = _libc(
        "mount",
        [
            ctypes.c_char_p,
            ctypes.c_char_p,
            ctypes.c_char_p,
            ctypes.c_ulong,
            ctypes.c_char_p,
        ],
    )
    encoded = lambda value: None if value is None else os.fsencode(value)
    if call(encoded(source), encoded(target), encoded(kind), flags, encoded(data)) != 0:
        raise RetainedBootstrapRefused("retained_pg_private_mount_refused") from None


def _namespaces(pid="self"):
    _need(pid == "self" or type(pid) is int and pid > 0)
    return {
        name: os.readlink(f"/proc/{pid}/ns/{name}") for name in ("net", "mnt", "ipc")
    }


def _private_namespaces():
    before = _namespaces()
    _need(_libc("unshare", [ctypes.c_int])(0x40000000 | 0x00020000 | 0x08000000) == 0)
    after = _namespaces()
    _need(all(before[name] != after[name] for name in before))
    _mount(None, "/", None, 16384 | 262144)
    return after


def _child_preexec(rootfs, parent_pid):
    import resource

    os.chroot(rootfs)
    os.chdir("/")
    os.setgroups([])
    os.setgid(70)
    os.setuid(70)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))

    class Header(ctypes.Structure):
        _fields_ = [("version", ctypes.c_uint32), ("pid", ctypes.c_int)]

    class Capabilities(ctypes.Structure):
        _fields_ = [
            ("effective", ctypes.c_uint32),
            ("permitted", ctypes.c_uint32),
            ("inheritable", ctypes.c_uint32),
        ]

    header, values = Header(0x20080522, 0), (Capabilities * 2)()
    arguments = [ctypes.POINTER(Header), ctypes.POINTER(Capabilities)]
    _need(_libc("capset", arguments)(ctypes.byref(header), values) == 0)
    _need(_libc("capget", arguments)(ctypes.byref(header), values) == 0)
    _need(
        all(
            value.effective == value.permitted == value.inheritable == 0
            for value in values
        )
    )
    prctl = _libc(
        "prctl",
        [ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong],
    )
    _need(prctl(38, 1, 0, 0, 0) == 0)
    _need(prctl(4, 0, 0, 0, 0) == 0)
    _need(prctl(1, signal.SIGKILL, 0, 0, 0) == 0)
    _need(
        os.getppid() == parent_pid
        and os.getuid() == os.getgid() == 70
        and os.getgroups() == []
    )


def _child_exited(server):
    return (
        os.waitid(os.P_PID, server.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
        is not None
    )


def _kill_held(server):
    _need(server.returncode is None)
    _need(os.getpgid(server.pid) == os.getsid(server.pid) == server.pid)
    os.killpg(server.pid, signal.SIGKILL)


def _stop_held(server):
    _need(server.returncode is None)
    if _child_exited(server):
        _kill_held(server)
        server.wait(timeout=5)
        return "ALREADY_EXITED_NOT_ACCEPTED"
    os.kill(server.pid, signal.SIGINT)
    try:
        return "FAST_SHUTDOWN_" + str(server.wait(timeout=12))
    except subprocess.TimeoutExpired:
        _kill_held(server)
        server.wait(timeout=5)
        return "FORCED_KILL_NOT_ACCEPTED"


def _run_image(argv, common, timeout):
    child = subprocess.Popen(
        argv,
        **common,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        stdout, stderr = child.communicate(timeout=timeout)
        return subprocess.CompletedProcess(argv, child.returncode, stdout, stderr)
    except BaseException:
        if child.returncode is None:
            _kill_held(child)
            child.wait(timeout=5)
        raise


def _control_state(result):
    _need(
        result.returncode == 0
        and result.stderr == b""
        and 0 < len(result.stdout) <= 32768
    )
    text = result.stdout.decode("ascii")
    system = re.findall(r"^Database system identifier:\s+([0-9]+)$", text, re.MULTILINE)
    state = re.findall(r"^Database cluster state:\s+([^\n]+)$", text, re.MULTILINE)
    _need(len(system) == len(state) == 1 and state[0] == "shut down")
    return _sha(system[0].encode("ascii"))


def _record(owner):
    _need(type(owner) is OwnedRetainedBootstrapV1)
    value = _RECORDS.get(id(owner))
    _need(type(value) is _LiveRecord and value.owner is owner)
    _need(
        all(
            type(item) is int
            for item in (
                value.process_id,
                value.thread_id,
                value.deadline_ns,
                value.working_fd,
            )
        )
    )
    _need(type(value.phase) is _Phase and type(value.origin) is _Origin)
    _need(type(value.server) is subprocess.Popen and value.working_fd >= 0)
    _need(value.process_id == os.getpid() and value.thread_id == threading.get_ident())
    task = asyncio.current_task()
    _need(task is value.task and task is not None and not task.cancelling())
    _need(time.monotonic_ns() < value.deadline_ns)
    _need(value.phase not in (_Phase.UNKNOWN, _Phase.CLOSED))
    _need(value.server.returncode is None and not _child_exited(value.server))
    _need(_directory_identity(os.fstat(value.working_fd)) == value.working_identity)
    _need(_namespaces() == value.namespaces)
    return value


def _bounded_regular(path, maximum, *, expected=None):
    """Fresh bytes with native identity; public image metadata need not be 0600."""
    before = path.lstat()
    _need(
        stat.S_ISREG(before.st_mode)
        and before.st_nlink == 1
        and 0 <= before.st_size <= maximum
    )
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        opened = os.fstat(descriptor)
        _need(_stable(opened) == _stable(before))
        raw = bytearray()
        while chunk := os.read(descriptor, 1024 * 1024):
            raw.extend(chunk)
            _need(len(raw) <= maximum)
        _need(
            len(raw) == opened.st_size
            and _stable(opened) == _stable(os.fstat(descriptor))
        )
    finally:
        os.close(descriptor)
    result = bytes(raw)
    _need(expected is None or _sha(result) == expected)
    return result


def _guest_link(path, target, *, hard):
    _need(type(target) is str and 0 < len(target) <= 1024 and "\\" not in target)
    _need(not any(ord(char) < 32 or ord(char) == 127 for char in target))
    candidate = (
        target.lstrip("/")
        if target.startswith("/") or hard
        else posixpath.join(path.parent.as_posix(), target)
    )
    result = posixpath.normpath(candidate)
    _relative(result)
    return result


def _image_signature(row, expected):
    _need(type(row) is dict)
    result = {name: row[name] for name in ("path", "kind", "mode", "uid", "gid")}
    _relative(row["path"])
    _need(row["kind"] in ("directory", "file", "hardlink", "symlink"))
    _need(
        all(
            type(row[name]) is int and row[name] >= 0 for name in ("mode", "uid", "gid")
        )
    )
    if row["kind"] in ("file", "hardlink"):
        source = (
            expected[_guest_link(_relative(row["path"]), row["link_target"], hard=True)]
            if row["kind"] == "hardlink"
            else row
        )
        _need(
            source["kind"] == "file"
            and type(source["size"]) is int
            and source["size"] >= 0
        )
        _need(
            type(source["sha256"]) is str
            and _HEX.fullmatch(source["sha256"]) is not None
        )
        result.update(size=source["size"], sha256=source["sha256"])
    if row["kind"] in ("symlink", "hardlink"):
        _guest_link(
            _relative(row["path"]), row["link_target"], hard=row["kind"] == "hardlink"
        )
        result["link_target"] = row["link_target"]
    return result


def _verify_image(root, inventory_file, *, python=False):
    expected_raw = PYTHON_INVENTORY_SHA256 if python else PG_INVENTORY_SHA256
    expected_tree = PYTHON_TREE_SHA256 if python else PG_TREE_SHA256
    inventory = json.loads(
        _bounded_regular(inventory_file, 2 * 1024 * 1024, expected=expected_raw)
    )
    _need(type(inventory) is list and len(inventory) == (5579 if python else 3891))
    rows = {row["path"]: row for row in inventory}
    _need(len(rows) == len(inventory))
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        initial = os.fstat(root_fd)
        _need(
            (initial.st_uid, initial.st_gid, stat.S_IMODE(initial.st_mode))
            == (0, 0, 0o755)
        )
        children = {}
        for name in rows:
            path = _relative(name)
            children.setdefault(path.parent.as_posix(), set()).add(path.name)
        for name in (
            ".",
            *(row["path"] for row in inventory if row["kind"] == "directory"),
        ):
            if name == ".":
                descriptor = os.dup(root_fd)
            else:
                path = _relative(name)
                parent = _parent_fd(root_fd, path)
                try:
                    descriptor = os.open(
                        path.name,
                        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                        dir_fd=parent,
                    )
                finally:
                    os.close(parent)
            try:
                _need(set(os.listdir(descriptor)) == children.get(name, set()))
            finally:
                os.close(descriptor)
        actual = []
        for row in inventory:
            signature = _image_signature(row, rows)
            path = _relative(row["path"])
            parent = _parent_fd(root_fd, path)
            try:
                before = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
                _need(
                    (before.st_uid, before.st_gid, stat.S_IMODE(before.st_mode))
                    == (row["uid"], row["gid"], row["mode"])
                )
                if row["kind"] == "directory":
                    _need(stat.S_ISDIR(before.st_mode))
                elif row["kind"] == "symlink":
                    _need(
                        stat.S_ISLNK(before.st_mode)
                        and os.readlink(path.name, dir_fd=parent) == row["link_target"]
                    )
                else:
                    _need(
                        stat.S_ISREG(before.st_mode)
                        and before.st_size == signature["size"]
                    )
                    descriptor = os.open(
                        path.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent
                    )
                    try:
                        opened = os.fstat(descriptor)
                        _need(_stable(opened) == _stable(before))
                        hasher, count = hashlib.sha256(), 0
                        while chunk := os.read(descriptor, 1024 * 1024):
                            count += len(chunk)
                            hasher.update(chunk)
                        _need(
                            count == signature["size"]
                            and hasher.hexdigest() == signature["sha256"]
                        )
                        _need(_stable(opened) == _stable(os.fstat(descriptor)))
                    finally:
                        os.close(descriptor)
                actual.append(signature)
            finally:
                os.close(parent)
        _need(_directory_identity(initial) == _directory_identity(os.fstat(root_fd)))
        _need(_sha(_canonical(actual)) == expected_tree)
    finally:
        os.close(root_fd)


def _verify_vendor_tree(root, manifest_raw):
    _need(_sha(manifest_raw) == VENDOR_TREE_SHA256)
    expected = json.loads(manifest_raw)
    _need(type(expected) is list and len(expected) == 4060)
    names, children = set(), {}
    for row in expected:
        _need(type(row) is dict and row.get("kind") in ("directory", "file", "symlink"))
        path = _relative(row["path"])
        _need(row["path"] not in names)
        names.add(row["path"])
        children.setdefault(path.parent.as_posix(), set()).add(path.name)
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        initial = os.fstat(root_fd)
        _need(
            (initial.st_uid, initial.st_gid, stat.S_IMODE(initial.st_mode))
            == (70, 70, 0o700)
        )
        for name in (
            ".",
            *(row["path"] for row in expected if row["kind"] == "directory"),
        ):
            if name == ".":
                descriptor = os.dup(root_fd)
            else:
                path = _relative(name)
                parent = _parent_fd(root_fd, path)
                try:
                    descriptor = os.open(
                        path.name,
                        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                        dir_fd=parent,
                    )
                finally:
                    os.close(parent)
            try:
                _need(set(os.listdir(descriptor)) == children.get(name, set()))
            finally:
                os.close(descriptor)
        for row in expected:
            path = _relative(row["path"])
            parent = _parent_fd(root_fd, path)
            try:
                before = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
                _need(
                    (before.st_uid, before.st_gid, stat.S_IMODE(before.st_mode))
                    == (row["uid"], row["gid"], row["mode"])
                )
                if row["kind"] == "directory":
                    _need(stat.S_ISDIR(before.st_mode))
                elif row["kind"] == "symlink":
                    _need(
                        stat.S_ISLNK(before.st_mode)
                        and os.readlink(path.name, dir_fd=parent) == row["target"]
                    )
                    _need(not row["target"].startswith("/"))
                    destination = _guest_link(path, row["target"], hard=False)
                    _need(destination in names)
                else:
                    _need(stat.S_ISREG(before.st_mode) and before.st_nlink == 1)
                    raw = _bounded_regular(
                        root / path, row["bytes"], expected=row["sha256"]
                    )
                    _need(len(raw) == before.st_size == row["bytes"])
            finally:
                os.close(parent)
        _need(_directory_identity(initial) == _directory_identity(os.fstat(root_fd)))
    finally:
        os.close(root_fd)


def _checked_provider_locator(raw):
    locator = json.loads(raw)
    _need(
        type(locator) is dict
        and set(locator)
        == {
            "schema",
            "outer",
            "venv",
            "tree_manifest",
            "tree_sha256",
            "python_image_tree_sha256",
            "source_dependency_input_sha256",
            "lock_sha256",
            "logical_executable",
            "logical_venv",
            "vendor_count",
            "project_installed",
            "consumer_must_verify_tree_and_remount_readonly",
            "saved_locator_is_maintenance_authority",
        }
    )
    _need(locator["schema"] == "ctcc.private_python.maintenance_vendor_locator.v1")
    _need(
        locator["tree_sha256"] == VENDOR_TREE_SHA256
        and locator["python_image_tree_sha256"] == PYTHON_TREE_SHA256
    )
    _need(
        locator["source_dependency_input_sha256"] == DEPENDENCY_INPUT_SHA256
        and locator["lock_sha256"] == LOCK_SHA256
    )
    _need(
        locator["logical_executable"] == "/run/venv/bin/python"
        and locator["logical_venv"] == "/run/venv"
    )
    _need(
        type(locator["vendor_count"]) is int
        and locator["vendor_count"] == 41
        and locator["project_installed"] is False
    )
    _need(
        locator["consumer_must_verify_tree_and_remount_readonly"] is True
        and locator["saved_locator_is_maintenance_authority"] is False
    )
    _need(
        all(
            type(locator[name]) is str and 0 < len(locator[name]) <= 2048
            for name in ("outer", "venv", "tree_manifest")
        )
    )
    outer, venv, manifest_file = (
        PurePosixPath(locator[name]) for name in ("outer", "venv", "tree_manifest")
    )
    _need(
        all(
            path.is_absolute() and ".." not in path.parts
            for path in (outer, venv, manifest_file)
        )
    )
    _need(
        venv == outer / "runtime/venv"
        and manifest_file == outer / "VENDOR_ENV_TREE_PRIVATE.json"
    )
    return locator, Path(venv.as_posix()), Path(manifest_file.as_posix())


def _require_runtime_provider(files, *, logical=False):
    # A locator is only a source-pinned inventory address. All program/vendor
    # bytes are reopened and rehashed, and this invocation establishes fresh
    # read-only mounts before any application/vendor import.
    raw_locator = _private_bytes(Path(files.runtime_provider_file), 16384)
    _locator, venv, manifest_file = _checked_provider_locator(raw_locator)
    if logical:
        # These paths are constructed by this module's fixed launcher. The
        # original locator bytes remain unmodified provenance DATA.
        venv, manifest_file = Path("/run/venv"), Path("/run/vendor-manifest.json")
    manifest_raw = _private_bytes(
        manifest_file, 2 * 1024 * 1024, expected=VENDOR_TREE_SHA256
    )
    _verify_vendor_tree(venv, manifest_raw)
    _verify_image(
        Path(files.python_rootfs), Path(files.python_inventory_file), python=True
    )
    if logical:
        _need(os.statvfs(venv).f_flag & os.ST_RDONLY != 0)
    return venv, manifest_raw, _sha(raw_locator)


def _require_runtime_metadata_v1():
    import collections
    import importlib.metadata
    import sysconfig

    _need(
        sys.prefix == "/run/venv"
        and sys.base_prefix == "/usr/local"
        and sys.flags.isolated == 1
        and sys.flags.no_site == 0
    )
    _need(sys.dont_write_bytecode is True and sys.version_info[:3] == (3, 12, 14))
    _need(sysconfig.get_config_var("SOABI") == "cpython-312-x86_64-linux-gnu")
    lock_raw = _bounded_regular(
        ROOT / "requirements/validation-linux-py312.lock",
        128 * 1024,
        expected=LOCK_SHA256,
    )
    _need(len(lock_raw) > 0)
    _bounded_regular(ROOT / "pyproject.toml", 128 * 1024, expected=PYPROJECT_SHA256)
    metadata = json.loads(
        _bounded_regular(ROOT / "requirements/validation-linux-py312.json", 128 * 1024)
    )
    _need(
        type(metadata) is dict
        and metadata.get("schema") == "ctcc.validation_dependency_lock.v1"
    )
    _need(
        (metadata.get("target"), metadata.get("architecture"), metadata.get("python"))
        == ("linux", "x86_64", "3.12")
    )
    _need(
        metadata.get("source_pyproject_sha256") == PYPROJECT_SHA256
        and metadata.get("lock_sha256") == LOCK_SHA256
    )
    rows = metadata.get("packages")
    _need(type(rows) is list and len(rows) == 41)
    expected = [(row["distribution_name"], row["version"]) for row in rows]
    actual = [
        (distribution.metadata["Name"], distribution.version)
        for distribution in importlib.metadata.distributions()
    ]
    _need(collections.Counter(actual) == collections.Counter(expected))
    _need(
        not any(
            name.lower().replace("_", "-") == "ctcc-v2" for name, _version in actual
        )
    )


def _archive_members(raw, source):
    """Reconstruct exact Git bytes/modes before extracting/importing source."""
    _need(
        type(source) is dict
        and set(source) == {"commit", "tree", "archive", "manifest"}
    )
    _need(
        all(
            type(source[key]) is str
            and (_GIT if key in ("commit", "tree") else _HEX).fullmatch(source[key])
            is not None
            for key in source
        )
    )
    _need(_sha(raw) == source["archive"])
    members, declared, tree, directories = {}, set(), {}, set()
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
        _need(archive.pax_headers.get("comment") == source["commit"])
        for item in archive.getmembers():
            name = item.name.rstrip("/") if item.isdir() else item.name
            _need(item.name == name or item.isdir() and item.name == name + "/")
            path = _relative(name)
            _need(name not in declared)
            declared.add(name)
            _need(
                not any(
                    part.casefold()
                    in (".git", ".venv", "__pycache__", "validation-results", "reports")
                    for part in path.parts
                )
            )
            _need(
                not path.name.casefold().startswith(".env")
                or path.name == ".env.example"
            )
            if item.isdir():
                directories.add(name)
                continue
            _need(item.isfile() and 0 <= item.size <= 16 * 1024 * 1024)
            stream = archive.extractfile(item)
            _need(stream is not None)
            content = stream.read()
            _need(len(content) == item.size)
            members[item.name] = (content, 0o755 if item.mode & 0o111 else 0o644)
            node = tree
            for part in path.parts[:-1]:
                node = node.setdefault(part, {})
                _need(type(node) is dict)
            _need(path.name not in node)
            digest = hashlib.sha1(
                b"blob " + str(len(content)).encode("ascii") + b"\x00" + content
            ).digest()
            node[path.name] = (b"100755" if item.mode & 0o111 else b"100644", digest)
    _need(0 < len(members) <= 10000 and "MANIFEST.sha256" in members)
    parents = {
        PurePosixPath(name).parts[:depth]
        for name in members
        for depth in range(1, len(PurePosixPath(name).parts))
    }
    _need(all(PurePosixPath(name).parts in parents for name in directories))

    def git_tree(node):
        records = []
        for name, value in node.items():
            directory = type(value) is dict
            mode, digest = (b"40000", git_tree(value)) if directory else value
            encoded = name.encode("utf-8")
            records.append(
                (
                    encoded + (b"/" if directory else b""),
                    mode + b" " + encoded + b"\x00" + digest,
                )
            )
        body = b"".join(record for _, record in sorted(records))
        return hashlib.sha1(
            b"tree " + str(len(body)).encode("ascii") + b"\x00" + body
        ).digest()

    _need(git_tree(tree).hex() == source["tree"])
    _need(_sha(members["MANIFEST.sha256"][0]) == source["manifest"])
    return members


def _verify_source(files, source):
    raw = _private_bytes(
        Path(files.source_archive_file), 128 * 1024 * 1024, expected=source["archive"]
    )
    members = _archive_members(raw, source)
    observed = set()
    for parent, directories, filenames in os.walk(ROOT, followlinks=False):
        directories.sort()
        for name in directories:
            _need(not (Path(parent) / name).is_symlink())
        for name in filenames:
            path = Path(parent) / name
            relative = path.relative_to(ROOT).as_posix()
            _need(relative in members)
            expected, _mode = members[relative]
            _need(_bounded_regular(path, 16 * 1024 * 1024) == expected)
            observed.add(relative)
    _need(observed == set(members))
    _need(os.statvfs(ROOT).f_flag & os.ST_RDONLY != 0)
    return raw


def _source_pins(pins):
    return {
        "commit": _source_pin_object(pins).source_commit,
        "tree": _source_pin_object(pins).source_tree,
        "archive": _source_pin_object(pins).source_archive_sha256,
        "manifest": _source_pin_object(pins).route.source_manifest_sha256,
    }


def _source_pin_object(pins):
    from scripts import retained0017_owner_upgrade_0055_v1 as owner_api

    return owner_api._transaction_source_v1(pins)


def _fixed_environment():
    return {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "LC_ALL": "C",
        "LANG": "C",
        "HOME": "/nonexistent",
    }


def _loopback_up():
    import fcntl
    import socket
    import struct

    _need(socket.if_nameindex() == [(1, "lo")])
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as control:
        request = struct.pack("16sH14x", b"lo", 0)
        observed = fcntl.ioctl(control.fileno(), 0x8913, request)
        flags = struct.unpack_from("H", observed, 16)[0]
        fcntl.ioctl(control.fileno(), 0x8914, struct.pack("16sH14x", b"lo", flags | 1))
        observed = fcntl.ioctl(control.fileno(), 0x8913, request)
        _need(bool(struct.unpack_from("H", observed, 16)[0] & 1))


def _tls_inputs(files):
    ca = _private_bytes(Path(files.ca_certificate_file), 65536)
    certificate = _private_bytes(Path(files.server_certificate_file), 65536)
    private_key = _private_bytes(Path(files.server_private_key_file), 65536)
    _need(
        ca.startswith(b"-----BEGIN CERTIFICATE-----")
        and certificate.startswith(b"-----BEGIN CERTIFICATE-----")
    )
    client = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    client.minimum_version = ssl.TLSVersion.TLSv1_2
    client.load_verify_locations(cadata=ca.decode("ascii"))
    _need(client.check_hostname is True and client.verify_mode == ssl.CERT_REQUIRED)
    # Real native TLS parsing checks the key/certificate pair before PG starts.
    server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server.minimum_version = ssl.TLSVersion.TLSv1_2
    server.load_cert_chain(files.server_certificate_file, files.server_private_key_file)
    _need(_private_bytes(Path(files.server_certificate_file), 65536) == certificate)
    _need(_private_bytes(Path(files.server_private_key_file), 65536) == private_key)
    return ca, certificate, private_key


def _journal(record, phase):
    _need(type(phase) is _Phase)
    value = {
        "schema": "ctcc.retained_pg.live_bootstrap_journal.v1",
        "operation_id": record.operation_id,
        "step": record.journal_step,
        "phase": phase.value,
        "origin": record.origin.value,
        "origin_sha256": record.origin_sha256,
        "cold_manifest_sha256": record.cold_manifest_sha256,
        "source_archive_sha256": record.source_archive_sha256,
        "observed_provider_locator_sha256": record.observed_provider_locator_sha256,
        "system_identifier_sha256": record.system_identifier_sha256,
        "database_oid": record.database_oid,
        "owner_oid": record.owner_oid,
        "execution_authority": False,
    }
    _write_private(
        record.private_runtime
        / (f"operation-{record.journal_step:02d}-{phase.value}.json"),
        _canonical(value),
    )
    record.journal_step += 1


def _guard():
    _need(_NATIVE_GUARD is not None, "retained_pg_source_guard_not_installed")
    _NATIVE_GUARD()


def _bind_engine_v1(owner, engine):
    _guard()
    value = _record(owner)
    _need(value.phase is _Phase.READY and value.engine is None)
    from sqlalchemy.ext.asyncio import AsyncEngine
    from sqlalchemy.pool import NullPool

    _need(type(engine) is AsyncEngine and type(engine.pool) is NullPool)
    _need(engine.url.password is None and engine.url.query == {})
    _need(
        (engine.url.host, engine.url.port, engine.url.database, engine.url.username)
        == ("127.0.0.1", PORT, value.database, value.owner_name)
    )
    value.engine = engine


async def _replay_owned_connection_v1(owner, connection):
    _guard()
    value = _record(owner)
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncConnection

    _need(
        type(connection) is AsyncConnection
        and connection.engine is value.engine
        and value.engine is not None
    )
    observed = (
        await connection.execute(
            text("""SELECT
 current_user::pg_catalog.text,session_user::pg_catalog.text,
 pg_catalog.current_database()::pg_catalog.text,d.oid::pg_catalog.int8,
 r.oid::pg_catalog.int8,d.datdba::pg_catalog.int8,
 (pg_catalog.pg_control_system()).system_identifier::pg_catalog.text,
 pg_catalog.current_setting('data_directory'),pg_catalog.current_setting('cluster_name'),
 pg_catalog.pg_postmaster_start_time(),s.ssl,s.version,
 pg_catalog.host(pg_catalog.inet_server_addr()),pg_catalog.inet_server_port(),
 pg_catalog.pg_is_in_recovery(),pg_catalog.current_setting('role'),
 pg_catalog.current_setting('session_preload_libraries'),pg_catalog.current_setting('local_preload_libraries')
 FROM pg_catalog.pg_database d JOIN pg_catalog.pg_roles r ON r.rolname=current_user
 JOIN pg_catalog.pg_stat_ssl s ON s.pid=pg_catalog.pg_backend_pid()
 WHERE d.datname=pg_catalog.current_database()""")
        )
    ).one()
    _guard()
    value = _record(owner)
    _need(
        tuple(observed[:6])
        == (
            value.owner_name,
            value.owner_name,
            value.database,
            value.database_oid,
            value.owner_oid,
            value.owner_oid,
        )
    )
    _need(
        type(observed[6]) is str
        and _sha(observed[6].encode("ascii")) == value.system_identifier_sha256
    )
    _need(tuple(observed[7:9]) == ("/run/data", value.cluster_name))
    _need(observed[10] is True and observed[11] in ("TLSv1.2", "TLSv1.3"))
    _need(tuple(observed[12:]) == ("127.0.0.1", PORT, False, "none", "", ""))
    if value.postmaster_started is None:
        _need(value.phase is _Phase.READY)
        value.postmaster_started = observed[9]
    else:
        _need(observed[9] == value.postmaster_started)


def _claim_owned_apply_v1(owner, *, fixture=False):
    _guard()
    value = _record(owner)
    _need(value.phase is _Phase.READY and value.engine is not None)
    _need(type(fixture) is bool)
    _need(value.origin is (_Origin.FRESH_FIXTURE if fixture else _Origin.PRESERVED))
    _journal(value, _Phase.CLAIMED)
    value.phase = _Phase.CLAIMED


def _advance_owned_apply_v1(owner, phase):
    _guard()
    value = _record(owner)
    expected = {
        _Phase.CLAIMED: _Phase.REHEARSAL,
        _Phase.REHEARSAL: _Phase.ROLLED_BACK,
        _Phase.ROLLED_BACK: _Phase.FINAL,
        _Phase.FINAL: _Phase.COMMIT_ATTEMPTED,
        _Phase.COMMIT_ATTEMPTED: _Phase.READBACK_ONLY,
    }
    _need(type(phase) is str and expected.get(value.phase) is not None)
    target = expected[value.phase]
    _need(target.value == phase)
    _journal(value, target)
    # Burn the only write capability synchronously BEFORE awaiting COMMIT.
    if target is _Phase.COMMIT_ATTEMPTED:
        value.commit_attempts += 1
    value.phase = target


def _claim_owned_fixture_apply_v1(owner):
    _claim_owned_apply_v1(owner, fixture=True)


def _mark_owned_unknown_v1(owner):
    # This boundary deliberately does not perform another external operation.
    # Cancellation, deadline expiry or server death still burns the record.
    value = _RECORDS.get(id(owner))
    if value is not None and value.owner is owner:
        value.phase = _Phase.UNKNOWN


def _owned_route_v1(owner, pins, *, fixture=False):
    _guard()
    value = _record(owner)
    _need(
        value.phase is _Phase.READY
        and value.origin is (_Origin.FRESH_FIXTURE if fixture else _Origin.PRESERVED)
    )
    p = _source_pin_object(pins).route
    _need(
        (p.host, p.port, p.database, p.database_oid, p.owner, p.owner_oid)
        == (
            "127.0.0.1",
            PORT,
            value.database,
            value.database_oid,
            value.owner_name,
            value.owner_oid,
        )
    )
    _need(
        p.cluster_system_identifier_sha256 == value.system_identifier_sha256
        and p.ca_sha256 == value.ca_sha256
    )
    _need(_source_pin_object(pins).source_archive_sha256 == value.source_archive_sha256)
    if value.origin is _Origin.PRESERVED:
        _need(
            _source_pin_object(pins).preserved_backup_sha256 == PRESERVED_BACKUP_SHA256
        )
    else:
        _need(_source_pin_object(pins).origin_sha256 == value.origin_sha256)
    _need(
        len(_private_bytes(value.ca_file, 65536, expected=value.ca_sha256, owner=70))
        > 0
    )
    from sqlalchemy.engine import URL

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_verify_locations(cafile=value.ca_file)
    _need(context.check_hostname is True and context.verify_mode == ssl.CERT_REQUIRED)
    return URL.create(
        "postgresql+asyncpg",
        username=value.owner_name,
        host="127.0.0.1",
        port=PORT,
        database=value.database,
    ), context


def _install_owner_guard_v1():
    global _NATIVE_GUARD
    _need(_NATIVE_GUARD is None)
    from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncTransaction

    from app.database import native_pg_tls_v1 as pg_tls
    from app.domain import collector_current_0054_catalog_v1 as inherited55
    from app.domain import collector_current_0055_catalog_v1 as source55
    from app.domain import native_database_profile_v1 as profiles
    from app.trade_qualification.native_continuation_guard_v1 import (
        install_native_guards_v1,
    )
    from scripts import current_contract_source_scopes_v1 as scopes
    from scripts import manifest
    from scripts import native_owner_heap_am_v1 as heap
    from scripts import native_owner_index_catalog_v1 as indexes
    from scripts import native_owner_retained_rows_v2 as retained_rows
    from scripts import native_owner_route_preflight_0056_v1 as route
    from scripts import native_owner_upgrade_0056_v1 as shared
    from scripts import retained0017_bootstrap_catalog_v2 as bootstrap
    from scripts import retained0017_catalog_baseline_v1 as baseline
    from scripts import retained0017_catalog_sql_types_v1 as catalog_types
    from scripts import retained0017_owner_plan_v1 as original
    from scripts import retained0017_owner_upgrade_0055_v1 as owner_api
    from scripts import retained0017_source_contract_v1 as source_contract
    from scripts import retained0017_upgrade_projection_v1 as projection
    from scripts import verify_migration_identity as identity

    os_names = (
        "open",
        "read",
        "write",
        "fstat",
        "stat",
        "listdir",
        "readlink",
        "getpid",
        "kill",
    )
    if sys.platform == "linux":
        os_names += ("geteuid", "getuid", "getgid", "getgroups", "killpg", "waitid")
    bindings = tuple(
        (owner, name, vars(owner)[name])
        for owner, names in (
            (AsyncEngine, ("connect", "dispose")),
            (
                AsyncConnection,
                (
                    "begin",
                    "begin_nested",
                    "execute",
                    "scalar",
                    "exec_driver_sql",
                    "run_sync",
                ),
            ),
            (AsyncTransaction, ("commit", "rollback")),
            (subprocess, ("Popen",)),
            (
                subprocess.Popen,
                (
                    "__init__",
                    "_execute_child",
                    "wait",
                    "_wait",
                    "communicate",
                    "_communicate",
                ),
            ),
            (asyncio, ("current_task",)),
            (os, os_names),
            (
                shared.archive_checks,
                (
                    "archive_tree",
                    "git_object",
                    "PRIVATE_ARCHIVE_DIRECTORIES",
                    "PRIVATE_ARCHIVE_NAMES",
                    "PRIVATE_ARCHIVE_SUFFIXES",
                    "PRIVATE_ARCHIVE_BACKUP_SUFFIXES",
                ),
            ),
        )
        for name in names
    )

    _NATIVE_GUARD = install_native_guards_v1(
        sys.modules[__name__],
        (
            "_bind_engine_v1",
            "_replay_owned_connection_v1",
            "_claim_owned_apply_v1",
            "_advance_owned_apply_v1",
            "_owned_route_v1",
        ),
        dependencies=(
            owner_api,
            shared,
            original,
            baseline,
            bootstrap,
            projection,
            retained_rows,
            heap,
            indexes,
            route,
            scopes,
            manifest,
            catalog_types,
            source_contract,
            identity,
            pg_tls,
            inherited55,
            source55,
            profiles,
        ),
        bindings=bindings,
    )
    _guard()


def _private_parent(path):
    _need(path.is_absolute() and ".." not in path.parts)
    current = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in path.parts[1:]:
            child = os.open(
                part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current
            )
            os.close(current)
            current = child
            info = os.fstat(current)
            _need(info.st_uid == 0 and stat.S_IMODE(info.st_mode) & 0o022 == 0)
        return _directory_identity(os.fstat(current))
    finally:
        os.close(current)


def _current_executable_sha():
    # This one kernel-owned procfs magic link is deliberately opened as such;
    # arbitrary resource paths always use O_NOFOLLOW above.
    link = os.readlink("/proc/self/exe")
    descriptor = os.open("/proc/self/exe", os.O_RDONLY)
    try:
        opened = os.fstat(descriptor)
        _need(stat.S_ISREG(opened.st_mode) and opened.st_size <= 64 * 1024 * 1024)
        hasher, size = hashlib.sha256(), 0
        while chunk := os.read(descriptor, 1024 * 1024):
            size += len(chunk)
            _need(size <= opened.st_size)
            hasher.update(chunk)
        _need(
            size == opened.st_size and _stable(opened) == _stable(os.fstat(descriptor))
        )
        _need(os.readlink("/proc/self/exe") == link)
        return hasher.hexdigest()
    finally:
        os.close(descriptor)


def _mount_pg_image(image, rootfs, runtime):
    _mount(image, rootfs, None, 4096)
    _mount(None, rootfs, None, 4096 | 32 | 1 | 2 | 4)
    _mount(runtime, rootfs / "run", None, 4096)
    _mount("tmpfs", rootfs / "dev", "tmpfs", 2 | 8, "mode=0755,size=1m")
    for name, major, minor in (("null", 1, 3), ("random", 1, 8), ("urandom", 1, 9)):
        path = rootfs / "dev" / name
        os.mknod(path, stat.S_IFCHR | 0o666, os.makedev(major, minor))
        os.chmod(path, 0o666)


def _bootstrap_config(runtime, *, database, owner, cluster_name, tls):
    _need(
        all(
            type(value) is str and _NAME.fullmatch(value) is not None
            for value in (database, owner, cluster_name)
        )
    )
    for name, raw in zip(
        ("ca-certificate.pem", "server-certificate.pem", "server-private-key.pem"),
        tls,
        strict=True,
    ):
        _write_private(runtime / name, raw, uid=70, gid=70)
    settings = {
        "data_directory": "'/run/data'",
        "hba_file": "'/run/hba.conf'",
        "ident_file": "'/run/ident.conf'",
        "listen_addresses": "'127.0.0.1'",
        "port": str(PORT),
        "cluster_name": "'" + cluster_name + "'",
        "unix_socket_directories": "'/run/socket'",
        "unix_socket_permissions": "0700",
        "ssl": "on",
        "ssl_cert_file": "'/run/server-certificate.pem'",
        "ssl_key_file": "'/run/server-private-key.pem'",
        "ssl_min_protocol_version": "'TLSv1.2'",
        "default_transaction_read_only": "on",
        "shared_buffers": "'16MB'",
        "max_connections": "4",
        "superuser_reserved_connections": "1",
        "reserved_connections": "0",
        "max_wal_senders": "0",
        "shared_preload_libraries": "''",
        "session_preload_libraries": "''",
        "local_preload_libraries": "''",
        "archive_mode": "off",
        "archive_library": "''",
        "archive_command": "''",
        "restore_command": "''",
        "archive_cleanup_command": "''",
        "recovery_end_command": "''",
        "primary_conninfo": "''",
        "primary_slot_name": "''",
        "external_pid_file": "''",
        "autovacuum": "off",
        "max_worker_processes": "0",
        "max_logical_replication_workers": "0",
        "max_parallel_workers": "0",
        "max_parallel_maintenance_workers": "0",
        "jit": "off",
        "huge_pages": "off",
        "dynamic_shared_memory_type": "mmap",
        "fsync": "on",
        "full_page_writes": "on",
        "synchronous_commit": "on",
        "logging_collector": "off",
        "log_destination": "'stderr'",
        "log_connections": "off",
        "log_disconnections": "off",
        "log_statement": "'none'",
        "log_min_error_statement": "'panic'",
        "log_min_messages": "'panic'",
    }
    _write_private(
        runtime / "maintenance.conf",
        ("\n".join(key + "=" + value for key, value in settings.items()) + "\n").encode(
            "ascii"
        ),
        uid=70,
        gid=70,
    )
    _write_private(
        runtime / "hba.conf",
        (
            "hostssl "
            + database
            + " "
            + owner
            + " 127.0.0.1/32 trust\nlocal all all reject\nhost all all 0.0.0.0/0 reject\nhost all all ::0/0 reject\n"
        ).encode("ascii"),
        uid=70,
        gid=70,
    )
    _write_private(runtime / "ident.conf", b"", uid=70, gid=70)
    return settings


def _clear_working_auto_conf(working_fd, runtime, manifest):
    # Only this invocation's new copy changes. Retain exact old bytes outside
    # PGDATA, then ensure no original auto.conf can load an arbitrary include
    # or callback before command-line overrides take effect.
    files = manifest[0]
    _need("postgresql.auto.conf" in files)
    size, expected = files["postgresql.auto.conf"]
    original = _read_file_at(working_fd, "postgresql.auto.conf", size, expected)
    _write_private(runtime / "original-postgresql.auto.conf.private", original)
    descriptor = os.open(
        "postgresql.auto.conf", os.O_WRONLY | os.O_NOFOLLOW, dir_fd=working_fd
    )
    try:
        before = os.fstat(descriptor)
        _need(
            stat.S_ISREG(before.st_mode)
            and before.st_nlink == 1
            and before.st_uid == before.st_gid == 70
        )
        os.ftruncate(descriptor, 0)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.fsync(working_fd)


async def _capture_current_bootstrap_v1(
    engine, route, *, cluster_name, system_identifier_sha256
):
    from sqlalchemy import text

    from scripts import native_owner_heap_am_v1 as storage
    from scripts import retained0017_bootstrap_catalog_v2 as bootstrap
    from scripts import retained0017_catalog_baseline_v1 as baseline

    async with engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.exec_driver_sql(
                "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
            )
            await connection.exec_driver_sql("SET LOCAL search_path TO pg_catalog")
            await connection.exec_driver_sql("SET LOCAL TIME ZONE 'UTC'")
            await storage.lock_native_owner_marker_v1(connection)
            observed = (
                await connection.execute(
                    text("""SELECT
 r.oid::pg_catalog.int8,d.oid::pg_catalog.int8,d.datdba::pg_catalog.int8,
 r.rolname::pg_catalog.text,d.datname::pg_catalog.text,
 (pg_catalog.pg_control_system()).system_identifier::pg_catalog.text,
 pg_catalog.current_setting('data_directory'),pg_catalog.current_setting('cluster_name'),
 pg_catalog.pg_postmaster_start_time(),s.ssl,s.version
 FROM pg_catalog.pg_roles r JOIN pg_catalog.pg_database d ON d.datname=pg_catalog.current_database()
 JOIN pg_catalog.pg_stat_ssl s ON s.pid=pg_catalog.pg_backend_pid()
 WHERE r.rolname=current_user AND current_user=session_user""")
                )
            ).one()
            p = route
            _need(
                tuple(observed[:5])
                == (p.owner_oid, p.database_oid, p.owner_oid, p.owner, p.database)
            )
            _need(
                type(observed[5]) is str
                and _sha(observed[5].encode("ascii")) == system_identifier_sha256
            )
            _need(tuple(observed[6:8]) == ("/run/data", cluster_name))
            _need(observed[9] is True and observed[10] in ("TLSv1.2", "TLSv1.3"))
            raw = await baseline.capture_catalog17_v1(connection)
            bootstrap.require_bootstrap_catalog17_v2(raw)
            return observed[8]
        finally:
            await transaction.rollback()


async def _owned_bootstrap_generator_v1(files, pins, origin):
    """Fixed genuine cold-copy factory. No supplied listener, checker or result."""
    _linux()
    _need(type(files) is RetainedBootstrapFilesV1 and type(origin) is _Origin)
    _need(
        sys.flags.isolated == 1
        and sys.flags.no_site == 0
        and sys.dont_write_bytecode is True
    )
    _need(
        sys.prefix == "/run/venv"
        and sys.base_prefix == "/usr/local"
        and sys.version_info[:3] == (3, 12, 14)
    )
    _need(_current_executable_sha() == PYTHON_ELF_SHA256)
    _venv, _vendor_manifest, locator_sha256 = _require_runtime_provider(
        files, logical=True
    )
    _verify_image(Path(files.pg_rootfs), Path(files.pg_inventory_file))
    from sqlalchemy.engine import URL
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    from scripts import retained0017_owner_upgrade_0055_v1 as owner_api

    if origin is _Origin.PRESERVED:
        _need(type(pins) is owner_api.Retained0017UpgradePinsV1)
        owner_api.original._checked_pins(_source_pin_object(pins))
    else:
        owner_api._checked_fresh_pins_v1(pins)
    source = _source_pins(pins)
    _verify_source(files, source)
    _require_runtime_metadata_v1()
    _need(
        all(
            _NAME.fullmatch(name) is not None
            for name in (
                _source_pin_object(pins).route.database,
                _source_pin_object(pins).route.owner,
            )
        )
    )
    _need(
        (
            _source_pin_object(pins).route.host,
            _source_pin_object(pins).route.port,
            _source_pin_object(pins).route.owner_oid,
        )
        == ("127.0.0.1", PORT, 10)
    )
    _need(
        origin is not _Origin.PRESERVED
        or _source_pin_object(pins).preserved_backup_sha256 == PRESERVED_BACKUP_SHA256
    )
    if _NATIVE_GUARD is None:
        _install_owner_guard_v1()
    _guard()
    parent = Path(files.private_output_parent)
    _private_parent(parent)
    raw_manifest = _private_bytes(Path(files.cold_manifest_file), 16 * 1024 * 1024)
    manifest = _cold_manifest(raw_manifest, origin=origin)
    cold_fd = os.open(
        files.cold_directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    )
    working_fd = None
    server = engine = lease = None
    failed = True
    try:
        _verify_cold_tree(cold_fd, manifest)
        tls = _tls_inputs(files)
        namespaces = _private_namespaces()
        _loopback_up()
        outer = parent / ("ctcc-owned-retained-" + uuid.uuid4().hex)
        outer.mkdir(mode=0o700)
        runtime, pg_rootfs = outer / "runtime", outer / "pg-rootfs"
        runtime.mkdir(mode=0o700)
        pg_rootfs.mkdir(mode=0o700)
        os.chown(runtime, 70, 70)
        working_fd = _copy_cold_tree(cold_fd, runtime / "data", manifest)
        _need(
            _directory_identity(os.fstat(working_fd))[:2]
            != _directory_identity(os.fstat(cold_fd))[:2]
        )
        (runtime / "socket").mkdir(mode=0o700)
        os.chown(runtime / "socket", 70, 70)
        cluster_name = "ctcc_retained_" + uuid.uuid4().hex
        settings = _bootstrap_config(
            runtime,
            database=_source_pin_object(pins).route.database,
            owner=_source_pin_object(pins).route.owner,
            cluster_name=cluster_name,
            tls=tls,
        )
        _mount_pg_image(Path(files.pg_rootfs), pg_rootfs, runtime)
        parent_pid = os.getpid()
        common = {
            "preexec_fn": lambda: _child_preexec(pg_rootfs, parent_pid),
            "env": _fixed_environment(),
            "close_fds": True,
            "stdin": subprocess.DEVNULL,
        }
        system_identifier = _control_state(
            _run_image(["/usr/local/bin/pg_controldata", "/run/data"], common, 5)
        )
        _need(
            system_identifier
            == _source_pin_object(pins).route.cluster_system_identifier_sha256
        )
        _clear_working_auto_conf(working_fd, runtime, manifest)
        arguments = [
            "/usr/local/bin/postgres",
            "-D",
            "/run/data",
            "-c",
            "config_file=/run/maintenance.conf",
        ]
        for name, value in settings.items():
            # PostgreSQL command-line -c strings are not config-file literals.
            literal = (
                value[1:-1] if value.startswith("'") and value.endswith("'") else value
            )
            arguments.extend(("-c", name + "=" + literal))
        log = os.open(
            runtime / "server.private.log",
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
        )
        try:
            # This dedicated, single-task supervisor must keep the direct
            # Popen child unreaped; no worker thread may mint its custody.
            server = subprocess.Popen(  # noqa: ASYNC220 -- the dedicated supervisor must own this unreaped child.
                arguments,
                **common,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        finally:
            os.close(log)
        async with asyncio.timeout(BOOTSTRAP_SECONDS):
            while not (runtime / f"socket/.s.PGSQL.{PORT}").exists():
                _need(server.returncode is None and not _child_exited(server))
                await asyncio.sleep(0.025)
            readiness = dict(common)
            readiness["env"] = {
                **common["env"],
                "PGCONNECT_TIMEOUT": "1",
                "PGSSLMODE": "verify-full",
                "PGSSLROOTCERT": "/run/ca-certificate.pem",
                "PGSSLCERT": "",
                "PGSSLKEY": "",
                "PGOPTIONS": "-c session_preload_libraries= -c local_preload_libraries= -c role=none -c search_path=pg_catalog -c default_transaction_read_only=on -c statement_timeout=10000 -c lock_timeout=1000",
            }
            while True:
                ready = _run_image(
                    [
                        "/usr/local/bin/pg_isready",
                        "-h",
                        "127.0.0.1",
                        "-p",
                        str(PORT),
                        "-U",
                        _source_pin_object(pins).route.owner,
                        "-d",
                        _source_pin_object(pins).route.database,
                        "-t",
                        "1",
                    ],
                    readiness,
                    2,
                )
                _need(server.returncode is None and not _child_exited(server))
                if ready.returncode == 0:
                    break
                _need(ready.returncode in (1, 2))
                await asyncio.sleep(0.025)
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            context.load_verify_locations(cadata=tls[0].decode("ascii"))
            url = URL.create(
                "postgresql+asyncpg",
                username=_source_pin_object(pins).route.owner,
                host="127.0.0.1",
                port=PORT,
                database=_source_pin_object(pins).route.database,
            )
            engine = create_async_engine(
                url,
                poolclass=NullPool,
                connect_args={
                    "ssl": context,
                    "timeout": 10,
                    "server_settings": dict(owner_api._STARTUP_SETTINGS),
                },
                hide_parameters=True,
            )
            started = await _capture_current_bootstrap_v1(
                engine,
                _source_pin_object(pins).route,
                cluster_name=cluster_name,
                system_identifier_sha256=system_identifier,
            )
            await engine.dispose()
            engine = None
            _guard()
            _need(server.returncode is None and not _child_exited(server))
            _verify_source(files, source)
            lease = object.__new__(OwnedRetainedBootstrapV1)
            record = _LiveRecord(
                owner=lease,
                origin=origin,
                phase=_Phase.READY,
                process_id=os.getpid(),
                thread_id=threading.get_ident(),
                task=asyncio.current_task(),
                deadline_ns=time.monotonic_ns() + OWNER_SECONDS * 10**9,
                server=server,
                working_fd=working_fd,
                working_identity=_directory_identity(os.fstat(working_fd)),
                namespaces=namespaces,
                cluster_name=cluster_name,
                system_identifier_sha256=system_identifier,
                database=_source_pin_object(pins).route.database,
                database_oid=_source_pin_object(pins).route.database_oid,
                owner_name=_source_pin_object(pins).route.owner,
                owner_oid=_source_pin_object(pins).route.owner_oid,
                cold_manifest_sha256=_sha(raw_manifest),
                origin_sha256=manifest[3],
                ca_sha256=_sha(tls[0]),
                ca_file=runtime / "ca-certificate.pem",
                private_runtime=outer,
                operation_id=uuid.uuid4().hex,
                source_archive_sha256=source["archive"],
                postmaster_started=started,
                observed_provider_locator_sha256=locator_sha256,
            )
            _journal(record, _Phase.READY)
            _RECORDS[id(lease)] = record
        yield lease
        failed = False
    finally:
        already_failed = sys.exc_info()[0] is not None
        value = None
        previous_phase = _Phase.UNKNOWN
        if lease is not None:
            value = _RECORDS.pop(id(lease), None)
            if value is not None:
                previous_phase = value.phase
                value.phase = _Phase.UNKNOWN if failed else _Phase.CLOSED
        try:
            # Always stop the actual owned server BEFORE any cancellable
            # cleanup await. A failed/cancelled disposer cannot orphan it.
            if server is not None:
                stopped = _stop_held(server)
                _need(stopped == "FAST_SHUTDOWN_0", "retained_pg_owned_cleanup_refused")
            if engine is not None:
                await engine.dispose()
            _verify_cold_tree(cold_fd, manifest)
            if value is not None and not failed:
                _COMPLETED_DATA[id(lease)] = {
                    "origin": value.origin.value,
                    "cold_manifest_sha256": value.cold_manifest_sha256,
                    "origin_sha256": value.origin_sha256,
                    "system_identifier_sha256": value.system_identifier_sha256,
                    "cold_source_unchanged_verified": True,
                    "distinct_working_copy_verified": True,
                    "owned_server_fast_shutdown_verified": True,
                    "journal_steps": value.journal_step,
                    "commit_attempts": value.commit_attempts,
                    "phase_before_close": previous_phase.value,
                }
        except BaseException:  # noqa: BLE001 -- never replace an existing commit-unknown/cancellation.
            if not already_failed:
                raise RetainedBootstrapRefused(
                    "retained_pg_owned_cleanup_refused"
                ) from None
        finally:
            if working_fd is not None:
                os.close(working_fd)
            os.close(cold_fd)


def begin_owned_preserved_pg_bootstrap_v1(files, pins):
    """Create a new preserved cold-copy server; never adopt an existing one."""
    return asynccontextmanager(_owned_bootstrap_generator_v1)(
        files, pins, _Origin.PRESERVED
    )


def _snapshot_fresh_cold_v1(root_fd, *, nonce, system_identifier):
    entries, total = [], 0

    def walk(descriptor, prefix, depth):
        nonlocal total
        _need(depth <= 32 and len(entries) <= 100000)
        info = os.fstat(descriptor)
        _need((info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) == (70, 70, 0o700))
        entries.append({"path": prefix, "mode": info.st_mode})
        for name in sorted(os.listdir(descriptor)):
            relative = name if prefix == "" else prefix + "/" + name
            _relative(relative)
            before = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            if stat.S_ISDIR(before.st_mode):
                child = os.open(
                    name,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=descriptor,
                )
                try:
                    _need(
                        _directory_identity(os.fstat(child))
                        == _directory_identity(before)
                    )
                    walk(child, relative, depth + 1)
                finally:
                    os.close(child)
            else:
                _need(
                    stat.S_ISREG(before.st_mode)
                    and before.st_nlink == 1
                    and 0 <= before.st_size <= 2**30
                )
                _need(
                    (before.st_uid, before.st_gid, stat.S_IMODE(before.st_mode))
                    == (70, 70, 0o600)
                )
                child = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=descriptor)
                try:
                    opened = os.fstat(child)
                    _need(_stable(opened) == _stable(before))
                    hasher, count = hashlib.sha256(), 0
                    while chunk := os.read(child, 1024 * 1024):
                        count += len(chunk)
                        _need(count <= before.st_size)
                        hasher.update(chunk)
                    _need(
                        count == before.st_size
                        and _stable(opened) == _stable(os.fstat(child))
                    )
                finally:
                    os.close(child)
                entries.append(
                    {
                        "path": relative,
                        "mode": before.st_mode,
                        "size": count,
                        "sha256": hasher.hexdigest(),
                    }
                )
                total += count
                _need(total <= 2**31)

    walk(root_fd, "", 0)
    raw = _canonical(
        {
            "schema": "ctcc.retained_pg.fresh_canonical17_cold.v1",
            "origin_nonce": nonce,
            "source17_prefix_sha256": SOURCE17_PREFIX_SHA256,
            "system_identifier_sha256": system_identifier,
            "entries": entries,
            "regular_bytes": total,
        }
    )
    parsed = _cold_manifest(raw, origin=_Origin.FRESH_FIXTURE)
    _verify_cold_tree(root_fd, parsed)
    _need(_sha(raw) != PRESERVED_MANIFEST_SHA256)
    return raw, parsed


def _build_fresh_schema17_v1(connection):
    from alembic.config import Config
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from alembic.script import ScriptDirectory

    from scripts import retained0017_owner_upgrade_0055_v1 as owner_api

    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    script = ScriptDirectory.from_config(config)
    _need(tuple(script.get_heads()) == ("0058",))
    migration = MigrationContext.configure(
        connection,
        opts={
            "fn": lambda heads, _: script._upgrade_revs("0017", heads),
            "target_metadata": owner_api.Base.metadata,
            "compare_type": True,
        },
    )
    _need(migration.get_current_heads() == ())
    with Operations.context(migration), migration.begin_transaction():
        migration.run_migrations()
    _need(migration.get_current_heads() == ("0017",))


def _new_server_v1(rootfs, runtime, settings):
    arguments = [
        "/usr/local/bin/postgres",
        "-D",
        "/run/data",
        "-c",
        "config_file=/run/maintenance.conf",
    ]
    for name, value in settings.items():
        arguments.extend(
            (
                "-c",
                name
                + "="
                + (
                    value[1:-1]
                    if value.startswith("'") and value.endswith("'")
                    else value
                ),
            )
        )
    parent = os.getpid()
    common = {
        "preexec_fn": lambda: _child_preexec(rootfs, parent),
        "env": _fixed_environment(),
        "close_fds": True,
        "stdin": subprocess.DEVNULL,
    }
    log = os.open(
        runtime / "server.private.log",
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
    )
    try:
        return subprocess.Popen(
            arguments,
            **common,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        ), common
    finally:
        os.close(log)


async def _wait_server_v1(server, common, *, database, owner):
    options = "-c session_preload_libraries= -c local_preload_libraries= -c role=none -c search_path=pg_catalog -c default_transaction_read_only=on -c statement_timeout=10000 -c lock_timeout=1000"
    readiness = {
        **common,
        "env": {
            **common["env"],
            "PGCONNECT_TIMEOUT": "1",
            "PGSSLMODE": "verify-full",
            "PGSSLROOTCERT": "/run/ca-certificate.pem",
            "PGSSLCERT": "",
            "PGSSLKEY": "",
            "PGOPTIONS": options,
        },
    }
    async with asyncio.timeout(BOOTSTRAP_SECONDS):
        while True:
            _need(server.returncode is None and not _child_exited(server))
            ready = _run_image(
                [
                    "/usr/local/bin/pg_isready",
                    "-h",
                    "127.0.0.1",
                    "-p",
                    str(PORT),
                    "-U",
                    owner,
                    "-d",
                    database,
                    "-t",
                    "1",
                ],
                readiness,
                2,
            )
            if ready.returncode == 0:
                return
            _need(ready.returncode in (1, 2))
            await asyncio.sleep(0.025)


async def _fresh_cold_generator_v1(resources, source):
    _linux()
    _need(type(resources) is FreshCanonical17ResourcesV1)
    _need(
        type(source) is dict
        and set(source) == {"commit", "tree", "archive", "manifest"}
    )
    source = json.loads(_canonical(source))
    _need(_current_executable_sha() == PYTHON_ELF_SHA256)
    _require_runtime_provider(resources, logical=True)
    _verify_image(Path(resources.pg_rootfs), Path(resources.pg_inventory_file))
    _verify_source(resources, source)
    _require_runtime_metadata_v1()
    from sqlalchemy import text
    from sqlalchemy.engine import URL
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    from scripts import native_owner_heap_am_v1 as storage
    from scripts import retained0017_owner_plan_v1 as original
    from scripts import retained0017_owner_upgrade_0055_v1 as owner_api

    if _NATIVE_GUARD is None:
        _install_owner_guard_v1()
    _guard()
    parent = Path(resources.private_output_parent)
    _private_parent(parent)
    creation_namespaces = _private_namespaces()
    _loopback_up()
    outer = parent / ("ctcc-fresh-canonical17-" + uuid.uuid4().hex)
    outer.mkdir(mode=0o700)
    runtime, rootfs = outer / "runtime", outer / "pg-rootfs"
    runtime.mkdir(mode=0o700)
    rootfs.mkdir(mode=0o700)
    os.chown(runtime, 70, 70)
    for name in ("data", "socket"):
        (runtime / name).mkdir(mode=0o700)
        os.chown(runtime / name, 70, 70)
    _mount_pg_image(Path(resources.pg_rootfs), rootfs, runtime)
    supervisor = os.getpid()
    common = {
        "preexec_fn": lambda: _child_preexec(rootfs, supervisor),
        "env": _fixed_environment(),
        "close_fds": True,
        "stdin": subprocess.DEVNULL,
    }
    initialized = _run_image(
        [
            "/usr/local/bin/initdb",
            "-D",
            "/run/data",
            "--auth-local=reject",
            "--auth-host=reject",
            "--username=ctcc_fixture_owner",
            "--locale=en_US.utf8",
            "--encoding=UTF8",
        ],
        common,
        20,
    )
    _write_private(outer / "initdb.stdout.private", initialized.stdout)
    _write_private(outer / "initdb.stderr.private", initialized.stderr)
    _need(initialized.returncode == 0)
    system = _control_state(
        _run_image(["/usr/local/bin/pg_controldata", "/run/data"], common, 5)
    )
    tls = _tls_inputs(resources)
    nonce = uuid.uuid4().hex
    settings = _bootstrap_config(
        runtime,
        database="postgres",
        owner="ctcc_fixture_owner",
        cluster_name="ctcc_fresh_" + nonce,
        tls=tls,
    )
    # Only a newly initialized fixture has this second fixed route. No caller
    # name/SQL or retained-cluster role/password/config mutation is accepted.
    hba = runtime / "hba.conf"
    descriptor = os.open(hba, os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW)
    try:
        raw = b"hostssl ctcc_fixture_db ctcc_fixture_owner 127.0.0.1/32 trust\n"
        # Put the new fixed route BEFORE the terminal reject rules.
        os.ftruncate(descriptor, 0)
        os.write(
            descriptor,
            b"hostssl postgres ctcc_fixture_owner 127.0.0.1/32 trust\n"
            + raw
            + b"local all all reject\nhost all all 0.0.0.0/0 reject\nhost all all ::0/0 reject\n",
        )
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    server = engine = fresh = None
    cold_fd = None
    try:
        server, common = _new_server_v1(rootfs, runtime, settings)
        await _wait_server_v1(
            server, common, database="postgres", owner="ctcc_fixture_owner"
        )
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_verify_locations(cadata=tls[0].decode("ascii"))
        url = URL.create(
            "postgresql+asyncpg",
            username="ctcc_fixture_owner",
            host="127.0.0.1",
            port=PORT,
            database="postgres",
        )
        arguments = {
            "ssl": context,
            "timeout": 10,
            "server_settings": {
                **owner_api._STARTUP_SETTINGS,
                "default_transaction_read_only": "off",
            },
        }
        engine = create_async_engine(
            url, poolclass=NullPool, connect_args=arguments, hide_parameters=True
        )
        async with engine.connect() as connection:
            await connection.execution_options(isolation_level="AUTOCOMMIT")
            await connection.exec_driver_sql(
                'CREATE DATABASE "ctcc_fixture_db" OWNER "ctcc_fixture_owner" TEMPLATE template0 ENCODING \'UTF8\''
            )
        await engine.dispose()
        engine = create_async_engine(
            url.set(database="ctcc_fixture_db"),
            poolclass=NullPool,
            connect_args=arguments,
            hide_parameters=True,
        )
        async with engine.begin() as connection:
            await connection.exec_driver_sql(
                "SET LOCAL search_path TO public, pg_catalog, pg_temp"
            )
            await connection.exec_driver_sql("SET LOCAL TIME ZONE 'UTC'")
            _guard()
            _verify_source(resources, source)
            await connection.run_sync(_build_fresh_schema17_v1)
            await connection.execute(
                text("""INSERT INTO public.demo_automation_state
 (id,armed,emergency_stop,locked,lock_reasons,session_date,baseline_equity,peak_equity,daily_pnl,
 trades_today,consecutive_losses,risk_peak_equity,realized_pnl_events)
 VALUES(1,false,true,true,'["unknown_retained_reason","unknown_retained_reason"]',current_date,
 300,325,-12.5,13,3,330,'[{"net_pnl":"-12.5","source":"declared-synthetic-cold-fixture"}]')""")
            )
            await connection.execute(
                text(
                    "INSERT INTO public.audit_logs(id,actor,action,resource_type,resource_id,\"before\") VALUES(:id,'declared_fresh_cold','null_retention','synthetic','source17-cold-acceptance',NULL)"
                ),
                {"id": uuid.uuid4()},
            )
            await storage.lock_native_owner_marker_v1(connection)
            actor = tuple((await connection.execute(owner_api._PROFILE_SQL)).one())
            stored = await connection.scalar(
                owner_api._SETTINGS_HASH,
                {"owner_oid": actor[4], "database_oid": actor[5]},
            )
        route = original.RetainedOwnerRoute0017V1(
            source["manifest"],
            _sha(tls[0]),
            system,
            "127.0.0.1",
            PORT,
            "ctcc_fixture_db",
            "ctcc_fixture_owner",
            actor[5],
            actor[4],
        )
        await _capture_current_bootstrap_v1(
            engine,
            route,
            cluster_name="ctcc_fresh_" + nonce,
            system_identifier_sha256=system,
        )
        await engine.dispose()
        engine = None
        _need(_stop_held(server) == "FAST_SHUTDOWN_0")
        server = None
        _need(
            _control_state(
                _run_image(["/usr/local/bin/pg_controldata", "/run/data"], common, 5)
            )
            == system
        )
        cold_fd = os.open(
            runtime / "data", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        )
        manifest_raw, manifest = _snapshot_fresh_cold_v1(
            cold_fd, nonce=nonce, system_identifier=system
        )
        manifest_file = outer / "fresh-cold-manifest.private.json"
        _write_private(manifest_file, manifest_raw)
        pins = owner_api._FreshUpgradePinsV1(
            owner_api._FreshSourcePinsV1(
                source["commit"], source["tree"], source["archive"], manifest[3], route
            ),
            stored,
        )
        files = RetainedBootstrapFilesV1(
            **asdict(resources),
            cold_directory=str(runtime / "data"),
            cold_manifest_file=str(manifest_file),
        )
        fresh = object.__new__(OwnedFreshCanonical17ColdV1)
        _FRESH_COLD_RECORDS[id(fresh)] = _FreshColdRecord(
            fresh,
            files,
            pins,
            os.getpid(),
            threading.get_ident(),
            asyncio.current_task(),
            cold_fd,
            _directory_identity(os.fstat(cold_fd)),
            _sha(manifest_raw),
            creation_namespaces,
        )
        yield fresh
        expected_namespaces = (
            _FRESH_COLD_RECORDS[id(fresh)].working_namespaces or creation_namespaces
        )
        _need(_namespaces() == expected_namespaces)
        _verify_cold_tree(cold_fd, manifest)
    finally:
        if fresh is not None:
            _FRESH_COLD_RECORDS.pop(id(fresh), None)
        if server is not None:
            _need(_stop_held(server) == "FAST_SHUTDOWN_0")
        if engine is not None:
            await engine.dispose()
        if cold_fd is not None:
            os.close(cold_fd)


def _create_owned_fresh_canonical17_cold_v1(resources, source):
    return asynccontextmanager(_fresh_cold_generator_v1)(resources, source)


async def _fresh_bootstrap_generator_v1(fresh):
    _guard()
    _need(type(fresh) is OwnedFreshCanonical17ColdV1)
    record = _FRESH_COLD_RECORDS.get(id(fresh))
    _need(record is not None and record.owner is fresh and record.spent is False)
    _need(
        record.process_id == os.getpid()
        and record.thread_id == threading.get_ident()
        and record.task is asyncio.current_task()
    )
    _need(_namespaces() == record.creation_namespaces)
    _need(
        _directory_identity(os.fstat(record.directory_fd)) == record.directory_identity
    )
    _need(
        _sha(_private_bytes(Path(record.files.cold_manifest_file), 16 * 1024 * 1024))
        == record.manifest_sha256
    )
    record.spent = True
    async with asynccontextmanager(_owned_bootstrap_generator_v1)(
        record.files, record.pins, _Origin.FRESH_FIXTURE
    ) as owned:
        working = _record(owned).namespaces
        _need(
            all(working[name] != record.creation_namespaces[name] for name in working)
        )
        record.working_namespaces = dict(working)
        yield owned, record.pins


def _begin_owned_fresh_fixture_pg_v1(fresh):
    return asynccontextmanager(_fresh_bootstrap_generator_v1)(fresh)


def _extract_source(raw, source, target):
    members = _archive_members(raw, source)
    target.mkdir(mode=0o700)
    for name, (content, mode) in sorted(members.items()):
        path = _relative(name)
        parent = target
        for part in path.parts[:-1]:
            parent = parent / part
            if not parent.exists():
                parent.mkdir(mode=0o700)
            _need(parent.is_dir() and not parent.is_symlink())
        _write_private(target / path, content)
        os.chmod(target / path, mode)
    return members


def _read_request(path):
    raw = _private_bytes(path, 65536)
    value = json.loads(raw)
    _need(
        type(value) is dict
        and set(value) == {"schema", "files", "pins", "confirmation"}
    )
    _need(value["schema"] == "ctcc.retained_pg.preserved_owner_request.v1")
    _need(value["confirmation"] == "UPGRADE PRESERVED CTCC DB0017 TO STOPPED DB0055")
    _need(
        type(value["files"]) is dict
        and set(value["files"]) == set(RetainedBootstrapFilesV1.__dataclass_fields__)
    )
    _need(
        all(
            type(item) is str
            and len(item) <= 2048
            and Path(item).is_absolute()
            and ".." not in Path(item).parts
            for item in value["files"].values()
        )
    )
    pins = value["pins"]
    _need(
        type(pins) is dict and set(pins) == {"retained", "maintenance_settings_sha256"}
    )
    retained = pins["retained"]
    _need(
        type(retained) is dict
        and set(retained)
        == {
            "source_commit",
            "source_tree",
            "source_archive_sha256",
            "preserved_backup_sha256",
            "route",
            "expected_catalog_sha256",
        }
    )
    _need(type(retained["route"]) is dict)
    source = {
        "commit": retained["source_commit"],
        "tree": retained["source_tree"],
        "archive": retained["source_archive_sha256"],
        "manifest": retained["route"].get("source_manifest_sha256"),
    }
    _need(retained["preserved_backup_sha256"] == PRESERVED_BACKUP_SHA256)
    return value, RetainedBootstrapFilesV1(**value["files"]), source


def _read_fresh_request(path):
    value = json.loads(_private_bytes(path, 65536))
    _need(type(value) is dict and set(value) == {"schema", "resources", "source"})
    _need(value["schema"] == "ctcc.retained_pg.fresh_acceptance_request.v1")
    resources = value["resources"]
    _need(
        type(resources) is dict
        and set(resources) == set(FreshCanonical17ResourcesV1.__dataclass_fields__)
    )
    _need(
        all(
            type(item) is str
            and 0 < len(item) <= 2048
            and Path(item).is_absolute()
            and ".." not in Path(item).parts
            for item in resources.values()
        )
    )
    source = value["source"]
    _need(
        type(source) is dict
        and set(source) == {"commit", "tree", "archive", "manifest"}
    )
    _need(
        all(
            type(source[key]) is str
            and (_GIT if key in ("commit", "tree") else _HEX).fullmatch(source[key])
            is not None
            for key in source
        )
    )
    return value, FreshCanonical17ResourcesV1(**resources), source


def _supervisor_preexec(rootfs, parent_pid):
    import resource

    os.chroot(rootfs)
    os.chdir("/")
    os.setgroups([])
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    prctl = _libc(
        "prctl",
        [ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong],
    )
    _need(prctl(4, 0, 0, 0, 0) == 0)
    _need(prctl(1, signal.SIGKILL, 0, 0, 0) == 0)
    _need(os.getppid() == parent_pid and os.geteuid() == 0 and os.getgroups() == [])


def _readonly_bind(source, target):
    _mount(source, target, None, 4096)
    _mount(None, target, None, 4096 | 32 | 1 | 2 | 4)
    _need(os.statvfs(target).f_flag & os.ST_RDONLY != 0)


def _run_fixed_supervisor_v1(request, files, source, origin):
    """One fixed genuine-runtime launcher, no external proof/listener adapter.

    Its child executes this same archived module using the verified real venv.
    The child (not this launcher or a saved receipt) must create and retain the
    actual PG child/handles, replay TLS/catalog custody, and own the transaction.
    """
    _linux()
    _need(type(origin) is _Origin)
    _need(
        type(files)
        is (
            RetainedBootstrapFilesV1
            if origin is _Origin.PRESERVED
            else FreshCanonical17ResourcesV1
        )
    )
    venv, vendor_manifest, _locator_sha256 = _require_runtime_provider(files)
    _verify_image(Path(files.pg_rootfs), Path(files.pg_inventory_file))
    source_raw = _private_bytes(
        Path(files.source_archive_file), 128 * 1024 * 1024, expected=source["archive"]
    )
    members = _archive_members(source_raw, source)
    this_raw = _bounded_regular(Path(__file__), 256 * 1024)
    _need(
        members.get("scripts/retained_pg_bootstrap_custody_v1.py", (None,))[0]
        == this_raw
    )
    manifest_raw = None
    if origin is _Origin.PRESERVED:
        # Validate the fixed preserved origin BEFORE any image program starts.
        manifest_raw = _private_bytes(
            Path(files.cold_manifest_file),
            16 * 1024 * 1024,
            expected=PRESERVED_MANIFEST_SHA256,
        )
        cold_manifest = _cold_manifest(manifest_raw, origin=_Origin.PRESERVED)
        cold = os.open(
            files.cold_directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        )
        try:
            _verify_cold_tree(cold, cold_manifest)
        finally:
            os.close(cold)
    tls = _tls_inputs(files)
    parent = Path(files.private_output_parent)
    _private_parent(parent)
    _private_namespaces()
    outer = parent / ("ctcc-preserved-supervisor-" + uuid.uuid4().hex)
    outer.mkdir(mode=0o700)
    python_mount, runtime = outer / "python", outer / "runtime"
    python_mount.mkdir(mode=0o700)
    runtime.mkdir(mode=0o700)
    resources = runtime / "resources"
    resources.mkdir(mode=0o700)
    for name in ("pg-rootfs", "python-rootfs", "cold", "source"):
        (resources / name).mkdir(mode=0o700)
    (runtime / "venv").mkdir(mode=0o700)
    (runtime / "operations").mkdir(mode=0o700)
    _extract_source(source_raw, source, runtime / "source")
    _readonly_bind(runtime / "source", resources / "source")
    _readonly_bind(Path(files.pg_rootfs), resources / "pg-rootfs")
    _readonly_bind(Path(files.python_rootfs), resources / "python-rootfs")
    if origin is _Origin.PRESERVED:
        _readonly_bind(Path(files.cold_directory), resources / "cold")
    _readonly_bind(venv, runtime / "venv")
    _write_private(runtime / "vendor-manifest.json", vendor_manifest)
    for name, original in (
        ("provider.json", files.runtime_provider_file),
        ("pg-inventory.json", files.pg_inventory_file),
        ("python-inventory.json", files.python_inventory_file),
    ):
        _write_private(
            resources / name, _bounded_regular(Path(original), 2 * 1024 * 1024)
        )
    _write_private(resources / "source.tar", source_raw)
    if origin is _Origin.PRESERVED:
        _write_private(resources / "cold-manifest.json", manifest_raw)
    for name, raw in zip(("ca.pem", "server.pem", "server.key"), tls, strict=True):
        _write_private(resources / name, raw)
    common_resources = {
        "pg_rootfs": "/run/resources/pg-rootfs",
        "pg_inventory_file": "/run/resources/pg-inventory.json",
        "python_rootfs": "/run/resources/python-rootfs",
        "python_inventory_file": "/run/resources/python-inventory.json",
        "private_output_parent": "/run/operations",
        "ca_certificate_file": "/run/resources/ca.pem",
        "server_certificate_file": "/run/resources/server.pem",
        "server_private_key_file": "/run/resources/server.key",
        "runtime_provider_file": "/run/resources/provider.json",
        "source_archive_file": "/run/resources/source.tar",
    }
    if origin is _Origin.PRESERVED:
        mapped = RetainedBootstrapFilesV1(
            **common_resources,
            cold_directory="/run/resources/cold",
            cold_manifest_file="/run/resources/cold-manifest.json",
        )
        inner_request = {**request, "files": asdict(mapped)}
    else:
        mapped = FreshCanonical17ResourcesV1(**common_resources)
        inner_request = {**request, "resources": asdict(mapped)}
    _write_private(runtime / "request.json", _canonical(inner_request))
    _readonly_bind(Path(files.python_rootfs), python_mount)
    _mount(runtime, python_mount / "run", None, 4096)
    _mount("proc", python_mount / "proc", "proc", 1 | 2 | 4 | 8)
    _mount("tmpfs", python_mount / "dev", "tmpfs", 2 | 8, "mode=0755,size=1m")
    for name, major, minor in (("null", 1, 3), ("random", 1, 8), ("urandom", 1, 9)):
        os.mknod(
            python_mount / "dev" / name, stat.S_IFCHR | 0o666, os.makedev(major, minor)
        )
        os.chmod(python_mount / "dev" / name, 0o666)
    program = (
        "import sys;sys.path.insert(0,'/run/resources/source');from scripts.retained_pg_bootstrap_custody_v1 import _supervisor_main_v1;raise SystemExit(_supervisor_main_v1())"
        if origin is _Origin.PRESERVED
        else "import sys;sys.path.insert(0,'/run/resources/source');from scripts.retained_pg_bootstrap_custody_v1 import _supervisor_fresh_main_v1;raise SystemExit(_supervisor_fresh_main_v1())"
    )
    controller_pid = os.getpid()
    common = {
        "preexec_fn": lambda: _supervisor_preexec(python_mount, controller_pid),
        "env": _fixed_environment(),
        "stdin": subprocess.DEVNULL,
        "close_fds": True,
    }
    # The only child entry point and all paths are source literals. Request
    # contents never become Python source, shell text, argv or an environment.
    outcome = _run_image(
        ["/run/venv/bin/python", "-I", "-B", "-c", program],
        common,
        BOOTSTRAP_SECONDS + OWNER_SECONDS + CLEANUP_SECONDS,
    )
    _write_private(outer / "supervisor.stdout.private", outcome.stdout)
    _write_private(outer / "supervisor.stderr.private", outcome.stderr)
    _need(
        outcome.returncode == 0
        and len(outcome.stdout) <= 65536
        and outcome.stderr == b"",
        "retained_pg_owned_supervisor_refused",
    )
    result = json.loads(outcome.stdout)
    _need(type(result) is dict and result.get("execution_authority") is False)
    return result


def run_preserved_owner_supervisor_v1(request_file):
    _linux()
    _need(type(request_file) is type(Path()))
    request, files, source = _read_request(request_file)
    return _run_fixed_supervisor_v1(request, files, source, _Origin.PRESERVED)


def run_fresh_canonical17_acceptance_supervisor_v1(request_file):
    """Explicit disposable-source acceptance; no preserved PG location exists."""
    _linux()
    _need(type(request_file) is type(Path()))
    request, resources, source = _read_fresh_request(request_file)
    return _run_fixed_supervisor_v1(request, resources, source, _Origin.FRESH_FIXTURE)


async def _execute_owned_request_v1(request, files):
    from scripts import retained0017_owner_plan_v1 as original
    from scripts import retained0017_owner_upgrade_0055_v1 as owner_api

    retained = request["pins"]["retained"]
    route = original.RetainedOwnerRoute0017V1(**retained["route"])
    pins = owner_api.Retained0017UpgradePinsV1(
        original.RetainedOwnerPins0017V1(**{**retained, "route": route}),
        request["pins"]["maintenance_settings_sha256"],
    )
    async with begin_owned_preserved_pg_bootstrap_v1(files, pins):
        # Until the reviewed owner hook and real positive transaction fixture
        # land, this original unconditional fence remains the only outcome.
        # A successful cold copy/TLS/catalog receipt cannot bypass it.
        owner_api._require_apply_lineage()
        raise RetainedBootstrapRefused("retained_pg_owner_custody_wiring_pending")


async def _execute_fresh_acceptance_v1(resources, source):
    from scripts import retained0017_owner_upgrade_0055_v1 as owner_api

    async with _create_owned_fresh_canonical17_cold_v1(resources, source) as fresh:
        async with _begin_owned_fresh_fixture_pg_v1(fresh) as (owned, pins):
            result = await owner_api._exercise_owned_fresh_0017_to_0055_v1(
                owned, pins, source_archive_file=Path(resources.source_archive_file)
            )
        completed = _COMPLETED_DATA.pop(id(owned), None)
        _need(
            type(completed) is dict
            and completed["origin"] == _Origin.FRESH_FIXTURE.value
        )
        _need(
            completed["phase_before_close"] == _Phase.READBACK_ONLY.value
            and completed["commit_attempts"] == 1
            and completed["journal_steps"] == 7
        )
        _need(
            all(
                completed[name] is True
                for name in (
                    "cold_source_unchanged_verified",
                    "distinct_working_copy_verified",
                    "owned_server_fast_shutdown_verified",
                )
            )
        )
    return {
        **result,
        **{
            name: value
            for name, value in completed.items()
            if name not in ("phase_before_close", "journal_steps")
        },
        "schema": "ctcc.retained_pg.fresh_acceptance_result.v1",
        "status": "FRESH_CANONICAL17_COLD_TO_STOPPED0055_VERIFIED",
        "preserved_pg_inputs_read": 0,
    }


def _supervisor_fresh_main_v1():
    try:
        _linux()
        _need(Path(__file__).resolve().parents[1] == Path("/run/resources/source"))
        request, resources, source = _read_fresh_request(Path("/run/request.json"))
        _need(_current_executable_sha() == PYTHON_ELF_SHA256)
        _require_runtime_provider(resources, logical=True)
        _verify_image(Path(resources.pg_rootfs), Path(resources.pg_inventory_file))
        _verify_source(resources, source)
        _require_runtime_metadata_v1()
        _need(request["schema"] == "ctcc.retained_pg.fresh_acceptance_request.v1")
        outcome = asyncio.run(_execute_fresh_acceptance_v1(resources, source))
        print(_canonical(outcome).decode("ascii"))
        return 0
    except BaseException:  # noqa: BLE001 -- fixed DATA refusal; complete diagnostics remain private.
        import traceback

        try:
            _write_private(
                Path("/run/fresh-acceptance-failure.private.txt"),
                traceback.format_exc().encode("utf-8"),
            )
        except BaseException:  # noqa: BLE001 -- absent diagnostics cannot turn refusal into acceptance.
            return 2
        print('{"status":"REFUSED","execution_authority":false}')
        return 2


def _supervisor_main_v1():
    try:
        _linux()
        _need(Path(__file__).resolve().parents[1] == Path("/run/resources/source"))
        request, files, _source = _read_request(Path("/run/request.json"))
        # Replay all executable/source resources in this actual inner process
        # BEFORE the first application or vendor import below.
        _need(_current_executable_sha() == PYTHON_ELF_SHA256)
        _require_runtime_provider(files, logical=True)
        _verify_image(Path(files.pg_rootfs), Path(files.pg_inventory_file))
        _verify_source(files, _source)
        _require_runtime_metadata_v1()
        outcome = asyncio.run(_execute_owned_request_v1(request, files))
        print(_canonical(outcome).decode("ascii"))
        return 0
    except BaseException:  # noqa: BLE001 -- fixed private failure journal, no secrets in stdout.
        # Full diagnostics stay private in the owned runtime. No key, path,
        # database/role name, SQL content or connection data crosses stdout.
        import traceback

        try:
            _write_private(
                Path("/run/supervisor-failure.private.txt"),
                traceback.format_exc().encode("utf-8"),
            )
        except BaseException:  # noqa: BLE001 -- failure to retain diagnostics remains a hard refusal.
            return 2
        print('{"status":"REFUSED","execution_authority":false}')
        return 2
