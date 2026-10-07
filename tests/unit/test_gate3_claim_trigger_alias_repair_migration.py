"""Offline invariants for the 0032 claim-guard repair on installed DBs."""

from tests.durable_migration_fixtures import RecordedDowngrade, load_migration


class Recorder:
    def __init__(self):
        self.sql: list[str] = []

    def execute(self, statement):
        self.sql.append(str(statement))


def test_0032_replaces_only_claim_guard_with_fresh_install_body():
    migration = load_migration("0032")
    assert migration.revision == "0032"
    assert migration.down_revision == "0031"
    repaired = Recorder()
    migration.op = repaired
    migration.upgrade()

    assert len(repaired.sql) == 3
    assert "IN ACCESS EXCLUSIVE MODE NOWAIT" in repaired.sql[0]
    assert (
        "CREATE OR REPLACE FUNCTION public.gate3_schedule_claim_insert_guard()"
        in (repaired.sql[1])
    )
    assert "legacy_row.classification" in repaired.sql[1]
    assert "old.classification" not in repaired.sql[1]
    assert repaired.sql[2].strip() == (
        "REVOKE ALL ON FUNCTION public.gate3_schedule_claim_insert_guard() FROM PUBLIC"
    )

    fresh = load_migration("0029")
    fresh_recorder = Recorder()
    fresh.op = fresh_recorder
    fresh.upgrade()
    fresh_body = next(
        sql
        for sql in fresh_recorder.sql
        if "CREATE FUNCTION public.gate3_schedule_claim_insert_guard()" in sql
    )
    assert (
        repaired.sql[1]
        .strip()
        .replace("CREATE OR REPLACE FUNCTION", "CREATE FUNCTION", 1)
        == fresh_body.strip()
    )
    ddl = "\n".join(repaired.sql).upper()
    for forbidden in ("UPDATE PUBLIC.", "DELETE FROM", "TRUNCATE", "DROP TABLE"):
        assert forbidden not in ddl


def test_0032_downgrade_requires_empty_and_keeps_safe_guard():
    commands = RecordedDowngrade("0032").commands
    assert len(commands) == 2
    assert commands[0][0] == "execute"
    assert commands[0][1].endswith("IN ACCESS EXCLUSIVE MODE NOWAIT")
    assert commands[1][0] == "execute"
    assert (
        "gate3_claim_trigger_alias_repair_downgrade_requires_empty" in (commands[1][1])
    )
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
