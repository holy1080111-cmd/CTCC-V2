"""Isolated PostgreSQL B3 persistence; HTTP input is explicitly synthetic."""

import asyncio
import json
from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.database.repositories.account_observation_index import (
    AccountObservationIndexRepository,
    Batch,
    Coverage,
    Fact,
    Finding,
    _keys,
)
from app.trade_qualification import account_bootstrap_runtime as bootstrap
from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_observation_index as observed
from app.trade_qualification.account_runtime import ControlledDemoAccountSession
from app.trade_qualification.reservations import LedgerScope
from tests.integration.test_account_history_query_verifier_repository import (
    now,
    persisted_capture,
)
from tests.integration.test_qualification_ledger_repository import (
    database as database,  # noqa: PLC0414
)
from tests.unit.test_qualification_account_capture import NOW, ms, row
from tests.unit.test_qualification_account_collector import Harness, credentials
from tests.unit.test_qualification_account_materializer import source_pages
from tests.unit.test_qualification_account_runtime import regional

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def additional_capture(monkeypatch, repo, scope, fills):
    current = now()
    anchor = current.replace(
        microsecond=current.microsecond // 1000 * 1000
    ) - timedelta(seconds=10)
    offset = int(ms(anchor)) - int(ms(NOW))

    def shift(value):
        if type(value) is list:
            return [shift(item) for item in value]
        if type(value) is dict:
            return {
                key: str(int(item) + offset)
                if key in {"ts", "cTime", "uTime", "fillTime"}
                and type(item) is str
                and item.isdigit()
                else scope.account_id
                if key == "uid"
                else shift(item)
                for key, item in value.items()
            }
        return value

    pages = source_pages()
    pages["fills_history"] = [fills, []] if fills else [[]]
    harness = Harness(
        monkeypatch,
        pages=pages,
        change=lambda stream, index, data: (
            data if stream == "fills_history" else shift(data)
        ),
    )
    harness.clock = now
    plan = regional(
        expected_uid=scope.account_id,
        created_at=anchor,
        history_start=anchor - timedelta(days=6),
        history_end=anchor - timedelta(seconds=1),
    )
    session = ControlledDemoAccountSession(
        credentials=credentials(),
        plan=plan,
        expected_plan_sha256=capture.plan_sha256(plan),
    )
    result = await bootstrap.collect_bootstrap_recorded(
        session,
        repository=repo.journal.ledger,
        journal_repository=repo.journal,
        clock=now,
        barrier_completed_at=anchor - timedelta(seconds=2),
    )
    chain = await repo.journal.read_chain(
        scope, json.loads(result.journal_receipt_json)["capture_id"]
    )
    harness.assert_closed()
    return observed.source_reference(chain), observed.ExecutionWindow(
        plan.history_start, plan.history_end - timedelta(seconds=600)
    )


def draft_members(scope, facts, coverage, findings):
    members = []
    for ordinal, item in enumerate(facts, 1):
        members.append(
            Fact(
                **_keys(scope),
                batch_sequence=1,
                ordinal=ordinal,
                **{
                    key: item[key]
                    for key in ("family", "product", "identity_sha256", "row_sha256")
                },
                metadata_sha256=item["metadata_observations"][0]["row_sha256"],
                generation_at=datetime.fromisoformat(item["generation_at"]),
                fill_at=None
                if item["fill_at"] is None
                else datetime.fromisoformat(item["fill_at"]),
                fact_json=observed.journal.canonical(item).decode(),
            )
        )
    for ordinal, item in enumerate(coverage, 1):
        members.append(
            Coverage(
                **_keys(scope),
                batch_sequence=1,
                ordinal=ordinal,
                family=item["family"],
                product=item["product"],
                started_at=datetime.fromisoformat(item["started_at"]),
                ended_at=datetime.fromisoformat(item["ended_at"]),
                coverage_json=observed.journal.canonical(item).decode(),
            )
        )
    for ordinal, item in enumerate(findings, 1):
        members.append(
            Finding(
                **_keys(scope),
                batch_sequence=1,
                ordinal=ordinal,
                finding_json=observed.journal.canonical(item).decode(),
            )
        )
    return members


async def test_normalized_database_constraints_rollback_and_preserve_original_journal(
    monkeypatch, database
):
    repo, scope, _, _ = await prepared(monkeypatch, database)
    # Actual B1-derived rows include an explicit unknown, so every normalized
    # family is nonempty. This test never supplies a trusted-result flag.
    sample = row(
        "fills_history", fillPnl="-3", ts=ms(now() - timedelta(seconds=40)), fillTime=""
    )
    reference, window = await additional_capture(
        monkeypatch,
        repo,
        scope,
        [sample, {**sample, "billId": "899", "tradeId": "699"}],
    )
    document, replay, facts, coverage, findings, chain = await repo._derive(
        scope, reference, window, 1
    )
    assert len(facts) == 2 and coverage and findings
    cases = (
        "missing_fact",
        "ordinal_gap",
        "aggregate_hash",
        "fact_family",
        "fact_time",
        "fact_metadata",
        "coverage_time",
        "missing_coverage",
        "missing_finding",
    )
    for case in cases:
        doc = json.loads(document)
        members = draft_members(scope, facts, coverage, findings)
        if case in {"missing_fact", "ordinal_gap"}:
            removed_ordinal = 2 if case == "missing_fact" else 1
            members = [
                item
                for item in members
                if not (type(item) is Fact and item.ordinal == removed_ordinal)
            ]
        elif case == "aggregate_hash":
            doc["source_membership_sha256"] = "0" * 64
        elif case == "fact_family":
            next(item for item in members if type(item) is Fact).family = "bills"
        elif case == "fact_time":
            next(
                item for item in members if type(item) is Fact
            ).generation_at += timedelta(seconds=1)
        elif case == "fact_metadata":
            next(item for item in members if type(item) is Fact).metadata_sha256 = (
                "0" * 64
            )
        elif case == "coverage_time":
            next(
                item for item in members if type(item) is Coverage
            ).started_at += timedelta(seconds=1)
        elif case == "missing_coverage":
            members = [item for item in members if type(item) is not Coverage]
        elif case == "missing_finding":
            members = [item for item in members if type(item) is not Finding]
        raw = observed.journal.canonical(doc)
        with pytest.raises(DBAPIError):
            async with database[1]() as session, session.begin():
                session.add(
                    Batch(
                        **_keys(scope),
                        sequence=1,
                        capture_id=reference.capture_id,
                        source_sequence=len(chain),
                        previous_sha256=None,
                        event_sha256=observed.journal.digest(raw),
                        document_json=raw.decode(),
                        receipt_json=replay.receipt_json.decode(),
                    )
                )
                await session.flush()
                session.add_all(members)
                await session.flush()
        assert await repo._rows(scope) == []
    accepted = await repo.append(scope, reference, window, expected_revision=0)
    for model in (Fact, Coverage, Finding):
        for operation in ("UPDATE", "DELETE", "TRUNCATE"):
            sql = (
                f"UPDATE {model.__tablename__} SET ordinal=ordinal WHERE account_id=:uid"
                if operation == "UPDATE"
                else f"DELETE FROM {model.__tablename__} WHERE account_id=:uid"
                if operation == "DELETE"
                else f"TRUNCATE {model.__tablename__}"
            )
            with pytest.raises(DBAPIError):
                async with database[1]() as session, session.begin():
                    await session.execute(text(sql), {"uid": scope.account_id})
        async with database[1]() as session:
            existing = await session.scalar(
                select(model).filter_by(**_keys(scope)).limit(1)
            )
            values = {
                column.name: getattr(existing, column.name)
                for column in model.__table__.columns
            }
        for ordinal in (values["ordinal"], 100):
            with pytest.raises(DBAPIError):
                async with database[1]() as session, session.begin():
                    session.add(model(**{**values, "ordinal": ordinal}))
                    await session.flush()
    assert await repo.read(scope) == (accepted,)
    assert [
        item.event
        for item in await repo.journal.read_chain(scope, reference.capture_id)
    ] == [item.event for item in chain]


async def test_continuation_rejects_truncated_prefix_counts(monkeypatch, database):
    repo, scope, _, _, first = await stored(monkeypatch, database)
    reference, window = await additional_capture(monkeypatch, repo, scope, [])
    original = AccountObservationIndexRepository._witnesses
    for field in ("facts", "coverage", "findings"):

        async def truncated(self, *args, field=field):
            selected, counts, overflow = await original(self, *args)
            counts[field] += 1
            return selected, counts, overflow

        monkeypatch.setattr(AccountObservationIndexRepository, "_witnesses", truncated)
        with pytest.raises(observed.AccountObservationError):
            await repo.append(scope, reference, window, expected_revision=1)
    monkeypatch.setattr(AccountObservationIndexRepository, "_witnesses", original)
    assert await repo.read(scope) == (first,)
    assert await repo.journal.read_chain(scope, reference.capture_id)


async def test_continues_past_32_and_preserves_prefix_missing_conflict_late_evidence(
    monkeypatch, database
):
    scope = LedgerScope(
        account_id=f"123456789{uuid4().int % 10**12:012d}", settlement_currency="USDT"
    )
    repo = AccountObservationIndexRepository(database[1], clock=now)
    await repo.journal.ledger.initialize_capture_scope(scope)
    sample = row(
        "fills_history",
        fillPnl="-3",
        fee="-0.1",
        ts=ms(now() - timedelta(seconds=40)),
        fillTime=ms(now() - timedelta(seconds=900)),
    )
    first = None
    for revision in range(33):
        reference, window = await additional_capture(monkeypatch, repo, scope, [sample])
        current = await repo.append(
            scope, reference, window, expected_revision=revision
        )
        value = json.loads(current.replay.receipt_json)
        assert current.sequence == revision + 1
        assert value["observed_fill_cashflow_total"] == {
            "numerator": "-31",
            "denominator": "10",
        }
        assert not value["findings"]
        assert (
            value["continuation_anchor"]["source_facts_through_sequence"]
            == revision + 1
        )
        assert len(value["captures"]) <= 3
        if first is None:
            first = current
    assert len(await repo._rows(scope)) == 32
    assert len(await repo._rows(scope, after=32)) == 1
    assert (await repo.read(scope, after=32, limit=1))[0] == current
    # Missing and conflicting rows are compared with sequence 1 despite the
    # page boundary. Returning the first raw variant cannot clear findings.
    for revision, fills, expected in (
        (33, [], "previous_row_missing_in_current_query"),
        (34, [{**sample, "fee": "-0.2"}], "conflicting_overlap"),
        (35, [sample], None),
    ):
        reference, window = await additional_capture(monkeypatch, repo, scope, fills)
        current = await repo.append(
            scope, reference, window, expected_revision=revision
        )
        value = json.loads(current.replay.receipt_json)
        assert value["observed_fill_cashflow_total"] is None
        if expected:
            assert expected in {item["kind"] for item in value["findings"]}
    # New generation, old fill occurrence: the original empty membership for
    # this different identity is the witness, not the last 32-capture suffix.
    late = row(
        "fills_history",
        "901",
        tradeId="701",
        fillPnl="-1",
        fee="0",
        fillTime=sample["fillTime"],
        ts=ms(now() - timedelta(seconds=11)),
    )
    reference, window = await additional_capture(
        monkeypatch, repo, scope, [late, sample]
    )
    current = await repo.append(scope, reference, window, expected_revision=36)
    value = json.loads(current.replay.receipt_json)
    assert "late_execution_discovered" in {item["kind"] for item in value["findings"]}
    assert 1 in {
        item["sequence"] for item in value["continuation_anchor"]["verified_witnesses"]
    }
    assert value["observed_fill_cashflow_total"] is None
    assert (await repo.read(scope, limit=1))[0] == first
    restarted = AccountObservationIndexRepository(database[1], clock=now)
    assert (await restarted.read(scope, after=36, limit=1))[0] == current
    assert (
        await repo.journal.ledger.read_bootstrap_checkpoint(scope)
    ).state.account_revision == 0


async def prepared(monkeypatch, database):
    _, chain, _, _, scope = await persisted_capture(monkeypatch, database)
    reference = observed.source_reference(chain)
    query = json.loads(
        observed.query.verify_history_query_chain(
            chain, **observed._pins(reference, scope)
        ).receipt_json
    )
    window = observed.ExecutionWindow(
        datetime.fromisoformat(query["requested_start"]),
        datetime.fromisoformat(query["requested_end"]) - timedelta(seconds=600),
    )
    repo = AccountObservationIndexRepository(database[1], clock=now)
    return repo, scope, reference, window


async def stored(monkeypatch, database):
    repo, scope, reference, window = await prepared(monkeypatch, database)
    result = await repo.append(scope, reference, window, expected_revision=0)
    return repo, scope, reference, window, result


async def test_restart_replays_original_journal_and_idempotency(monkeypatch, database):
    repo, scope, reference, window, result = await stored(monkeypatch, database)
    again = AccountObservationIndexRepository(database[1], clock=now)
    rows = await again.read(scope)
    assert rows == (result,)
    assert await repo.append(scope, reference, window, expected_revision=0) == result
    assert not result.execution_authority
    assert json.loads(result.replay.receipt_json)["account_complete"] is False
    assert (
        await repo.journal.ledger.read_bootstrap_checkpoint(scope)
    ).state.account_revision == 0


@pytest.mark.parametrize("operation", ["update", "delete", "truncate"])
async def test_database_rejects_mutation_without_losing_durable_source(
    monkeypatch, database, operation
):
    repo, scope, _, _, result = await stored(monkeypatch, database)
    statements = {
        "update": "UPDATE demo_account_observation_batches SET receipt_json=receipt_json WHERE account_id=:uid",
        "delete": "DELETE FROM demo_account_observation_batches WHERE account_id=:uid",
        "truncate": "TRUNCATE demo_account_observation_batches",
    }
    with pytest.raises(DBAPIError):
        async with database[1]() as session, session.begin():
            await session.execute(
                text(statements[operation]), {"uid": scope.account_id}
            )
    assert await repo.read(scope) == (result,)


async def test_wrong_revision_and_caller_result_do_not_enter_repository(
    monkeypatch, database
):
    repo, scope, reference, window, result = await stored(monkeypatch, database)
    with pytest.raises(observed.AccountObservationError):
        await repo.append(scope, reference, window, expected_revision=True)
    with pytest.raises(TypeError):
        await repo.append(
            scope, result.replay, window, expected_revision=0, passed=True
        )
    assert await repo.read(scope) == (result,)


async def test_concurrent_identical_ingestion_is_at_most_one_append(
    monkeypatch, database
):
    _, chain, _, _, scope = await persisted_capture(monkeypatch, database)
    reference = observed.source_reference(chain)
    query = json.loads(
        observed.query.verify_history_query_chain(
            chain, **observed._pins(reference, scope)
        ).receipt_json
    )
    window = observed.ExecutionWindow(
        datetime.fromisoformat(query["requested_start"]),
        datetime.fromisoformat(query["requested_end"]) - timedelta(seconds=600),
    )
    repo = AccountObservationIndexRepository(database[1], clock=now)
    outcomes = await asyncio.gather(
        *[repo.append(scope, reference, window, expected_revision=0) for _ in range(2)],
        return_exceptions=True,
    )
    assert any(not isinstance(result, Exception) for result in outcomes)
    assert len(await repo.read(scope)) == 1


async def test_late_readback_failure_does_not_delete_append(monkeypatch, database):
    repo, scope, reference, window = await prepared(monkeypatch, database)
    original = AccountObservationIndexRepository.read

    async def fail(*args, **kwargs):
        raise RuntimeError("private rejected late read")

    monkeypatch.setattr(AccountObservationIndexRepository, "read", fail)
    with pytest.raises(observed.AccountObservationError):
        await repo.append(scope, reference, window, expected_revision=0)
    monkeypatch.setattr(AccountObservationIndexRepository, "read", original)
    committed = await AccountObservationIndexRepository(database[1], clock=now).read(
        scope
    )
    assert len(committed) == 1
    assert json.loads(committed[0].document_json)[
        "reference"
    ] == observed.reference_document(reference)


async def test_rehashed_untrusted_read_view_must_replay_original_sources(
    monkeypatch, database
):
    repo, scope, _, _, result = await stored(monkeypatch, database)
    rows = await repo._rows(scope)
    forged = json.loads(rows[0].receipt_json)
    forged["observed_fill_cashflow_total"] = {"numerator": "1337", "denominator": "1"}
    rows[0].receipt_json = observed.journal.canonical(forged).decode()
    document = json.loads(rows[0].document_json)
    document["receipt_sha256"] = observed.journal.digest(rows[0].receipt_json.encode())
    rows[0].document_json = observed.journal.canonical(document).decode()
    rows[0].event_sha256 = observed.journal.digest(rows[0].document_json.encode())
    original = AccountObservationIndexRepository._rows

    async def fake(*args, **kwargs):
        return rows

    monkeypatch.setattr(AccountObservationIndexRepository, "_rows", fake)
    with pytest.raises(observed.AccountObservationError):
        await repo.read(scope)
    monkeypatch.setattr(AccountObservationIndexRepository, "_rows", original)
    assert await repo.read(scope) == (result,)


async def test_second_capture_late_execution_invalidation_survives_restart(
    monkeypatch, database
):
    repo, scope, _, _, first = await stored(monkeypatch, database)
    current = now()
    anchor = current.replace(
        microsecond=current.microsecond // 1000 * 1000
    ) - timedelta(seconds=10)
    shift_ms = int(ms(anchor)) - int(ms(NOW))

    def shift(value):
        if type(value) is list:
            return [shift(item) for item in value]
        if type(value) is dict:
            return {
                key: str(int(item) + shift_ms)
                if key in {"ts", "cTime", "uTime", "fillTime"}
                and type(item) is str
                and item.isdigit()
                else scope.account_id
                if key == "uid"
                else shift(item)
                for key, item in value.items()
            }
        return value

    pages = source_pages()
    pages["fills_history"] = [
        [row("fills_history", fillTime=ms(NOW - timedelta(seconds=900)), fillPnl="-3")],
        [],
    ]
    harness = Harness(
        monkeypatch, pages=pages, change=lambda stream, index, data: shift(data)
    )
    harness.clock = now
    plan = regional(
        expected_uid=scope.account_id,
        created_at=anchor,
        history_start=anchor - timedelta(days=7),
        history_end=anchor - timedelta(seconds=1),
    )
    session = ControlledDemoAccountSession(
        credentials=credentials(),
        plan=plan,
        expected_plan_sha256=capture.plan_sha256(plan),
    )
    result = await bootstrap.collect_bootstrap_recorded(
        session,
        repository=repo.journal.ledger,
        journal_repository=repo.journal,
        clock=now,
        barrier_completed_at=anchor - timedelta(seconds=2),
    )
    cid = json.loads(result.journal_receipt_json)["capture_id"]
    chain = await repo.journal.read_chain(scope, cid)
    second = await repo.append(
        scope,
        observed.source_reference(chain),
        observed.ExecutionWindow(
            plan.history_start, plan.history_end - timedelta(seconds=600)
        ),
        expected_revision=1,
    )
    rebuilt = AccountObservationIndexRepository(database[1], clock=now)
    assert await rebuilt.read(scope) == (first, second)
    data = json.loads(second.replay.receipt_json)
    assert len(data["captures"]) == 2 and len(data["cashflows"]) == 1
    assert data["findings"] and data["observed_fill_cashflow_total"] is None
    assert json.loads(first.replay.receipt_json)["source_index"] == []
    assert (
        await rebuilt.journal.ledger.read_bootstrap_checkpoint(scope)
    ).state.account_revision == 0
    harness.assert_closed()
