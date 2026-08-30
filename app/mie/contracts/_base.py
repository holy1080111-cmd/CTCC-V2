from __future__ import annotations

from datetime import datetime, timedelta

from pydantic import BaseModel, ConfigDict, Field, model_validator


class MieContract(BaseModel):
    """Immutable, strict base for every MIE boundary contract."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        allow_inf_nan=False,
        revalidate_instances="always",
        str_strip_whitespace=True,
    )


def require_utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware UTC")
    if value.utcoffset() != timedelta(0):
        raise ValueError(f"{field_name} must use UTC")
    return value


class ForecastHorizon(MieContract):
    """Canonical horizon with a human label and an exact duration."""

    label: str = Field(pattern=r"^[1-9][0-9]*(?:s|m|H|D)$")
    seconds: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_duration(self) -> "ForecastHorizon":
        amount = int(self.label[:-1])
        multiplier = {
            "s": 1,
            "m": 60,
            "H": 60 * 60,
            "D": 24 * 60 * 60,
        }[self.label[-1]]
        if self.seconds != amount * multiplier:
            raise ValueError("horizon label and seconds disagree")
        return self
