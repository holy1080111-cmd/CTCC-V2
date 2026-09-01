from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


LIVE_ARM_PHRASE = "ARM_OKX_LIVE_REAL_MONEY"
LIVE_DISARM_PHRASE = "DISARM_OKX_LIVE"
LIVE_EMERGENCY_STOP_PHRASE = "EMERGENCY_STOP_OKX_LIVE"
LIVE_CLEAR_STOP_PHRASE = "CLEAR_OKX_LIVE_STOP"
LIVE_UNRESOLVED_CLEAR_PHRASE = "RECONCILE_OKX_LIVE_UNRESOLVED_INTENTS"
LIVE_ORDER_PHRASE = "EXECUTE_OKX_LIVE_REAL_MONEY"
LIVE_CANCEL_PHRASE = "CANCEL_OKX_LIVE_REAL_ORDER"
LIVE_CLOSE_PHRASE = "CLOSE_OKX_LIVE_REAL_POSITION"
LIVE_LEVERAGE_PHRASE = "SET_OKX_LIVE_REAL_LEVERAGE"
LIVE_AUTOMATION_EXECUTE_PHRASE = "EXECUTE_OKX_LIVE_AUTOMATION"


class OkxLiveApiKeyCapability(BaseModel):
    permissions: list[str] = Field(default_factory=list)
    unknown_permissions: list[str] = Field(default_factory=list)
    read_permission: bool = False
    trade_permission: bool = False
    withdraw_permission: bool = False
    ip_bound: bool = False


class OkxLiveAccountConfig(BaseModel):
    uid: str | None = None
    main_uid: str | None = None
    is_sub_account: bool | None = None
    account_level: str | None = None
    position_mode: str = Field(min_length=1)
    account_stp_mode: str | None = None
    account_type: str | None = None
    capability: OkxLiveApiKeyCapability


class OkxLiveBalanceDetail(BaseModel):
    currency: str
    equity: Decimal
    cash_balance: Decimal
    available_balance: Decimal
    frozen_balance: Decimal
    unrealized_pnl: Decimal


class OkxLiveBalanceSnapshot(BaseModel):
    total_equity: Decimal
    isolated_equity: Decimal
    adjusted_equity: Decimal
    available_equity: Decimal
    details: list[OkxLiveBalanceDetail] = Field(default_factory=list)
    captured_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    raw: dict[str, Any] = Field(default_factory=dict)


class OkxLivePositionView(BaseModel):
    position_id: str = Field(min_length=1, max_length=100)
    instrument_id: str = Field(min_length=1, max_length=40)
    position_side: str
    size: Decimal
    available_size: Decimal
    average_price: Decimal | None = None
    mark_price: Decimal | None = None
    unrealized_pnl: Decimal
    leverage: Decimal | None = None
    margin_mode: str | None = None
    liquidation_price: Decimal | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    raw: dict[str, Any] = Field(default_factory=dict)

    @property
    def position_key(self) -> str:
        return self.position_id


class OkxLiveOrderView(BaseModel):
    order_id: str = Field(min_length=1, max_length=100)
    client_order_id: str | None = None
    instrument_id: str = Field(min_length=1, max_length=40)
    side: str
    position_side: str | None = None
    order_type: str
    state: str
    size: Decimal
    accumulated_fill_size: Decimal
    price: Decimal | None = None
    average_fill_price: Decimal | None = None
    reduce_only: bool = False
    created_at: datetime | None = None
    updated_at: datetime | None = None
    attached_algo_orders: list[dict[str, Any]] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)


class OkxLiveAlgoOrderView(BaseModel):
    algo_order_id: str = Field(min_length=1, max_length=100)
    client_algo_order_id: str | None = None
    instrument_type: str | None = None
    instrument_id: str = Field(min_length=1, max_length=40)
    order_type: str
    state: str
    side: str | None = None
    position_side: str | None = None
    margin_mode: str | None = None
    reduce_only: bool | None = None
    close_fraction: Decimal | None = None
    size: Decimal
    actual_size: Decimal = Decimal("0")
    take_profit_trigger_price: Decimal | None = None
    take_profit_trigger_price_type: str | None = None
    take_profit_order_price: Decimal | None = None
    stop_loss_trigger_price: Decimal | None = None
    stop_loss_trigger_price_type: str | None = None
    stop_loss_order_price: Decimal | None = None
    amend_price_on_trigger_type: str | None = None
    failure_code: str | None = None
    trigger_time: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class OkxLiveReconcileResult(BaseModel):
    account_config: OkxLiveAccountConfig
    balance: OkxLiveBalanceSnapshot
    positions: list[OkxLivePositionView]
    pending_orders: list[OkxLiveOrderView]
    recent_orders: list[OkxLiveOrderView]
    pending_algo_orders: list[OkxLiveAlgoOrderView]
    persisted: bool
    reconciled_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class OkxLiveMirrorStatus(BaseModel):
    available: bool
    order_count: int = 0
    position_count: int = 0
    algo_order_count: int = 0
    last_reconciled_at: datetime | None = None
    last_error: str | None = None
    safety_latched: bool = False
    safety_latch_code: str | None = None
    safety_latch_version: int = Field(default=0, ge=0)
    details: dict[str, Any] = Field(default_factory=dict)


class OkxLiveSafetyLatchState(BaseModel):
    latched: bool
    code: str | None = None
    version: int = Field(ge=0)
    latched_at: datetime | None = None


class OkxLiveArmStatus(BaseModel):
    armed: bool = False
    emergency_stop: bool = False
    expires_at: datetime | None = None
    baseline_equity: Decimal | None = None
    submissions: int = 0
    max_submissions: int = 1
    automation_running: bool = False
    unresolved_intent_count: int = Field(default=0, ge=0)
    safety_latch_code: str | None = None
    safety_latch_version: int = Field(default=0, ge=0)
    last_error: str | None = None


class OkxLiveStatus(BaseModel):
    enabled: bool
    trading_mode: str
    credentials_configured: bool
    read_ready: bool
    live_trading_enabled: bool
    writes_enabled: bool
    automation_enabled: bool
    base_url: str
    allowed_symbols: list[str]
    max_order_size_contracts: Decimal
    max_notional_usdt: Decimal
    max_open_positions: int
    max_leverage: int
    require_protection: bool
    require_ip_bound_key: bool
    forbid_withdraw_permission: bool
    capability: OkxLiveApiKeyCapability | None = None
    local_mirror_available: bool = False
    mirrored_order_count: int = 0
    mirrored_position_count: int = 0
    mirrored_algo_order_count: int = 0
    last_reconciled_at: datetime | None = None
    last_exchange_ok_at: datetime | None = None
    last_error: str | None = None
    blockers: list[str] = Field(default_factory=list)
    arm: OkxLiveArmStatus = Field(default_factory=OkxLiveArmStatus)


class OkxLiveReconcileSummary(BaseModel):
    total_equity: Decimal
    position_count: int
    pending_order_count: int
    recent_order_count: int
    pending_algo_order_count: int
    persisted: bool
    capability: OkxLiveApiKeyCapability
    reconciled_at: datetime


class OkxLiveAccountSummary(BaseModel):
    """Authenticated operator view with exchange account identifiers removed."""

    is_sub_account: bool | None = None
    account_level: str | None = None
    position_mode: str
    account_stp_mode: str | None = None
    account_type: str | None = None
    capability: OkxLiveApiKeyCapability


class OkxLiveBalanceSummary(BaseModel):
    total_equity: Decimal
    isolated_equity: Decimal
    adjusted_equity: Decimal
    available_equity: Decimal
    captured_at: datetime


class OkxLivePositionSummary(BaseModel):
    position_id: str
    instrument_id: str
    position_side: str
    size: Decimal
    available_size: Decimal
    average_price: Decimal | None = None
    mark_price: Decimal | None = None
    unrealized_pnl: Decimal
    leverage: Decimal | None = None
    margin_mode: str | None = None
    liquidation_price: Decimal | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class OkxLiveOrderSummary(BaseModel):
    order_id: str
    client_order_id: str | None = None
    instrument_id: str
    side: str
    position_side: str | None = None
    order_type: str
    state: str
    size: Decimal
    accumulated_fill_size: Decimal
    price: Decimal | None = None
    average_fill_price: Decimal | None = None
    reduce_only: bool = False
    protection_count: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None


class OkxLiveAlgoOrderSummary(BaseModel):
    algo_order_id: str
    client_algo_order_id: str | None = None
    instrument_id: str
    order_type: str
    state: str
    side: str | None = None
    position_side: str | None = None
    size: Decimal
    take_profit_trigger_price: Decimal | None = None
    stop_loss_trigger_price: Decimal | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class OkxLiveOrderAcknowledgement(BaseModel):
    order_id: str = ""
    client_order_id: str | None = None
    exchange_code: str = "0"


class OkxLiveWriteResult(BaseModel):
    action: Literal["place_order", "cancel_order", "close_position", "set_leverage"]
    accepted: bool
    final_state_confirmed: bool = False
    acknowledgement: OkxLiveOrderAcknowledgement | None = None
    order: OkxLiveOrderSummary | None = None
    reconciled: bool = False
    warnings: list[str] = Field(default_factory=list)
    completed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


LiveIntentAction = Literal[
    "place_order",
    "cancel_order",
    "close_position",
    "set_leverage",
]
LiveIntentStatus = Literal[
    "reserved",
    "acknowledged",
    "confirmed",
    "ambiguous",
    "rejected",
]


class OkxLiveExecutionIntentView(BaseModel):
    idempotency_key: str
    request_hash: str = Field(min_length=64, max_length=64)
    action: LiveIntentAction
    status: LiveIntentStatus
    instrument_id: str
    client_order_id: str | None = None
    exchange_order_id: str | None = None
    protection_client_order_id: str | None = None
    expected_protection_size: Decimal | None = None
    expected_stop_loss: Decimal | None = None
    expected_take_profit: Decimal | None = None
    expected_trigger_price_type: str | None = None
    detail_codes: list[str] = Field(default_factory=list)
    operator_reconciled_at: datetime | None = None
    operator_resolution_code: str | None = None
    created_at: datetime
    updated_at: datetime


class OkxLiveArmRequest(BaseModel):
    duration_seconds: int = Field(default=300, ge=60, le=900)
    confirmation: Literal[LIVE_ARM_PHRASE]


class OkxLiveDisarmRequest(BaseModel):
    confirmation: Literal[LIVE_DISARM_PHRASE]


class OkxLiveEmergencyStopRequest(BaseModel):
    confirmation: Literal[LIVE_EMERGENCY_STOP_PHRASE]


class OkxLiveIntentResolutionExpectation(BaseModel):
    idempotency_key: str = Field(
        min_length=11,
        max_length=32,
        pattern=r"^CTCC[XL][A-Za-z0-9]{6,27}$",
    )
    status: Literal["reserved", "acknowledged", "ambiguous"]
    updated_at: datetime

    @model_validator(mode="after")
    def require_timezone(self) -> "OkxLiveIntentResolutionExpectation":
        if self.updated_at.tzinfo is None:
            raise ValueError("updated_at must include a timezone")
        return self


class OkxLiveClearStopRequest(BaseModel):
    confirmation: Literal[LIVE_CLEAR_STOP_PHRASE]
    expected_unresolved_intents: list[
        OkxLiveIntentResolutionExpectation
    ] = Field(default_factory=list, max_length=100)
    unresolved_confirmation: Literal[LIVE_UNRESOLVED_CLEAR_PHRASE] | None = None

    @model_validator(mode="after")
    def validate_unresolved_confirmation(self) -> "OkxLiveClearStopRequest":
        keys = [item.idempotency_key for item in self.expected_unresolved_intents]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate unresolved intent expectation")
        if self.expected_unresolved_intents and self.unresolved_confirmation is None:
            raise ValueError("unresolved intent confirmation is required")
        if not self.expected_unresolved_intents and self.unresolved_confirmation:
            raise ValueError("unresolved intent confirmation requires expectations")
        return self


class OkxLiveOrderRequest(BaseModel):
    instrument_id: str = Field(
        pattern=r"^[A-Z0-9]+-[A-Z0-9]+-SWAP$", max_length=40
    )
    direction: Literal["long", "short"]
    size: Decimal = Field(gt=0)
    margin_mode: Literal["cross", "isolated"] = "cross"
    leverage: int = Field(default=1, ge=1, le=3)
    order_type: Literal["market"] = "market"
    stop_loss: Decimal = Field(gt=0)
    take_profit: Decimal = Field(gt=0)
    trigger_price_type: Literal["mark"] = "mark"
    client_order_id: str = Field(
        min_length=11,
        max_length=32,
        pattern=r"^CTCCL[A-Za-z0-9]{6,27}$",
    )
    confirmation: Literal[LIVE_ORDER_PHRASE]

    @model_validator(mode="after")
    def validate_protection(self) -> "OkxLiveOrderRequest":
        if self.direction == "long" and not self.stop_loss < self.take_profit:
            raise ValueError("long protection prices are inverted")
        if self.direction == "short" and not self.take_profit < self.stop_loss:
            raise ValueError("short protection prices are inverted")
        return self


class OkxLiveCancelRequest(BaseModel):
    instrument_id: str = Field(
        pattern=r"^[A-Z0-9]+-[A-Z0-9]+-SWAP$", max_length=40
    )
    order_id: str | None = Field(default=None, min_length=1, max_length=100)
    client_order_id: str | None = Field(default=None, min_length=1, max_length=32)
    idempotency_key: str = Field(
        min_length=11,
        max_length=32,
        pattern=r"^CTCCX[A-Za-z0-9]{6,27}$",
    )
    confirmation: Literal[LIVE_CANCEL_PHRASE]

    @model_validator(mode="after")
    def validate_identifier(self) -> "OkxLiveCancelRequest":
        if bool(self.order_id) == bool(self.client_order_id):
            raise ValueError("provide exactly one of order_id or client_order_id")
        return self


class OkxLiveCloseRequest(BaseModel):
    instrument_id: str = Field(
        pattern=r"^[A-Z0-9]+-[A-Z0-9]+-SWAP$", max_length=40
    )
    direction: Literal["long", "short"] | None = None
    margin_mode: Literal["cross", "isolated"] = "cross"
    idempotency_key: str = Field(
        min_length=11,
        max_length=32,
        pattern=r"^CTCCX[A-Za-z0-9]{6,27}$",
    )
    confirmation: Literal[LIVE_CLOSE_PHRASE]


class OkxLiveLeverageRequest(BaseModel):
    instrument_id: str = Field(
        pattern=r"^[A-Z0-9]+-[A-Z0-9]+-SWAP$", max_length=40
    )
    leverage: int = Field(ge=1, le=3)
    margin_mode: Literal["cross", "isolated"] = "cross"
    direction: Literal["long", "short"] | None = None
    idempotency_key: str = Field(
        min_length=11,
        max_length=32,
        pattern=r"^CTCCX[A-Za-z0-9]{6,27}$",
    )
    confirmation: Literal[LIVE_LEVERAGE_PHRASE]


LiveAutomationOutcome = Literal[
    "submitted",
    "approved_dry_run",
    "no_trade",
    "risk_rejected",
    "blocked",
    "duplicate",
    "monitoring",
    "error",
]


class OkxLiveAutomationRunRequest(BaseModel):
    symbols: list[str] | None = Field(default=None, min_length=1, max_length=2)
    execute: bool = False
    confirmation: str | None = None

    @model_validator(mode="after")
    def require_execute_confirmation(self) -> "OkxLiveAutomationRunRequest":
        if self.execute and self.confirmation != LIVE_AUTOMATION_EXECUTE_PHRASE:
            raise ValueError(
                "execute=true requires confirmation="
                f"{LIVE_AUTOMATION_EXECUTE_PHRASE}"
            )
        return self


class OkxLiveAutomationSymbolResult(BaseModel):
    symbol: str
    instrument_id: str | None = None
    outcome: LiveAutomationOutcome
    direction: Literal["long", "short"] | None = None
    strategy: str | None = None
    score: int | None = None
    reference_price: Decimal | None = None
    stop_loss: Decimal | None = None
    take_profit: Decimal | None = None
    approved_contracts: Decimal | None = None
    client_order_id: str | None = None
    exchange_order_id: str | None = None
    reason_codes: list[str] = Field(default_factory=list)
    detail: str


class OkxLiveAutomationRunResult(BaseModel):
    trigger: Literal["manual", "scheduled"]
    execute: bool
    started_at: datetime
    completed_at: datetime
    results: list[OkxLiveAutomationSymbolResult]
    total_equity: Decimal | None = None


class OkxLiveAutomationStatus(BaseModel):
    capability_enabled: bool
    running: bool
    armed: bool
    emergency_stop: bool
    symbols: list[str]
    scan_interval_seconds: int
    next_run_at: datetime | None = None
    last_started_at: datetime | None = None
    last_completed_at: datetime | None = None
    last_error: str | None = None
