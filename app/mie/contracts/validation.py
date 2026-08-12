from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import Field, field_validator, model_validator

from app.mie.contracts._base import MieContract, require_utc


class ValidationLevel(StrEnum):
    AUXILIARY = "auxiliary"
    COMPUTATIONAL = "computational"
    CAUSAL = "causal"
    PREQUENTIAL = "prequential"
    PREDICTIVE_OOS = "predictive_oos"
    ECONOMIC_OOS = "economic_oos"
    DEMO_EXECUTION = "demo_execution"
    PRODUCTION_ELIGIBLE = "production_eligible"


class EvidenceUse(StrEnum):
    SHADOW_ONLY = "shadow_only"
    AUXILIARY_TIE_BREAK = "auxiliary_tie_break"
    RISK_DOWNGRADE_ONLY = "risk_downgrade_only"
    DECISION_GATE = "decision_gate"


VALIDATION_RANK: dict[ValidationLevel, int] = {
    level: rank for rank, level in enumerate(ValidationLevel)
}


class ValidationMetric(MieContract):
    name: str = Field(
        min_length=1,
        max_length=80,
        pattern=r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$",
    )
    value: Decimal

    @model_validator(mode="after")
    def validate_value(self) -> "ValidationMetric":
        if not self.value.is_finite():
            raise ValueError("validation metric must be finite")
        return self


class ValidationReference(MieContract):
    """Reference to an immutable, externally produced validation artifact."""

    artifact_id: str = Field(min_length=1, max_length=160)
    source: str = Field(
        min_length=1,
        max_length=160,
        pattern=r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$",
    )
    model_version: str = Field(min_length=1, max_length=80)
    attested_level: ValidationLevel
    dataset_id: str = Field(min_length=1, max_length=160)
    sample_size: int = Field(ge=1)
    reviewer_id: str = Field(min_length=1, max_length=120)
    issued_at: datetime
    artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    metrics: tuple[ValidationMetric, ...] = ()

    @field_validator("issued_at")
    @classmethod
    def validate_issued_at(cls, value: datetime) -> datetime:
        return require_utc(value, "issued_at")

    @model_validator(mode="after")
    def validate_metrics(self) -> "ValidationReference":
        names = [metric.name for metric in self.metrics]
        if len(names) != len(set(names)):
            raise ValueError("validation metric names must be unique")
        return self


def validation_at_least(
    actual: ValidationLevel, required: ValidationLevel
) -> bool:
    return VALIDATION_RANK[actual] >= VALIDATION_RANK[required]
