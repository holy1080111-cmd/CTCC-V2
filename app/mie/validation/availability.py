"""Availability attestations, separate from event times and predictive claims.

These records bind supplied observations; they do not independently verify an
external receipt or establish that archived prices were visible at bar close.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import StrEnum
from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.mie.validation.contracts import (
    DatasetPartition,
    Gate3Claim,
    Gate3Contract,
    Sha256,
    require_utc,
)


class AvailabilityBasis(StrEnum):
    MEASURED_ROW_RECEIPT = "measured_row_receipt"
    ARCHIVE_OBSERVATION = "archive_observation"
    ASSUMED_BAR_CLOSE = "assumed_bar_close"


class AvailabilityProvenance(Gate3Contract):
    """Explicit timing basis for a hash-bound source row, never a promotion."""

    basis: AvailabilityBasis
    source_row_sha256: Sha256
    receipt_sha256: Sha256
    bar_closed_at: datetime
    observed_at: datetime
    retrieved_at: datetime
    available_at: datetime
    assumption: Literal["none", "confirmed_at_bar_close"] = "none"
    current_claim: Literal[Gate3Claim.COMPUTATIONAL] = Gate3Claim.COMPUTATIONAL
    predictive_oos_eligible: Literal[False] = False
    runtime_consumers: Literal[0] = 0
    execution_authority: Literal[False] = False

    @field_validator("bar_closed_at", "observed_at", "retrieved_at", "available_at")
    @classmethod
    def validate_utc(cls, value: datetime, info) -> datetime:
        return require_utc(value, info.field_name)

    @model_validator(mode="after")
    def validate_basis(self) -> AvailabilityProvenance:
        if self.observed_at < self.bar_closed_at:
            raise ValueError("row observation cannot predate its completed bar")
        if self.retrieved_at < self.observed_at:
            raise ValueError("receipt retrieval cannot predate observation")
        if self.basis == AvailabilityBasis.ASSUMED_BAR_CLOSE:
            if self.assumption != "confirmed_at_bar_close":
                raise ValueError(
                    "bar-close availability requires an explicit assumption"
                )
            expected = self.bar_closed_at
        else:
            if self.assumption != "none":
                raise ValueError(
                    "observed availability cannot carry a close assumption"
                )
            expected = (
                self.retrieved_at
                if self.basis == AvailabilityBasis.ARCHIVE_OBSERVATION
                else self.observed_at
            )
        if self.available_at != expected:
            raise ValueError(
                "available time disagrees with the declared evidence basis"
            )
        return self


class ArchiveObservationReceipt(Gate3Contract):
    """Supplied archive observation, not independently authenticated transport.

    The canonical receipt hash binds all fields. The bytes adapter separately
    verifies archive identity, safe member structure, and complete source rows.
    """

    schema_version: Literal["ctcc.mie.gate3.archive_observation.v1"] = (
        "ctcc.mie.gate3.archive_observation.v1"
    )
    archive_sha256: Sha256
    archive_byte_size: int = Field(ge=1, le=1024 * 1024)
    symbol: Literal["BTCUSDT", "ETHUSDT"]
    instrument_id: Literal["BTC-USDT-SWAP", "ETH-USDT-SWAP"]
    partition: Literal[DatasetPartition.DEVELOPMENT, DatasetPartition.VALIDATION]
    day_start_at: datetime
    member_name: str = Field(min_length=1, max_length=80)
    observed_at: datetime
    retrieved_at: datetime
    source: Literal["binance.public_data"] = "binance.public_data"
    market: Literal["futures_um"] = "futures_um"
    interval_seconds: Literal[60] = 60
    expected_rows: Literal[1440] = 1440
    revision_policy: Literal["provider_correctable"] = "provider_correctable"
    attestation: Literal["supplied_observation_not_independently_verified"] = (
        "supplied_observation_not_independently_verified"
    )
    current_claim: Literal[Gate3Claim.COMPUTATIONAL] = Gate3Claim.COMPUTATIONAL
    predictive_oos_eligible: Literal[False] = False
    runtime_consumers: Literal[0] = 0
    execution_authority: Literal[False] = False

    @field_validator("day_start_at", "observed_at", "retrieved_at")
    @classmethod
    def validate_utc(cls, value: datetime, info) -> datetime:
        return require_utc(value, info.field_name)

    @model_validator(mode="after")
    def validate_coordinates(self) -> ArchiveObservationReceipt:
        start = self.day_start_at
        if any((start.hour, start.minute, start.second, start.microsecond)):
            raise ValueError("archive day must begin at exact UTC midnight")
        expected_member = f"{self.symbol}-1m-{start.date().isoformat()}.csv"
        if self.member_name != expected_member:
            raise ValueError(
                "archive member does not match the declared daily coordinates"
            )
        expected_instrument = f"{self.symbol.removesuffix('USDT')}-USDT-SWAP"
        if self.instrument_id != expected_instrument:
            raise ValueError("archive symbol and instrument disagree")
        try:
            day_end = start + timedelta(days=1)
        except OverflowError as exc:
            raise ValueError(
                "archive day end exceeds the supported datetime range"
            ) from exc
        if self.observed_at < day_end:
            raise ValueError("archive observation cannot predate the complete day")
        if self.retrieved_at < self.observed_at:
            raise ValueError("archive retrieval cannot predate observation")
        return self
