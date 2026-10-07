"""Offline invariants for the 0031 repair of already-applied Gate 3 SQL."""

from tests.durable_migration_fixtures import RecordedDowngrade, load_migration


class Recorder:
    def __init__(self):
        self.sql: list[str] = []

    def execute(self, statement):
        self.sql.append(str(statement))


def test_0031_only_replaces_broken_predicates_and_keeps_role_grants():
    migration = load_migration("0031")
    assert migration.revision == "0031"
    assert migration.down_revision == "0030"
    recorder = Recorder()
    migration.op = recorder
    migration.upgrade()
    assert len(recorder.sql) == 5
    assert "IN ACCESS EXCLUSIVE MODE NOWAIT" in recorder.sql[0]
    assert (
        "CREATE OR REPLACE FUNCTION public.gate3_schedule_all_ascii(p jsonb)"
        in (recorder.sql[1])
    )
    assert "DECLARE kind text; member record; ascii_value text;" in recorder.sql[1]
    assert "SELECT item.key,item.value" in recorder.sql[1]
    assert "SELECT item.value" in recorder.sql[1]
    assert (
        "CREATE OR REPLACE FUNCTION public.gate3_prereg_integer_numbers(p jsonb)"
        in (recorder.sql[2])
    )
    assert "SELECT item.value" in recorder.sql[2]
    for source_revision, function_name, repair_sql in (
        ("0029", "gate3_schedule_all_ascii", recorder.sql[1]),
        ("0030", "gate3_prereg_integer_numbers", recorder.sql[2]),
    ):
        source = load_migration(source_revision)
        source_recorder = Recorder()
        source.op = source_recorder
        source.upgrade()
        fresh_sql = next(
            sql
            for sql in source_recorder.sql
            if f"CREATE FUNCTION public.{function_name}(" in sql
        )
        assert (
            repair_sql.strip().replace(
                "CREATE OR REPLACE FUNCTION", "CREATE FUNCTION", 1
            )
            == fresh_sql.strip()
        )
    assert "REVOKE ALL ON FUNCTION public.gate3_schedule_all_ascii" in recorder.sql[3]
    assert (
        "REVOKE ALL ON FUNCTION public.gate3_prereg_integer_numbers"
        in (recorder.sql[4])
    )
    ddl = "\n".join(recorder.sql).upper()
    for forbidden in ("UPDATE PUBLIC.", "DELETE FROM", "TRUNCATE", "DROP TABLE"):
        assert forbidden not in ddl
    assert "GATE3_CAPTURE_SCHEDULE_LEGACY_INVENTORY" not in ddl


def test_0031_downgrade_requires_all_evidence_empty_and_keeps_repair():
    commands = RecordedDowngrade("0031").commands
    assert len(commands) == 2
    assert commands[0][0] == "execute"
    assert commands[0][1].endswith("IN ACCESS EXCLUSIVE MODE NOWAIT")
    assert commands[1][0] == "execute"
    assert "gate3_canonical_sql_repair_downgrade_requires_empty" in commands[1][1]
    for table in (
        "gate3_capture_schedule_pins",
        "gate3_capture_schedule_publication_acks",
        "gate3_capture_schedule_legacy_inventory",
        "gate3_capture_schedule_key_claims",
        "gate3_capture_schedule_claim_acks",
        "gate3_preregistration_seals",
        "gate3_preregistration_seal_acks",
    ):
        assert f"public.{table}" in commands[0][1]
        assert f"public.{table}" in commands[1][1]
    assert "CREATE OR REPLACE FUNCTION" not in commands[1][1]
