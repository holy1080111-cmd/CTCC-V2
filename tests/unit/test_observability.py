import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.config.settings import Settings
from app.database.models.observability import DemoSoakSession
from app.domain.demo_automation import (
    DemoAutomationActiveTrade,
    DemoAutomationRunResult,
    DemoAutomationStatus,
    DemoAutomationSymbolResult,
)
from app.domain.observability import DemoSoakStartRequest
from app.domain.realtime import RealtimeStatus
from app.observability import DemoObservabilityError
from app.observability.service import DemoObservabilityService


class FakeAutomation:
    def __init__(
        self,
        *,
        armed: bool = False,
        outcome: str = "approved_dry_run",
        submission_count: int = 1,
    ) -> None:
        self.armed = armed
        self.outcome = outcome
        self.submission_count = submission_count
        self.runs: list[DemoAutomationRunResult] = []
        self.emergency = False
        self.locked = False
        self.active_instrument_id: str | None = None
        self.active_instrument_ids: set[str] = set()
        self.max_open_positions = 1
        self.submission_limits: list[int | None] = []
        self.disarm_calls = 0
        self.emergency_calls = 0
        self.run_in_progress = False
        self.status_calls = 0

    async def status(self) -> DemoAutomationStatus:
        self.status_calls += 1
        now = datetime.now(timezone.utc)
        return DemoAutomationStatus(
            capability_enabled=True,
            trading_mode="okx_demo",
            demo_writes_enabled=True,
            armed=self.armed,
            running=False,
            run_in_progress=self.run_in_progress,
            emergency_stop=self.emergency,
            locked=self.locked,
            lock_reasons=["emergency_stop_engaged"] if self.locked else [],
            configuration_blockers=[],
            symbols=["BTC-USDT-SWAP"],
            scan_interval_seconds=300,
            max_trades_per_day=3,
            daily_loss_limit_pct="0.01",
            max_consecutive_losses=2,
            max_open_positions=self.max_open_positions,
            active_trades=[
                DemoAutomationActiveTrade(
                    instrument_id=instrument_id,
                    started_at=now,
                )
                for instrument_id in sorted(self.active_instrument_ids)
            ],
            session_date=now.date(),
            active_instrument_id=self.active_instrument_id,
            recovered=True,
        )

    async def run_once(
        self,
        *,
        symbols=None,
        execute=False,
        trigger="manual",
        submission_limit=None,
    ):
        now = datetime.now(timezone.utc)
        self.submission_limits.append(submission_limit)
        outcome = "submitted" if execute and self.outcome == "submitted" else self.outcome
        if execute and submission_limit == 0 and outcome == "submitted":
            outcome = "blocked"
        if execute and outcome == "submitted":
            self.active_instrument_id = "BTC-USDT-SWAP"
        run = DemoAutomationRunResult(
            trigger=trigger,
            execute=execute,
            started_at=now,
            completed_at=now,
            results=[
                DemoAutomationSymbolResult(
                    symbol=(symbols or ["BTC-USDT-SWAP"])[0],
                    instrument_id=(
                        "BTC-USDT-SWAP" if index == 0 else "ETH-USDT-SWAP"
                    ),
                    outcome=outcome,
                    detail="unit_test",
                )
                for index in range(
                    self.submission_count if outcome == "submitted" else 1
                )
            ],
        )
        self.runs.append(run)
        return run

    async def history(self, limit: int = 20):
        return self.runs[-limit:]

    async def disarm(self):
        self.disarm_calls += 1
        self.armed = False
        return await self.status()

    async def emergency_stop(self):
        self.emergency_calls += 1
        self.armed = False
        self.emergency = True
        self.locked = True
        return await self.status()


class FakeRealtime:
    def __init__(self, *, connected: bool = True, parse_errors: int = 0) -> None:
        self.connected = connected
        self.parse_errors = parse_errors

    def status(self) -> RealtimeStatus:
        return RealtimeStatus(
            enabled=True,
            running=True,
            connected=self.connected,
            endpoint="wss://example.test",
            symbols=["BTC-USDT-SWAP"],
            message_count=10,
            parse_error_count=self.parse_errors,
            last_message_at=datetime.now(timezone.utc),
            paper_auto_ticks=False,
        )


class FakeDemoService:
    def __init__(self, *snapshots) -> None:
        self.snapshots = list(snapshots) or [exchange_snapshot()]
        self.calls = 0

    async def reconcile(self):
        self.calls += 1
        if len(self.snapshots) > 1:
            return self.snapshots.pop(0)
        return self.snapshots[0]


class TrackingRaceDemoService(FakeDemoService):
    def __init__(self, automation: FakeAutomation, *snapshots) -> None:
        super().__init__(*snapshots)
        self.automation = automation

    async def reconcile(self):
        snapshot = await super().reconcile()
        # Simulate the order task publishing its tracked state while the
        # watchdog is awaiting the exchange reconciliation round-trip.
        self.automation.active_instrument_id = "BTC-USDT-SWAP"
        return snapshot


def exchange_snapshot(
    *,
    equity: str = "1000",
    account_total_equity: str | None = None,
    account_level: str = "2",
    positions: int = 0,
    pending_orders: int = 0,
    algo_orders: int = 0,
    protected_recent_order: bool = False,
    position_symbols: list[str] | None = None,
    algo_symbols: list[str] | None = None,
):
    instrument_id = "BTC-USDT-SWAP"
    risk_equity = Decimal(equity)
    total_equity = Decimal(account_total_equity or equity)
    position_ids = position_symbols or [instrument_id for _ in range(positions)]
    algo_ids = algo_symbols or [instrument_id for _ in range(algo_orders)]
    return SimpleNamespace(
        account_config=SimpleNamespace(account_level=account_level),
        balance=SimpleNamespace(
            total_equity=total_equity,
            adjusted_equity=risk_equity,
            available_equity=risk_equity,
            details=[
                SimpleNamespace(
                    currency="USDT",
                    equity=risk_equity,
                    available_equity=risk_equity,
                )
            ],
        ),
        positions=[SimpleNamespace(instrument_id=item) for item in position_ids],
        pending_orders=[
            SimpleNamespace(instrument_id=instrument_id) for _ in range(pending_orders)
        ],
        pending_algo_orders=[SimpleNamespace(instrument_id=item) for item in algo_ids],
        recent_orders=[
            SimpleNamespace(
                instrument_id=instrument_id,
                attached_algo_orders=[{"protected": True}],
            )
        ]
        if protected_recent_order
        else [],
    )


def settings(**updates) -> Settings:
    values = dict(
        environment="test",
        okx_demo_observability_enabled=True,
        okx_demo_soak_enabled=True,
        okx_demo_soak_allow_execute=False,
        okx_demo_soak_default_duration_minutes=1,
        okx_demo_soak_max_duration_minutes=10,
        okx_demo_soak_interval_seconds=1,
        okx_demo_soak_max_runs=1,
        okx_demo_observability_heartbeat_seconds=1,
        okx_demo_observability_event_limit=50,
    )
    values.update(updates)
    return Settings(_env_file=None, **values)


def execute_settings(**updates) -> Settings:
    values = dict(
        environment="test",
        trading_mode="okx_demo",
        okx_demo_enabled=True,
        okx_demo_allow_order_writes=True,
        okx_demo_api_key="key",
        okx_demo_api_secret="secret",
        okx_demo_api_passphrase="pass",
        okx_demo_auto_execution=True,
        okx_ws_enabled=True,
        okx_demo_soak_enabled=True,
        okx_demo_soak_allow_execute=True,
        okx_demo_soak_default_duration_minutes=1,
        okx_demo_soak_max_duration_minutes=10,
        okx_demo_soak_interval_seconds=60,
        okx_demo_soak_max_runs=1,
        okx_demo_execution_soak_max_submissions=1,
        okx_demo_execution_soak_loss_limit_pct="0.0025",
        okx_demo_execution_soak_reconcile_attempts=2,
        okx_demo_execution_soak_reconcile_delay_seconds=0,
        okx_demo_observability_heartbeat_seconds=1,
        okx_demo_observability_event_limit=50,
    )
    values.update(updates)
    return Settings(_env_file=None, **values)


async def wait_for_finished(service: DemoObservabilityService):
    status = await service.soak_status()
    for _ in range(200):
        status = await service.soak_status()
        if status.state != "running":
            return status
        await asyncio.sleep(0.01)
    return status


@pytest.mark.asyncio
async def test_observation_soak_completes_without_execute() -> None:
    automation = FakeAutomation(outcome="approved_dry_run")
    service = DemoObservabilityService(
        automation=automation,
        settings=settings(),
        repository=None,
        realtime_client=FakeRealtime(),
    )
    await service.recover()
    started = await service.start_soak(
        DemoSoakStartRequest(
            execute=False,
            duration_minutes=1,
            interval_seconds=1,
            max_runs=1,
            confirmation="START_DEMO_SOAK_OBSERVE",
        )
    )
    assert started.state == "running"
    status = await wait_for_finished(service)
    assert status.state == "completed"
    assert status.completed_runs == 1
    assert status.dry_run_runs == 1
    assert automation.runs[0].execute is False


@pytest.mark.asyncio
async def test_execute_soak_requires_explicit_setting() -> None:
    service = DemoObservabilityService(
        automation=FakeAutomation(armed=True),
        settings=settings(),
        repository=None,
        realtime_client=FakeRealtime(),
        demo_service=FakeDemoService(exchange_snapshot()),
    )
    await service.recover()
    with pytest.raises(DemoObservabilityError, match="execute_soak_preflight_failed"):
        await service.start_soak(
            DemoSoakStartRequest(
                execute=True,
                duration_minutes=1,
                interval_seconds=60,
                max_runs=1,
                confirmation="START_DEMO_SOAK_EXECUTE",
            )
        )


@pytest.mark.asyncio
async def test_execute_preflight_requires_flat_exchange() -> None:
    service = DemoObservabilityService(
        automation=FakeAutomation(armed=True),
        settings=execute_settings(),
        repository=None,
        realtime_client=FakeRealtime(),
        demo_service=FakeDemoService(exchange_snapshot(positions=1, algo_orders=1)),
    )
    await service.recover()
    result = await service.execute_preflight()
    assert result.ready is False
    assert "exchange_exposure_must_be_zero_before_execute_soak" in result.blockers
    assert result.exchange_position_count == 1


@pytest.mark.asyncio
async def test_execute_preflight_separates_risk_equity_from_account_total() -> None:
    service = DemoObservabilityService(
        automation=FakeAutomation(armed=True),
        settings=execute_settings(),
        repository=None,
        realtime_client=FakeRealtime(),
        demo_service=FakeDemoService(
            exchange_snapshot(equity="5000", account_total_equity="97000")
        ),
    )
    await service.recover()

    result = await service.execute_preflight()

    assert result.ready is True
    assert result.total_equity == Decimal("97000")
    assert result.risk_equity == Decimal("5000")
    assert result.equity_basis == "single_currency:USDT"
    assert result.equity_currency == "USDT"


@pytest.mark.asyncio
async def test_execute_preflight_blocks_unresolved_risk_equity() -> None:
    service = DemoObservabilityService(
        automation=FakeAutomation(armed=True),
        settings=execute_settings(),
        repository=None,
        realtime_client=FakeRealtime(),
        demo_service=FakeDemoService(exchange_snapshot(account_level="1")),
    )
    await service.recover()

    result = await service.execute_preflight()

    assert result.ready is False
    assert "unsupported_okx_demo_account_level" in result.blockers
    assert result.risk_equity is None
    assert result.equity_basis is None


@pytest.mark.asyncio
async def test_execute_soak_auto_disarms_after_clean_run() -> None:
    automation = FakeAutomation(armed=True, outcome="no_trade")
    service = DemoObservabilityService(
        automation=automation,
        settings=execute_settings(),
        repository=None,
        realtime_client=FakeRealtime(),
        demo_service=FakeDemoService(exchange_snapshot()),
    )
    await service.recover()
    await service.start_soak(
        DemoSoakStartRequest(
            execute=True,
            duration_minutes=1,
            interval_seconds=60,
            max_runs=1,
            confirmation="START_DEMO_SOAK_EXECUTE",
        )
    )
    status = await wait_for_finished(service)
    assert status.state == "completed"
    assert status.stop_reason == "max_runs_reached"
    assert status.auto_disarmed is True
    assert automation.armed is False
    assert automation.disarm_calls == 1
    assert automation.submission_limits == [1]


@pytest.mark.asyncio
async def test_unprotected_position_safety_stops_and_engages_emergency_stop() -> None:
    automation = FakeAutomation(armed=True, outcome="submitted")
    service = DemoObservabilityService(
        automation=automation,
        settings=execute_settings(),
        repository=None,
        realtime_client=FakeRealtime(),
        demo_service=FakeDemoService(
            exchange_snapshot(),
            exchange_snapshot(),
            exchange_snapshot(positions=1),
        ),
    )
    await service.recover()
    await service.start_soak(
        DemoSoakStartRequest(
            execute=True,
            duration_minutes=1,
            interval_seconds=60,
            max_runs=1,
            confirmation="START_DEMO_SOAK_EXECUTE",
        )
    )
    status = await wait_for_finished(service)
    assert status.state == "safety_stopped"
    assert status.safety_stop_reason == "active_position_missing_protection"
    assert status.protection_failures == 1
    assert status.auto_disarmed is True
    assert automation.emergency_calls == 1


@pytest.mark.asyncio
async def test_protected_position_is_not_safety_stopped() -> None:
    automation = FakeAutomation(armed=True, outcome="submitted")
    protected = exchange_snapshot(positions=1, algo_orders=1)
    service = DemoObservabilityService(
        automation=automation,
        settings=execute_settings(),
        repository=None,
        realtime_client=FakeRealtime(),
        demo_service=FakeDemoService(
            exchange_snapshot(),
            exchange_snapshot(),
            protected,
        ),
    )
    await service.recover()
    await service.start_soak(
        DemoSoakStartRequest(
            execute=True,
            duration_minutes=1,
            interval_seconds=60,
            max_runs=1,
            confirmation="START_DEMO_SOAK_EXECUTE",
        )
    )
    status = await wait_for_finished(service)
    assert status.state == "stopped"
    assert status.stop_reason == "max_runs_reached_with_open_exposure"
    assert status.protection_verified is True
    assert status.protection_failures == 0
    assert status.auto_disarmed is True
    assert automation.emergency_calls == 0


@pytest.mark.asyncio
async def test_execute_soak_defensively_stops_over_limit_automation_result() -> None:
    automation = FakeAutomation(
        armed=True,
        outcome="submitted",
        submission_count=2,
    )
    service = DemoObservabilityService(
        automation=automation,
        settings=execute_settings(),
        repository=None,
        realtime_client=FakeRealtime(),
        demo_service=FakeDemoService(exchange_snapshot(), exchange_snapshot()),
    )
    await service.recover()
    await service.start_soak(
        DemoSoakStartRequest(
            execute=True,
            duration_minutes=1,
            interval_seconds=60,
            max_runs=1,
            confirmation="START_DEMO_SOAK_EXECUTE",
        )
    )

    status = await wait_for_finished(service)

    assert status.state == "safety_stopped"
    assert status.safety_stop_reason == "submission_limit_exceeded"
    assert status.submitted_runs == 2
    assert automation.emergency_calls == 1


@pytest.mark.asyncio
async def test_execute_soak_loss_limit_safety_stops() -> None:
    automation = FakeAutomation(armed=True, outcome="no_trade")
    service = DemoObservabilityService(
        automation=automation,
        settings=execute_settings(),
        repository=None,
        realtime_client=FakeRealtime(),
        demo_service=FakeDemoService(
            exchange_snapshot(equity="5000", account_total_equity="97000"),
            exchange_snapshot(equity="5000", account_total_equity="97500"),
            exchange_snapshot(equity="4980", account_total_equity="98000"),
        ),
    )
    await service.recover()
    await service.start_soak(
        DemoSoakStartRequest(
            execute=True,
            duration_minutes=1,
            interval_seconds=60,
            max_runs=1,
            confirmation="START_DEMO_SOAK_EXECUTE",
        )
    )
    status = await wait_for_finished(service)
    assert status.state == "safety_stopped"
    assert status.safety_stop_reason == "execution_soak_loss_limit_reached"
    assert status.equity_basis == "single_currency:USDT"
    assert status.equity_currency == "USDT"
    assert status.starting_equity == Decimal("5000")
    assert status.latest_equity == Decimal("4980")
    assert status.session_pnl == Decimal("-20")
    assert automation.emergency_calls == 1


@pytest.mark.asyncio
async def test_execute_soak_stops_if_equity_basis_changes() -> None:
    automation = FakeAutomation(armed=True, outcome="no_trade")
    service = DemoObservabilityService(
        automation=automation,
        settings=execute_settings(),
        repository=None,
        realtime_client=FakeRealtime(),
        demo_service=FakeDemoService(
            exchange_snapshot(equity="5000", account_level="2"),
            exchange_snapshot(equity="5000", account_level="3"),
        ),
    )
    await service.recover()
    await service.start_soak(
        DemoSoakStartRequest(
            execute=True,
            duration_minutes=1,
            interval_seconds=60,
            max_runs=1,
            confirmation="START_DEMO_SOAK_EXECUTE",
        )
    )

    status = await wait_for_finished(service)

    assert status.state == "safety_stopped"
    assert status.safety_stop_reason == "execution_soak_equity_basis_changed"
    assert automation.emergency_calls == 1
    assert automation.runs == []


def test_soak_session_model_persists_equity_identity() -> None:
    assert DemoSoakSession.__table__.c.equity_basis.type.length == 40
    assert DemoSoakSession.__table__.c.equity_basis.nullable is True
    assert DemoSoakSession.__table__.c.equity_currency.type.length == 16
    assert DemoSoakSession.__table__.c.equity_currency.nullable is True


@pytest.mark.asyncio
async def test_summary_reports_disconnected_websocket() -> None:
    service = DemoObservabilityService(
        automation=FakeAutomation(),
        settings=settings(),
        repository=None,
        realtime_client=FakeRealtime(connected=False),
    )
    await service.recover()
    summary = await service.summary()
    assert summary.status == "degraded"
    assert "okx_public_websocket_disconnected" in {event.code for event in summary.alerts}


@pytest.mark.asyncio
async def test_metrics_count_manual_and_scheduled_runs() -> None:
    automation = FakeAutomation(outcome="approved_dry_run")
    await automation.run_once(execute=False, trigger="manual")
    await automation.run_once(execute=False, trigger="scheduled")
    service = DemoObservabilityService(
        automation=automation,
        settings=settings(),
        repository=None,
        realtime_client=FakeRealtime(),
    )
    metrics = await service.metrics(24)
    assert metrics.total_runs == 2
    assert metrics.manual_runs == 1
    assert metrics.scheduled_runs == 1
    assert metrics.approved_dry_run == 2


@pytest.mark.asyncio
async def test_runtime_watchdog_stops_untracked_exchange_exposure() -> None:
    automation = FakeAutomation()

    service = DemoObservabilityService(
        automation=automation,
        settings=execute_settings(),
        repository=None,
        realtime_client=FakeRealtime(),
        demo_service=FakeDemoService(
            exchange_snapshot(positions=1),
        ),
    )

    await service.recover()
    await service._refresh_runtime_exchange_safety()

    assert automation.emergency_calls == 1
    assert automation.emergency is True
    assert automation.locked is True
    assert automation.armed is False
    assert any(
        event.code == "untracked_exchange_exposure_detected"
        for event in service._events
    )


@pytest.mark.asyncio
async def test_runtime_watchdog_refreshes_tracking_after_exchange_reconcile() -> None:
    automation = FakeAutomation()
    service = DemoObservabilityService(
        automation=automation,
        settings=execute_settings(),
        repository=None,
        realtime_client=FakeRealtime(),
        demo_service=TrackingRaceDemoService(
            automation,
            exchange_snapshot(positions=1, algo_orders=1),
        ),
    )

    await service.recover()
    await service._refresh_runtime_exchange_safety()

    assert automation.status_calls == 2
    assert automation.emergency_calls == 0
    assert not any(
        event.code == "untracked_exchange_exposure_detected"
        for event in service._events
    )


@pytest.mark.asyncio
async def test_runtime_watchdog_submission_grace_expires_fail_closed() -> None:
    automation = FakeAutomation()
    automation.run_in_progress = True
    service = DemoObservabilityService(
        automation=automation,
        settings=execute_settings(okx_demo_trade_reconcile_grace_seconds=5),
        repository=None,
        realtime_client=FakeRealtime(),
        demo_service=FakeDemoService(
            exchange_snapshot(positions=1, algo_orders=1),
        ),
    )

    await service.recover()
    await service._refresh_runtime_exchange_safety()

    assert automation.emergency_calls == 0
    assert any(
        event.code == "runtime_exchange_reconcile_grace_started"
        for event in service._events
    )

    service._runtime_exchange_grace_started_at = (
        datetime.now(timezone.utc) - timedelta(seconds=6)
    )
    await service._refresh_runtime_exchange_safety()

    assert automation.emergency_calls == 1
    assert automation.emergency is True
    assert any(
        event.code == "untracked_exchange_exposure_detected"
        for event in service._events
    )


@pytest.mark.asyncio
async def test_runtime_watchdog_stops_unprotected_active_position() -> None:
    automation = FakeAutomation()
    automation.active_instrument_id = "BTC-USDT-SWAP"

    service = DemoObservabilityService(
        automation=automation,
        settings=execute_settings(),
        repository=None,
        realtime_client=FakeRealtime(),
        demo_service=FakeDemoService(
            exchange_snapshot(positions=1),
        ),
    )

    await service.recover()
    await service._refresh_runtime_exchange_safety()

    assert automation.emergency_calls == 1
    assert automation.emergency is True
    assert automation.locked is True
    assert automation.armed is False
    assert any(
        event.code == "active_position_missing_protection"
        for event in service._events
    )


@pytest.mark.asyncio
async def test_runtime_watchdog_accepts_protected_active_position() -> None:
    automation = FakeAutomation()
    automation.active_instrument_id = "BTC-USDT-SWAP"

    service = DemoObservabilityService(
        automation=automation,
        settings=execute_settings(),
        repository=None,
        realtime_client=FakeRealtime(),
        demo_service=FakeDemoService(
            exchange_snapshot(
                positions=1,
                algo_orders=1,
            ),
        ),
    )

    await service.recover()
    await service._refresh_runtime_exchange_safety()

    assert automation.emergency_calls == 0
    assert automation.emergency is False
    assert automation.locked is False
    assert automation.armed is False
    assert not any(
        event.code in {
            "untracked_exchange_exposure_detected",
            "active_position_missing_protection",
            "exchange_exposure_symbol_mismatch",
            "multiple_exchange_positions_detected",
        }
        for event in service._events
    )


@pytest.mark.asyncio
async def test_runtime_watchdog_accepts_two_tracked_protected_positions() -> None:
    automation = FakeAutomation()
    automation.max_open_positions = 2
    automation.active_instrument_ids = {
        "BTC-USDT-SWAP",
        "ETH-USDT-SWAP",
    }
    snapshot = exchange_snapshot(
        position_symbols=["BTC-USDT-SWAP", "ETH-USDT-SWAP"],
        algo_symbols=["BTC-USDT-SWAP", "ETH-USDT-SWAP"],
    )
    service = DemoObservabilityService(
        automation=automation,
        settings=execute_settings(),
        repository=None,
        realtime_client=FakeRealtime(),
        demo_service=FakeDemoService(snapshot),
    )

    await service.recover()
    await service._refresh_runtime_exchange_safety()

    assert automation.emergency_calls == 0
    assert not any(
        event.code
        in {
            "multiple_exchange_positions_detected",
            "exchange_exposure_symbol_mismatch",
            "active_position_missing_protection",
        }
        for event in service._events
    )
