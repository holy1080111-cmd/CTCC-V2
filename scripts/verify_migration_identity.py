"""Read the source graph and real database revision independently; never migrate.

Only redacted revision/hash facts reach stdout. The caller must separately run
Alembic schema drift checking; matching revision labels do not prove schema.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

SCHEMA = "ctcc.migration_identity.v1"
SOURCE_ROOT = Path(__file__).resolve().parents[1]
_REVISION = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
_CODES = frozenset(
    {
        "source_head_not_unique",
        "source_revision_invalid",
        "database_head_not_unique",
        "database_revision_invalid",
        "migration_revision_mismatch",
        "migration_source_changed",
        "migration_source_layout_invalid",
        "migration_identity_read_failed",
    }
)


class MigrationIdentityError(ValueError):
    pass


@dataclass(frozen=True)
class SourceIdentity:
    head: str
    sha256: str


def _single_head(heads, kind):
    if len(heads) != 1:
        raise MigrationIdentityError(f"{kind}_head_not_unique")
    value = heads[0]
    if type(value) is not str or _REVISION.fullmatch(value) is None:
        raise MigrationIdentityError(f"{kind}_revision_invalid")
    return value


def source_identity(root: Path) -> SourceIdentity:
    """Resolve the repository's single configured migration graph, not a hint."""
    config = Config(str(root / "alembic.ini"))
    if config.get_main_option(
        "script_location"
    ) != "migrations" or config.get_main_option("version_locations"):
        raise MigrationIdentityError("migration_source_layout_invalid")
    config.set_main_option("path_separator", "os")
    config.set_main_option(
        "script_location", str(root / "migrations").replace("%", "%%")
    )
    script = ScriptDirectory.from_config(config)
    head = _single_head(script.get_heads(), "source")
    files = sorted((root / "migrations").rglob("*.py"))
    if not files or any(path.is_symlink() for path in files):
        raise MigrationIdentityError("migration_source_layout_invalid")
    # Include env.py and graph source bytes; never store a URL or local path.
    sources = {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in files
    }
    raw = json.dumps(sources, sort_keys=True, separators=(",", ":")).encode()
    return SourceIdentity(head, hashlib.sha256(raw).hexdigest())


def database_head(connection) -> str:
    """Online MigrationContext SELECTs the database's version table; no writes."""
    context = MigrationContext.configure(connection)
    if context.as_sql:
        raise MigrationIdentityError("migration_identity_read_failed")
    return _single_head(context.get_current_heads(), "database")


def _result(before, observed, after):
    if before != after:
        raise MigrationIdentityError("migration_source_changed")
    if observed != before.head:
        raise MigrationIdentityError("migration_revision_mismatch")
    return {
        "schema": SCHEMA,
        "status": "PASS",
        "source_head": before.head,
        "database_head": observed,
        "migration_sha256": before.sha256,
        "execution_authority": False,
    }


def verify_connection(root: Path, connection) -> dict:
    """Read-only adapter used with an already-open connection, including tests."""
    before = source_identity(root)
    observed = database_head(connection)
    return _result(before, observed, source_identity(root))


async def inspect_runtime(root: Path) -> dict:
    # Resolve source before reading settings or opening a database connection.
    before = source_identity(root)
    from app.config.settings import get_settings

    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            observed = await connection.run_sync(database_head)
    finally:
        await engine.dispose()
    return _result(before, observed, source_identity(root))


def main() -> int:
    try:
        result = asyncio.run(inspect_runtime(SOURCE_ROOT))
    except Exception as error:  # noqa: BLE001 - redacted CLI failure boundary
        # Connection/configuration exceptions may contain credentials. Never
        # serialize exception text, database URLs, settings or private headers.
        code = str(error) if type(error) is MigrationIdentityError else ""
        result = {
            "schema": SCHEMA,
            "status": "FAIL",
            "code": code if code in _CODES else "migration_identity_read_failed",
            "execution_authority": False,
        }
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
