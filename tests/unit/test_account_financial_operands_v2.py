"""Declared synthetic original rows/native archive format; no native acceptance."""

import json
from dataclasses import replace
from datetime import timedelta

import pytest

from app.domain.source_primitives import canonical, sha
from app.trade_qualification import account_financial_operands_v2 as financial
from app.trade_qualification import account_lifecycle_history as lifecycle
from app.trade_qualification import account_observation_index as observed
from tests.unit import test_account_lifecycle_history as original
from tests.unit.test_account_current_source_verifier import flat_pages, recorded
from tests.unit.test_account_history_risk_v8 import inputs
from tests.unit.test_qualification_account_capture import MAIN_UID, NOW, UID, ms, row


def native_position_source(reference, *, changes=None, settlement=None, session=None):
    """Declare raw native-format inputs before freezing; never relabel old bytes."""
    positions, pages = financial.positions, financial.position_pages
    identity = positions._NativePositionIdentityV1(UID, MAIN_UID, "2", "net_mode")
    start = NOW + timedelta(seconds=1900)
    plan = pages.PositionHistoryPlanV1(
        "global",
        "https://openapi.okx.com",
        identity.sha256,
        reference.session_binding_sha256 if session is None else session,
        start,
        int(ms(NOW)),
    )
    config = {"uid": UID, "mainUid": MAIN_UID, "acctLv": "2", "posMode": "net_mode"}
    if settlement is not None:
        config["settleCcy"] = settlement
    raw_config = canonical({"code": "0", "msg": "", "data": [config]})
    selected = {
        "posId": "950",
        "instId": "BTC-USDT-SWAP",
        "instType": "SWAP",
        "posSide": "net",
        "mgnMode": "cross",
        "ccy": "USDT",
        "type": "2",
        "direction": "long",
        "cTime": ms(NOW + timedelta(seconds=300)),
        "uTime": ms(NOW + timedelta(seconds=901)),
        "openAvgPx": "100",
        "closeAvgPx": "110",
        "openMaxPos": "4",
        "closeTotalPos": "4",
        "realizedPnl": "0.35",
        "pnl": "0.4",
        "fee": "-0.02",
        "fundingFee": "-0.03",
        "liqPenalty": "0",
        "settledPnl": "",
        **(changes or {}),
    }
    chain = []
    for index, data in enumerate(([selected], [])):
        at = start + timedelta(milliseconds=4 + index * 3)
        previous = chain[-1] if chain else None
        chain.append(
            pages.parse_page(
                plan,
                canonical({"code": "0", "msg": "", "data": data}),
                page_index=index,
                after=None if previous is None else previous.next_after,
                previous_page_sha256=None if previous is None else previous.page_sha256,
                request_started_at=at,
                headers_received_at=at + timedelta(milliseconds=1),
                body_completed_at=at + timedelta(milliseconds=2),
            )
        )
    before = positions._RawPage(
        raw_config,
        start + timedelta(milliseconds=1),
        start + timedelta(milliseconds=2),
        start + timedelta(milliseconds=3),
        "c" * 64,
    )
    after = positions._RawPage(
        raw_config,
        start + timedelta(milliseconds=10),
        start + timedelta(milliseconds=11),
        start + timedelta(milliseconds=12),
        "c" * 64,
    )
    archive = positions._freeze_native_source_archive(
        plan, identity, before, tuple(chain), after, ("c" * 64, "c" * 64)
    )
    return financial.NativePositionSourceV2(plan, identity, archive, sha(archive))


def derive(sources, arguments, *, position_source=None):
    selected = {
        key: value
        for key, value in arguments.items()
        if key not in {"position_source", "funding_sources"}
    }
    return financial.derive_account_financial_operands_v2(
        sources,
        **selected,
        position_source=native_position_source(sources[-1][1])
        if position_source is None
        else position_source,
        checked_at=NOW + timedelta(seconds=2010),
    )


@pytest.mark.asyncio
async def test_original_native_archive_reconciles_bounded_net_with_unknown_account_setting(
    monkeypatch,
):
    sources, arguments = await inputs(monkeypatch)
    position = native_position_source(sources[-1][1])
    result = derive(sources, arguments, position_source=position)
    data = json.loads(result.receipt_json)
    assert (
        result.receipt_json
        == derive(sources, arguments, position_source=position).receipt_json
    )
    assert (
        json.loads(position.archive)["version"]
        == financial.positions.NATIVE_POSITION_VERSION
    )
    assert data["observed_account_usd_settle_currency"] is None
    assert (
        "native_position_account_settlement_setting_unknown_or_different"
        in data["missing_obligations"]
    )
    assert data["reconciled_run_count"] == 1
    run = data["closed_runs"][0]
    assert run["reconciled_closed_run_net_pnl"] == {
        "numerator": "7",
        "denominator": "20",
    }
    assert (
        run["position_history_row"]["locator"]["archive_sha256"]
        == position.expected_archive_sha256
    )
    assert run["closed_at_execution"] == (NOW + timedelta(seconds=900)).isoformat()
    for key in (
        "daily_net_pnl",
        "weekly_net_pnl",
        "loss_streak_seed",
        "loss_streak",
        "funding_accrual_events",
        "current_history_tail_finality",
        "historical_native_hwm",
    ):
        assert data[key] is None
    for key in (
        "account_complete",
        "account_revision_published",
        "clear_stop_eligible",
        "arm_eligible",
        "execution_authority",
        "historical_native_prefix_verified",
    ):
        assert data[key] is False
    assert data["admission"] == "DENY"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"fundingFee": ""},
        {"realizedPnl": "0.38"},
        {"fee": None},
        {"settledPnl": None},
    ],
)
async def test_missing_or_conflicting_position_components_never_become_zero_net(
    monkeypatch, changes
):
    sources, arguments = await inputs(monkeypatch)
    data = json.loads(
        derive(
            sources,
            arguments,
            position_source=native_position_source(sources[-1][1], changes=changes),
        ).receipt_json
    )
    assert data["reconciled_run_count"] == 0
    assert all(
        run["reconciled_closed_run_net_pnl"] is None for run in data["closed_runs"]
    )
    assert data["unjoined_position_history_rows"]
    assert data["daily_net_pnl"] is data["funding_accrual_events"] is None


@pytest.mark.asyncio
async def test_cross_session_position_source_rejects_without_overwriting_original_references(
    monkeypatch,
):
    sources, arguments = await inputs(monkeypatch)
    before = lifecycle.source_set_sha256(sources)
    with pytest.raises(
        financial.AccountFinancialOperandsError, match="position_session_changed"
    ):
        derive(
            sources,
            arguments,
            position_source=native_position_source(sources[-1][1], session="f" * 64),
        )
    assert lifecycle.source_set_sha256(sources) == before


@pytest.mark.asyncio
async def test_legacy_diagnostic_archive_is_not_retagged_as_native_source(monkeypatch):
    sources, arguments = await inputs(monkeypatch)
    native = native_position_source(sources[-1][1])
    old = arguments["position_source"].archive
    assert json.loads(old)["version"] != json.loads(native.archive)["version"]
    with pytest.raises(financial.AccountFinancialOperandsError):
        derive(
            sources,
            arguments,
            position_source=replace(
                native,
                archive=old,
                expected_archive_sha256=sha(old),
            ),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "balance_change,fee,reconciled",
    [
        ("-0.03", "0", True),
        ("0.03", "0", False),
        ("-0.03", "", False),
    ],
)
async def test_original_all_product_bill_candidates_preserve_consistency_unknowns(
    monkeypatch, balance_change, fee, reconciled
):
    pages = flat_pages()
    bill = row(
        "bills_recent",
        "900",
        instId="BTC-USDT-SWAP",
        instType="SWAP",
        type="8",
        subType="173",
        pnl="-0.03",
        balChg=balance_change,
        fee=fee,
        ts=ms(NOW - timedelta(seconds=60)),
    )
    pages["bills_recent"] = pages["bills_archive"] = [[bill], []]
    chain = await recorded(monkeypatch, pages=pages)
    reference = observed.source_reference(chain)
    sources = ((chain, reference),)
    packet = lifecycle._packet(chain, reference)
    window = observed.ExecutionWindow(
        packet.plan.history_start, packet.plan.history_end - timedelta(seconds=300)
    )
    batches = original.original_index(sources, window)
    data = json.loads(
        derive(
            sources,
            {
                "scope": original.SCOPE,
                "window": window,
                "index_batches": batches,
                "expected_index_head_sha256": batches[-1].event_sha256,
                "expected_source_set_sha256": lifecycle.source_set_sha256(sources),
            },
        ).receipt_json
    )
    assert len(data["funding_payment_candidates"]) == 1
    candidate = data["funding_payment_candidates"][0]
    assert len(candidate["source_locators"]) == 2
    assert candidate["observed_signed_pnl"] == {"numerator": "-3", "denominator": "100"}
    assert (candidate["reconciled_candidate_amount"] is not None) is reconciled
    assert (
        candidate["effective_accrual_at"] is candidate["account_funding_event"] is None
    )
    assert candidate["complete_payment_or_accrual_verified"] is False
    if fee == "":
        assert candidate["fee_field_observed_zero"] is None
    assert data["funding_bill_audits"] and data["retained_account_bills"]
    assert data["funding_accrual_events"] is data["weekly_net_pnl"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [{"pnl": ""}, {"type": "9"}, {"ccy": "BTC"}])
async def test_incomplete_or_conflicting_bill_fields_stay_audited_without_payment_claim(
    monkeypatch, changes
):
    pages = flat_pages()
    bill = row(
        "bills_recent",
        "900",
        instId="BTC-USDT-SWAP",
        instType="SWAP",
        type="8",
        subType="173",
        pnl="-0.03",
        balChg="-0.03",
        fee="0",
        ts=ms(NOW - timedelta(seconds=60)),
    )
    bill.update(changes)
    pages["bills_recent"] = pages["bills_archive"] = [[bill], []]
    chain = await recorded(monkeypatch, pages=pages)
    reference = observed.source_reference(chain)
    sources = ((chain, reference),)
    packet = lifecycle._packet(chain, reference)
    window = observed.ExecutionWindow(
        packet.plan.history_start, packet.plan.history_end - timedelta(seconds=300)
    )
    batches = original.original_index(sources, window)
    data = json.loads(
        derive(
            sources,
            {
                "scope": original.SCOPE,
                "window": window,
                "index_batches": batches,
                "expected_index_head_sha256": batches[-1].event_sha256,
                "expected_source_set_sha256": lifecycle.source_set_sha256(sources),
            },
        ).receipt_json
    )
    assert data["funding_payment_candidates"] == []
    assert data["retained_account_bills"] and data["funding_bill_audits"]
    assert "funding_bill_semantics_unverified" in data["missing_obligations"]
    assert data["funding_accrual_events"] is data["daily_net_pnl"] is None
