"""Synthetic original V7 B1 sources; no authenticated account assertion."""

import json
from datetime import timedelta

import pytest

from app.trade_qualification import account_capture_journal as journal
from app.trade_qualification import account_observation_index as observed
from app.trade_qualification import account_v7_metadata_coverage as metadata
from app.trade_qualification.reservations import LedgerScope
from tests.unit.test_account_current_source_v7 import empty_v7_pages, recorded_v7
from tests.unit.test_account_current_source_verifier import SCOPE
from tests.unit.test_qualification_account_capture import NOW, row
from tests.unit.test_qualification_account_collector import SECRETS


def verify(chain, **changes):
    return metadata.verify_v7_declared_metadata(
        chain,
        **{
            "reference": observed.source_reference(chain),
            "scope": SCOPE,
            "validated_at": NOW + timedelta(seconds=4),
            **changes,
        },
    )


@pytest.mark.asyncio
async def test_declared_v7_metadata_present_is_still_not_account_complete(monkeypatch):
    chain, _ = await recorded_v7(monkeypatch)
    source_events = tuple(item.event for item in chain)
    result = verify(chain)
    value = json.loads(result.receipt_json)
    assert value["declared_metadata_coverage_observed"] is True
    assert value["expected_position_sides"] == ["net"]
    assert len(value["declared_instruments"]) == 1
    assert value["declared_instruments"][0]["missing_or_unusable"] == []
    assert value["current_observed_flat"] is True
    assert "account_wide_instrument_scope_unverified" in value["blocking_reasons"]
    assert value["snapshot"] is None
    assert value["account_complete"] is result.account_complete is False
    assert value["execution_authority"] is result.execution_authority is False
    assert value["admission"] == result.admission == "DENY"
    assert tuple(item.event for item in chain) == source_events
    assert SCOPE.account_id.encode() not in result.receipt_json
    assert all(secret.encode() not in result.receipt_json for secret in SECRETS)
    assert (
        metadata.verify_v7_declared_metadata_receipt(
            result,
            chain=chain,
            reference=observed.source_reference(chain),
            scope=SCOPE,
            validated_at=NOW + timedelta(seconds=4),
        ).receipt_json
        == result.receipt_json
    )
    altered = {**value, "declared_metadata_coverage_observed": False}
    forged_but_denied = metadata.V7MetadataCoverage(journal.canonical(altered))
    with pytest.raises(
        metadata.V7MetadataCoverageError, match="v7_metadata_receipt_changed"
    ):
        metadata.verify_v7_declared_metadata_receipt(
            forged_but_denied,
            chain=chain,
            reference=observed.source_reference(chain),
            scope=SCOPE,
            validated_at=NOW + timedelta(seconds=4),
        )


@pytest.mark.asyncio
async def test_empty_instrument_page_does_not_become_metadata_completeness(monkeypatch):
    pages = empty_v7_pages()
    pages["account_instruments"] = [[]]
    chain, _ = await recorded_v7(monkeypatch, pages=pages)
    value = json.loads(verify(chain).receipt_json)
    assert value["current_observed_flat"] is True
    assert value["declared_metadata_coverage_observed"] is False
    assert value["declared_instruments"][0]["instrument_row_sha256"] is None
    assert (
        "account_instrument_row_missing"
        in value["declared_instruments"][0]["missing_or_unusable"]
    )
    assert "declared_instrument_metadata_incomplete" in value["blocking_reasons"]
    assert value["account_complete"] is False and value["admission"] == "DENY"


@pytest.mark.asyncio
async def test_long_short_mode_requires_both_leverage_sides_per_mode(monkeypatch):
    pages = empty_v7_pages()
    for name in ("config_before", "config_after"):
        pages[name] = [[row(name, posMode="long_short_mode")]]
    for mode in ("cross", "isolated"):
        pages[f"leverage_{mode}"] = [[row(f"leverage_{mode}", posSide="long")]]
    chain, _ = await recorded_v7(monkeypatch, pages=pages)
    value = json.loads(verify(chain).receipt_json)
    item = value["declared_instruments"][0]
    assert value["expected_position_sides"] == ["long", "short"]
    assert value["declared_metadata_coverage_observed"] is False
    assert item["leverage_row_sha256_by_mode_and_side"]["cross"]["short"] is None
    assert item["leverage_row_sha256_by_mode_and_side"]["isolated"]["short"] is None
    assert (
        "cross_leverage_side_coverage_missing_or_conflicting"
        in item["missing_or_unusable"]
    )
    assert "isolated_leverage_value_unknown" in item["missing_or_unusable"]
    assert value["account_complete"] is False and value["admission"] == "DENY"


@pytest.mark.asyncio
@pytest.mark.parametrize("variant", ["missing", "zero"])
async def test_missing_or_zero_leverage_never_becomes_a_default(monkeypatch, variant):
    pages = empty_v7_pages()
    pages["leverage_isolated"] = (
        [[]] if variant == "missing" else [[row("leverage_isolated", lever="0")]]
    )
    chain, _ = await recorded_v7(monkeypatch, pages=pages)
    value = json.loads(verify(chain).receipt_json)
    item = value["declared_instruments"][0]
    assert value["declared_metadata_coverage_observed"] is False
    assert "isolated_leverage_value_unknown" in item["missing_or_unusable"]
    assert value["account_complete"] is False and value["admission"] == "DENY"


@pytest.mark.asyncio
async def test_inactive_contract_does_not_count_as_usable_metadata(monkeypatch):
    pages = empty_v7_pages()
    pages["account_instruments"] = [[row("account_instruments", state="suspend")]]
    chain, _ = await recorded_v7(monkeypatch, pages=pages)
    value = json.loads(verify(chain).receipt_json)
    assert value["declared_metadata_coverage_observed"] is False
    assert (
        "account_instrument_terms_unusable"
        in value["declared_instruments"][0]["missing_or_unusable"]
    )


@pytest.mark.asyncio
async def test_wrong_exact_uid_or_reference_is_rejected(monkeypatch):
    chain, _ = await recorded_v7(monkeypatch)
    with pytest.raises(metadata.V7MetadataCoverageError):
        verify(chain, scope=LedgerScope(account_id="999", settlement_currency="USDT"))
    ref = observed.source_reference(chain)
    wrong = observed.CaptureReference(
        ref.capture_id,
        "0" * 64,
        ref.plan_sha256,
        ref.packet_sha256,
        ref.session_binding_sha256,
    )
    with pytest.raises(
        metadata.V7MetadataCoverageError,
        match="v7_metadata_source_reference_mismatch",
    ):
        verify(chain, reference=wrong)


def test_caller_constructed_receipt_cannot_claim_authority():
    forged = journal.canonical(
        {
            "schema_version": metadata.SCHEMA_VERSION,
            "policy_sha256": metadata.POLICY_SHA256,
            "snapshot": {},
            "account_complete": True,
            "source_authenticity_verified": True,
            "execution_authority": True,
            "admission": "PASS",
        }
    )
    with pytest.raises(metadata.V7MetadataCoverageError):
        metadata.V7MetadataCoverage(forged)
