from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal
from uuid import UUID, uuid4

from pydantic import Field, field_validator, model_validator

from app.mie.contracts._base import ForecastHorizon, MieContract, require_utc


class DecisionAction(StrEnum):
    LONG_CANDIDATE = "long_candidate"
    SHORT_CANDIDATE = "short_candidate"
    NO_TRADE = "no_trade"


class DecisionChecks(MieContract):
    probability_ok: bool
    ev_net_positive: bool
    risk_ok: bool
    uncertainty_ok: bool
    regime_compatible: bool
    data_fresh: bool
    model_health_ok: bool

    @property
    def all_passed(self) -> bool:
        return all(
            (
                self.probability_ok,
                self.ev_net_positive,
                self.risk_ok,
                self.uncertainty_ok,
                self.regime_compatible,
                self.data_fresh,
                self.model_health_ok,
            )
        )


class DecisionCandidate(MieContract):
    """Logic output only. It intentionally has no order geometry."""

    decision_id: UUID = Field(default_factory=uuid4)
    instrument_id: str = Field(
        min_length=3,
        max_length=64,
        pattern=r"^[A-Z0-9]+(?:-[A-Z0-9]+)+$",
    )
    horizon: ForecastHorizon
    as_of: datetime
    data_cutoff: datetime
    generated_at: datetime
    action: DecisionAction
    net_expected_value: Decimal
    checks: DecisionChecks
    forecast_id: UUID
    regime_snapshot_id: UUID
    evidence_ids: tuple[UUID, ...] = Field(min_length=1)
    model_health_ids: tuple[UUID, ...] = Field(min_length=1)
    reason_codes: tuple[str, ...] = ()
    logic_id: str = Field(
        min_length=1,
        max_length=160,
        pattern=r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$",
    )
    logic_version: str = Field(min_length=1, max_length=80)
    provenance_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    authority: Literal["shadow_only"] = "shadow_only"
    execution_authority: Literal[False] = False

    @field_validator("as_of", "data_cutoff", "generated_at")
    @classmethod
    def validate_timestamps(cls, value: datetime, info) -> datetime:
        return require_utc(value, info.field_name)

    @field_validator("reason_codes")
    @classmethod
    def validate_reason_codes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("decision reason codes must be unique")
        if any(not item.strip() for item in value):
            raise ValueError("decision reason codes cannot be blank")
        return value

    @model_validator(mode="after")
    def validate_decision_candidate(self) -> "DecisionCandidate":
        if self.data_cutoff > self.as_of:
            raise ValueError("decision cannot use data after as_of")
        if self.generated_at < self.as_of:
            raise ValueError("decision cannot be generated before as_of")
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("decision evidence ids must be unique")
        if len(self.model_health_ids) != len(set(self.model_health_ids)):
            raise ValueError("decision model health ids must be unique")
        if self.checks.ev_net_positive != (self.net_expected_value > 0):
            raise ValueError(
                "EV logic check must match the signed net expected value"
            )

        directional = self.action in {
            DecisionAction.LONG_CANDIDATE,
            DecisionAction.SHORT_CANDIDATE,
        }
        if directional and (
            not self.checks.all_passed
            or self.net_expected_value <= 0
            or self.reason_codes
        ):
            raise ValueError(
                "directional candidate requires every logic gate and positive net EV"
            )
        if self.action == DecisionAction.NO_TRADE and not self.reason_codes:
            raise ValueError("no-trade decision requires explicit reason codes")
        return self
