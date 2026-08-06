from fastapi import APIRouter, HTTPException, Query

from app.analysis import AnalysisService
from app.domain.analysis import MultiTimeframeAnalysis
from app.exchange.okx.errors import OkxPublicApiError

router = APIRouter(prefix="/api/analysis", tags=["analysis"])


@router.get("/{symbol:path}", response_model=MultiTimeframeAnalysis)
async def analyze(symbol: str, candle_limit: int = Query(default=250, ge=200, le=300)) -> MultiTimeframeAnalysis:
    try:
        return await AnalysisService().analyze(symbol, candle_limit)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OkxPublicApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
