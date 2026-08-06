"""OKX Demo account mirror and reconciliation state.

Revision ID: 0004
Revises: 0003
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "okx_demo_balance_state",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("total_equity", sa.Numeric(28, 10), nullable=False),
        sa.Column("isolated_equity", sa.Numeric(28, 10), nullable=False),
        sa.Column("adjusted_equity", sa.Numeric(28, 10), nullable=False),
        sa.Column("available_equity", sa.Numeric(28, 10), nullable=False),
        sa.Column("details", postgresql.JSONB(), nullable=False),
        sa.Column("raw", postgresql.JSONB(), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("persisted_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("id = 1", name=op.f("ck_okx_demo_balance_state_singleton_id")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_okx_demo_balance_state")),
    )

    op.create_table(
        "okx_demo_order_state",
        sa.Column("order_id", sa.String(100), nullable=False),
        sa.Column("client_order_id", sa.String(32), nullable=True),
        sa.Column("instrument_id", sa.String(40), nullable=False),
        sa.Column("side", sa.String(16), nullable=False),
        sa.Column("position_side", sa.String(16), nullable=True),
        sa.Column("order_type", sa.String(32), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("size", sa.Numeric(28, 10), nullable=False),
        sa.Column("accumulated_fill_size", sa.Numeric(28, 10), nullable=False),
        sa.Column("price", sa.Numeric(28, 10), nullable=True),
        sa.Column("average_fill_price", sa.Numeric(28, 10), nullable=True),
        sa.Column("reduce_only", sa.Boolean(), nullable=False),
        sa.Column("attached_algo_orders", postgresql.JSONB(), nullable=False),
        sa.Column("raw", postgresql.JSONB(), nullable=False),
        sa.Column("exchange_created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("exchange_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("persisted_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("order_id", name=op.f("pk_okx_demo_order_state")),
        sa.UniqueConstraint("client_order_id", name=op.f("uq_okx_demo_order_state_client_order_id")),
    )
    op.create_index(op.f("ix_okx_demo_order_state_instrument_id"), "okx_demo_order_state", ["instrument_id"])
    op.create_index(op.f("ix_okx_demo_order_state_state"), "okx_demo_order_state", ["state"])

    op.create_table(
        "okx_demo_position_state",
        sa.Column("position_key", sa.String(80), nullable=False),
        sa.Column("instrument_id", sa.String(40), nullable=False),
        sa.Column("position_side", sa.String(16), nullable=False),
        sa.Column("size", sa.Numeric(28, 10), nullable=False),
        sa.Column("available_size", sa.Numeric(28, 10), nullable=False),
        sa.Column("average_price", sa.Numeric(28, 10), nullable=True),
        sa.Column("mark_price", sa.Numeric(28, 10), nullable=True),
        sa.Column("unrealized_pnl", sa.Numeric(28, 10), nullable=False),
        sa.Column("leverage", sa.Numeric(12, 4), nullable=True),
        sa.Column("margin_mode", sa.String(16), nullable=True),
        sa.Column("liquidation_price", sa.Numeric(28, 10), nullable=True),
        sa.Column("raw", postgresql.JSONB(), nullable=False),
        sa.Column("exchange_created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("exchange_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("persisted_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("position_key", name=op.f("pk_okx_demo_position_state")),
    )
    op.create_index(op.f("ix_okx_demo_position_state_instrument_id"), "okx_demo_position_state", ["instrument_id"])

    op.create_table(
        "okx_demo_algo_order_state",
        sa.Column("algo_order_id", sa.String(100), nullable=False),
        sa.Column("client_algo_order_id", sa.String(32), nullable=True),
        sa.Column("instrument_id", sa.String(40), nullable=False),
        sa.Column("order_type", sa.String(32), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("side", sa.String(16), nullable=True),
        sa.Column("position_side", sa.String(16), nullable=True),
        sa.Column("size", sa.Numeric(28, 10), nullable=False),
        sa.Column("take_profit_trigger_price", sa.Numeric(28, 10), nullable=True),
        sa.Column("stop_loss_trigger_price", sa.Numeric(28, 10), nullable=True),
        sa.Column("raw", postgresql.JSONB(), nullable=False),
        sa.Column("exchange_created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("exchange_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("persisted_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("algo_order_id", name=op.f("pk_okx_demo_algo_order_state")),
        sa.UniqueConstraint("client_algo_order_id", name=op.f("uq_okx_demo_algo_order_state_client_algo_order_id")),
    )
    op.create_index(op.f("ix_okx_demo_algo_order_state_instrument_id"), "okx_demo_algo_order_state", ["instrument_id"])
    op.create_index(op.f("ix_okx_demo_algo_order_state_state"), "okx_demo_algo_order_state", ["state"])

    op.create_table(
        "okx_demo_sync_checkpoints",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("order_count", sa.Integer(), nullable=False),
        sa.Column("position_count", sa.Integer(), nullable=False),
        sa.Column("algo_order_count", sa.Integer(), nullable=False),
        sa.Column("details", postgresql.JSONB(), nullable=False),
        sa.Column("last_error", sa.String(250), nullable=True),
        sa.Column("reconciled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("id = 1", name=op.f("ck_okx_demo_sync_checkpoints_singleton_id")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_okx_demo_sync_checkpoints")),
    )


def downgrade() -> None:
    op.drop_table("okx_demo_sync_checkpoints")
    op.drop_index(op.f("ix_okx_demo_algo_order_state_state"), table_name="okx_demo_algo_order_state")
    op.drop_index(op.f("ix_okx_demo_algo_order_state_instrument_id"), table_name="okx_demo_algo_order_state")
    op.drop_table("okx_demo_algo_order_state")
    op.drop_index(op.f("ix_okx_demo_position_state_instrument_id"), table_name="okx_demo_position_state")
    op.drop_table("okx_demo_position_state")
    op.drop_index(op.f("ix_okx_demo_order_state_state"), table_name="okx_demo_order_state")
    op.drop_index(op.f("ix_okx_demo_order_state_instrument_id"), table_name="okx_demo_order_state")
    op.drop_table("okx_demo_order_state")
    op.drop_table("okx_demo_balance_state")
