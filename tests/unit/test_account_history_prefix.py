"""Bounded B3-prefix mechanics; synthetic rows are not original account proof."""

import json

import pytest

from app.database.repositories import account_history_prefix as prefix
from app.database.repositories.account_observation_index import (
    ObservationBatchReadback,
)
from app.trade_qualification import account_capture_journal as journal
from app.trade_qualification import account_observation_index as observed
from app.trade_qualification.reservations import LedgerScope

SCOPE = LedgerScope(account_id="12345678901234567890", settlement_currency="USDT")


def _batches(
    count,
    *,
    wrong_sequence=None,
    wrong_policy=None,
    wrong_anchor_previous=None,
    wrong_membership=None,
):
    scope_sha = journal.digest(
        journal.canonical(
            [SCOPE.environment, SCOPE.account_id, SCOPE.settlement_currency]
        )
    )
    result = []
    prior_event = prior_anchor = None
    for number in range(1, count + 1):
        source_membership = journal.digest(journal.canonical([]))
        coverage_membership = journal.digest(journal.canonical([]))
        anchor = {
            "through_sequence": number,
            "previous_sequence": number - 1,
            "previous_event_sha256": (
                "0" * 64 if number == wrong_anchor_previous else prior_event
            ),
            "previous_anchor_sha256": prior_anchor,
            "policy_sha256": observed.POLICY_SHA256,
            "scope_sha256": scope_sha,
            "source_facts_through_sequence": 0,
            "coverage_through_sequence": 0,
            "unresolved_findings_through_sequence": 0,
            "current_source_membership_sha256": (
                "0" * 64 if number == wrong_membership else source_membership
            ),
            "current_coverage_membership_sha256": coverage_membership,
        }
        replay = observed.ObservationReplay(journal.canonical({"blocking_reasons": []}))
        document = {
            "schema_version": "ctcc.account_observation_batch.v1",
            "scope_sha256": scope_sha,
            "sequence": number if number != wrong_sequence else number + 1,
            "previous_sha256": prior_event,
            "policy_sha256": (
                observed.POLICY_SHA256 if number != wrong_policy else "0" * 64
            ),
            "anchor_json": journal.canonical(anchor).decode(),
            "anchor_sha256": journal.digest(journal.canonical(anchor)),
            "receipt_sha256": replay.receipt_sha256,
            "source_membership_sha256": source_membership,
            "coverage_membership_sha256": coverage_membership,
            "reference": {
                "capture_id": f"{number:032x}",
                "head_sha256": "a" * 64,
                "plan_sha256": "b" * 64,
                "packet_sha256": "c" * 64,
                "session_binding_sha256": "d" * 64,
            },
            "account_complete": False,
            "execution_authority": False,
            "admission": "DENY",
        }
        encoded = journal.canonical(document)
        event_sha = journal.digest(encoded)
        result.append(ObservationBatchReadback(number, event_sha, encoded, replay))
        prior_event, prior_anchor = event_sha, document["anchor_sha256"]
    return tuple(result)


async def _read(monkeypatch, rows, *, changed_final=False, short_first=False):
    repository = prefix.AccountHistoryPrefixRepository(None, clock=lambda: None)
    calls = []
    head_calls = 0

    async def heads(_scope):
        nonlocal head_calls
        head_calls += 1
        last = rows[-1]
        return (
            len(rows),
            "f" * 64 if changed_final and head_calls == 2 else last.event_sha256,
            0,
            None,
            "e" * 64,
        )

    async def read(_scope, *, after, limit):
        calls.append((after, limit))
        return rows[after : after + limit - (1 if short_first and after == 0 else 0)]

    monkeypatch.setattr(repository, "_heads", heads)
    monkeypatch.setattr(repository.history, "read", read)
    return repository, calls


@pytest.mark.asyncio
async def test_complete_prefix_crosses_32_row_page_without_account_authority(
    monkeypatch,
):
    repo, calls = await _read(monkeypatch, _batches(33))
    result = await repo.read_full_prefix(SCOPE)
    value = json.loads(result.receipt_json)
    assert calls == [(0, 32), (32, 1)]
    assert value["through_sequence"] == 33
    assert value["entire_accepted_b3_index_prefix_replayed"] is True
    assert value["exchange_history_finality_verified"] is False
    assert value["source_authenticity_verified"] is False
    assert value["account_complete"] is result.account_complete is False
    assert value["execution_authority"] is result.execution_authority is False
    assert value["admission"] == result.admission == "DENY"
    assert SCOPE.account_id.encode() not in result.receipt_json


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    [
        "short_page",
        "sequence",
        "policy",
        "anchor_previous",
        "membership",
        "head_changed",
    ],
)
async def test_prefix_rejects_missing_malformed_or_changed_history(monkeypatch, case):
    rows = _batches(
        33,
        wrong_sequence=17 if case == "sequence" else None,
        wrong_policy=17 if case == "policy" else None,
        wrong_anchor_previous=17 if case == "anchor_previous" else None,
        wrong_membership=17 if case == "membership" else None,
    )
    repo, _ = await _read(
        monkeypatch,
        rows,
        changed_final=case == "head_changed",
        short_first=case == "short_page",
    )
    with pytest.raises(
        prefix.AccountHistoryPrefixError, match="history_prefix_read_failed"
    ):
        await repo.read_full_prefix(SCOPE)
