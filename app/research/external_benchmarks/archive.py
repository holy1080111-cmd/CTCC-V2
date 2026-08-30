from __future__ import annotations

from decimal import Decimal, localcontext
from pathlib import Path, PurePosixPath
import stat
import unicodedata
import zipfile

from app.research.external_benchmarks.artifacts import sha256_file
from app.research.external_benchmarks.contracts import (
    ArchiveInspectionPolicy,
    ArchiveInspectionReport,
)


NESTED_ARCHIVE_SUFFIXES = (
    ".7z",
    ".bz2",
    ".gz",
    ".rar",
    ".tar",
    ".tar.bz2",
    ".tar.gz",
    ".tgz",
    ".xz",
    ".zip",
)


class ArchiveInspectionError(ValueError):
    def __init__(self, report: ArchiveInspectionReport) -> None:
        self.report = report
        super().__init__(
            "unsafe zip archive: " + ",".join(report.failure_codes)
        )


def _unsafe_member_path(name: str) -> bool:
    if (
        not name
        or "\\" in name
        or "\x00" in name
        or "//" in name
    ):
        return True
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        return True
    return bool(path.parts and ":" in path.parts[0])


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    unix_mode = (info.external_attr >> 16) & 0o170000
    return unix_mode == stat.S_IFLNK


def inspect_zip_archive(
    path: Path,
    *,
    policy: ArchiveInspectionPolicy | None = None,
) -> ArchiveInspectionReport:
    """Inspect ZIP metadata without extracting or trusting member paths."""

    inspection_policy = policy or ArchiveInspectionPolicy()
    artifact_sha256 = sha256_file(path)
    failure_codes: list[str] = []
    try:
        with zipfile.ZipFile(path) as archive:
            members = archive.infolist()
    except (OSError, zipfile.BadZipFile, zipfile.LargeZipFile):
        return ArchiveInspectionReport(
            artifact_sha256=artifact_sha256,
            member_count=0,
            total_compressed_bytes=0,
            total_uncompressed_bytes=0,
            maximum_expansion_ratio=Decimal("0"),
            duplicate_member_count=0,
            unsafe_path_count=0,
            encrypted_member_count=0,
            symlink_member_count=0,
            nested_archive_count=0,
            passed=False,
            failure_codes=("invalid_zip_archive",),
        )

    names: set[str] = set()
    duplicate_member_count = 0
    unsafe_path_count = 0
    encrypted_member_count = 0
    symlink_member_count = 0
    nested_archive_count = 0
    total_compressed_bytes = 0
    total_uncompressed_bytes = 0
    maximum_expansion_ratio = Decimal("0")
    oversized_member = False

    with localcontext() as context:
        context.prec = 50
        for member in members:
            normalized_name = unicodedata.normalize(
                "NFC",
                member.filename,
            ).casefold()
            if normalized_name in names:
                duplicate_member_count += 1
            names.add(normalized_name)
            unsafe_path_count += int(_unsafe_member_path(member.filename))
            encrypted_member_count += int(bool(member.flag_bits & 0x1))
            symlink_member_count += int(_is_symlink(member))
            lower_name = member.filename.lower()
            nested_archive_count += int(
                not member.is_dir()
                and lower_name.endswith(NESTED_ARCHIVE_SUFFIXES)
            )
            total_compressed_bytes += member.compress_size
            total_uncompressed_bytes += member.file_size
            oversized_member = (
                oversized_member
                or member.file_size > inspection_policy.max_single_member_bytes
            )
            if member.file_size == 0:
                ratio = Decimal("0")
            elif member.compress_size == 0:
                ratio = inspection_policy.max_expansion_ratio + Decimal("1")
            else:
                ratio = Decimal(member.file_size) / Decimal(member.compress_size)
            maximum_expansion_ratio = max(maximum_expansion_ratio, ratio)

    if len(members) == 0:
        failure_codes.append("archive_empty")
    if len(members) > inspection_policy.max_members:
        failure_codes.append("archive_member_limit_exceeded")
    if total_uncompressed_bytes > inspection_policy.max_total_uncompressed_bytes:
        failure_codes.append("archive_uncompressed_limit_exceeded")
    if oversized_member:
        failure_codes.append("archive_single_member_limit_exceeded")
    if maximum_expansion_ratio > inspection_policy.max_expansion_ratio:
        failure_codes.append("archive_expansion_ratio_exceeded")
    if duplicate_member_count:
        failure_codes.append("archive_duplicate_members")
    if unsafe_path_count:
        failure_codes.append("archive_unsafe_paths")
    if encrypted_member_count:
        failure_codes.append("archive_encrypted_members")
    if symlink_member_count:
        failure_codes.append("archive_symlink_members")
    if nested_archive_count and not inspection_policy.allow_nested_archives:
        failure_codes.append("archive_nested_archives")

    return ArchiveInspectionReport(
        artifact_sha256=artifact_sha256,
        member_count=len(members),
        total_compressed_bytes=total_compressed_bytes,
        total_uncompressed_bytes=total_uncompressed_bytes,
        maximum_expansion_ratio=maximum_expansion_ratio,
        duplicate_member_count=duplicate_member_count,
        unsafe_path_count=unsafe_path_count,
        encrypted_member_count=encrypted_member_count,
        symlink_member_count=symlink_member_count,
        nested_archive_count=nested_archive_count,
        passed=not failure_codes,
        failure_codes=tuple(failure_codes),
    )


def require_safe_zip_archive(
    path: Path,
    *,
    policy: ArchiveInspectionPolicy | None = None,
) -> ArchiveInspectionReport:
    report = inspect_zip_archive(path, policy=policy)
    if not report.passed:
        raise ArchiveInspectionError(report)
    return report
