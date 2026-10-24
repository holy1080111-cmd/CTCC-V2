"""Isolated PostgreSQL source binding; synthetic GET only, always DENY.

Requires an explicit migrated DATABASE_URL. No exchange credentials, order
writes, positive account revision or transport permission are involved.
"""

import asyncio
import json
from datetime import timedelta

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import func, insert, select, text
from sqlalchemy.exc import DBAPIError

from app.database.models.account_revision_source_witness import (
    DemoAccountRevisionSourceWitness as Witness,
)
from app.database.models.qualification_ledger import QualificationAccountScope
from app.database.repositories.account_current_observation import (
    AccountCurrentObservationRepository,
)
from app.database.repositories.account_observation_index_typed import (
    AccountObservationTypedIndexRepository,
)
from app.database.repositories.account_revision_source_witness import (
    AccountRevisionSourceError,
    AccountRevisionSourceWitnessRepository,
)
from app.database.repositories.qualification_ledger import QualificationLedgerRepository
from app.trade_qualification import account_capture_journal as journal
from app.trade_qualification import account_history_query_verifier as history_query
from app.trade_qualification import account_observation_index as observed
from app.trade_qualification import account_revision_recorded_claim as recorded_claim
from app.trade_qualification.reservations import QualificationLedgerError
from scripts.verify_final_hermetic import (
    ACCOUNT_MEASURED_HWM_V2_TRIGGER_CATALOG_SQL,
    ACCOUNT_REVISION_WITNESS_TRIGGER_CATALOG_SQL,
    NATIVE_WITNESS_IDENTITY_SCHEMA_SQL,
    PUBLIC_RUNTIME_BUNDLE_SCHEMA_SQL,
    migration_revision_readback,
)
from scripts.verify_migration_identity import database_head
from tests.durable_migration_fixtures import load_migration
from tests.integration.test_account_locked_current_history_join_v7_repository import (
    _captured_v7_pair,
    _now,
)
from tests.integration.test_qualification_ledger_repository import (
    database as database,  # noqa: PLC0414 -- explicit isolated PG fixture
)
from tests.integration.test_qualification_ledger_repository import refresh
from tests.unit.qualification_ledger_fixtures import ledger_fixture

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def _sources(monkeypatch, database):
    scope, journal, (history_id, current_id) = await _captured_v7_pair(
        monkeypatch, database
    )
    history_chain = await journal.read_chain(scope, history_id)
    current_chain = await journal.read_chain(scope, current_id)
    history_ref = observed.source_reference(history_chain)
    current_ref = observed.source_reference(current_chain)
    query = json.loads(
        history_query.verify_history_query_chain_v2(
            history_chain, **observed._pins(history_ref, scope)
        ).receipt_json
    )
    window = observed.ExecutionWindow(
        history_query._time(query["requested_start"]),
        history_query._time(query["requested_end"]) - timedelta(seconds=600),
    )
    typed = await AccountObservationTypedIndexRepository(
        database[1], clock=_now
    ).append(scope, history_ref, window, expected_revision=0)
    current = await AccountCurrentObservationRepository(database[1], clock=_now).append(
        scope, current_ref, expected_sequence=0
    )
    synthetic = await asyncio.to_thread(ledger_fixture, account_id=scope.account_id)
    ledger = QualificationLedgerRepository(database[1], clock=_now)
    claims = refresh(synthetic.claims, _now())
    state = await ledger.reconcile_scope(claims, expected_revision=0)
    assert state.account_revision == 1
    return scope, typed, current, ledger, claims


async def _committed_migration_witness_state(engine, witness_sha256):
    """Read the committed row and catalog through a new database connection."""
    async with engine.connect() as connection:
        head = await connection.run_sync(database_head)
        row = (
            await connection.execute(
                select(
                    Witness.witness_sha256,
                    Witness.witness_json,
                    Witness.claims_sha256,
                    Witness.admission,
                ).where(Witness.witness_sha256 == witness_sha256)
            )
        ).one()
        table_present = await connection.scalar(
            text(
                "SELECT pg_catalog.to_regclass('public.demo_account_revision_source_witnesses') IS NOT NULL"
            )
        )
        guard_present = await connection.scalar(
            text(
                "SELECT pg_catalog.to_regprocedure('public.account_revision_source_witness_guard()') IS NOT NULL"
            )
        )
        catalog = (
            await connection.execute(text(ACCOUNT_REVISION_WITNESS_TRIGGER_CATALOG_SQL))
        ).all()
        hwm_present = await connection.scalar(
            text(
                "SELECT pg_catalog.to_regclass('public.demo_account_measured_hwm_v2') IS NOT NULL"
            )
        )
        hwm_guard_present = await connection.scalar(
            text(
                "SELECT pg_catalog.to_regprocedure('public.account_measured_hwm_v2_guard()') IS NOT NULL"
            )
        )
        hwm_catalog = (
            await connection.execute(text(ACCOUNT_MEASURED_HWM_V2_TRIGGER_CATALOG_SQL))
        ).all()
        runtime_catalog = await connection.scalar(
            text(PUBLIC_RUNTIME_BUNDLE_SCHEMA_SQL)
        )
        native_catalog = await connection.scalar(
            text(NATIVE_WITNESS_IDENTITY_SCHEMA_SQL)
        )
        from scripts.verify_account_history_partition_schema_v1 import (
            read_account_history_partition_catalog,
        )
        from scripts.verify_controlled_writer_coverage_schema_v1 import read_catalog

        coverage_catalog = await read_catalog(connection)
        partition_catalog = await read_account_history_partition_catalog(connection)
    readback = migration_revision_readback(
        "0050",
        head,
        table_present,
        guard_present,
        catalog,
        hwm_present,
        hwm_guard_present,
        hwm_catalog,
        runtime_catalog,
        native_catalog,
        coverage_catalog=coverage_catalog,
        partition_catalog=partition_catalog,
    )
    return row, readback


async def test_owned_source_witness_binds_exact_sources_but_cannot_promote_claims(
    monkeypatch, database
):
    scope, typed, current, ledger, claims = await _sources(monkeypatch, database)
    repository = AccountRevisionSourceWitnessRepository(database[1], clock=_now)
    witness = await repository.record_denied_source_witness(
        scope,
        account_revision=1,
        typed_sequence=typed.sequence,
        current_sequence=current.sequence,
    )
    value = json.loads(witness.witness_json)
    assert value["schema_version"] == "ctcc.demo_account_revision_source_witness.v1"
    assert value["history"]["typed_head_sha256"] == typed.event_sha256
    assert value["current"]["index_sha256"] == current.event_sha256
    assert value["blocking_reasons"]
    assert witness.account_complete is witness.source_authenticity_verified is False
    assert witness.execution_authority is False and witness.admission == "DENY"
    assert (await repository.read_one(scope, witness.witness_sha256)) == witness
    assert (await ledger.read_scope(scope)).account_revision == 1
    with pytest.raises(
        QualificationLedgerError, match="ledger_owned_account_revision_missing"
    ):
        ledger._require_owned_account_revision(claims)


async def test_denied_source_claim_replays_exact_balance_in_separate_sessions(
    monkeypatch, database
):
    scope, typed, current, ledger, claims = await _sources(monkeypatch, database)
    repository = AccountRevisionSourceWitnessRepository(database[1], clock=_now)
    witness = await repository.record_denied_source_witness(
        scope,
        account_revision=1,
        typed_sequence=typed.sequence,
        current_sequence=current.sequence,
    )
    first = await repository.read_denied_recorded_source_claim(
        scope, witness.witness_sha256
    )
    second = await repository.read_denied_recorded_source_claim(
        scope, witness.witness_sha256
    )
    value = json.loads(first.receipt_json)
    assert first == second
    assert value["witness_sha256"] == witness.witness_sha256
    assert value["caller_account_revision"] == 1
    assert (
        value["recorded_current_reference"]["head_sha256"]
        == json.loads(witness.witness_json)["current"]["head_sha256"]
    )
    assert value["source_balance"]["equity"] == {
        "numerator": "1000",
        "denominator": "1",
    }
    assert value["source_balance"]["available_equity"] == {
        "numerator": "800",
        "denominator": "1",
    }
    assert value["account_revision_issued"] is first.account_revision_issued is False
    assert value["execution_authority"] is first.execution_authority is False
    assert value["admission"] == first.admission == "DENY"
    assert (await ledger.read_scope(scope)).account_revision == 1
    with pytest.raises(
        QualificationLedgerError, match="ledger_owned_account_revision_missing"
    ):
        ledger._require_owned_account_revision(claims)

    original = repository._recorded_claim_once
    calls = 0

    async def changed_on_second_read(*args):
        nonlocal calls
        result = await original(*args)
        calls += 1
        return recorded_claim.RecordedAccountClaim(b"{}") if calls == 2 else result

    monkeypatch.setattr(repository, "_recorded_claim_once", changed_on_second_read)
    with pytest.raises(
        AccountRevisionSourceError,
        match="account_revision_source_claim_readback_failed",
    ):
        await repository.read_denied_recorded_source_claim(
            scope, witness.witness_sha256
        )
    assert (await repository.read_one(scope, witness.witness_sha256)) == witness


async def test_committed_denial_blocks_0043_downgrade_and_rolls_back(
    monkeypatch, database
):
    scope, typed, current, _, _ = await _sources(monkeypatch, database)
    repository = AccountRevisionSourceWitnessRepository(database[1], clock=_now)
    witness = await repository.record_denied_source_witness(
        scope,
        account_revision=1,
        typed_sequence=typed.sequence,
        current_sequence=current.sequence,
    )
    assert witness.admission == "DENY" and witness.execution_authority is False
    before = await _committed_migration_witness_state(
        database[0], witness.witness_sha256
    )
    assert before[0].witness_sha256 == witness.witness_sha256
    assert before[1]["account_revision_witness_trigger_count"] == 3

    def downgrade_after_committed_readback(sync_connection):
        assert database_head(sync_connection) == "0050"
        assert (
            sync_connection.execute(
                text(
                    "SELECT count(*) FROM public.demo_account_revision_source_witnesses "
                    "WHERE witness_sha256=:witness_sha256 AND admission='DENY'"
                ),
                {"witness_sha256": witness.witness_sha256},
            ).scalar_one()
            == 1
        )
        migration = load_migration("0043")
        migration.op = Operations(MigrationContext.configure(sync_connection))
        migration.downgrade()

    with pytest.raises(
        DBAPIError, match="account_revision_source_witness_downgrade_requires_empty"
    ):
        async with database[0].begin() as connection:
            await connection.run_sync(downgrade_after_committed_readback)

    after = await _committed_migration_witness_state(
        database[0], witness.witness_sha256
    )
    assert after == before
    assert (await repository.read_one(scope, witness.witness_sha256)) == witness


async def test_witness_rejects_wrong_source_or_revision_without_row(
    monkeypatch, database
):
    scope, typed, current, _, _ = await _sources(monkeypatch, database)
    repository = AccountRevisionSourceWitnessRepository(database[1], clock=_now)
    for account_revision, typed_sequence, current_sequence in (
        (2, typed.sequence, current.sequence),
        (1, typed.sequence + 1, current.sequence),
        (1, typed.sequence, current.sequence + 1),
    ):
        with pytest.raises(AccountRevisionSourceError):
            await repository.record_denied_source_witness(
                scope,
                account_revision=account_revision,
                typed_sequence=typed_sequence,
                current_sequence=current_sequence,
            )
    async with database[1]() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(Witness)
            .filter_by(
                environment=scope.environment,
                account_id=scope.account_id,
                settlement_currency=scope.settlement_currency,
            )
        )
    assert count == 0


async def test_two_workers_cannot_publish_twice_for_one_revision(monkeypatch, database):
    scope, typed, current, _, _ = await _sources(monkeypatch, database)
    first = AccountRevisionSourceWitnessRepository(database[1], clock=_now)
    second = AccountRevisionSourceWitnessRepository(database[1], clock=_now)

    async def run(repository):
        return await repository.record_denied_source_witness(
            scope,
            account_revision=1,
            typed_sequence=typed.sequence,
            current_sequence=current.sequence,
        )

    outcomes = await asyncio.gather(run(first), run(second), return_exceptions=True)
    assert sum(not isinstance(item, Exception) for item in outcomes) == 1
    assert sum(isinstance(item, AccountRevisionSourceError) for item in outcomes) == 1
    async with database[1]() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(Witness)
            .filter_by(
                environment=scope.environment,
                account_id=scope.account_id,
                settlement_currency=scope.settlement_currency,
            )
        )
    assert count == 1


async def test_late_readback_failure_preserves_committed_denial(monkeypatch, database):
    scope, typed, current, _, _ = await _sources(monkeypatch, database)
    repository = AccountRevisionSourceWitnessRepository(database[1], clock=_now)
    original = repository.read_one

    async def unavailable(*_args):
        raise OSError("synthetic late readback failure")

    monkeypatch.setattr(repository, "read_one", unavailable)
    with pytest.raises(AccountRevisionSourceError):
        await repository.record_denied_source_witness(
            scope,
            account_revision=1,
            typed_sequence=typed.sequence,
            current_sequence=current.sequence,
        )
    monkeypatch.setattr(repository, "read_one", original)
    async with database[1]() as session:
        row = await session.scalar(
            select(Witness).filter_by(
                environment=scope.environment,
                account_id=scope.account_id,
                settlement_currency=scope.settlement_currency,
            )
        )
    assert row is not None and row.admission == "DENY"
    assert (await repository.read_one(scope, row.witness_sha256)).admission == "DENY"
    with pytest.raises(AccountRevisionSourceError):
        await repository.record_denied_source_witness(
            scope,
            account_revision=1,
            typed_sequence=typed.sequence,
            current_sequence=current.sequence,
        )


async def test_old_witness_replays_after_mutable_ledger_advances(monkeypatch, database):
    scope, typed, current, ledger, claims = await _sources(monkeypatch, database)
    repository = AccountRevisionSourceWitnessRepository(database[1], clock=_now)
    witness = await repository.record_denied_source_witness(
        scope,
        account_revision=1,
        typed_sequence=typed.sequence,
        current_sequence=current.sequence,
    )
    advanced = refresh(claims, _now())
    assert (
        await ledger.reconcile_scope(advanced, expected_revision=1)
    ).account_revision == 2
    assert (await repository.read_one(scope, witness.witness_sha256)) == witness
    assert witness.execution_authority is False


async def test_database_rejects_tamper_and_authority_flags(monkeypatch, database):
    scope, typed, current, _, _ = await _sources(monkeypatch, database)
    repository = AccountRevisionSourceWitnessRepository(database[1], clock=_now)
    witness = await repository.record_denied_source_witness(
        scope,
        account_revision=1,
        typed_sequence=typed.sequence,
        current_sequence=current.sequence,
    )
    async with database[1]() as session:
        with pytest.raises(DBAPIError):
            async with session.begin():
                await session.execute(
                    text(
                        "UPDATE public.demo_account_revision_source_witnesses SET admission='PASS' WHERE witness_sha256=:key"
                    ),
                    {"key": witness.witness_sha256},
                )
    async with database[1]() as session:
        with pytest.raises(DBAPIError):
            async with session.begin():
                await session.execute(
                    text(
                        "DELETE FROM public.demo_account_revision_source_witnesses WHERE witness_sha256=:key"
                    ),
                    {"key": witness.witness_sha256},
                )
    assert (
        await repository.read_one(scope, witness.witness_sha256)
    ).admission == "DENY"


@pytest.mark.parametrize(
    "field_path",
    (
        ("scope", "account_id"),
        ("scope", "settlement_currency"),
        ("account", "account_id"),
        ("account", "settlement_currency"),
        ("authority", "environment"),
        ("account", "balance_stamp", "account_id"),
    ),
)
async def test_sql_guard_rejects_nested_cross_account_claims(
    monkeypatch, database, field_path
):
    scope, typed, current, _, _ = await _sources(monkeypatch, database)
    repository = AccountRevisionSourceWitnessRepository(database[1], clock=_now)
    witness = await repository.record_denied_source_witness(
        scope,
        account_revision=1,
        typed_sequence=typed.sequence,
        current_sequence=current.sequence,
    )
    async with database[1]() as session:
        old = await session.get(Witness, witness.witness_sha256)
        assert old is not None
        source = {
            column.name: getattr(old, column.name)
            for column in Witness.__table__.columns
            if column.name != "db_recorded_at"
        }
    claims = json.loads(source["claims_json"])
    target = claims
    for key in field_path[:-1]:
        target = target[key]
    target[field_path[-1]] = "999999999" if field_path[-1] == "account_id" else "wrong"
    forged_claims = json.dumps(
        claims,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )
    forged_claims_sha = journal.digest(forged_claims.encode("utf-8"))
    document = json.loads(source["witness_json"])
    document.update(
        account_revision=2, ledger_revision=2, claims_sha256=forged_claims_sha
    )
    forged_witness = journal.canonical(document)
    source.update(
        account_revision=2,
        ledger_revision=2,
        claims_json=forged_claims,
        claims_sha256=forged_claims_sha,
        witness_json=forged_witness.decode("utf-8"),
        witness_sha256=journal.digest(forged_witness),
    )
    async with database[1]() as session, session.begin():
        row = await session.get(
            QualificationAccountScope,
            (scope.environment, scope.account_id, scope.settlement_currency),
        )
        assert row is not None and row.account_revision == row.ledger_revision == 1
        await session.execute(
            text(
                "UPDATE public.qualification_account_scopes "
                "SET claims_json=:claims_json, claims_sha256=:claims_sha256, "
                "account_revision=2, ledger_revision=2, updated_at=clock_timestamp() "
                "WHERE environment=:environment AND account_id=:account_id "
                "AND settlement_currency=:settlement_currency"
            ),
            {
                **{
                    key: getattr(scope, key)
                    for key in ("environment", "account_id", "settlement_currency")
                },
                "claims_json": forged_claims,
                "claims_sha256": forged_claims_sha,
            },
        )
    with pytest.raises(AccountRevisionSourceError):
        await repository.record_denied_source_witness(
            scope,
            account_revision=2,
            typed_sequence=typed.sequence,
            current_sequence=current.sequence,
        )
    async with database[1]() as session:
        with pytest.raises(DBAPIError) as denied:
            async with session.begin():
                await session.execute(insert(Witness).values(**source))
    assert "account_revision_witness_binding_denied" in str(denied.value.orig)
    assert (
        await repository.read_one(scope, witness.witness_sha256)
    ).admission == "DENY"
