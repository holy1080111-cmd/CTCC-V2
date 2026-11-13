"""Check the lineage transaction stays on its real writer and exact Control."""

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.database.repositories import (
    account_revision_collector_lineage_current55_v1 as lineage,
)
from app.trade_qualification.demo_control import ControlScope


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", [None, "scope", "control", "actor", "xid", "owner"])
async def test_db0056_lineage_transaction_cannot_leave_original_account(
    monkeypatch, fault
):
    engine = create_async_engine(
        "postgresql+asyncpg://writer@localhost/demo", poolclass=NullPool
    )
    factory = async_sessionmaker(engine, autoflush=False, expire_on_commit=False)
    database = object(), object()
    now = datetime(2026, 1, 1, tzinfo=UTC)
    expected_scope = object()
    scope = object() if fault == "scope" else expected_scope
    expected_control = SimpleNamespace(scope=ControlScope("demo", "12345"))
    pin = "a" * 64
    state = {"lineage_factory": factory}
    context = (*database, expected_scope, expected_control, pin)
    owner_db = Mock(side_effect=[context, None if fault == "owner" else context])
    monkeypatch.setattr(lineage, "_owner_database", owner_db)
    monkeypatch.setattr(lineage, "_remaining", lambda *_: 1)
    monkeypatch.setattr(lineage, "_owner", lambda _: (object(), object()))
    monkeypatch.setattr(
        lineage.locked56, "_factory_pins", lambda _: (factory, engine, (), ())
    )
    monkeypatch.setattr(lineage.locked56, "_require_session", lambda *_: None)
    old = AsyncMock(side_effect=AssertionError("DB0055 fallback"))
    monkeypatch.setattr(lineage, "_admit", old)
    sql = []

    async def execute(session, statement, parameters=None):
        assert session.bind is engine and session.in_transaction()
        sql.append(statement)

    monkeypatch.setattr(lineage.AsyncSession, "execute", execute)
    held = Mock()
    held.observation.control = SimpleNamespace(
        state=object() if fault == "control" else expected_control,
        event_sha256=pin,
    )

    @asynccontextmanager
    async def held_control(selected, session, control_scope, *, roles, purpose):
        assert selected is database[0] and roles is database[1]
        assert control_scope == expected_control.scope and session.in_transaction()
        assert (
            purpose
            is lineage.locked56.NativeWriterControlPurpose0056.PREPARE_OBSERVATION
        )
        yield held

    monkeypatch.setattr(
        lineage.locked56, "held_native_writer_control_0056_v1", held_control
    )
    first = ("writer", "writer", "none", 11, False, False, False, 11, "101", now)
    after = (
        *first[:8],
        "102" if fault == "xid" else first[8],
        now + timedelta(seconds=1),
    )
    catalog = lineage.issuer_transaction.NativeIssuerCatalogIdentity(
        "0056", 11, "b" * 64
    )
    changed = lineage.issuer_transaction.NativeIssuerCatalogIdentity(
        "0056", 12, "c" * 64
    )
    transaction = AsyncMock(
        side_effect=[
            (catalog, first),
            (changed if fault == "actor" else catalog, after),
        ]
    )
    monkeypatch.setattr(
        lineage.issuer_transaction, "read_actual_native_issuer_transaction", transaction
    )
    row = object()
    ledger = SimpleNamespace(_locked=AsyncMock(return_value=row))
    visited = []

    async def act():
        async with (
            factory() as session,
            session.begin(),
            lineage._owned_lineage_transaction(
                session, ledger, scope, state, object(), object()
            ) as result,
        ):
            visited.append(result)

    try:
        if fault is None:
            await act()
        else:
            with pytest.raises(
                lineage.policy.CollectorRevisionLineageError,
                match="^collector_lineage_database_0056_changed$",
            ):
                await act()
    finally:
        await engine.dispose()
    old.assert_not_awaited()
    if fault in {"scope", "control"}:
        assert not visited
        transaction.assert_not_awaited()
    else:
        assert visited == [(row, expected_control, pin)]
        assert lineage._DB56_LINEAGE_FENCE in sql
        assert transaction.await_count == 2
