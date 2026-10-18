"""Offline migration ordering contracts, not PostgreSQL acceptance.

The explicit schedule model injects a writer at the first DROP after the real
migration's guard. PostgreSQL's actual locks are tested separately on PostgreSQL.
"""

import re

import pytest

from tests.durable_migration_fixtures import DOWNGRADE_LOCKS, TABLES, RecordedDowngrade

EPOCH_TABLE = "public.demo_event_history_epoch_events"


def _migration_table_reference(revision, table):
    # The integration fixture retains unqualified catalog names. These
    # migrations deliberately lock their exact public-qualified references.
    return f"public.{table}" if revision in {"0043", "0044"} else table


@pytest.mark.parametrize("revision", TABLES)
def test_exclusive_nowait_locks_precede_any_read_or_drop(revision):
    commands = RecordedDowngrade(revision).commands
    kind, statement = commands[0]
    assert kind == "execute" and statement.startswith("LOCK TABLE ")
    assert statement.endswith("IN ACCESS EXCLUSIVE MODE NOWAIT")
    # DROP removes FK triggers on parent tables; acquire their locks up front.
    expected = (
        (EPOCH_TABLE,)
        if revision == "0041"
        else tuple(
            _migration_table_reference(revision, table)
            for table in DOWNGRADE_LOCKS[revision]
        )
    )
    actual = re.search(r"LOCK TABLE (.*?) IN ACCESS", statement, re.DOTALL)[1]
    assert tuple(item.strip() for item in actual.split(",")) == expected
    guard = commands[1][1]
    assert commands[1][0] == "execute" and "RAISE EXCEPTION" in guard
    guard_tables = (
        (EPOCH_TABLE,)
        if revision == "0041"
        else tuple(
            _migration_table_reference(revision, table) for table in TABLES[revision]
        )
    )
    assert (
        tuple(re.findall(r"EXISTS\s*\(SELECT 1 FROM ([\w.]+)\)", guard)) == guard_tables
    )
    if revision == "0041":
        assert (
            "RAISE EXCEPTION 'demo_event_history_epoch_downgrade_requires_empty'"
            in guard
        )
        assert commands[2:] == [
            ("execute", f"DROP TABLE {EPOCH_TABLE}"),
            ("execute", "DROP FUNCTION public.demo_event_history_epoch_insert_guard()"),
        ]
        return
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
    guard_seen = False
    for kind, value in RecordedDowngrade(revision).commands:
        if kind == "execute" and value.startswith("LOCK TABLE "):
            names = re.search(r"LOCK TABLE (.*?) IN ACCESS", value, re.DOTALL)[1]
            locked.update(name.strip() for name in names.split(","))
        elif kind == "execute" and "RAISE EXCEPTION" in value:
            guard_seen = True
        elif kind == "drop" or (
            revision == "0041"
            and kind == "execute"
            and value == f"DROP TABLE {EPOCH_TABLE}"
        ):
            assert guard_seen
            table = TABLES[revision][0] if revision == "0041" else value
            if not writer_attempted:
                writer_attempted = True
                locked_name = (
                    EPOCH_TABLE
                    if revision == "0041"
                    else _migration_table_reference(revision, TABLES[revision][0])
                )
                writer_blocked = locked_name in locked
                if not writer_blocked:
                    tables[TABLES[revision][0]].append("synthetic-durable-record")
            lost_durable_record |= bool(tables.pop(table))
    assert writer_attempted and writer_blocked and not lost_durable_record
