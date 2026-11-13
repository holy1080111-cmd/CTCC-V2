"""Public pure selector DATA tests only; no client, signature, source or order admission."""

import asyncio
import json
from unittest.mock import AsyncMock

import httpx
import pytest

from app.exchange.okx import private_rest
from app.exchange.okx.errors import OkxPrivateApiError
from app.exchange.okx.private_rest import (
    DemoConfigSelectorObservationV2,
    _demo_selector_append_chunk_v2,
    _demo_selector_json_constant_v2,
    _demo_selector_json_float_v2,
    _demo_selector_json_pairs_v2,
)
from tests.unit.exchange.test_private_rest import demo_settings


def _parse(raw):
    return json.loads(
        raw,
        object_pairs_hook=_demo_selector_json_pairs_v2,
        parse_constant=_demo_selector_json_constant_v2,
        parse_float=_demo_selector_json_float_v2,
    )


def test_exact_raw_byte_boundary_and_failed_overflow_preserve_prior_chunks():
    chunks = []
    size = _demo_selector_append_chunk_v2(chunks, 0, b"a" * 32768)
    size = _demo_selector_append_chunk_v2(chunks, size, b"b" * 32768)
    assert size == 65536 and b"".join(chunks) == b"a" * 32768 + b"b" * 32768
    with pytest.raises(ValueError, match="response_bound"):
        _demo_selector_append_chunk_v2(chunks, size, b"c")
    assert len(chunks) == 2 and len(b"".join(chunks)) == 65536


def test_empty_chunks_do_not_expand_retained_buffer_list():
    chunks = []
    for _ in range(1000):
        assert _demo_selector_append_chunk_v2(chunks, 0, b"") == 0
    assert chunks == []


def test_foreign_chunk_or_container_cannot_invoke_callbacks():
    class Foreign:
        def __len__(self):
            raise AssertionError("foreign length callback invoked")

        def append(self, _item):
            raise AssertionError("foreign append callback invoked")

    with pytest.raises(ValueError, match="stream_invalid"):
        _demo_selector_append_chunk_v2([], 0, Foreign())
    with pytest.raises(ValueError, match="stream_invalid"):
        _demo_selector_append_chunk_v2(Foreign(), 0, b"x")
    with pytest.raises(ValueError, match="stream_invalid"):
        _demo_selector_append_chunk_v2([], False, b"x")


@pytest.mark.parametrize(
    "raw",
    [
        b'{"code":"0","code":"1","data":[]}',
        b'{"code":"0","data":[{"uid":"123","uid":"456"}]}',
        b'{"code":"0","data":[{"nested":{"x":1,"x":2}}]}',
    ],
)
def test_duplicate_json_is_rejected_at_original_parser(raw):
    with pytest.raises(ValueError, match="duplicate_json"):
        _parse(raw)


@pytest.mark.parametrize(
    "number", ["NaN", "Infinity", "-Infinity", "1e10000", "-1e10000"]
)
def test_nonfinite_json_is_not_accepted(number):
    with pytest.raises(ValueError, match="nonfinite_json"):
        _parse('{"code":"0","data":[{"value":' + number + "}]}")


def test_finite_numbers_and_original_raw_utf8_are_preserved_as_data():
    raw = '{"code":"0","data":[{"uid":"123","mainUid":"123","note":"原始值","n":1.25}]}'.encode()
    parsed = _parse(raw)
    assert parsed["data"][0]["note"] == "原始值"
    assert parsed["data"][0]["n"] == 1.25
    observation = DemoConfigSelectorObservationV2(
        tuple(parsed["data"]), raw, "not-a-real-signature"
    )
    assert observation.raw_body is raw
    assert (
        observation.account_complete is False
        and observation.execution_authority is False
    )
    assert "not-a-real-signature" not in repr(observation) and "原始值" not in repr(
        observation
    )


@pytest.mark.asyncio
async def test_selector_requests_identity_bytes_on_signed_demo_get(monkeypatch):
    class RawBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'{"code":"0","data":[{"uid":"123","mainUid":"123"}]}'

    captured = []

    async def respond(request):
        captured.append(request)
        assert request.method == "GET"
        assert request.url.path == "/api/v5/account/config"
        assert request.headers["accept-encoding"] == "identity"
        assert request.headers["x-simulated-trading"] == "1"
        assert request.headers["ok-access-sign"]
        assert request.content == b""
        return httpx.Response(
            200, headers={"content-type": "application/json"}, stream=RawBody()
        )

    isolated_httpx = httpx.AsyncClient

    def in_process_transport(**kwargs):
        assert kwargs["trust_env"] is False and kwargs["follow_redirects"] is False
        return isolated_httpx(transport=httpx.MockTransport(respond), **kwargs)

    monkeypatch.setattr(private_rest.httpx, "AsyncClient", in_process_transport)
    client = private_rest.OkxDemoPrivateRestClient(settings=demo_settings())
    with client.pinned_read_session():
        observation = await client.account_config_selector_v2()
    assert len(captured) == 1
    assert observation.data == ({"uid": "123", "mainUid": "123"},)
    assert observation.execution_authority is False


@pytest.mark.parametrize("failure", [TimeoutError, asyncio.CancelledError])
@pytest.mark.asyncio
async def test_selector_reports_deadline_and_preserves_external_cancellation(
    monkeypatch, failure
):
    client = private_rest.OkxDemoPrivateRestClient(settings=demo_settings())
    read = AsyncMock(side_effect=failure)
    monkeypatch.setattr(client, "_request", read)
    with client.pinned_read_session():
        if failure is TimeoutError:
            with pytest.raises(OkxPrivateApiError) as error:
                await client.account_config_selector_v2()
            assert error.value.code == "demo_selector_deadline_exceeded"
        else:
            with pytest.raises(asyncio.CancelledError):
                await client.account_config_selector_v2()
    read.assert_awaited_once()


@pytest.mark.parametrize(
    ("payload", "selector", "expected"),
    [
        (b'{"code":"50113"}', True, "50113"),
        (b'{"code":"50113"}', False, None),
        (b'{"code":"50113","code":"50110"}', True, None),
    ],
)
def test_selector_can_classify_bounded_documented_401_without_remote_details(
    payload, selector, expected
):
    response = httpx.Response(
        401, content=payload, headers={"content-type": "application/json"}
    )
    assert (
        private_rest._read_only_http_auth_code(response, bounded_demo_selector=selector)
        == expected
    )
