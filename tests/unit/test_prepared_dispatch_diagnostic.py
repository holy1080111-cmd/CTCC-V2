"""DENY-only comparison of a proposed FOK intent with non-secret HTTP bytes."""

import hashlib
import json
from dataclasses import replace
from datetime import timedelta

import pytest

from app.trade_qualification import control_bound_ledger as bound
from app.trade_qualification import demo_control as control
from app.trade_qualification.prepared_dispatch_diagnostic import (
    PreparedOkxRequestProjectionV1,
    compare_control_bound_prepared_request_v1,
)
from app.trade_qualification.submission_intent import build_submission_intent
from tests.unit.qualification_execution_binding_fixtures import (
    consumed_receipt,
    execution_binding,
)
from tests.unit.qualification_ledger_fixtures import ledger_fixture


@pytest.fixture(scope="module")
def proposed():
    # The fixture internally runs asyncio and must be built outside an async test.
    fixture = ledger_fixture()
    request = fixture.request
    binding = execution_binding(fixture)
    session_id = json.loads(binding.account_packet_json)["plan"]["session_binding_id"]
    state = control.acquire(
        None,
        scope=control.ControlScope(request.scope.environment, request.scope.account_id),
        owner_sha256=control.owner_binding(b"T" * 32),
        pins=control.ControlPins(
            "a" * 64,
            request.origin.original_policy_sha256,
            control.digest(session_id),
        ),
        now=fixture.now,
    )
    state = control.advance(
        state,
        owner_sha256=control.owner_binding(b"T" * 32),
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
    document = json.loads(inner.canonical_json)
    logical = document["exchange_request"]
    body_bytes = json.dumps(
        logical["body"], sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode()
    prepared = PreparedOkxRequestProjectionV1(
        method="POST",
        url="https://openapi.okx.com/api/v5/trade/order",
        safe_headers=(
            ("Content-Type", "application/json"),
            ("x-simulated-trading", "1"),
            ("expTime", logical["headers"]["expTime"]),
        ),
        body_bytes=body_bytes,
        environment="demo",
        account_uid=request.scope.account_id,
        credential_session_sha256=state.pins.credential_session_sha256,
    )
    return request, inner, outer, prepared


def _diagnose(proposed, prepared=None, record=None):
    request, _, outer, default_prepared = proposed
    return compare_control_bound_prepared_request_v1(
        request,
        outer if record is None else record,
        default_prepared if prepared is None else prepared,
    )


def _assert_denied(result):
    assert result.version == "ctcc.prepared_dispatch_diagnostic.v1"
    assert result.admission == "DENY"
    assert result.execution_authority is False
    assert result.order_retry_authority is False
    assert result.order_submitted is False


def test_all_proposed_fields_match_but_wire_binding_is_unavailable(proposed):
    _, inner, outer, prepared = proposed
    result = _diagnose(proposed)
    assert result.code == "wire_binding_unavailable"
    assert result.proposed_fields_match is True
    assert result.mismatches == ()
    assert result.intent_sha256 == outer.sha256
    assert (
        result.logical_request_sha256
        == json.loads(inner.canonical_json)["exchange_request_sha256"]
    )
    assert (
        result.prepared_body_sha256 == hashlib.sha256(prepared.body_bytes).hexdigest()
    )
    assert result.logical_request_sha256 != result.prepared_body_sha256
    assert set(result.blockers) == {
        "intent_origin_unbound",
        "intent_wire_body_bytes_unbound",
        "signed_request_unverified",
        "credential_session_transport_unverified",
        "current_control_unverified",
        "first_byte_guard_unavailable",
    }
    _assert_denied(result)


def test_other_allowed_origin_is_still_unbound(proposed):
    prepared = replace(proposed[3], url="https://us.okx.com/api/v5/trade/order")
    result = _diagnose(proposed, prepared)
    assert result.code == "wire_binding_unavailable"
    assert "intent_origin_unbound" in result.blockers
    _assert_denied(result)


def test_equivalent_json_different_bytes_is_still_unbound(proposed):
    original = proposed[3]
    body = json.loads(original.body_bytes)
    prepared = replace(original, body_bytes=json.dumps(body, indent=2).encode())
    result = _diagnose(proposed, prepared)
    assert result.code == "wire_binding_unavailable"
    assert (
        result.prepared_body_sha256 != hashlib.sha256(original.body_bytes).hexdigest()
    )
    assert "intent_wire_body_bytes_unbound" in result.blockers
    _assert_denied(result)


@pytest.mark.parametrize(
    ("change", "field"),
    [
        ("method", "method"),
        ("path", "path"),
        ("query", "query"),
        ("content_type", "content_type"),
        ("demo_header", "x-simulated-trading"),
        ("expiry", "exptime"),
        ("body", "body"),
        ("client_id", "client_order_id"),
        ("protection_id", "protection_client_order_id"),
        ("environment", "environment"),
        ("account_uid", "account_uid"),
        ("session_hash", "credential_session_sha256"),
    ],
)
def test_any_available_field_mismatch_is_denied(proposed, change, field):
    prepared = proposed[3]
    if change == "method":
        prepared = replace(prepared, method="GET")
    elif change == "path":
        prepared = replace(
            prepared, url="https://openapi.okx.com/api/v5/trade/cancel-order"
        )
    elif change == "query":
        prepared = replace(prepared, url=prepared.url + "?debug=1")
    elif change in {"content_type", "demo_header", "expiry"}:
        headers = dict(prepared.safe_headers)
        key = {
            "content_type": "Content-Type",
            "demo_header": "x-simulated-trading",
            "expiry": "expTime",
        }[change]
        headers[key] = "wrong"
        prepared = replace(prepared, safe_headers=tuple(headers.items()))
    elif change in {"body", "client_id", "protection_id"}:
        body = json.loads(prepared.body_bytes)
        if change == "body":
            body["px"] = "0.1"
        elif change == "client_id":
            body["clOrdId"] = "CTQOTHER"
        else:
            body["attachAlgoOrds"][0]["attachAlgoClOrdId"] = "CTAOTHER"
        prepared = replace(prepared, body_bytes=json.dumps(body).encode())
    elif change == "environment":
        prepared = replace(prepared, environment="live")
    elif change == "account_uid":
        prepared = replace(prepared, account_uid="999999999")
    else:
        prepared = replace(prepared, credential_session_sha256="b" * 64)
    result = _diagnose(proposed, prepared)
    assert result.code == "prepared_mismatch"
    assert result.proposed_fields_match is False
    assert field in result.mismatches
    _assert_denied(result)


@pytest.mark.parametrize(
    "change",
    [
        "secret_header",
        "foreign_origin",
        "userinfo",
        "duplicate_key",
        "projection_version",
    ],
)
def test_invalid_projection_is_denied_without_echoing_values(proposed, change):
    prepared = proposed[3]
    if change == "secret_header":
        prepared = replace(
            prepared,
            safe_headers=prepared.safe_headers + (("OK-ACCESS-KEY", "synthetic"),),
        )
    elif change == "foreign_origin":
        prepared = replace(prepared, url="https://example.invalid/api/v5/trade/order")
    elif change == "userinfo":
        prepared = replace(
            prepared, url="https://u:p@openapi.okx.com/api/v5/trade/order"
        )
    elif change == "duplicate_key":
        prepared = replace(prepared, body_bytes=b'{"clOrdId":"a","clOrdId":"b"}')
    else:
        prepared = replace(prepared)
        object.__setattr__(prepared, "version", "v2")
    result = _diagnose(proposed, prepared)
    assert result.code == "prepared_invalid"
    assert result.proposed_fields_match is False
    assert "synthetic" not in repr(result)
    _assert_denied(result)


def test_forged_outer_record_is_denied(proposed):
    outer = proposed[2]
    forged = bound.ControlBoundSubmissionIntentRecord(outer.canonical_json, "b" * 64)
    result = _diagnose(proposed, record=forged)
    assert result.code == "record_invalid"
    assert result.proposed_fields_match is False
    _assert_denied(result)


def test_wrong_exact_record_type_is_denied(proposed):
    result = _diagnose(proposed, record=proposed[1])
    assert result.code == "record_invalid"
    _assert_denied(result)
