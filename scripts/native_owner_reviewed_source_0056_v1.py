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
    _verify_reviewed_archive,
)
from scripts.verify_final_hermetic import archive_files


class NativeOwnerReviewedSource0056Error(ValueError):
    """Fixed refusal without source or deployment path details."""


def require_exact_installed_owner_source_0056_v1(
    source: Path,
    pins: OwnerUpgradePinsV1,
    source_archive_file: Path,
) -> None:
    """Check a reviewed archive and every installed source byte it declares."""
    try:
        if (
            type(pins) is not OwnerUpgradePinsV1
            or source.resolve(strict=True) != source
        ):
            raise ValueError
        _verify_reviewed_archive(pins, source_archive_file)
        raw = capture_hermetic_archive(source_archive_file, pins.source_archive_sha256)
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
            files = archive_files(archive)
        installed = {
            path.relative_to(source).as_posix()
            for path in manifest.source_files(source)
        }
        if installed != files.keys() - {"MANIFEST.sha256"}:
            raise ValueError
        for name, expected in files.items():
            path = source / name
            mode = path.lstat()
            if (
                not stat.S_ISREG(mode.st_mode)
                or getattr(mode, "st_file_attributes", 0) & 1024
                or mode.st_size > 1_048_576
                or os.name == "posix"
                and bool(mode.st_mode & 0o111) != expected["executable"]
            ):
                raise ValueError
            with path.open("rb") as opened:
                value = opened.read(1_048_577)
            if len(value) != mode.st_size or sha(value) != expected["sha256"]:
                raise ValueError
    except Exception:  # noqa: BLE001 -- preserve a fixed operator-facing denial
        raise NativeOwnerReviewedSource0056Error(
            "db0056_owner_exact_installed_source_refused"
        ) from None
