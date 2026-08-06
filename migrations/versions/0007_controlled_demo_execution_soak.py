"""Controlled Demo execution-soak guardrails and session telemetry.

Revision ID: 0007
Revises: 0006
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "demo_soak_sessions",
        sa.Column("max_submissions", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "demo_soak_sessions",
        sa.Column("starting_equity", sa.Numeric(38, 18), nullable=True),
    )
    op.add_column(
        "demo_soak_sessions",
        sa.Column("latest_equity", sa.Numeric(38, 18), nullable=True),
    )
    op.add_column(
        "demo_soak_sessions",
        sa.Column("session_pnl", sa.Numeric(38, 18), nullable=False, server_default="0"),
    )
    op.add_column(
        "demo_soak_sessions",
        sa.Column(
            "max_drawdown_pct_observed",
            sa.Numeric(18, 12),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "demo_soak_sessions",
        sa.Column("protection_checks", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "demo_soak_sessions",
        sa.Column("protection_failures", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "demo_soak_sessions",
        sa.Column("active_position_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "demo_soak_sessions",
        sa.Column(
            "active_pending_order_count", sa.Integer(), nullable=False, server_default="0"
        ),
    )
    op.add_column(
        "demo_soak_sessions",
        sa.Column("active_algo_order_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "demo_soak_sessions",
        sa.Column("protection_verified", sa.Boolean(), nullable=True),
    )
    op.add_column(
        "demo_soak_sessions",
        sa.Column("auto_disarmed", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "demo_soak_sessions",
        sa.Column("safety_stop_reason", sa.String(120), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("demo_soak_sessions", "safety_stop_reason")
    op.drop_column("demo_soak_sessions", "auto_disarmed")
    op.drop_column("demo_soak_sessions", "protection_verified")
    op.drop_column("demo_soak_sessions", "active_algo_order_count")
    op.drop_column("demo_soak_sessions", "active_pending_order_count")
    op.drop_column("demo_soak_sessions", "active_position_count")
    op.drop_column("demo_soak_sessions", "protection_failures")
    op.drop_column("demo_soak_sessions", "protection_checks")
    op.drop_column("demo_soak_sessions", "max_drawdown_pct_observed")
    op.drop_column("demo_soak_sessions", "session_pnl")
    op.drop_column("demo_soak_sessions", "latest_equity")
    op.drop_column("demo_soak_sessions", "starting_equity")
    op.drop_column("demo_soak_sessions", "max_submissions")
