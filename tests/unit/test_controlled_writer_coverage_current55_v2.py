"""Inert comparison/cancellation mechanisms, never native coverage acceptance."""

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from app.database.repositories import qualification_ledger as ledger
from app.trade_qualification import controlled_writer_coverage_current55_v2 as subject
from app.trade_qualification import demo_control as control
from app.trade_qualification.reservations import LedgerScope, LedgerScopeState

AT = datetime(2026, 1, 1, tzinfo=UTC)
SCOPE = LedgerScope(environment="demo", account_id="123456", settlement_currency="USDT")


def _control():
    return control.ControlState(
        control.ControlScope("demo", "123456"),
        3,
        "a" * 64,
        1,
        AT + timedelta(minutes=1),
        control.ControlPins("b" * 64, "c" * 64, "d" * 64),
        False,
        "e" * 64,
        AT + timedelta(seconds=30),
        AT,
        AT,
    )


def _snapshot(*, session=None, xid="10", at=AT, blocking="synthetic_unknown"):
    # These are declared SYNTHETIC DATA objects. No native replay, factory,
    # account collector, database, credential or SourceKernel is simulated.
    state = LedgerScopeState(
        scope=SCOPE,
        account_revision=0,
        ledger_revision=0,
        claims_sha256=None,
        active=(),
    )
    local = ledger.PortfolioLocalCheckpoint(
        json.dumps(
            {
                "schema_version": "synthetic.local.comparison.only",
                "blocking_reasons": [blocking],
            },
            sort_keys=True,
        ).encode(),
        at,
        at,
    )
    metadata = (
        1,
        1,
        2001,
        xid,
        at,
        "none",
        "off",
        "read committed",
        "pg_catalog, public, pg_temp",
        "UTC",
        "on",
    )
    observed = subject.CoverageDatabaseObservationV2(metadata, metadata, at, at)
    return subject._LockedSnapshotV2(
        object() if session is None else session,
        _control(),
        "f" * 64,
        state,
        ledger.canonical(state),
        local,
        observed,
    )


def test_equal_full_unknown_data_is_retained_without_flat_or_authority_upgrade():
    first, second = (
        _snapshot(xid="10"),
        _snapshot(xid="11", at=AT + timedelta(seconds=1)),
    )
    subject._require_same_snapshots(first, second)
    assert first.local_checkpoint.document_json == second.local_checkpoint.document_json
    assert second.local_checkpoint.known_flat is False
    assert second.ledger_state.claims_sha256 is None
    result = subject.ControlledSourceCoverageObservationV2(
        SCOPE,
        second.control_state,
        second.control_event_sha256,
        second.ledger_state,
        second.local_checkpoint,
        object(),
        b"synthetic",
        first.database.observed_at,
        second.database.received_at,
        (first.database, second.database),
    )
    assert not result.account_complete
    assert not result.account_revision_issued
    assert not result.event_eligibility
    assert not result.execution_authority


@pytest.mark.parametrize(
    "mutation",
    (
        "same_session",
        "same_xid",
        "clock_reversal",
        "control",
        "event",
        "account_revision",
        "ledger_revision",
        "claims_sha256",
        "local_unknown_removed",
    ),
)
def test_observed_local_control_revision_or_independence_change_rejects(mutation):
    first, second = _snapshot(), _snapshot(xid="11", at=AT + timedelta(seconds=1))
    if mutation == "same_session":
        second = replace(second, session=first.session)
    elif mutation == "same_xid":
        second = replace(
            second,
            database=replace(
                second.database,
                before=(*second.database.before[:3], "10", *second.database.before[4:]),
            ),
        )
    elif mutation == "clock_reversal":
        second = replace(
            second,
            database=replace(second.database, observed_at=AT - timedelta(seconds=1)),
        )
    elif mutation == "control":
        second = replace(
            second, control_state=replace(second.control_state, revision=4)
        )
    elif mutation == "event":
        second = replace(second, control_event_sha256="1" * 64)
    elif mutation == "local_unknown_removed":
        changed = replace(
            second.local_checkpoint, document_json=b'{"blocking_reasons":[]}'
        )
        second = replace(second, local_checkpoint=changed)
    else:
        changed = second.ledger_state.model_copy(
            update={mutation: "2" * 64 if mutation == "claims_sha256" else 1}
        )
        # Changing the exact original wire bytes is the relevant comparison;
        # this synthetic mutant does not become a valid source/model claim.
        second = replace(second, ledger_json=ledger.canonical(changed))
    with pytest.raises(
        subject.ControlledCoverageSourceErrorV2,
        match="controlled55_coverage_original_local_or_control_changed",
    ):
        subject._require_same_snapshots(first, second)


@pytest.mark.asyncio
async def test_failed_source_check_closes_unstarted_operation_without_callback(
    monkeypatch,
):
    called = []

    async def operation():
        called.append(True)

    def deny(_state):
        raise subject.ControlledCoverageSourceErrorV2("synthetic_source_rejected")

    monkeypatch.setattr(subject, "_check", deny)
    pending = operation()
    with pytest.raises(subject.ControlledCoverageSourceErrorV2):
        await subject._checked_await({}, pending)
    assert pending.cr_frame is None
    assert called == []


@pytest.mark.asyncio
async def test_cancellation_yields_no_partial_observation_or_retry(monkeypatch):
    calls = []

    def check(_state):
        calls.append("checked")

    async def cancelled():
        raise asyncio.CancelledError()

    monkeypatch.setattr(subject, "_check", check)
    with pytest.raises(asyncio.CancelledError):
        await subject._checked_await({}, cancelled())
    assert calls == ["checked"]


def test_database_metadata_wire_preserves_original_values_and_time():
    original = _snapshot().database.before
    value = subject._wire_metadata(original)
    assert value[:4] == list(original[:4])
    assert value[4] == AT.isoformat()
    assert value[5:] == list(original[5:])
    assert original[4] is AT


def test_constructor_data_with_missing_current_source_is_not_a_valid_context():
    value = subject.ControlledCoverageSourceV6(
        None,
        SCOPE,
        _control(),
        "f" * 64,
        None,
        b"synthetic",
        None,
        "a" * 32,
        "b" * 64,
        object(),
        AT + timedelta(seconds=30),
        1,
        "c" * 64,
        None,
    )
    with pytest.raises(subject.ControlledCoverageSourceErrorV2):
        subject._context_signature(value)


def test_caller_boolean_or_dictionary_never_substitutes_for_source_context():
    for candidate in (True, {"passed": True}, {"account_complete": True}, None):
        with pytest.raises(subject.ControlledCoverageSourceErrorV2):
            subject._context_signature(candidate)


def test_fixed_dispatch_closure_values_never_invoke_foreign_equality():
    called = []

    class ForeignValue:
        def __eq__(self, _other):
            called.append(True)
            raise AssertionError("foreign equality must never execute")

    original = ForeignValue()
    assert subject._same_closed_value(original, original) is True
    assert subject._same_closed_value(ForeignValue(), original) is False
    assert (
        subject._same_closed_value("fixed_dispatch", "".join(("fixed_", "dispatch")))  # noqa: FLY002 -- distinct equal string objects exercise identity-safe comparison.
        is True
    )
    assert subject._same_closed_value("different_dispatch", "fixed_dispatch") is False
    assert called == []


def test_changed_captured_dispatch_name_rejects_before_any_source_callback(monkeypatch):
    # Negative internal mechanism only: this fixture is never admitted through
    # the public observer and never returns a source context or owner.
    called = []
    purpose = "original_fixed_purpose"

    def inert_wrapper(_binding, *, key):
        called.append((purpose, key))
        raise AssertionError("changed source callback must not execute")

    closure = inert_wrapper.__closure__
    state = {
        "replay": inert_wrapper,
        "replay_code": inert_wrapper.__code__,
        "replay_globals": inert_wrapper.__globals__,
        "replay_builtins": inert_wrapper.__builtins__,
        "replay_defaults": inert_wrapper.__defaults__,
        "replay_kwdefaults": inert_wrapper.__kwdefaults__,
        "replay_kwitems": (),
        "replay_closure": closure,
        "replay_cells": tuple(cell.cell_contents for cell in closure),
        "replay_cell_count": len(closure),
    }
    monkeypatch.setattr(
        subject.native, subject._REPLAY_NAME, inert_wrapper, raising=False
    )
    name_cell = next(cell for cell in closure if type(cell.cell_contents) is str)
    original = name_cell.cell_contents
    name_cell.cell_contents = "different_purpose"
    try:
        with pytest.raises(
            subject.ControlledCoverageSourceErrorV2,
            match="controlled55_coverage_original_replay_changed",
        ):
            subject._check(state)
    finally:
        name_cell.cell_contents = original
    assert called == []
