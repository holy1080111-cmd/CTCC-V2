"""Real synthetic raw-account replay with the DB0056 observation receipt boundary."""

from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

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
    settings = SimpleNamespace(okx_demo_native_evidence_root="unused")
    capture_attempt = AsyncMock()
    monkeypatch.setattr(source, "_settings", lambda instrument: settings)
    monkeypatch.setattr(
        source, "require_private_evidence_base_v2", Mock(side_effect=ValueError)
    )
    monkeypatch.setattr(source, "_capture", capture_attempt)
    with pytest.raises(
        source.NativeCurrentSource0056Error, match="observation_unavailable"
    ):
        await source.observe_configured_current_account_0056_v1("BTC-USDT-SWAP")
    capture_attempt.assert_not_awaited()


@pytest.mark.asyncio
async def test_0056_checks_exact_selected_factories_before_any_signed_get(monkeypatch):
    selectors = source.selectors
    factories = SimpleNamespace(
        frame_reader_engine=SimpleNamespace(dispose=AsyncMock()),
        custody_engine=SimpleNamespace(dispose=AsyncMock()),
    )
    client = SimpleNamespace(account_config_selector_v2=AsyncMock())
    monkeypatch.setattr(selectors, "_configured_inputs", Mock(return_value=object()))
    monkeypatch.setattr(selectors, "_server_settings_pin", Mock(return_value="pin"))
    monkeypatch.setattr(selectors, "_route_artifact", Mock(return_value=b"route"))
    monkeypatch.setattr(
        selectors,
        "select_configured_native_demo_factories_v2",
        Mock(return_value=factories),
    )
    witness = AsyncMock(side_effect=ValueError("synthetic private deployment detail"))
    monkeypatch.setattr(selectors, "_source_roles_before_get_0056", witness)
    with pytest.raises(
        selectors.NativeDemoCallerReadinessErrorV2,
        match="^native_demo_identity_selector_failed$",
    ):
        await selectors.select_configured_demo_source_identity_0056_v1(
            private_client=client,
            read_session=object(),
            instrument_ids=("BTC-USDT-SWAP",),
        )
    witness.assert_awaited_once_with(factories)
    client.account_config_selector_v2.assert_not_awaited()
    factories.frame_reader_engine.dispose.assert_awaited_once()
    factories.custody_engine.dispose.assert_awaited_once()


@pytest.mark.asyncio
async def test_0056_configuration_change_during_role_check_refuses_get(monkeypatch):
    selectors = source.selectors
    factories = SimpleNamespace(
        frame_reader_engine=SimpleNamespace(dispose=AsyncMock()),
        custody_engine=SimpleNamespace(dispose=AsyncMock()),
    )
    client = SimpleNamespace(account_config_selector_v2=AsyncMock())
    monkeypatch.setattr(selectors, "_configured_inputs", Mock(return_value=object()))
    monkeypatch.setattr(
        selectors, "_server_settings_pin", Mock(side_effect=("before", "changed"))
    )
    monkeypatch.setattr(selectors, "_route_artifact", Mock(return_value=b"route"))
    monkeypatch.setattr(
        selectors,
        "select_configured_native_demo_factories_v2",
        Mock(return_value=factories),
    )
    monkeypatch.setattr(
        selectors, "_source_roles_before_get_0056", AsyncMock(return_value=object())
    )
    with pytest.raises(
        selectors.NativeDemoCallerReadinessErrorV2, match="settings_changed_before_get"
    ):
        await selectors.select_configured_demo_source_identity_0056_v1(
            private_client=client,
            read_session=object(),
            instrument_ids=("BTC-USDT-SWAP",),
        )
    client.account_config_selector_v2.assert_not_awaited()
    factories.frame_reader_engine.dispose.assert_awaited_once()
    factories.custody_engine.dispose.assert_awaited_once()
