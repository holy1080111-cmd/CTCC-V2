"""Aggregate bucket margin acceptance; synthetic account, no exchange requests."""

import warnings
from datetime import UTC, datetime
from decimal import Decimal as D
from unittest.mock import patch

import pytest
from pydantic_settings import BaseSettings

from app.demo_automation import DemoAutomationSafetyError
from tests.unit.test_demo_automation import (
    FakeDemo,
    FakeHub,
    adaptive_service,
    candidate,
    prime_usdt_equity_basis,
    structural_demo_candidate,
    structural_dynamic_updates,
    tracked_trade,
)


def service_for(demo, symbols=("BTC-USDT-SWAP",), **settings):
    demo.equity = D(1000)
    high = candidate(score=95, stop_loss="99.9", take_profit="102")
    return adaptive_service(
        demo,
        {symbol: 95 for symbol in symbols},
        candidates={symbol: high for symbol in symbols},
        okx_demo_capital_bucket_enabled=True,
        okx_demo_position_margin_bucket_usdt=D(300),
        okx_demo_portfolio_max_margin_pct=D("0.60"),
        **settings,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("execute", (False, True))
async def test_two_300_buckets_exactly_reach_60_percent_third_cannot_submit(execute):
    demo = FakeDemo()
    service = service_for(demo, ("BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP"))
    await service.recover()
    if execute:
        await service.arm()
    result = await service.run_once(execute=execute)
    accepted = "submitted" if execute else "approved_dry_run"
    assert [item.outcome for item in result.results] == [accepted, accepted, "blocked"]
    assert result.portfolio_estimated_margin == D(600)
    assert result.results[-1].detail == "portfolio_margin_limit_reached"
    assert len(demo.place_calls) == (2 if execute else 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("execute", (False, True))
@pytest.mark.parametrize(
    "mutated_limit,expected_margin",
    ((D("0.90"), D(600)), (D("0.50"), D(500))),
)
async def test_structural_margin_runtime_policy_rejects_relaxation_and_keeps_tighter_cap(
    execute, mutated_limit, expected_margin
):
    demo = FakeDemo()
    demo.equity = D(1000)
    symbols = ("BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP")
    low_tier = structural_demo_candidate().model_copy(
        update={"score": 75, "risk_score": 75}
    )
    service = adaptive_service(
        demo,
        {symbol: 75 for symbol in symbols},
        candidates={symbol: low_tier for symbol in symbols},
        **structural_dynamic_updates(),
    )
    await service.recover()
    if execute:
        await service.arm()
    # Settings validates 60% at construction, but remains mutable afterward.
    service.settings.okx_demo_portfolio_max_margin_pct = mutated_limit

    if mutated_limit > D("0.60"):
        if execute:
            with pytest.raises(
                DemoAutomationSafetyError,
                match="demo_automation_runtime_policy_invalid",
            ):
                await service.run_once(execute=True)
        else:
            rejected = await service.run_once(execute=False)
            assert rejected.results[0].outcome == "blocked"
            assert (
                "demo_automation_runtime_policy_invalid" in rejected.results[0].detail
            )
        assert demo.place_calls == []
        assert (await service.status()).portfolio_max_margin_pct == D("0.60")
        return

    result = await service.run_once(execute=execute)

    accepted = "submitted" if execute else "approved_dry_run"
    assert [item.outcome for item in result.results] == [accepted, accepted, "blocked"]
    assert [item.estimated_margin for item in result.results[:2]] == [
        D(300),
        expected_margin - D(300),
    ]
    assert all(item.estimated_stop_loss_pct < D("0.005") for item in result.results[:2])
    assert result.results[-1].detail == "portfolio_margin_limit_reached"
    assert result.portfolio_estimated_margin == expected_margin
    assert len(demo.place_calls) == (2 if execute else 0)
    assert (await service.status()).portfolio_max_margin_pct == mutated_limit


@pytest.mark.asyncio
@pytest.mark.parametrize("execute", (False, True))
async def test_bucket_margin_cap_does_not_depend_on_structural_flag(execute):
    demo = FakeDemo()
    service = service_for(demo, ("BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP"))
    assert service.settings.okx_demo_structural_dynamic_leverage_enabled is False
    service.settings.okx_demo_portfolio_max_margin_pct = D("0.90")
    await service.recover()

    if execute:
        with pytest.raises(
            DemoAutomationSafetyError,
            match="demo_automation_runtime_policy_invalid",
        ):
            await service.arm()
    else:
        result = await service.run_once(execute=False)
        assert result.results[0].outcome == "blocked"
        assert "demo_automation_runtime_policy_invalid" in result.results[0].detail
    assert demo.place_calls == []
    assert (await service.status()).portfolio_max_margin_pct == D("0.60")


@pytest.mark.asyncio
async def test_non_bucket_score_risk_retains_configured_80_percent_margin_limit():
    demo = FakeDemo()
    demo.equity = D(1000)
    service = adaptive_service(
        demo,
        {"BTC-USDT-SWAP": 95},
        okx_demo_portfolio_max_margin_pct=D("0.80"),
    )
    await service.recover()

    result = await service.run_once(execute=False)

    assert result.results[0].outcome == "approved_dry_run"
    assert (await service.status()).portfolio_max_margin_pct == D("0.80")


@pytest.mark.asyncio
@pytest.mark.parametrize("execute", (False, True))
async def test_bucket_rejects_runtime_score_risk_disable_before_sizing(execute):
    demo = FakeDemo()
    service = service_for(demo)
    demo.equity = D(100)
    await service.recover()
    if execute:
        await service.arm()
    service.settings.okx_demo_score_risk_enabled = False

    if execute:
        with pytest.raises(
            DemoAutomationSafetyError, match="capital_bucket_requires_score_risk"
        ):
            await service.run_once(execute=True)
    else:
        run = await service.run_once(execute=False)
        assert run.results[0].outcome == "blocked"
        assert "capital_bucket_requires_score_risk" in run.results[0].detail
    assert demo.place_calls == []
    assert (
        "capital_bucket_requires_score_risk"
        in (await service.status()).configuration_blockers
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("execute", (False, True))
@pytest.mark.parametrize(
    "field,relaxed",
    (
        ("okx_demo_structural_extreme_risk_pct", D("0.02")),
        ("okx_demo_portfolio_max_risk_pct", D("0.05")),
        ("okx_demo_position_margin_bucket_usdt", D(600)),
        ("okx_demo_structural_20x_min_confidence", D("0.50")),
    ),
)
async def test_structural_runtime_policy_rejects_relaxed_constructor_invariant(
    execute, field, relaxed
):
    demo = FakeDemo()
    demo.equity = D(1000)
    service = adaptive_service(
        demo,
        {"BTC-USDT-SWAP": 99},
        candidates={"BTC-USDT-SWAP": structural_demo_candidate()},
        **structural_dynamic_updates(),
    )
    await service.recover()
    if execute:
        await service.arm()
    setattr(service.settings, field, relaxed)

    if execute:
        with pytest.raises(
            DemoAutomationSafetyError,
            match="demo_automation_runtime_policy_invalid",
        ):
            await service.run_once(execute=True)
    else:
        run = await service.run_once(execute=False)
        assert run.results[0].outcome == "blocked"
        assert "demo_automation_runtime_policy_invalid" in run.results[0].detail
    assert demo.place_calls == []


@pytest.mark.asyncio
async def test_structural_risk_keeps_valid_tighter_trade_and_portfolio_limits():
    demo = FakeDemo()
    demo.equity = D(1000)
    service = adaptive_service(
        demo,
        {"BTC-USDT-SWAP": 99},
        candidates={"BTC-USDT-SWAP": structural_demo_candidate()},
        **structural_dynamic_updates(),
    )
    await service.recover()
    for tier in ("low", "medium", "high", "elite", "extreme"):
        setattr(service.settings, f"okx_demo_structural_{tier}_risk_pct", D("0.004"))
    service.settings.okx_demo_score_high_risk_pct = D("0.008")
    service.settings.okx_demo_portfolio_max_risk_pct = D("0.008")

    run = await service.run_once(execute=False)
    status = await service.status()

    assert run.results[0].outcome == "approved_dry_run"
    assert run.results[0].risk_budget_pct == D("0.004")
    assert run.results[0].estimated_stop_loss_pct <= D("0.004")
    assert status.portfolio_max_risk_pct == D("0.008")
    assert {tier.risk_pct for tier in status.score_risk_tiers} == {D("0.004")}


@pytest.mark.asyncio
async def test_structural_runtime_validation_does_not_reopen_settings_sources():
    demo = FakeDemo()
    service = adaptive_service(
        demo,
        {"BTC-USDT-SWAP": 99},
        candidates={"BTC-USDT-SWAP": structural_demo_candidate()},
        **structural_dynamic_updates(),
    )
    await service.recover()

    with patch.object(
        BaseSettings,
        "_settings_build_values",
        side_effect=AssertionError("settings_sources_reopened"),
    ):
        run = await service.run_once(execute=False)

    assert run.results[0].outcome == "approved_dry_run"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "invalid"),
    (
        ("okx_demo_structural_round_trip_fee_bps", D(-1)),
        ("okx_demo_structural_dynamic_leverage_enabled", "false"),
    ),
)
async def test_runtime_field_bounds_and_types_block_structural_run(field, invalid):
    demo = FakeDemo()
    service = adaptive_service(
        demo,
        {"BTC-USDT-SWAP": 99},
        candidates={"BTC-USDT-SWAP": structural_demo_candidate()},
        **structural_dynamic_updates(),
    )
    await service.recover()
    await service.arm()
    setattr(service.settings, field, invalid)
    if field == "okx_demo_structural_round_trip_fee_bps":
        # Preserve the total modeled cost; only the Field ge=0 rule detects this.
        service.settings.okx_demo_structural_round_trip_slippage_bps += D(1)

    with (
        patch.object(
            BaseSettings,
            "_settings_build_values",
            side_effect=AssertionError("settings_sources_reopened"),
        ),
        pytest.raises(
            DemoAutomationSafetyError,
            match="demo_automation_runtime_policy_invalid",
        ),
    ):
        await service.run_once(execute=True)
    assert demo.place_calls == []


@pytest.mark.asyncio
async def test_invalid_mutated_tier_status_never_discloses_input_or_warns():
    demo = FakeDemo()
    service = adaptive_service(
        demo,
        {"BTC-USDT-SWAP": 99},
        candidates={"BTC-USDT-SWAP": structural_demo_candidate()},
        **structural_dynamic_updates(),
    )
    await service.recover()
    secret_like_value = "raw-sensitive-input"
    service.settings.okx_demo_structural_extreme_risk_pct = secret_like_value

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        status = await service.status()
        run = await service.run_once(execute=False)

    assert "demo_automation_runtime_policy_invalid" in status.configuration_blockers
    assert status.score_risk_tiers == []
    assert run.results[0].outcome == "blocked"
    assert "demo_automation_runtime_policy_invalid" in run.results[0].detail
    assert secret_like_value not in repr(status)
    assert secret_like_value not in repr(run)
    assert caught == []
    assert demo.place_calls == []


@pytest.mark.asyncio
async def test_recovery_preserves_state_with_invalid_policy_without_settings_sources():
    demo = FakeDemo()
    service = adaptive_service(
        demo,
        {"BTC-USDT-SWAP": 99},
        candidates={"BTC-USDT-SWAP": structural_demo_candidate()},
        **structural_dynamic_updates(),
    )
    await service.recover()
    await service.arm()
    restarted = adaptive_service(
        demo,
        {"BTC-USDT-SWAP": 99},
        candidates={"BTC-USDT-SWAP": structural_demo_candidate()},
        **structural_dynamic_updates(),
    )
    restarted.repository = service.repository
    restarted.settings.okx_demo_position_margin_bucket_usdt = D(600)

    with patch.object(
        BaseSettings,
        "_settings_build_values",
        side_effect=AssertionError("settings_sources_reopened"),
    ):
        await restarted.recover()
        status = await restarted.status()
    assert status.recovered is True
    assert status.armed is False
    assert status.emergency_stop is True
    assert status.last_error == "demo_automation_runtime_policy_invalid"
    assert "demo_automation_runtime_policy_invalid" in status.configuration_blockers
    assert restarted.repository.state["emergency_stop"] is True
    assert restarted.repository.state["armed"] is False
    assert restarted.repository.state["_restart_latch_required"] is True
    with pytest.raises(
        DemoAutomationSafetyError,
        match="demo_automation_runtime_policy_invalid",
    ):
        await restarted.arm()
    with pytest.raises(
        DemoAutomationSafetyError,
        match="demo_automation_runtime_policy_invalid",
    ):
        await restarted.run_once(execute=True)
    assert demo.place_calls == []


@pytest.mark.asyncio
async def test_valid_structural_relaxation_during_market_read_is_blocked_at_sizing():
    class RelaxingHub(FakeHub):
        async def snapshot(self, symbol):
            service.settings.okx_demo_structural_20x_min_confidence = D("0.65")
            return await super().snapshot(symbol)

    demo = FakeDemo()
    service = adaptive_service(
        demo,
        {"BTC-USDT-SWAP": 99},
        candidates={"BTC-USDT-SWAP": structural_demo_candidate()},
        **{
            **structural_dynamic_updates(),
            "okx_demo_structural_20x_min_confidence": D("0.95"),
        },
    )
    service.market_hub = RelaxingHub()
    await service.recover()

    run = await service.run_once(execute=False)

    assert run.results[0].outcome == "blocked"
    assert run.results[0].detail == "demo_automation_runtime_policy_invalid"
    assert demo.place_calls == []


@pytest.mark.asyncio
async def test_valid_mid_run_relaxation_cannot_change_structural_invocation_policy():
    class RelaxingDemo(FakeDemo):
        async def set_leverage(self, request):
            response = await super().set_leverage(request)
            service.settings.okx_demo_structural_20x_min_confidence = D("0.65")
            return response

    demo = RelaxingDemo()
    service = adaptive_service(
        demo,
        {"BTC-USDT-SWAP": 99},
        candidates={"BTC-USDT-SWAP": structural_demo_candidate()},
        **{
            **structural_dynamic_updates(),
            "okx_demo_structural_20x_min_confidence": D("0.95"),
        },
    )
    await service.recover()
    await service.arm()

    run = await service.run_once(execute=True)

    assert len(demo.leverage_calls) == 1
    assert demo.leverage_calls[0].leverage == 10
    assert demo.place_calls == []
    assert "demo_automation_runtime_policy_changed_before_submit" in (
        run.results[0].detail
    )


@pytest.mark.asyncio
async def test_fresh_equity_drop_rechecks_fixed_structural_stop_amount():
    class EquityDropDemo(FakeDemo):
        async def set_leverage(self, request):
            response = await super().set_leverage(request)
            self.equity = D(980)
            return response

    demo = EquityDropDemo()
    demo.equity = D(1000)
    service = adaptive_service(
        demo,
        {"BTC-USDT-SWAP": 99},
        candidates={"BTC-USDT-SWAP": structural_demo_candidate()},
        **structural_dynamic_updates(),
    )
    await service.recover()
    await service.arm()

    run = await service.run_once(execute=True)

    assert len(demo.leverage_calls) == 1
    assert demo.place_calls == []
    assert run.results[0].order_submission_attempted is False
    assert "structural_leverage_policy_changed_before_submit" in (run.results[0].detail)


@pytest.mark.asyncio
async def test_allowed_fresh_equity_change_updates_submitted_risk_evidence():
    class SmallEquityDropDemo(FakeDemo):
        async def set_leverage(self, request):
            response = await super().set_leverage(request)
            self.equity = D(995)
            return response

    demo = SmallEquityDropDemo()
    demo.equity = D(1000)
    service = adaptive_service(
        demo,
        {"BTC-USDT-SWAP": 99},
        candidates={"BTC-USDT-SWAP": structural_demo_candidate()},
        **structural_dynamic_updates(),
    )
    await service.recover()
    await service.arm()

    run = await service.run_once(execute=True)
    status = await service.status()

    assert run.results[0].outcome == "submitted"
    assert len(demo.place_calls) == 1
    assert len(status.active_trades) == 1
    hold = status.active_trades[0]
    assert run.results[0].estimated_stop_loss_pct == (
        hold.estimated_stop_loss_amount / D(995)
    )
    assert run.results[0].estimated_stop_loss_pct == hold.estimated_stop_loss_pct
    assert run.results[0].margin_allocation_pct == hold.margin_allocation_pct


@pytest.mark.asyncio
async def test_fresh_equity_drop_rechecks_existing_fixed_stop_amounts():
    class ThirdOrderEquityDropDemo(FakeDemo):
        async def set_leverage(self, request):
            response = await super().set_leverage(request)
            if len(self.leverage_calls) == 3:
                self.equity = D(980)
            return response

    symbols = ("BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP")
    demo = ThirdOrderEquityDropDemo()
    demo.equity = D(1000)
    updates = structural_dynamic_updates()
    for tier in ("low", "medium", "high", "elite", "extreme"):
        updates[f"okx_demo_structural_{tier}_risk_pct"] = D("0.0034")
    service = adaptive_service(
        demo,
        {symbol: 99 for symbol in symbols},
        candidates={symbol: structural_demo_candidate() for symbol in symbols},
        **updates,
    )
    await service.recover()
    await service.arm()

    run = await service.run_once(execute=True)

    assert [item.outcome for item in run.results] == [
        "submitted",
        "submitted",
        "error",
    ]
    assert "structural_leverage_policy_changed_before_submit" in run.results[2].detail
    assert run.results[2].order_submission_attempted is False
    assert len(demo.place_calls) == 2


@pytest.mark.asyncio
async def test_bucket_cap_uses_usdt_equity_instead_of_unrelated_assets():
    demo = FakeDemo()
    service = service_for(demo, ("BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP"))
    demo.other_asset_equity = D(100000)
    await service.recover()
    result = await service.run_once(execute=False)
    assert result.total_equity == D(101000)
    assert result.risk_equity == D(1000)
    assert result.portfolio_estimated_margin == D(600)
    assert result.results[-1].outcome == "blocked"


@pytest.mark.asyncio
@pytest.mark.parametrize("late_change", ("equity", "available", "untracked"))
async def test_fresh_after_leverage_account_can_veto_unchanged_bucket_order(
    late_change,
):
    class ChangedAccount(FakeDemo):
        async def set_leverage(self, request):
            result = await super().set_leverage(request)
            if late_change == "equity":
                self.equity = D(400)
            elif late_change == "available":
                self.available_equity = D(299)
            else:
                self.positions.append(self._position("ETH-USDT-SWAP", "long"))
            return result

    demo = ChangedAccount()
    service = service_for(demo)
    await service.recover()
    await service.arm()
    result = await service.run_once(execute=True)
    assert len(demo.leverage_calls) == 1
    assert demo.place_calls == []
    assert result.results[0].order_submission_attempted is False
    expected = (
        "untracked_exchange_exposure_detected"
        if late_change == "untracked"
        else "automation_locked"
        if late_change == "equity"
        else "portfolio_margin_limit_changed_before_submit"
    )
    assert expected in result.results[0].detail


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "late_change",
    (
        "cap",
        "local_hold",
        "disable_score_risk",
        "relax_cap",
        "malformed_inventory",
        "sparse_legacy_hold",
        "zero_margin_hold",
    ),
)
async def test_last_await_guard_rechecks_cap_and_new_local_exposure(late_change):
    class ChangedBeforePost(FakeDemo):
        async def place_order(self, request, *, before_submit=None):
            if late_change == "cap":
                service.settings.okx_demo_portfolio_max_margin_pct = D("0.20")
            elif late_change == "malformed_inventory":
                service._state["active_trades"]["corrupt"] = {"not_a_trade": True}
            elif late_change == "sparse_legacy_hold":
                service._state["active_trades"]["ETH-USDT-SWAP"] = {
                    "instrument_id": "ETH-USDT-SWAP",
                    "started_at": datetime.now(UTC).isoformat(),
                }
            else:
                hold = tracked_trade("ETH-USDT-SWAP", started_at=datetime.now(UTC))
                service._set_active_trade(
                    hold.model_copy(
                        update={
                            "estimated_margin": D(0)
                            if late_change == "zero_margin_hold"
                            else D(400),
                            "margin_allocation_pct": D("0.001"),
                        }
                    )
                )
                if late_change == "disable_score_risk":
                    service.settings.okx_demo_score_risk_enabled = False
                if late_change == "relax_cap":
                    service.settings.okx_demo_portfolio_max_margin_pct = D("0.90")
            return await super().place_order(request, before_submit=before_submit)

    demo = ChangedBeforePost()
    service = service_for(demo)
    await service.recover()
    await service.arm()
    result = await service.run_once(execute=True)
    assert demo.place_calls == []
    assert result.results[0].order_submission_attempted is False
    if late_change in {"malformed_inventory", "zero_margin_hold"}:
        expected = "portfolio_state_invalid"
        assert service._state["emergency_stop"] is True
        assert service._state["active_trades"]
    elif late_change == "sparse_legacy_hold":
        expected = "portfolio_margin_inventory_invalid"
    elif late_change == "disable_score_risk":
        expected = "capital_bucket_requires_score_risk"
    elif late_change in {"cap", "relax_cap"}:
        expected = "demo_automation_runtime_policy_invalid"
    else:
        expected = "portfolio_margin_limit_changed_before_submit"
    assert expected in result.results[0].detail


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change,expected",
    (
        ("size", "portfolio_margin_position_changed"),
        ("leverage", "portfolio_margin_position_changed"),
        ("missing_margin", "portfolio_margin_source_missing"),
        ("invalid_margin", "portfolio_margin_source_missing"),
        ("missing_fx", "portfolio_margin_source_missing"),
        ("wrong_currency", "portfolio_margin_source_missing"),
        ("depeg", "portfolio_margin_limit_changed_before_submit"),
        ("increased_margin", "portfolio_margin_limit_changed_before_submit"),
        ("extra_algo", "portfolio_pending_algos_unresolved"),
        ("protection_missing", "tracked_position_protection_missing_or_mismatched"),
    ),
)
async def test_existing_symbol_changes_after_leverage_cannot_hide_in_inventory(
    change, expected
):
    class Changed(FakeDemo):
        async def set_leverage(self, request):
            result = await super().set_leverage(request)
            if len(self.leverage_calls) == 2:
                position = self.positions[0]
                if change == "size":
                    self.positions[0] = position.model_copy(
                        update={"size": position.size * 100}
                    )
                elif change == "leverage":
                    self.positions[0] = position.model_copy(update={"leverage": D(1)})
                elif change in {"missing_margin", "invalid_margin", "increased_margin"}:
                    value = {
                        "missing_margin": "",
                        "invalid_margin": "NaN",
                        "increased_margin": "601",
                    }[change]
                    self.positions[0] = position.model_copy(
                        update={"raw": {**position.raw, "imr": value}}
                    )
                elif change in {"missing_fx", "wrong_currency", "depeg"}:
                    key, value = {
                        "missing_fx": ("usdPx", ""),
                        "wrong_currency": ("ccy", "BTC"),
                        "depeg": ("usdPx", "0.5"),
                    }[change]
                    self.positions[0] = position.model_copy(
                        update={"raw": {**position.raw, key: value}}
                    )
                elif change == "protection_missing":
                    self.protection_present = False
            return result

        async def reconcile(self):
            snapshot = await super().reconcile()
            if change == "extra_algo" and len(self.leverage_calls) == 2:
                snapshot.pending_algo_orders.append(
                    snapshot.pending_algo_orders[0].model_copy(
                        update={
                            "algo_order_id": "other-order",
                            "client_algo_order_id": "unknown-client",
                        }
                    )
                )
            return snapshot

    demo = Changed()
    service = service_for(demo, ("BTC-USDT-SWAP", "ETH-USDT-SWAP"))
    await service.recover()
    await service.arm()
    result = await service.run_once(execute=True)
    assert len(demo.place_calls) == 1
    assert result.results[0].outcome == "submitted"
    assert result.results[1].order_submission_attempted is False
    assert expected in result.results[1].detail
    assert len(demo.positions) == 1  # A failed recheck never auto-closes exposure.


@pytest.mark.asyncio
async def test_existing_bucket_exposure_above_aggregate_cap_latches_estop():
    demo = FakeDemo()
    service = service_for(demo)
    await service.recover()
    await service.arm()
    prime_usdt_equity_basis(service, demo)
    demo.positions.append(demo._position("ETH-USDT-SWAP", "long"))
    hold = tracked_trade("ETH-USDT-SWAP", started_at=datetime.now(UTC))
    service._set_active_trade(
        hold.model_copy(
            update={
                "estimated_margin": D(601),
                "estimated_notional": D(1803),
                "estimated_stop_loss_amount": D(1),
            }
        )
    )
    result = await service.run_once(execute=True)
    assert result.results[0].outcome == "locked"
    assert "active_portfolio_margin_limit_exceeded" in result.results[0].reason_codes
    assert (await service.status()).emergency_stop is True
    assert demo.place_calls == []
