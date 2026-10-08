"""The in-process guard runs before parsing caller account claims."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.database.models.qualification_ledger import QualificationReservation
from app.database.repositories import qualification_ledger as ledger
from app.database.repositories.qualification_ledger import QualificationLedgerRepository
from app.trade_qualification.reservations import LedgerScope, QualificationLedgerError


class Session:
    def __init__(self, state):
        self.state = state

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    def begin(self):
        return self

    async def get(self, model, _key):
        assert model is QualificationReservation
        return SimpleNamespace(state=self.state)


def _stub_schema_guard(monkeypatch, session):
    """These state-guard tests use a fake session; PG schema checks run elsewhere."""
    seen = []

    async def checked(actual):
        assert actual is session
        seen.append(actual)

    monkeypatch.setattr(ledger, "_require_event_journal_write_schema", checked)
    return seen


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ("consumed", "uncertain"))
async def test_post_submit_state_denies_before_caller_claims_are_read(
    state, monkeypatch
):
    now = datetime(2026, 10, 7, tzinfo=UTC)
    session = Session(state)
    schema_checks = _stub_schema_guard(monkeypatch, session)
    repo = QualificationLedgerRepository(lambda: session, clock=lambda: now)

    async def locked(_session, _scope):
        return SimpleNamespace(ledger_revision=3, updated_at=now)

    repo._locked = locked
    scope = LedgerScope(account_id="123456789", settlement_currency="USDT")
    with pytest.raises(
        QualificationLedgerError, match="post_submit_closure_witness_missing"
    ):
        await repo.reconcile_reservation(
            scope,
            "a" * 64,
            claims=object(),
            expected_revision=3,
        )
    assert schema_checks == [session]


@pytest.mark.asyncio
async def test_reserved_cancellation_keeps_exact_claim_contract(monkeypatch):
    now = datetime(2026, 10, 7, tzinfo=UTC)
    session = Session("reserved")
    schema_checks = _stub_schema_guard(monkeypatch, session)
    repo = QualificationLedgerRepository(lambda: session, clock=lambda: now)

    async def locked(_session, _scope):
        return SimpleNamespace(ledger_revision=2, updated_at=now)

    repo._locked = locked
    scope = LedgerScope(account_id="123456789", settlement_currency="USDT")
    with pytest.raises(
        QualificationLedgerError, match="exact_ledger_contract_required"
    ):
        await repo.reconcile_reservation(
            scope,
            "a" * 64,
            claims=object(),
            expected_revision=2,
        )
    assert schema_checks == [session]
