"""Fresh isolated DB0056 owner migration for hosted tests only.

This module is deliberately under ``tests``. It never reads application
settings, a production DSN, or a credential file, and is not a Compose or API
entry point. The manifest pin and CA digest must be supplied from independently
reviewed lab inputs. A successful result has no startup or trading authority.
"""

from __future__ import annotations

import asyncio
import re
import ssl
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

import app.database.models  # noqa: F401 -- register the exact migration metadata.
from app.database.base import Base
from app.database.native_pg_tls_v1 import verified_native_pg_tls_context_v1
from scripts import current0056_fitted_delta_catalog_v1 as fitted
from scripts import current_contract_source_scopes_v1 as source_scopes

_SOURCE = Path(__file__).resolve().parents[1]
_HEX = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_HOST = re.compile(r"ctcc-final-[0-9a-f]{12}-postgres\Z", re.ASCII)
_DATABASE = re.compile(r"ctcc_native_obs56_[0-9a-f]{12}\Z", re.ASCII)
_IDENTITY = text("""
SELECT pg_catalog.current_database()::text, current_user::text,
       (SELECT ssl FROM pg_catalog.pg_stat_ssl
        WHERE pid = pg_catalog.pg_backend_pid()),
       pg_catalog.current_setting('server_version_num')::text,
       NOT EXISTS (
         SELECT 1 FROM pg_catalog.pg_class c
         JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
         WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m','f','S')
       )
""")


class LabOwnerMigration0056Error(ValueError):
    """Fixed refusal without URL, certificate, or database details."""


@dataclass(frozen=True, slots=True)
class LabOwnerMigration0056Result:
    tls_context: ssl.SSLContext = field(repr=False, compare=False)
    migration_sha256: str
    manifest_sha256: str
    database_head: Literal["0056"] = field(default="0056", init=False)
    actual_lab_connection_observed: Literal[True] = field(default=True, init=False)
    startup_admission: Literal[False] = field(default=False, init=False)
    execution_authority: Literal[False] = field(default=False, init=False)


def _need(condition: bool) -> None:
    if condition is not True:
        raise LabOwnerMigration0056Error("native_owner_migration_lab_0056_denied")


def _prepare(owner_url: URL, ca_file: Path, ca_sha256: str, manifest_sha256: str):
    _need(
        type(owner_url) is URL
        and owner_url.drivername == "postgresql+asyncpg"
        and owner_url.username == "ctcc"
        and owner_url.password is None
        and type(owner_url.host) is str
        and _HOST.fullmatch(owner_url.host) is not None
        and owner_url.port == 5432
        and type(owner_url.database) is str
        and _DATABASE.fullmatch(owner_url.database) is not None
        and not owner_url.query
        and type(manifest_sha256) is str
        and _HEX.fullmatch(manifest_sha256) is not None
    )
    tls = verified_native_pg_tls_context_v1(ca_file, ca_sha256)
    scope = source_scopes.actual_current_source_scope(_SOURCE)
    _need(
        scope.actual_source_head == scope.database_target == "0056"
        and scope.manifest_sha256 == manifest_sha256
        and scope.account_complete is scope.execution_authority is False
    )
    fitted.pinned_migration(_SOURCE)
    config = Config(str(_SOURCE / "alembic.ini"))
    config.set_main_option("path_separator", "os")
    config.set_main_option("script_location", str(_SOURCE / "migrations"))
    script = ScriptDirectory.from_config(config)
    _need(
        tuple(script.get_heads()) == ("0056",)
        and script.get_revision("0056").down_revision == "0055"
    )
    return tls, scope, script


def _upgrade_fresh(sync_connection, scope, script) -> None:
    """Use the env.py metadata/options and the existing tested Alembic graph."""
    migration = MigrationContext.configure(
        sync_connection,
        opts={
            "fn": lambda heads, _: script._upgrade_revs("0056", heads),
            "target_metadata": Base.metadata,
            "compare_type": True,
        },
    )
    _need(migration.get_current_heads() == ())
    with Operations.context(migration), migration.begin_transaction():
        migration.run_migrations()
    source_scopes.verify_database_scope(sync_connection, _SOURCE, scope)


async def migrate_fresh_lab_owner_0056_v1(
    *, owner_url: URL, ca_file: Path, ca_sha256: str, manifest_sha256: str
) -> LabOwnerMigration0056Result:
    """Migrate only a fresh named lab DB, then independently read back head/TLS.

    The caller creates and later destroys the isolated database. The returned
    context may be reused by its restricted role engines in the same lab.
    """
    engine = None
    try:
        tls, scope, script = _prepare(owner_url, ca_file, ca_sha256, manifest_sha256)
        async with asyncio.timeout(120):
            engine = create_async_engine(
                owner_url, poolclass=NullPool, connect_args={"ssl": tls}
            )
            async with engine.begin() as connection:
                before = tuple((await connection.execute(_IDENTITY)).one())
                _need(before == (owner_url.database, "ctcc", True, "170011", True))
                await connection.run_sync(_upgrade_fresh, scope, script)
            # NullPool creates a new physical connection after the commit.
            async with engine.connect() as connection:
                after = tuple((await connection.execute(_IDENTITY)).one())
                _need(after[:4] == before[:4] and after[4] is False)
                await connection.run_sync(
                    source_scopes.verify_database_scope, _SOURCE, scope
                )
            _need(source_scopes.actual_current_source_scope(_SOURCE) == scope)
        return LabOwnerMigration0056Result(
            tls_context=tls,
            migration_sha256=scope.actual_migration_sha256,
            manifest_sha256=scope.manifest_sha256,
        )
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 -- fixed refusal avoids leaking lab details.
        raise LabOwnerMigration0056Error(
            "native_owner_migration_lab_0056_denied"
        ) from None
    finally:
        if engine is not None:
            await engine.dispose()
