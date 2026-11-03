"""Observe a real DB0054 child of source0055 without issuing current54 authority.

The exact-source0054 capture and its historical receipts remain in their
original module. This module only records the distinct source55/database54
case, with a fresh database-scope read before and after catalog observation.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import text

from app.domain import collector_current_0054_catalog_v1 as policy
from app.domain.source_primitives import canonical
from scripts import current_contract_source_scopes_v1 as scopes
from scripts.verify_migration_identity import SOURCE_ROOT

SCHEMA = "ctcc.source0055_historical0054.owner_observation.v1"
_FIELDS = frozenset(
    {
        "schema",
        "source_head",
        "database_head",
        "source_scope",
        "owner_catalog",
        "current0054_observation",
        "native_baseline_admission",
        "runtime_admission",
        "account_complete",
        "execution_authority",
    }
)


def _scope(root: Path):
    value = scopes.database0054_source_scope(root)
    if value.actual_source_head != "0055" or value.database_target != "0054":
        raise ValueError("source0055_historical0054_scope_required")
    return value


async def capture_historical0054_owner_on_current0055(
    connection, root: Path = SOURCE_ROOT
):
    """Return source-bound, read-only observation; never a native seal."""
    root = Path(root)
    original = _scope(root)
    for statement in (
        "SET TRANSACTION ISOLATION LEVEL READ COMMITTED",
        "SET TRANSACTION READ ONLY",
        "SET LOCAL TIME ZONE 'UTC'",
        "SET LOCAL search_path TO pg_catalog,public,pg_temp",
    ):
        await connection.execute(text(statement))
    before = await connection.run_sync(
        lambda actual: scopes.verify_database_scope(actual, root, original)
    )
    first = await connection.scalar(text(policy.OWNER_CATALOG_SQL))
    second = await connection.scalar(text(policy.OWNER_CATALOG_SQL))
    observed = policy.observe_actual_owner_catalog(first)
    after = await connection.run_sync(
        lambda actual: scopes.verify_database_scope(actual, root, original)
    )
    if (
        canonical(first) != canonical(second)
        or before != after
        or original != _scope(root)
        or observed.get("native_baseline_admission") is not False
        or observed.get("account_complete") is not False
        or observed.get("execution_authority") is not False
    ):
        raise ValueError("source0055_historical0054_observation_changed")
    document = {
        "schema": SCHEMA,
        "source_head": "0055",
        "database_head": "0054",
        "source_scope": before,
        "owner_catalog": first,
        "current0054_observation": observed,
        "native_baseline_admission": False,
        "runtime_admission": False,
        "account_complete": False,
        "execution_authority": False,
    }
    raw = canonical(document) + b"\n"
    if not 0 < len(raw) <= 16 * 1024 * 1024:
        raise ValueError("source0055_historical0054_observation_size")
    return document, raw


def verify_historical0054_owner_on_current0055(
    document, raw: bytes, root: Path = SOURCE_ROOT
):
    """Verify the distinct receipt shape without accepting caller authority."""
    scope = _scope(Path(root))
    if (
        type(document) is not dict
        or set(document) != _FIELDS
        or type(raw) is not bytes
        or not 0 < len(raw) <= 16 * 1024 * 1024
        or raw != canonical(document) + b"\n"
        or document["schema"] != SCHEMA
        or document["source_head"] != "0055"
        or document["database_head"] != "0054"
        or document["source_scope"]
        != {
            "schema": "ctcc.actual_source_historical_database_scope.v1",
            "source_head": scope.actual_source_head,
            "database_head": scope.database_target,
            "migration_sha256": scope.actual_migration_sha256,
            "historical_prefix_sha256": scope.historical_prefix_sha256,
            "manifest_sha256": scope.manifest_sha256,
            "account_complete": False,
            "execution_authority": False,
        }
        or document["current0054_observation"]
        != policy.observe_actual_owner_catalog(document["owner_catalog"])
        or any(
            document[name] is not False
            for name in (
                "native_baseline_admission",
                "runtime_admission",
                "account_complete",
                "execution_authority",
            )
        )
    ):
        raise ValueError("source0055_historical0054_receipt_invalid")
    return document["current0054_observation"]
