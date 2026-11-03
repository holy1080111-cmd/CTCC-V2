"""Synthetic closed-trade DATA; no issuer, SQL closure or report authority."""

from decimal import Decimal

import pytest

from app.domain.source_primitives import decode
from app.trade_evidence import native_closed_forensics_v55 as adapter
from app.trade_evidence.native_forensics_v2 import NativeForensicsError
from tests.unit.test_native_close_final_facts_v1 import change, terminal
from tests.unit.test_native_ledger_contracts_v2 import (
    synthetic as synthetic,  # noqa: PLC0414 -- Pytest collects this imported fixture.
)
from tests.unit.test_native_reconciliation_facts_v1 import (
    target as target,  # noqa: PLC0414 -- Pytest collects this imported fixture.
)


def test_complete_fill_and_fee_inventory_keeps_unobserved_funding_and_path_unknown(
    target,
):
    prepared = terminal(target)
    result = adapter.reconstruct_native_closed_forensics_v55(target, *prepared)
    analysis = result.analysis
    document = decode(result.receipt_json, 140000)
    assert analysis.position_status == "flat" and analysis.metrics_complete is False
    assert len(analysis.inputs.fills) == len(analysis.inputs.cashflows) == 2
    assert {item.role for item in analysis.inputs.fills} == {"entry", "exit"}
    assert (
        analysis.entry_contracts.value
        == analysis.exit_contracts.value
        == target.request.risk_inputs.requested_contracts
    )
    assert analysis.remaining_contracts.value == Decimal(0)
    assert (
        analysis.actual_fill.value is not None and analysis.exit_price.value is not None
    )
    assert analysis.fees.value == Decimal(
        0
    ) and analysis.gross_realized_pnl.value == Decimal(0)
    assert (
        analysis.funding.value is None
        and analysis.pnl.value is None
        and analysis.net_cash_pnl_to_date.value is None
    )
    assert analysis.mfe.value is None and analysis.mae.value is None
    assert (
        analysis.exit_slippage_bps.value is None and analysis.primary_cause == "unknown"
    )
    assert [item.status for item in analysis.inputs.coverage] == [
        "complete",
        "complete",
        "unknown",
        "unknown",
    ]
    assert all(
        item.started_at == analysis.inputs.candidate.recorded_at
        and item.ended_at == analysis.inputs.observed_at
        for item in analysis.inputs.coverage
    )
    assert (
        document["unknown_funding_effective_accrual"] is None
        and document["unknown_holding_price_path"] is None
    )
    assert document["actual_fill_pnl_already_contains_price_friction"] is True
    assert len(document["source_links"]) == 2 and all(
        len(item["source_pages"]) == 2 for item in document["source_links"]
    )
    assert (
        result.report_eligible
        is result.execution_authority
        is result.order_retry_authority
        is False
    )


@pytest.mark.parametrize("signed", ("-0.012", "0.002"))
def test_close_signed_fee_or_rebate_is_retained_once_without_price_friction_double_subtraction(
    target, signed
):
    original, prior, document, final = terminal(target)
    for role in (
        "close_fills_recent",
        "close_fills_history",
        "final_fills_recent_all",
        "final_fills_history_all",
    ):
        final = change(
            final,
            role,
            lambda rows, page: [
                row.update(fee=signed) for row in rows if row.get("ordId") == "10002"
            ],
        )
    result = adapter.reconstruct_native_closed_forensics_v55(
        target, original, prior, document, final
    )
    assert result.analysis.fees.value == Decimal(signed)
    assert result.analysis.gross_realized_pnl.value == Decimal(0)
    assert result.analysis.pnl.value is None


@pytest.mark.parametrize(
    "mutation", ("partial", "live_protection", "missing_cursor", "foreign_fill")
)
def test_partial_or_ambiguous_terminal_data_never_produces_a_reportable_forensic_packet(
    target, mutation
):
    original, prior, document, final = terminal(target)
    if mutation == "partial":
        final = change(
            final, "close_order_after", lambda rows, page: rows[0].update(accFillSz="0")
        )
    elif mutation == "live_protection":
        final = change(
            final,
            "original_protection_detail",
            lambda rows, page: rows[0].update(state="live"),
        )
    elif mutation == "missing_cursor":
        final = tuple(
            (role, page)
            for role, page in final
            if not (role == "final_fills_history_all" and not adapter.facts.rows(page))
        )
    else:
        final = change(
            final,
            "final_fills_history_all",
            lambda rows, page: rows and rows[0].update(ordId="999"),
        )
    with pytest.raises(
        NativeForensicsError, match="^native_closed_forensics_reconstruction_invalid$"
    ):
        adapter.reconstruct_native_closed_forensics_v55(
            target, original, prior, document, final
        )
