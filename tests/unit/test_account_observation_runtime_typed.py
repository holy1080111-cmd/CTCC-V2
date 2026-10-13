"""Owned typed B3 handoff; every network response is synthetic."""

import json

import pytest

from app.database.repositories.account_capture_journal import (
    AccountCaptureJournalRepository,
)
from app.database.repositories.account_observation_index_typed import (
    AccountObservationTypedIndexRepository,
    TypedObservationBatchReadback,
)
from app.trade_qualification import account_history_query_verifier as query
from app.trade_qualification import account_observation_index as observed
from app.trade_qualification import account_observation_runtime as runtime
from tests.unit.test_account_ingestion_journal_contracts import setup
from tests.unit.test_account_observation_index import WINDOW
from tests.unit.test_qualification_account_collector import SECRETS


@pytest.mark.asyncio
async def test_owned_typed_route_replays_b2a_before_db0040_append(monkeypatch):
    session, harness, _, args, events = setup(monkeypatch)
    typed_repository = AccountObservationTypedIndexRepository(None, clock=harness.clock)
    verified = []
    original = query.verify_history_query_chain_v2

    def verify(chain, **pins):
        result = original(chain, **pins)
        verified.append(result.receipt_sha256)
        return result

    async def append(self, scope, reference, window, *, expected_revision):
        assert self is typed_repository
        assert expected_revision == 0
        assert verified
        assert (
            reference.capture_id == json.loads(events[0].event.event_json)["capture_id"]
        )
        replay = observed.replay_observation_index_typed_v1(
            ((tuple(events), reference),), scope=scope, window=window
        )
        return TypedObservationBatchReadback(1, "a" * 64, b"{}", replay)

    monkeypatch.setattr(query, "verify_history_query_chain_v2", verify)
    monkeypatch.setattr(AccountObservationTypedIndexRepository, "append", append)
    result = await runtime.collect_observed_execution_window_typed_v1(
        session,
        **args,
        observation_repository=typed_repository,
        window=WINDOW,
        expected_revision=0,
    )
    receipt = json.loads(result.replay.receipt_json)
    assert receipt["schema_version"] == "ctcc.observed_account_execution_window.v3"
    assert receipt["captures"][0]["query_receipt_sha256"] == verified[0]
    assert result.account_complete is result.source_authenticity_verified is False
    assert result.execution_authority is False and result.admission == "DENY"
    assert receipt["account_complete"] is receipt["execution_authority"] is False
    assert receipt["admission"] == "DENY"
    assert len(verified) >= 2  # runtime and independent DB0040 replay
    assert all(request.method == "GET" for request in harness.requests)
    assert all(
        request.headers["x-simulated-trading"] == "1" for request in harness.requests
    )
    harness.assert_closed()
    count = len(harness.requests)
    with pytest.raises(observed.AccountObservationError):
        await runtime.collect_observed_execution_window_typed_v1(
            session,
            **args,
            observation_repository=typed_repository,
            window=WINDOW,
            expected_revision=0,
        )
    assert len(harness.requests) == count


@pytest.mark.asyncio
async def test_late_b2a_rejection_preserves_completed_b1_and_denies_append(monkeypatch):
    session, harness, _, args, events = setup(monkeypatch)
    typed_repository = AccountObservationTypedIndexRepository(None, clock=harness.clock)
    appended = []

    def fail(*_args, **_kwargs):
        raise query.HistoryQueryVerificationError("synthetic_private_detail")

    async def append(*_args, **_kwargs):
        appended.append(True)
        raise AssertionError("typed append must not run")

    monkeypatch.setattr(query, "verify_history_query_chain_v2", fail)
    monkeypatch.setattr(AccountObservationTypedIndexRepository, "append", append)
    with pytest.raises(
        observed.AccountObservationError, match="observation_typed_runtime_failed"
    ) as error:
        await runtime.collect_observed_execution_window_typed_v1(
            session,
            **args,
            observation_repository=typed_repository,
            window=WINDOW,
            expected_revision=0,
        )
    assert "synthetic_private_detail" not in str(error.value)
    assert (
        events
        and json.loads(events[-1].event.event_json)["outcome"] == "complete_recorded"
    )
    assert not appended
    harness.assert_closed()
    count = len(harness.requests)
    with pytest.raises(observed.AccountObservationError):
        await runtime.collect_observed_execution_window_typed_v1(
            session,
            **args,
            observation_repository=typed_repository,
            window=WINDOW,
            expected_revision=0,
        )
    assert len(harness.requests) == count


@pytest.mark.asyncio
async def test_partial_b1_failure_keeps_journal_and_burns_session(monkeypatch):
    session, harness, _, args, events = setup(monkeypatch)
    typed_repository = AccountObservationTypedIndexRepository(None, clock=harness.clock)
    original_append = AccountCaptureJournalRepository._append

    async def fail_after_first(self, scope, event):
        if events:
            raise RuntimeError(SECRETS[0])
        return await original_append(self, scope, event)

    monkeypatch.setattr(AccountCaptureJournalRepository, "_append", fail_after_first)
    with pytest.raises(
        observed.AccountObservationError, match="observation_typed_runtime_failed"
    ) as error:
        await runtime.collect_observed_execution_window_typed_v1(
            session,
            **args,
            observation_repository=typed_repository,
            window=WINDOW,
            expected_revision=0,
        )
    assert SECRETS[0] not in str(error.value)
    assert events and json.loads(events[0].event.event_json)["kind"] == "capture_start"
    assert session._used is True
    count = len(harness.requests)
    with pytest.raises(observed.AccountObservationError):
        await runtime.collect_observed_execution_window_typed_v1(
            session,
            **args,
            observation_repository=typed_repository,
            window=WINDOW,
            expected_revision=0,
        )
    assert len(harness.requests) == count
