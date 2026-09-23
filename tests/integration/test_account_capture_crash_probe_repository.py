"""Real PostgreSQL probe wiring; actual process death is a separate Docker check."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.database.repositories.qualification_ledger import QualificationLedgerRepository
from app.trade_qualification.reservations import LedgerScope
from scripts.account_capture_durability_probe import (
    confirm_ready_account_captures,
    seed_account_captures,
    verify_account_captures,
)
from tests.integration import test_qualification_ledger_repository as fixtures

database = fixtures.database
pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_preserved_failed_raw_and_unfinished_prefix_use_independent_sessions(
    database,
):
    scope = LedgerScope(
        environment="demo",
        account_id=f"987654321{uuid4().int % 10**12:012d}",
        settlement_currency="USDT",
    )
    repository = QualificationLedgerRepository(
        database[1], clock=lambda: datetime.now(UTC)
    )
    before = await repository.initialize_capture_scope(scope)
    seed = await seed_account_captures(database[1], scope=scope)
    confirm_ready_account_captures(seed)
    await verify_account_captures(database[1], seed.records, scope=scope)
    after = await repository.read_bootstrap_checkpoint(scope)
    assert before.state == after.state
    assert before.state_sha256 == after.state_sha256
    with pytest.raises(RuntimeError, match="durable_prefix_changed"):
        await verify_account_captures(database[1], seed.records, scope=scope)
