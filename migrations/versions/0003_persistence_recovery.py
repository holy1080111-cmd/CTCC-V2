"""Persistent paper state and restart recovery.

Revision ID: 0003
Revises: 0002
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "paper_account_state",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("starting_balance", sa.Numeric(28, 10), nullable=False),
        sa.Column("cash_balance", sa.Numeric(28, 10), nullable=False),
        sa.Column("equity", sa.Numeric(28, 10), nullable=False),
        sa.Column("realized_pnl", sa.Numeric(28, 10), nullable=False),
        sa.Column("unrealized_pnl", sa.Numeric(28, 10), nullable=False),
        sa.Column("fees_paid", sa.Numeric(28, 10), nullable=False),
        sa.Column("open_positions", sa.Integer(), nullable=False),
        sa.Column("pending_orders", sa.Integer(), nullable=False),
        sa.Column("closed_trades", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("state_checksum", sa.String(64), nullable=False),
        sa.Column("persisted_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("id = 1", name=op.f("ck_paper_account_state_singleton_id")),
        sa.CheckConstraint("starting_balance > 0", name=op.f("ck_paper_account_state_starting_balance_positive")),
        sa.CheckConstraint("revision >= 1", name=op.f("ck_paper_account_state_revision_positive")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_paper_account_state")),
    )

    op.create_table(
        "paper_order_state",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("client_order_id", sa.String(80), nullable=False),
        sa.Column("symbol", sa.String(40), nullable=False),
        sa.Column("side", sa.String(16), nullable=False),
        sa.Column("order_type", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("quantity", sa.Numeric(28, 10), nullable=False),
        sa.Column("reference_price", sa.Numeric(28, 10), nullable=False),
        sa.Column("limit_price", sa.Numeric(28, 10), nullable=True),
        sa.Column("average_fill_price", sa.Numeric(28, 10), nullable=True),
        sa.Column("stop_loss", sa.Numeric(28, 10), nullable=False),
        sa.Column("take_profit", sa.Numeric(28, 10), nullable=False),
        sa.Column("fee", sa.Numeric(28, 10), nullable=False),
        sa.Column("strategy", sa.String(100), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("reasons", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("filled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("persisted_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("quantity > 0", name=op.f("ck_paper_order_state_quantity_positive")),
        sa.CheckConstraint("reference_price > 0", name=op.f("ck_paper_order_state_reference_price_positive")),
        sa.CheckConstraint("stop_loss > 0", name=op.f("ck_paper_order_state_stop_loss_positive")),
        sa.CheckConstraint("take_profit > 0", name=op.f("ck_paper_order_state_take_profit_positive")),
        sa.CheckConstraint("fee >= 0", name=op.f("ck_paper_order_state_fee_nonnegative")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_paper_order_state")),
        sa.UniqueConstraint("client_order_id", name=op.f("uq_paper_order_state_client_order_id")),
    )
    op.create_index(op.f("ix_paper_order_state_symbol"), "paper_order_state", ["symbol"])
    op.create_index(op.f("ix_paper_order_state_status"), "paper_order_state", ["status"])

    op.create_table(
        "paper_position_state",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("symbol", sa.String(40), nullable=False),
        sa.Column("side", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("quantity", sa.Numeric(28, 10), nullable=False),
        sa.Column("entry_price", sa.Numeric(28, 10), nullable=False),
        sa.Column("mark_price", sa.Numeric(28, 10), nullable=False),
        sa.Column("stop_loss", sa.Numeric(28, 10), nullable=False),
        sa.Column("take_profit", sa.Numeric(28, 10), nullable=False),
        sa.Column("unrealized_pnl", sa.Numeric(28, 10), nullable=False),
        sa.Column("realized_pnl", sa.Numeric(28, 10), nullable=False),
        sa.Column("fees", sa.Numeric(28, 10), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("close_reason", sa.String(100), nullable=True),
        sa.Column("persisted_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("quantity > 0", name=op.f("ck_paper_position_state_quantity_positive")),
        sa.CheckConstraint("entry_price > 0", name=op.f("ck_paper_position_state_entry_price_positive")),
        sa.CheckConstraint("mark_price > 0", name=op.f("ck_paper_position_state_mark_price_positive")),
        sa.CheckConstraint("stop_loss > 0", name=op.f("ck_paper_position_state_stop_loss_positive")),
        sa.CheckConstraint("take_profit > 0", name=op.f("ck_paper_position_state_take_profit_positive")),
        sa.CheckConstraint("fees >= 0", name=op.f("ck_paper_position_state_fees_nonnegative")),
        sa.ForeignKeyConstraint(["order_id"], ["paper_order_state.id"], name=op.f("fk_paper_position_state_order_id_paper_order_state"), ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_paper_position_state")),
        sa.UniqueConstraint("order_id", name=op.f("uq_paper_position_state_order_id")),
    )
    op.create_index(op.f("ix_paper_position_state_symbol"), "paper_position_state", ["symbol"])
    op.create_index(op.f("ix_paper_position_state_status"), "paper_position_state", ["status"])

    op.create_table(
        "orchestrator_run_state",
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trigger", sa.String(16), nullable=False),
        sa.Column("execute", sa.Boolean(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("run_id", name=op.f("pk_orchestrator_run_state")),
    )
    op.create_index(op.f("ix_orchestrator_run_state_trigger"), "orchestrator_run_state", ["trigger"])
    op.create_index(op.f("ix_orchestrator_run_state_started_at"), "orchestrator_run_state", ["started_at"])
    op.create_index(op.f("ix_orchestrator_run_state_completed_at"), "orchestrator_run_state", ["completed_at"])

    op.create_table(
        "orchestrator_fingerprint_state",
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("details", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("fingerprint", name=op.f("pk_orchestrator_fingerprint_state")),
    )
    op.create_index(op.f("ix_orchestrator_fingerprint_state_expires_at"), "orchestrator_fingerprint_state", ["expires_at"])

    op.create_table(
        "recovery_checkpoints",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("state_checksum", sa.String(64), nullable=True),
        sa.Column("details", postgresql.JSONB(), nullable=False),
        sa.Column("recovered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("persisted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("id = 1", name=op.f("ck_recovery_checkpoints_singleton_id")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_recovery_checkpoints")),
    )


def downgrade() -> None:
    op.drop_table("recovery_checkpoints")
    op.drop_index(op.f("ix_orchestrator_fingerprint_state_expires_at"), table_name="orchestrator_fingerprint_state")
    op.drop_table("orchestrator_fingerprint_state")
    op.drop_index(op.f("ix_orchestrator_run_state_completed_at"), table_name="orchestrator_run_state")
    op.drop_index(op.f("ix_orchestrator_run_state_started_at"), table_name="orchestrator_run_state")
    op.drop_index(op.f("ix_orchestrator_run_state_trigger"), table_name="orchestrator_run_state")
    op.drop_table("orchestrator_run_state")
    op.drop_index(op.f("ix_paper_position_state_status"), table_name="paper_position_state")
    op.drop_index(op.f("ix_paper_position_state_symbol"), table_name="paper_position_state")
    op.drop_table("paper_position_state")
    op.drop_index(op.f("ix_paper_order_state_status"), table_name="paper_order_state")
    op.drop_index(op.f("ix_paper_order_state_symbol"), table_name="paper_order_state")
    op.drop_table("paper_order_state")
    op.drop_table("paper_account_state")
