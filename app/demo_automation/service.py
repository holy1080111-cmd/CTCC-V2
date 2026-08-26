from __future__ import annotations

import asyncio
from collections import deque
from contextlib import suppress
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, ROUND_DOWN
import hashlib
import logging
from typing import Any, Iterable

from app.config.settings import Settings, get_settings
from app.database.repositories.demo_automation import DemoAutomationRepository
from app.demo_automation import DemoAutomationBusyError, DemoAutomationSafetyError
from app.demo_automation.capital_bucket import (
    DemoCapitalBucketPlan,
    build_demo_capital_bucket_plan,
    demo_position_notional_ceiling,
)
from app.demo_automation.execution_quality import (
    adverse_fill_slippage_bps,
    bounded_fok_execution_price,
    candidate_at_execution_price,
    execution_quality_at_price,
)
from app.demo_automation.risk_profile import (
    configured_score_risk_tiers,
    mathematical_adjusted_score,
    score_risk_tier,
)
from app.demo_automation.structural_risk import (
    apply_cost_adjusted_reward_risk,
    candidate_with_structural_prices,
    select_structural_leverage,
    structural_cost_rate,
)
from app.domain.demo_automation import (
    DemoAutomationActiveTrade,
    DemoAutomationRiskTier,
    DemoAutomationRunResult,
    DemoAutomationStatus,
    DemoAutomationSymbolResult,
)
from app.domain.okx_demo import (
    DEMO_CONFIRMATION_PHRASE,
    OkxDemoAlgoOrderView,
    OkxDemoLeverageRequest,
    OkxDemoOrderView,
    OkxDemoOrderRequest,
    OkxDemoReconcileResult,
)
from app.domain.realtime import RealtimeSnapshot
from app.domain.risk import AccountRiskState, RiskLimits
from app.domain.strategy import StrategyDecision, TradeCandidate
from app.exchange.okx.public_rest import OkxPublicRestClient
from app.exchange.okx.symbols import to_instrument_id
from app.okx_demo.equity import DemoRiskCapital, resolve_demo_risk_capital
from app.okx_demo.service import OkxDemoService, okx_demo_service
from app.risk import RiskService
from app.strategies import StrategyService

logger = logging.getLogger(__name__)
D = Decimal
_AUTOMATION_SETTLEMENT_CURRENCY = "USDT"
_EQUITY_BASIS_LOCK = "equity_basis_change_requires_flat_session"


class SafeDemoAutomation:
    """Explicitly armed, Demo-only strategy automation.

    The service never enables real trading, never auto-arms after restart, and
    keeps every submitted position protected and enforces aggregate portfolio
    risk before any Demo write.
    """

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        strategy_service: StrategyService | None = None,
        risk_service: RiskService | None = None,
        demo_service: OkxDemoService | None = None,
        public_client: OkxPublicRestClient | None = None,
        repository: DemoAutomationRepository | None = None,
        strategy_control_repository=None,
        market_hub=None,
        market_client=None,
    ) -> None:
        self.settings = settings or get_settings()
        self.strategy_service = strategy_service or StrategyService()
        self.risk_service = risk_service or RiskService()
        self.demo_service = demo_service or okx_demo_service
        self.public_client = public_client or OkxPublicRestClient()
        self.repository = repository
        if repository is None and self.settings.environment != "test":
            from app.database.session import AsyncSessionFactory

            self.repository = DemoAutomationRepository(AsyncSessionFactory)
        self.strategy_control_repository = strategy_control_repository
        if strategy_control_repository is None and self.settings.environment != "test":
            from app.database.repositories.performance import DemoPerformanceRepository
            from app.database.session import AsyncSessionFactory

            self.strategy_control_repository = DemoPerformanceRepository(AsyncSessionFactory)
        if market_hub is None or market_client is None:
            if self.settings.environment == "test":
                class _NullHub:
                    async def snapshot(self, _symbol: str):
                        return None

                class _NullClient:
                    class _Status:
                        connected = False

                    def status(self):
                        return self._Status()

                realtime_hub = _NullHub()
                realtime_client = _NullClient()
            else:
                from app.market.realtime_service import realtime_client, realtime_hub
        self.market_hub = market_hub or realtime_hub
        self.market_client = market_client or realtime_client

        today = datetime.now(timezone.utc).date()
        self._state: dict[str, Any] = {
            "armed": False,
            "emergency_stop": False,
            "locked": False,
            "lock_reasons": [],
            "session_date": today,
            "equity_basis": None,
            "baseline_equity": None,
            "peak_equity": None,
            "risk_peak_equity": None,
            "daily_pnl": D("0"),
            "trades_today": 0,
            "consecutive_losses": 0,
            "active_instrument_id": None,
            "active_client_order_id": None,
            "active_start_equity": None,
            "active_started_at": None,
            "active_trades": {},
            "symbol_cooldowns": {},
            "realized_pnl_events": [],
            "last_trade_closed_at": None,
            "last_started_at": None,
            "last_completed_at": None,
            "last_error": None,
        }
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._run_lock = asyncio.Lock()
        self._next_run_at: datetime | None = None
        self._recovered = False
        self._history: deque[DemoAutomationRunResult] = deque(
            maxlen=self.settings.okx_demo_automation_history_limit
        )
        self._fingerprints: dict[str, datetime] = {}

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def recover(self) -> None:
        if self.repository is not None:
            stored = await self.repository.load_state()
            if stored:
                self._state.update(stored)
            history = await self.repository.load_runs(
                self.settings.okx_demo_automation_history_limit
            )
            self._history = deque(
                history, maxlen=self.settings.okx_demo_automation_history_limit
            )
        # Deliberately never restore an armed state after process restart.
        self._state["armed"] = False
        self._state["last_error"] = None
        self._normalize_portfolio_state()
        self._clear_disabled_continuous_session_locks()
        self._recovered = True
        await self._persist_state(required=False)

    async def status(self) -> DemoAutomationStatus:
        active_trades = self._active_trades()
        open_risk, margin_pct = self._portfolio_usage(active_trades)
        baseline_equity = self._state["baseline_equity"]
        status_equity = (
            baseline_equity + self._state["daily_pnl"]
            if isinstance(baseline_equity, Decimal)
            else None
        )
        bucket_plan = (
            self._capital_bucket_plan(status_equity, status_equity)
            if isinstance(status_equity, Decimal) and status_equity > 0
            else None
        )
        return DemoAutomationStatus(
            capability_enabled=self.settings.okx_demo_auto_execution,
            trading_mode=self.settings.trading_mode,
            demo_writes_enabled=self.settings.okx_demo_allow_order_writes,
            armed=bool(self._state["armed"]),
            running=self.running,
            run_in_progress=self._run_lock.locked(),
            emergency_stop=bool(self._state["emergency_stop"]),
            locked=bool(self._state["locked"]),
            lock_reasons=list(self._state["lock_reasons"]),
            configuration_blockers=self._configuration_blockers(),
            symbols=self.settings.okx_demo_scan_symbol_list,
            scan_interval_seconds=self.settings.okx_demo_scan_interval_seconds,
            execution_order_type="fok",
            execution_max_adverse_slippage_bps=D(
                str(self.settings.okx_demo_execution_max_adverse_slippage_bps)
            ),
            minimum_execution_risk_reward=D(
                str(
                    self.settings.okx_demo_structural_min_net_risk_reward
                    if self.settings.okx_demo_structural_dynamic_leverage_enabled
                    else self.settings.strategy_min_risk_reward
                )
            ),
            max_trades_per_day=self.settings.okx_demo_max_trades_per_day,
            daily_loss_limit_pct=D(str(self.settings.okx_demo_daily_loss_limit_pct)),
            max_consecutive_losses=self.settings.okx_demo_automation_max_consecutive_losses,
            continuous_session_enabled=(
                self.settings.okx_demo_continuous_session_enabled
            ),
            daily_loss_limit_enforced=(
                not self.settings.okx_demo_continuous_session_enabled
            ),
            daily_trade_limit_enforced=(
                not self.settings.okx_demo_continuous_session_enabled
            ),
            consecutive_loss_limit_enforced=(
                not self.settings.okx_demo_continuous_session_enabled
            ),
            effective_trade_cooldown_seconds=(
                self._effective_trade_cooldown_seconds()
            ),
            score_risk_enabled=self.settings.okx_demo_score_risk_enabled,
            derivative_risk_gate_enabled=self.settings.okx_demo_score_risk_enabled,
            mathematical_risk_gate_enabled=self.settings.okx_demo_score_risk_enabled,
            structural_dynamic_leverage_enabled=(
                self.settings.okx_demo_structural_dynamic_leverage_enabled
            ),
            structural_margin_mode=(
                "isolated"
                if self.settings.okx_demo_structural_dynamic_leverage_enabled
                else None
            ),
            structural_min_net_risk_reward=(
                D(str(self.settings.okx_demo_structural_min_net_risk_reward))
                if self.settings.okx_demo_structural_dynamic_leverage_enabled
                else None
            ),
            structural_estimated_cost_bps=(
                structural_cost_rate(self.settings) * D("10000")
                if self.settings.okx_demo_structural_dynamic_leverage_enabled
                else None
            ),
            score_risk_tiers=(
                configured_score_risk_tiers(self.settings)
                if self.settings.okx_demo_score_risk_enabled
                else []
            ),
            max_open_positions=self._portfolio_position_limit(status_equity),
            portfolio_max_risk_pct=(
                D(str(self.settings.okx_demo_portfolio_max_risk_pct))
                if self.settings.okx_demo_score_risk_enabled
                else D("0")
            ),
            portfolio_max_margin_pct=(
                D(str(self.settings.okx_demo_portfolio_max_margin_pct))
                if self.settings.okx_demo_score_risk_enabled
                and not self.settings.okx_demo_capital_bucket_enabled
                else D("0")
            ),
            capital_bucket_enabled=self.settings.okx_demo_capital_bucket_enabled,
            capital_bucket_usdt=(
                D(str(self.settings.okx_demo_position_margin_bucket_usdt))
                if self.settings.okx_demo_capital_bucket_enabled
                else None
            ),
            capital_bucket_position_limit=(
                bucket_plan.effective_position_limit
                if bucket_plan is not None
                else None
            ),
            portfolio_open_risk_pct=open_risk,
            portfolio_margin_pct=margin_pct,
            portfolio_estimated_margin=sum(
                (max(D("0"), item.estimated_margin) for item in active_trades),
                D("0"),
            ),
            active_position_count=len(active_trades),
            active_trades=active_trades,
            session_date=self._state["session_date"],
            equity_basis=self._state["equity_basis"],
            baseline_equity=self._state["baseline_equity"],
            peak_equity=self._state["peak_equity"],
            risk_peak_equity=self._state["risk_peak_equity"],
            daily_pnl=self._state["daily_pnl"],
            rolling_7d_realized_pnl=self._rolling_realized_pnl(),
            trades_today=int(self._state["trades_today"]),
            consecutive_losses=int(self._state["consecutive_losses"]),
            active_instrument_id=self._state["active_instrument_id"],
            active_client_order_id=self._state["active_client_order_id"],
            active_started_at=self._state["active_started_at"],
            last_trade_closed_at=self._state["last_trade_closed_at"],
            last_started_at=self._state["last_started_at"],
            last_completed_at=self._state["last_completed_at"],
            next_run_at=self._next_run_at,
            last_error=self._state["last_error"],
            recovered=self._recovered,
        )

    async def history(self, limit: int = 20) -> list[DemoAutomationRunResult]:
        return list(self._history)[-max(1, limit):]

    async def arm(self) -> DemoAutomationStatus:
        blockers = self._configuration_blockers()
        if blockers:
            raise DemoAutomationSafetyError(";".join(blockers))
        if self._state["emergency_stop"]:
            raise DemoAutomationSafetyError("emergency_stop_must_be_cleared")
        snapshot = await self.demo_service.reconcile()
        if snapshot.positions or snapshot.pending_orders or snapshot.pending_algo_orders:
            raise DemoAutomationSafetyError("exchange_exposure_must_be_zero_before_arming")
        if self._active_trades():
            raise DemoAutomationSafetyError(
                "tracked_trade_state_must_be_resolved_before_arming"
            )
        capital, capital_blocker = self._automation_capital(snapshot)
        if capital is None:
            raise DemoAutomationSafetyError(capital_blocker)
        self._ensure_capital_bucket_currency(capital)
        basis_blocker = self._roll_session(
            capital.risk_equity,
            capital.basis,
            force_if_empty=True,
            allow_rebase=int(self._state["trades_today"]) == 0,
        )
        if basis_blocker is not None:
            raise DemoAutomationSafetyError(basis_blocker)
        self._apply_locks(capital.risk_equity)
        if self._state["locked"]:
            raise DemoAutomationSafetyError("automation_locked:" + ",".join(self._state["lock_reasons"]))
        self._state["armed"] = True
        await self._persist_state(required=True)
        return await self.status()

    async def disarm(self) -> DemoAutomationStatus:
        self._state["armed"] = False
        await self.stop()
        await self._persist_state(required=False)
        return await self.status()

    async def emergency_stop(self) -> DemoAutomationStatus:
        self._state["armed"] = False
        self._state["emergency_stop"] = True
        self._state["locked"] = True
        self._state["lock_reasons"] = sorted(
            set([*self._state["lock_reasons"], "emergency_stop_engaged"])
        )
        await self.stop()
        await self._persist_state(required=False)
        return await self.status()

    async def clear_emergency_stop(self) -> DemoAutomationStatus:
        snapshot = await self.demo_service.reconcile()
        if snapshot.positions or snapshot.pending_orders or snapshot.pending_algo_orders:
            raise DemoAutomationSafetyError("exchange_exposure_must_be_zero_before_clearing_stop")
        if self._active_trades():
            raise DemoAutomationSafetyError(
                "tracked_trade_state_must_be_resolved_before_clearing_stop"
            )
        capital, capital_blocker = self._automation_capital(snapshot)
        if capital is None:
            raise DemoAutomationSafetyError(capital_blocker)
        basis_blocker = self._roll_session(
            capital.risk_equity,
            capital.basis,
            force_if_empty=True,
            allow_rebase=int(self._state["trades_today"]) == 0,
        )
        if basis_blocker is not None:
            raise DemoAutomationSafetyError(basis_blocker)
        self._state["emergency_stop"] = False
        self._apply_locks(capital.risk_equity)
        await self._persist_state(required=True)
        return await self.status()

    async def start(self) -> DemoAutomationStatus:
        self._clear_disabled_continuous_session_locks()
        self._ensure_execute_ready()
        if not self._recovered:
            raise DemoAutomationSafetyError("demo_automation_recovery_not_completed")
        if self.running:
            return await self.status()
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(self._loop(), name="safe-okx-demo-automation")
        return await self.status()

    async def stop(self) -> DemoAutomationStatus:
        if self._task is not None:
            self._stop.set()
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        self._next_run_at = None
        return await self.status()

    async def run_once(
        self,
        *,
        symbols: Iterable[str] | None = None,
        execute: bool = False,
        trigger: str = "manual",
        submission_limit: int | None = None,
    ) -> DemoAutomationRunResult:
        if submission_limit is not None and submission_limit < 0:
            raise ValueError("submission_limit_must_be_nonnegative")
        if submission_limit is not None and not execute:
            raise ValueError("submission_limit_requires_execute_run")
        if self._run_lock.locked():
            raise DemoAutomationBusyError("demo_automation_run_already_in_progress")
        if execute:
            self._ensure_execute_ready(allow_session_lock_refresh=True)

        async with self._run_lock:
            started = datetime.now(timezone.utc)
            self._state["last_started_at"] = started
            results: list[DemoAutomationSymbolResult] = []
            total_equity: Decimal | None = None
            risk_equity: Decimal | None = None
            risk_equity_currency: str | None = None
            capital_bucket_position_limit: int | None = None
            available_margin_equity = D("0")
            portfolio_view = self._active_trades()
            try:
                snapshot = await self.demo_service.reconcile()
                total_equity = snapshot.balance.total_equity
                exposed_symbols = {
                    item.instrument_id
                    for item in [
                        *snapshot.positions,
                        *snapshot.pending_orders,
                        *snapshot.pending_algo_orders,
                    ]
                }
                capital, capital_blocker = self._automation_capital(snapshot)
                if capital is None:
                    raise DemoAutomationSafetyError(capital_blocker)
                self._ensure_capital_bucket_currency(capital)
                risk_equity = capital.risk_equity
                risk_equity_currency = capital.currency
                available_margin_equity = capital.available_equity
                bucket_plan = self._capital_bucket_plan(
                    risk_equity,
                    available_margin_equity,
                )
                if bucket_plan is not None:
                    capital_bucket_position_limit = (
                        bucket_plan.effective_position_limit
                    )
                basis_blocker = self._roll_session(
                    risk_equity,
                    capital.basis,
                    allow_rebase=(
                        not exposed_symbols
                        and not portfolio_view
                        and int(self._state["trades_today"]) == 0
                    ),
                )
                if basis_blocker is not None:
                    raise DemoAutomationSafetyError(basis_blocker)
                self._finalize_active_trades(snapshot)
                self._refresh_active_trade_estimates(snapshot, risk_equity)
                self._apply_locks(risk_equity)
                portfolio_view = self._active_trades()

                tracked_symbols = {item.instrument_id for item in portfolio_view}
                untracked_symbols = exposed_symbols - tracked_symbols
                position_symbols = [item.instrument_id for item in snapshot.positions]
                exposure_violation = self._active_protection_violation(
                    snapshot,
                    portfolio_view,
                )
                if untracked_symbols:
                    exposure_violation = "untracked_exchange_exposure_detected"
                elif exposure_violation is not None:
                    pass
                elif len(position_symbols) > self._portfolio_position_limit(
                    risk_equity
                ):
                    exposure_violation = "exchange_position_limit_exceeded"
                elif len(position_symbols) != len(set(position_symbols)):
                    exposure_violation = "multiple_positions_per_instrument_detected"
                elif (
                    self.settings.okx_demo_score_risk_enabled
                    and self._portfolio_usage(portfolio_view)[0]
                    > D(str(self.settings.okx_demo_portfolio_max_risk_pct))
                ):
                    exposure_violation = "active_portfolio_stop_risk_limit_exceeded"
                elif (
                    self.settings.okx_demo_score_risk_enabled
                    and bucket_plan is None
                    and self._portfolio_usage(portfolio_view)[1]
                    > D(str(self.settings.okx_demo_portfolio_max_margin_pct))
                ):
                    exposure_violation = "active_portfolio_margin_limit_exceeded"
                elif bucket_plan is not None:
                    exposure_violation = self._capital_bucket_violation(
                        portfolio_view,
                        bucket_plan,
                    )
                if execute and exposure_violation:
                    self._engage_emergency(exposure_violation)
                    self._apply_locks(risk_equity)

                if execute and self._state["locked"]:
                    results.append(
                        DemoAutomationSymbolResult(
                            symbol="*",
                            outcome="locked",
                            reason_codes=list(self._state["lock_reasons"]),
                            detail="automation_safety_lock_active",
                        )
                    )
                elif untracked_symbols:
                    results.append(
                        DemoAutomationSymbolResult(
                            symbol="*",
                            outcome="blocked",
                            detail=(
                                "untracked_exchange_exposure_blocks_automation:"
                                + ",".join(sorted(untracked_symbols))
                            ),
                        )
                    )
                elif exposure_violation:
                    results.append(
                        DemoAutomationSymbolResult(
                            symbol="*",
                            outcome="blocked",
                            detail=exposure_violation,
                        )
                    )
                else:
                    requested = list(symbols or self.settings.okx_demo_scan_symbol_list)
                    initial_results, ranked = await self._rank_requested_symbols(
                        requested, started
                    )
                    results.extend(initial_results)
                    shadow_portfolio = list(portfolio_view)
                    submitted_this_run = 0
                    for raw_symbol, strategy in ranked:
                        if execute:
                            self._apply_locks(risk_equity)
                            if self._state["locked"]:
                                results.append(
                                    DemoAutomationSymbolResult(
                                        symbol=raw_symbol,
                                        instrument_id=strategy.instrument_id,
                                        outcome="locked",
                                        reason_codes=list(self._state["lock_reasons"]),
                                        detail="automation_safety_lock_active",
                                    )
                                )
                                break
                            if (
                                submission_limit is not None
                                and submitted_this_run >= submission_limit
                            ):
                                results.append(
                                    DemoAutomationSymbolResult(
                                        symbol=raw_symbol,
                                        instrument_id=strategy.instrument_id,
                                        outcome="blocked",
                                        detail="run_submission_limit_reached",
                                    )
                                )
                                break
                        result, reservation = await self._process_symbol(
                            raw_symbol,
                            strategy,
                            execute=execute,
                            balance_equity=risk_equity,
                            available_margin_equity=available_margin_equity,
                            portfolio=shadow_portfolio,
                        )
                        results.append(result)
                        if result.order_submission_attempted:
                            submitted_this_run += 1
                        if reservation is not None:
                            shadow_portfolio.append(reservation)
                            available_margin_equity = max(
                                D("0"),
                                available_margin_equity
                                - reservation.estimated_margin,
                            )
                    portfolio_view = (
                        self._active_trades() if execute else shadow_portfolio
                    )
            except DemoAutomationSafetyError as exc:
                self._state["last_error"] = self._safe_error(exc)
                results.append(
                    DemoAutomationSymbolResult(
                        symbol="*",
                        outcome="blocked",
                        detail=self._state["last_error"],
                    )
                )
            except Exception as exc:
                self._state["last_error"] = self._safe_error(exc)
                logger.exception("demo_automation_run_failed")
                results.append(
                    DemoAutomationSymbolResult(
                        symbol="*",
                        outcome="error",
                        detail=self._state["last_error"],
                    )
                )

            completed = datetime.now(timezone.utc)
            self._state["last_completed_at"] = completed
            run = DemoAutomationRunResult(
                trigger="scheduled" if trigger == "scheduled" else "manual",
                execute=execute,
                started_at=started,
                completed_at=completed,
                results=results,
                total_equity=total_equity,
                risk_equity=risk_equity,
                risk_equity_currency=risk_equity_currency,
                capital_bucket_enabled=(
                    self.settings.okx_demo_capital_bucket_enabled
                ),
                capital_bucket_usdt=(
                    D(str(self.settings.okx_demo_position_margin_bucket_usdt))
                    if self.settings.okx_demo_capital_bucket_enabled
                    else None
                ),
                capital_bucket_position_limit=capital_bucket_position_limit,
                daily_pnl=self._state["daily_pnl"],
                trades_today=int(self._state["trades_today"]),
                consecutive_losses=int(self._state["consecutive_losses"]),
                rolling_7d_realized_pnl=self._rolling_realized_pnl(completed),
                active_position_count=len(portfolio_view),
                portfolio_open_risk_pct=self._portfolio_usage(portfolio_view)[0],
                portfolio_margin_pct=self._portfolio_usage(portfolio_view)[1],
                portfolio_estimated_margin=sum(
                    (
                        max(D("0"), item.estimated_margin)
                        for item in portfolio_view
                    ),
                    D("0"),
                ),
            )
            self._history.append(run)
            await self._persist_state(required=False)
            if self.repository is not None:
                try:
                    await self.repository.save_run(
                        run, history_limit=self.settings.okx_demo_automation_history_limit
                    )
                except Exception as exc:
                    self._state["last_error"] = "run_persistence_failed:" + self._safe_error(exc)
            return run

    async def _rank_requested_symbols(
        self, requested: list[str], now: datetime
    ) -> tuple[
        list[DemoAutomationSymbolResult],
        list[tuple[str, StrategyDecision]],
    ]:
        results: list[DemoAutomationSymbolResult] = []
        ranked: list[
            tuple[int, int, Decimal, int, int, str, StrategyDecision]
        ] = []
        seen: set[str] = set()
        tracked = {item.instrument_id for item in self._active_trades()}
        disabled_strategies: set[str] = set()
        if self.strategy_control_repository is not None:
            disabled_strategies = (
                await self.strategy_control_repository.disabled_strategies()
            )

        for index, raw_symbol in enumerate(requested):
            try:
                instrument_id = to_instrument_id(raw_symbol)
            except ValueError as exc:
                results.append(
                    DemoAutomationSymbolResult(
                        symbol=raw_symbol, outcome="blocked", detail=str(exc)
                    )
                )
                continue
            if instrument_id in seen:
                results.append(
                    DemoAutomationSymbolResult(
                        symbol=raw_symbol,
                        instrument_id=instrument_id,
                        outcome="blocked",
                        detail="duplicate_instrument_in_scan",
                    )
                )
                continue
            seen.add(instrument_id)
            if instrument_id not in self.settings.okx_demo_allowed_symbol_list:
                results.append(
                    DemoAutomationSymbolResult(
                        symbol=raw_symbol,
                        instrument_id=instrument_id,
                        outcome="blocked",
                        detail="instrument_not_in_demo_allowlist",
                    )
                )
                continue
            if instrument_id in tracked:
                results.append(
                    DemoAutomationSymbolResult(
                        symbol=raw_symbol,
                        instrument_id=instrument_id,
                        outcome="monitoring",
                        detail="tracked_trade_monitoring",
                    )
                )
                continue
            if self._cooldown_active(instrument_id, now):
                results.append(
                    DemoAutomationSymbolResult(
                        symbol=raw_symbol,
                        instrument_id=instrument_id,
                        outcome="blocked",
                        detail="post_trade_cooldown_active",
                    )
                )
                continue

            try:
                if disabled_strategies:
                    strategy = await self.strategy_service.evaluate(
                        instrument_id,
                        self.settings.okx_demo_scan_candle_limit,
                        disabled_strategies=disabled_strategies,
                    )
                else:
                    strategy = await self.strategy_service.evaluate(
                        instrument_id, self.settings.okx_demo_scan_candle_limit
                    )
            except Exception as exc:
                logger.exception(
                    "demo_automation_strategy_failed symbol=%s", raw_symbol
                )
                results.append(
                    DemoAutomationSymbolResult(
                        symbol=raw_symbol,
                        instrument_id=instrument_id,
                        outcome="error",
                        detail=self._safe_error(exc),
                    )
                )
                continue
            candidate = strategy.selected_candidate
            if candidate is None:
                results.append(
                    DemoAutomationSymbolResult(
                        symbol=strategy.symbol,
                        instrument_id=instrument_id,
                        outcome="no_trade",
                        detail=";".join(strategy.blockers) or "no_strategy_candidate",
                    )
                )
                continue
            effective_score, mathematical_blocker = mathematical_adjusted_score(
                candidate, self.settings
            )
            ranking_score = -1 if mathematical_blocker else effective_score
            confirmation = candidate.mathematical_confirmation
            validated_confidence = (
                confirmation.confidence
                if confirmation is not None
                else D("0")
            )
            auxiliary_bonus = (
                confirmation.auxiliary_bonus
                if confirmation is not None
                else 0
            )
            ranked.append(
                (
                    -ranking_score,
                    -candidate.score,
                    -validated_confidence,
                    -auxiliary_bonus,
                    index,
                    raw_symbol,
                    strategy,
                )
            )

        ranked.sort(key=lambda item: item[:5])
        return results, [
            (raw, strategy)
            for _, _, _, _, _, raw, strategy in ranked
        ]

    async def _process_symbol(
        self,
        raw_symbol: str,
        strategy: StrategyDecision,
        *,
        execute: bool,
        balance_equity: Decimal,
        available_margin_equity: Decimal,
        portfolio: list[DemoAutomationActiveTrade],
    ) -> tuple[DemoAutomationSymbolResult, DemoAutomationActiveTrade | None]:
        instrument_id = strategy.instrument_id
        candidate = strategy.selected_candidate
        order_submission_attempted = False
        if candidate is None:
            return (
                DemoAutomationSymbolResult(
                    symbol=raw_symbol,
                    instrument_id=instrument_id,
                    outcome="no_trade",
                    detail="no_strategy_candidate",
                ),
                None,
            )
        effective_score, mathematical_blocker = mathematical_adjusted_score(
            candidate, self.settings
        )
        candidate = candidate.model_copy(update={"risk_score": effective_score})
        if mathematical_blocker is not None:
            return (
                self._candidate_result(
                    strategy.symbol,
                    instrument_id,
                    candidate,
                    outcome="blocked",
                    reference_price=candidate.entry,
                    detail=mathematical_blocker,
                ),
                None,
            )
        try:
            reference_price, realtime_error = await self._reference_price(
                instrument_id, candidate, require_realtime=execute
            )
            if realtime_error:
                return (
                    self._candidate_result(
                        strategy.symbol,
                        instrument_id,
                        candidate,
                        outcome="blocked",
                        reference_price=reference_price,
                        detail=realtime_error,
                    ),
                    None,
                )
            if self.settings.okx_demo_structural_dynamic_leverage_enabled:
                execution_candidate = candidate_with_structural_prices(
                    candidate,
                    reference_price=reference_price,
                )
                protective_detail = "structural_protection_geometry_unavailable"
            else:
                execution_candidate = self._candidate_at_reference(
                    candidate, reference_price
                )
                protective_detail = "reference_price_outside_protective_bounds"
            if execution_candidate is None:
                return (
                    self._candidate_result(
                        strategy.symbol,
                        instrument_id,
                        candidate,
                        outcome="blocked",
                        reference_price=reference_price,
                        detail=protective_detail,
                    ),
                    None,
                )

            now = datetime.now(timezone.utc)
            fingerprint = self._fingerprint(instrument_id, execution_candidate)
            if await self._fingerprint_exists(fingerprint, now):
                return (
                    self._candidate_result(
                        strategy.symbol,
                        instrument_id,
                        execution_candidate,
                        outcome="duplicate",
                        reference_price=reference_price,
                        detail="candidate_fingerprint_already_processed",
                    ),
                    None,
                )

            position_limit = self._portfolio_position_limit(balance_equity)
            if any(item.tier == "legacy" for item in portfolio):
                return (
                    self._candidate_result(
                        strategy.symbol,
                        instrument_id,
                        execution_candidate,
                        outcome="blocked",
                        reference_price=reference_price,
                        detail="legacy_active_trade_blocks_portfolio_expansion",
                    ),
                    None,
                )
            if len(portfolio) >= position_limit:
                return (
                    self._candidate_result(
                        strategy.symbol,
                        instrument_id,
                        execution_candidate,
                        outcome="blocked",
                        reference_price=reference_price,
                        detail="portfolio_open_position_limit_reached",
                    ),
                    None,
                )

            instruments = await self.public_client.instruments(instrument_id)
            if len(instruments) != 1:
                return (
                    self._candidate_result(
                        strategy.symbol,
                        instrument_id,
                        execution_candidate,
                        outcome="blocked",
                        reference_price=reference_price,
                        detail="instrument_metadata_not_unique",
                    ),
                    None,
                )
            instrument = instruments[0]
            if instrument.instrument_id.upper() != instrument_id.upper():
                return (
                    self._candidate_result(
                        strategy.symbol,
                        instrument_id,
                        execution_candidate,
                        outcome="blocked",
                        reference_price=reference_price,
                        detail="instrument_metadata_mismatch",
                    ),
                    None,
                )
            if instrument.instrument_type.upper() != "SWAP":
                return (
                    self._candidate_result(
                        strategy.symbol,
                        instrument_id,
                        execution_candidate,
                        outcome="blocked",
                        reference_price=reference_price,
                        detail="instrument_not_swap",
                    ),
                    None,
                )
            if instrument.state.lower() != "live":
                return (
                    self._candidate_result(
                        strategy.symbol,
                        instrument_id,
                        execution_candidate,
                        outcome="blocked",
                        reference_price=reference_price,
                        detail="instrument_not_live",
                    ),
                    None,
                )
            if instrument.settlement_currency != _AUTOMATION_SETTLEMENT_CURRENCY:
                return (
                    self._candidate_result(
                        strategy.symbol,
                        instrument_id,
                        execution_candidate,
                        outcome="blocked",
                        reference_price=reference_price,
                        detail="unsupported_or_missing_settlement_currency",
                    ),
                    None,
                )
            stop_loss, take_profit = self._align_protection(
                execution_candidate, instrument.tick_size
            )
            tick_aligned = execution_candidate.model_copy(
                update={"stop_loss": stop_loss, "take_profit": take_profit}
            )
            aligned_candidate = self._candidate_at_reference(
                tick_aligned, reference_price
            )
            if aligned_candidate is None:
                return (
                    self._candidate_result(
                        strategy.symbol,
                        instrument_id,
                        execution_candidate,
                        outcome="blocked",
                        reference_price=reference_price,
                        detail="tick_aligned_protection_geometry_invalid",
                    ),
                    None,
                )
            if self.settings.okx_demo_structural_dynamic_leverage_enabled:
                aligned_candidate, structural_blocker = (
                    apply_cost_adjusted_reward_risk(
                        aligned_candidate,
                        self.settings,
                    )
                )
                if aligned_candidate is None:
                    return (
                        self._candidate_result(
                            strategy.symbol,
                            instrument_id,
                            execution_candidate,
                            outcome="blocked",
                            reference_price=reference_price,
                            detail=structural_blocker or "structural_risk_rejected",
                        ),
                        None,
                    )

            execution_boundary, execution_boundary_blocker = (
                bounded_fok_execution_price(
                    aligned_candidate,
                    self.settings,
                    reference_price=reference_price,
                    tick_size=instrument.tick_size,
                )
            )
            if execution_boundary is None:
                return (
                    self._candidate_result(
                        strategy.symbol,
                        instrument_id,
                        aligned_candidate,
                        outcome="blocked",
                        reference_price=reference_price,
                        detail=(
                            execution_boundary_blocker
                            or "bounded_fok_execution_price_unavailable"
                        ),
                    ),
                    None,
                )
            sizing_candidate, sizing_blocker = candidate_at_execution_price(
                aligned_candidate,
                self.settings,
                price=execution_boundary.limit_price,
            )
            if sizing_candidate is None:
                return (
                    self._candidate_result(
                        strategy.symbol,
                        instrument_id,
                        aligned_candidate,
                        outcome="blocked",
                        reference_price=reference_price,
                        execution_limit_price=execution_boundary.limit_price,
                        detail=(
                            sizing_blocker
                            or "bounded_fok_execution_quality_unavailable"
                        ),
                    ),
                    None,
                )

            tier, requested_risk_pct, leverage, margin_cap_pct = self._risk_profile(
                aligned_candidate.risk_score
                if aligned_candidate.risk_score is not None
                else aligned_candidate.score
            )
            required_leverage: int | None = None
            leverage_cap: int | None = tier.leverage if tier is not None else None
            leverage_cap_reasons: list[str] = []
            bucket_plan = self._capital_bucket_plan(
                balance_equity,
                available_margin_equity,
            )
            position_margin_cap_usdt = (
                bucket_plan.available_position_margin_cap_usdt
                if bucket_plan is not None
                else None
            )
            open_risk_pct, open_margin_pct = self._portfolio_usage(portfolio)
            if self.settings.okx_demo_score_risk_enabled:
                remaining_risk = max(
                    D("0"),
                    D(str(self.settings.okx_demo_portfolio_max_risk_pct))
                    - open_risk_pct,
                )
                requested_risk_pct = min(requested_risk_pct, remaining_risk)
                if requested_risk_pct <= 0:
                    return (
                        self._candidate_result(
                            strategy.symbol,
                            instrument_id,
                            aligned_candidate,
                            outcome="blocked",
                            reference_price=reference_price,
                            score_tier=tier,
                            selected_leverage=leverage,
                            detail="portfolio_open_risk_limit_reached",
                        ),
                        None,
                    )
                if (
                    self.settings.okx_demo_structural_dynamic_leverage_enabled
                    and tier is not None
                ):
                    if (
                        position_margin_cap_usdt is None
                        or position_margin_cap_usdt <= 0
                    ):
                        return (
                            self._candidate_result(
                                strategy.symbol,
                                instrument_id,
                                aligned_candidate,
                                outcome="blocked",
                                reference_price=reference_price,
                                score_tier=tier,
                                selected_leverage=leverage,
                                position_margin_cap_usdt=None,
                                capital_bucket_usdt=(
                                    bucket_plan.configured_bucket_usdt
                                    if bucket_plan is not None
                                    else None
                                ),
                                detail="exchange_available_equity_exhausted",
                            ),
                            None,
                        )
                    selection = select_structural_leverage(
                        sizing_candidate,
                        tier.model_copy(update={"risk_pct": requested_risk_pct}),
                        self.settings,
                        account_equity=balance_equity,
                        position_margin_cap=position_margin_cap_usdt,
                    )
                    leverage = selection.selected_leverage
                    required_leverage = selection.required_leverage
                    leverage_cap = selection.leverage_cap
                    leverage_cap_reasons = list(selection.cap_reasons)
                if bucket_plan is not None:
                    if position_margin_cap_usdt is None or position_margin_cap_usdt <= 0:
                        return (
                            self._candidate_result(
                                strategy.symbol,
                                instrument_id,
                                aligned_candidate,
                                outcome="blocked",
                                reference_price=reference_price,
                                score_tier=tier,
                                selected_leverage=leverage,
                                position_margin_cap_usdt=None,
                                capital_bucket_usdt=(
                                    bucket_plan.configured_bucket_usdt
                                ),
                                detail="exchange_available_equity_exhausted",
                            ),
                            None,
                        )
                    margin_notional_cap = demo_position_notional_ceiling(
                        plan=bucket_plan,
                        leverage=leverage,
                        global_notional_ceiling_usdt=D(
                            str(self.settings.order_size_cap_usdt)
                        ),
                    )
                else:
                    remaining_margin = max(
                        D("0"),
                        D(str(self.settings.okx_demo_portfolio_max_margin_pct))
                        - open_margin_pct,
                    )
                    margin_cap_pct = min(margin_cap_pct, remaining_margin)
                    if margin_cap_pct <= 0:
                        return (
                            self._candidate_result(
                                strategy.symbol,
                                instrument_id,
                                aligned_candidate,
                                outcome="blocked",
                                reference_price=reference_price,
                                score_tier=tier,
                                selected_leverage=leverage,
                                detail="portfolio_margin_limit_reached",
                            ),
                            None,
                        )
                    margin_notional_cap = (
                        balance_equity * margin_cap_pct * D(leverage)
                    )
                max_notional = min(
                    D(str(self.settings.order_size_cap_usdt)),
                    margin_notional_cap,
                    available_margin_equity * D(leverage),
                )
                if max_notional <= 0:
                    return (
                        self._candidate_result(
                            strategy.symbol,
                            instrument_id,
                            aligned_candidate,
                            outcome="blocked",
                            reference_price=reference_price,
                            score_tier=tier,
                            selected_leverage=leverage,
                            position_margin_cap_usdt=position_margin_cap_usdt,
                            capital_bucket_usdt=(
                                bucket_plan.configured_bucket_usdt
                                if bucket_plan is not None
                                else None
                            ),
                            detail="exchange_available_equity_exhausted",
                        ),
                        None,
                    )
            else:
                max_notional = min(
                    D(str(self.settings.order_size_cap_usdt)),
                    available_margin_equity * D(leverage),
                )
                if max_notional <= 0:
                    return (
                        self._candidate_result(
                            strategy.symbol,
                            instrument_id,
                            aligned_candidate,
                            outcome="blocked",
                            reference_price=reference_price,
                            selected_leverage=leverage,
                            detail="exchange_available_equity_exhausted",
                        ),
                        None,
                    )

            account = AccountRiskState(
                equity=balance_equity,
                daily_realized_pnl=(
                    D("0")
                    if self.settings.okx_demo_continuous_session_enabled
                    else min(D("0"), self._state["daily_pnl"])
                ),
                # Only exchange-attributed, de-duplicated close outcomes may
                # enter the realized seven-day loss gate.  Account equity
                # deltas include open-position PnL, deposits, withdrawals, and
                # other assets, so mixing them into this field can fabricate a
                # weekly realized loss.  Account-wide deterioration remains
                # independently bounded by the persistent drawdown high-water.
                weekly_realized_pnl=self._rolling_realized_pnl(now),
                peak_equity=self._state["risk_peak_equity"] or balance_equity,
                consecutive_losses=(
                    0
                    if self.settings.okx_demo_continuous_session_enabled
                    else int(self._state["consecutive_losses"])
                ),
                open_positions=len(portfolio),
                same_direction_positions=sum(
                    1 for item in portfolio if item.direction == aligned_candidate.direction
                ),
                correlated_positions=len(portfolio),
            )
            risk = self.risk_service.evaluate(
                sizing_candidate,
                account,
                self._risk_limits(
                    risk_per_trade_pct=requested_risk_pct,
                    max_notional=max_notional,
                    position_limit=position_limit,
                ),
            )
            if risk.decision != "approved":
                return (
                    self._candidate_result(
                        strategy.symbol,
                        instrument_id,
                        aligned_candidate,
                        outcome="risk_rejected",
                        reference_price=reference_price,
                        approved_base_quantity=risk.approved_quantity,
                        score_tier=tier,
                        selected_leverage=leverage,
                        required_leverage=required_leverage,
                        leverage_cap=leverage_cap,
                        leverage_cap_reasons=leverage_cap_reasons,
                        risk_budget_pct=requested_risk_pct,
                        position_margin_cap_usdt=position_margin_cap_usdt,
                        capital_bucket_usdt=(
                            bucket_plan.configured_bucket_usdt
                            if bucket_plan is not None
                            else None
                        ),
                        reason_codes=risk.reason_codes,
                        detail="risk_engine_rejected_candidate",
                    ),
                    None,
                )

            contracts, size_error = self._contracts_from_base_quantity(
                risk.approved_quantity, instrument
            )
            if size_error:
                return (
                    self._candidate_result(
                        strategy.symbol,
                        instrument_id,
                        aligned_candidate,
                        outcome="blocked",
                        reference_price=reference_price,
                        approved_base_quantity=risk.approved_quantity,
                        approved_contracts=contracts,
                        score_tier=tier,
                        selected_leverage=leverage,
                        required_leverage=required_leverage,
                        leverage_cap=leverage_cap,
                        leverage_cap_reasons=leverage_cap_reasons,
                        risk_budget_pct=requested_risk_pct,
                        position_margin_cap_usdt=position_margin_cap_usdt,
                        capital_bucket_usdt=(
                            bucket_plan.configured_bucket_usdt
                            if bucket_plan is not None
                            else None
                        ),
                        detail=size_error,
                    ),
                    None,
                )

            base_quantity = contracts * instrument.contract_value
            sizing_notional_price = max(
                reference_price,
                execution_boundary.limit_price,
            )
            notional = base_quantity * sizing_notional_price
            estimated_margin = notional / D(leverage)
            estimated_margin_pct = estimated_margin / balance_equity
            estimated_price_stop_loss_amount = base_quantity * abs(
                execution_boundary.limit_price - stop_loss
            )
            estimated_cost_amount = (
                notional * sizing_candidate.estimated_round_trip_cost_pct
            )
            estimated_stop_loss_amount = (
                estimated_price_stop_loss_amount + estimated_cost_amount
            )
            estimated_stop_loss_pct = estimated_stop_loss_amount / balance_equity
            rounded_budget_detail: str | None = None
            if notional > max_notional:
                rounded_budget_detail = "rounded_order_exceeds_notional_cap"
            elif self.settings.okx_demo_score_risk_enabled and (
                open_risk_pct + estimated_stop_loss_pct
                > D(str(self.settings.okx_demo_portfolio_max_risk_pct))
            ):
                rounded_budget_detail = "rounded_order_exceeds_portfolio_budget"
            elif (
                bucket_plan is not None
                and position_margin_cap_usdt is not None
                and estimated_margin > position_margin_cap_usdt
            ):
                rounded_budget_detail = "rounded_order_exceeds_position_margin_bucket"
            elif (
                self.settings.okx_demo_score_risk_enabled
                and bucket_plan is None
                and open_margin_pct + estimated_margin_pct
                > D(str(self.settings.okx_demo_portfolio_max_margin_pct))
            ):
                rounded_budget_detail = "rounded_order_exceeds_portfolio_budget"
            if rounded_budget_detail is not None:
                return (
                    self._candidate_result(
                        strategy.symbol,
                        instrument_id,
                        aligned_candidate,
                        outcome="blocked",
                        reference_price=reference_price,
                        approved_base_quantity=base_quantity,
                        approved_contracts=contracts,
                        score_tier=tier,
                        selected_leverage=leverage,
                        required_leverage=required_leverage,
                        leverage_cap=leverage_cap,
                        leverage_cap_reasons=leverage_cap_reasons,
                        risk_budget_pct=requested_risk_pct,
                        estimated_stop_loss_pct=estimated_stop_loss_pct,
                        margin_allocation_pct=estimated_margin_pct,
                        estimated_margin=estimated_margin,
                        position_margin_cap_usdt=position_margin_cap_usdt,
                        capital_bucket_usdt=(
                            bucket_plan.configured_bucket_usdt
                            if bucket_plan is not None
                            else None
                        ),
                        detail=rounded_budget_detail,
                    ),
                    None,
                )

            reservation = DemoAutomationActiveTrade(
                instrument_id=instrument_id,
                settlement_currency=instrument.settlement_currency,
                direction=aligned_candidate.direction,
                strategy=aligned_candidate.strategy,
                score=aligned_candidate.score,
                effective_score=aligned_candidate.risk_score,
                derivative_status=(
                    aligned_candidate.derivative_confirmation.status
                    if aligned_candidate.derivative_confirmation is not None
                    else None
                ),
                derivative_confidence=(
                    aligned_candidate.derivative_confirmation.confidence
                    if aligned_candidate.derivative_confirmation is not None
                    else None
                ),
                mathematical_status=(
                    aligned_candidate.mathematical_confirmation.status
                    if aligned_candidate.mathematical_confirmation is not None
                    else None
                ),
                mathematical_risk_grade=(
                    aligned_candidate.mathematical_confirmation.risk_grade
                    if aligned_candidate.mathematical_confirmation is not None
                    else None
                ),
                mathematical_confidence=(
                    aligned_candidate.mathematical_confirmation.confidence
                    if aligned_candidate.mathematical_confirmation is not None
                    else None
                ),
                mathematical_reliability=(
                    aligned_candidate.mathematical_confirmation.reliability
                    if aligned_candidate.mathematical_confirmation is not None
                    else None
                ),
                mathematical_auxiliary_bonus=(
                    aligned_candidate.mathematical_confirmation.auxiliary_bonus
                    if aligned_candidate.mathematical_confirmation is not None
                    else 0
                ),
                mathematical_validated_components=(
                    aligned_candidate.mathematical_confirmation.component_codes
                    if aligned_candidate.mathematical_confirmation is not None
                    else []
                ),
                mathematical_auxiliary_components=(
                    aligned_candidate.mathematical_confirmation.auxiliary_component_codes
                    if aligned_candidate.mathematical_confirmation is not None
                    else []
                ),
                tier=tier.name if tier is not None else "legacy",
                contracts=contracts,
                leverage=leverage,
                required_leverage=required_leverage,
                leverage_cap=leverage_cap,
                leverage_cap_reasons=leverage_cap_reasons,
                margin_mode=(
                    "isolated"
                    if self.settings.okx_demo_structural_dynamic_leverage_enabled
                    else "cross"
                ),
                risk_budget_pct=requested_risk_pct,
                estimated_stop_loss_amount=estimated_stop_loss_amount,
                estimated_stop_loss_pct=estimated_stop_loss_pct,
                estimated_notional=notional,
                margin_allocation_pct=estimated_margin_pct,
                estimated_margin=estimated_margin,
                estimated_round_trip_cost_pct=(
                    sizing_candidate.estimated_round_trip_cost_pct
                ),
                estimated_cost_amount=estimated_cost_amount,
                position_margin_cap_usdt=position_margin_cap_usdt,
                capital_bucket_usdt=(
                    bucket_plan.configured_bucket_usdt
                    if bucket_plan is not None
                    else None
                ),
                reference_price=reference_price,
                execution_order_type="fok",
                execution_limit_price=execution_boundary.limit_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                protection_model=sizing_candidate.protection_model,
                structure_timeframe=(
                    sizing_candidate.structural_protection.timeframe
                    if sizing_candidate.protection_model == "structure"
                    and sizing_candidate.structural_protection is not None
                    else None
                ),
                structure_source_closed_at=(
                    sizing_candidate.structural_protection.source_closed_at
                    if sizing_candidate.protection_model == "structure"
                    and sizing_candidate.structural_protection is not None
                    else None
                ),
                structure_stop_anchor=(
                    sizing_candidate.structural_protection.stop_anchor
                    if sizing_candidate.protection_model == "structure"
                    and sizing_candidate.structural_protection is not None
                    else None
                ),
                structure_target_anchor=(
                    sizing_candidate.structural_protection.target_anchor
                    if sizing_candidate.protection_model == "structure"
                    and sizing_candidate.structural_protection is not None
                    else None
                ),
                structure_volatility_buffer=(
                    sizing_candidate.structural_protection.volatility_buffer
                    if sizing_candidate.protection_model == "structure"
                    and sizing_candidate.structural_protection is not None
                    else None
                ),
                gross_risk_reward=sizing_candidate.gross_risk_reward,
                net_risk_reward=sizing_candidate.net_risk_reward,
                start_equity=balance_equity,
                started_at=now,
            )
            if not execute:
                return (
                    self._candidate_result(
                        strategy.symbol,
                        instrument_id,
                        sizing_candidate,
                        outcome="approved_dry_run",
                        reference_price=reference_price,
                        execution_limit_price=execution_boundary.limit_price,
                        approved_base_quantity=base_quantity,
                        approved_contracts=contracts,
                        score_tier=tier,
                        selected_leverage=leverage,
                        required_leverage=required_leverage,
                        leverage_cap=leverage_cap,
                        leverage_cap_reasons=leverage_cap_reasons,
                        risk_budget_pct=requested_risk_pct,
                        estimated_stop_loss_pct=estimated_stop_loss_pct,
                        margin_allocation_pct=estimated_margin_pct,
                        estimated_margin=estimated_margin,
                        position_margin_cap_usdt=position_margin_cap_usdt,
                        capital_bucket_usdt=(
                            bucket_plan.configured_bucket_usdt
                            if bucket_plan is not None
                            else None
                        ),
                        detail=(
                            "risk_approved_bounded_fok_demo_execution_"
                            "disabled_for_this_run"
                        ),
                    ),
                    reservation,
                )

            client_order_id = "AUT" + fingerprint[:29]
            margin_mode = (
                "isolated"
                if self.settings.okx_demo_structural_dynamic_leverage_enabled
                else "cross"
            )
            try:
                leverage_write = await self.demo_service.set_leverage(
                    OkxDemoLeverageRequest(
                        instrument_id=instrument_id,
                        leverage=leverage,
                        margin_mode=margin_mode,
                        direction=sizing_candidate.direction,
                        confirmation=DEMO_CONFIRMATION_PHRASE,
                    )
                )
                if not leverage_write.acknowledged:
                    raise DemoAutomationSafetyError(
                        "okx_demo_leverage_exchange_response_unconfirmed"
                    )
            except Exception:
                self._engage_emergency("leverage_configuration_unconfirmed")
                await self._persist_state(required=False)
                raise
            order_submission_attempted = True
            write = await self.demo_service.place_order(
                OkxDemoOrderRequest(
                    instrument_id=instrument_id,
                    direction=sizing_candidate.direction,
                    size=contracts,
                    margin_mode=margin_mode,
                    order_type="fok",
                    price=execution_boundary.limit_price,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    trigger_price_type="mark",
                    client_order_id=client_order_id,
                    confirmation=DEMO_CONFIRMATION_PHRASE,
                )
            )
            acknowledgement = write.acknowledgement
            order = write.order
            exchange_order_id = (
                acknowledgement.order_id if acknowledgement is not None else None
            )
            expiry = max(
                sizing_candidate.expires_at,
                now + timedelta(
                    seconds=self._effective_trade_cooldown_seconds()
                ),
            )
            known_zero_fill = (
                write.acknowledged
                and acknowledgement is not None
                and bool(exchange_order_id)
                and order is not None
                and order.state.lower() in {"canceled", "mmp_canceled"}
                and order.accumulated_fill_size == 0
            )
            if known_zero_fill:
                try:
                    await self._save_fingerprint(
                        fingerprint,
                        expiry,
                        {
                            "instrument_id": instrument_id,
                            "strategy": sizing_candidate.strategy,
                            "client_order_id": client_order_id,
                            "exchange_order_id": exchange_order_id,
                            "execution_order_type": "fok",
                            "execution_limit_price": str(
                                execution_boundary.limit_price
                            ),
                            "outcome": "not_filled",
                        },
                    )
                except Exception:
                    self._engage_emergency(
                        "fok_no_fill_fingerprint_persistence_failed"
                    )
                    await self._persist_state(required=False)
                    raise
                return (
                    self._candidate_result(
                        strategy.symbol,
                        instrument_id,
                        sizing_candidate,
                        outcome="blocked",
                        reference_price=reference_price,
                        execution_limit_price=execution_boundary.limit_price,
                        approved_base_quantity=base_quantity,
                        approved_contracts=contracts,
                        score_tier=tier,
                        selected_leverage=leverage,
                        required_leverage=required_leverage,
                        leverage_cap=leverage_cap,
                        leverage_cap_reasons=leverage_cap_reasons,
                        risk_budget_pct=requested_risk_pct,
                        estimated_stop_loss_pct=estimated_stop_loss_pct,
                        margin_allocation_pct=estimated_margin_pct,
                        estimated_margin=estimated_margin,
                        position_margin_cap_usdt=position_margin_cap_usdt,
                        capital_bucket_usdt=(
                            bucket_plan.configured_bucket_usdt
                            if bucket_plan is not None
                            else None
                        ),
                        client_order_id=client_order_id,
                        exchange_order_id=exchange_order_id,
                        order_submission_attempted=True,
                        detail="bounded_fok_order_not_filled",
                    ),
                    None,
                )

            average_fill_price = (
                order.average_fill_price if order is not None else None
            )
            fill_quality = (
                execution_quality_at_price(
                    sizing_candidate,
                    self.settings,
                    price=average_fill_price,
                )
                if average_fill_price is not None
                else None
            )
            fill_slippage_bps = (
                adverse_fill_slippage_bps(
                    direction=sizing_candidate.direction,
                    reference_price=reference_price,
                    fill_price=average_fill_price,
                )
                if average_fill_price is not None
                else None
            )
            reservation = reservation.model_copy(
                update={
                    "client_order_id": client_order_id,
                    "exchange_order_id": exchange_order_id,
                    "protection_client_order_id": (
                        write.protection_client_order_id
                    ),
                    "average_fill_price": average_fill_price,
                    "actual_gross_risk_reward": (
                        fill_quality.gross_risk_reward
                        if fill_quality is not None
                        else None
                    ),
                    "actual_net_risk_reward": (
                        fill_quality.net_risk_reward
                        if fill_quality is not None
                        else None
                    ),
                    "actual_enforced_risk_reward": (
                        fill_quality.enforced_risk_reward
                        if fill_quality is not None
                        else None
                    ),
                    "adverse_fill_slippage_bps": fill_slippage_bps,
                }
            )
            self._set_active_trade(reservation)
            self._state["trades_today"] = int(self._state["trades_today"]) + 1
            state_persisted = False
            try:
                await self._persist_state(required=True)
                state_persisted = True
                if (
                    not write.acknowledged
                    or acknowledgement is None
                    or not exchange_order_id
                ):
                    self._engage_emergency(
                        "post_submission_acknowledgement_invalid"
                    )
                    await self._persist_state(required=False)
                    raise DemoAutomationSafetyError(
                        "okx_demo_order_submission_acknowledgement_invalid"
                    )
                if (
                    order is None
                    or order.order_type.lower() != "fok"
                    or order.state.lower() != "filled"
                    or order.accumulated_fill_size != contracts
                    or average_fill_price is None
                ):
                    self._engage_emergency("post_submission_fok_fill_unconfirmed")
                    await self._persist_state(required=False)
                    raise DemoAutomationSafetyError(
                        "okx_demo_fok_fill_unconfirmed"
                    )
                execution_price_outside_limit = (
                    sizing_candidate.direction == "long"
                    and average_fill_price > execution_boundary.limit_price
                ) or (
                    sizing_candidate.direction == "short"
                    and average_fill_price < execution_boundary.limit_price
                )
                if execution_price_outside_limit:
                    self._engage_emergency(
                        "post_submission_execution_price_exceeds_limit"
                    )
                    await self._persist_state(required=False)
                    raise DemoAutomationSafetyError(
                        "okx_demo_execution_price_exceeds_limit"
                    )
                if (
                    fill_quality is None
                    or fill_quality.enforced_risk_reward
                    < fill_quality.minimum_risk_reward
                ):
                    self._engage_emergency(
                        "post_submission_execution_risk_reward_below_minimum"
                    )
                    await self._persist_state(required=False)
                    raise DemoAutomationSafetyError(
                        "okx_demo_execution_risk_reward_below_minimum"
                    )
                if (
                    fill_slippage_bps is None
                    or fill_slippage_bps
                    > execution_boundary.max_adverse_slippage_bps
                ):
                    self._engage_emergency(
                        "post_submission_adverse_fill_slippage_exceeds_limit"
                    )
                    await self._persist_state(required=False)
                    raise DemoAutomationSafetyError(
                        "okx_demo_adverse_fill_slippage_exceeds_limit"
                    )
                if (
                    self.settings.okx_demo_require_protection
                    and write.protection_confirmed is not True
                ):
                    self._engage_emergency(
                        "post_submission_protection_unconfirmed"
                    )
                    await self._persist_state(required=False)
                    raise DemoAutomationSafetyError(
                        "okx_demo_order_protection_unconfirmed"
                    )
                await self._save_fingerprint(
                    fingerprint,
                    expiry,
                    {
                        "instrument_id": instrument_id,
                        "strategy": sizing_candidate.strategy,
                        "client_order_id": client_order_id,
                        "exchange_order_id": exchange_order_id,
                        "execution_order_type": "fok",
                        "execution_limit_price": str(
                            execution_boundary.limit_price
                        ),
                        "average_fill_price": str(average_fill_price),
                        "actual_enforced_risk_reward": str(
                            fill_quality.enforced_risk_reward
                        ),
                        "adverse_fill_slippage_bps": str(fill_slippage_bps),
                    },
                )
            except Exception:
                if not self._state["emergency_stop"]:
                    self._engage_emergency(
                        "post_submission_fingerprint_persistence_failed"
                        if state_persisted
                        else "post_submission_state_persistence_failed"
                    )
                await self._persist_state(required=False)
                raise
            return (
                self._candidate_result(
                    strategy.symbol,
                    instrument_id,
                    sizing_candidate,
                    outcome="submitted",
                    reference_price=reference_price,
                    execution_limit_price=execution_boundary.limit_price,
                    average_fill_price=average_fill_price,
                    actual_gross_risk_reward=(
                        fill_quality.gross_risk_reward
                        if fill_quality is not None
                        else None
                    ),
                    actual_net_risk_reward=(
                        fill_quality.net_risk_reward
                        if fill_quality is not None
                        else None
                    ),
                    actual_enforced_risk_reward=(
                        fill_quality.enforced_risk_reward
                        if fill_quality is not None
                        else None
                    ),
                    adverse_fill_slippage_bps=fill_slippage_bps,
                    approved_base_quantity=base_quantity,
                    approved_contracts=contracts,
                    score_tier=tier,
                    selected_leverage=leverage,
                    required_leverage=required_leverage,
                    leverage_cap=leverage_cap,
                    leverage_cap_reasons=leverage_cap_reasons,
                    risk_budget_pct=requested_risk_pct,
                    estimated_stop_loss_pct=estimated_stop_loss_pct,
                    margin_allocation_pct=estimated_margin_pct,
                    estimated_margin=estimated_margin,
                    position_margin_cap_usdt=position_margin_cap_usdt,
                    capital_bucket_usdt=(
                        bucket_plan.configured_bucket_usdt
                        if bucket_plan is not None
                        else None
                    ),
                    client_order_id=client_order_id,
                    exchange_order_id=exchange_order_id,
                    order_submission_attempted=True,
                    detail="protected_bounded_fok_order_filled",
                ),
                reservation,
            )
        except Exception as exc:
            logger.exception("demo_automation_symbol_failed symbol=%s", raw_symbol)
            return (
                DemoAutomationSymbolResult(
                    symbol=raw_symbol,
                    instrument_id=instrument_id,
                    outcome="error",
                    order_submission_attempted=order_submission_attempted,
                    detail=self._safe_error(exc),
                ),
                None,
            )

    async def _reference_price(
        self,
        instrument_id: str,
        candidate: TradeCandidate,
        *,
        require_realtime: bool,
    ) -> tuple[Decimal, str | None]:
        if require_realtime and not self.market_client.status().connected:
            return candidate.entry, "realtime_websocket_not_connected"
        snapshot: RealtimeSnapshot | None = await self.market_hub.snapshot(instrument_id)
        if snapshot is None:
            return candidate.entry, "realtime_snapshot_not_available" if require_realtime else None

        executable_quote = (
            snapshot.ask if candidate.direction == "long" else snapshot.bid
        )
        if executable_quote is None or executable_quote <= 0:
            if require_realtime:
                return candidate.entry, "realtime_executable_quote_not_available"
            executable_quote = snapshot.last or candidate.entry
        if require_realtime:
            if snapshot.quote_received_at is None:
                return executable_quote, "realtime_quote_timestamp_missing"
            quote_age = (
                datetime.now(timezone.utc) - snapshot.quote_received_at
            ).total_seconds()
            if quote_age > self.settings.okx_demo_scan_max_snapshot_age_seconds:
                return executable_quote, "realtime_executable_quote_stale"
            if (
                snapshot.bid is None
                or snapshot.ask is None
                or snapshot.bid <= 0
                or snapshot.ask <= 0
                or snapshot.bid > snapshot.ask
            ):
                return executable_quote, "realtime_quote_geometry_invalid"
            if snapshot.mark_price is None or snapshot.mark_price <= 0:
                return executable_quote, "realtime_mark_price_not_available"
            if snapshot.mark_price_received_at is None:
                return executable_quote, "realtime_mark_price_timestamp_missing"
            mark_age = (
                datetime.now(timezone.utc) - snapshot.mark_price_received_at
            ).total_seconds()
            if mark_age > self.settings.okx_demo_scan_max_snapshot_age_seconds:
                return executable_quote, "realtime_mark_price_stale"
            basis_bps = (
                abs(snapshot.mark_price - executable_quote)
                / executable_quote
                * D("10000")
            )
            if basis_bps > D(
                str(self.settings.okx_demo_scan_max_entry_drift_bps)
            ):
                return executable_quote, "mark_execution_basis_exceeds_limit"
            geometry = candidate.structural_protection
            stop_loss = (
                geometry.stop_loss
                if self.settings.okx_demo_structural_dynamic_leverage_enabled
                and geometry is not None
                else candidate.stop_loss
            )
            take_profit = (
                geometry.take_profit
                if self.settings.okx_demo_structural_dynamic_leverage_enabled
                and geometry is not None
                else candidate.take_profit
            )
            if candidate.direction == "long":
                mark_inside = stop_loss < snapshot.mark_price < take_profit
            else:
                mark_inside = take_profit < snapshot.mark_price < stop_loss
            if not mark_inside:
                return executable_quote, "mark_price_outside_protective_bounds"

        drift_bps = (
            abs(executable_quote - candidate.entry)
            / candidate.entry
            * D("10000")
        )
        if drift_bps > D(str(self.settings.okx_demo_scan_max_entry_drift_bps)):
            return executable_quote, "entry_price_drift_exceeds_limit"
        return executable_quote, None

    @staticmethod
    def _candidate_at_reference(
        candidate: TradeCandidate, reference_price: Decimal
    ) -> TradeCandidate | None:
        if candidate.direction == "long":
            if not candidate.stop_loss < reference_price < candidate.take_profit:
                return None
            risk = reference_price - candidate.stop_loss
            reward = candidate.take_profit - reference_price
        else:
            if not candidate.take_profit < reference_price < candidate.stop_loss:
                return None
            risk = candidate.stop_loss - reference_price
            reward = reference_price - candidate.take_profit
        if risk <= 0 or reward <= 0:
            return None
        return candidate.model_copy(
            update={"entry": reference_price, "risk_reward": reward / risk}
        )

    def _contracts_from_base_quantity(self, base_quantity, instrument) -> tuple[Decimal, str | None]:
        contract_value = instrument.contract_value
        base_currency = instrument.instrument_id.split("-")[0]
        if contract_value is None or contract_value <= 0:
            return D("0"), "instrument_contract_value_missing"
        if instrument.contract_currency and instrument.contract_currency != base_currency:
            return D("0"), "unsupported_contract_value_currency"
        raw = base_quantity / contract_value
        contracts = (raw / instrument.lot_size).to_integral_value(rounding=ROUND_DOWN) * instrument.lot_size
        contracts = min(contracts, self.settings.okx_demo_max_order_size_contracts)
        if contracts < instrument.minimum_size:
            return contracts, "risk_sized_contracts_below_exchange_minimum"
        if contracts <= 0:
            return contracts, "risk_sized_contracts_zero"
        return contracts, None

    @staticmethod
    def _align_protection(candidate: TradeCandidate, tick: Decimal) -> tuple[Decimal, Decimal]:
        if candidate.direction == "long":
            sl_round = ROUND_FLOOR
            tp_round = (
                ROUND_FLOOR
                if candidate.protection_model == "structure"
                else ROUND_CEILING
            )
        else:
            sl_round = ROUND_CEILING
            tp_round = (
                ROUND_CEILING
                if candidate.protection_model == "structure"
                else ROUND_FLOOR
            )
        stop = (candidate.stop_loss / tick).to_integral_value(rounding=sl_round) * tick
        take = (candidate.take_profit / tick).to_integral_value(rounding=tp_round) * tick
        # Re-validate geometry after exchange tick alignment.
        candidate.model_copy(update={"stop_loss": stop, "take_profit": take})
        return stop, take

    def _risk_profile(
        self, score: int
    ) -> tuple[DemoAutomationRiskTier | None, Decimal, int, Decimal]:
        if self.settings.okx_demo_score_risk_enabled:
            tiers = configured_score_risk_tiers(self.settings)
            tier = (
                tiers[0]
                if score < self.settings.strategy_min_score
                else score_risk_tier(score, self.settings)
            )
            return (
                tier,
                D(str(tier.risk_pct)),
                tier.leverage,
                D(str(tier.margin_allocation_pct)),
            )
        return (
            None,
            D(str(self.settings.risk_per_trade_pct)),
            self.settings.okx_demo_automation_leverage,
            D("1"),
        )

    def _portfolio_position_limit(
        self,
        risk_equity: Decimal | None = None,
    ) -> int:
        if self.settings.okx_demo_score_risk_enabled:
            if self.settings.okx_demo_capital_bucket_enabled:
                if risk_equity is None or risk_equity <= 0:
                    return 1
                plan = self._capital_bucket_plan(
                    risk_equity,
                    risk_equity,
                )
                if plan is None:
                    return 1
                return plan.effective_position_limit
            return self.settings.okx_demo_max_open_positions
        return 1

    def _capital_bucket_plan(
        self,
        risk_equity: Decimal,
        available_equity: Decimal,
    ) -> DemoCapitalBucketPlan | None:
        if not self.settings.okx_demo_capital_bucket_enabled:
            return None
        return build_demo_capital_bucket_plan(
            risk_equity_usdt=risk_equity,
            available_equity_usdt=available_equity,
            configured_bucket_usdt=D(
                str(self.settings.okx_demo_position_margin_bucket_usdt)
            ),
            configured_position_limit=self.settings.okx_demo_max_open_positions,
        )

    def _ensure_capital_bucket_currency(
        self,
        capital: _AutomationCapital,
    ) -> None:
        if (
            self.settings.okx_demo_capital_bucket_enabled
            and capital.currency != _AUTOMATION_SETTLEMENT_CURRENCY
        ):
            raise DemoAutomationSafetyError(
                "capital_bucket_requires_single_currency_usdt_equity"
            )

    @staticmethod
    def _capital_bucket_violation(
        trades: Iterable[DemoAutomationActiveTrade],
        plan: DemoCapitalBucketPlan,
    ) -> str | None:
        items = list(trades)
        if len(items) > plan.effective_position_limit:
            return "capital_bucket_position_limit_exceeded"
        for item in items:
            stored_cap = (
                item.position_margin_cap_usdt
                if item.position_margin_cap_usdt is not None
                else plan.target_position_margin_usdt
            )
            effective_cap = min(stored_cap, plan.target_position_margin_usdt)
            if max(D("0"), item.estimated_margin) > effective_cap:
                return "active_trade_exceeds_position_margin_bucket"
        return None

    @staticmethod
    def _automation_capital(
        snapshot: OkxDemoReconcileResult,
    ) -> tuple[DemoRiskCapital | None, str]:
        return resolve_demo_risk_capital(
            snapshot.account_config,
            snapshot.balance,
            settlement_currency=_AUTOMATION_SETTLEMENT_CURRENCY,
        )

    @staticmethod
    def _portfolio_usage(
        trades: Iterable[DemoAutomationActiveTrade],
    ) -> tuple[Decimal, Decimal]:
        items = list(trades)
        return (
            sum((max(D("0"), item.estimated_stop_loss_pct) for item in items), D("0")),
            sum((max(D("0"), item.margin_allocation_pct) for item in items), D("0")),
        )

    def _risk_limits(
        self,
        *,
        risk_per_trade_pct: Decimal,
        max_notional: Decimal,
        position_limit: int,
    ) -> RiskLimits:
        return RiskLimits(
            risk_per_trade_pct=risk_per_trade_pct,
            max_daily_loss_pct=D(str(self.settings.okx_demo_daily_loss_limit_pct)),
            max_weekly_loss_pct=D(str(self.settings.max_weekly_loss_pct)),
            max_drawdown_pct=D(str(self.settings.max_drawdown_pct)),
            max_consecutive_losses=self.settings.okx_demo_automation_max_consecutive_losses,
            max_open_positions=position_limit,
            max_same_direction_positions=position_limit,
            max_correlated_positions=position_limit,
            max_notional=max_notional,
            minimum_score=self.settings.strategy_min_score,
            minimum_risk_reward=(
                D(str(self.settings.okx_demo_structural_min_net_risk_reward))
                if self.settings.okx_demo_structural_dynamic_leverage_enabled
                else D(str(self.settings.strategy_min_risk_reward))
            ),
        )

    def _roll_session(
        self,
        equity: Decimal,
        equity_basis: str,
        *,
        force_if_empty: bool = False,
        allow_rebase: bool = False,
    ) -> str | None:
        today = datetime.now(timezone.utc).date()
        current_basis = self._state.get("equity_basis")
        if current_basis != equity_basis:
            if not allow_rebase:
                self._state["locked"] = True
                self._state["lock_reasons"] = sorted(
                    set([*self._state["lock_reasons"], _EQUITY_BASIS_LOCK])
                )
                return _EQUITY_BASIS_LOCK
            self._state["equity_basis"] = equity_basis
            self._state["session_date"] = today
            self._state["baseline_equity"] = equity
            self._state["peak_equity"] = equity
            self._state["risk_peak_equity"] = equity
            self._state["realized_pnl_events"] = []
            self._state["daily_pnl"] = D("0")
            self._state["trades_today"] = 0
            self._state["consecutive_losses"] = 0
        self._state["lock_reasons"] = [
            reason
            for reason in self._state["lock_reasons"]
            if reason != _EQUITY_BASIS_LOCK
        ]
        if self._state["session_date"] != today:
            self._state["session_date"] = today
            self._state["baseline_equity"] = equity
            self._state["peak_equity"] = equity
            self._state["daily_pnl"] = D("0")
            self._state["trades_today"] = 0
            self._state["consecutive_losses"] = 0
        elif force_if_empty and self._state["baseline_equity"] is None:
            self._state["baseline_equity"] = equity
            self._state["peak_equity"] = equity
            if self._state["risk_peak_equity"] is None:
                self._state["risk_peak_equity"] = equity
        baseline = self._state["baseline_equity"]
        if baseline is not None:
            self._state["daily_pnl"] = equity - baseline
        peak = self._state["peak_equity"]
        self._state["peak_equity"] = equity if peak is None else max(peak, equity)
        risk_peak = self._state["risk_peak_equity"]
        self._state["risk_peak_equity"] = (
            equity if risk_peak is None else max(risk_peak, equity)
        )
        self._normalize_realized_pnl_events()
        return None

    def _finalize_active_trades(
        self,
        snapshot: OkxDemoReconcileResult,
    ) -> None:
        active = self._active_trades()
        if not active:
            return
        exposed = {
            item.instrument_id
            for item in [
                *snapshot.positions,
                *snapshot.pending_orders,
                *snapshot.pending_algo_orders,
            ]
        }
        reconciled_at = snapshot.reconciled_at
        grace = timedelta(
            seconds=self.settings.okx_demo_trade_reconcile_grace_seconds
        )
        closed = [
            item
            for item in active
            if item.instrument_id not in exposed
            and reconciled_at - item.started_at.astimezone(timezone.utc) >= grace
        ]
        if not closed:
            return

        outcomes: list[
            tuple[datetime, DemoAutomationActiveTrade, Decimal, list[str]]
        ] = []
        unknown: list[DemoAutomationActiveTrade] = []
        for trade in closed:
            outcome = self._closing_trade_outcome(trade, snapshot.recent_orders)
            if outcome is None:
                unknown.append(trade)
            else:
                closed_at, net_pnl, closing_order_ids = outcome
                outcomes.append((closed_at, trade, net_pnl, closing_order_ids))

        if unknown:
            self._state["armed"] = False
            self._state["emergency_stop"] = True
            self._state["locked"] = True
            self._state["lock_reasons"] = sorted(
                set([*self._state["lock_reasons"], "trade_outcome_unconfirmed"])
            )
            self._state["last_error"] = (
                "trade_outcome_unconfirmed:"
                + ",".join(sorted(item.instrument_id for item in unknown))
            )[:250]
            return

        cooldowns = dict(self._state.get("symbol_cooldowns") or {})
        for closed_at, trade, net_pnl, closing_order_ids in sorted(
            outcomes, key=lambda item: item[0]
        ):
            self._record_realized_pnl_event(
                trade,
                closed_at,
                net_pnl,
                closing_order_ids=closing_order_ids,
            )
            if closed_at.date() == self._state["session_date"]:
                if net_pnl < 0:
                    self._state["consecutive_losses"] = (
                        int(self._state["consecutive_losses"]) + 1
                    )
                else:
                    self._state["consecutive_losses"] = 0
            cooldowns[trade.instrument_id] = closed_at.isoformat()
            self._remove_active_trade(trade.instrument_id)
            previous = self._state["last_trade_closed_at"]
            self._state["last_trade_closed_at"] = (
                closed_at if previous is None else max(previous, closed_at)
            )
        self._state["symbol_cooldowns"] = cooldowns

    def _active_protection_violation(
        self,
        snapshot: OkxDemoReconcileResult,
        active: Iterable[DemoAutomationActiveTrade],
    ) -> str | None:
        if not self.settings.okx_demo_require_protection:
            return None
        tracked = {item.instrument_id: item for item in active}
        pending = list(snapshot.pending_algo_orders)
        for position in snapshot.positions:
            trade = tracked.get(position.instrument_id)
            if trade is None:
                continue
            if not self._active_trade_has_matching_protection(
                trade,
                abs(position.size),
                pending,
            ):
                return "tracked_position_protection_missing_or_mismatched"
        return None

    @staticmethod
    def _active_trade_has_matching_protection(
        trade: DemoAutomationActiveTrade,
        position_size: Decimal,
        pending: Iterable[OkxDemoAlgoOrderView],
    ) -> bool:
        if (
            not trade.protection_client_order_id
            or trade.stop_loss is None
            or trade.take_profit is None
            or position_size <= 0
        ):
            return False
        for algo in pending:
            if (
                algo.instrument_id != trade.instrument_id
                or algo.client_algo_order_id
                != trade.protection_client_order_id
                or algo.stop_loss_trigger_price != trade.stop_loss
                or algo.take_profit_trigger_price != trade.take_profit
                or algo.size < position_size
            ):
                continue
            if (
                str(algo.raw.get("slTriggerPxType") or "") != "mark"
                or str(algo.raw.get("tpTriggerPxType") or "") != "mark"
            ):
                continue
            return True
        return False

    def _refresh_active_trade_estimates(
        self,
        snapshot: OkxDemoReconcileResult,
        equity: Decimal,
    ) -> None:
        if equity <= 0:
            return
        positions = {item.instrument_id: item for item in snapshot.positions}
        for trade in self._active_trades():
            if trade.tier == "legacy":
                continue
            position = positions.get(trade.instrument_id)
            if (
                trade.protection_model == "structure"
                and position is not None
                and position.margin_mode != "isolated"
            ):
                self._engage_emergency("isolated_margin_mode_mismatch")
                continue
            current_notional = trade.estimated_notional
            if (
                position is not None
                and position.mark_price is not None
                and trade.reference_price is not None
                and trade.reference_price > 0
            ):
                current_notional = (
                    trade.estimated_notional
                    * position.mark_price
                    / trade.reference_price
                )
            estimated_margin = current_notional / D(trade.leverage)
            updated = trade.model_copy(
                update={
                    "estimated_stop_loss_pct": (
                        trade.estimated_stop_loss_amount / equity
                    ),
                    "estimated_margin": estimated_margin,
                    "margin_allocation_pct": estimated_margin / equity,
                }
            )
            self._set_active_trade(updated)

    @classmethod
    def _closing_trade_outcome(
        cls,
        trade: DemoAutomationActiveTrade,
        orders: Iterable[OkxDemoOrderView],
    ) -> tuple[datetime, Decimal, list[str]] | None:
        matches: dict[str, tuple[datetime, Decimal]] = {}
        for order in orders:
            if order.instrument_id != trade.instrument_id:
                continue
            if order.state.lower() not in {"filled", "partially_filled"}:
                continue
            if not cls._is_closing_order(order):
                continue
            closed_at = order.updated_at or order.created_at
            if closed_at is None:
                continue
            if closed_at.tzinfo is None:
                closed_at = closed_at.replace(tzinfo=timezone.utc)
            closed_at = closed_at.astimezone(timezone.utc)
            if closed_at < trade.started_at.astimezone(timezone.utc):
                continue
            realized, present = cls._decimal_with_presence(
                order.raw, "pnl", "fillPnl", "realizedPnl"
            )
            if not present:
                continue
            fee_signed = cls._first_decimal(order.raw, "fee", "fillFee") or D("0")
            fee_cost = abs(fee_signed) if fee_signed < 0 else D("0")
            rebate = cls._first_decimal(order.raw, "rebate", "fillFee") or D("0")
            if rebate < 0:
                rebate = D("0")
            funding = cls._first_decimal(order.raw, "fundingFee") or D("0")
            value = (closed_at, realized - fee_cost + rebate + funding)
            previous = matches.get(order.order_id)
            if previous is None or closed_at >= previous[0]:
                matches[order.order_id] = value
        if not matches:
            return None
        unique = list(matches.values())
        return (
            max(item[0] for item in unique),
            sum((item[1] for item in unique), D("0")),
            sorted(matches),
        )

    def _record_realized_pnl_event(
        self,
        trade: DemoAutomationActiveTrade,
        closed_at: datetime,
        net_pnl: Decimal,
        *,
        closing_order_ids: Iterable[str] = (),
    ) -> None:
        closed_utc = closed_at.astimezone(timezone.utc)
        event_id = hashlib.sha256(
            "|".join(
                [
                    trade.instrument_id,
                    trade.started_at.astimezone(timezone.utc).isoformat(),
                    closed_utc.isoformat(),
                ]
            ).encode("utf-8")
        ).hexdigest()
        events = self._normalize_realized_pnl_events()
        events = [item for item in events if item["event_id"] != event_id]
        events.append(
            {
                "event_id": event_id,
                "instrument_id": trade.instrument_id,
                "strategy": trade.strategy,
                "direction": trade.direction,
                "settlement_currency": trade.settlement_currency,
                "reference_price": (
                    str(trade.reference_price)
                    if trade.reference_price is not None
                    else None
                ),
                "entry_client_order_id": trade.client_order_id,
                "entry_exchange_order_id": trade.exchange_order_id,
                "protection_client_order_id": trade.protection_client_order_id,
                "closing_order_ids": sorted(
                    {str(value) for value in closing_order_ids if value}
                ),
                "started_at": trade.started_at.astimezone(timezone.utc).isoformat(),
                "closed_at": closed_utc.isoformat(),
                "net_pnl": str(net_pnl),
            }
        )
        self._state["realized_pnl_events"] = sorted(
            events, key=lambda item: (item["closed_at"], item["event_id"])
        )

    def _normalize_realized_pnl_events(
        self,
        now: datetime | None = None,
    ) -> list[dict[str, Any]]:
        reference = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        cutoff = reference - timedelta(
            days=self.settings.okx_demo_performance_snapshot_retention_days
        )
        normalized: dict[str, dict[str, Any]] = {}
        raw_events = self._state.get("realized_pnl_events") or []
        if not isinstance(raw_events, list):
            raw_events = []
        for raw in raw_events:
            if not isinstance(raw, dict):
                continue
            event_id = str(raw.get("event_id") or "")
            instrument_id = str(raw.get("instrument_id") or "")
            closed_at = self._parse_datetime(raw.get("closed_at"))
            if not event_id or not instrument_id or closed_at is None:
                continue
            if closed_at < cutoff or closed_at > reference + timedelta(minutes=5):
                continue
            try:
                net_pnl = D(str(raw.get("net_pnl")))
            except (ArithmeticError, ValueError):
                continue
            event: dict[str, Any] = {
                "event_id": event_id,
                "instrument_id": instrument_id,
                "closed_at": closed_at.isoformat(),
                "net_pnl": str(net_pnl),
            }
            for key in (
                "strategy",
                "direction",
                "settlement_currency",
                "entry_client_order_id",
                "entry_exchange_order_id",
                "protection_client_order_id",
            ):
                value = raw.get(key)
                event[key] = str(value) if value not in (None, "") else None
            reference_price = raw.get("reference_price")
            try:
                event["reference_price"] = (
                    str(D(str(reference_price)))
                    if reference_price not in (None, "")
                    else None
                )
            except (ArithmeticError, ValueError):
                event["reference_price"] = None
            started_at = self._parse_datetime(raw.get("started_at"))
            event["started_at"] = (
                started_at.isoformat() if started_at is not None else None
            )
            raw_closing_ids = raw.get("closing_order_ids")
            event["closing_order_ids"] = sorted(
                {
                    str(value)
                    for value in (
                        raw_closing_ids if isinstance(raw_closing_ids, list) else []
                    )
                    if value
                }
            )
            normalized[event_id] = event
        events = sorted(
            normalized.values(), key=lambda item: (item["closed_at"], item["event_id"])
        )
        self._state["realized_pnl_events"] = events
        return events

    def _rolling_realized_pnl(self, now: datetime | None = None) -> Decimal:
        reference = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        cutoff = reference - timedelta(days=7)
        return sum(
            (
                D(item["net_pnl"])
                for item in self._normalize_realized_pnl_events(reference)
                if (closed_at := self._parse_datetime(item["closed_at"])) is not None
                and cutoff <= closed_at <= reference + timedelta(minutes=5)
            ),
            D("0"),
        )

    @staticmethod
    def _is_closing_order(order: OkxDemoOrderView) -> bool:
        if order.reduce_only:
            return True
        position_side = str(order.raw.get("posSide") or order.position_side or "").lower()
        side = order.side.lower()
        return (position_side == "long" and side == "sell") or (
            position_side == "short" and side == "buy"
        )

    @staticmethod
    def _first_decimal(raw: dict[str, Any], *keys: str) -> Decimal | None:
        value, present = SafeDemoAutomation._decimal_with_presence(raw, *keys)
        return value if present else None

    @staticmethod
    def _decimal_with_presence(
        raw: dict[str, Any], *keys: str
    ) -> tuple[Decimal, bool]:
        for key in keys:
            value = raw.get(key)
            if value not in (None, ""):
                try:
                    return D(str(value)), True
                except (ArithmeticError, ValueError):
                    continue
        return D("0"), False

    def _apply_locks(self, equity: Decimal) -> None:
        reasons: list[str] = []
        if _EQUITY_BASIS_LOCK in self._state["lock_reasons"]:
            reasons.append(_EQUITY_BASIS_LOCK)
        if not self.settings.okx_demo_continuous_session_enabled:
            baseline = self._state["baseline_equity"]
            if baseline is not None and baseline > 0:
                loss_limit = baseline * D(
                    str(self.settings.okx_demo_daily_loss_limit_pct)
                )
                if self._state["daily_pnl"] <= -loss_limit:
                    reasons.append("daily_loss_limit_reached")
            if (
                int(self._state["trades_today"])
                >= self.settings.okx_demo_max_trades_per_day
            ):
                reasons.append("daily_trade_count_limit_reached")
            if (
                int(self._state["consecutive_losses"])
                >= self.settings.okx_demo_automation_max_consecutive_losses
            ):
                reasons.append("consecutive_loss_limit_reached")
        if self._state["emergency_stop"]:
            reasons.append("emergency_stop_engaged")
            reasons.extend(
                reason
                for reason in self._state["lock_reasons"]
                if reason
                in {
                    "active_trade_exceeds_position_margin_bucket",
                    "active_portfolio_margin_limit_exceeded",
                    "active_portfolio_stop_risk_limit_exceeded",
                    "capital_bucket_position_limit_exceeded",
                    "exchange_position_limit_exceeded",
                    "isolated_margin_mode_mismatch",
                    "multiple_positions_per_instrument_detected",
                    "portfolio_state_invalid",
                    "post_submission_acknowledgement_invalid",
                    "post_submission_fingerprint_persistence_failed",
                    "post_submission_state_persistence_failed",
                    "tracked_position_protection_missing_or_mismatched",
                    "trade_outcome_unconfirmed",
                    "untracked_exchange_exposure_detected",
                }
            )
        self._state["lock_reasons"] = sorted(set(reasons))
        self._state["locked"] = bool(reasons)

    def _engage_emergency(self, reason: str) -> None:
        self._state["armed"] = False
        self._state["emergency_stop"] = True
        self._state["locked"] = True
        self._state["lock_reasons"] = sorted(
            set([*self._state["lock_reasons"], reason, "emergency_stop_engaged"])
        )

    def _cooldown_active(self, instrument_id: str, now: datetime) -> bool:
        if self.settings.okx_demo_continuous_session_enabled:
            return False
        value = (self._state.get("symbol_cooldowns") or {}).get(instrument_id)
        if value is None:
            return False
        closed = self._parse_datetime(value)
        if closed is None:
            return True
        return now < closed + timedelta(
            seconds=self._effective_trade_cooldown_seconds()
        )

    def _effective_trade_cooldown_seconds(self) -> int:
        if self.settings.okx_demo_continuous_session_enabled:
            return 0
        return self.settings.okx_demo_trade_cooldown_seconds

    def _clear_disabled_continuous_session_locks(self) -> None:
        if not self.settings.okx_demo_continuous_session_enabled:
            return
        disabled = {
            "daily_loss_limit_reached",
            "daily_trade_count_limit_reached",
            "consecutive_loss_limit_reached",
        }
        original = list(self._state["lock_reasons"])
        if not any(reason in disabled for reason in original):
            return
        retained = [
            reason
            for reason in original
            if reason not in disabled
        ]
        if self._state["emergency_stop"]:
            retained.append("emergency_stop_engaged")
        self._state["lock_reasons"] = sorted(set(retained))
        self._state["locked"] = bool(retained)

    def _normalize_portfolio_state(self) -> None:
        if self._state.get("risk_peak_equity") is None:
            self._state["risk_peak_equity"] = (
                self._state.get("peak_equity")
                or self._state.get("baseline_equity")
            )
        self._normalize_realized_pnl_events()
        raw_active = self._state.get("active_trades")
        if not isinstance(raw_active, dict):
            raw_active = {}

        if not raw_active and self._state.get("active_instrument_id"):
            instrument_id = str(self._state["active_instrument_id"])
            started_at = self._state.get("active_started_at") or datetime.now(timezone.utc)
            legacy = DemoAutomationActiveTrade(
                instrument_id=instrument_id,
                client_order_id=self._state.get("active_client_order_id"),
                tier="legacy",
                start_equity=self._state.get("active_start_equity"),
                started_at=started_at,
            )
            raw_active = {
                instrument_id: legacy.model_dump(mode="json")
            }

        normalized: dict[str, dict[str, Any]] = {}
        invalid: list[str] = []
        for key, value in raw_active.items():
            try:
                trade = DemoAutomationActiveTrade.model_validate(value)
            except Exception:
                invalid.append(str(key))
                continue
            normalized[trade.instrument_id] = trade.model_dump(mode="json")
        self._state["active_trades"] = normalized

        cooldowns = self._state.get("symbol_cooldowns")
        normalized_cooldowns: dict[str, str] = {}
        if isinstance(cooldowns, dict):
            for key, value in cooldowns.items():
                parsed = self._parse_datetime(value)
                if parsed is not None:
                    normalized_cooldowns[str(key)] = parsed.isoformat()
        self._state["symbol_cooldowns"] = normalized_cooldowns

        if invalid:
            self._state["armed"] = False
            self._state["emergency_stop"] = True
            self._state["locked"] = True
            self._state["lock_reasons"] = sorted(
                set([*self._state["lock_reasons"], "portfolio_state_invalid"])
            )
            self._state["last_error"] = (
                "portfolio_state_invalid:" + ",".join(sorted(invalid))
            )[:250]
        self._sync_legacy_active_fields()

    def _active_trades(self) -> list[DemoAutomationActiveTrade]:
        values: list[DemoAutomationActiveTrade] = []
        raw = self._state.get("active_trades") or {}
        if not isinstance(raw, dict):
            return values
        for value in raw.values():
            try:
                values.append(DemoAutomationActiveTrade.model_validate(value))
            except Exception:
                continue
        return sorted(values, key=lambda item: (item.started_at, item.instrument_id))

    def _set_active_trade(self, trade: DemoAutomationActiveTrade) -> None:
        active = dict(self._state.get("active_trades") or {})
        active[trade.instrument_id] = trade.model_dump(mode="json")
        self._state["active_trades"] = active
        self._sync_legacy_active_fields()

    def _remove_active_trade(self, instrument_id: str) -> None:
        active = dict(self._state.get("active_trades") or {})
        active.pop(instrument_id, None)
        self._state["active_trades"] = active
        self._sync_legacy_active_fields()

    def _sync_legacy_active_fields(self) -> None:
        active = self._active_trades()
        first = active[0] if active else None
        self._state["active_instrument_id"] = (
            first.instrument_id if first is not None else None
        )
        self._state["active_client_order_id"] = (
            first.client_order_id if first is not None else None
        )
        self._state["active_start_equity"] = (
            first.start_equity if first is not None else None
        )
        self._state["active_started_at"] = (
            first.started_at if first is not None else None
        )

    @staticmethod
    def _parse_datetime(value: Any) -> datetime | None:
        if isinstance(value, datetime):
            parsed = value
        elif isinstance(value, str):
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return None
        else:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    def _configuration_blockers(self) -> list[str]:
        blockers: list[str] = []
        if not self.settings.okx_demo_auto_execution:
            blockers.append("okx_demo_auto_execution_disabled")
        if self.settings.trading_mode != "okx_demo":
            blockers.append("okx_demo_trading_mode_required")
        if not self.settings.okx_demo_enabled:
            blockers.append("okx_demo_disabled")
        if not self.settings.okx_demo_allow_order_writes:
            blockers.append("okx_demo_order_writes_disabled")
        if not self.settings.okx_demo_credentials_configured:
            blockers.append("okx_demo_credentials_missing")
        if not self.settings.okx_ws_enabled:
            blockers.append("okx_public_websocket_required")
        if self.settings.paper_auto_execution:
            blockers.append("paper_auto_execution_must_be_disabled")
        if self.settings.auto_trade or self.settings.live_trading:
            blockers.append("real_auto_trade_must_remain_disabled")
        return blockers

    def _ensure_execute_ready(self, *, allow_session_lock_refresh: bool = False) -> None:
        blockers = self._configuration_blockers()
        if blockers:
            raise DemoAutomationSafetyError(";".join(blockers))
        if not self._state["armed"]:
            raise DemoAutomationSafetyError("demo_automation_not_armed")
        if self._state["emergency_stop"]:
            raise DemoAutomationSafetyError("emergency_stop_engaged")
        refreshable = {
            "daily_loss_limit_reached",
            "daily_trade_count_limit_reached",
            "consecutive_loss_limit_reached",
        }
        if self._state["locked"] and not (
            allow_session_lock_refresh
            and set(self._state["lock_reasons"]).issubset(refreshable)
        ):
            raise DemoAutomationSafetyError("automation_locked")

    async def _loop(self) -> None:
        initial = self.settings.okx_demo_scan_initial_delay_seconds
        if initial:
            self._next_run_at = datetime.now(timezone.utc) + timedelta(seconds=initial)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=initial)
                return
            except TimeoutError:
                pass
        while not self._stop.is_set():
            await self.run_once(execute=True, trigger="scheduled")
            interval = self.settings.okx_demo_scan_interval_seconds
            self._next_run_at = datetime.now(timezone.utc) + timedelta(seconds=interval)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=interval)
            except TimeoutError:
                continue

    async def _persist_state(self, *, required: bool) -> None:
        if self.repository is None:
            return
        payload = {key: self._state[key] for key in self._state}
        try:
            await self.repository.save_state(payload)
        except Exception as exc:
            self._state["last_error"] = "state_persistence_failed:" + self._safe_error(exc)
            if required:
                raise DemoAutomationSafetyError(self._state["last_error"]) from exc

    async def _fingerprint_exists(self, fingerprint: str, now: datetime) -> bool:
        self._fingerprints = {key: expiry for key, expiry in self._fingerprints.items() if expiry > now}
        if fingerprint in self._fingerprints:
            return True
        if self.repository is not None:
            await self.repository.cleanup_fingerprints(now)
            return await self.repository.fingerprint_exists(fingerprint, now)
        return False

    async def _save_fingerprint(
        self, fingerprint: str, expires_at: datetime, details: dict[str, Any]
    ) -> None:
        self._fingerprints[fingerprint] = expires_at
        if self.repository is not None:
            await self.repository.save_fingerprint(fingerprint, expires_at, details)

    @staticmethod
    def _fingerprint(instrument_id: str, candidate: TradeCandidate) -> str:
        raw = "|".join(
            [
                instrument_id,
                candidate.strategy,
                candidate.direction,
                str(candidate.entry),
                str(candidate.stop_loss),
                str(candidate.take_profit),
                candidate.expires_at.isoformat(),
            ]
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _candidate_result(
        symbol: str,
        instrument_id: str,
        candidate: TradeCandidate,
        *,
        outcome: str,
        reference_price: Decimal,
        detail: str,
        execution_limit_price: Decimal | None = None,
        average_fill_price: Decimal | None = None,
        actual_gross_risk_reward: Decimal | None = None,
        actual_net_risk_reward: Decimal | None = None,
        actual_enforced_risk_reward: Decimal | None = None,
        adverse_fill_slippage_bps: Decimal | None = None,
        approved_base_quantity: Decimal | None = None,
        approved_contracts: Decimal | None = None,
        score_tier: DemoAutomationRiskTier | None = None,
        selected_leverage: int | None = None,
        required_leverage: int | None = None,
        leverage_cap: int | None = None,
        leverage_cap_reasons: list[str] | None = None,
        risk_budget_pct: Decimal | None = None,
        estimated_stop_loss_pct: Decimal | None = None,
        margin_allocation_pct: Decimal | None = None,
        estimated_margin: Decimal | None = None,
        position_margin_cap_usdt: Decimal | None = None,
        capital_bucket_usdt: Decimal | None = None,
        client_order_id: str | None = None,
        exchange_order_id: str | None = None,
        order_submission_attempted: bool = False,
        reason_codes: list[str] | None = None,
    ) -> DemoAutomationSymbolResult:
        return DemoAutomationSymbolResult(
            symbol=symbol,
            instrument_id=instrument_id,
            outcome=outcome,
            direction=candidate.direction,
            strategy=candidate.strategy,
            score=candidate.score,
            effective_score=(
                candidate.risk_score
                if candidate.risk_score is not None
                else candidate.score
            ),
            derivative_status=(
                candidate.derivative_confirmation.status
                if candidate.derivative_confirmation is not None
                else None
            ),
            derivative_confidence=(
                candidate.derivative_confirmation.confidence
                if candidate.derivative_confirmation is not None
                else None
            ),
            mathematical_status=(
                candidate.mathematical_confirmation.status
                if candidate.mathematical_confirmation is not None
                else None
            ),
            mathematical_risk_grade=(
                candidate.mathematical_confirmation.risk_grade
                if candidate.mathematical_confirmation is not None
                else None
            ),
            mathematical_confidence=(
                candidate.mathematical_confirmation.confidence
                if candidate.mathematical_confirmation is not None
                else None
            ),
            mathematical_reliability=(
                candidate.mathematical_confirmation.reliability
                if candidate.mathematical_confirmation is not None
                else None
            ),
            mathematical_auxiliary_bonus=(
                candidate.mathematical_confirmation.auxiliary_bonus
                if candidate.mathematical_confirmation is not None
                else 0
            ),
            mathematical_validated_components=(
                candidate.mathematical_confirmation.component_codes
                if candidate.mathematical_confirmation is not None
                else []
            ),
            mathematical_auxiliary_components=(
                candidate.mathematical_confirmation.auxiliary_component_codes
                if candidate.mathematical_confirmation is not None
                else []
            ),
            reference_price=reference_price,
            execution_order_type=(
                "fok" if execution_limit_price is not None else None
            ),
            execution_limit_price=execution_limit_price,
            average_fill_price=average_fill_price,
            actual_gross_risk_reward=actual_gross_risk_reward,
            actual_net_risk_reward=actual_net_risk_reward,
            actual_enforced_risk_reward=actual_enforced_risk_reward,
            adverse_fill_slippage_bps=adverse_fill_slippage_bps,
            stop_loss=candidate.stop_loss,
            take_profit=candidate.take_profit,
            risk_reward=candidate.risk_reward,
            approved_base_quantity=approved_base_quantity,
            approved_contracts=approved_contracts,
            score_tier=score_tier.name if score_tier is not None else None,
            selected_leverage=selected_leverage,
            required_leverage=required_leverage,
            leverage_cap=leverage_cap,
            leverage_cap_reasons=leverage_cap_reasons or [],
            margin_mode=(
                "isolated" if candidate.protection_model == "structure" else "cross"
            ),
            risk_budget_pct=risk_budget_pct,
            estimated_stop_loss_pct=estimated_stop_loss_pct,
            margin_allocation_pct=margin_allocation_pct,
            estimated_margin=estimated_margin,
            protection_model=candidate.protection_model,
            structure_timeframe=(
                candidate.structural_protection.timeframe
                if candidate.protection_model == "structure"
                and candidate.structural_protection is not None
                else None
            ),
            structure_source_closed_at=(
                candidate.structural_protection.source_closed_at
                if candidate.protection_model == "structure"
                and candidate.structural_protection is not None
                else None
            ),
            structure_stop_anchor=(
                candidate.structural_protection.stop_anchor
                if candidate.protection_model == "structure"
                and candidate.structural_protection is not None
                else None
            ),
            structure_target_anchor=(
                candidate.structural_protection.target_anchor
                if candidate.protection_model == "structure"
                and candidate.structural_protection is not None
                else None
            ),
            structure_volatility_buffer=(
                candidate.structural_protection.volatility_buffer
                if candidate.protection_model == "structure"
                and candidate.structural_protection is not None
                else None
            ),
            estimated_round_trip_cost_pct=(
                candidate.estimated_round_trip_cost_pct
            ),
            estimated_cost_amount=(
                estimated_margin
                * D(selected_leverage)
                * candidate.estimated_round_trip_cost_pct
                if estimated_margin is not None and selected_leverage is not None
                else None
            ),
            gross_risk_reward=candidate.gross_risk_reward,
            net_risk_reward=candidate.net_risk_reward,
            position_margin_cap_usdt=position_margin_cap_usdt,
            capital_bucket_usdt=capital_bucket_usdt,
            client_order_id=client_order_id,
            exchange_order_id=exchange_order_id,
            order_submission_attempted=order_submission_attempted,
            reason_codes=reason_codes or [],
            detail=detail,
        )

    @staticmethod
    def _safe_error(exc: Exception) -> str:
        return f"{exc.__class__.__name__}: {exc}"[:250]
