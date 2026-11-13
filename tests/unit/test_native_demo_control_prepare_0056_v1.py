"""Purpose separation before any signed Demo configuration read."""

from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.core.native_role_witness_0056_v1 import RestrictedNativeRoles0056WitnessV1
from app.database.repositories.demo_control import ControlObservation
from app.demo_automation import native_server_selectors_v2 as selectors
from app.trade_qualification import account_history_source_v1 as history
from app.trade_qualification import demo_control as control
from app.trade_qualification import native_demo_control_prepare_0056_v1 as prepare

_INSTRUMENTS = ("BTC-USDT-SWAP",)


async def test_controlled_selector_refuses_before_get_when_roles_do_not_match(
    monkeypatch,
):
    client = SimpleNamespace(account_config_selector_v2=AsyncMock())
    factories = SimpleNamespace(
        frame_reader_engine=SimpleNamespace(dispose=AsyncMock()),
        custody_engine=SimpleNamespace(dispose=AsyncMock()),
    )
    settings = object()
    monkeypatch.setattr(selectors, "_configured_inputs", Mock(return_value=settings))
    monkeypatch.setattr(selectors, "_route_artifact", Mock(return_value=b"route"))
    monkeypatch.setattr(selectors, "_server_settings_pin", Mock(return_value="pin"))
    monkeypatch.setattr(
        selectors,
        "select_configured_native_demo_factories_v2",
        Mock(return_value=factories),
    )
    witness = AsyncMock(side_effect=ValueError("private anomaly"))
    monkeypatch.setattr(selectors, "_source_roles_before_get_0056", witness)

    with pytest.raises(selectors.NativeDemoCallerReadinessErrorV2):
        await selectors._select_configured_demo_controlled_identity_0056(
            private_client=client, read_session=object(), instrument_ids=_INSTRUMENTS
        )

    witness.assert_awaited_once_with(factories)
    client.account_config_selector_v2.assert_not_awaited()
    factories.frame_reader_engine.dispose.assert_awaited_once()
    factories.custody_engine.dispose.assert_awaited_once()


async def test_source_only_binding_refuses_before_any_writable_control_preparation(
    monkeypatch,
):
    witness = RestrictedNativeRoles0056WitnessV1(
        SimpleNamespace(database_head="0056"), "1" * 64, "2" * 64
    )
    selected_record = object()
    factories = object()
    data = {
        "purpose_0056": selectors._SOURCE_ONLY_0056,
        "source_roles_0056": witness,
        "record": selected_record,
        "factories": factories,
    }
    monkeypatch.setattr(selectors, "_record", Mock(return_value=data))
    repository = Mock()
    monkeypatch.setattr(prepare, "DemoControlRepository0056V1", repository)
    with pytest.raises(selectors.NativeDemoCallerReadinessErrorV2):
        await prepare.prepare_configured_demo_control_0056(object())
    repository.assert_not_called()

    data["purpose_0056"] = selectors._CONTROLLED_0056
    assert selectors._controlled_identity_0056(object()) == (
        selected_record,
        factories,
        witness,
    )


async def test_controlled_purpose_cannot_enter_source_only_history(monkeypatch):
    session, client = object(), object()
    data = {
        "purpose_0056": selectors._CONTROLLED_0056,
        "source_roles_0056": object(),
        "read_session": session,
        "private_client": client,
    }
    monkeypatch.setattr(selectors, "_record", Mock(return_value=data))
    acquire = AsyncMock()
    monkeypatch.setattr(history, "_acquire_native_history_source", acquire)
    with pytest.raises(ValueError, match="original_selector_required"):
        await history.acquire_configured_history_current_source_0056_v1(
            selection=object(),
            read_session=session,
            private_client=client,
            raw_root=Path("raw"),
            companion_root=Path("companion"),
        )
    acquire.assert_not_called()


@pytest.mark.parametrize("drift", ["none", "control", "identity"])
async def test_prepare_replays_genesis_between_exact_control_reads(monkeypatch, drift):
    instant = datetime(2026, 11, 14, tzinfo=UTC)
    scope = control.ControlScope("demo", "730001")
    stopped = control.stopped_genesis(scope, instant)
    factory = object()
    settings = SimpleNamespace(expected_uid=scope.account_id)
    plan = SimpleNamespace(settlement_currency="USDT")
    record = SimpleNamespace(
        scope_config=settings,
        current_plan=plan,
        route_evidence=SimpleNamespace(binding_sha256="1" * 64),
        read_session=SimpleNamespace(binding_id="original-credential-session"),
    )
    roles = SimpleNamespace(
        role_route_sha256="2" * 64, server_incarnation_sha256="3" * 64
    )
    selected = (record, SimpleNamespace(writer_factory=factory), roles)
    selection = Mock(
        side_effect=[
            selected,
            selected,
            ((object(), *selected[1:]) if drift == "identity" else selected),
            selected,
        ]
    )
    old = ControlObservation(stopped, "4" * 64, instant)
    new = ControlObservation(
        stopped,
        ("5" if drift == "control" else "4") * 64,
        instant + timedelta(seconds=1),
    )
    repo = SimpleNamespace(read=AsyncMock(side_effect=[old, new]))
    genesis = SimpleNamespace(window_started_at=instant, source_sha256="6" * 64)
    read_genesis = AsyncMock(return_value=genesis)
    monkeypatch.setattr(selectors, "_controlled_identity_0056", selection)
    monkeypatch.setattr(selectors.capture, "plan_sha256", lambda _: "7" * 64)
    monkeypatch.setattr(prepare, "DemoControlRepository0056V1", lambda *a, **kw: repo)
    monkeypatch.setattr(prepare.previous, "_read_genesis", read_genesis)
    monkeypatch.setattr(
        prepare.financial,
        "capture_configured_owned_risk_policy_v1",
        lambda **kwargs: SimpleNamespace(selected_settings_sha256="8" * 64),
    )
    monkeypatch.setattr(
        prepare.native,
        "_package_inventory_controlled55_v1",
        lambda: ({"writer_sha256": "9" * 64},),
    )

    if drift != "none":
        with pytest.raises(selectors.NativeDemoCallerReadinessErrorV2):
            await prepare.prepare_configured_demo_control_0056(object())
        return

    result = await prepare.prepare_configured_demo_control_0056(object())
    assert result.scope.account_id == scope.account_id and result.observation == new
    assert result.source_genesis_sha256 == genesis.source_sha256
    assert result.execution_authority is False
    assert repo.read.await_count == 2 and read_genesis.await_count == 1


@pytest.mark.parametrize("release_known", [True, False])
async def test_explicit_lifecycle_tightens_after_uncertain_release(
    monkeypatch, release_known
):
    lease = object()
    client = SimpleNamespace(pinned_read_session=lambda: nullcontext(lease))
    selection = SimpleNamespace(factories=object())
    select = AsyncMock(return_value=selection)
    stopped = control.stopped_genesis(
        control.ControlScope("demo", "730001"), datetime(2026, 11, 14, tzinfo=UTC)
    )
    observation = ControlObservation(stopped, "1" * 64, stopped.updated_at)
    owner = SimpleNamespace(
        release=AsyncMock(
            return_value=observation,
            side_effect=None if release_known else ValueError("unknown"),
        ),
        emergency_stop=AsyncMock(),
    )
    acquire = AsyncMock(return_value=owner)
    dispose = AsyncMock()
    monkeypatch.setattr(prepare, "_settings", Mock(return_value=object()))
    monkeypatch.setattr(prepare, "OkxDemoPrivateRestClient", Mock(return_value=client))
    monkeypatch.setattr(
        selectors, "_select_configured_demo_controlled_identity_0056", select
    )
    monkeypatch.setattr(
        prepare.previous, "prepare_selected_demo_control_owner_0056", acquire
    )
    monkeypatch.setattr(prepare, "_dispose", dispose)

    if release_known:
        assert (
            await prepare.verify_configured_demo_control_lifecycle_0056(_INSTRUMENTS[0])
            is observation
        )
        owner.emergency_stop.assert_not_awaited()
    else:
        with pytest.raises(ValueError, match="unknown"):
            await prepare.verify_configured_demo_control_lifecycle_0056(_INSTRUMENTS[0])
        owner.emergency_stop.assert_awaited_once()

    select.assert_awaited_once_with(
        private_client=client, read_session=lease, instrument_ids=_INSTRUMENTS
    )
    acquire.assert_awaited_once_with(selection)
    owner.release.assert_awaited_once()
    dispose.assert_awaited_once_with(selection, selection.factories)
