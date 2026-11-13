import asyncio
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from starlette.concurrency import run_in_threadpool

from app.analysis import AnalysisService
from app.analysis.service import analyze_snapshot_at
from app.api.security import require_ctcc_token
from app.config.settings import get_settings
from app.domain.analysis import MultiTimeframeAnalysis, OfflineSnapshotAnalysis
from app.domain.market import MarketSnapshot
from app.domain.source_primitives import MAX_RAW, decode, sha
from app.exchange.okx.errors import OkxPublicApiError
from app.exchange.okx.symbols import to_canonical_symbol, to_instrument_id
from app.trade_qualification.data import _market_copy, _validate_market_quotes

router = APIRouter(prefix="/api/analysis", tags=["analysis"])


@router.post(
    "/snapshot",
    response_model=OfflineSnapshotAnalysis,
    dependencies=[Depends(require_ctcc_token)],
    summary="Analyze an imported market snapshot offline",
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": {"type": "object"},
                }
            },
            "description": "An unmodified MarketSnapshot JSON document.",
        }
    },
)
async def analyze_imported_snapshot(request: Request) -> OfflineSnapshotAnalysis:
    raw = await _read_imported_snapshot_body(request)
    return await run_in_threadpool(
        _analyze_imported_snapshot, raw, get_settings().app_version
    )


async def _read_imported_snapshot_body(request: Request) -> bytes:
    """Shared bounded upload; callers choose their own authentication source."""
    media_type = request.headers.get("content-type", "").partition(";")[0]
    if media_type.strip().lower() != "application/json":
        raise HTTPException(status_code=415, detail="snapshot_json_required")
    body = bytearray()
    try:
        async with asyncio.timeout(10):
            async for chunk in request.stream():
                if len(body) + len(chunk) > MAX_RAW:
                    raise HTTPException(status_code=413, detail="snapshot_size_limit")
                body.extend(chunk)
    except TimeoutError:
        raise HTTPException(status_code=408, detail="snapshot_body_timeout") from None
    return bytes(body)


def _analyze_imported_snapshot(raw: bytes, version: str) -> OfflineSnapshotAnalysis:
    try:
        decode(raw, MAX_RAW)
        market = _market_copy(
            MarketSnapshot.model_validate_json(raw, strict=True, extra="forbid")
        )
        instrument = market.instrument_id
        if (
            instrument != to_instrument_id(instrument)
            or market.symbol != to_canonical_symbol(instrument)
            or market.ticker.instrument_id != instrument
            or market.order_book.instrument_id != instrument
            or market.mark_price <= 0
            or market.received_at > datetime.now(UTC)
            or market.ticker.timestamp > market.received_at
            or market.order_book.timestamp > market.received_at
        ):
            raise ValueError("market identity or chronology invalid")
        _validate_market_quotes(market)
        analysis = analyze_snapshot_at(
            market, evaluated_at=market.received_at, version=version
        )
    except (ValueError, ArithmeticError):
        raise HTTPException(status_code=422, detail="snapshot_invalid") from None
    return OfflineSnapshotAnalysis(
        input_sha256=sha(raw),
        analysis=analysis.model_copy(
            update={
                "trade_ready": False,
                "blockers": sorted(
                    {*analysis.blockers, "imported_snapshot_unverified"}
                ),
            }
        ),
    )


@router.get("/{symbol:path}", response_model=MultiTimeframeAnalysis)
async def analyze(
    symbol: str, candle_limit: int = Query(default=250, ge=200, le=300)
) -> MultiTimeframeAnalysis:
    try:
        return await AnalysisService().analyze(symbol, candle_limit)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OkxPublicApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
