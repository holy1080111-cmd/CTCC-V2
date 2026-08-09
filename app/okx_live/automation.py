from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_CEILING, ROUND_DOWN, ROUND_FLOOR
import hashlib
import re
from typing import Iterable

from app.config.settings import Settings, get_settings
from app.domain.okx_live import (
    LIVE_ORDER_PHRASE,
    OkxLiveAutomationRunResult,
    OkxLiveAutomationStatus,
    OkxLiveAutomationSymbolResult,
    OkxLiveOrderRequest,
)
from app.domain.realtime import RealtimeSnapshot
from app.domain.risk import AccountRiskState, RiskLimits
from app.domain.strategy import TradeCandidate
from app.exchange.okx.errors import OkxPrivateApiError, OkxPublicApiError
from app.exchange.okx.public_rest import OkxPublicRestClient
from app.exchange.okx.symbols import to_instrument_id
from app.okx_live import OkxLiveBusyError, OkxLiveSafetyError
from app.okx_live.service import OkxLiveService
from app.risk import RiskService
from app.strategies import StrategyService


D = Decimal


class ControlledLiveAutomation:
    """One protected production order per explicit, process-local arm."""

    def __init__(
        self,
        live_service: OkxLiveService,
        *,
        settings: Settings | None = None,
        strategy_service: StrategyService | None = None,
        risk_service: RiskService | None = None,
        public_client: OkxPublicRestClient | None = None,
        market_hub=None,
        market_client=None,
    ) -> None:
        self.settings = settings or get_settings()
        self.live_service = live_service
        self.strategy_service = strategy_service or StrategyService()
        self.risk_service = risk_service or RiskService()
        self.public_client = public_client or OkxPublicRestClient()
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
                from app.market.realtime_service import (
                    realtime_client,
                    realtime_hub,
                )
        self.market_hub = market_hub or realtime_hub
        self.market_client = market_client or realtime_client
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._run_lock = asyncio.Lock()
        self._next_run_at: datetime | None = None
        self._last_started_at: datetime | None = None
        self._last_completed_at: datetime | None = None
        self._last_error: str | None = None
        self._scheduled_symbols = list(self.settings.okx_live_scan_symbol_list)

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def status(self) -> OkxLiveAutomationStatus:
        arm = self.live_service.arm_status()
        return OkxLiveAutomationStatus(
            capability_enabled=self.settings.okx_live_auto_execution,
            running=self.running,
            armed=arm.armed,
            emergency_stop=arm.emergency_stop,
            symbols=list(self._scheduled_symbols),
            scan_interval_seconds=self.settings.okx_live_scan_interval_seconds,
            next_run_at=self._next_run_at,
            last_started_at=self._last_started_at,
            last_completed_at=self._last_completed_at,
            last_error=self._last_error,
        )

    async def start(
        self, *, symbols: Iterable[str] | None = None
    ) -> OkxLiveAutomationStatus:
        self._ensure_execute_ready()
        if self.running:
            return await self.status()
        selected = list(
            self.settings.okx_live_scan_symbol_list
            if symbols is None
            else symbols
        )
        if not selected:
            raise OkxLiveSafetyError("okx_live_scan_symbols_empty")
        for raw_symbol in selected:
            try:
                instrument_id = to_instrument_id(raw_symbol)
            except ValueError as exc:
                raise OkxLiveSafetyError("invalid_live_scan_symbol") from exc
            if instrument_id not in self.settings.okx_live_allowed_symbol_list:
                raise OkxLiveSafetyError(
                    "instrument_not_in_okx_live_allowlist"
                )
        self._scheduled_symbols = selected
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(
            self._loop(), name="controlled-okx-live-automation"
        )
        self.live_service.set_automation_running(True)
        return await self.status()

    async def stop(self) -> OkxLiveAutomationStatus:
        if self._task is not None:
            self._stop.set()
            if self._task is not asyncio.current_task():
                self._task.cancel()
                with suppress(asyncio.CancelledError):
                    await self._task
            self._task = None
        self._next_run_at = None
        self.live_service.set_automation_running(False)
        return await self.status()

    async def run_once(
        self,
        *,
        symbols: Iterable[str] | None = None,
        execute: bool = False,
        trigger: str = "manual",
    ) -> OkxLiveAutomationRunResult:
        if self._run_lock.locked():
            raise OkxLiveBusyError("okx_live_automation_run_in_progress")
        if execute:
            self._ensure_execute_ready()
        async with self._run_lock:
            started = datetime.now(timezone.utc)
            self._last_started_at = started
            results: list[OkxLiveAutomationSymbolResult] = []
            equity: Decimal | None = None
            try:
                snapshot = await self.live_service.reconcile()
                equity = snapshot.balance.total_equity
                if (
                    any(item.size != 0 for item in snapshot.positions)
                    or snapshot.pending_orders
                    or snapshot.pending_algo_orders
                ):
                    results.append(
                        OkxLiveAutomationSymbolResult(
                            symbol="*",
                            outcome="monitoring",
                            detail="live_exchange_exposure_blocks_new_automation_order",
                        )
                    )
                else:
                    requested = list(
                        symbols or self.settings.okx_live_scan_symbol_list
                    )
                    for raw_symbol in requested:
                        result = await self._process_symbol(
                            raw_symbol,
                            execute=execute,
                            equity=equity,
                        )
                        results.append(result)
                        if result.outcome == "submitted":
                            self._stop.set()
                            break
            except Exception as exc:
                self._last_error = self._safe_error(exc)
                results.append(
                    OkxLiveAutomationSymbolResult(
                        symbol="*",
                        outcome="error",
                        detail=self._last_error,
                    )
                )
            completed = datetime.now(timezone.utc)
            self._last_completed_at = completed
            return OkxLiveAutomationRunResult(
                trigger="scheduled" if trigger == "scheduled" else "manual",
                execute=execute,
                started_at=started,
                completed_at=completed,
                results=results,
                total_equity=equity,
            )

    async def _process_symbol(
        self,
        raw_symbol: str,
        *,
        execute: bool,
        equity: Decimal,
    ) -> OkxLiveAutomationSymbolResult:
        try:
            instrument_id = to_instrument_id(raw_symbol)
        except ValueError:
            return OkxLiveAutomationSymbolResult(
                symbol=raw_symbol,
                outcome="blocked",
                detail="invalid_live_scan_symbol",
            )
        if instrument_id not in self.settings.okx_live_allowed_symbol_list:
            return OkxLiveAutomationSymbolResult(
                symbol=raw_symbol,
                instrument_id=instrument_id,
                outcome="blocked",
                detail="instrument_not_in_okx_live_allowlist",
            )
        try:
            strategy = await self.strategy_service.evaluate(
                instrument_id,
                self.settings.okx_live_scan_candle_limit,
            )
            candidate = strategy.selected_candidate
            if candidate is None:
                return OkxLiveAutomationSymbolResult(
                    symbol=strategy.symbol,
                    instrument_id=instrument_id,
                    outcome="no_trade",
                    detail=";".join(strategy.blockers) or "no_strategy_candidate",
                )
            reference, error = await self._reference_price(
                instrument_id,
                candidate,
                require_realtime=execute,
            )
            if error:
                return self._result(
                    strategy.symbol,
                    instrument_id,
                    candidate,
                    outcome="blocked",
                    reference_price=reference,
                    detail=error,
                )
            candidate = self._candidate_at_reference(candidate, reference)
            if candidate is None:
                return OkxLiveAutomationSymbolResult(
                    symbol=strategy.symbol,
                    instrument_id=instrument_id,
                    outcome="blocked",
                    reference_price=reference,
                    detail="reference_price_outside_protective_bounds",
                )
            decision = self.risk_service.evaluate(
                candidate,
                AccountRiskState(
                    equity=equity,
                    peak_equity=equity,
                    open_positions=0,
                    same_direction_positions=0,
                    correlated_positions=0,
                ),
                self._risk_limits(),
            )
            if decision.decision != "approved":
                return self._result(
                    strategy.symbol,
                    instrument_id,
                    candidate,
                    outcome="risk_rejected",
                    reference_price=reference,
                    detail="live_risk_engine_rejected_candidate",
                    reason_codes=decision.reason_codes,
                )
            instruments = await self.public_client.instruments(instrument_id)
            if len(instruments) != 1 or instruments[0].state != "live":
                return self._result(
                    strategy.symbol,
                    instrument_id,
                    candidate,
                    outcome="blocked",
                    reference_price=reference,
                    detail="live_instrument_metadata_unavailable",
                )
            instrument = instruments[0]
            contracts, size_error = self._contracts_from_base_quantity(
                decision.approved_quantity,
                reference,
                instrument,
            )
            stop_loss, take_profit = self._align_protection(
                candidate, instrument.tick_size
            )
            aligned = candidate.model_copy(
                update={"stop_loss": stop_loss, "take_profit": take_profit}
            )
            if size_error:
                return self._result(
                    strategy.symbol,
                    instrument_id,
                    aligned,
                    outcome="blocked",
                    reference_price=reference,
                    approved_contracts=contracts,
                    detail=size_error,
                )
            if not execute:
                return self._result(
                    strategy.symbol,
                    instrument_id,
                    aligned,
                    outcome="approved_dry_run",
                    reference_price=reference,
                    approved_contracts=contracts,
                    detail="live_risk_approved_but_execution_not_requested",
                )
            client_order_id = "CTCCL" + self._fingerprint(
                instrument_id, aligned
            )[:27]
            write = await self.live_service.place_order(
                OkxLiveOrderRequest(
                    instrument_id=instrument_id,
                    direction=aligned.direction,
                    size=contracts,
                    margin_mode="cross",
                    leverage=self.settings.okx_live_automation_leverage,
                    order_type="market",
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    trigger_price_type="mark",
                    client_order_id=client_order_id,
                    confirmation=LIVE_ORDER_PHRASE,
                )
            )
            return self._result(
                strategy.symbol,
                instrument_id,
                aligned,
                outcome="submitted",
                reference_price=reference,
                approved_contracts=contracts,
                client_order_id=client_order_id,
                exchange_order_id=(
                    write.acknowledgement.order_id
                    if write.acknowledgement
                    else None
                ),
                detail="protected_okx_live_market_order_submitted",
            )
        except Exception as exc:
            return OkxLiveAutomationSymbolResult(
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
        snapshot: RealtimeSnapshot | None = await self.market_hub.snapshot(
            instrument_id
        )
        if snapshot is None or snapshot.last is None:
            return (
                candidate.entry,
                "realtime_snapshot_not_available" if require_realtime else None,
            )
        age = (datetime.now(timezone.utc) - snapshot.received_at).total_seconds()
        if age > self.settings.okx_live_scan_max_snapshot_age_seconds:
            return (
                snapshot.last,
                "realtime_snapshot_stale" if require_realtime else None,
            )
        drift = abs(snapshot.last - candidate.entry) / candidate.entry * D("10000")
        if drift > self.settings.okx_live_scan_max_entry_drift_bps:
            return snapshot.last, "entry_price_drift_exceeds_live_limit"
        return snapshot.last, None

    @staticmethod
    def _candidate_at_reference(
        candidate: TradeCandidate, reference: Decimal
    ) -> TradeCandidate | None:
        if candidate.direction == "long":
            if not candidate.stop_loss < reference < candidate.take_profit:
                return None
            risk = reference - candidate.stop_loss
            reward = candidate.take_profit - reference
        else:
            if not candidate.take_profit < reference < candidate.stop_loss:
                return None
            risk = candidate.stop_loss - reference
            reward = reference - candidate.take_profit
        if risk <= 0 or reward <= 0:
            return None
        return candidate.model_copy(
            update={"entry": reference, "risk_reward": reward / risk}
        )

    def _contracts_from_base_quantity(
        self, base_quantity: Decimal, reference: Decimal, instrument
    ) -> tuple[Decimal, str | None]:
        value = instrument.contract_value
        currency = (instrument.contract_currency or "").upper()
        if value is None or value <= 0 or instrument.lot_size <= 0:
            return D("0"), "instrument_contract_metadata_invalid"
        base, quote, _ = instrument.instrument_id.split("-", 2)
        if currency == base:
            raw = base_quantity / value
        elif currency == quote:
            raw = base_quantity * reference / value
        else:
            return D("0"), "unsupported_contract_value_currency"
        contracts = (
            (raw / instrument.lot_size).to_integral_value(rounding=ROUND_DOWN)
            * instrument.lot_size
        )
        contracts = min(
            contracts, self.settings.okx_live_max_order_size_contracts
        )
        if contracts < instrument.minimum_size or contracts <= 0:
            return contracts, "risk_sized_contracts_below_exchange_minimum"
        return contracts, None

    @staticmethod
    def _align_protection(
        candidate: TradeCandidate, tick: Decimal
    ) -> tuple[Decimal, Decimal]:
        if tick <= 0:
            raise OkxLiveSafetyError("live_instrument_tick_size_invalid")
        if candidate.direction == "long":
            stop_round, take_round = ROUND_FLOOR, ROUND_CEILING
        else:
            stop_round, take_round = ROUND_CEILING, ROUND_FLOOR
        stop = (candidate.stop_loss / tick).to_integral_value(
            rounding=stop_round
        ) * tick
        take = (candidate.take_profit / tick).to_integral_value(
            rounding=take_round
        ) * tick
        candidate.model_copy(update={"stop_loss": stop, "take_profit": take})
        return stop, take

    def _risk_limits(self) -> RiskLimits:
        return RiskLimits(
            risk_per_trade_pct=D(str(self.settings.risk_per_trade_pct)),
            max_daily_loss_pct=self.settings.okx_live_session_loss_limit_pct,
            max_weekly_loss_pct=D(str(self.settings.max_weekly_loss_pct)),
            max_drawdown_pct=D(str(self.settings.max_drawdown_pct)),
            max_consecutive_losses=1,
            max_open_positions=1,
            max_same_direction_positions=1,
            max_correlated_positions=1,
            max_notional=self.settings.okx_live_max_notional_usdt,
            minimum_score=self.settings.strategy_min_score,
            minimum_risk_reward=D(str(self.settings.strategy_min_risk_reward)),
        )

    def _ensure_execute_ready(self) -> None:
        if not self.settings.okx_live_auto_execution:
            raise OkxLiveSafetyError("okx_live_auto_execution_disabled")
        if not self.settings.okx_ws_enabled:
            raise OkxLiveSafetyError("okx_live_realtime_websocket_required")
        if not self.market_client.status().connected:
            raise OkxLiveSafetyError("okx_live_realtime_websocket_not_connected")
        arm = self.live_service.arm_status()
        if not arm.armed:
            raise OkxLiveSafetyError("okx_live_not_armed")
        if arm.emergency_stop:
            raise OkxLiveSafetyError("okx_live_emergency_stop_engaged")

    async def _loop(self) -> None:
        try:
            initial = self.settings.okx_live_scan_initial_delay_seconds
            if initial:
                self._next_run_at = datetime.now(timezone.utc) + timedelta(
                    seconds=initial
                )
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=initial)
                    return
                except TimeoutError:
                    pass
            while not self._stop.is_set():
                run = await self.run_once(
                    symbols=self._scheduled_symbols,
                    execute=True,
                    trigger="scheduled",
                )
                if any(item.outcome == "submitted" for item in run.results):
                    return
                if not self.live_service.arm_status().armed:
                    return
                interval = self.settings.okx_live_scan_interval_seconds
                self._next_run_at = datetime.now(timezone.utc) + timedelta(
                    seconds=interval
                )
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=interval)
                except TimeoutError:
                    continue
        finally:
            self._next_run_at = None
            self.live_service.set_automation_running(False)

    @staticmethod
    def _fingerprint(instrument_id: str, candidate: TradeCandidate) -> str:
        raw = "|".join(
            (
                instrument_id,
                candidate.strategy,
                candidate.direction,
                str(candidate.entry),
                str(candidate.stop_loss),
                str(candidate.take_profit),
                candidate.expires_at.isoformat(),
            )
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _result(
        symbol: str,
        instrument_id: str,
        candidate: TradeCandidate,
        *,
        outcome: str,
        reference_price: Decimal,
        detail: str,
        approved_contracts: Decimal | None = None,
        client_order_id: str | None = None,
        exchange_order_id: str | None = None,
        reason_codes: list[str] | None = None,
    ) -> OkxLiveAutomationSymbolResult:
        return OkxLiveAutomationSymbolResult(
            symbol=symbol,
            instrument_id=instrument_id,
            outcome=outcome,
            direction=candidate.direction,
            strategy=candidate.strategy,
            score=candidate.score,
            reference_price=reference_price,
            stop_loss=candidate.stop_loss,
            take_profit=candidate.take_profit,
            approved_contracts=approved_contracts,
            client_order_id=client_order_id,
            exchange_order_id=exchange_order_id,
            reason_codes=reason_codes or [],
            detail=detail,
        )

    @staticmethod
    def _safe_error(exc: Exception) -> str:
        if isinstance(exc, OkxPrivateApiError):
            code = str(getattr(exc, "code", "") or "")
            safe = code if re.fullmatch(r"[A-Za-z0-9_]{1,32}", code) else "unknown"
            return f"okx_live_private_api_error:{safe}"
        if isinstance(exc, OkxPublicApiError):
            code = str(getattr(exc, "code", "") or "")
            safe = code if re.fullmatch(r"[A-Za-z0-9_]{1,32}", code) else "unknown"
            return f"okx_live_public_api_error:{safe}"
        if isinstance(exc, OkxLiveSafetyError):
            return str(exc)[:250]
        return f"okx_live_automation_error:{exc.__class__.__name__}"[:250]
