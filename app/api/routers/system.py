from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.config.settings import get_settings
from app.database.health import check_database
from app.domain.state_machine import transition_map
from app.domain.system import (
    CapabilityResponse,
    DependencyStatus,
    LivenessResponse,
    ReadinessResponse,
    VersionResponse,
)
from app.monitoring.redis_health import check_redis

router = APIRouter(tags=["system"])
settings = get_settings()


@router.get("/liveness", response_model=LivenessResponse)
async def liveness() -> LivenessResponse:
    return LivenessResponse(version=settings.app_version)


@router.get("/readiness", response_model=ReadinessResponse)
async def readiness() -> JSONResponse | ReadinessResponse:
    database_ok, database_detail = await check_database()
    redis_ok, redis_detail = await check_redis()

    blockers: list[str] = []
    if settings.readiness_require_database and not database_ok:
        blockers.append("database_unavailable")
    if settings.readiness_require_redis and not redis_ok:
        blockers.append("redis_unavailable")
    if settings.auto_trade:
        blockers.append("legacy_auto_trade_forbidden")
    if settings.trading_mode == "live":
        if not settings.okx_live_enabled:
            blockers.append("okx_live_disabled")
        if not settings.okx_live_credentials_configured:
            blockers.append("okx_live_credentials_missing")
        if settings.live_trading and not settings.okx_live_allow_order_writes:
            blockers.append("okx_live_order_writes_disabled")
    if settings.trading_mode == "okx_demo":
        if not settings.okx_demo_enabled:
            blockers.append("okx_demo_disabled")
        if not settings.okx_demo_credentials_configured:
            blockers.append("okx_demo_credentials_missing")

    response = ReadinessResponse(
        status="ready" if not blockers else "blocked",
        version=settings.app_version,
        database=DependencyStatus(ok=database_ok, detail=database_detail),
        redis=DependencyStatus(ok=redis_ok, detail=redis_detail),
        blockers=blockers,
    )
    if blockers:
        return JSONResponse(status_code=503, content=response.model_dump(mode="json"))
    return response


@router.get("/api/version", response_model=VersionResponse)
async def version() -> VersionResponse:
    return VersionResponse(
        name=settings.app_name,
        version=settings.app_version,
        environment=settings.environment,
        trading_mode=settings.trading_mode,
        auto_trade=settings.auto_trade,
        live_trading=settings.live_trading,
    )


@router.get("/api/capabilities", response_model=CapabilityResponse)
async def capabilities() -> CapabilityResponse:
    return CapabilityResponse(
        version=settings.app_version,
        completed=[
            "fastapi_platform",
            "postgresql_schema",
            "redis_health",
            "domain_validation",
            "trade_lifecycle_state_machine",
            "optimistic_lifecycle_versioning",
            "audit_and_safety_event_storage",
            "okx_public_rest_market_data",
            "multi_timeframe_candles",
            "ticker_orderbook_funding_open_interest",
            "candle_data_quality_validation",
            "multi_strategy_evaluation",
            "deterministic_risk_engine",
            "position_sizing_and_loss_limits",
            "paper_execution",
            "paper_market_and_limit_orders",
            "paper_stop_loss_take_profit",
            "paper_pnl_and_fee_accounting",
            "okx_public_websocket_realtime_market",
            "auto_paper_orchestrator",
            "strategy_risk_paper_execution_pipeline",
            "candidate_deduplication_and_realtime_entry_guard",
            "postgresql_paper_state_persistence",
            "paper_restart_recovery",
            "orchestrator_history_persistence",
            "candidate_fingerprint_restart_deduplication",
            "paper_state_checksum_reconciliation",
            "paper_audit_log",
            "okx_demo_authenticated_rest",
            "okx_demo_simulated_trading_header",
            "okx_demo_read_only_account_sync",
            "okx_demo_manual_swap_orders",
            "okx_demo_attached_stop_loss_take_profit",
            "okx_demo_cancel_and_close",
            "okx_demo_leverage_safety_cap",
            "okx_demo_postgresql_mirror",
            "okx_demo_restart_reconciliation",
            "okx_demo_explicitly_armed_automation",
            "okx_demo_strategy_risk_execution_pipeline",
            "okx_demo_daily_loss_and_trade_count_locks",
            "okx_demo_emergency_stop_and_restart_disarm",
            "okx_demo_automation_state_persistence",
            "okx_demo_observability_watchdog",
            "okx_demo_durable_soak_sessions",
            "okx_demo_soak_restart_interruption_detection",
            "okx_demo_run_metrics_and_alert_events",
            "okx_demo_execute_soak_preflight",
            "okx_demo_execute_soak_session_loss_budget",
            "okx_demo_execute_soak_protection_verification",
            "okx_demo_execute_soak_auto_disarm",
            "okx_demo_execute_soak_emergency_safety_stop",
            "okx_demo_append_only_equity_snapshots",
            "okx_demo_daily_performance_reports",
            "okx_demo_fee_funding_and_slippage_analysis",
            "okx_demo_reliability_validation",
            "okx_demo_operator_strategy_controls",
            "okx_demo_disabled_strategy_execution_filter",
            "okx_live_authenticated_read_reconciliation",
            "okx_live_account_identity_pinning",
            "okx_live_postgresql_mirror",
            "okx_live_production_execution_transport",
            "okx_live_durable_intent_idempotency",
            "okx_live_explicit_arm_and_emergency_stop",
            "okx_live_protected_real_position_execution",
            "okx_live_one_shot_automation",
            "live_execution",
        ],
        not_yet_available=[
            "okx_private_websocket",
            "okx_live_real_account_operator_acceptance",
        ],
    )


@router.get("/api/lifecycle/transitions")
async def lifecycle_transitions() -> dict[str, list[str]]:
    return transition_map()
