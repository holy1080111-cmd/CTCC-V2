"""Post-submit local Notion outbox; no order, scheduler or Notion API wiring.

The producer supplies bounded Demo submission/evidence *claims*, not verified
exchange authority. Enqueue must occur after submission, outside its wait path.
An immutable report-id JSON is the enqueue commit marker; an append-only,
hash-linked journal records all later transitions. Cooperating processes use
the existing evidence store's root lease and no-clobber/fsync/readback primitives.
Remote adapters run only after a verified dispatch marker and outside that lease.

Only an explicit, receipted not-created result is retryable. Dispatch crashes,
timeouts and cancellation are uncertain, never an excuse to repeat creation (or
an order). Explicit reconciliation requires a quiesced worker and a receipt claim.
All jobs, including unknown deliveries, are retained; this module deletes none.

The pre-existing absolute root must be service-owned, with trusted ACLs. POSIX
also fsyncs directory metadata. Windows retains the existing ancestor handles;
permission denial fails closed, and sudden-power-loss atomic directory durability
is NOT promised. Hashes/leases do not defend against malicious same-user code or
prove exactly-once delivery. The injected clock and adapter are trusted boundaries.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import uuid
from collections.abc import Awaitable, Callable
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.trade_evidence import storage

MAX_RECORD_BYTES = 32768
MAX_EVENTS = 256
MAX_ROOT_ENTRIES = 16384
Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")]
ReportId = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,95}$")]
Token = Annotated[str, Field(pattern=r"^[a-f0-9]{32}$")]
Status = Literal[
    "queued",
    "claimed",
    "dispatching",
    "retry_wait",
    "delivered",
    "exhausted",
    "uncertain",
]
Strategy = Literal[
    "trend_pullback",
    "breakout_continuation",
    "liquidity_sweep_reversal",
    "fvg_return",
    "order_block_return",
    "range_reversal",
    "structure_reversal",
    "volatility_expansion",
]


class OutboxError(ValueError):
    """Static local codes only; never include adapter or filesystem error text."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def actual_utc() -> datetime:
    return datetime.now(UTC)


def _utc(value):
    if type(value) is not datetime or value.tzinfo is None:
        raise OutboxError("outbox_clock_invalid")
    try:
        if value.utcoffset() is None:
            raise OutboxError("outbox_clock_invalid")
        return value.astimezone(UTC)
    except Exception:  # noqa: BLE001 - A timezone implementation cannot leak exception text.
        raise OutboxError("outbox_clock_invalid") from None


def _now(clock):
    if not callable(clock):
        raise OutboxError("outbox_clock_invalid")
    try:
        return _utc(clock())
    except Exception:  # noqa: BLE001 - The injected clock cannot leak exception text.
        raise OutboxError("outbox_clock_invalid") from None


def _later(at, seconds):
    try:
        return at + timedelta(seconds=seconds)
    except OverflowError:
        raise OutboxError("outbox_clock_out_of_range") from None


class _Model(BaseModel):
    model_config = ConfigDict(
        strict=True,
        frozen=True,
        extra="forbid",
        revalidate_instances="always",
        str_strip_whitespace=False,
    )


class OutboxPayload(_Model):
    """Allowlisted metadata only: no headers, credentials, URLs or arbitrary body.

    Receipt hashes and non-secret order_reference come from a trusted producer;
    consistency validation does not authenticate those claims or an account.
    """

    report_id: ReportId
    instrument_id: Annotated[
        str, Field(pattern=r"^[A-Z0-9]{1,16}-[A-Z0-9]{1,16}-SWAP$")
    ]
    strategy: Strategy
    direction: Literal["long", "short"]
    order_reference: Identifier
    submitted_at: datetime
    evidence_completed_at: datetime
    source_sha256: Digest
    evidence_report_sha256: Digest
    submission_receipt_sha256: Digest
    venue: Literal["OKX_DEMO"] = "OKX_DEMO"
    execution_authority: Literal[False] = False
    source_authenticity_verified: Literal[False] = False

    _times = field_validator("submitted_at", "evidence_completed_at")(_utc)

    @field_validator("report_id")
    @classmethod
    def report_name(cls, value):
        _name(value)
        return value

    @field_validator(
        "execution_authority", "source_authenticity_verified", mode="before"
    )
    @classmethod
    def no_authority(cls, value):
        if type(value) is not bool or value is not False:
            raise ValueError("outbox_cannot_grant_authority")
        return value

    @model_validator(mode="after")
    def chronological(self):
        if self.evidence_completed_at > self.submitted_at:
            raise ValueError("outbox_evidence_after_submission")
        return self


class OutboxPolicy(_Model):
    max_attempts: int = Field(default=3, ge=1, le=10)
    lease_seconds: int = Field(default=60, ge=2, le=3600)
    adapter_timeout_seconds: int = Field(default=10, ge=1, le=300)
    base_backoff_seconds: int = Field(default=5, ge=1, le=86400)
    max_backoff_seconds: int = Field(default=3600, ge=1, le=86400)
    terminal_retention_seconds: int = Field(default=604800, ge=1, le=31536000)

    @model_validator(mode="after")
    def limits(self):
        if self.lease_seconds <= self.adapter_timeout_seconds:
            raise ValueError("outbox_lease_must_exceed_adapter_timeout")
        if self.max_backoff_seconds < self.base_backoff_seconds:
            raise ValueError("outbox_backoff_bounds_invalid")
        return self


class OutboxEnvelope(_Model):
    schema_version: Literal["ctcc.notion_outbox.v1"] = "ctcc.notion_outbox.v1"
    payload: OutboxPayload
    policy: OutboxPolicy
    enqueued_at: datetime
    envelope_sha256: Digest

    _time = field_validator("enqueued_at")(_utc)

    @model_validator(mode="after")
    def post_submission(self):
        if self.enqueued_at < self.payload.submitted_at:
            raise ValueError("outbox_enqueue_before_submission")
        return self


class DeliveryOutcome(_Model):
    report_id: ReportId
    envelope_sha256: Digest
    fence_token: Token
    status: Literal[
        "delivered", "not_created_retryable", "not_created_final", "uncertain"
    ]
    remote_page_id: Token | None = None
    receipt_sha256: Digest | None = None

    @model_validator(mode="after")
    def evidence(self):
        if self.status == "delivered":
            if self.remote_page_id is None or self.receipt_sha256 is None:
                raise ValueError("outbox_delivery_receipt_required")
        elif self.remote_page_id is not None or (
            (self.status == "uncertain") != (self.receipt_sha256 is None)
        ):
            raise ValueError("outbox_not_created_or_unknown_receipt_invalid")
        return self


class Reconciliation(_Model):
    """Explicit external reconciliation claim; this module performs no lookup."""

    report_id: ReportId
    envelope_sha256: Digest
    uncertain_event_sha256: Digest
    resolution: Literal["delivered", "not_created"]
    worker_quiesced: Literal[True]
    receipt_sha256: Digest
    remote_page_id: Token | None = None

    @field_validator("worker_quiesced", mode="before")
    @classmethod
    def exact_quiescence(cls, value):
        if type(value) is not bool or value is not True:
            raise ValueError("outbox_worker_quiescence_required")
        return value

    @model_validator(mode="after")
    def page(self):
        if (self.resolution == "delivered") != (self.remote_page_id is not None):
            raise ValueError("outbox_reconciliation_page_invalid")
        return self


class OutboxEvent(_Model):
    report_id: ReportId
    envelope_sha256: Digest
    revision: int = Field(ge=1, le=MAX_EVENTS)
    previous_sha256: Digest
    event_sha256: Digest
    action: Literal["enqueue", "claim", "begin", "finish", "recover", "reconcile"]
    status: Status
    attempts: int = Field(ge=0, le=10)
    at: datetime
    worker_id: Identifier | None = None
    fence_token: Token | None = None
    lease_expires_at: datetime | None = None
    next_attempt_at: datetime | None = None
    outcome: DeliveryOutcome | None = None
    reconciliation: Reconciliation | None = None

    _time = field_validator("at")(_utc)

    @field_validator("lease_expires_at", "next_attempt_at")
    @classmethod
    def optional_time(cls, value):
        return None if value is None else _utc(value)


class ClaimToken(_Model):
    report_id: ReportId
    envelope_sha256: Digest
    revision: int = Field(ge=1, le=MAX_EVENTS)
    event_sha256: Digest
    fence_token: Token
    worker_id: Identifier
    lease_expires_at: datetime
    status: Literal["claimed", "dispatching"]

    _time = field_validator("lease_expires_at")(_utc)


class OutboxView(_Model):
    envelope: OutboxEnvelope
    events: tuple[OutboxEvent, ...] = Field(min_length=1, max_length=MAX_EVENTS)
    verified_at: datetime
    execution_authority: Literal[False] = False

    _time = field_validator("verified_at")(_utc)
    _authority = field_validator("execution_authority", mode="before")(
        OutboxPayload.no_authority.__func__
    )

    @property
    def head(self):
        return self.events[-1]

    @property
    def status(self):
        return self.head.status

    @property
    def attempts(self):
        return self.head.attempts


class RetentionDecision(_Model):
    must_retain: Literal[True] = True
    eligible_after: datetime | None
    reason: Literal[
        "unresolved_delivery", "minimum_retention", "retained_no_automatic_deletion"
    ]

    _retain = field_validator("must_retain", mode="before")(
        Reconciliation.exact_quiescence.__func__
    )

    @field_validator("eligible_after")
    @classmethod
    def optional_time(cls, value):
        return None if value is None else _utc(value)


def _name(report_id):
    try:
        storage._report_name(report_id)
    except (storage.EvidencePublicationError, TypeError, ValueError):
        raise OutboxError("outbox_report_id_invalid") from None


def _guard(value, depth=0, budget=None, *, json_input=False):
    budget = [50000, 4 * 1024 * 1024] if budget is None else budget
    budget[0] -= 1
    if depth > 16 or budget[0] < 0:
        raise OutboxError("outbox_record_limit")
    kinds = {
        OutboxPayload,
        OutboxPolicy,
        OutboxEnvelope,
        OutboxEvent,
        OutboxView,
        ClaimToken,
        DeliveryOutcome,
        Reconciliation,
        RetentionDecision,
    }
    if isinstance(value, BaseModel):
        if (
            type(value) not in kinds
            or value.__pydantic_extra__
            or value.__pydantic_private__ is not None
            or set(value.__dict__) != set(type(value).model_fields)
        ):
            raise OutboxError("outbox_model_invalid")
        edges = {
            OutboxEnvelope: {"payload": OutboxPayload, "policy": OutboxPolicy},
            OutboxEvent: {"outcome": DeliveryOutcome, "reconciliation": Reconciliation},
            OutboxView: {"envelope": OutboxEnvelope},
        }.get(type(value), {})
        for name, expected in edges.items():
            if (
                value.__dict__[name] is not None
                and type(value.__dict__[name]) is not expected
            ):
                raise OutboxError("outbox_model_invalid")
        if type(value) is OutboxView and (
            type(value.events) is not tuple
            or not 1 <= len(value.events) <= MAX_EVENTS
            or any(type(event) is not OutboxEvent for event in value.events)
        ):
            raise OutboxError("outbox_model_invalid")
        _guard(value.__dict__, depth + 1, budget, json_input=json_input)
    elif type(value) is dict:
        if len(value) > 32 or any(
            type(key) is not str or len(key) > 64 for key in value
        ):
            raise OutboxError("outbox_record_limit")
        for item in value.values():
            _guard(item, depth + 1, budget, json_input=json_input)
    elif type(value) is tuple or (json_input and type(value) is list):
        if len(value) > MAX_EVENTS:
            raise OutboxError("outbox_record_limit")
        for item in value:
            _guard(item, depth + 1, budget, json_input=json_input)
    elif type(value) is str:
        budget[1] -= len(value)
        if len(value) > 128 or budget[1] < 0:
            raise OutboxError("outbox_record_limit")
    elif type(value) is datetime:
        _utc(value)
    elif (
        value is None
        or type(value) is bool
        or (type(value) is int and abs(value) <= 31536000)
    ):
        pass
    else:
        raise OutboxError("outbox_record_type_invalid")


def _plain(value):
    if isinstance(value, BaseModel):
        return {key: _plain(item) for key, item in value.__dict__.items()}
    if type(value) is tuple:
        return tuple(_plain(item) for item in value)
    return value


def _copy(value, kind):
    try:
        if type(value) is not kind:
            raise OutboxError("outbox_model_invalid")
        _guard(value)
        return kind.model_validate(_plain(value), strict=True)
    except (ValueError, TypeError, AttributeError, OverflowError):
        raise OutboxError("outbox_record_invalid") from None


def _wire(value, *, omit=None):
    _guard(value)
    data = value.model_dump(mode="json", exclude={omit} if omit else None)
    raw = json.dumps(
        data, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()
    if not 0 < len(raw) <= MAX_RECORD_BYTES:
        raise OutboxError("outbox_record_limit")
    return raw


def _digest(value, field):
    return hashlib.sha256(_wire(value, omit=field)).hexdigest()


def _seal(kind, values, field):
    draft = kind.model_validate(values | {field: "0" * 64}, strict=True)
    return kind.model_validate(values | {field: _digest(draft, field)}, strict=True)


def _decode(raw, kind):
    if type(raw) is not bytes or not 0 < len(raw) <= MAX_RECORD_BYTES:
        raise OutboxError("outbox_record_limit")
    try:
        data = json.loads(
            raw,
            object_pairs_hook=storage._unique_object,
            parse_constant=storage._json_constant,
        )
        _guard(data, json_input=True)
        result = kind.model_validate_json(raw, strict=True)
        if _wire(result) != raw:
            raise OutboxError("outbox_noncanonical_record")
        return result
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise OutboxError("outbox_disk_record_invalid") from None


def _event(
    envelope,
    previous,
    action,
    at,
    *,
    worker_id=None,
    fence_token=None,
    outcome=None,
    reconciliation=None,
):
    policy = envelope.policy
    if previous is None:
        if action != "enqueue" or at != envelope.enqueued_at:
            raise OutboxError("outbox_transition_invalid")
        worker_id, fence_token = None, None
        attempts, status, lease, due = 0, "queued", None, at
    else:
        if at < previous.at:
            raise OutboxError("outbox_clock_reversed")
        attempts, status = previous.attempts, previous.status
        lease, due = None, None
        if action != "claim":
            worker_id, fence_token = previous.worker_id, previous.fence_token
        if action == "claim":
            # Reserve begin + finish/recovery + one explicit reconciliation.
            # Capacity can stop local retries, never strand a new remote attempt
            # without space for the unknown-delivery barrier and its resolution.
            if previous.revision > MAX_EVENTS - 4:
                raise OutboxError("outbox_journal_capacity_reached")
            if (
                status not in {"queued", "retry_wait"}
                or previous.next_attempt_at > at
                or attempts >= policy.max_attempts
                or worker_id is None
                or fence_token is None
            ):
                raise OutboxError("outbox_not_claimable")
            status, lease = "claimed", _later(at, policy.lease_seconds)
        elif action == "begin":
            if status != "claimed" or at >= previous.lease_expires_at:
                raise OutboxError("outbox_claim_expired_or_invalid")
            attempts += 1
            status, lease = "dispatching", _later(at, policy.lease_seconds)
        elif action == "recover":
            if (
                status not in {"claimed", "dispatching"}
                or at < previous.lease_expires_at
            ):
                raise OutboxError("outbox_lease_not_expired")
            status = "retry_wait" if status == "claimed" else "uncertain"
            due = at if status == "retry_wait" else None
        elif action == "finish":
            if status != "dispatching" or outcome is None:
                raise OutboxError("outbox_transition_invalid")
            if at >= previous.lease_expires_at:
                raise OutboxError("outbox_dispatch_lease_expired")
            if (outcome.report_id, outcome.envelope_sha256, outcome.fence_token) != (
                envelope.payload.report_id,
                envelope.envelope_sha256,
                previous.fence_token,
            ):
                raise OutboxError("outbox_outcome_identity_mismatch")
            status = {
                "delivered": "delivered",
                "uncertain": "uncertain",
                "not_created_final": "exhausted",
                "not_created_retryable": "retry_wait",
            }[outcome.status]
        elif action == "reconcile":
            if status != "uncertain" or reconciliation is None:
                raise OutboxError("outbox_reconciliation_required")
            if (
                reconciliation.report_id,
                reconciliation.envelope_sha256,
                reconciliation.uncertain_event_sha256,
            ) != (
                envelope.payload.report_id,
                envelope.envelope_sha256,
                previous.event_sha256,
            ):
                raise OutboxError("outbox_reconciliation_identity_mismatch")
            status = (
                "delivered"
                if reconciliation.resolution == "delivered"
                else "retry_wait"
            )
        else:
            raise OutboxError("outbox_transition_invalid")
        if status == "retry_wait" and action != "recover":
            if (
                attempts >= policy.max_attempts
                or previous.revision + 1 > MAX_EVENTS - 4
            ):
                status = "exhausted"
            else:
                delay = min(
                    policy.max_backoff_seconds,
                    policy.base_backoff_seconds * 2 ** (attempts - 1),
                )
                due = _later(at, delay)
        elif status == "retry_wait" and previous.revision + 1 > MAX_EVENTS - 4:
            status, due = "exhausted", None
    if (action == "finish") != (outcome is not None) or (action == "reconcile") != (
        reconciliation is not None
    ):
        raise OutboxError("outbox_transition_metadata_invalid")
    return _seal(
        OutboxEvent,
        {
            "report_id": envelope.payload.report_id,
            "envelope_sha256": envelope.envelope_sha256,
            "revision": 1 if previous is None else previous.revision + 1,
            "previous_sha256": envelope.envelope_sha256
            if previous is None
            else previous.event_sha256,
            "action": action,
            "status": status,
            "attempts": attempts,
            "at": at,
            "worker_id": worker_id,
            "fence_token": fence_token,
            "lease_expires_at": lease,
            "next_attempt_at": due,
            "outcome": outcome,
            "reconciliation": reconciliation,
        },
        "event_sha256",
    )


def _verify(envelope, events):
    if _digest(envelope, "envelope_sha256") != envelope.envelope_sha256:
        raise OutboxError("outbox_envelope_hash_mismatch")
    previous = None
    for event in events:
        expected = _event(
            envelope,
            previous,
            event.action,
            event.at,
            worker_id=event.worker_id,
            fence_token=event.fence_token,
            outcome=event.outcome,
            reconciliation=event.reconciliation,
        )
        if _wire(event) != _wire(expected):
            raise OutboxError("outbox_journal_mismatch")
        previous = event


def validate_outbox_view(value):
    checked = _copy(value, OutboxView)
    _verify(checked.envelope, checked.events)
    if checked.verified_at < checked.head.at:
        raise OutboxError("outbox_clock_reversed")
    return checked


@contextmanager
def _root_context(root):
    context = storage._windows_root if os.name == "nt" else storage._posix_root
    with context(root) as directory:
        yield directory


@contextmanager
def _transaction(root, report_id):
    _name(report_id)
    try:
        checked = storage._root_path(root)
        with _root_context(checked) as directory:
            names = directory.names()
            if len(names) > MAX_ROOT_ENTRIES:
                raise OutboxError("outbox_directory_limit")
            targets = (report_id + ".json", report_id + ".state")
            if any(
                name.casefold() == target.casefold() and name != target
                for name in names
                for target in targets
            ):
                raise OutboxError("outbox_case_alias")
            yield directory
    except OutboxError:
        raise
    except PermissionError:
        raise OutboxError("outbox_storage_permission_denied") from None
    except Exception:  # noqa: BLE001 - Filesystem/driver errors are a redacted boundary.
        raise OutboxError("outbox_storage_unavailable") from None


def _load(directory, report_id):
    envelope = _decode(
        directory.read(report_id + ".json", MAX_RECORD_BYTES), OutboxEnvelope
    )
    if envelope.payload.report_id != report_id:
        raise OutboxError("outbox_report_mismatch")
    with directory.child(report_id + ".state") as journal:
        names = sorted(journal.names())
        if not 1 <= len(names) <= MAX_EVENTS or names != [
            f"{n:08d}.json" for n in range(1, len(names) + 1)
        ]:
            raise OutboxError("outbox_journal_incomplete")
        events = tuple(
            _decode(journal.read(name, MAX_RECORD_BYTES), OutboxEvent) for name in names
        )
    _verify(envelope, events)
    return envelope, events


def _view(envelope, events, clock):
    view = OutboxView(envelope=envelope, events=events, verified_at=_now(clock))
    if view.verified_at < view.head.at:
        raise OutboxError("outbox_clock_reversed")
    return view


def _append(directory, envelope, events, action, at, clock, **metadata):
    if len(events) >= MAX_EVENTS:
        raise OutboxError("outbox_journal_capacity_reached")
    event = _event(envelope, events[-1], action, at, **metadata)
    raw = _wire(event)
    with directory.child(envelope.payload.report_id + ".state") as journal:
        name = f"{event.revision:08d}.json"
        journal.publish(name, raw)
        if journal.read(name, MAX_RECORD_BYTES) != raw:
            raise OutboxError("outbox_readback_failed")
    loaded, history = _load(directory, envelope.payload.report_id)
    return _view(loaded, history, clock)


def enqueue(
    root: Path,
    payload: OutboxPayload,
    *,
    policy: OutboxPolicy | None = None,
    clock: Callable[[], datetime] = actual_utc,
) -> OutboxView:
    payload = _copy(payload, OutboxPayload)
    policy = OutboxPolicy() if policy is None else _copy(policy, OutboxPolicy)
    at = _now(clock)
    proposed = _seal(
        OutboxEnvelope,
        {"payload": payload, "policy": policy, "enqueued_at": at},
        "envelope_sha256",
    )
    report_id = payload.report_id
    with _transaction(root, report_id) as directory:
        names = directory.names()
        present = [
            name in names for name in (report_id + ".json", report_id + ".state")
        ]
        if any(present):
            if not all(present):
                raise OutboxError("outbox_enqueue_incomplete")
            envelope, events = _load(directory, report_id)
            if _wire(envelope.payload) != _wire(payload) or _wire(
                envelope.policy
            ) != _wire(policy):
                raise OutboxError("outbox_report_conflict")
        else:
            envelope = proposed
            initial = _event(envelope, None, "enqueue", at)
            directory.mkdir(report_id + ".state")
            with directory.child(report_id + ".state") as journal:
                raw = _wire(initial)
                journal.publish("00000001.json", raw)
                if journal.read("00000001.json", MAX_RECORD_BYTES) != raw:
                    raise OutboxError("outbox_readback_failed")
            directory.publish(report_id + ".json", _wire(envelope))
            envelope, events = _load(directory, report_id)
        return _view(envelope, events, clock)


def read_job(root: Path, report_id: str, *, clock=actual_utc) -> OutboxView:
    with _transaction(root, report_id) as directory:
        return _view(*_load(directory, report_id), clock)


def _claim(view):
    head = view.head
    return ClaimToken(**{name: getattr(head, name) for name in ClaimToken.model_fields})


def _check_claim(claim, envelope, events):
    head = events[-1]
    if any(
        getattr(claim, name) != getattr(head, name) for name in ClaimToken.model_fields
    ):
        raise OutboxError("outbox_fence_stale")
    if claim.envelope_sha256 != envelope.envelope_sha256:
        raise OutboxError("outbox_fence_stale")


def claim_job(
    root: Path, report_id: str, *, worker_id: str, clock=actual_utc
) -> ClaimToken:
    if (
        type(worker_id) is not str
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", worker_id) is None
    ):
        raise OutboxError("outbox_worker_id_invalid")
    with _transaction(root, report_id) as directory:
        envelope, events = _load(directory, report_id)
        result = _append(
            directory,
            envelope,
            events,
            "claim",
            _now(clock),
            clock,
            worker_id=worker_id,
            fence_token=uuid.uuid4().hex,
        )
        return _claim(result)


def begin_dispatch(root: Path, claim: ClaimToken, *, clock=actual_utc) -> ClaimToken:
    claim = _copy(claim, ClaimToken)
    with _transaction(root, claim.report_id) as directory:
        envelope, events = _load(directory, claim.report_id)
        _check_claim(claim, envelope, events)
        view = _append(directory, envelope, events, "begin", _now(clock), clock)
        if view.verified_at >= view.head.lease_expires_at:
            raise OutboxError("outbox_dispatch_lease_expired")
        return _claim(view)


def finish_dispatch(
    root: Path, claim: ClaimToken, outcome: DeliveryOutcome, *, clock=actual_utc
) -> OutboxView:
    claim, outcome = _copy(claim, ClaimToken), _copy(outcome, DeliveryOutcome)
    with _transaction(root, claim.report_id) as directory:
        envelope, events = _load(directory, claim.report_id)
        _check_claim(claim, envelope, events)
        return _append(
            directory, envelope, events, "finish", _now(clock), clock, outcome=outcome
        )


def recover_job(root: Path, report_id: str, *, clock=actual_utc) -> OutboxView:
    with _transaction(root, report_id) as directory:
        envelope, events = _load(directory, report_id)
        at, head = _now(clock), events[-1]
        if head.status not in {"claimed", "dispatching"} or at < head.lease_expires_at:
            return _view(envelope, events, clock)
        return _append(directory, envelope, events, "recover", at, clock)


def reconcile_job(
    root: Path, reconciliation: Reconciliation, *, clock=actual_utc
) -> OutboxView:
    reconciliation = _copy(reconciliation, Reconciliation)
    with _transaction(root, reconciliation.report_id) as directory:
        envelope, events = _load(directory, reconciliation.report_id)
        return _append(
            directory,
            envelope,
            events,
            "reconcile",
            _now(clock),
            clock,
            reconciliation=reconciliation,
        )


def retention_decision(value: OutboxView, *, now: datetime) -> RetentionDecision:
    view, at = validate_outbox_view(value), _utc(now)
    if at < view.verified_at:
        raise OutboxError("outbox_clock_reversed")
    if view.status not in {"delivered", "exhausted"}:
        return RetentionDecision(eligible_after=None, reason="unresolved_delivery")
    due = _later(view.head.at, view.envelope.policy.terminal_retention_seconds)
    return RetentionDecision(
        eligible_after=due,
        reason=("minimum_retention" if at < due else "retained_no_automatic_deletion"),
    )


def _finish_worker(root, dispatched, outcome, clock):
    try:
        return finish_dispatch(root, dispatched, outcome, clock=clock)
    except OutboxError as exc:
        if exc.code != "outbox_dispatch_lease_expired":
            raise
        # A late response cannot revive its expired fence, even if it reports
        # success. Preserve the expired dispatch as unknown for reconciliation.
        return recover_job(root, dispatched.report_id, clock=clock)


async def dispatch_once(
    root: Path,
    report_id: str,
    *,
    worker_id: str,
    adapter: Callable[[OutboxPayload, ClaimToken], Awaitable[DeliveryOutcome]],
    clock: Callable[[], datetime] = actual_utc,
) -> OutboxView:
    """One owned adapter attempt; never called inside order submit or a root lease.

    The adapter must honor cancellation and own/close its transport. An adapter
    that ignores cancellation is outside the bounded contract. No background
    task, scheduler, real HTTP client, order callback or retry loop is created.
    Failed outcome persistence leaves dispatching/uncertain for reconciliation.
    """
    if not callable(adapter):
        raise OutboxError("outbox_adapter_invalid")
    task = asyncio.current_task()
    if task is not None and task.cancelling():
        raise asyncio.CancelledError
    view = recover_job(root, report_id, clock=clock)
    claimed = claim_job(root, report_id, worker_id=worker_id, clock=clock)
    dispatched = begin_dispatch(root, claimed, clock=clock)
    unknown = DeliveryOutcome(
        report_id=report_id,
        envelope_sha256=dispatched.envelope_sha256,
        fence_token=dispatched.fence_token,
        status="uncertain",
    )
    interrupted = False
    try:
        if _now(clock) >= dispatched.lease_expires_at:
            raise OutboxError("outbox_dispatch_lease_expired")
        async with asyncio.timeout(
            view.envelope.policy.adapter_timeout_seconds
        ) as deadline:
            outcome = _copy(
                await adapter(view.envelope.payload, dispatched), DeliveryOutcome
            )
        if deadline.expired():
            raise OutboxError("outbox_adapter_timeout")
        if (outcome.report_id, outcome.envelope_sha256, outcome.fence_token) != (
            report_id,
            dispatched.envelope_sha256,
            dispatched.fence_token,
        ):
            raise OutboxError("outbox_outcome_identity_mismatch")
    except asyncio.CancelledError:
        interrupted = True
    except Exception:  # noqa: BLE001 - Unknown remote results are never retryable.
        outcome = unknown  # Never stringify remote errors or invalid response objects.
    if interrupted or (task is not None and task.cancelling()):
        try:
            _finish_worker(root, dispatched, unknown, clock)
        except Exception:  # noqa: BLE001, S110 - Preserve cancellation, never log untrusted text.
            pass  # Existing durable dispatch marker remains non-retryable.
        raise asyncio.CancelledError
    return _finish_worker(root, dispatched, outcome, clock)
