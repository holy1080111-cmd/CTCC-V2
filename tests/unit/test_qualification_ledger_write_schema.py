"""Schema drift must stop reservation/intent writes under the UID lock."""

from types import SimpleNamespace

import pytest

from app.database.repositories import qualification_ledger as ledger
from app.trade_qualification.demo_control import ControlScope, account_lock_key
from app.trade_qualification.reservations import LedgerScope, QualificationLedgerError
from tests.unit.qualification_ledger_fixtures import ledger_fixture


class _Session:
    def __init__(self):
        self.statements = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    def begin(self):
        return self

    def get_bind(self):
        return SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))

    async def execute(self, statement):
        self.statements.append(str(statement))


@pytest.fixture
def source():
    return ledger_fixture(account_id="12345678901234567890")


def test_write_schema_uses_same_uid_lock_across_currency_and_control(source):
    scope = source.request.scope
    other_currency = LedgerScope(
        environment=scope.environment,
        account_id=scope.account_id,
        settlement_currency="USDC",
    )
    control = ControlScope(scope.environment, scope.account_id)
    assert (
        ledger._account_uid_lock_key(scope)
        == ledger._account_uid_lock_key(other_currency)
        == account_lock_key(control)
    )


@pytest.mark.asyncio
async def test_write_schema_pins_uid_before_all_three_tables_and_catalog_read(
    monkeypatch, source
):
    session = _Session()
    observed = []

    async def session_guard(actual):
        assert actual is session
        observed.append("session")

    async def schema_guard(actual):
        assert actual is session
        observed.append("schema")

    monkeypatch.setattr(ledger, "_require_event_journal_session", session_guard)
    monkeypatch.setattr(ledger, "_require_event_journal_schema", schema_guard)
    await ledger._require_event_journal_write_schema(session, source.request.scope)
    assert observed == ["session", "schema"]
    assert len(session.statements) == 4
    assert session.statements[0] == "SET TRANSACTION ISOLATION LEVEL READ COMMITTED"
    assert (
        session.statements[1] == "SET LOCAL search_path TO pg_catalog, public, pg_temp"
    )
    assert "pg_advisory_xact_lock" in session.statements[2]
    assert all(
        name in session.statements[3]
        for name in (
            "public.qualification_account_scopes",
            "public.qualification_reservations",
            "public.qualification_reservation_transitions",
            "ROW EXCLUSIVE MODE NOWAIT",
        )
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ("reserve", "consume"))
async def test_schema_drift_aborts_before_account_row_mutation(
    monkeypatch, operation, source
):
    session = _Session()
    repository = ledger.QualificationLedgerRepository(
        lambda: session, clock=lambda: source.now
    )
    observed = []

    async def drift(actual, scope):
        assert actual is session and scope == source.request.scope
        observed.append("schema_drift")
        raise QualificationLedgerError("event_ledger_schema_retention_invalid")

    async def forbidden_lock(*_args, **_kwargs):
        pytest.fail("account lock/write reached after schema drift")

    monkeypatch.setattr(ledger, "_require_event_journal_write_schema", drift)
    monkeypatch.setattr(repository, "_locked", forbidden_lock)
    with pytest.raises(
        QualificationLedgerError, match="event_ledger_schema_retention_invalid"
    ):
        if operation == "reserve":
            await repository.reserve(source.request)
        else:
            await repository.consume_with_submission_intent(
                source.request.scope,
                source.request.origin.original_event_key,
                expected_revision=1,
            )
    assert observed == ["schema_drift"]
    assert session.statements == []
