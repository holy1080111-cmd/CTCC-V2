"""Copy and independently verify an owned, quiescent PostgreSQL data directory.

The caller holds the operation and source device fences. This module never
starts PostgreSQL, follows an external path, or removes an incomplete copy.
"""

import os
import re
import stat
import sys
from dataclasses import dataclass, field
from hashlib import sha256

_MAX_ENTRIES = 100000
_MAX_DEPTH = 32
_MAX_PATH_BYTES = 4096
_MAX_FILE_BYTES = 8 * 1024**3
_MAX_TREE_BYTES = 64 * 1024**3
_BLOCK = 1024 * 1024
_UNSUPPORTED_ROOT_FILES = {
    b"standby.signal",
    b"recovery.signal",
    b"backup_label",
    b"tablespace_map",
}
_MOUNT_ID = re.compile(rb"(?m)^mnt_id:[ \t]+([0-9]+)$")


class ColdCopyRefused(ValueError):
    """The held trees do not establish a verified distinct physical copy."""


@dataclass(frozen=True, slots=True)
class ColdEntry:
    relative_path: bytes
    kind: str
    mode: int
    size: int
    sha256: str | None


@dataclass(frozen=True, slots=True)
class ColdTree:
    entries: tuple[ColdEntry, ...]
    tree_sha256: str
    file_count: int
    total_bytes: int
    device: int
    inode: int
    _physical_metadata: tuple[tuple, ...] = field(default=(), repr=False)


@dataclass(frozen=True, slots=True)
class ColdCopyResult:
    source_before: ColdTree
    source_after: ColdTree
    destination_after: ColdTree


@dataclass(frozen=True, slots=True)
class _Scan:
    tree: ColdTree
    metadata: dict[bytes, tuple]
    inodes: frozenset[tuple[int, int]]


def _need(condition):
    if not condition:
        raise ColdCopyRefused("paper_wal_cold_copy_refused") from None


def _inode(info):
    return info.st_dev, info.st_ino


def _metadata(info):
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


def _mount(fd):
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
    reader = os.open(f"/proc/self/fdinfo/{fd}", flags)
    try:
        raw = os.read(reader, 4097)
        _need(0 < len(raw) <= 4096 and not os.read(reader, 1))
    finally:
        os.close(reader)
    values = _MOUNT_ID.findall(raw)
    _need(len(values) == 1)
    return int(values[0])


def _directory_flags():
    return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NOATIME


def _file_flags():
    return os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC | os.O_NOATIME


def _names(fd, limit):
    names = []
    # scandir may advance the supplied directory's shared open-file offset.
    enumeration = os.open(b".", _directory_flags(), dir_fd=fd)
    try:
        with os.scandir(enumeration) as listing:
            for entry in listing:
                _need(len(names) < limit)
                name = os.fsencode(entry.name)
                _need(0 < len(name) <= 255 and name not in (b".", b".."))
                names.append(name)
    finally:
        os.close(enumeration)
    _need(len(set(names)) == len(names))
    return sorted(names)


def _verify_shape(entries):
    by_name = {entry.relative_path: entry.kind for entry in entries}
    _need(by_name.get(b"PG_VERSION") == by_name.get(b"global/pg_control") == "file")
    _need(
        by_name.get(b"global")
        == by_name.get(b"pg_wal")
        == by_name.get(b"pg_tblspc")
        == "directory"
    )


def _make_tree(entries, metadata, file_count, total_bytes, root_info):
    ordered = tuple(sorted(entries, key=lambda entry: entry.relative_path))
    digest = sha256(b"ctcc.paper.wal_cold_tree.v1\0")
    for entry in ordered:
        raw = entry.relative_path
        digest.update(len(raw).to_bytes(4, "big"))
        digest.update(raw)
        digest.update(b"f" if entry.kind == "file" else b"d")
        digest.update(entry.mode.to_bytes(2, "big"))
        digest.update(entry.size.to_bytes(8, "big"))
        if entry.sha256 is not None:
            digest.update(bytes.fromhex(entry.sha256))
    return ColdTree(
        ordered,
        digest.hexdigest(),
        file_count,
        total_bytes,
        *_inode(root_info),
        tuple(metadata[entry.relative_path] for entry in ordered),
    )


def _configure_new(fd, uid, gid, mode):
    info = os.fstat(fd)
    if (info.st_uid, info.st_gid) != (uid, gid):
        os.fchown(fd, uid, gid)
    os.fchmod(fd, mode)


def _copy_bytes(reader, writer, size):
    digest = sha256()
    remaining = size
    while remaining:
        block = os.read(reader, min(_BLOCK, remaining))
        _need(block)
        digest.update(block)
        remaining -= len(block)
        if writer is not None:
            pending = memoryview(block)
            while pending:
                written = os.write(writer, pending)
                _need(written > 0)
                pending = pending[written:]
    _need(not os.read(reader, 1))
    return digest.hexdigest()


def _scan(root, uid, gid, *, destination=None, baseline=None, excluded_inode=None):
    root_info = os.fstat(root)
    mount = _mount(root)
    target_mount = None if destination is None else _mount(destination)
    entries, metadata, inodes = [], {}, set()
    file_count = total_bytes = 0

    def remember(relative, info):
        identity = _inode(info)
        found = _metadata(info)
        _need(
            info.st_dev == root_info.st_dev
            and (info.st_uid, info.st_gid) == (uid, gid)
            and identity != excluded_inode
            and identity not in inodes
            and len(entries) < _MAX_ENTRIES
        )
        if baseline is not None:
            _need(baseline.metadata.get(relative) == found)
        inodes.add(identity)
        metadata[relative] = found

    def scan_file(parent, name, relative, listed, target):
        nonlocal file_count, total_bytes
        _need(stat.S_IMODE(listed.st_mode) == 0o600 and listed.st_nlink == 1)
        _need(0 <= listed.st_size <= _MAX_FILE_BYTES)
        _need(total_bytes + listed.st_size <= _MAX_TREE_BYTES)
        source = os.open(name, _file_flags(), dir_fd=parent)
        output = None
        try:
            _need(
                _metadata(os.fstat(source)) == _metadata(listed)
                and _mount(source) == mount
            )
            remember(relative, listed)
            if target is not None:
                flags = (
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
                )
                output = os.open(name, flags, 0o600, dir_fd=target)
                _need(_mount(output) == target_mount and os.fstat(output).st_nlink == 1)
                _configure_new(output, uid, gid, 0o600)
            digest = _copy_bytes(source, output, listed.st_size)
            _need(_metadata(os.fstat(source)) == _metadata(listed))
            _need(
                _metadata(os.stat(name, dir_fd=parent, follow_symlinks=False))
                == _metadata(listed)
            )
            if output is not None:
                os.fsync(output)
        finally:
            if output is not None:
                os.close(output)
            os.close(source)
        file_count += 1
        total_bytes += listed.st_size
        entries.append(ColdEntry(relative, "file", 0o600, listed.st_size, digest))

    def walk(directory, relative, depth, target):
        info = os.fstat(directory)
        _need(
            depth <= _MAX_DEPTH
            and stat.S_ISDIR(info.st_mode)
            and stat.S_IMODE(info.st_mode) == 0o700
        )
        _need(_mount(directory) == mount)
        remember(relative, info)
        entries.append(ColdEntry(relative, "directory", 0o700, 0, None))
        names = _names(directory, _MAX_ENTRIES - len(entries))
        if not relative:
            _need(_UNSUPPORTED_ROOT_FILES.isdisjoint(names))
        if relative == b"pg_tblspc":
            _need(not names)
        for name in names:
            child = relative + b"/" + name if relative else name
            _need(len(child) <= _MAX_PATH_BYTES)
            listed = os.stat(name, dir_fd=directory, follow_symlinks=False)
            _need(listed.st_dev == root_info.st_dev)
            if stat.S_ISREG(listed.st_mode):
                scan_file(directory, name, child, listed, target)
                continue
            _need(stat.S_ISDIR(listed.st_mode))
            source = os.open(name, _directory_flags(), dir_fd=directory)
            output = None
            try:
                _need(
                    _metadata(os.fstat(source)) == _metadata(listed)
                    and _mount(source) == mount
                )
                if target is not None:
                    os.mkdir(name, 0o700, dir_fd=target)
                    output = os.open(name, _directory_flags(), dir_fd=target)
                    _need(_mount(output) == target_mount)
                    _configure_new(output, uid, gid, 0o700)
                walk(source, child, depth + 1, output)
                _need(
                    _metadata(os.stat(name, dir_fd=directory, follow_symlinks=False))
                    == _metadata(listed)
                )
            finally:
                if output is not None:
                    os.close(output)
                os.close(source)
        _need(
            _names(directory, len(names)) == names
            and _metadata(os.fstat(directory)) == _metadata(info)
        )
        if target is not None:
            os.fsync(target)

    walk(root, b"", 0, destination)
    _verify_shape(entries)
    tree = _make_tree(entries, metadata, file_count, total_bytes, root_info)
    return _Scan(tree, metadata, frozenset(inodes))


def _same_content(first, second):
    return (
        first.entries == second.entries
        and first.tree_sha256 == second.tree_sha256
        and first.file_count == second.file_count
        and first.total_bytes == second.total_bytes
    )


def inspect_cold_pgdata(source_fd, *, owner_uid=70, owner_gid=70):
    """Read the strict raw PGDATA inventory without consuming the caller's FD."""
    _need(sys.platform == "linux" and type(source_fd) is int and source_fd >= 0)
    _need(type(owner_uid) is type(owner_gid) is int and min(owner_uid, owner_gid) >= 0)
    source = os.open(b".", _directory_flags(), dir_fd=source_fd)
    try:
        _need(_mount(source) == _mount(source_fd))
        inspected = _scan(source, owner_uid, owner_gid)
        _need(inspected.metadata[b""] == _metadata(os.fstat(source_fd)))
        return inspected.tree
    finally:
        os.close(source)


def copy_cold_pgdata(source_fd, destination_fd, *, owner_uid=70, owner_gid=70):
    """Copy all eligible bytes into the held, separately reserved empty PGDATA."""
    _need(sys.platform == "linux")
    _need(
        type(source_fd) is type(destination_fd) is int
        and min(source_fd, destination_fd) >= 0
    )
    _need(type(owner_uid) is type(owner_gid) is int and min(owner_uid, owner_gid) >= 0)
    source = os.open(b".", _directory_flags(), dir_fd=source_fd)
    try:
        destination = os.open(b".", _directory_flags(), dir_fd=destination_fd)
        try:
            source_info = os.fstat(source)
            destination_info = os.fstat(destination)
            source_mount, destination_mount = _mount(source), _mount(destination)
            _need(
                (_mount(source_fd), _mount(destination_fd))
                == (source_mount, destination_mount)
            )
            _need(_inode(source_info) == _inode(os.fstat(source_fd)))
            _need(_inode(destination_info) == _inode(os.fstat(destination_fd)))
            _need(_inode(source_info) != _inode(destination_info))
            _need(
                (destination_info.st_uid, destination_info.st_gid)
                == (owner_uid, owner_gid)
            )
            _need(
                stat.S_IMODE(destination_info.st_mode) == 0o700
                and not _names(destination, 1)
            )
            destination_identity = _inode(destination_info)
            before = _scan(
                source, owner_uid, owner_gid, excluded_inode=destination_identity
            )
            _need(not _names(destination, 1))
            copied = _scan(
                source,
                owner_uid,
                owner_gid,
                destination=destination,
                baseline=before,
                excluded_inode=destination_identity,
            )
            after = _scan(
                source, owner_uid, owner_gid, excluded_inode=destination_identity
            )
            checked = _scan(destination, owner_uid, owner_gid)
            _need(before == copied == after)
            _need(
                _same_content(after.tree, checked.tree)
                and after.inodes.isdisjoint(checked.inodes)
            )
            _need(_inode(os.fstat(destination_fd)) == destination_identity)
            _need(_inode(os.fstat(source_fd)) == _inode(source_info))
            _need(
                (_mount(source_fd), _mount(destination_fd))
                == (source_mount, destination_mount)
            )
            return ColdCopyResult(before.tree, after.tree, checked.tree)
        finally:
            os.close(destination)
    finally:
        os.close(source)
