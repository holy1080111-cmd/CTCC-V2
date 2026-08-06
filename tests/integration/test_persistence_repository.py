from decimal import Decimal

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config.settings import get_settings
from app.database.models.operations import AuditLog
from app.database.models.persistence import PaperAccountState, PaperOrderState, PaperPositionState
from app.database.repositories.persistence import PersistenceRepository
from app.domain.paper import PaperOrderRequest
from app.paper.engine import PaperBroker


@pytest.mark.integration
@pytest.mark.asyncio
async def test_paper_state_round_trip_and_audit() -> None:
    settings = get_settings()
    test_engine = create_async_engine(settings.database_url, poolclass=NullPool)
    Session = async_sessionmaker(test_engine, expire_on_commit=False, autoflush=False)
    repository = PersistenceRepository(Session)

    async def cleanup() -> None:
        async with Session() as session:
            async with session.begin():
                await session.execute(delete(PaperPositionState))
                await session.execute(delete(PaperOrderState))
                await session.execute(delete(PaperAccountState))
                await session.execute(delete(AuditLog).where(AuditLog.action == "test_paper_state_saved"))

    try:
        await cleanup()
        broker = PaperBroker()
        broker.submit(
            PaperOrderRequest(
                symbol="BTC-USDT-SWAP",
                side="long",
                quantity=Decimal("1"),
                reference_price=Decimal("100"),
                stop_loss=Decimal("95"),
                take_profit=Decimal("110"),
                strategy="integration_test",
                score=80,
            )
        )
        expected = broker.state()
        await repository.save_paper_state(expected, action="test_paper_state_saved")
        actual = await repository.load_paper_state()

        assert actual == expected
        audits = await repository.audit_entries(10)
        assert any(item.action == "test_paper_state_saved" for item in audits)
    finally:
        await cleanup()
        await test_engine.dispose()
