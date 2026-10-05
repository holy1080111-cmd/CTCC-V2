"""Synthetic control/intent replay contracts; no runtime/source authority."""

import json
import pickle
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace

import pytest

from app.database.repositories.demo_control import DemoControlRepository
from app.trade_qualification import control_bound_ledger as bound
from app.trade_qualification import demo_control as control
from app.trade_qualification.reservations import QualificationLedgerError
from app.trade_qualification.submission_intent import build_submission_intent
from tests.unit.qualification_execution_binding_fixtures import (
    consumed_receipt,
    execution_binding,
)
from tests.unit.qualification_ledger_fixtures import ledger_fixture

TOKEN = b"T" * 32  # Synthetic only; no credential/session secret.
EVENT = "c" * 64


@pytest.fixture(scope="module")
def records():
    fixture = ledger_fixture()
    request = fixture.request
    binding = execution_binding(fixture)
    session_id = json.loads(binding.account_packet_json)["plan"]["session_binding_id"]
    pins = control.ControlPins(
        "a" * 64, request.origin.original_policy_sha256, control.digest(session_id)
    )
    scope = control.ControlScope(request.scope.environment, request.scope.account_id)
    state = control.acquire(
        None,
        scope=scope,
        owner_sha256=control.owner_binding(TOKEN),
        pins=pins,
        now=fixture.now,
    )
    state = control.advance(
        state,
        owner_sha256=control.owner_binding(TOKEN),
        owner_epoch=1,
        expected_revision=1,
        action="arm_requested",
        command_id="d" * 64,
        now=fixture.now,
        arm_expires_at=fixture.now + timedelta(seconds=20),
    )
    expectation = bound.ControlExpectation(scope, TOKEN, 1, 2, EVENT, pins)
    consumed = consumed_receipt(fixture)
    reserved = consumed.model_copy(
        update={"state": "reserved", "state_revision": 1, "ledger_revision": 2}
    )
    raw = bound.build_reserved_evidence(
        request, reserved, state, EVENT, checked_at=fixture.now
    )
    inner = build_submission_intent(request, consumed, execution_binding=binding)
    envelope = bound.build_control_bound_intent(request, inner, raw, state, EVENT)
    return fixture, state, expectation, reserved, raw, inner, envelope


def test_exact_v2_bytes_enveloped_and_every_result_stays_denied(records):
    fixture, state, expectation, reserved, raw, inner, envelope = records
    assert (
        bound.guard_current_control(
            state,
            EVENT,
            expectation,
            fixture.request.scope,
            now=fixture.now,
            request=fixture.request,
        )
        == state
    )
    assert bound.replay_reserved_evidence(raw, fixture.request) == (
        reserved,
        state,
        EVENT,
    )
    body = json.loads(envelope.canonical_json)
    assert body["intent_json"].encode() == inner.canonical_json.encode()
    assert body["intent_sha256"] == inner.sha256
    result, _ = bound.replay_control_bound_intent(
        envelope.canonical_json, fixture.request, expected_sha256=envelope.sha256
    )
    assert result == envelope and result.admission == "DENY"
    assert not result.execution_authority and not result.order_retry_authority
    assert body["owned_policy_verified"] is False and body["account_complete"] is False
    assert TOKEN.decode() not in raw + envelope.canonical_json
    assert TOKEN.hex() not in raw + envelope.canonical_json


@pytest.mark.parametrize(
    "field",
    ["token", "epoch", "revision", "scope", "event", "config", "session", "policy"],
)
def test_each_expected_control_coordinate_is_required(records, field):
    fixture, state, expected, *_ = records
    if field in {"config", "session", "policy"}:
        key = {
            "config": "config_sha256",
            "session": "credential_session_sha256",
            "policy": "policy_sha256",
        }[field]
        changed = replace(expected, pins=replace(expected.pins, **{key: "e" * 64}))
    else:
        values = {
            "token": {"owner_token": b"x" * 32},
            "epoch": {"owner_epoch": 2},
            "revision": {"control_revision": 3},
            "scope": {"scope": control.ControlScope("demo", "999")},
            "event": {"event_sha256": "e" * 64},
        }
        changed = replace(expected, **values[field])
    with pytest.raises(QualificationLedgerError, match="identity_or_revision"):
        bound.guard_current_control(
            state,
            EVENT,
            changed,
            fixture.request.scope,
            now=fixture.now,
            request=fixture.request,
        )


@pytest.mark.parametrize("change", ["stop", "disarm", "arm_expiry", "lease_expiry"])
def test_same_pins_do_not_override_current_stop_or_expiry(records, change):
    fixture, state, expected, *_ = records
    now = fixture.now
    if change in {"stop", "disarm"}:
        state = replace(
            state,
            arm_request_id=None,
            arm_expires_at=None,
            emergency_stop=change == "stop",
        )
    else:
        now = state.arm_expires_at if change == "arm_expiry" else state.lease_until
    with pytest.raises(QualificationLedgerError, match="stopped_disarmed_or_expired"):
        bound.guard_current_control(
            state,
            EVENT,
            expected,
            fixture.request.scope,
            now=now,
            request=fixture.request,
        )


@pytest.mark.parametrize(
    "mutation",
    [
        "authority",
        "policy_verified",
        "state_revision_bool",
        "event",
        "checked_at",
        "extra",
    ],
)
def test_rehashed_reserved_binding_cannot_change_meaning(records, mutation):
    fixture, *_ = records
    raw = records[4]
    value = json.loads(raw)
    if mutation == "authority":
        value["execution_authority"] = True
    elif mutation == "policy_verified":
        value["owned_policy_verified"] = 0
    elif mutation == "state_revision_bool":
        value["reserved_receipt"]["state_revision"] = True
    elif mutation == "event":
        value["control_event_sha256"] = True
    elif mutation == "checked_at":
        value["checked_at"] = fixture.request.origin.deadline.isoformat()
    else:
        value["passed"] = True
    with pytest.raises(QualificationLedgerError, match="reserved_evidence_invalid"):
        bound.replay_reserved_evidence(control.canonical(value), fixture.request)


@pytest.mark.parametrize(
    "mutation", ["inner", "binding_hash", "event", "checked_at", "authority", "extra"]
)
def test_rehashed_outer_envelope_does_not_rewrite_v2_or_control(records, mutation):
    fixture, *_ = records
    body = json.loads(records[-1].canonical_json)
    if mutation == "inner":
        inner = json.loads(body["intent_json"])
        inner["exchange_request"]["body"]["px"] = "1"
        body["intent_json"] = control.canonical(inner)
        body["intent_sha256"] = control.digest(body["intent_json"])
    elif mutation == "binding_hash":
        body["reservation_binding_sha256"] = "f" * 64
    elif mutation == "event":
        body["consume_control_event_sha256"] = "f" * 64
    elif mutation == "checked_at":
        body["consume_checked_at"] = fixture.request.origin.deadline.isoformat()
    elif mutation == "authority":
        body["execution_authority"] = 0
    else:
        body["passed"] = True
    raw = control.canonical(body)
    with pytest.raises(QualificationLedgerError, match="bound_control_intent_invalid"):
        bound.replay_control_bound_intent(
            raw, fixture.request, expected_sha256=control.digest(raw)
        )


def test_legacy_v1_intent_cannot_be_promoted(records):
    fixture, state, _, _, raw, *_ = records
    old = build_submission_intent(fixture.request, consumed_receipt(fixture))
    with pytest.raises(QualificationLedgerError, match="v2_intent_required"):
        bound.build_control_bound_intent(fixture.request, old, raw, state, EVENT)


@pytest.mark.parametrize("session_sha256", ["b" * 64, "e" * 64])
def test_control_and_verified_v2_account_packet_sessions_must_match(
    records, session_sha256
):
    fixture, state, _, reserved, _, inner, _ = records
    state = replace(
        state, pins=replace(state.pins, credential_session_sha256=session_sha256)
    )
    # Legacy reservation request has no account packet; it cannot assert that
    # account-session equality has already been proved before v2 consumption.
    raw = bound.build_reserved_evidence(
        fixture.request, reserved, state, EVENT, checked_at=fixture.now
    )
    with pytest.raises(QualificationLedgerError, match="account_session_conflict"):
        bound.build_control_bound_intent(fixture.request, inner, raw, state, EVENT)


def test_expectation_is_detached_and_token_not_printed_or_pickled(records):
    expected = records[2]
    frozen = bound.freeze_expectation(expected)
    assert (
        frozen == expected
        and frozen is not expected
        and frozen.pins is not expected.pins
    )
    assert TOKEN.decode() not in repr(frozen)
    with pytest.raises(QualificationLedgerError, match="not_serializable"):
        pickle.dumps(frozen)
    with pytest.raises(QualificationLedgerError, match="exact_expectation"):
        bound.freeze_expectation({"passed": True})


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", [None, "epoch_bool", "stop_int"])
async def test_historical_control_binding_compares_nested_types_exactly(
    records, mutation
):
    state = records[1]
    value = {
        "version": "ctcc.demo_control_event.v1",
        "command_id": "d" * 64,
        "action": "arm_requested",
        "previous_sha256": "f" * 64,
        "state": control.document(state),
    }
    if mutation == "epoch_bool":
        value["state"]["owner_epoch"] = True
    elif mutation == "stop_int":
        value["state"]["emergency_stop"] = 0
    raw = control.canonical(value)
    event = SimpleNamespace(
        event_json=raw,
        event_sha256=control.digest(raw),
        state_sha256=control.digest(control.canonical(control.document(state))),
        command_id=value["command_id"],
        action=value["action"],
        previous_sha256=value["previous_sha256"],
        occurred_at=state.updated_at,
    )

    class Session:
        async def get(self, _model, key):
            return (
                event
                if key[-1] == state.revision
                else SimpleNamespace(event_sha256="f" * 64)
            )

    if mutation is None:
        await DemoControlRepository.verify_historical_binding(
            Session(), state, event.event_sha256
        )
    else:
        with pytest.raises(
            control.DemoControlError, match="historical_binding_invalid"
        ):
            await DemoControlRepository.verify_historical_binding(
                Session(), state, event.event_sha256
            )
