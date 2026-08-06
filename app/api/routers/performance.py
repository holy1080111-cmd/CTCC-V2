from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status

from app.api.security import require_ctcc_token
from app.domain.performance import (
    DemoDailyPerformanceReport,
    DemoEquityPoint,
    DemoPerformanceSummary,
    DemoReliabilityValidation,
    DemoStrategyControlView,
    StrategyControlRequest,
)
from app.performance.runtime import demo_performance
from app.performance.service import DemoPerformanceError

router = APIRouter(prefix="/api/demo-performance", tags=["demo-performance"])


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, DemoPerformanceError):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="demo_performance_internal_error",
    )


@router.get("/summary", response_model=DemoPerformanceSummary)
async def summary(
    window_days: int | None = Query(default=None, ge=1, le=365),
    _: None = Depends(require_ctcc_token),
) -> DemoPerformanceSummary:
    try:
        return await demo_performance.summary(window_days)
    except Exception as exc:
        raise _http_error(exc) from exc


@router.get("/validation", response_model=DemoReliabilityValidation)
async def validation(
    window_days: int | None = Query(default=None, ge=1, le=365),
    _: None = Depends(require_ctcc_token),
) -> DemoReliabilityValidation:
    try:
        return await demo_performance.validation(window_days)
    except Exception as exc:
        raise _http_error(exc) from exc


@router.post("/snapshot/capture", response_model=DemoEquityPoint)
async def capture_snapshot(
    _: None = Depends(require_ctcc_token),
) -> DemoEquityPoint:
    try:
        return await demo_performance.capture_snapshot()
    except Exception as exc:
        raise _http_error(exc) from exc


@router.get("/daily/{report_date}", response_model=DemoDailyPerformanceReport)
async def daily_report(
    report_date: date,
    refresh: bool = Query(default=True),
    _: None = Depends(require_ctcc_token),
) -> DemoDailyPerformanceReport:
    try:
        return await demo_performance.daily_report(report_date, refresh=refresh)
    except Exception as exc:
        raise _http_error(exc) from exc


@router.get("/strategies", response_model=list[DemoStrategyControlView])
async def strategy_controls(
    _: None = Depends(require_ctcc_token),
) -> list[DemoStrategyControlView]:
    try:
        return await demo_performance.strategy_controls()
    except Exception as exc:
        raise _http_error(exc) from exc


@router.post("/strategies/{strategy}/disable", response_model=DemoStrategyControlView)
async def disable_strategy(
    request: StrategyControlRequest,
    strategy: str = Path(min_length=3, max_length=80),
    _: None = Depends(require_ctcc_token),
) -> DemoStrategyControlView:
    try:
        return await demo_performance.disable_strategy(strategy, request)
    except Exception as exc:
        raise _http_error(exc) from exc


@router.post("/strategies/{strategy}/enable", response_model=DemoStrategyControlView)
async def enable_strategy(
    request: StrategyControlRequest,
    strategy: str = Path(min_length=3, max_length=80),
    _: None = Depends(require_ctcc_token),
) -> DemoStrategyControlView:
    try:
        return await demo_performance.enable_strategy(strategy, request)
    except Exception as exc:
        raise _http_error(exc) from exc
