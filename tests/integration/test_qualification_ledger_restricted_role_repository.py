"""Check the actual 0055 ledger writer and PostgreSQL DDL denial."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.repositories.qualification_ledger import (
    _require_event_journal_write_schema,
)
from app.domain.collector_current_0055_catalog_v1 import Current0055CatalogError
from app.trade_qualification.reservations import LedgerScope
from tests.controlled_current55_fixtures import (
    controlled_current55_database as controlled_current55_database,  # noqa: PLC0414 -- Register shared pytest fixture.
)
from tests.controlled_current55_fixtures import (
    ledger_database as ledger_database,  # noqa: PLC0414 -- Register shared pytest fixture.
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_GUARD_FUNCTIONS = (
    "qualification_ledger_immutable",
    "qualification_scope_update",
    "qualification_reservation_update",
)


async def test_write_guard_accepts_restricted_role_and_rejects_function_replacement(
    ledger_database,
):
    owner, sessions = ledger_database
    scope = LedgerScope(account_id="123456789", settlement_currency="USDT")
    async with sessions() as session:
        async with session.begin():
            await _require_event_journal_write_schema(session, scope)
            rights = (
                await session.execute(
                    text(
                        "SELECT bool_and(current_user = session_user), "
                        "bool_and(NOT r.rolsuper AND NOT r.rolcreaterole "
                        "AND NOT r.rolcreatedb AND r.rolcanlogin), "
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
                pytest.fail("restricted role unexpectedly replaced a guard function")
            finally:
                # Roll back even if a future privilege change makes the
                # replacement succeed. Never commit altered guard code.
                await session.rollback()

        async with session.begin():
            await _require_event_journal_write_schema(session, scope)

    async with AsyncSession(owner) as session, session.begin():
        with pytest.raises(
            Current0055CatalogError, match="current0055_actual_catalog_unavailable"
        ):
            await _require_event_journal_write_schema(session, scope)
