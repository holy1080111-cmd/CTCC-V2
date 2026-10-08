"""Durable post-read clock sample for one committed public capture witness.

The restricted writer cannot append/read a witness or directly change this
table. PostgreSQL samples the clock after reading the exact witness row in its
insert guard. A separate session and a separate witness-chain replay validate
the committed result. These checks do not establish trusted time or Gate 3.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import ClassVar, Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.repositories.public_receipt_witness import (
    PublicReceiptWitnessRepository,
    PublicWitnessError,
    WitnessRevision,
)

_HEX32 = re.compile(r"[a-f0-9]{32}\Z")
_HEX64 = re.compile(r"[a-f0-9]{64}\Z")


class PublicReceiptPostReadError(ValueError):
    """Safe error code only; no raw rows, credentials, or database URLs."""


@dataclass(frozen=True, slots=True)
class PublicReceiptPostReadReadback:
    journal_key: str
    witness_revision: int
    witness_record_sha256: str
    checkpoint_sha256: str
    capture_sequence: int
    capture_head_sha256: str
    capture_id: str
    plan_sha256: str
    receipt_sha256: str
    source_rows_sha256: str
    v2_capture_sha256: str
    witness_recorded_at: datetime
    observed_at: datetime
    database_readback_at: datetime
    persisted_observation_replayable: ClassVar[Literal[True]] = True
    trusted_clock_verified: ClassVar[Literal[False]] = False
    independently_protected: ClassVar[Literal[False]] = False
    evaluator_first_read_proven: ClassVar[Literal[False]] = False
    predictive_oos_eligible: ClassVar[Literal[False]] = False
    execution_authority: ClassVar[Literal[False]] = False


def _check_pins(
    *,
    journal_key: str,
    witness_revision: int,
    witness_record_sha256: str,
    checkpoint_sha256: str,
    capture_id: str,
    plan_sha256: str,
    receipt_sha256: str,
    source_rows_sha256: str,
    v2_capture_sha256: str,
) -> None:
    if (
        type(journal_key) is not str
        or type(witness_revision) is not int
        or not 3 <= witness_revision <= 8192
        or type(capture_id) is not str
        or _HEX32.fullmatch(capture_id) is None
        or any(
            type(item) is not str or _HEX64.fullmatch(item) is None
            for item in (
                journal_key,
                witness_record_sha256,
                checkpoint_sha256,
                plan_sha256,
                receipt_sha256,
                source_rows_sha256,
                v2_capture_sha256,
            )
        )
    ):
        raise PublicReceiptPostReadError("post_read_input_invalid")


def _check_not_before(value: datetime) -> None:
    if (
        type(value) is not datetime
        or value.tzinfo is None
        or value.utcoffset() is None
        or value.utcoffset().total_seconds() != 0
    ):
        raise PublicReceiptPostReadError("post_read_time_input_invalid")


class PublicReceiptPostReadRepository:
    """Requires a separate restricted observation login and witness reader."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        witness_repository: PublicReceiptWitnessRepository,
    ):
        if type(witness_repository) is not PublicReceiptWitnessRepository:
            raise PublicReceiptPostReadError("witness_reader_required")
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
                      WHERE target.oid IN (w.oid,o.oid)
                        AND (pg_catalog.pg_has_role(current_user,
                          pg_catalog.pg_get_userbyid(target.relowner),'MEMBER')
                          OR pg_catalog.has_table_privilege(current_user,
                            target.oid,
                            'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER,MAINTAIN')
                          OR pg_catalog.has_any_column_privilege(current_user,
                            target.oid,'SELECT,INSERT,UPDATE,REFERENCES')))
                    ) AS direct_table_access,
                    pg_catalog.has_function_privilege(current_user,
                      'public.public_receipt_post_read_append(text,bigint,text,text,text,text,text,text,text)'::pg_catalog.regprocedure,
                      'EXECUTE') AS can_append,
                    pg_catalog.has_function_privilege(current_user,
                      'public.public_receipt_post_read_read(text,bigint)'::pg_catalog.regprocedure,
                      'EXECUTE') AS can_read,
                    pg_catalog.has_function_privilege(current_user,
                      'public.public_receipt_witness_append(jsonb)'::pg_catalog.regprocedure,
                      'EXECUTE') OR pg_catalog.has_function_privilege(current_user,
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
                       pg_catalog.pg_class o
                  WHERE r.rolname=current_user
                    AND w.oid='public.public_receipt_witness_revisions'::pg_catalog.regclass
                    AND o.oid='public.public_receipt_post_read_observations'::pg_catalog.regclass
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
            raise PublicReceiptPostReadError("restricted_post_read_role_required")

    async def verify_role(self) -> None:
        try:
            async with self.session_factory() as session:
                await self._role_guard(session)
        except PublicReceiptPostReadError:
            raise
        except Exception:  # noqa: BLE001 - Redact connection data.
            raise PublicReceiptPostReadError("post_read_role_unavailable") from None

    async def _witness(
        self, journal_key: str, witness_revision: int
    ) -> WitnessRevision:
        try:
            witness = await self.witness_repository.read_revision(
                journal_key, witness_revision
            )
        except PublicWitnessError:
            raise PublicReceiptPostReadError(
                "post_read_witness_replay_failed"
            ) from None
        if (
            witness.transition != "append_capture"
            or witness.state != "idle"
            or witness.attempt_outcome != "completed_collection"
            or witness.checkpoint.sequence < 1
        ):
            raise PublicReceiptPostReadError("post_read_witness_invalid")
        return witness

    async def read(
        self,
        *,
        journal_key: str,
        witness_revision: int,
        witness_record_sha256: str,
        checkpoint_sha256: str,
        capture_id: str,
        plan_sha256: str,
        receipt_sha256: str,
        source_rows_sha256: str,
        v2_capture_sha256: str,
        not_before: datetime,
    ) -> PublicReceiptPostReadReadback:
        pins = {
            "journal_key": journal_key,
            "witness_revision": witness_revision,
            "witness_record_sha256": witness_record_sha256,
            "checkpoint_sha256": checkpoint_sha256,
            "capture_id": capture_id,
            "plan_sha256": plan_sha256,
            "receipt_sha256": receipt_sha256,
            "source_rows_sha256": source_rows_sha256,
            "v2_capture_sha256": v2_capture_sha256,
        }
        _check_pins(**pins)
        _check_not_before(not_before)
        witness = await self._witness(journal_key, witness_revision)
        if (
            witness.record_sha256 != witness_record_sha256
            or witness.checkpoint_sha256 != checkpoint_sha256
            or witness.plan_sha256 != plan_sha256
        ):
            raise PublicReceiptPostReadError("post_read_witness_pin_mismatch")
        try:
            async with self.session_factory() as session:
                await self._role_guard(session)
                rows = tuple(
                    (
                        await session.execute(
                            text("""
                              SELECT observation.*,
                                pg_catalog.clock_timestamp() AS database_readback_at
                              FROM public.public_receipt_post_read_read(
                                :key,:revision) AS observation
                            """),
                            {"key": journal_key, "revision": witness_revision},
                        )
                    ).all()
                )
            if len(rows) != 1:
                raise PublicReceiptPostReadError(
                    "post_read_missing" if not rows else "post_read_duplicate"
                )
            row = rows[0]
            times = (
                row.witness_recorded_at,
                row.observed_at,
                row.database_readback_at,
            )
            if any(
                type(value) is not datetime
                or value.tzinfo is None
                or value.utcoffset() is None
                for value in times
            ):
                raise PublicReceiptPostReadError("post_read_time_invalid")
            recorded, observed, readback = (value.astimezone(UTC) for value in times)
            if not (
                all(getattr(row, key) == value for key, value in pins.items())
                and row.capture_sequence == witness.checkpoint.sequence
                and row.capture_head_sha256 == witness.checkpoint.head_sha256
                and recorded <= not_before <= observed <= readback
            ):
                raise PublicReceiptPostReadError("post_read_readback_mismatch")
            return PublicReceiptPostReadReadback(
                **pins,
                capture_sequence=row.capture_sequence,
                capture_head_sha256=row.capture_head_sha256,
                witness_recorded_at=recorded,
                observed_at=observed,
                database_readback_at=readback,
            )
        except PublicReceiptPostReadError:
            raise
        except Exception:  # noqa: BLE001 - Redact connection data and rows.
            raise PublicReceiptPostReadError("post_read_unavailable") from None

    async def append_new(
        self,
        *,
        journal_key: str,
        witness_revision: int,
        witness_record_sha256: str,
        checkpoint_sha256: str,
        capture_id: str,
        plan_sha256: str,
        receipt_sha256: str,
        source_rows_sha256: str,
        v2_capture_sha256: str,
        not_before: datetime,
    ) -> PublicReceiptPostReadReadback:
        pins = {
            "journal_key": journal_key,
            "witness_revision": witness_revision,
            "witness_record_sha256": witness_record_sha256,
            "checkpoint_sha256": checkpoint_sha256,
            "capture_id": capture_id,
            "plan_sha256": plan_sha256,
            "receipt_sha256": receipt_sha256,
            "source_rows_sha256": source_rows_sha256,
            "v2_capture_sha256": v2_capture_sha256,
        }
        _check_pins(**pins)
        _check_not_before(not_before)
        witness = await self._witness(journal_key, witness_revision)
        if (
            witness.record_sha256 != witness_record_sha256
            or witness.checkpoint_sha256 != checkpoint_sha256
            or witness.plan_sha256 != plan_sha256
        ):
            raise PublicReceiptPostReadError("post_read_witness_pin_mismatch")
        attempted = False
        try:
            async with self.session_factory() as session, session.begin():
                await self._role_guard(session)
                attempted = True
                await session.execute(
                    text("""
                      SELECT public.public_receipt_post_read_append(
                        :journal_key,:witness_revision,:witness_record_sha256,
                        :checkpoint_sha256,:capture_id,:plan_sha256,
                        :receipt_sha256,:source_rows_sha256,:v2_capture_sha256)
                    """),
                    pins,
                )
        except Exception:  # noqa: BLE001 - Unknown commit never triggers retry.
            raise PublicReceiptPostReadError(
                "post_read_commit_uncertain" if attempted else "post_read_rejected"
            ) from None
        return await self.read(**pins, not_before=not_before)


__all__ = (
    "PublicReceiptPostReadError",
    "PublicReceiptPostReadReadback",
    "PublicReceiptPostReadRepository",
)
