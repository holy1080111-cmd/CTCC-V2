from fastapi import APIRouter, HTTPException, Query, status

from app.domain.orchestrator import (
    OrchestratorRunRequest,
    OrchestratorRunResult,
    OrchestratorStatus,
)
from app.orchestrator import OrchestratorBusyError, OrchestratorConfigurationError
from app.orchestrator.runtime import auto_paper_orchestrator

router = APIRouter(prefix="/api/orchestrator", tags=["auto-paper-orchestrator"])


@router.get("/status", response_model=OrchestratorStatus)
async def orchestrator_status() -> OrchestratorStatus:
    return await auto_paper_orchestrator.status()


@router.get("/history", response_model=list[OrchestratorRunResult])
async def orchestrator_history(
    limit: int = Query(default=20, ge=1, le=100),
) -> list[OrchestratorRunResult]:
    return await auto_paper_orchestrator.history(limit)


@router.post("/run-once", response_model=OrchestratorRunResult)
async def run_once(request: OrchestratorRunRequest) -> OrchestratorRunResult:
    try:
        return await auto_paper_orchestrator.run_once(
            symbols=request.symbols,
            execute=request.execute,
            trigger="manual",
        )
    except OrchestratorBusyError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except OrchestratorConfigurationError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post("/start", response_model=OrchestratorStatus)
async def start() -> OrchestratorStatus:
    try:
        return await auto_paper_orchestrator.start()
    except OrchestratorConfigurationError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post("/stop", response_model=OrchestratorStatus)
async def stop() -> OrchestratorStatus:
    return await auto_paper_orchestrator.stop()


@router.delete("/history", response_model=OrchestratorStatus)
async def clear_history() -> OrchestratorStatus:
    return await auto_paper_orchestrator.clear_history()
