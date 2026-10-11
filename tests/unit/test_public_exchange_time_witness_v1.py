"""An offline time witness may prove consistency, never source ownership."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from app.domain.source_primitives import MAX_TIMESTAMP_NS, canonical, sha
from app.trade_qualification import public_exchange_time_witness_v1 as witness
from app.trade_qualification.public_exchange_time_witness_v1 import (
    PublicExchangeTimeWitnessError,
    build_diagnostic_public_time_witness_v1,
    verify_public_time_witness_v1,
)

SERVER_MS = 1_786_000_000_000
SERVER_NS = SERVER_MS * 1_000_000
BASE_MONOTONIC_NS = 8_000_000_000_000
HEADERS = (
    ("accept", "application/json"),
    ("accept-encoding", "identity"),
    ("user-agent", "CTCC-source-time/1"),
    ("x-simulated-trading", "1"),
)


def _stamp(offset_ns):
    return {
        "utc_ns": SERVER_NS + offset_ns,
        "monotonic_ns": BASE_MONOTONIC_NS + offset_ns,
    }


def _inputs(**changes):
    value = {
        "raw_body": canonical(
            {"code": "0", "msg": "", "data": [{"ts": str(SERVER_MS)}]}
        ),
        "registration_region": "global",
        "rest_origin": "https://openapi.okx.com",
        "tls_hostname": "openapi.okx.com",
        "tls_version": "TLSv1.3",
        "tls_peer_sha256": "a" * 64,
        "response_url": "https://openapi.okx.com/api/v5/public/time",
        "status_code": 200,
        "redirect_count": 0,
        "request_headers": HEADERS,
        "invocation_id": "b" * 32,
        "capture_plan_sha256": "c" * 64,
        "stage": "pre_capture",
        "request_start": _stamp(-200_000_000),
        "headers_received": _stamp(20_000_000),
        "body_complete": _stamp(100_000_000),
        "validation_complete": _stamp(120_000_000),
    }
    value.update(changes)
    return value


def _build(**changes):
    arguments = _inputs(**changes)
    return build_diagnostic_public_time_witness_v1(
        arguments.pop("raw_body"), **arguments
    )


def test_exact_raw_canonical_hashes_and_replay_stay_deny():
    value = _build()
    receipt = json.loads(value.receipt_json)
    assert value.canonical_body == _inputs()["raw_body"]
    assert receipt["raw_body_sha256"] == sha(value.raw_body)
    assert receipt["canonical_body_sha256"] == sha(value.canonical_body)
    assert receipt["timestamp_semantics"] == "server_system_time"
    assert receipt["server_time_ms"] == str(SERVER_MS)
    assert receipt["transport_claim_scope"] == "caller_supplied_unverified"
    assert receipt["admission"] == "DENY"
    assert all(
        receipt[name] is False
        for name in (
            "registration_region_authenticated",
            "transport_authenticity_verified",
            "source_authenticity_verified",
            "native_capture_binding_verified",
            "point_in_time_verified",
            "execution_authority",
        )
    )
    assert value.admission == "DENY"
    assert value.execution_authority is False
    assert (
        verify_public_time_witness_v1(
            value, expected_receipt_sha256=value.receipt_sha256
        )
        == value
    )


def test_noncanonical_wire_bytes_are_retained_while_json_is_canonicalized():
    raw = (
        b'{ "data" : [ { "ts" : "'
        + str(SERVER_MS).encode("ascii")
        + b'" } ], "msg" : "", "code" : "0" }'
    )
    value = _build(raw_body=raw)
    receipt = json.loads(value.receipt_json)
    assert value.raw_body == raw
    assert value.canonical_body != raw
    assert receipt["raw_body_sha256"] == sha(raw)
    assert receipt["canonical_body_sha256"] == sha(value.canonical_body)


def test_server_time_after_headers_but_before_body_remains_valid():
    value = _build(headers_received=_stamp(-20_000_000))
    assert value.admission == "DENY"


@pytest.mark.parametrize(
    "changes",
    [
        {"registration_region": "not_reviewed"},
        {"rest_origin": "https://www.okx.com"},
        {"tls_hostname": "www.okx.com"},
        {"tls_version": "TLSv1.1"},
        {"tls_peer_sha256": ""},
        {"response_url": "https://openapi.okx.com/api/v5/public/time?x=1"},
        {"status_code": 302},
        {"redirect_count": 1},
        {"request_headers": HEADERS[:-1]},
        {"request_headers": HEADERS + (("ok-access-key", "secret"),)},
        {"invocation_id": "invalid"},
        {"capture_plan_sha256": "invalid"},
        {"stage": "unknown"},
    ],
)
def test_unpinned_route_or_transport_claim_rejected(changes):
    with pytest.raises(PublicExchangeTimeWitnessError):
        _build(**changes)


def test_equal_but_nonexact_header_scalar_has_fixed_local_rejection():
    class HeaderString(str):
        pass

    headers = ((HeaderString("accept"), "application/json"), *HEADERS[1:])
    assert headers == HEADERS
    with pytest.raises(
        PublicExchangeTimeWitnessError,
        match="public_time_scope_or_transport_claim_invalid",
    ):
        _build(request_headers=headers)


@pytest.mark.parametrize(
    "region,origin,host",
    [
        ("us_au", "https://us.okx.com", "us.okx.com"),
        ("eea", "https://eea.okx.com", "eea.okx.com"),
    ],
)
def test_exact_reviewed_region_can_be_replayed_but_is_unverified(region, origin, host):
    value = _build(
        registration_region=region,
        rest_origin=origin,
        tls_hostname=host,
        response_url=origin + "/api/v5/public/time",
        stage="post_capture",
    )
    assert json.loads(value.receipt_json)["registration_region_authenticated"] is False
    assert value.admission == "DENY"


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b'{"code":"0","code":"0","msg":"","data":[{"ts":"1786000000000"}]}',
        b'{"code":"0","msg":"","data":[]}',
        b'{"code":"0","msg":"","data":[{"ts":"1786000000000"},{"ts":"1786000000000"}]}',
        b'{"code":"0","msg":"","data":[{"ts":"1786000000000","other":0}]}',
        b'{"code":"0","msg":"","data":[{"ts":1786000000000}]}',
        b'{"code":"0","msg":"","data":[{"ts":"1786000000000"}],"extra":1}',
        b'{"code":"0","msg":"","data":[{"ts":"1786000000000"}],"bad":NaN}',
    ],
)
def test_malformed_or_ambiguous_raw_body_rejected(raw):
    with pytest.raises(PublicExchangeTimeWitnessError):
        _build(raw_body=raw)


@pytest.mark.parametrize(
    "server_delta_ms",
    [-201, 101],
)
def test_server_time_must_be_inside_measured_request_to_body(server_delta_ms):
    raw = canonical(
        {
            "code": "0",
            "msg": "",
            "data": [{"ts": str(SERVER_MS + server_delta_ms)}],
        }
    )
    with pytest.raises(
        PublicExchangeTimeWitnessError,
        match="public_time_server_outside_request_interval",
    ):
        _build(raw_body=raw)


@pytest.mark.parametrize(
    "changes",
    [
        {"headers_received": _stamp(-201_000_000)},
        {"body_complete": _stamp(10_000_000)},
        {"validation_complete": _stamp(5_200_000_000)},
        {
            "validation_complete": {
                "utc_ns": SERVER_NS + 130_000_000,
                "monotonic_ns": BASE_MONOTONIC_NS + 120_000_000,
            }
        },
        {"request_start": {"utc_ns": True, "monotonic_ns": BASE_MONOTONIC_NS}},
    ],
)
def test_reversed_stale_jumped_or_invalid_clock_rejected(changes):
    with pytest.raises(PublicExchangeTimeWitnessError):
        _build(**changes)


@pytest.mark.parametrize("invalid_ns", [-1, MAX_TIMESTAMP_NS + 1])
def test_out_of_range_clock_field_has_fixed_local_rejection(invalid_ns):
    bad = _stamp(-200_000_000)
    bad["utc_ns"] = invalid_ns
    with pytest.raises(
        PublicExchangeTimeWitnessError,
        match="^public_time_clock_sequence_invalid$",
    ):
        _build(request_start=bad)


def test_raw_and_receipt_tampering_or_self_promotion_rejected():
    value = _build()
    with pytest.raises(PublicExchangeTimeWitnessError):
        verify_public_time_witness_v1(
            replace(value, raw_body=value.raw_body + b" "),
            expected_receipt_sha256=value.receipt_sha256,
        )
    with pytest.raises(PublicExchangeTimeWitnessError):
        verify_public_time_witness_v1(value, expected_receipt_sha256="d" * 64)
    document = json.loads(value.receipt_json)
    document["execution_authority"] = True
    promoted = replace(value, receipt_json=canonical(document))
    with pytest.raises(PublicExchangeTimeWitnessError):
        verify_public_time_witness_v1(
            promoted, expected_receipt_sha256=promoted.receipt_sha256
        )


def test_rejected_raw_clock_route_and_receipt_do_not_chain_private_errors():
    valid = _build()
    probes = (
        lambda: _build(raw_body=b"\xffPRIVATE"),
        lambda: _build(request_start={"utc_ns": -1, "monotonic_ns": 1}),
        lambda: _build(registration_region="not_reviewed"),
        lambda: verify_public_time_witness_v1(
            replace(valid, receipt_json=b"\xffPRIVATE"),
            expected_receipt_sha256=sha(b"\xffPRIVATE"),
        ),
    )
    for probe in probes:
        with pytest.raises(PublicExchangeTimeWitnessError) as caught:
            probe()
        assert caught.value.__cause__ is None
        assert caught.value.__context__ is None


def test_oversized_receipt_is_rejected_before_unbounded_hash(monkeypatch):
    def unexpected_sha(_value):
        pytest.fail("oversized receipt must not be hashed")

    valid = _build()
    monkeypatch.setattr(witness, "sha", unexpected_sha)
    with pytest.raises(
        PublicExchangeTimeWitnessError,
        match="public_time_witness_identity_invalid",
    ):
        verify_public_time_witness_v1(
            replace(valid, receipt_json=b"x" * (witness.MAX_RECEIPT_BYTES + 1)),
            expected_receipt_sha256="d" * 64,
        )
