"""Real synthetic raw-account replay with the DB0056 observation receipt boundary."""

from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace

import pytest

from app.database.repositories.demo_control import ControlObservation
from app.demo_automation import native_current_source_0056_v1 as source
from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_current_source_v4 as current
from app.trade_qualification.demo_control import ControlScope, stopped_genesis
from tests.unit.test_account_current_source_v4 import setup


@pytest.mark.asyncio
async def test_observation_binds_the_actual_frozen_packet_and_disarmed_control(
    monkeypatch, tmp_path
):
    with setup(monkeypatch, tmp_path) as original:
        owned = await current.acquire_initial_demo_account_source_v4(**original.args)
        operands = current.consume_owned_current_demo_account_v4(owned)
        plan = operands.packet.plan
        plan_sha = capture.plan_sha256(plan)
        assert operands.packet.packet_sha256 != operands.reference.packet_sha256

        at = operands.packet.completed_at
        control_state = stopped_genesis(ControlScope("demo", plan.expected_uid), at)
        prior = ControlObservation(control_state, "a" * 64, at)
        after_control = ControlObservation(
            control_state, "a" * 64, at + timedelta(seconds=1)
        )
        proof = SimpleNamespace(
            native_definitions_sha256="b" * 64, actor_catalog_sha256="c" * 64
        )
        before = source.NativeLockedDemoControl0056V1(prior, proof, "d" * 64, "e" * 64)
        after = replace(before, control=after_control)
        source._readback_control(before, after)
        obs = source._source_document(operands, plan, plan_sha, before, after)
        assert obs["current_page_chains_complete"] is True
        assert obs["packet_sha256"] == operands.reference.packet_sha256
        assert obs["admission"] == "DENY"
        assert obs["account_complete"] is obs["execution_authority"] is False
        assert "account_id" not in obs and "uid" not in obs
        assert plan.expected_uid not in str(obs.keys())
        with pytest.raises(source.NativeCurrentSource0056Error, match="packet_changed"):
            bad = replace(
                operands, reference=replace(operands.reference, packet_sha256="1" * 64)
            )
            source._source_document(bad, plan, plan_sha, before, after)
        with pytest.raises(
            source.NativeCurrentSource0056Error, match="control_changed"
        ):
            source._readback_control(
                before,
                replace(after, control=replace(after_control, event_sha256="2" * 64)),
            )


@pytest.mark.asyncio
async def test_preflight_refusal_cannot_run_account_requests(monkeypatch):
    async def refuse():
        raise ValueError("synthetic private URL")

    called = []
    monkeypatch.setattr(source, "_settings", lambda instrument: object())
    monkeypatch.setattr(source, "_observe_roles", refuse)
    monkeypatch.setattr(source, "_attempt", lambda settings: called.append(settings))
    with pytest.raises(
        source.NativeCurrentSource0056Error, match="observation_unavailable"
    ):
        await source.observe_configured_current_account_0056_v1("BTC-USDT-SWAP")
    assert called == []
