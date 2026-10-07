"""Read, fully replay, then ACK a separately committed Gate 3 seal.

The ACK credential belongs only to this independently administered observer.
Raw SQL use of that credential bypasses Python validation and is therefore a
custody failure, not a predictive or execution authorization path.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.mie.validation.artifact import verify_prospective_preregistration
from app.mie.validation.contracts import Gate3Claim
from app.mie.validation.prospective import Gate3ProspectivePreregistration
from app.mie.validation.prospective_capture_schedule import ProspectiveCoordinatePlanV1

_SHA256 = re.compile(r"[a-f0-9]{64}\Z")
MAX_SEAL_BYTES = 1_048_576


class Gate3CommittedSealError(ValueError):
    """A redacted, fail-closed seal observer result."""


@dataclass(frozen=True, slots=True)
class Gate3CommittedSealReadback:
    seal_sha256: str
    coordinate_plan_sha256: str
    window_key: str
    seal_recorded_at: datetime
    database_readback_at: datetime
    seal: Gate3ProspectivePreregistration
    current_claim: Gate3Claim = field(default=Gate3Claim.COMPUTATIONAL, init=False)
    predictive_oos_eligible: bool = field(default=False, init=False)
    execution_authority: bool = field(default=False, init=False)


@dataclass(frozen=True, slots=True)
class Gate3CommittedSealAckReadback:
    seal_sha256: str
    seal_recorded_at: datetime
    acknowledged_at: datetime
    database_readback_at: datetime
    current_claim: Gate3Claim = field(default=Gate3Claim.COMPUTATIONAL, init=False)
    predictive_oos_eligible: bool = field(default=False, init=False)
    execution_authority: bool = field(default=False, init=False)


class Gate3PreregistrationSealObserver:
    """Uses one restricted observer login and fresh sessions for each stage."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self.session_factory = session_factory

    @staticmethod
    def _validate_inputs(
        *,
        expected_seal_sha256: str,
        expected_coordinate_plan_sha256: str,
        coordinate_plan: ProspectiveCoordinatePlanV1,
    ) -> ProspectiveCoordinatePlanV1:
        if (
            type(expected_seal_sha256) is not str
            or _SHA256.fullmatch(expected_seal_sha256) is None
            or type(expected_coordinate_plan_sha256) is not str
            or _SHA256.fullmatch(expected_coordinate_plan_sha256) is None
            or type(coordinate_plan) is not ProspectiveCoordinatePlanV1
        ):
            raise Gate3CommittedSealError("prereg_seal_input_invalid")
        try:
            checked = ProspectiveCoordinatePlanV1.model_validate(
                coordinate_plan.model_dump(mode="python")
            )
        except Exception:  # noqa: BLE001 - Input details are not evidence.
            raise Gate3CommittedSealError("prereg_seal_input_invalid") from None
        if checked.canonical_sha256() != expected_coordinate_plan_sha256:
            raise Gate3CommittedSealError("prereg_seal_coordinate_pin_mismatch")
        return checked

    @staticmethod
    async def _require_observer_role(session: AsyncSession) -> None:
        allowed = await session.scalar(
            text("SELECT public.gate3_prereg_direct_role_allowed('seal_ack')")
        )
        if allowed is not True:
            raise Gate3CommittedSealError("prereg_seal_observer_role_required")

    async def read_seal(
        self,
        *,
        expected_seal_sha256: str,
        expected_coordinate_plan_sha256: str,
        coordinate_plan: ProspectiveCoordinatePlanV1,
    ) -> Gate3CommittedSealReadback:
        coordinate = self._validate_inputs(
            expected_seal_sha256=expected_seal_sha256,
            expected_coordinate_plan_sha256=expected_coordinate_plan_sha256,
            coordinate_plan=coordinate_plan,
        )
        try:
            async with self.session_factory() as session:
                await self._require_observer_role(session)
                rows = tuple(
                    (
                        await session.execute(
                            text(
                                "SELECT seal.*,pg_catalog.clock_timestamp() "
                                "AS database_readback_at FROM "
                                "public.gate3_prereg_seal_read(:sha) AS seal"
                            ),
                            {"sha": expected_seal_sha256},
                        )
                    ).all()
                )
            if len(rows) != 1:
                raise Gate3CommittedSealError("prereg_seal_missing_or_duplicate")
            row = rows[0]
            raw = row.seal_json.encode("utf-8")
            if not 1 <= len(raw) <= MAX_SEAL_BYTES:
                raise Gate3CommittedSealError("prereg_seal_payload_invalid")
            seal = verify_prospective_preregistration(
                raw, expected_sha256=expected_seal_sha256
            )
            window = seal.prospective_holdout
            times = (
                row.created_at,
                row.recorded_at,
                row.database_readback_at,
                row.window_start,
                row.window_end,
            )
            if any(
                value.tzinfo is None or value.utcoffset() is None for value in times
            ):
                raise Gate3CommittedSealError("prereg_seal_server_time_invalid")
            created_at, recorded_at, readback_at, start_at, end_at = (
                value.astimezone(UTC) for value in times
            )
            if (
                row.seal_sha256 != expected_seal_sha256
                or row.preregistration_id != seal.preregistration_id
                or row.coordinate_plan_sha256 != expected_coordinate_plan_sha256
                or row.window_key != coordinate.window_key()
                or row.holdout_id != coordinate.holdout_id
                or start_at != coordinate.start_at
                or end_at != coordinate.end_at
                or created_at != seal.created_at
                or window.coordinate_plan_sha256 != expected_coordinate_plan_sha256
                or window.holdout_id != coordinate.holdout_id
                or window.source != coordinate.source
                or window.source_version != coordinate.source_version
                or window.instrument_ids != coordinate.instrument_ids
                or window.start_at != coordinate.start_at
                or window.end_at != coordinate.end_at
                or window.bar_interval_seconds != 60
                or window.artifact_interval_seconds != 60
                or window.expected_rows != coordinate.expected_rows
                or window.expected_artifact_count != coordinate.expected_rows
                or not seal.created_at <= recorded_at < start_at
                or readback_at < recorded_at
            ):
                raise Gate3CommittedSealError("prereg_seal_readback_mismatch")
            return Gate3CommittedSealReadback(
                seal_sha256=expected_seal_sha256,
                coordinate_plan_sha256=expected_coordinate_plan_sha256,
                window_key=coordinate.window_key(),
                seal_recorded_at=recorded_at,
                database_readback_at=readback_at,
                seal=seal,
            )
        except Gate3CommittedSealError:
            raise
        except Exception:  # noqa: BLE001 - No DB/contract internals in evidence.
            raise Gate3CommittedSealError("prereg_seal_read_unavailable") from None

    async def read_ack(
        self,
        *,
        expected_seal_sha256: str,
        expected_coordinate_plan_sha256: str,
        coordinate_plan: ProspectiveCoordinatePlanV1,
    ) -> Gate3CommittedSealAckReadback:
        observed = await self.read_seal(
            expected_seal_sha256=expected_seal_sha256,
            expected_coordinate_plan_sha256=expected_coordinate_plan_sha256,
            coordinate_plan=coordinate_plan,
        )
        coordinate = self._validate_inputs(
            expected_seal_sha256=expected_seal_sha256,
            expected_coordinate_plan_sha256=expected_coordinate_plan_sha256,
            coordinate_plan=coordinate_plan,
        )
        try:
            async with self.session_factory() as session:
                await self._require_observer_role(session)
                rows = tuple(
                    (
                        await session.execute(
                            text(
                                "SELECT ack.*,pg_catalog.clock_timestamp() "
                                "AS database_readback_at FROM "
                                "public.gate3_prereg_seal_ack_read(:sha) AS ack"
                            ),
                            {"sha": expected_seal_sha256},
                        )
                    ).all()
                )
            if len(rows) != 1:
                raise Gate3CommittedSealError("prereg_seal_ack_missing_or_duplicate")
            row = rows[0]
            times = (
                row.seal_recorded_at,
                row.acknowledged_at,
                row.database_readback_at,
                row.window_start,
                row.window_end,
            )
            if any(
                value.tzinfo is None or value.utcoffset() is None for value in times
            ):
                raise Gate3CommittedSealError("prereg_seal_ack_time_invalid")
            recorded_at, acknowledged_at, readback_at, start_at, end_at = (
                value.astimezone(UTC) for value in times
            )
            if (
                row.seal_sha256 != expected_seal_sha256
                or row.preregistration_id != observed.seal.preregistration_id
                or row.coordinate_plan_sha256 != expected_coordinate_plan_sha256
                or row.window_key != coordinate.window_key()
                or row.holdout_id != coordinate.holdout_id
                or start_at != coordinate.start_at
                or end_at != coordinate.end_at
                or recorded_at != observed.seal_recorded_at
                or not recorded_at <= acknowledged_at < start_at
                or readback_at < acknowledged_at
            ):
                raise Gate3CommittedSealError("prereg_seal_ack_readback_mismatch")
            return Gate3CommittedSealAckReadback(
                seal_sha256=expected_seal_sha256,
                seal_recorded_at=recorded_at,
                acknowledged_at=acknowledged_at,
                database_readback_at=readback_at,
            )
        except Gate3CommittedSealError:
            raise
        except Exception:  # noqa: BLE001 - No DB details in evidence.
            raise Gate3CommittedSealError("prereg_seal_ack_read_unavailable") from None

    async def acknowledge(
        self,
        *,
        expected_seal_sha256: str,
        expected_coordinate_plan_sha256: str,
        coordinate_plan: ProspectiveCoordinatePlanV1,
    ) -> Gate3CommittedSealAckReadback:
        # A different session already observed the committed exact seal bytes.
        observed = await self.read_seal(
            expected_seal_sha256=expected_seal_sha256,
            expected_coordinate_plan_sha256=expected_coordinate_plan_sha256,
            coordinate_plan=coordinate_plan,
        )
        if observed.database_readback_at >= observed.seal.prospective_holdout.start_at:
            raise Gate3CommittedSealError("prereg_seal_publication_readback_late")
        inserted = False
        try:
            async with self.session_factory() as session, session.begin():
                await self._require_observer_role(session)
                await session.execute(
                    text("SELECT public.gate3_prereg_seal_ack_append(:sha)"),
                    {"sha": expected_seal_sha256},
                )
                inserted = True
        except Exception:  # noqa: BLE001 - A commit may have succeeded.
            if not inserted:
                raise Gate3CommittedSealError("prereg_seal_ack_rejected") from None
            try:
                return await self.read_ack(
                    expected_seal_sha256=expected_seal_sha256,
                    expected_coordinate_plan_sha256=expected_coordinate_plan_sha256,
                    coordinate_plan=coordinate_plan,
                )
            except Gate3CommittedSealError:
                raise Gate3CommittedSealError(
                    "prereg_seal_ack_commit_unresolved"
                ) from None
        return await self.read_ack(
            expected_seal_sha256=expected_seal_sha256,
            expected_coordinate_plan_sha256=expected_coordinate_plan_sha256,
            coordinate_plan=coordinate_plan,
        )


__all__ = (
    "Gate3CommittedSealAckReadback",
    "Gate3CommittedSealError",
    "Gate3CommittedSealReadback",
    "Gate3PreregistrationSealObserver",
)
