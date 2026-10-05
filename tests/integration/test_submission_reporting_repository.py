"""Real isolated PostgreSQL and native filesystem; synthetic exchange bytes only."""

import asyncio
import json
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import DBAPIError

from app.database.models.qualification_ledger import (
    QualificationAccountScope,
    QualificationReservation,
    QualificationReservationTransition,
)
from app.database.models.submission_reporting import (
    QualificationReportProjectionReceipt,
    QualificationReportSpool,
    QualificationSubmissionOutcome,
)
from app.database.repositories.qualification_ledger import QualificationLedgerRepository
from app.database.repositories.submission_reporting import SubmissionReportingRepository
from app.trade_evidence import outbox, storage
from app.trade_evidence.submission_reporting import (
    CapturedSubmission,
    SubmissionReportingError,
)
from app.trade_qualification import reservations
from tests.integration import test_qualification_ledger_repository as fixtures
from tests.unit.qualification_execution_binding_fixtures import execution_binding
from tests.unit.qualification_ledger_fixtures import ledger_fixture

database = fixtures.database
pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
QUEUE = "ctcc-synthetic-submission"


@pytest.fixture
def fixture(request):
    return ledger_fixture(
        getattr(request, "param", "long"),
        account_id=f"123456789{uuid4().int % 10**12:012d}",
        report_id="synthetic-submit-" + uuid4().hex,
    )


@pytest.fixture
def other(fixture):
    return ledger_fixture(
        "short",
        account_id=fixture.request.scope.account_id,
        report_id="synthetic-other-" + uuid4().hex,
    )


async def setup(database, fixture, *, status="acknowledged", pre_reserve_request=None):
    ledger, clock = await fixtures.initialize(database, fixture)
    hold = await ledger.reserve(fixture.request)
    revision = 2
    if pre_reserve_request is not None:
        await ledger.reserve(
            pre_reserve_request.model_copy(
                update={"expected_ledger_revision": revision}
            )
        )
        revision += 1
    intent = await ledger.consume_with_submission_intent(
        hold.scope,
        hold.original_event_key,
        expected_revision=revision,
        execution_binding=execution_binding(fixture),
    )
    body = json.loads(intent.canonical_json)
    clock.value += timedelta(milliseconds=1)
    capture = CapturedSubmission(
        exchange_request_sha256=body["exchange_request_sha256"],
        request_started_at=fixture.now,
        completed_at=clock.value,
    )
    if status != "uncertain":
        captured = {
            "code": "0" if status == "acknowledged" else "1",
            "data": [
                {
                    "sCode": "0" if status == "acknowledged" else "51000",
                    "ordId": "99887766" if status == "acknowledged" else "",
                    "clOrdId": body["client_order_id"],
                    "sMsg": "synthetic-response-must-not-reach-outbox",
                }
            ],
        }
        capture = capture.model_copy(
            update={
                "transport_complete": True,
                "headers_received_at": fixture.now,
                "body_completed_at": clock.value,
                "http_status": 200,
                "raw_body": json.dumps(captured).encode(),
            }
        )
    repository = SubmissionReportingRepository(database[1], clock=clock)
    args = {
        "expected_revision": revision + 1,
        "intent_sha256": intent.sha256,
        "capture": capture,
        "queue_namespace": QUEUE,
        "outbox_policy": outbox.OutboxPolicy(),
    }
    return ledger, repository, clock, hold, args


async def test_atomic_ack_restart_projection_and_idempotent_readback(
    database, fixture, tmp_path
):
    ledger, _repository, clock, hold, args = await setup(database, fixture)
    receipt = await ledger.record_submission_observation(
        hold.scope, hold.original_event_key, **args
    )
    state = await ledger.read_scope(hold.scope)
    assert state.ledger_revision == 4 and state.active[0].state == "consumed"
    assert not receipt.execution_authority and not receipt.order_retry_authority
    restarted = SubmissionReportingRepository(database[1], clock=clock)
    assert (
        await restarted.record_observation(hold.scope, hold.original_event_key, **args)
        == receipt
    )
    assert receipt.spool_id in await restarted.pending_ids(QUEUE)
    competing = SubmissionReportingRepository(database[1], clock=clock)
    projections = await asyncio.gather(
        *(
            repo.project_one(receipt.spool_id, root=tmp_path, queue_namespace=QUEUE)
            for repo in (restarted, competing)
        )
    )
    projected = next(item for item in projections if item is not None)
    assert all(item is None or item == projected for item in projections)
    again = await restarted.project_one(
        receipt.spool_id, root=tmp_path, queue_namespace=QUEUE
    )
    assert again == projected
    assert receipt.spool_id not in await restarted.pending_ids(QUEUE)
    view = outbox.read_job(tmp_path, hold.report_id, clock=clock)
    assert view.status == "queued" and len(view.events) == 1
    assert view.envelope.payload.order_reference.startswith("CTQ")
    assert (
        b"synthetic-response-must-not-reach-outbox"
        not in (tmp_path / (hold.report_id + ".json")).read_bytes()
    )
    async with database[1]() as session:
        saved = await session.get(QualificationSubmissionOutcome, receipt.outcome_id)
        assert json.loads(saved.capture_json)["raw_body_sha256"]
        assert saved.intent_sha256 == args["intent_sha256"]
        assert json.loads(saved.outcome_json)["binding"][
            "protection_client_order_id"
        ].startswith("CTA")


@pytest.mark.parametrize("status", ("rejected", "uncertain"))
async def test_rejected_unknown_retained_but_never_projected(
    database, fixture, tmp_path, status
):
    ledger, repository, clock, hold, args = await setup(
        database, fixture, status=status
    )
    result = await repository.record_observation(
        hold.scope, hold.original_event_key, **args
    )
    assert result.status == status
    assert result.spool_id not in await repository.pending_ids(QUEUE)
    with pytest.raises(SubmissionReportingError, match="not_eligible"):
        await repository.project_one(
            result.spool_id, root=tmp_path, queue_namespace=QUEUE
        )
    assert list(tmp_path.iterdir()) == []
    state = await ledger.read_scope(hold.scope)
    assert state.active[0].state == (
        "uncertain" if status == "uncertain" else "consumed"
    )
    if status == "uncertain":
        async with database[1]() as session:
            transitions = (
                await session.scalars(
                    select(QualificationReservationTransition).filter_by(
                        reservation_id=hold.reservation_id
                    )
                )
            ).all()
        assert (
            len(transitions) == 3
            and transitions[-1].reason_code == "post_submit_uncertain"
        )
        assert (
            json.loads(transitions[-1].evidence_json)["capture_sha256"]
            == result.capture_sha256
        )
    clock.value += timedelta(days=1)
    assert (
        await repository.read_observation(
            hold.scope, hold.original_event_key, expected_outcome_id=result.outcome_id
        )
        == result
    )


async def test_concurrent_identical_reporters_commit_one_outcome(database, fixture):
    _, repository, clock, hold, args = await setup(database, fixture)
    other = SubmissionReportingRepository(database[1], clock=clock)
    one, two = await asyncio.gather(
        *(
            item.record_observation(hold.scope, hold.original_event_key, **args)
            for item in (repository, other)
        )
    )
    assert one == two
    async with database[1]() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(QualificationSubmissionOutcome)
                .filter_by(reservation_id=hold.reservation_id)
            )
            == 1
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(QualificationReportSpool)
                .filter_by(reservation_id=hold.reservation_id)
            )
            == 1
        )
    state = await repository.read_scope(hold.scope)
    assert state.ledger_revision == 4
    conflict = {**args, "queue_namespace": "other-synthetic-queue"}
    with pytest.raises(SubmissionReportingError, match="conflict"):
        await other.record_observation(hold.scope, hold.original_event_key, **conflict)


async def test_commit_readback_failure_retains_outcome_and_spool(
    database, fixture, monkeypatch
):
    _, repository, clock, hold, args = await setup(database, fixture)

    async def lost(*a, **kw):
        raise ConnectionError("synthetic committed readback loss")

    monkeypatch.setattr(repository, "read_observation", lost)
    with pytest.raises(ConnectionError):
        await repository.record_observation(hold.scope, hold.original_event_key, **args)
    restarted = SubmissionReportingRepository(database[1], clock=clock)
    result = await restarted.record_observation(
        hold.scope, hold.original_event_key, **args
    )
    assert result.status == "acknowledged"
    assert (await restarted.read_scope(hold.scope)).ledger_revision == 4


async def test_spool_failure_rolls_back_unknown_trade_transition_and_outcome(
    database, fixture, monkeypatch
):
    _, repository, _, hold, args = await setup(database, fixture, status="uncertain")
    import app.database.repositories.submission_reporting as module

    def failed(*args):
        raise RuntimeError("synthetic before spool insert")

    monkeypatch.setattr(module, "spool_identity", failed)
    with pytest.raises(RuntimeError):
        await repository.record_observation(hold.scope, hold.original_event_key, **args)
    state = await repository.read_scope(hold.scope)
    assert state.ledger_revision == 3 and state.active[0].state == "consumed"
    async with database[1]() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(QualificationSubmissionOutcome)
                .filter_by(reservation_id=hold.reservation_id)
            )
            == 0
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(QualificationReservationTransition)
                .filter_by(reservation_id=hold.reservation_id)
            )
            == 2
        )


async def test_late_file_failure_preserves_journal_spool_and_trade(
    database, fixture, tmp_path, monkeypatch
):
    _, repository, _, hold, args = await setup(database, fixture)
    result = await repository.record_observation(
        hold.scope, hold.original_event_key, **args
    )
    directory = (
        storage._WindowsDirectory
        if __import__("os").name == "nt"
        else storage._PosixDirectory
    )
    original = directory.publish

    def late(self, name, raw):
        if name == hold.report_id + ".json":
            raise OSError("synthetic late envelope refusal")
        return original(self, name, raw)

    monkeypatch.setattr(directory, "publish", late)
    with pytest.raises(SubmissionReportingError, match="enqueue_pending"):
        await repository.project_one(
            result.spool_id, root=tmp_path, queue_namespace=QUEUE
        )
    journal = tmp_path / (hold.report_id + ".state") / "00000001.json"
    assert journal.is_file()
    saved = journal.read_bytes()
    monkeypatch.setattr(directory, "publish", original)
    with pytest.raises(SubmissionReportingError, match="enqueue_pending"):
        await repository.project_one(
            result.spool_id, root=tmp_path, queue_namespace=QUEUE
        )
    assert journal.read_bytes() == saved
    assert result.spool_id in await repository.pending_ids(QUEUE)
    assert (await repository.read_scope(hold.scope)).active[0].state == "consumed"


async def test_projected_envelope_survives_missing_db_receipt_then_replays(
    database, fixture, tmp_path
):
    _, repository, clock, hold, args = await setup(database, fixture)
    result = await repository.record_observation(
        hold.scope, hold.original_event_key, **args
    )
    import app.database.repositories.submission_reporting as module

    original = module.QualificationReportProjectionReceipt
    from sqlalchemy import event

    def before_insert(*a):
        raise ConnectionError("synthetic receipt commit failure")

    event.listen(original, "before_insert", before_insert)
    try:
        with pytest.raises(ConnectionError):
            await repository.project_one(
                result.spool_id, root=tmp_path, queue_namespace=QUEUE
            )
    finally:
        event.remove(original, "before_insert", before_insert)
    saved = (tmp_path / (hold.report_id + ".json")).read_bytes()
    restarted = SubmissionReportingRepository(database[1], clock=clock)
    projected = await restarted.project_one(
        result.spool_id, root=tmp_path, queue_namespace=QUEUE
    )
    assert projected and (tmp_path / (hold.report_id + ".json")).read_bytes() == saved
    assert len(outbox.read_job(tmp_path, hold.report_id, clock=clock).events) == 1


async def test_immutable_rows_cannot_be_changed_or_deleted(database, fixture, tmp_path):
    _, repository, _, hold, args = await setup(database, fixture)
    result = await repository.record_observation(
        hold.scope, hold.original_event_key, **args
    )
    await repository.project_one(result.spool_id, root=tmp_path, queue_namespace=QUEUE)
    cases = (
        (
            QualificationSubmissionOutcome,
            "outcome_id",
            result.outcome_id,
            "status",
            "uncertain",
        ),
        (QualificationReportSpool, "spool_id", result.spool_id, "eligible", False),
        (
            QualificationReportProjectionReceipt,
            "spool_id",
            result.spool_id,
            "envelope_sha256",
            "f" * 64,
        ),
    )
    for model, key, identity, column, value in cases:
        for statement in (
            update(model)
            .where(getattr(model, key) == identity)
            .values(**{column: value}),
            delete(model).where(getattr(model, key) == identity),
        ):
            async with database[1]() as session:
                with pytest.raises(DBAPIError):
                    await session.execute(statement)
                await session.rollback()


async def test_actual_process_crashes_replay_only_local_durable_work(
    database, fixture, tmp_path
):
    import os
    import subprocess
    import sys
    from pathlib import Path

    from app.trade_evidence.submission_reporting import freeze_capture

    _, repository, clock, hold, args = await setup(database, fixture)
    values = {
        "mode": "after_outcome_commit",
        "at": clock.value.isoformat(),
        "capture_json": freeze_capture(args["capture"]),
        "scope": hold.scope.model_dump(mode="json"),
        "event_key": hold.original_event_key,
        "revision": 3,
        "intent_sha256": args["intent_sha256"],
        "namespace": QUEUE,
    }

    async def crash(value):
        return await asyncio.to_thread(
            subprocess.run,
            [
                sys.executable,
                "-m",
                "tests.integration.submission_reporting_crash_worker",
            ],
            input=json.dumps(value).encode(),
            capture_output=True,
            cwd=Path(__file__).parents[2],
            env=dict(os.environ, CTCC_SUBMISSION_CRASH_CHILD="1"),
            timeout=180,
        )

    first = await crash(values)
    assert first.returncode == 73
    result = await repository.record_observation(
        hold.scope, hold.original_event_key, **args
    )
    assert (
        result.status == "acknowledged"
        and (await repository.read_scope(hold.scope)).ledger_revision == 4
    )
    second = await crash(
        {
            "mode": "after_enqueue_before_receipt",
            "at": clock.value.isoformat(),
            "spool_id": result.spool_id,
            "namespace": QUEUE,
            "root": str(tmp_path),
        }
    )
    assert second.returncode == 74
    saved = (tmp_path / (hold.report_id + ".json")).read_bytes()
    assert result.spool_id in await repository.pending_ids(QUEUE)
    await repository.project_one(result.spool_id, root=tmp_path, queue_namespace=QUEUE)
    assert (tmp_path / (hold.report_id + ".json")).read_bytes() == saved
    assert len(outbox.read_job(tmp_path, hold.report_id, clock=clock).events) == 1


@pytest.mark.parametrize("operation", ("reserve", "consume"))
async def test_uncertain_outcome_blocks_concurrent_other_event_in_same_account(
    database, fixture, other, operation
):
    ledger, repository, _, hold, args = await setup(
        database,
        fixture,
        status="uncertain",
        pre_reserve_request=other.request if operation == "consume" else None,
    )
    request = other.request.model_copy(update={"expected_ledger_revision": 3})
    next_revision = 4
    if operation == "consume":
        next_revision = 5
    async with database[1]() as session, session.begin():
        await session.scalar(
            select(QualificationAccountScope)
            .filter_by(account_id=hold.scope.account_id)
            .with_for_update()
        )
        outcome_task = asyncio.create_task(
            repository.record_observation(hold.scope, hold.original_event_key, **args)
        )
        await asyncio.sleep(0.1)
        attempted = asyncio.create_task(
            ledger.reserve(
                request.model_copy(update={"expected_ledger_revision": next_revision})
            )
            if operation == "reserve"
            else ledger.consume_once(
                hold.scope,
                other.request.origin.original_event_key,
                expected_revision=next_revision,
            )
        )
        await asyncio.sleep(0.1)
        assert not outcome_task.done() and not attempted.done()
    result = await outcome_task
    assert result.status == "uncertain"
    with pytest.raises(
        reservations.QualificationLedgerError, match="uncertain_exposure"
    ):
        await attempted
    state = await ledger.read_scope(hold.scope)
    assert state.ledger_revision == next_revision
    assert sum(item.state == "uncertain" for item in state.active) == 1


@pytest.mark.parametrize("operation", ("reserve", "consume"))
async def test_missing_outcome_inhibits_new_entry_after_restart(
    database, fixture, other, operation
):
    ledger, _, clock, hold, args = await setup(
        database,
        fixture,
        pre_reserve_request=other.request if operation == "consume" else None,
    )
    restarted = QualificationLedgerRepository(database[1], clock=clock)
    if operation == "reserve":
        action = restarted.reserve(
            other.request.model_copy(update={"expected_ledger_revision": 3})
        )
    else:
        action = restarted.consume_once(
            hold.scope, other.request.origin.original_event_key, expected_revision=4
        )
    with pytest.raises(
        reservations.QualificationLedgerError, match="unresolved_submission"
    ):
        await action
    assert (await ledger.read_scope(hold.scope)).ledger_revision == args[
        "expected_revision"
    ]


async def test_legacy_consumed_without_intent_also_inhibits_new_entry(
    database, fixture, other
):
    ledger, clock = await fixtures.initialize(database, fixture)
    hold = await ledger.reserve(fixture.request)
    await ledger.consume_once(hold.scope, hold.original_event_key, expected_revision=2)
    restarted = QualificationLedgerRepository(database[1], clock=clock)
    with pytest.raises(
        reservations.QualificationLedgerError, match="unresolved_submission"
    ):
        await restarted.reserve(
            other.request.model_copy(update={"expected_ledger_revision": 3})
        )
    state = await restarted.read_scope(hold.scope)
    assert state.ledger_revision == 3 and state.active[0].state == "consumed"


async def test_advisory_lock_serializes_exact_uid_across_settlement_scopes(database):
    from datetime import UTC, datetime

    repo = QualificationLedgerRepository(database[1], clock=lambda: datetime.now(UTC))
    uid = f"123456789{uuid4().int % 10**12:012d}"
    scope = reservations.LedgerScope(
        environment="demo", account_id=uid, settlement_currency="USDT"
    )
    other = scope.model_copy(update={"settlement_currency": "USDC"})
    different = scope.model_copy(
        update={"account_id": f"987654321{uuid4().int % 10**12:012d}"}
    )

    async def lock(value):
        async with database[1]() as session, session.begin():
            row = await repo._locked(session, value, create=True)
            return row.settlement_currency

    async with database[1]() as session, session.begin():
        await repo._locked(session, scope, create=True)
        waiting = asyncio.create_task(lock(other))
        await asyncio.sleep(0.05)
        assert await asyncio.wait_for(lock(different), 10) == "USDT"
        assert not waiting.done()
    assert await asyncio.wait_for(waiting, 10) == "USDC"


@pytest.mark.parametrize("state", ("reserved", "consumed", "uncertain"))
async def test_other_currency_exposure_denies_entry_but_not_read_reconciliation(
    database, fixture, other, state
):
    import hashlib

    ledger, clock = await fixtures.initialize(database, fixture)
    hold = await ledger.reserve(fixture.request)
    async with database[1]() as session, session.begin():
        original = await session.get(QualificationReservation, hold.reservation_id)
        other_scope = hold.scope.model_copy(update={"settlement_currency": "USDC"})
        await ledger._locked(session, other_scope, create=True)
        data = {
            column.name: getattr(original, column.name)
            for column in QualificationReservation.__table__.columns
        }
        data.update(
            reservation_id=hashlib.sha256(uuid4().bytes).hexdigest(),
            original_event_key=hashlib.sha256(uuid4().bytes).hexdigest(),
            report_id="synthetic-cross-currency",
            settlement_currency="USDC",
            state=state,
        )
        session.add(QualificationReservation(**data))
    # Use an independent event: replaying the original is correctly rejected
    # first by the UID/event tombstone before the currency-exposure check.
    assert other.request.scope == hold.scope
    assert other.request.origin.original_event_key != hold.original_event_key
    with pytest.raises(reservations.QualificationLedgerError, match="cross_currency"):
        await ledger.reserve(
            other.request.model_copy(update={"expected_ledger_revision": 2})
        )
    with pytest.raises(reservations.QualificationLedgerError, match="cross_currency"):
        await ledger.consume_once(
            hold.scope, hold.original_event_key, expected_revision=2
        )
    assert (await ledger.read_scope(hold.scope)).active[0].state == "reserved"
    clock.value += timedelta(seconds=1)
    resolved = await ledger.reconcile_reservation(
        hold.scope,
        hold.original_event_key,
        claims=fixtures.refresh(fixture.claims, clock.value, armed=False),
        expected_revision=2,
    )
    assert resolved.state == "reconciled_flat"
    async with database[1]() as session:
        assert (
            await session.scalar(
                select(QualificationReservation.state).filter_by(
                    account_id=hold.scope.account_id, settlement_currency="USDC"
                )
            )
            == state
        )


@pytest.mark.parametrize(
    "evidence",
    (None, "{}", '{"version":null}', '{"version":"ctcc-demo-submit-intent-v1"}'),
)
async def test_db_trigger_rejects_missing_or_null_v2_intent_version(database, evidence):
    import hashlib
    from datetime import UTC, datetime
    from decimal import Decimal

    now = datetime.now(UTC)
    rid = hashlib.sha256(uuid4().bytes).hexdigest()
    uid = f"123456789{uuid4().int % 10**12:012d}"
    with pytest.raises(DBAPIError, match="submission_outcome_binding_denied"):
        async with database[1]() as session, session.begin():
            session.add(
                QualificationAccountScope(
                    environment="demo",
                    account_id=uid,
                    settlement_currency="USDT",
                    account_revision=0,
                    ledger_revision=1,
                    created_at=now,
                    updated_at=now,
                )
            )
            await session.flush()
            session.add(
                QualificationReservation(
                    reservation_id=rid,
                    environment="demo",
                    account_id=uid,
                    settlement_currency="USDT",
                    original_event_key="a" * 64,
                    report_id="synthetic-null-version",
                    instrument_id="BTC-USDT-SWAP",
                    direction="long",
                    correlation_group="synthetic",
                    request_json="{}",
                    request_sha256="b" * 64,
                    coverage_json="{}",
                    risk_amount=Decimal(1),
                    margin_amount=Decimal(1),
                    notional_amount=Decimal(1),
                    state="consumed",
                    state_revision=2,
                    deadline=now + timedelta(seconds=1),
                    created_at=now,
                    updated_at=now,
                )
            )
            await session.flush()
            transition = QualificationReservationTransition(
                reservation_id=rid,
                state_revision=2,
                from_state="reserved",
                to_state="consumed",
                reason_code="consumed_with_submit_intent",
                evidence_json=evidence,
                occurred_at=now,
            )
            session.add(transition)
            await session.flush()
            session.add(
                QualificationSubmissionOutcome(
                    outcome_id=rid,
                    reservation_id=rid,
                    intent_transition_id=transition.id,
                    sequence=1,
                    observation_kind="initial",
                    status="acknowledged",
                    intent_sha256="c" * 64,
                    exchange_request_sha256="d" * 64,
                    capture_json="{}",
                    capture_sha256="e" * 64,
                    outcome_json="{}",
                    ledger_revision=1,
                    observed_at=now,
                    recorded_at=now,
                )
            )
            await session.flush()
