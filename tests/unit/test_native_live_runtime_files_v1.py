"""Synthetic private-file contracts. No credential/configuration is installed."""

import os

import pytest

from app.okx_live import native_runtime_v1 as subject


def _declared_directory_seam(monkeypatch):
    # Temporary test roots are NOT claimed as validated deployment ACLs.
    monkeypatch.setattr(subject, "require_private_evidence_base_v2", lambda path: path)
    monkeypatch.setattr(subject, "_windows_private", lambda path: None)


def test_runtime_secret_file_hardlink_is_refused_before_reading(monkeypatch, tmp_path):
    _declared_directory_seam(monkeypatch)
    private = tmp_path / "private"
    private.mkdir()
    outside = tmp_path / "outside-synthetic"
    outside.write_bytes(b"synthetic-no-secret")
    inside = private / "runtime.json"
    os.link(outside, inside)
    with pytest.raises(subject.LiveRuntimeConfigurationError):
        subject._read(inside, private)


def test_runtime_file_replacement_or_change_during_read_is_refused(
    monkeypatch, tmp_path
):
    _declared_directory_seam(monkeypatch)
    path = tmp_path / "runtime.json"
    path.write_bytes(b"synthetic")
    if os.name != "nt":
        path.chmod(0o600)
    original = subject._read_bounded_regular_file_v1

    def late_change(file, maximum):
        value = original(file, maximum)
        file.write_bytes(b"changed-synthetic")
        return value

    monkeypatch.setattr(subject, "_read_bounded_regular_file_v1", late_change)
    with pytest.raises(subject.LiveRuntimeConfigurationError):
        subject._read(path, tmp_path)


def test_runtime_file_cannot_escape_explicit_private_base(monkeypatch, tmp_path):
    _declared_directory_seam(monkeypatch)
    private = tmp_path / "private"
    private.mkdir()
    path = tmp_path / "runtime.json"
    path.write_bytes(b"synthetic")
    with pytest.raises(subject.LiveRuntimeConfigurationError):
        subject._read(path, private)
