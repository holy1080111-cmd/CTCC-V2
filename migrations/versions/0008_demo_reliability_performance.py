"""Demo reliability snapshots, daily reports, and strategy controls.

Revision ID: 0008
Revises: 0007
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "demo_performance_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("total_equity", sa.Numeric(38, 18), nullable=False),
        sa.Column("available_equity", sa.Numeric(38, 18), nullable=False),
        sa.Column("unrealized_pnl", sa.Numeric(38, 18), nullable=False, server_default="0"),
        sa.Column("position_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("pending_order_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("algo_order_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("details", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("persisted_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_demo_performance_snapshots")),
    )
    op.create_index(
        op.f("ix_demo_performance_snapshots_captured_at"),
        "demo_performance_snapshots",
        ["captured_at"],
    )

    op.create_table(
        "demo_strategy_controls",
        sa.Column("strategy", sa.String(80), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("reason", sa.String(250), nullable=True),
        sa.Column("updated_by", sa.String(80), nullable=False, server_default="system"),
        sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("strategy", name=op.f("pk_demo_strategy_controls")),
    )
    op.create_index(
        op.f("ix_demo_strategy_controls_enabled"),
        "demo_strategy_controls",
        ["enabled"],
    )

    op.create_table(
        "demo_daily_performance_reports",
        sa.Column("report_date", sa.Date(), nullable=False),
        sa.Column("opening_equity", sa.Numeric(38, 18), nullable=True),
        sa.Column("closing_equity", sa.Numeric(38, 18), nullable=True),
        sa.Column("net_equity_change", sa.Numeric(38, 18), nullable=True),
        sa.Column("realized_pnl", sa.Numeric(38, 18), nullable=False, server_default="0"),
        sa.Column("fees", sa.Numeric(38, 18), nullable=False, server_default="0"),
        sa.Column("rebates", sa.Numeric(38, 18), nullable=False, server_default="0"),
        sa.Column("funding_fees", sa.Numeric(38, 18), nullable=False, server_default="0"),
        sa.Column("net_after_costs", sa.Numeric(38, 18), nullable=False, server_default="0"),
        sa.Column("order_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("filled_order_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("realized_trade_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("wins", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("losses", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("breakeven", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("win_rate", sa.Numeric(18, 12), nullable=True),
        sa.Column("profit_factor", sa.Numeric(38, 18), nullable=True),
        sa.Column("average_adverse_slippage_bps", sa.Numeric(18, 8), nullable=True),
        sa.Column("max_adverse_slippage_bps", sa.Numeric(18, 8), nullable=True),
        sa.Column("max_drawdown_pct", sa.Numeric(18, 12), nullable=False, server_default="0"),
        sa.Column("strategy_stats", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("alerts", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("generated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("report_date", name=op.f("pk_demo_daily_performance_reports")),
    )


def downgrade() -> None:
    op.drop_table("demo_daily_performance_reports")
    op.drop_index(op.f("ix_demo_strategy_controls_enabled"), table_name="demo_strategy_controls")
    op.drop_table("demo_strategy_controls")
    op.drop_index(
        op.f("ix_demo_performance_snapshots_captured_at"),
        table_name="demo_performance_snapshots",
    )
    op.drop_table("demo_performance_snapshots")
