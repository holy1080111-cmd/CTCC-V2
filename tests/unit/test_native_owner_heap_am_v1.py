"""Catalog-only admission order; no handler, row, storage or DB is invoked."""

import re
from types import SimpleNamespace

import pytest

from scripts import native_owner_heap_am_v1 as subject
from scripts import native_owner_retained_rows_v2 as rows
from scripts import native_owner_upgrade_0056_v1 as owner55
from scripts import retained0017_owner_plan_v1 as owner17


def test_all_row_and_marker_readers_bind_the_same_closed_builtin_heap_predicate():
    predicate = subject.HEAP_RELATION_PREDICATE_V1
    assert predicate in str(rows._LAYOUT_SQL)
    assert predicate in str(owner17.SAFE_ROW_TYPES_SQL)
    assert predicate in str(owner55._SAFE_MARKER_V2)
    assert predicate in subject.NATIVE_MARKER_AND_HEAP_SQL_V1
    assert "parent.reltoastrelid" in subject.ALL_PUBLIC_TABLES_HEAP_V1
    assert "c.relkind<>'t'" in subject.ALL_PUBLIC_TABLES_HEAP_V1
    assert (
        "pg_catalog.current_setting('default_table_access_method')='heap'"
        in subject.ALL_PUBLIC_TABLES_HEAP_V1
    )
    assert not re.search(r"\bSET\b", subject.ALL_PUBLIC_TABLES_HEAP_V1)
    assert not re.search(r"\bheap_tableam_handler\s*\(", predicate)
    assert "native_am.oid=2" in predicate and "native_handler.oid=3" in predicate
    assert (
        "native_handler.prolang=12" in predicate
        and "native_handler.prorettype=269" in predicate
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("observed", [False, None, 1, "true"])
async def test_unknown_or_foreign_am_catalog_refuses_before_marker_lock_or_head(
    observed,
):
    called = []

    class Connection:
        async def scalar(self, sql):
            called.append("CATALOG_ONLY")
            assert str(sql) == subject.NATIVE_MARKER_AND_HEAP_SQL_V1
            return observed

        async def execute(self, *args):
            pytest.fail("application relation was opened")

    with pytest.raises(subject.NativeOwnerStorageRefused):
        await subject.lock_native_owner_marker_v1(Connection())
    assert called == ["CATALOG_ONLY"]


@pytest.mark.asyncio
async def test_actual_catalog_result_is_rechecked_after_native_marker_lock():
    called = []

    class Connection:
        async def scalar(self, sql):
            called.append("CATALOG_ONLY")
            return True

        async def execute(self, sql):
            assert (
                str(sql)
                == "LOCK TABLE public.alembic_version IN ACCESS SHARE MODE NOWAIT"
            )
            called.append("MARKER_LOCK")

    await subject.lock_native_owner_marker_v1(Connection())
    assert called == ["CATALOG_ONLY", "MARKER_LOCK", "CATALOG_ONLY"]


@pytest.mark.asyncio
@pytest.mark.parametrize("reader", ["route", "owner57", "owner58", "owner55_roles"])
async def test_each_canonical_owner_reader_checks_storage_before_any_head_or_role_query(
    monkeypatch, reader
):
    from scripts import native_owner_route_preflight_0056_v1 as route
    from scripts import native_owner_upgrade_0057_v1 as owner57
    from scripts import native_owner_upgrade_0058_v1 as owner58

    calls = []

    class Context:
        async def __aenter__(self):
            return connection

        async def __aexit__(self, *args):
            return False

        def __await__(self):
            async def ready():
                return self

            return ready().__await__()

        async def rollback(self):
            calls.append("ROLLBACK")

    class Connection:
        def begin(self):
            return Context()

        async def exec_driver_sql(self, sql):
            assert sql.startswith("SET ")

        async def scalar(self, sql, *args):
            assert str(sql) in (
                subject.NATIVE_MARKER_AND_HEAP_SQL_V1,
                str(owner55._SAFE_MARKER_V2),
            )
            calls.append("CATALOG_ONLY")
            return False

        async def execute(self, sql):
            assert str(sql).startswith("SET "), (
                "head/role/application query ran before storage refusal"
            )

    connection = Connection()
    engine = SimpleNamespace(connect=lambda: Context())
    pins = SimpleNamespace(
        route=SimpleNamespace(owner="declared_owner", owner_oid=30, database_oid=40)
    )
    if reader == "owner58":
        monkeypatch.setattr(
            owner58, "async_sessionmaker", lambda *a, **k: lambda: Context()
        )
    with pytest.raises(
        (subject.NativeOwnerStorageRefused, owner55.NativeOwnerUpgradeRefusal)
    ):
        if reader == "route":
            await route._read_one(engine, pins)
        elif reader == "owner57":
            await owner57._owner_observation(engine, pins, "0056")
        elif reader == "owner58":
            await owner58._owner_observation(engine, pins, "0057")
        else:
            await owner55._role_observation_v2(object(), engine, pins, "0055")
    assert calls[0] == "CATALOG_ONLY"
