from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from app.domain.paper import (
    ManualCloseRequest,
    MarketTickRequest,
    PaperAccountView,
    PaperOrderRequest,
    PaperOrderView,
    PaperPositionView,
    PaperStateView,
    PaperTickResult,
)
from app.paper import PaperBrokerError, PaperPersistenceError
from app.paper.service import paper_service

router = APIRouter(prefix="/api/paper", tags=["paper"])


def _handle(exc: PaperBrokerError | PaperPersistenceError) -> HTTPException:
    code = status.HTTP_404_NOT_FOUND if str(exc) in {"order_not_found", "position_not_found"} else status.HTTP_409_CONFLICT
    return HTTPException(status_code=code, detail=str(exc))


@router.post("/orders", response_model=PaperOrderView, status_code=status.HTTP_201_CREATED)
async def submit_order(request: PaperOrderRequest) -> PaperOrderView:
    try:
        return await paper_service.submit(request)
    except (PaperBrokerError, PaperPersistenceError) as exc:
        raise _handle(exc) from exc


@router.get("/orders/{order_id}", response_model=PaperOrderView)
async def get_order(order_id: UUID) -> PaperOrderView:
    try:
        return paper_service.get_order(order_id)
    except (PaperBrokerError, PaperPersistenceError) as exc:
        raise _handle(exc) from exc


@router.post("/orders/{order_id}/cancel", response_model=PaperOrderView)
async def cancel_order(order_id: UUID) -> PaperOrderView:
    try:
        return await paper_service.cancel(order_id)
    except (PaperBrokerError, PaperPersistenceError) as exc:
        raise _handle(exc) from exc


@router.post("/ticks", response_model=PaperTickResult)
async def process_tick(request: MarketTickRequest) -> PaperTickResult:
    try:
        return await paper_service.tick(symbol=request.symbol, price=request.price, timestamp=request.timestamp)
    except PaperPersistenceError as exc:
        raise _handle(exc) from exc


@router.get("/positions/{position_id}", response_model=PaperPositionView)
async def get_position(position_id: UUID) -> PaperPositionView:
    try:
        return paper_service.get_position(position_id)
    except (PaperBrokerError, PaperPersistenceError) as exc:
        raise _handle(exc) from exc


@router.post("/positions/{position_id}/close", response_model=PaperPositionView)
async def close_position(position_id: UUID, request: ManualCloseRequest) -> PaperPositionView:
    try:
        return await paper_service.close(position_id, price=request.price, reason=request.reason)
    except (PaperBrokerError, PaperPersistenceError) as exc:
        raise _handle(exc) from exc


@router.get("/account", response_model=PaperAccountView)
async def get_account() -> PaperAccountView:
    return paper_service.account()


@router.get("/state", response_model=PaperStateView)
async def get_state() -> PaperStateView:
    return paper_service.state()


@router.post("/reset", response_model=PaperStateView)
async def reset() -> PaperStateView:
    try:
        return await paper_service.reset()
    except PaperPersistenceError as exc:
        raise _handle(exc) from exc
