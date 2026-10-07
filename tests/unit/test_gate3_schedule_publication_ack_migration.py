"""Offline 0028 graph and evidence-retaining downgrade contract."""

from tests.durable_migration_fixtures import RecordedDowngrade, load_migration


def test_0028_revises_0027_and_empty_downgrade_locks_both_tables_first():
    migration = load_migration("0028")
    assert migration.revision == "0028"
    assert migration.down_revision == "0027"
    commands = RecordedDowngrade("0028").commands
    assert commands[0][0] == "execute"
    assert commands[0][1].startswith("LOCK TABLE public.gate3_capture_schedule_pins,")
    assert "public.gate3_capture_schedule_publication_acks" in commands[0][1]
    assert commands[0][1].endswith("IN ACCESS EXCLUSIVE MODE NOWAIT")
    assert commands[1][0] == "execute"
    assert "gate3_schedule_ack_downgrade_requires_empty" in commands[1][1]
    assert ("drop", "gate3_capture_schedule_publication_acks") in commands
    assert ("drop", "gate3_capture_schedule_pins") not in commands
