from fastapi import APIRouter

from app.api.routers.analysis import router as analysis_router
from app.api.routers.dashboard_snapshot import (
    router as dashboard_snapshot_router,
)
from app.api.routers.demo_automation import (
    router as demo_automation_router,
)
from app.api.routers.market import router as market_router
from app.api.routers.observability import (
    router as observability_router,
)
from app.api.routers.okx_demo import router as okx_demo_router
from app.api.routers.okx_live import router as okx_live_router
from app.api.routers.orchestrator import (
    router as orchestrator_router,
)
from app.api.routers.paper import router as paper_router
from app.api.routers.performance import (
    router as performance_router,
)
from app.api.routers.realtime import router as realtime_router
from app.api.routers.recovery import router as recovery_router
from app.api.routers.risk import router as risk_router
from app.api.routers.strategy import router as strategy_router
from app.api.routers.system import router as system_router
from app.dashboard.router import router as dashboard_router


api_router = APIRouter()

_child_routers = (
    system_router,
    realtime_router,
    recovery_router,
    okx_demo_router,
    okx_live_router,
    demo_automation_router,
    observability_router,
    performance_router,
    orchestrator_router,
    paper_router,
    risk_router,
    strategy_router,
    analysis_router,
    market_router,
    dashboard_router,
    dashboard_snapshot_router,
)

for child_router in _child_routers:
    api_router.routes.extend(child_router.routes)


_registered_paths = [
    getattr(route, "path", None)
    for route in api_router.routes
]

if any(path is None for path in _registered_paths):
    raise RuntimeError(
        "api_router_contains_route_without_path"
    )

for required_path in (
    "/dashboard",
    "/api/dashboard/snapshot",
):
    if required_path not in _registered_paths:
        raise RuntimeError(
            f"required_route_missing:{required_path}"
        )
