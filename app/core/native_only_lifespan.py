"""Opt-in, no-execution Demo lifespan preflight.

This narrows startup I/O; it does not authorize an order, certify account
completeness, or replace the account-scoped native control reconciliation.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text


class NativeOnlyLifespanError(RuntimeError):
    """A fixed, non-sensitive denial code."""


_MUST_BE_FALSE = (
    "auto_trade",
    "live_trading",
    "paper_persistence_enabled",
    "paper_auto_ticks",
    "paper_auto_execution",
    "okx_ws_enabled",
    "okx_demo_allow_order_writes",
    "okx_demo_auto_execution",
    "okx_demo_auto_reconcile_on_start",
    "okx_demo_observability_enabled",
    "okx_demo_soak_enabled",
    "okx_demo_soak_allow_execute",
    "okx_demo_continuous_session_enabled",
    "okx_live_enabled",
    "okx_live_allow_order_writes",
    "okx_live_auto_execution",
    "okx_live_auto_reconcile_on_start",
    "notion_outbox_enabled",
    "submission_outbox_enabled",
)

_CONTROL_SQL = text("""
SELECT control_revision, armed, emergency_stop, locked,
       restart_latch_required, lock_reasons, active_trades,
       symbol_cooldowns, realized_pnl_events
FROM public.demo_automation_state WHERE id = 1
""")
_RUNNING_SOAK_SQL = text("""
SELECT 1 FROM public.demo_soak_sessions WHERE state = 'running' LIMIT 1
""")


@dataclass(frozen=True)
class ExistingControl:
    revision: int
    armed: bool
    emergency_stop: bool
    locked: bool
    restart_latch_required: bool


def require_native_only_settings(settings) -> None:
    """Reject incompatible configuration before any startup database call."""
    if (
        getattr(settings, "environment", None) != "production"
        or getattr(settings, "trading_mode", None) != "okx_demo"
        or getattr(settings, "web_concurrency", None) != 1
        or getattr(settings, "okx_demo_enabled", None) is not True
        or any(getattr(settings, name, None) is not False for name in _MUST_BE_FALSE)
    ):
        raise NativeOnlyLifespanError("native_only_configuration_denied")


def _checked_control(row, running_soak) -> ExistingControl:
    if running_soak is not None:
        raise NativeOnlyLifespanError("native_only_running_soak_unresolved")
    if row is None:
        # The legacy recovery method would otherwise insert a default empty
        # state. A missing row cannot establish the absence of prior exposure.
        raise NativeOnlyLifespanError("native_only_control_state_missing")
    if type(row) is not dict or set(row) != {
        "control_revision",
        "armed",
        "emergency_stop",
        "locked",
        "restart_latch_required",
        "lock_reasons",
        "active_trades",
        "symbol_cooldowns",
        "realized_pnl_events",
    }:
        raise NativeOnlyLifespanError("native_only_control_state_invalid")
    revision = row["control_revision"]
    armed = row["armed"]
    stopped = row["emergency_stop"]
    locked = row["locked"]
    latch = row["restart_latch_required"]
    reasons = row["lock_reasons"]
    active = row["active_trades"]
    cooldowns = row["symbol_cooldowns"]
    realized = row["realized_pnl_events"]
    if (
        type(revision) is not int
        or revision < 1
        or any(type(value) is not bool for value in (armed, stopped, locked, latch))
        or type(reasons) is not list
        or len(reasons) > 128
        or any(type(reason) is not str or not reason for reason in reasons)
        or type(active) is not dict
        or type(cooldowns) is not dict
        or type(realized) is not list
        or (armed and (stopped or not latch))
        or (stopped and (not locked or "emergency_stop_engaged" not in reasons))
    ):
        raise NativeOnlyLifespanError("native_only_control_state_invalid")
    return ExistingControl(revision, armed, stopped, locked, latch)


async def inspect_existing_control(session_factory) -> ExistingControl:
    """Read actual control and stale-soak state in one read-only snapshot."""
    async with session_factory() as session, session.begin():
        await session.execute(
            text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        )
        control = (await session.execute(_CONTROL_SQL)).mappings().one_or_none()
        running = (await session.execute(_RUNNING_SOAK_SQL)).scalar_one_or_none()
    return _checked_control(None if control is None else dict(control), running)


def _check_recovery(before: ExistingControl, after, status) -> None:
    expected_stop = (
        before.armed or before.emergency_stop or before.restart_latch_required
    )
    if (
        type(after) is not dict
        or type(after.get("_control_revision")) is not int
        or after["_control_revision"] != before.revision + 1
        or after.get("armed") is not False
        or type(after.get("_restart_latch_required")) is not bool
        or (expected_stop and after["_restart_latch_required"] is not True)
        or (expected_stop and after.get("emergency_stop") is not True)
        or (expected_stop and after.get("locked") is not True)
        or (before.locked and after.get("locked") is not True)
        or getattr(status, "recovered", None) is not True
        or getattr(status, "armed", None) is not False
        or (expected_stop and getattr(status, "emergency_stop", None) is not True)
    ):
        raise NativeOnlyLifespanError("native_only_recovery_readback_unconfirmed")


async def start_native_only(
    settings,
    session_factory,
    automation,
    *,
    expected_control_revision: int | None = None,
) -> None:
    """Recover against an optional prior revision, then independently read back.

    The supplied revision is only a race fence. Its caller must establish the
    independent source/route proof; this method grants no startup authority.
    """
    require_native_only_settings(settings)
    if expected_control_revision is not None and (
        type(expected_control_revision) is not int or expected_control_revision < 1
    ):
        raise NativeOnlyLifespanError("native_only_expected_control_revision_invalid")
    try:
        before = await inspect_existing_control(session_factory)
        if (
            expected_control_revision is not None
            and before.revision != expected_control_revision
        ):
            raise NativeOnlyLifespanError("native_only_control_revision_changed")
        if automation.repository is None:
            raise NativeOnlyLifespanError("native_only_control_repository_missing")
        # Bind the recovery CAS to the exact admitted control revision.
        await automation.recover(
            require_existing=True, expected_revision=before.revision
        )
        after = await automation.repository.load_state()
        status = await automation.status()
        _check_recovery(before, after, status)
    except NativeOnlyLifespanError:
        raise
    except Exception:  # noqa: BLE001 - never expose URL/SQL/private exception text
        raise NativeOnlyLifespanError("native_only_preflight_unavailable") from None
