"""Static migration contract; isolated PostgreSQL must verify actual DDL."""

from sqlalchemy import CheckConstraint

from app.database.models.demo_automation import DemoAutomationState
from tests.durable_migration_fixtures import load_migration


class Operations:
    def __init__(self):
        self.calls = []

    def add_column(self, table, column):
        self.calls.append(("add_column", table, column))

    @staticmethod
    def f(name):
        return name

    def execute(self, sql):
        self.calls.append(("execute", str(sql)))

    def create_check_constraint(self, name, table, condition):
        self.calls.append(("check", name, table, condition))

    def drop_constraint(self, name, table, *, type_):
        self.calls.append(("drop_check", name, table, type_))

    def drop_column(self, table, column):
        self.calls.append(("drop_column", table, column))


def test_upgrade_latches_legacy_singleton_before_constraints():
    migration = load_migration("0034")
    operations = Operations()
    migration.op = operations
    migration.upgrade()

    columns = {
        call[2].name: call[2]
        for call in operations.calls
        if call[0] == "add_column" and call[1] == "demo_automation_state"
    }
    assert set(columns) == {"control_revision", "restart_latch_required"}
    assert all(not column.nullable for column in columns.values())
    assert all(column.server_default is not None for column in columns.values())
    upgrade_sql = next(
        value for action, value, *rest in operations.calls if action == "execute"
    )
    assert "armed = FALSE" in upgrade_sql
    assert "emergency_stop = TRUE" in upgrade_sql
    assert "locked = TRUE" in upgrade_sql
    assert "restart_latch_required = TRUE" in upgrade_sql
    assert operations.calls.index(
        next(call for call in operations.calls if call[0] == "execute")
    ) < operations.calls.index(
        next(call for call in operations.calls if call[0] == "check")
    )
    checks = {call[1]: call[3] for call in operations.calls if call[0] == "check"}
    assert checks == {
        "ck_demo_automation_state_control_revision_nonnegative": "control_revision >= 0",
        "ck_demo_automation_state_arm_stop_exclusive": "NOT (armed AND emergency_stop)",
        "ck_demo_automation_state_armed_requires_restart_latch": (
            "NOT armed OR restart_latch_required"
        ),
    }
    model_checks = {
        str(constraint.name): str(constraint.sqltext)
        for constraint in DemoAutomationState.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert model_checks == checks
    model_columns = DemoAutomationState.__table__.columns
    assert model_columns["control_revision"].server_default is not None
    assert model_columns["restart_latch_required"].server_default is not None
    sql_calls = [call[1] for call in operations.calls if call[0] == "execute"]
    assert sql_calls[1:] == [
        migration.CONTROL_GUARD_FUNCTION_SQL,
        migration.CONTROL_GUARD_TRIGGER_SQL,
    ]
    assert (
        "NEW.control_revision IS DISTINCT FROM OLD.control_revision + 1" in sql_calls[1]
    )
    assert (
        "OLD.restart_latch_required AND NOT NEW.restart_latch_required" in sql_calls[1]
    )


def test_downgrade_stops_control_before_dropping_cas_columns():
    migration = load_migration("0034")
    operations = Operations()
    migration.op = operations
    migration.downgrade()

    assert operations.calls[0][0] == "execute"
    stop_sql = operations.calls[0][1]
    assert "armed = FALSE" in stop_sql
    assert "emergency_stop = TRUE" in stop_sql
    assert "locked = TRUE" in stop_sql
    assert "control_revision = control_revision + 1" in stop_sql
    assert (
        "DROP TRIGGER demo_automation_control_revision_guard" in operations.calls[1][1]
    )
    assert (
        "DROP FUNCTION demo_automation_control_revision_guard()"
        in operations.calls[2][1]
    )
    assert [call[2] for call in operations.calls if call[0] == "drop_column"] == [
        "restart_latch_required",
        "control_revision",
    ]
    assert len([call for call in operations.calls if call[0] == "drop_check"]) == 3
