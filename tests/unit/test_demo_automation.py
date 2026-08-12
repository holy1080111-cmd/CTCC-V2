from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.config.settings import Settings
from app.demo_automation import DemoAutomationSafetyError
from app.demo_automation.service import SafeDemoAutomation
from app.domain.demo_automation import DemoAutomationActiveTrade
from app.domain.market import InstrumentInfo
from app.domain.okx_demo import (
    OkxDemoAccountConfig,
    OkxDemoBalanceDetail,
    OkxDemoBalanceSnapshot,
    OkxDemoOrderAcknowledgement,
    OkxDemoOrderView,
    OkxDemoPositionView,
    OkxDemoReconcileResult,
    OkxDemoWriteResult,
)
from app.domain.realtime import RealtimeSnapshot, RealtimeStatus
from app.domain.strategy import (
    DerivativeConfirmation,
    MathematicalConfirmation,
    StrategyDecision,
    TradeCandidate,
)
from app.exchange.okx.symbols import SUPPORTED_SYMBOLS


class FakeStrategy:
    def __init__(
        self,
        selected: TradeCandidate | None = None,
        *,
        by_symbol: dict[str, TradeCandidate | None] | None = None,
    ) -> None:
        self.selected = selected
        self.by_symbol = by_symbol or {}

    async def evaluate(
        self,
        symbol: str,
        _limit: int,
        *,
        disabled_strategies: set[str] | None = None,
    ) -> StrategyDecision:
        selected = self.by_symbol.get(symbol, self.selected)
        if selected is not None and selected.strategy in (disabled_strategies or set()):
            selected = None
        return StrategyDecision(
            symbol=symbol,
            instrument_id=symbol,
            decision=selected.direction if selected else "no_trade",
            selected_strategy=selected.strategy if selected else None,
            selected_candidate=selected,
            minimum_score=72,
            evaluations=[],
            blockers=[] if self.selected else ["no_signal"],
            generated_at=datetime.now(timezone.utc),
            version="1.5.0",
        )


class FakeDemo:
    def __init__(
        self,
        *,
        exposed: bool = False,
        acknowledged: bool = True,
        include_acknowledgement: bool = True,
    ) -> None:
        self.place_calls = []
        self.leverage_calls = []
        self.equity = Decimal("10000")
        self.other_asset_equity = Decimal("0")
        self.acknowledged = acknowledged
        self.include_acknowledgement = include_acknowledgement
        self.positions: list[OkxDemoPositionView] = []
        self.pending_orders: list[OkxDemoOrderView] = []
        self.recent_orders: list[OkxDemoOrderView] = []
        if exposed:
            self.positions.append(self._position("BTC-USDT-SWAP", "long"))

    @staticmethod
    def _position(instrument_id: str, direction: str) -> OkxDemoPositionView:
        return OkxDemoPositionView(
            instrument_id=instrument_id,
            position_side=direction,
            size=Decimal("1"),
            available_size=Decimal("1"),
            unrealized_pnl=Decimal("0"),
        )

    def close_with_pnl(
        self,
        instrument_id: str,
        pnl: Decimal,
        *,
        closed_at: datetime | None = None,
    ) -> None:
        self.positions = [
            item for item in self.positions if item.instrument_id != instrument_id
        ]
        self.recent_orders.append(
            OkxDemoOrderView(
                order_id=f"close-{instrument_id}-{len(self.recent_orders)}",
                instrument_id=instrument_id,
                side="sell",
                position_side="long",
                order_type="market",
                state="filled",
                size=Decimal("1"),
                accumulated_fill_size=Decimal("1"),
                reduce_only=True,
                updated_at=closed_at or datetime.now(timezone.utc),
                raw={"pnl": str(pnl), "fee": "0"},
            )
        )

    async def reconcile(self) -> OkxDemoReconcileResult:
        return OkxDemoReconcileResult(
            account_config=OkxDemoAccountConfig(
                account_level="2",
                position_mode="net_mode",
            ),
            balance=OkxDemoBalanceSnapshot(
                total_equity=self.equity + self.other_asset_equity,
                isolated_equity=Decimal("0"),
                adjusted_equity=Decimal("0"),
                available_equity=Decimal("0"),
                details=[
                    OkxDemoBalanceDetail(
                        currency="USDT",
                        equity=self.equity,
                        available_equity=self.equity,
                        cash_balance=self.equity,
                        available_balance=self.equity,
                        frozen_balance=Decimal("0"),
                        unrealized_pnl=Decimal("0"),
                    )
                ],
            ),
            positions=list(self.positions),
            pending_orders=list(self.pending_orders),
            recent_orders=list(self.recent_orders),
            pending_algo_orders=[],
            persisted=True,
        )

    async def set_leverage(self, request):
        self.leverage_calls.append(request)
        return OkxDemoWriteResult(action="set_leverage", acknowledged=True)

    async def place_order(self, request):
        self.place_calls.append(request)
        self.positions.append(self._position(request.instrument_id, request.direction))
        return OkxDemoWriteResult(
            action="place_order",
            acknowledged=self.acknowledged,
            acknowledgement=(
                OkxDemoOrderAcknowledgement(
                    order_id="123", client_order_id=request.client_order_id
                )
                if self.include_acknowledgement
                else None
            ),
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
                contract_currency=instrument_id.split("-")[0],
                settlement_currency="USDT",
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
        okx_demo_automation_max_consecutive_losses=3,
    )
    values.update(updates)
    return Settings(_env_file=None, **values)


def candidate(
    *,
    score: int = 82,
    stop_loss: str = "95",
    take_profit: str = "110",
    derivative_status: str = "confirmed",
    derivative_confidence: str = "0.80",
) -> TradeCandidate:
    return TradeCandidate(
        strategy="trend_pullback",
        direction="long",
        score=score,
        entry=Decimal("100"),
        stop_loss=Decimal(stop_loss),
        take_profit=Decimal(take_profit),
        risk_reward=(Decimal(take_profit) - Decimal("100"))
        / (Decimal("100") - Decimal(stop_loss)),
        invalidation="stop",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
        reasons=["unit-test"],
        derivative_confirmation=DerivativeConfirmation(
            status=derivative_status,
            confidence=Decimal(derivative_confidence),
            alignment_score=(
                Decimal("0.8")
                if derivative_status == "confirmed"
                else Decimal("-0.8")
                if derivative_status == "opposed"
                else Decimal("0")
            ),
            qualified_timeframes=["4H", "1H", "15m", "5m"],
            aligned_timeframes=(
                ["4H", "1H", "15m", "5m"]
                if derivative_status == "confirmed"
                else []
            ),
            opposed_timeframes=(
                ["4H", "1H", "15m", "5m"]
                if derivative_status == "opposed"
                else []
            ),
        ),
    )


def with_mathematical_confirmation(
    value: TradeCandidate,
    *,
    status: str,
    risk_grade: str,
    confidence: str = "0.8",
) -> TradeCandidate:
    return value.model_copy(
        update={
            "mathematical_confirmation": MathematicalConfirmation(
                status=status,
                risk_grade=risk_grade,
                confidence=Decimal(confidence),
                directional_support=(
                    Decimal("-0.8")
                    if status == "opposed"
                    else Decimal("0.8")
                ),
                reliability=Decimal("0.8"),
                coverage=Decimal("0.9"),
                consensus=Decimal("0.9"),
                instability=(
                    Decimal("0.9")
                    if status == "unstable"
                    else Decimal("0.1")
                ),
                component_codes=[
                    "derivative",
                    "state",
                    "conformal",
                ],
                auxiliary_bonus=(
                    0 if status in {"opposed", "unstable"} else 3
                ),
                auxiliary_directional_support=Decimal("0.8"),
                auxiliary_component_codes=["structure", "momentum"],
            )
        }
    )


def make_service(
    demo: FakeDemo,
    *,
    strategy: FakeStrategy | None = None,
    **setting_updates,
) -> SafeDemoAutomation:
    return SafeDemoAutomation(
        settings=configured_settings(**setting_updates),
        strategy_service=strategy or FakeStrategy(candidate()),
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
async def test_single_currency_margin_uses_usdt_risk_equity_not_total_equity() -> None:
    demo = FakeDemo()
    demo.equity = Decimal("4998.339000436543")
    demo.other_asset_equity = Decimal("76513.431528119597")
    service = adaptive_service(demo, {"BTC-USDT-SWAP": 95})
    await service.recover()

    run = await service.run_once(execute=False)
    status = await service.status()

    assert run.results[0].outcome == "approved_dry_run"
    assert run.total_equity == Decimal("81511.770528556140")
    assert run.risk_equity == Decimal("4998.339000436543")
    assert run.risk_equity_currency == "USDT"
    assert status.equity_basis == "single_currency:USDT"
    assert status.baseline_equity == Decimal("4998.339000436543")
    assert status.daily_pnl == Decimal("0")
    assert demo.place_calls == []


@pytest.mark.asyncio
async def test_equity_basis_change_requires_flat_untraded_session() -> None:
    demo = FakeDemo()
    service = adaptive_service(demo, {"BTC-USDT-SWAP": 95})
    await service.recover()
    service._set_active_trade(
        tracked_trade(
            "BTC-USDT-SWAP",
            started_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        )
    )

    run = await service.run_once(execute=False)
    status = await service.status()

    assert run.results[0].outcome == "blocked"
    assert run.results[0].detail == (
        "DemoAutomationSafetyError: equity_basis_change_requires_flat_session"
    )
    assert status.locked is True
    assert status.active_position_count == 1
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


def adaptive_service(
    demo: FakeDemo,
    scores: dict[str, int],
    *,
    candidates: dict[str, TradeCandidate] | None = None,
    **updates,
) -> SafeDemoAutomation:
    symbols = list(scores)
    strategy_candidates = candidates or {
        symbol: candidate(score=score) for symbol, score in scores.items()
    }
    settings = {
        "okx_demo_allowed_symbols": ",".join(symbols),
        "okx_demo_scan_symbols": ",".join(symbols),
        "okx_demo_score_risk_enabled": True,
        "okx_demo_max_open_positions": 3,
        "okx_demo_max_order_size_contracts": Decimal("1000"),
        "okx_demo_max_trades_per_day": 6,
        "okx_demo_daily_loss_limit_pct": Decimal("0.03"),
        "order_size_cap_usdt": 100000,
    }
    settings.update(updates)
    return make_service(
        demo,
        strategy=FakeStrategy(by_symbol=strategy_candidates),
        **settings,
    )


def prime_usdt_equity_basis(
    service: SafeDemoAutomation,
    demo: FakeDemo,
) -> None:
    service._state["equity_basis"] = "single_currency:USDT"
    service._state["baseline_equity"] = demo.equity
    service._state["peak_equity"] = demo.equity
    service._state["daily_pnl"] = Decimal("0")


def tracked_trade(
    instrument_id: str,
    *,
    started_at: datetime,
) -> DemoAutomationActiveTrade:
    return DemoAutomationActiveTrade(
        instrument_id=instrument_id,
        direction="long",
        strategy="trend_pullback",
        score=90,
        tier="high",
        client_order_id="AUT" + instrument_id.replace("-", "")[:20],
        exchange_order_id="order-" + instrument_id,
        contracts=Decimal("1"),
        leverage=3,
        risk_budget_pct=Decimal("0.01"),
        estimated_stop_loss_amount=Decimal("50"),
        estimated_stop_loss_pct=Decimal("0.005"),
        estimated_notional=Decimal("3000"),
        margin_allocation_pct=Decimal("0.10"),
        estimated_margin=Decimal("1000"),
        reference_price=Decimal("100"),
        stop_loss=Decimal("95"),
        take_profit=Decimal("110"),
        start_equity=Decimal("10000"),
        started_at=started_at,
    )


@pytest.mark.asyncio
async def test_high_score_uses_three_x_leverage_and_margin_cap() -> None:
    demo = FakeDemo()
    high = candidate(score=95, stop_loss="99.9", take_profit="102")
    service = adaptive_service(
        demo,
        {"BTC-USDT-SWAP": 95},
        candidates={"BTC-USDT-SWAP": high},
    )
    await service.recover()

    run = await service.run_once(execute=False)

    result = run.results[0]
    assert result.outcome == "approved_dry_run"
    assert result.score == 95
    assert result.effective_score == 95
    assert result.derivative_status == "confirmed"
    assert result.derivative_confidence == Decimal("0.80")
    assert result.score_tier == "high"
    assert result.selected_leverage == 3
    assert result.risk_budget_pct == Decimal("0.01")
    assert result.margin_allocation_pct == Decimal("0.25")
    assert result.estimated_margin == Decimal("2500")
    assert result.approved_contracts == Decimal("75")
    assert demo.place_calls == []


@pytest.mark.asyncio
async def test_medium_derivative_confidence_downgrades_high_score_to_two_x() -> None:
    demo = FakeDemo()
    high = candidate(
        score=95,
        stop_loss="99.9",
        take_profit="102",
        derivative_confidence="0.50",
    )
    service = adaptive_service(
        demo,
        {"BTC-USDT-SWAP": 95},
        candidates={"BTC-USDT-SWAP": high},
    )
    await service.recover()

    result = (await service.run_once(execute=False)).results[0]

    assert result.outcome == "approved_dry_run"
    assert result.score == 95
    assert result.effective_score == 89
    assert result.score_tier == "medium"
    assert result.selected_leverage == 2


@pytest.mark.asyncio
async def test_insufficient_derivative_confidence_downgrades_to_one_x() -> None:
    demo = FakeDemo()
    high = candidate(
        score=95,
        stop_loss="99.9",
        take_profit="102",
        derivative_status="insufficient",
        derivative_confidence="0",
    )
    service = adaptive_service(
        demo,
        {"BTC-USDT-SWAP": 95},
        candidates={"BTC-USDT-SWAP": high},
    )
    await service.recover()

    result = (await service.run_once(execute=False)).results[0]

    assert result.outcome == "approved_dry_run"
    assert result.effective_score == 79
    assert result.score_tier == "low"
    assert result.selected_leverage == 1


@pytest.mark.asyncio
async def test_opposing_derivative_blocks_order_before_exchange_write() -> None:
    demo = FakeDemo()
    opposed = candidate(
        score=95,
        derivative_status="opposed",
        derivative_confidence="0.90",
    )
    service = adaptive_service(
        demo,
        {"BTC-USDT-SWAP": 95},
        candidates={"BTC-USDT-SWAP": opposed},
    )
    await service.recover()
    await service.arm()

    result = (await service.run_once(execute=True)).results[0]

    assert result.outcome == "blocked"
    assert result.detail == "causal_derivative_opposes_trade_direction"
    assert demo.leverage_calls == []
    assert demo.place_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "detail"),
    [
        ("opposed", "mathematical_core_opposes_trade_direction"),
        ("unstable", "mathematical_core_regime_instability"),
    ],
)
async def test_mathematical_core_blocks_before_any_exchange_write(
    status: str, detail: str
) -> None:
    demo = FakeDemo()
    blocked = with_mathematical_confirmation(
        candidate(score=95),
        status=status,
        risk_grade="blocked",
    )
    service = adaptive_service(
        demo,
        {"BTC-USDT-SWAP": 95},
        candidates={"BTC-USDT-SWAP": blocked},
    )
    await service.recover()
    await service.arm()

    result = (await service.run_once(execute=True)).results[0]

    assert result.outcome == "blocked"
    assert result.detail == detail
    assert result.mathematical_status == status
    assert result.mathematical_risk_grade == "blocked"
    assert demo.leverage_calls == []
    assert demo.place_calls == []


@pytest.mark.asyncio
async def test_mathematical_audit_fields_persist_on_approved_dry_run() -> None:
    demo = FakeDemo()
    confirmed = with_mathematical_confirmation(
        candidate(score=95, stop_loss="99.9", take_profit="102"),
        status="confirmed",
        risk_grade="high",
    )
    service = adaptive_service(
        demo,
        {"BTC-USDT-SWAP": 95},
        candidates={"BTC-USDT-SWAP": confirmed},
    )
    await service.recover()

    result = (await service.run_once(execute=False)).results[0]

    assert result.outcome == "approved_dry_run"
    assert result.mathematical_status == "confirmed"
    assert result.mathematical_risk_grade == "high"
    assert result.mathematical_confidence == Decimal("0.8")
    assert result.mathematical_reliability == Decimal("0.8")
    assert result.mathematical_auxiliary_bonus == 3
    assert result.mathematical_validated_components == [
        "derivative",
        "state",
        "conformal",
    ]
    assert result.mathematical_auxiliary_components == [
        "structure",
        "momentum",
    ]


@pytest.mark.asyncio
async def test_candidates_are_ranked_by_score_and_two_symbols_can_be_open() -> None:
    demo = FakeDemo()
    service = adaptive_service(
        demo,
        {
            "BTC-USDT-SWAP": 80,
            "ETH-USDT-SWAP": 95,
        },
    )
    await service.recover()
    await service.arm()

    run = await service.run_once(execute=True)

    assert [item.instrument_id for item in demo.place_calls] == [
        "ETH-USDT-SWAP",
        "BTC-USDT-SWAP",
    ]
    assert [item.leverage for item in demo.leverage_calls] == [3, 2]
    assert [item.outcome for item in run.results] == ["submitted", "submitted"]
    assert run.active_position_count == 2
    status = await service.status()
    assert {item.instrument_id for item in status.active_trades} == {
        "BTC-USDT-SWAP",
        "ETH-USDT-SWAP",
    }

    second = await service.run_once(execute=True)
    assert {item.outcome for item in second.results} == {"monitoring"}
    assert len(demo.place_calls) == 2


@pytest.mark.asyncio
async def test_execute_submission_limit_cannot_be_bypassed_by_multi_symbol_run() -> None:
    demo = FakeDemo()
    service = adaptive_service(
        demo,
        {
            "BTC-USDT-SWAP": 80,
            "ETH-USDT-SWAP": 95,
        },
    )
    await service.recover()
    await service.arm()

    run = await service.run_once(execute=True, submission_limit=1)

    assert len(demo.place_calls) == 1
    assert demo.place_calls[0].instrument_id == "ETH-USDT-SWAP"
    assert [item.outcome for item in run.results] == ["submitted", "blocked"]
    assert run.results[1].detail == "run_submission_limit_reached"


@pytest.mark.asyncio
async def test_auxiliary_bonus_breaks_only_equal_cross_symbol_rank() -> None:
    demo = FakeDemo()
    no_bonus = with_mathematical_confirmation(
        candidate(score=90),
        status="confirmed",
        risk_grade="high",
    )
    no_bonus_confirmation = no_bonus.mathematical_confirmation
    assert no_bonus_confirmation is not None
    no_bonus = no_bonus.model_copy(
        update={
            "mathematical_confirmation": no_bonus_confirmation.model_copy(
                update={"auxiliary_bonus": 0}
            )
        }
    )
    with_bonus = with_mathematical_confirmation(
        candidate(score=90),
        status="confirmed",
        risk_grade="high",
    )
    service = adaptive_service(
        demo,
        {"BTC-USDT-SWAP": 90, "ETH-USDT-SWAP": 90},
        candidates={
            "BTC-USDT-SWAP": no_bonus,
            "ETH-USDT-SWAP": with_bonus,
        },
    )
    await service.recover()
    await service.arm()

    run = await service.run_once(execute=True, submission_limit=1)

    assert len(demo.place_calls) == 1
    assert demo.place_calls[0].instrument_id == "ETH-USDT-SWAP"
    assert run.results[0].mathematical_auxiliary_bonus == 3


@pytest.mark.asyncio
async def test_shadow_portfolio_enforces_aggregate_open_stop_risk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The production symbol boundary intentionally defaults to BTC and ETH.
    # Add a third test-only instrument so this case isolates aggregate risk
    # reservation instead of failing earlier in symbol normalization.
    monkeypatch.setitem(
        SUPPORTED_SYMBOLS,
        "SOL/USDT:USDT",
        "SOL-USDT-SWAP",
    )
    demo = FakeDemo()
    symbols = ["BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP"]
    service = adaptive_service(
        demo,
        {symbol: 95 for symbol in symbols},
        okx_demo_portfolio_max_risk_pct=Decimal("0.015"),
    )
    await service.recover()

    run = await service.run_once(execute=False)

    approved = [item for item in run.results if item.outcome == "approved_dry_run"]
    blocked = [item for item in run.results if item.outcome == "blocked"]
    assert [item.risk_budget_pct for item in approved] == [
        Decimal("0.01"),
        Decimal("0.005"),
    ]
    assert len(blocked) == 1
    assert blocked[0].detail == "portfolio_open_risk_limit_reached"
    assert run.portfolio_open_risk_pct == Decimal("0.015")
    assert demo.place_calls == []


@pytest.mark.asyncio
async def test_three_consecutive_stop_losses_lock_execution_for_utc_day() -> None:
    demo = FakeDemo()
    symbols = ["BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP"]
    service = adaptive_service(demo, {symbol: 95 for symbol in symbols})
    await service.recover()
    prime_usdt_equity_basis(service, demo)
    service._state["armed"] = True
    started_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    for index, symbol in enumerate(symbols):
        service._set_active_trade(tracked_trade(symbol, started_at=started_at))
        demo.close_with_pnl(
            symbol,
            Decimal("-1"),
            closed_at=datetime.now(timezone.utc) - timedelta(seconds=3 - index),
        )

    run = await service.run_once(execute=True)
    status = await service.status()

    assert run.results[0].outcome == "locked"
    assert "consecutive_loss_limit_reached" in run.results[0].reason_codes
    assert status.consecutive_losses == 3
    assert status.locked is True
    assert status.active_position_count == 0
    assert demo.place_calls == []


@pytest.mark.asyncio
async def test_profitable_close_resets_consecutive_stop_loss_count() -> None:
    demo = FakeDemo()
    service = adaptive_service(demo, {"BTC-USDT-SWAP": 95})
    await service.recover()
    prime_usdt_equity_basis(service, demo)
    service._state["consecutive_losses"] = 2
    started_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    service._set_active_trade(
        tracked_trade("BTC-USDT-SWAP", started_at=started_at)
    )
    demo.close_with_pnl("BTC-USDT-SWAP", Decimal("2"))

    await service.run_once(execute=False)
    status = await service.status()

    assert status.consecutive_losses == 0
    assert status.active_position_count == 0


@pytest.mark.asyncio
async def test_utc_day_rollover_clears_daily_stop_loss_lock() -> None:
    demo = FakeDemo()
    service = adaptive_service(demo, {"BTC-USDT-SWAP": 95})
    await service.recover()
    service._state["session_date"] = (
        datetime.now(timezone.utc) - timedelta(days=1)
    ).date()
    service._state["consecutive_losses"] = 3
    service._state["locked"] = True
    service._state["lock_reasons"] = ["consecutive_loss_limit_reached"]
    service._state["armed"] = True

    run = await service.run_once(execute=True)
    status = await service.status()

    assert status.session_date == datetime.now(timezone.utc).date()
    assert status.consecutive_losses == 0
    assert status.locked is False
    assert run.results[0].outcome == "submitted"


@pytest.mark.asyncio
async def test_late_reconciled_prior_utc_day_close_does_not_lock_new_day() -> None:
    demo = FakeDemo()
    service = adaptive_service(demo, {"BTC-USDT-SWAP": 95})
    await service.recover()
    prime_usdt_equity_basis(service, demo)
    now = datetime.now(timezone.utc)
    started_at = now - timedelta(days=1, minutes=10)
    service._set_active_trade(
        tracked_trade("BTC-USDT-SWAP", started_at=started_at)
    )
    demo.close_with_pnl(
        "BTC-USDT-SWAP",
        Decimal("-5"),
        closed_at=now - timedelta(days=1, minutes=5),
    )

    await service.run_once(execute=False)
    status = await service.status()

    assert status.session_date == now.date()
    assert status.consecutive_losses == 0
    assert status.locked is False


@pytest.mark.asyncio
async def test_unknown_multi_position_close_outcome_fails_closed() -> None:
    demo = FakeDemo()
    service = adaptive_service(
        demo,
        {
            "BTC-USDT-SWAP": 95,
            "ETH-USDT-SWAP": 90,
        },
    )
    await service.recover()
    prime_usdt_equity_basis(service, demo)
    started_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    service._set_active_trade(
        tracked_trade("BTC-USDT-SWAP", started_at=started_at)
    )
    service._set_active_trade(
        tracked_trade("ETH-USDT-SWAP", started_at=started_at)
    )

    await service.run_once(execute=False)
    status = await service.status()

    assert status.emergency_stop is True
    assert status.locked is True
    assert "trade_outcome_unconfirmed" in status.lock_reasons
    assert status.active_position_count == 2


@pytest.mark.asyncio
async def test_single_legacy_trade_keeps_equity_delta_close_fallback() -> None:
    demo = FakeDemo()
    demo.equity = Decimal("9990")
    service = make_service(demo)
    await service.recover()
    prime_usdt_equity_basis(service, demo)
    service._set_active_trade(
        DemoAutomationActiveTrade(
            instrument_id="BTC-USDT-SWAP",
            tier="legacy",
            start_equity=Decimal("10000"),
            started_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        )
    )

    await service.run_once(execute=False)
    status = await service.status()

    assert status.consecutive_losses == 1
    assert status.emergency_stop is False
    assert status.active_position_count == 0


@pytest.mark.asyncio
async def test_reconciliation_grace_prevents_premature_trade_finalization() -> None:
    demo = FakeDemo()
    service = adaptive_service(demo, {"BTC-USDT-SWAP": 95})
    await service.recover()
    prime_usdt_equity_basis(service, demo)
    service._set_active_trade(
        tracked_trade(
            "BTC-USDT-SWAP",
            started_at=datetime.now(timezone.utc),
        )
    )

    run = await service.run_once(execute=False)
    status = await service.status()

    assert run.results[0].outcome == "monitoring"
    assert status.active_position_count == 1
    assert status.emergency_stop is False


@pytest.mark.asyncio
async def test_invalid_post_submission_acknowledgement_stops_remaining_orders() -> None:
    demo = FakeDemo(acknowledged=False, include_acknowledgement=False)
    service = adaptive_service(
        demo,
        {
            "BTC-USDT-SWAP": 95,
            "ETH-USDT-SWAP": 90,
        },
    )
    await service.recover()
    await service.arm()

    run = await service.run_once(execute=True)
    status = await service.status()

    assert len(demo.place_calls) == 1
    assert run.results[0].outcome == "error"
    assert run.results[1].outcome == "locked"
    assert status.emergency_stop is True
    assert "post_submission_acknowledgement_invalid" in status.lock_reasons
    assert status.active_position_count == 1
