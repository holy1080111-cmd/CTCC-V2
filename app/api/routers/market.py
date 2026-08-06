from fastapi import APIRouter, HTTPException, Query

from app.domain.market import Candle, InstrumentInfo, MarketSnapshot
from app.exchange.okx.errors import OkxPublicApiError
from app.exchange.okx.public_rest import OkxPublicRestClient
from app.exchange.okx.symbols import SUPPORTED_SYMBOLS, to_instrument_id
from app.market.service import MarketDataService, SUPPORTED_BARS

router = APIRouter(prefix="/api/market", tags=["market"])


@router.get("/supported-symbols")
async def supported_symbols() -> dict[str, str]:
    return SUPPORTED_SYMBOLS


@router.get("/instruments/{symbol:path}", response_model=list[InstrumentInfo])
async def instrument(symbol: str) -> list[InstrumentInfo]:
    try:
        return await OkxPublicRestClient().instruments(to_instrument_id(symbol))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OkxPublicApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/candles/{symbol:path}", response_model=list[Candle])
async def candles(
    symbol: str,
    bar: str = Query(default="5m"),
    limit: int = Query(default=100, ge=20, le=300),
    confirmed_only: bool = Query(default=True),
) -> list[Candle]:
    if bar not in SUPPORTED_BARS:
        raise HTTPException(status_code=400, detail=f"bar must be one of {SUPPORTED_BARS}")
    try:
        rows = await OkxPublicRestClient().candles(to_instrument_id(symbol), bar, limit)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OkxPublicApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return [row for row in rows if row.confirmed] if confirmed_only else rows


@router.get("/snapshot/{symbol:path}", response_model=MarketSnapshot)
async def snapshot(
    symbol: str,
    candle_limit: int = Query(default=100, ge=50, le=300),
) -> MarketSnapshot:
    try:
        return await MarketDataService().snapshot(symbol, candle_limit)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OkxPublicApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
