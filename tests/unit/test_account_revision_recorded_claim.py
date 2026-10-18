"""Recorded source values never promote caller ledger claims into authority."""

import asyncio
import json
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from app.trade_qualification import account_observation_index as observed
from app.trade_qualification import account_revision_recorded_claim as claim
from app.trade_qualification import reservations
from tests.unit.qualification_ledger_fixtures import ledger_fixture
from tests.unit.test_account_current_source_v7 import empty_v7_pages, recorded_v7
from tests.unit.test_account_current_source_verifier import SCOPE
from tests.unit.test_qualification_account_capture import NOW


def _derive(chain, claims, *, reference=None, witness_sha256="a" * 64):
    return claim.derive_recorded_current_claim(
        chain,
        reference=observed.source_reference(chain) if reference is None else reference,
        scope=SCOPE,
        witness_sha256=witness_sha256,
        account_revision=1,
        claims_sha256=reservations.digest(claims),
        claims=claims,
        validated_at=NOW + timedelta(seconds=4),
    )


@pytest.mark.asyncio
async def test_source_values_and_exact_caller_comparison_are_deny_only(monkeypatch):
    chain, _ = await recorded_v7(monkeypatch)
    caller = (
        await asyncio.to_thread(ledger_fixture, account_id=SCOPE.account_id)
    ).claims
    matched = caller.model_copy(
        update={
            "account": caller.account.model_copy(
                update={
                    "equity": Decimal(1000),
                    "available_margin": Decimal(800),
                }
            )
        }
    )
    result = _derive(chain, matched)
    value = json.loads(result.receipt_json)
    assert result == _derive(chain, matched)
    assert value["source_balance"]["equity"] == {
        "numerator": "1000",
        "denominator": "1",
    }
    assert value["source_balance"]["available_equity"] == {
        "numerator": "800",
        "denominator": "1",
    }
    assert value["caller_claims_comparison"] == {
        "settlement_equity_matches": True,
        "exchange_available_margin_matches": True,
    }
    assert all(count == 0 for count in value["source_inventory_row_counts"].values())
    assert value["account_revision_issued"] is result.account_revision_issued is False
    assert value["flat_start_permission"] is False
    assert value["portfolio_snapshot"] is None
    assert value["account_complete"] is result.account_complete is False
    assert value["source_authenticity_verified"] is False
    assert value["execution_authority"] is result.execution_authority is False
    assert value["admission"] == result.admission == "DENY"

    divergent = matched.model_copy(
        update={"account": matched.account.model_copy(update={"equity": Decimal(999)})}
    )
    changed = json.loads(_derive(chain, divergent).receipt_json)
    assert changed["caller_claims_comparison"] == {
        "settlement_equity_matches": False,
        "exchange_available_margin_matches": True,
    }
    assert "caller_balance_diverges_from_recorded_source" in changed["blocking_reasons"]
    assert changed["account_revision_issued"] is False


@pytest.mark.asyncio
async def test_missing_settlement_balance_stays_unknown_not_zero(monkeypatch):
    pages = empty_v7_pages()
    pages["balance"][0][0]["details"] = []
    chain, _ = await recorded_v7(monkeypatch, pages=pages)
    caller = (
        await asyncio.to_thread(ledger_fixture, account_id=SCOPE.account_id)
    ).claims
    value = json.loads(_derive(chain, caller).receipt_json)
    assert value["source_balance"]["equity"] is None
    assert value["source_balance"]["available_equity"] is None
    assert value["caller_claims_comparison"] == {
        "settlement_equity_matches": None,
        "exchange_available_margin_matches": None,
    }
    assert "recorded_settlement_balance_unknown" in value["blocking_reasons"]
    assert value["admission"] == "DENY"


@pytest.mark.asyncio
async def test_wrong_chain_pin_or_witness_digest_yields_no_claim(monkeypatch):
    chain, _ = await recorded_v7(monkeypatch)
    caller = (
        await asyncio.to_thread(ledger_fixture, account_id=SCOPE.account_id)
    ).claims
    wrong = replace(observed.source_reference(chain), head_sha256="0" * 64)
    with pytest.raises(claim.RecordedAccountClaimError):
        _derive(chain, caller, reference=wrong)
    with pytest.raises(claim.RecordedAccountClaimError):
        _derive(chain, caller, witness_sha256="bad")
