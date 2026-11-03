"""Fresh current0053 database fixture; the historical0052 fixture stays intact.

This establishes a source/database migration boundary only. It does not grant
a raw reader, lineage actor, account credential, native catalog seal or any
execution authority. Import with an explicit current0053 fixture name.
"""

import os
import re
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from scripts import current_contract_source_scopes_v1 as source_scopes
from tests.original_frame_portable_pg_profile import (
    parse_portable_url,
    portable_profile,
    verify_portable_server,
    verify_portable_url,
)

ROOT = Path(__file__).resolve().parents[1]


def _migrate_0053(connection):
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    script = ScriptDirectory.from_config(config)
    source_scope = source_scopes.historical0053_source_scope(ROOT)
    assert script.get_current_head() == source_scope.actual_source_head
    assert script.get_revision("0053").down_revision == "0052"
    context = MigrationContext.configure(
        connection, opts={"fn": lambda heads, _: script._upgrade_revs("0053", heads)}
    )
    with Operations.context(context), context.begin_transaction():
        context.run_migrations()


@pytest.fixture
async def original_frame_database_0053():
    raw = os.environ.get("DATABASE_URL", "")
    if not raw:
        pytest.skip(
            "explicit isolated PostgreSQL DATABASE_URL required; skip is not acceptance"
        )
    profile = portable_profile(ROOT)
    if profile is None:
        # Preserve the original explicit Docker fixture contract unchanged.
        url = make_url(raw)
        assert url.drivername == "postgresql+asyncpg"
        assert re.fullmatch(r"ctcc-final-[a-f0-9]{12}-postgres", url.host or "")
        assert url.username == "ctcc" and url.password is None
        assert url.port == 5432 and url.database == "ctcc" and not url.query
    else:
        url = parse_portable_url(raw)
        verify_portable_url(url, profile)
    primary = create_async_engine(url, poolclass=NullPool)
    child_name = "ctcc_original_frame_" + uuid4().hex
    child = None
    created = False
    try:
        async with primary.connect() as connection:
            if profile is not None:
                await verify_portable_server(connection, url, profile)
            assert (
                170000
                <= await connection.scalar(
                    text("SELECT current_setting('server_version_num')::int")
                )
                < 180000
            )
        async with primary.connect() as connection:
            connection = await connection.execution_options(
                isolation_level="AUTOCOMMIT"
            )
            if profile is not None:
                await verify_portable_server(connection, url, profile)
            assert re.fullmatch(r"ctcc_original_frame_[a-f0-9]{32}", child_name)
            await connection.execute(text(f'CREATE DATABASE "{child_name}"'))
            created = True
        if profile is None:
            child = create_async_engine(
                url.set(database=child_name), poolclass=NullPool
            )
        else:
            # Fresh fixture-owned engine never crosses this test's event loop.
            # Separate committed-session readback remains unchanged in the repository.
            child = create_async_engine(
                url.set(database=child_name),
                pool_size=2,
                max_overflow=0,
                pool_pre_ping=True,
                pool_recycle=1800,
            )
        async with child.begin() as connection:
            await connection.run_sync(_migrate_0053)
            await connection.run_sync(
                lambda actual: source_scopes.verify_database_scope(
                    actual, ROOT, source_scopes.historical0053_source_scope(ROOT)
                )
            )
            assert (
                await connection.scalar(text("SELECT version_num FROM alembic_version"))
                == "0053"
            )
        yield child
    finally:
        if child is not None:
            await child.dispose()
        if created:
            assert re.fullmatch(r"ctcc_original_frame_[a-f0-9]{32}", child_name)
            async with primary.connect() as connection:
                connection = await connection.execution_options(
                    isolation_level="AUTOCOMMIT"
                )
                if profile is not None:
                    await verify_portable_server(connection, url, profile)
                assert (
                    await connection.scalar(text("SELECT current_database()")) == "ctcc"
                )
                await connection.execute(
                    text(f'DROP DATABASE "{child_name}" WITH (FORCE)')
                )
        await primary.dispose()
