from __future__ import annotations

import hashlib
import io
import json
import zipfile
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


def _quarter_zip(*, row_time_ms="1782864000000"):
    csv = (
        "billId,ccy,type,subType,ts,balChg,source_extra\n"
        f"9002,USDT,2,1,{row_time_ms},0.01,private-row\n"
        f"9001,USDT,2,1,{row_time_ms},-0.01,private-row\n"
    )
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("bills.csv", csv.encode())
    return output.getvalue()


def _finished_link():
    href = "https://archive.example/private/file.zip?token=private-link-token"
    journal = acquisition.DiagnosticArchiveJournal(
        _plan(reviewed_download_hosts=("archive.example",))
    ).record_status_response(
        _status("finished", NOW - timedelta(hours=1), href),
        request_started_at=NOW,
        body_completed_at=NOW + timedelta(seconds=1),
    )
    return journal, href


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


def test_supplied_zip_is_bound_to_status_link_but_never_grants_account_authority():
    linked, href = _finished_link()
    zip_bytes = _quarter_zip()
    downloaded = linked.record_supplied_download(
        zip_bytes,
        download_href=href,
        request_started_at=NOW + timedelta(seconds=2),
        headers_received_at=NOW + timedelta(seconds=3),
        body_completed_at=NOW + timedelta(seconds=4),
        response_status=200,
        redirect_count=0,
    )
    raw = acquisition.encode_journal(downloaded)
    assert downloaded.state == "archive_bytes_unverified"
    assert downloaded.admission == "DENY"
    assert downloaded.account_complete is False
    assert downloaded.execution_authority is False
    assert (
        downloaded.events[-1]["data"]["archive_sha256"]
        == hashlib.sha256(zip_bytes).hexdigest()
    )
    assert downloaded.events[-1]["data"]["row_count"] == 2
    assert href.encode() not in raw
    assert b"private-row" not in raw
    assert (
        acquisition.replay_journal(
            raw, expected_sha256=hashlib.sha256(raw).hexdigest()
        ).state
        == "archive_bytes_unverified"
    )


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"download_href": "https://archive.example/other.zip"}, "link_mismatch"),
        ({"response_status": 206}, "response_invalid"),
        ({"redirect_count": 1}, "response_invalid"),
        ({"headers_received_at": NOW + timedelta(seconds=5)}, "clock_invalid"),
        ({"request_started_at": NOW + timedelta(hours=6)}, "clock_invalid"),
    ],
)
def test_supplied_download_rejects_mismatched_link_redirect_status_or_clock(
    changes, reason
):
    linked, href = _finished_link()
    args = {
        "download_href": href,
        "request_started_at": NOW + timedelta(seconds=2),
        "headers_received_at": NOW + timedelta(seconds=3),
        "body_completed_at": NOW + timedelta(seconds=4),
        "response_status": 200,
        "redirect_count": 0,
    }
    args.update(changes)
    with pytest.raises(acquisition.ArchiveAcquisitionError, match=reason):
        linked.record_supplied_download(_quarter_zip(), **args)
    assert linked.state == "link_observed_unverified"


def test_supplied_download_rejects_malformed_or_wrong_quarter_bytes():
    linked, href = _finished_link()
    args = {
        "download_href": href,
        "request_started_at": NOW + timedelta(seconds=2),
        "headers_received_at": NOW + timedelta(seconds=3),
        "body_completed_at": NOW + timedelta(seconds=4),
        "response_status": 200,
        "redirect_count": 0,
    }
    with pytest.raises(
        acquisition.ArchiveAcquisitionError, match="archive_download_bytes_rejected"
    ):
        linked.record_supplied_download(b"not-a-zip", **args)
    prior_quarter_ms = str(int(datetime(2026, 4, 1, tzinfo=UTC).timestamp() * 1000))
    with pytest.raises(
        acquisition.ArchiveAcquisitionError, match="archive_download_bytes_rejected"
    ):
        linked.record_supplied_download(
            _quarter_zip(row_time_ms=prior_quarter_ms), **args
        )
    assert linked.state == "link_observed_unverified"


def test_replay_rejects_forged_download_link_even_when_event_hash_is_recomputed():
    linked, href = _finished_link()
    downloaded = linked.record_supplied_download(
        _quarter_zip(),
        download_href=href,
        request_started_at=NOW + timedelta(seconds=2),
        headers_received_at=NOW + timedelta(seconds=3),
        body_completed_at=NOW + timedelta(seconds=4),
        response_status=200,
        redirect_count=0,
    )
    doc = json.loads(acquisition.encode_journal(downloaded))
    event = doc["events"][-1]
    event["data"]["download_href_sha256"] = "b" * 64
    core = {key: value for key, value in event.items() if key != "event_sha256"}
    event["event_sha256"] = hashlib.sha256(_raw(core)).hexdigest()
    forged = _raw(doc)
    with pytest.raises(
        acquisition.ArchiveAcquisitionError, match="archive_download_link_mismatch"
    ):
        acquisition.replay_journal(
            forged, expected_sha256=hashlib.sha256(forged).hexdigest()
        )
