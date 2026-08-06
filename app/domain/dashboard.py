from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)

from app.domain.demo_automation import DemoAutomationStatus
from app.domain.observability import DemoObservabilityEventView
from app.domain.okx_demo import (
    OkxDemoAlgoOrderView,
    OkxDemoBalanceSnapshot,
    OkxDemoPositionView,
)
from app.domain.performance import (
    DemoPerformanceSummary,
    DemoReliabilityValidation,
)


DASHBOARD_SOURCE_NAMES: tuple[str, ...] = (
    "balance",
    "positions",
    "algo_orders",
    "automation",
    "performance",
    "validation",
    "events",
)


class DashboardSourceStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool
    duration_ms: int = Field(ge=0)

    started_at: datetime
    completed_at: datetime

    timed_out: bool = False
    error_code: str | None = None

    @model_validator(mode="after")
    def validate_source_status(
        self,
    ) -> "DashboardSourceStatus":
        if self.started_at.utcoffset() is None:
            raise ValueError(
                "source_started_at_must_be_timezone_aware"
            )

        if self.completed_at.utcoffset() is None:
            raise ValueError(
                "source_completed_at_must_be_timezone_aware"
            )

        if self.completed_at < self.started_at:
            raise ValueError(
                "source_completed_before_started"
            )

        if self.ok:
            if self.timed_out:
                raise ValueError(
                    "successful_source_cannot_time_out"
                )

            if self.error_code is not None:
                raise ValueError(
                    "successful_source_cannot_have_error"
                )

        if not self.ok and self.error_code is None:
            raise ValueError(
                "failed_source_requires_error_code"
            )

        if (
            self.timed_out
            and self.error_code != "source_timeout"
        ):
            raise ValueError(
                "timed_out_source_requires_timeout_code"
            )

        return self


class DashboardSnapshotResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["1.0"] = "1.0"

    snapshot_id: UUID
    generated_at: datetime
    duration_ms: int = Field(ge=0)
    complete: bool

    source_status: dict[
        str,
        DashboardSourceStatus,
    ]

    balance: OkxDemoBalanceSnapshot | None = None

    positions: list[OkxDemoPositionView] = Field(
        default_factory=list
    )

    algo_orders: list[OkxDemoAlgoOrderView] = Field(
        default_factory=list
    )

    automation: DemoAutomationStatus | None = None
    performance: DemoPerformanceSummary | None = None
    validation: DemoReliabilityValidation | None = None

    events: list[DemoObservabilityEventView] = Field(
        default_factory=list
    )

    @model_validator(mode="after")
    def validate_snapshot_contract(
        self,
    ) -> "DashboardSnapshotResponse":
        if self.generated_at.utcoffset() is None:
            raise ValueError(
                "generated_at_must_be_timezone_aware"
            )

        expected_sources = set(
            DASHBOARD_SOURCE_NAMES
        )

        actual_sources = set(
            self.source_status
        )

        if actual_sources != expected_sources:
            raise ValueError(
                "dashboard_source_contract_mismatch"
            )

        calculated_complete = all(
            status.ok
            for status in self.source_status.values()
        )

        if self.complete != calculated_complete:
            raise ValueError(
                "complete_does_not_match_source_status"
            )

        scalar_sources = {
            "balance": self.balance,
            "automation": self.automation,
            "performance": self.performance,
            "validation": self.validation,
        }

        for source_name, value in scalar_sources.items():
            status = self.source_status[source_name]

            if status.ok and value is None:
                raise ValueError(
                    f"successful_source_missing_value:"
                    f"{source_name}"
                )

            if not status.ok and value is not None:
                raise ValueError(
                    f"failed_source_contains_value:"
                    f"{source_name}"
                )

        list_sources = {
            "positions": self.positions,
            "algo_orders": self.algo_orders,
            "events": self.events,
        }

        for source_name, value in list_sources.items():
            status = self.source_status[source_name]

            if not status.ok and value:
                raise ValueError(
                    f"failed_source_contains_items:"
                    f"{source_name}"
                )

        return self