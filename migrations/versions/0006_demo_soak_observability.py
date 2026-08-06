"""Demo soak sessions and observability events.

Revision ID: 0006
Revises: 0005
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "demo_soak_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("execute", sa.Boolean(), nullable=False),
        sa.Column("symbols", postgresql.JSONB(), nullable=False),
        sa.Column("interval_seconds", sa.Integer(), nullable=False),
        sa.Column("duration_minutes", sa.Integer(), nullable=False),
        sa.Column("max_runs", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("planned_end_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("stopped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_runs", sa.Integer(), nullable=False),
        sa.Column("submitted_runs", sa.Integer(), nullable=False),
        sa.Column("dry_run_runs", sa.Integer(), nullable=False),
        sa.Column("blocked_runs", sa.Integer(), nullable=False),
        sa.Column("error_runs", sa.Integer(), nullable=False),
        sa.Column("consecutive_errors", sa.Integer(), nullable=False),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_outcome", sa.String(40), nullable=True),
        sa.Column("stop_reason", sa.String(120), nullable=True),
        sa.Column("last_error", sa.String(250), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_demo_soak_sessions")),
    )
    op.create_index(op.f("ix_demo_soak_sessions_state"), "demo_soak_sessions", ["state"])
    op.create_index(op.f("ix_demo_soak_sessions_started_at"), "demo_soak_sessions", ["started_at"])

    op.create_table(
        "demo_observability_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("code", sa.String(80), nullable=False),
        sa.Column("message", sa.String(250), nullable=False),
        sa.Column("details", postgresql.JSONB(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_demo_observability_events")),
    )
    op.create_index(op.f("ix_demo_observability_events_severity"), "demo_observability_events", ["severity"])
    op.create_index(op.f("ix_demo_observability_events_code"), "demo_observability_events", ["code"])
    op.create_index(op.f("ix_demo_observability_events_observed_at"), "demo_observability_events", ["observed_at"])


def downgrade() -> None:
    op.drop_index(op.f("ix_demo_observability_events_observed_at"), table_name="demo_observability_events")
    op.drop_index(op.f("ix_demo_observability_events_code"), table_name="demo_observability_events")
    op.drop_index(op.f("ix_demo_observability_events_severity"), table_name="demo_observability_events")
    op.drop_table("demo_observability_events")
    op.drop_index(op.f("ix_demo_soak_sessions_started_at"), table_name="demo_soak_sessions")
    op.drop_index(op.f("ix_demo_soak_sessions_state"), table_name="demo_soak_sessions")
    op.drop_table("demo_soak_sessions")
