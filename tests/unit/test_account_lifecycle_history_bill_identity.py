"""Original raw bill contradictions; component diagnostics grant no authority."""

import json

import pytest

from tests.unit import test_account_lifecycle_history as fixtures

DECLARATIONS = {
    "instId": {"instId": "ETH-USDT-SWAP"},
    "ordId": {"ordId": "999"},
    "tradeId": {"tradeId": "999"},
    "combined": {"instId": "ETH-USDT-SWAP", "ordId": "999", "tradeId": "999"},
}
COMPONENT = {"numerator": "2", "denominator": "5"}


def original_source_bytes(value):
    sources, arguments = value
    return (
        tuple(
            tuple(
                (item.event.event_json, item.event.raw_body, item.event.packet_payload)
                for item in chain
            )
            for chain, _reference in sources
        ),
        tuple(
            (batch.document_json, batch.replay.receipt_json)
            for batch in arguments["index_batches"]
        ),
    )


def replay_with_unknowns(value):
    result = fixtures.history.replay_account_lifecycle_history(value[0], **value[1])
    data = json.loads(result.receipt_json)
    assert result.execution_authority is False and result.admission == "DENY"
    assert data["admission"] == "DENY" and data["execution_authority"] is False
    assert data["owner"] is data["snapshot"] is data["net_loss_window"] is None
    assert data["loss_streak_at_history_start"] is None
    assert data["account_complete"] is data["account_revision_published"] is False
    assert data["source_authenticity_verified"] is False
    assert (
        data["current_tail_complete"] is data["historical_native_hwm_verified"] is False
    )
    assert "funding_accrual_provenance_missing" in data["blocking_reasons"]
    assert "complete_net_loss_window_unproven" in data["blocking_reasons"]
    for outcome in data["observed_lifecycles"]:
        assert outcome["funding_amount"] is outcome["net_pnl"] is None
        assert outcome["positive_net_reset_proven"] is False
    return result, data


@pytest.mark.asyncio
@pytest.mark.parametrize("case", tuple(DECLARATIONS))
async def test_explicit_raw_bill_identity_conflict(monkeypatch, case):
    # A genuine source replay precedes every independently acquired countercase.
    with monkeypatch.context() as baseline_patches:
        baseline_value = await fixtures.fixture(baseline_patches, "A06")
        baseline_bytes = original_source_bytes(baseline_value)
        baseline_result, baseline = replay_with_unknowns(baseline_value)
    assert original_source_bytes(baseline_value) == baseline_bytes
    assert baseline["observed_fill_cashflow_total"] == COMPONENT
    assert len(baseline["bills"]) == 1
    assert baseline["bills"][0]["kind"] == "reconciled_fee_pnl_mirror_not_summed"
    assert baseline["bills"][0]["effective_accrual_at"] is None
    assert (
        baseline["observed_lifecycles"][0]["source_observed_closed_gross"] == COMPONENT
    )
    assert (
        baseline["observed_lifecycles"][0]["source_observed_zeroing_fill_at"]
        is not None
    )
    baseline_receipt = baseline_result.receipt_json
    declared = dict(DECLARATIONS[case])
    original_script_factory = fixtures.script
    declarations = []

    def declared_original_script(*positional, **keyword):
        pages = keyword["pages"]
        changed = set()
        for stream in ("bills_recent", "bills_archive"):
            for page in pages[stream]:
                for raw in page:
                    if raw.get("type") == "2" and raw.get("billId") == "902":
                        raw.update(declared)
                        changed.add(id(raw))
                        declarations.append((stream, dict(raw)))
        # The empty anchor has no bill. The final raw source has one shared bill.
        assert len(changed) in (0, 1)
        # Construct original response bytes only after declaring the raw row.
        return original_script_factory(*positional, **keyword)

    with monkeypatch.context() as declared_patches:
        declared_patches.setattr(fixtures, "script", declared_original_script)
        variant_value = await fixtures.fixture(declared_patches, "A06")
        variant_bytes = original_source_bytes(variant_value)
        _variant_result, variant = replay_with_unknowns(variant_value)
    assert original_source_bytes(variant_value) == variant_bytes
    assert original_source_bytes(baseline_value) == baseline_bytes
    assert baseline_result.receipt_json == baseline_receipt
    assert len(declarations) == 2
    assert {stream for stream, _raw in declarations} == {
        "bills_recent",
        "bills_archive",
    }
    for _stream, raw in declarations:
        assert raw["billId"] == "902" and raw["fee"] == "0.01" and raw["pnl"] == "0.4"
        assert raw["balChg"] == "-0.03"  # Original field; no invented balance equation.
        assert all(raw[key] == value for key, value in declared.items())
    assert baseline["source_set_sha256"] != variant["source_set_sha256"]
    assert baseline["index_head_sha256"] != variant["index_head_sha256"]
    assert len(variant["bills"]) == 1
    assert variant["bills"][0]["kind"] == "unclassified_unknown"
    assert variant["bills"][0]["effective_accrual_at"] is None
    assert "history_fee_mirror_unreconciled" in variant["blocking_reasons"]
    assert variant["observed_fill_cashflow_total"] is None
    # Other component evidence is retained; it does not prove full net outcome.
    assert (
        variant["observed_lifecycles"][0]["source_observed_closed_gross"] == COMPONENT
    )
    assert (
        variant["observed_lifecycles"][0]["source_observed_zeroing_fill_at"] is not None
    )
    locators = variant["bills"][0]["locators"]
    assert {item["stream"] for item in locators} == {"bills_recent", "bills_archive"}
    assert all(item["raw_sha256"] and item["page_receipt_sha256"] for item in locators)
