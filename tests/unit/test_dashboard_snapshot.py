from unittest.mock import AsyncMock

import pytest

from app.api.router import api_router
from app.api.routers.dashboard_snapshot import (
    get_dashboard_snapshot,
)
from app.demo_automation.runtime import safe_demo_automation
from app.domain.demo_automation import DemoAutomationStatus
from app.domain.okx_demo import OkxDemoBalanceSnapshot
from app.domain.performance import (
    DemoPerformanceSummary,
    DemoReliabilityValidation,
)
from app.observability.runtime import demo_observability
from app.okx_demo.service import okx_demo_service
from app.performance.runtime import demo_performance


def patch_successful_sources(monkeypatch) -> None:
    monkeypatch.setattr(
        okx_demo_service,
        "balance",
        AsyncMock(
            return_value=(
                OkxDemoBalanceSnapshot.model_construct()
            )
        ),
    )

    monkeypatch.setattr(
        okx_demo_service,
        "positions",
        AsyncMock(return_value=[]),
    )

    monkeypatch.setattr(
        okx_demo_service,
        "pending_algo_orders",
        AsyncMock(return_value=[]),
    )

    monkeypatch.setattr(
        safe_demo_automation,
        "status",
        AsyncMock(
            return_value=(
                DemoAutomationStatus.model_construct()
            )
        ),
    )

    monkeypatch.setattr(
        demo_performance,
        "summary",
        AsyncMock(
            return_value=(
                DemoPerformanceSummary.model_construct()
            )
        ),
    )

    monkeypatch.setattr(
        demo_performance,
        "validation",
        AsyncMock(
            return_value=(
                DemoReliabilityValidation.model_construct()
            )
        ),
    )

    monkeypatch.setattr(
        demo_observability,
        "events",
        AsyncMock(return_value=[]),
    )


@pytest.mark.asyncio
async def test_dashboard_snapshot_is_complete(
    monkeypatch,
) -> None:
    patch_successful_sources(monkeypatch)

    snapshot = await get_dashboard_snapshot(None)

    assert snapshot.complete is True
    assert snapshot.duration_ms >= 0
    assert snapshot.snapshot_id
    assert snapshot.generated_at.tzinfo is not None

    assert set(snapshot.source_status) == {
        "balance",
        "positions",
        "algo_orders",
        "automation",
        "performance",
        "validation",
        "events",
    }

    assert all(
        status.ok
        for status in snapshot.source_status.values()
    )


@pytest.mark.asyncio
async def test_dashboard_snapshot_isolates_failure(
    monkeypatch,
) -> None:
    patch_successful_sources(monkeypatch)

    monkeypatch.setattr(
        okx_demo_service,
        "balance",
        AsyncMock(
            side_effect=RuntimeError(
                "sensitive-value-must-not-be-returned"
            )
        ),
    )

    snapshot = await get_dashboard_snapshot(None)

    assert snapshot.complete is False
    assert snapshot.balance is None
    assert snapshot.source_status["balance"].ok is False
    assert (
        snapshot.source_status["balance"].error_code
        == "RuntimeError"
    )

    serialized = snapshot.model_dump_json()

    assert "sensitive-value-must-not-be-returned" not in serialized
    assert snapshot.source_status["positions"].ok is True


def test_dashboard_snapshot_route_is_get_only() -> None:
    matching_routes = [
        route
        for route in api_router.routes
        if getattr(route, "path", None)
        == "/api/dashboard/snapshot"
    ]

    assert len(matching_routes) == 1

    methods = matching_routes[0].methods or set()

    assert "GET" in methods
    assert "POST" not in methods
    assert "PUT" not in methods
    assert "PATCH" not in methods
    assert "DELETE" not in methods
