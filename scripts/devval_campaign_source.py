"""Read-only exact-source admission for a prospective D/V campaign.

The caller must supply a separately pinned Git archive and its exact extracted
source root. This module does not read runtime configuration or contact a
provider, database, Git remote, or subprocess. It grants no Gate 3 authority.
"""

from __future__ import annotations

import hashlib
import io
import os
import re
import stat
import tarfile
from pathlib import Path, PurePosixPath

from scripts import manifest
from scripts.current_hermetic_contract_dispatch_v1 import capture_hermetic_archive
from scripts.verify_current0054_hermetic import archive_files, archive_tree

_HEX40 = re.compile(r"[0-9a-f]{40}\Z")
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_MAX_ARCHIVE = 64 * 1024 * 1024
_MAX_FILES = 10_000
_MAX_MANIFEST = 256 * 1024
_MAX_TOTAL = 64 * 1024 * 1024
_REPARSE_POINT = 0x400


class DevvalSourceCheckpointError(ValueError):
    """Fixed refusal without source paths or file contents."""


def _safe_path(path: Path, *, directory: bool) -> bool:
    if type(path) is not type(Path()) or not path.is_absolute():
        return False
    try:
        value = path.resolve(strict=True)
        metadata = path.lstat()
    except OSError:
        return False
    return (
        value == path
        and not path.is_symlink()
        and not getattr(path, "is_junction", lambda: False)()
        and not getattr(metadata, "st_file_attributes", 0) & _REPARSE_POINT
        and (
            stat.S_ISDIR(metadata.st_mode)
            if directory
            else stat.S_ISREG(metadata.st_mode)
        )
    )


def _archived_inventory(
    archive: tarfile.TarFile,
) -> tuple[dict, dict[str, int], set[str], str]:
    tree = archive_tree(archive)
    files = archive_files(archive)
    if not 0 < len(files) <= _MAX_FILES or "MANIFEST.sha256" not in files:
        raise DevvalSourceCheckpointError("devval_source_checkpoint_invalid")
    sizes = {}
    directories = set()
    total = 0
    for member in archive.getmembers():
        name = member.name
        if (
            type(name) is not str
            or not name
            or "\\" in name
            or PurePosixPath(name).as_posix() != name
        ):
            raise DevvalSourceCheckpointError("devval_source_checkpoint_invalid")
        if member.isfile():
            if name in sizes or member.size < 0:
                raise DevvalSourceCheckpointError("devval_source_checkpoint_invalid")
            sizes[name] = member.size
            total += member.size
            directories.update(
                PurePosixPath(*PurePosixPath(name).parts[:index]).as_posix()
                for index in range(1, len(PurePosixPath(name).parts))
            )
    if total > _MAX_TOTAL or set(sizes) != set(files):
        raise DevvalSourceCheckpointError("devval_source_checkpoint_invalid")
    return files, sizes, directories, tree


def _root_inventory(
    root: Path, files: dict, sizes: dict[str, int], directories: set[str]
) -> None:
    seen_files: set[str] = set()
    seen_directories: set[str] = set()
    pending = [root]
    entries = 0
    while pending:
        parent = pending.pop()
        with os.scandir(parent) as listed:
            for entry in listed:
                entries += 1
                if entries > _MAX_FILES + len(directories):
                    raise DevvalSourceCheckpointError(
                        "devval_source_checkpoint_invalid"
                    )
                relative = Path(entry.path).relative_to(root).as_posix()
                # Windows DirEntry.stat may omit file IDs needed for readback.
                metadata = os.lstat(entry.path)
                if (
                    entry.is_symlink()
                    or getattr(entry, "is_junction", lambda: False)()
                    or getattr(metadata, "st_file_attributes", 0) & _REPARSE_POINT
                ):
                    raise DevvalSourceCheckpointError(
                        "devval_source_checkpoint_invalid"
                    )
                if stat.S_ISDIR(metadata.st_mode):
                    if relative not in directories or relative in seen_directories:
                        raise DevvalSourceCheckpointError(
                            "devval_source_checkpoint_invalid"
                        )
                    seen_directories.add(relative)
                    pending.append(Path(entry.path))
                    continue
                expected = files.get(relative)
                if (
                    not stat.S_ISREG(metadata.st_mode)
                    or expected is None
                    or relative in seen_files
                    or metadata.st_size != sizes[relative]
                    or os.name == "posix"
                    and bool(metadata.st_mode & 0o111) != expected["executable"]
                ):
                    raise DevvalSourceCheckpointError(
                        "devval_source_checkpoint_invalid"
                    )
                seen_files.add(relative)
                digest = hashlib.sha256()
                with open(entry.path, "rb") as source:
                    before = os.fstat(source.fileno())
                    if (before.st_dev, before.st_ino, before.st_size) != (
                        metadata.st_dev,
                        metadata.st_ino,
                        metadata.st_size,
                    ):
                        raise DevvalSourceCheckpointError(
                            "devval_source_checkpoint_invalid"
                        )
                    remaining = sizes[relative]
                    while remaining:
                        chunk = source.read(min(1024 * 1024, remaining))
                        if not chunk:
                            raise DevvalSourceCheckpointError(
                                "devval_source_checkpoint_invalid"
                            )
                        digest.update(chunk)
                        remaining -= len(chunk)
                    if source.read(1):
                        raise DevvalSourceCheckpointError(
                            "devval_source_checkpoint_invalid"
                        )
                    after = os.fstat(source.fileno())
                    if (
                        after.st_dev,
                        after.st_ino,
                        after.st_size,
                        after.st_mtime_ns,
                    ) != (
                        before.st_dev,
                        before.st_ino,
                        before.st_size,
                        before.st_mtime_ns,
                    ):
                        raise DevvalSourceCheckpointError(
                            "devval_source_checkpoint_invalid"
                        )
                if digest.hexdigest() != expected["sha256"]:
                    raise DevvalSourceCheckpointError(
                        "devval_source_checkpoint_invalid"
                    )
    if seen_files != set(files) or seen_directories != directories:
        raise DevvalSourceCheckpointError("devval_source_checkpoint_invalid")


def verify_source_checkpoint(
    root: Path, archive: Path, expected_sha256: str
) -> dict[str, str]:
    """Return the archive-bound Git comment/tree and exact manifest digest.

    The archive SHA must itself come from a reviewed independent checkpoint.
    A passing source copy is not a pre-window witness or first-access proof.
    """
    try:
        if (
            not _safe_path(root, directory=True)
            or not _safe_path(archive, directory=False)
            or type(expected_sha256) is not str
            or _HEX64.fullmatch(expected_sha256) is None
            or not 0 < archive.stat().st_size <= _MAX_ARCHIVE
        ):
            raise DevvalSourceCheckpointError("devval_source_checkpoint_invalid")
        raw = capture_hermetic_archive(archive, expected_sha256)
        if not 0 < len(raw) <= _MAX_ARCHIVE:
            raise DevvalSourceCheckpointError("devval_source_checkpoint_invalid")
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as source:
            commit = source.pax_headers.get("comment")
            if type(commit) is not str or _HEX40.fullmatch(commit) is None:
                raise DevvalSourceCheckpointError("devval_source_checkpoint_invalid")
            files, sizes, directories, tree = _archived_inventory(source)
        _root_inventory(root, files, sizes, directories)
        manifest_path = root / "MANIFEST.sha256"
        if not 0 < manifest_path.stat().st_size <= _MAX_MANIFEST:
            raise DevvalSourceCheckpointError("devval_source_checkpoint_invalid")
        declared = manifest.read_manifest(manifest_path)
        if set(declared) != set(files) - {
            "MANIFEST.sha256"
        } or declared != manifest.build_manifest(root):
            raise DevvalSourceCheckpointError("devval_source_checkpoint_invalid")
        _root_inventory(root, files, sizes, directories)
        return {
            "expected_source_commit": commit,
            "expected_source_tree": tree,
            "expected_source_manifest_sha256": files["MANIFEST.sha256"]["sha256"],
        }
    except DevvalSourceCheckpointError:
        raise
    except Exception:  # noqa: BLE001 - Do not reveal source paths or raw archive data.
        raise DevvalSourceCheckpointError("devval_source_checkpoint_invalid") from None
