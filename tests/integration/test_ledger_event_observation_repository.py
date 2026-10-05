"""Real isolated SQL for bounded all-state UID event reads and bound dedup.

Fictional account/market data only. Tests are authored separately from executed
acceptance; no exchange metadata authenticity or account/order IO is implied.
"""

import asyncio
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import event, select

from app.database.models.qualification_ledger import (
    QualificationReservation,
    QualificationReservationTransition,
)
from app.database.repositories import qualification_ledger as repository_module
from app.database.repositories.qualification_ledger import QualificationLedgerRepository
from app.trade_qualification.engine import PortfolioInputs
from app.trade_qualification.reservations import (
    AccountLedgerClaims,
    LedgerScope,
    QualificationLedgerError,
    reservation_id,
)
from app.trade_qualification.service import _plain
from tests.integration import test_control_bound_ledger_repository as controls
from tests.integration import test_qualification_ledger_repository as fixtures
from tests.unit import qualification_ledger_fixtures as source_fixture

database = fixtures.database
fixture = fixtures.fixture
pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.fixture
def currency_pair(fixture):
    """Adversarial alternative claims built BEFORE synthetic G1--G12.

    No passing result/event is edited. This models untrusted alternative
    settlement claims for the same event, not real OKX contract metadata.
    """
    original_inputs = source_fixture.engine_inputs

    def other_currency_inputs(source):
        args = original_inputs(source)
        raw = _plain(args["risk_inputs"])
        raw["account"]["settlement_currency"] = "USDC"
        raw["instrument"]["settlement_currency"] = "USDC"
        args["risk_inputs"] = PortfolioInputs.model_validate(raw, strict=True)
        return args

    def other_scope(**kwargs):
        return LedgerScope(**{**kwargs, "settlement_currency": "USDC"})

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(source_fixture, "engine_inputs", other_currency_inputs)
        patch.setattr(source_fixture, "LedgerScope", other_scope)
        other = source_fixture.ledger_fixture(
            account_id=fixture.request.scope.account_id,
            report_id="synthetic-other-currency-report",
        )
    assert (
        other.request.origin.original_event_key
        == fixture.request.origin.original_event_key
    )
    assert other.request.scope.settlement_currency == "USDC"
    return fixture, other


@pytest.mark.parametrize(
    "state", ["reserved", "consumed", "uncertain", "reconciled_flat"]
)
async def test_exact_all_state_read_keeps_terminal_tombstone_without_revision_change(
    database, fixture, state
):
    repo, clock = await fixtures.initialize(database, fixture)
    receipt = await repo.reserve(fixture.request)
    if state == "consumed":
        receipt = await repo.consume_once(
            receipt.scope, receipt.original_event_key, expected_revision=2
        )
    elif state == "uncertain":
        receipt = await repo.mark_uncertain(
            receipt.scope, receipt.original_event_key, expected_revision=2
        )
    elif state == "reconciled_flat":
        clock.value += timedelta(microseconds=1)
        receipt = await repo.reconcile_reservation(
            receipt.scope,
            receipt.original_event_key,
            claims=fixtures.refresh(fixture.claims, clock.value),
            expected_revision=2,
        )
        assert not (await repo.read_scope(receipt.scope)).active
    before = await repo.read_scope(receipt.scope)
    restarted = QualificationLedgerRepository(database[1], clock=clock)
    observed = await restarted.read_event_observation(
        receipt.scope, receipt.original_event_key
    )
    assert observed.matched == receipt and observed.matched.state == state
    assert observed.account_revision == before.account_revision
    assert observed.ledger_revision == before.ledger_revision
    assert observed.request_started_at <= observed.observed_at <= observed.received_at
    assert observed.monotonic_started_ns <= observed.monotonic_received_ns
    assert observed.admission == "DENY" and not observed.execution_authority
    assert before == await restarted.read_scope(receipt.scope)


async def test_other_requested_currency_cannot_hide_original_uid_event(
    database, fixture
):
    repo, clock = await fixtures.initialize(database, fixture)
    receipt = await repo.reserve(fixture.request)
    clock.value += timedelta(microseconds=1)
    receipt = await repo.reconcile_reservation(
        receipt.scope,
        receipt.original_event_key,
        claims=fixtures.refresh(fixture.claims, clock.value),
        expected_revision=2,
    )
    other = LedgerScope(account_id=receipt.scope.account_id, settlement_currency="USDC")
    await repo.initialize_capture_scope(other)
    result = await repo.read_event_observation(other, receipt.original_event_key)
    assert (
        result.scope == other and result.account_revision == result.ledger_revision == 0
    )
    assert result.matched == receipt and result.matched.state == "reconciled_flat"


async def test_missing_scope_is_error_and_unrecorded_exact_key_is_only_diagnostic_absence(
    database, fixture
):
    repo, _ = await fixtures.initialize(database, fixture)
    existing = await repo.reserve(fixture.request)
    absent = await repo.read_event_observation(existing.scope, "f" * 64)
    assert absent.matched is None and absent.admission == "DENY"
    other = LedgerScope(
        account_id=f"9876{uuid4().int % 10**12:012d}", settlement_currency="USDT"
    )
    with pytest.raises(QualificationLedgerError, match="scope_missing"):
        await repo.read_event_observation(other, existing.original_event_key)
    await repo.initialize_capture_scope(other)
    different_uid = await repo.read_event_observation(
        other, existing.original_event_key
    )
    assert different_uid.matched is None
    assert (
        await repo.read_event_observation(existing.scope, existing.original_event_key)
    ).matched == existing


async def test_query_is_exact_bounded_all_state_and_defers_large_request_json(
    database, fixture
):
    repo, _ = await fixtures.initialize(database, fixture)
    receipt = await repo.reserve(fixture.request)
    captured = []

    def query(_conn, _cursor, statement, parameters, _context, _many):
        if "FROM qualification_reservations" in statement:
            captured.append((statement, parameters))

    event.listen(database[0].sync_engine, "before_cursor_execute", query)
    try:
        await repo.read_event_observation(receipt.scope, receipt.original_event_key)
    finally:
        event.remove(database[0].sync_engine, "before_cursor_execute", query)
    assert len(captured) == 1
    sql, values = captured[0]
    assert "request_json" not in sql and " LIMIT " in sql
    assert "qualification_reservations.original_event_key =" in sql
    assert "qualification_reservations.account_id =" in sql
    assert "qualification_reservations.environment =" in sql
    assert "state !=" not in sql and "state =" not in sql
    assert "settlement_currency =" not in sql
    assert values[-1] == 2


@pytest.mark.parametrize("mutation", ["utc", "monotonic", "monotonic_bool"])
async def test_invalid_measured_read_interval_is_error_not_absence(
    database, fixture, monkeypatch, mutation
):
    repo, clock = await fixtures.initialize(database, fixture)
    receipt = await repo.reserve(fixture.request)
    if mutation == "utc":
        samples = iter((clock.value + timedelta(seconds=1), clock.value, clock.value))
        monkeypatch.setattr(repo, "clock", lambda: next(samples))
    else:
        samples = iter((20, 10) if mutation == "monotonic" else (True, 20))
        monkeypatch.setattr(
            repository_module.time, "monotonic_ns", lambda: next(samples)
        )
    with pytest.raises(QualificationLedgerError, match="event_observation_invalid"):
        await repo.read_event_observation(receipt.scope, receipt.original_event_key)


async def test_uid_lock_covers_read_and_cross_currency_reservation(
    database, currency_pair
):
    original, other = currency_pair
    s = await controls.setup(database, original)
    await s.ledger.reconcile_scope(other.claims, expected_revision=0)
    async with database[1]() as session, session.begin():
        await s.ledger._locked(session, original.request.scope)
        lookup = asyncio.create_task(
            s.ledger.read_event_observation(
                other.request.scope, original.request.origin.original_event_key
            )
        )
        reserving = asyncio.create_task(
            s.ledger.reserve_control_bound(
                other.request, control_expectation=s.expected
            )
        )
        await asyncio.sleep(0.05)
        assert not lookup.done() and not reserving.done()
    observed, receipt = await asyncio.wait_for(asyncio.gather(lookup, reserving), 30)
    assert receipt.scope == other.request.scope
    assert (
        observed.matched is None
        or observed.matched.original_event_key == receipt.original_event_key
    )
    with pytest.raises(QualificationLedgerError, match="uid_event_already_recorded"):
        await s.reserve()


async def test_new_bound_route_rechecks_terminal_cross_currency_after_earlier_absence(
    database, currency_pair
):
    original, other = currency_pair
    s = await controls.setup(database, original)
    await s.ledger.reconcile_scope(other.claims, expected_revision=0)
    absent = await s.ledger.read_event_observation(
        other.request.scope, original.request.origin.original_event_key
    )
    assert absent.matched is None
    receipt = await s.reserve()
    s.clock.value += timedelta(microseconds=1)
    terminal = await s.ledger.reconcile_reservation(
        receipt.scope,
        receipt.original_event_key,
        claims=fixtures.refresh(original.claims, s.clock.value),
        expected_revision=2,
    )
    assert terminal.state == "reconciled_flat"
    with pytest.raises(QualificationLedgerError, match="uid_event_already_recorded"):
        await s.ledger.reserve_control_bound(
            other.request, control_expectation=s.expected
        )
    found = await s.ledger.read_event_observation(
        other.request.scope, receipt.original_event_key
    )
    assert (
        found.matched == terminal
        and found.matched.report_id != other.request.origin.candidate.report_id
    )


async def test_legacy_reserve_cannot_reuse_terminal_uid_event_in_another_currency(
    database, currency_pair
):
    original, other = currency_pair
    repo, clock = await fixtures.initialize(database, original)
    await repo.reconcile_scope(other.claims, expected_revision=0)
    receipt = await repo.reserve(original.request)
    clock.value += timedelta(microseconds=1)
    terminal = await repo.reconcile_reservation(
        receipt.scope,
        receipt.original_event_key,
        claims=fixtures.refresh(original.claims, clock.value),
        expected_revision=2,
    )
    assert terminal.state == "reconciled_flat"
    assert terminal.report_id != other.request.origin.candidate.report_id
    with pytest.raises(QualificationLedgerError, match="event_already_recorded"):
        await repo.reserve(other.request)
    assert (await repo.read_scope(other.request.scope)).active == ()
    found = await repo.read_event_observation(
        other.request.scope, receipt.original_event_key
    )
    assert found.matched == terminal


async def test_concurrent_bound_same_uid_event_different_report_currency_has_exactly_one_winner(
    database, currency_pair
):
    original, other = currency_pair
    s = await controls.setup(database, original)
    await s.ledger.reconcile_scope(other.claims, expected_revision=0)
    # Both candidate claims were independently recomputed before attempting the
    # race; the final transaction must repeat event lookup under the same UID.
    results = await asyncio.wait_for(
        asyncio.gather(
            s.reserve(),
            s.ledger.reserve_control_bound(
                other.request, control_expectation=s.expected
            ),
            return_exceptions=True,
        ),
        40,
    )
    wins = [r for r in results if not isinstance(r, BaseException)]
    failures = [r for r in results if isinstance(r, QualificationLedgerError)]
    assert len(wins) == len(failures) == 1
    assert "uid_event_already_recorded" in str(failures[0])
    assert wins[0].original_event_key == original.request.origin.original_event_key
    async with database[1]() as session:
        rows = (
            await session.scalars(
                select(QualificationReservation).where(
                    QualificationReservation.environment == "demo",
                    QualificationReservation.account_id
                    == original.request.scope.account_id,
                    QualificationReservation.original_event_key
                    == original.request.origin.original_event_key,
                )
            )
        ).all()
        assert len(rows) == 1


async def test_concurrent_legacy_and_bound_reserve_share_uid_event_lock(
    database, currency_pair
):
    original, other = currency_pair
    s = await controls.setup(database, original)
    await s.ledger.reconcile_scope(other.claims, expected_revision=0)
    async with database[1]() as session, session.begin():
        await s.ledger._locked(session, original.request.scope)
        legacy = asyncio.create_task(s.ledger.reserve(original.request))
        bound = asyncio.create_task(
            s.ledger.reserve_control_bound(
                other.request, control_expectation=s.expected
            )
        )
        await asyncio.sleep(0.05)
        assert not legacy.done() and not bound.done()
    results = await asyncio.wait_for(
        asyncio.gather(
            legacy,
            bound,
            return_exceptions=True,
        ),
        40,
    )
    wins = [result for result in results if not isinstance(result, BaseException)]
    failures = [
        result for result in results if isinstance(result, QualificationLedgerError)
    ]
    assert len(wins) == len(failures) == 1
    assert "event_already_recorded" in str(failures[0])
    assert wins[0].original_event_key == original.request.origin.original_event_key
    async with database[1]() as session:
        rows = (
            await session.scalars(
                select(QualificationReservation).where(
                    QualificationReservation.environment == "demo",
                    QualificationReservation.account_id
                    == original.request.scope.account_id,
                    QualificationReservation.original_event_key
                    == original.request.origin.original_event_key,
                )
            )
        ).all()
        assert len(rows) == 1


async def test_legacy_cross_currency_collision_is_reported_not_deduplicated(
    database, fixture
):
    repo, _ = await fixtures.initialize(database, fixture)
    receipt = await repo.reserve(fixture.request)
    raw = _plain(fixture.claims)
    raw["scope"]["settlement_currency"] = "USDC"
    raw["account"]["settlement_currency"] = "USDC"
    other_claims = AccountLedgerClaims.model_validate(raw, strict=True)
    await repo.reconcile_scope(other_claims, expected_revision=0)
    # Isolated malformed legacy evidence: the source request is intentionally
    # not promoted. Presence of two DB rows must produce a collision error.
    async with database[1]() as session, session.begin():
        await repo._locked(session, receipt.scope)
        original = await session.get(QualificationReservation, receipt.reservation_id)
        values = {
            column.name: getattr(original, column.name)
            for column in QualificationReservation.__table__.columns
        }
        values.update(
            settlement_currency="USDC",
            reservation_id=reservation_id(
                other_claims.scope, receipt.original_event_key
            ),
        )
        duplicate = QualificationReservation(**values)
        session.add(duplicate)
        other_row = await repo._locked(session, other_claims.scope)
        other_row.ledger_revision += 1
        await session.flush()
        session.add(
            QualificationReservationTransition(
                reservation_id=duplicate.reservation_id,
                state_revision=1,
                from_state=None,
                to_state="reserved",
                reason_code="synthetic_legacy_collision",
                occurred_at=duplicate.created_at,
            )
        )
    with pytest.raises(QualificationLedgerError, match="uid_event_scope_collision"):
        await repo.read_event_observation(receipt.scope, receipt.original_event_key)
