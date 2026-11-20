"""Explicit manual Demo startup using real restricted roles and durable recovery.

Startup loads the original stopped state; it never Arms, clears a stop, starts
a scheduler, or grants order authority. Every action still needs its original
authenticated native request, account/source proof, reservation and intent.
"""

import asyncio
from pathlib import Path

from app.core import native_only_lifespan as original
from app.domain import native_database_profile_v1 as profiles

_SOURCE = Path(__file__).resolve().parents[2]
_DISABLED = tuple(
    name
    for name in original._MUST_BE_FALSE
    if name
    not in {"okx_demo_allow_order_writes", "okx_demo_auto_execution", "okx_ws_enabled"}
) + ("ctcc_native_only_lifespan_v1", "ctcc_local_analysis_only_v1")


class NativeManualDemoStartup0056Error(RuntimeError):
    """Fixed refusal without private settings or recovery exceptions."""


def _need(value):
    if value is not True:
        raise NativeManualDemoStartup0056Error(
            "native_manual_demo_0056_startup_refused"
        ) from None


def require_manual_demo_settings_0056_v1(settings):
    _need(
        getattr(settings, "ctcc_native_demo_manual_lifespan_0056_v1", None) is True
        and getattr(settings, "environment", None) == "production"
        and getattr(settings, "trading_mode", None) == "okx_demo"
        and getattr(settings, "web_concurrency", None) == 1
        and getattr(settings, "okx_demo_enabled", None) is True
        and getattr(settings, "api_token_is_safe", None) is True
        and all(getattr(settings, name, None) is False for name in _DISABLED)
        and all(
            type(getattr(settings, name, None)) is bool
            for name in (
                "okx_demo_allow_order_writes",
                "okx_demo_auto_execution",
                "okx_ws_enabled",
            )
        )
    )


async def start_manual_demo_0056_v1(settings, session_factory, automation):
    """Pin DB0056, stop the signed account, run actual recovery and read back.

    The old monitor's trust descriptor is never accepted as execution authority.
    Missing source, account or role proof refuses before accepting HTTP actions.
    """
    require_manual_demo_settings_0056_v1(settings)
    from app.config.settings import get_settings
    from app.core import native_account_restart_recovery_0056_v1 as account_stop
    from app.core import native_role_witness_0056_v1 as roles56
    from app.core import native_startup_candidate_0056_v1 as source56
    from app.demo_automation import native_server_selectors_v2 as selectors
    from app.trade_qualification import native_account_risk_assembly_v1 as native

    selected = None
    failed = False
    try:
        _need(settings is get_settings() and automation.settings is settings)
        settings_pin = native._v3_data_pin(settings.__dict__)
        source_sha = source56._source_manifest_sha256(_SOURCE)
        selected = selectors.select_configured_native_demo_factories_v2()
        _need(selected.writer_factory is session_factory)
        actual_head = await selectors._observe_runtime_database_head_v2(selected)
        _need(actual_head in ("0056", "0057", "0058"))
        profile = profiles.from_head(actual_head)
        first = await roles56.observe_restricted_native_roles_0056_witness_v1(
            selected, source=_SOURCE, **profiles.arguments(profile)
        )
        _need(
            type(first) is roles56.RestrictedNativeRoles0056WitnessV1
            and first.observation.database_head == actual_head
            and selectors._profile_for_original_roles_0056(first) is profile
        )
        before = await original.inspect_existing_control(session_factory)
        symbols = settings.okx_demo_scan_symbol_list
        _need(type(symbols) is list and bool(symbols))
        stopped = await account_stop.recover_configured_account_restart_0056_v1(
            symbols[0], reconcile_only=False
        )
        _need(
            type(stopped) is account_stop.NativeAccountRestartRecovery0056V1
            and stopped.database_head == actual_head
            and stopped.emergency_stop is True
            and stopped.armed is False
        )
        _need(await original.inspect_existing_control(session_factory) == before)
        _need(automation.repository is not None)
        # Use the genuine recovery method. It persists the restart latch and
        # revision, loads retained history, and never restores an Arm.
        await automation.recover(
            require_existing=True, expected_revision=before.revision
        )
        after = await automation.repository.load_state()
        status = await automation.status()
        original._check_recovery(before, after, status)
        second = await roles56.observe_restricted_native_roles_0056_witness_v1(
            selected, source=_SOURCE, **profiles.arguments(profile)
        )
        _need(
            first == second
            and settings is get_settings()
            and native._v3_data_pin(settings.__dict__) == settings_pin
            and source56._source_manifest_sha256(_SOURCE) == source_sha
        )
        require_manual_demo_settings_0056_v1(settings)
        return actual_head
    except asyncio.CancelledError:
        failed = True
        raise
    except Exception:  # noqa: BLE001 -- no private DB/account failures escape startup.
        failed = True
        raise NativeManualDemoStartup0056Error(
            "native_manual_demo_0056_startup_refused"
        ) from None
    finally:
        if selected is not None:
            try:
                await selectors._dispose_failed_selection_factories_v2(selected)
            except BaseException as error:
                if not failed:
                    if isinstance(error, asyncio.CancelledError):
                        raise
                    raise NativeManualDemoStartup0056Error(
                        "native_manual_demo_0056_startup_refused"
                    ) from None
