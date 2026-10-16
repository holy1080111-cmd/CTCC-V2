"""Bounded readback of a DENY-only Demo event-history epoch journal.

The initial event deliberately says ``anchor_unverified``. Neither the local
database, an empty query result, nor this repository can establish that the
exchange retained every older order or that a legacy route never consumed an
event. This module is not imported by the G6 or execution authority path.
"""

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError

from app.database.models.demo_event_history_epoch import DemoEventHistoryEpochEvent

VERSION = "ctcc.demo_event_history_epoch_event.v1"
_EPOCH_PREFIX = b"ctcc-demo-event-history-epoch-v1\0"
_UID_LOCK_PREFIX = b"ctcc-qualification-account-v1\0"
_DIGEST = re.compile(r"[a-f0-9]{64}\Z")
_UID = re.compile(r"[0-9]{1,32}\Z")
_REASON = re.compile(r"[a-z][a-z0-9_]{1,63}\Z")
_MAX_EVENTS = 1024
_INSERT_GUARD_PROSRC_SHA256 = (
    "c6e67b6ef64e69fb79d15f059f476ac661ee45be4c8e73eb99daa034a674d1b8"
)
_IMMUTABLE_GUARD_PROSRC_SHA256 = (
    "14fe6a29f274ba10141a2ecdfbc40e4dc6e4014421f338f76a39352670e48649"
)


class EventHistoryEpochError(ValueError):
    """Stable denial without raw account, database, or credential details."""


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical(value: dict) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


@dataclass(frozen=True, slots=True, repr=False)
class EventEpochBinding:
    environment: str
    account_id: str
    credential_session_binding_sha256: str
    claimed_source_sha256: str
    policy_sha256: str

    def __post_init__(self):
        if (
            type(self.environment) is not str
            or self.environment != "demo"
            or type(self.account_id) is not str
            or _UID.fullmatch(self.account_id) is None
            or any(
                type(value) is not str or _DIGEST.fullmatch(value) is None
                for value in (
                    self.credential_session_binding_sha256,
                    self.claimed_source_sha256,
                    self.policy_sha256,
                )
            )
        ):
            raise EventHistoryEpochError("event_epoch_binding_invalid")

    @property
    def epoch_id(self) -> str:
        return hashlib.sha256(
            _EPOCH_PREFIX
            + b"\0".join(
                value.encode("ascii")
                for value in (
                    self.environment,
                    self.account_id,
                    self.credential_session_binding_sha256,
                    self.claimed_source_sha256,
                    self.policy_sha256,
                )
            )
        ).hexdigest()


@dataclass(frozen=True, slots=True, repr=False)
class EventEpochEvent:
    sequence: int
    kind: str
    reason_code: str
    evidence_sha256: str
    previous_sha256: str | None
    event_sha256: str
    db_recorded_at: datetime


@dataclass(frozen=True, slots=True, repr=False)
class EventEpochReadback:
    binding: EventEpochBinding
    events: tuple[EventEpochEvent, ...]

    @property
    def anchor_observed(self) -> bool:
        return bool(self.events)

    @property
    def invalidated(self) -> bool:
        return any(event.kind == "invalidated" for event in self.events)

    @property
    def history_complete(self) -> bool:
        return False

    @property
    def source_authenticity_verified(self) -> bool:
        return False

    @property
    def admission(self) -> str:
        return "DENY"

    @property
    def execution_authority(self) -> bool:
        return False


def _uid_lock_key(binding: EventEpochBinding) -> int:
    lock_bytes = hashlib.sha256(
        _UID_LOCK_PREFIX
        + binding.environment.encode("ascii")
        + b"\0"
        + binding.account_id.encode("ascii")
    ).digest()[:8]
    return int.from_bytes(lock_bytes, "big", signed=True)


def _wire(binding, *, sequence, kind, reason_code, evidence_sha256, previous):
    return _canonical(
        {
            "schema_version": VERSION,
            "epoch_id": binding.epoch_id,
            "sequence": sequence,
            "environment": binding.environment,
            "account_id": binding.account_id,
            "credential_session_binding_sha256": binding.credential_session_binding_sha256,
            "claimed_source_sha256": binding.claimed_source_sha256,
            "policy_sha256": binding.policy_sha256,
            "kind": kind,
            "reason_code": reason_code,
            "evidence_sha256": evidence_sha256,
            "previous_sha256": previous,
            "history_complete": False,
            "source_authenticity_verified": False,
            "admission": "DENY",
            "execution_authority": False,
        }
    )


async def _lock_and_check(session, binding):
    if session.get_bind().dialect.name != "postgresql":
        raise EventHistoryEpochError("event_epoch_postgresql_required")
    await session.execute(text("SET TRANSACTION ISOLATION LEVEL READ COMMITTED"))
    await session.execute(text("SET LOCAL search_path TO pg_catalog, public, pg_temp"))
    state = (
        await session.execute(
            text(
                "SELECT current_setting('session_replication_role'), current_setting('search_path')"
            )
        )
    ).one()
    if tuple(state) != ("origin", "pg_catalog, public, pg_temp"):
        raise EventHistoryEpochError("event_epoch_session_guard_invalid")
    await session.execute(select(func.pg_advisory_xact_lock(_uid_lock_key(binding))))
    # ROW EXCLUSIVE excludes concurrent trigger/constraint DDL but permits
    # unrelated UID writers. The same UID lock protects the qualification ledger.
    await session.execute(
        text(
            "LOCK TABLE public.demo_event_history_epoch_events IN ROW EXCLUSIVE MODE NOWAIT"
        )
    )
    trigger_rows = (
        await session.execute(
            text("""
              SELECT t.tgname,t.tgenabled,p.proname,t.tgtype,
                     encode(sha256(convert_to(p.prosrc,'UTF8')),'hex')
              FROM pg_catalog.pg_trigger t
              JOIN pg_catalog.pg_proc p ON p.oid=t.tgfoid
              WHERE t.tgrelid='public.demo_event_history_epoch_events'::regclass
                AND NOT t.tgisinternal
              ORDER BY t.tgname
            """)
        )
    ).all()
    required = [
        (
            "demo_event_history_epoch_immutable",
            "O",
            "qualification_ledger_immutable",
            27,
            _IMMUTABLE_GUARD_PROSRC_SHA256,
        ),
        (
            "demo_event_history_epoch_insert_guard",
            "O",
            "demo_event_history_epoch_insert_guard",
            7,
            _INSERT_GUARD_PROSRC_SHA256,
        ),
        (
            "demo_event_history_epoch_no_truncate",
            "O",
            "qualification_ledger_immutable",
            34,
            _IMMUTABLE_GUARD_PROSRC_SHA256,
        ),
    ]
    durable = (
        await session.scalar(
            text("""
              SELECT relpersistence FROM pg_catalog.pg_class
              WHERE oid='public.demo_event_history_epoch_events'::regclass
            """)
        )
    ) == "p"
    constraint_rows = (
        await session.execute(
            text("""
              SELECT c.conname,c.contype,c.convalidated,c.condeferrable,
                     string_agg(a.attname,',' ORDER BY ord.ordinality)
              FROM pg_catalog.pg_constraint c
              LEFT JOIN LATERAL unnest(c.conkey) WITH ORDINALITY
                   AS ord(attnum,ordinality) ON true
              LEFT JOIN pg_catalog.pg_attribute a
                ON a.attrelid=c.conrelid AND a.attnum=ord.attnum
              WHERE conrelid='public.demo_event_history_epoch_events'::regclass
              GROUP BY c.conname,c.contype,c.convalidated,c.condeferrable
              ORDER BY c.conname
            """)
        )
    ).all()
    constraints = {tuple(row) for row in constraint_rows}
    required_constraints = {
        (
            "pk_demo_event_history_epoch_events",
            "p",
            True,
            False,
            "epoch_id,sequence",
        ),
        (
            "uq_demo_event_history_epoch_uid_binding_sequence",
            "u",
            True,
            False,
            "environment,account_id,credential_session_binding_sha256,claimed_source_sha256,policy_sha256,sequence",
        ),
    }
    required_checks = {
        "ck_demo_event_history_epoch_events_scope",
        "ck_demo_event_history_epoch_events_hashes",
        "ck_demo_event_history_epoch_events_chain",
        "ck_demo_event_history_epoch_events_bounded",
    }
    observed_checks = {
        row[0]
        for row in constraints
        if row[1] == "c" and row[2] is True and row[3] is False
    }
    if (
        [tuple(row) for row in trigger_rows] != required
        or not durable
        or not required_constraints <= constraints
        or observed_checks != required_checks
    ):
        raise EventHistoryEpochError("event_epoch_schema_retention_invalid")


def _read_rows(binding, rows):
    if len(rows) > _MAX_EVENTS:
        raise EventHistoryEpochError("event_epoch_bound_exceeded")
    events = []
    previous = None
    previous_time = None
    for sequence, row in enumerate(rows, 1):
        if (
            row.epoch_id != binding.epoch_id
            or row.sequence != sequence
            or row.environment != binding.environment
            or row.account_id != binding.account_id
            or row.credential_session_binding_sha256
            != binding.credential_session_binding_sha256
            or row.claimed_source_sha256 != binding.claimed_source_sha256
            or row.policy_sha256 != binding.policy_sha256
            or row.previous_sha256 != previous
            or type(row.kind) is not str
            or type(row.reason_code) is not str
            or type(row.event_sha256) is not str
            or (
                sequence == 1
                and (row.kind, row.reason_code)
                != ("anchor_unverified", "source_history_unverified")
            )
            or (
                sequence > 1
                and (
                    row.kind != "invalidated"
                    or _REASON.fullmatch(row.reason_code) is None
                )
            )
            or type(row.db_recorded_at) is not datetime
            or row.db_recorded_at.tzinfo is None
            or (previous_time is not None and row.db_recorded_at < previous_time)
        ):
            raise EventHistoryEpochError("event_epoch_chain_invalid")
        expected = _wire(
            binding,
            sequence=sequence,
            kind=row.kind,
            reason_code=row.reason_code,
            evidence_sha256=row.evidence_sha256,
            previous=previous,
        )
        if (
            type(row.evidence_sha256) is not str
            or _DIGEST.fullmatch(row.evidence_sha256) is None
            or row.event_json != expected
            or row.event_sha256 != _digest(expected)
        ):
            raise EventHistoryEpochError("event_epoch_hash_or_payload_invalid")
        events.append(
            EventEpochEvent(
                sequence,
                row.kind,
                row.reason_code,
                row.evidence_sha256,
                previous,
                row.event_sha256,
                row.db_recorded_at,
            )
        )
        previous = row.event_sha256
        previous_time = row.db_recorded_at
    return EventEpochReadback(binding, tuple(events))


class DemoEventHistoryEpochRepository:
    """Local audit only; readback never supplies consumed-event completeness."""

    def __init__(self, session_factory):
        self.session_factory = session_factory

    async def _rows(self, session, binding):
        return (
            await session.scalars(
                select(DemoEventHistoryEpochEvent)
                .where(DemoEventHistoryEpochEvent.epoch_id == binding.epoch_id)
                .order_by(DemoEventHistoryEpochEvent.sequence)
                .limit(_MAX_EVENTS + 1)
            )
        ).all()

    async def read(self, binding: EventEpochBinding) -> EventEpochReadback:
        if type(binding) is not EventEpochBinding:
            raise EventHistoryEpochError("event_epoch_binding_invalid")
        try:
            async with self.session_factory() as session, session.begin():
                await _lock_and_check(session, binding)
                return _read_rows(binding, await self._rows(session, binding))
        except SQLAlchemyError:
            raise EventHistoryEpochError("event_epoch_database_denied") from None

    async def _append(self, binding, *, kind, reason_code, evidence_sha256):
        if type(binding) is not EventEpochBinding:
            raise EventHistoryEpochError("event_epoch_binding_invalid")
        if (
            type(evidence_sha256) is not str
            or _DIGEST.fullmatch(evidence_sha256) is None
        ):
            raise EventHistoryEpochError("event_epoch_evidence_invalid")
        if kind == "invalidated" and (
            type(reason_code) is not str or _REASON.fullmatch(reason_code) is None
        ):
            raise EventHistoryEpochError("event_epoch_reason_invalid")
        try:
            async with self.session_factory() as session, session.begin():
                await _lock_and_check(session, binding)
                readback = _read_rows(binding, await self._rows(session, binding))
                if kind == "anchor_unverified" and readback.events:
                    raise EventHistoryEpochError("event_epoch_anchor_exists")
                if kind == "invalidated" and not readback.events:
                    raise EventHistoryEpochError("event_epoch_anchor_missing")
                if len(readback.events) >= _MAX_EVENTS:
                    raise EventHistoryEpochError("event_epoch_bound_exceeded")
                sequence = len(readback.events) + 1
                previous = readback.events[-1].event_sha256 if readback.events else None
                wire = _wire(
                    binding,
                    sequence=sequence,
                    kind=kind,
                    reason_code=reason_code,
                    evidence_sha256=evidence_sha256,
                    previous=previous,
                )
                session.add(
                    DemoEventHistoryEpochEvent(
                        epoch_id=binding.epoch_id,
                        sequence=sequence,
                        environment=binding.environment,
                        account_id=binding.account_id,
                        credential_session_binding_sha256=binding.credential_session_binding_sha256,
                        claimed_source_sha256=binding.claimed_source_sha256,
                        policy_sha256=binding.policy_sha256,
                        kind=kind,
                        reason_code=reason_code,
                        evidence_sha256=evidence_sha256,
                        previous_sha256=previous,
                        event_sha256=_digest(wire),
                        event_json=wire,
                    )
                )
                await session.flush()
        except SQLAlchemyError:
            raise EventHistoryEpochError("event_epoch_database_denied") from None
        # Separate transaction: a commit with uncertain outcome is never retried.
        committed = await self.read(binding)
        if len(committed.events) < sequence or committed.events[
            sequence - 1
        ].event_sha256 != _digest(wire):
            raise EventHistoryEpochError("event_epoch_commit_readback_denied")
        return committed

    async def record_unverified_anchor(
        self, binding: EventEpochBinding, *, evidence_sha256: str
    ) -> EventEpochReadback:
        return await self._append(
            binding,
            kind="anchor_unverified",
            reason_code="source_history_unverified",
            evidence_sha256=evidence_sha256,
        )

    async def invalidate(
        self,
        binding: EventEpochBinding,
        *,
        reason_code: str,
        evidence_sha256: str,
    ) -> EventEpochReadback:
        return await self._append(
            binding,
            kind="invalidated",
            reason_code=reason_code,
            evidence_sha256=evidence_sha256,
        )
