"""Real local private-file boundaries; no Notion HTTP, worker, DB or token."""

import os

import pytest

from app.trade_evidence import notion_binding as binding
from app.trade_evidence import storage
from scripts import setup_notion_outbox as setup

RAW = b'{"declared_synthetic_setup_record":true}'


def test_setup_record_is_private_and_read_back_without_clobber(tmp_path):
    path = tmp_path / "destination.json"
    setup.write_local_record(path, RAW)
    with binding.private_file(path) as fd:
        assert storage._read_fd(fd, 1024) == RAW
    with pytest.raises(FileExistsError):
        setup.write_local_record(path, b"replacement")
    assert path.read_bytes() == RAW


def test_inherited_or_permissive_existing_record_is_refused_and_preserved(tmp_path):
    path = tmp_path / "existing-destination.json"
    path.write_bytes(RAW)
    if os.name != "nt":
        path.chmod(0o644)  # This exact test leaf only; no system policy/ACL change.
    with (
        pytest.raises(binding.NotionBindingError, match="permissions_invalid"),
        binding.private_file(path),
    ):
        pytest.fail("inherited/permissive record accepted")
    with pytest.raises(FileExistsError):
        setup.write_local_record(path, b"replacement")
    assert path.read_bytes() == RAW


def test_late_fsync_failure_retains_new_file_and_earlier_receipt(tmp_path, monkeypatch):
    accepted = tmp_path / "accepted-setup-receipt.json"
    setup.write_local_record(accepted, RAW)
    path = tmp_path / "late-destination.json"

    def failed(_):
        raise OSError("declared late filesystem failure")

    monkeypatch.setattr(storage.os, "fsync", failed)
    with pytest.raises(OSError, match="late filesystem"):
        setup.write_local_record(path, RAW)
    assert path.read_bytes() == accepted.read_bytes() == RAW


def test_readback_mismatch_retains_written_record_without_success(
    tmp_path, monkeypatch
):
    path = tmp_path / "readback-mismatch.json"
    monkeypatch.setattr(storage, "_read_fd", lambda *_: b"declared mismatch")
    with pytest.raises(ValueError, match="local_readback_failed"):
        setup.write_local_record(path, RAW)
    assert path.read_bytes() == RAW


def test_late_private_permission_failure_preserves_durable_record(
    tmp_path, monkeypatch
):
    path = tmp_path / "late-permissions.json"
    original = binding.private_permissions
    checked = []

    def late(target, *, fd):
        original(target, fd=fd)
        checked.append(target)
        if len(checked) == 2:
            raise binding.NotionBindingError("notion_private_file_permissions_invalid")

    monkeypatch.setattr(binding, "private_permissions", late)
    with pytest.raises(binding.NotionBindingError, match="permissions_invalid"):
        setup.write_local_record(path, RAW)
    assert checked == [path, path] and path.read_bytes() == RAW
