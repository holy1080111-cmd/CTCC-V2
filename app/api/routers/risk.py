from fastapi import APIRouter

from app.domain.risk import RiskDecision, RiskEvaluationRequest
from app.risk import RiskService

router = APIRouter(prefix="/api/risk", tags=["risk"])


@router.post("/evaluate", response_model=RiskDecision)
async def evaluate_risk(request: RiskEvaluationRequest) -> RiskDecision:
    return RiskService().evaluate(
        candidate=request.candidate,
        account=request.account,
        limits=request.limits,
    )
