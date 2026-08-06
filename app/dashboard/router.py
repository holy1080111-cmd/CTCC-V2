from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from app.dashboard.page import DASHBOARD_HTML


router = APIRouter(
    tags=["dashboard"],
    include_in_schema=False,
)


DASHBOARD_SECURITY_HEADERS = {
    "Cache-Control": "no-store, max-age=0",
    "Pragma": "no-cache",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Permissions-Policy": (
        "camera=(), microphone=(), geolocation=(), "
        "payment=(), usb=(), browsing-topics=()"
    ),
    "Content-Security-Policy": (
        "default-src 'self'; "
        "connect-src 'self'; "
        "img-src 'self' data:; "
        "style-src 'self' 'unsafe-inline'; "
        "script-src 'self' 'unsafe-inline'; "
        "object-src 'none'; "
        "base-uri 'none'; "
        "form-action 'none'; "
        "frame-ancestors 'none'"
    ),
}


@router.get(
    "/dashboard",
    response_class=HTMLResponse,
)
async def dashboard() -> HTMLResponse:
    return HTMLResponse(
        content=DASHBOARD_HTML,
        headers=DASHBOARD_SECURITY_HEADERS.copy(),
    )
