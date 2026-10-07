"""Synthetic original journals only; no account/DB/native-clock acceptance."""

import json
from datetime import datetime, timedelta

import pytest

from app.trade_qualification import account_bootstrap_runtime as bootstrap
from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_capture_journal as journal
from app.trade_qualification import account_collector as collector
from app.trade_qualification import account_current_history_join as joined
from app.trade_qualification import account_current_source_verifier as current
from app.trade_qualification import account_observation_index as observed
from app.trade_qualification.account_runtime import ControlledDemoAccountSession
from tests.unit.test_account_current_source_verifier import SCOPE, flat_pages, recorded
from tests.unit.test_account_ingestion_journal_contracts import setup
from tests.unit.test_qualification_account_capture import NOW, row
from tests.unit.test_qualification_account_collector import credentials
from tests.unit.test_qualification_account_materializer import ledger_evidence
from tests.unit.test_qualification_account_v4 import (
    plan as historical_plan,
)
from tests.unit.test_qualification_account_v4 import (
    script as account_script,
)


def current_plan(**changes):
    values = capture._plain(historical_plan())
    values.update(
        contract_version="ctcc.demo_current_account_plan.v6",
        capture_scope="all_current_standard_products_v6",
        **changes,
    )
    return capture.CurrentDemoAccountCapturePlanV6(**values)


async def recorded_current(
    monkeypatch,
    *,
    offset_seconds=1,
    pages=None,
    plan_changes=None,
    states=None,
    delayed_first_close_seconds=None,
):
    session, harness, _, args, events = setup(monkeypatch, states=states)
    selected = current_plan(**(plan_changes or {}))
    session = ControlledDemoAccountSession(
        credentials=credentials(session_binding_id=selected.session_binding_id),
        plan=selected,
        expected_plan_sha256=capture.plan_sha256(selected),
    )
    harness.script = account_script(
        source=flat_pages() if pages is None else pages,
        streams=capture.V6_CURRENT_STREAMS,
    )
    harness.clock.overrides.update(
        {
            index: NOW + timedelta(seconds=offset_seconds, milliseconds=index + 1)
            for index in range(300)
        }
    )
    if delayed_first_close_seconds is not None:
        original_close = collector._close

        async def delayed_first_close(response, *, pending_cancel=False):
            await original_close(response, pending_cancel=pending_cancel)
            if len(harness.streams) == 1:
                for index in range(harness.clock.calls, 300):
                    harness.clock.overrides[index] += timedelta(
                        seconds=delayed_first_close_seconds
                    )

        monkeypatch.setattr(collector, "_close", delayed_first_close)
    await bootstrap.collect_bootstrap_recorded(session, **args)
    harness.assert_closed()
    return tuple(events), harness


def verify_current(chain, *, at=NOW + timedelta(seconds=2)):
    return current.verify_current_account_sources(
        chain,
        reference=observed.source_reference(chain),
        scope=SCOPE,
        validated_at=at,
        expected_policy_sha256=current.V6_POLICY_SHA256,
    )


def join(old, new, *, at=NOW + timedelta(seconds=2)):
    return joined.join_recorded_account_sources(
        history_chain=old,
        history_reference=observed.source_reference(old),
        current_chain=new,
        current_reference=observed.source_reference(new),
        scope=SCOPE,
        validated_at=at,
    )


@pytest.mark.asyncio
async def test_v6_collects_only_complete_current_pages_and_keeps_history_unknown(
    monkeypatch,
):
    chain, harness = await recorded_current(monkeypatch)
    packet = capture.verify_demo_account_packet(
        next(item.event.packet_payload for item in chain if item.event.packet_payload),
        expected_sha256=observed.source_reference(chain).packet_sha256,
        expected_plan_sha256=observed.source_reference(chain).plan_sha256,
    )
    assert type(packet.plan) is capture.CurrentDemoAccountCapturePlanV6
    assert packet.schema_version == "ctcc.demo_current_account_capture.v6"
    assert tuple(
        dict.fromkeys(item.request.stream for item in packet.observations)
    ) == (capture.V6_CURRENT_STREAMS)
    assert all("history" not in item.request.stream for item in packet.observations)
    assert len(harness.requests) == len(packet.observations)
    assert "separate_history_source_join_required" in packet.incomplete_reasons
    assert packet.account_complete is packet.execution_authority is False
    proof = json.loads(verify_current(chain).receipt_json)
    assert proof["schema_version"] == "ctcc.current_account_source_observation.v4"
    assert proof["observation_interval"]["freshness_basis"] == (
        "replay_verified_B1_body_complete_EOF"
    )
    assert proof["observed_flat"] is True
    assert proof["history_query_verifier_sha256"] is None
    assert proof["history_join_state"] == "separate_original_history_required"
    assert proof["account_complete"] is proof["execution_authority"] is False


@pytest.mark.asyncio
async def test_v6_freshness_uses_verified_eof_when_first_response_close_is_delayed(
    monkeypatch,
):
    chain, _ = await recorded_current(monkeypatch, delayed_first_close_seconds=1)
    eof_record = next(
        journal.checked_event(item.event)
        for item in chain
        if journal.checked_event(item.event)["kind"] == "body_complete"
    )
    eof = capture._utc(datetime.fromisoformat(eof_record["observed_at"]))
    cutoff = eof + timedelta(seconds=30, milliseconds=500)
    pins = {
        "reference": observed.source_reference(chain),
        "scope": SCOPE,
        "validated_at": cutoff,
    }
    legacy = json.loads(
        current.verify_current_account_sources(
            chain,
            expected_policy_sha256=current.V6_LEGACY_POLICY_SHA256,
            **pins,
        ).receipt_json
    )
    fresh_contract = json.loads(
        current.verify_current_account_sources(
            chain, expected_policy_sha256=current.V6_POLICY_SHA256, **pins
        ).receipt_json
    )
    first_closed = capture._utc(
        datetime.fromisoformat(fresh_contract["current_pages"][0]["body_completed_at"])
    )
    assert eof + timedelta(seconds=30) < cutoff < first_closed + timedelta(seconds=30)
    assert legacy["schema_version"] == "ctcc.current_account_source_observation.v3"
    assert current.V6_LEGACY_POLICY_SHA256 == (
        "375b6c7429273718d81edb995e9433669f3fc941cb07f0042cad21fbbfede642"
    )
    assert legacy["observed_flat"] is True
    assert "body_exhausted_at" not in legacy["current_pages"][0]
    assert (
        legacy["current_pages"][0]["body_completed_at"]
        == (fresh_contract["current_pages"][0]["body_completed_at"])
    )
    assert (
        fresh_contract["schema_version"] == "ctcc.current_account_source_observation.v4"
    )
    assert fresh_contract["current_pages"][0]["body_exhausted_at"] == eof.isoformat()
    assert fresh_contract["observed_flat"] is False
    assert "measured_current_receipt_stale" in fresh_contract["blocking_reasons"]
    assert fresh_contract["admission"] == "DENY"
    assert fresh_contract["account_complete"] is False
    assert fresh_contract["execution_authority"] is False


@pytest.mark.asyncio
async def test_v6_original_current_and_v5_history_join_exact_scope_without_authority(
    monkeypatch,
):
    historical = await recorded(monkeypatch)
    fresh, _ = await recorded_current(monkeypatch)
    result = join(historical, fresh)
    value = json.loads(result.receipt_json)
    assert value["recorded_local_checkpoint_equal"] is True
    assert value["account_revision_verified"] is False
    assert value["current_observed_flat"] is True
    assert "history_tail_not_atomically_closed" in value["blocking_reasons"]
    assert value["snapshot"] is result.snapshot is None
    assert value["account_complete"] is result.account_complete is False
    assert value["execution_authority"] is result.execution_authority is False


@pytest.mark.asyncio
async def test_v6_rejects_wrong_policy_scope_or_missing_terminal(monkeypatch):
    fresh, _ = await recorded_current(monkeypatch)
    with pytest.raises(current.CurrentAccountSourceError):
        current.verify_current_account_sources(
            fresh,
            reference=observed.source_reference(fresh),
            scope=SCOPE,
            validated_at=NOW + timedelta(seconds=2),
        )
    with pytest.raises(current.CurrentAccountSourceError):
        verify_current(fresh[:-1])
    with pytest.raises(current.CurrentAccountSourceError):
        current.verify_current_account_sources(
            fresh,
            reference=observed.source_reference(fresh),
            scope=type(SCOPE)(account_id="999", settlement_currency="USDT"),
            validated_at=NOW + timedelta(seconds=2),
            expected_policy_sha256=current.V6_POLICY_SHA256,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "policy",
    [current.V6_LEGACY_POLICY_SHA256, current.V6_POLICY_SHA256],
)
async def test_v6_rejects_journal_scope_that_disagrees_with_packet_plan(
    monkeypatch, policy
):
    chain, _ = await recorded_current(monkeypatch)
    changed = []
    previous = None
    for item in chain:
        event = journal.checked_event(item.event)
        event["previous_sha256"] = previous
        if event["kind"] == "capture_start":
            event["data"]["account_id"] = "999"
        raw = journal.canonical(event)
        changed_event = journal._JournalEvent(
            journal._ISSUER, raw, item.event.raw_body, item.event.packet_payload
        )
        changed.append(
            journal.JournalReadback(
                changed_event, item.db_recorded_at, item.readback_at
            )
        )
        previous = journal.digest(raw)
    changed = tuple(changed)
    with pytest.raises(
        current.CurrentAccountSourceError,
        match="current_source_capture_binding_mismatch",
    ):
        current.verify_current_account_sources(
            changed,
            reference=observed.source_reference(changed),
            scope=type(SCOPE)(account_id="999", settlement_currency="USDT"),
            validated_at=NOW + timedelta(seconds=2),
            expected_policy_sha256=policy,
        )


@pytest.mark.asyncio
async def test_join_rejects_history_as_current_and_stale_tail_stays_blocked(
    monkeypatch,
):
    historical = await recorded(monkeypatch)
    fresh, _ = await recorded_current(monkeypatch, offset_seconds=3)
    with pytest.raises(joined.AccountSourceJoinError):
        join(historical, historical)
    value = json.loads(
        join(historical, fresh, at=NOW + timedelta(seconds=4)).receipt_json
    )
    assert value["snapshot"] is None
    assert "history_tail_not_atomically_closed" in value["blocking_reasons"]


@pytest.mark.asyncio
async def test_join_denies_changed_exact_session_or_main_uid(monkeypatch):
    historical = await recorded(monkeypatch)
    other_session, _ = await recorded_current(
        monkeypatch, plan_changes={"session_binding_id": "another-private-session"}
    )
    with pytest.raises(
        joined.AccountSourceJoinError, match="account_join_identity_or_session_changed"
    ):
        join(historical, other_session)
    pages = flat_pages()
    for name in ("config_before", "config_after"):
        pages[name] = [[row(name, mainUid="700003")]]
    other_parent, _ = await recorded_current(
        monkeypatch, pages=pages, plan_changes={"expected_main_uid": "700003"}
    )
    with pytest.raises(
        joined.AccountSourceJoinError, match="account_join_identity_or_session_changed"
    ):
        join(historical, other_parent)


@pytest.mark.asyncio
async def test_join_denies_changed_recorded_local_checkpoint(monkeypatch):
    historical = await recorded(monkeypatch)
    state = ledger_evidence().state
    changed, _ = await recorded_current(monkeypatch, states=[state, state])
    with pytest.raises(
        joined.AccountSourceJoinError,
        match="account_join_recorded_local_revision_changed",
    ):
        join(historical, changed)
