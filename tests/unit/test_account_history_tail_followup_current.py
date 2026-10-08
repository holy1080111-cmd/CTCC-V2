"""Synthetic four-chain replay only; no private account or execution authority."""

import json
from datetime import timedelta

import pytest

from app.trade_qualification import account_capture_journal as journal
from app.trade_qualification import account_history_tail_followup_current as followup
from app.trade_qualification import account_observation_index as observed
from tests.unit.test_account_current_history_join import recorded_current
from tests.unit.test_account_current_source_verifier import SCOPE
from tests.unit.test_account_history_tail_prerequisite import three_chains
from tests.unit.test_qualification_account_capture import NOW, row
from tests.unit.test_qualification_account_collector import SECRETS
from tests.unit.test_qualification_account_materializer import ledger_evidence


async def four_chains(
    monkeypatch, *, first=(), later=(), latest_offset=4, changes=None
):
    old, current, continuation = await three_chains(
        monkeypatch, first=first, later=later
    )
    latest, _ = await recorded_current(
        monkeypatch,
        offset_seconds=latest_offset,
        plan_changes=changes,
    )
    return old, current, continuation, latest


def inputs(chains, **changes):
    old, current, continuation, latest = chains
    return {
        "history_chain": old,
        "history_reference": observed.source_reference(old),
        "current_chain": current,
        "current_reference": observed.source_reference(current),
        "continuation_chain": continuation,
        "continuation_reference": observed.source_reference(continuation),
        "followup_current_chain": latest,
        "followup_current_reference": observed.source_reference(latest),
        "scope": SCOPE,
        "validated_at": NOW + timedelta(seconds=6),
        **changes,
    }


@pytest.mark.asyncio
async def test_four_original_chains_observe_followup_without_history_finality(
    monkeypatch,
):
    sample = row("fills_history")
    chains = await four_chains(monkeypatch, first=[sample], later=[sample])
    result = followup.verify_followup_current_diagnostic(**inputs(chains))
    value = json.loads(result.receipt_json)
    assert value["schema_version"] == "ctcc.demo_history_tail_followup_current.v1"
    assert len({item["capture_id"] for item in value["source_references"]}) == 4
    assert value["bounded_followup_current_query_observed"] is True
    assert (
        "post_continuation_current_capture_required" in value["prior_blocking_reasons"]
    )
    assert "post_continuation_current_capture_required" not in value["blocking_reasons"]
    assert "exchange_history_finality_unproven" in value["blocking_reasons"]
    assert (
        "post_continuation_current_native_proof_required" in value["blocking_reasons"]
    )
    assert value["history_comparison_findings"][0]["kind"] == "matching_overlap"
    assert value["history_tail_closed"] is value["account_complete"] is False
    assert value["snapshot"] is None
    assert value["execution_authority"] is result.execution_authority is False
    assert value["admission"] == result.admission == "DENY"
    assert all(secret.encode() not in result.receipt_json for secret in SECRETS)
    assert SCOPE.account_id.encode() not in result.receipt_json
    assert (
        followup.verify_followup_current_receipt(result, **inputs(chains)).receipt_json
        == result.receipt_json
    )


@pytest.mark.asyncio
async def test_late_row_finding_survives_followup_current(monkeypatch):
    chains = await four_chains(monkeypatch, later=[row("fills_history")])
    value = json.loads(
        followup.verify_followup_current_diagnostic(**inputs(chains)).receipt_json
    )
    assert "late_observation_in_prior_query" in {
        item["kind"] for item in value["history_comparison_findings"]
    }
    assert (
        "history_overlap_discovery_requires_reconciliation" in value["blocking_reasons"]
    )
    assert value["account_complete"] is False and value["admission"] == "DENY"


@pytest.mark.asyncio
async def test_fourth_capture_must_be_distinct_later_and_same_session(monkeypatch):
    chains = await four_chains(monkeypatch)
    reused = inputs(chains)
    reused["followup_current_chain"] = chains[1]
    reused["followup_current_reference"] = observed.source_reference(chains[1])
    with pytest.raises(
        followup.FollowupCurrentError,
        match="followup_current_distinct_captures_required",
    ):
        followup.verify_followup_current_diagnostic(**reused)

    early = await four_chains(monkeypatch, latest_offset=2)
    with pytest.raises(followup.FollowupCurrentError):
        followup.verify_followup_current_diagnostic(**inputs(early))

    changed = await four_chains(
        monkeypatch, changes={"session_binding_id": "synthetic-other-session"}
    )
    with pytest.raises(followup.FollowupCurrentError):
        followup.verify_followup_current_diagnostic(**inputs(changed))

    state = ledger_evidence().state
    changed_checkpoint, _ = await recorded_current(
        monkeypatch, offset_seconds=4, states=[state, state]
    )
    with pytest.raises(followup.FollowupCurrentError):
        followup.verify_followup_current_diagnostic(
            **inputs((*chains[:3], changed_checkpoint))
        )

    with pytest.raises(
        followup.FollowupCurrentError, match="followup_current_lease_expired"
    ):
        followup.verify_followup_current_diagnostic(
            **inputs(chains, validated_at=NOW + timedelta(seconds=40))
        )


@pytest.mark.asyncio
async def test_saved_four_chain_diagnostic_cannot_upgrade_or_change_blockers(
    monkeypatch,
):
    chains = await four_chains(monkeypatch)
    result = followup.verify_followup_current_diagnostic(**inputs(chains))
    changed = json.loads(result.receipt_json)
    changed["blocking_reasons"] = sorted(
        [*changed["blocking_reasons"], "caller_claimed_no_blockers"]
    )
    forged = followup.FollowupCurrentDiagnostic(journal.canonical(changed))
    with pytest.raises(
        followup.FollowupCurrentError, match="followup_current_receipt_changed"
    ):
        followup.verify_followup_current_receipt(forged, **inputs(chains))
    changed["account_complete"] = True
    with pytest.raises(
        followup.FollowupCurrentError, match="followup_current_receipt_invalid"
    ):
        followup.FollowupCurrentDiagnostic(journal.canonical(changed))
    changed["account_complete"] = False
    changed["caller_added_authority"] = True
    with pytest.raises(
        followup.FollowupCurrentError, match="followup_current_receipt_invalid"
    ):
        followup.FollowupCurrentDiagnostic(journal.canonical(changed))
