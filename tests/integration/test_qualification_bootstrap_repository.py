"""Real DB0017 cold initialization; requires the isolated PostgreSQL harness."""

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.database.models.qualification_ledger import (
    QualificationAccountScope,
    QualificationReservation,
)
from app.database.repositories.qualification_ledger import QualificationLedgerRepository
from app.trade_qualification.reservations import LedgerScope, QualificationLedgerError
from tests.integration import test_qualification_ledger_repository as fixtures

database = fixtures.database
fixture = fixtures.fixture
pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.fixture
def scope():
    return LedgerScope(
        account_id=f"123456789{uuid4().int % 10**12:012d}", settlement_currency="USDT"
    )


async def test_cold_start_persists_only_unknown_and_reads_in_separate_session(
    database, scope
):
    now = datetime.now(UTC)
    repository = QualificationLedgerRepository(database[1], clock=lambda: now)
    result = await repository.initialize_capture_scope(scope)
    assert not result.account_initialized and not result.execution_authority
    assert result.state.account_revision == result.state.ledger_revision == 0
    assert result.state.claims_sha256 is None and result.state.active == ()
    restarted = QualificationLedgerRepository(database[1], clock=lambda: now)
    assert (
        await restarted.read_bootstrap_checkpoint(scope)
    ).state_sha256 == result.state_sha256
    async with database[1]() as session:
        row = await session.get(
            QualificationAccountScope, ("demo", scope.account_id, "USDT")
        )
        assert row.claims_json is None and row.claims_sha256 is None
        assert (
            await session.scalar(
                select(func.count())
                .select_from(QualificationReservation)
                .where(QualificationReservation.account_id == scope.account_id)
            )
            == 0
        )
    with pytest.raises(QualificationLedgerError, match="ledger_account_claims_missing"):
        await repository.read_capture_checkpoint(scope)


async def test_concurrent_cold_initializers_never_publish_a_complete_revision(
    database, scope
):
    now = datetime.now(UTC)
    repositories = [
        QualificationLedgerRepository(database[1], clock=lambda: now) for _ in range(2)
    ]
    values = await asyncio.gather(
        *(r.initialize_capture_scope(scope) for r in repositories)
    )
    assert values[0].state_sha256 == values[1].state_sha256
    assert all(
        not value.account_initialized and not value.execution_authority
        for value in values
    )
    async with database[1]() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(QualificationAccountScope)
                .where(QualificationAccountScope.account_id == scope.account_id)
            )
            == 1
        )


async def test_bootstrap_never_overwrites_initialized_claims_or_active_event(
    database, fixture
):
    repository, clock = await fixtures.initialize(database, fixture)
    hold = await repository.reserve(fixture.request)
    before = await repository.read_scope(hold.scope)
    result = await repository.initialize_capture_scope(hold.scope)
    assert result.account_initialized and result.state == before
    assert result.state.active == (hold,)
    assert (await repository.read_scope(hold.scope)) == before
    restarted = QualificationLedgerRepository(database[1], clock=clock)
    assert (await restarted.initialize_capture_scope(hold.scope)).state == before


async def test_unknown_scope_cannot_reserve_using_only_a_bootstrap_checkpoint(
    database, fixture
):
    repository = QualificationLedgerRepository(database[1], clock=lambda: fixture.now)
    await repository.initialize_capture_scope(fixture.request.scope)
    with pytest.raises(QualificationLedgerError):
        await repository.reserve(fixture.request)
    result = await repository.read_bootstrap_checkpoint(fixture.request.scope)
    assert result.state.account_revision == result.state.ledger_revision == 0
    assert not result.state.active
