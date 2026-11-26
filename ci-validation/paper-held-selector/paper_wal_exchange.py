"""One Linux paired PGDATA rename after a durable, no-clobber witness.

The caller verifies the exact trees and operator descriptor and retains the
operator lock. This module classifies the held inodes and names only.
"""

import ctypes
import json
import os
import re
import stat
import sys
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from enum import Enum
from hashlib import sha256

_SCHEMA = "ctcc.paper.wal_exchange_witness.v1"
_RENAME_EXCHANGE = 2
_LOCK = b"active.lock"
_NAME = re.compile(rb"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
_DIGEST = re.compile(r"[a-f0-9]{64}", re.ASCII)
_MOUNT = re.compile(rb"(?m)^mnt_id:[ \t]+([0-9]+)$")
_EXCLUSIVE_FLOCK = re.compile(
    rb"(?m)^lock:[ \t]+[0-9]+:[ \t]+FLOCK[ \t]+ADVISORY[ \t]+WRITE"
    rb"[ \t]+[0-9]+[ \t]+([0-9a-fA-F]+):([0-9a-fA-F]+):([0-9]+)"
    rb"[ \t]+0[ \t]+EOF$"
)
_MAX_RECORD = 8192


class ExchangeRefused(ValueError):
    """The requested exchange inputs or existing witness could not be verified."""


class ExchangeState(Enum):
    PRE = "PRE"
    POST = "POST"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class DirectoryIdentity:
    device: int
    inode: int


@dataclass(frozen=True, slots=True)
class ExchangePlan:
    descriptor_sha256: str
    original_name: bytes
    recovered_name: bytes
    original: DirectoryIdentity
    recovered: DirectoryIdentity
    original_tree_sha256: str
    recovered_tree_sha256: str


@dataclass(frozen=True, slots=True)
class ExchangeResult:
    state: ExchangeState
    observed: ExchangeState
    parent_fsynced: bool
    kernel_called: bool
    witness_sha256: str | None


def _need(condition):
    if not condition:
        raise ExchangeRefused("paper_wal_exchange_refused") from None


def _name(value):
    return type(value) is bytes and _NAME.fullmatch(value) is not None


def _checked_plan(plan):
    _need(sys.platform == "linux" and type(plan) is ExchangePlan)
    _need(_name(plan.original_name) and _name(plan.recovered_name))
    _need(len({plan.original_name, plan.recovered_name, _LOCK}) == 3)
    for pin in (plan.original, plan.recovered):
        _need(
            type(pin) is DirectoryIdentity
            and type(pin.device) is type(pin.inode) is int
        )
        _need(pin.device >= 0 and pin.inode > 0)
    _need(
        plan.original != plan.recovered
        and plan.original.device == plan.recovered.device
    )
    for digest in (
        plan.descriptor_sha256,
        plan.original_tree_sha256,
        plan.recovered_tree_sha256,
    ):
        _need(type(digest) is str and _DIGEST.fullmatch(digest) is not None)


def _witness_name(plan, name):
    _need(_name(name) and name not in {plan.original_name, plan.recovered_name, _LOCK})


def _fd_info(fd):
    _need(type(fd) is int and fd >= 0)
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
    with os.fdopen(os.open(f"/proc/self/fdinfo/{fd}", flags), "rb") as stream:
        raw = stream.read(4097)
    _need(0 < len(raw) <= 4096)
    return raw


def _mount_id(fd):
    matches = _MOUNT.findall(_fd_info(fd))
    _need(len(matches) == 1)
    return int(matches[0])


def _identity(info):
    return DirectoryIdentity(info.st_dev, info.st_ino)


@contextmanager
def _directory(parent, name):
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    fd = os.open(name, flags, dir_fd=parent)
    try:
        yield fd
    finally:
        os.close(fd)


@contextmanager
def _parent(parent_fd):
    with _directory(parent_fd, b".") as fd:
        info = os.fstat(fd)
        _need(_identity(info) == _identity(os.fstat(parent_fd)))
        _need(_mount_id(fd) == _mount_id(parent_fd))
        _need((info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) == (0, 0, 0o700))
        yield fd


def _lock_identity_verified(parent, lock_fd):
    held = os.fstat(lock_fd)
    current = os.stat(_LOCK, dir_fd=parent, follow_symlinks=False)
    _need(stat.S_ISREG(held.st_mode) and held.st_mode == current.st_mode)
    _need(
        (held.st_uid, held.st_gid, stat.S_IMODE(held.st_mode), held.st_nlink)
        == (0, 0, 0o600, 1)
    )
    _need((current.st_uid, current.st_gid, current.st_nlink) == (0, 0, 1))
    _need(
        _identity(held) == _identity(current)
        and _mount_id(lock_fd) == _mount_id(parent)
    )
    matches = _EXCLUSIVE_FLOCK.findall(_fd_info(lock_fd))
    _need(len(matches) == 1)
    major, minor, inode = matches[0]
    _need(
        (int(major, 16), int(minor, 16), int(inode))
        == (os.major(held.st_dev), os.minor(held.st_dev), held.st_ino)
    )


def _held_sides(parent, original_fd, recovered_fd, plan):
    parent_info = os.fstat(parent)
    original, recovered = os.fstat(original_fd), os.fstat(recovered_fd)
    _need(
        all(
            stat.S_ISDIR(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o700
            for info in (original, recovered)
        )
    )
    _need(
        _identity(original) == plan.original and _identity(recovered) == plan.recovered
    )
    _need(parent_info.st_dev == original.st_dev == recovered.st_dev)
    _need(_identity(parent_info) not in (plan.original, plan.recovered))
    _need((original.st_uid, original.st_gid) == (recovered.st_uid, recovered.st_gid))
    mount = _mount_id(parent)
    _need(_mount_id(original_fd) == _mount_id(recovered_fd) == mount)
    return mount


def _classify(parent, original_fd, recovered_fd, plan):
    try:
        mount = _held_sides(parent, original_fd, recovered_fd, plan)
        with (
            _directory(parent, plan.original_name) as first,
            _directory(parent, plan.recovered_name) as second,
        ):
            _need(_mount_id(first) == _mount_id(second) == mount)
            named = (_identity(os.fstat(first)), _identity(os.fstat(second)))
            # Read the sibling names again while both looked-up FDs are held.
            for name, expected in reversed(
                tuple(
                    zip((plan.original_name, plan.recovered_name), named, strict=True)
                )
            ):
                live = os.stat(name, dir_fd=parent, follow_symlinks=False)
                _need(stat.S_ISDIR(live.st_mode) and _identity(live) == expected)
        if named == (plan.original, plan.recovered):
            return ExchangeState.PRE
        if named == (plan.recovered, plan.original):
            return ExchangeState.POST
    except (OSError, ExchangeRefused):
        pass
    return ExchangeState.UNKNOWN


def inspect_exchange(parent_fd, original_fd, recovered_fd, *, plan):
    """Classify the two current names against the caller's held data directories."""
    _checked_plan(plan)
    try:
        with _parent(parent_fd) as parent:
            return _classify(parent, original_fd, recovered_fd, plan)
    except (OSError, ExchangeRefused):
        return ExchangeState.UNKNOWN


def _witness_bytes(parent, plan, name):
    return (
        json.dumps(
            {
                "schema": _SCHEMA,
                "descriptor_sha256": plan.descriptor_sha256,
                "parent": asdict(_identity(os.fstat(parent))),
                "original_name": plan.original_name.decode("ascii"),
                "recovered_name": plan.recovered_name.decode("ascii"),
                "original": asdict(plan.original),
                "recovered": asdict(plan.recovered),
                "original_tree_sha256": plan.original_tree_sha256,
                "recovered_tree_sha256": plan.recovered_tree_sha256,
                "witness_name": name.decode("ascii"),
                "execution_authority": False,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("ascii")
        + b"\n"
    )


def _file_signature(info):
    _need(stat.S_ISREG(info.st_mode))
    _need(
        (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode), info.st_nlink)
        == (0, 0, 0o600, 1)
    )
    return _identity(info), info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _verify_witness(parent, plan, name):
    expected = _witness_bytes(parent, plan, name)
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
    with os.fdopen(os.open(name, flags, dir_fd=parent), "rb") as stream:
        fd = stream.fileno()
        first = _file_signature(os.fstat(fd))
        _need(first[1] == len(expected) and _mount_id(fd) == _mount_id(parent))
        _need(stream.read(_MAX_RECORD + 1) == expected)
        _need(_file_signature(os.fstat(fd)) == first)
        _need(
            _file_signature(os.stat(name, dir_fd=parent, follow_symlinks=False))
            == first
        )
    return sha256(expected).hexdigest()


def verify_witness(parent_fd, *, plan, witness_name):
    """Require an exact existing preexchange witness and return its digest."""
    _checked_plan(plan)
    _witness_name(plan, witness_name)
    with _parent(parent_fd) as parent:
        return _verify_witness(parent, plan, witness_name)


def _write_witness(parent, plan, name):
    raw = _witness_bytes(parent, plan, name)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
    fd = os.open(name, flags, 0o600, dir_fd=parent)
    try:
        os.fchmod(fd, 0o600)
        _file_signature(os.fstat(fd))
        _need(_mount_id(fd) == _mount_id(parent))
        pending = memoryview(raw)
        while pending:
            written = os.write(fd, pending)
            _need(written > 0)
            pending = pending[written:]
        os.fsync(fd)
    finally:
        os.close(fd)
    os.fsync(parent)
    return _verify_witness(parent, plan, name)


def _kernel_exchange():
    try:
        rename = ctypes.CDLL(None, use_errno=True).renameat2
    except AttributeError:
        raise ExchangeRefused("paper_wal_atomic_exchange_unavailable") from None
    rename.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    rename.restype = ctypes.c_int
    return rename


def exchange_pgdata(
    parent_fd, original_fd, recovered_fd, *, plan, witness_name, lock_fd
):
    """Attempt one RENAME_EXCHANGE after durably recording both potential layouts."""
    _checked_plan(plan)
    _witness_name(plan, witness_name)
    with _parent(parent_fd) as parent:
        _lock_identity_verified(parent, lock_fd)
        observed = _classify(parent, original_fd, recovered_fd, plan)
        if observed is not ExchangeState.PRE:
            return ExchangeResult(ExchangeState.UNKNOWN, observed, False, False, None)
        rename = _kernel_exchange()
        try:
            witness = _write_witness(parent, plan, witness_name)
        except FileExistsError:
            witness = _verify_witness(parent, plan, witness_name)
            return ExchangeResult(
                ExchangeState.UNKNOWN, observed, False, False, witness
            )

        _lock_identity_verified(parent, lock_fd)
        observed = _classify(parent, original_fd, recovered_fd, plan)
        if observed is not ExchangeState.PRE:
            return ExchangeResult(
                ExchangeState.UNKNOWN, observed, False, False, witness
            )
        kernel_result = rename(
            parent, plan.original_name, parent, plan.recovered_name, _RENAME_EXCHANGE
        )
        observed = _classify(parent, original_fd, recovered_fd, plan)
        if kernel_result == -1 and observed is ExchangeState.PRE:
            return ExchangeResult(ExchangeState.PRE, observed, True, True, witness)
        if kernel_result != 0 or observed is not ExchangeState.POST:
            return ExchangeResult(ExchangeState.UNKNOWN, observed, False, True, witness)
        try:
            os.fsync(parent)
            _lock_identity_verified(parent, lock_fd)
            observed = _classify(parent, original_fd, recovered_fd, plan)
            _need(_verify_witness(parent, plan, witness_name) == witness)
            _need(observed is ExchangeState.POST)
        except (ExchangeRefused, OSError):
            return ExchangeResult(ExchangeState.UNKNOWN, observed, False, True, witness)
        return ExchangeResult(ExchangeState.POST, observed, True, True, witness)
