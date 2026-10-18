"""Isolated PostgreSQL B5 proofs from synthetic B1/B3 bytes, always DENY.

These cases require an explicit isolated, migrated DATABASE_URL. They do not
authenticate to OKX, mutate a deployed database, or qualify current account
equity, account genesis, historical completeness, or order execution.
"""

import asyncio
import hashlib
import json

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from app.database.models.account_measured_hwm_v2 import (
    DemoAccountMeasuredHWMV2 as HWMRow,
)
from app.database.models.account_observation_index import (
    DemoAccountObservationBatch as Batch,
)
from app.database.repositories.account_measured_hwm_v2 import (
    AccountMeasuredHWMError,
    AccountMeasuredHWMRepository,
)
from app.trade_qualification import account_capture_journal as journal
from app.trade_qualification import account_measured_hwm_continuation as hwm
from app.trade_qualification import account_portfolio_components as components
from scripts.verify_final_hermetic import (
    ACCOUNT_MEASURED_HWM_V2_TRIGGER_CATALOG_SQL,
    ACCOUNT_REVISION_WITNESS_TRIGGER_CATALOG_SQL,
    migration_revision_readback,
)
from scripts.verify_migration_identity import database_head
from tests.durable_migration_fixtures import load_migration
from tests.integration.test_account_observation_index_repository import (
    additional_capture,
    now,
    stored,
)
from tests.integration.test_qualification_ledger_repository import (
    database as database,  # noqa: PLC0414 -- explicit isolated PG fixture
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def _first(monkeypatch, database):
    observation_repo, scope, _, _, batch = await stored(monkeypatch, database)
    repository = AccountMeasuredHWMRepository(database[1], clock=now)
    readback = await repository.append_next(
        scope, expected_sequence=1, expected_head_sha256=batch.event_sha256
    )
    assert readback.admission == "DENY" and readback.execution_authority is False
    return observation_repo, repository, scope, batch, readback


async def _second(monkeypatch, database):
    observation_repo, repository, scope, first, _ = await _first(monkeypatch, database)
    reference, window = await additional_capture(
        monkeypatch, observation_repo, scope, []
    )
    second = await observation_repo.append(
        scope, reference, window, expected_revision=1
    )
    return observation_repo, repository, scope, first, second


async def _row_count(database, scope):
    async with database[1]() as session:
        return await session.scalar(
            select(func.count())
            .select_from(HWMRow)
            .filter_by(
                environment=scope.environment,
                account_id=scope.account_id,
                settlement_currency=scope.settlement_currency,
            )
        )


async def _second_insert_values(database, repository, scope):
    """Build a valid SQL candidate before tampering with its outer JSON text."""
    async with database[1]() as session, session.begin():
        predecessor = await session.scalar(
            select(HWMRow).filter_by(
                environment=scope.environment,
                account_id=scope.account_id,
                settlement_currency=scope.settlement_currency,
                sequence=1,
            )
        )
        batch = await session.scalar(
            select(Batch).filter_by(
                environment=scope.environment,
                account_id=scope.account_id,
                settlement_currency=scope.settlement_currency,
                sequence=2,
            )
        )
        assert predecessor is not None and batch is not None
        projected, measurement, peak_sequence = await repository._compute(
            session, scope, batch, predecessor
        )
        assert peak_sequence == 1
        return {
            "environment": scope.environment,
            "account_id": scope.account_id,
            "settlement_currency": scope.settlement_currency,
            "sequence": 2,
            "capture_id": batch.capture_id,
            "source_sequence": batch.source_sequence,
            "event_sha256": batch.event_sha256,
            "previous_event_sha256": batch.previous_sha256,
            "measurement_sha256": journal.digest(measurement.source_json),
            "measurement_json": measurement.source_json.decode(),
            "genesis_sequence": 1,
            "peak_sequence": 1,
            "previous_projection_sha256": predecessor.projection_sha256,
            "projection_sha256": projected.receipt_sha256,
            "projection_json": projected.receipt_json.decode(),
            "account_complete": False,
            "execution_authority": False,
            "admission": "DENY",
        }, predecessor


async def test_b5_committed_chain_restarts_and_keeps_original_genesis_peak(
    monkeypatch, database
):
    _, repository, scope, first, second = await _second(monkeypatch, database)
    restarted = AccountMeasuredHWMRepository(database[1], clock=now)
    readback = await restarted.append_next(
        scope, expected_sequence=2, expected_head_sha256=second.event_sha256
    )
    assert readback.sequence == 2 and readback.head_sha256 == second.event_sha256
    assert readback.account_complete is False
    assert readback.durable_membership_verified is False
    assert readback.native_clock_verified is False
    assert readback.admission == "DENY"
    projected = json.loads(readback.projection_json)
    assert projected["sample_count"] == 2
    assert projected["previous_head_sha256"] == first.event_sha256
    assert projected["peak_equity"] == {"numerator": "1000", "denominator": "1"}
    assert (
        await restarted.readback(
            scope, expected_sequence=2, expected_head_sha256=second.event_sha256
        )
        == readback
    )
    assert (
        await repository.append_next(
            scope, expected_sequence=2, expected_head_sha256=second.event_sha256
        )
        == readback
    )
    assert await _row_count(database, scope) == 2


async def test_b5_direct_sql_forged_nonpeak_equity_cannot_enter_chain(
    monkeypatch, database
):
    _, repository, scope, _, second = await _second(monkeypatch, database)
    async with database[1]() as session, session.begin():
        predecessor = await session.scalar(
            select(HWMRow).filter_by(
                environment=scope.environment,
                account_id=scope.account_id,
                settlement_currency=scope.settlement_currency,
                sequence=1,
            )
        )
        batch = await session.scalar(
            select(Batch).filter_by(
                environment=scope.environment,
                account_id=scope.account_id,
                settlement_currency=scope.settlement_currency,
                sequence=2,
            )
        )
        reference, original = await repository._original(session, scope, batch)
        _, genesis = await repository._saved_witness(session, scope, 1)
        assert predecessor is not None and batch is not None
        forged_data = json.loads(original.source_json)
        assert forged_data["equity"] == {"numerator": "1000", "denominator": "1"}
        forged_data["equity"] = {"numerator": "900", "denominator": "1"}
        forged = components.BalanceMeasurement(journal.canonical(forged_data))
        projected = hwm.advance_measured_hwm(
            scope,
            sequence=2,
            previous_event_sha256=batch.previous_sha256,
            event_sha256=batch.event_sha256,
            measurement=forged,
            expected_reference=reference,
            predecessor=hwm.HWMContinuation(predecessor.projection_json.encode()),
            expected_predecessor_sha256=predecessor.projection_sha256,
            genesis_witness=genesis,
            selected_peak_witness=genesis,
        )
        assert json.loads(projected.receipt_json)["peak_equity"] == {
            "numerator": "1000",
            "denominator": "1",
        }
        insert_values = {
            "environment": scope.environment,
            "account_id": scope.account_id,
            "settlement_currency": scope.settlement_currency,
            "sequence": 2,
            "capture_id": batch.capture_id,
            "source_sequence": batch.source_sequence,
            "event_sha256": batch.event_sha256,
            "previous_event_sha256": batch.previous_sha256,
            "measurement_sha256": journal.digest(forged.source_json),
            "measurement_json": forged.source_json.decode(),
            "genesis_sequence": 1,
            "peak_sequence": 1,
            "previous_projection_sha256": predecessor.projection_sha256,
            "projection_sha256": projected.receipt_sha256,
            "projection_json": projected.receipt_json.decode(),
            "account_complete": False,
            "execution_authority": False,
            "admission": "DENY",
        }
    with pytest.raises(DBAPIError, match="account_measured_hwm_v2_equity_denied"):
        async with database[1]() as session, session.begin():
            session.add(HWMRow(**insert_values))
            await session.flush()
    assert await _row_count(database, scope) == 1
    accepted = await repository.append_next(
        scope, expected_sequence=2, expected_head_sha256=second.event_sha256
    )
    assert accepted.admission == "DENY" and await _row_count(database, scope) == 2


@pytest.mark.parametrize("poison", ("whitespace", "reordered", "projection_extra"))
async def test_b5_direct_sql_rejects_rehashed_noncanonical_or_extra_fields(
    monkeypatch, database, poison
):
    _, repository, scope, _, second = await _second(monkeypatch, database)
    values, predecessor = await _second_insert_values(database, repository, scope)
    if poison == "projection_extra":
        projected = json.loads(values["projection_json"])
        projected["extra_caller_claim"] = True
        raw_projection = journal.canonical(projected)
        values["projection_json"] = raw_projection.decode()
        values["projection_sha256"] = journal.digest(raw_projection)
    else:
        original = json.loads(values["measurement_json"])
        if poison == "whitespace":
            raw_measurement = json.dumps(
                original, sort_keys=True, indent=1, ensure_ascii=False
            ).encode()
        else:
            raw_measurement = json.dumps(
                dict(reversed(tuple(original.items()))),
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode()
        assert raw_measurement != journal.canonical(original)
        values["measurement_json"] = raw_measurement.decode()
        values["measurement_sha256"] = journal.digest(raw_measurement)
        projected = json.loads(values["projection_json"])
        projected["last_measurement_sha256"] = values["measurement_sha256"]
        member = journal.canonical(
            [
                2,
                values["previous_event_sha256"],
                values["event_sha256"],
                values["measurement_sha256"],
            ]
        )
        projected["membership_sha256"] = hashlib.sha256(
            hwm._MEMBERSHIP_DOMAIN
            + bytes.fromhex(
                json.loads(predecessor.projection_json)["membership_sha256"]
            )
            + member
        ).hexdigest()
        raw_projection = journal.canonical(projected)
        values["projection_json"] = raw_projection.decode()
        values["projection_sha256"] = journal.digest(raw_projection)
    # Every digest/member pin is internally consistent. Old JSONB-only checks
    # would accept this immutable row; the new SQL guard rejects before commit.
    with pytest.raises(DBAPIError, match="account_measured_hwm_v2_binding_denied"):
        async with database[1]() as session, session.begin():
            session.add(HWMRow(**values))
            await session.flush()
    assert await _row_count(database, scope) == 1
    accepted = await repository.append_next(
        scope, expected_sequence=2, expected_head_sha256=second.event_sha256
    )
    assert accepted.admission == "DENY" and await _row_count(database, scope) == 2


async def test_b5_shared_json_helpers_are_not_public_execution_grants(database):
    async with database[0].connect() as connection:
        rows = (
            await connection.execute(
                text("""
                  SELECT p.proname,
                    EXISTS(SELECT 1 FROM pg_catalog.aclexplode(
                      COALESCE(p.proacl,pg_catalog.acldefault('f',p.proowner))) AS a
                      WHERE a.grantee=0 AND a.privilege_type='EXECUTE'),
                    pg_catalog.has_function_privilege(
                      current_user,p.oid,'EXECUTE')
                  FROM pg_catalog.pg_proc AS p
                  WHERE p.oid IN (
                    'public.gate3_schedule_all_ascii(jsonb)'::pg_catalog.regprocedure,
                    'public.gate3_schedule_canonical_jsonb(jsonb)'::pg_catalog.regprocedure)
                  ORDER BY p.proname
                """)
            )
        ).all()
    assert [row[0] for row in rows] == [
        "gate3_schedule_all_ascii",
        "gate3_schedule_canonical_jsonb",
    ]
    assert all(row[1] is False for row in rows)
    assert all(row[2] is True for row in rows)


async def test_b5_ascii_sql_serializer_matches_python_canonical_examples(
    monkeypatch, database
):
    _, repository, scope, _, _ = await _second(monkeypatch, database)
    values, _ = await _second_insert_values(database, repository, scope)
    samples = (
        values["measurement_json"],
        values["projection_json"],
        journal.canonical(
            {
                "controls": 'quote" slash/ backslash\\ newline\n tab\t',
                "items": [0, 1, True, None],
            }
        ).decode(),
    )
    async with database[0].connect() as connection:
        for raw in samples:
            ascii_only, canonical = (
                await connection.execute(
                    text("""
                      SELECT public.gate3_schedule_all_ascii(CAST(:raw AS jsonb)),
                             public.gate3_schedule_canonical_jsonb(CAST(:raw AS jsonb))
                    """),
                    {"raw": raw},
                )
            ).one()
            assert ascii_only is True and canonical == raw


async def test_b5_two_writers_same_tail_create_only_one_durable_row(
    monkeypatch, database
):
    _, repository, scope, _, second = await _second(monkeypatch, database)
    outcomes = await asyncio.gather(
        *(
            repository.append_next(
                scope, expected_sequence=2, expected_head_sha256=second.event_sha256
            )
            for _ in range(2)
        ),
        return_exceptions=True,
    )
    assert any(not isinstance(item, Exception) for item in outcomes)
    assert await _row_count(database, scope) == 2
    assert all(
        isinstance(item, AccountMeasuredHWMError) or item.admission == "DENY"
        for item in outcomes
    )


async def test_b5_late_readback_failure_keeps_committed_denial(monkeypatch, database):
    _, repository, scope, _, second = await _second(monkeypatch, database)
    original = AccountMeasuredHWMRepository.readback

    async def fail(*_args, **_kwargs):
        raise RuntimeError("synthetic late readback failure")

    monkeypatch.setattr(AccountMeasuredHWMRepository, "readback", fail)
    with pytest.raises(AccountMeasuredHWMError, match="append_unknown"):
        await repository.append_next(
            scope, expected_sequence=2, expected_head_sha256=second.event_sha256
        )
    monkeypatch.setattr(AccountMeasuredHWMRepository, "readback", original)
    assert await _row_count(database, scope) == 2
    recovered = await repository.readback(
        scope, expected_sequence=2, expected_head_sha256=second.event_sha256
    )
    assert recovered.admission == "DENY"


async def test_b5_nonempty_downgrade_denies_and_rolls_back_exact_catalog(
    monkeypatch, database
):
    _, _, scope, _, _ = await _first(monkeypatch, database)

    async def inspect():
        async with database[0].connect() as connection:
            head = await connection.run_sync(database_head)
            witness_table = await connection.scalar(
                text(
                    "SELECT pg_catalog.to_regclass('public.demo_account_revision_source_witnesses') IS NOT NULL"
                )
            )
            witness_guard = await connection.scalar(
                text(
                    "SELECT pg_catalog.to_regprocedure('public.account_revision_source_witness_guard()') IS NOT NULL"
                )
            )
            witness_catalog = (
                await connection.execute(
                    text(ACCOUNT_REVISION_WITNESS_TRIGGER_CATALOG_SQL)
                )
            ).all()
            hwm_table = await connection.scalar(
                text(
                    "SELECT pg_catalog.to_regclass('public.demo_account_measured_hwm_v2') IS NOT NULL"
                )
            )
            hwm_guard = await connection.scalar(
                text(
                    "SELECT pg_catalog.to_regprocedure('public.account_measured_hwm_v2_guard()') IS NOT NULL"
                )
            )
            hwm_catalog = (
                await connection.execute(
                    text(ACCOUNT_MEASURED_HWM_V2_TRIGGER_CATALOG_SQL)
                )
            ).all()
        return migration_revision_readback(
            "0044",
            head,
            witness_table,
            witness_guard,
            witness_catalog,
            hwm_table,
            hwm_guard,
            hwm_catalog,
        )

    before = await inspect()

    def downgrade(sync_connection):
        migration = load_migration("0044")
        migration.op = Operations(MigrationContext.configure(sync_connection))
        migration.downgrade()

    with pytest.raises(
        DBAPIError, match="account_measured_hwm_v2_downgrade_requires_empty"
    ):
        async with database[0].begin() as connection:
            await connection.run_sync(downgrade)
    assert await inspect() == before
    assert await _row_count(database, scope) == 1


@pytest.mark.parametrize(
    "locked_parent", ("demo_account_capture_events", "demo_account_observation_batches")
)
async def test_b5_downgrade_nowait_refuses_concurrent_parent_reader(
    monkeypatch, database, locked_parent
):
    await _first(monkeypatch, database)

    def downgrade(sync_connection):
        migration = load_migration("0044")
        migration.op = Operations(MigrationContext.configure(sync_connection))
        migration.downgrade()

    async with database[0].begin() as holder:
        await holder.execute(
            text(f"LOCK TABLE public.{locked_parent} IN ACCESS SHARE MODE")
        )
        with pytest.raises(DBAPIError, match="could not obtain lock"):
            async with database[0].begin() as contender:
                await contender.run_sync(downgrade)
    async with database[0].connect() as connection:
        assert await connection.run_sync(database_head) == "0044"
