"""Restricted PostgreSQL observation of a committed public capture witness.

An ACK login cannot append a witness. The SQL trigger reads an already
committed append_capture revision and records server time after that read.
The server samples ``acknowledged_at`` before the ACK transaction commits.
The separate-session readback clock is returned in memory, not persisted as a
historical decision-time receipt. Neither value may be used as a predictive
availability cutoff. This seam has no Gate 3 or execution authority.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from time import perf_counter_ns
from typing import ClassVar, Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.repositories.public_receipt_witness import (
    PublicReceiptWitnessRepository,
    PublicWitnessError,
    WitnessRevision,
)

_HEX64 = re.compile(r"[a-f0-9]{64}\Z")
_MAX_BRACKET_NS = 30_000_000_000
_MAX_CLOCK_DRIFT_NS = 5_000_000


class PublicReceiptPublicationAckError(ValueError):
    """Safe error code only; never include source bytes or database URLs."""


@dataclass(frozen=True, slots=True)
class PublicReceiptPublicationAckReadback:
    journal_key: str
    witness_revision: int
    witness_record_sha256: str
    checkpoint_sha256: str
    capture_sequence: int
    capture_head_sha256: str
    witness_recorded_at: datetime
    acknowledged_at: datetime
    database_readback_at: datetime
    host_readback_clock_bracket_verified: bool
    host_ack_clock_bracket_verified: bool = False
    historical_decision_availability_verified: ClassVar[Literal[False]] = False
    trusted_clock_verified: ClassVar[Literal[False]] = False
    independently_protected: ClassVar[Literal[False]] = False
    predictive_oos_eligible: ClassVar[Literal[False]] = False
    execution_authority: ClassVar[Literal[False]] = False


def _checked_identity(journal_key: str, revision: int) -> None:
    if (
        type(journal_key) is not str
        or _HEX64.fullmatch(journal_key) is None
        or type(revision) is not int
        or not 1 <= revision <= 8192
    ):
        raise PublicReceiptPublicationAckError("publication_ack_input_invalid")


def _clock_sample() -> tuple[datetime, int]:
    return datetime.now(UTC), perf_counter_ns()


def _bracketed_server_time(
    *,
    before: tuple[datetime, int],
    after: tuple[datetime, int],
    server_time: datetime,
) -> bool:
    if (
        type(server_time) is not datetime
        or server_time.tzinfo is None
        or server_time.utcoffset() is None
    ):
        return False
    wall_ns = int((after[0] - before[0]).total_seconds() * 1_000_000_000)
    monotonic_ns = after[1] - before[1]
    return (
        0 <= monotonic_ns <= _MAX_BRACKET_NS
        and 0 <= wall_ns <= _MAX_BRACKET_NS
        and abs(wall_ns - monotonic_ns) <= _MAX_CLOCK_DRIFT_NS
        and before[0] <= server_time.astimezone(UTC) <= after[0]
    )


class PublicReceiptPublicationAckRepository:
    """Separate ACK login and separately restricted witness reader."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        witness_repository: PublicReceiptWitnessRepository,
    ):
        if type(witness_repository) is not PublicReceiptWitnessRepository:
            raise PublicReceiptPublicationAckError("witness_reader_required")
        self.session_factory = session_factory
        self.witness_repository = witness_repository

    @staticmethod
    async def _role_guard(session: AsyncSession) -> None:
        row = (
            await session.execute(
                text("""
                  SELECT session_user=current_user AS direct_login,
                    r.rolsuper OR r.rolcreatedb OR r.rolcreaterole
                      OR r.rolreplication OR r.rolbypassrls AS privileged,
                    EXISTS (
                      SELECT 1 FROM pg_catalog.pg_roles other_role
                      WHERE other_role.oid <> r.oid
                        AND pg_catalog.pg_has_role(current_user,
                          other_role.oid,'MEMBER')
                    ) AS member_of_other_role,
                    EXISTS (
                      SELECT 1 FROM pg_catalog.pg_class target
                      WHERE target.oid IN (w.oid,a.oid)
                        AND (pg_catalog.pg_has_role(current_user,
                          pg_catalog.pg_get_userbyid(target.relowner),'MEMBER')
                          OR pg_catalog.has_table_privilege(current_user,
                            target.oid,
                            'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER,MAINTAIN')
                          OR pg_catalog.has_any_column_privilege(current_user,
                            target.oid,'SELECT,INSERT,UPDATE,REFERENCES'))
                    ) AS direct_table_access,
                    pg_catalog.has_function_privilege(current_user,
                      'public.public_receipt_publication_ack_append(text,bigint,text,text)'::pg_catalog.regprocedure,
                      'EXECUTE') AS can_append,
                    pg_catalog.has_function_privilege(current_user,
                      'public.public_receipt_publication_ack_read(text,bigint)'::pg_catalog.regprocedure,
                      'EXECUTE') AS can_read,
                    pg_catalog.has_function_privilege(current_user,
                      'public.public_receipt_witness_append(jsonb)'::pg_catalog.regprocedure,
                      'EXECUTE')
                      OR pg_catalog.has_function_privilege(current_user,
                        'public.public_receipt_witness_read(text)'::pg_catalog.regprocedure,
                        'EXECUTE') AS can_witness,
                    EXISTS (
                      SELECT 1 FROM pg_catalog.pg_namespace n
                      WHERE n.nspname !~ '^pg_(temp|toast_temp)_[0-9]+$'
                        AND pg_catalog.has_schema_privilege(current_user,
                          n.oid,'CREATE')
                    ) OR pg_catalog.has_database_privilege(current_user,
                        pg_catalog.current_database(),'CREATE') AS can_create
                  FROM pg_catalog.pg_roles r, pg_catalog.pg_class w,
                       pg_catalog.pg_class a
                  WHERE r.rolname=current_user
                    AND w.oid='public.public_receipt_witness_revisions'::pg_catalog.regclass
                    AND a.oid='public.public_receipt_publication_acks'::pg_catalog.regclass
                """)
            )
        ).one_or_none()
        if row is None or not (
            row.direct_login
            and not row.privileged
            and not row.member_of_other_role
            and not row.direct_table_access
            and row.can_append
            and row.can_read
            and not row.can_witness
            and not row.can_create
        ):
            raise PublicReceiptPublicationAckError(
                "restricted_publication_ack_role_required"
            )

    async def verify_role(self) -> None:
        try:
            async with self.session_factory() as session:
                await self._role_guard(session)
        except PublicReceiptPublicationAckError:
            raise
        except Exception:  # noqa: BLE001 - Redact connection details.
            raise PublicReceiptPublicationAckError(
                "publication_ack_role_unavailable"
            ) from None

    async def _witness(self, journal_key: str, revision: int) -> WitnessRevision:
        try:
            witness = await self.witness_repository.read_revision(journal_key, revision)
        except PublicWitnessError:
            raise PublicReceiptPublicationAckError(
                "publication_ack_witness_replay_failed"
            ) from None
        if (
            witness.transition != "append_capture"
            or witness.state != "idle"
            or witness.attempt_outcome != "completed_collection"
            or witness.checkpoint.sequence < 1
        ):
            raise PublicReceiptPublicationAckError("publication_ack_witness_invalid")
        return witness

    async def read(
        self, *, journal_key: str, witness_revision: int
    ) -> PublicReceiptPublicationAckReadback:
        _checked_identity(journal_key, witness_revision)
        witness = await self._witness(journal_key, witness_revision)
        try:
            before = _clock_sample()
            async with self.session_factory() as session:
                await self._role_guard(session)
                rows = tuple(
                    (
                        await session.execute(
                            text("""
                              SELECT ack.*,pg_catalog.clock_timestamp()
                                AS database_readback_at
                              FROM public.public_receipt_publication_ack_read(
                                :key,:revision) AS ack
                            """),
                            {"key": journal_key, "revision": witness_revision},
                        )
                    ).all()
                )
            after = _clock_sample()
            if not rows:
                raise PublicReceiptPublicationAckError("publication_ack_missing")
            if len(rows) != 1:
                raise PublicReceiptPublicationAckError("publication_ack_duplicate")
            row = rows[0]
            times = (
                row.witness_recorded_at,
                row.acknowledged_at,
                row.database_readback_at,
            )
            if any(
                type(value) is not datetime
                or value.tzinfo is None
                or value.utcoffset() is None
                for value in times
            ):
                raise PublicReceiptPublicationAckError("publication_ack_time_invalid")
            recorded, acknowledged, readback = (
                value.astimezone(UTC) for value in times
            )
            bracketed = _bracketed_server_time(
                before=before, after=after, server_time=readback
            )
            if not (
                row.journal_key == journal_key
                and row.witness_revision == witness_revision
                and row.witness_record_sha256 == witness.record_sha256
                and row.checkpoint_sha256 == witness.checkpoint_sha256
                and row.capture_sequence == witness.checkpoint.sequence
                and row.capture_head_sha256 == witness.checkpoint.head_sha256
                and recorded <= acknowledged <= readback
                and bracketed
            ):
                raise PublicReceiptPublicationAckError(
                    "publication_ack_readback_mismatch"
                )
            return PublicReceiptPublicationAckReadback(
                journal_key=journal_key,
                witness_revision=witness_revision,
                witness_record_sha256=witness.record_sha256,
                checkpoint_sha256=witness.checkpoint_sha256,
                capture_sequence=witness.checkpoint.sequence,
                capture_head_sha256=witness.checkpoint.head_sha256,
                witness_recorded_at=recorded,
                acknowledged_at=acknowledged,
                database_readback_at=readback,
                host_readback_clock_bracket_verified=True,
            )
        except PublicReceiptPublicationAckError:
            raise
        except Exception:  # noqa: BLE001 - Redact rows and credentials.
            raise PublicReceiptPublicationAckError(
                "publication_ack_read_unavailable"
            ) from None

    async def acknowledge(
        self, *, journal_key: str, witness_revision: int
    ) -> PublicReceiptPublicationAckReadback:
        _checked_identity(journal_key, witness_revision)
        try:
            return await self.read(
                journal_key=journal_key, witness_revision=witness_revision
            )
        except PublicReceiptPublicationAckError as error:
            if str(error) != "publication_ack_missing":
                raise
        witness = await self._witness(journal_key, witness_revision)
        inserted = False
        before = _clock_sample()
        try:
            async with self.session_factory() as session, session.begin():
                await self._role_guard(session)
                await session.execute(
                    text("""
                      SELECT public.public_receipt_publication_ack_append(
                        :key,:revision,:record_sha,:checkpoint_sha)
                    """),
                    {
                        "key": journal_key,
                        "revision": witness_revision,
                        "record_sha": witness.record_sha256,
                        "checkpoint_sha": witness.checkpoint_sha256,
                    },
                )
                inserted = True
            after = _clock_sample()
        except Exception:  # noqa: BLE001 - A commit may have succeeded.
            raise PublicReceiptPublicationAckError(
                "publication_ack_commit_uncertain"
                if inserted
                else "publication_ack_rejected"
            ) from None
        result = await self.read(
            journal_key=journal_key, witness_revision=witness_revision
        )
        if not _bracketed_server_time(
            before=before, after=after, server_time=result.acknowledged_at
        ):
            raise PublicReceiptPublicationAckError(
                "publication_ack_clock_order_unverified"
            )
        return replace(result, host_ack_clock_bracket_verified=True)


__all__ = (
    "PublicReceiptPublicationAckError",
    "PublicReceiptPublicationAckReadback",
    "PublicReceiptPublicationAckRepository",
)
