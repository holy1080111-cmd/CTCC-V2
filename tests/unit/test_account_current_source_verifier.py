"""Synthetic owned B1 acquisition; no private account or current authority."""

import json
from dataclasses import replace
from datetime import timedelta

import pytest

from app.trade_qualification import account_bootstrap_runtime as bootstrap
from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_current_source_verifier as current
from app.trade_qualification import account_observation_index as observed
from app.trade_qualification.reservations import LedgerScope
from tests.unit.test_account_ingestion_journal_contracts import setup
from tests.unit.test_qualification_account_capture import NOW, UID, ms, row
from tests.unit.test_qualification_account_collector import credentials, script
from tests.unit.test_qualification_account_materializer import source_pages
from tests.unit.test_qualification_account_v4 import plan as all_product_plan
from tests.unit.test_qualification_account_v4 import script as all_product_script

SCOPE = LedgerScope(account_id=UID, settlement_currency="USDT")


def flat_pages():
    pages = source_pages()
    for name in current.INVENTORY_STREAMS:
        pages[name] = [[]]
    pages["account_position_risk"][0][0]["posData"] = []
    return pages


async def recorded(monkeypatch, *, pages=None, all_product=True):
    session, harness, _, args, events = setup(monkeypatch)
    pages = flat_pages() if pages is None else pages
    if all_product:
        plan = all_product_plan()
        session = type(session)(
            credentials=credentials(),
            plan=plan,
            expected_plan_sha256=capture.plan_sha256(plan),
        )
        harness.script = all_product_script(source=pages)
    else:
        harness.script = script(pages=pages)
    await bootstrap.collect_bootstrap_recorded(session, **args)
    harness.assert_closed()
    return tuple(events)


def verify(chain, **changes):
    return current.verify_current_account_sources(
        chain,
        **{
            "reference": observed.source_reference(chain),
            "scope": SCOPE,
            "validated_at": NOW + timedelta(seconds=2),
            **changes,
        },
    )


def test_policy_v2_pins_current_v5_stream_and_algo_inventory():
    policy = json.loads(current.POLICY_BYTES)
    assert policy["version"] == "ctcc.current_account_source_policy.v2"
    assert policy["capture_plan_contract"] == "ctcc.demo_account_plan.v5"
    assert policy["current_streams"] == list(current.CURRENT_STREAMS)
    assert policy["inventory_streams"] == list(current.INVENTORY_STREAMS)
    assert policy["algo_order_types"] == [
        "conditional",
        "oco",
        "trigger",
        "move_order_stop",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("all_product", [False, True])
async def test_empty_legacy_algo_pages_are_revoked_even_with_exact_scope(
    monkeypatch, all_product
):
    chain = await recorded(monkeypatch, all_product=all_product)
    if not all_product:
        with pytest.raises(
            current.CurrentAccountSourceError,
            match="current_source_capture_plan_version_required",
        ):
            verify(chain)
        return
    before = tuple(item.event for item in chain)
    result = verify(chain)
    value = json.loads(result.receipt_json)
    assert value["schema_version"] == "ctcc.current_account_source_observation.v5"
    assert value["algo_coverage_revocation_sha256"] == (
        current.ALGO_COVERAGE_REVOCATION_SHA256
    )
    assert value["effective_policy_sha256"] != value["policy_sha256"]
    assert value["observed_flat"] is False
    assert value["blocking_reasons"] == ["algo_type_coverage_incomplete"]
    assert all(count == 0 for count in value["inventory_row_counts"].values())
    assert value["packet_schema_version"].endswith("v5")
    assert "source_fields_missing" not in value["packet_incomplete_reasons"]
    assert value["flat_start_permission"] is result.flat_start_permission is False
    assert value["execution_authority"] is result.execution_authority is False
    assert value["account_complete"] is result.account_complete is False
    assert "local_uncertain_or_untracked_exposure" in value["unverified"]
    assert tuple(item.event for item in chain) == before


@pytest.mark.asyncio
async def test_sealed_v5_four_empty_algo_queries_cannot_hide_chase_or_twap(
    monkeypatch,
):
    chain = await recorded(monkeypatch)
    packet_bytes = next(
        item.event.packet_payload for item in chain if item.event.packet_payload
    )
    result = json.loads(verify(chain).receipt_json)
    assert result["policy_sha256"] == current.POLICY_SHA256
    assert result["effective_policy_sha256"] == current.journal.digest(
        current.journal.canonical(
            [current.POLICY_SHA256, current.ALGO_COVERAGE_REVOCATION_SHA256]
        )
    )
    for unqueried in ("algo_chase", "algo_twap"):
        with pytest.raises(capture.AccountCaptureError, match="stream_invalid"):
            capture.account_request(all_product_plan(), unqueried)
    assert result["observed_flat"] is False
    assert result["observed_inventory_state"] == "incomplete_or_exposed"
    assert "algo_type_coverage_incomplete" in result["blocking_reasons"]
    assert packet_bytes == next(
        item.event.packet_payload for item in chain if item.event.packet_payload
    )
    assert result["account_complete"] is result["execution_authority"] is False


@pytest.mark.asyncio
async def test_futures_inapplicable_top_level_available_equity_preserves_value_but_not_flatness(
    monkeypatch,
):
    pages = flat_pages()
    pages["balance"][0][0]["availEq"] = ""
    data = json.loads(verify(await recorded(monkeypatch, pages=pages)).receipt_json)
    assert data["observed_flat"] is False
    assert "algo_type_coverage_incomplete" in data["blocking_reasons"]
    assert "source_fields_missing" not in data["packet_incomplete_reasons"]
    assert data["balance"]["available_equity"] == {
        "numerator": "800",
        "denominator": "1",
    }
    assert data["account_complete"] is data["execution_authority"] is False


@pytest.mark.asyncio
async def test_futures_inapplicable_risk_adjusted_equity_does_not_restore_flatness(
    monkeypatch,
):
    pages = flat_pages()
    pages["account_position_risk"][0][0]["adjEq"] = ""
    data = json.loads(verify(await recorded(monkeypatch, pages=pages)).receipt_json)
    assert data["observed_flat"] is False
    assert "algo_type_coverage_incomplete" in data["blocking_reasons"]
    assert "source_fields_missing" not in data["packet_incomplete_reasons"]
    assert data["account_complete"] is data["execution_authority"] is False


@pytest.mark.asyncio
async def test_incomplete_current_source_field_cannot_claim_observed_flat(monkeypatch):
    pages = flat_pages()
    pages["account_instruments"] = [[row("account_instruments", ctMult="")]]
    data = json.loads(verify(await recorded(monkeypatch, pages=pages)).receipt_json)
    assert data["observed_flat"] is False
    assert "source_fields_missing" in data["packet_incomplete_reasons"]
    assert "current_packet_source_fields_missing" in data["blocking_reasons"]
    assert data["account_complete"] is data["execution_authority"] is False


@pytest.mark.asyncio
async def test_old_unchanged_u_time_remains_old_while_measured_receipt_is_new(
    monkeypatch,
):
    pages = flat_pages()
    old = ms(NOW - timedelta(days=10))
    pages["balance"][0][0]["uTime"] = old
    pages["balance"][0][0]["details"][0]["uTime"] = old
    data = json.loads(verify(await recorded(monkeypatch, pages=pages)).receipt_json)
    stamp = data["balance"]["measured_stamp"]
    assert data["balance"]["source_update_times"]["account_uTime"]["raw"] == old
    assert data["balance"]["source_update_times"]["settlement_uTime"]["raw"] == old
    assert (
        data["balance"]["source_update_times"]["account_uTime"]["value"]
        < stamp["request_started_at"]
    )
    assert data["observed_flat"] is False
    assert "algo_type_coverage_incomplete" in data["blocking_reasons"]


@pytest.mark.asyncio
async def test_fresh_top_level_usd_does_not_replace_missing_settlement_equity(
    monkeypatch,
):
    pages = flat_pages()
    pages["balance"][0][0]["details"][0]["eq"] = ""
    data = json.loads(verify(await recorded(monkeypatch, pages=pages)).receipt_json)
    assert data["balance"]["equity"] is None
    assert data["observed_flat"] is False
    assert "settlement_equity_or_margin_missing" in data["blocking_reasons"]


@pytest.mark.asyncio
async def test_freshness_is_measured_at_explicit_cutoff_not_rewritten_source_time(
    monkeypatch,
):
    chain = await recorded(monkeypatch)
    fresh = json.loads(verify(chain).receipt_json)
    stale = json.loads(
        verify(chain, validated_at=NOW + timedelta(seconds=31)).receipt_json
    )
    assert fresh["observed_flat"] is False and stale["observed_flat"] is False
    assert "algo_type_coverage_incomplete" in fresh["blocking_reasons"]
    assert "measured_current_receipt_stale" in stale["blocking_reasons"]
    assert (
        fresh["balance"]["source_update_times"]
        == stale["balance"]["source_update_times"]
    )
    with pytest.raises(current.CurrentAccountSourceError):
        verify(chain, validated_at=NOW - timedelta(seconds=1))


@pytest.mark.asyncio
async def test_exposure_and_pending_pages_are_retained_without_reduction_or_protection_guess(
    monkeypatch,
):
    chain = await recorded(monkeypatch, pages=source_pages())
    data = json.loads(verify(chain).receipt_json)
    assert data["observed_flat"] is False
    assert data["inventory_row_counts"]["positions"] == 1
    assert data["inventory_row_counts"]["orders_pending"] == 1
    assert data["inventory_row_counts"]["algo_conditional"] == 1
    pending = [
        page for page in data["current_pages"] if page["stream"] == "orders_pending"
    ]
    assert len(pending) == 2 and pending[-1]["terminal"] is True
    assert pending[-1]["row_count"] == 0 and pending[-1]["previous_page_sha256"]
    assert "exact_active_protection" in data["unverified"]


@pytest.mark.asyncio
async def test_conflicting_anchor_prevents_observed_flat(monkeypatch):
    pages = flat_pages()
    pages["account_position_risk"][0][0]["balData"][0]["eq"] = "900"
    data = json.loads(verify(await recorded(monkeypatch, pages=pages)).receipt_json)
    assert data["observed_flat"] is False
    assert data["consistency_findings"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field", ["head_sha256", "packet_sha256", "plan_sha256", "session_binding_sha256"]
)
async def test_changed_source_pins_cannot_be_replaced_by_caller_result(
    monkeypatch, field
):
    chain = await recorded(monkeypatch)
    pin = replace(observed.source_reference(chain), **{field: "0" * 64})
    with pytest.raises(current.CurrentAccountSourceError):
        verify(chain, reference=pin)
    with pytest.raises(current.CurrentAccountSourceError):
        verify(chain, scope=LedgerScope(account_id="999", settlement_currency="USDT"))
    with pytest.raises(current.CurrentAccountSourceError):
        current.verify_current_account_sources(
            verify(chain),
            reference=observed.source_reference(chain),
            scope=SCOPE,
            validated_at=NOW + timedelta(seconds=2),
        )


@pytest.mark.asyncio
async def test_missing_original_terminal_is_not_empty_inventory(monkeypatch):
    chain = await recorded(monkeypatch)
    with pytest.raises(current.CurrentAccountSourceError):
        current.verify_current_account_sources(
            chain[:-1],
            reference=observed.source_reference(chain),
            scope=SCOPE,
            validated_at=NOW + timedelta(seconds=2),
        )


@pytest.mark.asyncio
async def test_other_product_retained_in_v5_and_denies_supported_flat_scope(
    monkeypatch,
):
    pages = flat_pages()
    pages["orders_pending"] = [
        [row("orders_pending", instType="SPOT", instId="BTC-USDT", posSide="")],
        [],
    ]
    data = json.loads(
        verify(await recorded(monkeypatch, pages=pages, all_product=True)).receipt_json
    )
    assert data["inventory_row_counts"]["orders_pending"] == 1
    assert "current_exposure_product_unsupported" in data["blocking_reasons"]
    assert data["observed_flat"] is False
