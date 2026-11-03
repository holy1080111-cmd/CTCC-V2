"""Add the fixed native B1, B3 and B5 writer grants after core provisioning."""

from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.domain import account_history_original_source_catalog_v1 as original
from scripts import current0055_runtime_grants as core

_DELTA_TABLES = (
    # B1 journal read, append and independent committed readback.
    ("demo_account_capture_events", ("SELECT", "INSERT")),
    # B3 actual current55 history path uses the untyped observation repository.
    ("demo_account_observation_batches", ("SELECT", "INSERT")),
    ("demo_account_observation_facts", ("SELECT", "INSERT")),
    ("demo_account_observation_coverage", ("SELECT", "INSERT")),
    ("demo_account_observation_findings", ("SELECT", "INSERT")),
    # B5 checks the local mirror under SHARE and SHARE ROW EXCLUSIVE.
    ("demo_automation_state", ("SELECT", "MAINTAIN")),
    ("okx_demo_sync_checkpoints", ("SELECT", "INSERT", "UPDATE")),
    ("okx_demo_order_state", ("SELECT", "MAINTAIN")),
    ("okx_demo_position_state", ("SELECT", "MAINTAIN")),
    ("okx_demo_algo_order_state", ("SELECT", "MAINTAIN")),
    ("audit_logs", ("SELECT", "INSERT")),
)
_MIRROR_WRITE_TABLES = {"okx_demo_sync_checkpoints", "audit_logs"}
_SETTINGS = (
    "SET TRANSACTION ISOLATION LEVEL READ COMMITTED",
    "SET LOCAL search_path TO pg_catalog, public, pg_temp",
    "SET LOCAL TIME ZONE 'UTC'",
    "SET LOCAL lock_timeout='1000ms'",
    "SET LOCAL statement_timeout='10000ms'",
)
_ACTUAL_SESSION_SQL = text("""
SELECT r.oid::bigint,r.rolname::text,current_user=session_user,
       pg_catalog.current_setting('role')='none',r.rolcanlogin,r.rolsuper,
       d.oid::bigint,d.datname::text,pg_catalog.host(pg_catalog.inet_server_addr()),
       pg_catalog.inet_server_port(),pg_catalog.pg_postmaster_start_time(),
       pg_catalog.pg_backend_pid(),pg_catalog.txid_current()::text
FROM pg_catalog.pg_roles r,pg_catalog.pg_database d
WHERE r.rolname=current_user AND d.datname=pg_catalog.current_database()
""")
_DELTA_RELATION_SQL = text("""
SELECT c.relname,c.oid::bigint,c.relowner::bigint,c.relkind::text,
       c.relpersistence::text,c.relrowsecurity,c.relforcerowsecurity,
       c.relispartition,
       EXISTS(SELECT 1 FROM pg_catalog.pg_inherits i
              WHERE c.oid IN (i.inhrelid,i.inhparent)),
       EXISTS(SELECT 1 FROM pg_catalog.pg_rewrite r WHERE r.ev_class=c.oid),
       EXISTS(SELECT 1 FROM pg_catalog.pg_policy p WHERE p.polrelid=c.oid),
       EXISTS(SELECT 1 FROM pg_catalog.pg_attribute a
              CROSS JOIN LATERAL pg_catalog.aclexplode(a.attacl) acl
              WHERE a.attrelid=c.oid AND acl.grantee<>c.relowner),
       EXISTS(SELECT 1 FROM pg_catalog.pg_trigger t
              WHERE t.tgrelid=c.oid AND NOT t.tgisinternal)
FROM pg_catalog.pg_class c
JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
WHERE n.nspname='public' AND c.relname::text=ANY(CAST(:names AS text[]))
""")


def _require(condition: bool) -> None:
    if not condition:
        raise ValueError("current0055_native_account_source_acl_scope_changed")


def _plans(writer, custody, frame_reader):
    previous = core._plan(writer, custody, frame_reader)
    delta = tuple(
        core._Grant("TABLE", table, rights, writer) for table, rights in _DELTA_TABLES
    )
    core_targets = {item.target for item in previous if item.kind == "TABLE"}
    _require(len({item.target for item in delta}) == len(delta))
    _require(core_targets.isdisjoint(item.target for item in delta))
    return previous, delta


def current55_native_account_source_grant_sql_v1(
    *, writer: str, custody: str, frame_reader: str
) -> tuple[str, ...]:
    """Render only the new B1/B3/B5 statements for the nominated core writer."""
    _, delta = _plans(writer, custody, frame_reader)
    return tuple(item.sql() for item in delta)


async def _metadata(connection, *, owner):
    row = tuple((await connection.execute(_ACTUAL_SESSION_SQL)).one())
    _require(
        type(row[0]) is int
        and row[0] > 0
        and type(row[1]) is str
        and bool(row[1])
        and row[2:6] == (True, True, True, owner)
        and type(row[6]) is int
        and row[6] > 0
        and all(type(value) is str and value for value in row[7:9])
        and type(row[9]) is int
        and 1 <= row[9] <= 65535
        and type(row[10]) is datetime
        and row[10].tzinfo is not None
        and type(row[11]) is int
        and row[11] > 0
        and type(row[12]) is str
        and row[12].isascii()
        and row[12].isdecimal()
    )
    return row


async def _delta_relations(connection, expected, owner):
    names = [target for _, target in expected]
    rows = (await connection.execute(_DELTA_RELATION_SQL, {"names": names})).all()
    _require(len(rows) == len(names))
    actual = {}
    for row in rows:
        name, oid = row[:2]
        _require(
            ("TABLE", name) in expected
            and name not in actual
            and type(oid) is int
            and oid > 0
            and tuple(row[2:12])
            == (owner, "r", "p", False, False, False, False, False, False, False)
            # The two direct mirror write targets have no application triggers.
            and (name not in _MIRROR_WRITE_TABLES or row[12] is False)
        )
        actual[name] = oid
    return {("TABLE", name): oid for name, oid in actual.items()}


async def _observe_writer(connection, *, login, role_oid, owner_metadata):
    async with connection.begin():
        await connection.execute(text("SET TRANSACTION READ ONLY"))
        for statement in _SETTINGS:
            await connection.execute(text(statement))
        actual = await _metadata(connection, owner=False)
        _require(
            actual[:2] == (role_oid, login)
            and actual[6:11] == owner_metadata[6:11]
            and actual[11] != owner_metadata[11]
            and actual[12] != owner_metadata[12]
        )
        observed = core.catalog.verify_actual_readonly_writer_catalog(
            await connection.scalar(text(core.catalog.ACTOR_CATALOG_SQL))
        )
        _require(
            observed["database_head"] == "0055"
            and observed["role_oid"] == role_oid
            and observed["native_baseline_admission"] is True
            and observed["execution_authority"] is False
        )
        previous = await core.partition.read_account_history_partition_catalog(
            connection
        )
        source = await connection.scalar(text(original.CATALOG_SELECT))
        source_sha256 = original.verify_source_catalog(source, previous)
        return observed, source_sha256, actual[12]


async def provision_current55_native_account_source_acl_v1(
    owner_connection: AsyncConnection,
    writer_connection: AsyncConnection,
    *,
    writer: str,
    custody: str,
    frame_reader: str,
) -> dict:
    """Add the fixed delta on an exact core installation in one owner transaction."""
    previous, delta = _plans(writer, custody, frame_reader)
    _require(
        owner_connection is not writer_connection
        and all(
            type(connection) is AsyncConnection
            and connection.dialect.name == "postgresql"
            and connection.dialect.driver == "asyncpg"
            and not connection.in_transaction()
            for connection in (owner_connection, writer_connection)
        )
    )
    async with owner_connection.begin():
        for statement in _SETTINGS:
            await owner_connection.execute(text(statement))
        # Serialize with the exact core provisioner, without changing its ABI.
        await owner_connection.execute(
            text("SELECT pg_catalog.pg_advisory_xact_lock(5883109675522810055)")
        )
        owner_metadata = await _metadata(owner_connection, owner=True)
        owner = owner_metadata[0]
        _require(
            tuple(
                (
                    await owner_connection.execute(
                        core._OWNER_POLICY_SQL, {"owner": owner}
                    )
                ).one()
            )
            == (True, True)
        )
        names = (writer, custody, frame_reader)
        role_oids = await core._roles(owner_connection, names, owner)
        observed, source_sha256, writer_xid = await _observe_writer(
            writer_connection,
            login=writer,
            role_oid=role_oids[writer],
            owner_metadata=owner_metadata,
        )
        existing_acl = core._expected_acl(previous, role_oids, owner)
        existing = await core._target_oids(
            owner_connection, existing_acl, owner, granted=True
        )
        await core._access(
            owner_connection,
            previous,
            role_oids,
            granted=True,
            targets=existing,
        )
        delta_acl = core._expected_acl(delta, role_oids, owner)
        await owner_connection.exec_driver_sql(
            "LOCK TABLE "
            + ", ".join(f"public.{item.target}" for item in delta)
            + " IN ACCESS SHARE MODE NOWAIT"
        )
        additions = await core._target_oids(
            owner_connection,
            delta_acl,
            owner,
            granted=False,
        )
        _require(
            await _delta_relations(owner_connection, delta_acl, owner) == additions
        )
        for item in delta:
            await owner_connection.exec_driver_sql(item.sql())
        await core._access(
            owner_connection,
            previous + delta,
            role_oids,
            granted=True,
            targets={**existing, **additions},
        )
        _require(
            await core._target_oids(
                owner_connection,
                {**existing_acl, **delta_acl},
                owner,
                granted=True,
            )
            == {**existing, **additions}
        )
        _require(
            await _delta_relations(owner_connection, delta_acl, owner) == additions
        )
        _require(await core._roles(owner_connection, names, owner) == role_oids)
        _require(await _metadata(owner_connection, owner=True) == owner_metadata)
    return {
        "schema": "ctcc.current55.native_account_source_acl_delta.v1",
        "database_head": "0055",
        "native_definitions_sha256": observed["native_definitions_sha256"],
        "original_source_catalog_sha256": source_sha256,
        "owner_oid": owner,
        "writer_oid": role_oids[writer],
        "custody_oid": role_oids[custody],
        "frame_reader_oid": role_oids[frame_reader],
        "owner_xid": owner_metadata[12],
        "writer_core_readback_xid": writer_xid,
        "delta_table_oids": {table: oid for (_, table), oid in additions.items()},
        "delta_grants": len(delta),
        "direct_login_readback_pending": True,
        "complete_runtime_role_matrix": False,
        "execution_authority": False,
    }
