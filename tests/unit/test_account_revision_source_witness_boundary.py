"""The source-witness entry cannot be driven by caller claims or authority flags."""

import inspect

import pytest

from app.database.repositories.account_revision_source_witness import (
    AccountRevisionSourceError,
    AccountRevisionSourceWitnessRepository,
    _claims_match_scope,
)
from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_current_history_join as joined
from app.trade_qualification import account_observation_index as observed
from app.trade_qualification.reservations import LedgerScope
from tests.unit.qualification_ledger_fixtures import ledger_fixture
from tests.unit.test_account_current_source_v7 import recorded_v7
from tests.unit.test_account_current_source_verifier import recorded


class NoDatabase:
    def __init__(self):
        self.calls = 0

    def __call__(self):
        self.calls += 1
        raise AssertionError("invalid witness request opened a database session")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("revision", "typed", "current"),
    [
        (True, 1, 1),
        (0, 1, 1),
        (1, False, 1),
        (1, 0, 1),
        (1, 1, -1),
        (1, 1, 2**63),
    ],
)
async def test_invalid_locator_denies_before_source_or_sql(revision, typed, current):
    sessions = NoDatabase()
    repository = AccountRevisionSourceWitnessRepository(sessions, clock=lambda: None)
    with pytest.raises(AccountRevisionSourceError):
        await repository.record_denied_source_witness(
            LedgerScope(account_id="123456789", settlement_currency="USDT"),
            account_revision=revision,
            typed_sequence=typed,
            current_sequence=current,
        )
    assert sessions.calls == 0


@pytest.mark.asyncio
async def test_invalid_readback_digest_denies_before_sql():
    sessions = NoDatabase()
    repository = AccountRevisionSourceWitnessRepository(sessions, clock=lambda: None)
    with pytest.raises(AccountRevisionSourceError):
        await repository.read_one(
            LedgerScope(account_id="123456789", settlement_currency="USDT"),
            "not-a-source-witness",
        )
    assert sessions.calls == 0


def test_public_entry_has_no_claims_payload_or_authority_parameters():
    parameters = inspect.signature(
        AccountRevisionSourceWitnessRepository.record_denied_source_witness
    ).parameters
    assert set(parameters) == {
        "self",
        "scope",
        "account_revision",
        "typed_sequence",
        "current_sequence",
    }


@pytest.mark.asyncio
async def test_original_packet_replay_accepts_exact_v5_history_and_v7_current(
    monkeypatch,
):
    history = await recorded(monkeypatch)
    current, _ = await recorded_v7(monkeypatch)
    history_packet = joined._packet(history, observed.source_reference(history))
    current_packet = joined._packet(current, observed.source_reference(current))
    assert type(history_packet.plan) is capture.AllProductDemoAccountCapturePlan
    assert type(current_packet.plan) is capture.CurrentDemoAccountCapturePlanV7


def test_claimed_scope_must_match_all_nested_uid_and_currency_fields():
    claims = ledger_fixture().claims
    scope = claims.scope
    assert _claims_match_scope(claims, scope)
    wrong_scope = scope.model_copy(update={"account_id": "999999999"})
    wrong_account = claims.account.model_copy(update={"account_id": "999999999"})
    wrong_currency = claims.account.model_copy(update={"settlement_currency": "USD"})
    wrong_authority = claims.authority.model_copy(update={"environment": "live"})
    wrong_stamp = claims.account.balance_stamp.model_copy(
        update={"account_id": "999999999"}
    )
    wrong_stamped_account = claims.account.model_copy(
        update={"balance_stamp": wrong_stamp}
    )
    for changed in (
        claims.model_copy(update={"scope": wrong_scope}),
        claims.model_copy(update={"account": wrong_account}),
        claims.model_copy(update={"account": wrong_currency}),
        claims.model_copy(update={"authority": wrong_authority}),
        claims.model_copy(update={"account": wrong_stamped_account}),
    ):
        assert not _claims_match_scope(changed, scope)
