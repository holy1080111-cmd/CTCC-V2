from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from app.domain.paper import PaperAccountView

OrchestratorOutcome = Literal[
    "submitted",
    "approved_dry_run",
    "no_trade",
    "risk_rejected",
    "blocked",
    "duplicate",
    "error",
]


class OrchestratorRunRequest(BaseModel):
    symbols: list[str] | None = None
    execute: bool = False


class OrchestratorSymbolResult(BaseModel):
    symbol: str
    instrument_id: str | None = None
    outcome: OrchestratorOutcome
    strategy_decision: Literal["long", "short", "no_trade"] | None = None
    selected_strategy: str | None = None
    score: int | None = None
    candidate_entry: Decimal | None = None
    reference_price: Decimal | None = None
    risk_decision: Literal["approved", "rejected"] | None = None
    risk_reason_codes: list[str] = Field(default_factory=list)
    approved_quantity: Decimal | None = None
    order_id: UUID | None = None
    client_order_id: str | None = None
    detail: str
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class OrchestratorRunResult(BaseModel):
    run_id: UUID = Field(default_factory=uuid4)
    trigger: Literal["manual", "scheduled"]
    execute: bool
    started_at: datetime
    completed_at: datetime
    results: list[OrchestratorSymbolResult]
    account_after: PaperAccountView


class OrchestratorStatus(BaseModel):
    enabled: bool
    running: bool
    busy: bool
    trading_mode: str
    interval_seconds: int
    symbols: list[str]
    scan_count: int
    submission_count: int
    skipped_count: int
    error_count: int
    last_started_at: datetime | None = None
    last_completed_at: datetime | None = None
    next_run_at: datetime | None = None
    last_error: str | None = None
    last_run: OrchestratorRunResult | None = None
