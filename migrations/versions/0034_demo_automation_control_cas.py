"""Version Demo automation control writes and latch previously armed sessions.

Revision ID: 0034
Revises: 0033
"""

import sqlalchemy as sa
from alembic import op

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None

CONTROL_GUARD_FUNCTION_SQL = """
CREATE FUNCTION demo_automation_control_revision_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.control_revision IS DISTINCT FROM OLD.control_revision + 1 THEN
        RAISE EXCEPTION 'demo_automation_control_revision_must_advance'
            USING ERRCODE = '23514';
    END IF;
    IF OLD.restart_latch_required AND NOT NEW.restart_latch_required THEN
        RAISE EXCEPTION 'demo_automation_restart_latch_cannot_clear'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$
"""

CONTROL_GUARD_TRIGGER_SQL = """
CREATE TRIGGER demo_automation_control_revision_guard
BEFORE UPDATE ON demo_automation_state
FOR EACH ROW EXECUTE FUNCTION demo_automation_control_revision_guard()
"""


def upgrade() -> None:
    op.add_column(
        "demo_automation_state",
        sa.Column(
            "control_revision",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "demo_automation_state",
        sa.Column(
            "restart_latch_required",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    # An existing singleton has unknown prior Arm / clear history. Upgrade it
    # conservatively; an operator must reconcile and explicitly clear it.
    op.execute("""
        UPDATE demo_automation_state
           SET armed = FALSE,
               emergency_stop = TRUE,
               locked = TRUE,
               lock_reasons = COALESCE(lock_reasons, '[]'::jsonb)
                   || '["control_protocol_upgrade_requires_clear"]'::jsonb,
               restart_latch_required = TRUE
         WHERE id = 1
    """)
    op.create_check_constraint(
        op.f("ck_demo_automation_state_control_revision_nonnegative"),
        "demo_automation_state",
        "control_revision >= 0",
    )
    op.create_check_constraint(
        op.f("ck_demo_automation_state_arm_stop_exclusive"),
        "demo_automation_state",
        "NOT (armed AND emergency_stop)",
    )
    op.create_check_constraint(
        op.f("ck_demo_automation_state_armed_requires_restart_latch"),
        "demo_automation_state",
        "NOT armed OR restart_latch_required",
    )
    # A pre-0034 writer omits the new revision column. Reject its update at
    # the database boundary even when it bypasses the new Python repository.
    op.execute(CONTROL_GUARD_FUNCTION_SQL)
    op.execute(CONTROL_GUARD_TRIGGER_SQL)


def downgrade() -> None:
    # The older implementation has no CAS, so keep its visible control state
    # stopped before removing the version columns.
    op.execute("""
        UPDATE demo_automation_state
           SET armed = FALSE,
               emergency_stop = TRUE,
               locked = TRUE,
               control_revision = control_revision + 1,
               lock_reasons = COALESCE(lock_reasons, '[]'::jsonb)
                   || '["control_protocol_downgrade_requires_clear"]'::jsonb
         WHERE id = 1
    """)
    op.execute(
        "DROP TRIGGER demo_automation_control_revision_guard ON demo_automation_state"
    )
    op.execute("DROP FUNCTION demo_automation_control_revision_guard()")
    for constraint in (
        "ck_demo_automation_state_armed_requires_restart_latch",
        "ck_demo_automation_state_arm_stop_exclusive",
        "ck_demo_automation_state_control_revision_nonnegative",
    ):
        op.drop_constraint(op.f(constraint), "demo_automation_state", type_="check")
    op.drop_column("demo_automation_state", "restart_latch_required")
    op.drop_column("demo_automation_state", "control_revision")
