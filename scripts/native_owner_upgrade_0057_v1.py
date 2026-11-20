"""Reviewed owner-only 0056→0057 profile installation; never execution authority.

Default CLI behavior is read-only. The write path performs one migration and
the two fixed G12 grants in one transaction. An attempted uncertain COMMIT is
terminal; this module never retries, regrants, clears stops or starts services.
"""

from __future__ import annotations

import argparse
import asyncio
from contextlib import AsyncExitStack
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.database.native_pg_tls_v1 import _read_bounded_regular_file_v1
from app.domain import native_database_profile_v1 as profiles
from app.domain.source_primitives import canonical, sha
from app.trade_qualification import demo_control as control
from scripts import current0057_native_g12_acl_v1 as grants
from scripts import native_owner_retained_rows_v2 as retained_rows
from scripts import native_owner_route_preflight_0056_v1 as route
from scripts import native_owner_target_plan_0056_v1 as private_inputs
from scripts import native_owner_upgrade_0056_v1 as old
from scripts.evidence_json import read_evidence_json

ROOT = Path(__file__).resolve().parents[1]
CONFIRMATION = "UPGRADE CTCC OWNER DB0056 TO DB0057"
_MAX_ROWS = 1_000_000
_MAX_ROW_BYTES = 128 * 1024 * 1024
_MAX_RELATIONS = 512
_RELATIONS = text("""
SELECT c.relname::text,c.oid::bigint,c.relkind::text
FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
WHERE n.nspname='public' AND c.relkind IN ('r','p','m','f') ORDER BY c.relname
""")
_ACL = text("""
SELECT kind,object_oid,grantee,grantor,privilege,is_grantable FROM (
 SELECT 'relation'::text AS kind,c.oid::bigint AS object_oid,a.grantee::bigint,
 a.grantor::bigint,a.privilege_type::text AS privilege,a.is_grantable
 FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
 CROSS JOIN LATERAL pg_catalog.aclexplode(COALESCE(c.relacl,pg_catalog.acldefault(
 CASE WHEN c.relkind='S' THEN 's'::"char" ELSE 'r'::"char" END,c.relowner))) a
 WHERE n.nspname='public' AND c.relkind IN ('r','p','m','S','v','f')
 UNION ALL
 SELECT 'function',p.oid::bigint,a.grantee::bigint,a.grantor::bigint,a.privilege_type,a.is_grantable
 FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
 CROSS JOIN LATERAL pg_catalog.aclexplode(COALESCE(p.proacl,pg_catalog.acldefault('f',p.proowner))) a
 WHERE n.nspname='public'
 UNION ALL
 SELECT 'schema',n.oid::bigint,a.grantee::bigint,a.grantor::bigint,a.privilege_type,a.is_grantable
 FROM pg_catalog.pg_namespace n
 CROSS JOIN LATERAL pg_catalog.aclexplode(COALESCE(n.nspacl,pg_catalog.acldefault('n',n.nspowner))) a
 WHERE n.nspname='public'
 UNION ALL
 SELECT 'database',d.oid::bigint,a.grantee::bigint,a.grantor::bigint,a.privilege_type,a.is_grantable
 FROM pg_catalog.pg_database d
 CROSS JOIN LATERAL pg_catalog.aclexplode(COALESCE(d.datacl,pg_catalog.acldefault('d',d.datdba))) a
 WHERE d.datname=current_database()
) q ORDER BY kind,object_oid,grantee,grantor,privilege,is_grantable
""")
_SCHEMA = text("""
SELECT pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(jsonb_build_object(
 'relations',(SELECT jsonb_agg(jsonb_build_array(c.oid,c.relname,c.relkind,c.relowner,c.relpersistence,c.relrowsecurity,c.relforcerowsecurity) ORDER BY c.oid) FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public'),
 'attributes',(SELECT jsonb_agg(jsonb_build_array(a.attrelid,a.attnum,a.attname,a.atttypid,a.atttypmod,a.attnotnull,a.attisdropped,a.attcollation,a.attacl,pg_catalog.pg_get_expr(d.adbin,d.adrelid)) ORDER BY a.attrelid,a.attnum) FROM pg_catalog.pg_attribute a JOIN pg_catalog.pg_class c ON c.oid=a.attrelid JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace LEFT JOIN pg_catalog.pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum WHERE n.nspname='public' AND a.attnum>0),
 'constraints',(SELECT jsonb_agg(jsonb_build_array(c.oid,c.conrelid,c.conname,pg_catalog.pg_get_constraintdef(c.oid)) ORDER BY c.oid) FROM pg_catalog.pg_constraint c JOIN pg_catalog.pg_namespace n ON n.oid=c.connamespace WHERE n.nspname='public'),
 'triggers',(SELECT jsonb_agg(jsonb_build_array(t.oid,t.tgrelid,t.tgenabled,pg_catalog.pg_get_triggerdef(t.oid)) ORDER BY t.oid) FROM pg_catalog.pg_trigger t JOIN pg_catalog.pg_class c ON c.oid=t.tgrelid JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public'),
 'indexes',(SELECT jsonb_agg(jsonb_build_array(i.indexrelid,pg_catalog.pg_get_indexdef(i.indexrelid),i.indisvalid,i.indisready) ORDER BY i.indexrelid) FROM pg_catalog.pg_index i JOIN pg_catalog.pg_class c ON c.oid=i.indrelid JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public'),
 'functions',(SELECT jsonb_agg(to_jsonb(p) ORDER BY p.oid) FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public' AND p.oid<>'public.g12_invocation_publication_guard()'::regprocedure),
 'types',(SELECT jsonb_agg(jsonb_build_array(t.oid,t.typname,t.typtype,t.typbasetype,t.typrelid) ORDER BY t.oid) FROM pg_catalog.pg_type t JOIN pg_catalog.pg_namespace n ON n.oid=t.typnamespace WHERE n.nspname='public'),
 'enums',(SELECT jsonb_agg(jsonb_build_array(e.enumtypid,e.enumsortorder,e.enumlabel) ORDER BY e.enumtypid,e.enumsortorder) FROM pg_catalog.pg_enum e JOIN pg_catalog.pg_type t ON t.oid=e.enumtypid JOIN pg_catalog.pg_namespace n ON n.oid=t.typnamespace WHERE n.nspname='public'),
 'sequences',(SELECT jsonb_agg(to_jsonb(s) ORDER BY sequencename) FROM pg_catalog.pg_sequences s WHERE schemaname='public'),
 'default_acl',(SELECT jsonb_agg(to_jsonb(a) ORDER BY oid) FROM pg_catalog.pg_default_acl a)
)::text,'UTF8')),'hex')
""")
_ROLE_STATE = text("""
SELECT pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(jsonb_build_object(
 'roles',(SELECT jsonb_agg(jsonb_build_array(oid,rolname,rolsuper,rolinherit,rolcreaterole,rolcreatedb,rolcanlogin,rolreplication,rolconnlimit,rolvaliduntil,rolbypassrls,rolconfig) ORDER BY oid) FROM pg_catalog.pg_roles WHERE oid=ANY(CAST(:oids AS oid[]))),
 'memberships',(SELECT jsonb_agg(to_jsonb(m) ORDER BY roleid,member,grantor) FROM pg_catalog.pg_auth_members m WHERE roleid=ANY(CAST(:oids AS oid[])) OR member=ANY(CAST(:oids AS oid[]))),
 'settings',(SELECT jsonb_agg(to_jsonb(s) ORDER BY setdatabase,setrole) FROM pg_catalog.pg_db_role_setting s WHERE setrole=ANY(CAST(:oids AS oid[])) OR setdatabase=(SELECT oid FROM pg_catalog.pg_database WHERE datname=current_database()))
)::text,'UTF8')),'hex')
""")
_COUNTS = {
    "reservations": "SELECT count(*) FROM public.qualification_reservations WHERE state<>'reconciled_flat'",
    "configuration": "SELECT count(*) FROM public.qualification_leverage_configuration_intents_v1 WHERE phase NOT IN ('settled','no_change','not_sent')",
    "mirror_positions": "SELECT count(*) FROM public.okx_demo_position_state",
    "mirror_orders": "SELECT count(*) FROM public.okx_demo_order_state WHERE state NOT IN ('filled','canceled','mmp_canceled')",
    "mirror_algos": "SELECT count(*) FROM public.okx_demo_algo_order_state WHERE state NOT IN ('canceled','order_failed','effective')",
    "positions": "SELECT count(*) FROM public.positions WHERE status<>'closed' OR closed_at IS NULL",
    "orders": "SELECT count(*) FROM public.orders WHERE status NOT IN ('filled','canceled','cancelled','rejected','expired','mmp_canceled')",
    "protection": "SELECT count(*) FROM public.protective_orders WHERE status NOT IN ('filled','canceled','cancelled','rejected','expired','closed')",
    "trades": "SELECT count(*) FROM public.trades WHERE status<>'closed' OR closed_at IS NULL",
    "soak": "SELECT count(*) FROM public.demo_soak_sessions WHERE state='running'",
}


class OwnerProfileUpgrade0057Refused(ValueError):
    pass


class OwnerProfileUpgrade0057CommitUnknown(RuntimeError):
    pass


class OwnerProfileUpgrade0057ReadbackRequired(RuntimeError):
    pass


def _need(value):
    if value is not True:
        raise OwnerProfileUpgrade0057Refused("owner_profile_0057_refused") from None


@dataclass(frozen=True, slots=True, repr=False)
class OwnerProfileUpgrade0057PinsV1:
    source_commit: str
    source_tree: str
    source_archive_sha256: str
    route: route.OwnerRoutePinsV1
    role_names: tuple[str, str, str]
    role_oids: tuple[int, int, int]


def _prepare(pins, dsn, ca, archive):
    _need(type(pins) is OwnerProfileUpgrade0057PinsV1)
    _need(
        type(pins.route) is route.OwnerRoutePinsV1
        and pins.route.database_head in {"0056", "0057"}
    )
    _need(type(pins.role_names) is tuple and len(pins.role_names) == 3)
    _need(
        all(
            type(name) is str
            and old.re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", name) is not None
            for name in pins.role_names
        )
    )
    _need(len(set(pins.role_names)) == 3 and pins.route.owner not in pins.role_names)
    _need(
        type(pins.role_oids) is tuple
        and len(pins.role_oids) == 3
        and len(set(pins.role_oids)) == 3
        and all(
            type(oid) is int and 0 < oid < 2**32 and oid != pins.route.owner_oid
            for oid in pins.role_oids
        )
    )
    # The fixed renderer validates every identifier and exact 42+2 plan.
    _need(
        len(
            grants.current57_native_g12_grant_sql_v1(
                writer=pins.role_names[0],
                custody=pins.role_names[1],
                frame_reader=pins.role_names[2],
            )
        )
        == 2
    )
    private_inputs._require_private_file(dsn)
    prepared = route._prepare(
        pins.route, owner_dsn_file=dsn, ca_file=ca, profile=profiles.DB0057
    )
    old._verify_reviewed_source_archive(
        archive,
        source_commit=pins.source_commit,
        source_tree=pins.source_tree,
        source_archive_sha256=pins.source_archive_sha256,
        source_manifest_sha256=pins.route.source_manifest_sha256,
    )
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("path_separator", "os")
    config.set_main_option("script_location", str(ROOT / "migrations"))
    script = ScriptDirectory.from_config(config)
    _need(
        prepared.source_head in {"0057", "0058"}
        and tuple(script.get_heads()) == (prepared.source_head,)
        and script.get_revision("0057").down_revision == "0056"
    )
    return prepared, script


def _checked_owner(row, pins, head, *, read_only):
    old._checked_profile(row, pins.route, head, read_only=read_only)


def _checked_preflight(observation, pins, prepared):
    expected = {
        "schema": "ctcc.native_owner_0057.route_preflight.v1",
        "status": "PASS",
        "source_head": prepared.source_head,
        "source_manifest_sha256": prepared.manifest_sha256,
        "migration_sha256": prepared.migration_sha256,
        "database_head": pins.route.database_head,
        "route_sha256": prepared.route_sha256,
        "cluster_system_identifier_sha256": pins.route.cluster_system_identifier_sha256,
        "ca_sha256": pins.route.ca_sha256,
        "independent_readonly_observations": 2,
        "database_owner_verified": True,
        "password_authentication_verified": False,
        "schema_object_ownership_verified": False,
        "schema_drift_verified": False,
        "owner_provision_verified": False,
        "startup_admission": False,
        "execution_authority": False,
    }
    _need(type(observation) is dict and canonical(observation) == canonical(expected))


def _same_source(left, right):
    _need(
        left.url == right.url
        and left.manifest_sha256 == right.manifest_sha256
        and left.migration_sha256 == right.migration_sha256
        and left.source_head == right.source_head
        and left.source_head in {"0057", "0058"}
        and left.route_sha256 == right.route_sha256
    )


async def _owner_observation(engine, pins, head):
    from app.core import native_role_preflight_0056_full_diagnostic_v1 as roles

    profile = profiles.DB0056 if head == "0056" else profiles.DB0057
    async with engine.connect() as connection, connection.begin():
        for sql in old._READBACK_SETTINGS:
            await connection.exec_driver_sql(sql)
        _checked_owner(
            tuple((await connection.execute(route._PROFILE_SQL)).one()),
            pins,
            head,
            read_only=True,
        )
        factory = async_sessionmaker(engine, autoflush=False, expire_on_commit=False)
    # The typed owner identity query uses a separate physical transaction.
    async with factory() as session, session.begin():
        for sql in roles._OWNER_SETTINGS:
            await session.execute(text(sql))
        row = tuple((await session.execute(route._PROFILE_SQL)).one())
        _checked_owner(row, pins, head, read_only=True)
        actor = await roles._identity_at_0056(
            session, pins.route.owner, **profiles.arguments(profile)
        )
        _need(
            (actor[3], actor[4], actor[8])
            == (pins.route.owner_oid, pins.route.database_oid, pins.route.port)
        )
    return actor


def _server_sha(actor, profile):
    return sha(
        canonical(
            {
                "schema": profiles.schema(
                    profile, "ctcc.native_only.pg0056_incarnation.v1"
                ),
                "database_oid": actor[4],
                "postmaster_started_at": actor[5]
                .astimezone(UTC)
                .isoformat(timespec="microseconds"),
                "server_version_num": actor[6],
                "server_address": actor[7],
                "server_port": actor[8],
            }
        )
    )


async def _role_observation(selected, owner_engine, pins, head):
    from app.core import native_role_identity_0056_v2 as identity

    profile = profiles.DB0056 if head == "0056" else profiles.DB0057
    before = await _owner_observation(owner_engine, pins, head)
    actual = await identity.observe_restricted_role_identity_0056_v2(
        selected, source=ROOT, **profiles.arguments(profile)
    )
    after = await _owner_observation(owner_engine, pins, head)
    _need(
        type(actual) is identity.NativeRoleIdentity0056V2
        and actual.schema
        == profiles.schema(profile, "ctcc.native_only.restricted_role_identity0056.v2")
        and actual.deployment_reference_authenticated is False
        and before == after
        and actual.database_oid == pins.route.database_oid
        and actual.owner_oid == pins.route.owner_oid
        and actual.role_names == pins.role_names
        and actual.role_oids == pins.role_oids
        and actual.server_incarnation_sha256 == _server_sha(before, profile)
        and actual.startup_admission is actual.execution_authority is False
        and actual.admission == "DENY"
    )
    return actual, before


async def _configured_resources(stack, prepared, pins):
    from app.config.settings import get_settings
    from app.demo_automation import native_server_selectors_v2 as selectors

    configured = selectors.select_configured_native_demo_factories_v2()
    stack.push_async_callback(configured.custody_engine.dispose)
    stack.push_async_callback(configured.frame_reader_engine.dispose)
    _need(get_settings().ctcc_native_pg_tls_ca_sha256 == pins.route.ca_sha256)
    urls = tuple(
        factory.kw["bind"].url
        for factory in (
            configured.writer_factory,
            configured.custody_factory,
            configured.frame_reader_factory,
        )
    )
    expected = (
        "postgresql+asyncpg",
        pins.route.host,
        pins.route.port,
        pins.route.database,
    )
    _need(
        tuple(url.username for url in urls) == pins.role_names
        and all(selectors._database_route(url) == expected for url in urls)
    )
    engines = []
    for url in (prepared.url, *urls):
        engine = create_async_engine(
            url,
            poolclass=NullPool,
            connect_args={"ssl": prepared.tls, "timeout": 10},
            hide_parameters=True,
        )
        stack.push_async_callback(engine.dispose)
        engines.append(engine)
    owner, writer, custody, frame = engines

    def factory(engine):
        return async_sessionmaker(engine, autoflush=False, expire_on_commit=False)

    return owner, selectors.ConfiguredNativeDemoFactoriesV2(
        factory(writer), factory(custody), factory(frame), custody, frame
    )


def _utc(value):
    _need(type(value) is str)
    return control.utc(datetime.fromisoformat(value))


def _validate_local(document):
    _need(
        type(document) is dict
        and set(document) == {"now", "controls", "automation", "counts"}
    )
    now = _utc(document["now"])
    _need(type(document["controls"]) is list and 1 <= len(document["controls"]) <= 4096)
    for row in document["controls"]:
        state = control.decode(row["state_json"])
        _need(
            state.scope.environment == row["environment"] == "demo"
            and state.scope.account_id == row["account_id"]
            and state.revision == row["control_revision"]
            and control.digest(row["state_json"]) == row["state_sha256"]
            and state.owner_sha256 == row["owner_sha256"]
            and state.owner_epoch == row["owner_epoch"]
            and state.emergency_stop is row["emergency_stop"] is True
            and state.arm_request_id is row["arm_request_id"] is None
            and state.arm_expires_at is row["arm_expires_at"] is None
            and state.lease_until == _utc(row["lease_until"]) <= now
            and state.created_at == _utc(row["created_at"])
            and state.updated_at == _utc(row["updated_at"]) <= now
        )
    rows = document["automation"]
    _need(type(rows) is list and len(rows) == 1)
    state = rows[0]
    _need(
        type(state["id"]) is int
        and state["id"] == 1
        and state["armed"] is False
        and state["emergency_stop"]
        is state["locked"]
        is state["restart_latch_required"]
        is True
        and type(state["control_revision"]) is int
        and state["control_revision"] >= 1
        and type(state["lock_reasons"]) is list
        and "emergency_stop_engaged" in state["lock_reasons"]
        and all(type(item) is str and item for item in state["lock_reasons"])
        and state["last_error"] is None
        and type(state["active_trades"]) is dict
        and not state["active_trades"]
        and all(
            state[name] is None
            for name in (
                "active_instrument_id",
                "active_client_order_id",
                "active_start_equity",
                "active_started_at",
            )
        )
        and _utc(state["updated_at"]) <= now
    )
    first, last = state["last_started_at"], state["last_completed_at"]
    _need(
        (first is last is None)
        or first is not None
        and last is not None
        and _utc(first) <= _utc(last) <= now
    )
    _need(
        type(document["counts"]) is dict
        and set(document["counts"]) == set(_COUNTS)
        and all(
            type(count) is int and count == 0 for count in document["counts"].values()
        )
    )


async def _local(connection):
    now = (
        (await connection.scalar(text("SELECT pg_catalog.clock_timestamp()")))
        .astimezone(UTC)
        .isoformat()
    )
    controls = list(
        (
            await connection.execute(
                text(
                    "SELECT environment,account_id,control_revision,owner_sha256,owner_epoch,state_json,state_sha256,emergency_stop,arm_request_id,arm_expires_at,lease_until,created_at,updated_at FROM public.demo_account_controls ORDER BY environment,account_id LIMIT 4097"
                )
            )
        )
        .mappings()
        .all()
    )
    controls = [
        {
            **dict(row),
            **{
                name: row[name].astimezone(UTC).isoformat()
                for name in ("lease_until", "created_at", "updated_at")
            },
        }
        for row in controls
    ]
    automation = await connection.scalar(
        text(
            "SELECT jsonb_agg(to_jsonb(s) ORDER BY id) FROM public.demo_automation_state s"
        )
    )
    counts = {name: await connection.scalar(text(sql)) for name, sql in _COUNTS.items()}
    result = {
        "now": now,
        "controls": controls,
        "automation": automation,
        "counts": counts,
    }
    _validate_local(result)
    return result


async def _relations(connection, *, lock):
    rows = tuple(tuple(row) for row in (await connection.execute(_RELATIONS)).all())
    _need(0 < len(rows) <= _MAX_RELATIONS and all(row[2] in {"r", "p"} for row in rows))
    for name, oid, _kind in rows:
        _need(
            type(name) is str
            and old.re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", name) is not None
            and type(oid) is int
            and oid > 0
        )
    if lock:
        names = ",".join(
            'public."' + row[0] + '"' for row in rows if row[0] != "alembic_version"
        )
        _need(bool(names))
        await connection.exec_driver_sql(
            "LOCK TABLE " + names + " IN SHARE ROW EXCLUSIVE MODE NOWAIT"
        )
    return rows


async def _snapshot(connection, relations, pins):
    snapshot = await retained_rows.snapshot_rows_v2(
        connection,
        relations,
        owner_oid=pins.route.owner_oid,
        marker_scope=retained_rows.MarkerScopeV2.EXCLUDE_UPGRADE_MARKER,
        max_rows=_MAX_ROWS,
        max_table_bytes=_MAX_ROW_BYTES,
        max_total_bytes=_MAX_ROW_BYTES * _MAX_RELATIONS,
    )
    catalog = await connection.scalar(_SCHEMA)
    role_state = await connection.scalar(
        _ROLE_STATE, {"oids": [pins.route.owner_oid, *pins.role_oids]}
    )
    _need(old._pin(catalog, old._HEX) and old._pin(role_state, old._HEX))
    return snapshot | {"schema_sha256": catalog, "role_state_sha256": role_state}


async def _readonly_inventory(owner, pins):
    async with owner.connect() as connection, connection.begin():
        for sql in old._READBACK_SETTINGS:
            await connection.exec_driver_sql(sql)
        _checked_owner(
            tuple((await connection.execute(route._PROFILE_SQL)).one()),
            pins,
            pins.route.database_head,
            read_only=True,
        )
        relations = await _relations(connection, lock=False)
        await _local(connection)
        snapshot = await _snapshot(connection, relations, pins)
        acl = tuple(tuple(row) for row in (await connection.execute(_ACL)).all())
        return snapshot, acl


def _expected_acl(before, *, target_oids, writer_oid, owner_oid):
    _need(
        type(target_oids) is tuple
        and len(target_oids) == 2
        and all(
            type(oid) is int and 0 < oid < 2**32
            for oid in (*target_oids, writer_oid, owner_oid)
        )
        and len(set(target_oids)) == 2
        and writer_oid != owner_oid
    )
    delta = tuple(
        ("relation", oid, writer_oid, owner_oid, privilege, False)
        for oid in target_oids
        for privilege in ("SELECT", "INSERT")
    )
    _need(not any(row in before for row in delta))
    return tuple(sorted((*before, *delta)))


def _migrate(connection, script):
    migration = MigrationContext.configure(
        connection, opts={"fn": lambda heads, _: script._upgrade_revs("0057", heads)}
    )
    _need(migration.get_current_heads() == ("0056",))
    with Operations.context(migration), migration.begin_transaction():
        migration.run_migrations()
    _need(migration.get_current_heads() == ("0057",))


async def _commit_once(
    script, pins, owner_engine, expected_snapshot, expected_before_acl
):
    attempted = committed = False
    try:
        async with asyncio.timeout(180), owner_engine.connect() as connection:
            transaction = await connection.begin()
            try:
                for sql in old._OWNER_SETTINGS:
                    await connection.exec_driver_sql(sql)
                await connection.execute(old._OWNER_LOCK)
                await connection.exec_driver_sql(old._LOCK_REVISION)
                _checked_owner(
                    tuple((await connection.execute(route._PROFILE_SQL)).one()),
                    pins,
                    "0056",
                    read_only=False,
                )
                relations = await _relations(connection, lock=True)
                await _local(connection)
                from app.core.native_role_preflight_v1 import _exact_existing_acl

                await _exact_existing_acl(
                    connection, pins.role_names, pins.role_oids[0]
                )
                before = await _snapshot(connection, relations, pins)
                acl = tuple(
                    tuple(row) for row in (await connection.execute(_ACL)).all()
                )
                _need(before == expected_snapshot and acl == expected_before_acl)
                targets = tuple(
                    [
                        await connection.scalar(
                            text("SELECT CAST(:name AS regclass)::oid::bigint"),
                            {"name": "public." + name},
                        )
                        for name in grants.g12.TABLES
                    ]
                )
                expected_acl = _expected_acl(
                    acl,
                    target_oids=targets,
                    writer_oid=pins.role_oids[0],
                    owner_oid=pins.route.owner_oid,
                )
                await connection.run_sync(_migrate, script)
                for sql in grants.current57_native_g12_grant_sql_v1(
                    writer=pins.role_names[0],
                    custody=pins.role_names[1],
                    frame_reader=pins.role_names[2],
                ):
                    await connection.exec_driver_sql(sql)
                _checked_owner(
                    tuple((await connection.execute(route._PROFILE_SQL)).one()),
                    pins,
                    "0057",
                    read_only=False,
                )
                _need(await _relations(connection, lock=False) == relations)
                await _local(connection)
                _need(await _snapshot(connection, relations, pins) == before)
                _need(
                    tuple(tuple(row) for row in (await connection.execute(_ACL)).all())
                    == expected_acl
                )
                attempted = True
                await transaction.commit()
                committed = True
                return before, sha(canonical(expected_acl))
            except BaseException:
                if not attempted:
                    await transaction.rollback()
                raise
    except BaseException:  # noqa: BLE001 -- cancellation after COMMIT is uncertain; redact drivers.
        if committed:
            raise OwnerProfileUpgrade0057ReadbackRequired(
                "owner_profile_0057_committed_readback_required"
            ) from None
        if attempted:
            raise OwnerProfileUpgrade0057CommitUnknown(
                "owner_profile_0057_commit_unknown_do_not_retry"
            ) from None
        raise OwnerProfileUpgrade0057Refused("owner_profile_0057_refused") from None


async def install_reviewed_owner_profile_0057_v1(
    pins, *, owner_dsn_file, ca_file, source_archive_file, confirmation=None
):
    """Read-only by default; exact confirmation enables only this profile DDL."""
    committed = False
    try:
        _need(
            confirmation is None
            or type(confirmation) is str
            and confirmation == CONFIRMATION
        )
        prepared, script = _prepare(pins, owner_dsn_file, ca_file, source_archive_file)
        async with AsyncExitStack() as stack:
            owner, selected = await _configured_resources(stack, prepared, pins)
            initial = await route.preflight_owner_route_0056_v1(
                pins.route,
                owner_dsn_file=owner_dsn_file,
                ca_file=ca_file,
                profile=profiles.DB0057,
            )
            _checked_preflight(initial, pins, prepared)
            before_inventory = await _readonly_inventory(owner, pins)
            identity, incarnation = await _role_observation(
                selected, owner, pins, pins.route.database_head
            )
            _need(await _readonly_inventory(owner, pins) == before_inventory)
            fresh, _ = _prepare(pins, owner_dsn_file, ca_file, source_archive_file)
            _same_source(fresh, prepared)
            if confirmation is None:
                return {
                    "schema": "ctcc.owner_profile0057.dry_run.v2",
                    "row_encoding": retained_rows.ROW_ENCODING,
                    "status": "DRY_RUN_PASS",
                    "database_head": pins.route.database_head,
                    "role_profile_sha256": identity.stable_identity_sha256,
                    "execution_authority": False,
                    "account_complete": False,
                    "order_writes": False,
                }
            _need(pins.route.database_head == "0056")
            snapshot, acl_sha = await _commit_once(
                script, pins, owner, *before_inventory
            )
            committed = True
            after_pins = replace(pins, route=replace(pins.route, database_head="0057"))
            observed = await route.preflight_owner_route_0056_v1(
                after_pins.route,
                owner_dsn_file=owner_dsn_file,
                ca_file=ca_file,
                profile=profiles.DB0057,
            )
            after_prepared, _ = _prepare(
                after_pins, owner_dsn_file, ca_file, source_archive_file
            )
            _checked_preflight(observed, after_pins, after_prepared)
            after, actual_incarnation = await _role_observation(
                selected, owner, after_pins, "0057"
            )
            _need(actual_incarnation == incarnation)
            actual_snapshot, actual_acl = await _readonly_inventory(owner, after_pins)
            _need(actual_snapshot == snapshot and sha(canonical(actual_acl)) == acl_sha)
            final_prepared, _ = _prepare(
                after_pins, owner_dsn_file, ca_file, source_archive_file
            )
            _same_source(final_prepared, after_prepared)
        return {
            "schema": "ctcc.owner_profile0057.commit_readback.v2",
            "row_encoding": retained_rows.ROW_ENCODING,
            "status": "PASS",
            "database_head_before": "0056",
            "database_head_after": "0057",
            "source_commit": pins.source_commit,
            "source_tree": pins.source_tree,
            "source_archive_sha256": pins.source_archive_sha256,
            "source_manifest_sha256": prepared.manifest_sha256,
            "before_role_profile_sha256": identity.stable_identity_sha256,
            "after_role_profile_sha256": after.stable_identity_sha256,
            "preserved_rows_sha256": sha(canonical(snapshot)),
            "exact_acl_sha256": acl_sha,
            "fixed_g12_grant_statements": 2,
            "restricted_grant_statements_after": 44,
            "stopped_local_inventory_verified": True,
            "account_complete": False,
            "startup_admission": False,
            "execution_authority": False,
            "order_writes": False,
        }
    except (
        OwnerProfileUpgrade0057CommitUnknown,
        OwnerProfileUpgrade0057ReadbackRequired,
    ):
        raise
    except BaseException:  # noqa: BLE001 -- preserve terminal commit state and redact private details.
        if committed:
            raise OwnerProfileUpgrade0057ReadbackRequired(
                "owner_profile_0057_committed_readback_required"
            ) from None
        raise OwnerProfileUpgrade0057Refused("owner_profile_0057_refused") from None


def _load_pins(path, expected_sha):
    _need(old._pin(expected_sha, old._HEX))
    private_inputs._require_private_file(path)
    raw = _read_bounded_regular_file_v1(path, 65536)
    _need(sha(raw) == expected_sha)
    value = read_evidence_json(raw)
    _need(
        type(value) is dict
        and canonical(value) == raw
        and set(value)
        == {
            "schema",
            "source_commit",
            "source_tree",
            "source_archive_sha256",
            "route",
            "role_names",
            "role_oids",
        }
        and value["schema"] == "ctcc.owner_profile0057.reviewed_inputs.v1"
        and type(value["route"]) is dict
        and set(value["route"]) == set(route.OwnerRoutePinsV1.__dataclass_fields__)
        and type(value["role_names"]) is list
        and type(value["role_oids"]) is list
    )
    return OwnerProfileUpgrade0057PinsV1(
        value["source_commit"],
        value["source_tree"],
        value["source_archive_sha256"],
        route.OwnerRoutePinsV1(**value["route"]),
        tuple(value["role_names"]),
        tuple(value["role_oids"]),
    )


def main(argv=None):
    class PrivateParser(argparse.ArgumentParser):
        def error(self, message):
            _need(False)

    parser = PrivateParser(add_help=False)
    parser.add_argument("--reviewed-pins-file", type=Path)
    parser.add_argument("--reviewed-pins-sha256")
    parser.add_argument("--owner-dsn-file", type=Path)
    parser.add_argument("--ca-file", type=Path)
    parser.add_argument("--source-archive-file", type=Path)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirmation")
    try:
        args = parser.parse_args(argv)
        _need(
            all(
                isinstance(value, Path)
                for value in (
                    args.reviewed_pins_file,
                    args.owner_dsn_file,
                    args.ca_file,
                    args.source_archive_file,
                )
            )
        )
        _need(
            (args.execute and args.confirmation == CONFIRMATION)
            or not args.execute
            and args.confirmation is None
        )
        result = asyncio.run(
            install_reviewed_owner_profile_0057_v1(
                _load_pins(args.reviewed_pins_file, args.reviewed_pins_sha256),
                owner_dsn_file=args.owner_dsn_file,
                ca_file=args.ca_file,
                source_archive_file=args.source_archive_file,
                confirmation=args.confirmation if args.execute else None,
            )
        )
    except OwnerProfileUpgrade0057CommitUnknown:
        print(
            '{"status":"INCOMPLETE","code":"owner_profile_0057_commit_unknown_do_not_retry"}'
        )
        return 3
    except OwnerProfileUpgrade0057ReadbackRequired:
        print(
            '{"status":"INCOMPLETE","code":"owner_profile_0057_committed_readback_required"}'
        )
        return 4
    except BaseException:  # noqa: BLE001 -- CLI emits fixed codes, including cancellation.
        print('{"status":"REFUSED","code":"owner_profile_0057_refused"}')
        return 1
    print(canonical(result).decode("ascii"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
