"""Read-only retained0017 inventory and source upgrade plan; never migrate.

Private pins identify a separately preserved clone. A matching head or two equal
reads do not prove exchange flatness, exclude concurrent writers, or authorize
DDL. Raw account rows, credentials and connection routes never reach stdout.
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import hashlib
import io
import json
import re
from contextlib import redirect_stdout
from dataclasses import dataclass
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine
from sqlalchemy.pool import NullPool

from app.database.native_pg_tls_v1 import (
    _read_bounded_regular_file_v1,
    verified_native_pg_tls_context_v1,
)
from app.domain import native_database_profile_v1 as profiles
from scripts import native_owner_retained_rows_v2 as retained_rows
from scripts import native_owner_route_preflight_0056_v1 as route
from scripts import native_owner_upgrade_0056_v1 as archive
from scripts.evidence_json import read_evidence_json
from scripts.native_owner_target_plan_0056_v1 import _require_private_file

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "ctcc.retained0017.readonly_owner_plan.v1"
PINS_SCHEMA = "ctcc.retained0017.readonly_owner_pins.v1"
MAX_ROWS = 1_000_000
MAX_TABLE_BYTES = 256 * 1024 * 1024
MAX_TOTAL_BYTES = 1024 * 1024 * 1024
MAX_RELATIONS = 512
MAX_CATALOG_BYTES = 8 * 1024 * 1024
_HEX = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_GIT = re.compile(r"[0-9a-f]{40}\Z", re.ASCII)
_NAME = re.compile(r"[a-z_][a-z0-9_]{0,62}\Z", re.ASCII)
_READ_SETTINGS = (
    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY",
    "SET LOCAL search_path TO pg_catalog, public, pg_temp",
    "SET LOCAL TIME ZONE 'UTC'",
    "SET LOCAL lock_timeout='1000ms'",
    "SET LOCAL statement_timeout='30000ms'",
    "SET LOCAL idle_in_transaction_session_timeout='30000ms'",
)

# All public objects are inventoried, including unexpected grants, disabled
# triggers, invalid indexes and added/dropped attributes. Actual OIDs remain in
# this physical-clone receipt; none is normalized into a source/schema claim.
CATALOG_SQL = text("""
SELECT jsonb_build_object(
 'relations',(SELECT jsonb_agg(to_jsonb(c) ORDER BY c.oid)
  FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public'),
 'attributes',(SELECT jsonb_agg(jsonb_build_array(to_jsonb(a),to_jsonb(d)) ORDER BY a.attrelid,a.attnum)
  FROM pg_catalog.pg_attribute a JOIN pg_catalog.pg_class c ON c.oid=a.attrelid
  JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace LEFT JOIN pg_catalog.pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
  WHERE n.nspname='public' AND a.attnum>0),
 'constraints',(SELECT jsonb_agg(to_jsonb(c) ORDER BY c.oid)
  FROM pg_catalog.pg_constraint c JOIN pg_catalog.pg_namespace n ON n.oid=c.connamespace WHERE n.nspname='public'),
 'indexes',(SELECT jsonb_agg(to_jsonb(i) ORDER BY i.indexrelid)
  FROM pg_catalog.pg_index i JOIN pg_catalog.pg_class c ON c.oid=i.indrelid JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public'),
 'triggers',(SELECT jsonb_agg(to_jsonb(t) ORDER BY t.oid)
  FROM pg_catalog.pg_trigger t JOIN pg_catalog.pg_class c ON c.oid=t.tgrelid JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public'),
 'functions',(SELECT jsonb_agg(to_jsonb(p) ORDER BY p.oid) FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public'),
 'types',(SELECT jsonb_agg(to_jsonb(t) ORDER BY t.oid) FROM pg_catalog.pg_type t JOIN pg_catalog.pg_namespace n ON n.oid=t.typnamespace WHERE n.nspname='public'),
 'enums',(SELECT jsonb_agg(to_jsonb(e) ORDER BY e.enumtypid,e.enumsortorder) FROM pg_catalog.pg_enum e JOIN pg_catalog.pg_type t ON t.oid=e.enumtypid JOIN pg_catalog.pg_namespace n ON n.oid=t.typnamespace WHERE n.nspname='public'),
 'sequences',(SELECT jsonb_agg(to_jsonb(s) ORDER BY s.seqrelid) FROM pg_catalog.pg_sequence s JOIN pg_catalog.pg_class c ON c.oid=s.seqrelid JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public'),
 'dependencies',(SELECT jsonb_agg(to_jsonb(d) ORDER BY d.classid,d.objid,d.objsubid,d.refclassid,d.refobjid,d.refobjsubid,d.deptype)
  FROM pg_catalog.pg_depend d WHERE (d.classid='pg_catalog.pg_class'::regclass AND d.objid IN (SELECT c.oid FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public'))
   OR (d.refclassid='pg_catalog.pg_class'::regclass AND d.refobjid IN (SELECT c.oid FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public'))),
 'default_acl',(SELECT jsonb_agg(to_jsonb(d) ORDER BY d.oid) FROM pg_catalog.pg_default_acl d),
 'namespace',(SELECT to_jsonb(n) FROM pg_catalog.pg_namespace n WHERE n.nspname='public'),
 'database',(SELECT to_jsonb(d)-'datfrozenxid'-'datminmxid' FROM pg_catalog.pg_database d WHERE d.datname=current_database()),
 'owner',(SELECT to_jsonb(r) FROM pg_catalog.pg_roles r WHERE r.rolname=current_user),
 'memberships',(SELECT jsonb_agg(to_jsonb(m) ORDER BY m.roleid,m.member,m.grantor) FROM pg_catalog.pg_auth_members m),
 'role_settings',(SELECT jsonb_agg(to_jsonb(s) ORDER BY s.setdatabase,s.setrole) FROM pg_catalog.pg_db_role_setting s)
)::text
""")
RELATIONS_SQL = text("""SELECT c.relname::text,c.oid::bigint,c.relkind::text,c.relpersistence::text,
 c.relowner::bigint,c.relrowsecurity,c.relforcerowsecurity,c.relispartition,
 COALESCE((SELECT jsonb_agg(a.attname ORDER BY a.attnum) FROM pg_catalog.pg_attribute a
 WHERE a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped),'[]'::jsonb)
 FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
 WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m','f','S') ORDER BY c.relname""")
SAFE_ROW_TYPES_SQL = text(
    """SELECT
 NOT EXISTS(SELECT 1 FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
  WHERE n.nspname='public' AND c.relkind IN ('r','S') AND c.relhasrules)
 AND NOT EXISTS(SELECT 1 FROM pg_catalog.pg_inherits i JOIN pg_catalog.pg_class c ON c.oid=i.inhrelid OR c.oid=i.inhparent
  JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public')
 AND NOT EXISTS(SELECT 1 FROM pg_catalog.pg_attribute a JOIN pg_catalog.pg_class c ON c.oid=a.attrelid
  JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
  WHERE n.nspname='public' AND c.relkind IN ('r','S') AND a.attnum>0 AND NOT a.attisdropped
   AND a.atttypid NOT IN ("""
    + ",".join(str(oid) for oid in sorted(retained_rows._NATIVE_SCALAR_OIDS))
    + """))
"""
    + " AND "
    + retained_rows.heap_am.ALL_PUBLIC_TABLES_HEAP_V1
)


class Retained0017PlanRefused(ValueError):
    """Fixed safe refusal; no DSN, catalog, row, identifier or path echo."""


class _PrivateArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        # argparse's default error includes unknown arguments and invalid values.
        # They may be an accidentally pasted DSN; discard the original message.
        raise Retained0017PlanRefused("retained0017_cli_inputs_refused") from None


def _need(value, code="retained0017_readonly_plan_refused"):
    if value is not True:
        raise Retained0017PlanRefused(code) from None


def _canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _digest(value, *, git=False):
    return type(value) is str and (_GIT if git else _HEX).fullmatch(value) is not None


@dataclass(frozen=True, slots=True, repr=False)
class RetainedOwnerRoute0017V1:
    source_manifest_sha256: str
    ca_sha256: str
    cluster_system_identifier_sha256: str
    host: str
    port: int
    database: str
    owner: str
    database_oid: int
    owner_oid: int
    database_head: str = "0017"


@dataclass(frozen=True, slots=True, repr=False)
class RetainedOwnerPins0017V1:
    source_commit: str
    source_tree: str
    source_archive_sha256: str
    preserved_backup_sha256: str
    route: RetainedOwnerRoute0017V1
    expected_catalog_sha256: str | None = None


def _checked_pins(pins):
    _need(
        type(pins) is RetainedOwnerPins0017V1
        and type(pins.route) is RetainedOwnerRoute0017V1
    )
    p = pins.route
    _need(
        _digest(pins.source_commit, git=True)
        and _digest(pins.source_tree, git=True)
        and all(
            _digest(v)
            for v in (
                pins.source_archive_sha256,
                pins.preserved_backup_sha256,
                p.source_manifest_sha256,
                p.ca_sha256,
                p.cluster_system_identifier_sha256,
            )
        )
        and (
            pins.expected_catalog_sha256 is None
            or _digest(pins.expected_catalog_sha256)
        )
        and p.database_head == "0017"
        and type(p.host) is str
        and bool(p.host)
        and type(p.database) is str
        and bool(p.database)
        and type(p.owner) is str
        and bool(p.owner)
        and type(p.port) is int
        and 1 <= p.port <= 65535
        and all(type(v) is int and v > 0 for v in (p.database_oid, p.owner_oid))
    )
    return pins


def _column_expressions(arguments, module_ast):
    result = []
    for column in arguments:
        if isinstance(column, ast.Starred):
            call = column.value
            _need(
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Name)
                and call.func.id == "_timestamps"
                and not call.args
                and not call.keywords
            )
            helpers = [
                node
                for node in module_ast.body
                if isinstance(node, ast.FunctionDef) and node.name == "_timestamps"
            ]
            _need(len(helpers) == 1 and len(helpers[0].body) == 1)
            returned = helpers[0].body[0]
            _need(
                isinstance(returned, ast.Return)
                and isinstance(returned.value, ast.List)
            )
            result.extend(returned.value.elts)
        else:
            result.append(column)
    return result


def _original_operations(statements, bindings=None):
    """Expand only source-literal loops, in DDL order, without executing code."""
    bound = {} if bindings is None else bindings
    for statement in statements:
        if isinstance(statement, ast.For):
            _need(
                isinstance(statement.target, ast.Name)
                and isinstance(statement.iter, (ast.List, ast.Tuple))
                and not statement.orelse
            )
            for item in statement.iter.elts:
                _need(isinstance(item, ast.Constant) and type(item.value) is str)
                yield from _original_operations(
                    statement.body, bound | {statement.target.id: item.value}
                )
        elif isinstance(statement, ast.Expr):
            node = statement.value
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "op"
            ):
                yield node, bound
            else:
                _need(isinstance(node, ast.Constant) and type(node.value) is str)
        else:
            _need(isinstance(statement, ast.Pass))


def _source_plan(root=ROOT):
    """Read the actual58 graph/bytes; AST shape is only a minimum17 contract."""
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("path_separator", "os")
    config.set_main_option("script_location", str(root / "migrations"))
    graph = ScriptDirectory.from_config(config)
    _need(tuple(graph.get_heads()) == ("0058",))
    chain = tuple(reversed(tuple(graph.iterate_revisions("0055", "base"))))
    _need(tuple(r.revision for r in chain) == tuple(f"{i:04d}" for i in range(1, 56)))
    shape = {"alembic_version": ["version_num"]}
    files = []
    for revision in chain:
        path = Path(revision.path)
        _need(path.is_file() and not path.is_symlink() and not path.is_junction())
        raw = path.read_bytes()
        files.append([revision.revision, path.relative_to(root).as_posix(), _sha(raw)])
        if revision.revision > "0017":
            continue
        tree = ast.parse(raw.decode("utf-8"))
        bodies = [
            n
            for n in tree.body
            if isinstance(n, ast.FunctionDef) and n.name == "upgrade"
        ]
        _need(len(bodies) == 1)
        for node, bindings in _original_operations(bodies[0].body):
            if node.func.attr not in {"create_table", "add_column"}:
                _need(
                    node.func.attr not in {"drop_column", "drop_table", "rename_table"}
                )
                continue
            _need(
                node.args
                and isinstance(node.args[0], ast.Constant)
                and type(node.args[0].value) is str
            )
            name = node.args[0].value
            if node.func.attr == "create_table":
                _need(name not in shape)
                shape[name] = []
            _need(name in shape)
            for column in _column_expressions(node.args[1:], tree):
                if (
                    isinstance(column, ast.Call)
                    and isinstance(column.func, ast.Attribute)
                    and column.func.attr == "Column"
                ):
                    _need(
                        column.args
                        and (
                            isinstance(column.args[0], ast.Constant)
                            and type(column.args[0].value) is str
                            or isinstance(column.args[0], ast.Name)
                            and column.args[0].id in bindings
                        )
                    )
                    key = (
                        column.args[0].value
                        if isinstance(column.args[0], ast.Constant)
                        else bindings[column.args[0].id]
                    )
                    _need(key not in shape[name])
                    shape[name].append(key)
    _need(
        all(
            _NAME.fullmatch(name)
            and values
            and len(values) == len(set(values))
            and all(_NAME.fullmatch(c) for c in values)
            for name, values in shape.items()
        )
    )
    return {
        "source_head": "0058",
        "database_head_required": "0017",
        "target_head": "0055",
        "proposed_version_table_change": {
            "alembic_version.version_num": ["0017", "0055"]
        },
        "minimum17_columns": shape,
        "migrations": files,
        "mutation_implemented": False,
        "only_proposed_original_field_changes": {
            "0034": [
                "demo_automation_state.armed",
                "demo_automation_state.emergency_stop",
                "demo_automation_state.locked",
                "demo_automation_state.lock_reasons",
            ]
        },
        "requires_separate_0034_policy_review": True,
        "retained_0034_input_validation_performed": False,
        "known_0034_negative_transition": {
            "original_fields": {"armed": False, "emergency_stop": True, "locked": True},
            "new_fields": {"control_revision": 0, "restart_latch_required": True},
            "lock_reasons": {
                "operation": "append_one_exact_reason_to_original_list",
                "reason": "control_protocol_upgrade_requires_clear",
                "malformed_original": "REFUSE_WITHOUT_NORMALIZATION",
            },
            "all_other_original_fields_and_rows": "PRESERVE",
        },
        "execution_authority": False,
    }


def _prepared(pins, *, owner_dsn_file, ca_file, source_archive_file):
    _checked_pins(pins)
    _require_private_file(owner_dsn_file)
    with redirect_stdout(io.StringIO()):
        actual = route._source_for_profile(profiles.DB0058)
    _need(
        actual.actual_source_head == "0058"
        and actual.manifest_sha256 == pins.route.source_manifest_sha256
    )
    archive._verify_reviewed_source_archive(
        source_archive_file,
        source_commit=pins.source_commit,
        source_tree=pins.source_tree,
        source_archive_sha256=pins.source_archive_sha256,
        source_manifest_sha256=pins.route.source_manifest_sha256,
    )
    url = route._private_owner_url(owner_dsn_file)
    p = pins.route
    _need(
        (url.host, url.port, url.database, url.username)
        == (p.host, p.port, p.database, p.owner)
        and type(url.password) is str
        and bool(url.password)
    )
    tls = verified_native_pg_tls_context_v1(ca_file, p.ca_sha256)
    return url, tls, _source_plan()


def _checked_relations(rows, shape, owner_oid):
    _need(type(rows) is tuple and 0 < len(rows) <= MAX_RELATIONS)
    found = {}
    identities = set()
    for row in rows:
        _need(type(row) is tuple and len(row) == 9)
        name, oid, kind, persistence, owner, rls, forced, partition, columns = row
        _need(
            type(name) is str
            and _NAME.fullmatch(name) is not None
            and name not in found
            and type(oid) is int
            and oid > 0
            and oid not in identities
        )
        _need(
            type(kind) is str
            and kind in {"r", "S"}
            and type(persistence) is str
            and persistence == "p"
            and type(owner) is int
            and owner == owner_oid
            and rls is forced is partition is False
        )
        _need(
            type(columns) is list
            and all(type(c) is str and _NAME.fullmatch(c) is not None for c in columns)
        )
        if kind == "r":
            _need(
                name in shape and columns == shape[name],
                "retained0017_original_table_or_columns_changed",
            )
        found[name] = row
        identities.add(oid)
    _need(
        {k for k, v in found.items() if v[2] == "r"} == set(shape),
        "retained0017_original_table_inventory_changed",
    )
    return found


def _checked_table_result(row):
    _need(type(row) is tuple and len(row) == 3)
    count, size, digest = row
    _need(
        type(count) is int
        and 0 <= count <= MAX_ROWS
        and type(size) is int
        and 0 <= size <= MAX_TABLE_BYTES
        and _digest(digest),
        "retained0017_snapshot_capacity_or_hash_invalid",
    )
    return count, size, digest


def _row_expression(columns):
    try:
        return retained_rows.row_expression_v2(columns)
    except retained_rows.RetainedRowsV2Refused:
        _need(False)


async def _snapshot(connection, pins, plan):
    """Genuine current read-only owner transaction only; no arbitrary SQL input."""
    _need(
        type(connection) is AsyncConnection
        and connection.in_transaction()
        and connection.dialect.name == "postgresql"
        and connection.dialect.driver == "asyncpg"
    )
    # Inspect only pg_catalog before selecting even the version marker. A
    # caller-installed view, rewrite rule or custom type output function must
    # not run during discovery of an unknown historical schema.
    rows = tuple(tuple(row) for row in (await connection.execute(RELATIONS_SQL)).all())
    relations = _checked_relations(
        rows, plan["minimum17_columns"], pins.route.owner_oid
    )
    _need(
        await connection.scalar(SAFE_ROW_TYPES_SQL) is True,
        "retained0017_unreviewed_row_expression_or_type",
    )
    actor = tuple((await connection.execute(route._PROFILE_SQL)).one())
    archive._checked_profile(actor, pins.route, "0017", read_only=True)
    version = await connection.scalar(
        text("SELECT current_setting('server_version_num')::int")
    )
    _need(type(version) is int and 170000 <= version < 180000)
    _need(
        await connection.scalar(
            text(
                "SELECT NOT EXISTS(SELECT 1 FROM pg_catalog.pg_namespace WHERE nspname !~ '^pg_' AND nspname NOT IN ('public','information_schema'))"
                " AND NOT EXISTS(SELECT 1 FROM pg_catalog.pg_largeobject_metadata)"
                " AND NOT EXISTS(SELECT 1 FROM pg_catalog.pg_foreign_table)"
            )
        )
        is True,
        "retained0017_outside_source17_storage_scope",
    )
    catalog = await connection.scalar(CATALOG_SQL)
    _need(type(catalog) is str and 0 < len(catalog.encode()) <= MAX_CATALOG_BYTES)
    # Validate the exact observed JSON, then retain its canonical digest only.
    catalog_digest = _sha(_canonical(read_evidence_json(catalog.encode())))
    tables = []
    sequences = []
    total = 0
    for name, row in relations.items():
        qualified = 'public."' + name + '"'
        if row[2] == "S":
            value = tuple(
                (
                    await connection.execute(
                        text("SELECT last_value,is_called FROM " + qualified)
                    )
                ).one()
            )
            _need(len(value) == 2 and type(value[0]) is int and type(value[1]) is bool)
            sequences.append([name, row[1], value[0], value[1]])
            continue
        size_sql, hash_sql = retained_rows.row_multiset_sql_v2(name, row[8])
        count, size = tuple((await connection.execute(size_sql)).one())
        _need(
            type(count) is int
            and 0 <= count <= MAX_ROWS
            and type(size) is int
            and 0 <= size <= MAX_TABLE_BYTES
        )
        total += size
        _need(total <= MAX_TOTAL_BYTES)
        digest = await connection.scalar(hash_sql)
        _checked_table_result((count, size, digest))
        tables.append([name, row[1], list(row[8]), count, size, digest])
    _need(tuple((await connection.execute(route._PROFILE_SQL)).one()) == actor)
    _need(
        tuple(tuple(row) for row in (await connection.execute(RELATIONS_SQL)).all())
        == rows
    )
    # A sequence is not MVCC-protected. Re-read its actual value rather than
    # claim a repeatable-read snapshot covers concurrent nextval operations.
    for name, _oid, value, called in sequences:
        _need(
            tuple(
                (
                    await connection.execute(
                        text('SELECT last_value,is_called FROM public."' + name + '"')
                    )
                ).one()
            )
            == (value, called),
            "retained0017_sequence_changed",
        )
    return {
        "schema": "ctcc.retained0017.physical_snapshot.v1",
        "row_encoding": retained_rows.ROW_ENCODING,
        "database_head": "0017",
        "catalog_sha256": catalog_digest,
        "source17_minimum_shape_matched": True,
        "full_source17_schema_verified": False,
        "actor_sha256": _sha(_canonical(actor)),
        "tables": tables,
        "sequences": sequences,
        "total_row_bytes": total,
        "exchange_flat": None,
        "account_complete": False,
        "execution_authority": False,
        "admission": "DENY",
    }


def _join(first, second, pins, plan):
    _need(
        type(first) is dict and type(second) is dict and first == second,
        "retained0017_independent_snapshots_differ",
    )
    expected = pins.expected_catalog_sha256
    _need(
        expected is None or first["catalog_sha256"] == expected,
        "retained0017_reviewed_catalog_mismatch",
    )
    return {
        "schema": SCHEMA,
        "status": "OBSERVED_MATCH_DATA"
        if expected
        else "OBSERVED_UNREVIEWED_CATALOG_DATA",
        "source_commit": pins.source_commit,
        "source_tree": pins.source_tree,
        "source_archive_sha256": pins.source_archive_sha256,
        "source_manifest_sha256": pins.route.source_manifest_sha256,
        "preserved_backup_sha256": pins.preserved_backup_sha256,
        "snapshot": first,
        "snapshot_sha256": _sha(_canonical(first)),
        "source_plan": plan,
        "source_plan_sha256": _sha(_canonical(plan)),
        "independent_reads": 2,
        "two_reads_equal": True,
        "expected_catalog_pin_matched": expected is not None,
        "full_source17_schema_verified": False,
        "backup_to_database_lineage_verified": False,
        "concurrent_writers_excluded": False,
        "schema_upgrade_authorized": False,
        "exchange_flat": None,
        "account_complete": False,
        "execution_authority": False,
        "admission": "DENY",
    }


async def inspect_retained_owner_0017_v1(
    pins, *, owner_dsn_file, ca_file, source_archive_file
):
    url, tls, plan = _prepared(
        pins,
        owner_dsn_file=owner_dsn_file,
        ca_file=ca_file,
        source_archive_file=source_archive_file,
    )
    engine = create_async_engine(
        url,
        poolclass=NullPool,
        connect_args={"ssl": tls, "timeout": 10},
        hide_parameters=True,
    )
    observations = []
    try:
        async with asyncio.timeout(180):
            for _ in range(2):
                _need(
                    _prepared(
                        pins,
                        owner_dsn_file=owner_dsn_file,
                        ca_file=ca_file,
                        source_archive_file=source_archive_file,
                    )[2]
                    == plan
                )
                async with engine.connect() as connection:
                    transaction = await connection.begin()
                    try:
                        for statement in _READ_SETTINGS:
                            await connection.exec_driver_sql(statement)
                        observations.append(await _snapshot(connection, pins, plan))
                    finally:
                        await transaction.rollback()
            _need(
                _prepared(
                    pins,
                    owner_dsn_file=owner_dsn_file,
                    ca_file=ca_file,
                    source_archive_file=source_archive_file,
                )[2]
                == plan
            )
        return _join(*observations, pins, plan)
    finally:
        await engine.dispose()


def _load_pins(path, expected):
    _need(_digest(expected))
    _require_private_file(path)
    raw = _read_bounded_regular_file_v1(path, 32768)
    _need(_sha(raw) == expected)
    value = read_evidence_json(raw)
    _need(
        type(value) is dict
        and set(value)
        == {
            "schema",
            "source_commit",
            "source_tree",
            "source_archive_sha256",
            "preserved_backup_sha256",
            "route",
            "expected_catalog_sha256",
        }
        and value["schema"] == PINS_SCHEMA
    )
    _need(
        type(value["route"]) is dict
        and set(value["route"]) == set(RetainedOwnerRoute0017V1.__dataclass_fields__)
    )
    return _checked_pins(
        RetainedOwnerPins0017V1(
            **{k: v for k, v in value.items() if k not in {"schema", "route"}},
            route=RetainedOwnerRoute0017V1(**value["route"]),
        )
    )


def _public_result(value):
    """Only hashes/counts reach stdout; never row contents, DSNs or account IDs."""
    return {
        "schema": SCHEMA,
        "status": value["status"],
        "database_head": "0017",
        "source_head": "0058",
        "snapshot_sha256": value["snapshot_sha256"],
        "source_plan_sha256": value["source_plan_sha256"],
        "tables": len(value["snapshot"]["tables"]),
        "sequences": len(value["snapshot"]["sequences"]),
        "expected_catalog_pin_matched": value["expected_catalog_pin_matched"],
        "schema_upgrade_authorized": False,
        "exchange_flat": None,
        "account_complete": False,
        "execution_authority": False,
        "admission": "DENY",
    }


def _publish_private_receipt(value, directory):
    from app.trade_evidence.native_private_root_v2 import (
        require_private_evidence_base_v2,
    )
    from app.trade_qualification.account_native_proof_storage import (
        _root_context,
        _root_identity,
    )

    _need(type(directory) is type(Path()) and directory.is_absolute())
    _need(require_private_evidence_base_v2(str(directory)) == directory)
    before = _root_identity(directory)
    raw = _canonical(value)
    _need(len(raw) <= 2 * 1024 * 1024)
    digest = _sha(raw)
    name = "retained0017-plan-" + digest + ".json"
    with _root_context(directory) as opened:
        _need(name not in opened.names())
        opened.publish(name, raw)
    # Fresh native handle and complete independent readback, with no replacement
    # or cleanup of an accepted/partial receipt if late publication fails.
    _need(_root_identity(directory) == before)
    with _root_context(directory) as opened:
        _need(opened.read(name, 2 * 1024 * 1024) == raw)
    _need(_root_identity(directory) == before)
    return digest


def main(arguments=None):
    parser = _PrivateArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--inspect-retained-clone", action="store_true", required=True)
    for name in (
        "reviewed-pins-file",
        "owner-dsn-file",
        "ca-file",
        "source-archive-file",
    ):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--reviewed-pins-sha256", required=True)
    parser.add_argument("--private-receipt-directory", type=Path)
    try:
        args = parser.parse_args(arguments)
        pins = _load_pins(args.reviewed_pins_file, args.reviewed_pins_sha256)
        result = asyncio.run(
            inspect_retained_owner_0017_v1(
                pins,
                owner_dsn_file=args.owner_dsn_file,
                ca_file=args.ca_file,
                source_archive_file=args.source_archive_file,
            )
        )
        public_result = _public_result(result)
        if args.private_receipt_directory is not None:
            public_result["private_receipt_sha256"] = _publish_private_receipt(
                result, args.private_receipt_directory
            )
        print(_canonical(public_result).decode("ascii"))
        return 2  # Deliberately DATA/DENY, never a successful upgrade permit.
    except (Exception, KeyboardInterrupt):  # noqa: BLE001 -- no private exception text.
        print(
            _canonical(
                {
                    "schema": SCHEMA,
                    "status": "REFUSED",
                    "code": "retained0017_readonly_plan_refused",
                    "exchange_flat": None,
                    "account_complete": False,
                    "execution_authority": False,
                    "admission": "DENY",
                }
            ).decode("ascii")
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
