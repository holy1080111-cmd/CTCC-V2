"""Add only the restricted current55 no-execution lifespan grants."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from scripts import current0055_native_account_source_acl_v1 as account
from scripts import current0055_runtime_grants as core

_TABLE_RIGHTS = (
    ("demo_automation_state", "UPDATE"),
    ("demo_automation_runs", "SELECT"),
    ("demo_soak_sessions", "SELECT"),
)
_TABLES = tuple(("TABLE", table) for table, _ in _TABLE_RIGHTS)
_LOCK_SQL = """
LOCK TABLE public.demo_automation_state, public.demo_automation_runs,
           public.demo_soak_sessions IN ACCESS EXCLUSIVE MODE NOWAIT
"""
_GUARD_BODY_SHA256 = "623292f9f2d71d1e877947d6b713139f66d5b9f19e9954d788d9c9a2acfc71b9"
_CONTROL_GUARDS_SQL = text("""
SELECT t.tgrelid::bigint, t.tgname, t.tgenabled::text, t.tgtype::int,
       t.tgparentid=0 AND t.tgconstraint=0 AND t.tgnargs=0
        AND t.tgattr::text='' AND t.tgqual IS NULL
        AND t.tgoldtable IS NULL AND t.tgnewtable IS NULL AS plain_trigger,
       p.oid::bigint, p.proname, p.proowner::bigint, n.nspname, l.lanname,
       p.prorettype='pg_catalog.trigger'::regtype AND p.pronargs=0
        AND p.pronargdefaults=0 AND NOT p.proretset AND NOT p.prosecdef
        AND NOT p.proisstrict AND NOT p.proleakproof AND p.prokind='f'
        AND p.provolatile='v' AND p.proparallel='u' AND p.prosupport=0
        AND p.proconfig IS NULL AS plain_function,
       pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(p.prosrc,'UTF8')),'hex')
FROM pg_catalog.pg_trigger t
JOIN pg_catalog.pg_proc p ON p.oid=t.tgfoid
JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
JOIN pg_catalog.pg_language l ON l.oid=p.prolang
WHERE t.tgrelid::bigint=ANY(CAST(:tables AS bigint[])) AND NOT t.tgisinternal
""")


def _require(condition):
    if not condition:
        raise ValueError("current0055_native_lifecycle_acl_scope_changed")


def _plans(writer, custody, frame_reader):
    base, source = account._plans(writer, custody, frame_reader)
    previous = base + source
    additions = tuple(
        core._Grant("TABLE", table, (privilege,), writer)
        for table, privilege in _TABLE_RIGHTS
    )
    _require(
        tuple(
            item.rights for item in previous if item.target == "demo_automation_state"
        )
        == (("SELECT", "MAINTAIN"),)
        and all(
            item.target not in {"demo_automation_runs", "demo_soak_sessions"}
            for item in previous
        )
    )
    return previous, additions


def current55_native_lifecycle_grant_sql_v1(
    *, writer: str, custody: str, frame_reader: str
) -> tuple[str, ...]:
    """Render exactly the three additional grants for the existing writer."""
    _, additions = _plans(writer, custody, frame_reader)
    return tuple(item.sql() for item in additions)


async def _guard(connection, tables, owner):
    rows = (
        await connection.execute(_CONTROL_GUARDS_SQL, {"tables": list(tables.values())})
    ).all()
    _require(len(rows) == 1)
    row = tuple(rows[0])
    name = "demo_automation_control_revision_guard"
    _require(
        row[:5] == (tables[("TABLE", "demo_automation_state")], name, "O", 19, True)
        and type(row[5]) is int
        and row[5] > 0
        and row[6:] == (name, owner, "public", "plpgsql", True, _GUARD_BODY_SHA256)
    )
    return row


async def provision_current55_native_lifecycle_acl_v1(
    owner_connection: AsyncConnection,
    writer_connection: AsyncConnection,
    *,
    writer: str,
    custody: str,
    frame_reader: str,
) -> dict:
    """Commit the fixed delta over the exact core and account-source ACLs."""
    previous, additions = _plans(writer, custody, frame_reader)
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
        for statement in account._SETTINGS:
            await owner_connection.execute(text(statement))
        await owner_connection.execute(
            text("SELECT pg_catalog.pg_advisory_xact_lock(5883109675522810055)")
        )
        metadata = await account._metadata(owner_connection, owner=True)
        owner = metadata[0]
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
        roles = await core._roles(owner_connection, names, owner)
        observed, original_source_pin, writer_xid = await account._observe_writer(
            writer_connection,
            login=writer,
            role_oid=roles[writer],
            owner_metadata=metadata,
        )
        await owner_connection.exec_driver_sql(_LOCK_SQL)
        before_acl = core._expected_acl(previous, roles, owner)
        for table in _TABLES:
            before_acl.setdefault(table, set())
        targets = await core._target_oids(
            owner_connection, before_acl, owner, granted=True
        )
        tables = {table: targets[table] for table in _TABLES}
        _require(
            await account._delta_relations(owner_connection, tables, owner) == tables
        )
        guard = await _guard(owner_connection, tables, owner)
        await core._access(
            owner_connection, previous, roles, granted=True, targets=targets
        )
        for grant in additions:
            await owner_connection.exec_driver_sql(grant.sql())
        combined = previous + additions
        after_acl = core._expected_acl(combined, roles, owner)
        _require(
            await core._target_oids(owner_connection, after_acl, owner, granted=True)
            == targets
        )
        await core._access(
            owner_connection, combined, roles, granted=True, targets=targets
        )
        _require(
            await account._delta_relations(owner_connection, tables, owner) == tables
            and await _guard(owner_connection, tables, owner) == guard
            and await core._roles(owner_connection, names, owner) == roles
            and await account._metadata(owner_connection, owner=True) == metadata
        )
    return {
        "schema": "ctcc.current55.native_lifecycle_acl_delta.v1",
        "database_head": "0055",
        "native_definitions_sha256": observed["native_definitions_sha256"],
        "original_source_catalog_sha256": original_source_pin,
        "legacy_control_guard_body_sha256": _GUARD_BODY_SHA256,
        "owner_oid": owner,
        "owner_xid": metadata[12],
        "writer_core_readback_xid": writer_xid,
        "role_oids": {
            "writer": roles[writer],
            "custody": roles[custody],
            "frame_reader": roles[frame_reader],
        },
        "lifecycle_table_oids": {table: oid for (_, table), oid in tables.items()},
        "delta_grants": 3,
        "direct_login_readback_pending": True,
        "complete_runtime_role_matrix": False,
        "execution_authority": False,
    }
