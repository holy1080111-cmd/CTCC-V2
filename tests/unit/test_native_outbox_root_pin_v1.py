"""Actual temporary filesystem/lease checks; no DB, Notion, token or order."""

import os
import subprocess

import pytest

from app.trade_evidence import native_outbox_root_pin_v1 as subject
from app.trade_evidence import outbox, storage
from app.trade_evidence.native_private_root_v2 import mkdir_private_evidence_child_v2
from tests.unit.test_native_live_lifecycle_custody_v1 import _private_root_path


def test_retained_identity_allows_independent_transactions_but_not_two_publishers(
    tmp_path,
):
    root = _private_root_path(tmp_path)
    with subject.pin_outbox_root_v1(root) as pin:
        for index in range(2):
            with outbox._transaction(root, "synthetic") as directory:
                directory.publish(f"accepted{index}.json", b"accepted synthetic data")
                pin.check()
                with (
                    pytest.raises(outbox.OutboxError),
                    outbox._transaction(root, "synthetic"),
                ):
                    pytest.fail("second publisher entered unchanged exclusive lease")
            pin.check()
    assert pin.closed is True
    with pytest.raises(ValueError):
        pin.check()
    assert (root / "accepted0.json").read_bytes() == b"accepted synthetic data"
    assert (root / "accepted1.json").read_bytes() == b"accepted synthetic data"


@pytest.mark.parametrize("ancestor", [False, True])
def test_held_root_and_ancestor_cannot_be_silently_substituted(tmp_path, ancestor):
    private = _private_root_path(tmp_path)
    root = private / "child"
    if os.name == "nt":
        with storage._windows_root(private) as directory:
            mkdir_private_evidence_child_v2(directory, "child")
    else:
        root.mkdir(mode=0o700)
    original = private if ancestor else root
    retired = tmp_path / "retired-original"
    if os.name == "nt":
        # Original read handles omit FILE_SHARE_DELETE and prevent a rename.
        with subject.pin_outbox_root_v1(root) as pin:
            with pytest.raises(OSError):
                original.rename(retired)
            pin.check()
        assert original.is_dir() and not retired.exists()
    else:
        # POSIX permits rename of an open inode; recheck must reject its changed
        # path/parent linkage, then close every retained descriptor.
        with (
            pytest.raises((OSError, ValueError)),
            subject.pin_outbox_root_v1(root) as pin,
        ):
            original.rename(retired)
            original.mkdir(mode=0o700)
            pin.check()
        assert pin.closed is True and retired.is_dir()


def test_reparse_or_symlink_root_is_rejected_without_following_it(tmp_path):
    root = _private_root_path(tmp_path)
    link = tmp_path / "unexpected-alias"
    if os.name == "nt":
        # Junction creation needs no symlink privilege and changes only this
        # pytest-owned directory. No machine policy/ACL is altered.
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "New-Item -ItemType Junction -Path $env:CTCC_TEST_ALIAS -Target $env:CTCC_TEST_TARGET -ErrorAction Stop | Out-Null",
            ],
            env={
                **os.environ,
                "CTCC_TEST_ALIAS": str(link),
                "CTCC_TEST_TARGET": str(root),
            },
            capture_output=True,
            timeout=20,
            check=False,
        )
        assert result.returncode == 0
    else:
        link.symlink_to(root, target_is_directory=True)
    with pytest.raises((OSError, ValueError)), subject.pin_outbox_root_v1(link):
        pytest.fail("source followed substituted root")
    assert root.is_dir()


def test_late_failure_releases_identity_handles_and_preserves_accepted_journal(
    tmp_path,
):
    root = _private_root_path(tmp_path)
    with (
        pytest.raises(ValueError, match="synthetic_late_failure"),
        subject.pin_outbox_root_v1(root) as pin,
    ):
        with outbox._transaction(root, "synthetic") as directory:
            directory.publish("accepted-journal.json", b"retained")
        raise ValueError("synthetic_late_failure")
    assert pin.closed is True
    assert (root / "accepted-journal.json").read_bytes() == b"retained"
    with subject.pin_outbox_root_v1(root) as replacement:
        replacement.check()


def test_nonexistent_root_and_plain_file_refuse(tmp_path):
    root = tmp_path / "missing"
    with pytest.raises((OSError, ValueError)), subject.pin_outbox_root_v1(root):
        pytest.fail("missing root admitted")
    root.write_bytes(b"not a directory")
    with pytest.raises((OSError, ValueError)), subject.pin_outbox_root_v1(root):
        pytest.fail("plain file admitted")
