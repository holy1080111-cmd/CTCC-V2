from fastapi import APIRouter, HTTPException, Query

from app.domain.strategy import StrategyDecision
from app.exchange.okx.errors import OkxPublicApiError
from app.strategies import StrategyService

router = APIRouter(prefix="/api/strategy", tags=["strategy"])


@router.get("/{symbol:path}", response_model=StrategyDecision)
async def evaluate_strategy(
    symbol: str,
    candle_limit: int = Query(default=250, ge=200, le=300),
) -> StrategyDecision:
    try:
        return await StrategyService().evaluate(symbol, candle_limit)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OkxPublicApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
