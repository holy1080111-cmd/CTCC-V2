import pytest
from sqlalchemy import inspect

from app.database.session import engine

EXPECTED_TABLES = {
    "analysis_runs", "timeframe_analyses", "strategy_evaluations", "trade_candidates",
    "risk_decisions", "trade_lifecycles", "orders", "fills", "positions",
    "protective_orders", "trades", "market_snapshots", "account_snapshots",
    "portfolio_snapshots", "system_events", "audit_logs", "safety_incidents",
    "configuration_versions", "paper_account_state", "paper_order_state",
    "paper_position_state", "orchestrator_run_state",
    "orchestrator_fingerprint_state", "recovery_checkpoints",
    "okx_demo_balance_state", "okx_demo_order_state",
    "okx_demo_position_state", "okx_demo_algo_order_state",
    "okx_demo_sync_checkpoints",
    "demo_automation_state", "demo_automation_runs",
    "demo_automation_fingerprints",
    "demo_soak_sessions", "demo_observability_events",
    "demo_performance_snapshots", "demo_strategy_controls",
    "demo_daily_performance_reports",
}


@pytest.mark.integration
async def test_all_phase_one_tables_exist() -> None:
    async with engine.connect() as connection:
        tables = set(await connection.run_sync(lambda sync_conn: inspect(sync_conn).get_table_names()))
    assert EXPECTED_TABLES.issubset(tables)
