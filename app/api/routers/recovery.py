from fastapi import APIRouter, HTTPException, Query, status

from app.domain.recovery import AuditEntryView, RecoveryRequest, RecoveryStatus
from app.orchestrator.runtime import auto_paper_orchestrator
from app.paper import PaperPersistenceError
from app.paper.service import paper_service, persistence_repository

router = APIRouter(prefix="/api/recovery", tags=["persistence-recovery"])


@router.get("/status", response_model=RecoveryStatus)
async def recovery_status() -> RecoveryStatus:
    return await paper_service.recovery_status()


@router.post("/reconcile", response_model=RecoveryStatus)
async def reconcile(request: RecoveryRequest) -> RecoveryStatus:
    orchestrator = await auto_paper_orchestrator.status()
    if request.action != "verify" and orchestrator.running:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="stop_orchestrator_before_state_reconciliation",
        )
    try:
        return await paper_service.reconcile(request.action)
    except PaperPersistenceError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.get("/audit", response_model=list[AuditEntryView])
async def audit(limit: int = Query(default=50, ge=1, le=200)) -> list[AuditEntryView]:
    if persistence_repository is None:
        return []
    return await persistence_repository.audit_entries(limit)
