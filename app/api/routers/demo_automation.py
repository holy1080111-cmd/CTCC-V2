from fastapi import APIRouter, Depends, Header, HTTPException, Query, status

from app.api.security import require_ctcc_token
from app.demo_automation import DemoAutomationBusyError, DemoAutomationSafetyError
from app.demo_automation.native_requests_v2 import (
    NativeDemoActionResultV2,
    NativeDemoArmExecuteRequestV2,
    NativeDemoClearStopRequestV2,
    NativeDemoConfigureLeverageRequestV1,
    NativeDemoConfigureLeverageResultV1,
    NativeDemoCounterInitializationRequestV2,
    NativeDemoFilledCompleteResultV3,
    NativeDemoRecoveryRequestV1,
    NativeDemoRecoveryResultV1,
    NativeDemoZeroCompleteResultV3,
)
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


def _require_qualified_dispatch() -> None:
    # No source-owned QualifiedDispatchCoordinator can issue a current invocation
    # permit. An Arm or execute confirmation alone grants no write authority.
    raise DemoAutomationSafetyError("demo_qualified_dispatch_coordinator_unavailable")


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
        _require_qualified_dispatch()
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
        if request.execute:
            _require_qualified_dispatch()
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


async def _perform_native_request(request, operator_token):
    # The exact header is reauthenticated in this same request task. Positive
    # owners/clocks/factories/UID/claims/evidence roots never enter through HTTP.
    await require_ctcc_token(operator_token)
    try:
        return await safe_demo_automation.perform_native_action_v2(
            request, operator_token=operator_token
        )
    except HTTPException:
        raise
    except DemoAutomationBusyError:
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED, detail="native_demo_run_in_progress"
        ) from None
    except Exception:  # noqa: BLE001 - never serialize account/source exception data.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="native_demo_action_failed_or_unknown",
        ) from None


@router.post(
    "/native/arm-execute",
    response_model=NativeDemoActionResultV2
    | NativeDemoFilledCompleteResultV3
    | NativeDemoZeroCompleteResultV3,
)
async def native_arm_execute(
    request: NativeDemoArmExecuteRequestV2,
    operator_token: str | None = Header(default=None, alias="X-CTCC-Token"),
    _: None = Depends(require_ctcc_token),
) -> (
    NativeDemoActionResultV2
    | NativeDemoFilledCompleteResultV3
    | NativeDemoZeroCompleteResultV3
):
    return await _perform_native_request(request, operator_token)


@router.post(
    "/native/configure-leverage", response_model=NativeDemoConfigureLeverageResultV1
)
async def native_configure_leverage(
    request: NativeDemoConfigureLeverageRequestV1,
    operator_token: str | None = Header(default=None, alias="X-CTCC-Token"),
    _: None = Depends(require_ctcc_token),
) -> NativeDemoConfigureLeverageResultV1:
    return await _perform_native_request(request, operator_token)


@router.post("/native/clear-stop", response_model=NativeDemoActionResultV2)
async def native_clear_stop(
    request: NativeDemoClearStopRequestV2,
    operator_token: str | None = Header(default=None, alias="X-CTCC-Token"),
    _: None = Depends(require_ctcc_token),
) -> NativeDemoActionResultV2:
    return await _perform_native_request(request, operator_token)


@router.post(
    "/native/initialize-policy-counter", response_model=NativeDemoActionResultV2
)
async def native_initialize_policy_counter(
    request: NativeDemoCounterInitializationRequestV2,
    operator_token: str | None = Header(default=None, alias="X-CTCC-Token"),
    _: None = Depends(require_ctcc_token),
) -> NativeDemoActionResultV2:
    return await _perform_native_request(request, operator_token)


@router.post("/native/reconcile", response_model=NativeDemoRecoveryResultV1)
async def native_reconcile(
    request: NativeDemoRecoveryRequestV1,
    operator_token: str | None = Header(default=None, alias="X-CTCC-Token"),
    _: None = Depends(require_ctcc_token),
) -> NativeDemoRecoveryResultV1:
    await require_ctcc_token(operator_token)
    try:
        return await safe_demo_automation.reconcile_native_owned_v1(
            request, operator_token=operator_token
        )
    except HTTPException:
        raise
    except DemoAutomationBusyError:
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED, detail="native_demo_run_in_progress"
        ) from None
    except Exception:  # noqa: BLE001 -- no private account/source exception text leaves DATA recovery.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="native_demo_reconciliation_failed_or_unknown",
        ) from None
