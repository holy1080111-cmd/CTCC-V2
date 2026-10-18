"""A replayed durable intent yields exact proposed bytes, never a send permit."""

import hashlib
import json
from dataclasses import FrozenInstanceError, replace
from datetime import timedelta

import pytest

from app.trade_qualification import control_bound_ledger as bound
from app.trade_qualification import demo_control as control
from app.trade_qualification.intent_request_plan import (
    plan_demo_fok_from_control_bound_intent,
)
from app.trade_qualification.submission_intent import build_submission_intent
from tests.unit.qualification_execution_binding_fixtures import (
    consumed_receipt,
    execution_binding,
)
from tests.unit.qualification_ledger_fixtures import ledger_fixture


@pytest.fixture(scope="module")
def committed():
    fixture = ledger_fixture()
    request = fixture.request
    binding = execution_binding(fixture)
    session_id = json.loads(binding.account_packet_json)["plan"]["session_binding_id"]
    owner = control.owner_binding(b"T" * 32)
    state = control.acquire(
        None,
        scope=control.ControlScope(request.scope.environment, request.scope.account_id),
        owner_sha256=owner,
        pins=control.ControlPins(
            "a" * 64,
            request.origin.original_policy_sha256,
            control.digest(session_id),
        ),
        now=fixture.now,
    )
    state = control.advance(
        state,
        owner_sha256=owner,
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
        request, reserved, state, "c" * 64, checked_at=fixture.now
    )
    inner = build_submission_intent(request, consumed, execution_binding=binding)
    outer = bound.build_control_bound_intent(
        request, inner, reserved_raw, state, "c" * 64
    )
    return request, inner, outer, consumed


def test_replayed_intent_yields_exact_private_rest_order_encoding(committed):
    request, inner, outer, consumed = committed
    plan = plan_demo_fok_from_control_bound_intent(request, outer)
    logical = json.loads(inner.canonical_json)["exchange_request"]
    expected_body = json.dumps(
        logical["body"],
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    assert plan.body_bytes == expected_body
    assert plan.body_sha256 == hashlib.sha256(expected_body).hexdigest()
    assert plan.intent_sha256 == outer.sha256
    assert (
        plan.logical_request_sha256
        == json.loads(inner.canonical_json)["exchange_request_sha256"]
    )
    assert plan.method == "POST"
    assert plan.path == "/api/v5/trade/order"
    assert plan.environment == "demo"
    assert plan.client_order_id == "CTQ" + consumed.reservation_id[:29]
    assert plan.protection_client_order_id == "CTA" + consumed.reservation_id[:29]
    assert plan.exp_time == logical["headers"]["expTime"]
    assert plan.safe_headers == (
        ("Content-Type", "application/json"),
        ("x-simulated-trading", "1"),
        ("expTime", plan.exp_time),
    )
    assert plan.admission == "DENY"
    assert plan.execution_authority is False
    assert plan.order_retry_authority is False
    assert plan.order_submitted is False


@pytest.mark.parametrize(
    ("location", "replacement"),
    [
        (("exchange_request", "body", "px"), "1"),
        (("exchange_request", "body", "clOrdId"), "CTQforged"),
        (
            ("exchange_request", "body", "attachAlgoOrds", 0, "attachAlgoClOrdId"),
            "CTAforged",
        ),
        (("exchange_request", "headers", "expTime"), "1"),
        (("exchange_request", "environment"), "live"),
    ],
)
def test_rehashed_intent_field_drift_fails_full_control_replay(
    committed, location, replacement
):
    request, _, outer, _ = committed
    document = json.loads(outer.canonical_json)
    inner = json.loads(document["intent_json"])
    target = inner
    for key in location[:-1]:
        target = target[key]
    target[location[-1]] = replacement
    document["intent_json"] = json.dumps(
        inner, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    document["intent_sha256"] = hashlib.sha256(
        document["intent_json"].encode("utf-8")
    ).hexdigest()
    raw = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    tampered = replace(
        outer, canonical_json=raw, sha256=hashlib.sha256(raw.encode()).hexdigest()
    )
    with pytest.raises(ValueError, match="^demo_fok_intent_request_plan_invalid$"):
        plan_demo_fok_from_control_bound_intent(request, tampered)


def test_invalid_record_or_hash_never_produces_plan(committed):
    request, inner, outer, _ = committed
    for record in (inner, None, replace(outer, sha256="0" * 64)):
        with pytest.raises(ValueError, match="^demo_fok_intent_request_plan_invalid$"):
            plan_demo_fok_from_control_bound_intent(request, record)


def test_plan_has_no_mutable_alias_or_authority(committed):
    request, _, outer, _ = committed
    plan = plan_demo_fok_from_control_bound_intent(request, outer)
    original = plan.body_bytes
    parsed = json.loads(original)
    parsed["px"] = "0.1"
    parsed["attachAlgoOrds"][0]["attachAlgoClOrdId"] = "CTAforged"
    assert plan.body_bytes == original
    assert json.loads(plan.body_bytes)["px"] != "0.1"
    with pytest.raises(FrozenInstanceError):
        plan.body_bytes = b"{}"
    with pytest.raises(TypeError):
        plan.safe_headers[0] = ("expTime", "1")
    assert "body_bytes" not in repr(plan)
