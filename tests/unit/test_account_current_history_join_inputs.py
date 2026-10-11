"""Local input bounds fail before a database or exchange call."""

import pytest

from app.database.repositories.account_current_history_observation import (
    AccountCurrentHistoryObservationRepository,
    CurrentHistoryObservationError,
)
from app.trade_qualification.reservations import LedgerScope


@pytest.mark.asyncio
async def test_invalid_sequence_or_digest_never_opens_database():
    def forbidden_session():
        raise AssertionError("database must not open")

    repo = AccountCurrentHistoryObservationRepository(
        forbidden_session, clock=lambda: None
    )
    scope = LedgerScope(account_id="123456789", settlement_currency="USDT")
    for history_sequence, history_sha in ((0, "a" * 64), (1, "not-a-digest")):
        with pytest.raises(CurrentHistoryObservationError):
            await repo.read_joined(
                scope,
                history_sequence=history_sequence,
                history_sha256=history_sha,
                current_sequence=1,
                current_sha256="b" * 64,
            )
