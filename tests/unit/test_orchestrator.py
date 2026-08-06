from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.config.settings import Settings
from app.domain.realtime import RealtimeSnapshot, RealtimeStatus
from app.domain.strategy import StrategyDecision, TradeCandidate
from app.orchestrator.service import AutoPaperOrchestrator
from app.paper.engine import PaperBroker


class FakeStrategyService:
    def __init__(self, decision: StrategyDecision) -> None:
        self.decision = decision

    async def evaluate(self, _symbol: str, _candle_limit: int) -> StrategyDecision:
        return self.decision


class FakeHub:
    def __init__(self, snapshot: RealtimeSnapshot | None) -> None:
        self.value = snapshot

    async def snapshot(self, _symbol: str) -> RealtimeSnapshot | None:
        return self.value


class FakeClient:
    def status(self) -> RealtimeStatus:
        return RealtimeStatus(
            enabled=True,
            running=True,
            connected=True,
            endpoint="wss://example.test",
            symbols=["BTC-USDT-SWAP"],
            paper_auto_ticks=True,
        )


def settings(*, enabled: bool) -> Settings:
    return Settings(
        _env_file=None,
        environment="test",
        trading_mode="paper",
        auto_trade=False,
        live_trading=False,
        okx_ws_enabled=True,
        paper_auto_ticks=True,
        paper_auto_execution=enabled,
        paper_scan_symbols="BTC-USDT-SWAP",
        paper_scan_max_entry_drift_bps=100,
    )


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


def decision(selected: TradeCandidate | None) -> StrategyDecision:
    return StrategyDecision(
        symbol="BTC/USDT:USDT",
        instrument_id="BTC-USDT-SWAP",
        decision="long" if selected else "no_trade",
        selected_strategy=selected.strategy if selected else None,
        selected_candidate=selected,
        minimum_score=72,
        evaluations=[],
        blockers=[] if selected else ["no_signal"],
        generated_at=datetime.now(timezone.utc),
        version="1.0.0",
    )


def snapshot(*, age_seconds: int = 0) -> RealtimeSnapshot:
    return RealtimeSnapshot(
        symbol="BTC-USDT-SWAP",
        last=Decimal("100"),
        received_at=datetime.now(timezone.utc) - timedelta(seconds=age_seconds),
    )


@pytest.mark.asyncio
async def test_execute_submits_approved_paper_order() -> None:
    broker = PaperBroker()
    service = AutoPaperOrchestrator(
        settings=settings(enabled=True),
        strategy_service=FakeStrategyService(decision(candidate())),
        broker=broker,
        market_hub=FakeHub(snapshot()),
        market_client=FakeClient(),
    )
    run = await service.run_once(execute=True)
    assert run.results[0].outcome == "submitted"
    assert broker.account().open_positions == 1


@pytest.mark.asyncio
async def test_dry_run_does_not_submit_when_auto_execution_disabled() -> None:
    broker = PaperBroker()
    service = AutoPaperOrchestrator(
        settings=settings(enabled=False),
        strategy_service=FakeStrategyService(decision(candidate())),
        broker=broker,
        market_hub=FakeHub(snapshot()),
        market_client=FakeClient(),
    )
    run = await service.run_once(execute=False)
    assert run.results[0].outcome == "approved_dry_run"
    assert broker.account().open_positions == 0


@pytest.mark.asyncio
async def test_no_trade_is_recorded_without_order() -> None:
    broker = PaperBroker()
    service = AutoPaperOrchestrator(
        settings=settings(enabled=False),
        strategy_service=FakeStrategyService(decision(None)),
        broker=broker,
        market_hub=FakeHub(snapshot()),
        market_client=FakeClient(),
    )
    run = await service.run_once(execute=False)
    assert run.results[0].outcome == "no_trade"
    assert broker.account().open_positions == 0


@pytest.mark.asyncio
async def test_stale_realtime_snapshot_blocks_execution() -> None:
    broker = PaperBroker()
    service = AutoPaperOrchestrator(
        settings=settings(enabled=True),
        strategy_service=FakeStrategyService(decision(candidate())),
        broker=broker,
        market_hub=FakeHub(snapshot(age_seconds=120)),
        market_client=FakeClient(),
    )
    run = await service.run_once(execute=True)
    assert run.results[0].outcome == "blocked"
    assert run.results[0].detail == "realtime_snapshot_stale"
    assert broker.account().open_positions == 0


@pytest.mark.asyncio
async def test_existing_symbol_position_prevents_duplicate_order() -> None:
    broker = PaperBroker()
    service = AutoPaperOrchestrator(
        settings=settings(enabled=True),
        strategy_service=FakeStrategyService(decision(candidate())),
        broker=broker,
        market_hub=FakeHub(snapshot()),
        market_client=FakeClient(),
    )
    first = await service.run_once(execute=True)
    second = await service.run_once(execute=True)
    assert first.results[0].outcome == "submitted"
    assert second.results[0].outcome == "duplicate"
    assert broker.account().open_positions == 1


class DisconnectedClient:
    def status(self) -> RealtimeStatus:
        return RealtimeStatus(
            enabled=True,
            running=True,
            connected=False,
            endpoint="wss://example.test",
            symbols=["BTC-USDT-SWAP"],
            paper_auto_ticks=True,
        )


@pytest.mark.asyncio
async def test_disconnected_websocket_blocks_execution() -> None:
    broker = PaperBroker()
    service = AutoPaperOrchestrator(
        settings=settings(enabled=True),
        strategy_service=FakeStrategyService(decision(candidate())),
        broker=broker,
        market_hub=FakeHub(snapshot()),
        market_client=DisconnectedClient(),
    )
    run = await service.run_once(execute=True)
    assert run.results[0].outcome == "blocked"
    assert run.results[0].detail == "realtime_websocket_not_connected"
    assert broker.account().open_positions == 0
