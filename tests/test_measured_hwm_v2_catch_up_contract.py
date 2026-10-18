"""The bounded historical index never becomes an account authority."""

import pytest

from app.database.repositories.account_measured_hwm_v2 import (
    AccountMeasuredHWMError,
    AccountMeasuredHWMRepository,
    MeasuredHWMHistoryReadback,
)
from app.trade_qualification.reservations import LedgerScope


def test_history_receipt_never_grants_account_or_execution_authority():
    result = MeasuredHWMHistoryReadback(b"{}", "a" * 64, 1, "b" * 64, 3, "c" * 64, 1)
    assert result.remaining_observation_rows == 2
    assert result.durable_membership_verified is False
    assert result.native_clock_verified is False
    assert result.account_complete is False
    assert result.execution_authority is False
    assert result.admission == "DENY"


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", (False, 0, 5, "1"))
async def test_invalid_page_limit_denies_before_opening_database(invalid):
    def forbidden_session():
        raise AssertionError("invalid input reached database")

    repository = AccountMeasuredHWMRepository(forbidden_session, clock=lambda: None)
    scope = LedgerScope(
        environment="demo", account_id="12345", settlement_currency="USDT"
    )
    with pytest.raises(AccountMeasuredHWMError, match="catch_up_unknown"):
        await repository.catch_up_history(
            scope,
            expected_observation_sequence=1,
            expected_observation_head_sha256="a" * 64,
            max_rows=invalid,
        )
