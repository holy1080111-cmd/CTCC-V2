from app.config.settings import get_settings
from app.exchange.okx.private_rest import (
    OkxLiveExecutionRestClient,
    OkxLivePrivateRestClient,
)
from app.exchange.okx.public_rest import OkxPublicRestClient
from app.okx_live.service import OkxLiveService
from app.okx_live.automation import ControlledLiveAutomation


settings = get_settings()

if settings.environment != "test":
    from app.database.repositories.okx_live import OkxLiveRepository
    from app.database.repositories.okx_live_execution import (
        OkxLiveExecutionRepository,
    )
    from app.database.session import AsyncSessionFactory

    mirror_repository = OkxLiveRepository(AsyncSessionFactory)
    execution_repository = OkxLiveExecutionRepository(AsyncSessionFactory)
else:
    mirror_repository = None
    execution_repository = None


okx_live_service = OkxLiveService(
    OkxLivePrivateRestClient(settings=settings),
    OkxPublicRestClient(),
    mirror_repository,
    execution_client=OkxLiveExecutionRestClient(settings=settings),
    execution_repository=execution_repository,
    settings=settings,
)

controlled_live_automation = ControlledLiveAutomation(
    okx_live_service,
    settings=settings,
)
