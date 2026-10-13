"""DB0040 typed B3 persistence over synthetic, immutable Demo B1 captures."""

import asyncio
import json

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from app.database.repositories.account_observation_index_typed import (
    PERSISTENCE_POLICY_SHA256,
    AccountObservationTypedIndexRepository,
    Batch,
)
from app.trade_qualification import account_capture_journal as journal
from app.trade_qualification import account_history_query_verifier as query
from app.trade_qualification import account_observation_index as observed
from tests.integration.test_account_observation_index_repository import (
    additional_capture,
    now,
    stored,
)
from tests.integration.test_durable_migration_downgrade import migrate, shape
from tests.integration.test_durable_migration_downgrade import (
    sandbox as sandbox,  # noqa: PLC0414
)
from tests.integration.test_qualification_ledger_repository import (
    database as database,  # noqa: PLC0414
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_old_b3_and_typed_b3_coexist_with_exact_original_source(
    monkeypatch, database
):
    old, scope, reference, window, old_receipt = await stored(monkeypatch, database)
    old_bytes = old_receipt.replay.receipt_json
    old_document = old_receipt.document_json
    original = await old.journal.read_chain(scope, reference.capture_id)
    pins = observed._pins(reference, scope)
    predecessor = query.verify_history_query_chain(original, **pins)
    typed_source = query.verify_history_query_chain_v2(original, **pins)

    typed = AccountObservationTypedIndexRepository(database[1], clock=now)
    committed = await typed.append(scope, reference, window, expected_revision=0)
    value = json.loads(committed.replay.receipt_json)
    document = json.loads(committed.document_json)
    assert document["policy_sha256"] == PERSISTENCE_POLICY_SHA256
    assert document["typed_policy_sha256"] == observed.TYPED_POLICY_SHA256
    assert value["schema_version"] == "ctcc.observed_account_execution_window.v3"
    assert value["captures"][0]["query_receipt_sha256"] == typed_source.receipt_sha256
    assert (
        value["captures"][0]["legacy_v1_query_receipt_sha256"]
        == predecessor.receipt_sha256
    )
    assert value["account_complete"] is value["execution_authority"] is False
    assert value["source_authenticity_verified"] is False
    assert value["admission"] == "DENY"
    assert all("generation_at" not in item for item in value["source_index"])
    assert await old.read(scope) == (old_receipt,)
    assert old_receipt.replay.receipt_json == old_bytes
    assert old_receipt.document_json == old_document
    restarted = AccountObservationTypedIndexRepository(database[1], clock=now)
    assert await restarted.read(scope) == (committed,)
    assert (
        await typed.append(scope, reference, window, expected_revision=0) == committed
    )


async def test_typed_prefix_extends_without_touching_legacy_prefix(
    monkeypatch, database
):
    old, scope, reference, window, old_receipt = await stored(monkeypatch, database)
    typed = AccountObservationTypedIndexRepository(database[1], clock=now)
    first = await typed.append(scope, reference, window, expected_revision=0)
    next_reference, next_window = await additional_capture(monkeypatch, old, scope, [])
    second = await typed.append(scope, next_reference, next_window, expected_revision=1)
    assert second.sequence == 2
    assert second.event_sha256 != first.event_sha256
    assert (
        json.loads(second.replay.receipt_json)["continuation_anchor"][
            "previous_event_sha256"
        ]
        == first.event_sha256
    )
    assert await typed.read(scope) == (first, second)
    assert await old.read(scope) == (old_receipt,)


async def test_typed_append_concurrency_is_one_durable_row(monkeypatch, database):
    old, scope, reference, window, _ = await stored(monkeypatch, database)
    typed = AccountObservationTypedIndexRepository(database[1], clock=now)
    outcomes = await asyncio.gather(
        typed.append(scope, reference, window, expected_revision=0),
        typed.append(scope, reference, window, expected_revision=0),
        return_exceptions=True,
    )
    assert any(not isinstance(item, Exception) for item in outcomes)
    async with database[1]() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(Batch)
            .filter_by(
                environment=scope.environment,
                account_id=scope.account_id,
                settlement_currency=scope.settlement_currency,
            )
        )
    assert count == 1
    assert (await typed.read(scope))[0].admission == "DENY"
    assert await old.read(scope)


async def test_typed_batches_are_immutable_and_original_legacy_survives(
    monkeypatch, database
):
    old, scope, reference, window, old_receipt = await stored(monkeypatch, database)
    typed = AccountObservationTypedIndexRepository(database[1], clock=now)
    committed = await typed.append(scope, reference, window, expected_revision=0)
    for statement in (
        "UPDATE public.demo_account_observation_typed_batches SET receipt_json=receipt_json WHERE capture_id=:capture_id",
        "DELETE FROM public.demo_account_observation_typed_batches WHERE capture_id=:capture_id",
        "TRUNCATE public.demo_account_observation_typed_batches",
    ):
        with pytest.raises(DBAPIError):
            async with database[1]() as session, session.begin():
                await session.execute(
                    text(statement), {"capture_id": reference.capture_id}
                )
    assert await typed.read(scope) == (committed,)
    assert await old.read(scope) == (old_receipt,)


async def test_database_refuses_forged_typed_policy_even_with_matching_hash(
    monkeypatch, database
):
    old, scope, reference, window, old_receipt = await stored(monkeypatch, database)
    typed = AccountObservationTypedIndexRepository(database[1], clock=now)
    document, replay, _, _, _, chain, _ = await typed._derive_with_current_source(
        scope, reference, window, 1
    )
    forged = json.loads(document)
    forged["typed_policy_sha256"] = "0" * 64
    bytes_to_store = journal.canonical(forged)
    with pytest.raises(DBAPIError):
        async with database[1]() as session, session.begin():
            session.add(
                Batch(
                    environment=scope.environment,
                    account_id=scope.account_id,
                    settlement_currency=scope.settlement_currency,
                    sequence=1,
                    capture_id=reference.capture_id,
                    source_sequence=len(chain),
                    previous_sha256=None,
                    event_sha256=journal.digest(bytes_to_store),
                    document_json=bytes_to_store.decode(),
                    receipt_json=replay.receipt_json.decode(),
                )
            )
            await session.flush()
    assert await typed.read(scope) == ()
    assert await old.read(scope) == (old_receipt,)


@pytest.mark.parametrize("revision", ["0040"])
async def test_db0040_empty_downgrade_and_reupgrade_preserves_exact_schema(
    sandbox, revision
):
    engine, _ = sandbox
    before = await shape(engine)
    async with engine.begin() as connection:
        await connection.run_sync(migrate, revision, "downgrade")
    assert await shape(engine) != before
    async with engine.begin() as connection:
        await connection.run_sync(migrate, revision, "upgrade")
    assert await shape(engine) == before


@pytest.mark.parametrize("revision", ["0040"])
async def test_db0040_nonempty_downgrade_keeps_old_and_typed_evidence(
    monkeypatch, sandbox, revision
):
    engine, _ = sandbox
    old, scope, reference, window, legacy = await stored(monkeypatch, sandbox)
    typed = AccountObservationTypedIndexRepository(sandbox[1], clock=now)
    accepted = await typed.append(scope, reference, window, expected_revision=0)
    before = await shape(engine)
    with pytest.raises(
        DBAPIError, match="account_observation_typed_downgrade_requires_empty"
    ):
        async with engine.begin() as connection:
            await connection.run_sync(migrate, revision, "downgrade")
    assert await shape(engine) == before
    assert await old.read(scope) == (legacy,)
    assert await typed.read(scope) == (accepted,)
