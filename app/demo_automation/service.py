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
from app.domain.demo_automation import (
    DemoAutomationRunResult,
    DemoAutomationStatus,
    DemoAutomationSymbolResult,
)
from app.domain.okx_demo import (
    DEMO_CONFIRMATION_PHRASE,
    OkxDemoLeverageRequest,
    OkxDemoOrderRequest,
    OkxDemoReconcileResult,
)
from app.domain.realtime import RealtimeSnapshot
from app.domain.risk import AccountRiskState, RiskLimits
from app.domain.strategy import TradeCandidate
from app.exchange.okx.public_rest import OkxPublicRestClient
from app.exchange.okx.symbols import to_instrument_id
from app.okx_demo.service import OkxDemoService, okx_demo_service
from app.risk import RiskService
from app.strategies import StrategyService

logger = logging.getLogger(__name__)
D = Decimal


class SafeDemoAutomation:
    """Explicitly armed, Demo-only strategy automation.

    The service never enables real trading, never auto-arms after restart, and
    submits at most one protected Demo order per scan.
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
            "baseline_equity": None,
            "peak_equity": None,
            "daily_pnl": D("0"),
            "trades_today": 0,
            "consecutive_losses": 0,
            "active_instrument_id": None,
            "active_client_order_id": None,
            "active_start_equity": None,
            "active_started_at": None,
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
        self._recovered = True
        await self._persist_state(required=False)

    async def status(self) -> DemoAutomationStatus:
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
            session_date=self._state["session_date"],
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
        self._roll_session(snapshot.balance.total_equity, force_if_empty=True)
        self._apply_locks(snapshot.balance.total_equity)
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
        self._state["emergency_stop"] = False
        self._apply_locks(snapshot.balance.total_equity)
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
    ) -> DemoAutomationRunResult:
        if self._run_lock.locked():
            raise DemoAutomationBusyError("demo_automation_run_already_in_progress")
        if execute:
            self._ensure_execute_ready()

        async with self._run_lock:
            started = datetime.now(timezone.utc)
            self._state["last_started_at"] = started
            results: list[DemoAutomationSymbolResult] = []
            total_equity: Decimal | None = None
            try:
                snapshot = await self.demo_service.reconcile()
                total_equity = snapshot.balance.total_equity
                self._roll_session(total_equity)
                self._finalize_active_trade(snapshot)
                self._apply_locks(total_equity)

                if execute and self._state["locked"]:
                    results.append(
                        DemoAutomationSymbolResult(
                            symbol="*",
                            outcome="locked",
                            reason_codes=list(self._state["lock_reasons"]),
                            detail="automation_safety_lock_active",
                        )
                    )
                elif snapshot.positions or snapshot.pending_orders or snapshot.pending_algo_orders:
                    detail = (
                        "tracked_trade_monitoring"
                        if self._state["active_instrument_id"]
                        else "untracked_exchange_exposure_blocks_automation"
                    )
                    results.append(
                        DemoAutomationSymbolResult(
                            symbol=self._state["active_instrument_id"] or "*",
                            instrument_id=self._state["active_instrument_id"],
                            outcome="monitoring" if self._state["active_instrument_id"] else "blocked",
                            detail=detail,
                        )
                    )
                elif self._cooldown_active(started):
                    results.append(
                        DemoAutomationSymbolResult(
                            symbol="*",
                            outcome="blocked",
                            detail="post_trade_cooldown_active",
                        )
                    )
                else:
                    requested = list(symbols or self.settings.okx_demo_scan_symbol_list)
                    for raw_symbol in requested:
                        result = await self._process_symbol(
                            raw_symbol,
                            execute=execute,
                            balance_equity=total_equity,
                        )
                        results.append(result)
                        if result.outcome == "submitted":
                            break
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
                daily_pnl=self._state["daily_pnl"],
                trades_today=int(self._state["trades_today"]),
                consecutive_losses=int(self._state["consecutive_losses"]),
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

    async def _process_symbol(
        self,
        raw_symbol: str,
        *,
        execute: bool,
        balance_equity: Decimal,
    ) -> DemoAutomationSymbolResult:
        try:
            instrument_id = to_instrument_id(raw_symbol)
        except ValueError as exc:
            return DemoAutomationSymbolResult(
                symbol=raw_symbol, outcome="blocked", detail=str(exc)
            )
        if instrument_id not in self.settings.okx_demo_allowed_symbol_list:
            return DemoAutomationSymbolResult(
                symbol=raw_symbol,
                instrument_id=instrument_id,
                outcome="blocked",
                detail="instrument_not_in_demo_allowlist",
            )

        try:
            disabled_strategies: set[str] = set()
            if self.strategy_control_repository is not None:
                disabled_strategies = await self.strategy_control_repository.disabled_strategies()
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
            candidate = strategy.selected_candidate
            if candidate is None:
                return DemoAutomationSymbolResult(
                    symbol=strategy.symbol,
                    instrument_id=instrument_id,
                    outcome="no_trade",
                    detail=";".join(strategy.blockers) or "no_strategy_candidate",
                )

            reference_price, realtime_error = await self._reference_price(
                instrument_id, candidate, require_realtime=execute
            )
            if realtime_error:
                return self._candidate_result(
                    strategy.symbol,
                    instrument_id,
                    candidate,
                    outcome="blocked",
                    reference_price=reference_price,
                    detail=realtime_error,
                )
            execution_candidate = self._candidate_at_reference(candidate, reference_price)
            if execution_candidate is None:
                return self._candidate_result(
                    strategy.symbol,
                    instrument_id,
                    candidate,
                    outcome="blocked",
                    reference_price=reference_price,
                    detail="reference_price_outside_protective_bounds",
                )

            now = datetime.now(timezone.utc)
            fingerprint = self._fingerprint(instrument_id, execution_candidate)
            if await self._fingerprint_exists(fingerprint, now):
                return self._candidate_result(
                    strategy.symbol,
                    instrument_id,
                    execution_candidate,
                    outcome="duplicate",
                    reference_price=reference_price,
                    detail="candidate_fingerprint_already_processed",
                )

            account = AccountRiskState(
                equity=balance_equity,
                daily_realized_pnl=min(D("0"), self._state["daily_pnl"]),
                weekly_realized_pnl=min(D("0"), self._state["daily_pnl"]),
                peak_equity=self._state["peak_equity"] or balance_equity,
                consecutive_losses=int(self._state["consecutive_losses"]),
                open_positions=0,
                same_direction_positions=0,
                correlated_positions=0,
            )
            risk = self.risk_service.evaluate(
                execution_candidate, account, self._risk_limits()
            )
            if risk.decision != "approved":
                return self._candidate_result(
                    strategy.symbol,
                    instrument_id,
                    execution_candidate,
                    outcome="risk_rejected",
                    reference_price=reference_price,
                    approved_base_quantity=risk.approved_quantity,
                    reason_codes=risk.reason_codes,
                    detail="risk_engine_rejected_candidate",
                )

            instruments = await self.public_client.instruments(instrument_id)
            if not instruments:
                return self._candidate_result(
                    strategy.symbol,
                    instrument_id,
                    execution_candidate,
                    outcome="blocked",
                    reference_price=reference_price,
                    detail="instrument_metadata_not_available",
                )
            instrument = instruments[0]
            contracts, size_error = self._contracts_from_base_quantity(
                risk.approved_quantity, instrument
            )
            if size_error:
                return self._candidate_result(
                    strategy.symbol,
                    instrument_id,
                    execution_candidate,
                    outcome="blocked",
                    reference_price=reference_price,
                    approved_base_quantity=risk.approved_quantity,
                    approved_contracts=contracts,
                    detail=size_error,
                )

            stop_loss, take_profit = self._align_protection(execution_candidate, instrument.tick_size)
            aligned_candidate = execution_candidate.model_copy(
                update={"stop_loss": stop_loss, "take_profit": take_profit}
            )
            if not execute:
                return self._candidate_result(
                    strategy.symbol,
                    instrument_id,
                    aligned_candidate,
                    outcome="approved_dry_run",
                    reference_price=reference_price,
                    approved_base_quantity=risk.approved_quantity,
                    approved_contracts=contracts,
                    detail="risk_approved_demo_execution_disabled_for_this_run",
                )

            client_order_id = "AUT" + fingerprint[:29]
            await self.demo_service.set_leverage(
                OkxDemoLeverageRequest(
                    instrument_id=instrument_id,
                    leverage=self.settings.okx_demo_automation_leverage,
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
            self._state["active_instrument_id"] = instrument_id
            self._state["active_client_order_id"] = client_order_id
            self._state["active_start_equity"] = balance_equity
            self._state["active_started_at"] = now
            self._state["trades_today"] = int(self._state["trades_today"]) + 1
            expiry = max(
                aligned_candidate.expires_at,
                now + timedelta(seconds=self.settings.okx_demo_trade_cooldown_seconds),
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
            await self._persist_state(required=False)
            exchange_order_id = (
                write.acknowledgement.order_id if write.acknowledgement else None
            )
            return self._candidate_result(
                strategy.symbol,
                instrument_id,
                aligned_candidate,
                outcome="submitted",
                reference_price=reference_price,
                approved_base_quantity=risk.approved_quantity,
                approved_contracts=contracts,
                client_order_id=client_order_id,
                exchange_order_id=exchange_order_id,
                detail="protected_okx_demo_market_order_submitted",
            )
        except Exception as exc:
            logger.exception("demo_automation_symbol_failed symbol=%s", raw_symbol)
            return DemoAutomationSymbolResult(
                symbol=raw_symbol,
                instrument_id=instrument_id,
                outcome="error",
                detail=self._safe_error(exc),
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

    def _risk_limits(self) -> RiskLimits:
        return RiskLimits(
            risk_per_trade_pct=D(str(self.settings.risk_per_trade_pct)),
            max_daily_loss_pct=D(str(self.settings.okx_demo_daily_loss_limit_pct)),
            max_weekly_loss_pct=D(str(self.settings.max_weekly_loss_pct)),
            max_drawdown_pct=D(str(self.settings.max_drawdown_pct)),
            max_consecutive_losses=self.settings.okx_demo_automation_max_consecutive_losses,
            max_open_positions=1,
            max_same_direction_positions=1,
            max_correlated_positions=1,
            max_notional=D(str(self.settings.order_size_cap_usdt)),
            minimum_score=self.settings.strategy_min_score,
            minimum_risk_reward=D(str(self.settings.strategy_min_risk_reward)),
        )

    def _roll_session(self, equity: Decimal, *, force_if_empty: bool = False) -> None:
        today = datetime.now(timezone.utc).date()
        if self._state["session_date"] != today and self._state["active_instrument_id"] is None:
            self._state["session_date"] = today
            self._state["baseline_equity"] = equity
            self._state["peak_equity"] = equity
            self._state["daily_pnl"] = D("0")
            self._state["trades_today"] = 0
        elif force_if_empty and self._state["baseline_equity"] is None:
            self._state["baseline_equity"] = equity
            self._state["peak_equity"] = equity
        baseline = self._state["baseline_equity"]
        if baseline is not None:
            self._state["daily_pnl"] = equity - baseline
        peak = self._state["peak_equity"]
        self._state["peak_equity"] = equity if peak is None else max(peak, equity)

    def _finalize_active_trade(self, snapshot: OkxDemoReconcileResult) -> None:
        if self._state["active_instrument_id"] is None:
            return
        if snapshot.positions or snapshot.pending_orders or snapshot.pending_algo_orders:
            return
        start_equity = self._state["active_start_equity"]
        pnl = D("0") if start_equity is None else snapshot.balance.total_equity - start_equity
        if pnl < 0:
            self._state["consecutive_losses"] = int(self._state["consecutive_losses"]) + 1
        else:
            self._state["consecutive_losses"] = 0
        self._state["active_instrument_id"] = None
        self._state["active_client_order_id"] = None
        self._state["active_start_equity"] = None
        self._state["active_started_at"] = None
        self._state["last_trade_closed_at"] = datetime.now(timezone.utc)

    def _apply_locks(self, equity: Decimal) -> None:
        reasons: list[str] = []
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
        self._state["lock_reasons"] = sorted(set(reasons))
        self._state["locked"] = bool(reasons)

    def _cooldown_active(self, now: datetime) -> bool:
        closed = self._state["last_trade_closed_at"]
        if closed is None:
            return False
        return now < closed + timedelta(seconds=self.settings.okx_demo_trade_cooldown_seconds)

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

    def _ensure_execute_ready(self) -> None:
        blockers = self._configuration_blockers()
        if blockers:
            raise DemoAutomationSafetyError(";".join(blockers))
        if not self._state["armed"]:
            raise DemoAutomationSafetyError("demo_automation_not_armed")
        if self._state["emergency_stop"]:
            raise DemoAutomationSafetyError("emergency_stop_engaged")
        if self._state["locked"]:
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
            reference_price=reference_price,
            stop_loss=candidate.stop_loss,
            take_profit=candidate.take_profit,
            risk_reward=candidate.risk_reward,
            approved_base_quantity=approved_base_quantity,
            approved_contracts=approved_contracts,
            client_order_id=client_order_id,
            exchange_order_id=exchange_order_id,
            reason_codes=reason_codes or [],
            detail=detail,
        )

    @staticmethod
    def _safe_error(exc: Exception) -> str:
        return f"{exc.__class__.__name__}: {exc}"[:250]
