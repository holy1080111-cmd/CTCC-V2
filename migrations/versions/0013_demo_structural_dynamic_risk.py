"""Persist rolling Demo risk evidence and the non-daily equity high-water mark.

Revision ID: 0013
Revises: 0012
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0013"
down_revision: Union[str, None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "demo_automation_state",
        sa.Column("risk_peak_equity", sa.Numeric(28, 10), nullable=True),
    )
    op.add_column(
        "demo_automation_state",
        sa.Column(
            "realized_pnl_events",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.execute(
        "UPDATE demo_automation_state "
        "SET risk_peak_equity = COALESCE(peak_equity, baseline_equity) "
        "WHERE risk_peak_equity IS NULL"
    )
    op.alter_column(
        "demo_automation_state",
        "realized_pnl_events",
        server_default=None,
    )


def downgrade() -> None:
    op.drop_column("demo_automation_state", "realized_pnl_events")
    op.drop_column("demo_automation_state", "risk_peak_equity")
