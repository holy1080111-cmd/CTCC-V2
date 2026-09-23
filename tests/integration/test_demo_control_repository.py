"""Real isolated PostgreSQL DB0019 tests; no SQLite/mock database substitute.

Synthetic unique UIDs and tokens are retained in the isolated test database.
No exchange, service restart, legacy Arm, or deployed database is involved.
"""

import asyncio
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import DBAPIError

from app.database.models.demo_control import DemoAccountControl, DemoControlJournal
from app.database.repositories.demo_control import (
    ControlObservation,
    DemoControlRepository,
)
from app.trade_qualification import demo_control as c
from tests.integration import test_qualification_ledger_repository as fixtures
from tests.unit.test_demo_control import PINS, Clock

database = fixtures.database
pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
TOKEN = b"synthetic-owner-token-for-test-01"[:32]
OTHER = b"synthetic-owner-token-for-test-02"[-32:]


@pytest.fixture
def setup(database):
    assert len(TOKEN) == len(OTHER) == 32
    scope = c.ControlScope("demo", f"456789{uuid4().int % 10**14:014d}")
    clock = Clock()
    return DemoControlRepository(database[1], clock=clock), scope, clock


def command():
    return uuid4().hex * 2


async def acquire(setup):
    repo, scope, _ = setup
    return await repo.acquire(scope, owner_token=TOKEN, pins=PINS, command_id=command())


async def change(setup, observation, action, **kwargs):
    repo, scope, _ = setup
    return await repo.change(
        scope,
        owner_token=TOKEN,
        owner_epoch=observation.state.owner_epoch,
        expected_revision=observation.state.revision,
        action=action,
        command_id=command(),
        **kwargs,
    )


async def test_acquire_commit_independent_readback_journal_and_no_authority(
    database, setup
):
    observed = await acquire(setup)
    _repo, scope, clock = setup
    restarted_reader = DemoControlRepository(database[1], clock=clock)
    assert await restarted_reader.read(scope) == observed
    assert observed.state.owner_epoch == observed.state.revision == 1
    assert not observed.execution_authority and observed.state.arm_request_id is None
    async with database[1]() as session:
        event = await session.get(
            DemoControlJournal, (scope.environment, scope.account_id, 1)
        )
        assert event.event_sha256 == observed.event_sha256
        assert TOKEN.hex() not in event.event_json
        assert c.owner_binding(TOKEN) in event.event_json


async def test_two_concurrent_owners_only_one_acquires(setup):
    repo, scope, _ = setup
    results = await asyncio.gather(
        *(
            repo.acquire(scope, owner_token=token, pins=PINS, command_id=command())
            for token in (TOKEN, OTHER)
        ),
        return_exceptions=True,
    )
    assert sum(isinstance(result, ControlObservation) for result in results) == 1
    assert sum(isinstance(result, c.DemoControlError) for result in results) == 1
    assert (await repo.read(scope)).state.revision == 1


async def test_expired_new_owner_cannot_inherit_arm_and_old_owner_is_fenced(setup):
    initial = await acquire(setup)
    repo, scope, clock = setup
    armed = await change(
        setup, initial, "arm_requested", arm_expires_at=clock() + timedelta(seconds=20)
    )
    clock.value = armed.state.lease_until
    newer = await repo.acquire(
        scope, owner_token=OTHER, pins=PINS, command_id=command()
    )
    assert newer.state.owner_epoch == 2 and newer.state.arm_request_id is None
    with pytest.raises(c.DemoControlError, match="owner_or_revision_conflict"):
        await change(setup, newer, "renew")
    assert await repo.read(scope) == newer


async def test_concurrent_arm_cannot_discard_estop_due_to_old_revision(setup):
    initial = await acquire(setup)
    repo, scope, clock = setup
    await asyncio.gather(
        change(
            setup,
            initial,
            "arm_requested",
            arm_expires_at=clock() + timedelta(seconds=20),
        ),
        repo.latch_stop(scope, command_id=command()),
        return_exceptions=True,
    )
    observed = await repo.read(scope)
    assert observed.state.emergency_stop and observed.state.arm_request_id is None
    clock.value = observed.state.lease_until
    newer = await repo.acquire(
        scope, owner_token=OTHER, pins=PINS, command_id=command()
    )
    assert newer.state.emergency_stop and newer.state.arm_request_id is None
    with pytest.raises(c.DemoControlError, match="producer_unavailable"):
        await repo.clear_stop(scope, passed=True)


async def test_control_uses_the_same_uid_advisory_lock_before_row_lock(database, setup):
    repo, scope, _ = setup
    async with database[1]() as session, session.begin():
        await session.execute(
            select(func.pg_advisory_xact_lock(c.account_lock_key(scope)))
        )
        pending = asyncio.create_task(acquire(setup))
        await asyncio.sleep(0.05)
        assert not pending.done()
        other_scope = c.ControlScope("demo", scope.account_id + "1")
        other = await asyncio.wait_for(
            repo.acquire(
                other_scope, owner_token=OTHER, pins=PINS, command_id=command()
            ),
            5,
        )
        assert other.state.revision == 1
    assert (await asyncio.wait_for(pending, 5)).state.revision == 1


async def test_unknown_commit_ack_does_not_roll_back_durable_owner_or_grant_permission(
    database, setup
):
    _, scope, clock = setup

    class LostReadback(DemoControlRepository):
        async def _readback(self, *args):
            raise c.DemoControlError("synthetic_lost_ack")

    repo = LostReadback(database[1], clock=clock)
    with pytest.raises(c.DemoControlError, match="lost_ack"):
        await repo.acquire(scope, owner_token=TOKEN, pins=PINS, command_id=command())
    result = await DemoControlRepository(database[1], clock=clock).read(scope)
    assert result.state.revision == 1 and not result.execution_authority
    with pytest.raises(c.DemoControlError, match="owner_conflict"):
        await repo.acquire(scope, owner_token=OTHER, pins=PINS, command_id=command())


async def test_duplicate_command_rolls_back_state_and_journal(setup):
    initial = await acquire(setup)
    repo, scope, _ = setup
    repeated = command()
    first = await repo.change(
        scope,
        owner_token=TOKEN,
        owner_epoch=1,
        expected_revision=1,
        action="renew",
        command_id=repeated,
    )
    with pytest.raises(DBAPIError):
        await repo.change(
            scope,
            owner_token=TOKEN,
            owner_epoch=1,
            expected_revision=first.state.revision,
            action="renew",
            command_id=repeated,
        )
    assert await repo.read(scope) == first
    assert initial.state.revision == 1


async def test_deferred_guard_rejects_control_commit_without_matching_journal(
    database, setup
):
    initial = await acquire(setup)
    repo, scope, clock = setup
    next_state = c.advance(
        initial.state,
        owner_sha256=c.owner_binding(TOKEN),
        owner_epoch=1,
        expected_revision=1,
        action="renew",
        command_id=command(),
        now=clock(),
    )
    raw = c.canonical(c.document(next_state))
    with pytest.raises(DBAPIError):
        async with database[1]() as session, session.begin():
            await session.execute(
                update(DemoAccountControl)
                .filter_by(environment=scope.environment, account_id=scope.account_id)
                .values(
                    control_revision=2,
                    lease_until=next_state.lease_until,
                    state_json=raw,
                    state_sha256=c.digest(raw),
                )
            )
    assert await repo.read(scope) == initial


@pytest.mark.parametrize("operation", ["update", "delete", "truncate"])
async def test_journal_is_append_only_even_for_direct_sql(database, setup, operation):
    initial = await acquire(setup)
    repo, scope, _ = setup
    with pytest.raises(DBAPIError):
        async with database[1]() as session, session.begin():
            if operation == "truncate":
                statement = text("TRUNCATE TABLE demo_control_journal")
            elif operation == "delete":
                statement = delete(DemoControlJournal).filter_by(
                    environment=scope.environment, account_id=scope.account_id
                )
            else:
                statement = (
                    update(DemoControlJournal)
                    .filter_by(
                        environment=scope.environment, account_id=scope.account_id
                    )
                    .values(action="renew")
                )
            await session.execute(statement)
    assert await repo.read(scope) == initial


async def test_direct_sql_cannot_clear_durable_stop(database, setup):
    await acquire(setup)
    repo, scope, clock = setup
    stopped = await repo.latch_stop(scope, command_id=command())
    forged = replace(
        stopped.state,
        revision=stopped.state.revision + 1,
        emergency_stop=False,
        updated_at=clock(),
    )
    raw = c.canonical(c.document(forged))
    with pytest.raises(DBAPIError):
        async with database[1]() as session, session.begin():
            await session.execute(
                update(DemoAccountControl)
                .filter_by(environment=scope.environment, account_id=scope.account_id)
                .values(
                    control_revision=forged.revision,
                    emergency_stop=False,
                    state_json=raw,
                    state_sha256=c.digest(raw),
                )
            )
    assert await repo.read(scope) == stopped


async def test_disarm_and_stop_can_tighten_after_owner_lease_expired(setup):
    initial = await acquire(setup)
    repo, scope, clock = setup
    await change(
        setup, initial, "arm_requested", arm_expires_at=clock() + timedelta(seconds=20)
    )
    clock.value += timedelta(seconds=31)
    disarmed = await repo.revoke_arm(scope, command_id=command())
    assert (
        disarmed.state.arm_request_id is None and disarmed.state.lease_until == clock()
    )
    stopped = await repo.latch_stop(scope, command_id=command())
    assert stopped.state.emergency_stop and stopped.state.lease_until == clock()


async def test_same_hash_readback_clock_regression_denies_after_real_commit(
    database, setup
):
    _, scope, clock = setup
    origin = clock()
    samples = iter(
        (
            origin,
            origin + timedelta(milliseconds=1),
            origin + timedelta(milliseconds=2),
            origin + timedelta(milliseconds=1),
        )
    )
    repo = DemoControlRepository(database[1], clock=lambda: next(samples))
    with pytest.raises(c.DemoControlError, match="readback_conflict"):
        await repo.acquire(scope, owner_token=TOKEN, pins=PINS, command_id=command())
    clock.value = origin + timedelta(milliseconds=3)
    retained = await DemoControlRepository(database[1], clock=clock).read(scope)
    assert retained.state.revision == 1 and retained.state.arm_request_id is None
    assert not retained.execution_authority


async def test_cold_estop_is_durable_without_owner_or_fabricated_pins(database, setup):
    repo, scope, clock = setup
    stopped = await repo.latch_stop(scope, command_id=command())
    assert stopped.state.owner_epoch == 0 and stopped.state.owner_sha256 is None
    assert stopped.state.pins is None and stopped.state.emergency_stop
    restarted = DemoControlRepository(database[1], clock=clock)
    assert await restarted.read(scope) == stopped
    acquired = await restarted.acquire(
        scope, owner_token=TOKEN, pins=PINS, command_id=command()
    )
    assert acquired.state.owner_epoch == 1 and acquired.state.revision == 2
    assert acquired.state.emergency_stop and acquired.state.arm_request_id is None
    with pytest.raises(c.DemoControlError, match="estop_latched"):
        await restarted.change(
            scope,
            owner_token=TOKEN,
            owner_epoch=1,
            expected_revision=2,
            action="arm_requested",
            command_id=command(),
            arm_expires_at=clock() + timedelta(seconds=10),
        )
