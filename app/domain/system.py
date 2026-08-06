from datetime import datetime, timezone

from pydantic import BaseModel, Field


class DependencyStatus(BaseModel):
    ok: bool
    detail: str


class LivenessResponse(BaseModel):
    status: str = "alive"
    version: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ReadinessResponse(BaseModel):
    status: str
    version: str
    database: DependencyStatus
    redis: DependencyStatus
    blockers: list[str]
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class VersionResponse(BaseModel):
    name: str
    version: str
    environment: str
    trading_mode: str
    auto_trade: bool
    live_trading: bool
    architecture_stage: str = "demo_soak_observability"


class CapabilityResponse(BaseModel):
    version: str
    completed: list[str]
    not_yet_available: list[str]
