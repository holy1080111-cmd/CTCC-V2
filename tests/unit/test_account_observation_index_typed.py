"""B3 typed source-time replay over synthetic original B1 captures only."""

import json
from datetime import timedelta

import pytest

from app.trade_qualification import account_bootstrap_runtime as bootstrap
from app.trade_qualification import account_capture_journal as journal
from app.trade_qualification import account_history_query_verifier as query
from app.trade_qualification import account_observation_index as observed
from tests.unit.test_account_ingestion_journal_contracts import setup
from tests.unit.test_account_observation_index import (
    SCOPE,
    WINDOW,
    empty_history_window,
)
from tests.unit.test_qualification_account_capture import NOW, ms, row
from tests.unit.test_qualification_account_collector import script
from tests.unit.test_qualification_account_materializer import source_pages


def _sources(*chains):
    return tuple((chain, observed.source_reference(chain)) for chain in chains)


async def captured(monkeypatch, *, fills=(), bills=(), tick=0):
    session, harness, _, args, events = setup(monkeypatch)
    pages = source_pages()
    pages["fills_history"] = [list(fills), []] if fills else [[]]
    pages["bills_archive"] = [list(bills), []] if bills else [[]]
    harness.script = script(pages=pages)
    harness.clock.calls = tick
    await bootstrap.collect_bootstrap_recorded(session, **args)
    harness.assert_closed()
    return tuple(events)


def typed(*chains):
    return observed.replay_observation_index_typed_v1(
        _sources(*chains), scope=SCOPE, window=WINDOW
    )


@pytest.mark.asyncio
async def test_original_chain_yields_typed_bills_and_fills_without_authority(
    monkeypatch,
):
    fill = row(
        "fills_history",
        fillPnl="-3",
        fee="-0.1",
        fillTime=ms(NOW - timedelta(seconds=900)),
        ts=ms(NOW - timedelta(seconds=1)),
    )
    bill = row("bills_archive", "901", ts=ms(NOW - timedelta(seconds=2)))
    chain = await captured(monkeypatch, fills=[fill], bills=[bill])
    result = typed(chain)
    value = json.loads(result.receipt_json)
    pins = observed._pins(observed.source_reference(chain), SCOPE)
    v1 = query.verify_history_query_chain(chain, **pins)
    v2 = query.verify_history_query_chain_v2(chain, **pins)

    assert result == typed(chain)
    assert value["schema_version"] == "ctcc.observed_account_execution_window.v3"
    assert value["policy_sha256"] == observed.TYPED_POLICY_SHA256
    assert value["captures"][0]["query_receipt_sha256"] == v2.receipt_sha256
    assert value["captures"][0]["legacy_v1_query_receipt_sha256"] == v1.receipt_sha256
    assert {
        (item["family"], item["source_ts_semantics"]) for item in value["source_index"]
    } == {
        ("fills", "fill_record_generated_at"),
        ("bills", "balance_update_completed_at"),
    }
    assert all("generation_at" not in item for item in value["source_index"])
    assert all(item["time_domain"] != "funding_accrual" for item in value["coverage"])
    assert value["cashflows"][0]["execution_at"] < value["window"]["ended_at"]
    assert value["cashflows"][0]["source_ts_at"] > value["window"]["ended_at"]
    assert value["observed_fill_cashflow_total"] == {
        "numerator": "-31",
        "denominator": "10",
    }
    assert "funding_accrual" in value["unverified"]
    assert value["source_authenticity_verified"] is False
    assert result.account_complete is result.execution_authority is False
    assert result.admission == value["admission"] == "DENY"


@pytest.mark.asyncio
async def test_typed_late_bill_uses_balance_update_time_not_accrual(monkeypatch):
    bill = row("bills_archive", "901", ts=ms(NOW - timedelta(seconds=2)))
    before = await captured(monkeypatch)
    after = await captured(monkeypatch, bills=[bill], tick=5000)
    value = json.loads(typed(before, after).receipt_json)
    assert any(
        item["kind"] == "late_observation_in_prior_query" for item in value["findings"]
    )
    assert value["observed_fill_cashflow_total"] is None
    assert "source_observations_require_reconciliation" in value["blocking_reasons"]
    bill_row = next(item for item in value["source_index"] if item["family"] == "bills")
    assert bill_row["source_ts_semantics"] == "balance_update_completed_at"
    assert "funding_accrual" in value["unverified"]


@pytest.mark.asyncio
async def test_typed_conflict_and_missing_preserve_unknown(monkeypatch):
    bill = row("bills_archive", "901", ts=ms(NOW - timedelta(seconds=2)))
    first = await captured(monkeypatch, bills=[bill])
    conflict = await captured(monkeypatch, bills=[{**bill, "fee": "-0.2"}], tick=5000)
    missing = await captured(monkeypatch, tick=5000)
    for second, expected in (
        (conflict, "conflicting_overlap"),
        (missing, "previous_row_missing_in_current_query"),
    ):
        value = json.loads(typed(first, second).receipt_json)
        assert expected in {item["kind"] for item in value["findings"]}
        assert value["observed_fill_cashflow_total"] is None
        assert value["admission"] == "DENY"


@pytest.mark.asyncio
@pytest.mark.parametrize("current_start_days,requires_gap", [(7, True), (14, False)])
async def test_empty_typed_intervals_are_compared_in_their_own_domains(
    monkeypatch, current_start_days, requires_gap
):
    previous = await empty_history_window(
        monkeypatch, start_days=20, end_days=14, tick=0
    )
    current = await empty_history_window(
        monkeypatch, start_days=current_start_days, end_days=0, tick=5000
    )
    result = typed(previous, current)
    value = json.loads(result.receipt_json)
    gaps = [
        item
        for item in value["findings"]
        if item["kind"] == "effective_source_ts_overlap_gap"
    ]
    assert bool(gaps) is requires_gap
    assert value["admission"] == "DENY"
    if requires_gap:
        assert value["observed_fill_cashflow_total"] is None
        assert {
            (item["family"], item["time_domain"]) for item in gaps[0]["domains"]
        } == {
            ("fills", "fill_record_generated_at"),
            ("bills", "balance_update_completed_at"),
        }


@pytest.mark.asyncio
async def test_old_and_typed_policy_epochs_cannot_be_interchanged(monkeypatch):
    chain = await captured(monkeypatch)
    sources = _sources(chain)
    old = observed.replay_observation_index(sources, scope=SCOPE, window=WINDOW)
    typed_result = typed(chain)
    assert old == observed.replay_observation_index(sources, scope=SCOPE, window=WINDOW)
    assert old.receipt_json != typed_result.receipt_json
    assert (
        json.loads(old.receipt_json)["schema_version"]
        == "ctcc.observed_account_execution_window.v2"
    )
    with pytest.raises(
        observed.AccountObservationError, match="observation_policy_mismatch"
    ):
        observed.replay_observation_index(
            sources,
            scope=SCOPE,
            window=WINDOW,
            expected_policy_sha256=observed.TYPED_POLICY_SHA256,
        )
    with pytest.raises(
        observed.AccountObservationError, match="observation_policy_mismatch"
    ):
        observed.replay_observation_index_typed_v1(
            sources,
            scope=SCOPE,
            window=WINDOW,
            expected_policy_sha256=observed.POLICY_SHA256,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "corruption",
    [
        "schema_version",
        "policy_sha256",
        "state",
        "legacy_v1_receipt_sha256",
        "source_authenticity_verified",
        "query_domain",
        "time_domain",
        "covered_bool",
        "row_semantics",
        "row_old_field",
    ],
)
async def test_legacy_or_malformed_typed_query_is_denied(monkeypatch, corruption):
    chain = await captured(monkeypatch, fills=[row("fills_history", fillPnl="-1")])
    original = query.verify_history_query_chain_v2

    def corrupted(chain_arg, **kwargs):
        value = json.loads(original(chain_arg, **kwargs).receipt_json)
        if corruption == "schema_version":
            value["schema_version"] = "ctcc.observed_account_history_query.v1"
        elif corruption == "policy_sha256":
            value["policy_sha256"] = query.POLICY_SHA256
        elif corruption == "state":
            value["state"] = "retained_query_chain_verified"
        elif corruption == "legacy_v1_receipt_sha256":
            value["legacy_v1_receipt_sha256"] = "bad"
        elif corruption == "source_authenticity_verified":
            value["source_authenticity_verified"] = True
        elif corruption == "query_domain":
            value["queries"][0]["time_domain"] = "funding_accrual"
        elif corruption == "time_domain":
            value["coverage"][0]["time_domain"] = "funding_accrual"
        elif corruption == "covered_bool":
            value["coverage"][0]["requested_source_ts_window_covered"] = 1
        elif corruption == "row_semantics":
            value["rows"][0]["source_ts_semantics"] = "funding_accrual"
        else:
            value["rows"][0]["generation_at"] = value["rows"][0]["source_ts_at"]
        return query.HistoryQueryVerification(journal.canonical(value))

    monkeypatch.setattr(query, "verify_history_query_chain_v2", corrupted)
    with pytest.raises(observed.AccountObservationError):
        typed(chain)
