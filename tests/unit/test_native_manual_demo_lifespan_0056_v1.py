"""Synthetic startup sequencing; these fixtures grant no runtime authority."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core import native_manual_demo_lifespan_0056_v1 as manual


def settings(**changes):
    return SimpleNamespace(
        **(
            dict.fromkeys(manual._DISABLED, False)
            | {
                "ctcc_native_demo_manual_lifespan_0056_v1": True,
                "environment": "production",
                "trading_mode": "okx_demo",
                "web_concurrency": 1,
                "okx_demo_enabled": True,
                "api_token_is_safe": True,
                "okx_demo_allow_order_writes": False,
                "okx_demo_auto_execution": False,
                "okx_demo_scan_symbol_list": ["BTC-USDT-SWAP"],
            }
            | changes
        )
    )


@pytest.mark.parametrize("name", manual._DISABLED)
def test_incompatible_subsystem_cannot_start_manual_profile(name):
    with pytest.raises(manual.NativeManualDemoStartup0056Error):
        manual.require_manual_demo_settings_0056_v1(settings(**{name: True}))


@pytest.mark.parametrize(
    "change",
    [
        {"ctcc_native_demo_manual_lifespan_0056_v1": False},
        {"environment": "test"},
        {"trading_mode": "live"},
        {"web_concurrency": 2},
        {"okx_demo_enabled": False},
        {"api_token_is_safe": False},
        {"okx_demo_allow_order_writes": None},
        {"okx_demo_auto_execution": 1},
    ],
)
def test_unknown_or_other_environment_denied(change):
    with pytest.raises(manual.NativeManualDemoStartup0056Error):
        manual.require_manual_demo_settings_0056_v1(settings(**change))


def test_default_off_writes_can_start_disarmed_manual_control():
    manual.require_manual_demo_settings_0056_v1(settings())


def lab(monkeypatch):
    from app.config import settings as configured
    from app.core import native_account_restart_recovery_0056_v1 as stop
    from app.core import native_role_witness_0056_v1 as roles
    from app.core import native_startup_candidate_0056_v1 as source
    from app.demo_automation import native_server_selectors_v2 as selectors

    config = settings()
    factory = object()
    chosen = SimpleNamespace(writer_factory=factory)
    seen = []
    witness = roles.RestrictedNativeRoles0056WitnessV1(
        roles.old.RestrictedNativeRoles0056ObservationV1(
            "1" * 64, "2" * 64, "3" * 64, "4" * 64
        ),
        "5" * 64,
        "6" * 64,
    )
    before = manual.original.ExistingControl(7, False, True, True, True)

    async def role_read(*_args, **_kwargs):
        seen.append("roles")
        return witness

    async def account_stop(*_args, **kwargs):
        assert kwargs == {"reconcile_only": False}
        seen.append("account_stop")
        return stop.NativeAccountRestartRecovery0056V1("STOP_OBSERVED", 9, "a" * 64)

    async def recover(**kwargs):
        assert kwargs == {"require_existing": True, "expected_revision": 7}
        seen.append("genuine_recover")

    automation = SimpleNamespace(
        settings=config,
        repository=SimpleNamespace(
            load_state=AsyncMock(
                return_value={
                    "_control_revision": 8,
                    "armed": False,
                    "_restart_latch_required": True,
                    "emergency_stop": True,
                    "locked": True,
                }
            )
        ),
        recover=AsyncMock(side_effect=recover),
        status=AsyncMock(
            return_value=SimpleNamespace(
                recovered=True, armed=False, emergency_stop=True
            )
        ),
    )
    dispose = AsyncMock()
    monkeypatch.setattr(configured, "get_settings", lambda: config)
    monkeypatch.setattr(source, "_source_manifest_sha256", lambda _path: "a" * 64)
    monkeypatch.setattr(
        selectors, "select_configured_native_demo_factories_v2", lambda: chosen
    )
    monkeypatch.setattr(selectors, "_dispose_failed_selection_factories_v2", dispose)
    monkeypatch.setattr(
        roles,
        "observe_restricted_native_roles_0056_witness_v1",
        AsyncMock(side_effect=role_read),
    )
    monkeypatch.setattr(
        manual.original, "inspect_existing_control", AsyncMock(return_value=before)
    )
    monkeypatch.setattr(
        stop,
        "recover_configured_account_restart_0056_v1",
        AsyncMock(side_effect=account_stop),
    )
    return config, factory, chosen, automation, seen, dispose, roles, stop, source


@pytest.mark.asyncio
async def test_original_stop_then_genuine_recovery_and_independent_readback(
    monkeypatch,
):
    config, factory, chosen, automation, seen, dispose, *_ = lab(monkeypatch)
    await manual.start_manual_demo_0056_v1(config, factory, automation)
    assert seen == ["roles", "account_stop", "genuine_recover", "roles"]
    dispose.assert_awaited_once_with(chosen)


@pytest.mark.asyncio
async def test_failed_account_stop_cannot_recover_or_accept_startup(monkeypatch):
    config, factory, chosen, automation, _, dispose, _, stop, _ = lab(monkeypatch)
    monkeypatch.setattr(
        stop,
        "recover_configured_account_restart_0056_v1",
        AsyncMock(side_effect=ValueError("synthetic-private-detail")),
    )
    with pytest.raises(manual.NativeManualDemoStartup0056Error) as caught:
        await manual.start_manual_demo_0056_v1(config, factory, automation)
    assert "synthetic-private-detail" not in str(caught.value)
    automation.recover.assert_not_awaited()
    dispose.assert_awaited_once_with(chosen)


@pytest.mark.asyncio
async def test_recovery_revision_mismatch_refuses_startup(monkeypatch):
    config, factory, _, automation, _, _, *_ = lab(monkeypatch)
    automation.repository.load_state.return_value["_control_revision"] = 9
    with pytest.raises(manual.NativeManualDemoStartup0056Error):
        await manual.start_manual_demo_0056_v1(config, factory, automation)


@pytest.mark.asyncio
async def test_changed_source_after_recovery_refuses_startup(monkeypatch):
    config, factory, _, automation, _, _, _, _, source = lab(monkeypatch)
    values = iter(("a" * 64, "b" * 64))
    monkeypatch.setattr(source, "_source_manifest_sha256", lambda _path: next(values))
    with pytest.raises(manual.NativeManualDemoStartup0056Error):
        await manual.start_manual_demo_0056_v1(config, factory, automation)


@pytest.mark.asyncio
async def test_cancellation_preserved_when_disposal_also_fails(monkeypatch):
    config, factory, _, automation, _, dispose, _, stop, _ = lab(monkeypatch)
    monkeypatch.setattr(
        stop,
        "recover_configured_account_restart_0056_v1",
        AsyncMock(side_effect=asyncio.CancelledError()),
    )
    dispose.side_effect = ValueError("synthetic-disposal-detail")
    with pytest.raises(asyncio.CancelledError):
        await manual.start_manual_demo_0056_v1(config, factory, automation)
