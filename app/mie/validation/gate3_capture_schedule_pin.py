"""Restricted-role, immutable PostgreSQL pin for pre-window Gate 3 plans.

No scheduler, market request, evaluator access, predictive or order authority.
Production use requires an independently administered database and credential.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.repositories.public_receipt_witness import (
    PublicReceiptWitnessRepository,
    PublicWitnessError,
)
from app.mie.validation.prospective import Gate3ProspectivePreregistration
from app.mie.validation.prospective_capture_schedule import (
    ProspectiveCaptureScheduleV1,
    checked_capture_schedule,
)

_SHA256 = re.compile(r"[a-f0-9]{64}\Z")
MAX_SCHEDULE_BYTES = 8_388_608


class Gate3SchedulePinError(ValueError):
    """Safe error codes only; never expose database credentials or rows."""


@dataclass(frozen=True, slots=True)
class Gate3SchedulePinReadback:
    schedule_sha256: str
    seal_sha256: str
    coordinate_plan_sha256: str
    window_key: str
    recorded_at: datetime
    database_readback_at: datetime
    schedule: ProspectiveCaptureScheduleV1
    independently_protected: bool = field(default=False, init=False)
    predictive_oos_eligible: bool = field(default=False, init=False)
    execution_authority: bool = field(default=False, init=False)


class Gate3CaptureSchedulePinRepository:
    """Function-only capture login, with a separate-session readback."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self.session_factory = session_factory

    @staticmethod
    def _timely_publication_readback(
        result: Gate3SchedulePinReadback,
    ) -> Gate3SchedulePinReadback:
        if result.database_readback_at >= result.schedule.coordinate_plan.start_at:
            # The immutable row remains for audit, but cannot be accepted as
            # pre-window publication after this second database observation.
            raise Gate3SchedulePinError("schedule_publication_readback_late")
        return result

    @staticmethod
    async def _role_guard(session: AsyncSession) -> None:
        try:
            await PublicReceiptWitnessRepository._role_guard(session)
            row = (
                await session.execute(
                    text("""
                      SELECT current_user=session_user AS direct_login,
                        has_table_privilege(current_user,
                          'public.gate3_capture_schedule_pins','SELECT')
                          OR has_table_privilege(current_user,
                            'public.gate3_capture_schedule_pins','INSERT')
                          OR has_table_privilege(current_user,
                            'public.gate3_capture_schedule_pins','UPDATE')
                          OR has_table_privilege(current_user,
                            'public.gate3_capture_schedule_pins','DELETE')
                          OR has_table_privilege(current_user,
                            'public.gate3_capture_schedule_pins','TRUNCATE')
                          OR has_table_privilege(current_user,
                            'public.gate3_capture_schedule_pins','REFERENCES')
                          OR has_table_privilege(current_user,
                            'public.gate3_capture_schedule_pins','TRIGGER')
                          OR has_table_privilege(current_user,
                            'public.gate3_capture_schedule_pins','MAINTAIN')
                          OR has_any_column_privilege(current_user,
                            'public.gate3_capture_schedule_pins','SELECT')
                          OR has_any_column_privilege(current_user,
                            'public.gate3_capture_schedule_pins','INSERT')
                          OR has_any_column_privilege(current_user,
                            'public.gate3_capture_schedule_pins','UPDATE')
                          OR has_any_column_privilege(current_user,
                            'public.gate3_capture_schedule_pins','REFERENCES')
                          AS direct_table_access,
                        has_function_privilege(current_user,
                          'public.gate3_capture_schedule_append(jsonb)'::regprocedure,
                          'EXECUTE') AS can_append,
                        has_function_privilege(current_user,
                          'public.gate3_capture_schedule_read(text)'::regprocedure,
                          'EXECUTE') AS can_read
                    """)
                )
            ).one_or_none()
            if row is None or not (
                row.direct_login
                and not row.direct_table_access
                and row.can_append
                and row.can_read
            ):
                raise Gate3SchedulePinError("restricted_schedule_role_required")
        except PublicWitnessError as exc:
            raise Gate3SchedulePinError("restricted_schedule_role_required") from exc

    async def verify_role(self) -> None:
        try:
            async with self.session_factory() as session:
                await self._role_guard(session)
        except Gate3SchedulePinError:
            raise
        except Exception:  # noqa: BLE001 - Never expose connection details.
            raise Gate3SchedulePinError("schedule_role_unavailable") from None

    async def read(
        self,
        *,
        expected_schedule_sha256: str,
        seal: Gate3ProspectivePreregistration,
    ) -> Gate3SchedulePinReadback:
        if (
            type(expected_schedule_sha256) is not str
            or _SHA256.fullmatch(expected_schedule_sha256) is None
        ):
            raise Gate3SchedulePinError("schedule_pin_invalid")
        try:
            async with self.session_factory() as session:
                await self._role_guard(session)
                rows = tuple(
                    (
                        await session.execute(
                            text(
                                "SELECT pinned.*, pg_catalog.clock_timestamp() "
                                "AS database_readback_at FROM "
                                "public.gate3_capture_schedule_read(:sha) AS pinned"
                            ),
                            {"sha": expected_schedule_sha256},
                        )
                    ).all()
                )
            if len(rows) != 1:
                raise Gate3SchedulePinError("schedule_pin_missing_or_duplicate")
            row = rows[0]
            raw = row.schedule_json.encode("utf-8")
            coordinate_raw = row.coordinate_plan_json.encode("utf-8")
            schedule = ProspectiveCaptureScheduleV1.model_validate_json(raw)
            schedule = checked_capture_schedule(schedule, seal=seal)
            recorded_at = row.recorded_at
            database_readback_at = row.database_readback_at
            if (
                recorded_at.tzinfo is None
                or recorded_at.utcoffset() is None
                or database_readback_at.tzinfo is None
                or database_readback_at.utcoffset() is None
            ):
                raise Gate3SchedulePinError("schedule_server_time_invalid")
            recorded_at = recorded_at.astimezone(UTC)
            database_readback_at = database_readback_at.astimezone(UTC)
            coordinate = schedule.coordinate_plan
            if (
                raw != schedule.canonical_json_bytes()
                or coordinate_raw != coordinate.canonical_json_bytes()
                or row.schedule_sha256 != expected_schedule_sha256
                or row.schedule_sha256 != schedule.canonical_sha256()
                or row.seal_sha256 != schedule.preregistration_sha256
                or row.coordinate_plan_sha256 != schedule.coordinate_plan_sha256
                or row.window_key != coordinate.window_key()
                or row.holdout_id != coordinate.holdout_id
                or row.window_start != coordinate.start_at
                or row.window_end != coordinate.end_at
                or row.planned_at != schedule.planned_at
                or not schedule.planned_at <= recorded_at < coordinate.start_at
                or database_readback_at < recorded_at
            ):
                raise Gate3SchedulePinError("schedule_pin_readback_mismatch")
            return Gate3SchedulePinReadback(
                schedule_sha256=expected_schedule_sha256,
                seal_sha256=row.seal_sha256,
                coordinate_plan_sha256=row.coordinate_plan_sha256,
                window_key=row.window_key,
                recorded_at=recorded_at,
                database_readback_at=database_readback_at,
                schedule=schedule,
            )
        except Gate3SchedulePinError:
            raise
        except Exception:  # noqa: BLE001 - Never expose source or database rows.
            raise Gate3SchedulePinError("schedule_pin_read_unavailable") from None

    async def publish(
        self,
        *,
        schedule: ProspectiveCaptureScheduleV1,
        seal: Gate3ProspectivePreregistration,
    ) -> Gate3SchedulePinReadback:
        try:
            checked = checked_capture_schedule(schedule, seal=seal)
            raw = checked.canonical_json_bytes()
            if len(raw) > MAX_SCHEDULE_BYTES:
                raise Gate3SchedulePinError("schedule_payload_too_large")
            coordinate = checked.coordinate_plan
            coordinate_raw = coordinate.canonical_json_bytes()
            sha = checked.canonical_sha256()
            record = {
                "schedule_sha256": sha,
                "seal_sha256": checked.preregistration_sha256,
                "coordinate_plan_sha256": checked.coordinate_plan_sha256,
                "window_key": coordinate.window_key(),
                "holdout_id": coordinate.holdout_id,
                "window_start": coordinate.start_at.isoformat(),
                "window_end": coordinate.end_at.isoformat(),
                "planned_at": checked.planned_at.isoformat(),
                "coordinate_plan_json": coordinate_raw.decode("utf-8"),
                "schedule_json": raw.decode("utf-8"),
            }
        except Gate3SchedulePinError:
            raise
        except Exception:  # noqa: BLE001 - Redacted input boundary.
            raise Gate3SchedulePinError("schedule_input_invalid") from None
        inserted = False
        try:
            async with self.session_factory() as session, session.begin():
                await self._role_guard(session)
                await session.execute(
                    text(
                        "SELECT public.gate3_capture_schedule_append("
                        "CAST(:record AS jsonb))"
                    ),
                    {
                        "record": json.dumps(
                            record, separators=(",", ":"), sort_keys=True
                        )
                    },
                )
                inserted = True
        except Gate3SchedulePinError:
            raise
        except Exception:  # noqa: BLE001 - Commit may have succeeded; verify anew.
            if not inserted:
                raise Gate3SchedulePinError("schedule_publish_rejected") from None
            # The commit response may be uncertain. Only a new independent
            # session's exact row can resolve it; absent proof remains DENY.
            try:
                readback = await self.read(expected_schedule_sha256=sha, seal=seal)
            except Gate3SchedulePinError:
                raise Gate3SchedulePinError("schedule_commit_unresolved") from None
            return self._timely_publication_readback(readback)
        readback = await self.read(expected_schedule_sha256=sha, seal=seal)
        return self._timely_publication_readback(readback)


__all__ = (
    "Gate3CaptureSchedulePinRepository",
    "Gate3SchedulePinError",
    "Gate3SchedulePinReadback",
)
