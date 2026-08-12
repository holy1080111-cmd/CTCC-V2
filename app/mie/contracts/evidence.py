from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal
from uuid import UUID, uuid4

from pydantic import Field, field_validator, model_validator

from app.mie.contracts._base import ForecastHorizon, MieContract, require_utc
from app.mie.contracts.validation import (
    EvidenceUse,
    ValidationLevel,
    ValidationReference,
    validation_at_least,
)


class EvidenceDirection(StrEnum):
    LONG = "long"
    SHORT = "short"
    NEUTRAL = "neutral"


class Evidence(MieContract):
    """One versioned observation with no decision or execution authority."""

    evidence_id: UUID = Field(default_factory=uuid4)
    source: str = Field(
        min_length=1,
        max_length=160,
        pattern=r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$",
    )
    instrument_id: str = Field(
        min_length=3,
        max_length=64,
        pattern=r"^[A-Z0-9]+(?:-[A-Z0-9]+)+$",
    )
    horizon: ForecastHorizon
    observed_at: datetime
    data_cutoff: datetime
    generated_at: datetime
    direction: EvidenceDirection
    strength: Decimal = Field(ge=0, le=1)
    reliability: Decimal = Field(ge=0, le=1)
    uncertainty: Decimal = Field(ge=0, le=1)
    data_quality: Decimal = Field(ge=0, le=1)
    calibrated_probability: Decimal | None = Field(default=None, ge=0, le=1)
    validation_level: ValidationLevel
    permitted_use: EvidenceUse
    validation_sample_size: int = Field(default=0, ge=0)
    validation_metric: Decimal | None = None
    validation_reference: ValidationReference | None = None
    dependency_group: str = Field(
        min_length=1,
        max_length=160,
        pattern=r"^[a-z0-9][a-z0-9._:-]*$",
    )
    feature_version: str = Field(min_length=1, max_length=80)
    model_version: str = Field(min_length=1, max_length=80)
    provenance_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    detail_codes: tuple[str, ...] = ()
    execution_authority: Literal[False] = False

    @field_validator("observed_at", "data_cutoff", "generated_at")
    @classmethod
    def validate_timestamps(cls, value: datetime, info) -> datetime:
        return require_utc(value, info.field_name)

    @field_validator("detail_codes")
    @classmethod
    def validate_detail_codes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("evidence detail codes must be unique")
        if any(not item.strip() for item in value):
            raise ValueError("evidence detail codes cannot be empty")
        return value

    @model_validator(mode="after")
    def validate_evidence_authority(self) -> "Evidence":
        if self.data_cutoff > self.observed_at:
            raise ValueError("evidence cannot use data after observed_at")
        if self.generated_at < self.observed_at:
            raise ValueError("evidence cannot be generated before observed_at")
        if self.direction == EvidenceDirection.NEUTRAL and self.strength != 0:
            raise ValueError("neutral evidence strength must be zero")
        if self.direction != EvidenceDirection.NEUTRAL and self.strength == 0:
            raise ValueError("directional evidence requires positive strength")
        if self.validation_metric is not None:
            if not self.validation_metric.is_finite():
                raise ValueError("validation metric must be finite")
            if self.validation_sample_size == 0:
                raise ValueError("validation metric requires a validation sample")

        allowed_uses = {
            ValidationLevel.AUXILIARY: {
                EvidenceUse.SHADOW_ONLY,
                EvidenceUse.AUXILIARY_TIE_BREAK,
            },
            ValidationLevel.COMPUTATIONAL: {EvidenceUse.SHADOW_ONLY},
            ValidationLevel.CAUSAL: {
                EvidenceUse.SHADOW_ONLY,
                EvidenceUse.RISK_DOWNGRADE_ONLY,
            },
            ValidationLevel.PREQUENTIAL: {
                EvidenceUse.SHADOW_ONLY,
                EvidenceUse.RISK_DOWNGRADE_ONLY,
            },
            ValidationLevel.PREDICTIVE_OOS: {
                EvidenceUse.SHADOW_ONLY,
                EvidenceUse.RISK_DOWNGRADE_ONLY,
                EvidenceUse.DECISION_GATE,
            },
            ValidationLevel.ECONOMIC_OOS: {
                EvidenceUse.SHADOW_ONLY,
                EvidenceUse.RISK_DOWNGRADE_ONLY,
                EvidenceUse.DECISION_GATE,
            },
            ValidationLevel.DEMO_EXECUTION: {
                EvidenceUse.SHADOW_ONLY,
                EvidenceUse.RISK_DOWNGRADE_ONLY,
                EvidenceUse.DECISION_GATE,
            },
            ValidationLevel.PRODUCTION_ELIGIBLE: {
                EvidenceUse.SHADOW_ONLY,
                EvidenceUse.RISK_DOWNGRADE_ONLY,
                EvidenceUse.DECISION_GATE,
            },
        }[self.validation_level]
        if self.permitted_use not in allowed_uses:
            raise ValueError(
                "evidence use exceeds its mathematical validation level"
            )

        requires_reference = validation_at_least(
            self.validation_level, ValidationLevel.PREDICTIVE_OOS
        )
        if requires_reference and self.validation_reference is None:
            raise ValueError(
                "predictive or higher validation requires an external artifact"
            )
        if self.calibrated_probability is not None:
            if not validation_at_least(
                self.validation_level, ValidationLevel.PREQUENTIAL
            ):
                raise ValueError(
                    "calibrated probability requires prequential validation"
                )
            if self.validation_reference is None:
                raise ValueError(
                    "calibrated probability requires a validation artifact"
                )
        if self.validation_reference is not None and (
            self.validation_reference.source != self.source
            or self.validation_reference.model_version != self.model_version
        ):
            raise ValueError(
                "validation artifact does not match evidence source and model"
            )
        if (
            self.validation_reference is not None
            and not validation_at_least(
                self.validation_reference.attested_level,
                self.validation_level,
            )
        ):
            raise ValueError(
                "validation artifact is below the evidence claim"
            )
        if (
            self.validation_reference is not None
            and self.validation_sample_size
            != self.validation_reference.sample_size
        ):
            raise ValueError(
                "evidence sample size must match its validation artifact"
            )
        if (
            self.validation_reference is not None
            and self.validation_reference.issued_at > self.generated_at
        ):
            raise ValueError("evidence cannot use a future validation artifact")
        return self
