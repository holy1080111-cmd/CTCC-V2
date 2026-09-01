"""Persist operator reconciliation for unresolved OKX Live intents.

Revision ID: 0016
Revises: 0015
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0016"
down_revision: Union[str, None] = "0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "okx_live_sync_checkpoints",
        sa.Column(
            "safety_latched",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.add_column(
        "okx_live_sync_checkpoints",
        sa.Column("safety_latch_code", sa.String(length=80), nullable=True),
    )
    op.add_column(
        "okx_live_sync_checkpoints",
        sa.Column(
            "safety_latch_version",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )
    op.add_column(
        "okx_live_sync_checkpoints",
        sa.Column("safety_latched_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        op.f("ck_okx_live_sync_checkpoints_safety_latch_pair"),
        "okx_live_sync_checkpoints",
        "(NOT safety_latched AND safety_latch_code IS NULL AND "
        "safety_latched_at IS NULL) OR "
        "(safety_latched AND safety_latch_code IS NOT NULL AND "
        "safety_latched_at IS NOT NULL)",
    )
    op.create_check_constraint(
        op.f("ck_okx_live_sync_checkpoints_safety_latch_version_nonnegative"),
        "okx_live_sync_checkpoints",
        "safety_latch_version >= 0",
    )
    op.create_check_constraint(
        op.f("ck_okx_live_sync_checkpoints_safety_latch_code_safe"),
        "okx_live_sync_checkpoints",
        "safety_latch_code IS NULL OR "
        "safety_latch_code ~ '^[a-z0-9_]{1,80}$'",
    )
    op.add_column(
        "okx_live_execution_intents",
        sa.Column(
            "protection_client_order_id",
            sa.String(length=32),
            nullable=True,
        ),
    )
    op.create_unique_constraint(
        op.f("uq_okx_live_execution_intents_protection_client_order_id"),
        "okx_live_execution_intents",
        ["protection_client_order_id"],
    )
    op.add_column(
        "okx_live_execution_intents",
        sa.Column(
            "expected_protection_size",
            sa.Numeric(precision=28, scale=10),
            nullable=True,
        ),
    )
    op.add_column(
        "okx_live_execution_intents",
        sa.Column(
            "expected_stop_loss",
            sa.Numeric(precision=28, scale=10),
            nullable=True,
        ),
    )
    op.add_column(
        "okx_live_execution_intents",
        sa.Column(
            "expected_take_profit",
            sa.Numeric(precision=28, scale=10),
            nullable=True,
        ),
    )
    op.add_column(
        "okx_live_execution_intents",
        sa.Column(
            "expected_trigger_price_type",
            sa.String(length=16),
            nullable=True,
        ),
    )
    op.add_column(
        "okx_live_execution_intents",
        sa.Column(
            "operator_reconciled_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "okx_live_execution_intents",
        sa.Column(
            "operator_resolution_code",
            sa.String(length=80),
            nullable=True,
        ),
    )
    op.create_check_constraint(
        op.f("ck_okx_live_execution_intents_protection_expectation_complete"),
        "okx_live_execution_intents",
        "(protection_client_order_id IS NULL AND "
        "expected_protection_size IS NULL AND expected_stop_loss IS NULL "
        "AND expected_take_profit IS NULL AND "
        "expected_trigger_price_type IS NULL) OR "
        "(action = 'place_order' AND protection_client_order_id IS NOT NULL "
        "AND expected_protection_size IS NOT NULL "
        "AND expected_stop_loss IS NOT NULL "
        "AND expected_take_profit IS NOT NULL "
        "AND expected_trigger_price_type IS NOT NULL)",
    )
    op.create_check_constraint(
        op.f("ck_okx_live_execution_intents_operator_resolution_pair"),
        "okx_live_execution_intents",
        "(operator_reconciled_at IS NULL AND operator_resolution_code IS NULL) "
        "OR (operator_reconciled_at IS NOT NULL AND "
        "operator_resolution_code IS NOT NULL)",
    )
    op.create_check_constraint(
        op.f("ck_okx_live_execution_intents_operator_resolution_allowed"),
        "okx_live_execution_intents",
        "operator_resolution_code IS NULL OR "
        "(status IN ('reserved','acknowledged','ambiguous') AND "
        "operator_resolution_code = "
        "'operator_confirmed_flat_exchange_state')",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("uq_okx_live_execution_intents_protection_client_order_id"),
        "okx_live_execution_intents",
        type_="unique",
    )
    op.drop_constraint(
        op.f("ck_okx_live_execution_intents_protection_expectation_complete"),
        "okx_live_execution_intents",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_okx_live_execution_intents_operator_resolution_allowed"),
        "okx_live_execution_intents",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_okx_live_execution_intents_operator_resolution_pair"),
        "okx_live_execution_intents",
        type_="check",
    )
    op.drop_column(
        "okx_live_execution_intents",
        "operator_resolution_code",
    )
    op.drop_column(
        "okx_live_execution_intents",
        "operator_reconciled_at",
    )
    op.drop_column(
        "okx_live_execution_intents",
        "expected_trigger_price_type",
    )
    op.drop_column(
        "okx_live_execution_intents",
        "expected_take_profit",
    )
    op.drop_column(
        "okx_live_execution_intents",
        "expected_stop_loss",
    )
    op.drop_column(
        "okx_live_execution_intents",
        "expected_protection_size",
    )
    op.drop_column(
        "okx_live_execution_intents",
        "protection_client_order_id",
    )
    op.drop_constraint(
        op.f("ck_okx_live_sync_checkpoints_safety_latch_pair"),
        "okx_live_sync_checkpoints",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_okx_live_sync_checkpoints_safety_latch_version_nonnegative"),
        "okx_live_sync_checkpoints",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_okx_live_sync_checkpoints_safety_latch_code_safe"),
        "okx_live_sync_checkpoints",
        type_="check",
    )
    op.drop_column("okx_live_sync_checkpoints", "safety_latched_at")
    op.drop_column("okx_live_sync_checkpoints", "safety_latch_version")
    op.drop_column("okx_live_sync_checkpoints", "safety_latch_code")
    op.drop_column("okx_live_sync_checkpoints", "safety_latched")
