"""Explicit operator requests only; no account, source or execution authority."""

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.demo_automation import ARM_PHRASE, EXECUTE_PHRASE
from app.trade_qualification.demo_control_clear_v2 import CLEAR_CONFIRMATION_V2
from app.trade_qualification.demo_policy_counter_initialization_v1 import (
    COUNTER_CONFIRMATION_V1,
)

NativeDemoStrategyV2 = Literal[
    "trend_pullback",
    "breakout_continuation",
    "liquidity_sweep_reversal",
    "fvg_return",
    "order_block_return",
    "range_reversal",
    "structure_reversal",
    "volatility_expansion",
]

CONFIGURE_LEVERAGE_CONFIRMATION_V1 = "CONFIGURE OKX DEMO LEVERAGE WHILE FLAT"


class NativeDemoArmExecuteRequestV2(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    instrument_id: str = Field(pattern=r"^[A-Z0-9]{1,16}-USDT-SWAP$")
    strategy: NativeDemoStrategyV2
    contracts: str = Field(pattern=r"^(?:0|[1-9][0-9]{0,19})(?:\.[0-9]{1,20})?$")
    leverage: int = Field(ge=1, le=125)
    arm_confirmation: Literal[ARM_PHRASE]
    execute_confirmation: Literal[EXECUTE_PHRASE]

    @model_validator(mode="after")
    def positive_quantity(self):
        if Decimal(self.contracts) <= 0:
            raise ValueError("native_demo_contracts_must_be_positive")
        return self


class NativeDemoConfigureLeverageRequestV1(BaseModel):
    """One explicit, disarmed Demo configuration action."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    instrument_id: str = Field(pattern=r"^[A-Z0-9]{1,16}-USDT-SWAP$")
    strategy: NativeDemoStrategyV2
    contracts: str = Field(pattern=r"^(?:0|[1-9][0-9]{0,19})(?:\.[0-9]{1,20})?$")
    leverage_ceiling: Literal[3, 5, 8, 10, 20]
    confirmation: Literal[CONFIGURE_LEVERAGE_CONFIRMATION_V1]

    @model_validator(mode="after")
    def positive_quantity(self):
        if Decimal(self.contracts) <= 0:
            raise ValueError("native_demo_contracts_must_be_positive")
        return self


class NativeDemoConfigureLeverageResultV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    invocation_id: str
    action: Literal["configure_leverage"] = "configure_leverage"
    classification: Literal["settled", "no_change"]
    configuration_outcome_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    receipt_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    execution_authority: Literal[False] = False
    order_retry_authority: Literal[False] = False


class NativeDemoClearStopRequestV2(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    confirmation: Literal[CLEAR_CONFIRMATION_V2]


class NativeDemoCounterInitializationRequestV2(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    confirmation: Literal[COUNTER_CONFIRMATION_V1]


class NativeDemoActionResultV2(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    invocation_id: str
    action: Literal["arm_execute", "clear_stop", "initialize_policy_counter"]
    status: Literal[
        "reconciliation_data_hold_retained", "operator_command_recorded_disarmed"
    ]
    classification: str | None = None
    risk_hold_retained: bool | None = None
    execution_complete: Literal[False] = False


class NativeDemoFilledCompleteResultV3(BaseModel):
    """Displayed only after genuine result MOVE and durable resource cleanup."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    invocation_id: str
    action: Literal["arm_execute"] = "arm_execute"
    status: Literal["filled_flat_settled"] = "filled_flat_settled"
    classification: Literal["own_entry_and_close_filled_flat_settled"] = (
        "own_entry_and_close_filled_flat_settled"
    )
    closure_outcome_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    cleanup_outcome_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    forensics_outcome_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    report_spool_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    report_delivery_status: Literal[
        "queued_for_independent_worker", "not_eligible_under_current_report_policy"
    ]
    risk_hold_retained: Literal[False] = False
    execution_complete: Literal[True] = True
    metrics_complete: Literal[False] = False


class NativeDemoRecoveryRequestV1(BaseModel):
    """Select one durable event for DATA reconciliation; no Arm/write fields."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    reservation_id: str = Field(pattern=r"^[a-f0-9]{64}$")


class NativeDemoRecoveryResultV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    invocation_id: str
    reservation_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    status: Literal["reconciliation_data"] = "reconciliation_data"
    classification: str
    outcome_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    receipt_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    risk_hold_retained: bool
    fresh_resource_cleanup_confirmed: bool = False
    historical_transport_custody_unproven: Literal[True] = True
    execution_complete: Literal[False] = False
    execution_authority: Literal[False] = False
    order_retry_authority: Literal[False] = False


class NativeDemoZeroCompleteResultV3(BaseModel):
    """Display DATA after genuine zero closure, Control release and DB readback."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    invocation_id: str
    action: Literal["arm_execute"] = "arm_execute"
    status: Literal["zero_fill_flat_settled"] = "zero_fill_flat_settled"
    classification: Literal["terminal_zero_fill_flat_settled"] = (
        "terminal_zero_fill_flat_settled"
    )
    closure_outcome_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    cleanup_outcome_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    trade_created: Literal[False] = False
    risk_hold_retained: Literal[False] = False
    execution_complete: Literal[True] = True
    metrics_complete: Literal[False] = False
