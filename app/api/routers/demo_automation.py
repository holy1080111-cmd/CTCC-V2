from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.security import require_ctcc_token
from app.demo_automation import DemoAutomationBusyError, DemoAutomationSafetyError
from app.demo_automation.runtime import safe_demo_automation
from app.domain.demo_automation import (
    DemoAutomationArmRequest,
    DemoAutomationClearStopRequest,
    DemoAutomationDisarmRequest,
    DemoAutomationEmergencyStopRequest,
    DemoAutomationRunRequest,
    DemoAutomationRunResult,
    DemoAutomationStatus,
)

router = APIRouter(prefix="/api/demo-automation", tags=["demo-automation"])


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, DemoAutomationSafetyError):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    if isinstance(exc, DemoAutomationBusyError):
        return HTTPException(status_code=status.HTTP_423_LOCKED, detail=str(exc))
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="demo_automation_internal_error",
    )


@router.get("/status", response_model=DemoAutomationStatus)
async def automation_status(
    _: None = Depends(require_ctcc_token),
) -> DemoAutomationStatus:
    return await safe_demo_automation.status()


@router.get("/history", response_model=list[DemoAutomationRunResult])
async def automation_history(
    limit: int = Query(default=20, ge=1, le=100),
    _: None = Depends(require_ctcc_token),
) -> list[DemoAutomationRunResult]:
    return await safe_demo_automation.history(limit)


@router.post("/arm", response_model=DemoAutomationStatus)
async def arm(
    request: DemoAutomationArmRequest,
    _: None = Depends(require_ctcc_token),
) -> DemoAutomationStatus:
    del request
    try:
        return await safe_demo_automation.arm()
    except Exception as exc:
        raise _http_error(exc) from exc


@router.post("/disarm", response_model=DemoAutomationStatus)
async def disarm(
    request: DemoAutomationDisarmRequest,
    _: None = Depends(require_ctcc_token),
) -> DemoAutomationStatus:
    del request
    try:
        return await safe_demo_automation.disarm()
    except Exception as exc:
        raise _http_error(exc) from exc


@router.post("/start", response_model=DemoAutomationStatus)
async def start(_: None = Depends(require_ctcc_token)) -> DemoAutomationStatus:
    try:
        return await safe_demo_automation.start()
    except Exception as exc:
        raise _http_error(exc) from exc


@router.post("/stop", response_model=DemoAutomationStatus)
async def stop(_: None = Depends(require_ctcc_token)) -> DemoAutomationStatus:
    try:
        return await safe_demo_automation.stop()
    except Exception as exc:
        raise _http_error(exc) from exc


@router.post("/run-once", response_model=DemoAutomationRunResult)
async def run_once(
    request: DemoAutomationRunRequest,
    _: None = Depends(require_ctcc_token),
) -> DemoAutomationRunResult:
    try:
        return await safe_demo_automation.run_once(
            symbols=request.symbols,
            execute=request.execute,
            trigger="manual",
        )
    except Exception as exc:
        raise _http_error(exc) from exc


@router.post("/emergency-stop", response_model=DemoAutomationStatus)
async def emergency_stop(
    request: DemoAutomationEmergencyStopRequest,
    _: None = Depends(require_ctcc_token),
) -> DemoAutomationStatus:
    del request
    try:
        return await safe_demo_automation.emergency_stop()
    except Exception as exc:
        raise _http_error(exc) from exc


@router.post("/clear-emergency-stop", response_model=DemoAutomationStatus)
async def clear_emergency_stop(
    request: DemoAutomationClearStopRequest,
    _: None = Depends(require_ctcc_token),
) -> DemoAutomationStatus:
    del request
    try:
        return await safe_demo_automation.clear_emergency_stop()
    except Exception as exc:
        raise _http_error(exc) from exc
