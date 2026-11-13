"""Explicit localhost-only ASGI app with no trading or storage runtime."""

from __future__ import annotations

import hmac
import re

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

from app.api.routers.analysis import (
    _analyze_imported_snapshot,
    _read_imported_snapshot_body,
)
from app.dashboard.offline_snapshot_only_page import (
    OFFLINE_SNAPSHOT_ONLY_HEADERS,
    OFFLINE_SNAPSHOT_ONLY_HTML,
)
from app.domain.analysis import OfflineSnapshotAnalysis

_COMMIT = re.compile(r"[0-9a-f]{40}\Z", re.ASCII)
_TOKEN = re.compile(r"[A-Za-z0-9_-]{48,128}\Z", re.ASCII)


def build_offline_snapshot_only_app(
    *, token: bytes, source_commit: str, port: int
) -> FastAPI:
    """Build only three local routes with an explicitly supplied private token."""
    if type(token) is not bytes:
        raise ValueError("offline_snapshot_only_configuration_refused")
    try:
        expected = token.decode("ascii")
    except UnicodeDecodeError:
        raise ValueError("offline_snapshot_only_configuration_refused") from None
    if (
        _TOKEN.fullmatch(expected) is None
        or type(source_commit) is not str
        or _COMMIT.fullmatch(source_commit) is None
        or type(port) is not int
        or not 1024 <= port <= 65535
    ):
        raise ValueError("offline_snapshot_only_configuration_refused")
    base = f"http://127.0.0.1:{port}"
    version = f"offline-snapshot-only+{source_commit[:12]}"
    app = FastAPI(
        title="CTCC Offline Snapshot Analysis",
        openapi_url=None,
        docs_url=None,
        redoc_url=None,
        redirect_slashes=False,
    )

    @app.middleware("http")
    async def local_origin_only(request: Request, call_next):
        hosts = request.headers.getlist("host")
        origins = request.headers.getlist("origin")
        if hosts != [f"127.0.0.1:{port}"] or (origins != [] and origins != [base]):
            return JSONResponse(
                status_code=403,
                content={"detail": "offline_snapshot_origin_refused"},
                headers={"Cache-Control": "no-store"},
            )
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/dashboard", response_class=HTMLResponse)
    async def dashboard() -> HTMLResponse:
        return HTMLResponse(
            OFFLINE_SNAPSHOT_ONLY_HTML, headers=OFFLINE_SNAPSHOT_ONLY_HEADERS.copy()
        )

    @app.get("/readiness")
    async def readiness() -> dict[str, object]:
        return {
            "status": "OFFLINE_SNAPSHOT_ONLY",
            "source_commit": source_commit,
            "source_authenticity_verified": False,
            "execution_authority": False,
        }

    @app.post("/api/analysis/snapshot", response_model=OfflineSnapshotAnalysis)
    async def analyze_snapshot(request: Request) -> OfflineSnapshotAnalysis:
        supplied = request.headers.getlist("x-ctcc-token")
        if (
            len(supplied) != 1
            or len(supplied[0]) > 256
            or not supplied[0].isascii()
            or not hmac.compare_digest(supplied[0], expected)
        ):
            raise HTTPException(status_code=401, detail="invalid_ctcc_api_token")
        raw = await _read_imported_snapshot_body(request)
        return await run_in_threadpool(_analyze_imported_snapshot, raw, version)

    return app
