"""Closed physical database contracts; choosing a profile never grants authority."""

from __future__ import annotations

import hashlib
import json
from enum import Enum
from pathlib import Path


class NativeDatabaseProfileV1(Enum):
    DB0056 = "0056"
    DB0057 = "0057"
    DB0058 = "0058"


DB0056 = NativeDatabaseProfileV1.DB0056
DB0057 = NativeDatabaseProfileV1.DB0057
DB0058 = NativeDatabaseProfileV1.DB0058


def checked(profile: NativeDatabaseProfileV1) -> NativeDatabaseProfileV1:
    if type(profile) is not NativeDatabaseProfileV1:
        raise ValueError("native_database_profile_unsupported") from None
    return profile


def head(profile: NativeDatabaseProfileV1) -> str:
    return checked(profile).value


def from_head(value: str) -> NativeDatabaseProfileV1:
    """Closed DATA dispatch only; the caller must verify original SQL custody."""
    if type(value) is not str or value not in ("0056", "0057", "0058"):
        raise ValueError("native_database_profile_unsupported") from None
    return NativeDatabaseProfileV1(value)


def reservation_update_body(profile: NativeDatabaseProfileV1) -> str:
    """Exact source pin, meaningful only inside a genuine held catalog check."""
    checked(profile)
    if profile is DB0058:
        from app.domain.native_protective_hold_schema_v58 import CURRENT_BODY_SHA256
    else:
        from app.domain.native_filled_flat_closure_schema_v55 import CURRENT_BODY_SHA256
    return CURRENT_BODY_SHA256


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
    if profile in (DB0057, DB0058):
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
            **({"profile": profile} if profile is DB0058 else {}),
        )


def require_migration(profile, source: Path):
    checked(profile)
    from scripts.current0056_fitted_delta_catalog_v1 import pinned_migration

    pinned_migration(source)
    from scripts import current0056_migration_pin as previous
    from scripts import current0057_migration_pin as successor
    from scripts import current0058_migration_pin as protective
    from scripts.verify_migration_identity import source_identity

    identity = source_identity(source)
    choices = {DB0056: previous, DB0057: successor, DB0058: protective}
    actual = from_head(identity.head)
    expected = choices[actual]
    files = sorted((source / "migrations").rglob("*.py"))
    inventory = {
        path.relative_to(source).as_posix(): hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        for path in files
    }
    if (
        identity.sha256 != expected.MIGRATION_INVENTORY_SHA256
        or len(files) != expected.MIGRATION_INVENTORY_COUNT
        or tuple(choices).index(actual) < tuple(choices).index(profile)
        or source_identity(source) != identity
    ):
        raise ValueError("native_database_profile_migration_changed") from None
    prefix = dict(inventory)
    for selected, parent in ((DB0058, DB0057), (DB0057, DB0056)):
        if tuple(choices).index(actual) < tuple(choices).index(selected):
            continue
        pin, older = choices[selected], choices[parent]
        if prefix.pop(pin.MIGRATION_PATH, None) != pin.MIGRATION_SHA256:
            raise ValueError("native_database_profile_migration_changed") from None
        digest = hashlib.sha256(
            json.dumps(prefix, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        if (
            len(prefix) != older.MIGRATION_INVENTORY_COUNT
            or digest != older.MIGRATION_INVENTORY_SHA256
        ):
            raise ValueError("native_database_profile_migration_changed") from None
