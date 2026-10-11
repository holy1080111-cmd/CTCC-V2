"""Control-bound intent ownership remains diagnostic and cannot dispatch.

These records and the repository reader are synthetic. No credential, network
or exchange transport is used.
"""

import asyncio
import json
from datetime import timedelta

import pytest

from app.database.repositories.qualification_ledger import (
    QualificationLedgerRepository,
)
from app.trade_qualification import control_bound_ledger as bound
from app.trade_qualification import demo_control as control
from app.trade_qualification.dispatch_ownership import (
    DispatchOwnership,
    DispatchOwnershipError,
)
from app.trade_qualification.submission_intent import build_submission_intent
from tests.unit.qualification_execution_binding_fixtures import (
    consumed_receipt,
    execution_binding,
)
from tests.unit.qualification_ledger_fixtures import ledger_fixture

_EVENT = "c" * 64
_TOKEN = b"T" * 32


@pytest.fixture(scope="module")
def controlled():
    # ledger_fixture uses asyncio.run and must be created outside an async test.
    fixture = ledger_fixture()
    request = fixture.request
    binding = execution_binding(fixture)
    session_id = json.loads(binding.account_packet_json)["plan"]["session_binding_id"]
    pins = control.ControlPins(
        "a" * 64,
        request.origin.original_policy_sha256,
        control.digest(session_id),
    )
    scope = control.ControlScope(request.scope.environment, request.scope.account_id)
    state = control.acquire(
        None,
        scope=scope,
        owner_sha256=control.owner_binding(_TOKEN),
        pins=pins,
        now=fixture.now,
    )
    state = control.advance(
        state,
        owner_sha256=control.owner_binding(_TOKEN),
        owner_epoch=1,
        expected_revision=1,
        action="arm_requested",
        command_id="d" * 64,
        now=fixture.now,
        arm_expires_at=fixture.now + timedelta(seconds=20),
    )
    consumed = consumed_receipt(fixture)
    reserved = consumed.model_copy(
        update={"state": "reserved", "state_revision": 1, "ledger_revision": 2}
    )
    reserved_raw = bound.build_reserved_evidence(
        request, reserved, state, _EVENT, checked_at=fixture.now
    )
    inner = build_submission_intent(request, consumed, execution_binding=binding)
    outer = bound.build_control_bound_intent(
        request, inner, reserved_raw, state, _EVENT
    )
    return fixture, inner, outer, state, reserved


class _Reader:
    def __init__(self, record):
        self.record = record
        self.calls = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.release.set()

    async def read(self, repository, scope, event_key, *, expected_sha256):
        self.calls.append((scope, event_key, expected_sha256))
        self.started.set()
        await self.release.wait()
        return self.record


class _Clock:
    def __init__(self, fixture):
        self.now = fixture.now - timedelta(milliseconds=1)
        self.monotonic = 100.0

    def after_commit(self, fixture):
        self.now = fixture.now + timedelta(milliseconds=1)
        self.monotonic += 0.002


def _owner(monkeypatch, controlled, *, observed=None):
    fixture, _, outer, _, _ = controlled
    clock = _Clock(fixture)
    reader = _Reader(outer if observed is None else observed)
    repository = QualificationLedgerRepository(None, clock=lambda: clock.now)
    monkeypatch.setattr(
        QualificationLedgerRepository,
        "read_control_bound_submission_intent",
        reader.read,
    )

    async def wrong_reader(*args, **kwargs):
        pytest.fail("controlled binding used legacy intent readback")

    monkeypatch.setattr(
        QualificationLedgerRepository, "read_submission_intent", wrong_reader
    )
    owner = DispatchOwnership(
        repository,
        fixture.request.scope,
        clock=lambda: clock.now,
        monotonic=lambda: clock.monotonic,
    )
    return owner, reader, clock


@pytest.mark.asyncio
async def test_exact_controlled_envelope_binds_only_as_denied_diagnostic(
    monkeypatch, controlled
):
    fixture, _, outer, _, _ = controlled
    owner, reader, clock = _owner(monkeypatch, controlled)
    slot = owner.begin(fixture.request)
    clock.after_commit(fixture)
    result = await owner.bind_control_bound_intent(slot, outer)

    assert result.state == "bound_denied"
    assert result.intent_sha256 == outer.sha256
    assert not result.execution_authority and not result.order_retry_authority
    assert reader.calls == [
        (
            fixture.request.scope,
            fixture.request.origin.original_event_key,
            outer.sha256,
        )
    ]
    with pytest.raises(
        DispatchOwnershipError, match="^dispatch_trusted_producer_unavailable$"
    ):
        owner.require_ready(slot)
    assert owner.inspect(slot).state == "denied"


@pytest.mark.asyncio
@pytest.mark.parametrize("wrong_kind", ("inner_as_controlled", "outer_as_legacy"))
async def test_inner_and_outer_types_cannot_cross_readback_modes(
    monkeypatch, controlled, wrong_kind
):
    fixture, inner, outer, _, _ = controlled
    owner, reader, clock = _owner(monkeypatch, controlled)
    slot = owner.begin(fixture.request)
    clock.after_commit(fixture)
    with pytest.raises(DispatchOwnershipError, match="^dispatch_intent_invalid$"):
        if wrong_kind == "inner_as_controlled":
            owner.bind_control_bound_intent(slot, inner)
        else:
            owner.bind_intent(slot, outer)
    assert reader.calls == []
    assert owner.inspect(slot).state == "denied"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation",
    (
        "inner_body",
        "inner_sha256",
        "control_event",
        "consume_time",
        "authority",
        "extra",
    ),
)
async def test_rehashed_forged_controlled_envelope_cannot_bind(
    monkeypatch, controlled, mutation
):
    fixture, _, outer, _, _ = controlled
    body = json.loads(outer.canonical_json)
    if mutation == "inner_body":
        inner = json.loads(body["intent_json"])
        inner["exchange_request"]["body"]["px"] = "1"
        body["intent_json"] = control.canonical(inner)
        body["intent_sha256"] = control.digest(body["intent_json"])
    elif mutation == "inner_sha256":
        body["intent_sha256"] = "f" * 64
    elif mutation == "control_event":
        body["consume_control_event_sha256"] = "f" * 64
    elif mutation == "consume_time":
        body["consume_checked_at"] = fixture.request.origin.deadline.isoformat()
    elif mutation == "authority":
        body["execution_authority"] = True
    else:
        body["passed"] = True
    raw = control.canonical(body)
    changed = bound.ControlBoundSubmissionIntentRecord(raw, control.digest(raw))
    owner, reader, clock = _owner(monkeypatch, controlled)
    slot = owner.begin(fixture.request)
    clock.after_commit(fixture)
    with pytest.raises(DispatchOwnershipError, match="^dispatch_intent_invalid$"):
        owner.bind_control_bound_intent(slot, changed)
    assert reader.calls == []
    assert owner.inspect(slot).state == "denied"


@pytest.mark.asyncio
async def test_controlled_intent_from_before_invocation_is_rejected(
    monkeypatch, controlled
):
    fixture, _, outer, _, _ = controlled
    owner, reader, clock = _owner(monkeypatch, controlled)
    clock.after_commit(fixture)
    slot = owner.begin(fixture.request)
    with pytest.raises(
        DispatchOwnershipError, match="^dispatch_intent_not_same_invocation$"
    ):
        owner.bind_control_bound_intent(slot, outer)
    assert reader.calls == []
    assert owner.inspect(slot).state == "denied"


@pytest.mark.asyncio
async def test_independent_controlled_readback_mismatch_revokes_owner(
    monkeypatch, controlled
):
    fixture, inner, outer, state, reserved = controlled
    other_event = "e" * 64
    other_reserved = bound.build_reserved_evidence(
        fixture.request, reserved, state, other_event, checked_at=fixture.now
    )
    other = bound.build_control_bound_intent(
        fixture.request, inner, other_reserved, state, other_event
    )
    assert other != outer
    owner, reader, clock = _owner(monkeypatch, controlled, observed=other)
    slot = owner.begin(fixture.request)
    clock.after_commit(fixture)
    with pytest.raises(DispatchOwnershipError, match="^dispatch_readback_mismatch$"):
        await owner.bind_control_bound_intent(slot, outer)
    assert len(reader.calls) == 1
    with pytest.raises(DispatchOwnershipError, match="^dispatch_owner_revoked$"):
        owner.require_ready(slot)


@pytest.mark.asyncio
async def test_controlled_task_cancelled_before_first_step_keeps_tombstone(
    monkeypatch, controlled
):
    fixture, _, outer, _, _ = controlled
    owner, reader, clock = _owner(monkeypatch, controlled)
    slot = owner.begin(fixture.request)
    clock.after_commit(fixture)
    task = owner.bind_control_bound_intent(slot, outer)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0)  # Run the owner-held task's terminal callback.
    assert reader.calls == []
    assert owner.inspect(slot).state == "cancelled"
    with pytest.raises(DispatchOwnershipError):
        owner.require_ready(slot)


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_kind", ("task", "owner"))
async def test_controlled_cancellation_during_readback_cannot_bind_late_success(
    monkeypatch, controlled, cancel_kind
):
    fixture, _, outer, _, _ = controlled
    owner, reader, clock = _owner(monkeypatch, controlled)
    slot = owner.begin(fixture.request)
    clock.after_commit(fixture)
    reader.release.clear()
    task = owner.bind_control_bound_intent(slot, outer)
    await reader.started.wait()
    if cancel_kind == "task":
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        owner.cancel(slot)
        reader.release.set()
        with pytest.raises(DispatchOwnershipError):
            await task
    assert len(reader.calls) == 1
    assert owner.inspect(slot).state == "cancelled"
    with pytest.raises(DispatchOwnershipError):
        owner.require_ready(slot)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ("estop", "release"))
async def test_historical_control_envelope_cannot_assert_current_arm_after_change(
    monkeypatch, controlled, change
):
    fixture, _, outer, state, _ = controlled
    owner, reader, clock = _owner(monkeypatch, controlled)
    slot = owner.begin(fixture.request)
    changed_at = fixture.now + timedelta(microseconds=500)
    changed = control.advance(
        state,
        owner_sha256=control.owner_binding(_TOKEN),
        owner_epoch=state.owner_epoch,
        expected_revision=state.revision,
        action=change,
        command_id="e" * 64,
        now=changed_at,
    )
    assert not changed.arm_intent_current(changed_at)
    clock.after_commit(fixture)
    result = await owner.bind_control_bound_intent(slot, outer)
    assert len(reader.calls) == 1
    assert result.state == "bound_denied"
    assert not result.execution_authority
    with pytest.raises(
        DispatchOwnershipError, match="^dispatch_trusted_producer_unavailable$"
    ):
        owner.require_ready(slot)
