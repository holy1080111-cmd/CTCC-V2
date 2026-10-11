"""Isolated PostgreSQL V7 recency readback; all account HTTP is synthetic."""

import asyncio
import json
from datetime import datetime

import pytest
from sqlalchemy import func, select, text

from app.database.repositories import account_current_history_recency as recency
from app.database.repositories.account_capture_journal import (
    AccountCaptureJournalRepository,
)
from app.database.repositories.account_current_history_recency import (
    POLICY_SHA256,
    AccountCurrentHistoryRecencyRepository,
    CurrentHistoryRecencyError,
)
from app.database.repositories.qualification_ledger import QualificationLedgerRepository
from tests.integration.test_account_current_history_observation_repository import (
    _indexed_pair,
)
from tests.integration.test_account_locked_current_history_join_v7_repository import (
    _now,
)
from tests.integration.test_qualification_ledger_repository import (
    database as database,  # noqa: PLC0414 -- explicit isolated PostgreSQL fixture
)
from tests.integration.test_qualification_ledger_repository import refresh
from tests.unit.qualification_ledger_fixtures import ledger_fixture

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def pins(history, current):
    return {
        "history_sequence": history.sequence,
        "history_sha256": history.event_sha256,
        "current_sequence": current.sequence,
        "current_sha256": current.event_sha256,
    }


async def test_native_db_clock_after_locked_v7_chain_is_deny_only(
    monkeypatch, database
):
    scope, history, current = await _indexed_pair(monkeypatch, database)
    repo = AccountCurrentHistoryRecencyRepository(database[1], clock=_now)
    result = await repo.read_joined(scope, **pins(history, current))
    value = json.loads(result.receipt_json)

    assert (
        value["schema_version"]
        == "ctcc.demo_current_history_observation_join_recency.v2"
    )
    assert value["policy_sha256"] == POLICY_SHA256
    assert value["same_account_locked_head_binding"] is True
    assert value["recorded_cross_clock_delta_only"] is True
    clock = value["recorded_cross_clock_delta"]
    sampled_at = datetime.fromisoformat(clock["db_clock_at"])
    eof = datetime.fromisoformat(clock["earliest_replayed_body_exhausted_at"])
    assert sampled_at >= eof
    assert clock["recorded_eof_db_clock_delta_within_30_seconds"] == (
        clock["age_microseconds"] <= 30_000_000
    )
    assert "current_freshness_at_locked_readback_unproven" in value["blocking_reasons"]
    assert value["source_authenticity_verified"] is False
    assert value["history_tail_closed"] is False
    assert value["portfolio_risk_snapshot"] is None
    assert value["account_complete"] is result.account_complete is False
    assert value["flat_start_permission"] is result.flat_start_permission is False
    assert value["execution_authority"] is result.execution_authority is False
    assert value["admission"] == result.admission == "DENY"
    assert scope.account_id.encode("utf-8") not in result.receipt_json


async def test_native_recency_waits_for_exact_uid_lock_and_rejects_wrong_head(
    monkeypatch, database
):
    scope, history, current = await _indexed_pair(monkeypatch, database)
    repo = AccountCurrentHistoryRecencyRepository(database[1], clock=_now)
    journal = AccountCaptureJournalRepository(database[1], clock=_now)
    selected = pins(history, current)
    prior_done = asyncio.Event()
    release_prior = asyncio.Event()
    second_lock_attempted = asyncio.Event()
    original_prior = repo.prior.read_joined
    original_lock = repo.journal._lock

    async def pause_after_prior(scope, **requested):
        result = await original_prior(scope, **requested)
        prior_done.set()
        await release_prior.wait()
        return result

    async def observe_second_lock(session, scope):
        second_lock_attempted.set()
        return await original_lock(session, scope)

    monkeypatch.setattr(repo.prior, "read_joined", pause_after_prior)
    monkeypatch.setattr(repo.journal, "_lock", observe_second_lock)
    pending = asyncio.create_task(repo.read_joined(scope, **selected))
    await asyncio.wait_for(prior_done.wait(), timeout=30)

    async with database[1]() as session, session.begin():
        await journal._lock(session, scope)
        release_prior.set()
        await asyncio.wait_for(second_lock_attempted.wait(), timeout=30)
        await asyncio.sleep(0.05)
        assert pending.done() is False

    value = await asyncio.wait_for(pending, timeout=30)
    assert value.admission == "DENY"
    with pytest.raises(CurrentHistoryRecencyError):
        await repo.read_joined(
            scope,
            **{**selected, "current_sha256": "0" * 64},
        )


async def test_native_recency_rejects_local_change_after_prior_receipt(
    monkeypatch, database
):
    scope, history, current = await _indexed_pair(monkeypatch, database)
    repo = AccountCurrentHistoryRecencyRepository(database[1], clock=_now)
    ledger = QualificationLedgerRepository(database[1], clock=_now)
    original_prior = repo.prior.read_joined

    async def change_after_prior(scope, **requested):
        result = await original_prior(scope, **requested)
        fixture = await asyncio.to_thread(ledger_fixture, account_id=scope.account_id)
        await ledger.reconcile_scope(
            refresh(fixture.claims, _now()), expected_revision=0
        )
        return result

    monkeypatch.setattr(repo.prior, "read_joined", change_after_prior)
    with pytest.raises(CurrentHistoryRecencyError):
        await repo.read_joined(scope, **pins(history, current))


async def test_native_clock_statement_ignores_public_search_path_shadow(database):
    async with database[1]() as session:
        await session.begin()
        try:
            await session.execute(
                text(
                    "CREATE FUNCTION public.clock_timestamp() RETURNS timestamptz "
                    "LANGUAGE SQL AS $$ SELECT TIMESTAMPTZ "
                    "'2099-01-01 00:00:00+00' $$"
                )
            )
            await session.execute(text("SET LOCAL search_path TO public, pg_catalog"))
            shadowed = await session.scalar(select(func.clock_timestamp()))
            native = await session.scalar(recency._native_db_clock_statement())
            assert shadowed.year == 2099
            assert native.year != 2099 and native.tzinfo is not None
        finally:
            await session.rollback()
