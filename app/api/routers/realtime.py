from fastapi import APIRouter, HTTPException, status

from app.domain.realtime import RealtimeSnapshot, RealtimeStatus
from app.market.realtime_service import realtime_client, realtime_hub

router = APIRouter(prefix="/api/realtime", tags=["realtime-market"])


@router.get("/status", response_model=RealtimeStatus)
async def realtime_status() -> RealtimeStatus:
    return realtime_client.status()


@router.get("/snapshots", response_model=list[RealtimeSnapshot])
async def snapshots() -> list[RealtimeSnapshot]:
    return await realtime_hub.snapshots()


@router.get("/snapshots/{symbol}", response_model=RealtimeSnapshot)
async def snapshot(symbol: str) -> RealtimeSnapshot:
    result = await realtime_hub.snapshot(symbol)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="realtime_snapshot_not_available")
    return result


@router.post("/start", response_model=RealtimeStatus)
async def start() -> RealtimeStatus:
    await realtime_client.start()
    return realtime_client.status()


@router.post("/stop", response_model=RealtimeStatus)
async def stop() -> RealtimeStatus:
    await realtime_client.stop()
    return realtime_client.status()
