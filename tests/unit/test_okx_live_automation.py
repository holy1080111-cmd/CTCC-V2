from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.config.settings import Settings
from app.domain.market import InstrumentInfo
from app.domain.okx_live import (
    LIVE_AUTOMATION_EXECUTE_PHRASE,
    OkxLiveAutomationRunRequest,
    OkxLiveOrderAcknowledgement,
    OkxLiveWriteResult,
)
from app.domain.realtime import RealtimeSnapshot
from app.domain.strategy import TradeCandidate
from app.okx_live import OkxLiveSafetyError
from app.okx_live.automation import ControlledLiveAutomation


D = Decimal


def settings() -> Settings:
    return Settings(
        _env_file=None,
        environment="production",
        trading_mode="live",
        live_trading=True,
        okx_live_enabled=True,
        okx_live_allow_order_writes=True,
        okx_live_auto_execution=True,
        okx_live_api_key="key",
        okx_live_api_secret="secret",
        okx_live_api_passphrase="passphrase",
        api_token="x" * 40,
        web_concurrency=1,
        okx_ws_enabled=True,
        okx_live_scan_initial_delay_seconds=0,
        okx_live_max_order_size_contracts="1",
    )


def candidate() -> TradeCandidate:
    return TradeCandidate(
        strategy="trend_pullback",
        direction="long",
        score=90,
        entry=D("100000"),
        stop_loss=D("99000"),
        take_profit=D("102000"),
        risk_reward=D("2"),
        invalidation="test",
        expires_at=datetime(2026, 8, 10, tzinfo=timezone.utc),
    )


class FakeLiveService:
    def __init__(self) -> None:
        self.armed = True
        self.emergency_stop = False
        self.orders = []
        self.running_values = []
        self.exposure = False

    def arm_status(self):
        return SimpleNamespace(
            armed=self.armed,
            emergency_stop=self.emergency_stop,
        )

    def set_automation_running(self, value):
        self.running_values.append(value)

    async def reconcile(self):
        positions = []
        if self.exposure:
            positions = [SimpleNamespace(size=D("0.1"))]
        return SimpleNamespace(
            balance=SimpleNamespace(total_equity=D("10000")),
            positions=positions,
            pending_orders=[],
            pending_algo_orders=[],
        )

    async def place_order(self, request):
        self.orders.append(request)
        self.armed = False
        return OkxLiveWriteResult(
            action="place_order",
            accepted=True,
            final_state_confirmed=True,
            acknowledgement=OkxLiveOrderAcknowledgement(
                order_id="live-order-1",
                client_order_id=request.client_order_id,
            ),
        )


class FakeStrategy:
    def __init__(self, selected: TradeCandidate | None = None) -> None:
        self.selected = selected or candidate()

    async def evaluate(self, instrument_id, candle_limit):
        return SimpleNamespace(
            symbol=instrument_id,
            selected_candidate=self.selected,
            blockers=[],
        )


class FakeRisk:
    def __init__(self) -> None:
        self.candidates: list[TradeCandidate] = []

    def evaluate(self, candidate, account, limits):
        self.candidates.append(candidate)
        return SimpleNamespace(
            decision="approved",
            approved_quantity=D("0.001"),
            reason_codes=[],
        )


class FakePublic:
    def __init__(self, *, tick_size: Decimal = D("0.1")) -> None:
        self.tick_size = tick_size

    async def instruments(self, instrument_id):
        return [
            InstrumentInfo(
                symbol=instrument_id,
                instrument_id=instrument_id,
                instrument_type="SWAP",
                state="live",
                tick_size=self.tick_size,
                lot_size=D("0.1"),
                minimum_size=D("0.1"),
                contract_value=D("0.01"),
                contract_currency="BTC",
            )
        ]


class FakeHub:
    def __init__(
        self,
        *,
        last: Decimal = D("100000"),
        bid: Decimal = D("99999.9"),
        ask: Decimal = D("100000.1"),
        mark: Decimal = D("100000"),
        quote_age_seconds: int = 0,
        mark_age_seconds: int = 0,
    ) -> None:
        now = datetime.now(timezone.utc)
        self.value = RealtimeSnapshot(
            symbol="BTC-USDT-SWAP",
            last=last,
            bid=bid,
            ask=ask,
            mark_price=mark,
            last_received_at=now,
            quote_received_at=now - timedelta(seconds=quote_age_seconds),
            mark_price_received_at=now - timedelta(seconds=mark_age_seconds),
            received_at=now,
        )

    async def snapshot(self, symbol):
        return self.value.model_copy(update={"symbol": symbol})


class FakeMarketClient:
    def __init__(self, connected=True) -> None:
        self.connected = connected

    def status(self):
        return SimpleNamespace(connected=self.connected)


def automation(
    live: FakeLiveService,
    *,
    connected=True,
    hub: FakeHub | None = None,
    risk: FakeRisk | None = None,
    public: FakePublic | None = None,
    selected_candidate: TradeCandidate | None = None,
):
    return ControlledLiveAutomation(
        live,
        settings=settings(),
        strategy_service=FakeStrategy(selected_candidate),
        risk_service=risk or FakeRisk(),
        public_client=public or FakePublic(),
        market_hub=hub or FakeHub(),
        market_client=FakeMarketClient(connected),
    )


def test_execute_request_requires_second_explicit_confirmation() -> None:
    with pytest.raises(ValidationError):
        OkxLiveAutomationRunRequest(execute=True)

    request = OkxLiveAutomationRunRequest(
        execute=True,
        confirmation=LIVE_AUTOMATION_EXECUTE_PHRASE,
    )
    assert request.execute is True


@pytest.mark.asyncio
async def test_dry_run_never_calls_live_order_service() -> None:
    live = FakeLiveService()
    result = await automation(live, connected=False).run_once(execute=False)

    assert result.results[0].outcome == "approved_dry_run"
    assert result.results[0].approved_contracts == D("0.1")
    assert live.orders == []


@pytest.mark.asyncio
async def test_execute_submits_exactly_one_protected_order_and_consumes_arm() -> None:
    live = FakeLiveService()
    result = await automation(live).run_once(execute=True)

    assert [item.outcome for item in result.results] == ["submitted"]
    assert len(live.orders) == 1
    order = live.orders[0]
    assert order.stop_loss == D("99000")
    assert order.take_profit == D("102000")
    assert result.results[0].reference_price == D("100000.1")
    assert order.trigger_price_type == "mark"
    assert order.client_order_id.startswith("CTCCL")
    assert len(order.client_order_id) == 32
    assert live.armed is False


@pytest.mark.asyncio
async def test_live_market_sell_uses_fresh_bid_as_risk_reference() -> None:
    live = FakeLiveService()
    short = candidate().model_copy(
        update={
            "direction": "short",
            "stop_loss": D("101000"),
            "take_profit": D("98000"),
            "risk_reward": D("2"),
        }
    )
    worker = automation(
        live,
        hub=FakeHub(bid=D("99999.8")),
        selected_candidate=short,
    )

    result = await worker.run_once(
        symbols=["BTC-USDT-SWAP"],
        execute=True,
    )

    assert result.results[0].outcome == "submitted"
    assert result.results[0].reference_price == D("99999.8")
    assert len(live.orders) == 1
    assert live.orders[0].direction == "short"


@pytest.mark.asyncio
async def test_fresh_mark_update_does_not_hide_stale_executable_quote() -> None:
    live = FakeLiveService()
    worker = automation(
        live,
        hub=FakeHub(quote_age_seconds=120, mark_age_seconds=0),
    )

    result = await worker.run_once(execute=True)

    assert result.results[0].outcome == "blocked"
    assert result.results[0].detail == "realtime_executable_quote_stale"
    assert live.orders == []


@pytest.mark.asyncio
async def test_mark_to_execution_basis_above_limit_fails_closed() -> None:
    live = FakeLiveService()
    worker = automation(
        live,
        hub=FakeHub(mark=D("99000")),
    )

    result = await worker.run_once(execute=True)

    assert result.results[0].outcome == "blocked"
    assert result.results[0].detail == "mark_execution_basis_exceeds_live_limit"
    assert live.orders == []


@pytest.mark.asyncio
async def test_live_risk_uses_tick_aligned_protection_geometry() -> None:
    live = FakeLiveService()
    risk = FakeRisk()
    worker = automation(
        live,
        connected=False,
        hub=FakeHub(bid=D("100000"), ask=D("100000")),
        risk=risk,
        public=FakePublic(tick_size=D("700")),
    )

    result = await worker.run_once(
        symbols=["BTC-USDT-SWAP"],
        execute=False,
    )

    assert result.results[0].outcome == "approved_dry_run"
    assert len(risk.candidates) == 1
    assessed = risk.candidates[0]
    assert assessed.stop_loss == D("98700")
    assert assessed.take_profit == D("102200")
    assert assessed.risk_reward == D("2200") / D("1300")


@pytest.mark.asyncio
async def test_existing_live_exposure_only_monitors_and_never_evaluates_new_order() -> None:
    live = FakeLiveService()
    live.exposure = True
    result = await automation(live).run_once(execute=True)

    assert result.results[0].outcome == "monitoring"
    assert live.orders == []


@pytest.mark.asyncio
async def test_scheduled_start_honors_operator_symbol_scope() -> None:
    live = FakeLiveService()
    worker = automation(live)

    status = await worker.start(symbols=["BTC-USDT-SWAP"])
    await worker.stop()

    assert status.symbols == ["BTC-USDT-SWAP"]


@pytest.mark.asyncio
async def test_scheduled_start_rejects_unsupported_symbol() -> None:
    live = FakeLiveService()
    worker = automation(live)

    with pytest.raises(
        OkxLiveSafetyError, match="invalid_live_scan_symbol"
    ):
        await worker.start(symbols=["SOL-USDT-SWAP"])

    assert live.orders == []
