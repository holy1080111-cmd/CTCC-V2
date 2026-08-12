from __future__ import annotations

import asyncio
from collections import deque
from contextlib import suppress
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, ROUND_DOWN
import hashlib
import logging
from typing import Any, Iterable

from app.config.settings import Settings, get_settings
from app.database.repositories.demo_automation import DemoAutomationRepository
from app.demo_automation import DemoAutomationBusyError, DemoAutomationSafetyError
from app.demo_automation.risk_profile import (
    configured_score_risk_tiers,
    mathematical_adjusted_score,
    score_risk_tier,
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
    OkxDemoBalanceSnapshot,
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
from app.okx_demo.service import OkxDemoService, okx_demo_service
from app.risk import RiskService
from app.strategies import StrategyService

logger = logging.getLogger(__name__)
D = Decimal
_AUTOMATION_SETTLEMENT_CURRENCY = "USDT"
_EQUITY_BASIS_LOCK = "equity_basis_change_requires_flat_session"


@dataclass(frozen=True)
class _AutomationCapital:
    risk_equity: Decimal
    available_equity: Decimal
    currency: str
    basis: str


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
            "daily_pnl": D("0"),
            "trades_today": 0,
            "consecutive_losses": 0,
            "active_instrument_id": None,
            "active_client_order_id": None,
            "active_start_equity": None,
            "active_started_at": None,
            "active_trades": {},
            "symbol_cooldowns": {},
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
        self._recovered = True
        await self._persist_state(required=False)

    async def status(self) -> DemoAutomationStatus:
        active_trades = self._active_trades()
        open_risk, margin_pct = self._portfolio_usage(active_trades)
        return DemoAutomationStatus(
            capability_enabled=self.settings.okx_demo_auto_execution,
            trading_mode=self.settings.trading_mode,
            demo_writes_enabled=self.settings.okx_demo_allow_order_writes,
            armed=bool(self._state["armed"]),
            running=self.running,
            emergency_stop=bool(self._state["emergency_stop"]),
            locked=bool(self._state["locked"]),
            lock_reasons=list(self._state["lock_reasons"]),
            configuration_blockers=self._configuration_blockers(),
            symbols=self.settings.okx_demo_scan_symbol_list,
            scan_interval_seconds=self.settings.okx_demo_scan_interval_seconds,
            max_trades_per_day=self.settings.okx_demo_max_trades_per_day,
            daily_loss_limit_pct=D(str(self.settings.okx_demo_daily_loss_limit_pct)),
            max_consecutive_losses=self.settings.okx_demo_automation_max_consecutive_losses,
            score_risk_enabled=self.settings.okx_demo_score_risk_enabled,
            derivative_risk_gate_enabled=self.settings.okx_demo_score_risk_enabled,
            mathematical_risk_gate_enabled=self.settings.okx_demo_score_risk_enabled,
            score_risk_tiers=(
                configured_score_risk_tiers(self.settings)
                if self.settings.okx_demo_score_risk_enabled
                else []
            ),
            max_open_positions=self._portfolio_position_limit(),
            portfolio_max_risk_pct=(
                D(str(self.settings.okx_demo_portfolio_max_risk_pct))
                if self.settings.okx_demo_score_risk_enabled
                else D("0")
            ),
            portfolio_max_margin_pct=(
                D(str(self.settings.okx_demo_portfolio_max_margin_pct))
                if self.settings.okx_demo_score_risk_enabled
                else D("0")
            ),
            portfolio_open_risk_pct=open_risk,
            portfolio_margin_pct=margin_pct,
            active_position_count=len(active_trades),
            active_trades=active_trades,
            session_date=self._state["session_date"],
            equity_basis=self._state["equity_basis"],
            baseline_equity=self._state["baseline_equity"],
            peak_equity=self._state["peak_equity"],
            daily_pnl=self._state["daily_pnl"],
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
                risk_equity = capital.risk_equity
                risk_equity_currency = capital.currency
                available_margin_equity = capital.available_equity
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
                self._finalize_active_trades(snapshot, risk_equity)
                self._refresh_active_trade_estimates(snapshot, risk_equity)
                self._apply_locks(risk_equity)
                portfolio_view = self._active_trades()

                tracked_symbols = {item.instrument_id for item in portfolio_view}
                untracked_symbols = exposed_symbols - tracked_symbols
                position_symbols = [item.instrument_id for item in snapshot.positions]
                exposure_violation: str | None = None
                if untracked_symbols:
                    exposure_violation = "untracked_exchange_exposure_detected"
                elif len(position_symbols) > self._portfolio_position_limit():
                    exposure_violation = "exchange_position_limit_exceeded"
                elif len(position_symbols) != len(set(position_symbols)):
                    exposure_violation = "multiple_positions_per_instrument_detected"
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
                        if result.outcome == "submitted":
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
                daily_pnl=self._state["daily_pnl"],
                trades_today=int(self._state["trades_today"]),
                consecutive_losses=int(self._state["consecutive_losses"]),
                active_position_count=len(portfolio_view),
                portfolio_open_risk_pct=self._portfolio_usage(portfolio_view)[0],
                portfolio_margin_pct=self._portfolio_usage(portfolio_view)[1],
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
            execution_candidate = self._candidate_at_reference(candidate, reference_price)
            if execution_candidate is None:
                return (
                    self._candidate_result(
                        strategy.symbol,
                        instrument_id,
                        candidate,
                        outcome="blocked",
                        reference_price=reference_price,
                        detail="reference_price_outside_protective_bounds",
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

            position_limit = self._portfolio_position_limit()
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
            if not instruments:
                return (
                    self._candidate_result(
                        strategy.symbol,
                        instrument_id,
                        execution_candidate,
                        outcome="blocked",
                        reference_price=reference_price,
                        detail="instrument_metadata_not_available",
                    ),
                    None,
                )
            instrument = instruments[0]
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
            aligned_candidate = execution_candidate.model_copy(
                update={"stop_loss": stop_loss, "take_profit": take_profit}
            )

            tier, requested_risk_pct, leverage, margin_cap_pct = self._risk_profile(
                aligned_candidate.risk_score
                if aligned_candidate.risk_score is not None
                else aligned_candidate.score
            )
            open_risk_pct, open_margin_pct = self._portfolio_usage(portfolio)
            if self.settings.okx_demo_score_risk_enabled:
                remaining_risk = max(
                    D("0"),
                    D(str(self.settings.okx_demo_portfolio_max_risk_pct))
                    - open_risk_pct,
                )
                remaining_margin = max(
                    D("0"),
                    D(str(self.settings.okx_demo_portfolio_max_margin_pct))
                    - open_margin_pct,
                )
                requested_risk_pct = min(requested_risk_pct, remaining_risk)
                margin_cap_pct = min(margin_cap_pct, remaining_margin)
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
                margin_notional_cap = balance_equity * margin_cap_pct * D(leverage)
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
                daily_realized_pnl=min(D("0"), self._state["daily_pnl"]),
                weekly_realized_pnl=min(D("0"), self._state["daily_pnl"]),
                peak_equity=self._state["peak_equity"] or balance_equity,
                consecutive_losses=int(self._state["consecutive_losses"]),
                open_positions=len(portfolio),
                same_direction_positions=sum(
                    1 for item in portfolio if item.direction == aligned_candidate.direction
                ),
                correlated_positions=len(portfolio),
            )
            risk = self.risk_service.evaluate(
                aligned_candidate,
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
                        risk_budget_pct=requested_risk_pct,
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
                        risk_budget_pct=requested_risk_pct,
                        detail=size_error,
                    ),
                    None,
                )

            base_quantity = contracts * instrument.contract_value
            notional = base_quantity * reference_price
            estimated_margin = notional / D(leverage)
            estimated_margin_pct = estimated_margin / balance_equity
            estimated_stop_loss_amount = base_quantity * abs(
                reference_price - stop_loss
            )
            estimated_stop_loss_pct = estimated_stop_loss_amount / balance_equity
            if self.settings.okx_demo_score_risk_enabled and (
                open_risk_pct + estimated_stop_loss_pct
                > D(str(self.settings.okx_demo_portfolio_max_risk_pct))
                or open_margin_pct + estimated_margin_pct
                > D(str(self.settings.okx_demo_portfolio_max_margin_pct))
            ):
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
                        risk_budget_pct=requested_risk_pct,
                        estimated_stop_loss_pct=estimated_stop_loss_pct,
                        margin_allocation_pct=estimated_margin_pct,
                        estimated_margin=estimated_margin,
                        detail="rounded_order_exceeds_portfolio_budget",
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
                risk_budget_pct=requested_risk_pct,
                estimated_stop_loss_amount=estimated_stop_loss_amount,
                estimated_stop_loss_pct=estimated_stop_loss_pct,
                estimated_notional=notional,
                margin_allocation_pct=estimated_margin_pct,
                estimated_margin=estimated_margin,
                reference_price=reference_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                start_equity=balance_equity,
                started_at=now,
            )
            if not execute:
                return (
                    self._candidate_result(
                        strategy.symbol,
                        instrument_id,
                        aligned_candidate,
                        outcome="approved_dry_run",
                        reference_price=reference_price,
                        approved_base_quantity=base_quantity,
                        approved_contracts=contracts,
                        score_tier=tier,
                        selected_leverage=leverage,
                        risk_budget_pct=requested_risk_pct,
                        estimated_stop_loss_pct=estimated_stop_loss_pct,
                        margin_allocation_pct=estimated_margin_pct,
                        estimated_margin=estimated_margin,
                        detail="risk_approved_demo_execution_disabled_for_this_run",
                    ),
                    reservation,
                )

            client_order_id = "AUT" + fingerprint[:29]
            await self.demo_service.set_leverage(
                OkxDemoLeverageRequest(
                    instrument_id=instrument_id,
                    leverage=leverage,
                    margin_mode="cross",
                    direction=aligned_candidate.direction,
                    confirmation=DEMO_CONFIRMATION_PHRASE,
                )
            )
            write = await self.demo_service.place_order(
                OkxDemoOrderRequest(
                    instrument_id=instrument_id,
                    direction=aligned_candidate.direction,
                    size=contracts,
                    margin_mode="cross",
                    order_type="market",
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    trigger_price_type="mark",
                    client_order_id=client_order_id,
                    confirmation=DEMO_CONFIRMATION_PHRASE,
                )
            )
            acknowledgement = write.acknowledgement
            exchange_order_id = (
                acknowledgement.order_id if acknowledgement is not None else None
            )
            reservation = reservation.model_copy(
                update={
                    "client_order_id": client_order_id,
                    "exchange_order_id": exchange_order_id,
                }
            )
            self._set_active_trade(reservation)
            self._state["trades_today"] = int(self._state["trades_today"]) + 1
            expiry = max(
                aligned_candidate.expires_at,
                now + timedelta(seconds=self.settings.okx_demo_trade_cooldown_seconds),
            )
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
                await self._save_fingerprint(
                    fingerprint,
                    expiry,
                    {
                        "instrument_id": instrument_id,
                        "strategy": aligned_candidate.strategy,
                        "client_order_id": client_order_id,
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
                    aligned_candidate,
                    outcome="submitted",
                    reference_price=reference_price,
                    approved_base_quantity=base_quantity,
                    approved_contracts=contracts,
                    score_tier=tier,
                    selected_leverage=leverage,
                    risk_budget_pct=requested_risk_pct,
                    estimated_stop_loss_pct=estimated_stop_loss_pct,
                    margin_allocation_pct=estimated_margin_pct,
                    estimated_margin=estimated_margin,
                    client_order_id=client_order_id,
                    exchange_order_id=exchange_order_id,
                    detail="protected_okx_demo_market_order_submitted",
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
        if snapshot is None or snapshot.last is None:
            return candidate.entry, "realtime_snapshot_not_available" if require_realtime else None
        age = (datetime.now(timezone.utc) - snapshot.received_at).total_seconds()
        if age > self.settings.okx_demo_scan_max_snapshot_age_seconds:
            return snapshot.last, "realtime_snapshot_stale" if require_realtime else None
        drift_bps = abs(snapshot.last - candidate.entry) / candidate.entry * D("10000")
        if drift_bps > D(str(self.settings.okx_demo_scan_max_entry_drift_bps)):
            return snapshot.last, "entry_price_drift_exceeds_limit"
        return snapshot.last, None

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
            sl_round, tp_round = ROUND_FLOOR, ROUND_CEILING
        else:
            sl_round, tp_round = ROUND_CEILING, ROUND_FLOOR
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

    def _portfolio_position_limit(self) -> int:
        if self.settings.okx_demo_score_risk_enabled:
            return self.settings.okx_demo_max_open_positions
        return 1

    @staticmethod
    def _automation_capital(
        snapshot: OkxDemoReconcileResult,
    ) -> tuple[_AutomationCapital | None, str]:
        account_level = snapshot.account_config.account_level or ""
        balance: OkxDemoBalanceSnapshot = snapshot.balance
        if account_level == "2":
            matches = [
                item
                for item in balance.details
                if item.currency.upper() == _AUTOMATION_SETTLEMENT_CURRENCY
            ]
            if len(matches) != 1:
                return None, "demo_settlement_currency_balance_unavailable"
            detail = matches[0]
            if detail.equity <= 0:
                return None, "demo_settlement_currency_equity_exhausted"
            return (
                _AutomationCapital(
                    risk_equity=detail.equity,
                    available_equity=min(
                        detail.equity,
                        max(D("0"), detail.available_equity),
                    ),
                    currency=_AUTOMATION_SETTLEMENT_CURRENCY,
                    basis=(
                        "single_currency:"
                        + _AUTOMATION_SETTLEMENT_CURRENCY
                    ),
                ),
                "",
            )
        if account_level in {"3", "4"}:
            if balance.adjusted_equity <= 0:
                return None, "demo_account_adjusted_equity_unavailable"
            return (
                _AutomationCapital(
                    risk_equity=balance.adjusted_equity,
                    available_equity=min(
                        balance.adjusted_equity,
                        max(D("0"), balance.available_equity),
                    ),
                    currency="USD",
                    basis="account_adjusted:USD",
                ),
                "",
            )
        return None, "unsupported_okx_demo_account_level"

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
            minimum_risk_reward=D(str(self.settings.strategy_min_risk_reward)),
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
        baseline = self._state["baseline_equity"]
        if baseline is not None:
            self._state["daily_pnl"] = equity - baseline
        peak = self._state["peak_equity"]
        self._state["peak_equity"] = equity if peak is None else max(peak, equity)
        return None

    def _finalize_active_trades(
        self,
        snapshot: OkxDemoReconcileResult,
        risk_equity: Decimal,
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

        outcomes: list[tuple[datetime, DemoAutomationActiveTrade, Decimal]] = []
        unknown: list[DemoAutomationActiveTrade] = []
        for trade in closed:
            outcome = self._closing_trade_outcome(trade, snapshot.recent_orders)
            if outcome is None:
                unknown.append(trade)
            else:
                closed_at, net_pnl = outcome
                outcomes.append((closed_at, trade, net_pnl))

        # The former single-position implementation used account equity delta.
        # Retain that fallback only when no other trade can contaminate it.
        if unknown and len(active) == 1 and len(closed) == 1:
            trade = unknown.pop()
            if trade.start_equity is not None:
                outcomes.append(
                    (
                        snapshot.reconciled_at,
                        trade,
                        risk_equity - trade.start_equity,
                    )
                )

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
        for closed_at, trade, net_pnl in sorted(outcomes, key=lambda item: item[0]):
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
    ) -> tuple[datetime, Decimal] | None:
        matches: list[tuple[datetime, Decimal]] = []
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
            matches.append(
                (closed_at, realized - fee_cost + rebate + funding)
            )
        if not matches:
            return None
        return max(item[0] for item in matches), sum(
            (item[1] for item in matches), D("0")
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
        baseline = self._state["baseline_equity"]
        if baseline is not None and baseline > 0:
            loss_limit = baseline * D(str(self.settings.okx_demo_daily_loss_limit_pct))
            if self._state["daily_pnl"] <= -loss_limit:
                reasons.append("daily_loss_limit_reached")
        if int(self._state["trades_today"]) >= self.settings.okx_demo_max_trades_per_day:
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
                    "exchange_position_limit_exceeded",
                    "multiple_positions_per_instrument_detected",
                    "portfolio_state_invalid",
                    "post_submission_acknowledgement_invalid",
                    "post_submission_fingerprint_persistence_failed",
                    "post_submission_state_persistence_failed",
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
        value = (self._state.get("symbol_cooldowns") or {}).get(instrument_id)
        if value is None:
            return False
        closed = self._parse_datetime(value)
        if closed is None:
            return True
        return now < closed + timedelta(seconds=self.settings.okx_demo_trade_cooldown_seconds)

    def _normalize_portfolio_state(self) -> None:
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
        approved_base_quantity: Decimal | None = None,
        approved_contracts: Decimal | None = None,
        score_tier: DemoAutomationRiskTier | None = None,
        selected_leverage: int | None = None,
        risk_budget_pct: Decimal | None = None,
        estimated_stop_loss_pct: Decimal | None = None,
        margin_allocation_pct: Decimal | None = None,
        estimated_margin: Decimal | None = None,
        client_order_id: str | None = None,
        exchange_order_id: str | None = None,
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
            stop_loss=candidate.stop_loss,
            take_profit=candidate.take_profit,
            risk_reward=candidate.risk_reward,
            approved_base_quantity=approved_base_quantity,
            approved_contracts=approved_contracts,
            score_tier=score_tier.name if score_tier is not None else None,
            selected_leverage=selected_leverage,
            risk_budget_pct=risk_budget_pct,
            estimated_stop_loss_pct=estimated_stop_loss_pct,
            margin_allocation_pct=margin_allocation_pct,
            estimated_margin=estimated_margin,
            client_order_id=client_order_id,
            exchange_order_id=exchange_order_id,
            reason_codes=reason_codes or [],
            detail=detail,
        )

    @staticmethod
    def _safe_error(exc: Exception) -> str:
        return f"{exc.__class__.__name__}: {exc}"[:250]
