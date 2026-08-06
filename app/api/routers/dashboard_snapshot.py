from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from time import perf_counter
from typing import Any, Awaitable
from uuid import uuid4

from fastapi import APIRouter, Depends

from app.api.security import require_ctcc_token

from app.database.repositories.persistence import (
    PersistenceRepository,
)
from app.database.session import AsyncSessionFactory

from app.demo_automation.runtime import safe_demo_automation
from app.domain.dashboard import (
    DASHBOARD_SOURCE_NAMES,
    DashboardSnapshotResponse,
    DashboardSourceStatus,
)
from app.observability.runtime import demo_observability
from app.okx_demo.service import okx_demo_service
from app.performance.runtime import demo_performance


logger = logging.getLogger(__name__)

dashboard_snapshot_audit_repository = (
    PersistenceRepository(
        AsyncSessionFactory
    )
)

DASHBOARD_AUDIT_TIMEOUT_SECONDS = 2.0

router = APIRouter(
    prefix="/api/dashboard",
    tags=["dashboard-snapshot"],
)


DASHBOARD_SOURCE_TIMEOUT_SECONDS: dict[str, float] = {
    "balance": 5.0,
    "positions": 5.0,
    "algo_orders": 5.0,
    "automation": 3.0,
    "performance": 8.0,
    "validation": 8.0,
    "events": 5.0,
}


if set(DASHBOARD_SOURCE_TIMEOUT_SECONDS) != set(
    DASHBOARD_SOURCE_NAMES
):
    raise RuntimeError(
        "dashboard_timeout_contract_mismatch"
    )


def _duration_ms(started_at: float) -> int:
    return max(
        0,
        round(
            (perf_counter() - started_at) * 1000
        ),
    )


async def _collect_source(
    name: str,
    awaitable: Awaitable[Any],
) -> tuple[
    str,
    Any | None,
    DashboardSourceStatus,
]:
    monotonic_started_at = perf_counter()
    started_at = datetime.now(timezone.utc)

    timeout_seconds = (
        DASHBOARD_SOURCE_TIMEOUT_SECONDS[name]
    )

    try:
        value = await asyncio.wait_for(
            awaitable,
            timeout=timeout_seconds,
        )

        completed_at = datetime.now(timezone.utc)

        return (
            name,
            value,
            DashboardSourceStatus(
                ok=True,
                duration_ms=_duration_ms(
                    monotonic_started_at
                ),
                started_at=started_at,
                completed_at=completed_at,
            ),
        )

    except asyncio.TimeoutError:
        completed_at = datetime.now(timezone.utc)

        logger.warning(
            "dashboard_snapshot_source_timeout",
            extra={
                "dashboard_source": name,
                "timeout_seconds": timeout_seconds,
            },
        )

        return (
            name,
            None,
            DashboardSourceStatus(
                ok=False,
                duration_ms=_duration_ms(
                    monotonic_started_at
                ),
                started_at=started_at,
                completed_at=completed_at,
                timed_out=True,
                error_code="source_timeout",
            ),
        )

    except Exception as exc:
        completed_at = datetime.now(timezone.utc)

        logger.exception(
            "dashboard_snapshot_source_failed",
            extra={
                "dashboard_source": name,
            },
        )

        return (
            name,
            None,
            DashboardSourceStatus(
                ok=False,
                duration_ms=_duration_ms(
                    monotonic_started_at
                ),
                started_at=started_at,
                completed_at=completed_at,
                error_code=type(exc).__name__,
            ),
        )


async def _record_snapshot_audit(
    snapshot: DashboardSnapshotResponse,
) -> None:
    failed_sources = sorted(
        name
        for name in DASHBOARD_SOURCE_NAMES
        if not snapshot.source_status[name].ok
    )

    timed_out_sources = sorted(
        name
        for name in DASHBOARD_SOURCE_NAMES
        if snapshot.source_status[name].timed_out
    )

    try:
        await asyncio.wait_for(
            dashboard_snapshot_audit_repository
            .record_dashboard_snapshot_audit(
                snapshot_id=str(snapshot.snapshot_id),
                contract_version=(
                    snapshot.contract_version
                ),
                generated_at=snapshot.generated_at,
                duration_ms=snapshot.duration_ms,
                complete=snapshot.complete,
                failed_sources=failed_sources,
                timed_out_sources=timed_out_sources,
            ),
            timeout=DASHBOARD_AUDIT_TIMEOUT_SECONDS,
        )

    except asyncio.TimeoutError:
        logger.warning(
            "dashboard_snapshot_audit_timeout",
            extra={
                "dashboard_snapshot_id": str(
                    snapshot.snapshot_id
                ),
                "dashboard_audit_timeout_seconds": (
                    DASHBOARD_AUDIT_TIMEOUT_SECONDS
                ),
            },
        )

    except Exception:
        logger.exception(
            "dashboard_snapshot_audit_failed",
            extra={
                "dashboard_snapshot_id": str(
                    snapshot.snapshot_id
                ),
            },
        )

@router.get(
    "/snapshot",
    response_model=DashboardSnapshotResponse,
)
async def get_dashboard_snapshot(
    _: None = Depends(require_ctcc_token),
) -> DashboardSnapshotResponse:
    snapshot_started_at = perf_counter()

    source_calls: dict[str, Awaitable[Any]] = {
        "balance": okx_demo_service.balance(),

        "positions": (
            okx_demo_service.positions(None)
        ),

        "algo_orders": (
            okx_demo_service.pending_algo_orders(
                None
            )
        ),

        "automation": (
            safe_demo_automation.status()
        ),

        "performance": (
            demo_performance.summary(None)
        ),

        "validation": (
            demo_performance.validation(None)
        ),

        "events": (
            demo_observability.events(50)
        ),
    }

    if set(source_calls) != set(
        DASHBOARD_SOURCE_NAMES
    ):
        raise RuntimeError(
            "dashboard_source_call_contract_mismatch"
        )

    collected = await asyncio.gather(
        *(
            _collect_source(
                name,
                awaitable,
            )
            for name, awaitable
            in source_calls.items()
        )
    )

    values: dict[str, Any | None] = {}
    statuses: dict[
        str,
        DashboardSourceStatus,
    ] = {}

    for name, value, status in collected:
        values[name] = value
        statuses[name] = status

    complete = all(
        status.ok
        for status in statuses.values()
    )

    response = DashboardSnapshotResponse(
        contract_version="1.0",
        snapshot_id=uuid4(),
        generated_at=datetime.now(timezone.utc),
        duration_ms=_duration_ms(
            snapshot_started_at
        ),
        complete=complete,
        source_status=statuses,
        balance=values["balance"],
        positions=values["positions"] or [],
        algo_orders=values["algo_orders"] or [],
        automation=values["automation"],
        performance=values["performance"],
        validation=values["validation"],
        events=values["events"] or [],
    )

    await _record_snapshot_audit(
        response
    )

    return response