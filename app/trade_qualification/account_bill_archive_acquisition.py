"""Local, diagnostic-only contract for a quarterly OKX bills archive attempt.

There is deliberately no HTTP client, signer, credential loader, scheduler, or
execution integration here. Supplied response bytes are synthetic/unowned for
admission purposes, even when their contents resemble an OKX response. An apply
claim is only a serializable intent/tombstone contract. A future separately
reviewed DB owner must persist and independently read it back before sending
the Read-permission POST. This module cannot issue a request, durably persist
the claim, or make an account-history completeness claim.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from app.trade_qualification import account_bill_archive as archive

SCHEMA = "ctcc.demo_bill_archive_diagnostic_journal.v1"
APPLY_PATH = "/api/v5/account/bills-history-archive"
MAX_JOURNAL_BYTES = 32768
MAX_EVENTS = 16
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_UID = re.compile(r"[1-9][0-9]{0,39}\Z")
_BINDING = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}\Z")
_HOST = re.compile(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?\Z")
_ORIGINS = {
    "global": "https://openapi.okx.com",
    "us_au": "https://us.okx.com",
    "eea": "https://eea.okx.com",
    "tr": "https://tr.okx.com",
}
_STATES = frozenset(
    {
        "query_unobserved",
        "apply_uncertain",
        "generating",
        "existing_link_pending",
        "link_observed_unverified",
        "generation_failed",
    }
)


class ArchiveAcquisitionError(ValueError):
    """Fixed diagnostic reason; never includes account data or remote text."""


def _fail(code: str):
    raise ArchiveAcquisitionError(code)


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _utc(value: object) -> datetime:
    if type(value) is not datetime or value.tzinfo is None:
        _fail("archive_clock_invalid")
    result = value.astimezone(UTC)
    if result != value or value.utcoffset() != timedelta(0):
        _fail("archive_clock_invalid")
    return result


def _stamp(value: object) -> str:
    return _utc(value).isoformat()


def _read_stamp(value: object) -> datetime:
    if type(value) is not str or len(value) > 40:
        _fail("archive_clock_invalid")
    try:
        result = datetime.fromisoformat(value)
    except ValueError:
        _fail("archive_clock_invalid")
    if _stamp(result) != value:
        _fail("archive_clock_invalid")
    return result


def _digest(value: object) -> str:
    if type(value) is not str or _DIGEST.fullmatch(value) is None:
        _fail("archive_digest_invalid")
    return value


@dataclass(frozen=True, slots=True, repr=False)
class DiagnosticArchivePlan:
    expected_uid: str
    expected_main_uid: str
    session_binding_id: str
    registration_region: Literal["global", "us_au", "eea", "tr"]
    origin: str
    registration_evidence_sha256: str
    year: int
    quarter: int
    created_at: datetime
    reviewed_download_hosts: tuple[str, ...] = ()
    environment: Literal["demo"] = "demo"
    bill_types: Literal["all"] = "all"

    def __post_init__(self):
        if (
            type(self.expected_uid) is not str
            or _UID.fullmatch(self.expected_uid) is None
            or type(self.expected_main_uid) is not str
            or _UID.fullmatch(self.expected_main_uid) is None
            or type(self.session_binding_id) is not str
            or _BINDING.fullmatch(self.session_binding_id) is None
            or type(self.registration_region) is not str
            or self.registration_region not in _ORIGINS
            or self.origin != _ORIGINS[self.registration_region]
            or self.environment != "demo"
            or self.bill_types != "all"
        ):
            _fail("archive_plan_binding_invalid")
        _digest(self.registration_evidence_sha256)
        if (
            type(self.reviewed_download_hosts) is not tuple
            or len(self.reviewed_download_hosts) > 4
            or any(
                type(host) is not str or _HOST.fullmatch(host) is None or ".." in host
                for host in self.reviewed_download_hosts
            )
            or len(set(self.reviewed_download_hosts))
            != len(self.reviewed_download_hosts)
        ):
            _fail("archive_download_host_pin_invalid")
        start, end = archive.quarter_bounds(self.year, self.quarter)
        created = _utc(self.created_at)
        if not start < end <= created:
            _fail("archive_current_quarter_forbidden")

    def __repr__(self):
        return "<DiagnosticArchivePlan private diagnostic-only>"

    def document(self) -> dict:
        return {
            "expected_uid": self.expected_uid,
            "expected_main_uid": self.expected_main_uid,
            "session_binding_id": self.session_binding_id,
            "registration_region": self.registration_region,
            "origin": self.origin,
            "registration_evidence_sha256": self.registration_evidence_sha256,
            "year": self.year,
            "quarter": self.quarter,
            "created_at": _stamp(self.created_at),
            "reviewed_download_hosts": list(self.reviewed_download_hosts),
            "environment": self.environment,
            "bill_types": self.bill_types,
        }

    @property
    def plan_sha256(self) -> str:
        return _sha(_canonical(self.document()))

    @property
    def apply_scope_sha256(self) -> str:
        # The session is deliberately omitted: a new key/session cannot apply
        # the same account-quarter a second time through a different file name.
        return _sha(
            _canonical(
                {
                    "environment": "demo",
                    "uid": self.expected_uid,
                    "region": self.registration_region,
                    "year": self.year,
                    "quarter": self.quarter,
                    "bill_types": "all",
                }
            )
        )


def _event(core: dict) -> dict:
    return {**core, "event_sha256": _sha(_canonical(core))}


@dataclass(frozen=True, slots=True, repr=False)
class DiagnosticArchiveJournal:
    plan: DiagnosticArchivePlan
    events: tuple[dict, ...] = ()

    def __post_init__(self):
        if (
            type(self.plan) is not DiagnosticArchivePlan
            or type(self.events) is not tuple
        ):
            _fail("archive_journal_invalid")
        _validate_events(self.plan, self.events)

    def __repr__(self):
        return "<DiagnosticArchiveJournal private DENY>"

    @property
    def state(self) -> str:
        return _validate_events(self.plan, self.events)

    @property
    def account_complete(self) -> bool:
        return False

    @property
    def execution_authority(self) -> bool:
        return False

    @property
    def admission(self) -> str:
        return "DENY"

    def _append(
        self, kind: str, *, started: datetime, completed: datetime, data: dict
    ) -> DiagnosticArchiveJournal:
        if len(self.events) >= MAX_EVENTS:
            _fail("archive_event_limit")
        start, end = _utc(started), _utc(completed)
        previous_completed = (
            self.plan.created_at
            if not self.events
            else _read_stamp(self.events[-1]["completed_at"])
        )
        if not previous_completed <= start <= end:
            _fail("archive_clock_order_invalid")
        previous_sha = (
            self.plan.plan_sha256
            if not self.events
            else self.events[-1]["event_sha256"]
        )
        item = _event(
            {
                "index": len(self.events),
                "previous_sha256": previous_sha,
                "kind": kind,
                "started_at": _stamp(start),
                "completed_at": _stamp(end),
                "data": data,
            }
        )
        return DiagnosticArchiveJournal(self.plan, (*self.events, item))

    def claim_one_apply(self, *, recorded_at: datetime) -> DiagnosticArchiveJournal:
        """Create a tombstone contract; an external DB owner must persist it."""
        if self.state != "query_unobserved" or self.events:
            _fail("archive_apply_already_claimed_or_status_known")
        body = _canonical(
            {"year": str(self.plan.year), "quarter": f"Q{self.plan.quarter}"}
        )
        return self._append(
            "apply_claimed",
            started=recorded_at,
            completed=recorded_at,
            data={
                "method": "POST",
                "path": APPLY_PATH,
                "request_body_sha256": _sha(body),
                "remote_send_performed": False,
            },
        )

    def record_apply_response(
        self,
        raw: bytes,
        *,
        request_started_at: datetime,
        body_completed_at: datetime,
    ) -> DiagnosticArchiveJournal:
        if self.state != "apply_uncertain":
            _fail("archive_apply_response_without_claim")
        try:
            receipt = archive.parse_apply_response(raw)
        except archive.BillArchiveError:
            _fail("archive_apply_response_rejected")
        started, completed = _utc(request_started_at), _utc(body_completed_at)
        if receipt.first_server_receipt_at > completed:
            _fail("archive_server_time_future")
        # ts may be the first request for an existing file, not this response.
        next_status = (
            max(completed, receipt.first_server_receipt_at) + timedelta(hours=2)
            if receipt.result == "false"
            else completed
        )
        return self._append(
            "apply_response",
            started=started,
            completed=completed,
            data={
                "result": receipt.result,
                "body_sha256": receipt.body_sha256,
                "canonical_sha256": receipt.canonical_sha256,
                "first_server_receipt_at": _stamp(receipt.first_server_receipt_at),
                "earliest_status_at": _stamp(next_status),
                "source_classification": "supplied_bytes_unowned",
            },
        )

    def record_status_response(
        self,
        raw: bytes,
        *,
        request_started_at: datetime,
        body_completed_at: datetime,
    ) -> DiagnosticArchiveJournal:
        state = self.state
        if state not in {
            "query_unobserved",
            "apply_uncertain",
            "generating",
            "existing_link_pending",
        }:
            _fail("archive_status_terminal")
        started, completed = _utc(request_started_at), _utc(body_completed_at)
        if state == "generating":
            earliest = _read_stamp(self.events[-1]["data"]["earliest_status_at"])
            if started < earliest:
                _fail("archive_status_too_early")
        elif state == "apply_uncertain":
            if started < _read_stamp(self.events[-1]["completed_at"]) + timedelta(
                hours=2
            ):
                _fail("archive_status_too_early")
        try:
            receipt = archive.parse_status_response(
                raw,
                allowed_download_hosts=self.plan.reviewed_download_hosts,
            )
        except archive.BillArchiveError:
            _fail("archive_status_response_rejected")
        if receipt.first_server_receipt_at > completed:
            _fail("archive_server_time_future")
        next_status = (
            completed + timedelta(minutes=10) if receipt.state == "ongoing" else None
        )
        return self._append(
            "status_response",
            started=started,
            completed=completed,
            data={
                "state": receipt.state,
                "body_sha256": receipt.body_sha256,
                "canonical_sha256": receipt.canonical_sha256,
                "first_server_receipt_at": _stamp(receipt.first_server_receipt_at),
                "file_href_sha256": receipt.file_href_sha256,
                "earliest_status_at": None
                if next_status is None
                else _stamp(next_status),
                "source_classification": "supplied_bytes_unowned",
            },
        )


def _validate_events(plan: DiagnosticArchivePlan, events: tuple[dict, ...]) -> str:
    if len(events) > MAX_EVENTS:
        _fail("archive_event_limit")
    state = "query_unobserved"
    previous_sha = plan.plan_sha256
    prior_completed = plan.created_at
    for index, item in enumerate(events):
        if (
            type(item) is not dict
            or set(item)
            != {
                "index",
                "previous_sha256",
                "kind",
                "started_at",
                "completed_at",
                "data",
                "event_sha256",
            }
            or type(item["index"]) is not int
            or item["index"] != index
        ):
            _fail("archive_journal_invalid")
        core = {key: value for key, value in item.items() if key != "event_sha256"}
        if item["previous_sha256"] != previous_sha or item["event_sha256"] != _sha(
            _canonical(core)
        ):
            _fail("archive_journal_hash_mismatch")
        start, end = _read_stamp(item["started_at"]), _read_stamp(item["completed_at"])
        if not prior_completed <= start <= end:
            _fail("archive_clock_order_invalid")
        kind, data = item["kind"], item["data"]
        if type(data) is not dict:
            _fail("archive_journal_invalid")
        if kind == "apply_claimed":
            body = _canonical({"year": str(plan.year), "quarter": f"Q{plan.quarter}"})
            if (
                state != "query_unobserved"
                or index != 0
                or data
                != {
                    "method": "POST",
                    "path": APPLY_PATH,
                    "request_body_sha256": _sha(body),
                    "remote_send_performed": False,
                }
            ):
                _fail("archive_apply_already_claimed_or_status_known")
            state = "apply_uncertain"
        elif kind == "apply_response":
            if (
                state != "apply_uncertain"
                or set(data)
                != {
                    "result",
                    "body_sha256",
                    "canonical_sha256",
                    "first_server_receipt_at",
                    "earliest_status_at",
                    "source_classification",
                }
                or type(data["result"]) is not str
                or data["result"] not in {"true", "false"}
                or data["source_classification"] != "supplied_bytes_unowned"
            ):
                _fail("archive_journal_invalid")
            _digest(data["body_sha256"])
            _digest(data["canonical_sha256"])
            server_at = _read_stamp(data["first_server_receipt_at"])
            earliest = _read_stamp(data["earliest_status_at"])
            if server_at > end or earliest != (
                max(end, server_at) + timedelta(hours=2)
                if data["result"] == "false"
                else end
            ):
                _fail("archive_journal_invalid")
            state = (
                "generating" if data["result"] == "false" else "existing_link_pending"
            )
        elif kind == "status_response":
            if (
                state
                not in {
                    "query_unobserved",
                    "apply_uncertain",
                    "generating",
                    "existing_link_pending",
                }
                or set(data)
                != {
                    "state",
                    "body_sha256",
                    "canonical_sha256",
                    "first_server_receipt_at",
                    "file_href_sha256",
                    "earliest_status_at",
                    "source_classification",
                }
                or type(data["state"]) is not str
                or data["state"] not in {"finished", "ongoing", "failed"}
                or data["source_classification"] != "supplied_bytes_unowned"
            ):
                _fail("archive_journal_invalid")
            if state in {"apply_uncertain", "generating"}:
                earliest = (
                    prior_completed + timedelta(hours=2)
                    if state == "apply_uncertain"
                    else _read_stamp(events[index - 1]["data"]["earliest_status_at"])
                )
                if start < earliest:
                    _fail("archive_status_too_early")
            _digest(data["body_sha256"])
            _digest(data["canonical_sha256"])
            if _read_stamp(data["first_server_receipt_at"]) > end:
                _fail("archive_server_time_future")
            if data["state"] == "finished":
                _digest(data["file_href_sha256"])
                if data["earliest_status_at"] is not None:
                    _fail("archive_journal_invalid")
                state = "link_observed_unverified"
            elif data["state"] == "failed":
                if (
                    data["file_href_sha256"] is not None
                    or data["earliest_status_at"] is not None
                ):
                    _fail("archive_journal_invalid")
                state = "generation_failed"
            else:
                if data["file_href_sha256"] is not None or _read_stamp(
                    data["earliest_status_at"]
                ) != end + timedelta(minutes=10):
                    _fail("archive_journal_invalid")
                state = "generating"
        else:
            _fail("archive_journal_invalid")
        previous_sha, prior_completed = item["event_sha256"], end
    if state not in _STATES:
        _fail("archive_journal_invalid")
    return state


def encode_journal(journal: DiagnosticArchiveJournal) -> bytes:
    if type(journal) is not DiagnosticArchiveJournal:
        _fail("archive_journal_invalid")
    document = {
        "schema_version": SCHEMA,
        "plan": journal.plan.document(),
        "plan_sha256": journal.plan.plan_sha256,
        "apply_scope_sha256": journal.plan.apply_scope_sha256,
        "events": list(journal.events),
        "state": journal.state,
        "admission": "DENY",
        "account_complete": False,
        "execution_authority": False,
    }
    raw = _canonical(document)
    if len(raw) > MAX_JOURNAL_BYTES:
        _fail("archive_journal_size_invalid")
    return raw


def replay_journal(raw: bytes, *, expected_sha256: str) -> DiagnosticArchiveJournal:
    if (
        type(raw) is not bytes
        or not 0 < len(raw) <= MAX_JOURNAL_BYTES
        or _sha(raw) != _digest(expected_sha256)
    ):
        _fail("archive_journal_readback_mismatch")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=archive._unique_pairs)
    except (
        UnicodeError,
        ValueError,
        TypeError,
        RecursionError,
        archive.BillArchiveError,
    ):
        _fail("archive_journal_invalid")
    if (
        type(value) is not dict
        or set(value)
        != {
            "schema_version",
            "plan",
            "plan_sha256",
            "apply_scope_sha256",
            "events",
            "state",
            "admission",
            "account_complete",
            "execution_authority",
        }
        or value["schema_version"] != SCHEMA
        or value["admission"] != "DENY"
        or value["account_complete"] is not False
        or value["execution_authority"] is not False
        or _canonical(value) != raw
    ):
        _fail("archive_journal_invalid")
    plan_value = value["plan"]
    if type(plan_value) is not dict or set(plan_value) != set(
        DiagnosticArchivePlan.__dataclass_fields__
    ):
        _fail("archive_journal_invalid")
    try:
        plan = DiagnosticArchivePlan(
            **{
                **plan_value,
                "created_at": _read_stamp(plan_value["created_at"]),
                "reviewed_download_hosts": tuple(plan_value["reviewed_download_hosts"]),
            }
        )
    except (TypeError, AttributeError, KeyError):
        _fail("archive_journal_invalid")
    if (
        value["plan_sha256"] != plan.plan_sha256
        or value["apply_scope_sha256"] != plan.apply_scope_sha256
        or type(value["events"]) is not list
    ):
        _fail("archive_journal_invalid")
    journal = DiagnosticArchiveJournal(plan, tuple(value["events"]))
    if value["state"] != journal.state or encode_journal(journal) != raw:
        _fail("archive_journal_invalid")
    return journal
