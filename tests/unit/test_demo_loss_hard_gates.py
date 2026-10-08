"""Recorded risk-state persistence and continuous scheduling; synthetic IO only."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

import pytest

from app.demo_automation import DemoAutomationSafetyError
from tests.unit.test_demo_automation import (
    FakeDemo,
    MemoryAutomationRepository,
    adaptive_service,
    tracked_trade,
)


def configured(demo, *, continuous=True, repository=None):
    service = adaptive_service(
        demo,
        {"BTC-USDT-SWAP": 95},
        okx_demo_continuous_session_enabled=continuous,
        okx_demo_capital_bucket_enabled=True,
        okx_demo_trade_cooldown_seconds=0,
    )
    # Arm requires an explicit durable-state test double. Preserve a supplied
    # repository for restart/recovery cases, including its recorded loss state.
    service.repository = (
        MemoryAutomationRepository() if repository is None else repository
    )
    return service


def loss(service, amount=D(-300), *, when=None):
    when = when or datetime.now(UTC)
    service._record_realized_pnl_event(
        tracked_trade("BTC-USDT-SWAP", started_at=when - timedelta(minutes=5)),
        when,
        amount,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("continuous", [False, True])
@pytest.mark.parametrize("kind", ["daily", "streak"])
async def test_loss_cap_survives_restart_and_continuous_mode_toggle(continuous, kind):
    demo = FakeDemo()
    repository = MemoryAutomationRepository()
    service = configured(demo, continuous=continuous, repository=repository)
    await service.recover()
    await service.arm()
    if kind == "daily":
        loss(service)
    else:
        service._state["consecutive_losses"] = 3
    run = await service.run_once(execute=True)
    assert run.results[0].outcome == "locked"
    preserved = deepcopy(repository.state)
    reason = (
        "daily_loss_limit_reached"
        if kind == "daily"
        else "consecutive_loss_limit_reached"
    )
    assert reason in preserved["lock_reasons"]
    restarted = configured(demo, continuous=not continuous, repository=repository)
    await restarted.recover()
    assert (await restarted.status()).armed is False
    assert reason in (await restarted.status()).lock_reasons
    assert restarted._state["realized_pnl_events"] == preserved["realized_pnl_events"]
    assert restarted._state["consecutive_losses"] == preserved["consecutive_losses"]
    assert (await restarted.status()).emergency_stop is True
    with pytest.raises(
        DemoAutomationSafetyError, match="emergency_stop_must_be_cleared"
    ):
        await restarted.arm()
    await restarted.clear_emergency_stop()
    with pytest.raises(DemoAutomationSafetyError, match="automation_locked"):
        await restarted.arm()
    assert demo.place_calls == []


@pytest.mark.asyncio
async def test_deposit_or_equity_recovery_does_not_erase_realized_daily_loss():
    demo = FakeDemo()
    service = configured(demo)
    await service.recover()
    await service.arm()
    loss(service)
    demo.equity += D(1000)
    run = await service.run_once(execute=True)
    assert run.daily_pnl == D(1000)  # Equity delta is explicitly distinct.
    assert service._daily_realized_pnl() == D(-300)
    assert "daily_loss_limit_reached" in run.results[0].reason_codes
    assert demo.place_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["daily", "streak"])
async def test_last_await_loss_change_vetoes_submission(kind):
    class Changed(FakeDemo):
        async def place_order(self, request, *, before_submit=None):
            service.settings.okx_demo_continuous_session_enabled = True
            if kind == "daily":
                loss(service)
            else:
                service._state["consecutive_losses"] = 3
            return await super().place_order(request, before_submit=before_submit)

    demo = Changed()
    service = configured(demo, continuous=False)
    await service.recover()
    await service.arm()
    result = await service.run_once(execute=True)
    assert result.results[0].order_submission_attempted is False
    assert "automation_locked" in result.results[0].detail
    assert demo.place_calls == []


@pytest.mark.asyncio
async def test_fresh_after_leverage_equity_loss_cannot_use_previous_daily_budget():
    class Changed(FakeDemo):
        async def set_leverage(self, request):
            response = await super().set_leverage(request)
            self.equity -= D(300)
            return response

    demo = Changed()
    service = configured(demo)
    await service.recover()
    await service.arm()
    result = await service.run_once(execute=True)
    assert result.results[0].order_submission_attempted is False
    assert "daily_loss_limit_reached" in (await service.status()).lock_reasons
    assert demo.place_calls == []


@pytest.mark.asyncio
async def test_daily_latch_releases_only_at_later_utc_day_and_keeps_streak():
    demo = FakeDemo()
    service = configured(demo)
    await service.recover()
    await service.arm()
    service._state["daily_pnl"] = D(-300)
    service._apply_locks(demo.equity)
    service._state["daily_pnl"] = D(1000)
    service._apply_locks(demo.equity)
    assert "daily_loss_limit_reached" in service._state["lock_reasons"]
    service._state["session_date"] = datetime.now(UTC).date() - timedelta(days=1)
    service._state["consecutive_losses"] = 2
    service._roll_session(demo.equity, "single_currency:USDT")
    service._apply_locks(demo.equity)
    assert "daily_loss_limit_reached" not in service._state["lock_reasons"]
    assert service._state["consecutive_losses"] == 2


@pytest.mark.asyncio
async def test_basis_change_cannot_wipe_loss_history_even_when_flat():
    demo = FakeDemo()
    service = configured(demo)
    await service.recover()
    await service.arm()
    loss(service, D(-1))
    before = deepcopy(service._state["realized_pnl_events"])
    blocker = service._roll_session(
        demo.equity, "different-currency", allow_rebase=True
    )
    assert blocker == "equity_basis_change_requires_flat_session"
    assert service._state["realized_pnl_events"] == before


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["future", "nan", "malformed", "conflict", "naive"])
async def test_invalid_recorded_loss_history_is_retained_and_stops_recovery(fault):
    demo = FakeDemo()
    repository = MemoryAutomationRepository()
    service = configured(demo, repository=repository)
    await service.recover()
    await service.arm()
    loss(service, D(-1))
    raw = service._state["realized_pnl_events"]
    if fault == "future":
        raw[0]["closed_at"] = (datetime.now(UTC) + timedelta(seconds=1)).isoformat()
    elif fault == "nan":
        raw[0]["net_pnl"] = "NaN"
    elif fault == "naive":
        raw[0]["closed_at"] = datetime.now(UTC).replace(tzinfo=None).isoformat()
    elif fault == "malformed":
        raw.append({"missing": "outcome"})
    else:
        raw.append({**raw[0], "net_pnl": "100"})
    mutated = deepcopy(service._state)
    mutated["_control_revision"] = service._control_revision + 1
    mutated["_restart_latch_required"] = service._restart_latch_required
    await repository.save_state(mutated)
    retained = deepcopy(raw)
    restarted = configured(demo, repository=repository)
    await restarted.recover()
    status = await restarted.status()
    assert status.emergency_stop is True
    assert status.armed is False
    assert "realized_pnl_history_invalid" in status.lock_reasons
    assert restarted._state["realized_pnl_events"] == retained
    with pytest.raises(DemoAutomationSafetyError):
        await restarted.arm()
    assert demo.place_calls == []


@pytest.mark.asyncio
async def test_existing_loss_cannot_be_overwritten_by_changed_outcome():
    demo = FakeDemo()
    service = configured(demo)
    await service.recover()
    when = datetime.now(UTC)
    loss(service, D(-300), when=when)
    original = deepcopy(service._state["realized_pnl_events"])
    loss(service, D(300), when=when)
    assert service._state["realized_pnl_events"] == original
    assert (await service.status()).emergency_stop is True


@pytest.mark.asyncio
async def test_new_outcome_cannot_discard_prior_malformed_history():
    demo = FakeDemo()
    service = configured(demo)
    await service.recover()
    service._state["realized_pnl_events"] = [{"unresolved": "source"}]
    prior = deepcopy(service._state["realized_pnl_events"])
    loss(service)
    assert service._state["realized_pnl_events"] == prior
    assert (await service.status()).emergency_stop is True


@pytest.mark.asyncio
async def test_backward_utc_date_never_resets_daily_baseline_or_latch():
    demo = FakeDemo()
    service = configured(demo)
    await service.recover()
    await service.arm()
    service._state["session_date"] = datetime.now(UTC).date() + timedelta(days=1)
    baseline = service._state["baseline_equity"]
    assert (
        service._roll_session(D(1), "single_currency:USDT")
        == "risk_session_clock_reversed"
    )
    assert service._state["baseline_equity"] == baseline
    assert (await service.status()).emergency_stop is True
