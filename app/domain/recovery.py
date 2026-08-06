from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


RecoveryAction = Literal["verify", "restore_from_database", "persist_memory"]


class RecoveryRequest(BaseModel):
    action: RecoveryAction = "verify"


class RecoveryStatus(BaseModel):
    persistence_enabled: bool
    initialized: bool
    recovered: bool
    consistent: bool | None = None
    memory_checksum: str | None = None
    database_checksum: str | None = None
    order_count: int = 0
    position_count: int = 0
    orchestrator_history_count: int = 0
    fingerprint_count: int = 0
    last_recovered_at: datetime | None = None
    last_persisted_at: datetime | None = None
    last_error: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class AuditEntryView(BaseModel):
    action: str
    resource_type: str
    resource_id: str | None = None
    actor: str
    after: dict[str, Any] | None = None
    occurred_at: datetime
