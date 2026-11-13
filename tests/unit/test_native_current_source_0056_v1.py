"""Real synthetic raw-account replay with the DB0056 observation receipt boundary."""

import json
from contextlib import asynccontextmanager, nullcontext
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.database.repositories.demo_control import ControlObservation
from app.demo_automation import native_current_source_0056_v1 as source
from app.domain.source_primitives import canonical, sha
from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_current_source_v4 as current
from app.trade_qualification.demo_control import ControlScope, stopped_genesis
from scripts import observe_demo_current_source_0056 as cli
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


@pytest.mark.asyncio
async def test_qualification_preflight_rejects_ledger_or_time_drift(
    monkeypatch, tmp_path
):
    with setup(monkeypatch, tmp_path) as original:
        owned = await current.acquire_initial_demo_account_source_v4(**original.args)
        operands = current.consume_owned_current_demo_account_v4(owned)
        packet = operands.packet
        plan = packet.plan
        plan_sha = capture.plan_sha256(plan)
        first_request = min(x.request_started_at for x in packet.observations)
        before = source.ledger56.NativeLedgerPreflight0056V1(
            control_revision=1,
            control_event_sha256="a" * 64,
            emergency_stop=True,
            stored_arm_present=False,
            account_revision=0,
            ledger_revision=0,
            scope_state="BOOTSTRAP",
            unresolved_reservation_count=0,
            unresolved_consumed_intent_count=0,
            server_incarnation_sha256="b" * 64,
            role_route_sha256="c" * 64,
            native_definitions_sha256="d" * 64,
            uid_ledger_checkpoint_sha256="e" * 64,
            observed_at=first_request - timedelta(seconds=1),
        )
        after = replace(before, observed_at=packet.completed_at + timedelta(seconds=1))
        result = source._qualification_source_document(
            operands, plan, plan_sha, before, after
        )
        assert result["local_ledger_sha256"] == sha(canonical(result["local_ledger"]))
        assert result["local_ledger"]["uid_ledger_checkpoint_sha256"] == "e" * 64
        assert result["local_ledger"]["scope_state"] == "BOOTSTRAP"
        assert result["packet_sha256"] == operands.reference.packet_sha256
        assert result["account_complete"] is result["execution_authority"] is False
        assert result["trade_qualified"] is result["post_g12_eligible"] is False
        assert result["control_observation_semantics"] == "ENDPOINTS_ONLY"
        assert result["admission"] == "DENY"
        assert "uid" not in result and "account_id" not in result["local_ledger"]
        for patch in (
            {"control_event_sha256": "f" * 64},
            {"server_incarnation_sha256": "f" * 64},
            {"role_route_sha256": "f" * 64},
            {"native_definitions_sha256": "f" * 64},
            {"uid_ledger_checkpoint_sha256": "f" * 64},
            {"unresolved_consumed_intent_count": 1},
            {"ledger_revision": 1},
        ):
            with pytest.raises(
                source.NativeCurrentSource0056Error, match="state_changed"
            ):
                source._qualification_source_document(
                    operands, plan, plan_sha, before, replace(after, **patch)
                )
        for left, right in (
            (
                replace(before, observed_at=first_request + timedelta(microseconds=1)),
                after,
            ),
            (
                before,
                replace(
                    after, observed_at=packet.completed_at - timedelta(microseconds=1)
                ),
            ),
        ):
            with pytest.raises(
                source.NativeCurrentSource0056Error, match="order_invalid"
            ):
                source._qualification_source_document(
                    operands, plan, plan_sha, left, right
                )


def test_qualification_preflight_cli_has_explicit_mode_and_no_auth_flag(
    monkeypatch, capsys
):
    operation = AsyncMock(
        return_value=source.NativeCurrentSource0056ObservationV1("a" * 64)
    )
    monkeypatch.setattr(
        cli, "observe_configured_qualification_preflight_0056_v1", operation
    )
    assert cli.main(["--instrument", "BTC-USDT-SWAP", "--qualification-preflight"]) == 0
    operation.assert_awaited_once_with("BTC-USDT-SWAP")
    packet = json.loads(capsys.readouterr().out)
    assert packet["status"] == "QUALIFICATION_PREFLIGHT_OBSERVED"
    assert packet["account_complete"] is packet["execution_authority"] is False
    assert packet["admission"] == "DENY"


@pytest.mark.asyncio
async def test_qualification_ledger_observation_always_rolls_back(monkeypatch):
    transaction = SimpleNamespace(rollback=AsyncMock())
    session = SimpleNamespace(begin=AsyncMock(return_value=transaction))

    @asynccontextmanager
    async def factory():
        yield session

    factories = SimpleNamespace(writer_factory=factory)
    plan = SimpleNamespace(expected_uid="123", settlement_currency="USDT")
    forbidden = AsyncMock(side_effect=source.ledger56.NativeLedgerPreflight0056Error)
    monkeypatch.setattr(
        source.ledger56, "read_locked_native_ledger_preflight_0056_v1", forbidden
    )
    with pytest.raises(source.ledger56.NativeLedgerPreflight0056Error):
        await source._observe_qualification_ledger(factories, object(), plan)
    session.begin.assert_awaited_once()
    transaction.rollback.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("identity_changed", [False, True])
async def test_qualification_capture_brackets_one_acquisition_in_original_lease(
    monkeypatch, tmp_path, identity_changed
):
    plan = SimpleNamespace(expected_uid="123", settlement_currency="USDT")
    lease = Mock()
    client = SimpleNamespace(pinned_read_session=lambda: nullcontext(lease))
    factory = object()
    custody_factory = object()
    factories = SimpleNamespace(writer_factory=factory, custody_factory=custody_factory)
    roles = object()
    selected = SimpleNamespace(
        current_plan=plan, factories=factories, source_roles_0056=roles
    )
    original_record = object()
    ledger_before, ledger_after, operands, account = (object() for _ in range(4))
    owned = object.__new__(source.current.OwnedCurrentDemoAccountSourceV4)
    custody = object()
    seen = []

    async def checkpoint(configured_factories, role_witness, selected_plan):
        assert (configured_factories, role_witness, selected_plan) == (
            factories,
            roles,
            plan,
        )
        seen.append("ledger")
        return ledger_before if len(seen) == 1 else ledger_after

    async def acquire(**kwargs):
        assert seen == ["ledger"]
        assert kwargs == {
            "account_session": account,
            "private_client": client,
            "session_factory": factory,
            "private_proof_root": tmp_path / "route-proof",
            "native_proof_root": tmp_path / "native-proof",
            "custody_repository": custody,
        }
        seen.append("acquire")
        return owned

    def consume(candidate):
        assert candidate is owned and seen == ["ledger", "acquire", "ledger"]
        seen.append("consume")
        return operands

    selector = AsyncMock(return_value=selected)
    read_record = Mock(
        side_effect=[original_record, object() if identity_changed else original_record]
    )
    dispose = AsyncMock()
    document = Mock(return_value={"status": "inert"})
    monkeypatch.setattr(source, "OkxDemoPrivateRestClient", lambda **_: client)
    monkeypatch.setattr(
        source.selectors, "select_configured_demo_source_identity_0056_v1", selector
    )
    monkeypatch.setattr(source.selectors, "_record", read_record)
    monkeypatch.setattr(source, "_observe_qualification_ledger", checkpoint)
    monkeypatch.setattr(source, "RuntimeBundleCustodyRepository", lambda _: custody)
    monkeypatch.setattr(source, "_attempt", lambda *_a, **_kw: tmp_path)
    monkeypatch.setattr(source.capture, "plan_sha256", lambda _: "a" * 64)
    make_account = Mock(return_value=account)
    monkeypatch.setattr(
        source.ControlledDemoAccountSession, "from_pinned_read_session", make_account
    )
    monkeypatch.setattr(
        source.current, "acquire_initial_demo_account_source_v4", acquire
    )
    monkeypatch.setattr(
        source.current, "consume_owned_current_demo_account_v4", consume
    )
    monkeypatch.setitem(source.current._CURRENT, owned, object())
    monkeypatch.setattr(source, "_qualification_source_document", document)
    monkeypatch.setattr(source, "_dispose", dispose)
    if identity_changed:
        with pytest.raises(
            source.NativeCurrentSource0056Error, match="selection_changed"
        ):
            await source._capture(
                object(), "BTC-USDT-SWAP", qualification_preflight=True
            )
        assert seen == ["ledger", "acquire", "ledger"]
        document.assert_not_called()
    else:
        assert await source._capture(
            object(), "BTC-USDT-SWAP", qualification_preflight=True
        ) == (tmp_path, {"status": "inert"})
        assert seen == ["ledger", "acquire", "ledger", "consume"]
        document.assert_called_once_with(
            operands, plan, "a" * 64, ledger_before, ledger_after
        )
    assert owned not in source.current._CURRENT
    selector.assert_awaited_once_with(
        private_client=client, read_session=lease, instrument_ids=("BTC-USDT-SWAP",)
    )
    make_account.assert_called_once_with(
        read_session=lease, plan=plan, expected_plan_sha256="a" * 64
    )
    dispose.assert_awaited_once_with(selected, factories)
