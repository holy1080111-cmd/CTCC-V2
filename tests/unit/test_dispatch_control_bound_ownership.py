"""Control-bound intent ownership remains diagnostic and cannot dispatch.

These records and the repository reader are synthetic. No credential, network
or exchange transport is used.
"""

import asyncio
import hashlib
import json
from datetime import timedelta

import httpx
import pytest

from app.database.repositories.qualification_ledger import (
    QualificationLedgerRepository,
)
from app.exchange.okx.errors import OkxPrivateApiError
from app.exchange.okx.private_rest import OkxDemoPrivateRestClient
from app.trade_qualification import control_bound_ledger as bound
from app.trade_qualification import demo_control as control
from app.trade_qualification.dispatch_ownership import (
    DispatchOwnership,
    DispatchOwnershipError,
)
from app.trade_qualification.submission_intent import build_submission_intent
from tests.unit.exchange.test_private_rest import demo_settings
from tests.unit.qualification_execution_binding_fixtures import (
    consumed_receipt,
    execution_binding,
)
from tests.unit.qualification_ledger_fixtures import ledger_fixture

_EVENT = "c" * 64
_TOKEN = b"ctcc_unit_test_control_token_001"


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


async def _bound_transport_probe(monkeypatch, controlled):
    fixture, inner, outer, _, _ = controlled
    owner, _, clock = _owner(monkeypatch, controlled)
    slot = owner.begin(fixture.request)
    clock.after_commit(fixture)
    await owner.bind_control_bound_intent(slot, outer)
    document = json.loads(inner.canonical_json)
    logical = document["exchange_request"]
    sent = []

    def forbidden(request):
        sent.append(request)
        pytest.fail("qualified diagnostic must never dispatch an order")

    monkeypatch.setattr(
        OkxDemoPrivateRestClient, "_before_send", lambda self, **_: None
    )
    monkeypatch.setattr(
        OkxDemoPrivateRestClient, "_final_dispatch_check", lambda self, **_: None
    )
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(forbidden), base_url="https://www.okx.com"
    )
    client = OkxDemoPrivateRestClient(http, settings=demo_settings())
    client._qualification_dispatch_owner = owner
    client._qualification_dispatch_slot = slot
    client._request_extra_headers = lambda **_: {
        "expTime": logical["headers"]["expTime"]
    }
    return fixture, logical, owner, clock, http, client, sent


@pytest.mark.asyncio
async def test_httpx_exact_request_observation_is_readback_bound_but_denied(
    monkeypatch, controlled
):
    fixture, logical, owner, _, http, client, sent = await _bound_transport_probe(
        monkeypatch, controlled
    )
    try:
        with pytest.raises(OkxPrivateApiError) as denied:
            await client.place_order(dict(reversed(tuple(logical["body"].items()))))
        observed = client.last_prepared_dispatch_diagnostic
        assert denied.value.code == "private_write_authority_unavailable"
        assert observed.code == "transport_visible_match_unbound"
        assert observed.visible_fields_match is True
        assert observed.mismatches == ()
        assert observed.admission == "DENY"
        assert observed.execution_authority is False
        assert observed.order_retry_authority is False
        assert observed.order_submitted is False
        assert (
            observed.prepared_body_sha256
            == hashlib.sha256(
                json.dumps(
                    logical["body"], separators=(",", ":"), ensure_ascii=False
                ).encode("utf-8")
            ).hexdigest()
        )
        assert len(observed.prepared_request_sha256) == 64
        assert "write_side_account_uid_unverified" in observed.blockers
        assert "write_side_credential_session_unverified" in observed.blockers
        assert "current_account_revision_unverified" in observed.blockers
        assert "first_byte_guard_unavailable" in observed.blockers
        assert fixture.request.scope.account_id not in repr(observed)
        assert "demo-secret" not in repr(observed)
        assert sent == []
        assert owner.inspect(client._qualification_dispatch_slot).state == "denied"
        with pytest.raises(OkxPrivateApiError):
            await client.place_order(logical["body"])
        assert client.last_prepared_dispatch_diagnostic is None
        assert sent == []
    finally:
        await http.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changed",
    ("body", "equivalent_body_bytes", "expiry", "expiry_absent", "selected_origin"),
)
async def test_httpx_request_drift_tombstones_slot_before_any_send(
    monkeypatch, controlled, changed
):
    _, logical, owner, _, http, client, sent = await _bound_transport_probe(
        monkeypatch, controlled
    )
    try:
        body = dict(logical["body"])
        if changed == "body":
            body["px"] = "0.1"
        elif changed == "expiry":
            client._request_extra_headers = lambda **_: {"expTime": "1"}
        elif changed == "expiry_absent":
            client._request_extra_headers = lambda **_: {}
        elif changed == "equivalent_body_bytes":
            original = http.build_request

            def changed_encoding(method, url, **kwargs):
                kwargs["content"] = json.dumps(
                    json.loads(kwargs["content"]), indent=2
                ).encode("utf-8")
                return original(method, url, **kwargs)

            monkeypatch.setattr(http, "build_request", changed_encoding)
        else:
            original = http.build_request

            def changed_target(method, url, **kwargs):
                source = httpx.URL(url)
                return original(
                    method,
                    f"https://us.okx.com{source.raw_path.decode('ascii')}",
                    **kwargs,
                )

            monkeypatch.setattr(http, "build_request", changed_target)
        with pytest.raises(OkxPrivateApiError) as denied:
            await client.place_order(body)
        observed = client.last_prepared_dispatch_diagnostic
        assert denied.value.code == "private_write_authority_unavailable"
        assert observed.code == "prepared_mismatch"
        assert {
            "body": "body",
            "equivalent_body_bytes": "body_bytes",
            "expiry": "exptime",
            "expiry_absent": "exptime",
            "selected_origin": "selected_target",
        }[changed] in observed.mismatches
        assert owner.inspect(client._qualification_dispatch_slot).state == "denied"
        assert sent == []
    finally:
        await http.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ("revoked", "expired", "cancelled"))
async def test_transport_observation_rejects_inactive_owner_before_send(
    monkeypatch, controlled, invalid
):
    fixture, logical, owner, clock, http, client, sent = await _bound_transport_probe(
        monkeypatch, controlled
    )
    try:
        if invalid == "revoked":
            owner.revoke_now()
        elif invalid == "expired":
            clock.now = fixture.request.origin.deadline
            clock.monotonic += 1000
        else:
            owner.cancel(client._qualification_dispatch_slot)
        with pytest.raises(OkxPrivateApiError) as denied:
            await client.place_order(logical["body"])
        assert denied.value.code == "private_write_authority_unavailable"
        assert client.last_prepared_dispatch_diagnostic is None
        assert sent == []
    finally:
        await http.aclose()


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
