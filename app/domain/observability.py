from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

START_OBSERVE_PHRASE = "START_DEMO_SOAK_OBSERVE"
START_EXECUTE_PHRASE = "START_DEMO_SOAK_EXECUTE"
STOP_SOAK_PHRASE = "STOP_DEMO_SOAK"

ObservabilitySeverity = Literal["info", "warning", "critical"]
SoakSessionState = Literal[
    "idle",
    "running",
    "completed",
    "stopped",
    "safety_stopped",
    "interrupted",
    "error",
]


class DemoSoakStartRequest(BaseModel):
    execute: bool = False
    duration_minutes: int | None = Field(default=None, ge=1, le=10_080)
    interval_seconds: int | None = Field(default=None, ge=1, le=86_400)
    max_runs: int | None = Field(default=None, ge=1, le=10_000)
    symbols: list[str] | None = None
    confirmation: str

    @model_validator(mode="after")
    def validate_confirmation(self) -> "DemoSoakStartRequest":
        expected = START_EXECUTE_PHRASE if self.execute else START_OBSERVE_PHRASE
        if self.confirmation != expected:
            raise ValueError(f"confirmation must equal {expected}")
        return self


class DemoSoakStopRequest(BaseModel):
    confirmation: Literal[STOP_SOAK_PHRASE]


class DemoObservabilityEventView(BaseModel):
    id: UUID | None = None
    severity: ObservabilitySeverity
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    observed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class DemoExecutionSoakPreflight(BaseModel):
    ready: bool
    blockers: list[str] = Field(default_factory=list)
    execute_soak_enabled: bool
    demo_writes_enabled: bool
    automation_capability_enabled: bool
    automation_armed: bool
    automation_locked: bool
    automation_emergency_stop: bool
    automation_configuration_blockers: list[str] = Field(default_factory=list)
    exchange_position_count: int = 0
    exchange_pending_order_count: int = 0
    exchange_algo_order_count: int = 0
    # Account-wide totalEq remains diagnostic only. Execute-soak loss controls
    # use the explicitly resolved strategy risk-equity basis below.
    total_equity: Decimal | None = None
    risk_equity: Decimal | None = None
    equity_basis: str | None = None
    equity_currency: str | None = None
    execution_order_type: Literal["fok"] = "fok"
    execution_max_adverse_slippage_bps: Decimal = Field(
        default=Decimal("5"), ge=0
    )
    minimum_execution_risk_reward: Decimal = Field(
        default=Decimal("1.8"), gt=0
    )
    require_flat_start: bool
    require_protection: bool
    auto_disarm: bool
    max_submissions: int
    loss_limit_pct: Decimal
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class DemoSoakSessionView(BaseModel):
    id: UUID | None = None
    state: SoakSessionState = "idle"
    execute: bool = False
    symbols: list[str] = Field(default_factory=list)
    interval_seconds: int = 0
    duration_minutes: int = 0
    max_runs: int = 0
    max_submissions: int = 0
    started_at: datetime | None = None
    planned_end_at: datetime | None = None
    stopped_at: datetime | None = None
    completed_runs: int = 0
    submitted_runs: int = 0
    dry_run_runs: int = 0
    blocked_runs: int = 0
    error_runs: int = 0
    consecutive_errors: int = 0
    equity_basis: str | None = None
    equity_currency: str | None = None
    starting_equity: Decimal | None = None
    latest_equity: Decimal | None = None
    session_pnl: Decimal = Decimal("0")
    max_drawdown_pct_observed: Decimal = Decimal("0")
    protection_checks: int = 0
    protection_failures: int = 0
    active_position_count: int = 0
    active_pending_order_count: int = 0
    active_algo_order_count: int = 0
    protection_verified: bool | None = None
    auto_disarmed: bool = False
    last_run_at: datetime | None = None
    last_outcome: str | None = None
    stop_reason: str | None = None
    safety_stop_reason: str | None = None
    last_error: str | None = None


class DemoObservabilityMetrics(BaseModel):
    window_hours: int
    total_runs: int = 0
    manual_runs: int = 0
    scheduled_runs: int = 0
    execute_runs: int = 0
    dry_runs: int = 0
    submitted: int = 0
    approved_dry_run: int = 0
    no_trade: int = 0
    risk_rejected: int = 0
    blocked: int = 0
    duplicate: int = 0
    monitoring: int = 0
    locked: int = 0
    errors: int = 0
    last_run_at: datetime | None = None


class DemoObservabilitySummary(BaseModel):
    status: Literal["healthy", "degraded", "critical"]
    process_started_at: datetime
    uptime_seconds: int
    recovered: bool
    watchdog_running: bool
    last_heartbeat_at: datetime
    websocket_enabled: bool
    websocket_running: bool
    websocket_connected: bool
    websocket_connection_count: int
    websocket_reconnect_count: int
    websocket_message_count: int
    websocket_parse_error_count: int
    websocket_last_message_at: datetime | None = None
    automation_armed: bool
    automation_running: bool
    automation_locked: bool
    automation_emergency_stop: bool
    automation_last_completed_at: datetime | None = None
    soak: DemoSoakSessionView
    metrics: DemoObservabilityMetrics
    alerts: list[DemoObservabilityEventView] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
