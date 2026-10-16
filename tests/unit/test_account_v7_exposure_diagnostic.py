"""Synthetic V7 exposure diagnostic: original pages, strict DENY, no orders."""

import json
from datetime import timedelta

import pytest

from app.domain.source_primitives import canonical, sha
from app.trade_qualification import account_current_source_verifier as current
from app.trade_qualification import account_observation_index as observed
from app.trade_qualification import account_v7_exposure_diagnostic as diagnostic
from tests.unit.test_account_native_proof import companion_fixture


async def source(monkeypatch, *, exposed=True, gateway_case="ordered"):
    raw, _files, chain, scope, _ = await companion_fixture(
        monkeypatch,
        source_case="fresh_exposed" if exposed else "fresh_flat",
        current_v7=True,
        gateway_case=gateway_case,
        verify_baseline=False,
    )
    reference = observed.source_reference(chain)
    packet = current._verify_current_only_chain(
        chain, reference, scope, policy=current.V7_POLICY_SHA256
    )
    return raw, chain, reference, scope, packet.completed_at


@pytest.mark.asyncio
async def test_nonempty_v7_original_chain_yields_readback_only_denied_diagnostic(
    monkeypatch,
):
    raw, chain, reference, scope, at = await source(monkeypatch)
    receipt = diagnostic.diagnose_v7_recorded_exposure(
        chain=chain, reference=reference, scope=scope, validated_at=at
    )
    value = json.loads(receipt.receipt_json)
    assert value["schema_version"] == diagnostic.SCHEMA_VERSION
    assert value["policy_sha256"] == diagnostic.POLICY_SHA256
    assert value["recorded_current_chain_head_sha256"] == reference.head_sha256
    assert value["page_count"] >= 17
    assert value["inventory_row_counts"]["positions"] == 1
    assert value["inventory_row_counts"]["orders_pending"] == 1
    assert value["inventory_row_counts"]["algo_conditional"] == 1
    assert value["exposure_row_count"] == 3
    assert value["observed_nonempty_current_inventory"] is True
    assert value["gateway_causal_coverage_complete"] is True
    assert value["known_source_row_times_ordered"] is True
    assert value["source_clock_coverage_complete"] is False
    assert value["native_clock_proof_verified"] is False
    assert value["source_authenticity_verified"] is False
    assert value["history_complete"] is None
    assert value["local_exposure_complete"] is None
    assert value["active_protection_complete"] is None
    assert value["snapshot"] is receipt.snapshot is None
    assert value["account_complete"] is receipt.account_complete is False
    assert value["flat_start_permission"] is receipt.flat_start_permission is False
    assert value["execution_authority"] is receipt.execution_authority is False
    assert value["admission"] == "DENY"
    assert receipt.receipt_json == canonical(value)
    assert receipt.receipt_sha256 == sha(receipt.receipt_json)
    assert (
        diagnostic.verify_v7_recorded_exposure(
            receipt,
            chain=chain,
            reference=reference,
            scope=scope,
            validated_at=at,
            expected_receipt_sha256=receipt.receipt_sha256,
        ).receipt_json
        == receipt.receipt_json
    )
    assert raw not in receipt.receipt_json
    retained_body = next(item.event.raw_body for item in chain if item.event.raw_body)
    assert retained_body not in receipt.receipt_json


@pytest.mark.asyncio
async def test_empty_stale_or_incomplete_gateway_cannot_mint_nonempty_receipt(
    monkeypatch,
):
    _, chain, reference, scope, at = await source(monkeypatch, exposed=False)
    with pytest.raises(
        diagnostic.V7ExposureDiagnosticError,
        match="account_v7_exposure_source_incomplete_or_empty",
    ):
        diagnostic.diagnose_v7_recorded_exposure(
            chain=chain, reference=reference, scope=scope, validated_at=at
        )
    _, chain, reference, scope, at = await source(monkeypatch)
    with pytest.raises(
        diagnostic.V7ExposureDiagnosticError,
        match="account_v7_exposure_source_incomplete_or_empty",
    ):
        diagnostic.diagnose_v7_recorded_exposure(
            chain=chain,
            reference=reference,
            scope=scope,
            validated_at=at + timedelta(seconds=40),
        )
    for gateway_case in ("missing", "row_after_outtime"):
        _, chain, reference, scope, at = await source(
            monkeypatch, gateway_case=gateway_case
        )
        with pytest.raises(diagnostic.V7ExposureDiagnosticError):
            diagnostic.diagnose_v7_recorded_exposure(
                chain=chain, reference=reference, scope=scope, validated_at=at
            )


@pytest.mark.asyncio
async def test_backdated_cutoff_can_only_mint_historical_denied_diagnostic(monkeypatch):
    _, chain, reference, scope, at = await source(monkeypatch)
    later_cutoff = at + timedelta(days=7)
    with pytest.raises(
        diagnostic.V7ExposureDiagnosticError,
        match="account_v7_exposure_source_incomplete_or_empty",
    ):
        diagnostic.diagnose_v7_recorded_exposure(
            chain=chain,
            reference=reference,
            scope=scope,
            validated_at=later_cutoff,
        )
    # Supplying the old cutoff makes the recorded pages historically fresh.
    # It does not create a live measured clock, account snapshot, or permit.
    historical = diagnostic.diagnose_v7_recorded_exposure(
        chain=chain, reference=reference, scope=scope, validated_at=at
    )
    assert (
        diagnostic.verify_v7_recorded_exposure(
            historical,
            chain=chain,
            reference=reference,
            scope=scope,
            validated_at=at,
            expected_receipt_sha256=historical.receipt_sha256,
        ).receipt_json
        == historical.receipt_json
    )
    value = json.loads(historical.receipt_json)
    assert value["observed_nonempty_current_inventory"] is True
    assert value["native_clock_proof_verified"] is False
    assert value["snapshot"] is None
    assert value["account_complete"] is False
    assert value["flat_start_permission"] is False
    assert value["execution_authority"] is False
    assert value["admission"] == "DENY"


@pytest.mark.asyncio
async def test_changed_source_or_unpinned_receipt_is_denied(monkeypatch):
    _, chain, reference, scope, at = await source(monkeypatch)
    receipt = diagnostic.diagnose_v7_recorded_exposure(
        chain=chain, reference=reference, scope=scope, validated_at=at
    )
    with pytest.raises(diagnostic.V7ExposureDiagnosticError):
        diagnostic.V7ExposureDiagnostic(receipt.receipt_json)
    with pytest.raises(
        diagnostic.V7ExposureDiagnosticError,
        match="account_v7_exposure_readback_mismatch",
    ):
        diagnostic.verify_v7_recorded_exposure(
            receipt,
            chain=chain,
            reference=reference,
            scope=scope,
            validated_at=at,
            expected_receipt_sha256="0" * 64,
        )
    with pytest.raises(diagnostic.V7ExposureDiagnosticError):
        diagnostic.verify_v7_recorded_exposure(
            receipt,
            chain=chain[:-1],
            reference=reference,
            scope=scope,
            validated_at=at,
            expected_receipt_sha256=receipt.receipt_sha256,
        )
    altered = json.loads(receipt.receipt_json)
    altered["execution_authority"] = True
    with pytest.raises(diagnostic.V7ExposureDiagnosticError):
        diagnostic.V7ExposureDiagnostic(canonical(altered), _issuer=diagnostic._ISSUER)
    altered["execution_authority"] = False
    altered["exposure_row_count"] = 0
    with pytest.raises(diagnostic.V7ExposureDiagnosticError):
        diagnostic.V7ExposureDiagnostic(canonical(altered), _issuer=diagnostic._ISSUER)
    altered = json.loads(receipt.receipt_json)
    original_page_digest = altered["page_evidence_sha256"]
    altered["page_evidence_sha256"] = (
        "0" if original_page_digest[0] != "0" else "1"
    ) + original_page_digest[1:]
    forged = diagnostic.V7ExposureDiagnostic(
        canonical(altered), _issuer=diagnostic._ISSUER
    )
    assert forged.receipt_sha256 == sha(forged.receipt_json)
    with pytest.raises(
        diagnostic.V7ExposureDiagnosticError,
        match="account_v7_exposure_readback_mismatch",
    ):
        diagnostic.verify_v7_recorded_exposure(
            forged,
            chain=chain,
            reference=reference,
            scope=scope,
            validated_at=at,
            expected_receipt_sha256=forged.receipt_sha256,
        )
