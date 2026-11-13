"""Verify the owner-host attempt journal with actual file publication."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

import pytest

from app.domain.source_primitives import sha
from scripts import native_owner_grant_journal_0056_v1 as subject

_PINS = {
    "target_sha256": "1" * 64,
    "plan_sha256": "2" * 64,
    "source_archive_sha256": "3" * 64,
}


@pytest.fixture
def private_journal(tmp_path, monkeypatch):
    # Exercise the actual handle, no-clobber publication and hashes without
    # imposing platform-dependent ACLs on the runner's test-temp directory.
    monkeypatch.setattr(subject, "require_private_evidence_base_v2", lambda _: tmp_path)
    monkeypatch.setattr(subject, "_require_private_file", lambda _: None)
    return tmp_path


def _filename(directory, suffix):
    return directory / f"ctcc-db0056-owner-{_PINS['target_sha256']}.{suffix}.json"


def test_inspection_does_not_start_missing_attempt(private_journal):
    assert subject.load_existing(private_journal, **_PINS) is None
    assert list(private_journal.iterdir()) == []


def test_interruption_leaves_exact_started_marker_and_no_fresh_capability(
    private_journal,
):
    first = subject.load_or_begin(private_journal, **_PINS)
    assert first.created and first.status == "STARTED"
    started = _filename(private_journal, "started")
    assert first.started_sha256 == sha(started.read_bytes())
    after = subject.load_or_begin(private_journal, **_PINS)
    assert not after.created and after.status == "STARTED"
    assert after.started_sha256 == first.started_sha256
    with pytest.raises(subject.OwnerGrantJournal0056Error):
        after.record_terminal("OBSERVED_GRANTED")
    assert not _filename(private_journal, "terminal").exists()


@pytest.mark.parametrize(
    "outcome",
    ["REFUSED", "COMMIT_UNKNOWN", "POSTCOMMIT_INCOMPLETE", "OBSERVED_GRANTED"],
)
def test_terminal_is_exact_no_clobber_and_never_reopens_start(private_journal, outcome):
    fresh = subject.load_or_begin(private_journal, **_PINS)
    started = _filename(private_journal, "started").read_bytes()
    digest = fresh.record_terminal(outcome)
    terminal = _filename(private_journal, "terminal")
    raw = terminal.read_bytes()
    assert digest == sha(raw)
    with pytest.raises(subject.OwnerGrantJournal0056Error):
        fresh.record_terminal(outcome)
    assert terminal.read_bytes() == raw
    assert _filename(private_journal, "started").read_bytes() == started
    old = subject.load_or_begin(private_journal, **_PINS)
    assert not old.created and old.status == outcome and old.terminal_sha256 == digest


@pytest.mark.parametrize("pin", ["plan_sha256", "source_archive_sha256"])
def test_new_plan_or_source_cannot_reset_target(private_journal, pin):
    subject.load_or_begin(private_journal, **_PINS)
    expected = _filename(private_journal, "started").read_bytes()
    with pytest.raises(subject.OwnerGrantJournal0056Error):
        subject.load_or_begin(private_journal, **{**_PINS, pin: "9" * 64})
    assert _filename(private_journal, "started").read_bytes() == expected


@pytest.mark.parametrize("status", ["started", "terminal", "reconciliation"])
def test_corrupt_record_remains_fail_closed(private_journal, status):
    if status != "started":
        subject.load_or_begin(private_journal, **_PINS)
    target = _filename(private_journal, status)
    target.write_bytes(b'{"status":')
    with pytest.raises(subject.OwnerGrantJournal0056Error):
        subject.load_or_begin(private_journal, **_PINS)
    assert target.read_bytes() == b'{"status":'


def test_interrupted_publish_detects_leftover_partial(private_journal):
    target = _filename(private_journal, "started")
    incomplete = target.with_name(f".{target.name}.ab12.partial")
    incomplete.write_bytes(b"unfinished write")
    with pytest.raises(subject.OwnerGrantJournal0056Error):
        subject.load_or_begin(private_journal, **_PINS)
    assert incomplete.read_bytes() == b"unfinished write"
    assert not target.exists()


def test_binding_refuses_replaced_private_root_identity(private_journal):
    fresh = subject.load_or_begin(private_journal, **_PINS)
    with (
        patch.object(subject, "_root_identity", return_value=(0, 0)),
        pytest.raises(subject.OwnerGrantJournal0056Error),
    ):
        fresh.record_terminal("OBSERVED_GRANTED")
    assert not _filename(private_journal, "terminal").exists()


def test_reconciliation_separately_retains_and_does_not_rewrite_uncertainty(
    private_journal,
):
    fresh = subject.load_or_begin(private_journal, **_PINS)
    fresh.record_terminal("COMMIT_UNKNOWN")
    original = _filename(private_journal, "terminal").read_bytes()
    with pytest.raises(subject.OwnerGrantJournal0056Error):
        fresh.record_reconciliation("EXACT_GRANTED_ACL_OBSERVED")
    reopened = subject.load_existing(private_journal, **_PINS)
    digest = reopened.record_reconciliation("EXACT_GRANTED_ACL_OBSERVED")
    assert reopened.record_reconciliation("EXACT_GRANTED_ACL_OBSERVED") == digest
    with pytest.raises(subject.OwnerGrantJournal0056Error):
        reopened.record_reconciliation("EXACT_PREGRANT_OBSERVED")
    final = subject.load_or_begin(private_journal, **_PINS)
    assert not final.created and final.status == "COMMIT_UNKNOWN"
    assert final.reconciliation_status == "EXACT_GRANTED_ACL_OBSERVED"
    assert final.reconciliation_sha256 == digest
    assert _filename(private_journal, "terminal").read_bytes() == original


def test_reconcile_can_record_commit_observed_after_process_interrupt(
    private_journal,
):
    subject.load_or_begin(private_journal, **_PINS)
    reopened = subject.load_existing(private_journal, **_PINS)
    reopened.record_reconciliation("EXACT_GRANTED_ACL_OBSERVED")
    final = subject.load_existing(private_journal, **_PINS)
    assert final.status == "STARTED" and final.terminal_sha256 is None
    assert final.reconciliation_status == "EXACT_GRANTED_ACL_OBSERVED"


def test_conflicting_pregrant_observation_does_not_alter_positive_terminal(
    private_journal,
):
    fresh = subject.load_or_begin(private_journal, **_PINS)
    fresh.record_terminal("OBSERVED_GRANTED")
    reopened = subject.load_existing(private_journal, **_PINS)
    with pytest.raises(subject.OwnerGrantJournal0056Error):
        reopened.record_reconciliation("EXACT_PREGRANT_OBSERVED")
    assert not _filename(private_journal, "reconciliation").exists()


def test_contending_threads_publish_at_most_one_start(private_journal):
    barrier = Barrier(2)

    def start():
        barrier.wait(timeout=3)
        try:
            return subject.load_or_begin(private_journal, **_PINS).created
        except subject.OwnerGrantJournal0056Error:
            # The other process may currently hold the private root.
            return False

    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(lambda _: start(), range(2))) == [False, True]
    assert not subject.load_existing(private_journal, **_PINS).created
