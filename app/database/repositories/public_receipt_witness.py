"""Restricted-role PostgreSQL witness for the native public-minute journal.

This protects the latest checkpoint from a simultaneous local-journal rollback
only when the database and its credential are independently administered. It
does not authenticate exchange bytes, grant predictive eligibility or orders.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.public_market_source.public_market_receipts import checked, sha
from app.public_market_source.public_receipt_storage import PublicJournalCheckpointV1

_HEX32 = re.compile(r"[a-f0-9]{32}\Z")
_HEX64 = re.compile(r"[a-f0-9]{64}\Z")
_TRANSITIONS = frozenset(
    {
        "initialize",
        "open_attempt",
        "append_attempt",
        "append_capture",
        "close_rejected_attempt",
    }
)


class PublicWitnessError(ValueError):
    """Safe code only; never include database URLs or private connection data."""


@dataclass(frozen=True, slots=True)
class WitnessRevision:
    journal_key: str
    revision: int
    journal_id: str
    root_device: int
    root_inode: int
    transition: str
    state: str
    operation_id: str | None
    plan_sha256: str | None
    attempt_outcome: str | None
    previous_record_sha256: str | None
    checkpoint: PublicJournalCheckpointV1
    checkpoint_sha256: str
    record_sha256: str

    @property
    def execution_authority(self) -> bool:
        return False


@dataclass(frozen=True, slots=True)
class CommittedWitnessObservation:
    """Server-clock sample after a separate-session committed chain read.

    The server clock and operator custody are not independently trusted here.
    A later read cannot reproduce this timestamp without a persisted observation.
    """

    revision: WitnessRevision
    observed_at: datetime
    trusted_clock_verified: bool = False
    independently_protected: bool = False
    predictive_oos_eligible: bool = False
    execution_authority: bool = False


def _record_hash(row: WitnessRevision, checkpoint_json: str) -> str:
    fields = (
        row.journal_key,
        str(row.revision),
        row.journal_id,
        str(row.root_device),
        str(row.root_inode),
        row.transition,
        row.state,
        row.operation_id or "",
        row.plan_sha256 or "",
        row.attempt_outcome or "",
        row.previous_record_sha256 or "",
        row.checkpoint_sha256,
        checkpoint_json,
    )
    return sha("\x1f".join(fields).encode("utf-8"))


def _decode_row(row) -> WitnessRevision:
    raw = row.checkpoint_json.encode("utf-8")
    try:
        checkpoint = PublicJournalCheckpointV1.model_validate_json(raw)
        checkpoint = checked(checkpoint, PublicJournalCheckpointV1)
    except Exception as error:
        raise PublicWitnessError("witness_checkpoint_invalid") from error
    if checkpoint.canonical_bytes() != raw or sha(raw) != row.checkpoint_sha256:
        raise PublicWitnessError("witness_checkpoint_noncanonical")
    value = WitnessRevision(
        journal_key=row.journal_key,
        revision=row.revision,
        journal_id=row.journal_id,
        root_device=row.root_device,
        root_inode=row.root_inode,
        transition=row.transition,
        state=row.state,
        operation_id=row.operation_id,
        plan_sha256=row.plan_sha256,
        attempt_outcome=row.attempt_outcome,
        previous_record_sha256=row.previous_record_sha256,
        checkpoint=checkpoint,
        checkpoint_sha256=row.checkpoint_sha256,
        record_sha256=row.record_sha256,
    )
    if (
        checkpoint.genesis_sha256 != value.journal_key
        or (checkpoint.root_device, checkpoint.root_inode)
        != (value.root_device, value.root_inode)
        or _HEX64.fullmatch(value.journal_key) is None
        or _HEX32.fullmatch(value.journal_id) is None
        or _HEX64.fullmatch(value.record_sha256) is None
        or _record_hash(value, row.checkpoint_json) != value.record_sha256
    ):
        raise PublicWitnessError("witness_record_invalid")
    return value


def verify_witness_chain(rows: tuple):
    """Replay all revisions; an isolated latest-row hash is not enough."""
    if not rows or len(rows) > 8193:
        raise PublicWitnessError("witness_chain_length_invalid")
    prior = None
    seen: tuple[WitnessRevision, ...] = ()
    for index, row in enumerate(rows):
        item = _decode_row(row)
        if item.revision != index or item.transition not in _TRANSITIONS:
            raise PublicWitnessError("witness_revision_gap")
        if prior is None:
            checkpoint = item.checkpoint
            if (
                item.transition != "initialize"
                or item.state != "idle"
                or item.operation_id is not None
                or item.plan_sha256 is not None
                or item.attempt_outcome is not None
                or item.previous_record_sha256 is not None
                or checkpoint.sequence != 0
                or checkpoint.attempt_sequence != 0
                or checkpoint.head_sha256 != item.journal_key
                or checkpoint.attempt_head_sha256 != item.journal_key
            ):
                raise PublicWitnessError("witness_genesis_invalid")
        elif (
            item.journal_key != prior.journal_key
            or item.journal_id != prior.journal_id
            or (item.root_device, item.root_inode)
            != (prior.root_device, prior.root_inode)
            or item.previous_record_sha256 != prior.record_sha256
        ):
            raise PublicWitnessError("witness_chain_changed")
        else:
            before, after = prior.checkpoint, item.checkpoint
            same = after == before
            same_operation = (
                item.operation_id == prior.operation_id
                and item.plan_sha256 == prior.plan_sha256
            )
            if item.operation_id is None or item.plan_sha256 is None:
                raise PublicWitnessError("witness_operation_missing")
            if item.transition == "open_attempt":
                valid = (
                    prior.state == "idle"
                    and item.state == "open"
                    and item.attempt_outcome is None
                    and same
                    and not any(
                        old.transition == "open_attempt"
                        and old.operation_id == item.operation_id
                        for old in seen
                    )
                )
            elif item.transition == "append_attempt":
                valid = (
                    prior.state == "open"
                    and item.state == "attempt_anchored"
                    and same_operation
                    and item.attempt_outcome
                    in ("completed_collection", "rejected", "incomplete")
                    and after.sequence == before.sequence
                    and after.head_sha256 == before.head_sha256
                    and after.attempt_sequence == before.attempt_sequence + 1
                    and after.attempt_head_sha256 != before.attempt_head_sha256
                )
            elif item.transition == "append_capture":
                valid = (
                    prior.state == "attempt_anchored"
                    and prior.attempt_outcome == "completed_collection"
                    and item.state == "idle"
                    and same_operation
                    and item.attempt_outcome == prior.attempt_outcome
                    and after.sequence == before.sequence + 1
                    and after.head_sha256 != before.head_sha256
                    and after.attempt_sequence == before.attempt_sequence
                    and after.attempt_head_sha256 == before.attempt_head_sha256
                )
            elif item.transition == "close_rejected_attempt":
                valid = (
                    prior.state == "attempt_anchored"
                    and prior.attempt_outcome in ("rejected", "incomplete")
                    and item.state == "idle"
                    and same_operation
                    and item.attempt_outcome == prior.attempt_outcome
                    and same
                )
            else:
                valid = False
            if not valid:
                raise PublicWitnessError("witness_transition_invalid")
        prior = item
        seen = (*seen, item) if index else (item,)
    return prior


class PublicReceiptWitnessRepository:
    """No settings/global DB; caller must supply a separately restricted DSN."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self.session_factory = session_factory

    async def verify_role(self):
        """Probe the effective login before a new journal root is created."""
        try:
            async with self.session_factory() as session:
                await self._role_guard(session)
        except PublicWitnessError:
            raise
        except Exception:  # noqa: BLE001 - Never include connection details.
            raise PublicWitnessError("witness_role_unavailable") from None

    @staticmethod
    async def _role_guard(session: AsyncSession):
        row = (
            await session.execute(
                text("""
                  SELECT session_user=current_user AS direct_login,
                    r.rolsuper OR r.rolcreatedb OR r.rolcreaterole
                      OR r.rolreplication OR r.rolbypassrls AS privileged,
                    pg_has_role(current_user,
                      pg_get_userbyid(c.relowner),'MEMBER') AS owner_member,
                    has_table_privilege(current_user,c.oid,'SELECT')
                      OR has_table_privilege(current_user,c.oid,'INSERT')
                      OR has_table_privilege(current_user,c.oid,'UPDATE')
                      OR has_table_privilege(current_user,c.oid,'DELETE')
                      OR has_table_privilege(current_user,c.oid,'TRUNCATE')
                      OR has_table_privilege(current_user,c.oid,'REFERENCES')
                      OR has_table_privilege(current_user,c.oid,'TRIGGER')
                      OR has_table_privilege(current_user,c.oid,'MAINTAIN')
                      OR has_any_column_privilege(current_user,c.oid,'SELECT')
                      OR has_any_column_privilege(current_user,c.oid,'INSERT')
                      OR has_any_column_privilege(current_user,c.oid,'UPDATE')
                      OR has_any_column_privilege(current_user,c.oid,'REFERENCES')
                      AS direct_table_access,
                    EXISTS (
                      SELECT 1 FROM pg_catalog.pg_roles inherited
                      WHERE inherited.oid <> r.oid
                        AND pg_catalog.pg_has_role(
                          current_user,inherited.oid,'MEMBER')
                    ) AS member_of_other_role,
                    has_function_privilege(current_user,
                      'public.public_receipt_witness_read(text)'::regprocedure,
                      'EXECUTE') AS can_read,
                    has_function_privilege(current_user,
                      'public.public_receipt_witness_append(jsonb)'::regprocedure,
                      'EXECUTE') AS can_append,
                    has_schema_privilege(current_user,'public','CREATE')
                      OR has_database_privilege(current_user,current_database(),
                        'CREATE') AS can_create
                  FROM pg_catalog.pg_roles r, pg_catalog.pg_class c
                  WHERE r.rolname=current_user
                    AND c.oid='public.public_receipt_witness_revisions'::regclass
                """)
            )
        ).one_or_none()
        if row is None or not (
            row.direct_login
            and not row.privileged
            and not row.owner_member
            and not row.member_of_other_role
            and row.can_read
            and row.can_append
            and not row.direct_table_access
            and not row.can_create
        ):
            raise PublicWitnessError("restricted_witness_role_required")

    async def read_latest(self, journal_key: str):
        if type(journal_key) is not str or _HEX64.fullmatch(journal_key) is None:
            raise PublicWitnessError("witness_journal_key_invalid")
        try:
            async with self.session_factory() as session:
                await self._role_guard(session)
                rows = tuple(
                    (
                        await session.execute(
                            text(
                                "SELECT * FROM public.public_receipt_witness_read(:key)"
                            ),
                            {"key": journal_key},
                        )
                    ).all()
                )
            return None if not rows else verify_witness_chain(rows)
        except PublicWitnessError:
            raise
        except Exception:  # noqa: BLE001 - Never leak connection/credential details.
            raise PublicWitnessError("witness_read_unavailable") from None

    async def read_revision(self, journal_key: str, revision: int) -> WitnessRevision:
        """Verify the complete current chain before selecting an old revision."""
        if (
            type(journal_key) is not str
            or _HEX64.fullmatch(journal_key) is None
            or type(revision) is not int
            or not 0 <= revision <= 8192
        ):
            raise PublicWitnessError("witness_revision_input_invalid")
        try:
            async with self.session_factory() as session:
                await self._role_guard(session)
                rows = tuple(
                    (
                        await session.execute(
                            text(
                                "SELECT * FROM public.public_receipt_witness_read(:key)"
                            ),
                            {"key": journal_key},
                        )
                    ).all()
                )
            if not rows:
                raise PublicWitnessError("witness_revision_missing")
            latest = verify_witness_chain(rows)
            if revision > latest.revision:
                raise PublicWitnessError("witness_revision_missing")
            return verify_witness_chain(rows[: revision + 1])
        except PublicWitnessError:
            raise
        except Exception:  # noqa: BLE001 - Never leak connection/credential details.
            raise PublicWitnessError("witness_read_unavailable") from None

    async def observe_committed_revision(
        self, journal_key: str, revision: int
    ) -> CommittedWitnessObservation:
        """Read a committed revision, then sample the database server clock.

        The caller must invoke this after capture publication. The selected
        revision is visible through the restricted read function in a fresh
        session; `clock_timestamp()` is sampled only after the full read and
        chain replay. A witness INSERT timestamp would not prove commit.
        """
        if (
            type(journal_key) is not str
            or _HEX64.fullmatch(journal_key) is None
            or type(revision) is not int
            or not 0 <= revision <= 8192
        ):
            raise PublicWitnessError("witness_revision_input_invalid")
        try:
            async with self.session_factory() as session:
                await session.execute(text("SET TRANSACTION READ ONLY"))
                await self._role_guard(session)
                rows = tuple(
                    (
                        await session.execute(
                            text(
                                "SELECT * FROM public.public_receipt_witness_read(:key)"
                            ),
                            {"key": journal_key},
                        )
                    ).all()
                )
                if not rows:
                    raise PublicWitnessError("witness_revision_missing")
                latest = verify_witness_chain(rows)
                if revision > latest.revision:
                    raise PublicWitnessError("witness_revision_missing")
                selected = verify_witness_chain(rows[: revision + 1])
                observed = (
                    await session.execute(text("SELECT clock_timestamp()"))
                ).scalar_one()
            if type(observed) is not datetime or observed.tzinfo is None:
                raise PublicWitnessError("witness_server_clock_invalid")
            return CommittedWitnessObservation(
                revision=selected, observed_at=observed.astimezone(UTC)
            )
        except PublicWitnessError:
            raise
        except Exception:  # noqa: BLE001 - Never leak connection/credential details.
            raise PublicWitnessError("witness_observation_unavailable") from None

    async def append(
        self,
        *,
        journal_id: str,
        checkpoint: PublicJournalCheckpointV1,
        transition: str,
        expected: WitnessRevision | None,
        operation_id: str | None = None,
        plan_sha256: str | None = None,
        attempt_outcome: str | None = None,
    ) -> WitnessRevision:
        checkpoint = checked(checkpoint, PublicJournalCheckpointV1)
        if (
            type(journal_id) is not str
            or _HEX32.fullmatch(journal_id) is None
            or type(transition) is not str
            or transition not in _TRANSITIONS
            or (
                operation_id is not None
                and (
                    type(operation_id) is not str
                    or _HEX32.fullmatch(operation_id) is None
                )
            )
            or (
                plan_sha256 is not None
                and (
                    type(plan_sha256) is not str
                    or _HEX64.fullmatch(plan_sha256) is None
                )
            )
            or (expected is not None and type(expected) is not WitnessRevision)
        ):
            raise PublicWitnessError("witness_append_input_invalid")
        raw = checkpoint.canonical_bytes()
        record = {
            "journal_key": checkpoint.genesis_sha256,
            "revision": 0 if expected is None else expected.revision + 1,
            "journal_id": journal_id,
            "root_device": checkpoint.root_device,
            "root_inode": checkpoint.root_inode,
            "transition": transition,
            "state": {
                "initialize": "idle",
                "open_attempt": "open",
                "append_attempt": "attempt_anchored",
                "append_capture": "idle",
                "close_rejected_attempt": "idle",
            }[transition],
            "operation_id": operation_id,
            "plan_sha256": plan_sha256,
            "attempt_outcome": attempt_outcome,
            "previous_record_sha256": (
                None if expected is None else expected.record_sha256
            ),
            "checkpoint_json": raw.decode("utf-8"),
            "checkpoint_sha256": sha(raw),
        }
        try:
            async with self.session_factory() as session, session.begin():
                await self._role_guard(session)
                await session.execute(
                    text(
                        "SELECT public.public_receipt_witness_append("
                        "CAST(:record AS jsonb))"
                    ),
                    {
                        "record": json.dumps(
                            record, sort_keys=True, separators=(",", ":")
                        )
                    },
                )
            # A new session/transaction reads after commit. The pool may reuse
            # the physical connection; uncertainty never triggers re-append.
            actual = await self.read_latest(checkpoint.genesis_sha256)
            if (
                actual is None
                or actual.revision != (0 if expected is None else expected.revision + 1)
                or actual.checkpoint != checkpoint
                or actual.transition != transition
                or actual.operation_id != operation_id
                or actual.plan_sha256 != plan_sha256
                or actual.attempt_outcome != attempt_outcome
            ):
                raise PublicWitnessError("witness_commit_readback_mismatch")
            return actual
        except PublicWitnessError:
            raise
        except Exception:  # noqa: BLE001 - Commit may have occurred; redact details.
            raise PublicWitnessError("witness_commit_uncertain") from None
