from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.config.settings import Settings
from app.demo_automation import DemoAutomationSafetyError
from app.demo_automation.service import SafeDemoAutomation
from app.domain.market import InstrumentInfo
from app.domain.okx_demo import (
    OkxDemoAccountConfig,
    OkxDemoBalanceSnapshot,
    OkxDemoOrderAcknowledgement,
    OkxDemoReconcileResult,
    OkxDemoWriteResult,
)
from app.domain.realtime import RealtimeSnapshot, RealtimeStatus
from app.domain.strategy import StrategyDecision, TradeCandidate


class FakeStrategy:
    def __init__(self, selected: TradeCandidate | None) -> None:
        self.selected = selected

    async def evaluate(self, symbol: str, _limit: int) -> StrategyDecision:
        return StrategyDecision(
            symbol=symbol,
            instrument_id=symbol,
            decision=self.selected.direction if self.selected else "no_trade",
            selected_strategy=self.selected.strategy if self.selected else None,
            selected_candidate=self.selected,
            minimum_score=72,
            evaluations=[],
            blockers=[] if self.selected else ["no_signal"],
            generated_at=datetime.now(timezone.utc),
            version="1.5.0",
        )


class FakeDemo:
    def __init__(self, *, exposed: bool = False) -> None:
        self.exposed = exposed
        self.place_calls = []
        self.leverage_calls = []
        self.equity = Decimal("10000")

    async def reconcile(self) -> OkxDemoReconcileResult:
        positions = []
        if self.exposed:
            from app.domain.okx_demo import OkxDemoPositionView

            positions = [
                OkxDemoPositionView(
                    instrument_id="BTC-USDT-SWAP",
                    position_side="long",
                    size=Decimal("1"),
                    available_size=Decimal("1"),
                    unrealized_pnl=Decimal("0"),
                )
            ]
        return OkxDemoReconcileResult(
            account_config=OkxDemoAccountConfig(position_mode="net_mode"),
            balance=OkxDemoBalanceSnapshot(
                total_equity=self.equity,
                isolated_equity=Decimal("0"),
                adjusted_equity=self.equity,
                available_equity=self.equity,
            ),
            positions=positions,
            pending_orders=[],
            recent_orders=[],
            pending_algo_orders=[],
            persisted=True,
        )

    async def set_leverage(self, request):
        self.leverage_calls.append(request)
        return OkxDemoWriteResult(action="set_leverage", acknowledged=True)

    async def place_order(self, request):
        self.place_calls.append(request)
        return OkxDemoWriteResult(
            action="place_order",
            acknowledged=True,
            acknowledgement=OkxDemoOrderAcknowledgement(order_id="123", client_order_id=request.client_order_id),
        )


class FakePublic:
    async def instruments(self, instrument_id: str):
        return [
            InstrumentInfo(
                symbol=instrument_id,
                instrument_id=instrument_id,
                instrument_type="SWAP",
                state="live",
                tick_size=Decimal("0.1"),
                lot_size=Decimal("1"),
                minimum_size=Decimal("1"),
                contract_value=Decimal("1"),
                contract_currency="BTC",
            )
        ]


class FakeHub:
    async def snapshot(self, symbol: str):
        return RealtimeSnapshot(
            symbol=symbol,
            last=Decimal("100"),
            received_at=datetime.now(timezone.utc),
        )


class FakeClient:
    def status(self):
        return RealtimeStatus(
            enabled=True,
            running=True,
            connected=True,
            endpoint="wss://example.test",
            symbols=["BTC-USDT-SWAP"],
            paper_auto_ticks=False,
        )


def configured_settings(**updates) -> Settings:
    values = dict(
        environment="test",
        trading_mode="okx_demo",
        auto_trade=False,
        live_trading=False,
        paper_auto_execution=False,
        okx_ws_enabled=True,
        okx_demo_enabled=True,
        okx_demo_allow_order_writes=True,
        okx_demo_api_key="key",
        okx_demo_api_secret="secret",
        okx_demo_api_passphrase="pass",
        okx_demo_auto_execution=True,
        okx_demo_allowed_symbols="BTC-USDT-SWAP",
        okx_demo_scan_symbols="BTC-USDT-SWAP",
        okx_demo_max_order_size_contracts=Decimal("1"),
        okx_demo_max_trades_per_day=3,
    )
    values.update(updates)
    return Settings(_env_file=None, **values)


def candidate() -> TradeCandidate:
    return TradeCandidate(
        strategy="trend_pullback",
        direction="long",
        score=82,
        entry=Decimal("100"),
        stop_loss=Decimal("95"),
        take_profit=Decimal("110"),
        risk_reward=Decimal("2"),
        invalidation="stop",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
        reasons=["unit-test"],
    )


def make_service(demo: FakeDemo, **setting_updates) -> SafeDemoAutomation:
    return SafeDemoAutomation(
        settings=configured_settings(**setting_updates),
        strategy_service=FakeStrategy(candidate()),
        demo_service=demo,
        public_client=FakePublic(),
        market_hub=FakeHub(),
        market_client=FakeClient(),
        repository=None,
    )


@pytest.mark.asyncio
async def test_dry_run_never_places_demo_order() -> None:
    demo = FakeDemo()
    service = make_service(demo)
    await service.recover()
    run = await service.run_once(execute=False)
    assert run.results[0].outcome == "approved_dry_run"
    assert demo.place_calls == []


@pytest.mark.asyncio
async def test_execute_requires_explicit_arming() -> None:
    service = make_service(FakeDemo())
    await service.recover()
    with pytest.raises(DemoAutomationSafetyError, match="not_armed"):
        await service.run_once(execute=True)


@pytest.mark.asyncio
async def test_arm_rejects_existing_exchange_exposure() -> None:
    service = make_service(FakeDemo(exposed=True))
    await service.recover()
    with pytest.raises(DemoAutomationSafetyError, match="exposure"):
        await service.arm()


@pytest.mark.asyncio
async def test_armed_execute_places_one_protected_demo_order() -> None:
    demo = FakeDemo()
    service = make_service(demo)
    await service.recover()
    await service.arm()
    run = await service.run_once(execute=True)
    assert run.results[0].outcome == "submitted"
    assert len(demo.place_calls) == 1
    request = demo.place_calls[0]
    assert request.size == Decimal("1")
    assert request.stop_loss == Decimal("95")
    assert request.take_profit == Decimal("110")
    assert request.confirmation == "OKX_DEMO_ONLY"


@pytest.mark.asyncio
async def test_emergency_stop_disarms_and_locks() -> None:
    service = make_service(FakeDemo())
    await service.recover()
    await service.arm()
    status = await service.emergency_stop()
    assert status.armed is False
    assert status.emergency_stop is True
    assert status.locked is True
    assert "emergency_stop_engaged" in status.lock_reasons


@pytest.mark.asyncio
async def test_daily_trade_count_lock_blocks_execution() -> None:
    demo = FakeDemo()
    service = make_service(demo, okx_demo_max_trades_per_day=1)
    await service.recover()
    await service.arm()
    service._state["trades_today"] = 1
    run = await service.run_once(execute=True)
    assert run.results[0].outcome == "locked"
    assert "daily_trade_count_limit_reached" in run.results[0].reason_codes
    assert demo.place_calls == []
