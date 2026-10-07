"""Pre-window public-minute coordinates and exact capture-plan schedule.

These contracts make preplanning checkable against an independently timestamped
pin.  They neither fetch market data nor promote a Gate 3 predictive claim.
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.mie.validation.contracts import (
    Gate3Claim,
    Gate3Contract,
    Identifier,
    Sha256,
    require_utc,
)
from app.mie.validation.prospective import Gate3ProspectivePreregistration
from app.public_market_source.public_market_receipts import (
    ORIGINS,
    PublicMinuteCapturePlanV1,
    checked,
)

MAX_SCHEDULE_ROWS = 4096
MINUTE = timedelta(minutes=1)
MINUTE_NS = 60_000_000_000
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_INSTRUMENT = re.compile(r"[A-Z0-9]{2,20}-USDT-SWAP\Z")


def _ns(value: datetime) -> int:
    delta = value - EPOCH
    return (
        (delta.days * 86_400 + delta.seconds) * 1_000_000 + delta.microseconds
    ) * 1_000


class ProspectiveCoordinatePlanV1(Gate3Contract):
    """Complete finite minute grid, materialized before the candidate seal."""

    schema_version: Literal["ctcc.mie.gate3.coordinate_plan.v1"] = (
        "ctcc.mie.gate3.coordinate_plan.v1"
    )
    holdout_id: Identifier
    source: Literal["okx.public_market_source"] = "okx.public_market_source"
    source_version: Literal["okx.history_candles.nine_strings.v1"] = (
        "okx.history_candles.nine_strings.v1"
    )
    origin: Literal[
        "https://openapi.okx.com", "https://us.okx.com", "https://eea.okx.com"
    ]
    instrument_ids: tuple[str, ...] = Field(min_length=1)
    start_at: datetime
    end_at: datetime
    interval_seconds: Literal[60] = 60
    expected_rows: int = Field(ge=1, le=MAX_SCHEDULE_ROWS)
    current_claim: Literal[Gate3Claim.COMPUTATIONAL] = Gate3Claim.COMPUTATIONAL
    predictive_oos_eligible: Literal[False] = False
    runtime_consumers: Literal[0] = 0
    execution_authority: Literal[False] = False

    @field_validator("start_at", "end_at")
    @classmethod
    def utc(cls, value: datetime, info) -> datetime:
        return require_utc(value, info.field_name)

    @model_validator(mode="after")
    def coordinates(self) -> ProspectiveCoordinatePlanV1:
        if (
            self.origin not in ORIGINS
            or self.instrument_ids != tuple(sorted(set(self.instrument_ids)))
            or any(_INSTRUMENT.fullmatch(i) is None for i in self.instrument_ids)
            or self.end_at <= self.start_at
            or _ns(self.start_at) % MINUTE_NS
            or _ns(self.end_at) % MINUTE_NS
            or (self.end_at - self.start_at) // MINUTE * len(self.instrument_ids)
            != self.expected_rows
        ):
            raise ValueError("prospective minute coordinate plan is invalid")
        return self

    def window_key(self) -> str:
        """One source/calendar window cannot be republished under new IDs."""
        fields = (
            self.source,
            "\x1f".join(self.instrument_ids),
            str(_ns(self.start_at) // 1_000),
            str(_ns(self.end_at) // 1_000),
        )
        return hashlib.sha256("\x1f".join(fields).encode("utf-8")).hexdigest()


class ProspectiveCaptureScheduleV1(Gate3Contract):
    """Exact plans committed after seal but before the first minute opens."""

    schema_version: Literal["ctcc.mie.gate3.capture_schedule.v1"] = (
        "ctcc.mie.gate3.capture_schedule.v1"
    )
    preregistration_sha256: Sha256
    coordinate_plan: ProspectiveCoordinatePlanV1
    coordinate_plan_sha256: Sha256
    planned_at: datetime
    plans: tuple[PublicMinuteCapturePlanV1, ...] = Field(
        min_length=1, max_length=MAX_SCHEDULE_ROWS
    )
    current_claim: Literal[Gate3Claim.COMPUTATIONAL] = Gate3Claim.COMPUTATIONAL
    predictive_oos_eligible: Literal[False] = False
    reference_only: Literal[True] = True
    runtime_consumers: Literal[0] = 0
    execution_authority: Literal[False] = False

    @field_validator("planned_at")
    @classmethod
    def utc(cls, value: datetime) -> datetime:
        return require_utc(value, "planned_at")

    @model_validator(mode="after")
    def complete_schedule(self) -> ProspectiveCaptureScheduleV1:
        coordinates = self.coordinate_plan
        if (
            type(coordinates) is not ProspectiveCoordinatePlanV1
            or self.coordinate_plan_sha256 != coordinates.canonical_sha256()
            or len(self.plans) != coordinates.expected_rows
            or self.planned_at >= coordinates.start_at
        ):
            raise ValueError("capture schedule coordinate identity differs")
        planned_ns = _ns(self.planned_at)
        for ordinal, source in enumerate(self.plans):
            plan = checked(source, PublicMinuteCapturePlanV1)
            minute, symbol = divmod(ordinal, len(coordinates.instrument_ids))
            expected_start_ns = _ns(coordinates.start_at + minute * MINUTE)
            if (
                plan.origin != coordinates.origin
                or plan.instrument_id != coordinates.instrument_ids[symbol]
                or plan.start_ns != expected_start_ns
                or plan.end_ns != expected_start_ns + MINUTE_NS
                or plan.expected_rows != 1
                or plan.created_ns != planned_ns
                or plan.source_parser != coordinates.source_version
            ):
                raise ValueError("capture schedule has missing or changed minute")
        return self


def checked_capture_schedule(
    schedule: ProspectiveCaptureScheduleV1,
    *,
    seal: Gate3ProspectivePreregistration,
) -> ProspectiveCaptureScheduleV1:
    """Revalidate nested bytes and bind the schedule to a separately pinned seal."""
    if (
        type(schedule) is not ProspectiveCaptureScheduleV1
        or type(seal) is not Gate3ProspectivePreregistration
    ):
        raise ValueError("exact prospective schedule and seal required")
    checked_seal = Gate3ProspectivePreregistration.model_validate(
        seal.model_dump(mode="python")
    )
    checked_schedule = ProspectiveCaptureScheduleV1.model_validate(
        schedule.model_dump(mode="python")
    )
    window = checked_seal.prospective_holdout
    coordinates = checked_schedule.coordinate_plan
    if (
        checked_schedule.preregistration_sha256 != checked_seal.canonical_sha256()
        or window.coordinate_plan_sha256 != coordinates.canonical_sha256()
        or window.holdout_id != coordinates.holdout_id
        or window.source != coordinates.source
        or window.source_version != coordinates.source_version
        or window.instrument_ids != coordinates.instrument_ids
        or window.start_at != coordinates.start_at
        or window.end_at != coordinates.end_at
        or window.bar_interval_seconds != 60
        or window.artifact_interval_seconds != 60
        or window.expected_rows != coordinates.expected_rows
        or window.expected_artifact_count != coordinates.expected_rows
        or not checked_seal.created_at < checked_schedule.planned_at < window.start_at
    ):
        raise ValueError("capture schedule differs from future-window seal")
    return checked_schedule


def build_capture_schedule(
    *,
    seal: Gate3ProspectivePreregistration,
    coordinate_plan: ProspectiveCoordinatePlanV1,
    planned_at: datetime,
) -> ProspectiveCaptureScheduleV1:
    """Build exact per-minute plans without network, credentials or order routes."""
    if type(coordinate_plan) is not ProspectiveCoordinatePlanV1:
        raise ValueError("exact coordinate plan required")
    require_utc(planned_at, "planned_at")
    created_ns = _ns(planned_at)
    plans = tuple(
        PublicMinuteCapturePlanV1(
            origin=coordinate_plan.origin,
            instrument_id=instrument,
            start_ns=_ns(coordinate_plan.start_at + minute * MINUTE),
            end_ns=_ns(coordinate_plan.start_at + (minute + 1) * MINUTE),
            expected_rows=1,
            created_ns=created_ns,
        )
        for minute in range(
            (coordinate_plan.end_at - coordinate_plan.start_at) // MINUTE
        )
        for instrument in coordinate_plan.instrument_ids
    )
    return checked_capture_schedule(
        ProspectiveCaptureScheduleV1(
            preregistration_sha256=seal.canonical_sha256(),
            coordinate_plan=coordinate_plan,
            coordinate_plan_sha256=coordinate_plan.canonical_sha256(),
            planned_at=planned_at,
            plans=plans,
        ),
        seal=seal,
    )
