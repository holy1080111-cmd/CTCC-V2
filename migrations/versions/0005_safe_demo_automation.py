"""Safe OKX Demo automation state and history.

Revision ID: 0005
Revises: 0004
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "demo_automation_state",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("armed", sa.Boolean(), nullable=False),
        sa.Column("emergency_stop", sa.Boolean(), nullable=False),
        sa.Column("locked", sa.Boolean(), nullable=False),
        sa.Column("lock_reasons", postgresql.JSONB(), nullable=False),
        sa.Column("session_date", sa.Date(), nullable=False),
        sa.Column("baseline_equity", sa.Numeric(28, 10), nullable=True),
        sa.Column("peak_equity", sa.Numeric(28, 10), nullable=True),
        sa.Column("daily_pnl", sa.Numeric(28, 10), nullable=False),
        sa.Column("trades_today", sa.Integer(), nullable=False),
        sa.Column("consecutive_losses", sa.Integer(), nullable=False),
        sa.Column("active_instrument_id", sa.String(40), nullable=True),
        sa.Column("active_client_order_id", sa.String(32), nullable=True),
        sa.Column("active_start_equity", sa.Numeric(28, 10), nullable=True),
        sa.Column("active_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_trade_closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(250), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("id = 1", name=op.f("ck_demo_automation_state_singleton_id")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_demo_automation_state")),
    )

    op.create_table(
        "demo_automation_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trigger", sa.String(16), nullable=False),
        sa.Column("execute", sa.Boolean(), nullable=False),
        sa.Column("result", postgresql.JSONB(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_demo_automation_runs")),
    )
    op.create_index(op.f("ix_demo_automation_runs_trigger"), "demo_automation_runs", ["trigger"])
    op.create_index(op.f("ix_demo_automation_runs_started_at"), "demo_automation_runs", ["started_at"])
    op.create_index(op.f("ix_demo_automation_runs_completed_at"), "demo_automation_runs", ["completed_at"])

    op.create_table(
        "demo_automation_fingerprints",
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("details", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("fingerprint", name=op.f("pk_demo_automation_fingerprints")),
    )
    op.create_index(
        op.f("ix_demo_automation_fingerprints_expires_at"),
        "demo_automation_fingerprints",
        ["expires_at"],
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_demo_automation_fingerprints_expires_at"), table_name="demo_automation_fingerprints")
    op.drop_table("demo_automation_fingerprints")
    op.drop_index(op.f("ix_demo_automation_runs_completed_at"), table_name="demo_automation_runs")
    op.drop_index(op.f("ix_demo_automation_runs_started_at"), table_name="demo_automation_runs")
    op.drop_index(op.f("ix_demo_automation_runs_trigger"), table_name="demo_automation_runs")
    op.drop_table("demo_automation_runs")
    op.drop_table("demo_automation_state")
