"""Closed physical database contracts; choosing a profile never grants authority."""

from __future__ import annotations

import hashlib
import json
from enum import Enum
from pathlib import Path


class NativeDatabaseProfileV1(Enum):
    DB0056 = "0056"
    DB0057 = "0057"


DB0056 = NativeDatabaseProfileV1.DB0056
DB0057 = NativeDatabaseProfileV1.DB0057


def checked(profile: NativeDatabaseProfileV1) -> NativeDatabaseProfileV1:
    if type(profile) is not NativeDatabaseProfileV1:
        raise ValueError("native_database_profile_unsupported") from None
    return profile


def head(profile: NativeDatabaseProfileV1) -> str:
    return checked(profile).value


def schema(profile: NativeDatabaseProfileV1, previous: str) -> str:
    """Version a source-defined wire label, never an observed database record."""
    checked(profile)
    if type(previous) is not str or previous.count("0056") != 1:
        raise ValueError("native_database_profile_schema_changed") from None
    return previous.replace("0056", head(profile), 1)


def arguments(profile: NativeDatabaseProfileV1) -> dict:
    """Keep legacy default call shape; explicitly carry the successor choice."""
    return {} if checked(profile) is DB0056 else {"profile": profile}


def catalog_query(profile, sql, previous_schema):
    """Change only the query's wire label; all physical head reads stay real."""
    checked(profile)
    old = "'schema','" + previous_schema + "'"
    if type(sql) is not str or sql.count(old) != 1:
        raise ValueError("native_database_profile_query_changed") from None
    return sql.replace(old, "'schema','" + schema(profile, previous_schema) + "'", 1)


def grant_plan(profile, names):
    checked(profile)
    from scripts import current0055_native_lifecycle_acl_v1 as previous

    inherited, lifecycle = previous._plans(*names)
    plan = inherited + lifecycle
    if len(plan) != 42:
        raise ValueError("native_database_profile_acl_changed") from None
    if profile is DB0057:
        from scripts import current0057_native_g12_acl_v1 as successor

        checked_previous, delta = successor._plans(*names)
        if checked_previous != plan or len(delta) != 2:
            raise ValueError("native_database_profile_acl_changed") from None
        plan += delta
    return plan


async def require_exact_acl(
    connection, names, writer_oid, *, profile=DB0056, read_only=True
):
    checked(profile)
    if profile is DB0056:
        from app.core.native_role_preflight_v1 import _exact_existing_acl

        await _exact_existing_acl(connection, names, writer_oid)
    else:
        from scripts.current0057_native_g12_acl_v1 import (
            observe_current0057_g12_writer_profile_v1,
        )

        await observe_current0057_g12_writer_profile_v1(
            connection,
            writer=names[0],
            custody=names[1],
            frame_reader=names[2],
            expected_writer_oid=writer_oid,
            read_only=read_only,
        )


def require_migration(profile, source: Path):
    checked(profile)
    from scripts.current0056_fitted_delta_catalog_v1 import pinned_migration

    pinned_migration(source)
    from scripts import current0056_migration_pin as previous
    from scripts import current0057_migration_pin as successor
    from scripts.verify_migration_identity import source_identity

    identity = source_identity(source)
    expected = previous if identity.head == "0056" else successor
    files = sorted((source / "migrations").rglob("*.py"))
    inventory = {
        path.relative_to(source).as_posix(): hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        for path in files
    }
    prefix = {
        path: digest
        for path, digest in inventory.items()
        if path != successor.MIGRATION_PATH
    }
    prefix_sha = hashlib.sha256(
        json.dumps(prefix, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if (
        identity.head not in {"0056", "0057"}
        or identity.sha256 != expected.MIGRATION_INVENTORY_SHA256
        or len(files) != expected.MIGRATION_INVENTORY_COUNT
        or (profile is DB0057 and identity.head != "0057")
        or (
            identity.head == "0057"
            and (
                inventory.get(successor.MIGRATION_PATH) != successor.MIGRATION_SHA256
                or len(prefix) != previous.MIGRATION_INVENTORY_COUNT
                or prefix_sha != previous.MIGRATION_INVENTORY_SHA256
            )
        )
        or source_identity(source) != identity
    ):
        raise ValueError("native_database_profile_migration_changed") from None
