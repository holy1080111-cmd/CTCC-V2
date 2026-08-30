"""Durable fail-closed OKX Live execution intents.

Revision ID: 0010
Revises: 0009
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "okx_live_execution_intents",
        sa.Column("idempotency_key", sa.String(32), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("instrument_id", sa.String(40), nullable=False),
        sa.Column("client_order_id", sa.String(32), nullable=True),
        sa.Column("exchange_order_id", sa.String(100), nullable=True),
        sa.Column(
            "detail_codes",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "action IN ('place_order','cancel_order','close_position','set_leverage')",
            name=op.f("ck_okx_live_execution_intents_action_allowed"),
        ),
        sa.CheckConstraint(
            "status IN ('reserved','acknowledged','confirmed','ambiguous','rejected')",
            name=op.f("ck_okx_live_execution_intents_status_allowed"),
        ),
        sa.PrimaryKeyConstraint(
            "idempotency_key",
            name=op.f("pk_okx_live_execution_intents"),
        ),
    )
    for column in (
        "action",
        "status",
        "instrument_id",
        "client_order_id",
        "exchange_order_id",
    ):
        op.create_index(
            op.f(f"ix_okx_live_execution_intents_{column}"),
            "okx_live_execution_intents",
            [column],
        )


def downgrade() -> None:
    for column in (
        "exchange_order_id",
        "client_order_id",
        "instrument_id",
        "status",
        "action",
    ):
        op.drop_index(
            op.f(f"ix_okx_live_execution_intents_{column}"),
            table_name="okx_live_execution_intents",
        )
    op.drop_table("okx_live_execution_intents")
