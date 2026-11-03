"""Fresh exact-head55 owned PG fixture; no original fixture is overwritten.

Actual complete migrations, genuine portable PG17 inventory and original
fresh-database create/drop lifecycle are retained. No native seal, source
ownership, account credential, role grants or execution authority is supplied.
The historical/current54 fixtures remain unchanged and strict.
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
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import app.database.models  # noqa: F401 - register all ORM tables as in migrations/env.py.
from app.database.base import Base
from tests.original_frame_portable_pg_profile import (
    parse_portable_url,
    portable_profile,
    verify_portable_server,
    verify_portable_url,
)

ROOT = Path(__file__).resolve().parents[1]


def migrate_current55(connection, target="0055", *, down=False):
    if target not in {"0054", "0055"}:
        raise ValueError("current55_component_target")
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    script = ScriptDirectory.from_config(config)
    assert script.get_current_head() == "0055"
    assert script.get_revision("0055").down_revision == "0054"
    direction = script._downgrade_revs if down else script._upgrade_revs
    # Alembic applies Base.metadata's naming convention while constructing DDL.
    context = MigrationContext.configure(
        connection,
        opts={
            "fn": lambda heads, _: direction(target, heads),
            "target_metadata": Base.metadata,
            "compare_type": True,
        },
    )
    with Operations.context(context), context.begin_transaction():
        context.run_migrations()


@pytest.fixture
async def controlled_current55_database():
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
            await connection.run_sync(migrate_current55)
            assert (
                await connection.scalar(text("SELECT version_num FROM alembic_version"))
                == "0055"
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


@pytest.fixture
async def ledger_database(controlled_current55_database):
    """Run ledger transactions as the real 0055 actor in a fresh database."""
    from app.domain import collector_current_0055_catalog_v1 as catalog
    from app.domain import controlled_history_current55_schema_v2 as history
    from tests.integration.test_account_history_original_frame_repository_v1 import (
        collector,
        settings,
    )

    owner = controlled_current55_database
    grants = (
        ("SELECT, INSERT, UPDATE", "qualification_account_scopes"),
        ("SELECT, INSERT, UPDATE", "qualification_reservations"),
        # PostgreSQL 17 requires a separate privilege for SHARE locks. The
        # transition journal remains INSERT/SELECT only for row operations.
        ("SELECT, INSERT, MAINTAIN", "qualification_reservation_transitions"),
        ("SELECT, INSERT", "demo_collector_current_revision_lineages"),
        ("SELECT, INSERT", "demo_collector_issued_account_revisions"),
        ("SELECT, INSERT, UPDATE", "demo_account_controls"),
        ("SELECT, INSERT", "demo_control_journal"),
        ("SELECT, INSERT", "demo_control_clear_acknowledgements_v2"),
        ("SELECT, INSERT", "demo_policy_counter_initializations_v1"),
        ("SELECT, INSERT", "qualification_submission_outcomes"),
        ("SELECT, INSERT", "qualification_report_spool"),
        ("SELECT, INSERT", "qualification_report_projection_receipts"),
        ("SELECT, INSERT, UPDATE", "qualification_leverage_configuration_intents_v1"),
        ("SELECT, INSERT", "qualification_leverage_configuration_attempts_v1"),
        ("SELECT, INSERT", "qualification_leverage_configuration_results_v1"),
        ("SELECT", "alembic_version"),
    )
    append = f"public.{history.APPEND}({history.SIGNATURES[history.APPEND]})"
    frame = f"public.{history.FRAME}({history.SIGNATURES[history.FRAME]})"
    async with (
        collector(owner, grant=True) as (raw_reader, raw_oid),
        collector(owner) as (restricted, role_oid),
    ):
        assert raw_oid != role_oid
        raw_name = raw_reader.url.username
        assert re.fullmatch(r"ctcc_raw_frame_[a-f0-9]{32}", raw_name or "")
        name = restricted.url.username
        assert re.fullmatch(r"ctcc_raw_frame_[a-f0-9]{32}", name or "")
        async with owner.begin() as connection:
            for rights, table in grants:
                await connection.execute(
                    text(f'GRANT {rights} ON public.{table} TO "{name}"')
                )
            await connection.execute(
                text(
                    f'GRANT USAGE ON SEQUENCE public.qualification_reservation_transitions_id_seq TO "{name}"'
                )
            )
            await connection.execute(
                text(f'GRANT EXECUTE ON FUNCTION {append} TO "{name}"')
            )
            await connection.execute(
                text(f'GRANT EXECUTE ON FUNCTION {frame} TO "{raw_name}"')
            )
            await connection.execute(
                text(f'GRANT SELECT ON public.alembic_version TO "{raw_name}"')
            )
        writer = None
        try:
            writer = create_async_engine(
                restricted.url,
                poolclass=NullPool,
                connect_args={"server_settings": {"timezone": "UTC"}},
            )
            async with writer.begin() as connection:
                await settings(connection)
                actual = await connection.scalar(text(catalog.ACTOR_CATALOG_SQL))
                admitted = catalog.verify_actual_writer_catalog(actual)
                assert admitted["role_oid"] == role_oid
                assert admitted["native_baseline_admission"] is True
                assert admitted["execution_authority"] is False
            yield (
                owner,
                async_sessionmaker(writer, expire_on_commit=False, autoflush=False),
            )
        finally:
            if writer is not None:
                await writer.dispose()
            async with owner.begin() as connection:
                await connection.execute(
                    text(f'REVOKE SELECT ON public.alembic_version FROM "{raw_name}"')
                )
                await connection.execute(
                    text(f'REVOKE EXECUTE ON FUNCTION {frame} FROM "{raw_name}"')
                )
                await connection.execute(
                    text(f'REVOKE EXECUTE ON FUNCTION {append} FROM "{name}"')
                )
                await connection.execute(
                    text(
                        f'REVOKE USAGE ON SEQUENCE public.qualification_reservation_transitions_id_seq FROM "{name}"'
                    )
                )
                for rights, table in reversed(grants):
                    await connection.execute(
                        text(f'REVOKE {rights} ON public.{table} FROM "{name}"')
                    )
