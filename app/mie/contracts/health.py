from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID, uuid4

from pydantic import Field, field_validator, model_validator

from app.mie.contracts._base import MieContract, require_utc
from app.mie.contracts.forecast import CalibrationStatus
from app.mie.contracts.validation import (
    ValidationLevel,
    ValidationReference,
    validation_at_least,
)


class ModelHealthStatus(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


class ModelHealth(MieContract):
    health_id: UUID = Field(default_factory=uuid4)
    model_id: str = Field(
        min_length=1,
        max_length=160,
        pattern=r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$",
    )
    model_version: str = Field(min_length=1, max_length=80)
    covered_sources: tuple[str, ...] = Field(min_length=1)
    evaluated_at: datetime
    data_cutoff: datetime
    status: ModelHealthStatus
    data_fresh: bool
    leakage_check_passed: bool
    calibration_status: CalibrationStatus
    validation_level: ValidationLevel
    last_oos_validation_at: datetime | None = None
    validation_reference: ValidationReference | None = None
    failure_codes: tuple[str, ...] = ()
    execution_authority: Literal[False] = False

    @field_validator(
        "evaluated_at", "data_cutoff", "last_oos_validation_at"
    )
    @classmethod
    def validate_timestamps(cls, value: datetime | None, info):
        if value is None:
            return value
        return require_utc(value, info.field_name)

    @field_validator("covered_sources", "failure_codes")
    @classmethod
    def validate_codes(
        cls, value: tuple[str, ...], info
    ) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError(f"{info.field_name} must be unique")
        if any(not item.strip() for item in value):
            raise ValueError(f"{info.field_name} cannot contain blank values")
        return value

    @model_validator(mode="after")
    def validate_health(self) -> "ModelHealth":
        if self.data_cutoff > self.evaluated_at:
            raise ValueError("model health cannot use future data")
        if (
            self.last_oos_validation_at is not None
            and self.last_oos_validation_at > self.evaluated_at
        ):
            raise ValueError("model health cannot use future OOS validation")
        if self.status == ModelHealthStatus.HEALTHY and (
            not self.data_fresh
            or not self.leakage_check_passed
            or self.failure_codes
            or self.calibration_status == CalibrationStatus.DEGRADED
        ):
            raise ValueError(
                "healthy model requires fresh data, leakage pass, and no failures"
            )
        if self.status in {
            ModelHealthStatus.DEGRADED,
            ModelHealthStatus.UNHEALTHY,
        } and not self.failure_codes:
            raise ValueError("degraded or unhealthy model requires failure codes")
        if (
            self.calibration_status == CalibrationStatus.DEGRADED
            and not self.failure_codes
        ):
            raise ValueError(
                "degraded calibration requires an explicit failure code"
            )
        if validation_at_least(
            self.validation_level, ValidationLevel.PREDICTIVE_OOS
        ):
            if (
                self.last_oos_validation_at is None
                or self.validation_reference is None
            ):
                raise ValueError(
                    "OOS-validated model health requires validation evidence"
                )
        if self.calibration_status == CalibrationStatus.CALIBRATED:
            if not validation_at_least(
                self.validation_level, ValidationLevel.PREQUENTIAL
            ):
                raise ValueError(
                    "calibrated model health requires prequential validation"
                )
            if (
                self.last_oos_validation_at is None
                or self.validation_reference is None
            ):
                raise ValueError(
                    "calibrated model health requires validation evidence"
                )
        if (self.last_oos_validation_at is None) != (
            self.validation_reference is None
        ):
            raise ValueError(
                "model health validation time and artifact must appear together"
            )
        if self.validation_reference is not None and (
            self.validation_reference.source != self.model_id
            or self.validation_reference.model_version != self.model_version
        ):
            raise ValueError(
                "model health validation artifact does not match model"
            )
        if (
            self.validation_reference is not None
            and not validation_at_least(
                self.validation_reference.attested_level,
                self.validation_level,
            )
        ):
            raise ValueError(
                "validation artifact is below the model health claim"
            )
        if (
            self.validation_reference is not None
            and self.validation_reference.issued_at > self.evaluated_at
        ):
            raise ValueError(
                "model health cannot use a future validation artifact"
            )
        return self
