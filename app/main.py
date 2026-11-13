import logging
from asyncio import CancelledError
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI

from app.api.native_only_http import router as native_only_api_router
from app.api.router import api_router
from app.config.settings import get_settings
from app.core.logging import configure_logging
from app.core.native_only_lifespan import (
    require_native_only_settings,
    start_native_only,
)
from app.core.native_role_preflight_v1 import preflight_configured_native_roles_v1
from app.database.session import AsyncSessionFactory, engine
from app.demo_automation.runtime import safe_demo_automation
from app.market.realtime_service import realtime_client
from app.observability.runtime import demo_observability
from app.okx_demo.service import okx_demo_service
from app.okx_live.runtime import controlled_live_automation, okx_live_service
from app.orchestrator.runtime import auto_paper_orchestrator
from app.paper.service import paper_service
from app.trade_evidence.runtime import NotionOutboxRuntime
from app.trade_evidence.submission_runtime import SubmissionOutboxRuntime

configure_logging()
logger = logging.getLogger(__name__)
settings = get_settings()
notion_outbox_runtime = NotionOutboxRuntime(settings)
submission_outbox_runtime = SubmissionOutboxRuntime(settings)


@asynccontextmanager
async def lifespan(instance: FastAPI):
    logger.info(
        "ctcc_starting version=%s environment=%s trading_mode=%s",
        settings.app_version,
        settings.environment,
        settings.trading_mode,
    )
    if settings.ctcc_native_only_lifespan_v1:
        instance.state.native_only_recovery_confirmed = False
        try:
            # Actual restricted role proof precedes the first recovery CAS.
            # There is no owner-credential or shell-only fallback in this path.
            require_native_only_settings(settings)
            await preflight_configured_native_roles_v1(AsyncSessionFactory)
            await start_native_only(settings, AsyncSessionFactory, safe_demo_automation)
        except BaseException:
            # No service stop before recovery. Keep the original fixed refusal
            # or cancellation even when startup engine disposal fails.
            with suppress(Exception, CancelledError):
                await engine.dispose()
            raise
        instance.state.native_only_recovery_confirmed = True
        try:
            yield
        finally:
            instance.state.native_only_recovery_confirmed = False
            try:
                await safe_demo_automation.stop()
            finally:
                await engine.dispose()
        return

    if settings.environment != "test":
        await paper_service.recover()
        await auto_paper_orchestrator.recover()
    if settings.okx_ws_enabled and settings.environment != "test":
        await realtime_client.start()
    if settings.paper_auto_execution and settings.environment != "test":
        await auto_paper_orchestrator.start()
    if settings.environment != "test":
        await okx_demo_service.startup()
        await okx_live_service.startup()
        await safe_demo_automation.recover()
        await demo_observability.recover()
        await demo_observability.start_monitoring()
    await notion_outbox_runtime.start()
    await submission_outbox_runtime.start()
    try:
        yield
    finally:
        try:
            await demo_observability.shutdown()
            await controlled_live_automation.stop()
            await okx_live_service.shutdown()
            await safe_demo_automation.stop()
            await auto_paper_orchestrator.stop()
            await realtime_client.stop()
            if settings.environment != "test":
                await paper_service.persist_now("paper_state_shutdown_checkpoint")
            await engine.dispose()
        finally:
            try:
                await notion_outbox_runtime.stop()
            finally:
                await submission_outbox_runtime.stop()
        logger.info("ctcc_stopped")


async def root() -> dict[str, str]:
    return {
        "name": settings.app_name,
        "version": settings.app_version,
        "stage": "qualification_execution_unavailable",
        "docs": "/docs",
    }


def _build_app() -> FastAPI:
    native_only = settings.ctcc_native_only_lifespan_v1
    docs_options = (
        {
            "openapi_url": None,
            "docs_url": None,
            "redoc_url": None,
            "redirect_slashes": False,
        }
        if native_only
        else {}
    )
    instance = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        lifespan=lifespan,
        **docs_options,
    )
    instance.state.notion_outbox_runtime = notion_outbox_runtime
    instance.state.submission_outbox_runtime = submission_outbox_runtime
    if native_only:
        instance.include_router(native_only_api_router)
    else:
        instance.include_router(api_router)
        instance.add_api_route("/", root, methods=["GET"], include_in_schema=False)
    return instance


app = _build_app()
