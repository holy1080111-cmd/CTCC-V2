"""Local native journal DATA; never proof of a current exchange position."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.trade_qualification.models import require_aware


class NativeJournalItemV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    reservation_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    report_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,95}$")
    instrument_id: str = Field(pattern=r"^[A-Z0-9]{1,16}-USDT-SWAP$")
    direction: Literal["long", "short"]
    local_state: Literal["reserved", "consumed", "uncertain", "reconciled_flat"]
    state_revision: int = Field(ge=1, le=4)
    created_at: datetime
    updated_at: datetime
    deadline: datetime
    protection_status: Literal["NOT_RECHECKED"] = "NOT_RECHECKED"

    _utc = field_validator("created_at", "updated_at", "deadline")(require_aware)

    @model_validator(mode="after")
    def clocks_and_revision(self):
        if (
            self.state_revision
            != {"reserved": 1, "consumed": 2, "uncertain": 3, "reconciled_flat": 4}[
                self.local_state
            ]
            or self.updated_at < self.created_at
            or self.deadline <= self.created_at
        ):
            raise ValueError("native_journal_row_inconsistent")
        return self


class NativeJournalReadoutV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    schema_version: Literal["ctcc.native_journal_readout.v1"] = (
        "ctcc.native_journal_readout.v1"
    )
    account_binding_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    database_head: Literal["0056", "0057", "0058"]
    observed_at: datetime
    limit: int = Field(ge=1, le=100)
    total_native: int = Field(ge=0)
    unresolved_native: int = Field(ge=0)
    unresolved_other: int = Field(ge=0)
    truncated: bool
    entries: tuple[NativeJournalItemV1, ...] = Field(max_length=100)
    account_complete: Literal[False] = False
    execution_authority: Literal[False] = False
    order_retry_authority: Literal[False] = False

    _utc = field_validator("observed_at")(require_aware)

    @field_validator(
        "account_complete",
        "execution_authority",
        "order_retry_authority",
        mode="before",
    )
    @classmethod
    def no_authority(cls, value):
        if type(value) is not bool or value is not False:
            raise ValueError("native_journal_has_no_authority")
        return value

    @model_validator(mode="after")
    def complete_local_page(self):
        if (
            self.unresolved_native > self.total_native
            or len(self.entries) != min(self.limit, self.total_native)
            or self.truncated != (self.total_native > self.limit)
            or len({item.reservation_id for item in self.entries}) != len(self.entries)
            or any(item.updated_at > self.observed_at for item in self.entries)
        ):
            raise ValueError("native_journal_page_inconsistent")
        return self
