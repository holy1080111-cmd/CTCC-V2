import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

import app.api.routers.dashboard_snapshot as snapshot_module
from app.demo_automation.runtime import safe_demo_automation
from app.domain.dashboard import (
    DASHBOARD_SOURCE_NAMES,
    DashboardSnapshotResponse,
    DashboardSourceStatus,
)
from app.domain.demo_automation import DemoAutomationStatus
from app.domain.okx_demo import OkxDemoBalanceSnapshot
from app.domain.performance import (
    DemoPerformanceSummary,
    DemoReliabilityValidation,
)
from app.observability.runtime import demo_observability
from app.okx_demo.service import okx_demo_service
from app.performance.runtime import demo_performance


def patch_successful_sources(
    monkeypatch,
) -> None:
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
async def test_snapshot_contract_version_and_sources(
    monkeypatch,
) -> None:
    patch_successful_sources(monkeypatch)

    snapshot = await (
        snapshot_module.get_dashboard_snapshot(None)
    )

    assert snapshot.contract_version == "1.0"
    assert set(snapshot.source_status) == set(
        DASHBOARD_SOURCE_NAMES
    )
    assert snapshot.complete is True

    for status in snapshot.source_status.values():
        assert status.ok is True
        assert status.timed_out is False
        assert status.error_code is None
        assert status.duration_ms >= 0
        assert status.started_at.utcoffset() is not None
        assert status.completed_at.utcoffset() is not None
        assert status.completed_at >= status.started_at


@pytest.mark.asyncio
async def test_snapshot_timeout_is_isolated(
    monkeypatch,
) -> None:
    patch_successful_sources(monkeypatch)

    async def slow_balance():
        await asyncio.sleep(0.05)

        return (
            OkxDemoBalanceSnapshot.model_construct()
        )

    monkeypatch.setattr(
        okx_demo_service,
        "balance",
        slow_balance,
    )

    monkeypatch.setitem(
        snapshot_module.
        DASHBOARD_SOURCE_TIMEOUT_SECONDS,
        "balance",
        0.01,
    )

    snapshot = await (
        snapshot_module.get_dashboard_snapshot(None)
    )

    balance_status = (
        snapshot.source_status["balance"]
    )

    assert snapshot.complete is False
    assert snapshot.balance is None

    assert balance_status.ok is False
    assert balance_status.timed_out is True
    assert (
        balance_status.error_code
        == "source_timeout"
    )

    assert (
        snapshot.source_status["positions"].ok
        is True
    )

    serialized = snapshot.model_dump_json()

    assert "slow_balance" not in serialized
    assert "Traceback" not in serialized


def test_snapshot_contract_rejects_complete_mismatch(
) -> None:
    now = datetime.now(timezone.utc)

    statuses = {
        name: DashboardSourceStatus(
            ok=True,
            duration_ms=0,
            started_at=now,
            completed_at=now,
        )
        for name in DASHBOARD_SOURCE_NAMES
    }

    statuses["balance"] = DashboardSourceStatus(
        ok=False,
        duration_ms=0,
        started_at=now,
        completed_at=now,
        error_code="RuntimeError",
    )

    with pytest.raises(
        ValidationError,
        match="complete_does_not_match_source_status",
    ):
        DashboardSnapshotResponse(
            contract_version="1.0",
            snapshot_id=uuid4(),
            generated_at=now,
            duration_ms=0,
            complete=True,
            source_status=statuses,
            balance=None,
            automation=(
                DemoAutomationStatus.model_construct()
            ),
            performance=(
                DemoPerformanceSummary.model_construct()
            ),
            validation=(
                DemoReliabilityValidation.model_construct()
            ),
        )