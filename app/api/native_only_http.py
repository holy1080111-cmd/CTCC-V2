"""The fixed HTTP surface for the no-execution native lifespan."""

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.api.routers.demo_automation import automation_history, automation_status
from app.api.routers.system import (
    liveness,
    native_only_readiness,
    native_only_version,
)
from app.domain.system import ReadinessResponse


async def _require_recovered(request: Request) -> None:
    if getattr(request.app.state, "native_only_recovery_confirmed", None) is not True:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="native_only_recovery_unconfirmed",
        )


router = APIRouter()
router.add_api_route("/liveness", liveness, methods=["GET"])
router.add_api_route(
    "/readiness",
    native_only_readiness,
    methods=["GET"],
    response_model=ReadinessResponse,
)
router.add_api_route("/api/version", native_only_version, methods=["GET"])
router.add_api_route(
    "/api/demo-automation/status",
    automation_status,
    methods=["GET"],
    dependencies=[Depends(_require_recovered)],
)
router.add_api_route(
    "/api/demo-automation/history",
    automation_history,
    methods=["GET"],
    dependencies=[Depends(_require_recovered)],
)
