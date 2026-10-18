"""Isolated PostgreSQL B3-only prefix catch-up from synthetic B1 originals.

All receipts remain DENY. These tests do not touch OKX or prove native account
history; they require an explicit migrated, isolated DATABASE_URL.
"""

import asyncio

import pytest
from sqlalchemy import select

from app.database.models.account_measured_hwm_v2 import (
    DemoAccountMeasuredHWMV2 as HWMRow,
)
from app.database.repositories.account_measured_hwm_v2 import (
    AccountMeasuredHWMError,
    AccountMeasuredHWMRepository,
)
from app.trade_qualification import account_observation_index as observed
from tests.integration.test_account_observation_index_repository import (
    additional_capture,
    now,
    stored,
)
from tests.integration.test_measured_hwm_v2_postgres import (
    _row_count,
)
from tests.integration.test_measured_hwm_v2_postgres import (
    database as database,  # noqa: PLC0414 -- explicit isolated PG fixture
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def _two_b3_only(monkeypatch, database):
    observations, scope, _, _, first = await stored(monkeypatch, database)
    reference, window = await additional_capture(monkeypatch, observations, scope, [])
    second = await observations.append(scope, reference, window, expected_revision=1)
    assert await _row_count(database, scope) == 0
    return scope, first, second


async def test_b3_only_scope_catches_up_from_original_genesis_in_bounded_pages(
    monkeypatch, database
):
    scope, first, second = await _two_b3_only(monkeypatch, database)
    repository = AccountMeasuredHWMRepository(database[1], clock=now)
    with pytest.raises(AccountMeasuredHWMError, match="append_unknown"):
        await repository.append_next(
            scope, expected_sequence=2, expected_head_sha256=second.event_sha256
        )
    assert await _row_count(database, scope) == 0

    first_page = await repository.catch_up_history(
        scope,
        expected_observation_sequence=2,
        expected_observation_head_sha256=second.event_sha256,
        max_rows=1,
    )
    assert first_page.indexed_through_sequence == 1
    assert first_page.indexed_head_sha256 == first.event_sha256
    assert first_page.observation_head_sequence == 2
    assert first_page.remaining_observation_rows == 1
    assert first_page.verified_rows == 1
    assert first_page.admission == "DENY"
    assert first_page.account_complete is False
    assert first_page.execution_authority is False
    assert first_page.durable_membership_verified is False
    assert first_page.native_clock_verified is False
    assert await _row_count(database, scope) == 1

    restarted = AccountMeasuredHWMRepository(database[1], clock=now)
    second_page = await restarted.catch_up_history(
        scope,
        expected_observation_sequence=2,
        expected_observation_head_sha256=second.event_sha256,
        max_rows=1,
    )
    assert second_page.indexed_through_sequence == 2
    assert second_page.indexed_head_sha256 == second.event_sha256
    assert second_page.remaining_observation_rows == 0
    assert second_page.verified_rows == 1
    assert second_page.admission == "DENY"
    assert await _row_count(database, scope) == 2
    # The earlier committed page remains independently replayable even after
    # a later worker has advanced the B5 tip under the same B3 head.
    assert (
        await restarted.readback_history(
            scope,
            expected_observation_sequence=2,
            expected_observation_head_sha256=second.event_sha256,
            first_sequence=1,
            through_sequence=1,
            expected_projection_sha256=first_page.projection_sha256,
        )
        == first_page
    )
    with pytest.raises(AccountMeasuredHWMError, match="history_readback_unknown"):
        await restarted.readback_history(
            scope,
            expected_observation_sequence=2,
            expected_observation_head_sha256=second.event_sha256,
            first_sequence=1,
            through_sequence=1,
            expected_projection_sha256="0" * 64,
        )
    assert (
        await restarted.catch_up_history(
            scope,
            expected_observation_sequence=2,
            expected_observation_head_sha256=second.event_sha256,
            max_rows=1,
        )
    ).indexed_through_sequence == 2
    assert await _row_count(database, scope) == 2


async def test_catch_up_rejects_stale_head_and_old_policy_without_new_rows(
    monkeypatch, database
):
    scope, first, second = await _two_b3_only(monkeypatch, database)
    repository = AccountMeasuredHWMRepository(database[1], clock=now)
    with pytest.raises(AccountMeasuredHWMError, match="catch_up_unknown"):
        await repository.catch_up_history(
            scope,
            expected_observation_sequence=1,
            expected_observation_head_sha256=first.event_sha256,
            max_rows=1,
        )
    assert await _row_count(database, scope) == 0
    with monkeypatch.context() as changed:
        changed.setattr(observed, "POLICY_SHA256", "0" * 64)
        with pytest.raises(AccountMeasuredHWMError, match="catch_up_unknown"):
            await repository.catch_up_history(
                scope,
                expected_observation_sequence=2,
                expected_observation_head_sha256=second.event_sha256,
                max_rows=1,
            )
    assert await _row_count(database, scope) == 0


async def test_catch_up_late_readback_failure_preserves_committed_prefix(
    monkeypatch, database
):
    scope, _, second = await _two_b3_only(monkeypatch, database)
    repository = AccountMeasuredHWMRepository(database[1], clock=now)
    original = AccountMeasuredHWMRepository.readback_history

    async def fail(*_args, **_kwargs):
        raise RuntimeError("synthetic post-commit readback failure")

    with monkeypatch.context() as changed:
        changed.setattr(AccountMeasuredHWMRepository, "readback_history", fail)
        with pytest.raises(AccountMeasuredHWMError, match="catch_up_unknown"):
            await repository.catch_up_history(
                scope,
                expected_observation_sequence=2,
                expected_observation_head_sha256=second.event_sha256,
                max_rows=1,
            )
    assert await _row_count(database, scope) == 1
    async with database[1]() as session:
        first = await session.scalar(
            select(HWMRow).filter_by(
                environment=scope.environment,
                account_id=scope.account_id,
                settlement_currency=scope.settlement_currency,
                sequence=1,
            )
        )
    assert first is not None
    restored = await original(
        repository,
        scope,
        expected_observation_sequence=2,
        expected_observation_head_sha256=second.event_sha256,
        first_sequence=1,
        through_sequence=1,
        expected_projection_sha256=first.projection_sha256,
    )
    assert restored.admission == "DENY"
    final = await repository.catch_up_history(
        scope,
        expected_observation_sequence=2,
        expected_observation_head_sha256=second.event_sha256,
        max_rows=1,
    )
    assert final.indexed_through_sequence == 2
    assert await _row_count(database, scope) == 2


async def test_concurrent_catch_up_never_duplicates_a_b3_sequence(
    monkeypatch, database
):
    scope, _, second = await _two_b3_only(monkeypatch, database)
    repository = AccountMeasuredHWMRepository(database[1], clock=now)
    outcomes = await asyncio.gather(
        *(
            repository.catch_up_history(
                scope,
                expected_observation_sequence=2,
                expected_observation_head_sha256=second.event_sha256,
                max_rows=1,
            )
            for _ in range(2)
        ),
        return_exceptions=True,
    )
    assert all(
        isinstance(value, AccountMeasuredHWMError) or value.admission == "DENY"
        for value in outcomes
    )
    assert await _row_count(database, scope) == 2
    async with database[1]() as session:
        tip = await session.scalar(
            select(HWMRow).filter_by(
                environment=scope.environment,
                account_id=scope.account_id,
                settlement_currency=scope.settlement_currency,
                sequence=2,
            )
        )
    assert tip is not None
    replayed = await repository.readback_history(
        scope,
        expected_observation_sequence=2,
        expected_observation_head_sha256=second.event_sha256,
        first_sequence=1,
        through_sequence=2,
        expected_projection_sha256=tip.projection_sha256,
    )
    assert replayed.verified_rows == 2 and replayed.admission == "DENY"
