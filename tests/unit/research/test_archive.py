from __future__ import annotations

from pathlib import Path
import zipfile

import pytest

from app.research.external_benchmarks import (
    ArchiveInspectionError,
    ArchiveInspectionPolicy,
    inspect_zip_archive,
    require_safe_zip_archive,
)


def test_safe_zip_is_profiled_without_extraction(tmp_path: Path) -> None:
    archive_path = tmp_path / "trades.zip"
    with zipfile.ZipFile(
        archive_path,
        "w",
        compression=zipfile.ZIP_DEFLATED,
    ) as archive:
        archive.writestr("BTCUSDT-trades-2026-01.csv", "1,100,2\n2,101,1\n")

    report = require_safe_zip_archive(archive_path)

    assert report.passed is True
    assert report.member_count == 1
    assert report.total_uncompressed_bytes == 16
    assert report.execution_authority is False
    assert list(tmp_path.iterdir()) == [archive_path]


def test_zip_inspection_rejects_traversal_nested_and_duplicate_members(
    tmp_path: Path,
) -> None:
    archive_path = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("../escape.csv", "unsafe")
        archive.writestr("nested.zip", "not-a-real-zip")
        archive.writestr("duplicate.csv", "first")
        with pytest.warns(UserWarning, match="Duplicate name"):
            archive.writestr("duplicate.csv", "second")

    report = inspect_zip_archive(archive_path)

    assert report.passed is False
    assert set(report.failure_codes) == {
        "archive_duplicate_members",
        "archive_nested_archives",
        "archive_unsafe_paths",
    }
    with pytest.raises(ArchiveInspectionError) as exc_info:
        require_safe_zip_archive(archive_path)
    assert exc_info.value.report == report


def test_zip_inspection_rejects_expansion_ratio_and_invalid_zip(
    tmp_path: Path,
) -> None:
    compressed = tmp_path / "compressed.zip"
    with zipfile.ZipFile(
        compressed,
        "w",
        compression=zipfile.ZIP_DEFLATED,
    ) as archive:
        archive.writestr("zeros.csv", b"0" * 20_000)

    report = inspect_zip_archive(
        compressed,
        policy=ArchiveInspectionPolicy(max_expansion_ratio="2"),
    )
    assert report.passed is False
    assert "archive_expansion_ratio_exceeded" in report.failure_codes

    invalid = tmp_path / "invalid.zip"
    invalid.write_bytes(b"not-a-zip")
    invalid_report = inspect_zip_archive(invalid)
    assert invalid_report.failure_codes == ("invalid_zip_archive",)
