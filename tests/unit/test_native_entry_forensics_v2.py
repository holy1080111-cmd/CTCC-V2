"""Synthetic source DATA only; no owned registry, DB, API or close authority."""

from decimal import Decimal

import pytest

from app.domain.source_primitives import decode, sha
from app.trade_evidence import native_forensics_v2 as adapter
from tests.unit.test_native_reconciliation_facts_v1 import changed, data
from tests.unit.test_native_reconciliation_facts_v1 import (
    synthetic as synthetic,  # noqa: PLC0414 -- Pytest collects this imported fixture.
)
from tests.unit.test_native_reconciliation_facts_v1 import (
    target as target,  # noqa: PLC0414 -- Pytest collects this imported fixture.
)


def test_real_native_raw_replay_maps_entry_rows_once_and_retains_unknowns(target):
    observations = data(target, filled=True)
    result = adapter.reconstruct_native_entry_forensics_v2(target, observations)
    analysis = result.analysis
    value = decode(result.receipt_json, 4 * 1024 * 1024)
    assert sha(result.receipt_json) == result.receipt_sha256
    assert analysis.position_status == "unknown"
    assert len(analysis.inputs.fills) == 1
    assert len(analysis.inputs.cashflows) == 1
    fill, fee = analysis.inputs.fills[0], analysis.inputs.cashflows[0]
    assert fill.role == "entry" and fee.fill_id == fill.evidence_id
    outer = decode(target.intent_json.encode(), 16 * 1024 * 1024)
    expected = decode(outer["intent_json"].encode(), 16 * 1024 * 1024)["exact_order"]
    assert fill.price == Decimal(expected["px"])
    assert fill.contracts == target.request.risk_inputs.requested_contracts
    assert fee.amount == Decimal(0)  # This explicit actual fixture row is zero.
    assert fill.source_sha256 in {sha(page.raw_body) for _, page in observations}
    assert len(value["source_links"][0]["source_pages"]) == 2
    assert {item["role"] for item in value["source_links"][0]["source_pages"]} == {
        "fills_recent",
        "fills_history",
    }
    assert all(
        item.status == "partial" and item.reason for item in analysis.inputs.coverage
    )
    assert analysis.actual_fill.value is None
    assert analysis.fees.value is None and analysis.funding.value is None
    assert analysis.gross_realized_pnl.value is None and analysis.pnl.value is None
    assert analysis.mfe.value is None and analysis.mae.value is None
    assert value["unobserved_close_rows"] is None
    assert value["unobserved_funding_rows"] is None
    assert value["unobserved_holding_path"] is None
    assert value["unobserved_prefill_quote_references"] is None
    assert (
        value["native_original_candidate_sha256"]
        == target.request.origin.original_candidate_sha256
    )
    assert value["forensic_projection_candidate_sha256"] == analysis.candidate_sha256
    assert value["reservation_id"] == target.reservation_id
    assert value["intent_sha256"] == target.intent_sha256
    assert value["original_event_key"] == target.request.origin.original_event_key
    assert (
        analysis.inputs.candidate.recorded_at
        == target.request.origin.candidate.evaluated_at
    )
    assert value["original_recorded_at_basis"] == "original_qualification_evaluated_at"
    assert (
        value["original_R_basis"]
        == "planned_contracts_times_ctVal_times_abs_entry_minus_stop_price_cash"
    )
    assert value[
        "native_reservation_cost_inclusive_coverage"
    ] == target.request.coverage.model_dump(mode="json", round_trip=True)
    assert value["actual_fill_pnl_already_contains_price_friction"] is True
    metrics = dict(result.execution.metrics)
    assert metrics["entry_spread_cost"].value is None
    assert metrics["entry_residual_slippage"].value is None
    assert metrics["net_pnl_from_reference_decomposition"].value is None
    assert (
        result.closed_trade_verified
        is result.report_eligible
        is result.execution_authority
        is False
    )
    assert result.source_authenticity_verified is False


@pytest.mark.parametrize("signed", ("-0.00000001", "0.00000002"))
def test_original_signed_entry_fee_and_rebate_are_not_capped_or_double_counted(
    target, signed
):
    observations = data(target, filled=True)
    for role in ("fills_recent", "fills_history"):
        observations = changed(
            observations, role, lambda rows: [row.update(fee=signed) for row in rows]
        )
    result = adapter.reconstruct_native_entry_forensics_v2(target, observations)
    assert len(result.analysis.inputs.cashflows) == 1
    assert result.analysis.inputs.cashflows[0].amount == Decimal(signed)
    assert result.analysis.inputs.cashflows[0].kind == "fee"
    assert (
        result.analysis.pnl.value is None
    )  # No terminal/funding completeness invented.


@pytest.mark.parametrize(
    "mutation",
    (
        "no_fill",
        "missing_fee",
        "foreign_fee_currency",
        "overlap_conflict",
        "missing_terminal",
        "future_generation",
        "unknown_raw",
    ),
)
def test_partial_or_conflicting_native_rows_never_become_forensic_completeness(
    target, mutation
):
    observations = data(target, filled=True)
    if mutation == "no_fill":
        observations = data(target, filled=False)
    elif mutation in ("missing_fee", "foreign_fee_currency"):

        def edit(rows):
            for row in rows:
                if mutation == "missing_fee":
                    row.pop("fee")
                else:
                    row["feeCcy"] = "BTC"

        for role in ("fills_recent", "fills_history"):
            observations = changed(observations, role, edit)
    elif mutation == "overlap_conflict":
        observations = changed(
            observations,
            "fills_history",
            lambda rows: [row.update(fee="0.00000001") for row in rows],
        )
    elif mutation == "missing_terminal":
        observations = tuple(
            (role, page)
            for role, page in observations
            if not (role == "fills_history" and not adapter.facts.rows(page))
        )
    elif mutation == "future_generation":
        for role in ("fills_recent", "fills_history"):
            observations = changed(
                observations,
                role,
                lambda rows: [row.update(ts="9999999999999") for row in rows],
            )
    else:
        observations = ()
    with pytest.raises(
        adapter.NativeForensicsError,
        match="^native_entry_forensics_reconstruction_invalid$",
    ):
        adapter.reconstruct_native_entry_forensics_v2(target, observations)


def test_new_data_view_construction_never_grants_native_or_close_permission(target):
    observations = data(target, filled=True)
    result = adapter.reconstruct_native_entry_forensics_v2(target, observations)
    assert result.closed_trade_verified is False
    assert result.report_eligible is False
    assert result.execution_authority is False
    with pytest.raises(TypeError):
        adapter.NativeEntryForensicsReplayV2(
            result.analysis,
            result.execution,
            result.receipt_json,
            result.receipt_sha256,
            execution_authority=True,
        )
    # No synthetic component result is passed into a native registry/SQL API.


@pytest.mark.parametrize(
    "kind", ("none", "truth_dict", "inert_source_view", "unregistered_result")
)
def test_projected_or_forged_data_cannot_acquire_owned_entry_forensics(kind):
    from app.trade_qualification import native_postsubmit_reconciliation_v1 as native

    before = (len(native._LIVE), len(native._UNKNOWN))
    values = {
        "none": None,
        "truth_dict": {
            "closed_trade_verified": True,
            "source_authenticity_verified": True,
            "execution_authority": True,
        },
        "inert_source_view": adapter.NativeEntryForensicsSourceV2(None, (), None),
        "unregistered_result": object.__new__(native.NativeReconciliationResultV1),
    }
    with pytest.raises(
        adapter.NativeForensicsError,
        match="^native_entry_forensics_owned_source_unavailable$",
    ):
        adapter.analyze_owned_native_entry_v2(values[kind])
    assert (len(native._LIVE), len(native._UNKNOWN)) == before
