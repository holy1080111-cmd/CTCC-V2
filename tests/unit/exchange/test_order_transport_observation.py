"""No real transport, order, Notion or reporting writes are used here."""

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.exchange.okx import private_rest
from app.exchange.okx.errors import OkxPrivateApiError
from app.exchange.okx.private_rest import OkxDemoPrivateRestClient
from app.exchange.okx.transport_observation import (
    MAX_ORDER_RESPONSE_BYTES,
    observe_order_response,
)
from tests.unit.exchange.test_private_rest import demo_settings

NOW = datetime(2026, 10, 13, 13, 0, tzinfo=UTC)
BODY = b'{"clOrdId":"CTQ123","ordType":"fok"}'


class StaticStream(httpx.AsyncByteStream):
    def __init__(self, body):
        self.body = body

    async def __aiter__(self):
        yield self.body


async def observed(body, *, headers=None, status=200, stream=None):
    times = iter((NOW + timedelta(milliseconds=1), NOW + timedelta(milliseconds=2)))

    def handler(request):
        return httpx.Response(
            status,
            stream=StaticStream(body) if stream is None else stream,
            headers=headers or {"content-type": "application/json"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        request = client.build_request(
            "POST",
            "https://www.okx.com/api/v5/trade/order",
            content=BODY,
            headers={"OK-ACCESS-KEY": "never-log-this-key"},
        )
        response = await client.send(request, stream=True)
        result = await observe_order_response(
            request, response, send_invoked_at=NOW, clock=lambda: next(times)
        )
        assert response.is_closed
        return result


@pytest.mark.asyncio
async def test_exact_raw_response_and_body_hash_are_retained_privately():
    raw = b' { "code": "0", "data": [] }\n'
    result = await observed(raw, headers={"content-length": str(len(raw))})
    assert result.transport_complete is True
    assert result.raw_body == raw
    assert result.request_body_sha256 == hashlib.sha256(BODY).hexdigest()
    assert result.send_invoked_at == NOW
    assert result.response_returned_at == NOW + timedelta(milliseconds=1)
    assert result.body_observed_at == NOW + timedelta(milliseconds=2)
    assert result.execution_authority is False
    assert result.order_retry_authority is False
    assert "never-log-this-key" not in repr(result)
    assert raw.decode() not in repr(result)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body,headers",
    (
        (b"abc", {"content-length": "4"}),
        (b"x" * (MAX_ORDER_RESPONSE_BYTES + 1), {}),
        (b"abc", {"content-encoding": "gzip"}),
        (b"abc", {"content-length": str(MAX_ORDER_RESPONSE_BYTES + 1)}),
    ),
    ids=("short-body", "oversized-stream", "encoded", "declared-oversized"),
)
async def test_bounded_or_contradictory_response_remains_unknown(body, headers):
    result = await observed(body, headers=headers)
    assert result.transport_complete is False
    assert result.raw_body is None
    assert result.body_observed_at is None
    assert result.execution_authority is False


class BrokenStream(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield b"partial"
        raise httpx.ReadError("private raw error")


@pytest.mark.asyncio
async def test_stream_failure_drops_partial_body_and_never_means_rejection():
    result = await observed(b"", stream=BrokenStream())
    assert result.transport_complete is False
    assert result.raw_body is None
    assert result.body_observed_at is None


class CancelledStream(httpx.AsyncByteStream):
    async def __aiter__(self):
        raise asyncio.CancelledError
        yield b"unreachable"


@pytest.mark.asyncio
async def test_cancellation_is_never_converted_to_a_rejected_order():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, stream=CancelledStream())
        )
    ) as client:
        request = client.build_request(
            "POST", "https://www.okx.com/api/v5/trade/order", content=BODY
        )
        response = await client.send(request, stream=True)
        with pytest.raises(asyncio.CancelledError):
            await observe_order_response(
                request, response, send_invoked_at=NOW, clock=lambda: NOW
            )
        assert response.is_closed


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", ("invalid_clock", "reversed_clock", "wrong_request")
)
async def test_all_post_send_preflight_failures_close_response(failure):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, stream=StaticStream(b"private"))
        )
    ) as client:
        original = client.build_request(
            "POST", "https://www.okx.com/api/v5/trade/order", content=BODY
        )
        response = await client.send(original, stream=True)
        supplied = (
            client.build_request(
                "POST", "https://www.okx.com/api/v5/trade/order", content=BODY
            )
            if failure == "wrong_request"
            else original
        )
        observed_clock = (
            NOW.replace(tzinfo=None)
            if failure == "invalid_clock"
            else NOW - timedelta(milliseconds=1)
            if failure == "reversed_clock"
            else NOW
        )
        with pytest.raises(ValueError):
            await observe_order_response(
                supplied,
                response,
                send_invoked_at=NOW,
                clock=lambda: observed_clock,
            )
        assert response.is_closed


@pytest.mark.asyncio
async def test_observed_order_route_retains_all_existing_hard_denials():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"code": "0", "data": []})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://www.okx.com"
    ) as client:
        private = OkxDemoPrivateRestClient(client, settings=demo_settings())
        with pytest.raises(OkxPrivateApiError):
            await private.place_order_observed(
                {"instId": "BTC-USDT-SWAP", "clOrdId": "CTQ123", "ordType": "fok"}
            )
    assert requests == []


@pytest.mark.asyncio
async def test_synthetic_observed_branch_pairs_same_prepared_request_and_raw_response(
    monkeypatch,
):
    """Test-only guard stubs cannot grant production authority or reach a network."""

    requests = []
    raw = b' {"code":"0","data":[{"sCode":"0","ordId":"123"}]}\n'
    ticks = iter(NOW + timedelta(milliseconds=index) for index in range(10))

    def handler(request):
        requests.append(request)
        return httpx.Response(
            200,
            stream=StaticStream(raw),
            headers={"content-length": str(len(raw))},
        )

    with monkeypatch.context() as patch:
        patch.setattr(private_rest, "enforce_demo_submission_boundary", lambda *a: None)
        patch.setattr(private_rest, "enforce_demo_final_dispatch", lambda *a: None)
        patch.setattr(
            private_rest,
            "require_owned_private_write_authority",
            lambda *a, **k: None,
        )
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url="https://www.okx.com"
        ) as client:
            result = await OkxDemoPrivateRestClient(
                client, settings=demo_settings(), clock=lambda: next(ticks)
            ).place_order_observed(
                {"ordType": "fok", "clOrdId": "CTQ123", "instId": "BTC-USDT-SWAP"}
            )

    assert len(requests) == 1
    request = requests[0]
    assert request.url.path == "/api/v5/trade/order"
    assert request.headers["x-simulated-trading"] == "1"
    assert (
        result.observation.request_body_sha256
        == hashlib.sha256(request.content).hexdigest()
    )
    assert result.observation.raw_body == raw
    assert result.response_body_sha256 == hashlib.sha256(raw).hexdigest()
    assert result.observation.transport_complete is True
    assert result.execution_authority is False
    assert result.order_retry_authority is False
    assert "ordId" not in repr(result)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ("timeout", "cancel"))
async def test_synthetic_post_send_failure_never_retries_or_returns_ack(
    monkeypatch, failure
):
    requests = []

    async def handler(request):
        requests.append(request)
        if failure == "cancel":
            raise asyncio.CancelledError
        raise httpx.ReadTimeout("unknown exchange result")

    with monkeypatch.context() as patch:
        patch.setattr(private_rest, "enforce_demo_submission_boundary", lambda *a: None)
        patch.setattr(private_rest, "enforce_demo_final_dispatch", lambda *a: None)
        patch.setattr(
            private_rest,
            "require_owned_private_write_authority",
            lambda *a, **k: None,
        )
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url="https://www.okx.com"
        ) as client:
            private = OkxDemoPrivateRestClient(client, settings=demo_settings())
            if failure == "cancel":
                with pytest.raises(asyncio.CancelledError):
                    await private.place_order_observed({"ordType": "fok"})
            else:
                with pytest.raises(OkxPrivateApiError) as error:
                    await private.place_order_observed({"ordType": "fok"})
                assert error.value.code == "transport_error"
    assert len(requests) == 1
