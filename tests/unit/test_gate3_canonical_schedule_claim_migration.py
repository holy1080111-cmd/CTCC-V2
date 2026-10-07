"""Offline shape and evidence-retaining downgrade checks for 0029.

PostgreSQL behavior belongs to the isolated 0029 integration case.
"""

from tests.durable_migration_fixtures import RecordedDowngrade, load_migration


def test_0029_static_ddl_and_orm_names_stay_aligned():
    import app.database.models  # noqa: F401 - register metadata
    from app.database.base import Base

    class Recorder:
        def __init__(self):
            self.sql: list[str] = []

        def execute(self, statement):
            self.sql.append(str(statement))

    migration = load_migration("0029")
    recorder = Recorder()
    migration.op = recorder
    migration.upgrade()
    ddl = "\n".join(recorder.sql)
    for name in (
        "gate3_capture_schedule_key_claims",
        "gate3_capture_schedule_claim_acks",
        "gate3_capture_schedule_legacy_inventory",
    ):
        table = Base.metadata.tables[name]
        assert f"CREATE TABLE public.{name}" in ddl
        for constraint in table.constraints:
            assert constraint.name in ddl
        for index in table.indexes:
            assert index.name in ddl
    raw = Base.metadata.tables["gate3_capture_schedule_pins"]
    assert not raw.c.seal_sha256.unique
    assert not raw.c.window_key.unique
    assert not raw.c.holdout_id.unique
    assert "gate3_schedule_legacy_classify" in ddl
    assert "gate3_schedule_legacy_inventory_immutable" in ddl
    assert "gate3_schedule_legacy_append_closed" in ddl
    assert "CREATE FUNCTION public.gate3_capture_schedule_claim_append" in ddl
    assert "gate3_schedule_claim_role_denied" in ddl


def test_0029_graph_and_empty_only_downgrade():
    migration = load_migration("0029")
    assert migration.revision == "0029"
    assert migration.down_revision == "0028"
    commands = RecordedDowngrade("0029").commands
    assert commands[0][0] == "execute"
    assert commands[0][1].startswith("LOCK TABLE public.gate3_capture_schedule_pins,")
    assert commands[0][1].endswith("IN ACCESS EXCLUSIVE MODE NOWAIT")
    assert "gate3_schedule_claim_downgrade_requires_empty" in commands[1][1]
    assert ("drop", "gate3_capture_schedule_claim_acks") in commands
    assert ("drop", "gate3_capture_schedule_key_claims") in commands
    assert ("drop", "gate3_capture_schedule_legacy_inventory") in commands
    assert ("drop", "gate3_capture_schedule_pins") not in commands
    assert ("drop", "gate3_capture_schedule_publication_acks") not in commands
