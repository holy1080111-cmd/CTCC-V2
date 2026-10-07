"""Replayable observation that a canonical schedule pin was committed early.

The database acknowledgement role cannot append a 0026 pin. Its server time
therefore follows the separate pin transaction's commit. Both reads replay the
original canonical schedule bytes through the 0026 repository. This provides
no independent custody, predictive eligibility, or execution authority.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.mie.validation.gate3_capture_schedule_pin import (
    Gate3CaptureSchedulePinRepository,
    Gate3SchedulePinError,
)
from app.mie.validation.prospective import Gate3ProspectivePreregistration
from app.mie.validation.prospective_capture_schedule import ProspectiveCaptureScheduleV1

_SHA256 = re.compile(r"[a-f0-9]{64}\Z")


class Gate3SchedulePublicationAckError(ValueError):
    """Safe error codes only; never expose the connection or source bytes."""


@dataclass(frozen=True, slots=True)
class Gate3SchedulePublicationAckReadback:
    schedule_sha256: str
    seal_sha256: str
    coordinate_plan_sha256: str
    window_key: str
    holdout_id: str
    schedule_recorded_at: datetime
    acknowledged_at: datetime
    database_readback_at: datetime
    schedule: ProspectiveCaptureScheduleV1
    server_clock_ordered_committed_pin_observation: bool = field(
        default=True, init=False
    )
    trusted_clock_verified: bool = field(default=False, init=False)
    independently_protected: bool = field(default=False, init=False)
    predictive_oos_eligible: bool = field(default=False, init=False)
    execution_authority: bool = field(default=False, init=False)


class Gate3CaptureSchedulePublicationAckRepository:
    """Separate ACK login and canonical 0026 pin reader, each on its own pool."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        pin_repository: Gate3CaptureSchedulePinRepository,
    ):
        if type(pin_repository) is not Gate3CaptureSchedulePinRepository:
            raise Gate3SchedulePublicationAckError("schedule_pin_reader_required")
        self.session_factory = session_factory
        self.pin_repository = pin_repository

    @staticmethod
    async def _role_guard(session: AsyncSession) -> None:
        row = (
            await session.execute(
                text("""
                  SELECT session_user=current_user AS direct_login,
                    r.rolsuper OR r.rolcreatedb OR r.rolcreaterole
                      OR r.rolreplication OR r.rolbypassrls AS privileged,
                    pg_catalog.pg_has_role(current_user,
                      pg_catalog.pg_get_userbyid(p.relowner),'MEMBER')
                      OR pg_catalog.pg_has_role(current_user,
                        pg_catalog.pg_get_userbyid(a.relowner),'MEMBER') AS owner_member,
                    EXISTS (
                      SELECT 1 FROM pg_catalog.pg_roles other_role
                      WHERE other_role.oid <> r.oid
                        AND pg_catalog.pg_has_role(
                          current_user,other_role.oid,'MEMBER')
                    ) AS member_of_other_role,
                    pg_catalog.has_table_privilege(current_user,p.oid,
                      'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER,MAINTAIN')
                      OR pg_catalog.has_any_column_privilege(current_user,p.oid,
                        'SELECT,INSERT,UPDATE,REFERENCES')
                      OR pg_catalog.has_table_privilege(current_user,a.oid,
                        'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER,MAINTAIN')
                      OR pg_catalog.has_any_column_privilege(current_user,a.oid,
                        'SELECT,INSERT,UPDATE,REFERENCES')
                      OR pg_catalog.has_table_privilege(current_user,w.oid,
                        'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER,MAINTAIN')
                      OR pg_catalog.has_any_column_privilege(current_user,w.oid,
                        'SELECT,INSERT,UPDATE,REFERENCES') AS direct_table_access,
                    pg_catalog.has_function_privilege(current_user,
                      'public.gate3_capture_schedule_ack_append(text,text,text,text,text)'::pg_catalog.regprocedure,
                      'EXECUTE') AS can_append,
                    pg_catalog.has_function_privilege(current_user,
                      'public.gate3_capture_schedule_ack_read(text)'::pg_catalog.regprocedure,
                      'EXECUTE') AS can_read,
                    pg_catalog.has_function_privilege(current_user,
                      'public.gate3_capture_schedule_append(jsonb)'::pg_catalog.regprocedure,
                      'EXECUTE') AS can_pin_append,
                    pg_catalog.has_function_privilege(current_user,
                      'public.public_receipt_witness_append(jsonb)'::pg_catalog.regprocedure,
                      'EXECUTE')
                      OR pg_catalog.has_function_privilege(current_user,
                        'public.public_receipt_witness_read(text)'::pg_catalog.regprocedure,
                        'EXECUTE') AS can_witness,
                    EXISTS (
                      SELECT 1 FROM pg_catalog.pg_namespace n
                      WHERE n.nspname !~ '^pg_(temp|toast_temp)_[0-9]+$'
                        AND pg_catalog.has_schema_privilege(
                          current_user,n.oid,'CREATE')
                    ) OR pg_catalog.has_database_privilege(current_user,
                        pg_catalog.current_database(),
                        'CREATE') AS can_create
                  FROM pg_catalog.pg_roles r, pg_catalog.pg_class p,
                       pg_catalog.pg_class a, pg_catalog.pg_class w
                  WHERE r.rolname=current_user
                    AND p.oid='public.gate3_capture_schedule_pins'::pg_catalog.regclass
                    AND a.oid='public.gate3_capture_schedule_publication_acks'::pg_catalog.regclass
                    AND w.oid='public.public_receipt_witness_revisions'::pg_catalog.regclass
                """)
            )
        ).one_or_none()
        if row is None or not (
            row.direct_login
            and not row.privileged
            and not row.owner_member
            and not row.member_of_other_role
            and not row.direct_table_access
            and row.can_append
            and row.can_read
            and not row.can_pin_append
            and not row.can_witness
            and not row.can_create
        ):
            raise Gate3SchedulePublicationAckError(
                "restricted_schedule_ack_role_required"
            )

    async def verify_role(self) -> None:
        try:
            async with self.session_factory() as session:
                await self._role_guard(session)
        except Gate3SchedulePublicationAckError:
            raise
        except Exception:  # noqa: BLE001 - Redact connection details.
            raise Gate3SchedulePublicationAckError(
                "schedule_ack_role_unavailable"
            ) from None

    async def read(
        self,
        *,
        expected_schedule_sha256: str,
        seal: Gate3ProspectivePreregistration,
    ) -> Gate3SchedulePublicationAckReadback:
        if (
            type(expected_schedule_sha256) is not str
            or _SHA256.fullmatch(expected_schedule_sha256) is None
            or type(seal) is not Gate3ProspectivePreregistration
        ):
            raise Gate3SchedulePublicationAckError("schedule_ack_input_invalid")
        try:
            # The 0026 reader rejects noncanonical but hash-valid raw pins.
            pin = await self.pin_repository.read(
                expected_schedule_sha256=expected_schedule_sha256, seal=seal
            )
        except Gate3SchedulePinError:
            raise Gate3SchedulePublicationAckError(
                "schedule_pin_replay_failed"
            ) from None
        try:
            async with self.session_factory() as session:
                await self._role_guard(session)
                rows = tuple(
                    (
                        await session.execute(
                            text(
                                "SELECT ack.*, pg_catalog.clock_timestamp() "
                                "AS database_readback_at FROM "
                                "public.gate3_capture_schedule_ack_read(:sha) AS ack"
                            ),
                            {"sha": expected_schedule_sha256},
                        )
                    ).all()
                )
            if len(rows) != 1:
                raise Gate3SchedulePublicationAckError(
                    "schedule_ack_missing_or_duplicate"
                )
            row = rows[0]
            for value in (
                row.schedule_recorded_at,
                row.acknowledged_at,
                row.database_readback_at,
            ):
                if value.tzinfo is None or value.utcoffset() is None:
                    raise Gate3SchedulePublicationAckError("schedule_ack_time_invalid")
            schedule_recorded_at = row.schedule_recorded_at.astimezone(UTC)
            acknowledged_at = row.acknowledged_at.astimezone(UTC)
            database_readback_at = row.database_readback_at.astimezone(UTC)
            coordinate = pin.schedule.coordinate_plan
            if (
                row.schedule_sha256 != pin.schedule_sha256
                or row.seal_sha256 != pin.seal_sha256
                or row.coordinate_plan_sha256 != pin.coordinate_plan_sha256
                or row.window_key != pin.window_key
                or row.holdout_id != coordinate.holdout_id
                or row.window_start != coordinate.start_at
                or row.window_end != coordinate.end_at
                or schedule_recorded_at != pin.recorded_at
                or not pin.recorded_at <= acknowledged_at < coordinate.start_at
                or database_readback_at < acknowledged_at
            ):
                raise Gate3SchedulePublicationAckError("schedule_ack_readback_mismatch")
            return Gate3SchedulePublicationAckReadback(
                schedule_sha256=pin.schedule_sha256,
                seal_sha256=pin.seal_sha256,
                coordinate_plan_sha256=pin.coordinate_plan_sha256,
                window_key=pin.window_key,
                holdout_id=coordinate.holdout_id,
                schedule_recorded_at=schedule_recorded_at,
                acknowledged_at=acknowledged_at,
                database_readback_at=database_readback_at,
                schedule=pin.schedule,
            )
        except Gate3SchedulePublicationAckError:
            raise
        except Exception:  # noqa: BLE001 - Redact rows and credentials.
            raise Gate3SchedulePublicationAckError(
                "schedule_ack_read_unavailable"
            ) from None

    async def acknowledge(
        self,
        *,
        expected_schedule_sha256: str,
        seal: Gate3ProspectivePreregistration,
    ) -> Gate3SchedulePublicationAckReadback:
        if (
            type(expected_schedule_sha256) is not str
            or _SHA256.fullmatch(expected_schedule_sha256) is None
            or type(seal) is not Gate3ProspectivePreregistration
        ):
            raise Gate3SchedulePublicationAckError("schedule_ack_input_invalid")
        # A retry may arrive after the window. An already durable exact ACK
        # remains valid evidence of the pin's earlier commit; never rewrite it.
        try:
            return await self.read(
                expected_schedule_sha256=expected_schedule_sha256, seal=seal
            )
        except Gate3SchedulePublicationAckError as exc:
            if str(exc) != "schedule_ack_missing_or_duplicate":
                raise
        try:
            pin = await self.pin_repository.read(
                expected_schedule_sha256=expected_schedule_sha256, seal=seal
            )
        except Gate3SchedulePinError:
            raise Gate3SchedulePublicationAckError(
                "schedule_pin_replay_failed"
            ) from None
        if pin.database_readback_at >= pin.schedule.coordinate_plan.start_at:
            raise Gate3SchedulePublicationAckError("schedule_ack_precheck_late")
        inserted = False
        try:
            async with self.session_factory() as session, session.begin():
                await self._role_guard(session)
                await session.execute(
                    text("""
                      SELECT public.gate3_capture_schedule_ack_append(
                        :schedule_sha,:seal_sha,:coordinate_sha,
                        :window_key,:holdout_id)
                    """),
                    {
                        "schedule_sha": pin.schedule_sha256,
                        "seal_sha": pin.seal_sha256,
                        "coordinate_sha": pin.coordinate_plan_sha256,
                        "window_key": pin.window_key,
                        "holdout_id": pin.schedule.coordinate_plan.holdout_id,
                    },
                )
                inserted = True
        except Gate3SchedulePublicationAckError:
            raise
        except Exception:  # noqa: BLE001 - Commit result may be uncertain.
            try:
                return await self.read(
                    expected_schedule_sha256=expected_schedule_sha256, seal=seal
                )
            except Gate3SchedulePublicationAckError:
                code = (
                    "schedule_ack_commit_unresolved"
                    if inserted
                    else "schedule_ack_rejected"
                )
                raise Gate3SchedulePublicationAckError(code) from None
        return await self.read(
            expected_schedule_sha256=expected_schedule_sha256, seal=seal
        )


__all__ = (
    "Gate3CaptureSchedulePublicationAckRepository",
    "Gate3SchedulePublicationAckError",
    "Gate3SchedulePublicationAckReadback",
)
