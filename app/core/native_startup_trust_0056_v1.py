"""Read the DB0056 startup reference installed by the OS administrator."""

from __future__ import annotations

import os
import re
import stat
import sys
from contextlib import ExitStack
from dataclasses import dataclass, field, fields
from typing import Literal

from app.domain.source_primitives import canonical, decode, sha

_SCHEMA = "ctcc.native_only.db0056_admin_startup_trust.v1"
_MODE = "read_only_control_monitoring"
_DIRECTORIES = ("run", "ctcc-native")
_FILENAME = "0056-startup-trust.json"
_LIMIT = 4096
_PROC_STATUS_LIMIT = 16384
_CAPABILITY_FIELDS = (b"CapPrm:", b"CapEff:", b"CapAmb:")
_LINUX = sys.platform == "linux"
_DIGEST = re.compile(r"[a-f0-9]{64}", re.ASCII)
_GIT = re.compile(r"[a-f0-9]{40}", re.ASCII)


class NativeStartupTrust0056Error(ValueError):
    """Fixed refusal that keeps paths and trust contents private."""


def _require(condition: bool) -> None:
    if condition is not True:
        raise NativeStartupTrust0056Error("native_startup_trust_0056_refused") from None


@dataclass(frozen=True, slots=True, repr=False)
class AdminStartupTrust0056V1:
    descriptor_sha256: str
    source_commit: str
    source_tree: str
    source_archive_sha256: str
    source_manifest_sha256: str
    tls_ca_sha256: str
    restricted_role_identity_sha256: str
    inherited_native_definitions_sha256: str
    granted_inherited_acl_shape_sha256: str
    reviewed_portable_reference_sha256: str
    trust_sha256: str
    startup_mode: Literal["read_only_control_monitoring"] = field(
        default=_MODE, init=False
    )
    database_head: Literal["0056"] = field(default="0056", init=False)


_REFERENCE_FIELDS = tuple(
    definition.name
    for definition in fields(AdminStartupTrust0056V1)
    if definition.init and definition.name != "trust_sha256"
)


def _parse(raw: bytes) -> AdminStartupTrust0056V1:
    value = decode(raw, _LIMIT)
    _require(
        type(value) is dict
        and set(value)
        == {"schema", "database_head", "startup_mode", *_REFERENCE_FIELDS}
        and value["schema"] == _SCHEMA
        and value["database_head"] == "0056"
        and value["startup_mode"] == _MODE
        and canonical(value) == raw
    )
    for name in _REFERENCE_FIELDS:
        digest = value[name]
        pattern = _GIT if name in {"source_commit", "source_tree"} else _DIGEST
        _require(type(digest) is str and pattern.fullmatch(digest) is not None)
    return AdminStartupTrust0056V1(
        **{name: value[name] for name in _REFERENCE_FIELDS}, trust_sha256=sha(raw)
    )


def _require_runtime() -> int:
    _require(os.name == "posix")
    uid = os.getuid()
    _require(uid > 0 and os.geteuid() == uid)
    getresuid = getattr(os, "getresuid", None)
    if getresuid is not None:
        _require(getresuid() == (uid, uid, uid))
    if _LINUX:
        lines = _linux_status().splitlines()
        for field_prefix in _CAPABILITY_FIELDS:
            values = [
                line[len(field_prefix) :].strip()
                for line in lines
                if line.startswith(field_prefix)
            ]
            _require(
                len(values) == 1 and re.fullmatch(rb"0{16}", values[0]) is not None
            )
    return uid


def _linux_status() -> bytes:
    with open("/proc/self/status", "rb") as stream:
        raw = stream.read(_PROC_STATUS_LIMIT + 1)
    _require(0 < len(raw) <= _PROC_STATUS_LIMIT)
    return raw


def _directory_identity(info: os.stat_result) -> tuple[int, ...]:
    _require(
        stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not (info.st_mode & 0o022)
    )
    return info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid


def _file_identity(info: os.stat_result) -> tuple[int, ...]:
    _require(
        stat.S_ISREG(info.st_mode)
        and info.st_uid == 0
        and stat.S_IMODE(info.st_mode) == 0o444
        and info.st_nlink == 1
        and 0 < info.st_size <= _LIMIT
    )
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_uid,
        info.st_gid,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _read_trust_path() -> bytes:
    """Hold the administrator-owned directory lineage while reading the leaf."""
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
    file_flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK
    with ExitStack() as stack:
        root = os.open("/", directory_flags)
        stack.callback(os.close, root)
        root_identity = _directory_identity(os.fstat(root))
        _require(not os.access("/", os.W_OK, follow_symlinks=False))
        parent = root
        directories = []
        for name in _DIRECTORIES:
            held = os.open(name, directory_flags, dir_fd=parent)
            stack.callback(os.close, held)
            identity = _directory_identity(os.fstat(held))
            directories.append((parent, name, held, identity))
            _require(not os.access(name, os.W_OK, dir_fd=parent, follow_symlinks=False))
            parent = held
        initial_stat = os.stat(_FILENAME, dir_fd=parent, follow_symlinks=False)
        initial = _file_identity(initial_stat)
        leaf = os.open(_FILENAME, file_flags, dir_fd=parent)
        stack.callback(os.close, leaf)
        _require(
            _file_identity(os.fstat(leaf)) == initial
            and not os.access(_FILENAME, os.W_OK, dir_fd=parent, follow_symlinks=False)
        )
        with os.fdopen(leaf, "rb", closefd=False) as stream:
            raw = stream.read(_LIMIT + 1)
        _require(
            len(raw) == initial_stat.st_size
            and _file_identity(os.fstat(leaf)) == initial
            and _file_identity(os.stat(_FILENAME, dir_fd=parent, follow_symlinks=False))
            == initial
            and _directory_identity(os.fstat(root)) == root_identity
            and _directory_identity(os.stat("/", follow_symlinks=False))
            == root_identity
            and not os.access("/", os.W_OK, follow_symlinks=False)
        )
        for directory, name, held, expected in directories:
            _require(
                _directory_identity(os.fstat(held)) == expected
                and _directory_identity(
                    os.stat(name, dir_fd=directory, follow_symlinks=False)
                )
                == expected
            )
        return raw


def read_native_startup_trust_0056_v1() -> AdminStartupTrust0056V1:
    """Read fixed administrator-controlled pins. This performs no service startup."""
    try:
        uid = _require_runtime()
        trust = _parse(_read_trust_path())
        _require(_require_runtime() == uid)
        return trust
    except NativeStartupTrust0056Error:
        raise
    except Exception:  # noqa: BLE001 -- expose neither file contents nor OS details.
        raise NativeStartupTrust0056Error("native_startup_trust_0056_refused") from None
