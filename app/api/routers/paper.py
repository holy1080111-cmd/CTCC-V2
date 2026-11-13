import hmac
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from app.api.security import require_ctcc_token
from app.config.settings import get_settings
from app.domain.paper import (
    ManualCloseRequest,
    MarketTickRequest,
    PaperAccountView,
    PaperOrderRequest,
    PaperOrderView,
    PaperPositionView,
    PaperRuntimeStateView,
    PaperStateView,
    PaperTickResult,
)
from app.paper import PaperBrokerError, PaperPersistenceError
from app.paper.execution_service import PaperPersistenceOutcomeUnknown
from app.paper.service import paper_service


async def require_expected_runtime(request: Request) -> None:
    expected = request.headers.getlist("x-ctcc-paper-runtime")
    if expected and (
        len(expected) != 1 or not paper_service.matches_runtime(expected[0])
    ):
        raise HTTPException(status_code=409, detail="paper_runtime_changed")


router = APIRouter(
    prefix="/api/paper",
    tags=["paper"],
    dependencies=[Depends(require_ctcc_token)],
)
local_paper_runtime_router = APIRouter(
    prefix="/api/paper",
    tags=["paper"],
    dependencies=[Depends(require_ctcc_token)],
)


class PaperRuntimeReadback(PaperRuntimeStateView):
    installation_binding: str


def _handle(exc: PaperBrokerError | PaperPersistenceError) -> HTTPException:
    if isinstance(exc, PaperPersistenceOutcomeUnknown):
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="paper_persistence_outcome_unknown",
        )
    code = (
        status.HTTP_404_NOT_FOUND
        if str(exc) in {"order_not_found", "position_not_found"}
        else status.HTTP_409_CONFLICT
    )
    return HTTPException(status_code=code, detail=str(exc))


@router.post(
    "/orders",
    response_model=PaperOrderView,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_expected_runtime)],
)
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


@router.post(
    "/orders/{order_id}/cancel",
    response_model=PaperOrderView,
    dependencies=[Depends(require_expected_runtime)],
)
async def cancel_order(order_id: UUID) -> PaperOrderView:
    try:
        return await paper_service.cancel(order_id)
    except (PaperBrokerError, PaperPersistenceError) as exc:
        raise _handle(exc) from exc


@router.post(
    "/ticks",
    response_model=PaperTickResult,
    dependencies=[Depends(require_expected_runtime)],
)
async def process_tick(request: MarketTickRequest) -> PaperTickResult:
    try:
        return await paper_service.tick(
            symbol=request.symbol, price=request.price, timestamp=request.timestamp
        )
    except (PaperBrokerError, PaperPersistenceError) as exc:
        raise _handle(exc) from exc


@router.get("/positions/{position_id}", response_model=PaperPositionView)
async def get_position(position_id: UUID) -> PaperPositionView:
    try:
        return paper_service.get_position(position_id)
    except (PaperBrokerError, PaperPersistenceError) as exc:
        raise _handle(exc) from exc


@router.post(
    "/positions/{position_id}/close",
    response_model=PaperPositionView,
    dependencies=[Depends(require_expected_runtime)],
)
async def close_position(
    position_id: UUID, request: ManualCloseRequest
) -> PaperPositionView:
    try:
        return await paper_service.close(
            position_id, price=request.price, reason=request.reason
        )
    except (PaperBrokerError, PaperPersistenceError) as exc:
        raise _handle(exc) from exc


@router.get("/account", response_model=PaperAccountView)
async def get_account() -> PaperAccountView:
    try:
        return paper_service.account()
    except PaperPersistenceOutcomeUnknown as exc:
        raise _handle(exc) from exc


@router.get("/state", response_model=PaperStateView)
async def get_state() -> PaperStateView:
    try:
        return paper_service.state()
    except PaperPersistenceOutcomeUnknown as exc:
        raise _handle(exc) from exc


@local_paper_runtime_router.get("/runtime/state", response_model=PaperRuntimeReadback)
async def get_runtime_state(response: Response) -> PaperRuntimeReadback:
    try:
        view = await paper_service.runtime_state()
    except PaperPersistenceOutcomeUnknown as exc:
        raise _handle(exc) from exc
    binding = hmac.digest(
        get_settings().api_token_value.encode("utf-8"),
        b"ctcc.paper.local_runtime_installation.v1",
        "sha256",
    ).hex()
    response.headers["Cache-Control"] = "no-store"
    return PaperRuntimeReadback(**view.model_dump(), installation_binding=binding)


@router.post("/reset", response_model=PaperStateView)
async def reset() -> PaperStateView:
    try:
        return await paper_service.reset()
    except PaperPersistenceError as exc:
        raise _handle(exc) from exc
