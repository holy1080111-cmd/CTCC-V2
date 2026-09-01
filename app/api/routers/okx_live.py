import re

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.security import require_ctcc_token
from app.database.repositories.okx_live import OkxLiveRepositoryError
from app.database.repositories.okx_live_execution import (
    OkxLiveExecutionRepositoryError,
)
from app.domain.okx_live import (
    OkxLiveAccountSummary,
    OkxLiveAlgoOrderSummary,
    OkxLiveArmRequest,
    OkxLiveAutomationRunRequest,
    OkxLiveAutomationRunResult,
    OkxLiveAutomationStatus,
    OkxLiveBalanceSummary,
    OkxLiveCancelRequest,
    OkxLiveClearStopRequest,
    OkxLiveCloseRequest,
    OkxLiveDisarmRequest,
    OkxLiveEmergencyStopRequest,
    OkxLiveIntentResolutionExpectation,
    OkxLiveLeverageRequest,
    OkxLiveOrderRequest,
    OkxLiveOrderSummary,
    OkxLivePositionSummary,
    OkxLiveReconcileSummary,
    OkxLiveStatus,
    OkxLiveWriteResult,
)
from app.exchange.okx.errors import OkxPrivateApiError, OkxPublicApiError
from app.okx_live import OkxLiveBusyError, OkxLiveSafetyError, OkxLiveUnavailableError
from app.okx_live.runtime import controlled_live_automation, okx_live_service


router = APIRouter(
    prefix="/api/okx-live",
    tags=["okx-live"],
    dependencies=[Depends(require_ctcc_token)],
)


def _safe_exchange_code(exc: Exception) -> str | None:
    value = str(getattr(exc, "code", "") or "")
    return value if re.fullmatch(r"[A-Za-z0-9_]{1,32}", value) else None


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, OkxLiveSafetyError):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    if isinstance(exc, OkxLiveBusyError):
        return HTTPException(status_code=status.HTTP_423_LOCKED, detail=str(exc))
    if isinstance(
        exc,
        (
            OkxLiveUnavailableError,
            OkxLiveRepositoryError,
            OkxLiveExecutionRepositoryError,
        ),
    ):
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="okx_live_service_unavailable",
        )
    if isinstance(exc, (OkxPrivateApiError, OkxPublicApiError)):
        return HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "message": "okx_live_exchange_request_failed",
                "exchange_code": _safe_exchange_code(exc),
            },
        )
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="okx_live_internal_error",
    )


@router.get("/status", response_model=OkxLiveStatus)
async def live_status() -> OkxLiveStatus:
    return await okx_live_service.status()


@router.post("/connectivity-check", response_model=OkxLiveStatus)
async def connectivity_check() -> OkxLiveStatus:
    try:
        return await okx_live_service.connectivity_check()
    except Exception as exc:
        raise _http_error(exc) from exc


@router.get("/account-config", response_model=OkxLiveAccountSummary)
async def account_config() -> OkxLiveAccountSummary:
    try:
        value = await okx_live_service.account_config()
        return okx_live_service.account_summary(value)
    except Exception as exc:
        raise _http_error(exc) from exc


@router.get("/balance", response_model=OkxLiveBalanceSummary)
async def balance() -> OkxLiveBalanceSummary:
    try:
        value = await okx_live_service.balance()
        return okx_live_service.balance_summary(value)
    except Exception as exc:
        raise _http_error(exc) from exc


@router.get("/positions", response_model=list[OkxLivePositionSummary])
async def positions(
    instrument_id: str | None = Query(default=None, max_length=40),
) -> list[OkxLivePositionSummary]:
    try:
        values = await okx_live_service.positions(instrument_id)
        return [okx_live_service.position_summary(item) for item in values]
    except Exception as exc:
        raise _http_error(exc) from exc


@router.get("/orders/pending", response_model=list[OkxLiveOrderSummary])
async def pending_orders(
    instrument_id: str | None = Query(default=None, max_length=40),
) -> list[OkxLiveOrderSummary]:
    try:
        values = await okx_live_service.pending_orders(instrument_id)
        return [okx_live_service.order_summary(item) for item in values]
    except Exception as exc:
        raise _http_error(exc) from exc


@router.get("/algo-orders/pending", response_model=list[OkxLiveAlgoOrderSummary])
async def pending_algo_orders(
    instrument_id: str | None = Query(default=None, max_length=40),
) -> list[OkxLiveAlgoOrderSummary]:
    try:
        values = await okx_live_service.pending_algo_orders(instrument_id)
        return [okx_live_service.algo_summary(item) for item in values]
    except Exception as exc:
        raise _http_error(exc) from exc


@router.get("/order-detail", response_model=OkxLiveOrderSummary)
async def order_detail(
    instrument_id: str = Query(max_length=40),
    order_id: str | None = Query(default=None, max_length=100),
    client_order_id: str | None = Query(default=None, max_length=32),
) -> OkxLiveOrderSummary:
    try:
        value = await okx_live_service.order_detail(
            instrument_id,
            order_id=order_id,
            client_order_id=client_order_id,
        )
        return okx_live_service.order_summary(value)
    except Exception as exc:
        raise _http_error(exc) from exc


@router.post("/reconcile", response_model=OkxLiveReconcileSummary)
async def reconcile() -> OkxLiveReconcileSummary:
    try:
        value = await okx_live_service.reconcile()
        return okx_live_service.reconcile_summary(value)
    except Exception as exc:
        raise _http_error(exc) from exc


@router.post("/arm", response_model=OkxLiveStatus)
async def arm(request: OkxLiveArmRequest) -> OkxLiveStatus:
    try:
        return await okx_live_service.arm(request)
    except Exception as exc:
        raise _http_error(exc) from exc


@router.post("/disarm", response_model=OkxLiveStatus)
async def disarm(request: OkxLiveDisarmRequest) -> OkxLiveStatus:
    del request
    try:
        await controlled_live_automation.stop()
        return await okx_live_service.disarm()
    except Exception as exc:
        raise _http_error(exc) from exc


@router.post("/emergency-stop", response_model=OkxLiveStatus)
async def emergency_stop(request: OkxLiveEmergencyStopRequest) -> OkxLiveStatus:
    del request
    try:
        await controlled_live_automation.stop()
        return await okx_live_service.emergency_stop()
    except Exception as exc:
        raise _http_error(exc) from exc


@router.post("/clear-emergency-stop", response_model=OkxLiveStatus)
async def clear_emergency_stop(request: OkxLiveClearStopRequest) -> OkxLiveStatus:
    try:
        return await okx_live_service.clear_emergency_stop(request)
    except Exception as exc:
        raise _http_error(exc) from exc


@router.get(
    "/execution-intents/unresolved",
    response_model=list[OkxLiveIntentResolutionExpectation],
)
async def unresolved_execution_intents(
) -> list[OkxLiveIntentResolutionExpectation]:
    try:
        return await okx_live_service.unresolved_intent_expectations()
    except Exception as exc:
        raise _http_error(exc) from exc


@router.post(
    "/orders",
    response_model=OkxLiveWriteResult,
    status_code=status.HTTP_201_CREATED,
)
async def place_order(request: OkxLiveOrderRequest) -> OkxLiveWriteResult:
    try:
        return await okx_live_service.place_order(request)
    except Exception as exc:
        raise _http_error(exc) from exc


@router.post("/orders/cancel", response_model=OkxLiveWriteResult)
async def cancel_order(request: OkxLiveCancelRequest) -> OkxLiveWriteResult:
    try:
        return await okx_live_service.cancel_order(request)
    except Exception as exc:
        raise _http_error(exc) from exc


@router.post("/positions/close", response_model=OkxLiveWriteResult)
async def close_position(request: OkxLiveCloseRequest) -> OkxLiveWriteResult:
    try:
        return await okx_live_service.close_position(request)
    except Exception as exc:
        raise _http_error(exc) from exc


@router.post("/leverage", response_model=OkxLiveWriteResult)
async def set_leverage(request: OkxLiveLeverageRequest) -> OkxLiveWriteResult:
    try:
        return await okx_live_service.set_leverage(request)
    except Exception as exc:
        raise _http_error(exc) from exc


@router.get("/automation/status", response_model=OkxLiveAutomationStatus)
async def automation_status() -> OkxLiveAutomationStatus:
    return await controlled_live_automation.status()


@router.post("/automation/start", response_model=OkxLiveAutomationStatus)
async def automation_start(
    request: OkxLiveAutomationRunRequest,
) -> OkxLiveAutomationStatus:
    if not request.execute:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="automation_start_requires_execute_true",
        )
    try:
        return await controlled_live_automation.start(symbols=request.symbols)
    except Exception as exc:
        raise _http_error(exc) from exc


@router.post("/automation/stop", response_model=OkxLiveAutomationStatus)
async def automation_stop() -> OkxLiveAutomationStatus:
    return await controlled_live_automation.stop()


@router.post("/automation/run-once", response_model=OkxLiveAutomationRunResult)
async def automation_run_once(
    request: OkxLiveAutomationRunRequest,
) -> OkxLiveAutomationRunResult:
    try:
        return await controlled_live_automation.run_once(
            symbols=request.symbols,
            execute=request.execute,
            trigger="manual",
        )
    except Exception as exc:
        raise _http_error(exc) from exc
