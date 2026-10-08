"""Synthetic B1 chains only; no private account, native owner or DB acceptance."""

import json
from datetime import timedelta

import pytest

from app.trade_qualification import account_bootstrap_runtime as bootstrap
from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_capture_journal as journal
from app.trade_qualification import account_history_tail_prerequisite as tail
from app.trade_qualification import account_observation_index as observed
from app.trade_qualification.account_runtime import ControlledDemoAccountSession
from app.trade_qualification.reservations import LedgerScope
from tests.unit.test_account_current_history_join import recorded_current
from tests.unit.test_account_current_source_verifier import SCOPE, flat_pages
from tests.unit.test_account_ingestion_journal_contracts import setup
from tests.unit.test_qualification_account_capture import NOW, row
from tests.unit.test_qualification_account_collector import SECRETS, credentials
from tests.unit.test_qualification_account_v4 import plan as historical_plan
from tests.unit.test_qualification_account_v4 import script as account_script


async def recorded_history(monkeypatch, *, offset_seconds, fills=(), plan_changes=None):
    session, harness, _, args, events = setup(monkeypatch)
    selected = historical_plan(**(plan_changes or {}))
    session = ControlledDemoAccountSession(
        credentials=credentials(session_binding_id=selected.session_binding_id),
        plan=selected,
        expected_plan_sha256=capture.plan_sha256(selected),
    )
    pages = flat_pages()
    pages["fills_history"] = [list(fills), []] if fills else [[]]
    harness.script = account_script(source=pages)
    harness.clock.overrides.update(
        {
            index: NOW + timedelta(seconds=offset_seconds, milliseconds=index + 1)
            for index in range(1200)
        }
    )
    await bootstrap.collect_bootstrap_recorded(session, **args)
    harness.assert_closed()
    return tuple(events)


async def three_chains(
    monkeypatch,
    *,
    first=(),
    later=(),
    later_plan=None,
    current_offset=1,
    later_offset=3,
):
    first_chain = await recorded_history(monkeypatch, offset_seconds=0, fills=first)
    current_chain, _ = await recorded_current(
        monkeypatch, offset_seconds=current_offset
    )
    later_chain = await recorded_history(
        monkeypatch,
        offset_seconds=later_offset,
        fills=later,
        plan_changes={
            "created_at": NOW + timedelta(seconds=2),
            "history_end": NOW + timedelta(seconds=2),
            **(later_plan or {}),
        },
    )
    return first_chain, current_chain, later_chain


def replay_inputs(chains, **changes):
    old, current, later = chains
    return {
        "history_chain": old,
        "history_reference": observed.source_reference(old),
        "current_chain": current,
        "current_reference": observed.source_reference(current),
        "continuation_chain": later,
        "continuation_reference": observed.source_reference(later),
        "scope": SCOPE,
        "validated_at": NOW + timedelta(seconds=5),
        **changes,
    }


def replay(chains, **changes):
    return tail.verify_history_tail_prerequisites(**replay_inputs(chains, **changes))


@pytest.mark.asyncio
async def test_matching_overlapping_original_chains_still_deny_finality(monkeypatch):
    sample = row("fills_history")
    chains = await three_chains(monkeypatch, first=[sample], later=[sample])
    source_events = tuple(tuple(item.event for item in chain) for chain in chains)
    result = replay(chains)
    value = json.loads(result.receipt_json)
    assert value["schema_version"] == "ctcc.demo_history_tail_prerequisite.v1"
    assert len({item["capture_id"] for item in value["source_references"]}) == 3
    assert (
        value["source_references"][0]["head_sha256"]
        == observed.source_reference(chains[0]).head_sha256
    )
    assert {item["kind"] for item in value["history_comparison_findings"]} == {
        "matching_overlap"
    }
    assert value["history_tail_closed"] is False
    assert value["snapshot"] is None
    assert value["account_complete"] is result.account_complete is False
    assert value["execution_authority"] is result.execution_authority is False
    assert value["admission"] == result.admission == "DENY"
    assert "exchange_history_finality_unproven" in value["blocking_reasons"]
    assert "post_continuation_current_capture_required" in value["blocking_reasons"]
    assert (
        tuple(tuple(item.event for item in chain) for chain in chains) == source_events
    )
    assert all(secret.encode() not in result.receipt_json for secret in SECRETS)
    assert SCOPE.account_id.encode() not in result.receipt_json


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change,expected",
    [
        ("late", "late_observation_in_prior_query"),
        ("conflict", "conflicting_overlap"),
        ("missing", "previous_row_missing_in_current_query"),
    ],
)
async def test_late_conflicting_or_missing_row_preserves_denied_evidence(
    monkeypatch, change, expected
):
    sample = row("fills_history")
    first = [] if change == "late" else [sample]
    later = (
        []
        if change == "missing"
        else [{**sample, "fee": "-0.002"}]
        if change == "conflict"
        else [sample]
    )
    value = json.loads(
        replay(await three_chains(monkeypatch, first=first, later=later)).receipt_json
    )
    assert expected in {item["kind"] for item in value["history_comparison_findings"]}
    assert (
        "history_overlap_discovery_requires_reconciliation" in value["blocking_reasons"]
    )
    assert value["history_tail_closed"] is value["account_complete"] is False
    assert value["admission"] == "DENY"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change,code",
    [
        ({"history_start": NOW}, "history_tail_requested_overlap_missing"),
        (
            {"registration_evidence_sha256": "a" * 64},
            "history_tail_identity_or_region_changed",
        ),
        (
            {"session_binding_id": "synthetic-other-session"},
            "history_tail_identity_or_region_changed",
        ),
        (
            {"registration_region": "us_au", "origin": "https://us.okx.com"},
            "history_tail_source_invalid",
        ),
    ],
)
async def test_changed_overlap_registration_or_session_rejected(
    monkeypatch, change, code
):
    chains = await three_chains(monkeypatch, later_plan=change)
    with pytest.raises(tail.HistoryTailPrerequisiteError, match=code):
        replay(chains)


@pytest.mark.asyncio
async def test_noncausal_continuation_and_recomputed_reference_rejected(monkeypatch):
    chains = await three_chains(monkeypatch, current_offset=3, later_offset=2)
    with pytest.raises(
        tail.HistoryTailPrerequisiteError,
        match="history_tail_capture_chronology_invalid",
    ):
        replay(chains)
    chains = await three_chains(monkeypatch)
    reference = observed.source_reference(chains[-1])
    forged = observed.CaptureReference(
        reference.capture_id,
        "0" * 64,
        reference.plan_sha256,
        reference.packet_sha256,
        reference.session_binding_sha256,
    )
    with pytest.raises(
        tail.HistoryTailPrerequisiteError,
        match="history_tail_source_reference_mismatch",
    ):
        replay(chains, continuation_reference=forged)
    with pytest.raises(tail.HistoryTailPrerequisiteError):
        replay(chains, scope=LedgerScope(account_id="999", settlement_currency="USDT"))


def test_direct_receipt_cannot_claim_account_completion():
    forged = journal.canonical(
        {
            "schema_version": "ctcc.demo_history_tail_prerequisite.v1",
            "policy_sha256": tail.POLICY_SHA256,
            "history_tail_closed": True,
            "snapshot": {},
            "account_complete": True,
            "source_authenticity_verified": True,
            "execution_authority": True,
            "admission": "PASS",
        }
    )
    with pytest.raises(tail.HistoryTailPrerequisiteError):
        tail.HistoryTailPrerequisite(forged)


@pytest.mark.asyncio
async def test_saved_diagnostic_requires_original_chain_replay(monkeypatch):
    chains = await three_chains(monkeypatch)
    original = replay(chains)
    assert (
        tail.verify_history_tail_prerequisite_receipt(
            original, **replay_inputs(chains)
        ).receipt_json
        == original.receipt_json
    )
    modified = json.loads(original.receipt_json)
    modified["blocking_reasons"] = ["caller_claimed_no_blockers"]
    forged = tail.HistoryTailPrerequisite(journal.canonical(modified))
    with pytest.raises(
        tail.HistoryTailPrerequisiteError, match="history_tail_receipt_changed"
    ):
        tail.verify_history_tail_prerequisite_receipt(forged, **replay_inputs(chains))
