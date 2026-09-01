from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI

from app.api.router import api_router
from app.config.settings import get_settings
from app.core.logging import configure_logging
from app.database.session import engine
from app.market.realtime_service import realtime_client
from app.paper.service import paper_service
from app.orchestrator.runtime import auto_paper_orchestrator
from app.okx_demo.service import okx_demo_service
from app.okx_live.runtime import controlled_live_automation, okx_live_service
from app.demo_automation.runtime import safe_demo_automation
from app.observability.runtime import demo_observability

configure_logging()
logger = logging.getLogger(__name__)
settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    logger.info(
        "ctcc_starting version=%s environment=%s trading_mode=%s",
        settings.app_version,
        settings.environment,
        settings.trading_mode,
    )
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
    yield
    await demo_observability.shutdown()
    await controlled_live_automation.stop()
    await okx_live_service.shutdown()
    await safe_demo_automation.stop()
    await auto_paper_orchestrator.stop()
    await realtime_client.stop()
    if settings.environment != "test":
        await paper_service.persist_now("paper_state_shutdown_checkpoint")
    await engine.dispose()
    logger.info("ctcc_stopped")


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    lifespan=lifespan,
)
app.include_router(api_router)


@app.get("/", include_in_schema=False)
async def root() -> dict[str, str]:
    return {
        "name": settings.app_name,
        "version": settings.app_version,
        "stage": "Controlled OKX Live execution boundary v1.6.9",
        "docs": "/docs",
    }
