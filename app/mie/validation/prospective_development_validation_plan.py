"""Finite, source-only coordinates for a future Development/Validation pilot.

The caller's planning time is not an independent pre-window timestamp. This
contract never seals a candidate, opens a holdout, or starts a collector.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.mie.validation.contracts import (
    Gate3Claim,
    Gate3Contract,
    Identifier,
    require_utc,
)
from app.public_market_source.public_market_receipts import (
    MINUTE_NS,
    PublicMinuteCapturePlanV1,
)

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_MINUTE = timedelta(minutes=1)
# One owned public journal permits at most 1,024 captures and attempts. Leave
# headroom for failed attempts; this pilot is not a sufficient research history.
MAX_PILOT_ROWS = 960
INSTRUMENTS = ("BTC-USDT-SWAP", "ETH-USDT-SWAP")


def _ns(value: datetime) -> int:
    delta = value - _EPOCH
    return (
        (delta.days * 86_400 + delta.seconds) * 1_000_000 + delta.microseconds
    ) * 1_000


class ProspectiveDevelopmentValidationPlanV1(Gate3Contract):
    """Deterministic two-symbol minute grid without candidate or OOS authority."""

    schema_version: Literal["ctcc.mie.gate3.prospective_devval_plan.v1"] = (
        "ctcc.mie.gate3.prospective_devval_plan.v1"
    )
    plan_id: Identifier
    planned_at: datetime
    planning_time_basis: Literal["caller_declared_unverified"] = (
        "caller_declared_unverified"
    )
    pre_window_commit_proven: Literal[False] = False
    source: Literal["okx.public_market_source"] = "okx.public_market_source"
    origin: Literal[
        "https://openapi.okx.com", "https://us.okx.com", "https://eea.okx.com"
    ]
    instrument_ids: tuple[str, str] = INSTRUMENTS
    development_start_at: datetime
    development_end_at: datetime
    validation_start_at: datetime
    validation_end_at: datetime
    expected_rows: int = Field(ge=4, le=MAX_PILOT_ROWS)
    maximum_observation_lag_seconds: Literal[60] = 60
    candidate_fitted: Literal[False] = False
    holdout_accessed: Literal[False] = False
    current_claim: Literal[Gate3Claim.COMPUTATIONAL] = Gate3Claim.COMPUTATIONAL
    predictive_oos_eligible: Literal[False] = False
    promotion_eligible: Literal[False] = False
    reference_only: Literal[True] = True
    runtime_consumers: Literal[0] = 0
    execution_authority: Literal[False] = False

    @field_validator(
        "planned_at",
        "development_start_at",
        "development_end_at",
        "validation_start_at",
        "validation_end_at",
    )
    @classmethod
    def utc(cls, value: datetime, info) -> datetime:
        return require_utc(value, info.field_name)

    @model_validator(mode="after")
    def finite_disjoint_grid(self) -> ProspectiveDevelopmentValidationPlanV1:
        if self.instrument_ids != INSTRUMENTS:
            raise ValueError("devval pilot requires exact sorted BTC/ETH instruments")
        starts_ends = (
            self.development_start_at,
            self.development_end_at,
            self.validation_start_at,
            self.validation_end_at,
        )
        if any(_ns(value) % MINUTE_NS for value in starts_ends):
            raise ValueError("devval coordinates must be UTC minute aligned")
        if not (
            self.planned_at
            < self.development_start_at
            < self.development_end_at
            <= self.validation_start_at
            < self.validation_end_at
        ):
            raise ValueError("devval windows are not preplanned and disjoint")
        minutes = (self.development_end_at - self.development_start_at) // _MINUTE + (
            self.validation_end_at - self.validation_start_at
        ) // _MINUTE
        if self.expected_rows != minutes * len(INSTRUMENTS):
            raise ValueError("devval row count differs from complete minute grid")
        return self

    def coordinate(self, ordinal: int) -> tuple[str, datetime, str]:
        """Partition, opening time, instrument in a fixed minute-major order."""
        if type(ordinal) is not int or not 0 <= ordinal < self.expected_rows:
            raise ValueError("devval ordinal is outside the preplanned grid")
        development_rows = (
            (self.development_end_at - self.development_start_at) // _MINUTE
        ) * len(INSTRUMENTS)
        if ordinal < development_rows:
            partition, start, local = "development", self.development_start_at, ordinal
        else:
            partition, start, local = (
                "validation",
                self.validation_start_at,
                ordinal - development_rows,
            )
        minute, symbol = divmod(local, len(INSTRUMENTS))
        return partition, start + minute * _MINUTE, INSTRUMENTS[symbol]

    def capture_plan(self, ordinal: int) -> PublicMinuteCapturePlanV1:
        """Exact input for the existing one-shot owned public source journal."""
        _, opening, instrument = self.coordinate(ordinal)
        start_ns = _ns(opening)
        return PublicMinuteCapturePlanV1(
            origin=self.origin,
            instrument_id=instrument,
            start_ns=start_ns,
            end_ns=start_ns + MINUTE_NS,
            expected_rows=1,
            created_ns=_ns(self.planned_at),
            page_size=1,
            max_pages=1,
        )


__all__ = ("MAX_PILOT_ROWS", "ProspectiveDevelopmentValidationPlanV1")
