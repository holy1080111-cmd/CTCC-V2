"""Owner provisioning of the audited, bounded current0055 native core ACL."""

import re
from dataclasses import dataclass
from hashlib import sha256

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.database.repositories import account_history_partition as partition
from app.database.repositories import public_runtime_bundle_custody as custody_policy
from app.domain import account_history_original_frame_v1 as frame
from app.domain import collector_current_0055_catalog_v1 as catalog
from app.domain import controlled_history_current55_schema_v2 as history

_LOGIN = re.compile(r"[a-z][a-z0-9_]{0,62}")
_WRITER_TABLES = (
    ("qualification_account_scopes", ("SELECT", "INSERT", "UPDATE")),
    ("qualification_reservations", ("SELECT", "INSERT", "UPDATE")),
    ("qualification_reservation_transitions", ("SELECT", "INSERT", "MAINTAIN")),
    ("demo_collector_current_revision_lineages", ("SELECT", "INSERT")),
    ("demo_collector_issued_account_revisions", ("SELECT", "INSERT")),
    ("demo_account_controls", ("SELECT", "INSERT", "UPDATE")),
    ("demo_control_journal", ("SELECT", "INSERT")),
    ("demo_control_clear_acknowledgements_v2", ("SELECT", "INSERT")),
    ("demo_policy_counter_initializations_v1", ("SELECT", "INSERT")),
    ("qualification_submission_outcomes", ("SELECT", "INSERT")),
    ("qualification_report_spool", ("SELECT", "INSERT")),
    ("qualification_report_projection_receipts", ("SELECT", "INSERT")),
    ("qualification_leverage_configuration_intents_v1", ("SELECT", "INSERT", "UPDATE")),
    ("qualification_leverage_configuration_attempts_v1", ("SELECT", "INSERT")),
    ("qualification_leverage_configuration_results_v1", ("SELECT", "INSERT")),
)
_READ_PARTITION = "account_history_partition_v1_read"
_ROLE_SQL = text("""
SELECT r.oid::bigint, r.rolcanlogin,
       (r.rolsuper OR r.rolcreatedb OR r.rolcreaterole
         OR r.rolreplication OR r.rolbypassrls),
       EXISTS(SELECT 1 FROM pg_catalog.pg_auth_members m
              WHERE r.oid IN (m.roleid,m.member)),
       pg_catalog.has_schema_privilege(r.oid,'public','CREATE'),
       pg_catalog.has_database_privilege(r.oid,pg_catalog.current_database(),'CREATE'),
       pg_catalog.has_database_privilege(r.oid,pg_catalog.current_database(),'CONNECT')
FROM pg_catalog.pg_roles r WHERE r.rolname=:name
""")
_OWNER_POLICY_SQL = text("""
SELECT
  NOT EXISTS (SELECT 1 FROM pg_catalog.pg_default_acl d
    CROSS JOIN LATERAL pg_catalog.aclexplode(d.defaclacl) a
    WHERE d.defaclrole=CAST(:owner AS oid) AND a.grantee<>d.defaclrole),
  (SELECT n.nspowner::bigint=CAST(:owner AS bigint)
    OR (n.nspowner='pg_database_owner'::regrole
      AND (SELECT datdba::bigint FROM pg_catalog.pg_database
        WHERE datname=pg_catalog.current_database())=CAST(:owner AS bigint))
   FROM pg_catalog.pg_namespace n WHERE n.nspname='public')
""")
_RELATION_ACCESS_SQL = text("""
SELECT c.relname,p.privilege
FROM pg_catalog.pg_class c
JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
CROSS JOIN (VALUES ('SELECT'),('INSERT'),('UPDATE'),('DELETE'),('TRUNCATE'),
                   ('REFERENCES'),('TRIGGER'),('MAINTAIN')) p(privilege)
WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m','f')
  AND pg_catalog.has_table_privilege(CAST(:role AS oid),c.oid,p.privilege)
UNION ALL
SELECT c.relname,p.privilege
FROM pg_catalog.pg_class c
JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
CROSS JOIN (VALUES ('SELECT'),('UPDATE'),('USAGE')) p(privilege)
WHERE n.nspname='public' AND c.relkind='S'
  AND pg_catalog.has_sequence_privilege(CAST(:role AS oid),c.oid,p.privilege)
""")
_DIRECT_ROUTINE_ACL_SQL = text("""
SELECT p.oid::bigint,a.privilege_type,a.is_grantable
FROM pg_catalog.pg_proc p
JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
CROSS JOIN LATERAL pg_catalog.aclexplode(p.proacl) a
WHERE n.nspname='public' AND a.grantee=CAST(:role AS oid)
""")
_COLUMN_ACL_SQL = text("""
SELECT EXISTS(
 SELECT 1 FROM pg_catalog.pg_class c
 JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
 JOIN pg_catalog.pg_attribute a ON a.attrelid=c.oid
 CROSS JOIN LATERAL pg_catalog.aclexplode(a.attacl) acl
 WHERE n.nspname='public' AND acl.grantee IN (0,CAST(:role AS oid))
)
""")
_TARGET_RELATION_SQL = text("""
SELECT c.oid::bigint, c.relowner::bigint, c.relkind::text,
  coalesce((SELECT jsonb_agg(jsonb_build_array(
              a.grantee::bigint,a.grantor::bigint,a.privilege_type,a.is_grantable))
            FROM pg_catalog.aclexplode(c.relacl) a
            WHERE a.grantee<>c.relowner),'[]'::jsonb)
FROM pg_catalog.pg_class c WHERE c.oid=pg_catalog.to_regclass(:object)
""")
_TARGET_FUNCTION_SQL = text("""
SELECT p.oid::bigint, p.proowner::bigint, p.prokind::text,
  coalesce((SELECT jsonb_agg(jsonb_build_array(
              a.grantee::bigint,a.grantor::bigint,a.privilege_type,a.is_grantable))
            FROM pg_catalog.aclexplode(COALESCE(p.proacl,
                   pg_catalog.acldefault('f',p.proowner))) a
            WHERE a.grantee<>p.proowner),'[]'::jsonb)
FROM pg_catalog.pg_proc p WHERE p.oid=pg_catalog.to_regprocedure(:object)
""")
_SCHEMA_USAGE_SQL = text(
    "SELECT pg_catalog.has_schema_privilege(CAST(:role AS oid),'public','USAGE')"
)


@dataclass(frozen=True)
class _Grant:
    kind: str
    target: str
    rights: tuple[str, ...]
    login: str

    def sql(self) -> str:
        qualified = self.target if self.kind == "SCHEMA" else f"public.{self.target}"
        rights = ", ".join(self.rights)
        return f'GRANT {rights} ON {self.kind} {qualified} TO "{self.login}"'


def _require(condition: bool) -> None:
    if not condition:
        raise ValueError("current0055_core_grant_scope_or_catalog_changed")


def _plan(writer: str, custody: str, frame_reader: str) -> tuple[_Grant, ...]:
    roles = (writer, custody, frame_reader)
    _require(
        all(
            type(role) is str
            and _LOGIN.fullmatch(role) is not None
            and role != "public"
            and not role.startswith("pg_")
            for role in roles
        )
        and len(set(roles)) == 3
    )
    partition_signature = partition.policy.FUNCTION_SIGNATURES[_READ_PARTITION]
    return (
        *(_Grant("SCHEMA", "public", ("USAGE",), role) for role in roles),
        *(_Grant("TABLE", "alembic_version", ("SELECT",), role) for role in roles),
        *(_Grant("TABLE", name, rights, writer) for name, rights in _WRITER_TABLES),
        _Grant(
            "SEQUENCE",
            "qualification_reservation_transitions_id_seq",
            ("USAGE",),
            writer,
        ),
        _Grant(
            "FUNCTION",
            f"{_READ_PARTITION}({partition_signature})",
            ("EXECUTE",),
            writer,
        ),
        _Grant(
            "FUNCTION",
            f"{history.APPEND}({history.SIGNATURES[history.APPEND]})",
            ("EXECUTE",),
            writer,
        ),
        _Grant(
            "FUNCTION",
            f"{frame.FUNCTION}({frame.SIGNATURE})",
            ("EXECUTE",),
            frame_reader,
        ),
        _Grant(
            "FUNCTION",
            f"{history.FRAME}({history.SIGNATURES[history.FRAME]})",
            ("EXECUTE",),
            frame_reader,
        ),
        _Grant(
            "FUNCTION",
            "public_runtime_bundle_v1_append(text,text,text,bytea)",
            ("EXECUTE",),
            custody,
        ),
        _Grant(
            "FUNCTION", "public_runtime_bundle_v1_read(text)", ("EXECUTE",), custody
        ),
    )


def current55_core_grant_sql(
    *, writer: str, custody: str, frame_reader: str
) -> tuple[str, ...]:
    """Render the fixed reviewed SQL, with no connection or credential inputs."""
    return tuple(item.sql() for item in _plan(writer, custody, frame_reader))


def _expected_acl(plan, role_oids, owner):
    expected = {}
    for item in plan:
        if item.kind != "SCHEMA":
            key = (item.kind, item.target)
            expected.setdefault(key, set()).update(
                (role_oids[item.login], owner, right, False) for right in item.rights
            )
    return expected


async def _roles(connection, names, owner):
    found = {}
    for name in names:
        row = (await connection.execute(_ROLE_SQL, {"name": name})).one_or_none()
        _require(
            row is not None
            and type(row[0]) is int
            and row[0] > 0
            and row[0] != owner
            and tuple(row[1:]) == (True, False, False, False, False, True)
        )
        found[name] = row[0]
    _require(len(set(found.values())) == len(names))
    return found


async def _access(connection, plan, role_oids, *, granted, targets):
    for name, oid in role_oids.items():
        want = {
            (item.target, right)
            for item in plan
            if granted and item.login == name and item.kind in {"TABLE", "SEQUENCE"}
            for right in item.rights
        }
        observed = (await connection.execute(_RELATION_ACCESS_SQL, {"role": oid})).all()
        _require({tuple(row) for row in observed} == want)
        _require(await connection.scalar(_COLUMN_ACL_SQL, {"role": oid}) is False)
        routines = (
            await connection.execute(_DIRECT_ROUTINE_ACL_SQL, {"role": oid})
        ).all()
        _require(
            {tuple(row) for row in routines}
            == {
                (targets[(item.kind, item.target)], "EXECUTE", False)
                for item in plan
                if granted and item.login == name and item.kind == "FUNCTION"
            }
        )
        if granted:
            _require(await connection.scalar(_SCHEMA_USAGE_SQL, {"role": oid}) is True)


async def _target_oids(connection, expected, owner, *, granted):
    result = {}
    for key, rights in expected.items():
        kind, target = key
        query = _TARGET_FUNCTION_SQL if kind == "FUNCTION" else _TARGET_RELATION_SQL
        row = (
            await connection.execute(query, {"object": f"public.{target}"})
        ).one_or_none()
        _require(
            row is not None
            and type(row[0]) is int
            and row[0] > 0
            and row[1] == owner
            and row[2] == {"FUNCTION": "f", "TABLE": "r", "SEQUENCE": "S"}[kind]
            and len(row[3]) == (len(rights) if granted else 0)
            and {tuple(member) for member in row[3]} == (rights if granted else set())
        )
        result[key] = row[0]
    return result


async def _verify_owner_functions(connection, owner):
    previous = await partition.read_account_history_partition_catalog(connection)
    original = await connection.scalar(text(frame.CATALOG_SQL))
    frame.verify_owner_installation_catalog(original, previous)
    custody_rows = (await connection.execute(custody_policy._FUNCTION_SQL)).all()
    _require(len(custody_rows) == len(custody_policy._FUNCTIONS))
    _require({row.proname for row in custody_rows} == set(custody_policy._FUNCTIONS))
    for row in custody_rows:
        actual = (
            sha256(row.prosrc.encode("utf-8")).hexdigest(),
            row.prosecdef,
            row.lanname,
            row.return_type,
            row.proretset,
            row.argtypes,
            row.provolatile,
        )
        _require(
            actual == custody_policy._FUNCTIONS[row.proname]
            and tuple(row.proconfig or ()) == ("search_path=pg_catalog, public",)
            and row.proowner == owner
            and row.proisstrict is False
            and row.proparallel == "u"
            and row.proleakproof is False
            and row.prokind == "f"
            and row.prosupport == 0
            and row.public_execute is False
        )


async def provision_current55_core_grants(
    connection: AsyncConnection, *, writer: str, custody: str, frame_reader: str
) -> dict:
    """Apply one fresh owner-only core grant transaction on a precreated-role DB."""
    plan = _plan(writer, custody, frame_reader)
    _require(
        type(connection) is AsyncConnection
        and connection.dialect.name == "postgresql"
        and connection.dialect.driver == "asyncpg"
        and not connection.in_transaction()
    )
    async with connection.begin():
        for statement in (
            "SET TRANSACTION ISOLATION LEVEL READ COMMITTED",
            "SET LOCAL search_path TO pg_catalog, public, pg_temp",
            "SET LOCAL TIME ZONE 'UTC'",
            "SET LOCAL lock_timeout='1000ms'",
            "SET LOCAL statement_timeout='10000ms'",
        ):
            await connection.execute(text(statement))
        await connection.execute(
            text("SELECT pg_catalog.pg_advisory_xact_lock(5883109675522810055)")
        )
        observed = catalog._observed(
            await connection.scalar(text(catalog.OWNER_CATALOG_SQL)),
            owner_mode=True,
            read_only="off",
        )
        _require(observed["native_definitions_sha256"] == catalog._required_baseline())
        owner = observed["role_oid"]
        _require(
            tuple((await connection.execute(_OWNER_POLICY_SQL, {"owner": owner})).one())
            == (True, True)
        )
        names = (writer, custody, frame_reader)
        role_oids = await _roles(connection, names, owner)
        await _verify_owner_functions(connection, owner)
        expected = _expected_acl(plan, role_oids, owner)
        before = await _target_oids(connection, expected, owner, granted=False)
        await _access(connection, plan, role_oids, granted=False, targets=before)
        for item in plan:
            await connection.exec_driver_sql(item.sql())
        await _access(connection, plan, role_oids, granted=True, targets=before)
        _require(await _roles(connection, names, owner) == role_oids)
        _require(
            await _target_oids(connection, expected, owner, granted=True) == before
        )
    return {
        "schema": "ctcc.current55.audited_core_grants.v1",
        "database_head": "0055",
        "native_definitions_sha256": observed["native_definitions_sha256"],
        "owner_oid": owner,
        "writer_oid": role_oids[writer],
        "custody_oid": role_oids[custody],
        "frame_reader_oid": role_oids[frame_reader],
        "grants": len(plan),
        "direct_login_readback_pending": True,
        "complete_runtime_role_matrix": False,
        "execution_authority": False,
    }
