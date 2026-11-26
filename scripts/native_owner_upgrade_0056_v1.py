"""Explicit owner-only DB0055 to DB0056 migration; no role grants or API start.

The positive path requires reviewed V2 source/archive/route/CA and three exact
restricted-role identities. It rehearses the fixed migration in a savepoint,
verifies the actual rollback, reapplies it once and independently reads back
every retained row/column/object/sequence plus all42 grants. V1 inputs remain
historical DATA helpers only. Once COMMIT is attempted, an uncertain result is
terminal and this command never retries.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
import json
import re
import stat
import tarfile
from contextlib import AsyncExitStack, asynccontextmanager, redirect_stdout
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

import app.database.models  # noqa: F401 -- exact migration metadata registry.
from app.database.base import Base
from app.database.native_pg_tls_v1 import _read_bounded_regular_file_v1
from scripts import current0056_fitted_delta_catalog_v1 as fitted56
from scripts import current_contract_source_scopes_v1 as scopes
from scripts import manifest
from scripts import native_owner_retained_rows_v2 as retained_rows
from scripts import verify_final_hermetic as archive_checks
from scripts.evidence_json import read_evidence_json
from scripts.native_owner_route_preflight_0056_v1 import (
    _PROFILE_SQL,
    OwnerRoutePinsV1,
    preflight_owner_route_0056_v1,
)
from scripts.native_owner_route_preflight_0056_v1 import (
    _prepare as prepare_route,
)

_SOURCE = Path(__file__).resolve().parents[1]
_HEX = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_GIT = re.compile(r"[0-9a-f]{40}\Z", re.ASCII)
_OWNER_LOCK = text("SELECT pg_catalog.pg_advisory_xact_lock(5883109675522810055)")
_LOCK_REVISION = "LOCK TABLE public.alembic_version IN ACCESS EXCLUSIVE MODE NOWAIT"
_OWNER_SETTINGS = (
    "SET TRANSACTION ISOLATION LEVEL READ COMMITTED READ WRITE",
    "SET LOCAL search_path TO pg_catalog, public, pg_temp",
    "SET LOCAL TIME ZONE 'UTC'",
    "SET LOCAL synchronous_commit='on'",
    "SET LOCAL lock_timeout='1000ms'",
    "SET LOCAL statement_timeout='120000ms'",
)
_READBACK_SETTINGS = (
    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY",
    "SET LOCAL search_path TO pg_catalog, public, pg_temp",
    "SET LOCAL TIME ZONE 'UTC'",
    "SET LOCAL lock_timeout='1000ms'",
    "SET LOCAL statement_timeout='10000ms'",
)


class NativeOwnerUpgradeRefusal(ValueError):
    """Nothing committed by this invocation; fixed nonprivate refusal."""


class NativeOwnerUpgradeCommitUnknown(RuntimeError):
    """COMMIT was attempted but its result cannot be trusted; never retry."""


class NativeOwnerUpgradePostcommitIncomplete(RuntimeError):
    """COMMIT returned, but independent readback or cleanup failed."""


def _deny(code: str = "native_owner_0056_upgrade_refused") -> None:
    raise NativeOwnerUpgradeRefusal(code) from None


def _need(condition: bool) -> None:
    if condition is not True:
        _deny()


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True, slots=True, repr=False)
class OwnerUpgradePinsV1:
    source_commit: str
    source_tree: str
    source_archive_sha256: str
    route: OwnerRoutePinsV1


@dataclass(frozen=True, slots=True, repr=False)
class OwnerUpgradePinsV2:
    """Reviewed identity DATA; the live catalog and all three roles must agree."""

    source_commit: str
    source_tree: str
    source_archive_sha256: str
    route: OwnerRoutePinsV1
    role_names: tuple[str, str, str]
    role_oids: tuple[int, int, int]


def _upgrade_pins_v2(pins):
    _need(type(pins) is OwnerUpgradePinsV2 and type(pins.route) is OwnerRoutePinsV1)
    _need(pins.route.database_head == "0055")
    _need(type(pins.role_names) is tuple and len(pins.role_names) == 3)
    _need(
        all(
            type(name) is str
            and re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", name)
            and name != "public"
            and not name.startswith("pg_")
            for name in pins.role_names
        )
    )
    _need(len(set(pins.role_names)) == 3 and pins.route.owner not in pins.role_names)
    _need(type(pins.role_oids) is tuple and len(pins.role_oids) == 3)
    _need(
        all(
            type(oid) is int and 0 < oid < 2**32 and oid != pins.route.owner_oid
            for oid in pins.role_oids
        )
        and len(set(pins.role_oids)) == 3
    )
    return OwnerUpgradePinsV1(
        pins.source_commit, pins.source_tree, pins.source_archive_sha256, pins.route
    )


def _require_positive_private_file_v2(path):
    # Lazy import preserves the historical DATA helper's import direction.
    from scripts.native_owner_target_plan_0056_v1 import _require_private_file

    _require_private_file(path)


def _pin(value: object, expression: re.Pattern[str]) -> bool:
    return type(value) is str and expression.fullmatch(value) is not None


def _archive_manifest(raw: bytes) -> dict[str, str]:
    _need(raw.endswith(b"\n") and b"\r" not in raw)
    rows: dict[str, str] = {}
    for line in raw.decode("utf-8").splitlines():
        digest, delimiter, name = line.partition("  ")
        _need(delimiter == "  " and _pin(digest, _HEX) and name and name not in rows)
        rows[name] = digest
    _need(bool(rows))
    return rows


def _canonical_source_sha(name: str, raw: bytes) -> str:
    path = PurePosixPath(name)
    if (
        path.name in manifest.TEXT_NAMES
        or path.suffix.lower() in manifest.TEXT_SUFFIXES
    ):
        raw = raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return _sha(raw)


def _verify_reviewed_archive(pins: OwnerUpgradePinsV1, path: Path) -> None:
    """Reconstruct the reviewed Git tree and bind every archived source row."""
    _need(type(pins) is OwnerUpgradePinsV1)
    _verify_reviewed_source_archive(
        path,
        source_commit=pins.source_commit,
        source_tree=pins.source_tree,
        source_archive_sha256=pins.source_archive_sha256,
        source_manifest_sha256=pins.route.source_manifest_sha256,
    )


def _verify_reviewed_source_archive(
    path: Path,
    *,
    source_commit: str,
    source_tree: str,
    source_archive_sha256: str,
    source_manifest_sha256: str,
) -> None:
    _need(
        _pin(source_commit, _GIT)
        and _pin(source_tree, _GIT)
        and _pin(source_archive_sha256, _HEX)
        and _pin(source_manifest_sha256, _HEX)
        and type(path) is type(Path())
        and path.is_absolute()
        and ".." not in path.parts
        and not path.is_symlink()
        and not path.is_junction()
    )
    entry = path.lstat()
    _need(stat.S_ISREG(entry.st_mode) and 0 < entry.st_size <= 128 * 1024 * 1024)
    raw = path.read_bytes()
    _need(len(raw) == entry.st_size and _sha(raw) == source_archive_sha256)
    current_manifest = (_SOURCE / "MANIFEST.sha256").read_bytes()
    _need(_sha(current_manifest) == source_manifest_sha256)
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
        _need(
            archive.pax_headers.get("comment") == source_commit
            and archive_checks.archive_tree(archive) == source_tree
        )
        members = {item.name: item for item in archive.getmembers() if item.isfile()}
        _need("MANIFEST.sha256" in members)
        stream = archive.extractfile(members["MANIFEST.sha256"])
        _need(stream is not None and stream.read() == current_manifest)
        expected = _archive_manifest(current_manifest)
        _need(set(members) == set(expected) | {"MANIFEST.sha256"})
        for name, digest in expected.items():
            stream = archive.extractfile(members[name])
            _need(
                stream is not None
                and _canonical_source_sha(name, stream.read()) == digest
            )


def _checked_profile(
    row: tuple, pins: OwnerRoutePinsV1, head: str, *, read_only: bool
) -> None:
    _need(
        type(row) is tuple
        and len(row) == 12
        and row[0] == row[1] == pins.owner
        and row[2] == pins.database
        and row[3] == pins.port
        and row[4] == row[6] == pins.owner_oid
        and row[5] == pins.database_oid
        and row[7] is True
        and row[8] in ("TLSv1.2", "TLSv1.3")
        and row[9] == ("on" if read_only else "off")
        and row[10] == head
        and type(row[11]) is str
        and re.fullmatch(r"[1-9][0-9]{0,19}", row[11], re.ASCII) is not None
        and _sha(row[11].encode("ascii")) == pins.cluster_system_identifier_sha256
    )


def _prepared(
    pins: OwnerUpgradePinsV1, owner_dsn_file: Path, ca_file: Path, archive_file: Path
):
    _need(type(pins) is OwnerUpgradePinsV1 and pins.route.database_head == "0055")
    route = prepare_route(pins.route, owner_dsn_file=owner_dsn_file, ca_file=ca_file)
    _verify_reviewed_archive(pins, archive_file)
    with redirect_stdout(io.StringIO()):
        before = scopes.database0055_source_scope(_SOURCE)
        after = scopes.database0056_source_scope(_SOURCE)
    _need(
        before.actual_source_head == after.actual_source_head == route.source_head
        and route.source_head in {"0056", "0057", "0058"}
        and before.database_target == "0055"
        and after.database_target == "0056"
        and before.manifest_sha256 == after.manifest_sha256 == route.manifest_sha256
        and route.migration_sha256 == after.actual_migration_sha256
    )
    fitted56.pinned_migration(_SOURCE)
    config = Config(str(_SOURCE / "alembic.ini"))
    config.set_main_option("path_separator", "os")
    config.set_main_option("script_location", str(_SOURCE / "migrations"))
    script = ScriptDirectory.from_config(config)
    _need(
        tuple(script.get_heads()) == (route.source_head,)
        and script.get_revision("0056").down_revision == "0055"
    )
    return route, before, after, script


def _checked_preflight(value: dict, route, pins: OwnerRoutePinsV1) -> None:
    _need(
        type(value) is dict
        and value.get("schema") == "ctcc.native_owner_0056.route_preflight.v1"
        and value.get("status") == "PASS"
        and value.get("source_head") == route.source_head
        and value.get("source_manifest_sha256") == route.manifest_sha256
        and value.get("migration_sha256") == route.migration_sha256
        and value.get("database_head") == pins.database_head
        and value.get("route_sha256") == route.route_sha256
        and value.get("cluster_system_identifier_sha256")
        == pins.cluster_system_identifier_sha256
        and value.get("ca_sha256") == pins.ca_sha256
        and value.get("independent_readonly_observations") == 2
        and value.get("database_owner_verified") is True
        and value.get("schema_drift_verified") is False
        and value.get("owner_provision_verified") is False
        and value.get("startup_admission") is False
        and value.get("execution_authority") is False
    )


def _upgrade_exact_0055(sync_connection, before, after, script) -> None:
    with redirect_stdout(io.StringIO()):
        scopes.verify_database_scope(sync_connection, _SOURCE, before)
    migration = MigrationContext.configure(
        sync_connection,
        opts={
            "fn": lambda heads, _: script._upgrade_revs("0056", heads),
            "target_metadata": Base.metadata,
            "compare_type": True,
        },
    )
    _need(migration.get_current_heads() == ("0055",))
    with Operations.context(migration), migration.begin_transaction():
        migration.run_migrations()
    with redirect_stdout(io.StringIO()):
        scopes.verify_database_scope(sync_connection, _SOURCE, after)


_ORIGINAL_OBJECTS = text("""
WITH relations AS (
 SELECT c.* FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
 WHERE n.nspname='public'
), new_data AS (
 SELECT c.oid,c.reltype FROM relations c WHERE c.relname=ANY(CAST(:added_tables AS pg_catalog.text[]))
), objects AS (
 SELECT 'relation' AS kind,c.oid::pg_catalog.int8 AS oid,0 AS member,
 pg_catalog.to_jsonb(c)-ARRAY['relpages','reltuples','relallvisible','relfrozenxid','relminmxid'] AS value
 FROM relations c
 UNION ALL SELECT 'attribute',a.attrelid::pg_catalog.int8,a.attnum,pg_catalog.to_jsonb(a)
 FROM pg_catalog.pg_attribute a JOIN relations c ON c.oid=a.attrelid WHERE a.attnum>0
 UNION ALL SELECT 'default',d.oid::pg_catalog.int8,0,pg_catalog.to_jsonb(d)
 FROM pg_catalog.pg_attrdef d JOIN relations c ON c.oid=d.adrelid
 UNION ALL SELECT 'constraint',k.oid::pg_catalog.int8,0,pg_catalog.to_jsonb(k)
 FROM pg_catalog.pg_constraint k JOIN pg_catalog.pg_namespace n ON n.oid=k.connamespace WHERE n.nspname='public'
 UNION ALL SELECT 'trigger',t.oid::pg_catalog.int8,0,pg_catalog.to_jsonb(t)
 FROM pg_catalog.pg_trigger t JOIN relations c ON c.oid=t.tgrelid
 UNION ALL SELECT 'index',i.indexrelid::pg_catalog.int8,0,pg_catalog.to_jsonb(i)
 FROM pg_catalog.pg_index i JOIN relations c ON c.oid=i.indrelid
 UNION ALL SELECT 'function',p.oid::pg_catalog.int8,0,pg_catalog.to_jsonb(p)
 FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public'
 UNION ALL SELECT 'type',t.oid::pg_catalog.int8,0,pg_catalog.to_jsonb(t)
 FROM pg_catalog.pg_type t JOIN pg_catalog.pg_namespace n ON n.oid=t.typnamespace WHERE n.nspname='public'
 UNION ALL SELECT 'enum',e.oid::pg_catalog.int8,0,pg_catalog.to_jsonb(e)
 FROM pg_catalog.pg_enum e JOIN pg_catalog.pg_type t ON t.oid=e.enumtypid
 JOIN pg_catalog.pg_namespace n ON n.oid=t.typnamespace WHERE n.nspname='public'
 UNION ALL SELECT 'namespace',n.oid::pg_catalog.int8,0,pg_catalog.to_jsonb(n)
 FROM pg_catalog.pg_namespace n WHERE n.nspname='public'
 UNION ALL SELECT 'default_acl',d.oid::pg_catalog.int8,0,pg_catalog.to_jsonb(d)
 FROM pg_catalog.pg_default_acl d
)
SELECT kind,oid,member,pg_catalog.encode(pg_catalog.sha256(
 pg_catalog.convert_to(value::pg_catalog.text,'UTF8')),'hex'),
 CASE kind
 WHEN 'relation' THEN EXISTS(SELECT 1 FROM new_data d WHERE d.oid=objects.oid)
   OR EXISTS(SELECT 1 FROM pg_catalog.pg_index i JOIN new_data d ON d.oid=i.indrelid WHERE i.indexrelid=objects.oid)
 WHEN 'attribute' THEN EXISTS(SELECT 1 FROM new_data d WHERE d.oid=objects.oid)
 WHEN 'default' THEN EXISTS(SELECT 1 FROM pg_catalog.pg_attrdef a JOIN new_data d ON d.oid=a.adrelid WHERE a.oid=objects.oid)
 WHEN 'constraint' THEN EXISTS(SELECT 1 FROM pg_catalog.pg_constraint k JOIN new_data d ON d.oid=k.conrelid WHERE k.oid=objects.oid)
 WHEN 'trigger' THEN EXISTS(SELECT 1 FROM pg_catalog.pg_trigger t WHERE t.oid=objects.oid AND (
   t.tgrelid IN (SELECT d.oid FROM new_data d)
   OR (t.tgisinternal AND EXISTS(SELECT 1 FROM pg_catalog.pg_constraint k JOIN new_data d ON d.oid=k.conrelid WHERE k.oid=t.tgconstraint))
   OR (NOT t.tgisinternal AND t.tgname='gate3_fitted_candidate_claim_guard'
     AND t.tgrelid=pg_catalog.to_regclass('public.gate3_capture_schedule_key_claims'))))
 WHEN 'index' THEN EXISTS(SELECT 1 FROM pg_catalog.pg_index i JOIN new_data d ON d.oid=i.indrelid WHERE i.indexrelid=objects.oid)
 WHEN 'function' THEN EXISTS(SELECT 1 FROM pg_catalog.pg_proc p WHERE p.oid=objects.oid
   AND p.proname::pg_catalog.text=ANY(CAST(:added_functions AS pg_catalog.text[])))
 WHEN 'type' THEN EXISTS(SELECT 1 FROM pg_catalog.pg_type t WHERE t.oid=objects.oid
   AND (t.typrelid IN (SELECT d.oid FROM new_data d) OR t.typelem IN (SELECT d.reltype FROM new_data d)))
 ELSE FALSE END AS source56_delta
 FROM objects ORDER BY kind,oid,member
""")
_ADDED_TABLES = frozenset(
    {
        "gate3_fitted_candidate_bindings",
        "gate3_fitted_candidate_acks",
        "gate3_fitted_candidate_evaluations",
    }
)
_STORAGE_SCOPE_V2 = text("""
SELECT NOT EXISTS(SELECT 1 FROM pg_catalog.pg_namespace
 WHERE nspname !~ '^pg_' AND nspname NOT IN ('public','information_schema'))
 AND NOT EXISTS(SELECT 1 FROM pg_catalog.pg_largeobject_metadata)
 AND NOT EXISTS(SELECT 1 FROM pg_catalog.pg_foreign_table)
""")
_SAFE_MARKER_V2 = text(
    """
SELECT current_user::pg_catalog.text=:owner AND session_user::pg_catalog.text=:owner
 AND (SELECT d.oid=:database_oid AND d.datdba=:owner_oid FROM pg_catalog.pg_database d
      WHERE d.datname=pg_catalog.current_database())
 AND EXISTS(SELECT 1 FROM pg_catalog.pg_stat_ssl WHERE pid=pg_catalog.pg_backend_pid() AND ssl)
 AND EXISTS(SELECT 1 FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
  WHERE n.nspname='public' AND c.relname='alembic_version' AND c.relkind='r'
  AND c.relpersistence='p' AND c.relowner=:owner_oid AND NOT c.relrowsecurity
  AND NOT c.relforcerowsecurity AND NOT c.relispartition AND NOT c.relhasrules
  AND NOT EXISTS(SELECT 1 FROM pg_catalog.pg_inherits i WHERE i.inhrelid=c.oid OR i.inhparent=c.oid)
  AND (SELECT pg_catalog.count(*) FROM pg_catalog.pg_attribute a WHERE a.attrelid=c.oid AND a.attnum>0)=1
  AND EXISTS(SELECT 1 FROM pg_catalog.pg_attribute a WHERE a.attrelid=c.oid AND a.attnum=1
   AND a.attname='version_num' AND a.atttypid=1043 AND a.atttypmod=36
   AND a.attnotnull AND NOT a.attisdropped AND NOT a.atthasdef
   AND a.attgenerated='' AND a.attidentity=''))
"""
    + " AND "
    + retained_rows.heap_am.ALL_PUBLIC_TABLES_HEAP_V1
)


async def _marker_safe_v2(connection, pins):
    _need(
        await connection.scalar(
            _SAFE_MARKER_V2,
            {
                "owner": pins.route.owner,
                "owner_oid": pins.route.owner_oid,
                "database_oid": pins.route.database_oid,
            },
        )
        is True
    )


@asynccontextmanager
async def _marker_checked_v2(owner, pins):
    # Prove metadata before opening an application relation, then hold all
    # native public tables against AM/DDL substitution during separate role
    # sessions. Ordinary DML remains possible and later snapshot equality is
    # still required under the existing exclusive upgrade locks.
    async with owner.connect() as connection:
        transaction = await connection.begin()
        try:
            for sql in _READBACK_SETTINGS:
                await connection.exec_driver_sql(sql)
            await _marker_safe_v2(connection, pins)
            names = tuple(
                (
                    await connection.execute(
                        text(
                            "SELECT c.relname::pg_catalog.text FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind='r' ORDER BY c.relname"
                        )
                    )
                )
                .scalars()
                .all()
            )
            _need(
                0 < len(names) <= 512
                and "alembic_version" in names
                and all(
                    type(name) is str
                    and re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", name) is not None
                    for name in names
                )
            )
            await connection.exec_driver_sql(
                "LOCK TABLE "
                + ",".join('public."' + name + '"' for name in names)
                + " IN ACCESS SHARE MODE NOWAIT"
            )
            await _marker_safe_v2(connection, pins)
            yield connection
        finally:
            await transaction.rollback()


async def _catalog_v2(connection, pins, head, *, read_only):
    """Actual source-defined catalog admission precedes all application-row reads."""
    from app.core.native_role_preflight_v1 import _exact_existing_acl
    from app.domain import collector_current_0055_catalog_v1 as current55
    from app.domain import collector_current_0056_inherited_catalog_v1 as current56
    from scripts import current0055_runtime_grants as core

    _need(head in {"0055", "0056"})
    _need(await connection.scalar(_STORAGE_SCOPE_V2) is True)
    query = (
        current55.OWNER_CATALOG_SQL if head == "0055" else current56.OWNER_CATALOG_SQL
    )
    document = await connection.scalar(text(query))
    repeated = await connection.scalar(text(query))
    _need(document == repeated)
    if head == "0055":
        observed = (
            current55._observe_owner_repeatable_read_catalog_v1(document)
            if read_only
            else current55._observed(document, owner_mode=True, read_only="off")
        )
        _need(observed["native_definitions_sha256"] == current55._required_baseline())
    else:
        observed = (
            current56._observe_owner_repeatable_read_catalog_v1(document)
            if read_only
            else current56.observe_owner_readwrite_installation_0056(document)
        )
        fitted = await connection.scalar(
            text(
                fitted56.CATALOG_SQL
                if read_only
                else fitted56.OWNER_READWRITE_CATALOG_SQL
            )
        )
        check = (
            fitted56.inspect_catalog_document
            if read_only
            else fitted56.inspect_owner_readwrite_catalog
        )
        arguments = {"owner": pins.route.owner, "demo_roles": pins.role_names}
        if read_only:
            arguments["fitted_roles"] = None
        _need(_pin(check(fitted, **arguments), _HEX))
    _need(
        observed["database_head"] == head
        and observed["role_oid"] == pins.route.owner_oid
        and observed["execution_authority"] is False
    )
    actual = await core._roles(connection, pins.role_names, pins.route.owner_oid)
    _need(tuple(actual[name] for name in pins.role_names) == pins.role_oids)
    await _exact_existing_acl(connection, pins.role_names, pins.role_oids[0])


async def _inventory_v2(connection, pins, head, *, read_only):
    # Lazy reuse avoids a module-load cycle: canonical57 deliberately retains
    # the original56 archive/profile helpers for historical DATA callers.
    from scripts import native_owner_upgrade_0057_v1 as shared
    from scripts.native_owner_catalog_snapshot_0058_v1 import LOGICAL_CATALOG

    await _marker_safe_v2(connection, pins)
    _checked_profile(
        tuple((await connection.execute(_PROFILE_SQL)).one()),
        pins.route,
        head,
        read_only=read_only,
    )
    await _catalog_v2(connection, pins, head, read_only=read_only)
    relations = await shared._relations(connection, lock=False)
    await shared._local(connection)
    snapshot = await shared._snapshot(connection, relations, pins)
    objects = tuple(
        tuple(row)
        for row in (
            await connection.execute(
                _ORIGINAL_OBJECTS,
                {
                    "added_tables": sorted(_ADDED_TABLES),
                    "added_functions": sorted(
                        {
                            signature.partition("(")[0]
                            for signature in fitted56.INTERNAL | fitted56.APIS.keys()
                        }
                    ),
                },
            )
        ).all()
    )
    _need(
        0 < len(objects) <= 100_000
        and len({row[:3] for row in objects}) == len(objects)
    )
    acl = tuple(tuple(row) for row in (await connection.execute(shared._ACL)).all())
    size, logical = (await connection.execute(LOGICAL_CATALOG)).one()
    _need(type(size) is int and 0 < size <= 32 * 1024 * 1024 and _pin(logical, _HEX))
    return {
        "relations": relations,
        "snapshot": snapshot,
        "objects": objects,
        "acl": acl,
        "logical_sha256": logical,
    }


async def _readonly_inventory_v2(owner, pins, head):
    async with _marker_checked_v2(owner, pins) as connection:
        return await _inventory_v2(connection, pins, head, read_only=True)


def _preserved_v2(before, after):
    _need(
        before["snapshot"].get("row_encoding")
        == after["snapshot"].get("row_encoding")
        == retained_rows.ROW_ENCODING
    )
    for field in ("rows", "row_layouts"):
        old = {row[0]: row for row in before["snapshot"][field]}
        new = {row[0]: row for row in after["snapshot"][field]}
        _need(
            len(old) == len(before["snapshot"][field])
            and len(new) == len(after["snapshot"][field])
        )
        _need(
            set(new) - set(old) == _ADDED_TABLES
            and all(new.get(name) == row for name, row in old.items())
        )
        if field == "rows":
            _need(
                all(tuple(new[name]) == (name, 0, _sha(b"")) for name in _ADDED_TABLES)
            )
    old_rel = {row[0]: row for row in before["relations"]}
    new_rel = {row[0]: row for row in after["relations"]}
    _need(
        len(old_rel) == len(before["relations"])
        and len(new_rel) == len(after["relations"])
    )
    _need(
        set(new_rel) - set(old_rel) == _ADDED_TABLES
        and all(new_rel.get(name) == row for name, row in old_rel.items())
    )
    _need(
        before["snapshot"]["sequence_state"] == after["snapshot"]["sequence_state"]
        and before["snapshot"]["role_state_sha256"]
        == after["snapshot"]["role_state_sha256"]
    )
    old_objects = {row[:3]: row[3] for row in before["objects"]}
    new_objects = {row[:3]: row[3] for row in after["objects"]}
    _need(
        len(old_objects) == len(before["objects"])
        and len(new_objects) == len(after["objects"])
    )
    _need(all(new_objects.get(key) == digest for key, digest in old_objects.items()))
    _need(
        all(
            len(row) == 5 and type(row[4]) is bool
            for row in (*before["objects"], *after["objects"])
        )
    )
    _need(all(row[4] is True for row in after["objects"] if row[:3] not in old_objects))
    # New owner-only fitted objects add owner ACL rows. Every original ACL
    # row, including all42 restricted grants, remains exact; new object ACLs
    # are additionally checked by the complete source-defined fitted catalog.
    old_acl = set(before["acl"])
    new_acl = set(after["acl"])
    _need(
        len(old_acl) == len(before["acl"])
        and len(new_acl) == len(after["acl"])
        and old_acl <= new_acl
    )
    old_targets = {(row[0], row[1]) for row in before["acl"]}
    _need({row for row in new_acl if (row[0], row[1]) in old_targets} == old_acl)


async def _configured_resources_v2(stack, prepared, pins):
    from scripts import native_owner_upgrade_0057_v1 as shared

    return await shared._configured_resources(stack, prepared[0], pins)


async def _role_observation_v2(selected, owner, pins, head):
    async with _marker_checked_v2(owner, pins):
        return await _role_observation_body_v2(selected, owner, pins, head)


async def _role_observation_body_v2(selected, owner, pins, head):
    from app.core import native_role_preflight_v1 as roles

    if head == "0055":
        observed = await roles.verify_selected_native_roles_v1(selected)
        _need(
            observed["database_head"] == "0055"
            and observed["execution_authority"] is False
        )
    else:
        from scripts import native_owner_upgrade_0057_v1 as shared

        observed, _ = await shared._role_observation(selected, owner, pins, "0056")
        _need(
            observed.role_names == pins.role_names
            and observed.role_oids == pins.role_oids
        )
    actors = []
    for factory, name in zip(
        (
            selected.writer_factory,
            selected.custody_factory,
            selected.frame_reader_factory,
        ),
        pins.role_names,
        strict=True,
    ):
        async with factory() as session, session.begin():
            await roles._session_settings(session, frame=False)
            actors.append(await roles._identity(session, name))
    async with selected.writer_factory() as session, session.begin():
        await roles._session_settings(session, frame=False)
        actors.append(await roles._identity(session, pins.role_names[0]))
    roles._same_server(*actors)
    _need(
        tuple(row[3] for row in actors[:3]) == pins.role_oids
        and actors[0][4] == pins.route.database_oid
    )
    async with owner.connect() as connection, connection.begin():
        for setting in _READBACK_SETTINGS:
            await connection.exec_driver_sql(setting)
        _checked_profile(
            tuple((await connection.execute(_PROFILE_SQL)).one()),
            pins.route,
            head,
            read_only=True,
        )
        own = await roles._identity(connection, pins.route.owner)
    _need(own[3] == pins.route.owner_oid and own[4:] == actors[0][4:])
    return tuple(actors), own


async def _commit_once(prepared, pins: OwnerUpgradePinsV2, engine, expected):
    """A single owner transaction; the commit-attempt bit is monotone."""
    _route, before, after, script = prepared
    attempted = False
    committed = False
    failure: BaseException | None = None
    try:
        async with asyncio.timeout(180):
            _upgrade_pins_v2(pins)
            async with engine.connect() as connection:
                transaction = await connection.begin()
                try:
                    for setting in _OWNER_SETTINGS:
                        await connection.exec_driver_sql(setting)
                    await _marker_safe_v2(connection, pins)
                    await connection.execute(_OWNER_LOCK)
                    await connection.exec_driver_sql(_LOCK_REVISION)
                    await _marker_safe_v2(connection, pins)
                    from scripts import native_owner_upgrade_0057_v1 as shared

                    await shared._relations(connection, lock=True)
                    before_state = await _inventory_v2(
                        connection, pins, "0055", read_only=False
                    )
                    _need(before_state == expected)
                    rehearsal = await connection.begin_nested()
                    await connection.run_sync(
                        _upgrade_exact_0055, before, after, script
                    )
                    preview = await _inventory_v2(
                        connection, pins, "0056", read_only=False
                    )
                    _preserved_v2(before_state, preview)
                    await rehearsal.rollback()
                    _need(
                        await _inventory_v2(connection, pins, "0055", read_only=False)
                        == before_state
                    )
                    await connection.run_sync(
                        _upgrade_exact_0055, before, after, script
                    )
                    final = await _inventory_v2(
                        connection, pins, "0056", read_only=False
                    )
                    _preserved_v2(before_state, final)
                    _need(final["logical_sha256"] == preview["logical_sha256"])
                    attempted = True
                    await transaction.commit()
                    committed = True
                    return final
                except BaseException:
                    if not attempted:
                        try:
                            await transaction.rollback()
                        except BaseException:  # noqa: BLE001 -- fixed denial if rollback fails.
                            _deny()
                    raise
    except BaseException as error:  # noqa: BLE001 -- classify cancellation at COMMIT.
        failure = error
    if failure is not None:
        if committed:
            raise NativeOwnerUpgradePostcommitIncomplete(
                "native_owner_0056_committed_readback_required"
            ) from None
        if attempted:
            raise NativeOwnerUpgradeCommitUnknown(
                "native_owner_0056_commit_outcome_unknown"
            ) from None
        _deny()


async def _independent_readback(prepared, pins, owner_dsn_file, ca_file, archive_file):
    route, _, expected, _ = prepared
    fresh = replace(pins.route, database_head="0056")
    async with asyncio.timeout(60):
        observed = await preflight_owner_route_0056_v1(
            fresh, owner_dsn_file=owner_dsn_file, ca_file=ca_file
        )
    _checked_preflight(observed, route, fresh)
    reread = _prepared(_upgrade_pins_v2(pins), owner_dsn_file, ca_file, archive_file)
    _need(
        reread[0].url == route.url
        and reread[0].manifest_sha256 == route.manifest_sha256
        and reread[0].migration_sha256 == route.migration_sha256
    )
    engine = create_async_engine(
        route.url,
        poolclass=NullPool,
        connect_args={"ssl": reread[0].tls, "timeout": 10},
    )
    try:
        async with asyncio.timeout(45):
            async with engine.connect() as connection:
                transaction = await connection.begin()
                try:
                    for setting in _READBACK_SETTINGS:
                        await connection.exec_driver_sql(setting)
                    await retained_rows.heap_am.lock_native_owner_marker_v1(connection)
                    row = tuple((await connection.execute(_PROFILE_SQL)).one())
                    _checked_profile(row, fresh, "0056", read_only=True)
                    with redirect_stdout(io.StringIO()):
                        scope = await connection.run_sync(
                            scopes.verify_database_scope, _SOURCE, expected
                        )
                    _need(
                        scope["database_head"] == "0056"
                        and scope["manifest_sha256"] == route.manifest_sha256
                        and scope["execution_authority"] is False
                    )
                finally:
                    await transaction.rollback()
    finally:
        await engine.dispose()
    return observed


async def upgrade_owner_0055_to_0056_v1(
    pins: OwnerUpgradePinsV2,
    *,
    owner_dsn_file: Path,
    ca_file: Path,
    source_archive_file: Path,
) -> dict[str, object]:
    """One explicit migration, then independent post-commit physical readback."""
    committed = False
    try:
        legacy_data = _upgrade_pins_v2(pins)
        _require_positive_private_file_v2(owner_dsn_file)
        prepared = _prepared(legacy_data, owner_dsn_file, ca_file, source_archive_file)
        fresh_prepared = _prepared(
            legacy_data, owner_dsn_file, ca_file, source_archive_file
        )
        _need(
            fresh_prepared[0].url == prepared[0].url
            and fresh_prepared[0].manifest_sha256 == prepared[0].manifest_sha256
            and fresh_prepared[0].migration_sha256 == prepared[0].migration_sha256
        )
        async with AsyncExitStack() as stack:
            owner, selected = await _configured_resources_v2(
                stack, fresh_prepared, pins
            )
            async with _marker_checked_v2(owner, pins):
                async with asyncio.timeout(60):
                    prior = await preflight_owner_route_0056_v1(
                        pins.route, owner_dsn_file=owner_dsn_file, ca_file=ca_file
                    )
                _checked_preflight(prior, prepared[0], pins.route)
                before_roles = await _role_observation_v2(selected, owner, pins, "0055")
                expected = await _readonly_inventory_v2(owner, pins, "0055")
                _need(
                    await _role_observation_v2(selected, owner, pins, "0055")
                    == before_roles
                )
            final = await _commit_once(fresh_prepared, pins, owner, expected)
            committed = True
            async with _marker_checked_v2(owner, pins):
                observed = await _independent_readback(
                    fresh_prepared, pins, owner_dsn_file, ca_file, source_archive_file
                )
                _need(await _readonly_inventory_v2(owner, pins, "0056") == final)
                _need(
                    await _role_observation_v2(selected, owner, pins, "0056")
                    == before_roles
                )
                _need(await _readonly_inventory_v2(owner, pins, "0056") == final)
    except (NativeOwnerUpgradeCommitUnknown, NativeOwnerUpgradePostcommitIncomplete):
        raise
    except BaseException:  # noqa: BLE001 -- no private precommit detail escapes.
        if committed:
            raise NativeOwnerUpgradePostcommitIncomplete(
                "native_owner_0056_committed_readback_required"
            ) from None
        _deny()
    receipt = {
        "schema": "ctcc.native_owner_0056.migration_commit_readback.v2",
        "row_encoding": retained_rows.ROW_ENCODING,
        "status": "PASS",
        "source_commit": pins.source_commit,
        "source_tree": pins.source_tree,
        "source_archive_sha256": pins.source_archive_sha256,
        "source_manifest_sha256": prepared[0].manifest_sha256,
        "migration_sha256": prepared[0].migration_sha256,
        "route_sha256": prepared[0].route_sha256,
        "cluster_system_identifier_sha256": pins.route.cluster_system_identifier_sha256,
        "database_head_before": "0055",
        "database_head_after": observed["database_head"],
        "independent_postcommit_readonly_observations": 3,
        "schema_drift_verified": False,
        "native_profile_catalog_verified": True,
        "all_original_metadata_preserved": True,
        "all_original_rows_layouts_oids_sequences_preserved": True,
        "independent_role_logins_verified": 3,
        "unchanged_demo_grants": 42,
        "rehearsal_rollback_verified": True,
        "preserved_rows_sha256": _sha(
            json.dumps(final["snapshot"], sort_keys=True, separators=(",", ":")).encode(
                "ascii"
            )
        ),
        "grants_installed": False,
        "startup_admission": False,
        "execution_authority": False,
    }
    if prepared[0].source_head in {"0057", "0058"}:
        receipt.update(
            source_head=prepared[0].source_head,
            database_target="0056",
            database_migration_prefix_sha256=prepared[2].historical_prefix_sha256,
        )
    return receipt


class _PrivateParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        _deny()


def _load_pins(path: Path, expected_sha256: str) -> OwnerUpgradePinsV1:
    _need(_pin(expected_sha256, _HEX))
    raw = _read_bounded_regular_file_v1(path, 65536)
    _need(_sha(raw) == expected_sha256)
    value = read_evidence_json(raw)
    _need(
        type(value) is dict
        and set(value)
        == {"schema", "source_commit", "source_tree", "source_archive_sha256", "route"}
        and value["schema"] == "ctcc.native_owner_0056.reviewed_upgrade_inputs.v1"
        and type(value["route"]) is dict
        and set(value["route"]) == set(OwnerRoutePinsV1.__dataclass_fields__)
    )
    return OwnerUpgradePinsV1(
        value["source_commit"],
        value["source_tree"],
        value["source_archive_sha256"],
        OwnerRoutePinsV1(**value["route"]),
    )


def _load_upgrade_pins_v2(path: Path, expected_sha256: str) -> OwnerUpgradePinsV2:
    _need(_pin(expected_sha256, _HEX))
    _require_positive_private_file_v2(path)
    raw = _read_bounded_regular_file_v1(path, 65536)
    _need(_sha(raw) == expected_sha256)
    value = read_evidence_json(raw)
    _need(
        type(value) is dict
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
        and value["schema"] == "ctcc.native_owner_0056.reviewed_upgrade_inputs.v2"
        and type(value["route"]) is dict
        and set(value["route"]) == set(OwnerRoutePinsV1.__dataclass_fields__)
        and type(value["role_names"]) is list
        and type(value["role_oids"]) is list
    )
    pins = OwnerUpgradePinsV2(
        value["source_commit"],
        value["source_tree"],
        value["source_archive_sha256"],
        OwnerRoutePinsV1(**value["route"]),
        tuple(value["role_names"]),
        tuple(value["role_oids"]),
    )
    _upgrade_pins_v2(pins)
    return pins


def main(argv: list[str] | None = None) -> int:
    parser = _PrivateParser(add_help=False)
    parser.add_argument(
        "--upgrade-reviewed-native-owner-0055-to-0056", action="store_true"
    )
    parser.add_argument("--reviewed-pins-file", type=Path)
    parser.add_argument("--reviewed-pins-sha256")
    parser.add_argument("--owner-dsn-file", type=Path)
    parser.add_argument("--ca-file", type=Path)
    parser.add_argument("--source-archive-file", type=Path)
    try:
        args = parser.parse_args(argv)
        _need(args.upgrade_reviewed_native_owner_0055_to_0056 is True)
        pins = _load_upgrade_pins_v2(args.reviewed_pins_file, args.reviewed_pins_sha256)
        receipt = asyncio.run(
            upgrade_owner_0055_to_0056_v1(
                pins,
                owner_dsn_file=args.owner_dsn_file,
                ca_file=args.ca_file,
                source_archive_file=args.source_archive_file,
            )
        )
    except NativeOwnerUpgradeCommitUnknown:
        print(
            '{"status":"INCOMPLETE","code":"native_owner_0056_commit_outcome_unknown"}'
        )
        return 3
    except NativeOwnerUpgradePostcommitIncomplete:
        print(
            '{"status":"INCOMPLETE","code":"native_owner_0056_committed_readback_required"}'
        )
        return 4
    except BaseException:  # noqa: BLE001 -- never reveal route, SQL or private files.
        print('{"status":"REFUSED","code":"native_owner_0056_upgrade_refused"}')
        return 1
    print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
