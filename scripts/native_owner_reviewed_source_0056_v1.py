"""Bind a reviewed owner archive to the exact source executing a diagnostic."""

import io
import os
import stat
import tarfile
from pathlib import Path

from app.domain.source_primitives import sha
from scripts import manifest
from scripts.current_hermetic_contract_dispatch_v1 import capture_hermetic_archive
from scripts.native_owner_upgrade_0056_v1 import (
    OwnerUpgradePinsV1,
    _verify_reviewed_source_archive,
)
from scripts.verify_final_hermetic import archive_files


class NativeOwnerReviewedSource0056Error(ValueError):
    """Fixed refusal without source or deployment path details."""


def _installed_file_pin(entry: os.stat_result) -> tuple[int, ...]:
    return (
        entry.st_dev,
        entry.st_ino,
        entry.st_mode,
        entry.st_size,
        entry.st_mtime_ns,
        getattr(entry, "st_file_attributes", 0),
    )


def require_exact_installed_owner_source_0056_v1(
    source: Path,
    pins: OwnerUpgradePinsV1,
    source_archive_file: Path,
) -> None:
    """Check a reviewed archive and every installed source byte it declares."""
    if type(pins) is not OwnerUpgradePinsV1:
        raise NativeOwnerReviewedSource0056Error(
            "db0056_owner_exact_installed_source_refused"
        )
    require_exact_installed_source_0056_v1(
        source,
        source_archive_file,
        source_commit=pins.source_commit,
        source_tree=pins.source_tree,
        source_archive_sha256=pins.source_archive_sha256,
        source_manifest_sha256=pins.route.source_manifest_sha256,
    )


def require_exact_installed_source_0056_v1(
    source: Path,
    source_archive_file: Path,
    *,
    source_commit: str,
    source_tree: str,
    source_archive_sha256: str,
    source_manifest_sha256: str,
) -> None:
    """Verify the release bytes without deployment secrets or owner identity."""
    try:
        if source.resolve(strict=True) != source:
            raise ValueError
        _verify_reviewed_source_archive(
            source_archive_file,
            source_commit=source_commit,
            source_tree=source_tree,
            source_archive_sha256=source_archive_sha256,
            source_manifest_sha256=source_manifest_sha256,
        )
        raw = capture_hermetic_archive(source_archive_file, source_archive_sha256)
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
            files = archive_files(archive)
            member_sizes = {
                item.name: item.size for item in archive.getmembers() if item.isfile()
            }
        installed = {
            path.relative_to(source).as_posix()
            for path in manifest.source_files(source)
        }
        if installed != files.keys() - {"MANIFEST.sha256"}:
            raise ValueError
        for name, expected in files.items():
            path = source / name
            mode = path.lstat()
            size = member_sizes[name]
            if (
                not stat.S_ISREG(mode.st_mode)
                or getattr(mode, "st_file_attributes", 0) & 1024
                or mode.st_size != size
                or os.name == "posix"
                and bool(mode.st_mode & 0o111) != expected["executable"]
            ):
                raise ValueError
            pin = _installed_file_pin(mode)
            with path.open("rb") as opened:
                before_read = os.fstat(opened.fileno())
                if _installed_file_pin(before_read) != pin:
                    raise ValueError
                value = opened.read(size + 1)
                after_read = os.fstat(opened.fileno())
                # Windows path and descriptor stats can report distinct
                # creation-time precision. Check stability within each view.
                if (
                    _installed_file_pin(after_read) != pin
                    or after_read.st_ctime_ns != before_read.st_ctime_ns
                ):
                    raise ValueError
            path_after = path.lstat()
            if (
                _installed_file_pin(path_after) != pin
                or path_after.st_ctime_ns != mode.st_ctime_ns
                or len(value) != size
                or sha(value) != expected["sha256"]
            ):
                raise ValueError
    except Exception:  # noqa: BLE001 -- preserve a fixed operator-facing denial
        raise NativeOwnerReviewedSource0056Error(
            "db0056_owner_exact_installed_source_refused"
        ) from None
