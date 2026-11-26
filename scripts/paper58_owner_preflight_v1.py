"""Fresh-unpinned source58 pre-provision catalog proof, without startup authority.

This purpose is deliberately separate from retained/bootstrap continuation.
It cannot approve an initdb-owner catalog, provision a role, alter an ACL,
recover Paper state, or start an application. The future owned Paper context
must combine an actual held owner observation with its separately measured
restricted actor and exact post-provision ACL overlay.
"""

from __future__ import annotations

import hashlib
import re

from scripts import native_owner_heap_am_v1 as storage
from scripts import paper58_catalog_normalization_v1 as normalizer
from scripts import paper58_catalog_reference_v1 as reference
from scripts import paper58_source_shape_v1 as shape

SCHEMA = "ctcc.paper58.fresh_unpinned_preprovision_catalog_data.v1"
PURPOSE = "fresh_canonical58_unpinned_owner_before_paper_acl_provisioning"
CATALOG_SHA256 = "0c0336e396efe2b91c2d725f8e936673ef44aa081dd330b3a62c1f1369b8f8ab"
SOURCE_SCOPE_SHA256 = "6b8ba75b430afa523faba02bc27a7df518d1d881bd3009cfc327f98567e0b9c1"
OWNER_FLAGS = (True, False, False, False, True, False, False)
ORIGIN = {
    "schema": "ctcc.paper58.official_fresh_preprovision_origin.v1",
    "run_id": 44890988317,
    "artifact_id": 14701788130,
    "source_commit": "72d99c9b4d82e13b5ccd1269a37dbe6a6dc1fbde",
    "source_tree": "7a38c3ba7061d4608459c106cb42eba59f16e9ef",
    "official_zip_sha256": "b9e52e21f43df382448528163744defc26d56d9e2e9885d7fd7dc9979a32ff7f",
    "independent_readback_sha256": "a76d6b62481813089727c4de334cfe11aa1c5f3296642e18f036830aea4c6b74",
    "normalized_catalog_sha256": CATALOG_SHA256,
    "source58_scope_sha256": SOURCE_SCOPE_SHA256,
    "two_actual_reference_cases_passed": True,
    "original_archived_verifier_passed": True,
    "schema_admission": False,
    "paper_actor_admission": False,
    "startup_admission": False,
    "execution_authority": False,
}
_NAME = re.compile(r"[a-z_][a-z0-9_]{0,62}\Z", re.ASCII)


class Paper58OwnerPreflightRefused(ValueError):
    """Fixed refusals contain no connection information or catalog values."""


def _need(value):
    if value is not True:
        raise Paper58OwnerPreflightRefused("paper58_owner_preflight_refused") from None


def require_fresh_unpinned_preprovision_catalog_v1(document):
    """Replay immutable catalog DATA; its result cannot admit a runtime.

    The physical owner check precedes normalization. In particular, owner10
    with PostgreSQL's pinned-owner dependency omission is not repaired or
    accepted through the unpinned reference literal.
    """
    normalizer._plain(document)
    _require_fresh_unpinned_owner_profile(document)
    normalized = normalizer.normalize_catalog58_v1(document)
    _need(normalized["source58_scope_sha256"] == SOURCE_SCOPE_SHA256)
    _need(shape.source_shape_v1()["source_scope_sha256"] == SOURCE_SCOPE_SHA256)
    actual = reference.digest(reference.canonical(normalized))
    _need(actual == CATALOG_SHA256)
    return {
        "schema": SCHEMA,
        "purpose": PURPOSE,
        "database_head": "0058",
        "normalized_catalog_sha256": actual,
        "source58_scope_sha256": SOURCE_SCOPE_SHA256,
        "preprovision_catalog_matches": True,
        "bootstrap_owner_continuation_accepted": False,
        "postprovision_acl_overlay_verified": False,
        "schema_admission": False,
        "paper_actor_admission": False,
        "startup_admission": False,
        "execution_authority": False,
    }


def _require_fresh_unpinned_owner_profile(document):
    _need(type(document) is dict and document.get("schema") == reference.RAW_SCHEMA)
    _need(document.get("head") == ["0058"])
    actor = document.get("actor_physical")
    profile = document.get("profile")
    _need(type(actor) is dict and type(profile) is dict)
    owner = actor.get("owner")
    _need(type(owner) is list and len(owner) == 9)
    _need(type(owner[0]) is int and 16384 <= owner[0] < 2**32)
    _need(type(owner[1]) is str and _NAME.fullmatch(owner[1]) is not None)
    _need(all(type(flag) is bool for flag in owner[2:]))
    _need(tuple(owner[2:]) == OWNER_FLAGS)
    _need(profile.get("owner") == profile.get("session_owner") == owner[1])
    _need(
        type(actor.get("database_oid")) is int
        and 16384 <= actor["database_oid"] < 2**32
    )
    _need(type(actor.get("membership_count")) is int and actor["membership_count"] == 0)
    _need(profile.get("server_version") == "170011")


async def capture_fresh_unpinned_preprovision_owner_v1(connection):
    """Capture actual READ ONLY owner DATA under the existing native fences.

    The owner holds catalog/table locks for the caller's transaction. No
    Paper actor is granted or lent those privileges. This new-purpose
    collector does not call the fresh-fixture exporter or its actor API.
    """
    document, measurement = await _capture_fresh_unpinned_owner_catalog(connection)
    matched = require_fresh_unpinned_preprovision_catalog_v1(document)
    return document, matched | measurement


async def _capture_fresh_unpinned_owner_catalog(connection):
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncConnection

    _need(
        type(connection) is AsyncConnection
        and connection.in_transaction()
        and connection.dialect.name == "postgresql"
        and connection.dialect.driver == "asyncpg"
    )
    admission = """SELECT current_user=session_user
 AND pg_catalog.current_setting('role')='none'
 AND pg_catalog.current_setting('transaction_read_only')='on'
 AND pg_catalog.current_setting('search_path')='pg_catalog'
 AND pg_catalog.current_setting('TimeZone')='UTC'
 AND pg_catalog.current_setting('server_version_num')='170011'
 AND pg_catalog.current_setting('session_replication_role')='origin'
 AND pg_catalog.current_setting('session_preload_libraries')=''
 AND pg_catalog.current_setting('local_preload_libraries')=''
 AND pg_catalog.current_setting('shared_preload_libraries')=''
 AND pg_catalog.version() ~ ' on x86_64[^,]*,'
 AND EXISTS(SELECT 1 FROM pg_catalog.pg_stat_ssl
  WHERE pid=pg_catalog.pg_backend_pid() AND ssl)
 AND EXISTS(SELECT 1 FROM pg_catalog.pg_database d
 JOIN pg_catalog.pg_roles r ON r.oid=d.datdba
 WHERE d.datname=pg_catalog.current_database() AND r.rolname=current_user
 AND d.oid>=16384 AND r.oid>=16384
 AND r.rolsuper AND r.rolcanlogin AND NOT r.rolinherit
 AND NOT r.rolcreatedb AND NOT r.rolcreaterole
 AND NOT r.rolreplication AND NOT r.rolbypassrls)
 AND NOT EXISTS(SELECT 1 FROM pg_catalog.pg_auth_members m
 JOIN pg_catalog.pg_roles r ON r.rolname=current_user
 WHERE m.member=r.oid OR m.roleid=r.oid OR m.grantor=r.oid)
 AND NOT EXISTS(SELECT 1 FROM pg_catalog.pg_db_role_setting s
 JOIN pg_catalog.pg_database d ON d.datname=pg_catalog.current_database()
 JOIN pg_catalog.pg_roles r ON r.rolname=current_user
 WHERE (s.setdatabase=0 AND s.setrole=0) OR s.setdatabase=d.oid OR s.setrole=r.oid)
"""
    _need(await connection.scalar(text(admission)) is True)
    await storage.lock_native_owner_marker_v1(connection)
    predicates = reference.raw_safety_queries_v1()
    for predicate in predicates:
        _need(await connection.scalar(text(predicate)) is True)
    names = tuple(
        (
            await connection.execute(
                text(
                    "SELECT c.relname::pg_catalog.text FROM pg_catalog.pg_class c "
                    "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
                    "WHERE n.nspname='public' AND c.relkind='r' "
                    'ORDER BY c.relname COLLATE pg_catalog."C"'
                )
            )
        ).scalars()
    )
    _need(names == reference.TABLES and len(names) == 105)
    _need(
        all(type(name) is str and _NAME.fullmatch(name) is not None for name in names)
    )
    await connection.exec_driver_sql(
        "LOCK TABLE "
        + ",".join('public."' + name + '"' for name in names)
        + " IN ACCESS SHARE MODE NOWAIT"
    )
    await storage.require_native_owner_storage_v1(connection)
    for predicate in predicates:
        _need(await connection.scalar(text(predicate)) is True)
    query = reference.catalog_query_v1()
    document = await connection.scalar(text(query))
    _need(type(document) is dict and document == await connection.scalar(text(query)))
    return document, {
        "actual_owner_transaction_read_only": True,
        "native_storage_before_and_after_locks": True,
        "raw_safety_before_and_after_locks": True,
        "repeated_catalog_exactly_equal": True,
        "typed_catalog_query_sha256": hashlib.sha256(query.encode()).hexdigest(),
        "owner_connection_lent_to_paper_actor": False,
        "acl_changes_performed": False,
        "migrations_performed": False,
    }
