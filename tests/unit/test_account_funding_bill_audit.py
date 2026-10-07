"""Synthetic original B1 bytes; never authenticated OKX evidence or authority."""

import json
from dataclasses import replace

import pytest

from app.trade_qualification import account_capture_journal as journal
from app.trade_qualification import account_funding_bill_audit as funding
from app.trade_qualification import account_observation_index as observed
from tests.unit.test_account_current_source_verifier import (
    SCOPE,
    flat_pages,
    recorded,
)
from tests.unit.test_qualification_account_capture import INSTRUMENT, row


@pytest.mark.asyncio
async def test_original_account_bill_payment_candidate_never_becomes_accrual(
    monkeypatch,
):
    pages = flat_pages()
    bill = row(
        "bills_recent",
        "900",
        instId=INSTRUMENT,
        instType="SWAP",
        pnl="-0.03",
        balChg="-0.03",
    )
    pages["bills_recent"] = [[bill], []]
    pages["bills_archive"] = [[bill], []]
    chain = await recorded(monkeypatch, pages=pages)
    reference = observed.source_reference(chain)
    audit = funding.audit_funding_bills(chain, reference=reference, scope=SCOPE)
    value = json.loads(audit.receipt_json)

    assert (
        value["schema_version"] == "ctcc.demo_account_funding_bill_candidate_audit.v1"
    )
    assert value["policy_sha256"] == funding.POLICY_SHA256
    assert [item["stream"] for item in value["account_bill_queries"]] == [
        "bills_recent",
        "bills_archive",
    ]
    assert all(
        len(item["page_receipts"]) == 2 for item in value["account_bill_queries"]
    )
    assert len(value["account_bill_rows"]) == 1  # exact recent/archive overlap
    item = value["account_bill_rows"][0]
    assert item["classification"] == "account_funding_payment_candidate"
    assert item["payment_field"] == "pnl"
    assert item["payment_sign"] == "negative"
    assert len(item["locators"]) == 2
    assert item["source_balance_update_at"] is not None
    assert item["effective_accrual_at"] is None
    assert value["funding_accrual_at"] is None
    assert value["net_funding_cashflow"] is None
    assert "funding_accrual_provenance_missing" in value["blocking_reasons"]
    assert audit.account_complete is False
    assert audit.execution_authority is False
    assert audit.admission == "DENY"
    assert (
        funding.verify_funding_bill_audit(
            chain,
            reference=reference,
            scope=SCOPE,
            expected_receipt_json=audit.receipt_json,
        ).receipt_json
        == audit.receipt_json
    )

    for changed_field in ("classification", "locator"):
        forged = json.loads(audit.receipt_json)
        if changed_field == "classification":
            forged["account_bill_rows"][0]["classification"] = "other_account_movement"
        else:
            forged["account_bill_rows"][0]["locators"][0]["page_receipt_sha256"] = (
                "f" * 64
            )
        assert forged["admission"] == "DENY"
        assert forged["funding_accrual_at"] is None
        with pytest.raises(
            funding.FundingBillAuditError, match="funding_bill_receipt_mismatch"
        ):
            funding.verify_funding_bill_audit(
                chain,
                reference=reference,
                scope=SCOPE,
                expected_receipt_json=journal.canonical(forged),
            )


@pytest.mark.asyncio
async def test_funding_type_subtype_or_payment_conflict_stays_unknown(monkeypatch):
    pages = flat_pages()
    pages["bills_recent"] = [
        [
            row("bills_recent", "902", type="2", subType="173", instId=INSTRUMENT),
            row("bills_recent", "901", type="8", subType="999", instId=INSTRUMENT),
            row("bills_recent", "900", type="8", subType="174", instId=""),
        ],
        [],
    ]
    chain = await recorded(monkeypatch, pages=pages)
    audit = funding.audit_funding_bills(
        chain, reference=observed.source_reference(chain), scope=SCOPE
    )
    value = json.loads(audit.receipt_json)
    assert [item["classification"] for item in value["account_bill_rows"]] == [
        "funding_type_subtype_conflict",
        "funding_type_subtype_conflict",
        "funding_payment_fields_unverified",
    ]
    assert all(item["payment_field"] is None for item in value["account_bill_rows"])
    assert "funding_bill_semantics_unverified" in value["blocking_reasons"]
    assert value["funding_accrual_at"] is None
    assert value["admission"] == "DENY"


def test_asset_bill_subtype_numbers_cannot_be_reused_for_trading_account():
    example = row(
        "bills_recent", type="8", subType="173", instType="SWAP", instId=INSTRUMENT
    )
    with pytest.raises(
        funding.FundingBillAuditError,
        match="funding_bill_account_endpoint_required",
    ):
        funding._classify_account_bill("/api/v5/asset/bills", example, "USDT")
    assert funding._classify_account_bill(
        "/api/v5/account/bills", {**example, "pnl": "-0.03"}, "USDT"
    ) == ("account_funding_payment_candidate", "negative")


def test_conflicting_or_missing_product_is_not_a_funding_payment_candidate():
    example = row(
        "bills_recent",
        type="8",
        subType="173",
        instId=INSTRUMENT,
        pnl="-0.03",
    )
    for product in (None, "SPOT", "FUTURES"):
        raw = dict(example)
        if product is not None:
            raw["instType"] = product
        assert funding._classify_account_bill("/api/v5/account/bills", raw, "USDT") == (
            "funding_payment_fields_unverified",
            "negative",
        )


@pytest.mark.asyncio
async def test_empty_account_bill_pages_do_not_mean_zero_funding(monkeypatch):
    chain = await recorded(monkeypatch)
    audit = funding.audit_funding_bills(
        chain, reference=observed.source_reference(chain), scope=SCOPE
    )
    value = json.loads(audit.receipt_json)
    assert value["account_bill_rows"] == []
    assert value["net_funding_cashflow"] is None
    assert value["funding_accrual_at"] is None
    assert "complete_account_cashflow_history_unproven" in value["blocking_reasons"]
    assert audit.admission == "DENY"


@pytest.mark.asyncio
async def test_changed_source_pin_and_forged_receipt_do_not_issue_audit(monkeypatch):
    chain = await recorded(monkeypatch)
    reference = observed.source_reference(chain)
    with pytest.raises(
        funding.FundingBillAuditError, match="funding_bill_source_invalid"
    ):
        funding.audit_funding_bills(
            chain, reference=replace(reference, packet_sha256="f" * 64), scope=SCOPE
        )
    with pytest.raises(
        funding.FundingBillAuditError, match="funding_bill_receipt_invalid"
    ):
        funding.FundingBillAudit(b'{"account_complete":true}')
