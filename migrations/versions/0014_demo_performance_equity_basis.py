"""Separate Demo strategy equity from multi-asset account equity.

Revision ID: 0014
Revises: 0013
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0014"
down_revision: Union[str, None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "demo_performance_snapshots",
        sa.Column("performance_equity", sa.Numeric(38, 18), nullable=True),
    )
    op.add_column(
        "demo_performance_snapshots",
        sa.Column("performance_available_equity", sa.Numeric(38, 18), nullable=True),
    )
    op.add_column(
        "demo_performance_snapshots",
        sa.Column("equity_basis", sa.String(length=40), nullable=True),
    )
    op.add_column(
        "demo_performance_snapshots",
        sa.Column("equity_currency", sa.String(length=16), nullable=True),
    )

    op.add_column(
        "demo_daily_performance_reports",
        sa.Column("performance_window_started_at", sa.DateTime(timezone=True)),
    )
    op.add_column(
        "demo_daily_performance_reports",
        sa.Column("equity_basis", sa.String(length=40)),
    )
    op.add_column(
        "demo_daily_performance_reports",
        sa.Column("equity_currency", sa.String(length=16)),
    )
    for name in (
        "performance_snapshot_count",
        "excluded_snapshot_count",
        "attributed_realized_trade_count",
        "unattributed_realized_trade_count",
    ):
        op.add_column(
            "demo_daily_performance_reports",
            sa.Column(
                name,
                sa.Integer(),
                nullable=False,
                server_default="0",
            ),
        )
    for name in (
        "account_opening_equity",
        "account_closing_equity",
        "account_equity_change",
    ):
        op.add_column(
            "demo_daily_performance_reports",
            sa.Column(name, sa.Numeric(38, 18), nullable=True),
        )
    op.add_column(
        "demo_daily_performance_reports",
        sa.Column(
            "account_max_drawdown_pct",
            sa.Numeric(18, 12),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    for name in (
        "account_max_drawdown_pct",
        "account_equity_change",
        "account_closing_equity",
        "account_opening_equity",
        "unattributed_realized_trade_count",
        "attributed_realized_trade_count",
        "excluded_snapshot_count",
        "performance_snapshot_count",
        "equity_currency",
        "equity_basis",
        "performance_window_started_at",
    ):
        op.drop_column("demo_daily_performance_reports", name)
    for name in (
        "equity_currency",
        "equity_basis",
        "performance_available_equity",
        "performance_equity",
    ):
        op.drop_column("demo_performance_snapshots", name)
