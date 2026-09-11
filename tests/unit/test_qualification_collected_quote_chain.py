"""Synthetic publication -> new public capture -> fixed-zone check.

This tests real module boundaries with a mock public transport. It is not the
complete G12/post-render evaluator: interval OHLC, executable RR, fresh account
risk, durable reservations and a real runtime authority boundary remain required.
"""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest

from app.trade_evidence.renderer import render_evidence
from app.trade_evidence.storage import publish_evidence
from app.trade_qualification.location import evaluate_location
from app.trade_qualification.quote_collector import (
    QuoteCollectionError,
    QuoteCollectionPolicy,
    collect_executable_quote,
    validate_collected_quote,
)
from tests.unit.test_qualification_events import OBSERVED
from tests.unit.test_trade_evidence_pipeline import synthetic_snapshot

D = Decimal


class Body(httpx.AsyncByteStream):
    def __init__(self, payload):
        self.payload = payload
        self.closed = False

    async def __aiter__(self):
        middle = len(self.payload) // 2
        yield self.payload[:middle]
        yield self.payload[middle:]

    async def aclose(self):
        self.closed = True


def milliseconds(value):
    delta = value - datetime(1970, 1, 1, tzinfo=UTC)
    return str((delta.days * 86400 + delta.seconds) * 1000 + delta.microseconds // 1000)


@pytest.mark.parametrize("direction", ["long", "short"])
@pytest.mark.parametrize("move", ["inside", "outside"])
async def test_published_candidate_is_not_repriced_after_fresh_public_capture(
    tmp_path, direction, move
):
    snapshot = synthetic_snapshot(direction)
    q = snapshot.qualification
    packet = render_evidence(snapshot)
    before = snapshot.model_dump_json(round_trip=True)
    root = tmp_path / "trusted-evidence-root"
    root.mkdir()
    published_at = OBSERVED + timedelta(seconds=2)
    receipt = publish_evidence(
        root, report_id=q.report_id, files=packet, clock=lambda: published_at
    )
    zone = q.entry_zone
    reference = q.candidate_entry
    if move == "outside":
        reference = (
            zone.zone_high + D(1) if direction == "long" else zone.zone_low - D(1)
        )
    bid = reference - D("0.01") if direction == "long" else reference
    ask = reference if direction == "long" else reference + D("0.01")
    collection_start = published_at + timedelta(seconds=1)
    times = iter(
        collection_start + timedelta(milliseconds=index * 100) for index in range(10)
    )
    requests, streams = [], []

    def respond(request):
        index = len(requests)
        requests.append(request)
        row = {
            "instId": snapshot.instrument_id,
            "instType": "SWAP",
            "ts": milliseconds(collection_start + timedelta(milliseconds=index * 300)),
        }
        if request.url.path == "/api/v5/market/ticker":
            row.update(bidPx=str(bid), askPx=str(ask), bidSz="3", askSz="4")
        elif request.url.path == "/api/v5/public/mark-price":
            row["markPx"] = str(reference)
        elif request.url.path == "/api/v5/public/funding-rate":
            row.update(
                fundingRate="0",
                nextFundingTime=milliseconds(OBSERVED + timedelta(hours=8)),
            )
        else:
            raise AssertionError("Unexpected endpoint")
        body = Body(json.dumps({"code": "0", "msg": "", "data": [row]}).encode())
        streams.append(body)
        return httpx.Response(
            200, headers={"content-type": "application/json"}, stream=body
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond), trust_env=False
    ) as client:
        collected = await collect_executable_quote(
            client=client,
            clock=lambda: next(times),
            report_id=q.report_id,
            instrument_id=snapshot.instrument_id,
            policy=QuoteCollectionPolicy(max_age_seconds=5),
            barrier_completed_at=receipt.completed_at,
        )
    assert validate_collected_quote(collected) == collected
    assert len(requests) == len(streams) == 3 and all(
        stream.closed for stream in streams
    )
    assert all(
        request.method == "GET" and request.url.host == "www.okx.com"
        for request in requests
    )
    assert all(
        item.request_started_at > receipt.completed_at for item in collected.provenance
    )
    assert (
        collected.quote.quote_time
        < collected.quote.mark_time
        < collected.quote.funding_time
    )
    location = evaluate_location(
        report_id=q.report_id,
        instrument_id=snapshot.instrument_id,
        direction=direction,
        zone=zone,
        candidate_entry=q.candidate_entry,
        quote=collected.quote,
        current_time=collected.completed_at,
        max_quote_age_seconds=5,
    )
    assert location.passed is (move == "inside")
    assert location.code == (
        "passed" if move == "inside" else "reference_outside_entry_zone"
    )
    assert location.reference_price == reference
    assert snapshot.model_dump_json(round_trip=True) == before
    for name, payload in packet.items():
        assert (root / q.report_id / name).read_bytes() == payload
    assert hashlib.sha256(packet["report.json"]).hexdigest() == receipt.report_sha256
    assert collected.execution_authority is False
    assert location.execution_authority is False
    assert q.qualified is False
    assert snapshot.evidence_gate == snapshot.execution_recheck == "not_evaluated"


@pytest.mark.parametrize("offset", [-1, 0])
async def test_capture_cannot_start_at_or_before_publication_barrier(offset):
    requests = []

    def forbidden(request):
        requests.append(request)
        raise AssertionError("Barrier failure must precede HTTP IO")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(forbidden), trust_env=False
    ) as client:
        with pytest.raises(
            QuoteCollectionError, match="publication_barrier_not_crossed"
        ):
            await collect_executable_quote(
                client=client,
                clock=lambda: OBSERVED + timedelta(microseconds=offset),
                report_id="synthetic_publication_barrier",
                instrument_id="BTC-USDT-SWAP",
                policy=QuoteCollectionPolicy(max_age_seconds=5),
                barrier_completed_at=OBSERVED,
            )
    assert requests == []
