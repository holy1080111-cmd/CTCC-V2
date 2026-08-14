from __future__ import annotations

import asyncio
from collections import deque
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import logging
from typing import Iterable

from app.config.settings import Settings, get_settings
from app.domain.orchestrator import (
    OrchestratorRunResult,
    OrchestratorStatus,
    OrchestratorSymbolResult,
)
from app.domain.paper import PaperOrderRequest, PaperStateView
from app.domain.realtime import RealtimeSnapshot
from app.domain.risk import AccountRiskState
from app.domain.strategy import TradeCandidate
from app.exchange.okx.symbols import to_instrument_id
from app.database.repositories.persistence import PersistenceRepository
from app.paper.engine import PaperBroker, PaperBrokerError
from app.paper.execution_service import PaperExecutionService
from app.risk import RiskService
from app.strategies import StrategyService

logger = logging.getLogger(__name__)
D = Decimal


class OrchestratorConfigurationError(ValueError):
    pass


class OrchestratorBusyError(RuntimeError):
    pass


class AutoPaperOrchestrator:
    """Connect strategy evaluation, risk approval, and paper execution.

    It never submits exchange orders. Automatic execution is a separate,
    explicit paper-only opt-in controlled by PAPER_AUTO_EXECUTION.
    """

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        strategy_service: StrategyService | None = None,
        risk_service: RiskService | None = None,
        broker: PaperBroker | None = None,
        paper_execution: PaperExecutionService | None = None,
        persistence_repository: PersistenceRepository | None = None,
        market_hub=None,
        market_client=None,
    ) -> None:
        self.settings = settings or get_settings()
        self.strategy_service = strategy_service or StrategyService()
        self.risk_service = risk_service or RiskService()
        if paper_execution is not None:
            self.paper = paper_execution
            self.persistence_repository = persistence_repository
        elif broker is not None:
            self.paper = PaperExecutionService(broker)
            self.persistence_repository = persistence_repository
        else:
            from app.paper.service import paper_service, persistence_repository as global_repository

            self.paper = paper_service
            self.persistence_repository = persistence_repository or global_repository
        if market_hub is None or market_client is None:
            from app.market.realtime_service import realtime_client, realtime_hub
        self.market_hub = market_hub or realtime_hub
        self.market_client = market_client or realtime_client

        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._run_lock = asyncio.Lock()
        self._history: deque[OrchestratorRunResult] = deque(
            maxlen=self.settings.paper_scan_history_limit
        )
        self._fingerprints: dict[str, datetime] = {}
        self._peak_equity = D(str(self.settings.paper_starting_balance))

        self._scan_count = 0
        self._submission_count = 0
        self._skipped_count = 0
        self._error_count = 0
        self._last_started_at: datetime | None = None
        self._last_completed_at: datetime | None = None
        self._next_run_at: datetime | None = None
        self._last_error: str | None = None
        self._recovered = False

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def recover(self) -> None:
        """Restore run history and candidate fingerprints before scheduling."""
        if self.persistence_repository is None:
            self._recovered = True
            return
        now = datetime.now(timezone.utc)
        recovery_limit = min(
            self.settings.paper_scan_history_limit,
            self.settings.paper_recovery_history_limit,
        )
        history = await self.persistence_repository.load_orchestrator_runs(recovery_limit)
        self._history = deque(history, maxlen=self.settings.paper_scan_history_limit)
        self._fingerprints = await self.persistence_repository.load_fingerprints(now)
        self._scan_count = len(history)
        self._submission_count = sum(
            1 for run in history for result in run.results if result.outcome == "submitted"
        )
        self._error_count = sum(
            1 for run in history for result in run.results if result.outcome == "error"
        )
        self._skipped_count = sum(
            1
            for run in history
            for result in run.results
            if result.outcome not in {"submitted", "error"}
        )
        if history:
            self._last_started_at = history[-1].started_at
            self._last_completed_at = history[-1].completed_at
        self._peak_equity = max(self._peak_equity, self.paper.account().equity)
        self._recovered = True

    def _validate_auto_execution_configuration(self) -> None:
        if not self.settings.paper_auto_execution:
            raise OrchestratorConfigurationError("paper_auto_execution_disabled")
        if self.settings.trading_mode != "paper":
            raise OrchestratorConfigurationError("paper_mode_required")
        if not self.settings.okx_ws_enabled:
            raise OrchestratorConfigurationError("okx_websocket_required")
        if not self.settings.paper_auto_ticks:
            raise OrchestratorConfigurationError("paper_auto_ticks_required")
        if self.settings.auto_trade or self.settings.live_trading:
            raise OrchestratorConfigurationError("exchange_auto_trade_must_remain_disabled")

    async def start(self) -> OrchestratorStatus:
        self._validate_auto_execution_configuration()
        if self.persistence_repository is not None and not self._recovered:
            raise OrchestratorConfigurationError("orchestrator_recovery_not_completed")
        if self.running:
            return await self.status()
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(self._loop(), name="auto-paper-orchestrator")
        return await self.status()

    async def stop(self) -> OrchestratorStatus:
        self._stop.set()
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        self._next_run_at = None
        return await self.status()

    async def status(self) -> OrchestratorStatus:
        return OrchestratorStatus(
            enabled=self.settings.paper_auto_execution,
            running=self.running,
            busy=self._run_lock.locked(),
            trading_mode=self.settings.trading_mode,
            interval_seconds=self.settings.paper_scan_interval_seconds,
            symbols=self.settings.paper_scan_symbol_list,
            scan_count=self._scan_count,
            submission_count=self._submission_count,
            skipped_count=self._skipped_count,
            error_count=self._error_count,
            last_started_at=self._last_started_at,
            last_completed_at=self._last_completed_at,
            next_run_at=self._next_run_at,
            last_error=self._last_error,
            last_run=self._history[-1] if self._history else None,
        )

    async def history(self, limit: int = 20) -> list[OrchestratorRunResult]:
        bounded = max(1, min(limit, self.settings.paper_scan_history_limit))
        return list(self._history)[-bounded:][::-1]

    async def clear_history(self) -> OrchestratorStatus:
        self._history.clear()
        if self.persistence_repository is not None:
            await self.persistence_repository.clear_orchestrator_runs()
        return await self.status()

    async def run_once(
        self,
        *,
        symbols: Iterable[str] | None = None,
        execute: bool = False,
        trigger: str = "manual",
    ) -> OrchestratorRunResult:
        if self._run_lock.locked():
            raise OrchestratorBusyError("orchestrator_run_in_progress")
        if execute:
            self._validate_auto_execution_configuration()

        async with self._run_lock:
            started = datetime.now(timezone.utc)
            self._last_started_at = started
            self._last_error = None
            await self._cleanup_fingerprints(started)

            selected_symbols = list(symbols or self.settings.paper_scan_symbol_list)
            results: list[OrchestratorSymbolResult] = []
            for raw_symbol in selected_symbols:
                result = await self._process_symbol(str(raw_symbol), execute=execute)
                results.append(result)
                if result.outcome == "submitted":
                    self._submission_count += 1
                elif result.outcome == "error":
                    self._error_count += 1
                else:
                    self._skipped_count += 1

            completed = datetime.now(timezone.utc)
            run = OrchestratorRunResult(
                trigger="scheduled" if trigger == "scheduled" else "manual",
                execute=execute,
                started_at=started,
                completed_at=completed,
                results=results,
                account_after=self.paper.account(),
            )
            self._scan_count += 1
            self._last_completed_at = completed
            self._history.append(run)
            if self.persistence_repository is not None:
                await self.persistence_repository.save_orchestrator_run(
                    run,
                    history_limit=self.settings.paper_scan_history_limit,
                )
            return run

    async def _process_symbol(self, raw_symbol: str, *, execute: bool) -> OrchestratorSymbolResult:
        try:
            instrument_id = to_instrument_id(raw_symbol)
        except ValueError as exc:
            return OrchestratorSymbolResult(
                symbol=raw_symbol,
                outcome="blocked",
                detail=str(exc),
            )

        try:
            existing_reason = self._existing_exposure_reason(instrument_id)
            if existing_reason is not None:
                return OrchestratorSymbolResult(
                    symbol=raw_symbol,
                    instrument_id=instrument_id,
                    outcome="duplicate",
                    detail=existing_reason,
                )

            strategy = await self.strategy_service.evaluate(
                instrument_id,
                self.settings.paper_scan_candle_limit,
            )
            candidate = strategy.selected_candidate
            if candidate is None:
                return OrchestratorSymbolResult(
                    symbol=strategy.symbol,
                    instrument_id=instrument_id,
                    outcome="no_trade",
                    strategy_decision=strategy.decision,
                    selected_strategy=strategy.selected_strategy,
                    detail=";".join(strategy.blockers) or "no_strategy_candidate",
                )

            reference_price, realtime_error = await self._reference_price(
                instrument_id,
                candidate,
                require_realtime=execute,
            )
            if realtime_error is not None:
                return OrchestratorSymbolResult(
                    symbol=strategy.symbol,
                    instrument_id=instrument_id,
                    outcome="blocked",
                    strategy_decision=strategy.decision,
                    selected_strategy=strategy.selected_strategy,
                    score=candidate.score,
                    candidate_entry=candidate.entry,
                    reference_price=reference_price,
                    detail=realtime_error,
                )

            execution_candidate = self._candidate_at_reference(candidate, reference_price)
            if execution_candidate is None:
                return OrchestratorSymbolResult(
                    symbol=strategy.symbol,
                    instrument_id=instrument_id,
                    outcome="blocked",
                    strategy_decision=strategy.decision,
                    selected_strategy=strategy.selected_strategy,
                    score=candidate.score,
                    candidate_entry=candidate.entry,
                    reference_price=reference_price,
                    detail="reference_price_outside_protective_bounds",
                )

            fingerprint = self._fingerprint(instrument_id, execution_candidate)
            if fingerprint in self._fingerprints:
                return OrchestratorSymbolResult(
                    symbol=strategy.symbol,
                    instrument_id=instrument_id,
                    outcome="duplicate",
                    strategy_decision=strategy.decision,
                    selected_strategy=strategy.selected_strategy,
                    score=candidate.score,
                    candidate_entry=candidate.entry,
                    reference_price=reference_price,
                    detail="candidate_fingerprint_already_processed",
                )

            account_state = self._account_risk_state(instrument_id, execution_candidate.direction)
            risk = self.risk_service.evaluate(execution_candidate, account_state)
            if risk.decision != "approved":
                return OrchestratorSymbolResult(
                    symbol=strategy.symbol,
                    instrument_id=instrument_id,
                    outcome="risk_rejected",
                    strategy_decision=strategy.decision,
                    selected_strategy=strategy.selected_strategy,
                    score=candidate.score,
                    candidate_entry=candidate.entry,
                    reference_price=reference_price,
                    risk_decision=risk.decision,
                    risk_reason_codes=risk.reason_codes,
                    approved_quantity=risk.approved_quantity,
                    detail="risk_engine_rejected_candidate",
                )

            if not execute:
                return OrchestratorSymbolResult(
                    symbol=strategy.symbol,
                    instrument_id=instrument_id,
                    outcome="approved_dry_run",
                    strategy_decision=strategy.decision,
                    selected_strategy=strategy.selected_strategy,
                    score=candidate.score,
                    candidate_entry=candidate.entry,
                    reference_price=reference_price,
                    risk_decision=risk.decision,
                    approved_quantity=risk.approved_quantity,
                    detail="risk_approved_but_execution_disabled_for_this_run",
                )

            client_order_id = f"auto{fingerprint[:32]}"
            order = await self.paper.submit(
                PaperOrderRequest(
                    symbol=instrument_id,
                    side=execution_candidate.direction,
                    quantity=risk.approved_quantity,
                    reference_price=reference_price,
                    stop_loss=execution_candidate.stop_loss,
                    take_profit=execution_candidate.take_profit,
                    order_type="market",
                    risk_decision="approved",
                    strategy=execution_candidate.strategy,
                    score=execution_candidate.score,
                    reasons=list(execution_candidate.reasons),
                    client_order_id=client_order_id,
                )
            )
            hold_until = max(
                execution_candidate.expires_at,
                datetime.now(timezone.utc)
                + timedelta(seconds=self.settings.paper_scan_cooldown_seconds),
            )
            self._fingerprints[fingerprint] = hold_until
            if self.persistence_repository is not None:
                await self.persistence_repository.save_fingerprint(
                    fingerprint,
                    hold_until,
                    details={
                        "instrument_id": instrument_id,
                        "strategy": execution_candidate.strategy,
                        "client_order_id": order.client_order_id,
                    },
                )
            return OrchestratorSymbolResult(
                symbol=strategy.symbol,
                instrument_id=instrument_id,
                outcome="submitted",
                strategy_decision=strategy.decision,
                selected_strategy=strategy.selected_strategy,
                score=candidate.score,
                candidate_entry=candidate.entry,
                reference_price=reference_price,
                risk_decision=risk.decision,
                approved_quantity=risk.approved_quantity,
                order_id=order.id,
                client_order_id=order.client_order_id,
                detail="paper_market_order_submitted",
            )
        except PaperBrokerError as exc:
            return OrchestratorSymbolResult(
                symbol=raw_symbol,
                instrument_id=instrument_id,
                outcome="blocked",
                detail=f"paper_broker:{exc}",
            )
        except Exception as exc:  # Per-symbol containment: one failure must not stop the whole scan.
            self._last_error = f"{exc.__class__.__name__}: {exc}"
            logger.exception("orchestrator_symbol_error symbol=%s", raw_symbol)
            return OrchestratorSymbolResult(
                symbol=raw_symbol,
                instrument_id=instrument_id,
                outcome="error",
                detail=self._last_error,
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

        observed_at = snapshot.last_received_at or snapshot.received_at
        age = (datetime.now(timezone.utc) - observed_at).total_seconds()
        if age > self.settings.paper_scan_max_snapshot_age_seconds:
            return snapshot.last, "realtime_snapshot_stale" if require_realtime else None

        drift_bps = abs(snapshot.last - candidate.entry) / candidate.entry * D("10000")
        if drift_bps > D(str(self.settings.paper_scan_max_entry_drift_bps)):
            return snapshot.last, "entry_price_drift_exceeds_limit"
        return snapshot.last, None

    @staticmethod
    def _candidate_at_reference(
        candidate: TradeCandidate,
        reference_price: Decimal,
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
            update={
                "entry": reference_price,
                "risk_reward": reward / risk,
            }
        )

    def _existing_exposure_reason(self, instrument_id: str) -> str | None:
        state = self.paper.state()
        if any(p.symbol == instrument_id and p.status == "open" for p in state.positions):
            return "open_position_already_exists_for_symbol"
        if any(o.symbol == instrument_id and o.status == "pending" for o in state.orders):
            return "pending_order_already_exists_for_symbol"
        return None

    def _account_risk_state(self, instrument_id: str, direction: str) -> AccountRiskState:
        state: PaperStateView = self.paper.state()
        account = state.account
        now = datetime.now(timezone.utc)
        week_start = (now - timedelta(days=now.weekday())).date()
        closed = sorted(
            (p for p in state.positions if p.status == "closed" and p.closed_at is not None),
            key=lambda p: p.closed_at or now,
        )
        daily = sum(
            (p.realized_pnl for p in closed if p.closed_at and p.closed_at.date() == now.date()),
            D("0"),
        )
        weekly = sum(
            (p.realized_pnl for p in closed if p.closed_at and p.closed_at.date() >= week_start),
            D("0"),
        )
        consecutive_losses = 0
        for position in reversed(closed):
            if position.realized_pnl < 0:
                consecutive_losses += 1
            else:
                break

        open_positions = [p for p in state.positions if p.status == "open"]
        same_direction = sum(1 for p in open_positions if p.side == direction)
        configured = set(self.settings.paper_scan_symbol_list)
        correlated = sum(
            1 for p in open_positions
            if p.symbol != instrument_id and p.symbol in configured
        )
        self._peak_equity = max(self._peak_equity, account.equity)
        return AccountRiskState(
            equity=account.equity,
            daily_realized_pnl=daily,
            weekly_realized_pnl=weekly,
            peak_equity=self._peak_equity,
            consecutive_losses=consecutive_losses,
            open_positions=len(open_positions),
            same_direction_positions=same_direction,
            correlated_positions=correlated,
        )

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

    async def _cleanup_fingerprints(self, now: datetime) -> None:
        self._fingerprints = {
            key: expiry for key, expiry in self._fingerprints.items() if expiry > now
        }
        if self.persistence_repository is not None:
            await self.persistence_repository.delete_expired_fingerprints(now)

    async def _loop(self) -> None:
        try:
            initial = self.settings.paper_scan_initial_delay_seconds
            if initial > 0:
                self._next_run_at = datetime.now(timezone.utc) + timedelta(seconds=initial)
                await asyncio.wait_for(self._stop.wait(), timeout=initial)
                return
        except TimeoutError:
            pass

        while not self._stop.is_set():
            try:
                await self.run_once(execute=True, trigger="scheduled")
            except OrchestratorBusyError:
                logger.warning("orchestrator_scheduled_run_skipped reason=busy")
            except Exception as exc:
                self._last_error = f"{exc.__class__.__name__}: {exc}"
                self._error_count += 1
                logger.exception("orchestrator_scheduled_run_failed")

            interval = self.settings.paper_scan_interval_seconds
            self._next_run_at = datetime.now(timezone.utc) + timedelta(seconds=interval)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=interval)
            except TimeoutError:
                continue
            break
