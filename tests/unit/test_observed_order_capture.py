"""Pure private capture handoff; no DB, HTTP, order or outbox writes."""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.database.repositories import submission_reporting as repository_module
from app.exchange.okx.transport_observation import (
    ObservedOrderResult,
    OrderTransportObservation,
)
from app.trade_evidence import observed_order_capture as module
from app.trade_evidence.submission_reporting import (
    SubmissionReportingError,
    classify,
    decode_capture,
    freeze_capture,
    sha,
)
from app.trade_qualification.submission_intent import _json

NOW = datetime(2026, 10, 13, 13, 0, tzinfo=UTC)
UID = "synthetic-demo-uid"
CLIENT = "CTQ123"
RAW = b' {"code":"0","data":[{"sCode":"0","ordId":"123","clOrdId":"CTQ123"}]}\n'


def setup(monkeypatch):
    order = {"instId": "BTC-USDT-SWAP", "clOrdId": CLIENT, "ordType": "fok"}
    dispatch = {
        "method": "POST",
        "path": "/api/v5/trade/order",
        "environment": "demo",
        "account_uid": UID,
        "headers": {"x-simulated-trading": "1", "expTime": "123"},
        "body": order,
    }
    intent = {
        "version": "ctcc-demo-submit-intent-v2",
        "exchange_request": dispatch,
        "exchange_request_sha256": hashlib.sha256(_json(dispatch).encode()).hexdigest(),
    }
    replayed = SimpleNamespace(canonical_json=_json(intent))
    calls = []

    def replay(request, raw, digest):
        calls.append((request, raw, digest))
        return None, replayed, None, {}

    monkeypatch.setattr(module, "_replay_reporting_intent", replay)
    request = SimpleNamespace(scope=SimpleNamespace(account_id=UID))
    order_bytes = json.dumps(
        order,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode()
    return request, intent, order_bytes, calls


def observation(order_bytes, *, raw=RAW, complete=True, body_pin=None):
    returned = NOW + timedelta(milliseconds=1)
    observed = NOW + timedelta(milliseconds=2) if complete else None
    return ObservedOrderResult(
        OrderTransportObservation(
            request_body_sha256=hashlib.sha256(order_bytes).hexdigest(),
            send_invoked_at=NOW,
            response_returned_at=returned,
            body_observed_at=observed,
            http_status=200,
            raw_body=raw if complete else None,
            transport_complete=complete,
        ),
        body_pin
        if body_pin is not None
        else hashlib.sha256(raw).hexdigest()
        if complete
        else None,
    )


def test_exact_raw_response_is_retained_but_cannot_become_ack(monkeypatch):
    request, intent, order_bytes, calls = setup(monkeypatch)
    observed = observation(order_bytes)
    capture = module.capture_unverified_order_response(
        request, "private-intent", intent_sha256="a" * 64, observed=observed
    )
    assert calls == [(request, "private-intent", "a" * 64)]
    assert capture.exchange_request_sha256 == intent["exchange_request_sha256"]
    assert capture.raw_body == RAW
    assert capture.request_started_at == NOW
    assert capture.completed_at == NOW + timedelta(milliseconds=2)
    assert capture.headers_received_at is None
    assert capture.body_completed_at is None
    assert capture.transport_complete is False
    assert classify(capture, CLIENT, NOW + timedelta(seconds=1))[0] == "uncertain"
    frozen = freeze_capture(capture)
    assert decode_capture(frozen, sha(frozen.encode())) == capture
    assert RAW.decode().strip() not in repr(capture)


@pytest.mark.parametrize("fault", ("order_body", "response_body", "raw_on_incomplete"))
def test_mutation_and_contradiction_fail_closed(monkeypatch, fault):
    request, _, order_bytes, _ = setup(monkeypatch)
    observed = observation(order_bytes)
    if fault == "order_body":
        observed = observation(b"different")
    elif fault == "response_body":
        observed = observation(order_bytes, body_pin="f" * 64)
    else:
        observed = ObservedOrderResult(
            OrderTransportObservation(
                request_body_sha256=observed.observation.request_body_sha256,
                send_invoked_at=NOW,
                response_returned_at=NOW + timedelta(milliseconds=1),
                body_observed_at=None,
                http_status=200,
                raw_body=RAW,
                transport_complete=False,
            ),
            observed.response_body_sha256,
        )
    with pytest.raises(
        SubmissionReportingError, match="^submission_transport_capture_unverified$"
    ):
        module.capture_unverified_order_response(
            request, "private-intent", intent_sha256="a" * 64, observed=observed
        )


def test_incomplete_transport_is_retained_without_fabricated_body(monkeypatch):
    request, _, order_bytes, _ = setup(monkeypatch)
    capture = module.capture_unverified_order_response(
        request,
        "private-intent",
        intent_sha256="a" * 64,
        observed=observation(order_bytes, complete=False),
    )
    assert capture.completed_at == NOW + timedelta(milliseconds=1)
    assert capture.raw_body is None and capture.transport_complete is False
    assert classify(capture, CLIENT, NOW + timedelta(seconds=1))[0] == "uncertain"


@pytest.mark.asyncio
async def test_repository_handoff_reads_persisted_intent_then_uses_existing_writer(
    monkeypatch,
):
    scope = object()
    request = SimpleNamespace(scope=scope)
    entry = SimpleNamespace(evidence_json="persisted-consumed-intent")
    row = SimpleNamespace(
        state="consumed", request_json="persisted-request", request_sha256="p" * 64
    )
    events = []

    class Rows:
        def all(self):
            return [entry]

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, kind, key):
            events.append(("read_reservation", key))
            return row

        async def scalars(self, query):
            events.append(("read_consumed_intent",))
            return Rows()

    monkeypatch.setattr(
        repository_module.reservations, "checked", lambda value, kind: value
    )
    monkeypatch.setattr(
        repository_module.reservations, "reservation_id", lambda a, b: "r" * 64
    )
    monkeypatch.setattr(
        repository_module.reservations,
        "decode_reservation_request",
        lambda raw: request,
    )
    monkeypatch.setattr(
        repository_module.reservations, "digest", lambda value: "p" * 64
    )
    sentinel = object()

    def convert(value, intent, *, intent_sha256, observed):
        events.append(("convert", value, intent, intent_sha256, observed))
        return sentinel

    async def persist(self, supplied_scope, supplied_event, **kwargs):
        events.append(("persist", supplied_scope, supplied_event, kwargs))
        return "readback-receipt"

    monkeypatch.setattr(repository_module, "capture_unverified_order_response", convert)
    monkeypatch.setattr(
        repository_module.SubmissionReportingRepository, "record_observation", persist
    )
    repository = repository_module.SubmissionReportingRepository(
        lambda: Session(), clock=lambda: NOW
    )
    outcome = await repository.record_unverified_transport_result(
        scope,
        "event",
        expected_revision=7,
        intent_sha256="a" * 64,
        observed="private-observation",
        queue_namespace="synthetic",
        outbox_policy="private-policy",
    )
    assert outcome == "readback-receipt"
    assert events[:2] == [
        ("read_reservation", "r" * 64),
        ("read_consumed_intent",),
    ]
    assert events[2] == (
        "convert",
        request,
        "persisted-consumed-intent",
        "a" * 64,
        "private-observation",
    )
    assert events[3] == (
        "persist",
        scope,
        "event",
        {
            "expected_revision": 7,
            "intent_sha256": "a" * 64,
            "capture": sentinel,
            "queue_namespace": "synthetic",
            "outbox_policy": "private-policy",
        },
    )
