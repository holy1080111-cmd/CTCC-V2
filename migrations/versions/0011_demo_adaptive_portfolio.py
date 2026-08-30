"""Persist score-tiered Demo portfolio automation state.

Revision ID: 0011
Revises: 0010
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "demo_automation_state",
        sa.Column(
            "active_trades",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(
        "demo_automation_state",
        sa.Column(
            "symbol_cooldowns",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )

    # Preserve a legacy in-flight Demo trade so the upgraded service can keep
    # monitoring it. Missing strategy/risk details deliberately remain zeroed
    # and therefore never grant additional portfolio capacity.
    op.execute(
        sa.text(
            """
            UPDATE demo_automation_state
            SET active_trades = jsonb_build_object(
                active_instrument_id,
                jsonb_build_object(
                    'instrument_id', active_instrument_id,
                    'tier', 'legacy',
                    'client_order_id', active_client_order_id,
                    'contracts', '0',
                    'leverage', 1,
                    'risk_budget_pct', '0',
                    'estimated_stop_loss_pct', '0',
                    'margin_allocation_pct', '0',
                    'estimated_margin', '0',
                    'start_equity', active_start_equity::text,
                    'started_at', COALESCE(active_started_at, updated_at)
                )
            )
            WHERE active_instrument_id IS NOT NULL
              AND active_trades = '{}'::jsonb
            """
        )
    )


def downgrade() -> None:
    op.drop_column("demo_automation_state", "symbol_cooldowns")
    op.drop_column("demo_automation_state", "active_trades")
