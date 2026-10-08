"""Isolated PostgreSQL checks for a bounded, read-only UID journal inspection.

All account IDs, reservations and writer races here are synthetic. No exchange
credentials, account reads, orders, or production database are used.
"""

import asyncio
import json
from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.database.models.qualification_ledger import (
    QualificationAccountScope,
    QualificationReservation,
    QualificationReservationTransition,
)
from app.database.repositories.qualification_ledger import (
    QualificationLedgerRepository,
    _require_event_journal_schema,
    _require_event_journal_session,
)
from app.trade_qualification.reservations import (
    LedgerScope,
    QualificationLedgerError,
    ReservationRequest,
    canonical,
    digest,
    prepare_reservation,
    reservation_id,
)
from app.trade_qualification.service import _plain
from tests.integration.test_qualification_ledger_repository import (
    initialize,
    refresh,
)

pytest_plugins = ["tests.integration.test_qualification_ledger_repository"]
pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_committed_event_journal_schema_retention_guard_accepts_current_head(
    database,
):
    """Surface a missing retention guard before the long PostgreSQL suite."""
    async with database[1]() as session:
        await _require_event_journal_schema(session)


@pytest.mark.parametrize(
    "state", ("reserved", "consumed", "uncertain", "reconciled_flat")
)
async def test_all_states_and_expired_tombstone_remain_consumed(
    database, fixture, state
):
    repository, clock = await initialize(database, fixture)
    scope = fixture.request.scope
    empty = await repository.inspect_consumed_event_journal(scope)
    initial = json.loads(empty.document_json)
    assert empty.consumed_event_keys == frozenset()
    assert initial["event_count"] == initial["transition_count"] == 0
    assert initial["db_journal_row_chain_verified"] is True
    assert initial["db_journal_complete"] is False
    assert initial["external_event_history_complete"] is False
    assert empty.execution_authority is False

    reserved = await repository.reserve(fixture.request)
    revision = 2
    if state in {"consumed", "uncertain"}:
        await repository.consume_once(
            scope, reserved.original_event_key, expected_revision=revision
        )
        revision += 1
    if state == "uncertain":
        clock.value = fixture.request.origin.deadline + timedelta(days=1)
        await repository.mark_uncertain(
            scope, reserved.original_event_key, expected_revision=revision
        )
        revision += 1
    elif state == "reconciled_flat":
        clock.value += timedelta(seconds=1)
        claims = refresh(fixture.claims, clock.value)
        await repository.reconcile_reservation(
            scope,
            reserved.original_event_key,
            claims=claims,
            expected_revision=revision,
        )
        revision += 1
    clock.value = fixture.request.origin.deadline + timedelta(days=2)
    observed = await repository.inspect_consumed_event_journal(scope)
    record = json.loads(observed.document_json)
    assert observed.consumed_event_keys == frozenset({reserved.original_event_key})
    assert record["event_count"] == 1
    assert record["transition_count"] == revision - 1
    assert record["state_counts"][state] == 1
    assert sum(record["state_counts"].values()) == 1
    assert record["db_journal_row_chain_verified"] is True
    assert record["db_journal_complete"] is False
    assert record["admission"] == "DENY"
    assert observed.execution_authority is False


async def _insert_synthetic_reservation(
    session, fixture, *, scope, with_initial_transition
):
    """Controlled test-only SQL writer; intentionally skips repo reservation rules."""
    raw = _plain(fixture.request)
    raw["scope"] = _plain(scope)
    request = ReservationRequest.model_validate(raw, strict=True)
    coverage, result = prepare_reservation(
        fixture.request, fixture.claims, (), observed_at=fixture.now
    )
    key = request.origin.original_event_key
    rid = reservation_id(scope, key)
    session.add(
        QualificationReservation(
            environment=scope.environment,
            account_id=scope.account_id,
            settlement_currency=scope.settlement_currency,
            reservation_id=rid,
            original_event_key=key,
            report_id=result.report_id,
            instrument_id=result.instrument_id,
            direction=result.direction,
            correlation_group=request.risk_inputs.instrument.correlation_group,
            request_json=canonical(request),
            request_sha256=digest(request),
            coverage_json=canonical(coverage),
            risk_amount=coverage.risk_amount,
            margin_amount=coverage.margin_amount,
            notional_amount=coverage.notional_amount,
            state="reserved",
            state_revision=1,
            deadline=request.origin.deadline,
            created_at=fixture.now,
            updated_at=fixture.now,
        )
    )
    await session.flush()
    if with_initial_transition:
        session.add(
            QualificationReservationTransition(
                reservation_id=rid,
                state_revision=1,
                from_state=None,
                to_state="reserved",
                reason_code="risk_reserved",
                evidence_json=None,
                occurred_at=fixture.now,
            )
        )
        await session.flush()
    return key


async def test_missing_transition_denies_instead_of_publishing_empty(database, fixture):
    repository, _ = await initialize(database, fixture)
    async with database[1]() as session, session.begin():
        await repository._locked(session, fixture.request.scope)
        await _insert_synthetic_reservation(
            session, fixture, scope=fixture.request.scope, with_initial_transition=False
        )
    with pytest.raises(
        QualificationLedgerError, match="event_ledger_transition_incomplete"
    ):
        await repository.inspect_consumed_event_journal(fixture.request.scope)


async def test_post_uid_lock_read_sees_other_currency_writer(database, fixture):
    repository, _ = await initialize(database, fixture)
    requested_scope = fixture.request.scope
    other_scope = LedgerScope(
        account_id=requested_scope.account_id, settlement_currency="USDC"
    )
    async with database[1]() as session, session.begin():
        await repository._locked(session, requested_scope)
        session.add(
            QualificationAccountScope(
                environment=other_scope.environment,
                account_id=other_scope.account_id,
                settlement_currency=other_scope.settlement_currency,
                account_revision=0,
                ledger_revision=0,
            )
        )
        await session.flush()
        key = await _insert_synthetic_reservation(
            session, fixture, scope=other_scope, with_initial_transition=True
        )
        reader = asyncio.create_task(
            repository.inspect_consumed_event_journal(requested_scope)
        )
        await asyncio.sleep(0.05)
        assert not reader.done()
    observed = await reader
    assert observed.consumed_event_keys == frozenset({key})
    record = json.loads(observed.document_json)
    assert record["event_count"] == record["transition_count"] == 1
    assert record["db_journal_row_chain_verified"] is True
    assert record["db_journal_complete"] is False


async def test_temp_shadow_table_cannot_replace_public_ledger(database, fixture):
    repository, clock = await initialize(database, fixture)
    reserved = await repository.reserve(fixture.request)
    async with database[0].connect() as connection:
        await connection.execute(
            text("CREATE TEMP TABLE qualification_reservations (shadow_marker integer)")
        )
        await connection.execute(text("SET search_path TO pg_temp, public"))
        await connection.commit()
        bound = QualificationLedgerRepository(
            async_sessionmaker(connection, expire_on_commit=False, autoflush=False),
            clock=clock,
        )
        observed = await bound.inspect_consumed_event_journal(fixture.request.scope)
        assert observed.consumed_event_keys == frozenset({reserved.original_event_key})
        assert json.loads(observed.document_json)["db_journal_complete"] is False


async def test_session_guard_rejects_shadow_first_search_path(database):
    async with database[1]() as session:
        await session.execute(text("SET LOCAL search_path TO pg_temp, public"))
        try:
            with pytest.raises(
                QualificationLedgerError, match="event_ledger_session_guards_invalid"
            ):
                await _require_event_journal_session(session)
        finally:
            await session.rollback()


async def test_session_guard_rejects_replica_role_when_test_role_allows_it(database):
    async with database[1]() as session:
        await session.execute(
            text("SET LOCAL search_path TO pg_catalog, public, pg_temp")
        )
        try:
            await session.execute(text("SET LOCAL session_replication_role TO replica"))
        except DBAPIError:
            await session.rollback()
            pytest.skip("isolated PostgreSQL role cannot set session_replication_role")
        try:
            with pytest.raises(
                QualificationLedgerError, match="event_ledger_session_guards_invalid"
            ):
                await _require_event_journal_session(session)
        finally:
            await session.rollback()


async def test_schema_guard_rejects_disabled_immutable_transition(database):
    async with database[1]() as session:
        try:
            await session.execute(text("SET LOCAL lock_timeout TO '1000ms'"))
            await session.execute(
                text(
                    "ALTER TABLE public.qualification_reservation_transitions "
                    "DISABLE TRIGGER qualification_transition_immutable"
                )
            )
        except DBAPIError:
            await session.rollback()
            pytest.skip("isolated PostgreSQL role cannot alter test trigger")
        try:
            with pytest.raises(
                QualificationLedgerError, match="event_ledger_schema_retention_invalid"
            ):
                await _require_event_journal_schema(session)
        finally:
            # DDL is transactional; no disabled trigger survives this test.
            await session.rollback()


async def test_schema_guard_rejects_same_name_wrong_uid_unique_key(database):
    async with database[1]() as session:
        try:
            await session.execute(text("SET LOCAL lock_timeout TO '1000ms'"))
            await session.execute(
                text(
                    "ALTER TABLE public.qualification_reservations "
                    "DROP CONSTRAINT uq_qualification_reservations_uid_event"
                )
            )
            await session.execute(
                text(
                    "ALTER TABLE public.qualification_reservations ADD CONSTRAINT "
                    "uq_qualification_reservations_uid_event UNIQUE "
                    "(environment, account_id, settlement_currency, original_event_key)"
                )
            )
        except DBAPIError:
            await session.rollback()
            pytest.skip("isolated PostgreSQL role cannot alter test constraint")
        try:
            with pytest.raises(
                QualificationLedgerError, match="event_ledger_schema_retention_invalid"
            ):
                await _require_event_journal_schema(session)
        finally:
            # Roll back the temporary key change even if the assertion fails.
            await session.rollback()
