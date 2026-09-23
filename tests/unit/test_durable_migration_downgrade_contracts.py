"""Offline migration ordering contracts, not PostgreSQL acceptance.

The explicit schedule model injects a writer at the first DROP after the real
migration's guard. PostgreSQL's actual locks are tested separately on PostgreSQL.
"""

import re

import pytest

from tests.durable_migration_fixtures import DOWNGRADE_LOCKS, TABLES, RecordedDowngrade


@pytest.mark.parametrize("revision", TABLES)
def test_exclusive_nowait_locks_precede_any_read_or_drop(revision):
    commands = RecordedDowngrade(revision).commands
    kind, statement = commands[0]
    assert kind == "execute" and statement.startswith("LOCK TABLE ")
    assert statement.endswith("IN ACCESS EXCLUSIVE MODE NOWAIT")
    # DROP removes FK triggers on parent tables; acquire their locks up front.
    expected = DOWNGRADE_LOCKS[revision]
    actual = re.search(r"LOCK TABLE (.*?) IN ACCESS", statement, re.DOTALL)[1]
    assert tuple(item.strip() for item in actual.split(",")) == expected
    guard = commands[1][1]
    assert commands[1][0] == "execute" and "RAISE EXCEPTION" in guard
    assert (
        tuple(re.findall(r"EXISTS\s*\(SELECT 1 FROM (\w+)\)", guard))
        == TABLES[revision]
    )
    assert tuple(value for kind, value in commands if kind == "drop") == tuple(
        reversed(TABLES[revision])
    )


@pytest.mark.parametrize("revision", TABLES)
def test_scheduled_write_between_guard_and_drop_cannot_be_lost(revision):
    # This is a bounded schedule model using actual migration operations. It
    # cannot establish that PostgreSQL executes or enforces these statements.
    tables = {name: [] for name in TABLES[revision]}
    locked = set()
    writer_attempted = writer_blocked = lost_durable_record = False
    for kind, value in RecordedDowngrade(revision).commands:
        if kind == "execute" and value.startswith("LOCK TABLE "):
            names = re.search(r"LOCK TABLE (.*?) IN ACCESS", value, re.DOTALL)[1]
            locked.update(name.strip() for name in names.split(","))
        elif kind == "drop":
            if not writer_attempted:
                writer_attempted = True
                writer_blocked = TABLES[revision][0] in locked
                if not writer_blocked:
                    tables[TABLES[revision][0]].append("synthetic-durable-record")
            lost_durable_record |= bool(tables.pop(value))
    assert writer_attempted and writer_blocked and not lost_durable_record
