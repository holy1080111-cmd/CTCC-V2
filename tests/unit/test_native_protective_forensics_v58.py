"""Synthetic original-OCO reports; no exchange, SQL release or report grant."""

from decimal import Decimal

import pytest

from app.domain.source_primitives import decode
from app.trade_evidence import native_closed_forensics_v55 as own_close
from app.trade_evidence import native_protective_forensics_v58 as adapter
from app.trade_evidence.native_forensics_v2 import NativeForensicsError
from tests.unit.test_native_ledger_contracts_v2 import (
    synthetic as synthetic,  # noqa: PLC0414 -- pytest fixture reexport.
)
from tests.unit.test_native_protective_exit_facts_v1 import mutate, protective_terminal
from tests.unit.test_native_reconciliation_facts_v1 import (
    target as target,  # noqa: PLC0414 -- pytest fixture reexport.
)


def test_original_oco_full_exit_replays_fifo_and_preserves_unknowns(target):
    original, terminal = protective_terminal(target)
    result = adapter.reconstruct_native_protective_forensics_v58(
        target, original, terminal
    )
    assert type(result) is adapter.NativeProtectiveForensicsDataV58
    doc = decode(result.receipt_json, 140000)
    assert doc["version"] == "ctcc.native_original_oco_forensics.v1"
    assert doc["reservation_id"] == target.reservation_id
    assert doc["original_event_key"] == target.request.origin.original_event_key
    assert doc["g12_evidence_sha256"] == target.request.origin.evidence_sha256
    assert (
        decode(doc["terminal_assessment_json"].encode())["classification"]
        == "original_oco_full_exit_flat"
    )
    a = result.analysis
    assert a.position_status == "flat" and a.exit_reason == "take_profit"
    assert (
        a.entry_contracts.value
        == a.exit_contracts.value
        == target.request.risk_inputs.requested_contracts
    )
    assert a.remaining_contracts.value == Decimal(0)
    assert a.fees.value == a.gross_realized_pnl.value == Decimal(0)
    assert a.metrics_complete is False
    assert a.funding.value is a.pnl.value is a.mfe.value is a.mae.value is None
    assert a.exit_slippage_bps.value is None
    assert a.inputs.path == ()
    assert [item.status for item in a.inputs.coverage] == [
        "complete",
        "complete",
        "unknown",
        "unknown",
    ]
    assert doc["actual_fill_pnl_already_contains_price_friction"] is True
    assert len(doc["source_links"]) == 2
    assert all(len(item["source_pages"]) == 2 for item in doc["source_links"])
    assert (
        result.report_eligible
        is result.execution_authority
        is result.order_retry_authority
        is False
    )


@pytest.mark.parametrize("signed", ("-0.012", "0.002"))
def test_original_oco_fee_or_rebate_keeps_exact_signed_cash_once(target, signed):
    original, terminal = protective_terminal(target)
    for role in (
        "exit_fills_recent",
        "exit_fills_history",
        "exit_all_fills_recent",
        "exit_all_fills_history",
    ):
        # The all-fill population also includes the entry; change only the child.
        from dataclasses import replace

        from app.domain.source_primitives import canonical

        changed = []
        for name, page in terminal:
            if name == role:
                raw = decode(page.raw_body)
                for row in raw["data"]:
                    if row["ordId"] == "10002":
                        row["fee"] = signed
                page = replace(page, raw_body=canonical(raw))
            changed.append((name, page))
        terminal = tuple(changed)
    result = adapter.reconstruct_native_protective_forensics_v58(
        target, original, terminal
    )
    assert result.analysis.fees.value == Decimal(signed)
    assert result.analysis.gross_realized_pnl.value == Decimal(0)
    assert result.analysis.pnl.value is None


@pytest.mark.parametrize(
    "role,key,value",
    (
        ("protection_before", "algoId", "9999"),
        ("protection_before", "state", "canceled"),
        ("protection_before", "ordIdList", ["10002", "10003"]),
        ("exit_order_before", "state", "partially_filled"),
        ("exit_fills_recent", "feeCcy", "BTC"),
        ("exit_all_fills_history", "ordId", "10099"),
    ),
)
def test_changed_partial_or_foreign_original_exit_never_becomes_forensic_truth(
    target, role, key, value
):
    original, terminal = protective_terminal(target)
    with pytest.raises(NativeForensicsError):
        adapter.reconstruct_native_protective_forensics_v58(
            target, original, mutate(terminal, role, key, value)
        )


def test_missing_cursor_chain_is_unknown_and_not_an_empty_fee_or_funding_set(target):
    original, terminal = protective_terminal(target)
    for omitted in ("exit_bills_archive", "exit_fills_history", "positions"):
        with pytest.raises(NativeForensicsError):
            adapter.reconstruct_native_protective_forensics_v58(
                target, original, tuple(row for row in terminal if row[0] != omitted)
            )


def test_oco_raw_pages_cannot_impersonate_a_local_close_intent(target):
    original, terminal = protective_terminal(target)
    with pytest.raises(NativeForensicsError):
        own_close.reconstruct_native_closed_forensics_v55(
            target, original, (), b"{}", terminal
        )
    with pytest.raises(NativeForensicsError):
        own_close._reconstruct_closed_forensics_profile(
            target,
            original,
            (),
            b"{}",
            terminal,
            profile=own_close.ClosedForensicsProfileV1.ORIGINAL_OCO,
        )
