"""Exact installed-source checks with a declared local release fixture."""

import tarfile
from pathlib import Path

import pytest

from app.domain.source_primitives import sha
from scripts import manifest
from scripts import native_owner_reviewed_source_0056_v1 as subject
from scripts import native_owner_upgrade_0056_v1 as archive_owner
from scripts.verify_final_hermetic import archive_tree


@pytest.fixture
def reviewed_source(tmp_path, monkeypatch):
    installed = tmp_path.resolve() / "installed"
    installed.mkdir()
    fixture = installed / "reviewed-data.json"
    fixture.write_bytes(b'{"data":"' + b"x" * 1_048_576 + b'"}\n')
    manifest_path = installed / "MANIFEST.sha256"
    manifest.write_manifest(installed, manifest_path)
    archive_path = installed.parent / "reviewed-source.tar"
    commit = "a" * 40
    with tarfile.open(archive_path, "w", pax_headers={"comment": commit}) as archive:
        for path in (fixture, manifest_path):
            archive.add(path, arcname=path.name, recursive=False)
    with tarfile.open(archive_path, "r") as archive:
        tree = archive_tree(archive)
    monkeypatch.setattr(archive_owner, "_SOURCE", installed)
    pins = {
        "source_commit": commit,
        "source_tree": tree,
        "source_archive_sha256": sha(archive_path.read_bytes()),
        "source_manifest_sha256": sha(manifest_path.read_bytes()),
    }
    return installed, archive_path, fixture, pins


def test_reviewed_installed_member_larger_than_one_mib_is_verified(reviewed_source):
    installed, archive, fixture, pins = reviewed_source
    assert fixture.stat().st_size > 1_048_576
    subject.require_exact_installed_source_0056_v1(installed, archive, **pins)


@pytest.mark.parametrize("change", ("size", "same_size_bytes"))
def test_reviewed_large_member_drift_refuses(reviewed_source, change):
    installed, archive, fixture, pins = reviewed_source
    raw = fixture.read_bytes()
    fixture.write_bytes(raw + b" " if change == "size" else raw.replace(b"x", b"y", 1))
    with pytest.raises(
        subject.NativeOwnerReviewedSource0056Error,
        match="db0056_owner_exact_installed_source_refused",
    ):
        subject.require_exact_installed_source_0056_v1(installed, archive, **pins)


def test_equal_bytes_do_not_hide_replacement_between_stat_and_open(
    reviewed_source, monkeypatch
):
    installed, archive, fixture, pins = reviewed_source
    substitute = installed.parent / "replacement.json"
    substitute.write_bytes(fixture.read_bytes())
    original = Path.open
    reads = 0

    def open_with_replacement(path, mode="r", *args, **kwargs):
        nonlocal reads
        if path == fixture and mode == "rb":
            reads += 1
            # The source inventory performs the first read. Replace the file
            # after the verifier's lstat and before its exact read.
            if reads == 2:
                substitute.replace(fixture)
        return original(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", open_with_replacement)
    with pytest.raises(
        subject.NativeOwnerReviewedSource0056Error,
        match="db0056_owner_exact_installed_source_refused",
    ):
        subject.require_exact_installed_source_0056_v1(installed, archive, **pins)
    assert reads == 2
