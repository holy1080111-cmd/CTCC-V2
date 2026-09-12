"""Offline revocation tests against the real Demo service and synthetic IO.

These verify existing write authority only, NOT G1--G12 runtime admission.
"""

import asyncio
from decimal import Decimal

import pytest

from app.domain.okx_demo import (
    OkxDemoCancelRequest,
    OkxDemoCloseRequest,
    OkxDemoLeverageRequest,
)
from app.okx_demo import OkxDemoSafetyError
from app.okx_demo.service import OkxDemoService
from tests.unit.test_okx_demo_service import FakePrivate, FakePublic, request, settings

INSTRUMENT = "BTC-USDT-SWAP"
CONFIRMATION = "OKX_DEMO_ONLY"
WRITES = ("place_order", "cancel_order", "close_position", "set_leverage")


def command(action):
    common = {"instrument_id": INSTRUMENT, "confirmation": CONFIRMATION}
    if action == "place_order":
        return request()
    if action == "cancel_order":
        return OkxDemoCancelRequest(**common, order_id="synthetic-order")
    if action == "close_position":
        return OkxDemoCloseRequest(**common)
    return OkxDemoLeverageRequest(**common, leverage=3)


def revoke(config, kind):
    if kind == "symbol":
        config.okx_demo_allowed_symbols = "ETH-USDT-SWAP"
    else:
        name, value = {
            "writes": ("okx_demo_allow_order_writes", False),
            "enabled": ("okx_demo_enabled", False),
            "mode": ("trading_mode", "analysis_only"),
            "paper": ("paper_auto_execution", True),
            "live": ("live_trading", True),
            "auto": ("auto_trade", True),
            "leverage_cap": ("okx_demo_max_leverage", 1),
        }[kind]
        setattr(config, name, value)


class RecordingPrivate(FakePrivate):
    def __init__(self):
        super().__init__(
            positions=[
                {
                    "instId": INSTRUMENT,
                    "posSide": "net",
                    "pos": "0.1",
                    "availPos": "0.1",
                    "avgPx": "100000",
                    "markPx": "100000",
                    "upl": "0",
                    "lever": "3",
                    "mgnMode": "cross",
                }
            ]
        )
        self.write_calls = []
        self.read_calls = []
        self.after_config = None
        self.after_positions = None

    async def account_config(self):
        self.read_calls.append("config")
        result = await super().account_config()
        if self.after_config is not None:
            self.after_config()
        return result

    async def positions(self, instrument_id=None):
        self.read_calls.append("positions")
        result = await super().positions(instrument_id)
        if self.after_positions is not None:
            self.after_positions()
        return result

    async def place_order(self, payload):
        self.write_calls.append(("place_order", payload.copy()))
        return await super().place_order(payload)

    async def cancel_order(self, payload):
        self.write_calls.append(("cancel_order", payload.copy()))
        return await super().cancel_order(payload)

    async def close_position(self, payload):
        self.write_calls.append(("close_position", payload.copy()))
        return await super().close_position(payload)

    async def set_leverage(self, payload):
        self.write_calls.append(("set_leverage", payload.copy()))
        return await super().set_leverage(payload)


@pytest.mark.asyncio
@pytest.mark.parametrize("action", WRITES)
@pytest.mark.parametrize(
    "kind", ("writes", "enabled", "mode", "symbol", "paper", "live", "auto")
)
async def test_all_demo_writes_recheck_after_real_lock_wait(action, kind):
    config = settings()
    private = RecordingPrivate()
    service = OkxDemoService(private, FakePublic(), None, settings=config)
    await service._lock.acquire()
    task = asyncio.create_task(getattr(service, action)(command(action)))
    try:
        await asyncio.sleep(0)
        assert not task.done()
        assert private.read_calls == private.write_calls == []
        revoke(config, kind)
    finally:
        service._lock.release()
    with pytest.raises(OkxDemoSafetyError):
        await task
    assert private.read_calls == private.write_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ("close_position", "set_leverage"))
@pytest.mark.parametrize(
    "kind", ("writes", "enabled", "mode", "symbol", "paper", "live", "auto")
)
async def test_maintenance_writes_recheck_after_final_read(action, kind):
    config = settings()
    private = RecordingPrivate()
    callback = lambda: revoke(config, kind)
    if action == "close_position":
        private.after_positions = callback
    else:
        private.after_config = callback
    service = OkxDemoService(private, FakePublic(), None, settings=config)
    with pytest.raises(OkxDemoSafetyError):
        await getattr(service, action)(command(action))
    assert private.read_calls
    assert private.write_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("when", ("lock_wait", "final_read"))
async def test_leverage_cap_is_rechecked_before_write(when):
    config = settings()
    private = RecordingPrivate()
    service = OkxDemoService(private, FakePublic(), None, settings=config)
    if when == "lock_wait":
        await service._lock.acquire()
        task = asyncio.create_task(service.set_leverage(command("set_leverage")))
        await asyncio.sleep(0)
        config.okx_demo_max_leverage = 1
        service._lock.release()
    else:
        private.after_config = lambda: revoke(config, "leverage_cap")
        task = service.set_leverage(command("set_leverage"))
    with pytest.raises(
        OkxDemoSafetyError, match="requested_leverage_exceeds_demo_safety_cap"
    ):
        await task
    assert private.write_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("action", WRITES)
async def test_existing_demo_write_operations_remain_available_when_authorized(action):
    private = RecordingPrivate()
    if action == "place_order":
        private.position_rows = []
    service = OkxDemoService(private, FakePublic(), None, settings=settings())
    result = await getattr(service, action)(command(action))
    assert result.acknowledged
    assert len(private.write_calls) == 1
    assert private.write_calls[0][0] == action
    if action == "set_leverage":
        assert Decimal(private.write_calls[0][1]["lever"]) == Decimal(3)


@pytest.mark.asyncio
@pytest.mark.parametrize("when", ("final_read", "before_submit"))
@pytest.mark.parametrize("kind", ("size_cap", "position_cap", "protection"))
async def test_entry_rechecks_current_policy_after_last_await_and_callback(when, kind):
    config = settings(okx_demo_max_open_positions=2, okx_demo_require_protection=False)
    private = RecordingPrivate()
    private.position_rows = []
    order = request()
    if kind == "position_cap":
        private.position_rows = [
            {"instId": "ETH-USDT-SWAP", "posSide": "net", "pos": "1", "availPos": "1"}
        ]
    if kind == "protection":
        order = request(stop_loss=None, take_profit=None)

    def change_policy():
        name, value = {
            "size_cap": ("okx_demo_max_order_size_contracts", Decimal("0.05")),
            "position_cap": ("okx_demo_max_open_positions", 1),
            "protection": ("okx_demo_require_protection", True),
        }[kind]
        setattr(config, name, value)

    class Public(FakePublic):
        async def ticker(self, instrument_id):
            result = await super().ticker(instrument_id)
            if when == "final_read":
                change_policy()
            return result

    service = OkxDemoService(private, Public(), None, settings=config)
    with pytest.raises(OkxDemoSafetyError):
        await service.place_order(
            order, before_submit=change_policy if when == "before_submit" else None
        )
    assert private.write_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind", ("writes", "enabled", "mode", "symbol", "paper", "live", "auto")
)
async def test_entry_callback_cannot_retain_revoked_write_authority(kind):
    config = settings()
    private = RecordingPrivate()
    private.position_rows = []
    service = OkxDemoService(private, FakePublic(), None, settings=config)
    with pytest.raises(OkxDemoSafetyError):
        await service.place_order(request(), before_submit=lambda: revoke(config, kind))
    assert private.write_calls == []
