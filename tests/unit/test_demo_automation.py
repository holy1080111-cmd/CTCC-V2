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
    OkxDemoAlgoOrderView,
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
    StructuralProtectionGeometry,
    StrategyDecision,
    TradeCandidate,
)


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
        leverage_acknowledged: bool = True,
        protection_confirmed: bool = True,
    ) -> None:
        self.place_calls = []
        self.leverage_calls = []
        self.account_level = "2"
        self.equity = Decimal("10000")
        self.available_equity: Decimal | None = None
        self.adjusted_equity = Decimal("0")
        self.account_available_equity = Decimal("0")
        self.other_asset_equity = Decimal("0")
        self.acknowledged = acknowledged
        self.include_acknowledgement = include_acknowledgement
        self.leverage_acknowledged = leverage_acknowledged
        self.protection_confirmed = protection_confirmed
        self.protection_present = True
        self.protection_by_instrument: dict[
            str, tuple[str, Decimal, Decimal]
        ] = {}
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
        pending_algo_orders: list[OkxDemoAlgoOrderView] = []
        if self.protection_present:
            for position in self.positions:
                client_id, stop_loss, take_profit = (
                    self.protection_by_instrument.get(
                        position.instrument_id,
                        (
                            fake_protection_id(position.instrument_id),
                            Decimal("95"),
                            Decimal("110"),
                        ),
                    )
                )
                pending_algo_orders.append(
                    OkxDemoAlgoOrderView(
                        algo_order_id="algo-" + position.instrument_id,
                        client_algo_order_id=client_id,
                        instrument_id=position.instrument_id,
                        order_type="oco",
                        state="live",
                        side="sell",
                        position_side=position.position_side,
                        size=abs(position.size),
                        take_profit_trigger_price=take_profit,
                        stop_loss_trigger_price=stop_loss,
                        raw={
                            "slTriggerPxType": "mark",
                            "tpTriggerPxType": "mark",
                        },
                    )
                )
        return OkxDemoReconcileResult(
            account_config=OkxDemoAccountConfig(
                account_level=self.account_level,
                position_mode="net_mode",
            ),
            balance=OkxDemoBalanceSnapshot(
                total_equity=self.equity + self.other_asset_equity,
                isolated_equity=Decimal("0"),
                adjusted_equity=self.adjusted_equity,
                available_equity=self.account_available_equity,
                details=[
                    OkxDemoBalanceDetail(
                        currency="USDT",
                        equity=self.equity,
                        available_equity=(
                            self.equity
                            if self.available_equity is None
                            else self.available_equity
                        ),
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
            pending_algo_orders=pending_algo_orders,
            persisted=True,
        )

    async def set_leverage(self, request):
        self.leverage_calls.append(request)
        return OkxDemoWriteResult(
            action="set_leverage",
            acknowledged=self.leverage_acknowledged,
        )

    async def place_order(self, request):
        self.place_calls.append(request)
        self.positions.append(self._position(request.instrument_id, request.direction))
        protection_client_order_id = fake_protection_id(request.instrument_id)
        if request.stop_loss is not None and request.take_profit is not None:
            self.protection_by_instrument[request.instrument_id] = (
                protection_client_order_id,
                request.stop_loss,
                request.take_profit,
            )
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
            protection_confirmed=self.protection_confirmed,
            protection_client_order_id=protection_client_order_id,
        )


class FakePublic:
    def __init__(
        self,
        *,
        instrument_type: str = "SWAP",
        state: str = "live",
        settlement_currency: str = "USDT",
        returned_instrument_id: str | None = None,
    ) -> None:
        self.instrument_type = instrument_type
        self.state = state
        self.settlement_currency = settlement_currency
        self.returned_instrument_id = returned_instrument_id

    async def instruments(self, instrument_id: str):
        returned_instrument_id = self.returned_instrument_id or instrument_id
        return [
            InstrumentInfo(
                symbol=returned_instrument_id,
                instrument_id=returned_instrument_id,
                instrument_type=self.instrument_type,
                state=self.state,
                tick_size=Decimal("0.1"),
                lot_size=Decimal("1"),
                minimum_size=Decimal("1"),
                contract_value=Decimal("1"),
                contract_currency=returned_instrument_id.split("-")[0],
                settlement_currency=self.settlement_currency,
            )
        ]


class FakeHub:
    def __init__(
        self,
        *,
        last: Decimal = Decimal("100"),
        bid: Decimal = Decimal("100"),
        ask: Decimal = Decimal("100"),
        mark: Decimal = Decimal("100"),
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

    async def snapshot(self, symbol: str):
        return self.value.model_copy(update={"symbol": symbol})


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


def structural_demo_candidate() -> TradeCandidate:
    value = with_mathematical_confirmation(
        candidate(score=99),
        status="confirmed",
        risk_grade="high",
        confidence="0.9",
    )
    return value.model_copy(
        update={
            "structural_protection": StructuralProtectionGeometry(
                timeframe="15m",
                source_closed_at=datetime.now(timezone.utc) - timedelta(minutes=1),
                reference_entry=Decimal("100"),
                stop_anchor=Decimal("99.95"),
                target_anchor=Decimal("101"),
                volatility_buffer=Decimal("0.05"),
                stop_loss=Decimal("99.9"),
                take_profit=Decimal("101"),
                gross_risk_reward=Decimal("10"),
            )
        }
    )


def structural_dynamic_updates() -> dict[str, object]:
    return {
        **continuous_session_updates(),
        "okx_demo_structural_dynamic_leverage_enabled": True,
        "okx_demo_max_leverage": 20,
        "okx_demo_portfolio_max_risk_pct": Decimal("0.10"),
        "max_weekly_loss_pct": 0.10,
    }


def make_service(
    demo: FakeDemo,
    *,
    strategy: FakeStrategy | None = None,
    market_hub: FakeHub | None = None,
    public_client: FakePublic | None = None,
    **setting_updates,
) -> SafeDemoAutomation:
    return SafeDemoAutomation(
        settings=configured_settings(**setting_updates),
        strategy_service=strategy or FakeStrategy(candidate()),
        demo_service=demo,
        public_client=public_client or FakePublic(),
        market_hub=market_hub or FakeHub(),
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
@pytest.mark.parametrize(
    ("public_client", "expected_detail"),
    [
        (
            FakePublic(returned_instrument_id="ETH-USDT-SWAP"),
            "instrument_metadata_mismatch",
        ),
        (FakePublic(instrument_type="FUTURES"), "instrument_not_swap"),
        (FakePublic(state="suspend"), "instrument_not_live"),
    ],
)
async def test_dry_run_fails_closed_for_ineligible_instrument_metadata(
    public_client: FakePublic,
    expected_detail: str,
) -> None:
    demo = FakeDemo()
    service = make_service(demo, public_client=public_client)
    await service.recover()

    run = await service.run_once(execute=False)

    assert run.results[0].outcome == "blocked"
    assert run.results[0].detail == expected_detail
    assert demo.place_calls == []


@pytest.mark.asyncio
async def test_demo_market_buy_uses_fresh_ask_as_risk_reference() -> None:
    demo = FakeDemo()
    service = make_service(
        demo,
        market_hub=FakeHub(ask=Decimal("100.2")),
    )
    await service.recover()
    await service.arm()

    run = await service.run_once(execute=True)

    assert run.results[0].outcome == "submitted"
    assert run.results[0].reference_price == Decimal("100.2")
    assert len(demo.place_calls) == 1
    assert demo.place_calls[0].trigger_price_type == "mark"


@pytest.mark.asyncio
async def test_demo_market_sell_uses_fresh_bid_as_risk_reference() -> None:
    demo = FakeDemo()
    short = candidate().model_copy(
        update={
            "direction": "short",
            "stop_loss": Decimal("105"),
            "take_profit": Decimal("90"),
            "risk_reward": Decimal("2"),
        }
    )
    service = make_service(
        demo,
        strategy=FakeStrategy(short),
        market_hub=FakeHub(bid=Decimal("99.8")),
    )
    await service.recover()
    await service.arm()

    run = await service.run_once(execute=True)

    assert run.results[0].outcome == "submitted"
    assert run.results[0].reference_price == Decimal("99.8")
    assert len(demo.place_calls) == 1
    assert demo.place_calls[0].direction == "short"


@pytest.mark.asyncio
async def test_demo_fresh_mark_does_not_hide_stale_executable_quote() -> None:
    demo = FakeDemo()
    service = make_service(
        demo,
        market_hub=FakeHub(quote_age_seconds=120, mark_age_seconds=0),
    )
    await service.recover()
    await service.arm()

    run = await service.run_once(execute=True)

    assert run.results[0].outcome == "blocked"
    assert run.results[0].detail == "realtime_executable_quote_stale"
    assert demo.place_calls == []


@pytest.mark.asyncio
async def test_demo_mark_to_execution_basis_above_limit_fails_closed() -> None:
    demo = FakeDemo()
    service = make_service(
        demo,
        market_hub=FakeHub(mark=Decimal("99")),
    )
    await service.recover()
    await service.arm()

    run = await service.run_once(execute=True)

    assert run.results[0].outcome == "blocked"
    assert run.results[0].detail == "mark_execution_basis_exceeds_limit"
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
async def test_usdt_capital_bucket_rejects_pooled_usd_equity_basis() -> None:
    demo = FakeDemo()
    demo.account_level = "3"
    demo.adjusted_equity = Decimal("5000")
    demo.account_available_equity = Decimal("5000")
    service = adaptive_service(
        demo,
        {"BTC-USDT-SWAP": 95},
        okx_demo_capital_bucket_enabled=True,
    )
    await service.recover()

    run = await service.run_once(execute=False)

    assert run.results[0].outcome == "blocked"
    assert run.results[0].detail == (
        "DemoAutomationSafetyError: "
        "capital_bucket_requires_single_currency_usdt_equity"
    )
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


def continuous_session_updates() -> dict[str, object]:
    return {
        "okx_demo_continuous_session_enabled": True,
        "okx_demo_trade_cooldown_seconds": 0,
        "okx_demo_capital_bucket_enabled": True,
    }


@pytest.mark.asyncio
async def test_continuous_session_status_exposes_disabled_time_gates() -> None:
    service = adaptive_service(
        FakeDemo(),
        {"BTC-USDT-SWAP": 95},
        **continuous_session_updates(),
    )
    await service.recover()

    status = await service.status()

    assert status.continuous_session_enabled is True
    assert status.daily_loss_limit_enforced is False
    assert status.daily_trade_limit_enforced is False
    assert status.consecutive_loss_limit_enforced is False
    assert status.effective_trade_cooldown_seconds == 0
    assert status.daily_loss_limit_pct == Decimal("0.03")


@pytest.mark.asyncio
async def test_continuous_session_ignores_trade_count_and_loss_streak_locks() -> None:
    demo = FakeDemo()
    service = adaptive_service(
        demo,
        {"BTC-USDT-SWAP": 95},
        **continuous_session_updates(),
    )
    await service.recover()
    await service.arm()
    service._state["trades_today"] = 99
    service._state["consecutive_losses"] = 12
    service._state["locked"] = True
    service._state["lock_reasons"] = [
        "daily_loss_limit_reached",
        "daily_trade_count_limit_reached",
        "consecutive_loss_limit_reached",
    ]

    run = await service.run_once(execute=True)
    status = await service.status()

    assert run.results[0].outcome == "submitted"
    assert len(demo.place_calls) == 1
    assert status.trades_today == 100
    assert status.consecutive_losses == 12
    assert status.locked is False
    assert status.lock_reasons == []


@pytest.mark.asyncio
async def test_continuous_session_scheduler_can_refresh_a_legacy_time_lock() -> None:
    service = adaptive_service(
        FakeDemo(),
        {"BTC-USDT-SWAP": 95},
        okx_demo_scan_initial_delay_seconds=600,
        **continuous_session_updates(),
    )
    await service.recover()
    service._state["armed"] = True
    service._state["locked"] = True
    service._state["lock_reasons"] = [
        "daily_trade_count_limit_reached",
        "consecutive_loss_limit_reached",
    ]

    status = await service.start()

    assert status.running is True
    assert status.locked is False
    assert status.lock_reasons == []
    await service.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("hard_reason", "emergency_stop", "error_match"),
    [
        ("trade_outcome_unconfirmed", False, "automation_locked"),
        ("emergency_stop_engaged", True, "emergency_stop_engaged"),
    ],
)
async def test_continuous_session_does_not_clear_hard_safety_locks(
    hard_reason: str,
    emergency_stop: bool,
    error_match: str,
) -> None:
    service = adaptive_service(
        FakeDemo(),
        {"BTC-USDT-SWAP": 95},
        **continuous_session_updates(),
    )
    await service.recover()
    service._state["armed"] = True
    service._state["emergency_stop"] = emergency_stop
    service._state["locked"] = True
    service._state["lock_reasons"] = [
        "daily_loss_limit_reached",
        "daily_trade_count_limit_reached",
        "consecutive_loss_limit_reached",
        hard_reason,
    ]

    with pytest.raises(DemoAutomationSafetyError, match=error_match):
        await service.start()

    status = await service.status()
    assert status.locked is True
    assert hard_reason in status.lock_reasons
    assert "daily_loss_limit_reached" not in status.lock_reasons
    assert "daily_trade_count_limit_reached" not in status.lock_reasons
    assert "consecutive_loss_limit_reached" not in status.lock_reasons


@pytest.mark.asyncio
async def test_continuous_session_does_not_enforce_daily_loss_limit() -> None:
    demo = FakeDemo()
    service = adaptive_service(
        demo,
        {"BTC-USDT-SWAP": 95},
        **continuous_session_updates(),
    )
    await service.recover()
    await service.arm()
    demo.equity = Decimal("9700")

    run = await service.run_once(execute=True)
    status = await service.status()

    assert run.results[0].outcome == "submitted"
    assert "daily_loss_limit_reached" not in run.results[0].reason_codes
    assert status.daily_loss_limit_enforced is False
    assert status.locked is False
    assert status.daily_pnl == Decimal("-300")
    assert len(demo.place_calls) == 1


@pytest.mark.asyncio
async def test_continuous_session_retains_weekly_loss_backstop() -> None:
    demo = FakeDemo()
    service = adaptive_service(
        demo,
        {"BTC-USDT-SWAP": 95},
        **continuous_session_updates(),
    )
    await service.recover()
    await service.arm()
    now = datetime.now(timezone.utc)
    service._record_realized_pnl_event(
        tracked_trade(
            "BTC-USDT-SWAP",
            started_at=now - timedelta(hours=2),
        ),
        now - timedelta(hours=1),
        Decimal("-600"),
    )

    run = await service.run_once(execute=True)
    status = await service.status()

    assert run.results[0].outcome == "risk_rejected"
    assert "weekly_loss_limit_reached" in run.results[0].reason_codes
    assert "daily_loss_limit_reached" not in run.results[0].reason_codes
    assert status.locked is False
    assert demo.place_calls == []


@pytest.mark.asyncio
async def test_unrealized_equity_delta_is_not_weekly_realized_pnl() -> None:
    demo = FakeDemo()
    service = adaptive_service(
        demo,
        {"BTC-USDT-SWAP": 95},
        max_drawdown_pct=Decimal("0.50"),
        **continuous_session_updates(),
    )
    await service.recover()
    await service.arm()
    demo.equity = Decimal("9400")

    run = await service.run_once(execute=True)

    assert run.rolling_7d_realized_pnl == Decimal("0")
    assert "weekly_loss_limit_reached" not in run.results[0].reason_codes
    assert run.results[0].outcome == "submitted"


@pytest.mark.asyncio
async def test_standard_session_still_enforces_daily_loss_limit() -> None:
    demo = FakeDemo()
    service = adaptive_service(demo, {"BTC-USDT-SWAP": 95})
    await service.recover()
    await service.arm()
    demo.equity = Decimal("9700")

    run = await service.run_once(execute=True)
    status = await service.status()

    assert run.results[0].outcome == "locked"
    assert "daily_loss_limit_reached" in run.results[0].reason_codes
    assert status.daily_loss_limit_enforced is True
    assert status.locked is True
    assert demo.place_calls == []


@pytest.mark.asyncio
async def test_continuous_session_retains_drawdown_backstop() -> None:
    demo = FakeDemo()
    service = adaptive_service(
        demo,
        {"BTC-USDT-SWAP": 95},
        max_weekly_loss_pct=Decimal("0.50"),
        **continuous_session_updates(),
    )
    await service.recover()
    await service.arm()
    demo.equity = Decimal("8900")

    run = await service.run_once(execute=True)

    assert run.results[0].outcome == "risk_rejected"
    assert "drawdown_limit_reached" in run.results[0].reason_codes
    assert "daily_loss_limit_reached" not in run.results[0].reason_codes
    assert demo.place_calls == []


@pytest.mark.asyncio
async def test_continuous_session_bypasses_post_trade_cooldown_only() -> None:
    demo = FakeDemo()
    service = adaptive_service(
        demo,
        {"BTC-USDT-SWAP": 95},
        **continuous_session_updates(),
    )
    await service.recover()
    service._state["symbol_cooldowns"] = {
        "BTC-USDT-SWAP": datetime.now(timezone.utc).isoformat()
    }

    run = await service.run_once(execute=False)

    assert run.results[0].outcome == "approved_dry_run"
    assert demo.place_calls == []


@pytest.mark.asyncio
async def test_standard_session_still_enforces_post_trade_cooldown() -> None:
    demo = FakeDemo()
    service = adaptive_service(demo, {"BTC-USDT-SWAP": 95})
    await service.recover()
    service._state["symbol_cooldowns"] = {
        "BTC-USDT-SWAP": datetime.now(timezone.utc).isoformat()
    }

    run = await service.run_once(execute=False)

    assert run.results[0].outcome == "blocked"
    assert run.results[0].detail == "post_trade_cooldown_active"
    assert demo.place_calls == []


@pytest.mark.asyncio
async def test_continuous_session_does_not_bypass_candidate_fingerprint() -> None:
    demo = FakeDemo()
    selected = candidate(score=95)
    service = adaptive_service(
        demo,
        {"BTC-USDT-SWAP": 95},
        candidates={"BTC-USDT-SWAP": selected},
        **continuous_session_updates(),
    )
    await service.recover()
    fingerprint = service._fingerprint("BTC-USDT-SWAP", selected)
    service._fingerprints[fingerprint] = datetime.now(timezone.utc) + timedelta(
        minutes=10
    )

    run = await service.run_once(execute=False)

    assert run.results[0].outcome == "duplicate"
    assert run.results[0].detail == "candidate_fingerprint_already_processed"
    assert demo.place_calls == []


@pytest.mark.asyncio
async def test_continuous_session_keeps_per_run_submission_limit() -> None:
    demo = FakeDemo()
    service = adaptive_service(
        demo,
        {
            "BTC-USDT-SWAP": 80,
            "ETH-USDT-SWAP": 95,
        },
        **continuous_session_updates(),
    )
    await service.recover()
    await service.arm()

    run = await service.run_once(execute=True, submission_limit=1)

    assert len(demo.place_calls) == 1
    assert run.results[1].detail == "run_submission_limit_reached"


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


def fake_protection_id(instrument_id: str) -> str:
    return "PROT" + instrument_id.replace("-", "")[:24]


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
        protection_client_order_id=fake_protection_id(instrument_id),
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


def test_active_trade_recovery_preserves_margin_above_current_equity() -> None:
    trade = tracked_trade(
        "BTC-USDT-SWAP",
        started_at=datetime.now(timezone.utc) - timedelta(minutes=5),
    )
    payload = trade.model_dump(mode="json")
    payload["estimated_stop_loss_pct"] = "1.5"
    payload["margin_allocation_pct"] = "1.25"
    payload["estimated_margin"] = "12500"

    recovered = DemoAutomationActiveTrade.model_validate(payload)

    assert recovered.estimated_stop_loss_pct == Decimal("1.5")
    assert recovered.margin_allocation_pct == Decimal("1.25")
    assert recovered.estimated_margin == Decimal("12500")


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
async def test_capital_bucket_caps_high_score_position_at_2000_usdt_margin() -> None:
    demo = FakeDemo()
    high = candidate(score=95, stop_loss="99.9", take_profit="102")
    service = adaptive_service(
        demo,
        {"BTC-USDT-SWAP": 95},
        candidates={"BTC-USDT-SWAP": high},
        okx_demo_capital_bucket_enabled=True,
    )
    await service.recover()

    run = await service.run_once(execute=False)
    result = run.results[0]

    assert result.outcome == "approved_dry_run"
    assert result.selected_leverage == 3
    assert result.position_margin_cap_usdt == Decimal("2000")
    assert result.capital_bucket_usdt == Decimal("2000")
    assert result.estimated_margin == Decimal("2000")
    assert result.approved_contracts == Decimal("60")
    assert run.capital_bucket_enabled is True
    assert run.capital_bucket_position_limit == 3
    assert run.portfolio_estimated_margin == Decimal("2000")
    assert demo.place_calls == []


@pytest.mark.asyncio
async def test_capital_bucket_execute_uses_the_dry_run_contract_size() -> None:
    demo = FakeDemo()
    high = candidate(score=95, stop_loss="99.9", take_profit="102")
    service = adaptive_service(
        demo,
        {"BTC-USDT-SWAP": 95},
        candidates={"BTC-USDT-SWAP": high},
        okx_demo_capital_bucket_enabled=True,
    )
    await service.recover()
    await service.arm()

    result = (await service.run_once(execute=True)).results[0]

    assert result.outcome == "submitted"
    assert result.position_margin_cap_usdt == Decimal("2000")
    assert result.estimated_margin == Decimal("2000")
    assert len(demo.leverage_calls) == 1
    assert demo.leverage_calls[0].leverage == 3
    assert demo.leverage_calls[0].margin_mode == "cross"
    assert len(demo.place_calls) == 1
    assert demo.place_calls[0].size == Decimal("60")
    assert demo.place_calls[0].margin_mode == "cross"


@pytest.mark.asyncio
async def test_capital_below_2000_forms_one_full_equity_slot() -> None:
    demo = FakeDemo()
    demo.equity = Decimal("1500")
    high = candidate(score=95, stop_loss="99.9", take_profit="102")
    service = adaptive_service(
        demo,
        {"BTC-USDT-SWAP": 95, "ETH-USDT-SWAP": 95},
        candidates={
            "BTC-USDT-SWAP": high,
            "ETH-USDT-SWAP": high,
        },
        okx_demo_capital_bucket_enabled=True,
    )
    await service.recover()

    run = await service.run_once(execute=False)
    approved = [item for item in run.results if item.outcome == "approved_dry_run"]
    blocked = [item for item in run.results if item.outcome == "blocked"]

    assert len(approved) == 1
    assert approved[0].position_margin_cap_usdt == Decimal("1500")
    assert approved[0].estimated_margin == Decimal("1500")
    assert len(blocked) == 1
    assert blocked[0].detail == "portfolio_open_position_limit_reached"
    assert run.capital_bucket_position_limit == 1
    assert run.active_position_count == 1
    status = await service.status()
    assert status.max_open_positions == 1
    assert status.capital_bucket_enabled is True
    assert status.capital_bucket_usdt == Decimal("2000")
    assert status.capital_bucket_position_limit == 1


@pytest.mark.asyncio
async def test_capital_bucket_uses_only_complete_2000_usdt_position_slots() -> None:
    demo = FakeDemo()
    demo.equity = Decimal("4998.339000436543")
    high = candidate(score=95, stop_loss="99.9", take_profit="102")
    symbols = ["BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP"]
    service = adaptive_service(
        demo,
        {symbol: 95 for symbol in symbols},
        candidates={symbol: high for symbol in symbols},
        okx_demo_capital_bucket_enabled=True,
    )
    await service.recover()

    run = await service.run_once(execute=False)
    approved = [item for item in run.results if item.outcome == "approved_dry_run"]
    blocked = [item for item in run.results if item.outcome == "blocked"]

    assert len(approved) == 2
    assert [item.estimated_margin for item in approved] == [
        Decimal("2000"),
        Decimal("2000"),
    ]
    assert len(blocked) == 1
    assert blocked[0].detail == "portfolio_open_position_limit_reached"
    assert run.capital_bucket_position_limit == 2
    assert run.portfolio_estimated_margin == Decimal("4000")


@pytest.mark.asyncio
async def test_status_position_limit_tracks_latest_equity_across_bucket_boundary() -> None:
    demo = FakeDemo()
    demo.equity = Decimal("6000")
    service = adaptive_service(
        demo,
        {"BTC-USDT-SWAP": 95},
        okx_demo_capital_bucket_enabled=True,
    )
    await service.recover()
    await service.run_once(execute=False)

    assert (await service.status()).capital_bucket_position_limit == 3

    demo.equity = Decimal("5999.99")
    await service.run_once(execute=False)
    status = await service.status()

    assert status.capital_bucket_position_limit == 2
    assert status.max_open_positions == 2


@pytest.mark.asyncio
async def test_stop_risk_and_available_equity_can_only_reduce_bucket_size() -> None:
    demo = FakeDemo()
    demo.equity = Decimal("5000")
    demo.available_equity = Decimal("750")
    high = candidate(score=95, stop_loss="99.9", take_profit="102")
    service = adaptive_service(
        demo,
        {"BTC-USDT-SWAP": 95},
        candidates={"BTC-USDT-SWAP": high},
        okx_demo_capital_bucket_enabled=True,
    )
    await service.recover()

    result = (await service.run_once(execute=False)).results[0]

    assert result.outcome == "approved_dry_run"
    assert result.position_margin_cap_usdt == Decimal("750")
    assert result.estimated_margin == Decimal("733.3333333333333333333333333")
    assert result.estimated_margin <= result.position_margin_cap_usdt


@pytest.mark.asyncio
async def test_reconciled_active_trade_above_stored_bucket_blocks_new_work() -> None:
    demo = FakeDemo(exposed=True)
    service = adaptive_service(
        demo,
        {"ETH-USDT-SWAP": 95},
        okx_demo_capital_bucket_enabled=True,
    )
    await service.recover()
    prime_usdt_equity_basis(service, demo)
    oversized = tracked_trade(
        "BTC-USDT-SWAP",
        started_at=datetime.now(timezone.utc) - timedelta(minutes=5),
    ).model_copy(
        update={
            "estimated_notional": Decimal("6003"),
            "estimated_margin": Decimal("2001"),
            "margin_allocation_pct": Decimal("0.2001"),
            "position_margin_cap_usdt": Decimal("2000"),
            "capital_bucket_usdt": Decimal("2000"),
        }
    )
    service._set_active_trade(oversized)

    run = await service.run_once(execute=False)

    assert len(run.results) == 1
    assert run.results[0].outcome == "blocked"
    assert run.results[0].detail == "active_trade_exceeds_position_margin_bucket"
    assert demo.place_calls == []


@pytest.mark.asyncio
async def test_execute_run_emergency_stops_when_active_margin_exceeds_bucket() -> None:
    demo = FakeDemo(exposed=True)
    service = adaptive_service(
        demo,
        {"ETH-USDT-SWAP": 95},
        okx_demo_capital_bucket_enabled=True,
    )
    await service.recover()
    prime_usdt_equity_basis(service, demo)
    service._set_active_trade(
        tracked_trade(
            "BTC-USDT-SWAP",
            started_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        ).model_copy(
            update={
                "estimated_notional": Decimal("6003"),
                "estimated_margin": Decimal("2001"),
                "margin_allocation_pct": Decimal("0.2001"),
                "position_margin_cap_usdt": Decimal("2000"),
                "capital_bucket_usdt": Decimal("2000"),
            }
        )
    )
    service._state["armed"] = True

    run = await service.run_once(execute=True)
    status = await service.status()

    assert run.results[0].outcome == "locked"
    assert "active_trade_exceeds_position_margin_bucket" in run.results[0].reason_codes
    assert status.emergency_stop is True
    assert status.armed is False
    assert demo.place_calls == []


@pytest.mark.asyncio
async def test_execute_run_emergency_stops_when_open_stop_risk_exceeds_limit() -> None:
    demo = FakeDemo(exposed=True)
    service = adaptive_service(
        demo,
        {"ETH-USDT-SWAP": 95},
        okx_demo_capital_bucket_enabled=True,
    )
    await service.recover()
    prime_usdt_equity_basis(service, demo)
    service._set_active_trade(
        tracked_trade(
            "BTC-USDT-SWAP",
            started_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        ).model_copy(
            update={
                "estimated_stop_loss_amount": Decimal("300"),
                "estimated_stop_loss_pct": Decimal("0.03"),
                "position_margin_cap_usdt": Decimal("2000"),
                "capital_bucket_usdt": Decimal("2000"),
            }
        )
    )
    service._state["armed"] = True

    run = await service.run_once(execute=True)

    assert run.results[0].outcome == "locked"
    assert "active_portfolio_stop_risk_limit_exceeded" in (
        run.results[0].reason_codes
    )
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
async def test_shadow_portfolio_enforces_aggregate_open_stop_risk() -> None:
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
async def test_single_legacy_trade_never_uses_account_equity_delta_as_pnl() -> None:
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

    assert status.consecutive_losses == 0
    assert status.emergency_stop is True
    assert status.locked is True
    assert "trade_outcome_unconfirmed" in status.lock_reasons
    assert status.active_position_count == 1
    assert service._state["realized_pnl_events"] == []


@pytest.mark.asyncio
async def test_account_deposit_is_not_attributed_to_closed_trade() -> None:
    demo = FakeDemo()
    demo.equity = Decimal("10150")
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

    assert status.emergency_stop is True
    assert "trade_outcome_unconfirmed" in status.lock_reasons
    assert status.active_position_count == 1
    assert service._state["realized_pnl_events"] == []


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


@pytest.mark.asyncio
async def test_unconfirmed_leverage_stops_before_any_demo_order() -> None:
    demo = FakeDemo(leverage_acknowledged=False)
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

    assert run.results[0].outcome == "error"
    assert (
        run.results[0].detail
        == "DemoAutomationSafetyError: "
        "okx_demo_leverage_exchange_response_unconfirmed"
    )
    assert demo.place_calls == []
    assert status.emergency_stop is True
    assert "leverage_configuration_unconfirmed" in status.lock_reasons


@pytest.mark.asyncio
async def test_unconfirmed_protection_stops_after_ack_without_auto_close() -> None:
    demo = FakeDemo(protection_confirmed=False)
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

    assert run.results[0].outcome == "error"
    assert (
        run.results[0].detail
        == "DemoAutomationSafetyError: okx_demo_order_protection_unconfirmed"
    )
    assert len(demo.place_calls) == 1
    assert status.emergency_stop is True
    assert "post_submission_protection_unconfirmed" in status.lock_reasons
    # The exchange is authoritative after acknowledgement.  The safety stop
    # never guesses that exposure vanished and never silently closes it.
    assert status.active_position_count == 1


@pytest.mark.asyncio
async def test_open_tracked_position_without_matching_algo_protection_stops() -> None:
    demo = FakeDemo(exposed=True)
    demo.protection_present = False
    service = adaptive_service(demo, {"ETH-USDT-SWAP": 95})
    await service.recover()
    prime_usdt_equity_basis(service, demo)
    service._set_active_trade(
        tracked_trade(
            "BTC-USDT-SWAP",
            started_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        )
    )
    service._state["armed"] = True

    run = await service.run_once(execute=True)
    status = await service.status()

    assert run.results[0].outcome == "locked"
    assert (
        "tracked_position_protection_missing_or_mismatched"
        in run.results[0].reason_codes
    )
    assert status.emergency_stop is True
    assert status.armed is False
    assert status.active_position_count == 1
    assert demo.place_calls == []


@pytest.mark.asyncio
async def test_structural_dynamic_demo_uses_isolated_20x_only_after_all_gates() -> None:
    demo = FakeDemo()
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

    result = run.results[0]
    assert result.outcome == "submitted"
    assert result.protection_model == "structure"
    assert result.margin_mode == "isolated"
    assert result.selected_leverage == 20
    # 6% of 10,000 USDT must be funded from one 2,000-USDT bucket.
    # With a 0.26% stop-plus-cost rate this would require 116x, so the
    # executable result remains capped at 20x and consumes less than the
    # nominal risk budget.
    assert result.required_leverage == 116
    assert result.leverage_cap == 20
    assert result.leverage_cap_reasons == [
        "required_leverage_exceeds_20x_safety_cap"
    ]
    assert result.stop_loss == Decimal("99.9")
    assert result.take_profit == Decimal("101")
    assert result.estimated_round_trip_cost_pct == Decimal("0.0016")
    assert result.net_risk_reward is not None
    assert result.net_risk_reward >= Decimal("2")
    assert demo.leverage_calls[0].margin_mode == "isolated"
    assert demo.leverage_calls[0].leverage == 20
    assert demo.place_calls[0].margin_mode == "isolated"
    assert status.active_trades[0].protection_client_order_id == (
        fake_protection_id("BTC-USDT-SWAP")
    )
    assert status.structural_dynamic_leverage_enabled is True
    assert status.structural_margin_mode == "isolated"


@pytest.mark.asyncio
async def test_structural_dynamic_demo_fails_closed_without_structure() -> None:
    demo = FakeDemo()
    service = adaptive_service(
        demo,
        {"BTC-USDT-SWAP": 99},
        candidates={
            "BTC-USDT-SWAP": with_mathematical_confirmation(
                candidate(score=99),
                status="confirmed",
                risk_grade="high",
            )
        },
        **structural_dynamic_updates(),
    )
    await service.recover()

    run = await service.run_once(execute=False)

    assert run.results[0].outcome == "blocked"
    assert run.results[0].detail == "structural_protection_geometry_unavailable"
    assert demo.place_calls == []


@pytest.mark.asyncio
async def test_150_usdt_structural_profile_uses_one_full_equity_margin_ceiling() -> None:
    demo = FakeDemo()
    demo.equity = Decimal("150")
    service = adaptive_service(
        demo,
        {"BTC-USDT-SWAP": 99},
        candidates={"BTC-USDT-SWAP": structural_demo_candidate()},
        **structural_dynamic_updates(),
    )
    await service.recover()

    run = await service.run_once(execute=False)

    result = run.results[0]
    assert result.outcome == "approved_dry_run"
    assert run.risk_equity == Decimal("150")
    assert run.capital_bucket_position_limit == 1
    assert result.position_margin_cap_usdt == Decimal("150")
    assert result.estimated_margin == Decimal("150")
    assert result.selected_leverage == 20
    assert result.estimated_stop_loss_pct == Decimal("0.052")
    assert result.estimated_stop_loss_pct < result.risk_budget_pct
    assert demo.place_calls == []


@pytest.mark.asyncio
async def test_rolling_seven_day_realized_pnl_prunes_old_and_deduplicates() -> None:
    demo = FakeDemo()
    service = adaptive_service(demo, {"BTC-USDT-SWAP": 95})
    await service.recover()
    now = datetime.now(timezone.utc)
    trade = tracked_trade(
        "BTC-USDT-SWAP", started_at=now - timedelta(hours=2)
    )

    service._record_realized_pnl_event(trade, now - timedelta(hours=1), Decimal("-12"))
    service._record_realized_pnl_event(trade, now - timedelta(hours=1), Decimal("-12"))
    service._state["realized_pnl_events"].append(
        {
            "event_id": "expired",
            "instrument_id": "ETH-USDT-SWAP",
            "closed_at": (now - timedelta(days=8)).isoformat(),
            "net_pnl": "-999",
        }
    )

    assert service._rolling_realized_pnl(now) == Decimal("-12")
    assert len(service._state["realized_pnl_events"]) == 1


def test_closing_pnl_deduplicates_repeated_exchange_order_id() -> None:
    started_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    closed_at = started_at + timedelta(minutes=1)
    trade = tracked_trade("BTC-USDT-SWAP", started_at=started_at)
    order = OkxDemoOrderView(
        order_id="same-close-order",
        instrument_id="BTC-USDT-SWAP",
        side="sell",
        position_side="long",
        order_type="market",
        state="filled",
        size=Decimal("1"),
        accumulated_fill_size=Decimal("1"),
        reduce_only=True,
        updated_at=closed_at,
        raw={"pnl": "10", "fee": "-1"},
    )

    outcome = SafeDemoAutomation._closing_trade_outcome(
        trade,
        [order, order.model_copy(deep=True)],
    )

    assert outcome == (closed_at, Decimal("9"))


@pytest.mark.asyncio
async def test_risk_high_water_does_not_reset_at_utc_day_boundary() -> None:
    demo = FakeDemo()
    service = adaptive_service(demo, {"BTC-USDT-SWAP": 95})
    await service.recover()
    service._state["equity_basis"] = "single_currency:USDT"
    service._state["session_date"] = datetime.now(timezone.utc).date() - timedelta(days=1)
    service._state["baseline_equity"] = Decimal("11000")
    service._state["peak_equity"] = Decimal("11500")
    service._state["risk_peak_equity"] = Decimal("12000")

    blocker = service._roll_session(
        Decimal("10000"),
        "single_currency:USDT",
    )

    assert blocker is None
    assert service._state["peak_equity"] == Decimal("10000")
    assert service._state["risk_peak_equity"] == Decimal("12000")
