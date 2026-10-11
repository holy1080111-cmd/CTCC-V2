"""Offline, DENY-only replay of a claimed OKX public system-time response.

The caller supplies every byte, clock stamp, route and TLS assertion. A matching
digest or a plausible server timestamp does not authenticate any of them or
bind this diagnostic to a native capture. The native public owner must acquire
and join its own evidence before this can participate in source admission.

OKX documents ``GET /api/v5/public/time`` ``ts`` as system time, unlike a
historical candlestick's opening time or a funding forecast's settlement time:
https://www.okx.com/docs-v5/en/#public-data-rest-api-get-system-time
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from pydantic import ValidationError

from app.domain.source_primitives import (
    MAX_CLOCK_DEVIATION_NS,
    PublicReceiptError,
    canonical,
    decode,
    sha,
    validate_stamps,
)
from app.trade_qualification.demo_public_origin import reviewed_demo_public_route

SCHEMA_VERSION = "ctcc.public_exchange_time_witness.v1"
ENDPOINT = "/api/v5/public/time"
MAX_BODY_BYTES = 4096
MAX_RECEIPT_BYTES = 8192
MAX_OBSERVATION_NS = 5_000_000_000
_DIGEST = re.compile(r"[a-f0-9]{64}")
_INVOCATION = re.compile(r"[a-f0-9]{32}")
_MILLISECONDS = re.compile(r"[1-9][0-9]{12}")
_REQUEST_HEADERS = (
    ("accept", "application/json"),
    ("accept-encoding", "identity"),
    ("user-agent", "CTCC-source-time/1"),
    ("x-simulated-trading", "1"),
)
_FLAGS = (
    "registration_region_authenticated",
    "transport_authenticity_verified",
    "source_authenticity_verified",
    "native_capture_binding_verified",
    "point_in_time_verified",
    "execution_authority",
)


class PublicExchangeTimeWitnessError(ValueError):
    """Fixed local rejection code; no response or transport text is exposed."""


def _deny(code: str):
    raise PublicExchangeTimeWitnessError(code)


def _raw_body(raw_body: bytes) -> tuple[bytes, str]:
    if type(raw_body) is not bytes or not 0 < len(raw_body) <= MAX_BODY_BYTES:
        _deny("public_time_raw_body_invalid")
    try:
        payload = decode(raw_body, MAX_BODY_BYTES)
    except PublicReceiptError:
        # Leave the decoder's exception handler before raising. Its chained
        # UnicodeDecodeError could otherwise retain the rejected raw bytes.
        payload = None
    if payload is None:
        _deny("public_time_raw_body_invalid")
    if (
        type(payload) is not dict
        or set(payload) != {"code", "msg", "data"}
        or payload["code"] != "0"
        or payload["msg"] != ""
        or type(payload["data"]) is not list
        or len(payload["data"]) != 1
        or type(payload["data"][0]) is not dict
        or set(payload["data"][0]) != {"ts"}
    ):
        _deny("public_time_envelope_invalid")
    server_ms = payload["data"][0]["ts"]
    if type(server_ms) is not str or _MILLISECONDS.fullmatch(server_ms) is None:
        _deny("public_time_server_timestamp_invalid")
    return canonical(payload), server_ms


def _stamps(request_start, headers_received, body_complete, validation_complete):
    values = (request_start, headers_received, body_complete, validation_complete)
    if any(
        type(item) is not dict
        or set(item) != {"utc_ns", "monotonic_ns"}
        or any(type(item[name]) is not int for name in item)
        for item in values
    ):
        _deny("public_time_clock_stamp_invalid")
    try:
        checked = validate_stamps(values)
    except (PublicReceiptError, ValidationError):
        checked = None
    if checked is None:
        _deny("public_time_clock_sequence_invalid")
    if checked[-1].monotonic_ns - checked[0].monotonic_ns > MAX_OBSERVATION_NS:
        _deny("public_time_observation_stale")
    return tuple(value.model_dump() for value in checked)


@dataclass(frozen=True, slots=True)
class PublicExchangeTimeWitnessV1:
    """Raw and canonical bytes plus a replayable, untrusted diagnostic receipt."""

    raw_body: bytes
    canonical_body: bytes
    receipt_json: bytes

    @property
    def receipt_sha256(self) -> str:
        return sha(self.receipt_json)

    @property
    def admission(self) -> Literal["DENY"]:
        return "DENY"

    @property
    def execution_authority(self) -> Literal[False]:
        return False


def build_diagnostic_public_time_witness_v1(
    raw_body: bytes,
    *,
    registration_region: str,
    rest_origin: str,
    tls_hostname: str,
    tls_version: str,
    tls_peer_sha256: str,
    response_url: str,
    status_code: int,
    redirect_count: int,
    request_headers: tuple[tuple[str, str], ...],
    invocation_id: str,
    capture_plan_sha256: str,
    stage: Literal["pre_capture", "post_capture"],
    request_start: dict,
    headers_received: dict,
    body_complete: dict,
    validation_complete: dict,
) -> PublicExchangeTimeWitnessV1:
    """Check one caller-supplied time response without issuing source authority."""
    if type(registration_region) is not str:
        _deny("public_time_route_invalid")
    try:
        route = reviewed_demo_public_route(registration_region)
    except ValueError:
        route = None
    if route is None:
        _deny("public_time_route_invalid")
    if (
        type(rest_origin) is not str
        or rest_origin != route.rest_origin
        or type(tls_hostname) is not str
        or tls_hostname != route.rest_hostname
        or type(response_url) is not str
        or response_url != route.rest_origin + ENDPOINT
        or type(tls_version) is not str
        or tls_version not in ("TLSv1.2", "TLSv1.3")
        or type(tls_peer_sha256) is not str
        or _DIGEST.fullmatch(tls_peer_sha256) is None
        or type(status_code) is not int
        or status_code != 200
        or type(redirect_count) is not int
        or redirect_count != 0
        or type(request_headers) is not tuple
        or any(
            type(pair) is not tuple
            or len(pair) != 2
            or type(pair[0]) is not str
            or type(pair[1]) is not str
            for pair in request_headers
        )
        or request_headers != _REQUEST_HEADERS
        or type(invocation_id) is not str
        or _INVOCATION.fullmatch(invocation_id) is None
        or type(capture_plan_sha256) is not str
        or _DIGEST.fullmatch(capture_plan_sha256) is None
        or type(stage) is not str
        or stage not in ("pre_capture", "post_capture")
    ):
        _deny("public_time_scope_or_transport_claim_invalid")
    stamps = _stamps(
        request_start, headers_received, body_complete, validation_complete
    )
    body, server_ms = _raw_body(raw_body)
    server_ns = int(server_ms) * 1_000_000
    if not stamps[0]["utc_ns"] <= server_ns <= stamps[2]["utc_ns"]:
        _deny("public_time_server_outside_request_interval")
    document = {
        "schema_version": SCHEMA_VERSION,
        "environment": "demo",
        "registration_region": registration_region,
        "rest_origin": rest_origin,
        "method": "GET",
        "endpoint": ENDPOINT,
        "query": [],
        "request_headers": [list(pair) for pair in request_headers],
        "response_url": response_url,
        "status_code": status_code,
        "redirect_count": redirect_count,
        "tls_hostname": tls_hostname,
        "tls_version": tls_version,
        "tls_peer_sha256": tls_peer_sha256,
        "transport_claim_scope": "caller_supplied_unverified",
        "invocation_id": invocation_id,
        "capture_plan_sha256": capture_plan_sha256,
        "stage": stage,
        "timestamp_semantics": "server_system_time",
        "server_time_ms": server_ms,
        "request_start": stamps[0],
        "headers_received": stamps[1],
        "body_complete": stamps[2],
        "validation_complete": stamps[3],
        "max_observation_ns": MAX_OBSERVATION_NS,
        "max_clock_deviation_ns": MAX_CLOCK_DEVIATION_NS,
        "raw_body_size": len(raw_body),
        "raw_body_sha256": sha(raw_body),
        "canonical_body_sha256": sha(body),
        **{name: False for name in _FLAGS},
        "admission": "DENY",
    }
    receipt = canonical(document)
    if len(receipt) > MAX_RECEIPT_BYTES:
        _deny("public_time_receipt_too_large")
    return PublicExchangeTimeWitnessV1(raw_body, body, receipt)


def verify_public_time_witness_v1(
    value: PublicExchangeTimeWitnessV1, *, expected_receipt_sha256: str
) -> PublicExchangeTimeWitnessV1:
    """Replay all fields; the expected hash is an integrity pin, not authority."""
    if (
        type(value) is not PublicExchangeTimeWitnessV1
        or type(value.raw_body) is not bytes
        or type(value.canonical_body) is not bytes
        or type(value.receipt_json) is not bytes
        or type(expected_receipt_sha256) is not str
        or _DIGEST.fullmatch(expected_receipt_sha256) is None
        or not 0 < len(value.receipt_json) <= MAX_RECEIPT_BYTES
        or sha(value.receipt_json) != expected_receipt_sha256
    ):
        _deny("public_time_witness_identity_invalid")
    replay = None
    try:
        document = decode(value.receipt_json, MAX_RECEIPT_BYTES)
        if type(document) is not dict or canonical(document) != value.receipt_json:
            raise ValueError("noncanonical_public_time_receipt")
        replay = build_diagnostic_public_time_witness_v1(
            value.raw_body,
            registration_region=document["registration_region"],
            rest_origin=document["rest_origin"],
            tls_hostname=document["tls_hostname"],
            tls_version=document["tls_version"],
            tls_peer_sha256=document["tls_peer_sha256"],
            response_url=document["response_url"],
            status_code=document["status_code"],
            redirect_count=document["redirect_count"],
            request_headers=tuple(tuple(pair) for pair in document["request_headers"]),
            invocation_id=document["invocation_id"],
            capture_plan_sha256=document["capture_plan_sha256"],
            stage=document["stage"],
            request_start=document["request_start"],
            headers_received=document["headers_received"],
            body_complete=document["body_complete"],
            validation_complete=document["validation_complete"],
        )
    except (PublicReceiptError, KeyError, TypeError, ValueError):
        replay = None
    if replay is None:
        _deny("public_time_witness_replay_invalid")
    if (
        replay.canonical_body != value.canonical_body
        or replay.receipt_json != value.receipt_json
    ):
        _deny("public_time_witness_replay_mismatch")
    return replay
