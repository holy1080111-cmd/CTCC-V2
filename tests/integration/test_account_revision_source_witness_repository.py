"""Isolated PostgreSQL source binding; synthetic GET only, always DENY.

Requires an explicit migrated DATABASE_URL. No exchange credentials, order
writes, positive account revision or transport permission are involved.
"""

import asyncio
import json
from datetime import timedelta

import pytest
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
from app.trade_qualification.reservations import QualificationLedgerError
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
