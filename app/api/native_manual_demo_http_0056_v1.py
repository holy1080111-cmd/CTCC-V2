"""Manual native Demo surface; every write retains original authentication."""

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.routers import demo_automation as demo
from app.api.routers.system import liveness
from app.dashboard.native_manual_demo_0056_v1 import manual_demo_dashboard_0056_v1


async def _require_ready(request: Request):
    if getattr(
        request.app.state, "native_manual_demo_recovery_confirmed", None
    ) is not True or getattr(
        request.app.state, "native_manual_demo_database_head", None
    ) not in ("0056", "0057", "0058"):
        raise HTTPException(
            status_code=503, detail="native_manual_demo_recovery_unconfirmed"
        )


router = APIRouter(dependencies=[Depends(_require_ready)])
router.add_api_route("/liveness", liveness, methods=["GET"])
router.add_api_route(
    "/", manual_demo_dashboard_0056_v1, methods=["GET"], include_in_schema=False
)
router.add_api_route(
    "/dashboard",
    manual_demo_dashboard_0056_v1,
    methods=["GET"],
    include_in_schema=False,
)


@router.get("/readiness")
async def readiness(request: Request):
    return {
        "status": "MANUAL_DEMO_CONTROL_READY",
        "database_head": request.app.state.native_manual_demo_database_head,
        "startup_recovery_confirmed": True,
        "execution_authority": False,
    }


_ROUTES = {
    "/api/demo-automation/status": "GET",
    "/api/demo-automation/history": "GET",
    "/api/demo-automation/native/journal": "GET",
    "/api/demo-automation/stop": "POST",
    "/api/demo-automation/emergency-stop": "POST",
    "/api/demo-automation/native/arm-execute": "POST",
    "/api/demo-automation/native/entry-hold": "POST",
    "/api/demo-automation/native/configure-leverage": "POST",
    "/api/demo-automation/native/clear-stop": "POST",
    "/api/demo-automation/native/initialize-policy-counter": "POST",
    "/api/demo-automation/native/reconcile": "POST",
}
_selected = APIRouter()
_seen = set()
for _route in demo.router.routes:
    if _route.path in _ROUTES:
        if _route.methods != {_ROUTES[_route.path]} or _route.path in _seen:
            raise RuntimeError("native_manual_demo_http_scope_changed")
        _selected.routes.append(_route)
        _seen.add(_route.path)
if _seen != set(_ROUTES):
    raise RuntimeError("native_manual_demo_http_scope_changed")
router.include_router(_selected)
