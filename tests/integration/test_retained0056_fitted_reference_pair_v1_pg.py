"""One actual fresh PostgreSQL 17.11 bootstrap-owner fitted reference."""

from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from scripts import current0056_fitted_catalog_pair_v1 as pinned_pg
from scripts import current0056_fitted_delta_catalog_v1 as fitted
from scripts import native_owner_upgrade_0056_v1 as installed
from scripts import retained0017_bootstrap_catalog_v2 as bootstrap
from scripts import retained0056_fitted_reference_pair_v1 as reference
from scripts import retained0056_unscoped_fitted_observation_v1 as unscoped
from tests.integration import test_gate3_fitted_candidate_custody_pg as lab
from tests.native_owner_migration_lab_0056_v1 import migrate_fresh_lab_owner_0056_v1

ROOT = Path(__file__).resolve().parents[2]
pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
_IDENTITY = text("""
SELECT d.oid::pg_catalog.int8,c.oid::pg_catalog.int8,
       pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(s.system_identifier::pg_catalog.text,'UTF8')),'hex'),
       pg_catalog.version(),
       r.oid=10 AND d.datdba=r.oid AND r.rolsuper AND r.rolinherit AND r.rolcreatedb
         AND r.rolcreaterole AND r.rolcanlogin AND r.rolreplication AND r.rolbypassrls
         AND NOT EXISTS(SELECT 1 FROM pg_catalog.pg_roles x WHERE x.rolcanlogin AND x.oid<>10)
         AND NOT EXISTS(SELECT 1 FROM pg_catalog.pg_auth_members m WHERE 10 IN (m.member,m.roleid))
         AND pg_catalog.current_setting('role')='none'
         AND pg_catalog.current_setting('transaction_read_only')='on'
         AND pg_catalog.current_setting('transaction_isolation')='repeatable read'
         AND EXISTS(SELECT 1 FROM pg_catalog.pg_stat_ssl WHERE pid=pg_catalog.pg_backend_pid() AND ssl)
FROM pg_catalog.pg_database d, pg_catalog.pg_roles r,
     pg_catalog.pg_class c, pg_catalog.pg_control_system() s
WHERE d.datname=pg_catalog.current_database() AND r.rolname=CURRENT_USER AND r.rolname=SESSION_USER
  AND c.oid='public.alembic_version'::pg_catalog.regclass AND c.relowner=r.oid AND c.relkind='r'
""")

# The exact fitted observer keeps raw ACL representations. This closure only
# replaces allocated object addresses in graph edges. The two allocated name
# forms below are derived from real parent OIDs, not from prefix guesses alone.
_DEPENDENCIES = text("""
WITH tables AS MATERIALIZED (
  SELECT c.* FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
  WHERE n.nspname='public' AND c.relkind='r' AND c.relname=ANY(CAST(:tables AS pg_catalog.text[]))
), toast AS MATERIALIZED (
  SELECT p.relname AS parent_name,p.oid AS parent_oid,c.oid,c.relowner,c.relacl,c.relname
  FROM tables p JOIN pg_catalog.pg_class c ON c.oid=p.reltoastrelid
  JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
  WHERE n.nspname='pg_toast' AND c.relkind='t' AND c.relname=('pg_toast_'||p.oid::pg_catalog.text)
), toast_indexes AS MATERIALIZED (
  SELECT t.parent_name,c.oid,c.relowner,c.relacl FROM toast t
  JOIN pg_catalog.pg_index i ON i.indrelid=t.oid JOIN pg_catalog.pg_class c ON c.oid=i.indexrelid
  WHERE c.relkind='i' AND c.relname=('pg_toast_'||t.parent_oid::pg_catalog.text||'_index')
), constraints AS MATERIALIZED (
  SELECT k.* FROM pg_catalog.pg_constraint k JOIN tables t ON t.oid=k.conrelid
), triggers AS MATERIALIZED (
  SELECT g.*,k.conname,k.contype FROM pg_catalog.pg_trigger g
  LEFT JOIN constraints k ON k.oid=g.tgconstraint
  WHERE g.tgrelid IN (SELECT oid FROM tables) OR k.oid IS NOT NULL
    OR (g.tgname='gate3_fitted_candidate_claim_guard' AND g.tgrelid='public.gate3_capture_schedule_key_claims'::pg_catalog.regclass)
), types AS MATERIALIZED (
  SELECT y.* FROM pg_catalog.pg_type y JOIN tables t ON y.oid=t.reltype
  UNION ALL
  SELECT a.* FROM pg_catalog.pg_type a JOIN pg_catalog.pg_type y ON a.oid=y.typarray
  JOIN tables t ON t.reltype=y.oid
), routines AS MATERIALIZED (
  SELECT p.* FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
  WHERE n.nspname='public' AND p.oid::pg_catalog.regprocedure::pg_catalog.text=ANY(CAST(:routines AS pg_catalog.text[]))
), targets(classid,objid,alias,owner_oid,raw_acl) AS MATERIALIZED (
  SELECT 'pg_catalog.pg_class'::pg_catalog.regclass::pg_catalog.oid,t.oid,NULL::pg_catalog.jsonb,t.relowner,t.relacl::pg_catalog.text[] FROM tables t
  UNION ALL SELECT 'pg_catalog.pg_class'::pg_catalog.regclass::pg_catalog.oid,c.oid,NULL,c.relowner,c.relacl::pg_catalog.text[]
    FROM pg_catalog.pg_class c JOIN pg_catalog.pg_index i ON c.oid=i.indexrelid JOIN tables t ON t.oid=i.indrelid
  UNION ALL SELECT 'pg_catalog.pg_class'::pg_catalog.regclass::pg_catalog.oid,oid,
    pg_catalog.jsonb_build_array('toast_of',parent_name),relowner,relacl::pg_catalog.text[] FROM toast
  UNION ALL SELECT 'pg_catalog.pg_class'::pg_catalog.regclass::pg_catalog.oid,oid,
    pg_catalog.jsonb_build_array('toast_index_of',parent_name),relowner,relacl::pg_catalog.text[] FROM toast_indexes
  UNION ALL SELECT 'pg_catalog.pg_type'::pg_catalog.regclass::pg_catalog.oid,oid,NULL,typowner,typacl::pg_catalog.text[] FROM types
  UNION ALL SELECT 'pg_catalog.pg_proc'::pg_catalog.regclass::pg_catalog.oid,oid,NULL,proowner,proacl::pg_catalog.text[] FROM routines
  UNION ALL SELECT 'pg_catalog.pg_constraint'::pg_catalog.regclass::pg_catalog.oid,oid,NULL,NULL,NULL FROM constraints
  UNION ALL SELECT 'pg_catalog.pg_attrdef'::pg_catalog.regclass::pg_catalog.oid,d.oid,NULL,NULL,NULL
    FROM pg_catalog.pg_attrdef d JOIN tables t ON t.oid=d.adrelid
  UNION ALL SELECT 'pg_catalog.pg_trigger'::pg_catalog.regclass::pg_catalog.oid,g.oid,
    CASE WHEN g.tgisinternal THEN pg_catalog.jsonb_build_array('foreign_key_trigger',g.conname,
      g.tgrelid::pg_catalog.regclass::pg_catalog.text,g.tgfoid::pg_catalog.regprocedure::pg_catalog.text,
      g.tgtype,g.tgenabled::pg_catalog.text,g.tgdeferrable,g.tginitdeferred) END,NULL,NULL
    FROM triggers g
), dependencies AS MATERIALIZED (
  SELECT d.* FROM pg_catalog.pg_depend d WHERE EXISTS(SELECT 1 FROM targets t
    WHERE (d.classid,d.objid)=(t.classid,t.objid) OR (d.refclassid,d.refobjid)=(t.classid,t.objid))
), references_set(classid,objid) AS MATERIALIZED (
  SELECT classid,objid FROM targets UNION SELECT classid,objid FROM dependencies
  UNION SELECT refclassid,refobjid FROM dependencies
), names AS MATERIALIZED (
  SELECT r.classid,r.objid,COALESCE(t.alias,pg_catalog.to_jsonb(pg_catalog.pg_identify_object(r.classid,r.objid,0))) AS semantic
  FROM references_set r LEFT JOIN targets t ON (t.classid,t.objid)=(r.classid,r.objid)
), graph AS MATERIALIZED (
  SELECT pg_catalog.jsonb_build_array(d.classid::pg_catalog.int8,src.semantic,d.objsubid,
    d.refclassid::pg_catalog.int8,dst.semantic,d.refobjsubid,d.deptype::pg_catalog.text) AS edge
  FROM dependencies d JOIN names src ON (src.classid,src.objid)=(d.classid,d.objid)
  JOIN names dst ON (dst.classid,dst.objid)=(d.refclassid,d.refobjid)
), bound AS MATERIALIZED (
  SELECT pg_catalog.jsonb_build_array(t.classid::pg_catalog.int8,n.semantic,t.owner_oid::pg_catalog.int8,t.raw_acl) AS entry
  FROM targets t JOIN names n ON (t.classid,t.objid)=(n.classid,n.objid)
)
SELECT pg_catalog.jsonb_build_object(
 'layout_ok',(SELECT count(*)=3 FROM tables) AND (SELECT count(*)=20 FROM routines)
  AND (SELECT count(*)=6 FROM types) AND (SELECT count(*)=3 FROM toast) AND (SELECT count(*)=3 FROM toast_indexes)
  AND (SELECT count(*)=12 FROM triggers WHERE tgisinternal)
  AND NOT EXISTS(SELECT 1 FROM triggers WHERE tgisinternal AND (contype IS DISTINCT FROM 'f'
    OR tgname !~ '^RI_ConstraintTrigger_[ac]_[0-9]+$' OR tgenabled<>'O' OR tgdeferrable OR tginitdeferred))
  AND NOT EXISTS(SELECT 1 FROM targets WHERE owner_oid IS NOT NULL AND owner_oid<>10)
  AND NOT EXISTS(SELECT 1 FROM names WHERE semantic IS NULL OR semantic->>'identity' IS NULL
    AND pg_catalog.jsonb_typeof(semantic)<>'array')
  AND NOT EXISTS(SELECT 1 FROM dependencies WHERE deptype NOT IN ('n','a','i')),
 'shared',(SELECT count(*) FROM pg_catalog.pg_shdepend d JOIN targets t ON (d.classid,d.objid)=(t.classid,t.objid)
   WHERE d.dbid IN (0,(SELECT oid FROM pg_catalog.pg_database WHERE datname=pg_catalog.current_database()))),
 'initial',(SELECT count(*) FROM pg_catalog.pg_init_privs p JOIN targets t ON (p.classoid,p.objoid)=(t.classid,t.objid)),
 'dependency_count',(SELECT count(*) FROM dependencies),
 'edges',(SELECT pg_catalog.jsonb_agg(edge ORDER BY edge::pg_catalog.text COLLATE "C") FROM graph),
 'objects',(SELECT pg_catalog.jsonb_agg(entry ORDER BY entry::pg_catalog.text COLLATE "C") FROM bound),
 'physical',(SELECT pg_catalog.jsonb_agg(pg_catalog.jsonb_build_array(classid::pg_catalog.int8,objid::pg_catalog.int8)
   ORDER BY classid,objid) FROM targets)
)
""")


async def _database(admin, name, *, drop=False):
    assert re.fullmatch(r"ctcc_native_obs56_[a-f0-9]{12}", name) is not None
    statement = (
        f'DROP DATABASE "{name}"'
        if drop
        else f'CREATE DATABASE "{name}" TEMPLATE template0'
    )
    async with admin.connect() as connection:
        await connection.execution_options(isolation_level="AUTOCOMMIT")
        await connection.exec_driver_sql(statement)


async def _read(engine):
    async with engine.connect() as connection, connection.begin():
        for command in installed._READBACK_SETTINGS:
            await connection.exec_driver_sql(command)
        identity = tuple((await connection.execute(_IDENTITY)).one())
        assert identity[3:] == (pinned_pg.PG_BUILD, True)
        observation = await unscoped.observe_unscoped_fitted_catalog_0056_v1(
            connection, source=ROOT, owner="ctcc", owner_oid=10
        )
        await connection.exec_driver_sql("SET LOCAL search_path TO pg_catalog, pg_temp")
        dependencies = await connection.scalar(
            _DEPENDENCIES,
            {
                "tables": sorted(fitted.TABLES),
                "routines": sorted(
                    "public." + name for name in fitted.INTERNAL | fitted.APIS.keys()
                ),
            },
        )
        return identity, observation, dependencies


def _publish(value, label, manifest):
    target = Path("/validation-results/unscoped-fitted56-reference.json")
    payload = reference.canonical(value)
    reference.measurement(payload, label=label, manifest=manifest)
    with target.open("xb") as stream:
        assert stream.write(payload) == len(payload)
        stream.flush()
        os.fsync(stream.fileno())
    assert target.read_bytes() == payload


async def test_actual_unscoped0056_bootstrap_owner_reference():
    label = os.environ.get("CTCC_UNSCOPED_FITTED56_REFERENCE")
    assert label in reference.LABELS
    manifest = os.environ["CTCC_DEVVAL_CUSTODY_TEST_SOURCE_MANIFEST_SHA256"]
    bootstrap._require_primary_pinned_owner_v2()
    url, tls = lab._isolated_tls_database_config()
    assert (url.username, url.database, url.port) == ("ctcc", "ctcc", 5432)
    settings = {
        "poolclass": NullPool,
        "connect_args": {"ssl": tls},
        "hide_parameters": True,
    }
    admin = create_async_engine(url, **settings)
    database = "ctcc_native_obs56_" + uuid4().hex[:12]
    engine = create_async_engine(url.set(database=database), **settings)
    created = False
    try:
        if label == "b":
            scratch = "ctcc_native_obs56_" + uuid4().hex[:12]
            assert scratch != database
            await _database(admin, scratch)
            await _database(admin, scratch, drop=True)
        await _database(admin, database)
        created = True
        migrated = await migrate_fresh_lab_owner_0056_v1(
            owner_url=url.set(database=database),
            ca_file=Path(os.environ["CTCC_DEVVAL_CUSTODY_TEST_TLS_CA_FILE"]),
            ca_sha256=os.environ["CTCC_DEVVAL_CUSTODY_TEST_TLS_CA_SHA256"],
            manifest_sha256=manifest,
        )
        assert (
            migrated.database_head == "0056" and migrated.execution_authority is False
        )
        first = await _read(engine)
        assert first == await _read(engine)
        identity, observation, bound = first
        assert observation.execution_authority is False
        assert bound["layout_ok"] is True and bound["shared"] == bound["initial"] == 0
        for field in ("edges", "objects", "physical"):
            assert type(bound[field]) is list and bound[field]
            assert len({reference.canonical(item) for item in bound[field]}) == len(
                bound[field]
            )
        assert bound["dependency_count"] == len(bound["edges"])
        assert len(bound["objects"]) == len(bound["physical"])
        evidence = {
            "schema": reference.SCHEMA,
            "reference": label,
            "database_head": "0056",
            "source_manifest_sha256": manifest,
            "postgres_version_num": "170011",
            "bootstrap_owner_oid": 10,
            "independent_readbacks": 2,
            "fitted_tables": 3,
            "fitted_routines": 20,
            "fitted_shared_dependencies": bound["shared"],
            "fitted_initial_privileges": bound["initial"],
            "fitted_dependency_count": bound["dependency_count"],
            "database_oid": identity[0],
            "marker_relation_oid": identity[1],
            "cluster_system_identifier_sha256": identity[2],
            "structural_sha256": observation.structural_sha256,
            "role_catalog_sha256": observation.fitted_context_role_sha256,
            "logical_dependency_sha256": reference.digest(
                reference.canonical([bound["edges"], bound["objects"]])
            ),
            "physical_allocation_sha256": reference.digest(
                reference.canonical(bound["physical"])
            ),
            "external_role_grants_installed": False,
            "startup_admission": False,
            "execution_authority": False,
        }
    finally:
        await engine.dispose()
        try:
            if created:
                await _database(admin, database, drop=True)
        finally:
            await admin.dispose()
    await asyncio.to_thread(_publish, evidence, label, manifest)
