"""Synthetic G5/journal join: a visible absence never becomes G6 authority."""

import asyncio
import inspect

import pytest

from app.database.repositories import qualification_ledger as ledger_repo
from app.domain.source_primitives import canonical, decode, sha
from app.trade_qualification import owned_original_base_event_v7 as g5
from app.trade_qualification import owned_original_event_ledger_v8 as boundary
from app.trade_qualification.reservations import LedgerScope, digest
from tests.unit.research.test_owned_original_source_coordinator_v2 import (
    clock,
    session,
)

EVENT = "a" * 64


def _g5_receipt(event=True):
    record = {
        "schema_version": g5._SCHEMA,
        "code": "base_g1_g5_inspected" if event else "native_precursor_unavailable",
        "base_diagnostic_sha256": "b" * 64,
        "original_diagnostic_sha256": "c" * 64,
        "public_packet_sha256": "d" * 64 if event else None,
        "account_packet_sha256": "e" * 64 if event else None,
        "precursor_receipt_sha256": "f" * 64 if event else None,
        "precursor_intent_sha256": "1" * 64 if event else None,
        "base_prefix_receipt_sha256": "2" * 64 if event else None,
        "event_receipt_sha256": "3" * 64 if event else None,
        "event_key_sha256": EVENT if event else None,
        "g1_g5_replayed": event,
        "admission": "DENY",
        **{name: False for name in g5._FALSE_FIELDS},
    }
    return g5.OwnedOriginalBaseEventDiagnosticV7(canonical(record))


def _journal(scope, keys, clock_source, *, alter=None, event_state="reserved"):
    observed = clock_source()
    received = clock_source()
    keys = frozenset(keys)
    value = {
        "schema_version": boundary._JOURNAL_SCHEMA,
        "scope_sha256": digest(scope),
        "account_revision": 1,
        "ledger_revision": 1,
        "claims_sha256": "4" * 64,
        "observed_at": observed.isoformat(),
        "received_at": received.isoformat(),
        "event_count": len(keys),
        "transition_count": len(keys),
        "state_counts": {
            state: len(keys) if state == event_state else 0
            for state in ("reserved", "consumed", "uncertain", "reconciled_flat")
        },
        "event_keys_sha256": sha(canonical(sorted(keys))),
        "rows_sha256": "5" * 64,
        "transitions_sha256": "6" * 64,
        "db_journal_row_chain_verified": True,
        "db_journal_complete": False,
        "external_event_history_complete": False,
        "source_authenticity_verified": False,
        "execution_authority": False,
        "admission": "DENY",
    }
    if alter:
        value.update(alter)
    return ledger_repo._ConsumedEventJournalInspection(
        keys, canonical(value), observed, received
    )


def _setup(
    monkeypatch,
    controlled,
    *,
    keys=(),
    second_keys=None,
    alter=None,
    event=True,
    event_state="reserved",
):
    monkeypatch.setattr(boundary, "native_stamp", clock())
    fixed = _g5_receipt(event)
    state = {"calls": 0, "same_task": asyncio.current_task()}

    async def source(*_args, **kwargs):
        assert asyncio.current_task() is state["same_task"]
        assert kwargs["account_session"] is controlled
        assert kwargs["session_factory"] is state["factory"]
        if event:
            controlled._used = True
        return fixed

    async def inspect_journal(self, scope):
        assert asyncio.current_task() is state["same_task"]
        assert self.session_factory is state["factory"]
        assert scope == LedgerScope(
            environment="demo",
            account_id=controlled._plan.expected_uid,
            settlement_currency="USDT",
        )
        state["calls"] += 1
        seen = keys if state["calls"] == 1 or second_keys is None else second_keys
        return _journal(scope, seen, self.clock, alter=alter, event_state=event_state)

    state["factory"] = object()
    monkeypatch.setattr(g5, "preflight_owned_base_event_v7", source)
    monkeypatch.setattr(
        ledger_repo.QualificationLedgerRepository,
        "inspect_consumed_event_journal",
        inspect_journal,
    )
    return state


async def _observe(controlled, factory):
    return await boundary.observe_owned_g5_event_ledger_v8(
        object(),
        object(),
        instrument_id="BTC-USDT-SWAP",
        strategy="fvg_return",
        market_policy=object(),
        account_session=controlled,
        session_factory=factory,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("keys", "event_state", "expected"),
    [
        ((), "reserved", "event_absent_from_controlled_journal_only"),
        ((EVENT,), "reserved", "event_seen_in_controlled_journal"),
        ((EVENT,), "reconciled_flat", "event_seen_in_controlled_journal"),
    ],
)
async def test_two_independent_reads_are_hash_only_and_never_g6(
    monkeypatch, keys, event_state, expected
):
    controlled = session()
    state = _setup(monkeypatch, controlled, keys=keys, event_state=event_state)
    result = await _observe(controlled, state["factory"])
    raw = decode(result.receipt_json)
    assert state["calls"] == 2
    assert raw["code"] == expected
    assert raw["event_seen_in_controlled_journal"] is bool(keys)
    assert raw["ledger_first_sha256"] != raw["ledger_second_sha256"]
    assert raw["ledger_revision"] == 1
    assert all(raw[name] is False for name in boundary._FALSE_FIELDS)
    assert result.admission == "DENY" and result.execution_authority is False
    assert controlled._plan.expected_uid.encode() not in result.receipt_json
    assert b"session_binding_id" not in result.receipt_json
    assert boundary.OwnedOriginalEventLedgerDiagnosticV8(result.receipt_json) == result


@pytest.mark.asyncio
async def test_changed_journal_cannot_repair_or_pass_event(monkeypatch):
    controlled = session()
    state = _setup(monkeypatch, controlled, keys=(), second_keys=(EVENT,))
    result = await _observe(controlled, state["factory"])
    raw = decode(result.receipt_json)
    assert raw["code"] == "ledger_changed"
    assert raw["ledger_first_sha256"] and raw["ledger_second_sha256"]
    assert raw["ledger_revision"] is None
    assert raw["event_seen_in_controlled_journal"] is None
    assert raw["g6_evaluated"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "alter",
    [
        {"event_keys_sha256": "0" * 64},
        {"scope_sha256": "0" * 64},
        {"db_journal_complete": True},
        {"ledger_revision": 0},
    ],
)
async def test_tampered_or_unverified_journal_denies_without_pin(monkeypatch, alter):
    controlled = session()
    state = _setup(monkeypatch, controlled, alter=alter)
    result = await _observe(controlled, state["factory"])
    raw = decode(result.receipt_json)
    assert raw["code"] == "ledger_unavailable"
    assert raw["ledger_first_sha256"] is None
    assert raw["event_seen_in_controlled_journal"] is None
    assert raw["execution_authority"] is False


@pytest.mark.asyncio
async def test_missing_g5_does_not_read_journal(monkeypatch):
    controlled = session()
    state = _setup(monkeypatch, controlled, event=False)
    result = await _observe(controlled, state["factory"])
    raw = decode(result.receipt_json)
    assert state["calls"] == 0
    assert raw["code"] == "g5_unavailable"
    assert raw["event_key_sha256"] is None
    assert raw["g1_g5_replayed"] is False


@pytest.mark.asyncio
async def test_missing_g5_after_account_use_still_does_not_read_journal(monkeypatch):
    controlled = session()
    state = _setup(monkeypatch, controlled, event=False)

    async def stopped_after_account(*_args, **_kwargs):
        controlled._used = True
        return _g5_receipt(event=False)

    monkeypatch.setattr(g5, "preflight_owned_base_event_v7", stopped_after_account)
    result = await _observe(controlled, state["factory"])
    assert decode(result.receipt_json)["code"] == "g5_unavailable"
    assert state["calls"] == 0


@pytest.mark.asyncio
async def test_cancelled_journal_read_propagates(monkeypatch):
    controlled = session()
    state = _setup(monkeypatch, controlled)

    async def cancelled(_self, _scope):
        raise asyncio.CancelledError

    monkeypatch.setattr(
        ledger_repo.QualificationLedgerRepository,
        "inspect_consumed_event_journal",
        cancelled,
    )
    with pytest.raises(asyncio.CancelledError):
        await _observe(controlled, state["factory"])


@pytest.mark.asyncio
async def test_pending_cancel_after_second_read_cannot_publish_result(monkeypatch):
    controlled = session()
    state = _setup(monkeypatch, controlled)

    async def cancel_after_read(self, scope):
        state["calls"] += 1
        value = _journal(scope, (), self.clock)
        if state["calls"] == 2:
            asyncio.current_task().cancel()
        return value

    monkeypatch.setattr(
        ledger_repo.QualificationLedgerRepository,
        "inspect_consumed_event_journal",
        cancel_after_read,
    )
    child = asyncio.create_task(_observe(controlled, state["factory"]))
    state["same_task"] = child
    with pytest.raises(asyncio.CancelledError):
        await child
    assert state["calls"] == 2


@pytest.mark.asyncio
async def test_session_pin_change_after_g5_denies_before_journal(monkeypatch):
    controlled = session()
    state = _setup(monkeypatch, controlled)

    async def changed(*_args, **_kwargs):
        controlled._used = True
        controlled._pin = "0" * 64
        return _g5_receipt()

    monkeypatch.setattr(g5, "preflight_owned_base_event_v7", changed)
    with pytest.raises(boundary.OwnedOriginalEventLedgerError):
        await _observe(controlled, state["factory"])
    assert state["calls"] == 0


def test_no_caller_pass_event_or_receipt_and_no_rehashed_authority():
    names = set(inspect.signature(boundary.observe_owned_g5_event_ledger_v8).parameters)
    assert not names.intersection(
        {"passed", "event", "consumed_event_keys", "g6", "receipt", "permit", "clock"}
    )
    raw = {
        "schema_version": boundary._SCHEMA,
        "code": "g5_unavailable",
        "v7_receipt_sha256": "1" * 64,
        "account_plan_sha256": "2" * 64,
        "scope_sha256": "3" * 64,
        "session_binding_sha256": "4" * 64,
        "event_key_sha256": None,
        "ledger_first_sha256": None,
        "ledger_second_sha256": None,
        "ledger_revision": None,
        "ledger_observed_at": None,
        "event_seen_in_controlled_journal": None,
        "g1_g5_replayed": False,
        "admission": "DENY",
        **{name: False for name in boundary._FALSE_FIELDS},
    }
    boundary.OwnedOriginalEventLedgerDiagnosticV8(canonical(raw))
    raw["execution_authority"] = True
    with pytest.raises(boundary.OwnedOriginalEventLedgerError):
        boundary.OwnedOriginalEventLedgerDiagnosticV8(canonical(raw))
