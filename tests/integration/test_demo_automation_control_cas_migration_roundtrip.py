"""Exercise actual Demo table migrations through 0034 in isolated PostgreSQL.

The prior table is built by the migrations that touched it, preserving its
singleton constraint and historical defaults. No exchange I/O occurs.
"""

import importlib.util
import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from tests.durable_migration_fixtures import load_migration

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
_PRIOR_MIGRATIONS = {
    "0005": "0005_safe_demo_automation.py",
    "0011": "0011_demo_adaptive_portfolio.py",
    "0012": "0012_demo_automation_equity_basis.py",
    "0013": "0013_demo_structural_dynamic_risk.py",
}


def _load_prior_migration(revision):
    path = (
        Path(__file__).resolve().parents[2]
        / "migrations"
        / "versions"
        / _PRIOR_MIGRATIONS[revision]
    )
    spec = importlib.util.spec_from_file_location(f"demo_prior_{revision}", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"demo_prior_migration_unavailable:{revision}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _migrate(connection, direction, revision="0034"):
    migration = (
        load_migration(revision)
        if revision == "0034"
        else _load_prior_migration(revision)
    )
    migration.op = Operations(MigrationContext.configure(connection))
    getattr(migration, direction)()


def _create_prior_table(connection):
    # These are the only migrations before 0034 that touched this table.
    for revision in ("0005", "0011", "0012", "0013"):
        _migrate(connection, "upgrade", revision)


async def _shape(connection):
    columns = (
        await connection.execute(
            text("""
                SELECT column_name, data_type, is_nullable, column_default
                FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = 'demo_automation_state'
                ORDER BY ordinal_position
            """)
        )
    ).all()
    constraints = (
        await connection.execute(
            text("""
                SELECT conname, pg_get_constraintdef(k.oid)
                FROM pg_constraint k
                JOIN pg_class c ON c.oid = k.conrelid
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = current_schema()
                  AND c.relname = 'demo_automation_state'
                ORDER BY conname
            """)
        )
    ).all()
    triggers = (
        await connection.execute(
            text("""
                SELECT tgname, pg_get_triggerdef(t.oid)
                FROM pg_trigger t
                JOIN pg_class c ON c.oid = t.tgrelid
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = current_schema()
                  AND c.relname = 'demo_automation_state'
                  AND NOT t.tgisinternal
                ORDER BY tgname
            """)
        )
    ).all()
    functions = (
        await connection.execute(
            text("""
                SELECT proname, pg_get_functiondef(p.oid)
                FROM pg_proc p
                JOIN pg_namespace n ON n.oid = p.pronamespace
                WHERE n.nspname = current_schema()
                  AND proname = 'demo_automation_control_revision_guard'
                ORDER BY proname
            """)
        )
    ).all()
    indexes = (
        await connection.execute(
            text("""
                SELECT indexname, indexdef FROM pg_indexes
                WHERE schemaname = current_schema()
                  AND tablename = 'demo_automation_state'
                ORDER BY indexname
            """)
        )
    ).all()
    return columns, constraints, triggers, functions, indexes


@pytest.fixture
async def isolated_engine():
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        pytest.skip("explicit isolated DATABASE_URL required")
    schema = f"ctcc_demo_cas_migrate_{uuid4().hex}"
    admin = create_async_engine(url, poolclass=NullPool)
    engine = None
    created = False
    try:
        async with admin.begin() as connection:
            await connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
        created = True
        engine = create_async_engine(
            url,
            poolclass=NullPool,
            connect_args={"server_settings": {"search_path": f'"{schema}",pg_catalog'}},
        )
        async with engine.begin() as connection:
            await connection.run_sync(_create_prior_table)
            await connection.execute(
                text("""
                    INSERT INTO demo_automation_state (
                        id, armed, emergency_stop, locked, lock_reasons,
                        session_date, daily_pnl, trades_today,
                        consecutive_losses, active_trades, symbol_cooldowns,
                        realized_pnl_events
                    ) VALUES (
                        1, TRUE, FALSE, FALSE, '[]'::jsonb,
                        CURRENT_DATE, 0, 0, 0,
                        '{"synthetic_uncertain":{"source":"migration_test"}}'::jsonb,
                        '{}'::jsonb, '[]'::jsonb
                    )
                """)
            )
        yield engine
    finally:
        if engine is not None:
            await engine.dispose()
        if created:
            async with admin.begin() as connection:
                await connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        await admin.dispose()


async def test_0034_actual_upgrade_downgrade_reupgrade_preserves_uncertain_row(
    isolated_engine,
):
    engine = isolated_engine
    async with engine.begin() as connection:
        prior_shape = await _shape(connection)
        assert any(
            name == "ck_demo_automation_state_singleton_id"
            for name, _ in prior_shape[1]
        )
        await connection.run_sync(_migrate, "upgrade")
        upgraded_shape = await _shape(connection)
        row = (
            await connection.execute(
                text("""
                    SELECT armed, emergency_stop, locked, control_revision,
                           restart_latch_required, active_trades, lock_reasons
                    FROM demo_automation_state WHERE id = 1
                """)
            )
        ).one()
        assert row[:5] == (False, True, True, 0, True)
        assert row.active_trades == {
            "synthetic_uncertain": {"source": "migration_test"}
        }
        assert "control_protocol_upgrade_requires_clear" in row.lock_reasons
        assert len(upgraded_shape[2]) == 1
        assert len(upgraded_shape[3]) == 1

    # An old writer cannot alter even a non-control field without advancing
    # the new revision. Keep the rejected transaction separate from the DDL.
    with pytest.raises(IntegrityError):
        async with engine.begin() as connection:
            await connection.execute(
                text("""
                    UPDATE demo_automation_state
                    SET last_error = 'stale_writer' WHERE id = 1
                """)
            )

    async with engine.begin() as connection:
        await connection.run_sync(_migrate, "downgrade")
        assert await _shape(connection) == prior_shape
        stopped = (
            await connection.execute(
                text("""
                    SELECT armed, emergency_stop, locked, active_trades
                    FROM demo_automation_state WHERE id = 1
                """)
            )
        ).one()
        assert stopped.armed is False
        assert stopped.emergency_stop is True
        assert stopped.locked is True
        assert stopped.active_trades == row.active_trades
        await connection.run_sync(_migrate, "upgrade")
        assert await _shape(connection) == upgraded_shape
        final = (
            await connection.execute(
                text("""
                    SELECT armed, emergency_stop, locked,
                           restart_latch_required, active_trades
                    FROM demo_automation_state WHERE id = 1
                """)
            )
        ).one()
        assert final[:4] == (False, True, True, True)
        assert final.active_trades == row.active_trades
