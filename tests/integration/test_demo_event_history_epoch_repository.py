"""DB0041 in an isolated PostgreSQL child database, with synthetic UID only."""

import asyncio
import json
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.database.repositories.demo_event_history_epoch import (
    DemoEventHistoryEpochRepository,
    EventEpochBinding,
    EventEpochReadback,
    EventHistoryEpochError,
    _digest,
    _wire,
)
from tests.integration.test_durable_migration_downgrade import migrate, shape
from tests.integration.test_durable_migration_downgrade import (
    sandbox as sandbox,  # noqa: PLC0414
)
from tests.integration.test_qualification_ledger_repository import (
    database as database,  # noqa: PLC0414
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.fixture
def bound():
    return EventEpochBinding(
        environment="demo",
        account_id=f"456789{uuid4().int % 10**14:014d}",
        credential_session_binding_sha256="a" * 64,
        claimed_source_sha256="b" * 64,
        policy_sha256="c" * 64,
    )


@pytest.mark.parametrize("revision", ["0041"])
async def test_restart_readback_and_append_only_denial(sandbox, bound):
    engine, sessions = sandbox
    repo = DemoEventHistoryEpochRepository(sessions)
    anchor = await repo.record_unverified_anchor(bound, evidence_sha256="d" * 64)
    assert [event.kind for event in anchor.events] == ["anchor_unverified"]
    assert anchor.admission == "DENY" and not anchor.history_complete
    assert not anchor.execution_authority
    restarted = DemoEventHistoryEpochRepository(sessions)
    assert (await restarted.read(bound)).events == anchor.events
    invalid = await restarted.invalidate(
        bound, reason_code="source_gap", evidence_sha256="e" * 64
    )
    assert [event.kind for event in invalid.events] == [
        "anchor_unverified",
        "invalidated",
    ]
    assert invalid.events[1].previous_sha256 == anchor.events[0].event_sha256
    assert (await repo.read(bound)).events == invalid.events

    for sql in (
        "UPDATE public.demo_event_history_epoch_events SET reason_code='altered'",
        "DELETE FROM public.demo_event_history_epoch_events",
        "TRUNCATE public.demo_event_history_epoch_events",
    ):
        with pytest.raises(DBAPIError):
            async with engine.begin() as connection:
                await connection.execute(text(sql))
        assert (await repo.read(bound)).events == invalid.events


@pytest.mark.parametrize("revision", ["0041"])
async def test_concurrent_duplicate_anchor_denied_and_invalidations_ordered(
    sandbox, bound
):
    _, sessions = sandbox
    first = DemoEventHistoryEpochRepository(sessions)
    second = DemoEventHistoryEpochRepository(sessions)
    results = await asyncio.gather(
        first.record_unverified_anchor(bound, evidence_sha256="d" * 64),
        second.record_unverified_anchor(bound, evidence_sha256="d" * 64),
        return_exceptions=True,
    )
    assert sum(type(result) is EventEpochReadback for result in results) == 1
    assert (
        sum(
            isinstance(result, EventHistoryEpochError)
            and str(result) == "event_epoch_anchor_exists"
            for result in results
        )
        == 1
    )
    results = await asyncio.gather(
        first.invalidate(bound, reason_code="source_gap", evidence_sha256="e" * 64),
        second.invalidate(
            bound, reason_code="session_changed", evidence_sha256="f" * 64
        ),
    )
    assert all(len(result.events) in (2, 3) for result in results)
    final = await first.read(bound)
    assert [event.sequence for event in final.events] == [1, 2, 3]
    assert all(
        event.previous_sha256 == final.events[index - 1].event_sha256
        for index, event in enumerate(final.events)
        if index
    )
    assert final.admission == "DENY" and not final.history_complete


@pytest.mark.parametrize("revision", ["0041"])
async def test_positive_or_broken_link_insert_is_rejected_by_database(sandbox, bound):
    engine, sessions = sandbox
    repo = DemoEventHistoryEpochRepository(sessions)
    await repo.record_unverified_anchor(bound, evidence_sha256="d" * 64)
    current = await repo.read(bound)
    previous = current.events[0].event_sha256
    valid = _wire(
        bound,
        sequence=2,
        kind="invalidated",
        reason_code="source_gap",
        evidence_sha256="e" * 64,
        previous=previous,
    )
    for wire, link in (
        (json.dumps({**json.loads(valid), "history_complete": True}), previous),
        (valid, "f" * 64),
    ):
        with pytest.raises(DBAPIError):
            async with engine.begin() as connection:
                await connection.execute(
                    text("""
                      INSERT INTO public.demo_event_history_epoch_events(
                        epoch_id,sequence,environment,account_id,
                        credential_session_binding_sha256,claimed_source_sha256,
                        policy_sha256,kind,reason_code,evidence_sha256,
                        previous_sha256,event_sha256,event_json)
                      VALUES(:epoch_id,2,'demo',:account_id,:session_sha,:source_sha,
                        :policy_sha,'invalidated','source_gap',:evidence_sha,
                        :previous_sha,:event_sha,:event_json)
                    """),
                    {
                        "epoch_id": bound.epoch_id,
                        "account_id": bound.account_id,
                        "session_sha": bound.credential_session_binding_sha256,
                        "source_sha": bound.claimed_source_sha256,
                        "policy_sha": bound.policy_sha256,
                        "evidence_sha": "e" * 64,
                        "previous_sha": link,
                        "event_sha": _digest(wire),
                        "event_json": wire,
                    },
                )
        assert len((await repo.read(bound)).events) == 1


@pytest.mark.parametrize("revision", ["0041"])
async def test_disabled_guard_readback_denied_without_changing_journal(sandbox, bound):
    engine, sessions = sandbox
    repo = DemoEventHistoryEpochRepository(sessions)
    anchor = await repo.record_unverified_anchor(bound, evidence_sha256="d" * 64)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text("""
                  ALTER TABLE public.demo_event_history_epoch_events
                  DISABLE TRIGGER demo_event_history_epoch_insert_guard
                """)
            )
        with pytest.raises(
            EventHistoryEpochError, match="event_epoch_schema_retention_invalid"
        ):
            await repo.read(bound)
    finally:
        async with engine.begin() as connection:
            await connection.execute(
                text("""
                  ALTER TABLE public.demo_event_history_epoch_events
                  ENABLE TRIGGER demo_event_history_epoch_insert_guard
                """)
            )
    assert (await repo.read(bound)).events == anchor.events


@pytest.mark.parametrize("revision", ["0041"])
async def test_nonempty_downgrade_denies_and_preserves_chain(sandbox, bound):
    engine, sessions = sandbox
    repo = DemoEventHistoryEpochRepository(sessions)
    anchor = await repo.record_unverified_anchor(bound, evidence_sha256="d" * 64)
    before = await shape(engine)
    with pytest.raises(
        DBAPIError, match="demo_event_history_epoch_downgrade_requires_empty"
    ):
        async with engine.begin() as connection:
            await connection.run_sync(migrate, "0041", "downgrade")
    assert await shape(engine) == before
    assert (await repo.read(bound)).events == anchor.events
