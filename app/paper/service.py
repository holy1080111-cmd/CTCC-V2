from decimal import Decimal

from app.config.settings import get_settings
from app.database.repositories.persistence import PersistenceRepository
from app.database.session import AsyncSessionFactory
from app.paper.engine import PaperBroker
from app.paper.execution_service import PaperExecutionService

settings = get_settings()

paper_broker = PaperBroker(
    starting_balance=Decimal(str(settings.paper_starting_balance)),
    taker_fee_rate=Decimal(str(settings.paper_taker_fee_rate)),
    maker_fee_rate=Decimal(str(settings.paper_maker_fee_rate)),
    slippage_bps=Decimal(str(settings.paper_slippage_bps)),
)

persistence_repository = (
    PersistenceRepository(AsyncSessionFactory)
    if settings.paper_persistence_enabled and settings.environment != "test"
    else None
)

paper_service = PaperExecutionService(
    paper_broker,
    persistence_repository,
    persist_mark_interval_seconds=settings.paper_persist_mark_interval_seconds,
)
