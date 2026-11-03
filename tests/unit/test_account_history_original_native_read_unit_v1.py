"""UNRUN real-Task cancellation guards; no database or owner acceptance.

The public-API test uses a genuine UNBOUND AsyncSession factory, so no database
can be reached. The role/scope are
declared synthetic rejection inputs, not a source owner or credential claim.
"""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.database.repositories import account_history_original_frame_v1 as wire
from app.database.repositories import account_history_original_native_read_v1 as native
from app.domain import account_history_partition_v1 as records

pytestmark = pytest.mark.asyncio


async def _drain_synthetic_cancellation(task):
    # This belongs only to a test-owned child Task. In Python 3.12 uncancel()
    # alone need not rescind an already scheduled CancelledError delivery.
    try:
        await asyncio.sleep(0)
    except asyncio.CancelledError:
        pass
    while task.cancelling():
        task.uncancel()


@pytest.mark.parametrize("delivery", ["pending", "suppressed"])
async def test_public_pg_read_rejects_cancellation_without_database(
    delivery,
):
    async def scenario():
        task = asyncio.current_task()
        request = wire.OriginalFrameRequestV1(
            records.HistoryPartitionScopeV1("demo", "123456789", "USDT"),
            1,
            "a" * 64,
            0,
            None,
            1,
            0,
            0,
            datetime.now(UTC) + timedelta(seconds=20),
        )
        factory = async_sessionmaker()
        saw_cancel = wrong_exception = returned = False
        task.cancel()
        if delivery == "suppressed":
            try:
                await asyncio.sleep(0)
            except asyncio.CancelledError:
                pass
        assert task.cancelling() > 0
        try:
            await native.replay_original_history_from_pg_v1(
                factory,
                genesis_request=request,
                expected_checkpoint_sha256=None,
                approved_collector_role_oid=42,
            )
            returned = True
        except asyncio.CancelledError:
            saw_cancel = True
        except Exception:  # noqa: BLE001 -- only the closed exception class matters
            wrong_exception = True
        finally:
            await _drain_synthetic_cancellation(task)
        return saw_cancel, wrong_exception, returned

    # Cancellation state and uncancel() never affect the pytest runner Task.
    assert await asyncio.create_task(scenario()) == (True, False, False)


@pytest.mark.parametrize("delivery", ["pending", "suppressed"])
async def test_existing_fence_rejects_cancellation_after_admission(delivery):
    async def scenario():
        task = asyncio.current_task()
        fence = native._Fence(datetime.now(UTC) + timedelta(seconds=20))
        task.cancel()
        if delivery == "suppressed":
            try:
                await asyncio.sleep(0)
            except asyncio.CancelledError:
                pass
        assert task.cancelling() > 0
        saw_cancel = False
        try:
            # This is the same check used after awaits, before page yield and
            # before result return. It does not authenticate any native owner.
            fence.check()
        except asyncio.CancelledError:
            saw_cancel = True
        finally:
            await _drain_synthetic_cancellation(task)
        return saw_cancel

    assert await asyncio.create_task(scenario()) is True
