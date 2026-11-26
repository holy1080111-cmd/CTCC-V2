"""Local-only HTTP boundary for the explicitly owned persistent Paper service."""

from __future__ import annotations

import asyncio
import hmac
import re
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool

from app.api.routers.analysis import (
    _analyze_imported_snapshot,
    _read_imported_snapshot_body,
)
from app.core.owned_paper58_runtime_v1 import (
    OwnedPaper58Runtime,
    Paper58RuntimeRefused,
    open_fresh_owned_paper58_runtime_v1,
)
from app.dashboard.router import local_analysis
from app.domain.analysis import OfflineSnapshotAnalysis
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
from app.domain.system import CapabilityResponse, LivenessResponse, VersionResponse
from app.exchange.okx.symbols import SUPPORTED_SYMBOLS
from app.paper import PaperBrokerError, PaperExecutionService, PaperPersistenceError
from app.paper.execution_service import PaperPersistenceOutcomeUnknown

_TOKEN = re.compile(r"[A-Za-z0-9_-]{48,128}\Z", re.ASCII)
_VERSION = "2.0.0"


class PaperRuntimeReadback(PaperRuntimeStateView):
    installation_binding: str


def build_owned_paper58_app_v1(
    *,
    owner_database_url: str,
    actor_database_url: str,
    ca_file: Path,
    ca_sha256: str,
    token: str,
    port: int,
) -> FastAPI:
    if (
        type(token) is not str
        or _TOKEN.fullmatch(token) is None
        or type(port) is not int
        or not 1024 <= port <= 65535
    ):
        raise Paper58RuntimeRefused("paper58_application_configuration_refused")
    local_host = f"127.0.0.1:{port}"
    installation = hmac.digest(
        token.encode("ascii"),
        b"ctcc.paper.local_runtime_installation.v1",
        "sha256",
    ).hex()
    request_lock = asyncio.Lock()

    @asynccontextmanager
    async def lifespan(app):
        async with open_fresh_owned_paper58_runtime_v1(
            owner_database_url=owner_database_url,
            actor_database_url=actor_database_url,
            ca_file=ca_file,
            ca_sha256=ca_sha256,
        ) as runtime:
            app.state.owned_paper58_runtime = runtime
            try:
                yield
            finally:
                app.state.owned_paper58_runtime = None

    app = FastAPI(
        title="CTCC Persistent Paper",
        version=_VERSION,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        redirect_slashes=False,
    )

    @app.middleware("http")
    async def loopback_only(request: Request, call_next):
        if request.headers.getlist("host") != [local_host] or request.headers.getlist(
            "origin"
        ) not in (
            [],
            [f"http://{local_host}"],
        ):
            return JSONResponse(
                {"detail": "paper58_origin_refused"},
                status_code=403,
                headers={"Cache-Control": "no-store"},
            )
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    def runtime():
        result = getattr(app.state, "owned_paper58_runtime", None)
        if type(result) is not OwnedPaper58Runtime:
            raise Paper58RuntimeRefused("paper58_runtime_unavailable")
        result.require_active()
        return result

    async def require_token(request: Request) -> None:
        supplied = request.headers.getlist("x-ctcc-token")
        if (
            len(supplied) != 1
            or _TOKEN.fullmatch(supplied[0]) is None
            or not hmac.compare_digest(supplied[0], token)
        ):
            raise HTTPException(
                status_code=401,
                detail="invalid_ctcc_api_token",
                headers={"WWW-Authenticate": "APIKey"},
            )

    async def owned_service():
        async with request_lock:
            yield runtime().service

    service_dependency = Depends(owned_service)

    async def require_expected_runtime(
        request: Request, service: PaperExecutionService = service_dependency
    ) -> None:
        expected = request.headers.getlist("x-ctcc-paper-runtime")
        if expected and (
            len(expected) != 1 or not service.matches_runtime(expected[0])
        ):
            raise HTTPException(status_code=409, detail="paper_runtime_changed")

    auth = [Depends(require_token)]
    mutate = [*auth, Depends(require_expected_runtime)]

    @app.exception_handler(Paper58RuntimeRefused)
    @app.exception_handler(SQLAlchemyError)
    async def unavailable(_request, _error):
        return JSONResponse({"detail": "paper58_runtime_unavailable"}, status_code=503)

    @app.exception_handler(PaperPersistenceError)
    @app.exception_handler(PaperBrokerError)
    async def paper_error(_request, error):
        if isinstance(error, PaperPersistenceOutcomeUnknown):
            return JSONResponse(
                {"detail": "paper_persistence_outcome_unknown"}, status_code=503
            )
        detail = str(error)
        code = 404 if detail in {"order_not_found", "position_not_found"} else 409
        return JSONResponse({"detail": detail}, status_code=code)

    @app.get("/")
    @app.get("/dashboard")
    async def redirect():
        return RedirectResponse("/dashboard/local-analysis")

    app.add_api_route("/dashboard/local-analysis", local_analysis, methods=["GET"])

    @app.get("/liveness")
    async def liveness() -> LivenessResponse:
        return LivenessResponse(version=_VERSION)

    @app.get("/readiness")
    async def readiness():
        async with request_lock:
            await runtime().probe()
        return {
            "status": "ready",
            "database_head": "0058",
            "profile": "persistent_paper",
            "execution_authority": False,
        }

    @app.get("/api/version")
    async def version() -> VersionResponse:
        return VersionResponse(
            name="CTCC",
            version=_VERSION,
            environment="production",
            trading_mode="analysis_only",
            auto_trade=False,
            live_trading=False,
            architecture_stage="persistent_paper",
        )

    @app.get("/api/capabilities")
    async def capabilities() -> CapabilityResponse:
        return CapabilityResponse(
            version=_VERSION,
            completed=["offline_snapshot_analysis", "persistent_paper"],
            not_yet_available=["venue_execution"],
        )

    @app.get("/api/market/supported-symbols")
    async def supported_symbols() -> dict[str, str]:
        return SUPPORTED_SYMBOLS

    @app.post("/api/analysis/snapshot", dependencies=auth)
    async def snapshot(request: Request) -> OfflineSnapshotAnalysis:
        raw = await _read_imported_snapshot_body(request)
        return await run_in_threadpool(_analyze_imported_snapshot, raw, _VERSION)

    @app.get("/api/paper/account", dependencies=auth)
    async def account(
        service: PaperExecutionService = service_dependency,
    ) -> PaperAccountView:
        return service.account()

    @app.get("/api/paper/state", dependencies=auth)
    async def state(
        service: PaperExecutionService = service_dependency,
    ) -> PaperStateView:
        return service.state()

    @app.get("/api/paper/runtime/state", dependencies=auth)
    async def runtime_state(
        response: Response, service: PaperExecutionService = service_dependency
    ) -> PaperRuntimeReadback:
        result = await service.runtime_state()
        response.headers["Cache-Control"] = "no-store"
        return PaperRuntimeReadback(
            **result.model_dump(), installation_binding=installation
        )

    @app.post("/api/paper/orders", status_code=201, dependencies=mutate)
    async def submit(
        request: PaperOrderRequest,
        service: PaperExecutionService = service_dependency,
    ) -> PaperOrderView:
        return await service.submit(request)

    @app.get("/api/paper/orders/{order_id}", dependencies=auth)
    async def get_order(
        order_id: UUID, service: PaperExecutionService = service_dependency
    ) -> PaperOrderView:
        return service.get_order(order_id)

    @app.post("/api/paper/orders/{order_id}/cancel", dependencies=mutate)
    async def cancel(
        order_id: UUID, service: PaperExecutionService = service_dependency
    ) -> PaperOrderView:
        return await service.cancel(order_id)

    @app.post("/api/paper/ticks", dependencies=mutate)
    async def tick(
        request: MarketTickRequest,
        service: PaperExecutionService = service_dependency,
    ) -> PaperTickResult:
        return await service.tick(
            symbol=request.symbol, price=request.price, timestamp=request.timestamp
        )

    @app.get("/api/paper/positions/{position_id}", dependencies=auth)
    async def get_position(
        position_id: UUID, service: PaperExecutionService = service_dependency
    ) -> PaperPositionView:
        return service.get_position(position_id)

    @app.post("/api/paper/positions/{position_id}/close", dependencies=mutate)
    async def close(
        position_id: UUID,
        request: ManualCloseRequest,
        service: PaperExecutionService = service_dependency,
    ) -> PaperPositionView:
        return await service.close(
            position_id, price=request.price, reason=request.reason
        )

    return app
