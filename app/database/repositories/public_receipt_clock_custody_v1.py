"""Owned runtime DB0035/0047 deployment checks, not independent time custody.

Every acquisition/recheck uses private exact native AsyncSession factories and
real catalog reads. Saved readbacks, constructors and caller flags cannot mint
an owner. This does not attest UTC, server binary/host, DBA resistance or orders.
DB0046 retains its own existing verifier; this module grants it no exception.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from types import FunctionType, MethodType
from weakref import WeakKeyDictionary

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.database.repositories.public_receipt_clock_catalog_v1 import (
    CHECKS,
    COLUMNS,
    FUNCTION_HASHES,
    FUNCTION_METADATA,
    INDEXES,
    KEYS,
    OBSERVATION,
    TABLES,
    WITNESS,
    expression_tree,
)
from app.database.repositories.public_receipt_post_read_observation import (
    PublicReceiptPostReadRepository,
)
from app.database.repositories.public_receipt_witness import (
    PublicReceiptWitnessRepository,
)
from app.database.repositories.public_runtime_bundle_custody import _native_engine

_ISSUER = object()
_OWNERS = WeakKeyDictionary()
_LIFETIME_NS = 30_000_000_000
_METHODS = {
    cls: {
        name: value
        for name, value in vars(cls).items()
        if type(value) in (FunctionType, staticmethod)
    }
    for cls in (PublicReceiptWitnessRepository, PublicReceiptPostReadRepository)
}
_TARGETS = (
    "('public_receipt_witness_revisions','public_receipt_post_read_observations')"
)

_ROLE_SQL = text("""
SELECT session_user=current_user AS direct_login, r.oid::bigint AS login_oid,
 r.rolsuper OR r.rolcreatedb OR r.rolcreaterole OR r.rolreplication OR r.rolbypassrls AS privileged,
 EXISTS(SELECT 1 FROM pg_catalog.pg_roles x WHERE x.oid<>r.oid AND pg_catalog.pg_has_role(current_user,x.oid,'MEMBER')) AS member_other,
 EXISTS(SELECT 1 FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
   WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname !~ '^pg_(toast|temp)'
   AND c.relkind IN ('r','p','v','m','f') AND
   (pg_catalog.has_table_privilege(current_user,c.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER,MAINTAIN')
    OR pg_catalog.has_any_column_privilege(current_user,c.oid,'SELECT,INSERT,UPDATE,REFERENCES'))) AS table_access,
 EXISTS(SELECT 1 FROM pg_catalog.pg_namespace n WHERE n.nspname !~ '^pg_(temp|toast_temp)_[0-9]+$'
   AND pg_catalog.has_schema_privilege(current_user,n.oid,'CREATE'))
   OR pg_catalog.has_database_privilege(current_user,current_database(),'CREATE') AS can_create,
 pg_catalog.current_setting('fsync') AS fsync,pg_catalog.current_setting('full_page_writes') AS full_page_writes,
 pg_catalog.current_setting('synchronous_commit') AS synchronous_commit,
 pg_catalog.current_setting('session_replication_role') AS replication_role,
 pg_catalog.current_setting('search_path') AS catalog_search_path,
 pg_catalog.pg_is_in_recovery() AS recovery,
 pg_catalog.current_setting('server_version_num') AS server_version_num,
 d.oid::bigint AS database_oid,d.datname AS database_name,
 pg_catalog.inet_server_addr()::text AS server_address,pg_catalog.inet_server_port() AS server_port,
 pg_catalog.pg_postmaster_start_time() AS server_started
FROM pg_catalog.pg_roles r,pg_catalog.pg_database d
WHERE r.rolname=current_user AND d.datname=current_database()
""")
_TABLE_SQL = text(
    """
SELECT c.relname,c.oid::bigint AS table_oid,c.relowner::bigint AS owner_oid,
 c.relkind::text AS relkind,c.relpersistence::text AS persistence,c.relrowsecurity AS rls,c.relforcerowsecurity AS force_rls,
 c.relispartition,c.relreplident::text AS replica_identity,
 EXISTS(SELECT 1 FROM pg_catalog.pg_inherits i WHERE i.inhrelid=c.oid OR i.inhparent=c.oid) AS inherited,
 EXISTS(SELECT 1 FROM pg_catalog.aclexplode(COALESCE(c.relacl,pg_catalog.acldefault('r',c.relowner))) a
   WHERE a.grantee<>c.relowner) AS foreign_acl,
 EXISTS(SELECT 1 FROM pg_catalog.pg_attribute a,LATERAL pg_catalog.aclexplode(a.attacl) x
   WHERE a.attrelid=c.oid AND x.grantee<>c.relowner) AS foreign_column_acl
FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
WHERE n.nspname='public' AND c.relname IN """
    + _TARGETS
    + " ORDER BY c.relname"
)
_FUNCTION_SQL = text("""
SELECT p.oid::bigint AS function_oid,p.proname,p.prosrc,p.prosecdef,l.lanname,t.typname AS return_type,tn.nspname AS return_namespace,
 p.proretset,pg_catalog.oidvectortypes(p.proargtypes) AS argtypes,p.provolatile::text AS volatility,
 p.proconfig,p.proowner::bigint AS owner_oid,p.proisstrict,p.proparallel::text AS parallel,p.proleakproof,
 p.prokind::text AS kind,p.prosupport::bigint AS support,p.pronargdefaults,p.provariadic::bigint AS variadic,p.proargmodes,
 (SELECT COALESCE(jsonb_agg(an.nspname ORDER BY x.ordinality),'[]'::jsonb)
  FROM unnest(p.proargtypes) WITH ORDINALITY x(oid,ordinality) JOIN pg_catalog.pg_type arg_type ON arg_type.oid=x.oid
  JOIN pg_catalog.pg_namespace an ON an.oid=arg_type.typnamespace) AS argument_namespaces,
 pg_catalog.has_function_privilege(current_user,p.oid,'EXECUTE') AS can_execute,
 (SELECT COALESCE(jsonb_agg(jsonb_build_array(a.grantee::bigint,a.grantor::bigint,a.privilege_type,a.is_grantable)
    ORDER BY a.grantee,a.grantor,a.privilege_type,a.is_grantable),'[]'::jsonb)
  FROM pg_catalog.aclexplode(COALESCE(p.proacl,pg_catalog.acldefault('f',p.proowner))) a) AS acl
FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
JOIN pg_catalog.pg_language l ON l.oid=p.prolang JOIN pg_catalog.pg_type t ON t.oid=p.prorettype
JOIN pg_catalog.pg_namespace tn ON tn.oid=t.typnamespace
WHERE n.nspname='public' AND (p.proname LIKE 'public_receipt_witness_%' OR p.proname LIKE 'public_receipt_post_read_%')
ORDER BY p.proname,p.oid
""")
_COLUMN_SQL = text(
    """
SELECT c.relname,a.attnum,a.attname,pg_catalog.format_type(a.atttypid,a.atttypmod) AS type_name,
 tn.nspname AS type_namespace,a.atttypmod,a.attnotnull,a.attisdropped,
 a.attcollation::bigint AS collation_oid,coll.collname AS collation_name,coll_ns.nspname AS collation_namespace,
 a.attidentity::text AS identity,a.attgenerated::text AS generated,
 pg_catalog.pg_get_expr(d.adbin,d.adrelid,false) AS default_expression,
 EXISTS(SELECT 1 FROM pg_catalog.pg_depend x JOIN pg_catalog.pg_proc p ON p.oid=x.refobjid
 JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace WHERE x.classid='pg_attrdef'::regclass
 AND x.objid=d.oid AND x.refclassid='pg_proc'::regclass AND n.nspname<>'pg_catalog') AS unsafe_default
FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
JOIN pg_catalog.pg_attribute a ON a.attrelid=c.oid AND a.attnum>0
JOIN pg_catalog.pg_type t ON t.oid=a.atttypid JOIN pg_catalog.pg_namespace tn ON tn.oid=t.typnamespace
LEFT JOIN pg_catalog.pg_collation coll ON coll.oid=a.attcollation
LEFT JOIN pg_catalog.pg_namespace coll_ns ON coll_ns.oid=coll.collnamespace
LEFT JOIN pg_catalog.pg_attrdef d ON d.adrelid=c.oid AND d.adnum=a.attnum
WHERE n.nspname='public' AND c.relname IN """
    + _TARGETS
    + " ORDER BY c.relname,a.attnum"
)
_TRIGGER_SQL = text(
    """
SELECT c.relname,g.oid::bigint AS trigger_oid,p.oid::bigint AS function_oid,g.tgname,g.tgtype,g.tgenabled::text AS enabled,g.tgisinternal,g.tgconstraint::bigint AS constraint_oid,
 g.tgnargs,g.tgargs,pg_catalog.pg_get_expr(g.tgqual,g.tgrelid,false) AS qualification,
 g.tgoldtable,g.tgnewtable,p.proname,n.nspname AS function_namespace,g.tgdeferrable,g.tginitdeferred
FROM pg_catalog.pg_trigger g JOIN pg_catalog.pg_class c ON c.oid=g.tgrelid
JOIN pg_catalog.pg_namespace cn ON cn.oid=c.relnamespace JOIN pg_catalog.pg_proc p ON p.oid=g.tgfoid
JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
WHERE cn.nspname='public' AND c.relname IN """
    + _TARGETS
    + " AND NOT g.tgisinternal ORDER BY c.relname,g.tgname"
)
_CONSTRAINT_SQL = text(
    """
SELECT c.relname,k.oid::bigint AS constraint_oid,k.conindid::bigint AS index_oid,k.conname,k.contype::text AS kind,k.convalidated,k.condeferrable,k.condeferred,
 k.connoinherit,k.conislocal,k.coninhcount,k.conkey,k.confkey,rc.relname AS referenced_table,rn.nspname AS referenced_namespace,
 k.confupdtype::text AS update_action,k.confdeltype::text AS delete_action,k.confmatchtype::text AS match_type,
 pg_catalog.pg_get_expr(k.conbin,k.conrelid,false) AS expression,
 EXISTS(SELECT 1 FROM pg_catalog.pg_depend x LEFT JOIN pg_catalog.pg_proc p
   ON x.refclassid='pg_proc'::regclass AND p.oid=x.refobjid LEFT JOIN pg_catalog.pg_operator o
   ON x.refclassid='pg_operator'::regclass AND o.oid=x.refobjid
   LEFT JOIN pg_catalog.pg_collation dep_coll ON x.refclassid='pg_collation'::regclass AND dep_coll.oid=x.refobjid
   WHERE x.classid='pg_constraint'::regclass AND x.objid=k.oid
   AND (p.oid IS NOT NULL AND p.pronamespace<>'pg_catalog'::regnamespace
     OR o.oid IS NOT NULL AND o.oprnamespace<>'pg_catalog'::regnamespace
     OR dep_coll.oid IS NOT NULL AND (dep_coll.collnamespace<>'pg_catalog'::regnamespace OR dep_coll.collname<>'default'))) AS unsafe_dependencies,
 (SELECT COALESCE(jsonb_agg(jsonb_build_array(p.proname,n.nspname,g.tgtype,g.tgenabled::text,
    g.tgnargs,pg_catalog.encode(g.tgargs,'hex'),g.tgqual IS NULL,g.tgdeferrable,g.tginitdeferred)
    ORDER BY p.proname),'[]'::jsonb)
  FROM pg_catalog.pg_trigger g JOIN pg_catalog.pg_proc p ON p.oid=g.tgfoid
  JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace WHERE g.tgconstraint=k.oid) AS fk_triggers
FROM pg_catalog.pg_constraint k JOIN pg_catalog.pg_class c ON c.oid=k.conrelid
JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace LEFT JOIN pg_catalog.pg_class rc ON rc.oid=k.confrelid
LEFT JOIN pg_catalog.pg_namespace rn ON rn.oid=rc.relnamespace
WHERE n.nspname='public' AND c.relname IN """
    + _TARGETS
    + " ORDER BY c.relname,k.conname"
)
_INDEX_SQL = text(
    """
SELECT c.relname,ic.oid::bigint AS index_oid,ic.relname AS index_name,ic.relowner::bigint AS owner_oid,ic.relkind::text AS relkind,
 ic.relpersistence::text AS persistence,n.nspname AS index_namespace,am.amname,
 i.indisunique,i.indisprimary,i.indisexclusion,i.indisvalid,i.indisready,i.indislive,i.indimmediate,
 i.indnatts,i.indnkeyatts,i.indkey::smallint[] AS keys,i.indoption::smallint[] AS options,
 pg_catalog.pg_get_expr(i.indexprs,i.indrelid,false) AS expression,
 pg_catalog.pg_get_expr(i.indpred,i.indrelid,false) AS predicate,
 (SELECT jsonb_agg(jsonb_build_array(op.opcname,onsp.nspname) ORDER BY x.ordinality)
  FROM unnest(i.indclass) WITH ORDINALITY x(oid,ordinality) JOIN pg_catalog.pg_opclass op ON op.oid=x.oid
  JOIN pg_catalog.pg_namespace onsp ON onsp.oid=op.opcnamespace) AS opclasses,
 (SELECT jsonb_agg(CASE WHEN x.oid=0 THEN 'none' WHEN co.collname='default' AND cn.nspname='pg_catalog' THEN 'default' ELSE 'forbidden' END ORDER BY x.ordinality)
  FROM unnest(i.indcollation) WITH ORDINALITY x(oid,ordinality) LEFT JOIN pg_catalog.pg_collation co ON co.oid=x.oid
  LEFT JOIN pg_catalog.pg_namespace cn ON cn.oid=co.collnamespace) AS collations
FROM pg_catalog.pg_index i JOIN pg_catalog.pg_class c ON c.oid=i.indrelid
JOIN pg_catalog.pg_namespace cn ON cn.oid=c.relnamespace JOIN pg_catalog.pg_class ic ON ic.oid=i.indexrelid
JOIN pg_catalog.pg_namespace n ON n.oid=ic.relnamespace JOIN pg_catalog.pg_am am ON am.oid=ic.relam
WHERE cn.nspname='public' AND c.relname IN """
    + _TARGETS
    + " ORDER BY c.relname,ic.relname"
)
_QUERIES = {
    "role": _ROLE_SQL,
    "tables": _TABLE_SQL,
    "functions": _FUNCTION_SQL,
    "columns": _COLUMN_SQL,
    "triggers": _TRIGGER_SQL,
    "constraints": _CONSTRAINT_SQL,
    "indexes": _INDEX_SQL,
}
_FIELDS = {
    "role": "direct_login login_oid privileged member_other table_access can_create fsync full_page_writes synchronous_commit replication_role catalog_search_path recovery server_version_num database_oid database_name server_address server_port server_started",
    "tables": "relname table_oid owner_oid relkind persistence rls force_rls relispartition replica_identity inherited foreign_acl foreign_column_acl",
    "functions": "function_oid proname prosrc prosecdef lanname return_type return_namespace proretset argtypes volatility proconfig owner_oid proisstrict parallel proleakproof kind support pronargdefaults variadic proargmodes argument_namespaces can_execute acl",
    "columns": "relname attnum attname type_name type_namespace atttypmod attnotnull attisdropped collation_oid collation_name collation_namespace identity generated default_expression unsafe_default",
    "triggers": "relname trigger_oid function_oid tgname tgtype enabled tgisinternal constraint_oid tgnargs tgargs qualification tgoldtable tgnewtable proname function_namespace tgdeferrable tginitdeferred",
    "constraints": "relname constraint_oid index_oid conname kind convalidated condeferrable condeferred connoinherit conislocal coninhcount conkey confkey referenced_table referenced_namespace update_action delete_action match_type expression unsafe_dependencies fk_triggers",
    "indexes": "relname index_oid index_name owner_oid relkind persistence index_namespace amname indisunique indisprimary indisexclusion indisvalid indisready indislive indimmediate indnatts indnkeyatts keys options expression predicate opclasses collations",
}


class PublicReceiptClockCustodyError(ValueError):
    """Fixed safe code; never expose driver, SQL, URL or catalog row values."""


def _deny():
    raise PublicReceiptClockCustodyError("public_receipt_clock_deployment_denied")


def _canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _eq(left, right):
    return _canonical(left) == _canonical(right)


def _oid(value):
    if type(value) is not int or not 0 < value < 2**32:
        _deny()
    return value


def _context():
    try:
        task = asyncio.current_task()
    except RuntimeError:
        _deny()
    if task is None or task.done() or task.cancelling():
        _deny()
    return os.getpid(), threading.get_ident(), asyncio.get_running_loop(), task


@dataclass(frozen=True, slots=True)
class _Pins:
    repositories: tuple
    factories: tuple
    engines: tuple
    options: tuple


def _native_query_engine(engine):
    """Do not let instance hooks replace the reviewed native query/decoder.

    The shared native-engine guard remains unchanged. These additional local
    checks close mutable instance creator/dialect/JSON hooks used by this new
    metadata path. Arbitrary shared class/code replacement is still outside
    the restricted application-role threat model.
    """
    if not _native_engine(engine):
        return False
    sync = engine.sync_engine
    pool = sync.pool
    dialect = engine.dialect
    if (
        pool._invoke_creator is not pool._creator
        or dialect._json_deserializer is not None
        or dialect._json_serializer is not None
    ):
        return False
    for item in (sync, dialect, dialect.dbapi, pool):
        for key, value in vars(item).items():
            if callable(value) and not (
                item is pool
                and key in ("_creator_arg", "_invoke_creator")
                and value is pool._creator
            ):
                return False
    return True


def _pins(witness, observation, old=None):
    repositories = (witness, observation)
    factories, engines, options = [], [], []
    for repository, cls, fields in zip(
        repositories,
        (PublicReceiptWitnessRepository, PublicReceiptPostReadRepository),
        ({"session_factory"}, {"session_factory", "witness_repository"}),
        strict=True,
    ):
        if type(repository) is not cls or set(vars(repository)) != fields:
            _deny()
        for name, expected in _METHODS[cls].items():
            bound = getattr(repository, name)
            if vars(cls).get(name) is not expected or not (
                type(expected) is staticmethod
                and bound is expected.__func__
                or type(expected) is FunctionType
                and type(bound) is MethodType
                and bound.__self__ is repository
                and bound.__func__ is expected
            ):
                _deny()
        factory = repository.session_factory
        if (
            type(factory) is not async_sessionmaker
            or set(vars(factory)) != {"kw", "class_"}
            or factory.class_ is not AsyncSession
            or type(factory.kw) is not dict
            or not set(factory.kw) <= {"bind", "autoflush", "expire_on_commit"}
            or any(
                key in factory.kw and type(factory.kw[key]) is not bool
                for key in ("autoflush", "expire_on_commit")
            )
        ):
            _deny()
        engine = factory.kw.get("bind")
        if type(engine) is not AsyncEngine or not _native_query_engine(engine):
            _deny()
        factories.append(factory)
        engines.append(engine)
        options.append(tuple(sorted(factory.kw.items())))
    if observation.witness_repository is not witness:
        _deny()
    result = _Pins(repositories, tuple(factories), tuple(engines), tuple(options))
    if old is not None and (
        type(old) is not _Pins
        or old.options != result.options
        or any(
            a is not b
            for group, new in (
                (old.repositories, result.repositories),
                (old.factories, result.factories),
                (old.engines, result.engines),
            )
            for a, b in zip(group, new, strict=True)
        )
    ):
        _deny()
    return result


def _inventory(rows, key, expected):
    if (
        type(rows) is not list
        or len(rows) != len(expected)
        or any(type(row) is not dict for row in rows)
    ):
        _deny()
    if {row[key] for row in rows} != set(expected):
        _deny()
    return {row[key]: row for row in rows}


def _check_columns(tree, table):
    names = set()

    def walk(node):
        if type(node) is tuple:
            if len(node) == 2 and node[0] == "column":
                names.add(node[1])
            else:
                for item in node:
                    walk(item)

    walk(tree)
    return {
        index for index, (name, _, _) in enumerate(COLUMNS[table], 1) if name in names
    }


def _validate_catalog(catalog, kind):
    """Pure validator only; passing a synthetic dictionary never issues owner."""
    if (
        type(catalog) is not dict
        or set(catalog) != set(_QUERIES)
        or kind not in ("witness", "observation")
    ):
        _deny()
    for category, fields in _FIELDS.items():
        rows = [catalog[category]] if category == "role" else catalog[category]
        if (
            type(rows) is not list
            or not 1 <= len(rows) <= 128
            or any(
                type(row) is not dict or set(row) != set(fields.split()) for row in rows
            )
        ):
            _deny()
    role = catalog["role"]
    if (
        type(role) is not dict
        or not all(
            role.get(key) is False
            for key in (
                "privileged",
                "member_other",
                "table_access",
                "can_create",
                "recovery",
            )
        )
        or role.get("direct_login") is not True
    ):
        _deny()
    if (
        any(
            role.get(key) != "on"
            for key in ("fsync", "full_page_writes", "synchronous_commit")
        )
        or role.get("replication_role") != "origin"
        or role.get("catalog_search_path") != "pg_catalog"
        or role.get("server_version_num") != "170011"
    ):
        _deny()
    for key in ("login_oid", "database_oid"):
        _oid(role[key])
    if (
        type(role.get("database_name")) is not str
        or not 1 <= len(role["database_name"]) <= 63
        or type(role.get("server_address")) is not str
        or not 1 <= len(role["server_address"]) <= 128
        or type(role.get("server_port")) is not int
        or not 1 <= role["server_port"] <= 65535
    ):
        _deny()
    stamp = role.get("server_started")
    if (
        type(stamp) is not datetime
        or stamp.tzinfo is None
        or stamp.utcoffset() is None
        or stamp.utcoffset().total_seconds() != 0
    ):
        _deny()
    tables = _inventory(catalog["tables"], "relname", TABLES)
    for row in tables.values():
        _oid(row["owner_oid"])
        _oid(row["table_oid"])
        if (
            row["owner_oid"] == role["login_oid"]
            or row["relkind"] != "r"
            or row["persistence"] != "p"
            or row["replica_identity"] != "d"
            or any(
                row[key] is not False
                for key in (
                    "rls",
                    "force_rls",
                    "relispartition",
                    "inherited",
                    "foreign_acl",
                    "foreign_column_acl",
                )
            )
        ):
            _deny()
    if tables[WITNESS]["owner_oid"] != tables[OBSERVATION]["owner_oid"]:
        _deny()
    owner_oid = tables[WITNESS]["owner_oid"]
    functions = _inventory(catalog["functions"], "proname", FUNCTION_HASHES)
    for name, row in functions.items():
        _oid(row["function_oid"])
        _oid(row["owner_oid"])
        if type(row["prosrc"]) is not str or not 1 <= len(row["prosrc"]) <= 65536:
            _deny()
        actual = (
            _sha(row["prosrc"].encode()),
            row["prosecdef"],
            row["lanname"],
            row["return_type"],
            row["proretset"],
            row["argtypes"],
            row["volatility"],
        )
        if (
            not _eq(actual, (FUNCTION_HASHES[name], *FUNCTION_METADATA[name]))
            or row["owner_oid"] != owner_oid
            or row["return_namespace"]
            != ("public" if row["return_type"] in TABLES else "pg_catalog")
            or row["proconfig"] != ["search_path=pg_catalog, pg_temp"]
        ):
            _deny()
        if (
            row["proisstrict"] is not False
            or row["parallel"] != "u"
            or row["proleakproof"] is not False
            or row["kind"] != "f"
            or type(row["support"]) is not int
            or row["support"] != 0
            or type(row["pronargdefaults"]) is not int
            or row["pronargdefaults"] != 0
            or type(row["variadic"]) is not int
            or row["variadic"] != 0
            or row["proargmodes"] is not None
            or not _eq(
                row["argument_namespaces"],
                ["pg_catalog"]
                * (0 if not row["argtypes"] else len(row["argtypes"].split(", "))),
            )
        ):
            _deny()
        permitted = name.endswith(("_append", "_read")) and (
            (kind == "witness") == name.startswith("public_receipt_witness_")
        )
        if row["can_execute"] is not permitted:
            _deny()
    for table in TABLES:
        columns = [row for row in catalog["columns"] if row["relname"] == table]
        if len(columns) != len(COLUMNS[table]):
            _deny()
        for ordinal, (row, expected) in enumerate(
            zip(columns, COLUMNS[table], strict=True), 1
        ):
            textual = expected[1] == "text" or expected[1].startswith(
                "character varying"
            )
            if textual:
                _oid(row["collation_oid"])
                if (
                    row["collation_name"] != "default"
                    or row["collation_namespace"] != "pg_catalog"
                ):
                    _deny()
            elif (
                type(row["collation_oid"]) is not int
                or row["collation_oid"] != 0
                or row["collation_name"] is not None
                or row["collation_namespace"] is not None
            ):
                _deny()
            if (
                not _eq((row["attname"], row["type_name"], row["attnotnull"]), expected)
                or type(row["attnum"]) is not int
                or row["attnum"] != ordinal
                or type(row["atttypmod"]) is not int
                or row["type_namespace"] != "pg_catalog"
                or row["attisdropped"] is not False
                or row["identity"] != ""
                or row["generated"] != ""
                or row["unsafe_default"] is not False
                or row["default_expression"]
                != (
                    "clock_timestamp()"
                    if table == WITNESS and expected[0] == "recorded_at"
                    else None
                )
                or expected[0] in ("root_device", "root_inode")
                and (type(row["atttypmod"]) is not int or row["atttypmod"] != -1)
            ):
                _deny()
        prefix = (
            "public_receipt_witness" if table == WITNESS else "public_receipt_post_read"
        )
        triggers = _inventory(
            [row for row in catalog["triggers"] if row["relname"] == table],
            "tgname",
            {
                prefix + suffix
                for suffix in ("_insert_guard", "_immutable", "_no_truncate")
            },
        )
        for name, row in triggers.items():
            _oid(row["trigger_oid"])
            _oid(row["function_oid"])
            suffix = name.removeprefix(prefix)
            if (
                not _eq(
                    (row["tgtype"], row["constraint_oid"], row["tgnargs"]),
                    (
                        {"_insert_guard": 7, "_immutable": 27, "_no_truncate": 34}[
                            suffix
                        ],
                        0,
                        0,
                    ),
                )
                or row["proname"]
                != prefix
                + ("_insert_guard" if suffix == "_insert_guard" else "_immutable")
                or row["enabled"] != "O"
                or row["function_namespace"] != "public"
                or type(row["tgargs"]) is not bytes
                or row["tgargs"] != b""
                or row["function_oid"] != functions[row["proname"]]["function_oid"]
                or any(
                    row[key] is not False
                    for key in ("tgisinternal", "tgdeferrable", "tginitdeferred")
                )
                or any(
                    row[key] is not None
                    for key in ("qualification", "tgoldtable", "tgnewtable")
                )
            ):
                _deny()
        indexes = _inventory(
            [row for row in catalog["indexes"] if row["relname"] == table],
            "index_name",
            INDEXES[table],
        )
        constraints = _inventory(
            [row for row in catalog["constraints"] if row["relname"] == table],
            "conname",
            set(CHECKS[table]) | set(KEYS[table]),
        )
        for name, row in constraints.items():
            _oid(row["constraint_oid"])
            if (
                row["convalidated"] is not True
                or row["conislocal"] is not True
                or row["connoinherit"] is not (row["kind"] != "c")
                or row["condeferrable"] is not False
                or row["condeferred"] is not False
                or type(row["coninhcount"]) is not int
                or row["coninhcount"] != 0
                or row["unsafe_dependencies"] is not False
            ):
                _deny()
            if name in CHECKS[table]:
                expected_tree = expression_tree(CHECKS[table][name], table)
                if (
                    row["kind"] != "c"
                    or expression_tree(row["expression"], table) != expected_tree
                ):
                    _deny()
                key_columns = row["conkey"]
                expected_keys = _check_columns(expected_tree, table)
                if (
                    type(key_columns) is not list
                    or any(type(key) is not int for key in key_columns)
                    or len(key_columns) != len(expected_keys)
                    or set(key_columns) != expected_keys
                    or type(row["index_oid"]) is not int
                    or row["index_oid"] != 0
                ):
                    _deny()
            elif (
                not _eq((row["kind"], row["conkey"]), KEYS[table][name])
                or row["expression"] is not None
            ):
                _deny()
            if row["kind"] == "f":
                expected_triggers = sorted(
                    [
                        [f, "pg_catalog", t, "O", 0, "", True, False, False]
                        for f, t in (
                            ("RI_FKey_check_ins", 5),
                            ("RI_FKey_check_upd", 17),
                            ("RI_FKey_noaction_del", 9),
                            ("RI_FKey_noaction_upd", 17),
                        )
                    ]
                )
                if (
                    row["referenced_table"] != WITNESS
                    or row["referenced_namespace"] != "public"
                    or not _eq(row["confkey"], [1, 2])
                    or (row["update_action"], row["delete_action"], row["match_type"])
                    != ("a", "a", "s")
                    or not _eq(row["fk_triggers"], expected_triggers)
                ):
                    _deny()
                referenced_index = next(
                    (
                        r
                        for r in catalog["indexes"]
                        if r["relname"] == WITNESS
                        and r["index_name"] == "pk_public_receipt_witness_revisions"
                    ),
                    None,
                )
                if (
                    referenced_index is None
                    or type(row["index_oid"]) is not int
                    or row["index_oid"] != referenced_index["index_oid"]
                ):
                    _deny()
            elif row["fk_triggers"] != [] or (
                row["confkey"] is not None
                or row["referenced_table"] is not None
                or row["referenced_namespace"] is not None
                or (row["update_action"], row["delete_action"], row["match_type"])
                != (" ", " ", " ")
            ):
                _deny()
            if row["kind"] in ("p", "u"):
                owned_index = indexes[name]
                if (
                    type(row["index_oid"]) is not int
                    or row["index_oid"] != owned_index["index_oid"]
                ):
                    _deny()
        for name, row in indexes.items():
            _oid(row["index_oid"])
            _oid(row["owner_oid"])
            keys, opclasses, predicate = INDEXES[table][name]
            if (
                not _eq(row["keys"], keys)
                or not _eq(row["options"], [0] * len(keys))
                or not _eq(
                    row["opclasses"], [[name, "pg_catalog"] for name in opclasses]
                )
                or not _eq(
                    row["collations"],
                    ["default" if name == "text_ops" else "none" for name in opclasses],
                )
                or type(row["indnatts"]) is not int
                or type(row["indnkeyatts"]) is not int
                or row["indnatts"] != len(keys)
                or row["indnkeyatts"] != len(keys)
                or row["owner_oid"] != owner_oid
                or row["relkind"] != "i"
                or row["persistence"] != "p"
                or row["index_namespace"] != "public"
                or row["amname"] != "btree"
                or row["expression"] is not None
                or any(
                    row[key] is not True
                    for key in (
                        "indisunique",
                        "indisvalid",
                        "indisready",
                        "indislive",
                        "indimmediate",
                    )
                )
                or row["indisexclusion"] is not False
                or row["indisprimary"] is not name.startswith("pk_")
            ):
                _deny()
            if (
                predicate is None
                and row["predicate"] is not None
                or predicate is not None
                and expression_tree(row["predicate"], table)
                != expression_tree(predicate, table)
            ):
                _deny()
    for key in ("columns", "triggers", "constraints", "indexes"):
        if any(row["relname"] not in TABLES for row in catalog[key]):
            _deny()
    return tables, functions


def _validate_pair(catalogs):
    outputs = [
        _validate_catalog(catalog, kind)
        for catalog, kind in zip(catalogs, ("witness", "observation"), strict=True)
    ]
    left, right = (catalog["role"] for catalog in catalogs)
    scope_fields = (
        "database_oid",
        "database_name",
        "server_address",
        "server_port",
        "server_started",
        "server_version_num",
    )
    if (
        any(left[key] != right[key] for key in scope_fields)
        or left["login_oid"] == right["login_oid"]
    ):
        _deny()
    if not _eq(outputs[0][0], outputs[1][0]):
        _deny()
    for category in ("columns", "triggers", "constraints", "indexes"):
        left_rows, right_rows = (catalog[category] for catalog in catalogs)
        if category == "triggers":
            left_rows = [{**row, "tgargs": row["tgargs"].hex()} for row in left_rows]
            right_rows = [{**row, "tgargs": row["tgargs"].hex()} for row in right_rows]
        if not _eq(left_rows, right_rows):
            _deny()
    owner = outputs[0][0][WITNESS]["owner_oid"]
    for index, (_, functions) in enumerate(outputs):
        for name, row in functions.items():
            login = (
                left["login_oid"]
                if name.startswith("public_receipt_witness_")
                else right["login_oid"]
            )
            expected = [[owner, owner, "EXECUTE", False]] + (
                [[login, owner, "EXECUTE", False]]
                if name.endswith(("_append", "_read"))
                else []
            )
            if not _eq(row["acl"], sorted(expected)):
                _deny()
            counterpart = outputs[1 - index][1][name]
            if not _eq(
                {k: v for k, v in row.items() if k != "can_execute"},
                {k: v for k, v in counterpart.items() if k != "can_execute"},
            ):
                _deny()


@dataclass(frozen=True, slots=True)
class ClockCustodyScopeV1:
    database_oid: int
    database_name_sha256: str
    server_address_sha256: str
    server_port: int
    server_started: str
    server_version_num: str
    login_oid: int
    owner_oid: int


@dataclass(frozen=True, slots=True)
class ClockCustodyDeploymentReadbackV1:
    """Audit facts only. Constructing or saving these never supplies an owner."""

    fingerprint: str
    scopes: tuple[ClockCustodyScopeV1, ClockCustodyScopeV1] = field(repr=False)
    catalog_sha256: tuple[str, str]
    absolute_utc_verified: bool = field(default=False, init=False)
    host_binary_attested: bool = field(default=False, init=False)
    independently_protected: bool = field(default=False, init=False)
    execution_authority: bool = field(default=False, init=False)

    def canonical_json_bytes(self):
        raw = _canonical(
            {
                "schema": "ctcc.public_receipt_clock_deployment_readback.v1",
                "fingerprint": self.fingerprint,
                "scopes": [
                    {
                        key: getattr(scope, key)
                        for key in ClockCustodyScopeV1.__dataclass_fields__
                    }
                    for scope in self.scopes
                ],
                "catalog_sha256": self.catalog_sha256,
                "absolute_utc_verified": False,
                "host_binary_attested": False,
                "independently_protected": False,
                "execution_authority": False,
            }
        )
        if len(raw) > 65536:
            _deny()
        return raw


class OwnedClockCustodyDeploymentV1:
    __slots__ = ("__weakref__",)

    def __init__(self, issuer):
        if issuer is not _ISSUER:
            _deny()

    def __copy__(self):
        _deny()

    def __deepcopy__(self, memo):
        _deny()

    def __reduce_ex__(self, protocol):
        _deny()


@dataclass(slots=True)
class _State:
    pins: _Pins
    private_pins: _Pins
    context: tuple
    started: int
    readback: ClockCustodyDeploymentReadbackV1 | None = None
    busy: bool = False
    poisoned: bool = False


def _check(state):
    if (
        state.poisoned
        or state.context != _context()
        or not 0 <= time.monotonic_ns() - state.started <= _LIFETIME_NS
    ):
        _deny()
    _pins(*state.pins.repositories, state.pins)
    _pins(*state.private_pins.repositories, state.private_pins)


async def _collect(state):
    catalogs = []
    async with asyncio.timeout(5):
        for index, factory in enumerate(state.private_pins.factories):
            _check(state)
            async with factory() as session, session.begin():
                if type(session) is not AsyncSession:
                    _deny()
                await AsyncSession.execute(
                    session,
                    text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"),
                )
                _check(state)
                # Pin catalog name resolution before old role guards and all
                # metadata functions/casts, even if engine options requested a
                # hostile caller search_path. No application/global state edit.
                await AsyncSession.execute(
                    session, text("SET LOCAL search_path = pg_catalog")
                )
                _check(state)
                repository = state.private_pins.repositories[index]
                await type(repository)._role_guard(session)
                _check(state)
                catalog = {}
                for name, statement in _QUERIES.items():
                    rows = (
                        (await AsyncSession.execute(session, statement))
                        .mappings()
                        .all()
                    )
                    _check(state)
                    if len(rows) > 128:
                        _deny()
                    values = [dict(row) for row in rows]
                    if name == "role":
                        if len(values) != 1:
                            _deny()
                        catalog[name] = values[0]
                    else:
                        catalog[name] = values
            _check(state)
            catalogs.append(catalog)
    _validate_pair(catalogs)
    serial = []
    scopes = []
    for catalog in catalogs:
        role = dict(catalog["role"])
        role["server_started"] = role["server_started"].isoformat()
        serial.append({**catalog, "role": role})
        owner = catalog["tables"][0]["owner_oid"]
        scopes.append(
            ClockCustodyScopeV1(
                **{
                    key: role[key]
                    for key in (
                        "database_oid",
                        "server_port",
                        "server_started",
                        "server_version_num",
                        "login_oid",
                    )
                },
                database_name_sha256=_sha(role["database_name"].encode()),
                server_address_sha256=_sha(role["server_address"].encode()),
                owner_oid=owner,
            )
        )
    # tgargs are native bytes; canonical catalog fingerprint records exact hex.
    for catalog in serial:
        catalog["triggers"] = [
            {**row, "tgargs": bytes(row["tgargs"]).hex()} for row in catalog["triggers"]
        ]
    digests = tuple(_sha(_canonical(catalog)) for catalog in serial)
    scope_values = [
        {key: getattr(scope, key) for key in ClockCustodyScopeV1.__dataclass_fields__}
        for scope in scopes
    ]
    result = ClockCustodyDeploymentReadbackV1(
        _sha(_canonical([scope_values, digests])), tuple(scopes), digests
    )
    _check(state)
    return result


async def acquire_clock_custody_deployment_v1(
    *, witness_repository, observation_repository
):
    try:
        pins = _pins(witness_repository, observation_repository)
        factories = tuple(
            async_sessionmaker(class_=AsyncSession, **dict(options))
            for options in pins.options
        )
        witness = PublicReceiptWitnessRepository(factories[0])
        observation = PublicReceiptPostReadRepository(factories[1], witness)
        state = _State(
            pins, _pins(witness, observation), _context(), time.monotonic_ns()
        )
        state.readback = await _collect(state)
        owner = OwnedClockCustodyDeploymentV1(_ISSUER)
        _OWNERS[owner] = state
        return owner
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 -- Fixed safe code, never raw SQL/connection data.
        raise PublicReceiptClockCustodyError(
            "public_receipt_clock_deployment_denied"
        ) from None


async def recheck_clock_custody_deployment_v1(
    owner, *, witness_repository, observation_repository
):
    state = _OWNERS.get(owner) if type(owner) is OwnedClockCustodyDeploymentV1 else None
    if state is None or state.busy:
        _deny()
    try:
        _check(state)
        _pins(witness_repository, observation_repository, state.pins)
        state.busy = True
        current = await _collect(state)
        if current.canonical_json_bytes() != state.readback.canonical_json_bytes():
            _deny()
        return current
    except asyncio.CancelledError:
        state.poisoned = True
        raise
    except Exception:  # noqa: BLE001 -- Poison owner and redact native driver details.
        state.poisoned = True
        raise PublicReceiptClockCustodyError(
            "public_receipt_clock_deployment_denied"
        ) from None
    finally:
        state.busy = False


def readback_clock_custody_deployment_v1(owner):
    state = _OWNERS.get(owner) if type(owner) is OwnedClockCustodyDeploymentV1 else None
    if state is None or state.busy or state.readback is None:
        _deny()
    _check(state)
    return state.readback
