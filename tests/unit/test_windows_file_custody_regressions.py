"""Synthetic bytes and reader-local stat observations; no real OOS is read."""

from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

import app.mie.validation.controlled_holdout_reader as reader
from app.mie.validation import (
    ControlledHoldoutReadError,
    Gate3SealLedger,
    SealLedgerError,
    read_prospective_holdout_once,
)
from app.trade_evidence import storage
from app.trade_evidence.storage import EvidencePublicationError
from tests.unit.mie.test_gate3_controlled_holdout_reader import _published


def _observations(path):
    actual = path.stat()
    values = {
        "st_dev": actual.st_dev,
        "st_ino": actual.st_ino,
        "st_mode": actual.st_mode,
        "st_nlink": 1,
        "st_size": actual.st_size,
        "st_mtime_ns": actual.st_mtime_ns,
        "st_ctime_ns": 100,
        "st_file_attributes": 0,
        "st_reparse_tag": 0,
        "st_birthtime_ns": 100,
    }
    assert actual.st_ino > 0
    return [SimpleNamespace(**values) for _ in range(4)]


def _scripted_path(path, before, after):
    samples = iter((before, after))

    class ObservedPath(type(path)):
        def lstat(self):
            return next(samples)

    return ObservedPath(path)


class _ReaderLocalOS:
    """The application module sees Windows samples; pytest/pathlib keep real os."""

    name = "nt"

    def __init__(self, by_path=None, by_fd=None):
        self.by_path = by_path or {}
        self.by_fd = by_fd or {}
        self.opened = []
        self.fstat_calls = 0

    def __getattr__(self, name):
        return getattr(os, name)

    def open(self, path, flags):
        original = Path(path)
        fd = os.open(path, flags)
        self.opened.append(original)
        self.by_fd[fd] = iter(self.by_path[original])
        return fd

    def fstat(self, fd):
        self.fstat_calls += 1
        return next(self.by_fd[fd])


def _pinned_read(tmp_path, monkeypatch, case=None):
    path = tmp_path / "synthetic-input.csv"
    payload = b"synthetic,point-in-time,rows\n"
    path.write_bytes(payload)
    before, opened, after_fd, after_path = _observations(path)
    opened.st_ctime_ns = after_fd.st_ctime_ns = 200
    if case == "fd_ctime_only":
        after_fd.st_ctime_ns += 1
    elif case == "path_ctime_only":
        after_path.st_ctime_ns += 1
    elif case == "birth_missing":
        del opened.st_birthtime_ns
    elif case in {"birth_zero", "birth_negative"}:
        for sample in (before, opened, after_fd, after_path):
            sample.st_birthtime_ns = 0 if case == "birth_zero" else -1
    elif case == "identity_zero":
        for sample in (before, opened, after_fd, after_path):
            sample.st_ino = 0
    elif case == "reparse_before":
        before.st_file_attributes = 0x400
    elif case == "replace_after_path":
        after_path.st_ino += 1
    elif case is not None:
        raise AssertionError("unknown synthetic case")
    proxy = _ReaderLocalOS(by_path={path: (opened, after_fd)})
    monkeypatch.setattr(reader, "os", proxy)
    observed_path = _scripted_path(path, before, after_path)
    return path, payload, proxy, observed_path


def test_holdout_reads_real_bytes_with_stable_distinct_windows_stat_channels(
    tmp_path, monkeypatch
):
    host_name = os.name
    path, payload, proxy, observed = _pinned_read(tmp_path, monkeypatch)
    result, identity = reader._read_pinned_file(
        observed, hashlib.sha256(payload).hexdigest(), 1024
    )
    assert result == payload == path.read_bytes()
    assert identity == (path.stat().st_dev, path.stat().st_ino)
    assert proxy.opened == [path]
    assert proxy.fstat_calls == 2
    assert reader.os is proxy
    assert os.name == host_name


@pytest.mark.parametrize(
    "case",
    (
        "fd_ctime_only",
        "path_ctime_only",
        "birth_missing",
        "birth_zero",
        "birth_negative",
        "identity_zero",
        "reparse_before",
        "replace_after_path",
    ),
)
def test_holdout_read_denies_custody_loss_even_when_digest_bytes_match(
    tmp_path, monkeypatch, case
):
    path, payload, _proxy, observed = _pinned_read(tmp_path, monkeypatch, case)
    with pytest.raises(ControlledHoldoutReadError):
        reader._read_pinned_file(observed, hashlib.sha256(payload).hexdigest(), 1024)
    assert path.read_bytes() == payload


def test_holdout_custody_failure_consumes_formal_slot_without_retry(
    tmp_path, monkeypatch
):
    kwargs = _published(tmp_path, monkeypatch)
    by_path = {}
    for field in ("manifest_path", "dataset_path"):
        original = kwargs[field]
        before, opened, after_fd, after_path = _observations(original)
        opened.st_ctime_ns = after_fd.st_ctime_ns = 200
        if field == "dataset_path":
            after_fd.st_ctime_ns += 1
        by_path[original] = (opened, after_fd)
        kwargs[field] = _scripted_path(original, before, after_path)
    proxy = _ReaderLocalOS(by_path=by_path)
    monkeypatch.setattr(reader, "os", proxy)
    with pytest.raises(ControlledHoldoutReadError, match="changed during read"):
        read_prospective_holdout_once(**kwargs)
    reserved = Gate3SealLedger(kwargs["ledger_path"]).read_evaluation(
        kwargs["expected_seal_sha256"]
    )
    assert reserved.receipt_sha256 == kwargs["expected_receipt_sha256"]
    assert proxy.opened == [
        Path(kwargs["manifest_path"]),
        Path(kwargs["dataset_path"]),
    ]
    opened_once = tuple(proxy.opened)
    with pytest.raises(SealLedgerError, match="already consumed"):
        read_prospective_holdout_once(**kwargs)
    assert tuple(proxy.opened) == opened_once


@pytest.mark.parametrize(
    "case", (None, "ctime_only", "not_regular", "hardlink", "reparse")
)
def test_storage_bounded_read_rechecks_descriptor_and_preserves_durable_journal(
    tmp_path, monkeypatch, case
):
    journal = tmp_path / "journal.json"
    payload = b'{"synthetic":"already durable journal"}\n'
    journal.write_bytes(payload)
    before, _opened, after, _path = _observations(journal)
    if case == "ctime_only":
        after.st_ctime_ns += 1
    elif case == "not_regular":
        after.st_mode = stat.S_IFDIR | 0o700
    elif case == "hardlink":
        after.st_nlink = 2
    elif case == "reparse":
        after.st_file_attributes = 0x400
    fd = os.open(journal, os.O_RDONLY | getattr(os, "O_BINARY", 0))
    proxy = _ReaderLocalOS(by_fd={fd: iter((before, after))})
    monkeypatch.setattr(storage, "os", proxy)
    try:
        if case is None:
            assert storage._read_fd(fd, 1024) == payload
        else:
            with pytest.raises(EvidencePublicationError):
                storage._read_fd(fd, 1024)
    finally:
        os.close(fd)
    assert proxy.fstat_calls == 2
    assert journal.read_bytes() == payload
    assert tuple(tmp_path.iterdir()) == (journal,)
