"""Exact metadata join semantics on synthetic catalog rows, not PG evidence.

SQLite executes the unchanged, portable relational fragment used by the
PostgreSQL admission. The two actual fresh PG references separately prove
native heap/index admission and nonempty attached TOAST in the real catalog.
"""

import sqlite3

import pytest

from scripts import retained0017_catalog_baseline_v1 as subject


@pytest.mark.parametrize(
    ("oid", "accepted"),
    (
        (100, True),  # Exact public parent -> TOAST heap.
        (101, True),  # Exact index -> that TOAST heap.
        (200, False),  # Foreign parent -> TOAST heap.
        (201, False),  # Index crosses to the foreign parent's TOAST heap.
        (300, False),  # Orphan heap, despite its pg_toast namespace.
        (301, False),  # Orphan index, despite a real pg_index row.
        (302, False),  # Index points directly to an ordinary public table.
        (400, False),  # Wrong namespace, despite a public parent pointer.
        (401, False),  # Index crosses to that wrong-namespace heap.
        (500, False),  # Wrong relation kind, despite a parent pointer.
    ),
)
def test_only_actual_public_parent_toast_allocations_are_exempt(oid, accepted):
    with sqlite3.connect(":memory:") as db:
        db.execute("ATTACH DATABASE ':memory:' AS pg_catalog")
        db.execute("CREATE TABLE pg_catalog.pg_namespace(oid INTEGER,nspname TEXT)")
        db.execute(
            "CREATE TABLE pg_catalog.pg_class(oid INTEGER,relnamespace INTEGER,"
            "relkind TEXT,reltoastrelid INTEGER)"
        )
        db.execute(
            "CREATE TABLE pg_catalog.pg_index(indexrelid INTEGER,indrelid INTEGER)"
        )
        db.executemany(
            "INSERT INTO pg_catalog.pg_namespace VALUES (?,?)",
            ((1, "public"), (2, "pg_toast"), (3, "foreign_schema")),
        )
        db.executemany(
            "INSERT INTO pg_catalog.pg_class VALUES (?,?,?,?)",
            (
                (10, 1, "r", 100),
                (20, 3, "r", 200),
                (30, 1, "r", 400),
                (40, 1, "r", 500),
                (100, 2, "t", 0),
                (101, 2, "i", 0),
                (200, 2, "t", 0),
                (201, 2, "i", 0),
                (300, 2, "t", 0),
                (301, 2, "i", 0),
                (302, 2, "i", 0),
                (400, 3, "t", 0),
                (401, 2, "i", 0),
                (500, 2, "r", 0),
            ),
        )
        db.executemany(
            "INSERT INTO pg_catalog.pg_index VALUES (?,?)",
            ((101, 100), (201, 200), (301, 300), (302, 10), (401, 400)),
        )
        actual = db.execute(
            "SELECT CASE WHEN "
            + subject._SOURCE_TOAST_ALLOCATION_PREDICATE
            + " THEN 1 ELSE 0 END FROM pg_catalog.pg_class o "
            "JOIN pg_catalog.pg_namespace n ON n.oid=o.relnamespace WHERE o.oid=?",
            (oid,),
        ).fetchone()
        assert actual == (int(accepted),)
