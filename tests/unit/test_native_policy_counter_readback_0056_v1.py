"""Test the DB0056 counter comparison with synthetic operands and real codecs."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.core import native_policy_counter_readback_0056_v1 as subject
from app.domain.source_primitives import sha
from app.trade_qualification import demo_policy_counter_initialization_v1 as counter
from app.trade_qualification.demo_control import (
    ControlScope,
    canonical,
    stopped_genesis,
)
from app.trade_qualification.reservations import LedgerScope
from tests.unit.test_demo_policy_counter_initialization_v1 import _declared_ack

_NOW = datetime(2026, 11, 14, tzinfo=UTC)
_SCOPE = LedgerScope(account_id="1234", settlement_currency="USDT")
_CONTROL = stopped_genesis(ControlScope(_SCOPE.environment, _SCOPE.account_id), _NOW)
_FIRST = subject._Read(_CONTROL, "b" * 64, None, "12", _NOW)


@pytest.mark.parametrize("row", [None, b"retained counter data"])
async def test_comparison_never_treats_a_counter_as_authoritative(monkeypatch, row):
    first = replace(_FIRST, row=row)
    second = replace(
        first, transaction_id="13", observed_at=_NOW + timedelta(seconds=1)
    )
    monkeypatch.setattr(subject, "_read_once", AsyncMock(side_effect=(first, second)))
    result = await subject.observe_native_policy_counter_0056_v1(None, None, _SCOPE)
    assert result.status == ("ABSENT" if row is None else "OBSERVED_UNVERIFIED")
    assert result.counter_row_sha256 == (None if row is None else sha(row))
    assert result.control_revision == 1 and result.control_event_sha256 == "b" * 64
    assert result.admission == "DENY"
    assert result.source_authenticity_verified is result.account_complete is False
    assert result.execution_authority is False


@pytest.mark.parametrize(
    "changes",
    [
        {"transaction_id": "12"},
        {"event_sha256": "a" * 64},
        {"row": b"late counter"},
        {"observed_at": _NOW - timedelta(seconds=1)},
    ],
)
async def test_changed_or_reused_snapshot_refuses(monkeypatch, changes):
    second = replace(_FIRST, **{"transaction_id": "13", **changes})
    monkeypatch.setattr(subject, "_read_once", AsyncMock(side_effect=(_FIRST, second)))
    with pytest.raises(subject.NativePolicyCounterReadback0056Error):
        await subject.observe_native_policy_counter_0056_v1(None, None, _SCOPE)


async def test_currency_substitution_refuses_before_opening_sql(monkeypatch):
    read = AsyncMock()
    monkeypatch.setattr(subject, "_read_once", read)
    with pytest.raises(subject.NativePolicyCounterReadback0056Error):
        await subject.observe_native_policy_counter_0056_v1(
            None, None, _SCOPE.model_copy(update={"settlement_currency": "BTC"})
        )
    read.assert_not_awaited()


async def test_original_codec_and_journal_link_are_checked_on_retained_row(monkeypatch):
    ack = await _declared_ack(monkeypatch)
    initialized = ack.owner_sampled_at + timedelta(milliseconds=1)
    data = {
        name: getattr(ack, name)
        for name in (
            "initialization_sha256",
            "settlement_currency",
            "command_id",
            "control_revision",
            "control_event_sha256",
            "control_state_sha256",
            "source_lineage_sha256",
            "account_revision",
            "ledger_revision",
            "claims_sha256",
            "configured_policy_json",
            "measured_genesis_json",
            "owner_sampled_at",
            "original_expires_at",
        )
    }
    row = subject.CounterRow(
        **data,
        environment=ack.scope.environment,
        account_id=ack.scope.account_id,
        configured_policy_sha256=sha(ack.configured_policy_json.encode("ascii")),
        measured_genesis_sha256=sha(ack.measured_genesis_json.encode("ascii")),
        ack_json=canonical(counter.policy_counter_ack_document_v1(ack)),
        initialized_at=initialized,
        db_recorded_at=initialized,
    )
    event = subject.DemoControlJournal(
        event_sha256=ack.control_event_sha256, state_sha256=ack.control_state_sha256
    )
    scope = LedgerScope(account_id=ack.scope.account_id, settlement_currency="USDT")
    session = SimpleNamespace(
        get=AsyncMock(return_value=event),
        scalars=AsyncMock(return_value=SimpleNamespace(all=lambda: [row])),
    )
    held = Mock()
    state = replace(stopped_genesis(ack.scope, _NOW), revision=2)
    assert await subject._read_counter_row(
        session, scope, state, held
    ) == subject.original._row_raw(row, subject.original._COUNTER_COLUMNS)
    session.get.assert_awaited_once_with(
        subject.DemoControlJournal,
        (scope.environment, scope.account_id, ack.control_revision),
    )
    assert held.call_count == 2
    event.event_sha256 = "6" * 64
    with pytest.raises(subject.NativePolicyCounterReadback0056Error):
        await subject._read_counter_row(session, scope, state, held)
