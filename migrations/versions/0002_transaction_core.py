"""Create transaction core schema.

Revision ID: 0002
Revises: 0001
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "analysis_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("symbol", sa.String(40), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("input_timeframes", postgresql.JSONB(), nullable=False),
        sa.Column("data_quality", postgresql.JSONB(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(100), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_analysis_runs")),
    )
    op.create_index(op.f("ix_analysis_runs_symbol"), "analysis_runs", ["symbol"])

    op.create_table(
        "market_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("symbol", sa.String(40), nullable=False),
        sa.Column("bid", sa.Numeric(28, 10), nullable=True),
        sa.Column("ask", sa.Numeric(28, 10), nullable=True),
        sa.Column("mark_price", sa.Numeric(28, 10), nullable=True),
        sa.Column("funding_rate", sa.Numeric(18, 12), nullable=True),
        sa.Column("open_interest", sa.Numeric(28, 10), nullable=True),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_market_snapshots")),
    )
    op.create_index(op.f("ix_market_snapshots_symbol"), "market_snapshots", ["symbol"])
    op.create_index(op.f("ix_market_snapshots_captured_at"), "market_snapshots", ["captured_at"])

    op.create_table(
        "account_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("broker", sa.String(20), nullable=False),
        sa.Column("equity", sa.Numeric(28, 10), nullable=False),
        sa.Column("balance", sa.Numeric(28, 10), nullable=False),
        sa.Column("margin_used", sa.Numeric(28, 10), nullable=False),
        sa.Column("available_margin", sa.Numeric(28, 10), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_account_snapshots")),
    )
    op.create_index(op.f("ix_account_snapshots_broker"), "account_snapshots", ["broker"])
    op.create_index(op.f("ix_account_snapshots_captured_at"), "account_snapshots", ["captured_at"])

    op.create_table(
        "portfolio_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("gross_exposure", sa.Numeric(28, 10), nullable=False),
        sa.Column("net_exposure", sa.Numeric(28, 10), nullable=False),
        sa.Column("heat", sa.Numeric(12, 8), nullable=False),
        sa.Column("open_positions", sa.Integer(), nullable=False),
        sa.Column("details", postgresql.JSONB(), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_portfolio_snapshots")),
    )
    op.create_index(op.f("ix_portfolio_snapshots_captured_at"), "portfolio_snapshots", ["captured_at"])

    op.create_table(
        "system_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("aggregate_type", sa.String(50), nullable=True),
        sa.Column("aggregate_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("correlation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("causation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_system_events")),
    )
    for col in ["event_type", "aggregate_type", "aggregate_id", "severity", "correlation_id", "occurred_at"]:
        op.create_index(op.f(f"ix_system_events_{col}"), "system_events", [col])

    op.create_table(
        "audit_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor", sa.String(100), nullable=False),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("resource_type", sa.String(50), nullable=False),
        sa.Column("resource_id", sa.String(100), nullable=True),
        sa.Column("before", postgresql.JSONB(), nullable=True),
        sa.Column("after", postgresql.JSONB(), nullable=True),
        sa.Column("ip_address", postgresql.INET(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_logs")),
    )
    for col in ["action", "resource_type", "resource_id", "occurred_at"]:
        op.create_index(op.f(f"ix_audit_logs_{col}"), "audit_logs", [col])

    op.create_table(
        "safety_incidents",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("incident_code", sa.String(100), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("summary", sa.String(250), nullable=False),
        sa.Column("details", postgresql.JSONB(), nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_safety_incidents")),
    )
    for col in ["incident_code", "severity", "status"]:
        op.create_index(op.f(f"ix_safety_incidents_{col}"), "safety_incidents", [col])

    op.create_table(
        "configuration_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version_name", sa.String(100), nullable=False),
        sa.Column("checksum", sa.String(128), nullable=False),
        sa.Column("configuration", postgresql.JSONB(), nullable=False),
        sa.Column("created_by", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_configuration_versions")),
        sa.UniqueConstraint("version_name", name=op.f("uq_configuration_versions_version_name")),
        sa.UniqueConstraint("checksum", name=op.f("uq_configuration_versions_checksum")),
    )

    op.create_table(
        "timeframe_analyses",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("analysis_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("timeframe", sa.String(10), nullable=False),
        sa.Column("trend", sa.String(32), nullable=False),
        sa.Column("volatility", sa.String(32), nullable=False),
        sa.Column("indicators", postgresql.JSONB(), nullable=False),
        sa.Column("structure", postgresql.JSONB(), nullable=False),
        sa.Column("quality", postgresql.JSONB(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["analysis_run_id"], ["analysis_runs.id"], name=op.f("fk_timeframe_analyses_analysis_run_id_analysis_runs"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_timeframe_analyses")),
    )
    op.create_index(op.f("ix_timeframe_analyses_analysis_run_id"), "timeframe_analyses", ["analysis_run_id"])

    op.create_table(
        "strategy_evaluations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("analysis_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("strategy_name", sa.String(100), nullable=False),
        sa.Column("direction", sa.String(16), nullable=False),
        sa.Column("eligible", sa.Boolean(), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("completion_ratio", sa.Numeric(6, 5), nullable=False),
        sa.Column("passed_conditions", postgresql.JSONB(), nullable=False),
        sa.Column("failed_conditions", postgresql.JSONB(), nullable=False),
        sa.Column("vetoes", postgresql.JSONB(), nullable=False),
        sa.Column("score_breakdown", postgresql.JSONB(), nullable=False),
        sa.Column("config_version", sa.String(64), nullable=False),
        *_timestamps(),
        sa.CheckConstraint("score >= 0 AND score <= 100", name=op.f("ck_strategy_evaluations_score_range")),
        sa.CheckConstraint("completion_ratio >= 0 AND completion_ratio <= 1", name=op.f("ck_strategy_evaluations_completion_ratio_range")),
        sa.ForeignKeyConstraint(["analysis_run_id"], ["analysis_runs.id"], name=op.f("fk_strategy_evaluations_analysis_run_id_analysis_runs"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_strategy_evaluations")),
    )
    op.create_index(op.f("ix_strategy_evaluations_analysis_run_id"), "strategy_evaluations", ["analysis_run_id"])
    op.create_index(op.f("ix_strategy_evaluations_strategy_name"), "strategy_evaluations", ["strategy_name"])

    op.create_table(
        "trade_candidates",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("strategy_evaluation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("client_candidate_id", sa.String(80), nullable=False),
        sa.Column("symbol", sa.String(40), nullable=False),
        sa.Column("side", sa.String(16), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("entry_price", sa.Numeric(28, 10), nullable=False),
        sa.Column("stop_loss", sa.Numeric(28, 10), nullable=False),
        sa.Column("take_profit", sa.Numeric(28, 10), nullable=False),
        sa.Column("risk_reward", sa.Numeric(12, 6), nullable=False),
        sa.Column("reasons", postgresql.JSONB(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.CheckConstraint("score >= 0 AND score <= 100", name=op.f("ck_trade_candidates_score_range")),
        sa.CheckConstraint("entry_price > 0", name=op.f("ck_trade_candidates_entry_positive")),
        sa.CheckConstraint("stop_loss > 0", name=op.f("ck_trade_candidates_stop_positive")),
        sa.CheckConstraint("take_profit > 0", name=op.f("ck_trade_candidates_take_profit_positive")),
        sa.CheckConstraint("risk_reward > 0", name=op.f("ck_trade_candidates_risk_reward_positive")),
        sa.ForeignKeyConstraint(["strategy_evaluation_id"], ["strategy_evaluations.id"], name=op.f("fk_trade_candidates_strategy_evaluation_id_strategy_evaluations"), ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_trade_candidates")),
        sa.UniqueConstraint("strategy_evaluation_id", name=op.f("uq_trade_candidates_strategy_evaluation_id")),
        sa.UniqueConstraint("client_candidate_id", name=op.f("uq_trade_candidates_client_candidate_id")),
    )
    op.create_index(op.f("ix_trade_candidates_symbol"), "trade_candidates", ["symbol"])
    op.create_index(op.f("ix_trade_candidates_status"), "trade_candidates", ["status"])

    op.create_table(
        "risk_decisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("candidate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("reason_codes", postgresql.JSONB(), nullable=False),
        sa.Column("requested_risk_pct", sa.Numeric(8, 5), nullable=False),
        sa.Column("approved_risk_pct", sa.Numeric(8, 5), nullable=False),
        sa.Column("approved_quantity", sa.Numeric(28, 10), nullable=False),
        sa.Column("account_equity", sa.Numeric(28, 10), nullable=True),
        sa.Column("max_loss_amount", sa.Numeric(28, 10), nullable=True),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        *_timestamps(),
        sa.CheckConstraint("requested_risk_pct >= 0", name=op.f("ck_risk_decisions_requested_risk_nonnegative")),
        sa.CheckConstraint("approved_risk_pct >= 0", name=op.f("ck_risk_decisions_approved_risk_nonnegative")),
        sa.CheckConstraint("approved_quantity >= 0", name=op.f("ck_risk_decisions_approved_quantity_nonnegative")),
        sa.ForeignKeyConstraint(["candidate_id"], ["trade_candidates.id"], name=op.f("fk_risk_decisions_candidate_id_trade_candidates"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_risk_decisions")),
    )
    op.create_index(op.f("ix_risk_decisions_candidate_id"), "risk_decisions", ["candidate_id"])
    op.create_index(op.f("ix_risk_decisions_decision"), "risk_decisions", ["decision"])

    op.create_table(
        "trade_lifecycles",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("candidate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("failure_code", sa.String(100), nullable=True),
        sa.Column("last_event_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        *_timestamps(),
        sa.CheckConstraint("version >= 1", name=op.f("ck_trade_lifecycles_version_positive")),
        sa.ForeignKeyConstraint(["candidate_id"], ["trade_candidates.id"], name=op.f("fk_trade_lifecycles_candidate_id_trade_candidates"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_trade_lifecycles")),
        sa.UniqueConstraint("candidate_id", name=op.f("uq_trade_lifecycles_candidate_id")),
    )
    op.create_index(op.f("ix_trade_lifecycles_state"), "trade_lifecycles", ["state"])

    op.create_table(
        "orders",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("lifecycle_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("candidate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("client_order_id", sa.String(80), nullable=False),
        sa.Column("exchange_order_id", sa.String(100), nullable=True),
        sa.Column("broker", sa.String(20), nullable=False),
        sa.Column("symbol", sa.String(40), nullable=False),
        sa.Column("side", sa.String(16), nullable=False),
        sa.Column("order_type", sa.String(32), nullable=False),
        sa.Column("purpose", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("requested_quantity", sa.Numeric(28, 10), nullable=False),
        sa.Column("filled_quantity", sa.Numeric(28, 10), nullable=False),
        sa.Column("price", sa.Numeric(28, 10), nullable=True),
        sa.Column("average_fill_price", sa.Numeric(28, 10), nullable=True),
        sa.Column("reduce_only", sa.Boolean(), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.CheckConstraint("requested_quantity > 0", name=op.f("ck_orders_requested_quantity_positive")),
        sa.CheckConstraint("filled_quantity >= 0", name=op.f("ck_orders_filled_quantity_nonnegative")),
        sa.ForeignKeyConstraint(["candidate_id"], ["trade_candidates.id"], name=op.f("fk_orders_candidate_id_trade_candidates"), ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["lifecycle_id"], ["trade_lifecycles.id"], name=op.f("fk_orders_lifecycle_id_trade_lifecycles"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_orders")),
        sa.UniqueConstraint("client_order_id", name=op.f("uq_orders_client_order_id")),
        sa.UniqueConstraint("exchange_order_id", name=op.f("uq_orders_exchange_order_id")),
    )
    for col in ["lifecycle_id", "candidate_id", "symbol", "status"]:
        op.create_index(op.f(f"ix_orders_{col}"), "orders", [col])

    op.create_table(
        "fills",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("exchange_fill_id", sa.String(100), nullable=True),
        sa.Column("price", sa.Numeric(28, 10), nullable=False),
        sa.Column("quantity", sa.Numeric(28, 10), nullable=False),
        sa.Column("fee", sa.Numeric(28, 10), nullable=False),
        sa.Column("fee_currency", sa.String(20), nullable=True),
        sa.Column("filled_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("price > 0", name=op.f("ck_fills_price_positive")),
        sa.CheckConstraint("quantity > 0", name=op.f("ck_fills_quantity_positive")),
        sa.CheckConstraint("fee >= 0", name=op.f("ck_fills_fee_nonnegative")),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], name=op.f("fk_fills_order_id_orders"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_fills")),
        sa.UniqueConstraint("exchange_fill_id", name=op.f("uq_fills_exchange_fill_id")),
    )
    op.create_index(op.f("ix_fills_order_id"), "fills", ["order_id"])

    op.create_table(
        "positions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("lifecycle_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("symbol", sa.String(40), nullable=False),
        sa.Column("side", sa.String(16), nullable=False),
        sa.Column("broker", sa.String(20), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("quantity", sa.Numeric(28, 10), nullable=False),
        sa.Column("entry_price", sa.Numeric(28, 10), nullable=True),
        sa.Column("mark_price", sa.Numeric(28, 10), nullable=True),
        sa.Column("stop_loss", sa.Numeric(28, 10), nullable=True),
        sa.Column("take_profit", sa.Numeric(28, 10), nullable=True),
        sa.Column("realized_pnl", sa.Numeric(28, 10), nullable=False),
        sa.Column("unrealized_pnl", sa.Numeric(28, 10), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        *_timestamps(),
        sa.CheckConstraint("quantity >= 0", name=op.f("ck_positions_quantity_nonnegative")),
        sa.CheckConstraint("version >= 1", name=op.f("ck_positions_version_positive")),
        sa.ForeignKeyConstraint(["lifecycle_id"], ["trade_lifecycles.id"], name=op.f("fk_positions_lifecycle_id_trade_lifecycles"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_positions")),
        sa.UniqueConstraint("lifecycle_id", name=op.f("uq_positions_lifecycle_id")),
    )
    op.create_index(op.f("ix_positions_symbol"), "positions", ["symbol"])
    op.create_index(op.f("ix_positions_status"), "positions", ["status"])

    op.create_table(
        "protective_orders",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("position_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("protection_type", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("trigger_price", sa.Numeric(28, 10), nullable=False),
        sa.Column("quantity", sa.Numeric(28, 10), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.CheckConstraint("quantity > 0", name=op.f("ck_protective_orders_quantity_positive")),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], name=op.f("fk_protective_orders_order_id_orders"), ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["position_id"], ["positions.id"], name=op.f("fk_protective_orders_position_id_positions"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_protective_orders")),
        sa.UniqueConstraint("order_id", name=op.f("uq_protective_orders_order_id")),
    )
    op.create_index(op.f("ix_protective_orders_position_id"), "protective_orders", ["position_id"])

    op.create_table(
        "trades",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("lifecycle_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("candidate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("position_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("symbol", sa.String(40), nullable=False),
        sa.Column("side", sa.String(16), nullable=False),
        sa.Column("broker", sa.String(20), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("entry_price", sa.Numeric(28, 10), nullable=True),
        sa.Column("exit_price", sa.Numeric(28, 10), nullable=True),
        sa.Column("quantity", sa.Numeric(28, 10), nullable=False),
        sa.Column("gross_pnl", sa.Numeric(28, 10), nullable=False),
        sa.Column("net_pnl", sa.Numeric(28, 10), nullable=False),
        sa.Column("fees", sa.Numeric(28, 10), nullable=False),
        sa.Column("funding", sa.Numeric(28, 10), nullable=False),
        sa.Column("r_multiple", sa.Numeric(16, 8), nullable=True),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("close_reason", sa.String(100), nullable=True),
        sa.Column("snapshot", postgresql.JSONB(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["candidate_id"], ["trade_candidates.id"], name=op.f("fk_trades_candidate_id_trade_candidates"), ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["lifecycle_id"], ["trade_lifecycles.id"], name=op.f("fk_trades_lifecycle_id_trade_lifecycles"), ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["position_id"], ["positions.id"], name=op.f("fk_trades_position_id_positions"), ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_trades")),
        sa.UniqueConstraint("lifecycle_id", name=op.f("uq_trades_lifecycle_id")),
        sa.UniqueConstraint("position_id", name=op.f("uq_trades_position_id")),
    )
    op.create_index(op.f("ix_trades_candidate_id"), "trades", ["candidate_id"])
    op.create_index(op.f("ix_trades_symbol"), "trades", ["symbol"])
    op.create_index(op.f("ix_trades_status"), "trades", ["status"])


def downgrade() -> None:
    for table in [
        "trades", "protective_orders", "positions", "fills", "orders", "trade_lifecycles",
        "risk_decisions", "trade_candidates", "strategy_evaluations", "timeframe_analyses",
        "configuration_versions", "safety_incidents", "audit_logs", "system_events",
        "portfolio_snapshots", "account_snapshots", "market_snapshots", "analysis_runs",
    ]:
        op.drop_table(table)
