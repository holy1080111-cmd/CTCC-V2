"""Serve the installed DB0056 control monitor on IPv4 loopback."""

from __future__ import annotations

import asyncio
import hmac
import re
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from app.domain.source_primitives import canonical
from scripts import native_control_status_0056_v1 as status

_TOKEN = re.compile(r"[A-Za-z0-9_-]{32,128}")
_SHA = re.compile(r"[0-9a-f]{64}")
_GIT = re.compile(r"[0-9a-f]{40}")
_SOURCE_FIELDS = (
    "source_commit",
    "source_tree",
    "source_manifest_sha256",
    "deployment_admin_anchor_sha256",
)
_CONTROL_FLAGS = (
    "stored_armed",
    "emergency_stop",
    "locked",
    "restart_latch_required",
)


def _check_readback(report: dict) -> tuple[str, ...]:
    status.deployment._need(
        type(report) is dict
        and set(report)
        == {
            "status",
            "database_head",
            "control_snapshot_sha256",
            "control_revision",
            *_SOURCE_FIELDS,
            *_CONTROL_FLAGS,
        }
        and report["status"] == "CONTROL_OBSERVED"
        and report["database_head"] == "0056"
        and type(report["control_revision"]) is int
        and report["control_revision"] > 0
        and all(type(report[key]) is bool for key in _CONTROL_FLAGS)
    )
    for key in (*_SOURCE_FIELDS, "control_snapshot_sha256"):
        pattern = _GIT if key in ("source_commit", "source_tree") else _SHA
        status.deployment._need(
            type(report[key]) is str and pattern.fullmatch(report[key]) is not None
        )
    return tuple(report[key] for key in _SOURCE_FIELDS)


def build_native_control_monitor_0056(
    *, descriptor_file: Path, source_archive_file: Path, port: int
) -> FastAPI:
    status.deployment._need(type(port) is int and 1024 <= port <= 65535)
    host = f"127.0.0.1:{port}"
    origin = f"http://{host}"
    lock = asyncio.Lock()
    binding = None
    settings = None
    token = None
    writer = None

    async def observe():
        async with asyncio.timeout(240):
            report = await status.read_restricted_control_status_0056_v1(
                descriptor_file=descriptor_file, source_archive_file=source_archive_file
            )
        return report, _check_readback(report)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        nonlocal binding, settings, token, writer
        try:
            settings = status.deployment.get_settings()
            token = settings.api_token_value
            status.deployment._need(
                settings.api_token_is_safe is True
                and type(token) is str
                and _TOKEN.fullmatch(token) is not None
            )
            from app.database.session import engine

            writer = engine
            _, binding = await observe()
            yield
        finally:
            binding = None
            token = settings = None
            if writer is not None:
                try:
                    await writer.dispose()
                finally:
                    writer = None

    app = FastAPI(
        lifespan=lifespan,
        openapi_url=None,
        docs_url=None,
        redoc_url=None,
        redirect_slashes=False,
    )

    @app.middleware("http")
    async def local_requests(request: Request, call_next):
        if request.headers.getlist("host") != [host] or request.headers.getlist(
            "origin"
        ) not in ([], [origin]):
            return JSONResponse(
                status_code=403,
                content={"detail": "native_control_monitor_origin_refused"},
                headers={"Cache-Control": "no-store"},
            )
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/liveness")
    async def liveness():
        return {"status": "RUNNING"}

    @app.get("/api/demo/control")
    async def inspect(request: Request):
        nonlocal binding
        if binding is None:
            raise HTTPException(503, "native_control_monitor_unavailable")
        supplied = request.headers.getlist("x-ctcc-token")
        if (
            len(supplied) != 1
            or _TOKEN.fullmatch(supplied[0]) is None
            or not hmac.compare_digest(supplied[0], token)
        ):
            raise HTTPException(401, "invalid_ctcc_api_token")
        if lock.locked():
            raise HTTPException(429, "native_control_monitor_busy")
        async with lock:
            try:
                status.deployment._need(
                    status.deployment.get_settings() is settings
                    and settings.api_token_value == token
                )
                report, observed = await observe()
                status.deployment._need(
                    observed == binding
                    and status.deployment.get_settings() is settings
                    and settings.api_token_value == token
                )
            except Exception:  # noqa: BLE001 -- refuse private route and database information
                binding = None
                raise HTTPException(503, "native_control_monitor_unavailable") from None
        return {
            **report,
            "monitoring_only": True,
            "execution_authority": False,
            "order_writes": 0,
        }

    return app


def main(argv: list[str] | None = None) -> int:
    parser = status.deployment._PrivateParser(allow_abbrev=False, description=__doc__)
    parser.add_argument("--source-archive-file", required=True, type=Path)
    parser.add_argument("--descriptor-file", required=True, type=Path)
    parser.add_argument("--port", type=int, default=8126)
    try:
        options = vars(parser.parse_args(argv))
        app = build_native_control_monitor_0056(**options)
        from uvicorn import Config, Server

        server = Server(
            Config(
                app,
                host="127.0.0.1",
                port=options["port"],
                lifespan="on",
                proxy_headers=False,
                access_log=False,
                log_level="critical",
            )
        )

        asyncio.run(server.serve())
        status.deployment._need(server.started)
    except (Exception, KeyboardInterrupt, asyncio.CancelledError):  # noqa: BLE001 -- no private startup details
        print(
            canonical({"status": "MONITOR_UNAVAILABLE", "order_writes": 0}).decode(
                "ascii"
            )
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
