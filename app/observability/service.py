from __future__ import annotations

import asyncio
from collections import deque
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import logging
from typing import Any

from app.config.settings import Settings, get_settings
from app.database.repositories.observability import DemoObservabilityRepository
from app.demo_automation.service import SafeDemoAutomation
from app.domain.demo_automation import DemoAutomationRunResult
from app.domain.observability import (
    DemoExecutionSoakPreflight,
    DemoObservabilityEventView,
    DemoObservabilityMetrics,
    DemoObservabilitySummary,
    DemoSoakSessionView,
    DemoSoakStartRequest,
)
from app.okx_demo.equity import DemoRiskCapital, resolve_demo_risk_capital

logger = logging.getLogger(__name__)
D = Decimal


class DemoObservabilityError(RuntimeError):
    pass


class DemoObservabilityService:
    """Watchdog and durable controlled OKX Demo soak-test controller.

    Observation sessions never execute. Execute sessions require a separately
    enabled and armed Demo automation, a flat exchange account at start,
    protected exposure, a session loss budget, and automatic disarming on every
    exit path. This service never enables writes, never arms automatically, and
    never closes an exchange position implicitly.
    """

    def __init__(
        self,
        *,
        automation: SafeDemoAutomation,
        settings: Settings | None = None,
        repository: DemoObservabilityRepository | None = None,
        realtime_client=None,
        demo_service=None,
    ) -> None:
        self.automation = automation
        self.settings = settings or get_settings()
        self.repository = repository
        if repository is None and self.settings.environment != "test":
            from app.database.session import AsyncSessionFactory

            self.repository = DemoObservabilityRepository(AsyncSessionFactory)

        if realtime_client is None:
            if self.settings.environment == "test":
                class _NullRealtime:
                    class _Status:
                        enabled = False
                        running = False
                        connected = False
                        connection_count = 0
                        reconnect_count = 0
                        message_count = 0
                        parse_error_count = 0
                        last_message_at = None

                    def status(self):
                        return self._Status()

                realtime_client = _NullRealtime()
            else:
                from app.market.realtime_service import realtime_client as runtime_client

                realtime_client = runtime_client
        self.realtime_client = realtime_client
        self.demo_service = demo_service or getattr(automation, "demo_service", None)

        self._process_started_at = datetime.now(timezone.utc)
        self._last_heartbeat_at = self._process_started_at
        self._recovered = False
        self._monitor_task: asyncio.Task[None] | None = None
        self._monitor_stop = asyncio.Event()
        self._soak_task: asyncio.Task[None] | None = None
        self._soak_stop = asyncio.Event()
        self._soak = DemoSoakSessionView()
        self._events: deque[DemoObservabilityEventView] = deque(
            maxlen=self.settings.okx_demo_observability_event_limit
        )
        self._active_alert_codes: set[str] = set()
        self._last_parse_error_count = 0
        self._last_parse_error_at: datetime | None = None
        self._runtime_exchange_grace_started_at: datetime | None = None
        self._runtime_exchange_grace_symbols: frozenset[str] = frozenset()
        self._lock = asyncio.Lock()

    @property
    def monitoring(self) -> bool:
        return self._monitor_task is not None and not self._monitor_task.done()

    @property
    def soak_running(self) -> bool:
        return self._soak_task is not None and not self._soak_task.done()

    async def recover(self) -> None:
        if self.repository is not None:
            interrupted = await self.repository.interrupt_running_sessions()
            latest = await self.repository.latest_session()
            if latest is not None:
                self._soak = latest
                if latest.execute and latest.state == "interrupted":
                    self._soak.auto_disarmed = True
                    await self.repository.update_session(self._soak)
            self._events = deque(
                await self.repository.events(self.settings.okx_demo_observability_event_limit),
                maxlen=self.settings.okx_demo_observability_event_limit,
            )
            if interrupted:
                await self._emit(
                    "warning",
                    "soak_session_interrupted_by_restart",
                    "A running Demo soak session was marked interrupted after API restart.",
                    {"interrupted_sessions": interrupted},
                )
        elif self._soak.state == "running":
            self._soak.state = "interrupted"
            self._soak.stopped_at = datetime.now(timezone.utc)
            self._soak.stop_reason = "api_process_restarted"
            self._soak.auto_disarmed = bool(self._soak.execute)

        realtime = self.realtime_client.status()
        self._last_parse_error_count = int(realtime.parse_error_count)
        self._recovered = True
        self._last_heartbeat_at = datetime.now(timezone.utc)

    async def start_monitoring(self) -> None:
        if not self.settings.okx_demo_observability_enabled or self.monitoring:
            return
        self._monitor_stop = asyncio.Event()
        self._monitor_task = asyncio.create_task(
            self._monitor_loop(), name="demo-observability-watchdog"
        )

    async def shutdown(self) -> None:
        await self.stop_soak(reason="api_shutdown")
        self._monitor_stop.set()
        task, self._monitor_task = self._monitor_task, None
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    async def soak_status(self) -> DemoSoakSessionView:
        return self._soak.model_copy(deep=True)

    async def execute_preflight(self) -> DemoExecutionSoakPreflight:
        automation = await self.automation.status()
        blockers: list[str] = []
        snapshot = None

        if not self.settings.okx_demo_soak_enabled:
            blockers.append("demo_soak_disabled")
        if not self.settings.okx_demo_soak_allow_execute:
            blockers.append("execute_soak_disabled")
        if self.soak_running:
            blockers.append("demo_soak_already_running")
        blockers.extend(automation.configuration_blockers)
        if not automation.armed:
            blockers.append("execute_soak_requires_armed_automation")
        if automation.emergency_stop:
            blockers.append("automation_emergency_stop_active")
        if automation.locked:
            blockers.append("automation_safety_lock_active")

        if self.demo_service is None:
            blockers.append("okx_demo_service_unavailable")
        else:
            try:
                snapshot = await self.demo_service.reconcile()
            except Exception as exc:
                blockers.append("okx_demo_reconcile_failed:" + self._safe_error(exc))

        position_count = len(snapshot.positions) if snapshot is not None else 0
        pending_count = len(snapshot.pending_orders) if snapshot is not None else 0
        algo_count = len(snapshot.pending_algo_orders) if snapshot is not None else 0
        total_equity = snapshot.balance.total_equity if snapshot is not None else None
        risk_capital: DemoRiskCapital | None = None
        if snapshot is not None:
            risk_capital, capital_blocker = resolve_demo_risk_capital(
                snapshot.account_config,
                snapshot.balance,
            )
            if risk_capital is None:
                blockers.append(capital_blocker)

        if (
            snapshot is not None
            and self.settings.okx_demo_execution_soak_require_flat_start
            and (position_count or pending_count or algo_count)
        ):
            blockers.append("exchange_exposure_must_be_zero_before_execute_soak")
        if risk_capital is not None and risk_capital.risk_equity <= 0:
            blockers.append("demo_equity_must_be_positive")

        blockers = sorted(set(blockers))
        return DemoExecutionSoakPreflight(
            ready=not blockers,
            blockers=blockers,
            execute_soak_enabled=self.settings.okx_demo_soak_allow_execute,
            demo_writes_enabled=self.settings.okx_demo_allow_order_writes,
            automation_capability_enabled=self.settings.okx_demo_auto_execution,
            automation_armed=automation.armed,
            automation_locked=automation.locked,
            automation_emergency_stop=automation.emergency_stop,
            automation_configuration_blockers=list(automation.configuration_blockers),
            exchange_position_count=position_count,
            exchange_pending_order_count=pending_count,
            exchange_algo_order_count=algo_count,
            total_equity=total_equity,
            risk_equity=(
                risk_capital.risk_equity if risk_capital is not None else None
            ),
            equity_basis=(risk_capital.basis if risk_capital is not None else None),
            equity_currency=(
                risk_capital.currency if risk_capital is not None else None
            ),
            require_flat_start=self.settings.okx_demo_execution_soak_require_flat_start,
            require_protection=self.settings.okx_demo_execution_soak_require_protection,
            auto_disarm=self.settings.okx_demo_execution_soak_auto_disarm,
            max_submissions=self.settings.okx_demo_execution_soak_max_submissions,
            loss_limit_pct=D(str(self.settings.okx_demo_execution_soak_loss_limit_pct)),
        )

    async def start_soak(self, request: DemoSoakStartRequest) -> DemoSoakSessionView:
        async with self._lock:
            if not self.settings.okx_demo_soak_enabled:
                raise DemoObservabilityError("demo_soak_disabled")
            if self.soak_running:
                raise DemoObservabilityError("demo_soak_already_running")

            duration = request.duration_minutes or self.settings.okx_demo_soak_default_duration_minutes
            if duration > self.settings.okx_demo_soak_max_duration_minutes:
                raise DemoObservabilityError("demo_soak_duration_exceeds_configured_maximum")
            interval = request.interval_seconds or self.settings.okx_demo_soak_interval_seconds
            if request.execute and interval < 60:
                raise DemoObservabilityError("execute_soak_interval_must_be_at_least_60_seconds")
            max_runs = request.max_runs or self.settings.okx_demo_soak_max_runs
            symbols = list(request.symbols or self.settings.okx_demo_scan_symbol_list)
            if not symbols:
                raise DemoObservabilityError("demo_soak_symbols_empty")

            starting_equity = None
            equity_basis = None
            equity_currency = None
            if request.execute:
                preflight = await self.execute_preflight()
                if not preflight.ready:
                    raise DemoObservabilityError(
                        "execute_soak_preflight_failed:" + ",".join(preflight.blockers)
                    )
                starting_equity = preflight.risk_equity
                equity_basis = preflight.equity_basis
                equity_currency = preflight.equity_currency

            now = datetime.now(timezone.utc)
            session = DemoSoakSessionView(
                state="running",
                execute=request.execute,
                symbols=symbols,
                interval_seconds=interval,
                duration_minutes=duration,
                max_runs=max_runs,
                max_submissions=(
                    self.settings.okx_demo_execution_soak_max_submissions
                    if request.execute
                    else 0
                ),
                started_at=now,
                planned_end_at=now + timedelta(minutes=duration),
                equity_basis=equity_basis,
                equity_currency=equity_currency,
                starting_equity=starting_equity,
                latest_equity=starting_equity,
                protection_verified=True if request.execute else None,
            )
            if self.repository is not None:
                session = await self.repository.create_session(session)
            self._soak = session
            self._soak_stop = asyncio.Event()
            self._soak_task = asyncio.create_task(
                self._soak_loop(), name="demo-soak-session"
            )
            await self._emit(
                "info",
                "soak_session_started",
                "Demo soak session started.",
                {
                    "execute": request.execute,
                    "duration_minutes": duration,
                    "interval_seconds": interval,
                    "max_runs": max_runs,
                    "max_submissions": self._soak.max_submissions,
                    "starting_equity": str(starting_equity) if starting_equity is not None else None,
                    "equity_basis": equity_basis,
                    "equity_currency": equity_currency,
                    "symbols": symbols,
                },
            )
            return self._soak.model_copy(deep=True)

    async def stop_soak(self, *, reason: str = "operator_stop") -> DemoSoakSessionView:
        self._soak_stop.set()
        task = self._soak_task
        if task is not None and task is not asyncio.current_task():
            with suppress(asyncio.CancelledError):
                await task
        if self._soak.state == "running":
            await self._finish_soak("stopped", reason)
        self._soak_task = None
        return self._soak.model_copy(deep=True)

    async def events(self, limit: int = 50) -> list[DemoObservabilityEventView]:
        if self.repository is not None:
            return await self.repository.events(limit)
        return list(self._events)[:limit]

    async def metrics(self, window_hours: int = 24) -> DemoObservabilityMetrics:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)
        if self.repository is not None:
            selected = await self.repository.automation_runs_since(
                cutoff, limit=self.settings.okx_demo_observability_metrics_run_limit
            )
        else:
            runs = await self.automation.history(self.settings.okx_demo_automation_history_limit)
            selected = [run for run in runs if run.completed_at >= cutoff]
        metrics = DemoObservabilityMetrics(window_hours=window_hours)
        metrics.total_runs = len(selected)
        metrics.manual_runs = sum(run.trigger == "manual" for run in selected)
        metrics.scheduled_runs = sum(run.trigger == "scheduled" for run in selected)
        metrics.execute_runs = sum(run.execute for run in selected)
        metrics.dry_runs = sum(not run.execute for run in selected)
        if selected:
            metrics.last_run_at = max(run.completed_at for run in selected)
        for run in selected:
            outcomes = {result.outcome for result in run.results}
            for outcome in outcomes:
                if hasattr(metrics, outcome):
                    setattr(metrics, outcome, getattr(metrics, outcome) + 1)
            if "error" in outcomes:
                metrics.errors += 1
        return metrics

    async def summary(self, window_hours: int = 24) -> DemoObservabilitySummary:
        now = datetime.now(timezone.utc)
        realtime = self.realtime_client.status()
        automation = await self.automation.status()
        metrics = await self.metrics(window_hours)
        self._update_runtime_counters(now, realtime)
        alerts = self._current_alerts(now, realtime, automation)
        severity = {event.severity for event in alerts}
        status = "critical" if "critical" in severity else "degraded" if alerts else "healthy"
        return DemoObservabilitySummary(
            status=status,
            process_started_at=self._process_started_at,
            uptime_seconds=max(0, int((now - self._process_started_at).total_seconds())),
            recovered=self._recovered,
            watchdog_running=self.monitoring,
            last_heartbeat_at=self._last_heartbeat_at,
            websocket_enabled=bool(realtime.enabled),
            websocket_running=bool(realtime.running),
            websocket_connected=bool(realtime.connected),
            websocket_connection_count=int(getattr(realtime, "connection_count", 0)),
            websocket_reconnect_count=int(getattr(realtime, "reconnect_count", 0)),
            websocket_message_count=int(realtime.message_count),
            websocket_parse_error_count=int(realtime.parse_error_count),
            websocket_last_message_at=realtime.last_message_at,
            automation_armed=automation.armed,
            automation_running=automation.running,
            automation_locked=automation.locked,
            automation_emergency_stop=automation.emergency_stop,
            automation_last_completed_at=automation.last_completed_at,
            soak=self._soak.model_copy(deep=True),
            metrics=metrics,
            alerts=alerts,
        )

    async def _soak_loop(self) -> None:
        try:
            while not self._soak_stop.is_set():
                now = datetime.now(timezone.utc)
                if self._soak.planned_end_at is not None and now >= self._soak.planned_end_at:
                    await self._finish_for_limit("duration_reached")
                    return
                if self._soak.completed_runs >= self._soak.max_runs:
                    await self._finish_for_limit("max_runs_reached")
                    return

                should_stop = await self._execute_soak_run()
                if should_stop:
                    return
                if self._soak.completed_runs >= self._soak.max_runs:
                    await self._finish_for_limit("max_runs_reached")
                    return

                if await self._wait_with_heartbeat(self._soak.interval_seconds):
                    await self._finish_soak("stopped", "operator_stop")
                    return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("demo_soak_loop_failed")
            self._soak.last_error = self._safe_error(exc)
            if self._soak.execute:
                await self._safety_stop(
                    "unhandled_execute_soak_error",
                    {"error": self._soak.last_error},
                )
            else:
                await self._finish_soak("error", "unhandled_soak_error")
                await self._emit(
                    "critical",
                    "soak_loop_failed",
                    "Demo soak session stopped because of an unhandled error.",
                    {"error": self._soak.last_error},
                )
        finally:
            self._soak_task = None

    async def _execute_soak_run(self) -> bool:
        now = datetime.now(timezone.utc)
        try:
            if self._soak.execute:
                if await self._refresh_execution_safety(stage="before_run"):
                    return True
                if (
                    self._soak.submitted_runs >= self._soak.max_submissions
                    and not self._has_exchange_exposure()
                ):
                    await self._finish_soak("completed", "submission_limit_reached")
                    return True

            submission_limit = (
                max(
                    0,
                    self._soak.max_submissions - self._soak.submitted_runs,
                )
                if self._soak.execute
                else None
            )
            run = await self.automation.run_once(
                symbols=self._soak.symbols,
                execute=self._soak.execute,
                trigger="scheduled",
                submission_limit=submission_limit,
            )
            actual_submissions = sum(
                result.outcome == "submitted" for result in run.results
            )
            self._apply_run(run)
            await self._persist_soak()

            if self._soak.execute:
                if actual_submissions > (submission_limit or 0):
                    await self._safety_stop(
                        "submission_limit_exceeded",
                        {
                            "allowed": submission_limit,
                            "actual": actual_submissions,
                        },
                    )
                    return True
                submitted = actual_submissions > 0
                if await self._refresh_execution_safety(
                    stage="after_run", poll_for_protection=submitted
                ):
                    return True
                if (
                    self._soak.submitted_runs >= self._soak.max_submissions
                    and not self._has_exchange_exposure()
                ):
                    await self._finish_soak("completed", "submission_limit_reached")
                    return True

            if self._soak.consecutive_errors >= self.settings.okx_demo_observability_error_threshold:
                if self._soak.execute:
                    await self._safety_stop(
                        "consecutive_error_threshold",
                        {
                            "consecutive_errors": self._soak.consecutive_errors,
                            "threshold": self.settings.okx_demo_observability_error_threshold,
                        },
                    )
                else:
                    await self._emit(
                        "critical",
                        "soak_consecutive_error_threshold",
                        "Demo soak session reached the consecutive error threshold.",
                        {
                            "consecutive_errors": self._soak.consecutive_errors,
                            "threshold": self.settings.okx_demo_observability_error_threshold,
                        },
                    )
                    await self._finish_soak("error", "consecutive_error_threshold")
                return True
            return False
        except Exception as exc:
            self._soak.completed_runs += 1
            self._soak.error_runs += 1
            self._soak.consecutive_errors += 1
            self._soak.last_run_at = now
            self._soak.last_outcome = "exception"
            self._soak.last_error = self._safe_error(exc)
            await self._persist_soak()
            await self._emit(
                "warning",
                "soak_run_exception",
                "A Demo soak run raised an exception.",
                {"error": self._soak.last_error, "execute": self._soak.execute},
            )
            if self._soak.execute:
                await self._safety_stop(
                    "execute_soak_run_failed", {"error": self._soak.last_error}
                )
                return True
            return False

    async def _refresh_execution_safety(
        self, *, stage: str, poll_for_protection: bool = False
    ) -> bool:
        if self.demo_service is None:
            return await self._safety_stop(
                "okx_demo_service_unavailable", {"stage": stage}
            )

        snapshot = await self.demo_service.reconcile()
        self._update_execution_exposure(snapshot)
        capital, capital_blocker = resolve_demo_risk_capital(
            snapshot.account_config,
            snapshot.balance,
        )
        if capital is None:
            return await self._safety_stop(
                "demo_risk_capital_unavailable",
                {"stage": stage, "blocker": capital_blocker},
            )
        if (
            self._soak.equity_basis is not None
            and capital.basis != self._soak.equity_basis
        ):
            return await self._safety_stop(
                "execution_soak_equity_basis_changed",
                {
                    "stage": stage,
                    "expected_basis": self._soak.equity_basis,
                    "actual_basis": capital.basis,
                },
            )
        if (
            self._soak.equity_currency is not None
            and capital.currency != self._soak.equity_currency
        ):
            return await self._safety_stop(
                "execution_soak_equity_currency_changed",
                {
                    "stage": stage,
                    "expected_currency": self._soak.equity_currency,
                    "actual_currency": capital.currency,
                },
            )
        self._update_execution_snapshot(snapshot, capital)
        automation = await self.automation.status()

        if automation.emergency_stop:
            return await self._safety_stop(
                "automation_emergency_stop_active", {"stage": stage}, engage_stop=False
            )
        if automation.locked:
            return await self._safety_stop(
                "automation_safety_lock_active",
                {"stage": stage, "reasons": automation.lock_reasons},
                engage_stop=False,
            )

        if self._soak.active_position_count > automation.max_open_positions:
            return await self._safety_stop(
                "multiple_exchange_positions_detected",
                {
                    "stage": stage,
                    "count": self._soak.active_position_count,
                    "limit": automation.max_open_positions,
                },
            )

        tracked_ids = {item.instrument_id for item in automation.active_trades}
        if not tracked_ids and automation.active_instrument_id:
            tracked_ids.add(automation.active_instrument_id)
        exposure = self._has_exchange_exposure()
        if exposure and not tracked_ids:
            return await self._safety_stop(
                "untracked_exchange_exposure_detected",
                {
                    "stage": stage,
                    "positions": self._soak.active_position_count,
                    "pending_orders": self._soak.active_pending_order_count,
                    "algo_orders": self._soak.active_algo_order_count,
                },
            )

        exposed_symbols = {
            item.instrument_id
            for item in [
                *snapshot.positions,
                *snapshot.pending_orders,
                *snapshot.pending_algo_orders,
            ]
        }
        untracked_symbols = exposed_symbols - tracked_ids
        if untracked_symbols:
            return await self._safety_stop(
                "exchange_exposure_symbol_mismatch",
                {
                    "stage": stage,
                    "tracked_instrument_ids": sorted(tracked_ids),
                    "exchange_symbols": sorted(exposed_symbols),
                    "untracked_symbols": sorted(untracked_symbols),
                },
            )

        if self._soak.active_position_count and self.settings.okx_demo_execution_soak_require_protection:
            position_ids = {item.instrument_id for item in snapshot.positions}
            missing_protection = {
                instrument_id
                for instrument_id in position_ids
                if not self._protection_present(snapshot, instrument_id)
            }
            protected = not missing_protection
            if not protected and poll_for_protection:
                attempts = self.settings.okx_demo_execution_soak_reconcile_attempts
                delay = self.settings.okx_demo_execution_soak_reconcile_delay_seconds
                for _ in range(max(0, attempts - 1)):
                    if delay:
                        await asyncio.sleep(delay)
                    snapshot = await self.demo_service.reconcile()
                    self._update_execution_exposure(snapshot)
                    position_ids = {item.instrument_id for item in snapshot.positions}
                    missing_protection = {
                        instrument_id
                        for instrument_id in position_ids
                        if not self._protection_present(snapshot, instrument_id)
                    }
                    protected = not missing_protection
                    if protected:
                        break
            self._soak.protection_checks += 1
            self._soak.protection_verified = protected
            if not protected:
                self._soak.protection_failures += 1
                await self._persist_soak()
                return await self._safety_stop(
                    "active_position_missing_protection",
                    {
                        "stage": stage,
                        "instrument_ids": sorted(missing_protection),
                    },
                )
        elif self._soak.active_position_count == 0:
            self._soak.protection_verified = True

        start = self._soak.starting_equity
        if start is not None and start > 0:
            limit = start * D(str(self.settings.okx_demo_execution_soak_loss_limit_pct))
            if self._soak.session_pnl <= -limit:
                await self._persist_soak()
                return await self._safety_stop(
                    "execution_soak_loss_limit_reached",
                    {
                        "stage": stage,
                        "starting_equity": str(start),
                        "latest_equity": str(self._soak.latest_equity),
                        "session_pnl": str(self._soak.session_pnl),
                        "loss_limit": str(limit),
                    },
                )

        await self._persist_soak()
        return False

    def _update_execution_exposure(self, snapshot) -> None:
        self._soak.active_position_count = len(snapshot.positions)
        self._soak.active_pending_order_count = len(snapshot.pending_orders)
        self._soak.active_algo_order_count = len(snapshot.pending_algo_orders)

    def _update_execution_snapshot(
        self,
        snapshot,
        capital: DemoRiskCapital,
    ) -> None:
        self._update_execution_exposure(snapshot)
        latest = capital.risk_equity
        if self._soak.equity_basis is None:
            self._soak.equity_basis = capital.basis
        if self._soak.equity_currency is None:
            self._soak.equity_currency = capital.currency
        self._soak.latest_equity = latest
        if self._soak.starting_equity is None:
            self._soak.starting_equity = latest
        start = self._soak.starting_equity
        if start is not None:
            self._soak.session_pnl = latest - start
            if start > 0:
                drawdown = max(D("0"), (start - latest) / start)
                self._soak.max_drawdown_pct_observed = max(
                    self._soak.max_drawdown_pct_observed, drawdown
                )

    @staticmethod
    def _protection_present(snapshot, active_instrument_id: str | None) -> bool:
        if active_instrument_id is None:
            return False
        if any(
            order.instrument_id == active_instrument_id
            for order in snapshot.pending_algo_orders
        ):
            return True
        return any(
            order.instrument_id == active_instrument_id
            and bool(order.attached_algo_orders)
            for order in snapshot.recent_orders
        )

    def _has_exchange_exposure(self) -> bool:
        return bool(
            self._soak.active_position_count
            or self._soak.active_pending_order_count
            or self._soak.active_algo_order_count
        )

    async def _finish_for_limit(self, reason: str) -> None:
        if self._soak.execute:
            try:
                if await self._refresh_execution_safety(stage="session_limit"):
                    return
            except Exception as exc:
                await self._safety_stop(
                    "session_limit_reconcile_failed", {"error": self._safe_error(exc)}
                )
                return
            if self._has_exchange_exposure():
                await self._finish_soak("stopped", reason + "_with_open_exposure")
                return
        await self._finish_soak("completed", reason)

    async def _safety_stop(
        self,
        reason: str,
        details: dict[str, Any],
        *,
        engage_stop: bool = True,
    ) -> bool:
        if self._soak.state != "running":
            return True
        self._soak.safety_stop_reason = reason
        self._soak.last_error = reason
        await self._emit(
            "critical",
            "execute_soak_safety_stop",
            "Controlled Demo execute soak stopped on a safety condition.",
            {"reason": reason, **details},
        )
        if engage_stop:
            try:
                await self.automation.emergency_stop()
            except Exception as exc:
                self._soak.last_error = (
                    reason + ";emergency_stop_failed:" + self._safe_error(exc)
                )[:250]
        await self._finish_soak("safety_stopped", reason)
        return True

    def _apply_run(self, run: DemoAutomationRunResult) -> None:
        outcomes = [result.outcome for result in run.results]
        outcome_set = set(outcomes)
        self._soak.completed_runs += 1
        self._soak.last_run_at = run.completed_at
        self._soak.last_outcome = outcomes[0] if len(outcomes) == 1 else ",".join(outcomes)[:40]
        submitted_count = outcomes.count("submitted")
        if submitted_count:
            # The legacy field name is retained in the persisted contract, but
            # the value is an order-submission count rather than a run count.
            self._soak.submitted_runs += submitted_count
        if "approved_dry_run" in outcome_set:
            self._soak.dry_run_runs += 1
        if outcome_set.intersection({"blocked", "locked", "monitoring", "duplicate"}):
            self._soak.blocked_runs += 1
        if "error" in outcome_set:
            self._soak.error_runs += 1
            self._soak.consecutive_errors += 1
            self._soak.last_error = next(
                (result.detail for result in run.results if result.outcome == "error"),
                "automation_run_error",
            )[:250]
        else:
            self._soak.consecutive_errors = 0
            self._soak.last_error = None

    async def _wait_with_heartbeat(self, seconds: int) -> bool:
        deadline = datetime.now(timezone.utc) + timedelta(seconds=seconds)
        heartbeat = max(1, self.settings.okx_demo_observability_heartbeat_seconds)
        while not self._soak_stop.is_set():
            self._last_heartbeat_at = datetime.now(timezone.utc)
            remaining = (deadline - self._last_heartbeat_at).total_seconds()
            if remaining <= 0:
                return False
            try:
                await asyncio.wait_for(self._soak_stop.wait(), timeout=min(heartbeat, remaining))
                return True
            except TimeoutError:
                continue
        return True

    async def _finish_soak(self, state: str, reason: str) -> None:
        if self._soak.state != "running":
            return

        if self._soak.execute and self.settings.okx_demo_execution_soak_auto_disarm:
            try:
                await self.automation.disarm()
                self._soak.auto_disarmed = True
            except Exception as exc:
                self._soak.last_error = (
                    (self._soak.last_error + ";") if self._soak.last_error else ""
                ) + "auto_disarm_failed:" + self._safe_error(exc)
                self._soak.last_error = self._soak.last_error[:250]

        self._soak.state = state
        self._soak.stopped_at = datetime.now(timezone.utc)
        self._soak.stop_reason = reason
        await self._persist_soak()
        await self._emit(
            "info" if state in {"completed", "stopped"} else "warning",
            "soak_session_finished",
            f"Demo soak session finished with state={state}.",
            {
                "state": state,
                "reason": reason,
                "completed_runs": self._soak.completed_runs,
                "error_runs": self._soak.error_runs,
                "submitted_runs": self._soak.submitted_runs,
                "session_pnl": str(self._soak.session_pnl),
                "auto_disarmed": self._soak.auto_disarmed,
                "active_position_count": self._soak.active_position_count,
                "active_algo_order_count": self._soak.active_algo_order_count,
            },
        )

    async def _refresh_runtime_exchange_safety(self) -> None:
        """Reconcile OKX Demo exposure outside controlled soak sessions."""

        automation = await self.automation.status()

        if automation.emergency_stop:
            return

        snapshot = await self.demo_service.reconcile()

        # Status captured before reconcile can become stale while an exchange
        # order is being acknowledged and its active-trade state is persisted.
        # Refresh after the network round-trip before classifying exposure.
        automation = await self.automation.status()
        if automation.emergency_stop:
            return

        positions = list(snapshot.positions)
        pending_orders = list(snapshot.pending_orders)
        pending_algo_orders = list(snapshot.pending_algo_orders)

        tracked_ids = {item.instrument_id for item in automation.active_trades}
        if not tracked_ids and automation.active_instrument_id:
            tracked_ids.add(automation.active_instrument_id)
        has_exposure = bool(
            positions
            or pending_orders
            or pending_algo_orders
        )
        exposed_symbols = {
            item.instrument_id
            for item in [
                *positions,
                *pending_orders,
                *pending_algo_orders,
            ]
        }

        reason = None
        details = {
            "stage": "runtime_watchdog",
            "tracked_instrument_ids": sorted(tracked_ids),
            "positions": len(positions),
            "pending_orders": len(pending_orders),
            "algo_orders": len(pending_algo_orders),
        }

        if len(positions) > automation.max_open_positions:
            reason = "multiple_exchange_positions_detected"
            details["position_limit"] = automation.max_open_positions

        elif has_exposure and not tracked_ids:
            reason = "untracked_exchange_exposure_detected"

        else:
            untracked_symbols = exposed_symbols - tracked_ids
            if untracked_symbols:
                reason = "exchange_exposure_symbol_mismatch"
                details["exchange_symbols"] = sorted(exposed_symbols)
                details["untracked_symbols"] = sorted(untracked_symbols)

            elif positions and self.settings.okx_demo_execution_soak_require_protection:
                missing_protection = {
                    position.instrument_id
                    for position in positions
                    if not self._protection_present(
                        snapshot, position.instrument_id
                    )
                }
                if missing_protection:
                    reason = "active_position_missing_protection"
                    details["instrument_ids"] = sorted(missing_protection)

        if reason is None:
            self._runtime_exchange_grace_started_at = None
            self._runtime_exchange_grace_symbols = frozenset()
            return

        grace_eligible = (
            automation.run_in_progress
            and reason
            in {
                "untracked_exchange_exposure_detected",
                "exchange_exposure_symbol_mismatch",
                "active_position_missing_protection",
            }
        )
        if grace_eligible:
            now = datetime.now(timezone.utc)
            symbols = frozenset(exposed_symbols)
            if (
                self._runtime_exchange_grace_started_at is None
                or symbols != self._runtime_exchange_grace_symbols
            ):
                self._runtime_exchange_grace_started_at = now
                self._runtime_exchange_grace_symbols = symbols
                await self._emit(
                    "info",
                    "runtime_exchange_reconcile_grace_started",
                    "Runtime exchange exposure is awaiting active-trade reconciliation.",
                    {
                        "reason": reason,
                        "instrument_ids": sorted(symbols),
                        "grace_seconds": self.settings.okx_demo_trade_reconcile_grace_seconds,
                    },
                )
            elapsed = now - self._runtime_exchange_grace_started_at
            if elapsed < timedelta(
                seconds=self.settings.okx_demo_trade_reconcile_grace_seconds
            ):
                return
            details["reconcile_grace_elapsed_seconds"] = elapsed.total_seconds()

        self._runtime_exchange_grace_started_at = None
        self._runtime_exchange_grace_symbols = frozenset()

        await self._emit(
            "critical",
            reason,
            "Runtime exchange safety verification failed.",
            details,
        )

        await self.automation.emergency_stop()

    async def _monitor_loop(self) -> None:
        interval = max(1, self.settings.okx_demo_observability_heartbeat_seconds)
        while not self._monitor_stop.is_set():
            self._last_heartbeat_at = datetime.now(timezone.utc)
            try:
                realtime = self.realtime_client.status()
                await self._refresh_runtime_exchange_safety()
                automation = await self.automation.status()
                self._update_runtime_counters(self._last_heartbeat_at, realtime)
                alerts = self._current_alerts(self._last_heartbeat_at, realtime, automation)
                current_codes = {event.code for event in alerts}
                for event in alerts:
                    if event.code not in self._active_alert_codes:
                        await self._emit(
                            event.severity,
                            event.code,
                            event.message,
                            event.details,
                        )
                for resolved in self._active_alert_codes - current_codes:
                    await self._emit(
                        "info",
                        "alert_resolved",
                        f"Observability alert resolved: {resolved}",
                        {"resolved_code": resolved},
                    )
                self._active_alert_codes = current_codes
            except Exception as exc:
                logger.exception("demo_observability_monitor_failed")
                await self._emit(
                    "warning",
                    "observability_monitor_exception",
                    "The observability watchdog encountered an exception.",
                    {"error": self._safe_error(exc)},
                )
            try:
                await asyncio.wait_for(self._monitor_stop.wait(), timeout=interval)
            except TimeoutError:
                continue

    def _current_alerts(self, now, realtime, automation) -> list[DemoObservabilityEventView]:
        alerts: list[DemoObservabilityEventView] = []
        if realtime.enabled and not realtime.connected:
            alerts.append(
                DemoObservabilityEventView(
                    severity="warning",
                    code="okx_public_websocket_disconnected",
                    message="OKX public WebSocket is enabled but disconnected.",
                    details={"running": bool(realtime.running)},
                    observed_at=now,
                )
            )
        if (
            self._last_parse_error_at is not None
            and now <= self._last_parse_error_at
            + timedelta(seconds=self.settings.okx_demo_observability_stale_after_seconds)
        ):
            alerts.append(
                DemoObservabilityEventView(
                    severity="warning",
                    code="okx_public_websocket_parse_errors",
                    message="OKX public WebSocket recorded a recent parse error increase.",
                    details={"parse_error_count": int(realtime.parse_error_count)},
                    observed_at=now,
                )
            )
        if automation.emergency_stop:
            alerts.append(
                DemoObservabilityEventView(
                    severity="critical",
                    code="demo_automation_emergency_stop",
                    message="Demo automation emergency stop is engaged.",
                    details={},
                    observed_at=now,
                )
            )
        elif automation.locked:
            alerts.append(
                DemoObservabilityEventView(
                    severity="warning",
                    code="demo_automation_locked",
                    message="Demo automation is safety-locked.",
                    details={"reasons": automation.lock_reasons},
                    observed_at=now,
                )
            )
        if self._soak.state == "safety_stopped":
            alerts.append(
                DemoObservabilityEventView(
                    severity="critical",
                    code="demo_execute_soak_safety_stopped",
                    message="The latest controlled execute soak stopped on a safety condition.",
                    details={"reason": self._soak.safety_stop_reason},
                    observed_at=now,
                )
            )
        elif self._soak.state in {"error", "interrupted"}:
            alerts.append(
                DemoObservabilityEventView(
                    severity="warning",
                    code="demo_soak_not_cleanly_completed",
                    message="The latest Demo soak session did not complete cleanly.",
                    details={"state": self._soak.state, "reason": self._soak.stop_reason},
                    observed_at=now,
                )
            )
        if (
            self._recovered
            and now > self._last_heartbeat_at
            + timedelta(seconds=self.settings.okx_demo_observability_stale_after_seconds)
        ):
            alerts.append(
                DemoObservabilityEventView(
                    severity="critical",
                    code="observability_watchdog_stale",
                    message="The Demo observability watchdog heartbeat is stale.",
                    details={
                        "last_heartbeat_at": self._last_heartbeat_at.isoformat(),
                        "stale_after_seconds": self.settings.okx_demo_observability_stale_after_seconds,
                    },
                    observed_at=now,
                )
            )
        if self.soak_running and self._soak.last_run_at is not None:
            stale_after = max(
                self.settings.okx_demo_observability_stale_after_seconds,
                self._soak.interval_seconds * 2,
            )
            if now > self._soak.last_run_at + timedelta(seconds=stale_after):
                alerts.append(
                    DemoObservabilityEventView(
                        severity="critical",
                        code="demo_soak_run_stale",
                        message="The running Demo soak session has not completed a run on schedule.",
                        details={"stale_after_seconds": stale_after},
                        observed_at=now,
                    )
                )
        return alerts

    def _update_runtime_counters(self, now: datetime, realtime) -> None:
        parse_errors = int(realtime.parse_error_count)
        if parse_errors > self._last_parse_error_count:
            self._last_parse_error_at = now
        self._last_parse_error_count = parse_errors

    async def _persist_soak(self) -> None:
        if self.repository is not None:
            await self.repository.update_session(self._soak)

    async def _emit(
        self,
        severity: str,
        code: str,
        message: str,
        details: dict,
    ) -> DemoObservabilityEventView:
        event = DemoObservabilityEventView(
            severity=severity,
            code=code,
            message=message,
            details=details,
        )
        if self.repository is not None:
            event = await self.repository.add_event(
                severity=severity,
                code=code,
                message=message,
                details=details,
                event_limit=self.settings.okx_demo_observability_event_limit,
            )
        self._events.appendleft(event)
        return event

    @staticmethod
    def _safe_error(exc: Exception) -> str:
        return f"{exc.__class__.__name__}: {exc}"[:250]
