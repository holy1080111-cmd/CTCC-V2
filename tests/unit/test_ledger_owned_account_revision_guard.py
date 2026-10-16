"""Caller-claims revisions cannot become an authenticated account source."""

import pytest
from pydantic import ValidationError

from app.database.repositories.qualification_ledger import QualificationLedgerRepository
from app.trade_qualification.reservations import QualificationLedgerError
from tests.unit.qualification_ledger_fixtures import ledger_fixture


def test_complete_caller_claims_still_lack_owned_source_witness():
    claims = ledger_fixture().claims
    assert claims.account.balance_stamp.complete
    assert claims.account.positions_stamp.complete
    assert claims.account.history_stamp.complete
    assert claims.account.reservations_stamp.complete
    assert claims.source_authenticity_verified is False
    with pytest.raises(
        QualificationLedgerError, match="ledger_owned_account_revision_missing"
    ):
        QualificationLedgerRepository._require_owned_account_revision(claims)


def test_caller_cannot_inject_an_account_source_flag():
    claims = ledger_fixture().claims
    with pytest.raises(ValidationError, match="ledger_cannot_grant_authority"):
        QualificationLedgerRepository._require_owned_account_revision(
            claims.model_copy(update={"source_authenticity_verified": True})
        )
