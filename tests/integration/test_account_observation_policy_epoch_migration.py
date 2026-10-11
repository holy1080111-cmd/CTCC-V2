"""DB0036/0037 policy-epoch migrations in a disposable PostgreSQL database.

The shared sandbox accepts only the credential-free hermetic test cluster and
creates its own child database. Synthetic B1/B3 rows never touch an account.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.integration.test_account_observation_index_repository import stored
from tests.integration.test_durable_migration_downgrade import (
    migrate,
    shape,
)
from tests.integration.test_durable_migration_downgrade import (
    sandbox as sandbox,  # noqa: PLC0414
)
from tests.integration.test_qualification_ledger_repository import (
    database as database,  # noqa: PLC0414
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_GUARD_TABLES = (
    "demo_account_capture_events",
    "demo_account_observation_batches",
)


@pytest.mark.parametrize("revision", ["0036", "0037"])
async def test_empty_policy_epoch_downgrade_and_reupgrade_preserve_exact_shape(
    sandbox, revision
):
    engine, _ = sandbox
    before = await shape(engine)
    async with engine.begin() as connection:
        await connection.run_sync(migrate, revision, "downgrade")
    after_downgrade = await shape(engine)
    assert after_downgrade != before
    async with engine.begin() as connection:
        await connection.run_sync(migrate, revision, "upgrade")
    assert await shape(engine) == before


@pytest.mark.parametrize("revision", ["0036", "0037"])
async def test_policy_epoch_migration_nowait_denies_active_writer_without_drift(
    sandbox, revision
):
    engine, _ = sandbox
    initial = await shape(engine)
    for table in _GUARD_TABLES:
        async with engine.begin() as writer:
            await writer.execute(
                text(f"LOCK TABLE public.{table} IN ROW EXCLUSIVE MODE")
            )
            with pytest.raises(DBAPIError) as denied:
                async with engine.begin() as migration:
                    await migration.run_sync(migrate, revision, "downgrade")
            assert denied.value.orig.sqlstate == "55P03"
        assert await shape(engine) == initial
    async with engine.begin() as connection:
        await connection.run_sync(migrate, revision, "downgrade")
    old = await shape(engine)
    for table in _GUARD_TABLES:
        async with engine.begin() as writer:
            await writer.execute(
                text(f"LOCK TABLE public.{table} IN ROW EXCLUSIVE MODE")
            )
            with pytest.raises(DBAPIError) as denied:
                async with engine.begin() as migration:
                    await migration.run_sync(migrate, revision, "upgrade")
            assert denied.value.orig.sqlstate == "55P03"
        assert await shape(engine) == old
    async with engine.begin() as connection:
        await connection.run_sync(migrate, revision, "upgrade")
    assert await shape(engine) == initial


@pytest.mark.parametrize("revision", ["0037"])
async def test_v2_receipt_blocks_downgrade_and_preserves_original_source(
    monkeypatch, sandbox, revision
):
    engine, _ = sandbox
    repo, scope, reference, _, accepted = await stored(monkeypatch, sandbox)
    original_chain = await repo.journal.read_chain(scope, reference.capture_id)
    before = await shape(engine)
    with pytest.raises(
        DBAPIError,
        match="account_observation_membership_repair_downgrade_requires_no_v2_rows",
    ):
        async with engine.begin() as connection:
            await connection.run_sync(migrate, revision, "downgrade")
    assert await shape(engine) == before
    assert await repo.read(scope) == (accepted,)
    assert await repo.journal.read_chain(scope, reference.capture_id) == original_chain
