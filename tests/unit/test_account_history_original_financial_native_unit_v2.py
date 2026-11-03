"""HELD no-database admission/cancellation tests, not source-owner evidence."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.database.repositories import (
    account_history_original_financial_read_v2 as native,
)
from app.database.repositories import account_history_original_frame_v1 as wire
from app.domain import account_history_partition_v1 as records
from app.trade_qualification import account_history_original_stream_v1 as stream

pytestmark = pytest.mark.asyncio


def request():
    return wire.OriginalFrameRequestV1(
        records.HistoryPartitionScopeV1("demo", "123456789", "USDT"),
        2,
        "a" * 64,
        0,
        None,
        1,
        0,
        0,
        datetime.now(UTC) + timedelta(seconds=20),
    )


async def drain(task):
    try:
        await asyncio.sleep(0)
    except asyncio.CancelledError:
        pass
    while task.cancelling():
        task.uncancel()


@pytest.mark.parametrize("delivery", ["pending", "suppressed"])
async def test_financial_native_read_checks_real_task_cancel_before_database(delivery):
    async def scenario():
        task = asyncio.current_task()
        original = request()
        task.cancel()
        if delivery == "suppressed":
            try:
                await asyncio.sleep(0)
            except asyncio.CancelledError:
                pass
        try:
            # A real UNBOUND factory cannot reach a database. Cancellation must
            # beat any session attempt; no mock claims a successful acquisition.
            with pytest.raises(asyncio.CancelledError) as rejected:
                await native.replay_original_history_financial_from_pg_v2(
                    async_sessionmaker(),
                    genesis_request=original,
                    expected_checkpoint_sha256=None,
                    approved_collector_role_oid=42,
                    expected_financial_policy_sha256=stream.original_history_financial_stream_policy_sha256_v2(),
                )
            assert rejected.value.args == ()
        finally:
            await drain(task)

    await asyncio.create_task(scenario())


@pytest.mark.parametrize("policy", [True, None, "f" * 64])
async def test_financial_policy_is_exact_and_checked_before_unbound_database(policy):
    with pytest.raises(
        native.OriginalHistoryFinancialPgReadError,
        match="^original_financial_pg_policy_mismatch$",
    ):
        await native.replay_original_history_financial_from_pg_v2(
            async_sessionmaker(),
            genesis_request=request(),
            expected_checkpoint_sha256=None,
            approved_collector_role_oid=42,
            expected_financial_policy_sha256=policy,
        )


async def test_non_genesis_request_cannot_seed_a_financial_native_read():
    non_genesis = replace(request(), after=1, previous="b" * 64)
    with pytest.raises(
        native.OriginalHistoryFinancialPgReadError,
        match="^original_pg_request_invalid$",
    ):
        await native.replay_original_history_financial_from_pg_v2(
            async_sessionmaker(),
            genesis_request=non_genesis,
            expected_checkpoint_sha256=None,
            approved_collector_role_oid=42,
            expected_financial_policy_sha256=stream.original_history_financial_stream_policy_sha256_v2(),
        )


async def test_expired_original_request_is_not_renewed_for_financial_replay():
    expired = replace(request(), expires_at=datetime.now(UTC) - timedelta(seconds=1))
    with pytest.raises(
        native.OriginalHistoryFinancialPgReadError,
        match="^original_pg_original_expiry$",
    ):
        await native.replay_original_history_financial_from_pg_v2(
            async_sessionmaker(),
            genesis_request=expired,
            expected_checkpoint_sha256=None,
            approved_collector_role_oid=42,
            expected_financial_policy_sha256=stream.original_history_financial_stream_policy_sha256_v2(),
        )
