from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta

import pytest

from app.trade_qualification import account_bill_archive_acquisition as acquisition

NOW = datetime(2026, 10, 6, 1, tzinfo=UTC)


def _plan(**changes):
    fields = {
        "expected_uid": "1234567",
        "expected_main_uid": "1234567",
        "session_binding_id": "private-session-1",
        "registration_region": "us_au",
        "origin": "https://us.okx.com",
        "registration_evidence_sha256": "a" * 64,
        "year": 2026,
        "quarter": 3,
        "created_at": NOW,
        "reviewed_download_hosts": (),
    }
    fields.update(changes)
    return acquisition.DiagnosticArchivePlan(**fields)


def _raw(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _ms(value):
    return str(int(value.timestamp() * 1000))


def _apply(result, first):
    return _raw(
        {"code": "0", "data": [{"result": result, "ts": _ms(first)}], "msg": ""}
    )


def _status(state, first, href=None):
    row = {"state": state, "ts": _ms(first)}
    if href is not None:
        row["fileHref"] = href
    return _raw({"code": "0", "data": [row], "msg": ""})


def test_exact_scope_and_current_quarter_are_fail_closed():
    plan = _plan()
    assert plan.environment == "demo"
    assert plan.bill_types == "all"
    assert (
        plan.apply_scope_sha256
        == _plan(session_binding_id="other-session").apply_scope_sha256
    )
    assert plan.plan_sha256 != _plan(session_binding_id="other-session").plan_sha256
    with pytest.raises(
        acquisition.ArchiveAcquisitionError, match="archive_current_quarter_forbidden"
    ):
        _plan(quarter=4)
    with pytest.raises(
        acquisition.ArchiveAcquisitionError, match="archive_plan_binding_invalid"
    ):
        _plan(origin="https://openapi.okx.com")
    with pytest.raises(
        acquisition.ArchiveAcquisitionError, match="archive_plan_binding_invalid"
    ):
        _plan(bill_types="2")


def test_one_apply_tombstone_serializes_and_replays_without_storage_or_network():
    plan = _plan()
    journal = acquisition.DiagnosticArchiveJournal(plan).claim_one_apply(
        recorded_at=NOW
    )
    assert journal.state == "apply_uncertain"
    assert journal.admission == "DENY"
    assert journal.account_complete is False
    assert journal.execution_authority is False
    raw = acquisition.encode_journal(journal)
    digest = hashlib.sha256(raw).hexdigest()
    assert (
        acquisition.replay_journal(raw, expected_sha256=digest).state
        == "apply_uncertain"
    )
    with pytest.raises(
        acquisition.ArchiveAcquisitionError, match="archive_apply_already_claimed"
    ):
        journal.claim_one_apply(recorded_at=NOW)


def test_false_apply_waits_two_hours_and_status_never_grants_completeness():
    claimed = acquisition.DiagnosticArchiveJournal(
        _plan(reviewed_download_hosts=("archive.example",))
    ).claim_one_apply(recorded_at=NOW)
    sent = NOW + timedelta(seconds=1)
    completed = sent + timedelta(seconds=1)
    journal = claimed.record_apply_response(
        _apply("false", sent),
        request_started_at=sent,
        body_completed_at=completed,
    )
    assert journal.state == "generating"
    with pytest.raises(
        acquisition.ArchiveAcquisitionError, match="archive_status_too_early"
    ):
        journal.record_status_response(
            _status("ongoing", sent),
            request_started_at=completed + timedelta(hours=1),
            body_completed_at=completed + timedelta(hours=1, seconds=1),
        )
    href = "https://archive.example/private/file.zip?token=do-not-persist"
    later = completed + timedelta(hours=2)
    ready = journal.record_status_response(
        _status("finished", sent, href),
        request_started_at=later,
        body_completed_at=later + timedelta(seconds=1),
    )
    raw = acquisition.encode_journal(ready)
    assert ready.state == "link_observed_unverified"
    assert ready.account_complete is False
    assert ready.execution_authority is False
    assert href.encode() not in raw
    assert (
        acquisition.replay_journal(
            raw, expected_sha256=hashlib.sha256(raw).hexdigest()
        ).state
        == ready.state
    )


def test_ambiguous_claim_never_retries_and_can_only_query_after_delay():
    claimed = acquisition.DiagnosticArchiveJournal(_plan()).claim_one_apply(
        recorded_at=NOW
    )
    with pytest.raises(
        acquisition.ArchiveAcquisitionError, match="archive_status_too_early"
    ):
        claimed.record_status_response(
            _status("ongoing", NOW),
            request_started_at=NOW + timedelta(minutes=2),
            body_completed_at=NOW + timedelta(minutes=2, seconds=1),
        )
    later = NOW + timedelta(hours=2)
    pending = claimed.record_status_response(
        _status("ongoing", NOW),
        request_started_at=later,
        body_completed_at=later + timedelta(seconds=1),
    )
    assert pending.state == "generating"
    with pytest.raises(
        acquisition.ArchiveAcquisitionError, match="archive_apply_already_claimed"
    ):
        pending.claim_one_apply(recorded_at=later + timedelta(minutes=1))


def test_status_failure_is_terminal_and_unknown_download_host_rejected():
    start = acquisition.DiagnosticArchiveJournal(_plan())
    failed = start.record_status_response(
        _status("failed", NOW - timedelta(hours=1)),
        request_started_at=NOW,
        body_completed_at=NOW + timedelta(seconds=1),
    )
    assert failed.state == "generation_failed"
    with pytest.raises(
        acquisition.ArchiveAcquisitionError, match="archive_status_terminal"
    ):
        failed.record_status_response(
            _status("finished", NOW, "https://archive.example/a.zip"),
            request_started_at=NOW + timedelta(seconds=2),
            body_completed_at=NOW + timedelta(seconds=3),
        )
    with pytest.raises(
        acquisition.ArchiveAcquisitionError, match="archive_status_response_rejected"
    ):
        start.record_status_response(
            _status("finished", NOW, "https://unreviewed.example/a.zip"),
            request_started_at=NOW,
            body_completed_at=NOW + timedelta(seconds=1),
        )


def test_canonical_replay_rejects_tampering_flags_duplicate_keys_and_changed_pin():
    journal = acquisition.DiagnosticArchiveJournal(_plan())
    raw = acquisition.encode_journal(journal)
    pin = hashlib.sha256(raw).hexdigest()
    assert (
        acquisition.replay_journal(raw, expected_sha256=pin).state == "query_unobserved"
    )
    with pytest.raises(
        acquisition.ArchiveAcquisitionError, match="archive_journal_readback_mismatch"
    ):
        acquisition.replay_journal(raw + b" ", expected_sha256=pin)
    changed = json.loads(raw)
    changed["execution_authority"] = True
    forged = _raw(changed)
    with pytest.raises(
        acquisition.ArchiveAcquisitionError, match="archive_journal_invalid"
    ):
        acquisition.replay_journal(
            forged, expected_sha256=hashlib.sha256(forged).hexdigest()
        )
    duplicate = raw[:-1] + b',"state":"query_unobserved"}'
    with pytest.raises(
        acquisition.ArchiveAcquisitionError, match="archive_journal_invalid"
    ):
        acquisition.replay_journal(
            duplicate, expected_sha256=hashlib.sha256(duplicate).hexdigest()
        )
