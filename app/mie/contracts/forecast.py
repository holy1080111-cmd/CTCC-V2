from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal
from uuid import UUID, uuid4

from pydantic import Field, field_validator, model_validator

from app.mie.contracts._base import ForecastHorizon, MieContract, require_utc
from app.mie.contracts.validation import (
    ValidationLevel,
    ValidationReference,
    validation_at_least,
)


_SUM_TOLERANCE = Decimal("1e-12")


class CalibrationStatus(StrEnum):
    UNCALIBRATED = "uncalibrated"
    CALIBRATING = "calibrating"
    CALIBRATED = "calibrated"
    DEGRADED = "degraded"


class ProbabilityVector(MieContract):
    long: Decimal = Field(ge=0, le=1)
    short: Decimal = Field(ge=0, le=1)
    neutral: Decimal = Field(ge=0, le=1)

    @model_validator(mode="after")
    def validate_total_probability(self) -> "ProbabilityVector":
        total = self.long + self.short + self.neutral
        if abs(total - Decimal("1")) > _SUM_TOLERANCE:
            raise ValueError("forecast probabilities must sum to one")
        return self


class ProbabilityForecast(MieContract):
    forecast_id: UUID = Field(default_factory=uuid4)
    instrument_id: str = Field(
        min_length=3,
        max_length=64,
        pattern=r"^[A-Z0-9]+(?:-[A-Z0-9]+)+$",
    )
    horizon: ForecastHorizon
    as_of: datetime
    data_cutoff: datetime
    generated_at: datetime
    probabilities: ProbabilityVector
    uncertainty: Decimal = Field(ge=0, le=1)
    evidence_ids: tuple[UUID, ...] = Field(min_length=1)
    model_id: str = Field(
        min_length=1,
        max_length=160,
        pattern=r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$",
    )
    model_version: str = Field(min_length=1, max_length=80)
    provenance_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    validation_level: ValidationLevel
    calibration_status: CalibrationStatus
    validation_reference: ValidationReference | None = None
    calibration_reference: ValidationReference | None = None
    execution_authority: Literal[False] = False

    @field_validator("as_of", "data_cutoff", "generated_at")
    @classmethod
    def validate_timestamps(cls, value: datetime, info) -> datetime:
        return require_utc(value, info.field_name)

    @model_validator(mode="after")
    def validate_forecast(self) -> "ProbabilityForecast":
        if self.data_cutoff > self.as_of:
            raise ValueError("forecast cannot use data after as_of")
        if self.generated_at < self.as_of:
            raise ValueError("forecast cannot be generated before as_of")
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("forecast evidence ids must be unique")
        if validation_at_least(
            self.validation_level, ValidationLevel.PREDICTIVE_OOS
        ) and self.validation_reference is None:
            raise ValueError(
                "predictive forecast requires an external validation artifact"
            )
        if self.calibration_status == CalibrationStatus.CALIBRATED:
            if not validation_at_least(
                self.validation_level, ValidationLevel.PREQUENTIAL
            ):
                raise ValueError(
                    "calibrated forecast requires prequential validation"
                )
            if self.calibration_reference is None:
                raise ValueError(
                    "calibrated forecast requires a calibration artifact"
                )
        references = (
            ("validation", self.validation_reference),
            ("calibration", self.calibration_reference),
        )
        for reference_name, reference in references:
            if reference is None:
                continue
            if (
                reference.source != self.model_id
                or reference.model_version != self.model_version
            ):
                raise ValueError(
                    f"{reference_name} artifact does not match forecast model"
                )
            required_level = (
                self.validation_level
                if reference_name == "validation"
                else ValidationLevel.PREQUENTIAL
            )
            if not validation_at_least(
                reference.attested_level, required_level
            ):
                raise ValueError(
                    f"{reference_name} artifact is below the forecast claim"
                )
            if reference.issued_at > self.generated_at:
                raise ValueError(
                    f"forecast cannot use a future {reference_name} artifact"
                )
        return self
