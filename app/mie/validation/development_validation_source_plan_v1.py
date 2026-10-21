"""Finite source acquisition coordinates for Development and Validation only.

This portable plan fixes collection intent, not measured availability. In
particular, historical warmup may be planned after its candles close. Neither
``planned_at`` nor a capture plan's clock-policy name attests a host clock,
backdates availability, or grants the process-local authority to acquire data.
The native owner must separately claim each ordinal before its first await.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.mie.validation.contracts import (
    Gate3Contract,
    Identifier,
    Sha256,
    require_utc,
)
from app.public_market_source.public_attempt_journal import MAX_ATTEMPTS
from app.public_market_source.public_market_receipts import (
    PublicMinuteCapturePlanV1,
    checked,
)
from app.public_market_source.public_receipt_storage import MAX_CAPTURES

MAX_SOURCE_BATCH_COORDINATES = min(MAX_CAPTURES, MAX_ATTEMPTS)
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")


def _ns(value: datetime) -> int:
    delta = value - _EPOCH
    return (
        (delta.days * 86_400 + delta.seconds) * 1_000_000 + delta.microseconds
    ) * 1_000


class DevelopmentValidationSourceCoordinateV1(Gate3Contract):
    """One exact bounded native capture request at a fixed zero-based ordinal."""

    ordinal: int = Field(ge=0, lt=MAX_SOURCE_BATCH_COORDINATES)
    partition: Literal["development", "validation"]
    purpose: Literal["historical_warmup", "prospective_collection"]
    capture_plan: PublicMinuteCapturePlanV1
    capture_plan_sha256: Sha256

    @field_validator("capture_plan", mode="before")
    @classmethod
    def exact_capture_instance(cls, value):
        if isinstance(value, PublicMinuteCapturePlanV1):
            return checked(value, PublicMinuteCapturePlanV1)
        return value

    @model_validator(mode="after")
    def capture_identity(self) -> DevelopmentValidationSourceCoordinateV1:
        plan = checked(self.capture_plan, PublicMinuteCapturePlanV1)
        if self.capture_plan_sha256 != plan.canonical_sha256():
            raise ValueError("source coordinate capture plan digest differs")
        return self


class DevelopmentValidationSourcePlanV1(Gate3Contract):
    """Immutable finite batch schedule, with no holdout or trading authority.

    Tuple order is the acquisition order. Adjacent batches are permitted;
    overlapping event windows for the same instrument are rejected even when
    their declared partitions or purposes differ. No sorting, filling, or
    deduplication repairs a malformed caller plan.
    """

    schema_version: Literal["ctcc.mie.devval.source_plan.v1"] = (
        "ctcc.mie.devval.source_plan.v1"
    )
    collection_id: Identifier
    source: Literal["okx.public_market_source"] = "okx.public_market_source"
    source_version: Literal["okx.history_candles.nine_strings.v1"] = (
        "okx.history_candles.nine_strings.v1"
    )
    origin: Literal[
        "https://openapi.okx.com", "https://us.okx.com", "https://eea.okx.com"
    ]
    planned_at: datetime
    coordinates: tuple[DevelopmentValidationSourceCoordinateV1, ...] = Field(
        min_length=1, max_length=MAX_SOURCE_BATCH_COORDINATES
    )
    expected_rows: int = Field(ge=1, le=3000 * MAX_SOURCE_BATCH_COORDINATES)
    current_claim: Literal["computational"] = "computational"
    predictive_oos_eligible: Literal[False] = False
    native_capture_authority: Literal[False] = False
    candidate_seal_authority: Literal[False] = False
    holdout_access_authority: Literal[False] = False
    runtime_consumers: Literal[0] = 0
    execution_authority: Literal[False] = False

    @field_validator(
        "predictive_oos_eligible",
        "native_capture_authority",
        "candidate_seal_authority",
        "holdout_access_authority",
        "execution_authority",
        mode="before",
    )
    @classmethod
    def strict_authority(cls, value):
        if type(value) is not bool or value is not False:
            raise ValueError("source plan authority forbidden")
        return value

    @field_validator("runtime_consumers", mode="before")
    @classmethod
    def strict_consumers(cls, value):
        if type(value) is not int or value != 0:
            raise ValueError("source plan authority forbidden")
        return value

    @field_validator("planned_at")
    @classmethod
    def utc(cls, value: datetime) -> datetime:
        return require_utc(value, "planned_at")

    @field_validator("coordinates", mode="before")
    @classmethod
    def exact_coordinate_instances(cls, value, info):
        if info.mode == "json" and type(value) is list:
            value = tuple(value)
        if type(value) is tuple:
            for coordinate in value:
                if (
                    isinstance(coordinate, DevelopmentValidationSourceCoordinateV1)
                    and type(coordinate) is not DevelopmentValidationSourceCoordinateV1
                ):
                    raise ValueError("exact source coordinate required")
        return value

    @model_validator(mode="after")
    def finite_coordinates(self) -> DevelopmentValidationSourcePlanV1:
        planned_ns = _ns(self.planned_at)
        windows: dict[str, list[tuple[int, int]]] = {}
        rows = 0
        for ordinal, coordinate in enumerate(self.coordinates):
            if type(coordinate) is not DevelopmentValidationSourceCoordinateV1:
                raise ValueError("exact source coordinate required")
            plan = checked(coordinate.capture_plan, PublicMinuteCapturePlanV1)
            if (
                coordinate.ordinal != ordinal
                or coordinate.capture_plan_sha256 != plan.canonical_sha256()
                or plan.origin != self.origin
                or plan.source_parser != self.source_version
                or plan.created_ns != planned_ns
            ):
                raise ValueError("source plan coordinate identity differs")
            if (
                coordinate.purpose == "historical_warmup" and plan.end_ns > planned_ns
            ) or (
                coordinate.purpose == "prospective_collection"
                and plan.start_ns <= planned_ns
            ):
                raise ValueError("source plan purpose contradicts planning metadata")
            prior = windows.setdefault(plan.instrument_id, [])
            if any(plan.start_ns < end and start < plan.end_ns for start, end in prior):
                raise ValueError("source plan instrument windows overlap")
            prior.append((plan.start_ns, plan.end_ns))
            rows += plan.expected_rows
        if rows != self.expected_rows:
            raise ValueError("source plan expected row total differs")
        return self

    def coordinate(self, ordinal: int) -> DevelopmentValidationSourceCoordinateV1:
        """Read one fixed coordinate; this does not claim or consume it."""
        plan = checked_development_validation_source_plan(self)
        if type(ordinal) is not int or not 0 <= ordinal < len(plan.coordinates):
            raise ValueError("source plan ordinal invalid")
        return plan.coordinates[ordinal]

    def capture_plan(self, ordinal: int) -> PublicMinuteCapturePlanV1:
        """Return the revalidated exact request without minting native authority."""
        return self.coordinate(ordinal).capture_plan


def checked_development_validation_source_plan(
    plan: DevelopmentValidationSourcePlanV1,
    *,
    expected_sha256: str | None = None,
) -> DevelopmentValidationSourcePlanV1:
    """Replay immutable fields and optionally check a separately retained digest.

    A matching caller-supplied digest attests consistency only. The native owner
    must retain the initial digest and perform independent one-use admission.
    """
    if type(plan) is not DevelopmentValidationSourcePlanV1:
        raise ValueError("exact development validation source plan required")
    replayed = DevelopmentValidationSourcePlanV1.model_validate(
        dict(object.__getattribute__(plan, "__dict__"))
    )
    if expected_sha256 is not None and (
        type(expected_sha256) is not str
        or _DIGEST.fullmatch(expected_sha256) is None
        or replayed.canonical_sha256() != expected_sha256
    ):
        raise ValueError("source plan retained digest differs")
    return replayed
