"""Synthetic original V7 inventory metadata replay; never account authority."""

import json
from datetime import timedelta

import pytest

from app.trade_qualification import account_observation_index as observed
from app.trade_qualification import account_v7_inventory_metadata as inventory
from tests.unit.test_account_current_source_v7 import empty_v7_pages, recorded_v7
from tests.unit.test_account_current_source_verifier import SCOPE
from tests.unit.test_qualification_account_capture import NOW, row


def verify(chain, **changes):
    return inventory.observe_v7_inventory_metadata(
        chain,
        **{
            "reference": observed.source_reference(chain),
            "scope": SCOPE,
            "validated_at": NOW + timedelta(seconds=4),
            **changes,
        },
    )


@pytest.mark.asyncio
async def test_declared_exposure_metadata_is_observed_but_never_authority(monkeypatch):
    pages = empty_v7_pages()
    pages["orders_pending"] = [[row("orders_pending")], []]
    chain, _ = await recorded_v7(monkeypatch, pages=pages)
    result = verify(chain)
    value = json.loads(result.receipt_json)
    assert value["inventory_row_count"] == 1
    assert value["observed_inventory_metadata_coverage"] is True
    assert len(value["instruments"]) == 1
    assert value["instruments"][0]["declared_leverage_query"] is True
    assert value["instruments"][0]["missing_or_unusable"] == []
    assert value["snapshot"] is None
    assert value["source_authenticity_verified"] is False
    assert value["account_complete"] is result.account_complete is False
    assert value["execution_authority"] is result.execution_authority is False
    assert value["admission"] == result.admission == "DENY"
    assert SCOPE.account_id.encode() not in result.receipt_json
    assert (
        inventory.verify_v7_inventory_metadata_receipt(
            result,
            expected_receipt_sha256=result.receipt_sha256,
            chain=chain,
            reference=observed.source_reference(chain),
            scope=SCOPE,
            validated_at=NOW + timedelta(seconds=4),
        ).receipt_json
        == result.receipt_json
    )


@pytest.mark.asyncio
async def test_undeclared_pending_instrument_cannot_borrow_candidate_leverage(
    monkeypatch,
):
    pages = empty_v7_pages()
    pages["orders_pending"] = [
        [row("orders_pending", instId="ETH-USDT-SWAP")],
        [],
    ]
    pages["account_instruments"] = [
        [row("account_instruments"), row("account_instruments", instId="ETH-USDT-SWAP")]
    ]
    chain, _ = await recorded_v7(monkeypatch, pages=pages)
    value = json.loads(verify(chain).receipt_json)
    assert value["observed_inventory_metadata_coverage"] is False
    item = value["instruments"][0]
    assert item["instrument_id"] == "ETH-USDT-SWAP"
    assert item["instrument_row_sha256"] is not None
    assert item["declared_leverage_query"] is False
    assert "leverage_instrument_not_declared_or_queried" in item["missing_or_unusable"]
    assert "inventory_instrument_metadata_incomplete" in value["blocking_reasons"]
    assert value["account_complete"] is False and value["admission"] == "DENY"


@pytest.mark.asyncio
async def test_mixed_declared_and_undeclared_exposure_blocks_aggregate_coverage(
    monkeypatch,
):
    pages = empty_v7_pages()
    pages["orders_pending"] = [[row("orders_pending")], []]
    pages["algo_chase"] = [[row("algo_chase", instId="ETH-USDT-SWAP")], []]
    pages["account_instruments"] = [
        [row("account_instruments"), row("account_instruments", instId="ETH-USDT-SWAP")]
    ]
    chain, _ = await recorded_v7(monkeypatch, pages=pages)
    value = json.loads(verify(chain).receipt_json)
    assert value["inventory_row_count"] == 2
    assert value["observed_inventory_metadata_coverage"] is False
    by_id = {item["instrument_id"]: item for item in value["instruments"]}
    assert len(by_id) == 2
    assert by_id["BTC-USDT-SWAP"]["missing_or_unusable"] == []
    assert by_id["ETH-USDT-SWAP"]["missing_or_unusable"] == [
        "leverage_instrument_not_declared_or_queried"
    ]
    assert "inventory_instrument_metadata_incomplete" in value["blocking_reasons"]


@pytest.mark.asyncio
async def test_missing_undeclared_spec_and_empty_inventory_cannot_claim_coverage(
    monkeypatch,
):
    pages = empty_v7_pages()
    pages["algo_chase"] = [[row("algo_chase", instId="ETH-USDT-SWAP")], []]
    chain, _ = await recorded_v7(monkeypatch, pages=pages)
    value = json.loads(verify(chain).receipt_json)
    assert value["observed_inventory_metadata_coverage"] is False
    assert value["instruments"][0]["missing_or_unusable"] == [
        "leverage_instrument_not_declared_or_queried",
        "account_instrument_row_missing",
    ]
    empty, _ = await recorded_v7(monkeypatch)
    zero = json.loads(verify(empty).receipt_json)
    assert zero["inventory_row_count"] == 0
    assert zero["instruments"] == []
    assert zero["observed_inventory_metadata_coverage"] is False
    assert "no_observed_inventory_instrument" in zero["blocking_reasons"]


@pytest.mark.asyncio
async def test_wrong_source_policy_or_receipt_cannot_relabel_coverage(monkeypatch):
    pages = empty_v7_pages()
    pages["orders_pending"] = [[row("orders_pending")], []]
    chain, _ = await recorded_v7(monkeypatch, pages=pages)
    result = verify(chain)
    with pytest.raises(inventory.V7InventoryMetadataError):
        inventory.V7InventoryMetadata(result.receipt_json)
    with pytest.raises(
        inventory.V7InventoryMetadataError, match="v7_inventory_metadata_policy_invalid"
    ):
        verify(chain, expected_policy_sha256="0" * 64)
    with pytest.raises(
        inventory.V7InventoryMetadataError,
        match="v7_inventory_metadata_readback_changed",
    ):
        inventory.verify_v7_inventory_metadata_receipt(
            result,
            expected_receipt_sha256="0" * 64,
            chain=chain,
            reference=observed.source_reference(chain),
            scope=SCOPE,
            validated_at=NOW + timedelta(seconds=4),
        )
    altered = json.loads(result.receipt_json)
    altered["execution_authority"] = True
    with pytest.raises(inventory.V7InventoryMetadataError):
        inventory.V7InventoryMetadata(
            inventory.journal.canonical(altered), _issuer=inventory._ISSUER
        )
    with pytest.raises(inventory.V7InventoryMetadataError):
        verify(chain[:-1], reference=observed.source_reference(chain))


@pytest.mark.asyncio
async def test_receipt_rejects_bad_inner_count_or_missing_required_blocker(monkeypatch):
    pages = empty_v7_pages()
    pages["orders_pending"] = [[row("orders_pending")], []]
    chain, _ = await recorded_v7(monkeypatch, pages=pages)
    value = json.loads(verify(chain).receipt_json)
    altered = json.loads(inventory.journal.canonical(value))
    altered["instruments"][0]["exposure_row_count"] = 0
    with pytest.raises(inventory.V7InventoryMetadataError):
        inventory.V7InventoryMetadata(
            inventory.journal.canonical(altered), _issuer=inventory._ISSUER
        )
    altered = json.loads(inventory.journal.canonical(value))
    altered["blocking_reasons"].remove("source_authenticity_unverified")
    with pytest.raises(inventory.V7InventoryMetadataError):
        inventory.V7InventoryMetadata(
            inventory.journal.canonical(altered), _issuer=inventory._ISSUER
        )


@pytest.mark.asyncio
async def test_more_than_128_observed_instruments_aborts_instead_of_truncating(
    monkeypatch,
):
    pages = empty_v7_pages()
    orders = [
        row(
            "orders_pending",
            identifier=str(5000 - index),
            instId=f"T{index:03d}-USDT-SWAP",
        )
        for index in range(129)
    ]
    pages["orders_pending"] = [orders[:100], orders[100:], []]
    chain, _ = await recorded_v7(monkeypatch, pages=pages)
    with pytest.raises(
        inventory.V7InventoryMetadataError,
        match="v7_inventory_metadata_instrument_limit",
    ):
        verify(chain)


@pytest.mark.asyncio
async def test_saved_receipt_rejected_after_original_chain_changes(monkeypatch):
    pages = empty_v7_pages()
    pages["orders_pending"] = [[row("orders_pending")], []]
    original_chain, _ = await recorded_v7(monkeypatch, pages=pages)
    saved = verify(original_chain)
    pages["orders_pending"] = [
        [row("orders_pending", instId="ETH-USDT-SWAP")],
        [],
    ]
    changed_chain, _ = await recorded_v7(monkeypatch, pages=pages)
    with pytest.raises(
        inventory.V7InventoryMetadataError,
        match="v7_inventory_metadata_readback_changed",
    ):
        inventory.verify_v7_inventory_metadata_receipt(
            saved,
            expected_receipt_sha256=saved.receipt_sha256,
            chain=changed_chain,
            reference=observed.source_reference(changed_chain),
            scope=SCOPE,
            validated_at=NOW + timedelta(seconds=4),
        )


@pytest.mark.asyncio
async def test_stale_current_source_cannot_observe_inventory_coverage(monkeypatch):
    pages = empty_v7_pages()
    pages["orders_pending"] = [[row("orders_pending")], []]
    chain, _ = await recorded_v7(monkeypatch, pages=pages)
    value = json.loads(
        verify(chain, validated_at=NOW + timedelta(seconds=40)).receipt_json
    )
    assert value["observed_inventory_metadata_coverage"] is False
    assert "measured_current_receipt_stale" in value["blocking_reasons"]
    assert value["snapshot"] is None
    assert value["account_complete"] is False
    assert value["execution_authority"] is False
    assert value["admission"] == "DENY"
