"""Versioned pure V5-history + V7-current join; synthetic and non-authoritative."""

import json
from datetime import timedelta

import pytest

from app.trade_qualification import account_current_history_join as joined
from app.trade_qualification import account_current_source_verifier as current
from app.trade_qualification import account_observation_index as observed
from tests.unit.test_account_current_source_v7 import recorded_v7
from tests.unit.test_account_current_source_verifier import SCOPE, recorded
from tests.unit.test_qualification_account_capture import NOW
from tests.unit.test_qualification_account_materializer import ledger_evidence


def join_v7(old, new, *, at=NOW + timedelta(seconds=4), **changes):
    return joined.join_recorded_account_sources(
        history_chain=old,
        history_reference=observed.source_reference(old),
        current_chain=new,
        current_reference=observed.source_reference(new),
        scope=SCOPE,
        validated_at=at,
        expected_policy_sha256=joined.V7_POLICY_SHA256,
        **changes,
    )


def test_v7_join_policy_is_new_and_v6_history_join_identity_is_unchanged():
    assert joined.POLICY_SHA256 == (
        "efc0b970ecf8cd71d99b21d47422f698baeba4e6980f616d82cefb864a8f5d7e"
    )
    policy = json.loads(joined.V7_POLICY_BYTES)
    assert policy["version"] == "ctcc.recorded_account_current_history_join.v2"
    assert policy["prior_join_policy_sha256"] == joined.POLICY_SHA256
    assert policy["current_policy_sha256"] == current.V7_POLICY_SHA256
    assert policy["current_capture_plan"] == "ctcc.demo_current_account_plan.v7"
    assert joined.V7_POLICY_SHA256 != joined.POLICY_SHA256


@pytest.mark.asyncio
async def test_v7_current_and_v5_history_join_keeps_all_remaining_unknowns(
    monkeypatch,
):
    historical = await recorded(monkeypatch)
    fresh, _ = await recorded_v7(monkeypatch)
    value = json.loads(join_v7(historical, fresh).receipt_json)
    assert value["schema_version"] == "ctcc.recorded_account_current_history_join.v2"
    assert value["policy_sha256"] == joined.V7_POLICY_SHA256
    assert value["current_source_policy_sha256"] == current.V7_POLICY_SHA256
    assert value["current_source_receipt_sha256"]
    assert value["recorded_local_checkpoint_equal"] is True
    assert value["current_observed_flat"] is True
    assert value["account_revision_verified"] is False
    assert {
        "history_tail_not_atomically_closed",
        "history_late_arrival_finality_unproven",
        "funding_accrual_provenance_missing",
        "complete_net_loss_window_unproven",
        "loss_streak_seed_unknown",
        "historical_native_hwm_unknown",
        "current_local_revision_readback_required",
        "current_native_owner_unverified",
    } <= set(value["blocking_reasons"])
    assert value["snapshot"] is None
    assert value["account_complete"] is False
    assert value["account_revision_published"] is False
    assert value["flat_start_permission"] is False
    assert value["execution_authority"] is False
    assert value["admission"] == "DENY"


@pytest.mark.asyncio
async def test_v7_join_denies_relabelled_v6_policy_or_changed_session(monkeypatch):
    historical = await recorded(monkeypatch)
    fresh, _ = await recorded_v7(monkeypatch)
    with pytest.raises(joined.AccountSourceJoinError):
        joined.join_recorded_account_sources(
            history_chain=historical,
            history_reference=observed.source_reference(historical),
            current_chain=fresh,
            current_reference=observed.source_reference(fresh),
            scope=SCOPE,
            validated_at=NOW + timedelta(seconds=4),
        )
    other_session, _ = await recorded_v7(
        monkeypatch, plan_changes={"session_binding_id": "another-private-session"}
    )
    with pytest.raises(
        joined.AccountSourceJoinError, match="account_join_identity_or_session_changed"
    ):
        join_v7(historical, other_session)


@pytest.mark.asyncio
async def test_v7_join_denies_changed_recorded_local_checkpoint(monkeypatch):
    historical = await recorded(monkeypatch)
    state = ledger_evidence().state
    changed, _ = await recorded_v7(monkeypatch, states=[state, state])
    with pytest.raises(
        joined.AccountSourceJoinError,
        match="account_join_recorded_local_revision_changed",
    ):
        join_v7(historical, changed)


@pytest.mark.asyncio
async def test_v7_join_denies_current_capture_before_history_completed(monkeypatch):
    historical = await recorded(monkeypatch)
    early, _ = await recorded_v7(monkeypatch, offset_seconds=0)
    with pytest.raises(
        joined.AccountSourceJoinError, match="account_join_chronology_invalid"
    ):
        join_v7(historical, early)
