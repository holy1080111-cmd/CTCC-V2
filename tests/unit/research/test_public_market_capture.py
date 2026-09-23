from __future__ import annotations

import asyncio
import ssl

import httpx
import pytest

from app.research import public_market_capture as capture
from app.research.public_market_receipts import PublicReceiptError, canonical
from tests.unit.research.public_receipt_fixtures import SyntheticCapture


def test_public_api_rejects_mock_transport_as_source_authentication(
    monkeypatch, tmp_path
):
    from tests.unit.research.test_public_journal_contracts import memory_publisher

    fixture, directory, journal, _ = memory_publisher(monkeypatch)
    fixture.requests.clear()
    with pytest.raises(PublicReceiptError, match="native_public_capture_required"):
        asyncio.run(
            capture.collect_and_publish_public_minutes(
                plan=fixture.plan,
                expected_plan_sha256=fixture.plan.canonical_sha256(),
                journal=journal,
            )
        )
    assert journal.checkpoint.sequence == 0
    assert journal.checkpoint.attempt_sequence == 1
    assert "attempt-00000001" in directory.content["attempts"]
    assert len(fixture.requests) == 3


def test_unhealthy_os_clock_sends_zero_requests(monkeypatch):
    fixture = SyntheticCapture(monkeypatch, rows=2)

    def stopped():
        raise PublicReceiptError("os_time_service_unsynchronized")

    monkeypatch.setattr(capture, "native_os_clock", stopped)
    with pytest.raises(PublicReceiptError, match="unsynchronized"):
        fixture.collect()
    assert not fixture.requests
    assert not fixture.clients


@pytest.mark.parametrize(
    "kind",
    [
        "http_error",
        "redirect",
        "wrong_origin",
        "timeout",
        "encoding",
        "content_type",
        "too_large",
        "body_length",
        "time_future",
    ],
)
def test_http_failures_are_never_retried(monkeypatch, kind):
    def failed(request, body):
        if kind == "timeout":
            raise httpx.ReadTimeout("synthetic timeout")
        if kind == "http_error":
            return httpx.Response(500, stream=httpx.ByteStream(b"{}"))
        if kind == "redirect":
            return httpx.Response(
                302,
                headers={"location": "https://us.okx.com"},
                stream=httpx.ByteStream(b"{}"),
            )
        if kind == "wrong_origin":
            return httpx.Response(
                200,
                request=httpx.Request("GET", "https://us.okx.com/api/v5/public/time"),
                stream=httpx.ByteStream(b"{}"),
            )
        headers = {"content-type": "application/json"}
        if kind == "encoding":
            headers["content-encoding"] = "gzip"
        if kind == "content_type":
            headers["content-type"] = "text/plain"
        if kind == "too_large":
            headers["content-length"] = "99999999"
        if kind == "body_length":
            headers["content-length"] = "100"
        if kind == "time_future":
            body["data"][0]["ts"] = str(int(body["data"][0]["ts"]) + 10000)
            return body
        return httpx.Response(200, headers=headers, stream=httpx.ByteStream(b"{}"))

    fixture = SyntheticCapture(monkeypatch, mutate_response=failed)
    with pytest.raises((PublicReceiptError, httpx.ReadTimeout)):
        fixture.collect()
    assert len(fixture.requests) == 1
    assert all(client.is_closed for client in fixture.clients)


@pytest.mark.parametrize(
    "kind",
    [
        "environment",
        "retry",
        "unverified_tls",
        "auth",
        "hook",
        "cookie",
        "query",
        "redirect",
        "proxy_mount",
    ],
)
def test_client_isolation_rejects_mutable_or_unsafe_configuration(kind):
    kwargs = {"trust_env": False}
    if kind == "environment":
        kwargs["trust_env"] = True
    elif kind == "retry":
        kwargs["transport"] = httpx.AsyncHTTPTransport(retries=1)
    elif kind == "unverified_tls":
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        kwargs["transport"] = httpx.AsyncHTTPTransport(verify=context)
    elif kind == "auth":
        kwargs["auth"] = ("synthetic", "synthetic")
    elif kind == "hook":
        kwargs["event_hooks"] = {"request": [lambda _: None]}
    elif kind == "cookie":
        kwargs["cookies"] = {"untrusted": "value"}
    elif kind == "query":
        kwargs["params"] = {"untrusted": "value"}
    elif kind == "redirect":
        kwargs["follow_redirects"] = True
    elif kind == "proxy_mount":
        kwargs["mounts"] = {"all://": httpx.AsyncHTTPTransport()}
    client = httpx.AsyncClient(**kwargs)
    try:
        with pytest.raises(PublicReceiptError):
            capture._client_guard(client, first=True)
    finally:
        asyncio.run(client.aclose())


@pytest.mark.parametrize("future_ms", [168, 181, 169])
def test_previously_observed_future_clock_offsets_still_reject(monkeypatch, future_ms):
    def future(request, body):
        body["data"][0]["ts"] = str(int(body["data"][0]["ts"]) + future_ms)
        return body

    fixture = SyntheticCapture(monkeypatch, mutate_response=future)
    with pytest.raises(PublicReceiptError, match="exchange_time_outside_request"):
        fixture.collect()
    assert len(fixture.requests) == 1


def test_short_page_does_not_mean_complete(monkeypatch):
    def short(request, body):
        if request.url.path.endswith("candles"):
            body["data"] = body["data"][:1]
        return body

    fixture = SyntheticCapture(monkeypatch, rows=4, mutate_response=short)
    packet, _ = fixture.collect()
    assert len(packet.pages) == 4
    assert len(packet.rows) == 4
    assert len(fixture.requests) == 6


def test_short_page_budget_exhaustion_is_incomplete(monkeypatch):
    def short(request, body):
        if request.url.path.endswith("candles"):
            body["data"] = body["data"][:1]
        return body

    fixture = SyntheticCapture(monkeypatch, rows=4, mutate_response=short)
    fixture.plan = type(fixture.plan).model_validate(
        {**fixture.plan.model_dump(), "max_pages": 1}
    )
    with pytest.raises(PublicReceiptError, match="capture_page_budget"):
        fixture.collect()
    assert len(fixture.requests) == 2


def test_response_cleanup_failure_prevents_completed_packet(monkeypatch):
    class FailingClose(httpx.AsyncByteStream):
        def __init__(self, raw):
            self.raw = raw

        async def __aiter__(self):
            yield self.raw

        async def aclose(self):
            raise OSError("synthetic sharing failure")

    def failed(request, body):
        return httpx.Response(
            200,
            stream=FailingClose(canonical(body)),
            headers={"content-type": "application/json"},
        )

    fixture = SyntheticCapture(monkeypatch, mutate_response=failed)
    with pytest.raises(PublicReceiptError, match="public_cleanup_failed"):
        fixture.collect()
    assert len(fixture.requests) == 1
    assert all(client.is_closed for client in fixture.clients)


def test_cancelled_request_is_not_retried(monkeypatch):
    def cancelled(request, body):
        raise asyncio.CancelledError

    fixture = SyntheticCapture(monkeypatch, mutate_response=cancelled)
    with pytest.raises(asyncio.CancelledError):
        fixture.collect()
    assert len(fixture.requests) == 1
    assert all(client.is_closed for client in fixture.clients)
