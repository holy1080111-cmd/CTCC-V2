from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.security import require_ctcc_token
from app.domain.okx_demo import (
    OkxDemoAccountConfig,
    OkxDemoAlgoOrderView,
    OkxDemoBalanceSnapshot,
    OkxDemoCancelRequest,
    OkxDemoCloseRequest,
    OkxDemoLeverageRequest,
    OkxDemoOrderRequest,
    OkxDemoOrderView,
    OkxDemoPositionView,
    OkxDemoReconcileResult,
    OkxDemoStatus,
    OkxDemoWriteResult,
)
from app.exchange.okx.errors import OkxPrivateApiError, OkxPublicApiError
from app.okx_demo import OkxDemoSafetyError, OkxDemoUnavailableError
from app.okx_demo.service import okx_demo_service

router = APIRouter(prefix="/api/okx-demo", tags=["okx-demo"])


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, OkxDemoSafetyError):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    if isinstance(exc, OkxDemoUnavailableError):
        code = status.HTTP_404_NOT_FOUND if "not_found" in str(exc) else status.HTTP_503_SERVICE_UNAVAILABLE
        return HTTPException(status_code=code, detail=str(exc))
    if isinstance(exc, (OkxPrivateApiError, OkxPublicApiError)):
        return HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"message": str(exc), "exchange_code": getattr(exc, "code", None)},
        )
    return HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="okx_demo_internal_error")


@router.get("/status", response_model=OkxDemoStatus)
async def demo_status() -> OkxDemoStatus:
    return await okx_demo_service.status()


@router.post("/connectivity-check", response_model=OkxDemoStatus)
async def connectivity_check(_: None = Depends(require_ctcc_token)) -> OkxDemoStatus:
    try:
        return await okx_demo_service.connectivity_check()
    except Exception as exc:
        raise _http_error(exc) from exc


@router.get("/account-config", response_model=OkxDemoAccountConfig)
async def account_config(_: None = Depends(require_ctcc_token)) -> OkxDemoAccountConfig:
    try:
        return await okx_demo_service.account_config()
    except Exception as exc:
        raise _http_error(exc) from exc


@router.get("/balance", response_model=OkxDemoBalanceSnapshot)
async def balance(_: None = Depends(require_ctcc_token)) -> OkxDemoBalanceSnapshot:
    try:
        return await okx_demo_service.balance()
    except Exception as exc:
        raise _http_error(exc) from exc


@router.get("/positions", response_model=list[OkxDemoPositionView])
async def positions(
    instrument_id: str | None = Query(default=None, max_length=40),
    _: None = Depends(require_ctcc_token),
) -> list[OkxDemoPositionView]:
    try:
        return await okx_demo_service.positions(instrument_id)
    except Exception as exc:
        raise _http_error(exc) from exc


@router.get("/orders/pending", response_model=list[OkxDemoOrderView])
async def pending_orders(
    instrument_id: str | None = Query(default=None, max_length=40),
    _: None = Depends(require_ctcc_token),
) -> list[OkxDemoOrderView]:
    try:
        return await okx_demo_service.pending_orders(instrument_id)
    except Exception as exc:
        raise _http_error(exc) from exc


@router.get("/algo-orders/pending", response_model=list[OkxDemoAlgoOrderView])
async def pending_algo_orders(
    instrument_id: str | None = Query(default=None, max_length=40),
    _: None = Depends(require_ctcc_token),
) -> list[OkxDemoAlgoOrderView]:
    try:
        return await okx_demo_service.pending_algo_orders(instrument_id)
    except Exception as exc:
        raise _http_error(exc) from exc


@router.get("/order-detail", response_model=OkxDemoOrderView)
async def order_detail(
    instrument_id: str = Query(max_length=40),
    order_id: str | None = Query(default=None, max_length=100),
    client_order_id: str | None = Query(default=None, max_length=32),
    _: None = Depends(require_ctcc_token),
) -> OkxDemoOrderView:
    try:
        return await okx_demo_service.order_detail(
            instrument_id,
            order_id=order_id,
            client_order_id=client_order_id,
        )
    except Exception as exc:
        raise _http_error(exc) from exc


@router.post("/reconcile", response_model=OkxDemoReconcileResult)
async def reconcile(_: None = Depends(require_ctcc_token)) -> OkxDemoReconcileResult:
    try:
        return await okx_demo_service.reconcile()
    except Exception as exc:
        raise _http_error(exc) from exc


@router.post("/orders", response_model=OkxDemoWriteResult, status_code=status.HTTP_201_CREATED)
async def place_order(
    request: OkxDemoOrderRequest,
    _: None = Depends(require_ctcc_token),
) -> OkxDemoWriteResult:
    try:
        return await okx_demo_service.place_order(request)
    except Exception as exc:
        raise _http_error(exc) from exc


@router.post("/orders/cancel", response_model=OkxDemoWriteResult)
async def cancel_order(
    request: OkxDemoCancelRequest,
    _: None = Depends(require_ctcc_token),
) -> OkxDemoWriteResult:
    try:
        return await okx_demo_service.cancel_order(request)
    except Exception as exc:
        raise _http_error(exc) from exc


@router.post("/positions/close", response_model=OkxDemoWriteResult)
async def close_position(
    request: OkxDemoCloseRequest,
    _: None = Depends(require_ctcc_token),
) -> OkxDemoWriteResult:
    try:
        return await okx_demo_service.close_position(request)
    except Exception as exc:
        raise _http_error(exc) from exc


@router.post("/leverage", response_model=OkxDemoWriteResult)
async def set_leverage(
    request: OkxDemoLeverageRequest,
    _: None = Depends(require_ctcc_token),
) -> OkxDemoWriteResult:
    try:
        return await okx_demo_service.set_leverage(request)
    except Exception as exc:
        raise _http_error(exc) from exc
