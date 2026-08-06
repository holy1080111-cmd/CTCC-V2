from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config.settings import get_settings
from app.database.models.analysis import AnalysisRun, StrategyEvaluation
from app.database.models.trading import TradeCandidate
from app.database.repositories.lifecycle import SqlAlchemyLifecycleRepository
from app.domain.enums import LifecycleState
from app.domain.errors import DomainError


@pytest.mark.integration
@pytest.mark.asyncio
async def test_lifecycle_persists_and_uses_optimistic_version() -> None:
    settings = get_settings()
    test_engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        async with test_engine.connect() as connection:
            transaction = await connection.begin()
            Session = async_sessionmaker(bind=connection, expire_on_commit=False, autoflush=False)
            try:
                async with Session() as session:
                    run = AnalysisRun(symbol="BTC/USDT:USDT", status="completed", input_timeframes=["5m"], data_quality={})
                    session.add(run)
                    await session.flush()
                    evaluation = StrategyEvaluation(
                        analysis_run_id=run.id,
                        strategy_name="test_strategy",
                        direction="long",
                        eligible=True,
                        score=80,
                        completion_ratio=Decimal("1"),
                        passed_conditions=["test"],
                        failed_conditions=[],
                        vetoes=[],
                        score_breakdown={"test": 80},
                        config_version="test",
                    )
                    session.add(evaluation)
                    await session.flush()
                    candidate = TradeCandidate(
                        strategy_evaluation_id=evaluation.id,
                        client_candidate_id=f"test-{uuid4().hex}",
                        symbol="BTC/USDT:USDT",
                        side="long",
                        status="created",
                        score=80,
                        entry_price=Decimal("100"),
                        stop_loss=Decimal("95"),
                        take_profit=Decimal("110"),
                        risk_reward=Decimal("2"),
                        reasons=["integration-test"],
                    )
                    session.add(candidate)
                    await session.flush()
                    repository = SqlAlchemyLifecycleRepository(session)
                    lifecycle = await repository.add(candidate.id)
                    updated = await repository.transition(
                        lifecycle.id,
                        expected_version=1,
                        target=LifecycleState.RISK_APPROVED,
                    )
                    assert updated.state == LifecycleState.RISK_APPROVED
                    assert updated.version == 2
                    with pytest.raises(DomainError):
                        await repository.transition(
                            lifecycle.id,
                            expected_version=1,
                            target=LifecycleState.SUBMITTED,
                        )
            finally:
                if transaction.is_active:
                    await transaction.rollback()
    finally:
        await test_engine.dispose()
