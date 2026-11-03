"""Actual separate writer/raw-reader current0053 catalog observation.

The frozen0053 actor verifier runs only as its actual writer role. A separate
profile checks the actual raw-reader catalog without rewriting either actor.
No observation in this module issues an account revision or trading authority.
"""

import asyncio
from types import MappingProxyType

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.repositories import public_runtime_bundle_custody as custody
from app.domain import account_revision_collector_lineage_schema_v1 as schema
from app.domain import collector_current_0053_owner_installation_v1 as native53
from app.trade_qualification import account_history_source_v1 as history

PROFILE = "ctcc.collector_current0053.actual_multirole_catalog.v1"
FAILED_CLOSE_SECONDS = 5.0
# These are the unchanged keys of the actual frozen writer catalog, not a
# caller-created projection which substitutes another actor.
BASE_KEYS = frozenset(
    (
        "owner",
        "actor",
        "table_ok",
        "parent_owners",
        "columns",
        "constraints",
        "triggers",
        "functions",
        "bad_table_acl",
        "column_acl",
        "public_schema_create",
    )
)
_NATIVE_SELECT = native53._once(
    schema.CATALOG_SQL,
    "SELECT jsonb_build_object(\n",
    "SELECT jsonb_build_object(\n"
    + native53._OWNER_META.replace(
        "ctcc.collector_current0053.actual_owner_catalog.v1",
        "ctcc.collector_current0053.actual_multirole_native_catalog.v1",
    ),
)
CATALOG_SQL = f"""
SELECT jsonb_build_object(
 'schema','{PROFILE}',
 'catalog',({schema.CATALOG_SQL}),
 'native',({_NATIVE_SELECT}),
 'lineage_table_guard',(SELECT NOT c.relispartition
   AND NOT EXISTS(SELECT 1 FROM pg_inherits h WHERE h.inhrelid=c.oid OR h.inhparent=c.oid)
   AND NOT EXISTS(SELECT 1 FROM pg_rewrite r WHERE r.ev_class=c.oid)
   AND NOT EXISTS(SELECT 1 FROM pg_policy p WHERE p.polrelid=c.oid)
   FROM pg_class c WHERE c.oid='public.demo_collector_current_revision_lineages'::regclass),
 'role',(SELECT jsonb_build_object(
   'oid',r.oid::bigint,'name',r.rolname,'current_user',current_user,'session_user',session_user,
   'setting',current_setting('role'),'login',r.rolcanlogin,'superuser',r.rolsuper,
   'createdb',r.rolcreatedb,'createrole',r.rolcreaterole,'replication',r.rolreplication,
   'bypassrls',r.rolbypassrls,
   'membership',EXISTS(SELECT 1 FROM pg_roles x WHERE x.oid<>r.oid AND pg_has_role(current_user,x.oid,'MEMBER')),
   'create',has_schema_privilege(current_user,'public','CREATE')
      OR has_database_privilege(current_user,current_database(),'CREATE'))
   FROM pg_roles r WHERE r.rolname=current_user),
 'acl',(SELECT jsonb_agg(jsonb_build_object(
   'grantee',x.grantee::bigint,'grantor',x.grantor::bigint,
   'privilege',x.privilege_type,'grantable',x.is_grantable)
   ORDER BY x.grantee,x.privilege_type COLLATE "C")
   FROM pg_class c CROSS JOIN LATERAL aclexplode(COALESCE(c.relacl,acldefault('r',c.relowner))) x
   WHERE c.oid='public.demo_collector_current_revision_lineages'::regclass),
 'database',jsonb_build_array(current_database(),
   (SELECT oid::bigint FROM pg_database WHERE datname=current_database()),
   inet_server_addr()::text,inet_server_port()))
"""


class Current0053MultiRoleError(ValueError):
    pass


def _deny():
    raise Current0053MultiRoleError(
        "current0053_multirole_catalog_unavailable"
    ) from None


def _data_pin(value, depth=0):
    # Same bounded plain-data/native identity semantics as the captured V4
    # factory route check. Values and credentials never enter a receipt.
    if depth > 24:
        _deny()
    kind = type(value)
    if value is None or kind in (str, bytes, int, bool, float):
        return kind, value
    if kind in (tuple, list):
        return kind, tuple(_data_pin(part, depth + 1) for part in value)
    if kind in (dict, MappingProxyType):
        return kind, tuple(
            (_data_pin(key, depth + 1), _data_pin(part, depth + 1))
            for key, part in value.items()
        )
    if kind in (set, frozenset):
        return kind, frozenset(_data_pin(part, depth + 1) for part in value)
    return "exact_identity", id(value)


def _factory_route(factory):
    pin = history._factory_pin(factory)
    parameters = custody._closure(pin[1].sync_engine.pool._creator).get("cparams")
    allowed = {
        "host",
        "port",
        "database",
        "user",
        "password",
        "ssl",
        "timeout",
        "command_timeout",
        "server_settings",
        "statement_cache_size",
        "prepared_statement_cache_size",
        "max_cached_statement_lifetime",
        "max_cacheable_statement_size",
    }
    if (
        type(parameters) not in (dict, custody.immutabledict)
        or not set(parameters) <= allowed
        or not {"host", "port", "database", "user"} <= set(parameters)
        or type(parameters["host"]) is not str
        or not 1 <= len(parameters["host"]) <= 253
        or any(
            c
            not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:-"
            for c in parameters["host"]
        )
        or type(parameters["port"]) is not int
        or not 1 <= parameters["port"] <= 65535
        or type(parameters["database"]) is not str
        or not 1 <= len(parameters["database"]) <= 63
        or any(
            ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF for c in parameters["database"]
        )
        or type(parameters["user"]) is not str
        or not parameters["user"]
    ):
        _deny()
    route = {
        name: parameters[name]
        for name in sorted(parameters)
        if name not in {"user", "password"}
    }
    return (
        pin,
        (type(parameters), _data_pin(dict(parameters))),
        _data_pin(route),
        parameters["user"],
    )


def _role(row, expected_name, *, expected_oid=None):
    if type(row) is not dict or set(row) != {
        "oid",
        "name",
        "current_user",
        "session_user",
        "setting",
        "login",
        "superuser",
        "createdb",
        "createrole",
        "replication",
        "bypassrls",
        "membership",
        "create",
    }:
        _deny()
    if (
        type(row["oid"]) is not int
        or not 0 < row["oid"] < 2**32
        or (expected_oid is not None and row["oid"] != expected_oid)
        or row["name"] != expected_name
        or row["current_user"] != expected_name
        or row["session_user"] != expected_name
        or row["setting"] != "none"
        or row["login"] is not True
        or any(
            row[key] is not False
            for key in (
                "superuser",
                "createdb",
                "createrole",
                "replication",
                "bypassrls",
                "membership",
                "create",
            )
        )
    ):
        _deny()


def _native_context(value, *, role, read_only, owner):
    if (
        type(value) is not dict
        or set(value) != BASE_KEYS | native53.EXTRA_KEYS
        or value["schema_version"]
        != "ctcc.collector_current0053.actual_multirole_native_catalog.v1"
        or value["database_head"] != "0053"
        or value["owner"] != owner
        or value["actor"] != role["oid"]
    ):
        _deny()
    server = value["server"]
    if type(server) is not dict or set(server) != {
        "version_num",
        "current_user",
        "session_user",
        "role_setting",
        "database",
        "timezone",
        "search_path",
        "isolation",
        "read_only",
        "synchronous_commit",
        "fsync",
        "full_page_writes",
        "replication_role",
        "recovery",
        "role_oid",
        "superuser",
        "can_login",
    }:
        _deny()
    version = server["version_num"]
    if (
        type(version) is not str
        or not version.isascii()
        or not version.isdecimal()
        or not 170000 <= int(version) < 180000
        or server["role_oid"] != role["oid"]
        or server["current_user"] != role["name"]
        or server["session_user"] != role["name"]
        or server["superuser"] is not False
        or server["can_login"] is not True
        or server["recovery"] is not False
        or type(server["database"]) is not str
        or not server["database"]
    ):
        _deny()
    for key, expected in (
        ("role_setting", "none"),
        ("timezone", "UTC"),
        ("search_path", "pg_catalog, public, pg_temp"),
        ("isolation", "read committed"),
        ("read_only", read_only),
        ("synchronous_commit", "on"),
        ("fsync", "on"),
        ("full_page_writes", "on"),
        ("replication_role", "origin"),
    ):
        if server[key] != expected:
            _deny()
    parents = value["parent_facts"]
    if type(parents) is not list or len(parents) != len(native53.PARENTS):
        _deny()
    names = set()
    for row in parents:
        if (
            type(row) is not dict
            or set(row)
            != {"name", "oid", "owner", "kind", "persistence", "rls", "force_rls"}
            or type(row["name"]) is not str
            or row["name"] in names
            or row["name"] not in native53.PARENTS
            or type(row["oid"]) is not int
            or not 0 < row["oid"] < 2**32
            or type(row["owner"]) is not int
            or row["owner"] != owner
            or row["kind"] != "r"
            or row["persistence"] != "p"
            or row["rls"] is not False
            or row["force_rls"] is not False
        ):
            _deny()
        names.add(row["name"])
    return native53._native_observation(value)


def verify_actual_multirole_catalog(
    raw, writer, *, raw_name, writer_name, approved_raw_role_oid, parent_owner_oid
):
    """Compare actual independently measured roles; never fabricate an actor.

    Names are private values derived only by the controlled adapter from the
    checked native creators. This pure verifier's output is always inert.
    """
    for value in (raw, writer):
        if (
            type(value) is not dict
            or set(value)
            != {
                "schema",
                "catalog",
                "native",
                "role",
                "acl",
                "database",
                "lineage_table_guard",
            }
            or value["schema"] != PROFILE
            or value["lineage_table_guard"] is not True
        ):
            _deny()
    _role(raw["role"], raw_name, expected_oid=approved_raw_role_oid)
    _role(writer["role"], writer_name)
    if (
        raw_name == writer_name
        or raw["role"]["oid"] == writer["role"]["oid"]
        or type(parent_owner_oid) is not int
        or parent_owner_oid in (raw["role"]["oid"], writer["role"]["oid"])
    ):
        _deny()
    # The frozen verifier receives the untouched JSON produced in the actual
    # writer session. It never runs as the migration owner or raw-reader.
    schema.verify_catalog(writer["catalog"])
    for value in (raw, writer):
        row = value["catalog"]
        if (
            type(row) is not dict
            or set(row) != BASE_KEYS
            or row["owner"] != parent_owner_oid
            or row["actor"] != value["role"]["oid"]
            or row["table_ok"] is not True
        ):
            _deny()
        if any(value["native"][key] != row[key] for key in BASE_KEYS):
            _deny()
    if (
        raw["catalog"]["bad_table_acl"] is not True
        or writer["catalog"]["bad_table_acl"] is not False
    ):
        _deny()
    for key in BASE_KEYS - {"actor", "bad_table_acl"}:
        if raw["catalog"][key] != writer["catalog"][key]:
            _deny()
    if raw["acl"] != writer["acl"] or type(raw["acl"]) is not list:
        _deny()
    acl_seen = set()
    for grant in raw["acl"]:
        if (
            type(grant) is not dict
            or set(grant) != {"grantee", "grantor", "privilege", "grantable"}
            or type(grant["grantee"]) is not int
            or grant["grantor"] != parent_owner_oid
            or type(grant["grantable"]) is not bool
            or (grant["grantee"], grant["privilege"]) in acl_seen
        ):
            _deny()
        acl_seen.add((grant["grantee"], grant["privilege"]))
        if grant["grantee"] == writer["role"]["oid"]:
            if (
                grant["privilege"] not in {"SELECT", "INSERT"}
                or grant["grantable"] is not False
            ):
                _deny()
        elif grant["grantee"] != parent_owner_oid:
            _deny()
    if {
        privilege for grantee, privilege in acl_seen if grantee == writer["role"]["oid"]
    } != {"SELECT", "INSERT"}:
        _deny()
    if {
        privilege for grantee, privilege in acl_seen if grantee == parent_owner_oid
    } != {
        "SELECT",
        "INSERT",
        "UPDATE",
        "DELETE",
        "TRUNCATE",
        "REFERENCES",
        "TRIGGER",
        "MAINTAIN",
    }:
        _deny()
    if (
        type(raw["database"]) is not list
        or len(raw["database"]) != 4
        or type(raw["database"][0]) is not str
        or not raw["database"][0]
        or type(raw["database"][1]) is not int
        or not 0 < raw["database"][1] < 2**32
        or type(raw["database"][2]) is not str
        or not raw["database"][2]
        or type(raw["database"][3]) is not int
        or not 1 <= raw["database"][3] <= 65535
        or raw["database"] != writer["database"]
    ):
        _deny()
    if any(
        value["native"]["server"]["database"] != value["database"][0]
        for value in (raw, writer)
    ):
        _deny()
    raw_native = _native_context(
        raw["native"], role=raw["role"], read_only="off", owner=parent_owner_oid
    )
    writer_native = _native_context(
        writer["native"], role=writer["role"], read_only="on", owner=parent_owner_oid
    )
    for key in (
        "table_oid",
        "parent_facts",
        "native_definitions",
        "ri_triggers",
        "custody_incoming",
    ):
        if raw["native"][key] != writer["native"][key]:
            _deny()
    if (
        raw_native != writer_native
        or raw_native != native53._required_native_baseline()
    ):
        _deny()
    return {
        "schema": PROFILE,
        "database_head": "0053",
        "ddl_sha256": schema.DDL_SHA256,
        "native_definitions_sha256": raw_native,
        "actual_separate_roles_verified": True,
        "account_complete": False,
        "execution_authority": False,
    }


async def _close_owned(session):
    # Reviewed cap16 cancellation ownership pattern, retained with a fixed
    # five-second close cap. No close retry or background task escapes.
    async def close_once():
        async with asyncio.timeout(FAILED_CLOSE_SECONDS):
            await session.close()

    task = asyncio.create_task(close_once())
    cancelled = bool(asyncio.current_task().cancelling())
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
        except Exception:  # noqa: BLE001 -- redacted fixed error below.
            break
    if cancelled:
        if not task.cancelled():
            task.exception()
        raise asyncio.CancelledError() from None
    try:
        task.result()
    except asyncio.CancelledError:
        raise asyncio.CancelledError() from None
    except Exception:  # noqa: BLE001 -- no SQL/credential details.
        _deny()


async def read_actual_multirole_catalog(
    raw_session,
    fence,
    *,
    approved_raw_role_oid,
    lineage_session_factory,
    raw_session_factory,
    parent_owner_oid,
):
    """Use the actual captured writer factory under the original raw-read fence."""
    initial_writer = _factory_route(lineage_session_factory)
    initial_raw = _factory_route(raw_session_factory)
    if (
        initial_writer[2] != initial_raw[2]
        or initial_writer[3] == initial_raw[3]
        or raw_session.get_bind() is not initial_raw[0][1].sync_engine
    ):
        _deny()

    def check():
        fence.check()
        for factory, expected in (
            (lineage_session_factory, initial_writer),
            (raw_session_factory, initial_raw),
        ):
            if _factory_route(factory) != expected:
                _deny()
        if raw_session.get_bind() is not initial_raw[0][1].sync_engine:
            _deny()

    writer = None
    try:
        check()
        writer = lineage_session_factory()
        if (
            type(writer) is not AsyncSession
            or writer.get_bind() is not initial_writer[0][1].sync_engine
        ):
            _deny()
        await writer.begin()
        check()
        for statement in (
            "SET TRANSACTION ISOLATION LEVEL READ COMMITTED",
            "SET TRANSACTION READ ONLY",
            "SET LOCAL search_path TO pg_catalog, public, pg_temp",
            "SET LOCAL TIME ZONE 'UTC'",
            "SET LOCAL lock_timeout='1000ms'",
            "SET LOCAL statement_timeout='10000ms'",
        ):
            await writer.execute(text(statement))
            check()
        writer_value = (await writer.execute(text(CATALOG_SQL))).scalar_one()
        check()
        raw_value = (await raw_session.execute(text(CATALOG_SQL))).scalar_one()
        check()
        profile = verify_actual_multirole_catalog(
            raw_value,
            writer_value,
            raw_name=initial_raw[3],
            writer_name=initial_writer[3],
            approved_raw_role_oid=approved_raw_role_oid,
            parent_owner_oid=parent_owner_oid,
        )
        check()
        await writer.commit()
        check()
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 -- only fixed closed failure leaves this adapter.
        _deny()
    finally:
        if writer is not None:
            await _close_owned(writer)
    check()
    # Complete actual catalogs enter only the enclosing acquisition digest.
    # No connection parameters or secret values are included.
    return {"profile": profile, "raw": raw_value, "writer": writer_value}
