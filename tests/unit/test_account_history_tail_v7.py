"""Synthetic V7 four-chain replay; never account completion or authority."""

import json
from datetime import timedelta

import pytest

from app.trade_qualification import account_capture_journal as journal
from app.trade_qualification import account_history_tail_v7 as tail_v7
from app.trade_qualification import account_observation_index as observed
from tests.unit.test_account_current_history_join import recorded_current
from tests.unit.test_account_current_source_v7 import recorded_v7
from tests.unit.test_account_current_source_verifier import SCOPE
from tests.unit.test_account_history_tail_prerequisite import recorded_history
from tests.unit.test_qualification_account_capture import NOW, row
from tests.unit.test_qualification_account_collector import SECRETS
from tests.unit.test_qualification_account_materializer import ledger_evidence


async def four_v7_chains(
    monkeypatch, *, first=(), later=(), followup_offset=4, followup_changes=None
):
    old = await recorded_history(monkeypatch, offset_seconds=0, fills=first)
    current, _ = await recorded_v7(monkeypatch, offset_seconds=1)
    continuation = await recorded_history(
        monkeypatch,
        offset_seconds=3,
        fills=later,
        plan_changes={
            "created_at": NOW + timedelta(seconds=2),
            "history_end": NOW + timedelta(seconds=2),
        },
    )
    followup, _ = await recorded_v7(
        monkeypatch,
        offset_seconds=followup_offset,
        plan_changes=followup_changes,
    )
    return old, current, continuation, followup


def inputs(chains, **changes):
    old, current, continuation, followup = chains
    return {
        "history_chain": old,
        "history_reference": observed.source_reference(old),
        "current_chain": current,
        "current_reference": observed.source_reference(current),
        "continuation_chain": continuation,
        "continuation_reference": observed.source_reference(continuation),
        "followup_current_chain": followup,
        "followup_current_reference": observed.source_reference(followup),
        "scope": SCOPE,
        "validated_at": NOW + timedelta(seconds=6),
        **changes,
    }


@pytest.mark.asyncio
async def test_v7_four_chains_replay_both_eight_algo_currents_and_still_deny(
    monkeypatch,
):
    sample = row("fills_history")
    chains = await four_v7_chains(monkeypatch, first=[sample], later=[sample])
    result = tail_v7.verify_v7_history_tail_diagnostic(**inputs(chains))
    value = json.loads(result.receipt_json)
    assert value["schema_version"] == "ctcc.demo_history_tail_followup_current.v2"
    assert value["policy_sha256"] == tail_v7.POLICY_SHA256
    assert len({ref["capture_id"] for ref in value["source_references"]}) == 4
    assert value["eight_algo_empty_terminal_chains_replayed"] is True
    assert value["history_terminal_db_timestamps_before_current_requests"] is True
    assert value["native_pre_http_history_commit_verified"] is False
    assert value["observed_current_flat"] is True
    assert value["observed_followup_flat"] is True
    assert {item["kind"] for item in value["history_comparison_findings"]} == {
        "matching_overlap"
    }
    assert {
        "exchange_history_finality_unproven",
        "native_pre_http_history_commit_proof_missing",
        "funding_accrual_provenance_missing",
        "historical_native_hwm_unknown",
    } <= set(value["blocking_reasons"])
    assert value["history_tail_closed"] is value["account_complete"] is False
    assert value["flat_start_permission"] is value["execution_authority"] is False
    assert value["snapshot"] is None
    assert value["admission"] == result.admission == "DENY"
    assert all(secret.encode() not in result.receipt_json for secret in SECRETS)
    assert SCOPE.account_id.encode() not in result.receipt_json
    assert (
        tail_v7.verify_v7_history_tail_receipt(result, **inputs(chains)).receipt_json
        == result.receipt_json
    )


@pytest.mark.asyncio
async def test_v7_late_history_row_requires_reconciliation(monkeypatch):
    chains = await four_v7_chains(monkeypatch, later=[row("fills_history")])
    value = json.loads(
        tail_v7.verify_v7_history_tail_diagnostic(**inputs(chains)).receipt_json
    )
    comparison_bytes = tail_v7.history.compare_history_query_chains(
        previous_chain=chains[0],
        previous_pins=observed._pins(observed.source_reference(chains[0]), SCOPE),
        current_chain=chains[2],
        current_pins=observed._pins(observed.source_reference(chains[2]), SCOPE),
    )
    comparison = json.loads(comparison_bytes)
    assert comparison["schema_version"] == tail_v7.HISTORY_COMPARISON_SCHEMA_VERSION
    assert comparison["dependent_current_claims_require_reconciliation"] is True
    assert value["history_comparison_sha256"] == journal.digest(comparison_bytes)
    assert "late_observation_in_prior_query" in {
        finding["kind"] for finding in value["history_comparison_findings"]
    }
    assert (
        "history_overlap_discovery_requires_reconciliation" in value["blocking_reasons"]
    )
    assert value["account_complete"] is value["execution_authority"] is False


@pytest.mark.asyncio
async def test_v7_legacy_comparison_schema_fails_closed(monkeypatch):
    chains = await four_v7_chains(monkeypatch)
    monkeypatch.setattr(
        tail_v7.history,
        "compare_history_query_chains",
        lambda **_: journal.canonical(
            {"schema_version": "ctcc.observed_history_query_comparison.v1"}
        ),
    )
    with pytest.raises(
        tail_v7.V7HistoryTailError,
        match="v7_history_tail_comparison_schema_invalid",
    ):
        tail_v7.verify_v7_history_tail_diagnostic(**inputs(chains))


@pytest.mark.asyncio
async def test_v7_rejects_wrong_plan_reused_capture_session_and_chronology(monkeypatch):
    chains = await four_v7_chains(monkeypatch)
    v6, _ = await recorded_current(monkeypatch, offset_seconds=4)
    with pytest.raises(tail_v7.V7HistoryTailError):
        tail_v7.verify_v7_history_tail_diagnostic(**inputs((*chains[:3], v6)))

    reused = inputs(chains)
    reused["followup_current_chain"] = chains[1]
    reused["followup_current_reference"] = observed.source_reference(chains[1])
    with pytest.raises(tail_v7.V7HistoryTailError, match="distinct_captures"):
        tail_v7.verify_v7_history_tail_diagnostic(**reused)

    changed = await four_v7_chains(
        monkeypatch, followup_changes={"session_binding_id": "synthetic-other-session"}
    )
    with pytest.raises(tail_v7.V7HistoryTailError):
        tail_v7.verify_v7_history_tail_diagnostic(**inputs(changed))

    early = await four_v7_chains(monkeypatch, followup_offset=2)
    with pytest.raises(tail_v7.V7HistoryTailError):
        tail_v7.verify_v7_history_tail_diagnostic(**inputs(early))

    with pytest.raises(tail_v7.V7HistoryTailError):
        tail_v7.verify_v7_history_tail_diagnostic(
            **inputs(chains, expected_policy_sha256="0" * 64)
        )


@pytest.mark.asyncio
async def test_v7_rejects_checkpoint_change_and_missing_original_terminal(monkeypatch):
    chains = await four_v7_chains(monkeypatch)
    state = ledger_evidence().state
    changed, _ = await recorded_v7(monkeypatch, offset_seconds=4, states=[state, state])
    with pytest.raises(tail_v7.V7HistoryTailError):
        tail_v7.verify_v7_history_tail_diagnostic(**inputs((*chains[:3], changed)))

    truncated = inputs(chains)
    truncated["followup_current_chain"] = chains[3][:-1]
    truncated["followup_current_reference"] = observed.source_reference(chains[3][:-1])
    with pytest.raises(tail_v7.V7HistoryTailError):
        tail_v7.verify_v7_history_tail_diagnostic(**truncated)


@pytest.mark.asyncio
async def test_v7_saved_receipt_cannot_gain_authority_or_change_sources(monkeypatch):
    chains = await four_v7_chains(monkeypatch)
    result = tail_v7.verify_v7_history_tail_diagnostic(**inputs(chains))
    changed_reference = inputs(
        chains, history_reference=observed.source_reference(chains[2])
    )
    with pytest.raises(tail_v7.V7HistoryTailError):
        tail_v7.verify_v7_history_tail_receipt(result, **changed_reference)

    previous_policy = json.loads(tail_v7.POLICY_BYTES)
    assert (
        previous_policy.pop("history_comparison_schema")
        == tail_v7.HISTORY_COMPARISON_SCHEMA_VERSION
    )
    old_policy_sha256 = journal.digest(journal.canonical(previous_policy))
    assert old_policy_sha256 != tail_v7.POLICY_SHA256
    old_receipt = json.loads(result.receipt_json)
    assert old_receipt["admission"] == "DENY"
    old_receipt["policy_sha256"] = old_policy_sha256
    with pytest.raises(tail_v7.V7HistoryTailError, match="receipt_invalid"):
        tail_v7.V7HistoryTailDiagnostic(journal.canonical(old_receipt))

    altered = json.loads(result.receipt_json)
    altered["account_complete"] = True
    with pytest.raises(tail_v7.V7HistoryTailError, match="receipt_invalid"):
        tail_v7.V7HistoryTailDiagnostic(journal.canonical(altered))
    altered["account_complete"] = False
    altered["blocking_reasons"].append("synthetic_extra_blocker")
    forged = tail_v7.V7HistoryTailDiagnostic(journal.canonical(altered))
    with pytest.raises(tail_v7.V7HistoryTailError, match="receipt_changed"):
        tail_v7.verify_v7_history_tail_receipt(forged, **inputs(chains))
