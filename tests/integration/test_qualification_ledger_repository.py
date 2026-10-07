"""Requires an explicitly supplied isolated PostgreSQL migrated through 0017.

Fictional numeric UID scopes are unique per test and tombstones are NOT cleaned
up. The isolated database is disposed by its harness, never a deployed DB. No
orders/account API or Live repository is involved; real SQL locks/commits only.
"""

import asyncio
import os
from dataclasses import dataclass
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.database.models.qualification_ledger import (
    QualificationAccountScope,
    QualificationReservation,
    QualificationReservationTransition,
)
from app.database.repositories.qualification_ledger import QualificationLedgerRepository
from app.trade_qualification.portfolio import PendingReservation
from app.trade_qualification.reservations import (
    STAMP_FIELDS,
    AccountLedgerClaims,
    QualificationLedgerError,
    ReservationRequest,
    checked,
    digest,
)
from app.trade_qualification.service import _plain
from tests.unit.qualification_ledger_fixtures import ledger_fixture

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@dataclass
class Clock:
    value: object
    calls: int = 0

    def __call__(self):
        self.calls += 1
        return self.value


@pytest.fixture
def fixture(request):
    direction = getattr(request, "param", "long")
    synthetic_uid = f"123456789{uuid4().int % 10**12:012d}"
    return ledger_fixture(direction, account_id=synthetic_uid)


@pytest.fixture
def renamed(fixture):
    return ledger_fixture(
        fixture.request.origin.candidate.direction,
        report_id="synthetic-renamed-ledger-report",
        account_id=fixture.request.scope.account_id,
    )


@pytest.fixture
def latefixture():
    synthetic_uid = f"123456789{uuid4().int % 10**12:012d}"
    return ledger_fixture(account_id=synthetic_uid, quote_max_age_seconds=60)


@pytest.fixture
def database():
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        pytest.skip("explicit isolated DATABASE_URL required")
    # This integration file intentionally has no production/default DB fallback.
    engine = create_async_engine(url, poolclass=NullPool)
    return engine, async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


def refresh(claims, now, *, armed=None, empty=True):
    raw = _plain(claims)
    raw["reconciliation_id"] = uuid4().hex * 2
    for name in STAMP_FIELDS:
        raw["account"][name].update(
            observed_at=now, received_at=now, source_sha256=uuid4().hex * 2
        )
    raw["authority"]["stamp"].update(
        observed_at=now, received_at=now, source_sha256=uuid4().hex * 2
    )
    if armed is not None:
        raw["authority"]["armed"] = armed
    raw["account"]["history_end"] = now
    if empty:
        raw["account"].update(
            positions=(),
            position_count=0,
            pending_reservations=(),
            pending_reservation_count=0,
        )
    return AccountLedgerClaims.model_validate(raw, strict=True)


async def initialize(database, fixture):
    _, sessions = database
    clock = Clock(fixture.now)
    repository = QualificationLedgerRepository(sessions, clock=clock)
    state = await repository.reconcile_scope(fixture.claims, expected_revision=0)
    assert (state.account_revision, state.ledger_revision) == (1, 1)
    return repository, clock


@pytest.mark.parametrize("fixture", ("long", "short"), indirect=True)
async def test_real_sql_durable_two_scenario_hold_consume_once_restart(
    database, fixture
):
    repository, clock = await initialize(database, fixture)
    receipt = await repository.reserve(fixture.request)
    assert receipt.state == "reserved" and receipt.ledger_revision == 2
    assert receipt.execution_authority is False
    restarted = QualificationLedgerRepository(database[1], clock=clock)
    state = await restarted.read_scope(fixture.request.scope)
    assert state.active == (receipt,)
    consumed = await restarted.consume_once(
        fixture.request.scope, receipt.original_event_key, expected_revision=2
    )
    assert consumed.state == "consumed" and consumed.ledger_revision == 3
    with pytest.raises(QualificationLedgerError, match="transition_denied"):
        await restarted.consume_once(
            fixture.request.scope, receipt.original_event_key, expected_revision=3
        )
    clock.value = fixture.request.origin.deadline + timedelta(days=1)
    uncertain = await restarted.mark_uncertain(
        fixture.request.scope, receipt.original_event_key, expected_revision=3
    )
    assert uncertain.state == "uncertain"
    assert len((await restarted.read_scope(fixture.request.scope)).active) == 1
    assert uncertain.coverage == receipt.coverage
    assert uncertain.execution_authority is False


async def test_two_workers_and_renamed_report_cannot_reuse_original_event(
    database, fixture, renamed
):
    repository, clock = await initialize(database, fixture)
    second = QualificationLedgerRepository(database[1], clock=clock)
    results = await asyncio.gather(
        repository.reserve(fixture.request),
        second.reserve(fixture.request),
        return_exceptions=True,
    )
    assert sum(not isinstance(item, Exception) for item in results) == 1
    assert sum(isinstance(item, QualificationLedgerError) for item in results) == 1
    assert (
        renamed.request.origin.original_event_key
        == fixture.request.origin.original_event_key
    )
    request = renamed.request.model_copy(update={"expected_ledger_revision": 2})
    with pytest.raises(QualificationLedgerError, match="event_already_recorded"):
        await repository.reserve(request)
    assert len((await repository.read_scope(fixture.request.scope)).active) == 1


async def test_expiry_is_checked_after_waiting_for_scope_lock(database, fixture):
    repository, clock = await initialize(database, fixture)
    baseline = clock.calls
    async with database[1]() as session, session.begin():
        await session.scalar(
            select(QualificationAccountScope)
            .filter_by(account_id=fixture.request.scope.account_id)
            .with_for_update()
        )
        waiting = asyncio.create_task(repository.reserve(fixture.request))
        await asyncio.sleep(0.05)
        assert not waiting.done() and clock.calls == baseline
        clock.value = fixture.request.origin.deadline
    with pytest.raises(ValueError, match="outside_original_window"):
        await waiting
    state = await repository.read_scope(fixture.request.scope)
    assert state.ledger_revision == 1 and not state.active


async def test_cancel_while_waiting_does_not_create_hold(database, fixture):
    repository, clock = await initialize(database, fixture)
    baseline = clock.calls
    async with database[1]() as session, session.begin():
        await session.scalar(
            select(QualificationAccountScope)
            .filter_by(account_id=fixture.request.scope.account_id)
            .with_for_update()
        )
        waiting = asyncio.create_task(repository.reserve(fixture.request))
        await asyncio.sleep(0.05)
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting
        assert clock.calls == baseline
    state = await repository.read_scope(fixture.request.scope)
    assert state.ledger_revision == 1 and not state.active


async def test_account_revision_and_current_disarm_invalidate_reservation(
    database, fixture
):
    repository, clock = await initialize(database, fixture)
    clock.value += timedelta(milliseconds=1)
    claims = refresh(fixture.claims, clock.value, armed=False)
    await repository.reconcile_scope(claims, expected_revision=1)
    with pytest.raises(QualificationLedgerError, match="revision_conflict"):
        await repository.reserve(fixture.request)
    raw = _plain(fixture.request)
    raw.update(expected_account_revision=2, expected_ledger_revision=2)
    raw["risk_inputs"].update(
        account=_plain(claims.account), authority=_plain(claims.authority)
    )
    with pytest.raises(QualificationLedgerError, match="current_risk_denied"):
        await repository.reserve(ReservationRequest.model_validate(raw, strict=True))
    assert not (await repository.read_scope(fixture.request.scope)).active


async def test_caller_flat_claims_cannot_retire_uncertain_hold_or_tombstone(
    database, fixture
):
    repository, clock = await initialize(database, fixture)
    receipt = await repository.reserve(fixture.request)
    await repository.mark_uncertain(
        fixture.request.scope, receipt.original_event_key, expected_revision=2
    )
    before = await repository.read_scope(fixture.request.scope)
    assert before.active[0].state == "uncertain"
    with pytest.raises(
        QualificationLedgerError, match="post_submit_closure_witness_missing"
    ):
        await repository.reconcile_reservation(
            fixture.request.scope,
            receipt.original_event_key,
            claims=fixture.claims,
            expected_revision=3,
        )
    clock.value += timedelta(seconds=1)
    claims = refresh(fixture.claims, clock.value, armed=False)
    restarted = QualificationLedgerRepository(database[1], clock=clock)
    with pytest.raises(
        QualificationLedgerError, match="post_submit_closure_witness_missing"
    ):
        await restarted.reconcile_reservation(
            fixture.request.scope,
            receipt.original_event_key,
            claims=claims,
            expected_revision=3,
        )
    # A missing/forged caller claim is not even inspected after the durable
    # submitted/uncertain state is found.
    with pytest.raises(
        QualificationLedgerError, match="post_submit_closure_witness_missing"
    ):
        await restarted.reconcile_reservation(
            fixture.request.scope,
            receipt.original_event_key,
            claims=object(),
            expected_revision=3,
        )
    assert await restarted.read_scope(fixture.request.scope) == before
    observed = await restarted.read_event_observation(
        receipt.scope, receipt.original_event_key
    )
    assert observed.matched == before.active[0]
    again = fixture.request.model_copy(update={"expected_ledger_revision": 3})
    with pytest.raises(QualificationLedgerError, match="event_already_recorded"):
        await repository.reserve(again)
    async with database[1]() as session:
        record = await session.get(QualificationReservation, receipt.reservation_id)
        assert record is not None and record.state == "uncertain"
        count = await session.scalar(
            select(func.count())
            .select_from(QualificationReservationTransition)
            .filter_by(reservation_id=receipt.reservation_id)
        )
        assert count == 2


@pytest.mark.parametrize("state", ("consumed", "uncertain"))
async def test_database_rejects_direct_post_submit_flat_transition(
    database, fixture, state
):
    repository, clock = await initialize(database, fixture)
    receipt = await repository.reserve(fixture.request)
    await repository.consume_once(
        receipt.scope, receipt.original_event_key, expected_revision=2
    )
    if state == "uncertain":
        await repository.mark_uncertain(
            receipt.scope, receipt.original_event_key, expected_revision=3
        )
    before = await repository.read_scope(receipt.scope)
    assert before.active[0].state == state
    clock.value += timedelta(seconds=1)
    with pytest.raises(DBAPIError, match="qualification_reservation_update_denied"):
        async with database[1]() as session, session.begin():
            await session.execute(
                update(QualificationReservation)
                .where(
                    QualificationReservation.reservation_id == receipt.reservation_id
                )
                .values(
                    state="reconciled_flat",
                    state_revision=before.active[0].state_revision + 1,
                    updated_at=clock.value,
                )
            )
    assert await repository.read_scope(receipt.scope) == before
    async with database[1]() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(QualificationReservationTransition)
            .filter_by(reservation_id=receipt.reservation_id)
        )
        assert count == (2 if state == "consumed" else 3)
    assert (
        await repository.read_event_observation(
            receipt.scope, receipt.original_event_key
        )
    ).matched == before.active[0]


async def test_failed_journal_insert_rolls_back_reservation_and_revision(
    database, fixture, monkeypatch
):
    repository, _ = await initialize(database, fixture)

    def fail_journal(*args, **kwargs):
        raise RuntimeError("synthetic_journal_failure")

    with monkeypatch.context() as patch:
        patch.setattr(repository, "_journal", fail_journal)
        with pytest.raises(RuntimeError, match="synthetic_journal_failure"):
            await repository.reserve(fixture.request)
    state = await repository.read_scope(fixture.request.scope)
    assert state.ledger_revision == 1 and not state.active
    assert (await repository.reserve(fixture.request)).state == "reserved"


@pytest.mark.parametrize(
    "mutation", ("delete", "risk", "event", "deadline", "transition")
)
async def test_database_itself_protects_tombstones_and_original_operands(
    database, fixture, mutation
):
    repository, _ = await initialize(database, fixture)
    receipt = await repository.reserve(fixture.request)
    if mutation == "delete":
        statement = delete(QualificationReservation).filter_by(
            reservation_id=receipt.reservation_id
        )
    elif mutation == "transition":
        statement = delete(QualificationReservationTransition).filter_by(
            reservation_id=receipt.reservation_id
        )
    else:
        fields = {
            "risk": {"risk_amount": receipt.coverage.risk_amount * 2},
            "event": {"original_event_key": "c" * 64},
            "deadline": {"deadline": receipt.deadline + timedelta(seconds=1)},
        }
        statement = (
            update(QualificationReservation)
            .filter_by(reservation_id=receipt.reservation_id)
            .values(**fields[mutation])
        )
    with pytest.raises(DBAPIError):
        async with database[1]() as session, session.begin():
            await session.execute(statement)
    assert (
        checked(
            (await repository.read_scope(fixture.request.scope)).active[0],
            type(receipt),
        )
        == receipt
    )


@pytest.mark.parametrize("stage", ("reserve", "consume"))
@pytest.mark.parametrize("late", ("expiry", "quote", "account", "regression"))
async def test_late_clock_failure_rolls_back_after_work_and_flush(
    database, latefixture, stage, late
):
    fixture = latefixture
    repository, _ = await initialize(database, fixture)
    if stage == "consume":
        await repository.reserve(fixture.request)
    now = fixture.now
    target = {
        "expiry": fixture.request.origin.deadline,
        "quote": now + timedelta(seconds=61),
        "account": now + timedelta(seconds=31),
        "regression": now - timedelta(microseconds=1),
    }[late]
    moments = iter((now, target))
    repository.clock = lambda: next(moments)
    expected = {
        "expiry": "ledger_late_expiry",
        "quote": "ledger_late_quote_stale",
        "account": "ledger_late_account_stale",
        "regression": "ledger_clock_regressed",
    }[late]
    with pytest.raises(QualificationLedgerError, match=expected):
        if stage == "reserve":
            await repository.reserve(fixture.request)
        else:
            await repository.consume_once(
                fixture.request.scope,
                fixture.request.origin.original_event_key,
                expected_revision=2,
            )
    state = await repository.read_scope(fixture.request.scope)
    if stage == "reserve":
        assert state.ledger_revision == 1 and not state.active
    else:
        assert state.ledger_revision == 2 and state.active[0].state == "reserved"
    async with database[1]() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(QualificationReservationTransition)
            .join(
                QualificationReservation,
                QualificationReservation.reservation_id
                == QualificationReservationTransition.reservation_id,
            )
            .where(
                QualificationReservation.account_id == fixture.request.scope.account_id
            )
        )
        assert count == (0 if stage == "reserve" else 1)


async def test_duplicate_current_self_holds_cannot_be_stored_then_laundered(
    database, fixture
):
    repository, clock = await initialize(database, fixture)
    receipt = await repository.reserve(fixture.request)
    pending = PendingReservation(
        reservation_id=receipt.reservation_id,
        instrument_id=receipt.instrument_id,
        direction=receipt.direction,
        settlement_currency=receipt.scope.settlement_currency,
        notional=receipt.coverage.notional_amount,
        margin=receipt.coverage.margin_amount,
        risk_amount=receipt.coverage.risk_amount,
        correlation_group=receipt.correlation_group,
    )
    clock.value += timedelta(milliseconds=1)
    claims = refresh(fixture.claims, clock.value)
    raw = _plain(claims)
    raw["account"].update(
        pending_reservations=(_plain(pending), _plain(pending)),
        pending_reservation_count=2,
    )
    malformed = AccountLedgerClaims.model_validate(raw, strict=True)
    with pytest.raises(QualificationLedgerError, match="duplicate_claimed_reservation"):
        await repository.reconcile_scope(malformed, expected_revision=2)
    state = await repository.read_scope(fixture.request.scope)
    assert state.account_revision == 1 and state.ledger_revision == 2
    assert state.active[0].state == "reserved"


async def test_capture_checkpoint_reads_persisted_revision_and_keeps_unresolved_hold(
    database, fixture
):
    repository, clock = await initialize(database, fixture)
    first = await repository.read_capture_checkpoint(fixture.request.scope)
    assert first.state_sha256 == digest(first.state)
    assert first.state.account_revision == first.state.ledger_revision == 1
    assert first.observed_at == first.received_at == fixture.now
    reserved = await repository.reserve(fixture.request)
    restarted = QualificationLedgerRepository(database[1], clock=clock)
    second = await restarted.read_capture_checkpoint(fixture.request.scope)
    assert second.state_sha256 != first.state_sha256
    assert second.state.active == (reserved,)
    assert second.state.ledger_revision == 2
    # A checkpoint is read-only; it cannot advance claims, release holds or arm.
    third = await restarted.read_capture_checkpoint(fixture.request.scope)
    assert third.state_sha256 == second.state_sha256
    assert third.state.execution_authority is False


@pytest.mark.parametrize("problem", ("clock", "claims_hash", "claims_scope"))
async def test_capture_checkpoint_denies_bad_clock_and_persisted_claims(
    database, fixture, problem
):
    repository, clock = await initialize(database, fixture)
    if problem == "clock":
        clock.value -= timedelta(seconds=1)
        match = "ledger_clock_regressed"
    else:
        raw_claims = _plain(fixture.claims)
        if problem == "claims_scope":
            raw_claims["scope"]["account_id"] = "999999"
        claims = AccountLedgerClaims.model_validate(raw_claims, strict=True)
        from app.trade_qualification.reservations import canonical

        async with database[1]() as session, session.begin():
            await session.execute(
                update(QualificationAccountScope)
                .where(
                    QualificationAccountScope.account_id
                    == fixture.request.scope.account_id
                )
                .values(
                    # Respect the DB0017 revision guard. This simulates corrupt
                    # producer evidence, not a bypass of immutable SQL rules.
                    account_revision=2,
                    ledger_revision=2,
                    claims_json=canonical(claims),
                    claims_sha256=(
                        "f" * 64 if problem == "claims_hash" else digest(claims)
                    ),
                )
            )
        match = (
            "ledger_claim_digest_mismatch"
            if problem == "claims_hash"
            else "ledger_claim_scope_mismatch"
        )
    with pytest.raises(QualificationLedgerError, match=match):
        await repository.read_capture_checkpoint(fixture.request.scope)
