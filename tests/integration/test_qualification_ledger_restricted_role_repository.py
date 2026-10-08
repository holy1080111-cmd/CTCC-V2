"""DB0035 qualification write guard under a non-owner PostgreSQL role.

The shared PostgreSQL harness migrates an isolated database to head before this
test. A generated NOLOGIN role has only the qualification tables' normal
reader/writer rights; it never owns tables or trigger functions. This test
performs no exchange or account I/O and leaves no ledger rows.
"""

from __future__ import annotations

import re
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.repositories.qualification_ledger import (
    _require_event_journal_schema,
    _require_event_journal_write_schema,
)

pytest_plugins = ["tests.integration.test_qualification_ledger_repository"]
pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TABLE_GRANTS = (
    ("qualification_account_scopes", "SELECT, INSERT, UPDATE"),
    ("qualification_reservations", "SELECT, INSERT, UPDATE"),
    ("qualification_reservation_transitions", "SELECT, INSERT"),
)
_GUARD_FUNCTIONS = (
    "qualification_ledger_immutable",
    "qualification_scope_update",
    "qualification_reservation_update",
)


@pytest.fixture
async def restricted_role(database):
    engine, _ = database
    role = "ctcc_r6_" + uuid4().hex[:20]
    assert re.fullmatch(r"ctcc_r6_[a-f0-9]{20}", role)
    created = False
    try:
        async with engine.begin() as connection:
            # The Docker harness performs the full Alembic upgrade, rather
            # than constructing an alternate subset of the ledger schema.
            assert (
                await connection.scalar(text("SELECT version_num FROM alembic_version"))
                == "0035"
            )
            assert (
                int(await connection.scalar(text("SHOW server_version_num"))) >= 170000
            )
            await connection.execute(text(f"CREATE ROLE {role} NOLOGIN"))
            await connection.execute(text(f"GRANT USAGE ON SCHEMA public TO {role}"))
            for table, privileges in _TABLE_GRANTS:
                await connection.execute(
                    text(f"GRANT {privileges} ON public.{table} TO {role}")
                )
        created = True
        yield engine, role
    finally:
        if created:
            # Revoke only this test's exact grants before removing its role.
            # No DROP OWNED/CASCADE or ledger table cleanup is involved.
            async with engine.begin() as connection:
                for table, privileges in _TABLE_GRANTS:
                    await connection.execute(
                        text(f"REVOKE {privileges} ON public.{table} FROM {role}")
                    )
                await connection.execute(
                    text(f"REVOKE USAGE ON SCHEMA public FROM {role}")
                )
                await connection.execute(text(f"DROP ROLE {role}"))


async def test_write_guard_accepts_restricted_role_and_rejects_function_replacement(
    restricted_role,
):
    engine, role = restricted_role
    async with engine.connect() as connection:
        # SET ROLE survives transaction commit on this one isolated connection.
        # The guard's READ COMMITTED statement remains its first SQL in the
        # subsequent qualification transaction.
        await connection.execute(text(f"SET ROLE {role}"))
        await connection.commit()
        try:
            async with AsyncSession(bind=connection) as session:
                async with session.begin():
                    await _require_event_journal_write_schema(session)
                    rights = (
                        await session.execute(
                            text(
                                "SELECT bool_and(current_user <> session_user), "
                                "bool_and(NOT r.rolsuper AND NOT r.rolcreaterole "
                                "AND NOT r.rolcreatedb AND NOT r.rolcanlogin), "
                                "bool_and(f.proowner <> r.oid), count(*)=3 "
                                "FROM pg_catalog.pg_roles r "
                                "CROSS JOIN pg_catalog.pg_proc f "
                                "JOIN pg_catalog.pg_namespace n ON n.oid=f.pronamespace "
                                "WHERE r.rolname=current_user AND n.nspname='public' "
                                "AND f.proname IN ('qualification_ledger_immutable', "
                                "'qualification_scope_update', "
                                "'qualification_reservation_update')"
                            )
                        )
                    ).one()
                    assert tuple(rights) == (True, True, True, True)
                    schema_rights = (
                        await session.execute(
                            text(
                                "SELECT pg_catalog.has_schema_privilege("
                                "current_user, 'public', 'USAGE'), "
                                "NOT pg_catalog.has_schema_privilege("
                                "current_user, 'public', 'CREATE')"
                            )
                        )
                    ).one()
                    assert tuple(schema_rights) == (True, True)
                    forbidden = (
                        await session.execute(
                            text(
                                "SELECT bool_and(NOT pg_catalog.has_table_privilege("
                                "current_user, c.oid, 'DELETE')), "
                                "bool_and(NOT pg_catalog.has_table_privilege("
                                "current_user, c.oid, 'TRUNCATE')), "
                                "bool_and(NOT pg_catalog.has_table_privilege("
                                "current_user, c.oid, 'TRIGGER')), count(*)=3 "
                                "FROM pg_catalog.pg_class c "
                                "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
                                "WHERE n.nspname='public' AND c.relname IN "
                                "('qualification_account_scopes', "
                                "'qualification_reservations', "
                                "'qualification_reservation_transitions')"
                            )
                        )
                    ).one()
                    assert tuple(forbidden) == (True, True, True, True)

                for function in _GUARD_FUNCTIONS:
                    try:
                        await session.execute(
                            text(
                                f"CREATE OR REPLACE FUNCTION public.{function}() "
                                "RETURNS trigger AS $$ BEGIN RAISE EXCEPTION "
                                "'restricted_role_probe'; END; $$ LANGUAGE plpgsql"
                            )
                        )
                    except DBAPIError as exc:
                        origin = exc.orig
                        sqlstate = getattr(origin, "sqlstate", None) or getattr(
                            getattr(origin, "__cause__", None), "sqlstate", None
                        )
                        assert sqlstate == "42501"
                    else:
                        pytest.fail(
                            "restricted role unexpectedly replaced a guard function"
                        )
                    finally:
                        # Roll back even if a future privilege change makes the
                        # replacement succeed. Never commit altered guard code.
                        await session.rollback()
        finally:
            await connection.rollback()
            await connection.execute(text("RESET ROLE"))
            await connection.commit()

    # The owner sees the same reviewed functions after all denied attempts.
    async with AsyncSession(engine) as owner_session:
        await _require_event_journal_schema(owner_session)
