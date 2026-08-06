from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.security import require_ctcc_token
from app.domain.observability import (
    DemoExecutionSoakPreflight,
    DemoObservabilityEventView,
    DemoObservabilityMetrics,
    DemoObservabilitySummary,
    DemoSoakSessionView,
    DemoSoakStartRequest,
    DemoSoakStopRequest,
)
from app.observability import DemoObservabilityError
from app.observability.runtime import demo_observability

router = APIRouter(prefix="/api/demo-observability", tags=["demo-observability"])


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, DemoObservabilityError):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="demo_observability_internal_error",
    )


@router.get("/summary", response_model=DemoObservabilitySummary)
async def summary(
    window_hours: int = Query(default=24, ge=1, le=720),
    _: None = Depends(require_ctcc_token),
) -> DemoObservabilitySummary:
    return await demo_observability.summary(window_hours)


@router.get("/metrics", response_model=DemoObservabilityMetrics)
async def metrics(
    window_hours: int = Query(default=24, ge=1, le=720),
    _: None = Depends(require_ctcc_token),
) -> DemoObservabilityMetrics:
    return await demo_observability.metrics(window_hours)


@router.get("/events", response_model=list[DemoObservabilityEventView])
async def events(
    limit: int = Query(default=50, ge=1, le=500),
    _: None = Depends(require_ctcc_token),
) -> list[DemoObservabilityEventView]:
    return await demo_observability.events(limit)


@router.get("/soak/status", response_model=DemoSoakSessionView)
async def soak_status(
    _: None = Depends(require_ctcc_token),
) -> DemoSoakSessionView:
    return await demo_observability.soak_status()


@router.get("/soak/preflight", response_model=DemoExecutionSoakPreflight)
async def soak_preflight(
    _: None = Depends(require_ctcc_token),
) -> DemoExecutionSoakPreflight:
    try:
        return await demo_observability.execute_preflight()
    except Exception as exc:
        raise _http_error(exc) from exc


@router.post("/soak/start", response_model=DemoSoakSessionView)
async def soak_start(
    request: DemoSoakStartRequest,
    _: None = Depends(require_ctcc_token),
) -> DemoSoakSessionView:
    try:
        return await demo_observability.start_soak(request)
    except Exception as exc:
        raise _http_error(exc) from exc


@router.post("/soak/stop", response_model=DemoSoakSessionView)
async def soak_stop(
    request: DemoSoakStopRequest,
    _: None = Depends(require_ctcc_token),
) -> DemoSoakSessionView:
    del request
    try:
        return await demo_observability.stop_soak(reason="operator_stop")
    except Exception as exc:
        raise _http_error(exc) from exc
