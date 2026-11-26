"""Reviewed retained0017 -> stopped0055 owner migration, pending source seal.

No original database, service, runtime grant, Arm or venue action is selected by
this module. It works only on an explicitly pinned preserved clone. EMPTY
source17 reference constants refuse before opening even a database connection.
One attempted COMMIT is terminal; an unknown result never retries the DDL.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import sys
from contextlib import redirect_stdout
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

import app.database.models  # noqa: F401 -- exact canonical naming metadata.
from app.database.base import Base
from app.database.native_pg_tls_v1 import _read_bounded_regular_file_v1
from app.domain import collector_current_0055_catalog_v1 as target55
from app.domain.native_live_legacy_safety_v1 import (
    require_legacy_live_negative_fence_v1,
)
from scripts import native_owner_retained_rows_v2 as rows
from scripts import native_owner_upgrade_0056_v1 as shared
from scripts import retained0017_catalog_baseline_v1 as baseline
from scripts import retained0017_owner_plan_v1 as original
from scripts import retained0017_upgrade_projection_v1 as projection
from scripts.evidence_json import read_evidence_json
from scripts.native_owner_catalog_snapshot_0058_v1 import (
    LOGICAL_ACL,
    LOGICAL_CATALOG_ENTRIES_V1,
)
from scripts.native_owner_route_preflight_0056_v1 import _PROFILE_SQL

ROOT = Path(__file__).resolve().parents[1]
CONFIRMATION = "UPGRADE PRESERVED CTCC DB0017 TO STOPPED DB0055"
PINS_SCHEMA = "ctcc.retained0017.owner_upgrade0055_pins.v1"
SCHEMA = "ctcc.retained0017.owner_upgrade0055_receipt.v1"
_SEALED_STATUS = "SEALED_FROM_TWO_REVIEWED_ACTUAL_PG17_REFERENCES"
_FUTURE_TABLES = frozenset(
    {
        "gate3_fitted_candidate_bindings",
        "gate3_fitted_candidate_acks",
        "gate3_fitted_candidate_evaluations",
        "native_live_controls_v1",
        "native_live_reservations_v1",
        "native_live_intents_v1",
        "native_live_transitions_v1",
        "native_live_financial_epochs_v1",
        "native_live_financial_samples_v1",
        "native_live_lifecycle_keys_v1",
        "native_live_lifecycle_proofs_v1",
        "native_live_controlled_close_intents_v1",
        "native_live_controlled_close_claims_v1",
    }
)
_POLICY = {
    "fsync": frozenset({"on"}),
    "full_page_writes": frozenset({"on"}),
    "synchronous_commit": frozenset({"on"}),
    "default_table_access_method": frozenset({"heap"}),
    "default_tablespace": frozenset({""}),
    "temp_tablespaces": frozenset({""}),
    "session_replication_role": frozenset({"origin"}),
    "row_security": frozenset({"on"}),
    "check_function_bodies": frozenset({"on"}),
    "shared_preload_libraries": frozenset({""}),
    "session_preload_libraries": frozenset({""}),
    "local_preload_libraries": frozenset({""}),
    "search_path": frozenset(
        {'"$user", public', "pg_catalog", "pg_catalog, public, pg_temp"}
    ),
    "TimeZone": frozenset({"UTC", "Etc/UTC"}),
}
_DDL_SEARCH_PATH = "public, pg_catalog, pg_temp"
_STARTUP_SETTINGS = {
    "session_preload_libraries": "",
    "local_preload_libraries": "",
    "role": "none",
    "search_path": "pg_catalog",
    "timezone": "UTC",
    "default_transaction_read_only": "on",
    "statement_timeout": "120000ms",
    "lock_timeout": "1000ms",
    "idle_in_transaction_session_timeout": "30000ms",
}
_STORED_POLICY = {name.lower(): allowed for name, allowed in _POLICY.items()} | {
    "role": frozenset({"none"}),
    "default_transaction_read_only": frozenset({"on", "off"}),
    "statement_timeout": frozenset({"10000ms", "30000ms", "120000ms"}),
    "lock_timeout": frozenset({"1000ms"}),
    "idle_in_transaction_session_timeout": frozenset({"30000ms"}),
}


def _stored_policy_sql():
    # Literal source names/values only. Observed settings never become SQL.
    def literal(value):
        return "'" + value.replace("'", "''") + "'"

    values = ",".join(
        "("
        + literal(name)
        + ",ARRAY["
        + ",".join(literal(value) for value in sorted(allowed))
        + "]::pg_catalog.text[])"
        for name, allowed in sorted(_STORED_POLICY.items())
    )
    return text(
        """WITH original_settings AS (
 SELECT pg_catalog.unnest(r.rolconfig) value FROM pg_catalog.pg_roles r
 WHERE r.oid=:owner_oid AND r.rolname=current_user
 UNION ALL SELECT pg_catalog.unnest(s.setconfig) FROM pg_catalog.pg_db_role_setting s
 WHERE s.setrole IN (0,:owner_oid) AND s.setdatabase IN (0,:database_oid)
), expected(name,allowed) AS (VALUES """
        + values
        + """)
 SELECT NOT EXISTS(SELECT 1 FROM original_settings s LEFT JOIN expected e
 ON e.name=pg_catalog.lower(pg_catalog.split_part(s.value,'=',1))
 WHERE (s.value ~ '^[A-Za-z_][A-Za-z0-9_]*=[^=]*$' AND e.name IS NOT NULL
 AND pg_catalog.split_part(s.value,'=',2)=ANY(e.allowed)) IS NOT TRUE)"""
    )


_STORED_POLICY_SQL = _stored_policy_sql()
_COUNTS17 = {
    "reservations": "SELECT pg_catalog.count(*) FROM public.qualification_reservations",
    "positions": "SELECT pg_catalog.count(*) FROM public.positions WHERE status<>'closed' OR closed_at IS NULL",
    "orders": "SELECT pg_catalog.count(*) FROM public.orders WHERE status NOT IN ('filled','canceled','cancelled','rejected','expired','mmp_canceled')",
    "protection": "SELECT pg_catalog.count(*) FROM public.protective_orders WHERE status NOT IN ('filled','canceled','cancelled','rejected','expired','closed')",
    "trades": "SELECT pg_catalog.count(*) FROM public.trades WHERE status<>'closed' OR closed_at IS NULL",
    "soak": "SELECT pg_catalog.count(*) FROM public.demo_soak_sessions WHERE state='running'",
    "mirror_positions": "SELECT pg_catalog.count(*) FROM public.okx_demo_position_state",
    "mirror_orders": "SELECT pg_catalog.count(*) FROM public.okx_demo_order_state WHERE state NOT IN ('filled','canceled','mmp_canceled')",
    "mirror_algos": "SELECT pg_catalog.count(*) FROM public.okx_demo_algo_order_state WHERE state NOT IN ('canceled','order_failed','effective')",
}
_CLUSTER_ROLES = text("""SELECT pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(
 pg_catalog.jsonb_build_object(
 'roles',(SELECT pg_catalog.jsonb_agg(pg_catalog.jsonb_build_array(oid,rolname,rolsuper,rolinherit,
 rolcreaterole,rolcreatedb,rolcanlogin,rolreplication,rolconnlimit,rolvaliduntil,rolbypassrls,rolconfig) ORDER BY oid) FROM pg_catalog.pg_roles),
 'memberships',(SELECT pg_catalog.jsonb_agg(pg_catalog.to_jsonb(m) ORDER BY roleid,member,grantor) FROM pg_catalog.pg_auth_members m),
 'settings',(SELECT pg_catalog.jsonb_agg(pg_catalog.to_jsonb(s) ORDER BY setdatabase,setrole) FROM pg_catalog.pg_db_role_setting s)
 )::pg_catalog.text,'UTF8')),'hex')""")
_SETTINGS_HASH = text("""SELECT pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(
 pg_catalog.jsonb_build_object('role_config',r.rolconfig,'settings',
 (SELECT pg_catalog.jsonb_agg(pg_catalog.to_jsonb(s) ORDER BY s.setdatabase,s.setrole)
 FROM pg_catalog.pg_db_role_setting s WHERE s.setrole=r.oid OR s.setdatabase=:database_oid)
 )::pg_catalog.text,'UTF8')),'hex') FROM pg_catalog.pg_roles r
 WHERE r.oid=:owner_oid AND r.rolname=current_user""")
_NO_OTHER_CLIENT = text("""SELECT NOT EXISTS(SELECT 1 FROM pg_catalog.pg_stat_activity
 WHERE datid=(SELECT oid FROM pg_catalog.pg_database WHERE datname=pg_catalog.current_database())
 AND pid<>pg_catalog.pg_backend_pid() AND backend_type='client backend')""")
_COLUMN_METADATA = text("""SELECT c.relname::pg_catalog.text,a.attnum,
 pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(
 pg_catalog.jsonb_build_array(pg_catalog.to_jsonb(a),pg_catalog.to_jsonb(d))::pg_catalog.text,'UTF8')),'hex')
 FROM pg_catalog.pg_attribute a JOIN pg_catalog.pg_class c ON c.oid=a.attrelid
 JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
 LEFT JOIN pg_catalog.pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
 WHERE n.nspname='public' AND c.relkind='r' AND a.attnum>0
 ORDER BY c.relname COLLATE pg_catalog."C",a.attnum""")
_COLUMN_ACL = text("""SELECT 'column'::pg_catalog.text,c.relname||'.'||a.attname,
 x.grantee::pg_catalog.int8,x.grantor::pg_catalog.int8,x.privilege_type::pg_catalog.text,x.is_grantable
 FROM pg_catalog.pg_attribute a JOIN pg_catalog.pg_class c ON c.oid=a.attrelid
 JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
 CROSS JOIN LATERAL pg_catalog.aclexplode(a.attacl) x
 WHERE n.nspname='public' AND a.attnum>0
 ORDER BY c.relname COLLATE pg_catalog."C",a.attname COLLATE pg_catalog."C",x.grantee,x.grantor,x.privilege_type,x.is_grantable""")

# Independent canonical55 PG17.11 artifact R451 (raw SHA256 1756e0ad43a255f76199be126ffd4dfd8197b133ae033efccfa27ab24a004d92).
# These two CHECK trees are pinned in full with only three FUNCEXPR funcid
# tokens mapped to exact qualified signatures. Keep the original fingerprint
# for post-commit checks of the final generation's physical function bindings.
_RETAINED_CATALOG = text(
    LOGICAL_CATALOG_ENTRIES_V1
    + r""", transport_pins(relation_name,constraint_name,ast_sha256) AS (VALUES
 ('qualification_report_spool','ck_qualification_report_spool_transport_unverified',
  '8e1831bd2ea990031d4629e5b0f78b2dddef4c5800c0c4e808d521fb45b2f7e6'),
 ('qualification_report_projection_receipts','ck_qualification_report_projection_receipts_transport_unverifie',
  '1a97bcc8629d4f41f5da4551f34854a044088696411d1443b7c084b264422f88')
), callbacks AS (
 SELECT pg_catalog.to_regprocedure('public.qualification_native_canonical_v55(pg_catalog.jsonb)')::pg_catalog.oid::pg_catalog.int8 AS canonical_id,
 pg_catalog.to_regprocedure('public.qualification_native_report_eligible_v55(pg_catalog.text,pg_catalog.text,pg_catalog.text,pg_catalog.text)')::pg_catalog.oid::pg_catalog.int8 AS report_id,
 pg_catalog.to_regprocedure('public.qualification_native_projection_eligible_v55(pg_catalog.text,pg_catalog.text,pg_catalog.text,pg_catalog.text,pg_catalog.timestamptz)')::pg_catalog.oid::pg_catalog.int8 AS projection_id
), checked_entries AS (
 SELECT e.kind,e.value,p.relation_name,p.ast_sha256,CASE WHEN p.relation_name IS NOT NULL THEN
 pg_catalog.replace(pg_catalog.replace(pg_catalog.replace(e.value->>'conbin',
  '{FUNCEXPR \:funcid '||f.canonical_id||' \:funcresulttype ',
  '{FUNCEXPR \:funcid [public.qualification_native_canonical_v55(pg_catalog.jsonb)] \:funcresulttype '),
  '{FUNCEXPR \:funcid '||f.report_id||' \:funcresulttype ',
  '{FUNCEXPR \:funcid [public.qualification_native_report_eligible_v55(pg_catalog.text,pg_catalog.text,pg_catalog.text,pg_catalog.text)] \:funcresulttype '),
  '{FUNCEXPR \:funcid '||f.projection_id||' \:funcresulttype ',
  '{FUNCEXPR \:funcid [public.qualification_native_projection_eligible_v55(pg_catalog.text,pg_catalog.text,pg_catalog.text,pg_catalog.text,timestamp with time zone)] \:funcresulttype ')
 END AS mapped_ast
 FROM entries e CROSS JOIN callbacks f LEFT JOIN transport_pins p
 ON e.kind='constraint' AND e.value->>'contype'='c'
 AND e.value->>'relation'=pg_catalog.to_regclass('public.'||p.relation_name)::pg_catalog.text
 AND e.value->>'conname'=p.constraint_name
 WHERE f.canonical_id>=16384 AND f.report_id>=16384 AND f.projection_id>=16384
 AND f.canonical_id<>f.report_id AND f.canonical_id<>f.projection_id AND f.report_id<>f.projection_id
), mapped_entries AS (
 SELECT kind,value,relation_name,CASE WHEN relation_name IS NULL THEN value
 ELSE pg_catalog.jsonb_set(value,'{conbin}',pg_catalog.to_jsonb(mapped_ast),false) END AS mapped_value,
 pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(mapped_ast,'UTF8')),'hex') IS NOT DISTINCT FROM ast_sha256 AS ast_matches
 FROM checked_entries
), documents AS (
 SELECT pg_catalog.jsonb_agg(pg_catalog.jsonb_build_array(kind,value) ORDER BY kind,value::pg_catalog.text)::pg_catalog.text AS original,
 pg_catalog.jsonb_agg(pg_catalog.jsonb_build_array(kind,mapped_value) ORDER BY kind,mapped_value::pg_catalog.text)::pg_catalog.text AS mapped,
 count(relation_name)=2 AND count(DISTINCT relation_name)=2 AND bool_and(ast_matches) AS approved
 FROM mapped_entries
)
SELECT approved,pg_catalog.octet_length(original),
 pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(original,'UTF8')),'hex'),
 pg_catalog.octet_length(mapped),
 pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(mapped,'UTF8')),'hex')
FROM documents
"""
)


class Retained0017UpgradeRefused(ValueError):
    pass


class Retained0017UpgradeCommitUnknown(RuntimeError):
    pass


class Retained0017UpgradeReadbackRequired(RuntimeError):
    pass


def _need(value, code="retained0017_upgrade0055_refused"):
    if value is not True:
        raise Retained0017UpgradeRefused(code) from None


@dataclass(frozen=True, slots=True, repr=False)
class Retained0017UpgradePinsV1:
    retained: original.RetainedOwnerPins0017V1
    maintenance_settings_sha256: str


@dataclass(frozen=True, slots=True, repr=False)
class _FreshSourcePinsV1:
    source_commit: str
    source_tree: str
    source_archive_sha256: str
    origin_sha256: str
    route: original.RetainedOwnerRoute0017V1
    expected_catalog_sha256: str | None = None


@dataclass(frozen=True, slots=True, repr=False)
class _FreshUpgradePinsV1:
    source: _FreshSourcePinsV1
    maintenance_settings_sha256: str


def _transaction_source_v1(pins):
    if type(pins) is Retained0017UpgradePinsV1:
        _need(type(pins.retained) is original.RetainedOwnerPins0017V1)
        return pins.retained
    _need(type(pins) is _FreshUpgradePinsV1 and type(pins.source) is _FreshSourcePinsV1)
    return pins.source


def _checked_fresh_pins_v1(pins):
    _need(type(pins) is _FreshUpgradePinsV1 and type(pins.source) is _FreshSourcePinsV1)
    p = pins.source
    _need(type(p.route) is original.RetainedOwnerRoute0017V1)
    _need(
        all(
            type(value) is str
            for value in (
                p.route.database_head,
                p.route.host,
                p.route.database,
                p.route.owner,
            )
        )
    )
    _need(
        original._digest(p.source_commit, git=True)
        and original._digest(p.source_tree, git=True)
    )
    _need(
        all(
            original._digest(value)
            for value in (
                p.source_archive_sha256,
                p.origin_sha256,
                p.route.source_manifest_sha256,
                p.route.ca_sha256,
                p.route.cluster_system_identifier_sha256,
                pins.maintenance_settings_sha256,
            )
        )
    )
    _need(p.route.database_head == "0017" and p.route.host == "127.0.0.1")
    _need(type(p.route.port) is int and p.route.port == 15439)
    _need(
        type(p.route.owner_oid) is int
        and p.route.owner_oid == 10
        and type(p.route.database_oid) is int
        and p.route.database_oid > 0
    )
    _need(type(p.route.database) is str and type(p.route.owner) is str)
    _need(
        p.expected_catalog_sha256 is None or original._digest(p.expected_catalog_sha256)
    )


class _TransactionPurpose(Enum):
    READ_SOURCE17 = "read_original_source17"
    WRITE_FIXED_DDL = "owner_fixed0018_through0055"
    READ_TARGET55 = "read_committed_target55"


def _require_reference_seal():
    _need(
        baseline.REFERENCE_STATUS == _SEALED_STATUS
        and original._digest(baseline.SEALED_CATALOG_SHA256)
        and original._digest(baseline.SEALED_SOURCE17_PREFIX_SHA256),
        "retained0017_upgrade0055_reference_pending",
    )
    _need(
        baseline.source17_contract_v1()["source17_prefix_sha256"]
        == baseline.SEALED_SOURCE17_PREFIX_SHA256,
        "retained0017_upgrade0055_reference_source_changed",
    )


def _require_apply_lineage():
    # A backup digest's format, a schema reference and a matching SQL route do
    # not prove that this selected datadir descends from the preserved backup.
    # No caller flag/report can enable writes. The actual source-verified copy
    # and controlled-bootstrap custody chain must be wired in a later review.
    raise Retained0017UpgradeRefused(
        "retained0017_upgrade0055_apply_lineage_pending"
    ) from None


def _prepared(pins, *, owner_dsn_file, ca_file, source_archive_file):
    # This is deliberately first: the source-owned literal proof is mandatory;
    # neither a caller hash nor a saved receipt substitutes it or grants DDL.
    _require_reference_seal()
    _need(type(pins) is Retained0017UpgradePinsV1)
    _need(original._digest(pins.maintenance_settings_sha256))
    url, tls, plan = original._prepared(
        _transaction_source_v1(pins),
        owner_dsn_file=owner_dsn_file,
        ca_file=ca_file,
        source_archive_file=source_archive_file,
    )
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    script = ScriptDirectory.from_config(config)
    _need(tuple(script.get_heads()) == ("0058",))
    _need(
        tuple(
            step.revision
            for step in reversed(tuple(script.iterate_revisions("0055", "0017")))
        )
        == tuple(f"{number:04d}" for number in range(18, 56))
    )
    return url, tls, plan, script, source_archive_file


def _replay_source(prepared, pins):
    _require_reference_seal()
    shared._verify_reviewed_source_archive(
        prepared[4],
        source_commit=_transaction_source_v1(pins).source_commit,
        source_tree=_transaction_source_v1(pins).source_tree,
        source_archive_sha256=_transaction_source_v1(pins).source_archive_sha256,
        source_manifest_sha256=_transaction_source_v1(
            pins
        ).route.source_manifest_sha256,
    )
    _need(
        original._source_plan() == prepared[2],
        "retained0017_upgrade0055_source_changed",
    )


def _target_tables():
    names = set(Base.metadata.tables)
    _need(_FUTURE_TABLES <= names)
    result = (names - _FUTURE_TABLES) | {"alembic_version"}
    _need(
        48 < len(result) <= 512 and all(rows._NAME.fullmatch(name) for name in result)
    )
    return result


async def _maintenance(connection, pins, *, before_settings):
    p = _transaction_source_v1(pins).route
    _need(
        await connection.scalar(
            _STORED_POLICY_SQL,
            {"owner_oid": p.owner_oid, "database_oid": p.database_oid},
        )
        is True,
        "retained0017_upgrade0055_stored_maintenance_policy_refused",
    )
    _need(
        await connection.scalar(_NO_OTHER_CLIENT) is True,
        "retained0017_upgrade0055_other_client_present",
    )
    _need(
        await connection.scalar(
            _SETTINGS_HASH, {"owner_oid": p.owner_oid, "database_oid": p.database_oid}
        )
        == pins.maintenance_settings_sha256,
        "retained0017_upgrade0055_stored_maintenance_policy_changed",
    )
    for name, allowed in _POLICY.items():
        value = await connection.scalar(
            text("SELECT pg_catalog.current_setting(:name)"), {"name": name}
        )
        if not before_settings and name in {"search_path", "TimeZone"}:
            allowed = (
                frozenset({"pg_catalog, public, pg_temp", _DDL_SEARCH_PATH})
                if name == "search_path"
                else frozenset({"UTC"})
            )
        _need(
            type(value) is str and value in allowed,
            "retained0017_upgrade0055_session_policy_refused",
        )
    _need(
        await connection.scalar(
            text(
                "SELECT rolsuper AND rolcanlogin FROM pg_catalog.pg_roles WHERE rolname=current_user"
            )
        )
        is True,
        "retained0017_upgrade0055_frozen0050_requires_actual_superowner",
    )
    _need(
        await connection.scalar(
            text("SELECT NOT EXISTS(SELECT 1 FROM pg_catalog.pg_event_trigger)")
        )
        is True
    )
    _need(
        await connection.scalar(text("SELECT pg_catalog.pg_is_in_recovery()")) is False,
        "retained0017_upgrade0055_readwrite_owner_required",
    )


async def _begin_reviewed(connection, pins, *, purpose):
    _need(type(purpose) is _TransactionPurpose)
    # Observe stored/effective policy in a separate rolled-back catalog probe.
    # PostgreSQL requires SET TRANSACTION before the first query of the actual
    # transaction; reading policy and then trying to SET its isolation fails.
    probe = await connection.begin()
    try:
        await rows.heap_am.require_native_owner_storage_v1(connection)
        await _maintenance(connection, pins, before_settings=True)
    finally:
        await probe.rollback()
    transaction = await connection.begin()
    settings = (
        shared._OWNER_SETTINGS
        if purpose is _TransactionPurpose.WRITE_FIXED_DDL
        else original._READ_SETTINGS
        if purpose is _TransactionPurpose.READ_SOURCE17
        else shared._READBACK_SETTINGS
    )
    try:
        for sql in settings:
            if purpose is _TransactionPurpose.READ_SOURCE17:
                sql = sql.replace("pg_catalog, public, pg_temp", "pg_catalog")
            elif purpose is _TransactionPurpose.WRITE_FIXED_DDL:
                sql = sql.replace("pg_catalog, public, pg_temp", _DDL_SEARCH_PATH)
            await connection.exec_driver_sql(sql)
    except BaseException:
        await transaction.rollback()
        raise
    return transaction


async def _actor(connection, pins, head, *, read_only):
    await rows.heap_am.require_native_owner_storage_v1(connection)
    profile = tuple((await connection.execute(_PROFILE_SQL)).one())
    shared._checked_profile(
        profile, _transaction_source_v1(pins).route, head, read_only=read_only
    )
    return profile


async def _layouts(connection, pins, *, expected_names):
    actual = tuple(
        tuple(row) for row in (await connection.execute(rows._LAYOUT_SQL)).all()
    )
    relations = tuple((row[0], row[1], row[2]) for row in actual)
    _need({row[0] for row in actual} == expected_names)
    return rows._checked_layouts(
        actual,
        rows._expected_relations(relations),
        _transaction_source_v1(pins).route.owner_oid,
    )


async def _lock_original_tables(connection, columns):
    await connection.exec_driver_sql(
        "LOCK TABLE "
        + ",".join('public."' + name + '"' for name in sorted(columns))
        + " IN ACCESS EXCLUSIVE MODE NOWAIT"
    )


async def _capture_source17(connection, *, read_only, _custody=None):
    components = (
        ("scope", baseline.SAFE_CATALOG_SQL.removeprefix("\nSELECT ")),
        ("functions", baseline._source_functions_predicate()),
        ("language", baseline._LANGUAGE_ADMISSION_SQL),
        ("nonpublic", baseline._unknown_nonpublic_objects_predicate()),
    )
    combined = "SELECT " + " AND ".join(part for _, part in components)
    await baseline._require_raw_catalog_admission_v1(connection, combined, components)
    # Match the original read-only capture chain before any later whole-row
    # projection: a metadata proof must not race an ALTER TABLE/type change.
    # The write purpose already holds ACCESS EXCLUSIVE on these same tables.
    contract = baseline.source17_contract_v1()
    await connection.exec_driver_sql(
        "LOCK TABLE "
        + ",".join('public."' + name + '"' for name in sorted(contract["columns"]))
        + " IN ACCESS SHARE MODE NOWAIT"
    )
    await rows.heap_am.require_native_owner_storage_v1(connection)
    await baseline._require_raw_catalog_admission_v1(connection, combined, components)
    raw = await connection.scalar(text(baseline.CATALOG_SQL))
    # Actual observed read/write mode is validated explicitly, never retagged.
    if _custody is None:
        normalized = baseline._normalize(raw, read_only="on" if read_only else "off")
        _need(
            baseline.sha(baseline.canonical(normalized))
            == baseline.SEALED_CATALOG_SHA256
        )
    else:
        from scripts import retained0017_bootstrap_catalog_v2 as bootstrap
        from scripts import retained_pg_bootstrap_custody_v1 as custody

        await custody._replay_owned_connection_v1(_custody, connection)
        bootstrap._require_bootstrap_catalog17_v2(
            raw, read_only="on" if read_only else "off"
        )
    _require_reference_seal()
    _need(baseline.source17_contract_v1() == contract)
    return baseline.sha(
        baseline.canonical(
            {
                name: raw[name]
                for name in (
                    "head",
                    "references",
                    "toast",
                    "entries",
                    "dependencies",
                    "shared_dependencies",
                    "bootstrap",
                    "actor_physical",
                )
            }
        )
    )


async def _local17(connection):
    automation = await connection.scalar(
        text(
            "SELECT pg_catalog.jsonb_agg(pg_catalog.to_jsonb(s) ORDER BY id) FROM public.demo_automation_state s"
        )
    )
    projection.validate_original_control_v1(automation)
    counts = {
        name: await connection.scalar(text(sql)) for name, sql in _COUNTS17.items()
    }
    _need(
        all(type(value) is int and value == 0 for value in counts.values()),
        "retained0017_upgrade0055_local_unresolved_state",
    )
    await require_legacy_live_negative_fence_v1(connection)
    return baseline.sha(
        baseline.canonical({"automation": automation, "counts": counts})
    )


async def _projection(connection, layouts, *, phase):
    result, total = [], 0
    for name, _oid, _kind, columns in layouts:
        if name == "alembic_version":
            continue  # Its single exact actual value is checked independently.
        size_sql, hash_sql = projection.old_projection_sql_v1(
            name,
            [column[0] for column in columns],
            phase=phase,
        )
        count, size = tuple((await connection.execute(size_sql)).one())
        _need(type(count) is int and 0 <= count <= original.MAX_ROWS)
        _need(type(size) is int and 0 <= size <= original.MAX_TABLE_BYTES)
        total += size
        _need(total <= original.MAX_TOTAL_BYTES)
        digest = await connection.scalar(hash_sql)
        _need(original._digest(digest))
        result.append([name, count, size, digest])
    return result


async def _state(connection, pins, layouts):
    relations = tuple((name, oid, kind) for name, oid, kind, _columns in layouts)
    snapshot = await rows.snapshot_rows_v2(
        connection,
        relations,
        owner_oid=_transaction_source_v1(pins).route.owner_oid,
        marker_scope=rows.MarkerScopeV2.EXCLUDE_UPGRADE_MARKER,
        max_rows=original.MAX_ROWS,
        max_table_bytes=original.MAX_TABLE_BYTES,
        max_total_bytes=original.MAX_TOTAL_BYTES,
    )
    acl = tuple(tuple(row) for row in (await connection.execute(LOGICAL_ACL)).all())
    column_acl = tuple(
        tuple(row) for row in (await connection.execute(_COLUMN_ACL)).all()
    )
    columns = tuple(
        tuple(row) for row in (await connection.execute(_COLUMN_METADATA)).all()
    )
    roles = await connection.scalar(_CLUSTER_ROLES)
    _need(original._digest(roles))
    return snapshot | {
        "acl": acl + column_acl,
        "column_metadata": columns,
        "roles_sha256": roles,
    }


async def _optional_legacy_catalog_pin(connection, pins):
    expected = _transaction_source_v1(pins).expected_catalog_sha256
    if expected is None:
        return None
    _need(original._digest(expected))
    # Called only after the mandatory complete sealed17 admission. Reuse the
    # exact historical diagnostic encoding; this catalog has no transaction
    # read/write or session-setting fields and no application row selection.
    value = await connection.scalar(original.CATALOG_SQL)
    _need(type(value) is str and 0 < len(value.encode()) <= original.MAX_CATALOG_BYTES)
    digest = original._sha(original._canonical(read_evidence_json(value.encode())))
    _need(digest == expected, "retained0017_upgrade0055_reviewed_catalog_mismatch")
    return digest


async def _before(connection, pins, *, read_only, _custody=None):
    await _actor(connection, pins, "0017", read_only=read_only)
    catalog = await _capture_source17(
        connection, read_only=read_only, _custody=_custody
    )
    legacy_catalog_pin = await _optional_legacy_catalog_pin(connection, pins)
    contract = baseline.source17_contract_v1()
    layouts = await _layouts(connection, pins, expected_names=set(contract["columns"]))
    _need(
        all(
            [column[0] for column in columns] == contract["columns"][name]
            for name, _oid, _kind, columns in layouts
        )
    )
    local = await _local17(connection)
    state = await _state(connection, pins, layouts)
    state.update(
        catalog_sha256=catalog,
        legacy_reviewed_catalog_sha256=legacy_catalog_pin,
        local_sha256=local,
        old_projection=await _projection(
            connection, layouts, phase=projection.OldColumnProjectionV1.ORIGINAL
        ),
        expected_projection=await _projection(
            connection, layouts, phase=projection.OldColumnProjectionV1.AFTER_FIXED_0034
        ),
    )
    return state


async def _after(connection, pins, before, *, read_only):
    await connection.exec_driver_sql(
        "SET LOCAL search_path TO pg_catalog, public, pg_temp"
    )
    await _actor(connection, pins, "0055", read_only=read_only)
    # This is the existing genuine source55 native catalog/baseline, not an
    # observed rehearsal substituted for an independent expected definition.
    value = await connection.scalar(text(target55.OWNER_CATALOG_SQL))
    observed = (
        target55._observe_retained_postcommit_owner_catalog_v1(value)
        if read_only
        else target55._observed(value, owner_mode=True, read_only="off")
    )
    _need(observed["native_definitions_sha256"] == target55._required_baseline())
    layouts = await _layouts(connection, pins, expected_names=_target_tables())
    projection.require_preserved_old_layouts_v1(before["row_layouts"], layouts)
    await _maintenance(connection, pins, before_settings=False)
    state = await _state(connection, pins, layouts)
    original_names = {row[0] for row in before["row_layouts"]}
    old_layouts = [row for row in before["row_layouts"] if row[0] in original_names]
    actual = await _projection(
        connection, old_layouts, phase=projection.OldColumnProjectionV1.ORIGINAL
    )
    projection.require_preserved_projection_v1(before["expected_projection"], actual)
    _need(state["roles_sha256"] == before["roles_sha256"])
    _need(state["sequence_state"] == before["sequence_state"])
    old_acl_targets = {(row[0], row[1]) for row in before["acl"]}
    _need(
        tuple(row for row in state["acl"] if (row[0], row[1]) in old_acl_targets)
        == before["acl"]
    )
    old_column_keys = {item[:2] for item in before["column_metadata"]}
    _need(
        tuple(item for item in state["column_metadata"] if item[:2] in old_column_keys)
        == before["column_metadata"]
    )
    _need(
        all(
            count == 0
            for name, count, _digest in state["rows"]
            if name not in original_names
        )
    )
    control = await connection.scalar(
        text(
            "SELECT pg_catalog.jsonb_agg(pg_catalog.to_jsonb(s) ORDER BY id) FROM public.demo_automation_state s"
        )
    )
    _need(type(control) is list and len(control) == 1)
    item = control[0]
    _need(
        item["armed"] is False
        and item["emergency_stop"]
        is item["locked"]
        is item["restart_latch_required"]
        is True
        and type(item["control_revision"]) is int
        and item["control_revision"] == 0
    )
    approved, size, logical, mapped_size, mapped = (
        await connection.execute(_RETAINED_CATALOG)
    ).one()
    _need(
        approved is True,
        "retained0017_upgrade0055_transport_check_identity_mismatch",
    )
    _need(
        all(type(n) is int and 0 < n <= 32 * 1024 * 1024 for n in (size, mapped_size))
        and all(original._digest(digest) for digest in (logical, mapped))
    )
    return state | {
        "logical_sha256": logical,
        "rehearsal_catalog_sha256": mapped,
        "old_projection": actual,
    }


def _migrate(sync_connection, script):
    migration = MigrationContext.configure(
        sync_connection,
        opts={
            "fn": lambda heads, _: script._upgrade_revs("0055", heads),
            "target_metadata": Base.metadata,
            "compare_type": True,
        },
    )
    _need(migration.get_current_heads() == ("0017",))
    with Operations.context(migration), migration.begin_transaction():
        migration.run_migrations()
    _need(migration.get_current_heads() == ("0055",))


async def _readonly_before(engine, pins, *, _custody=None):
    async with engine.connect() as connection:
        transaction = await _begin_reviewed(
            connection, pins, purpose=_TransactionPurpose.READ_SOURCE17
        )
        try:
            await rows.heap_am.lock_native_owner_marker_v1(connection)
            return await _before(connection, pins, read_only=True, _custody=_custody)
        finally:
            await transaction.rollback()


async def _commit_once(engine, pins, prepared, expected, *, _custody=None):
    if _custody is None:
        _require_apply_lineage()
    else:
        from scripts import retained_pg_bootstrap_custody_v1 as custody

        # Only the separately produced FRESH fixture path is enabled here.
        # The public PRESERVED entry retains its unconditional preconnect deny.
        custody._claim_owned_fixture_apply_v1(_custody)
    attempted = committed = False
    try:
        async with engine.connect() as connection:
            transaction = await _begin_reviewed(
                connection, pins, purpose=_TransactionPurpose.WRITE_FIXED_DDL
            )
            try:
                await rows.heap_am.require_native_owner_storage_v1(connection)
                await connection.execute(shared._OWNER_LOCK)
                _need(
                    await connection.scalar(
                        text("SELECT pg_catalog.pg_try_advisory_xact_lock(1680010)")
                    )
                    is True
                )
                await connection.exec_driver_sql(shared._LOCK_REVISION)
                await _lock_original_tables(
                    connection, baseline.source17_contract_v1()["columns"]
                )
                # pg_catalog-only is part of the sealed raw capture profile.
                await connection.exec_driver_sql("SET LOCAL search_path TO pg_catalog")
                before = await _before(
                    connection, pins, read_only=False, _custody=_custody
                )
                _need(before == expected)
                await connection.exec_driver_sql(
                    "SET LOCAL search_path TO " + _DDL_SEARCH_PATH
                )
                await _maintenance(connection, pins, before_settings=False)
                _replay_source(prepared, pins)
                savepoint = await connection.begin_nested()
                if _custody is not None:
                    await custody._replay_owned_connection_v1(_custody, connection)
                    custody._advance_owned_apply_v1(_custody, "rehearsal")
                await connection.run_sync(_migrate, prepared[3])
                _replay_source(prepared, pins)
                rehearsal = await _after(connection, pins, before, read_only=False)
                await savepoint.rollback()
                await connection.exec_driver_sql("SET LOCAL search_path TO pg_catalog")
                _need(
                    await _before(connection, pins, read_only=False, _custody=_custody)
                    == before
                )
                if _custody is not None:
                    custody._advance_owned_apply_v1(_custody, "rolled_back")
                await connection.exec_driver_sql(
                    "SET LOCAL search_path TO " + _DDL_SEARCH_PATH
                )
                await _maintenance(connection, pins, before_settings=False)
                _replay_source(prepared, pins)
                if _custody is not None:
                    await custody._replay_owned_connection_v1(_custody, connection)
                    custody._advance_owned_apply_v1(_custody, "final")
                await connection.run_sync(_migrate, prepared[3])
                _replay_source(prepared, pins)
                final = await _after(connection, pins, before, read_only=False)
                _need(
                    final["rehearsal_catalog_sha256"]
                    == rehearsal["rehearsal_catalog_sha256"],
                    "retained0017_upgrade0055_rehearsal_catalog_mismatch",
                )
                if _custody is not None:
                    await custody._replay_owned_connection_v1(_custody, connection)
                    _replay_source(prepared, pins)
                    custody._advance_owned_apply_v1(_custody, "commit_attempted")
                attempted = True
                await transaction.commit()
                committed = True
                if _custody is not None:
                    custody._advance_owned_apply_v1(_custody, "readback_only")
                return before, final
            except BaseException:
                if not attempted:
                    await transaction.rollback()
                raise
    except BaseException:  # noqa: BLE001 -- redact driver/cancellation after the commit boundary.
        if _custody is not None:
            custody._mark_owned_unknown_v1(_custody)
        if committed:
            raise Retained0017UpgradeReadbackRequired(
                "retained0017_upgrade0055_committed_readback_required"
            ) from None
        if attempted:
            raise Retained0017UpgradeCommitUnknown(
                "retained0017_upgrade0055_commit_unknown_do_not_retry"
            ) from None
        raise Retained0017UpgradeRefused("retained0017_upgrade0055_refused") from None


def _prepared_owned_fixture_v1(owned, pins, source_archive_file):
    """Private actual-FRESH route; no DSN file, fake password or report adoption."""
    from contextlib import redirect_stdout

    from scripts import native_owner_route_preflight_0056_v1 as route
    from scripts import retained_pg_bootstrap_custody_v1 as custody

    _require_reference_seal()
    url, tls = custody._owned_route_v1(owned, pins, fixture=True)
    _checked_fresh_pins_v1(pins)
    with redirect_stdout(io.StringIO()):
        source = route._source_for_profile(route.profiles.DB0058)
    _need(
        source.actual_source_head == "0058"
        and source.manifest_sha256
        == _transaction_source_v1(pins).route.source_manifest_sha256
    )
    shared._verify_reviewed_source_archive(
        source_archive_file,
        source_commit=_transaction_source_v1(pins).source_commit,
        source_tree=_transaction_source_v1(pins).source_tree,
        source_archive_sha256=_transaction_source_v1(pins).source_archive_sha256,
        source_manifest_sha256=_transaction_source_v1(
            pins
        ).route.source_manifest_sha256,
    )
    plan = original._source_plan()
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    graph = ScriptDirectory.from_config(config)
    _need(tuple(graph.get_heads()) == ("0058",))
    _need(
        tuple(
            step.revision
            for step in reversed(tuple(graph.iterate_revisions("0055", "0017")))
        )
        == tuple(f"{number:04d}" for number in range(18, 56))
    )
    return url, tls, plan, graph, source_archive_file


async def _exercise_owned_fresh_0017_to_0055_v1(owned, pins, *, source_archive_file):
    """Same canonical DDL/retention core, solely on actual minted FRESH custody.

    This private test purpose cannot authorize the public preserved DB entry.
    No bool/callback/result can substitute the same-task live opaque producer.
    """
    from scripts import retained_pg_bootstrap_custody_v1 as custody

    prepared = _prepared_owned_fixture_v1(owned, pins, source_archive_file)
    engine = create_async_engine(
        prepared[0],
        poolclass=NullPool,
        connect_args={
            "ssl": prepared[1],
            "timeout": 10,
            "server_settings": dict(_STARTUP_SETTINGS),
        },
        hide_parameters=True,
    )
    committed = False
    try:
        custody._bind_engine_v1(owned, engine)
        async with asyncio.timeout(600):
            first = await _readonly_before(engine, pins, _custody=owned)
            second = await _readonly_before(engine, pins, _custody=owned)
            _need(first == second)
            _need(
                _prepared_owned_fixture_v1(owned, pins, source_archive_file)[2]
                == prepared[2]
            )
            before, final = await _commit_once(
                engine, pins, prepared, first, _custody=owned
            )
            committed = True
            for _ in range(2):
                _replay_source(prepared, pins)
                async with engine.connect() as connection:
                    transaction = await _begin_reviewed(
                        connection, pins, purpose=_TransactionPurpose.READ_TARGET55
                    )
                    try:
                        await custody._replay_owned_connection_v1(owned, connection)
                        await rows.heap_am.lock_native_owner_marker_v1(connection)
                        _need(
                            await _after(connection, pins, before, read_only=True)
                            == final
                        )
                    finally:
                        await transaction.rollback()
            return {
                "migration_performed": True,
                "database_head": "0055",
                "row_encoding": rows.ROW_ENCODING,
                "before_sha256": baseline.sha(baseline.canonical(before)),
                "after_sha256": baseline.sha(baseline.canonical(final)),
                "source17_bootstrap_catalog_verified": True,
                "rehearsal_rolled_back_verified": True,
                "independent_readbacks": 2,
                "armed": False,
                "emergency_stop": True,
                "restart_latch_required": True,
                "control_revision": 0,
                "preserved_backup_lineage_verified": False,
                "schema_upgrade_authorized": False,
                "exchange_flat": None,
                "account_complete": False,
                "execution_authority": False,
            }
    except (Retained0017UpgradeCommitUnknown, Retained0017UpgradeReadbackRequired):
        raise
    except BaseException:  # noqa: BLE001 -- no private DB values from failed fixture acceptance.
        custody._mark_owned_unknown_v1(owned)
        if committed:
            raise Retained0017UpgradeReadbackRequired(
                "retained0017_upgrade0055_committed_readback_required"
            ) from None
        raise Retained0017UpgradeRefused(
            "retained0017_upgrade0055_fresh_custody_refused"
        ) from None
    finally:
        already_failed = sys.exc_info()[0] is not None
        try:
            await engine.dispose()
        except BaseException:  # noqa: BLE001 -- never replace commit-unknown with a cleanup error.
            if not already_failed:
                raise Retained0017UpgradeReadbackRequired(
                    "retained0017_upgrade0055_cleanup_readback_required"
                ) from None


async def upgrade_preserved_owner_0017_to_0055_v1(
    pins,
    *,
    owner_dsn_file,
    ca_file,
    source_archive_file,
    confirmation=None,
):
    _need(confirmation is None or confirmation == CONFIRMATION)
    if confirmation is not None:
        _require_apply_lineage()
    prepared = _prepared(
        pins,
        owner_dsn_file=owner_dsn_file,
        ca_file=ca_file,
        source_archive_file=source_archive_file,
    )
    engine = create_async_engine(
        prepared[0],
        poolclass=NullPool,
        connect_args={
            "ssl": prepared[1],
            "timeout": 10,
            "server_settings": dict(_STARTUP_SETTINGS),
        },
        hide_parameters=True,
    )
    committed = False
    try:
        async with asyncio.timeout(600):
            first = await _readonly_before(engine, pins)
            second = await _readonly_before(engine, pins)
            _need(first == second, "retained0017_upgrade0055_independent_before_differ")
            if confirmation is None:
                return {
                    "schema": SCHEMA,
                    "status": "READ_ONLY_ELIGIBILITY_DATA",
                    "database_head": "0017",
                    "snapshot_sha256": baseline.sha(baseline.canonical(first)),
                    "migration_performed": False,
                    "schema_upgrade_authorized": False,
                    "exchange_flat": None,
                    "account_complete": False,
                    "execution_authority": False,
                }
            _need(
                _prepared(
                    pins,
                    owner_dsn_file=owner_dsn_file,
                    ca_file=ca_file,
                    source_archive_file=source_archive_file,
                )[2]
                == prepared[2]
            )
            before, final = await _commit_once(engine, pins, prepared, first)
            committed = True
            # Two fresh NullPool sessions; no retained transaction or result is
            # used as proof of the committed state.
            for _ in range(2):
                fresh = _prepared(
                    pins,
                    owner_dsn_file=owner_dsn_file,
                    ca_file=ca_file,
                    source_archive_file=source_archive_file,
                )
                _need(fresh[2] == prepared[2])
                async with engine.connect() as connection:
                    transaction = await _begin_reviewed(
                        connection, pins, purpose=_TransactionPurpose.READ_TARGET55
                    )
                    try:
                        await rows.heap_am.lock_native_owner_marker_v1(connection)
                        _need(
                            await _after(connection, pins, before, read_only=True)
                            == final
                        )
                    finally:
                        await transaction.rollback()
            return {
                "schema": SCHEMA,
                "status": "COMMITTED_STOPPED_0055_READBACK_VERIFIED",
                "database_head": "0055",
                "row_encoding": rows.ROW_ENCODING,
                "source_commit": _transaction_source_v1(pins).source_commit,
                "source_tree": _transaction_source_v1(pins).source_tree,
                "preserved_backup_sha256": _transaction_source_v1(
                    pins
                ).preserved_backup_sha256,
                "backup_to_database_lineage_verified": False,
                "before_sha256": baseline.sha(baseline.canonical(before)),
                "after_sha256": baseline.sha(baseline.canonical(final)),
                "rehearsal_rolled_back_verified": True,
                "independent_readbacks": 2,
                "migration_performed": True,
                "armed": False,
                "emergency_stop": True,
                "restart_latch_required": True,
                "control_revision": 0,
                "source17_schema_verified": True,
                "frozen_migration_sequence_verified": True,
                "independent_full_schema0055_baseline_verified": False,
                "schema_upgrade_authorized": False,
                "exchange_flat": None,
                "account_complete": False,
                "execution_authority": False,
            }
    except (Retained0017UpgradeCommitUnknown, Retained0017UpgradeReadbackRequired):
        raise
    except BaseException:  # noqa: BLE001 -- no private values from a driver failure.
        if committed:
            raise Retained0017UpgradeReadbackRequired(
                "retained0017_upgrade0055_committed_readback_required"
            ) from None
        raise Retained0017UpgradeRefused("retained0017_upgrade0055_refused") from None
    finally:
        already_failed = sys.exc_info()[0] is not None
        try:
            await engine.dispose()
        except BaseException:  # noqa: BLE001 -- preserve an existing unknown-commit classification.
            if not already_failed:
                if committed:
                    raise Retained0017UpgradeReadbackRequired(
                        "retained0017_upgrade0055_committed_readback_required"
                    ) from None
                raise Retained0017UpgradeRefused(
                    "retained0017_upgrade0055_cleanup_refused"
                ) from None


class _PrivateParser(argparse.ArgumentParser):
    def error(self, message):
        raise Retained0017UpgradeRefused(
            "retained0017_upgrade0055_cli_inputs_refused"
        ) from None


def _load_pins(path, digest):
    original._require_private_file(path)
    raw = _read_bounded_regular_file_v1(path, 65536)
    _need(original._digest(digest) and baseline.sha(raw) == digest)
    data = read_evidence_json(raw)
    _need(
        type(data) is dict
        and set(data) == {"schema", "retained", "maintenance_settings_sha256"}
        and data["schema"] == PINS_SCHEMA
    )
    value = data["retained"]
    _need(
        type(value) is dict
        and set(value)
        == {
            "source_commit",
            "source_tree",
            "source_archive_sha256",
            "preserved_backup_sha256",
            "route",
            "expected_catalog_sha256",
        }
    )
    route = value["route"]
    _need(
        type(route) is dict
        and set(route) == set(original.RetainedOwnerRoute0017V1.__dataclass_fields__)
    )
    retained = original.RetainedOwnerPins0017V1(
        **{**value, "route": original.RetainedOwnerRoute0017V1(**route)}
    )
    original._checked_pins(retained)
    _need(original._digest(data["maintenance_settings_sha256"]))
    return Retained0017UpgradePinsV1(retained, data["maintenance_settings_sha256"])


def main(arguments=None):
    try:
        parser = _PrivateParser()
        parser.add_argument("--pins-file", type=Path, required=True)
        parser.add_argument("--pins-sha256", required=True)
        parser.add_argument("--owner-dsn-file", type=Path, required=True)
        parser.add_argument("--ca-file", type=Path, required=True)
        parser.add_argument("--source-archive-file", type=Path, required=True)
        parser.add_argument("--confirm")
        args = parser.parse_args(arguments)
        with redirect_stdout(io.StringIO()):
            result = asyncio.run(
                upgrade_preserved_owner_0017_to_0055_v1(
                    _load_pins(args.pins_file, args.pins_sha256),
                    owner_dsn_file=args.owner_dsn_file,
                    ca_file=args.ca_file,
                    source_archive_file=args.source_archive_file,
                    confirmation=args.confirm,
                )
            )
        print(baseline.canonical(result).decode("ascii"))
        return 0
    except Retained0017UpgradeCommitUnknown:
        print('{"status":"COMMIT_UNKNOWN_DO_NOT_RETRY","execution_authority":false}')
        return 3
    except Retained0017UpgradeReadbackRequired:
        print('{"status":"COMMITTED_READBACK_REQUIRED","execution_authority":false}')
        return 4
    except Exception:  # noqa: BLE001 -- never echo private arguments or driver details.
        print('{"status":"REFUSED","execution_authority":false}')
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
