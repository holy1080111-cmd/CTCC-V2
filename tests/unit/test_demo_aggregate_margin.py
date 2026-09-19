"""Aggregate bucket margin acceptance; synthetic account, no exchange requests."""

from datetime import UTC, datetime
from decimal import Decimal as D

import pytest

from tests.unit.test_demo_automation import (
    FakeDemo,
    adaptive_service,
    candidate,
    prime_usdt_equity_basis,
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
