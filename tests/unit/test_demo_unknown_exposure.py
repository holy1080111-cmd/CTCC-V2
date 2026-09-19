"""Malformed durable holdings must never be projected as a flat account."""

from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.database.repositories.demo_automation import DemoAutomationRepository
from app.demo_automation import DemoAutomationSafetyError
from tests.unit.test_demo_automation import (
    FakeDemo,
    MemoryAutomationRepository,
    tracked_trade,
)
from tests.unit.test_demo_loss_hard_gates import configured


def raw_trade(symbol="BTC-USDT-SWAP"):
    return tracked_trade(symbol, started_at=datetime.now(UTC)).model_dump(mode="json")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raw", [None, [], "unknown", {"ETH-USDT-SWAP": {"contracts": "unknown"}}]
)
async def test_corrupt_exposure_survives_restart_and_cannot_clear_stop(raw):
    demo = FakeDemo()
    repository = MemoryAutomationRepository()
    service = configured(demo, repository=repository)
    service._state["active_trades"] = deepcopy(raw)
    service._state["active_instrument_id"] = "unresolved-legacy-instrument"
    await service._persist_state(required=True)
    for _ in range(2):
        service = configured(demo, repository=repository)
        await service.recover()
        assert service._state["active_trades"] == raw
        assert repository.state["active_trades"] == raw
        assert service._state["active_instrument_id"] == "unresolved-legacy-instrument"
        assert service._state["emergency_stop"] is True
        assert service._state["armed"] is False
        assert "portfolio_state_invalid" in service._state["lock_reasons"]
        with pytest.raises(DemoAutomationSafetyError, match="portfolio_state_invalid"):
            await service.status()
        with pytest.raises(DemoAutomationSafetyError, match="portfolio_state_invalid"):
            await service.clear_emergency_stop()
        with pytest.raises(
            DemoAutomationSafetyError, match="emergency_stop_must_be_cleared"
        ):
            await service.arm()
    assert demo.place_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("corruption", ["row", "key", "duplicate_client", "naive_time"])
async def test_partial_inventory_is_not_returned_or_rewritten(corruption):
    demo = FakeDemo()
    repository = MemoryAutomationRepository()
    service = configured(demo, repository=repository)
    first, second = raw_trade(), raw_trade("ETH-USDT-SWAP")
    raw = {first["instrument_id"]: first, second["instrument_id"]: second}
    if corruption == "row":
        second["contracts"] = "not-a-number"
    elif corruption == "key":
        second["instrument_id"] = first["instrument_id"]
    elif corruption == "duplicate_client":
        second["client_order_id"] = first["client_order_id"]
    else:
        second["started_at"] = "2020-01-01T00:00:00"
    service._state["active_trades"] = deepcopy(raw)
    await service._persist_state(required=True)
    await service.recover()
    assert service._state["active_trades"] == raw
    assert repository.state["active_trades"] == raw
    with pytest.raises(DemoAutomationSafetyError, match="portfolio_state_invalid"):
        service._active_trades()
    with pytest.raises(DemoAutomationSafetyError, match="portfolio_state_invalid"):
        service._remove_active_trade(first["instrument_id"])
    with pytest.raises(DemoAutomationSafetyError, match="portfolio_state_invalid"):
        service._set_active_trade(
            tracked_trade("SOL-USDT-SWAP", started_at=datetime.now(UTC))
        )
    assert service._state["active_trades"] == raw
    assert demo.place_calls == []


@pytest.mark.asyncio
async def test_late_corruption_is_latched_before_arm_and_no_flat_view_is_available():
    demo = FakeDemo()
    service = configured(demo)
    await service.recover()
    service._state["active_trades"] = {"BTC-USDT-SWAP": {"contracts": "unknown"}}
    with pytest.raises(DemoAutomationSafetyError, match="portfolio_state_invalid"):
        await service.arm()
    assert service._state["emergency_stop"] is True
    assert service._state["armed"] is False
    assert demo.place_calls == []


@pytest.mark.asyncio
async def test_corruption_latch_cannot_be_erased_by_replacing_inventory_with_empty():
    service = configured(FakeDemo())
    service._state["active_trades"] = []
    await service.recover()
    service._state["active_trades"] = {}
    with pytest.raises(DemoAutomationSafetyError, match="portfolio_state_invalid"):
        await service.clear_emergency_stop()
    assert service._state["emergency_stop"] is True


@pytest.mark.asyncio
async def test_legacy_holding_without_start_time_is_preserved_without_invented_timestamp():
    repository = MemoryAutomationRepository()
    service = configured(FakeDemo(), repository=repository)
    service._state["active_instrument_id"] = "BTC-USDT-SWAP"
    await service.recover()
    assert service._state["active_started_at"] is None
    assert service._state["active_instrument_id"] == "BTC-USDT-SWAP"
    assert service._state["emergency_stop"] is True
    with pytest.raises(DemoAutomationSafetyError, match="portfolio_state_invalid"):
        await service.clear_emergency_stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", [None, [], {}, "unknown"])
async def test_repository_does_not_coerce_json_corruption_before_validation(raw):
    state = configured(FakeDemo())._state
    row = SimpleNamespace(**deepcopy(state))
    row.active_trades = deepcopy(raw)
    row.realized_pnl_events = deepcopy(raw)

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, *args):
            return row

    recovered = await DemoAutomationRepository(Session).load_state()
    assert recovered["active_trades"] == raw
    assert type(recovered["active_trades"]) is type(raw)
    assert recovered["realized_pnl_events"] == raw
    assert type(recovered["realized_pnl_events"]) is type(raw)
