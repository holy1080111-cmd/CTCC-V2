"""Synthetic fixtures only; never evidence of a real OKX TLS acquisition."""

from __future__ import annotations

import asyncio
import itertools

import httpx

from app.public_market_source import public_clock as clock
from app.public_market_source import public_market_capture as capture
from app.public_market_source.public_market_receipts import (
    MINUTE_NS,
    PublicMinuteCapturePlanV1,
    PublicRawReceiptV1,
    canonical,
    sha,
)

START = 1_787_788_800_000_000_000  # UTC 2026-08-27 00:00; exact 4H grid


def os_evidence(stamp):
    diagnostic = {
        "service_state": 4,
        "service_start_type": 2,
        "timezone_id": "Taipei Standard Time",
        "utc_offset_minutes": 480,
        "status_exit_code": 0,
        "status_text": "Leap Indicator: 0(no warning)\nStratum: 3 (secondary reference)\nSource: time.windows.com,0x8\nLast Sync Error: 0 (The command completed successfully.)\nTime since Last Good Sync Time: 10.125s",
    }
    return {
        "schema_version": "ctcc.windows_clock_observation.v1",
        "sample": stamp,
        "diagnostic": diagnostic,
        "diagnostic_sha256": sha(canonical(diagnostic)),
    }


def plan_for(rows=240):
    return PublicMinuteCapturePlanV1(
        origin="https://openapi.okx.com",
        instrument_id="BTC-USDT-SWAP",
        start_ns=START,
        end_ns=START + rows * MINUTE_NS,
        expected_rows=rows,
        created_ns=START,
    )


class SyntheticCapture:
    def __init__(self, monkeypatch, *, rows=240, mutate_response=None):
        self.plan = plan_for(rows)
        self.counter = itertools.count(1)
        self.base = self.plan.end_ns + 1_000_000_000
        self.current = self.base
        self.requests = []
        self.clients = []
        self.mutate_response = mutate_response
        monkeypatch.setattr(capture, "native_stamp", self.stamp)
        monkeypatch.setattr(
            capture, "native_os_clock", lambda: os_evidence(self.stamp())
        )
        # Test-only adapter output; this fixture proves no native clock or IO.
        monkeypatch.setattr(clock, "native_os_clock", lambda: capture.native_os_clock())
        monkeypatch.setattr(
            capture,
            "_observe_owned_clock",
            clock._observe_owned_clock,
        )
        monkeypatch.setattr(capture, "_new_client", self.client)

    def stamp(self):
        self.current = self.base + next(self.counter) * 10_000_000
        return {"utc_ns": self.current, "monotonic_ns": self.current - START}

    def handler(self, request):
        self.requests.append(request)
        if request.url.path.endswith("/time"):
            body = {
                "code": "0",
                "msg": "",
                "data": [{"ts": str((self.current + 1_000_000) // 1_000_000)}],
            }
        else:
            after = int(request.url.params["after"])
            count = int(request.url.params["limit"])
            body = {
                "code": "0",
                "msg": "",
                "data": [
                    [
                        str(after - 60_000 * (i + 1)),
                        "100",
                        "102",
                        "99",
                        "101",
                        "2",
                        "0.002",
                        "0.202",
                        "1",
                    ]
                    for i in range(count)
                ],
            }
        if self.mutate_response is not None:
            result = self.mutate_response(request, body)
            if type(result) is httpx.Response:
                return result
            body = result
        return httpx.Response(
            200,
            stream=httpx.ByteStream(canonical(body)),
            headers={
                "content-type": "application/json",
                "set-cookie": "untrusted=cookie",
                "last-modified": "Mon, 01 Jan 2001 00:00:00 GMT",
            },
        )

    def client(self):
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(self.handler),
            trust_env=False,
            follow_redirects=False,
        )
        self.clients.append(client)
        return client

    def collect(self):
        return asyncio.run(
            capture._collect_packet(self.plan, self.plan.canonical_sha256())
        )


def storage_fixture_capture(packet, files):
    """Explicitly forged private carrier for native filesystem tests, not TLS."""
    data = packet.model_dump()
    data["transport_origin"] = "owned_native_tls"
    for item in (data["time_before"], *data["pages"], data["time_after"]):
        item["transport_origin"] = "owned_native_tls"
    previous = None
    for page in data["pages"]:
        page["previous_page_sha256"] = previous
        previous = PublicRawReceiptV1.model_validate(page).canonical_sha256()
    packet = type(packet).model_validate(data)
    return capture._OwnedPublicCapture(capture._ISSUER, packet, files)
