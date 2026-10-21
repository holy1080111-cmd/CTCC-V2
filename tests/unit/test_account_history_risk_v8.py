"""Synthetic original-source replay; no exchange/clock/account acceptance."""

import json
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace

import pytest

from app.domain.source_primitives import canonical, sha
from app.trade_qualification import account_history_risk_v8 as risk
from app.trade_qualification import account_lifecycle_history as lifecycle
from app.trade_qualification import account_observation_index as observed
from app.trade_qualification import account_positions_history_collector_v1 as positions
from app.trade_qualification import account_positions_history_v1 as position_pages
from app.trade_qualification import settled_funding_history_source as funding
from tests.unit import test_account_lifecycle_history as original
from tests.unit.test_qualification_account_capture import MAIN_UID, NOW, UID, ms, row


def source_archive(
    reference, *, changes=None, duplicate=False, position_mode="net_mode"
):
    identity = positions.DemoPositionIdentityV1(
        UID, MAIN_UID, "2", position_mode, "USDT"
    )
    start = NOW + timedelta(seconds=1900)
    plan = position_pages.PositionHistoryPlanV1(
        "global",
        "https://openapi.okx.com",
        identity.sha256,
        reference.session_binding_sha256,
        start,
        int(ms(NOW)),
    )
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
    raw_rows = [selected]
    if duplicate:
        raw_rows.append({**selected, "uTime": ms(NOW + timedelta(seconds=900))})
    pages = []
    for index, rows in enumerate((raw_rows, [])):
        at = start + timedelta(milliseconds=5 + 5 * index)
        previous = pages[-1] if pages else None
        pages.append(
            position_pages.parse_page(
                plan,
                canonical({"code": "0", "msg": "", "data": rows}),
                page_index=index,
                after=None if previous is None else previous.next_after,
                previous_page_sha256=None if previous is None else previous.page_sha256,
                request_started_at=at,
                headers_received_at=at + timedelta(milliseconds=1),
                body_completed_at=at + timedelta(milliseconds=2),
            )
        )
    config = canonical(
        {
            "code": "0",
            "msg": "",
            "data": [
                {
                    "uid": UID,
                    "mainUid": MAIN_UID,
                    "acctLv": "2",
                    "posMode": position_mode,
                    "settleCcy": "USDT",
                }
            ],
        }
    )
    before = positions._RawPage(
        config,
        start + timedelta(milliseconds=1),
        start + timedelta(milliseconds=2),
        start + timedelta(milliseconds=3),
        None,
    )
    after = positions._RawPage(
        config,
        start + timedelta(milliseconds=16),
        start + timedelta(milliseconds=17),
        start + timedelta(milliseconds=18),
        None,
    )
    payload = positions.freeze_source_bound_archive(
        plan, identity, before, tuple(pages), after, (None, None)
    )
    return risk.PositionHistorySourceV8(plan, identity, payload, sha(payload))


def funding_source(*, event_at=None, duplicate=False):
    event_at = event_at or NOW + timedelta(seconds=600)
    scope = funding.FundingHistoryScope("global", "BTC-USDT-SWAP", NOW)
    event = {
        "instType": "SWAP",
        "instId": "BTC-USDT-SWAP",
        "fundingTime": ms(event_at),
        "realizedRate": "0.0001",
    }
    pages = []
    for index, rows in enumerate(([event, event] if duplicate else [event], [])):
        at = NOW + timedelta(seconds=2000 + index)
        query = (("instId", scope.instrument_id), ("limit", "400"))
        if index:
            query += (("after", ms(event_at)),)
        pages.append(
            funding.RawFundingHistoryPage(
                "GET",
                "https://openapi.okx.com",
                funding.ENDPOINT,
                query,
                "1",
                at,
                at + timedelta(milliseconds=1),
                1_000_000_000 + index * 1_000_000_000,
                1_001_000_000 + index * 1_000_000_000,
                200,
                canonical({"code": "0", "msg": "", "data": rows}),
            )
        )
    pages = tuple(pages)
    return risk.SettledFundingSourceV8(
        scope, pages, funding.source_pages_sha256(scope, pages)
    )


async def inputs(
    monkeypatch,
    *,
    witness=True,
    bill_currency="USDT",
    position_changes=None,
    duplicate_position=False,
    witness_changes=None,
    position_mode="net_mode",
):
    old_script = original.script

    def declared_script(*, pages):
        for stream in ("config_before", "config_after"):
            for page in pages.setdefault(stream, [[row(stream)]]):
                for item in page:
                    item["posMode"] = position_mode
        for page in pages.get("positions", ()):
            for item in page:
                item.update(witness_changes or {})
        if any(
            item.get("billId") == "902"
            for page in pages.get("fills_history", ())
            for item in page
        ):
            bill = row(
                "bills_archive",
                "990",
                ccy=bill_currency,
                type="8",
                subType="173",
                instId="BTC-USDT-SWAP",
                pnl="-0.03",
                fee="0",
                balChg="-0.03",
                ts=ms(NOW + timedelta(seconds=1100)),
            )
            pages["bills_recent"] = pages["bills_archive"] = [[bill], []]
        return old_script(pages=pages)

    monkeypatch.setattr(original, "script", declared_script)
    chains = [
        await original.original_capture(
            monkeypatch, NOW, base=NOW, filled=False, case="A01"
        )
    ]
    if witness:
        chains.append(
            await original.original_capture(
                monkeypatch,
                NOW + timedelta(seconds=600),
                base=NOW,
                filled=True,
                case="A03",
            )
        )
    chains.append(
        await original.original_capture(
            monkeypatch,
            NOW + timedelta(seconds=1800),
            base=NOW,
            filled=True,
            case="A01",
        )
    )
    sources = tuple((chain, observed.source_reference(chain)) for chain in chains)
    window = observed.ExecutionWindow(
        NOW - timedelta(hours=1), NOW + timedelta(seconds=1200)
    )
    batches = original.original_index(sources, window)
    return sources, {
        "scope": original.SCOPE,
        "window": window,
        "index_batches": batches,
        "expected_index_head_sha256": batches[-1].event_sha256,
        "expected_source_set_sha256": lifecycle.source_set_sha256(sources),
        "position_source": source_archive(
            sources[-1][1],
            changes=position_changes,
            duplicate=duplicate_position,
            position_mode=position_mode,
        ),
        "funding_sources": (funding_source(),),
    }


def replay(values):
    result = risk.replay_account_history_risk_v8(values[0], **values[1])
    return result, json.loads(result.receipt_json)


@pytest.mark.asyncio
async def test_original_three_sources_reconcile_net_without_promoting_bill_posting_time(
    monkeypatch,
):
    values = await inputs(monkeypatch)
    result, data = replay(values)
    assert result.receipt_json == replay(values)[0].receipt_json
    assert data["reconciled_run_count"] == 1
    part = data["closed_runs"][0]
    assert part["fill_gross_pnl"] == {"numerator": "2", "denominator": "5"}
    assert part["signed_fill_fees"] == {"numerator": "-1", "denominator": "50"}
    assert part["position_funding_total"] == {"numerator": "-3", "denominator": "100"}
    assert part["reconciled_closed_run_net_pnl"] == {
        "numerator": "7",
        "denominator": "20",
    }
    assert part["observed_positive_net"] is True
    assert part["closed_at_execution"] == (NOW + timedelta(seconds=900)).isoformat()
    assert (
        part["position_history_row"]["position_updated_at"]
        == (NOW + timedelta(seconds=901)).isoformat()
    )
    assert (
        data["account_bills"][0]["record_posted_at"]
        == (NOW + timedelta(seconds=1100)).isoformat()
    )
    event = part["public_settlement_event_candidates"][0]
    assert event["settlement_at"] == (NOW + timedelta(seconds=600)).isoformat()
    assert part["account_funding_accrual_events"] is None
    assert data["account_bills"][0]["funding_accrual_at"] is None
    assert data["loss_streak_at_history_start"] is None
    assert not set(data) & {
        "owner",
        "snapshot",
        "execution_authority",
        "account_complete",
    }
    assert (
        "loss_streak_seed_or_complete_positive_reset_unproven"
        in data["missing_obligations"]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"fundingFee": ""}, "position_financial_component_missing"),
        ({"realizedPnl": "0.38"}, "position_fill_financial_totals_conflict"),
        ({"fee": "0"}, "position_fill_financial_totals_conflict"),
        ({"closeTotalPos": "2"}, "position_fill_quantity_price_or_direction_conflict"),
        ({"settledPnl": None}, "position_settled_pnl_applicability_unknown"),
    ],
)
async def test_original_financial_missing_or_conflict_preserves_unknown(
    monkeypatch, changes, reason
):
    _result, data = replay(await inputs(monkeypatch, position_changes=changes))
    assert data["reconciled_run_count"] == 0
    part = data["closed_runs"][0]
    assert part["reconciled_closed_run_net_pnl"] is None
    assert reason in part["missing_obligations"]
    assert data["unjoined_position_history_rows"]


@pytest.mark.asyncio
async def test_no_posid_witness_or_reused_history_cannot_mint_net(monkeypatch):
    _result, data = replay(await inputs(monkeypatch, witness=False))
    assert data["reconciled_run_count"] == 0
    assert (
        "position_lifecycle_identity_witness_missing_or_ambiguous"
        in data["closed_runs"][0]["missing_obligations"]
    )


@pytest.mark.asyncio
async def test_original_position_witness_signed_quantity_must_match_fill_inventory(
    monkeypatch,
):
    _result, data = replay(await inputs(monkeypatch, witness_changes={"pos": "-4"}))
    assert data["reconciled_run_count"] == 0
    assert (
        "position_lifecycle_witness_inventory_conflict"
        in data["closed_runs"][0]["missing_obligations"]
    )


@pytest.mark.asyncio
async def test_original_hedge_mode_config_cannot_use_signed_net_position_math(
    monkeypatch,
):
    values = await inputs(monkeypatch, position_mode="long_short_mode")
    with pytest.raises(
        risk.AccountHistoryRiskError, match="history_risk_account_mode_unsupported"
    ):
        replay(values)


def test_original_position_history_future_update_or_cutoff_is_rejected_before_reduction():
    reference = SimpleNamespace(session_binding_sha256="b" * 64)
    with pytest.raises(position_pages.PositionHistoryError, match="row_clock_invalid"):
        source_archive(reference, changes={"uTime": ms(NOW + timedelta(seconds=1901))})
    source = source_archive(reference)
    with pytest.raises(position_pages.PositionHistoryError, match="plan_invalid"):
        replace(source.plan, cutoff_u_time_ms=int(ms(source.plan.created_at)) + 1)


@pytest.mark.asyncio
async def test_duplicate_position_versions_never_select_convenient_total(monkeypatch):
    _result, data = replay(await inputs(monkeypatch, duplicate_position=True))
    assert data["reconciled_run_count"] == 0
    assert (
        "position_full_close_source_missing_or_ambiguous"
        in data["closed_runs"][0]["missing_obligations"]
    )


@pytest.mark.asyncio
async def test_foreign_currency_bill_remains_account_wide_unmapped(monkeypatch):
    _result, data = replay(await inputs(monkeypatch, bill_currency="BTC"))
    assert "account_wide_bill_currency_unmapped" in data["missing_obligations"]
    assert data["account_bills"][0]["signed_posted_balance_change"] is None
    assert data["account_bills"][0]["currency_matches"] is False


@pytest.mark.asyncio
async def test_public_funding_collision_is_not_resolved_by_bill_generation(monkeypatch):
    values = await inputs(monkeypatch)
    values[1]["funding_sources"] = (
        funding_source(event_at=NOW + timedelta(seconds=900)),
    )
    _result, data = replay(values)
    part = data["closed_runs"][0]
    assert part["net_outcome_arithmetic_reconciled"] is True
    assert "funding_at_fill_time_ambiguous" in part["missing_obligations"]
    assert part["account_funding_accrual_events"] is None


@pytest.mark.asyncio
async def test_duplicate_public_funding_rows_and_rehashed_original_archive_fail(
    monkeypatch,
):
    values = await inputs(monkeypatch)
    values[1]["funding_sources"] = (funding_source(duplicate=True),)
    with pytest.raises(
        risk.AccountHistoryRiskError,
        match="history_risk_original_source_replay_invalid",
    ):
        replay(values)
    values[1]["funding_sources"] = ()
    position = values[1]["position_source"]
    edited = json.loads(position.archive)
    edited["history_archive"]["chain_sha256"] = "0" * 64
    raw = canonical(edited)
    values[1]["position_source"] = replace(
        position, archive=raw, expected_archive_sha256=sha(raw)
    )
    with pytest.raises(
        risk.AccountHistoryRiskError,
        match="history_risk_original_source_replay_invalid",
    ):
        replay(values)
