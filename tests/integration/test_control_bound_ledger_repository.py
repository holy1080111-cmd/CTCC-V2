"""Real isolated PostgreSQL control/hold/intent transactions, synthetic UID only.

No exchange transport, credential lookup, runtime Arm or dispatch is invoked.
Durable tombstones are retained; the harness owns the disposable test database.
"""

import asyncio
import json
from dataclasses import dataclass, replace
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.database.models.qualification_ledger import (
    QualificationReservation,
    QualificationReservationTransition,
)
from app.database.repositories.demo_control import DemoControlRepository
from app.database.repositories.qualification_ledger import QualificationLedgerRepository
from app.trade_qualification import control_bound_ledger as bound
from app.trade_qualification import demo_control as control
from app.trade_qualification.reservations import (
    QualificationLedgerError,
    ReservationReceipt,
    checked_reservation_request,
    reservation_id,
)
from app.trade_qualification.submission_intent import build_submission_intent
from tests.integration import test_qualification_ledger_repository as fixtures
from tests.unit.qualification_execution_binding_fixtures import execution_binding
from tests.unit.qualification_range_v5_fixtures import range_v5_ledger_fixture

database = fixtures.database
fixture = fixtures.fixture
pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
TOKEN = b"T" * 32


def command():
    return uuid4().hex * 2


def expectation(observed):
    state = observed.state
    return bound.ControlExpectation(
        state.scope,
        TOKEN,
        state.owner_epoch,
        state.revision,
        observed.event_sha256,
        state.pins,
    )


@dataclass
class Setup:
    ledger: object
    control: object
    fixture: object
    clock: object
    observed: object
    binding: object

    @property
    def expected(self):
        return expectation(self.observed)

    async def reserve(self):
        return await self.ledger.reserve_control_bound(
            self.fixture.request,
            control_expectation=self.expected,
        )

    async def consume(self, *, expected=None, revision=2):
        return await self.ledger.consume_with_control_bound_submission_intent(
            self.fixture.request.scope,
            self.fixture.request.origin.original_event_key,
            expected_revision=revision,
            control_expectation=self.expected if expected is None else expected,
            execution_binding=self.binding,
        )


async def setup(database, fixture, *, session_sha256=None):
    ledger, clock = await fixtures.initialize(database, fixture)
    repo = DemoControlRepository(database[1], clock=clock)
    scope = control.ControlScope("demo", fixture.request.scope.account_id)
    binding = execution_binding(fixture)
    session_id = json.loads(binding.account_packet_json)["plan"]["session_binding_id"]
    pins = control.ControlPins(
        "a" * 64,
        fixture.request.origin.original_policy_sha256,
        control.digest(session_id) if session_sha256 is None else session_sha256,
    )
    first = await repo.acquire(
        scope, owner_token=TOKEN, pins=pins, command_id=command()
    )
    armed = await repo.change(
        scope,
        owner_token=TOKEN,
        owner_epoch=first.state.owner_epoch,
        expected_revision=first.state.revision,
        action="arm_requested",
        command_id=command(),
        arm_expires_at=clock() + timedelta(seconds=20),
    )
    return Setup(ledger, repo, fixture, clock, armed, binding)


async def journal(database, receipt, revision):
    async with database[1]() as session:
        return await session.scalar(
            select(QualificationReservationTransition).filter_by(
                reservation_id=receipt.reservation_id,
                state_revision=revision,
            )
        )


async def test_control_bound_exact_inner_v2_replay_build_bytes_and_no_authority(
    database, fixture
):
    s = await setup(database, fixture)
    receipt = await asyncio.wait_for(s.reserve(), 20)
    reserved = await journal(database, receipt, 1)
    assert reserved.reason_code == bound.RESERVED_REASON
    assert (
        bound.replay_reserved_evidence(reserved.evidence_json, fixture.request)[0]
        == receipt
    )
    intent = await asyncio.wait_for(s.consume(), 30)
    body = json.loads(intent.canonical_json)
    replayed, consumed = bound.replay_control_bound_intent(
        intent.canonical_json,
        fixture.request,
        expected_sha256=intent.sha256,
    )
    original = build_submission_intent(
        fixture.request, consumed, execution_binding=s.binding
    )
    assert body["intent_json"].encode() == original.canonical_json.encode()
    assert replayed == intent and intent.admission == "DENY"
    assert not intent.execution_authority and not intent.order_retry_authority
    for name in (
        "owned_policy_verified",
        "account_complete",
        "source_authenticity_verified",
    ):
        assert body[name] is False
    assert TOKEN.decode() not in reserved.evidence_json + intent.canonical_json
    assert TOKEN.hex() not in reserved.evidence_json + intent.canonical_json


async def test_control_bound_committed_intent_stop_restart_historical_readback(
    database, fixture
):
    s = await setup(database, fixture)
    receipt = await asyncio.wait_for(s.reserve(), 20)
    reserved = await journal(database, receipt, 1)
    assert reserved.reason_code == bound.RESERVED_REASON
    intent = await asyncio.wait_for(s.consume(), 30)
    assert TOKEN.decode() not in reserved.evidence_json + intent.canonical_json
    assert TOKEN.hex() not in reserved.evidence_json + intent.canonical_json
    await s.control.latch_stop(s.observed.state.scope, command_id=command())
    restarted = QualificationLedgerRepository(database[1], clock=s.clock)
    assert (
        await restarted.read_control_bound_submission_intent(
            receipt.scope,
            receipt.original_event_key,
            expected_sha256=intent.sha256,
        )
        == intent
    )


@pytest.mark.parametrize("route", ["consume_once", "consume_with_submission_intent"])
async def test_legacy_routes_cannot_consume_bound_hold(database, fixture, route):
    s = await setup(database, fixture)
    receipt = await s.reserve()
    with pytest.raises(
        QualificationLedgerError, match="bound_control_consume_required"
    ):
        await getattr(s.ledger, route)(
            receipt.scope, receipt.original_event_key, expected_revision=2
        )
    state = await s.ledger.read_scope(receipt.scope)
    assert state.active == (receipt,) and state.ledger_revision == 2
    assert await journal(database, receipt, 2) is None


async def test_control_path_cannot_promote_legacy_hold_or_read_as_legacy(
    database, fixture
):
    s = await setup(database, fixture)
    receipt = await s.ledger.reserve(fixture.request)
    with pytest.raises(
        QualificationLedgerError, match="bound_control_reservation_required"
    ):
        await s.consume()
    assert (await s.ledger.read_scope(receipt.scope)).active == (receipt,)


async def test_legacy_reader_cannot_read_new_envelope(database, fixture):
    s = await setup(database, fixture)
    receipt = await s.reserve()
    intent = await s.consume()
    with pytest.raises(QualificationLedgerError, match="submit_intent_missing"):
        await s.ledger.read_submission_intent(
            receipt.scope, receipt.original_event_key, expected_sha256=intent.sha256
        )


async def test_same_uid_event_reservation_race_has_one_durable_winner(
    database, fixture
):
    s = await setup(database, fixture)
    results = await asyncio.wait_for(
        asyncio.gather(s.reserve(), s.reserve(), return_exceptions=True), 30
    )
    assert sum(type(r) is ReservationReceipt for r in results) == 1
    assert sum(isinstance(r, QualificationLedgerError) for r in results) == 1
    state = await s.ledger.read_scope(fixture.request.scope)
    assert len(state.active) == 1 and state.ledger_revision == 2


async def test_same_uid_intent_race_has_one_durable_winner(database, fixture):
    s = await setup(database, fixture)
    receipt = await s.reserve()
    results = await asyncio.wait_for(
        asyncio.gather(s.consume(), s.consume(), return_exceptions=True), 40
    )
    assert (
        sum(type(r) is bound.ControlBoundSubmissionIntentRecord for r in results) == 1
    )
    assert sum(isinstance(r, QualificationLedgerError) for r in results) == 1
    assert (await s.ledger.read_scope(receipt.scope)).ledger_revision == 3
    assert (await journal(database, receipt, 2)).reason_code == bound.CONSUMED_REASON


@pytest.mark.parametrize("mutation", ["stop", "disarm", "renew", "session", "config"])
async def test_control_change_between_reserve_consume_denies_even_fresh_expectation(
    database, fixture, mutation
):
    s = await setup(database, fixture)
    receipt = await s.reserve()
    scope = s.observed.state.scope
    if mutation == "stop":
        newer = await s.control.latch_stop(scope, command_id=command())
    elif mutation == "disarm":
        newer = await s.control.revoke_arm(scope, command_id=command())
    else:
        kwargs = {}
        if mutation != "renew":
            name = (
                "credential_session_sha256"
                if mutation == "session"
                else "config_sha256"
            )
            kwargs["pins"] = replace(s.observed.state.pins, **{name: "d" * 64})
        newer = await s.control.change(
            scope,
            owner_token=TOKEN,
            owner_epoch=1,
            expected_revision=2,
            action="renew" if mutation == "renew" else "rebind",
            command_id=command(),
            **kwargs,
        )
    for expected in (s.expected, expectation(newer)):
        with pytest.raises(QualificationLedgerError, match="bound_control_"):
            await s.consume(expected=expected)
    assert (await s.ledger.read_scope(receipt.scope)).active == (receipt,)
    assert await journal(database, receipt, 2) is None


@pytest.mark.parametrize("stage", ["reserve", "consume"])
async def test_wrong_expected_session_is_not_authority(database, fixture, stage):
    s = await setup(database, fixture)
    bad = replace(
        s.expected, pins=replace(s.expected.pins, credential_session_sha256="c" * 64)
    )
    if stage == "consume":
        receipt = await s.reserve()
    with pytest.raises(QualificationLedgerError, match="identity_or_revision_conflict"):
        if stage == "consume":
            await s.consume(expected=bad)
        else:
            await s.ledger.reserve_control_bound(
                fixture.request, control_expectation=bad
            )
    state = await s.ledger.read_scope(fixture.request.scope)
    assert state.active == ((receipt,) if stage == "consume" else ())


@pytest.mark.parametrize("stage", ["reserve", "consume"])
async def test_arm_expiring_after_flush_rolls_back_same_transaction(
    database, fixture, monkeypatch, stage
):
    s = await setup(database, fixture)
    receipt = await s.reserve() if stage == "consume" else None
    original = s.ledger._late_guard

    def expire_after_original(*args):
        original(*args)
        s.clock.value = s.observed.state.arm_expires_at

    monkeypatch.setattr(s.ledger, "_late_guard", expire_after_original)
    with pytest.raises(QualificationLedgerError, match="stopped_disarmed_or_expired"):
        await (s.consume() if stage == "consume" else s.reserve())
    state = await s.ledger.read_scope(fixture.request.scope)
    assert state.active == ((receipt,) if receipt else ())
    assert state.ledger_revision == (2 if receipt else 1)
    if receipt:
        assert await journal(database, receipt, 2) is None


async def test_stop_serializes_after_locked_consume_then_historical_intent_remains_denied(
    database, fixture, monkeypatch
):
    s = await setup(database, fixture)
    receipt = await s.reserve()
    locked, proceed = asyncio.Event(), asyncio.Event()
    original = s.ledger._control_locked

    async def pause_after_locks(*args, **kwargs):
        result = await original(*args, **kwargs)
        locked.set()
        await proceed.wait()
        return result

    monkeypatch.setattr(s.ledger, "_control_locked", pause_after_locks)
    consuming = asyncio.create_task(s.consume())
    await asyncio.wait_for(locked.wait(), 10)
    stopping = asyncio.create_task(
        s.control.latch_stop(s.observed.state.scope, command_id=command())
    )
    try:
        await asyncio.sleep(0.05)
        assert not stopping.done()  # Same UID lock: stop cannot mutate locked control.
    finally:
        proceed.set()
    intent, stopped = await asyncio.wait_for(asyncio.gather(consuming, stopping), 30)
    assert stopped.state.emergency_stop
    assert not intent.execution_authority
    assert (
        await s.ledger.read_control_bound_submission_intent(
            receipt.scope, receipt.original_event_key, expected_sha256=intent.sha256
        )
        == intent
    )
    with pytest.raises(QualificationLedgerError, match="bound_control_"):
        await s.consume(revision=3, expected=expectation(stopped))


async def test_stop_committed_before_control_read_cannot_consume(database, fixture):
    s = await setup(database, fixture)
    receipt = await s.reserve()
    await s.control.latch_stop(s.observed.state.scope, command_id=command())
    with pytest.raises(QualificationLedgerError, match="bound_control_"):
        await s.consume()
    assert (await s.ledger.read_scope(receipt.scope)).active == (receipt,)


async def test_unknown_commit_readback_preserves_intent_and_uncertain_hold_after_restart(
    database, fixture
):
    s = await setup(database, fixture)
    receipt = await s.reserve()

    class LostReadback(QualificationLedgerRepository):
        async def read_control_bound_submission_intent(self, *args, **kwargs):
            raise QualificationLedgerError("synthetic_readback_ack_lost")

    s.ledger = LostReadback(database[1], clock=s.clock)
    with pytest.raises(QualificationLedgerError, match="synthetic_readback_ack_lost"):
        await s.consume()
    stored = await journal(database, receipt, 2)
    assert stored.reason_code == bound.CONSUMED_REASON
    expected_sha = control.digest(stored.evidence_json)
    restarted = QualificationLedgerRepository(database[1], clock=s.clock)
    uncertain = await restarted.mark_uncertain(
        receipt.scope, receipt.original_event_key, expected_revision=3
    )
    assert uncertain.state == "uncertain" and uncertain.coverage == receipt.coverage
    await s.control.latch_stop(s.observed.state.scope, command_id=command())
    s.clock.value = fixture.request.origin.deadline + timedelta(days=1)
    intent = await restarted.read_control_bound_submission_intent(
        receipt.scope, receipt.original_event_key, expected_sha256=expected_sha
    )
    assert (
        intent.canonical_json == stored.evidence_json
        and not intent.order_retry_authority
    )
    assert (await restarted.read_scope(receipt.scope)).active == (uncertain,)
    assert (await journal(database, receipt, 2)).evidence_json == stored.evidence_json


async def test_transaction_helper_requires_existing_transaction(database, fixture):
    scope = control.ControlScope("demo", fixture.request.scope.account_id)
    async with database[1]() as session:
        with pytest.raises(
            control.DemoControlError, match="active_transaction_required"
        ):
            await DemoControlRepository.read_in_transaction(session, scope)


async def test_missing_control_cannot_materialize_hold(database, fixture):
    repo, _ = await fixtures.initialize(database, fixture)
    pins = control.ControlPins(
        "a" * 64, fixture.request.origin.original_policy_sha256, "b" * 64
    )
    expected = bound.ControlExpectation(
        control.ControlScope("demo", fixture.request.scope.account_id),
        TOKEN,
        1,
        2,
        "c" * 64,
        pins,
    )
    with pytest.raises(control.DemoControlError, match="control_scope_missing"):
        await repo.reserve_control_bound(fixture.request, control_expectation=expected)
    assert (await repo.read_scope(fixture.request.scope)).active == ()


@pytest.fixture(params=("long", "short"))
def range_chain(request, monkeypatch):
    return range_v5_ledger_fixture(
        request.param, monkeypatch, account_id=f"123456789{uuid4().int % 10**12:012d}"
    )[:2]


async def test_range_v5_original_current_replay_is_preserved_by_control_envelope(
    database, range_chain
):
    fixture, binding = range_chain
    s = await setup(database, fixture)
    receipt = await s.reserve()
    intent = await s.ledger.consume_with_control_bound_submission_intent(
        receipt.scope,
        receipt.original_event_key,
        expected_revision=2,
        control_expectation=s.expected,
        execution_binding=binding,
    )
    _, consumed = bound.replay_control_bound_intent(
        intent.canonical_json, fixture.request, expected_sha256=intent.sha256
    )
    inner = build_submission_intent(
        fixture.request, consumed, execution_binding=binding
    )
    assert json.loads(intent.canonical_json)["intent_json"] == inner.canonical_json
    assert intent.admission == "DENY" and not intent.execution_authority


async def test_account_packet_session_mismatch_rolls_back_intent_and_retains_hold(
    database, fixture
):
    s = await setup(database, fixture, session_sha256="e" * 64)
    receipt = await s.reserve()
    with pytest.raises(QualificationLedgerError, match="account_session_conflict"):
        await s.consume()
    assert (await s.ledger.read_scope(receipt.scope)).active == (receipt,)
    assert await journal(database, receipt, 2) is None


async def test_range_v5_packet_session_is_also_verified_before_reserve(
    database, range_chain
):
    fixture, _ = range_chain
    s = await setup(database, fixture, session_sha256="e" * 64)
    with pytest.raises(QualificationLedgerError, match="account_session_conflict"):
        await s.reserve()
    state = await s.ledger.read_scope(fixture.request.scope)
    assert not state.active and state.ledger_revision == 1


@pytest.mark.parametrize("change", ("old_barrier", "future_completion"))
async def test_range_v5_stale_account_packet_cannot_create_event_hold(
    database, range_chain, change
):
    fixture, _ = range_chain
    s = await setup(database, fixture)
    altered = execution_binding(
        fixture,
        **(
            {
                "account_barrier": fixture.request.origin.publication_completed_at
                - timedelta(seconds=1)
            }
            if change == "old_barrier"
            else {"account_start_delay": timedelta(seconds=2)}
        ),
    )
    changed_binding = fixture.request.replay_binding.model_copy(
        update={
            name: getattr(altered, name)
            for name in (
                "account_packet_json",
                "account_packet_sha256",
                "account_plan_sha256",
            )
        }
    )
    request = checked_reservation_request(
        fixture.request.model_copy(update={"replay_binding": changed_binding})
    )
    with pytest.raises(
        QualificationLedgerError, match="bound_control_account_packet_causality_invalid"
    ):
        await s.ledger.reserve_control_bound(request, control_expectation=s.expected)
    state = await s.ledger.read_scope(request.scope)
    assert not state.active and state.ledger_revision == 1
    rid = reservation_id(request.scope, request.origin.original_event_key)
    async with database[1]() as session:
        assert await session.get(QualificationReservation, rid) is None
        assert (
            await session.scalar(
                select(QualificationReservationTransition).filter_by(reservation_id=rid)
            )
            is None
        )
