from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, model_validator

ARM_PHRASE = "ARM_OKX_DEMO_AUTOMATION"
DISARM_PHRASE = "DISARM_OKX_DEMO_AUTOMATION"
EXECUTE_PHRASE = "OKX_DEMO_AUTOMATION_EXECUTE"
EMERGENCY_STOP_PHRASE = "EMERGENCY_STOP_OKX_DEMO"
CLEAR_STOP_PHRASE = "CLEAR_OKX_DEMO_STOP"

DemoAutomationOutcome = Literal[
    "submitted",
    "approved_dry_run",
    "no_trade",
    "risk_rejected",
    "blocked",
    "duplicate",
    "monitoring",
    "locked",
    "error",
]


class DemoAutomationArmRequest(BaseModel):
    confirmation: Literal[ARM_PHRASE]


class DemoAutomationDisarmRequest(BaseModel):
    confirmation: Literal[DISARM_PHRASE]


class DemoAutomationEmergencyStopRequest(BaseModel):
    confirmation: Literal[EMERGENCY_STOP_PHRASE]


class DemoAutomationClearStopRequest(BaseModel):
    confirmation: Literal[CLEAR_STOP_PHRASE]


class DemoAutomationRunRequest(BaseModel):
    symbols: list[str] | None = None
    execute: bool = False
    confirmation: str | None = None

    @model_validator(mode="after")
    def require_execute_confirmation(self) -> "DemoAutomationRunRequest":
        if self.execute and self.confirmation != EXECUTE_PHRASE:
            raise ValueError(f"execute=true requires confirmation={EXECUTE_PHRASE}")
        return self


class DemoAutomationSymbolResult(BaseModel):
    symbol: str
    instrument_id: str | None = None
    outcome: DemoAutomationOutcome
    direction: Literal["long", "short"] | None = None
    strategy: str | None = None
    score: int | None = None
    reference_price: Decimal | None = None
    stop_loss: Decimal | None = None
    take_profit: Decimal | None = None
    risk_reward: Decimal | None = None
    approved_base_quantity: Decimal | None = None
    approved_contracts: Decimal | None = None
    client_order_id: str | None = None
    exchange_order_id: str | None = None
    reason_codes: list[str] = Field(default_factory=list)
    detail: str
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class DemoAutomationRunResult(BaseModel):
    trigger: Literal["manual", "scheduled"]
    execute: bool
    started_at: datetime
    completed_at: datetime
    results: list[DemoAutomationSymbolResult]
    total_equity: Decimal | None = None
    daily_pnl: Decimal = Decimal("0")
    trades_today: int = 0
    consecutive_losses: int = 0


class DemoAutomationStatus(BaseModel):
    capability_enabled: bool
    trading_mode: str
    demo_writes_enabled: bool
    armed: bool
    running: bool
    emergency_stop: bool
    locked: bool
    lock_reasons: list[str] = Field(default_factory=list)
    configuration_blockers: list[str] = Field(default_factory=list)
    symbols: list[str]
    scan_interval_seconds: int
    max_trades_per_day: int
    daily_loss_limit_pct: Decimal
    max_consecutive_losses: int
    session_date: date
    baseline_equity: Decimal | None = None
    peak_equity: Decimal | None = None
    daily_pnl: Decimal = Decimal("0")
    trades_today: int = 0
    consecutive_losses: int = 0
    active_instrument_id: str | None = None
    active_client_order_id: str | None = None
    active_started_at: datetime | None = None
    last_trade_closed_at: datetime | None = None
    last_started_at: datetime | None = None
    last_completed_at: datetime | None = None
    next_run_at: datetime | None = None
    last_error: str | None = None
    recovered: bool = False
