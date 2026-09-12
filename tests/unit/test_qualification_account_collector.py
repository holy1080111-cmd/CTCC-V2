"""Synthetic credentials and MockTransport only; never real account or network IO."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import inspect
import ssl
import time
import traceback
from dataclasses import FrozenInstanceError, fields
from datetime import datetime, timedelta, timezone, tzinfo

import httpx
import pytest
from pydantic import model_serializer

from app.trade_qualification import account_capture as capture_api
from app.trade_qualification import account_collector as module
from tests.unit.test_qualification_account_capture import (
    BARRIER,
    CURSORS,
    NOW,
    STREAMS,
    plan,
    row,
    wire,
)

API_KEY = "synthetic-only-account-api-key"
API_SECRET = "synthetic-only-account-api-secret"
PASSPHRASE = "synthetic-only-account-passphrase"
SECRETS = (API_KEY, API_SECRET, PASSPHRASE)
FORBIDDEN_CALLS = []


@pytest.fixture(autouse=True)
def opaque_callbacks_must_not_run():
    FORBIDDEN_CALLS.clear()
    yield
    assert FORBIDDEN_CALLS == []


def credentials(**changes):
    return module.DemoAccountCredentials(
        **{
            "api_key": API_KEY,
            "api_secret": API_SECRET,
            "passphrase": PASSPHRASE,
            "session_binding_id": "synthetic-private-session",
            **changes,
        }
    )


class Clock:
    def __init__(self, overrides=None):
        self.calls = 0
        self.overrides = {} if overrides is None else overrides
        self.values = []

    def __call__(self):
        value = self.overrides.get(
            self.calls, NOW + timedelta(milliseconds=self.calls + 1)
        )
        self.calls += 1
        self.values.append(value)
        return value


class Stream(httpx.AsyncByteStream):
    def __init__(self, body, *, error=None, chunks=None):
        self.body = body
        self.error = error
        self.chunks = (body[:11], body[11:]) if chunks is None else chunks
        self.yield_count = 0
        self.close_count = 0

    async def __aiter__(self):
        for chunk in self.chunks:
            self.yield_count += 1
            yield chunk
        if self.error is not None:
            raise self.error

    async def aclose(self):
        self.close_count += 1


def script(*, empty=False, pages=None):
    pages = {} if pages is None else pages
    result = []
    for stream in STREAMS:
        if stream in pages:
            stream_pages = pages[stream]
        elif stream in CURSORS:
            stream_pages = [[]] if empty else [[row(stream)], []]
        else:
            stream_pages = [[row(stream)]]
        result.extend((stream, data) for data in stream_pages)
    return result


class Harness:
    def __init__(
        self,
        monkeypatch,
        *,
        pages=None,
        empty=False,
        change=None,
        status=200,
        response_headers=None,
        stream_error=None,
        handler_error=None,
        chunks=None,
        client_options=None,
        configure_client=None,
    ):
        self.script = script(empty=empty, pages=pages)
        self.requests = []
        self.streams = []
        self.clients = []
        self.factory_calls = 0
        self.clock = Clock()

        def handler(request):
            index = len(self.requests)
            self.requests.append(request)
            if handler_error is not None:
                raise handler_error
            assert index < len(self.script), "unexpected retry or extra account GET"
            stream_name, data = self.script[index]
            body = wire(data)
            if change is not None:
                replacement = change(stream_name, index, data)
                if replacement is not None:
                    body = (
                        replacement if type(replacement) is bytes else wire(replacement)
                    )
            response_stream = Stream(body, error=stream_error, chunks=chunks)
            self.streams.append(response_stream)
            headers = {
                "Content-Type": "application/json; charset=utf-8",
                "Content-Length": str(len(body)),
            }
            if response_headers is not None:
                headers.update(response_headers)
            return httpx.Response(status, headers=headers, stream=response_stream)

        def factory(*args, **kwargs):
            self.factory_calls += 1
            options = {"trust_env": False, **(client_options or {})}
            client = httpx.AsyncClient(
                transport=httpx.MockTransport(handler), **options
            )
            if configure_client is not None:
                configure_client(client)
            self.clients.append(client)
            return client

        monkeypatch.setattr(module, "_new_client", factory)

    async def collect(self, *, selected=None, supplied=None, clock=None, **changes):
        selected = plan() if selected is None else selected
        return await module.collect_demo_account_records(
            **{
                "credentials": credentials() if supplied is None else supplied,
                "clock": self.clock if clock is None else clock,
                "plan": selected,
                "expected_plan_sha256": capture_api.plan_sha256(selected),
                "barrier_completed_at": BARRIER,
                **changes,
            }
        )

    def assert_closed(self):
        assert all(client.is_closed for client in self.clients)
        assert all(stream.close_count == 1 for stream in self.streams)


def assert_secret_free(value):
    assert all(secret not in value for secret in SECRETS)


@pytest.mark.asyncio
@pytest.mark.parametrize("empty,expected_receipts", [(False, 21), (True, 13)])
async def test_all_streams_fixed_gets_auth_signatures_and_incomplete_packet(
    monkeypatch, empty, expected_receipts
):
    harness = Harness(monkeypatch, empty=empty)
    selected = plan()
    result = await harness.collect(selected=selected)
    assert len(result.observations) == len(harness.requests) == expected_receipts
    assert harness.factory_calls == 1
    assert harness.clock.calls == 2 + 3 * expected_receipts
    assert (
        tuple(dict.fromkeys(o.request.stream for o in result.observations)) == STREAMS
    )
    assert result.plan == selected
    assert result.plan_sha256 == capture_api.plan_sha256(selected)
    assert result.state == "records_verified_incomplete_account"
    assert result.account_complete is False
    assert result.execution_authority is False
    assert result.source_authenticity_verified is False
    assert "local_uncertain_ledger_missing" in result.incomplete_reasons
    first = result.observations[0]
    for index, (request, observed) in enumerate(
        zip(harness.requests, result.observations, strict=True)
    ):
        expected = capture_api.account_request(
            selected, observed.request.stream, observed.after
        )
        assert observed.request == expected
        assert request.method == "GET"
        assert request.url.scheme == "https" and request.url.host == "www.okx.com"
        assert request.url.path == expected.endpoint
        assert tuple(request.url.params.multi_items()) == expected.parameters
        assert request.content == b""
        assert request.headers["x-simulated-trading"] == "1"
        assert request.headers["OK-ACCESS-KEY"] == API_KEY
        assert request.headers["OK-ACCESS-PASSPHRASE"] == PASSPHRASE
        timestamp = request.headers["OK-ACCESS-TIMESTAMP"]
        signed_at = datetime.fromisoformat(timestamp)
        assert signed_at == observed.request_started_at
        prehash = timestamp.encode() + b"GET" + request.url.raw_path
        expected_signature = base64.b64encode(
            hmac.new(API_SECRET.encode(), prehash, hashlib.sha256).digest()
        ).decode()
        assert request.headers["OK-ACCESS-SIGN"] == expected_signature
        assert (
            "cookie" not in request.headers and "authorization" not in request.headers
        )
        assert observed.request_started_at == harness.clock.values[1 + index * 3]
        assert observed.headers_received_at == harness.clock.values[2 + index * 3]
        assert observed.body_completed_at == harness.clock.values[3 + index * 3]
        assert observed.request_started_at > BARRIER
        assert observed.session_binding_id == selected.session_binding_id
        assert observed.identity_receipt_sha256 == (
            None if index == 0 else first.receipt_sha256
        )
        assert (
            observed.body_sha256 == hashlib.sha256(observed.response_body).hexdigest()
        )
        assert observed.body_size_bytes == len(observed.response_body)
        assert (
            observed.canonical_sha256
            == hashlib.sha256(observed.canonical_json.encode()).hexdigest()
        )
        assert_secret_free(repr(observed))
    assert_secret_free(result.model_dump_json())
    for stream in CURSORS:
        chain = [o for o in result.observations if o.request.stream == stream]
        assert chain[-1].terminal and chain[-1].rows == ()
    harness.assert_closed()


@pytest.mark.asyncio
async def test_noncontiguous_descending_ids_produce_exact_exclusive_raw_cursor(
    monkeypatch,
):
    ids = ("987654321012345678901234567890", "987654321012345678901234567001")
    harness = Harness(
        monkeypatch,
        pages={
            "fills_history": [
                [row("fills_history", ids[0])],
                [row("fills_history", ids[1])],
                [],
            ]
        },
    )
    result = await harness.collect()
    chain = [o for o in result.observations if o.request.stream == "fills_history"]
    assert len(chain) == 3
    assert [o.after for o in chain] == [None, *ids]
    assert [o.page_index for o in chain] == [0, 1, 2]
    assert chain[1].previous_page_sha256 == chain[0].receipt_sha256
    assert chain[2].previous_page_sha256 == chain[1].receipt_sha256
    requests = [r for r in harness.requests if r.url.path.endswith("/fills-history")]
    assert b"after=" + ids[0].encode() in requests[1].url.raw_path
    assert b"after=" + ids[1].encode() in requests[2].url.raw_path
    assert all("begin" in r.url.params and "end" in r.url.params for r in requests)
    assert chain[-1].terminal
    harness.assert_closed()


@pytest.mark.asyncio
async def test_packet_freeze_external_pins_replay_and_raw_whitespace_preserved(
    monkeypatch,
):
    def whitespace(stream, index, data):
        return b" \n\t" + wire(data) + b"\r\n "

    harness = Harness(monkeypatch, change=whitespace)
    result = await harness.collect()
    frozen = capture_api.freeze_demo_account_packet(
        result, expected_plan_sha256=result.plan_sha256
    )
    restored = capture_api.verify_demo_account_packet(
        frozen.payload,
        expected_sha256=frozen.sha256,
        expected_plan_sha256=result.plan_sha256,
    )
    assert restored == result
    assert all(o.response_body.startswith(b" \n\t") for o in result.observations)
    assert_secret_free(frozen.payload.decode())
    with pytest.raises(capture_api.AccountCaptureError):
        capture_api.verify_demo_account_packet(
            frozen.payload,
            expected_sha256="0" * 64,
            expected_plan_sha256=result.plan_sha256,
        )
    harness.assert_closed()


def test_credentials_are_frozen_slotted_and_redacted():
    value = credentials()
    assert value.environment == "demo"
    assert not hasattr(value, "__dict__")
    assert_secret_free(repr(value))
    with pytest.raises((FrozenInstanceError, AttributeError)):
        value.api_key = "replacement"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status", [301, 302, 307, 308, 400, 401, 403, 404, 429, 500, 503]
)
async def test_http_failure_has_no_retry_redirect_or_secret_error(monkeypatch, status):
    harness = Harness(
        monkeypatch,
        status=status,
        response_headers={"Location": "https://unexpected.invalid/" + API_KEY},
        change=lambda *args: wire([], msg=API_SECRET + PASSPHRASE),
    )
    with pytest.raises(module.AccountCollectionError) as error:
        await harness.collect()
    assert len(harness.requests) == 1
    assert_secret_free(str(error.value))
    assert_secret_free("".join(traceback.format_exception(error.value)))
    harness.assert_closed()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exception",
    [
        httpx.ConnectTimeout,
        httpx.ReadTimeout,
        httpx.ConnectError,
        httpx.RemoteProtocolError,
    ],
)
async def test_transport_error_is_redacted_no_retry_and_client_closes(
    monkeypatch, exception
):
    harness = Harness(monkeypatch, handler_error=exception(API_SECRET))
    with pytest.raises(module.AccountCollectionError) as error:
        await harness.collect()
    assert len(harness.requests) == 1
    assert_secret_free(str(error.value))
    assert_secret_free("".join(traceback.format_exception(error.value)))
    harness.assert_closed()


@pytest.mark.asyncio
@pytest.mark.parametrize("where", ["headers", "body"])
async def test_cancellation_propagates_without_retry_and_closes_owned_resources(
    monkeypatch, where
):
    cancellation = asyncio.CancelledError(API_SECRET)
    harness = Harness(
        monkeypatch,
        handler_error=cancellation if where == "headers" else None,
        stream_error=cancellation if where == "body" else None,
    )
    with pytest.raises(asyncio.CancelledError) as error:
        await harness.collect()
    assert error.value.args == ()
    assert error.value.__cause__ is None and error.value.__context__ is None
    assert_secret_free("".join(traceback.format_exception(error.value)))
    assert len(harness.requests) == 1
    harness.assert_closed()


@pytest.mark.asyncio
async def test_body_read_failure_closes_response_and_suppresses_secret_context(
    monkeypatch,
):
    harness = Harness(monkeypatch, stream_error=httpx.ReadError(PASSPHRASE))
    with pytest.raises(module.AccountCollectionError) as error:
        await harness.collect()
    assert len(harness.requests) == 1
    assert_secret_free("".join(traceback.format_exception(error.value)))
    harness.assert_closed()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stream,changes",
    [
        ("config_before", {"uid": "700002"}),
        ("config_before", {"mainUid": "700003"}),
        ("config_before", {"acctLv": "unknown"}),
        ("config_before", {"posMode": "unknown"}),
        ("config_after", {"uid": "700002"}),
        ("config_after", {"mainUid": "700003"}),
        ("config_after", {"acctLv": "3"}),
        ("config_after", {"posMode": "long_short_mode"}),
        ("balance", {"uid": "700002"}),
        ("balance", {"details": [{"ccy": "USDT", "uid": "700002"}]}),
    ],
)
async def test_config_and_nested_response_identity_changes_fail_closed(
    monkeypatch, stream, changes
):
    harness = Harness(
        monkeypatch,
        change=lambda current, index, data: (
            [row(stream, **changes)] if current == stream else None
        ),
    )
    with pytest.raises(module.AccountCollectionError):
        await harness.collect()
    assert harness.script[len(harness.requests) - 1][0] == stream
    harness.assert_closed()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        b"",
        b"not-json",
        b'{"code":"0","code":"0","data":[]}',
        b'{"code":"0","data":NaN}',
        b'{"code":0,"data":[]}',
        b'{"code":"0","data":[true]}',
    ],
)
async def test_malformed_wire_is_not_normalized_or_retried(monkeypatch, body):
    harness = Harness(monkeypatch, change=lambda *args: body)
    with pytest.raises(module.AccountCollectionError):
        await harness.collect()
    assert len(harness.requests) == 1
    harness.assert_closed()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field", ["apiKey", "apiSecret", "passphrase", "OK-ACCESS-SIGN"]
)
async def test_response_secret_fields_cannot_enter_packet_or_error(monkeypatch, field):
    harness = Harness(
        monkeypatch,
        change=lambda stream, index, data: [row(stream, **{field: API_SECRET})],
    )
    with pytest.raises(module.AccountCollectionError) as error:
        await harness.collect()
    assert len(harness.requests) == 1
    assert_secret_free(str(error.value))
    harness.assert_closed()


class Opaque:
    def __getattribute__(self, name):
        FORBIDDEN_CALLS.append("opaque attribute")
        raise AssertionError("opaque attribute executed")

    def __str__(self):
        FORBIDDEN_CALLS.append("opaque string")
        raise AssertionError("opaque string executed")

    def __repr__(self):
        FORBIDDEN_CALLS.append("opaque repr")
        raise AssertionError("opaque repr executed")

    def __bool__(self):
        FORBIDDEN_CALLS.append("opaque truthiness")
        raise AssertionError("opaque truthiness executed")

    def __hash__(self):
        FORBIDDEN_CALLS.append("opaque hash")
        raise AssertionError("opaque hash executed")


def forbid_clock():
    FORBIDDEN_CALLS.append("invalid input reached clock")
    raise AssertionError("invalid input reached clock")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field",
    ["api_key", "api_secret", "passphrase", "session_binding_id", "environment"],
)
@pytest.mark.parametrize(
    "bad", [None, True, 1, b"not-text", "", pytest.param(Opaque(), id="opaque")]
)
async def test_invalid_credentials_rejected_before_clock_factory_or_io(
    monkeypatch, field, bad
):
    harness = Harness(monkeypatch)
    with pytest.raises(module.AccountCollectionError):
        await harness.collect(supplied=credentials(**{field: bad}), clock=forbid_clock)
    assert harness.factory_calls == 0 and harness.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,bad",
    [
        ("environment", "live"),
        ("session_binding_id", "different-session"),
        ("api_key", "key\r\ninjected: yes"),
        ("api_secret", "x" * 8193),
        ("passphrase", "pass\nphrase"),
    ],
)
async def test_credential_scope_and_header_safety_before_signer_or_io(
    monkeypatch, field, bad
):
    harness = Harness(monkeypatch)
    with pytest.raises(module.AccountCollectionError):
        await harness.collect(supplied=credentials(**{field: bad}), clock=forbid_clock)
    assert harness.factory_calls == 0 and harness.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field",
    ["api_key", "api_secret", "passphrase", "session_binding_id", "environment"],
)
async def test_bypassed_frozen_credentials_still_prechecked_without_scalar_callbacks(
    monkeypatch, field
):
    harness = Harness(monkeypatch)
    supplied = credentials()
    object.__setattr__(supplied, field, Opaque())
    with pytest.raises(module.AccountCollectionError):
        await harness.collect(supplied=supplied, clock=forbid_clock)
    assert harness.factory_calls == 0 and harness.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,bad",
    [
        ("page_size", True),
        ("max_total_rows", 1.0),
        pytest.param("max_response_bytes", Opaque(), id="opaque_byte_budget"),
        pytest.param("session_binding_id", Opaque(), id="opaque_session"),
        ("environment", "live"),
        ("created_at", NOW.replace(tzinfo=None)),
    ],
)
async def test_copied_invalid_plan_is_rejected_before_clock_or_serializer(
    monkeypatch, field, bad
):
    harness = Harness(monkeypatch)
    selected = plan().model_copy(update={field: bad})
    with pytest.raises(module.AccountCollectionError):
        await module.collect_demo_account_records(
            credentials=credentials(),
            clock=forbid_clock,
            plan=selected,
            expected_plan_sha256=capture_api.plan_sha256(plan()),
            barrier_completed_at=BARRIER,
        )
    assert harness.factory_calls == 0 and harness.requests == []


@pytest.mark.asyncio
async def test_plan_subclass_serializer_never_runs(monkeypatch):
    class PlanTrap(capture_api.DemoAccountCapturePlan):
        @model_serializer
        def forbidden(self):
            FORBIDDEN_CALLS.append("plan serializer")
            raise AssertionError("plan serializer executed")

    harness = Harness(monkeypatch)
    selected = PlanTrap.model_construct(**plan().__dict__)
    with pytest.raises(module.AccountCollectionError):
        await module.collect_demo_account_records(
            credentials=credentials(),
            clock=forbid_clock,
            plan=selected,
            expected_plan_sha256=capture_api.plan_sha256(plan()),
            barrier_completed_at=BARRIER,
        )
    assert harness.factory_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad", [None, True, b"0" * 64, "0" * 64, pytest.param(Opaque(), id="opaque")]
)
async def test_external_plan_pin_is_required_before_io(monkeypatch, bad):
    harness = Harness(monkeypatch)
    with pytest.raises(module.AccountCollectionError):
        await harness.collect(clock=forbid_clock, expected_plan_sha256=bad)
    assert harness.factory_calls == 0 and harness.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad", [None, True, NOW.replace(tzinfo=None), pytest.param(Opaque(), id="opaque")]
)
async def test_barrier_requires_exact_aware_datetime_before_io(monkeypatch, bad):
    harness = Harness(monkeypatch)
    with pytest.raises(module.AccountCollectionError):
        await harness.collect(clock=forbid_clock, barrier_completed_at=bad)
    assert harness.factory_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("index", [0, 1, 2, 3, 4, 63, 64])
@pytest.mark.parametrize(
    "bad",
    [None, True, 1.0, NOW.replace(tzinfo=None), pytest.param(Opaque(), id="opaque")],
)
async def test_every_clock_boundary_is_strict_and_cleanup_is_preserved(
    monkeypatch, index, bad
):
    harness = Harness(monkeypatch)
    clock = Clock({index: bad})
    with pytest.raises(module.AccountCollectionError):
        await harness.collect(clock=clock)
    assert clock.calls == index + 1
    harness.assert_closed()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "index,bad", [(0, BARRIER), (1, BARRIER), (2, NOW), (3, NOW), (4, NOW), (64, NOW)]
)
async def test_preflight_per_request_and_final_clock_cannot_reverse(
    monkeypatch, index, bad
):
    harness = Harness(monkeypatch)
    with pytest.raises(module.AccountCollectionError):
        await harness.collect(clock=Clock({index: bad}))
    harness.assert_closed()


@pytest.mark.asyncio
@pytest.mark.parametrize("index", [2, 3])
async def test_request_deadline_rejects_late_headers_or_body(monkeypatch, index):
    harness = Harness(monkeypatch)
    with pytest.raises(module.AccountCollectionError):
        await harness.collect(
            clock=Clock({index: NOW + timedelta(seconds=6)}),
            selected=plan(max_request_seconds=5),
        )
    assert len(harness.requests) == 1
    harness.assert_closed()


@pytest.mark.asyncio
@pytest.mark.parametrize("index", [4, 64])
async def test_batch_deadline_includes_next_request_and_final_client_close_clock(
    monkeypatch, index
):
    harness = Harness(monkeypatch)
    with pytest.raises(module.AccountCollectionError):
        await harness.collect(
            clock=Clock({index: NOW + timedelta(seconds=2)}),
            selected=plan(max_batch_seconds=1),
        )
    assert len(harness.requests) == (1 if index == 4 else 21)
    harness.assert_closed()


@pytest.mark.asyncio
async def test_nonempty_page_at_stream_budget_never_issues_extra_terminal_get(
    monkeypatch,
):
    harness = Harness(monkeypatch)
    with pytest.raises(module.AccountCollectionError):
        await harness.collect(selected=plan(max_pages_per_stream=1))
    assert len(harness.requests) == 5
    harness.assert_closed()


@pytest.mark.asyncio
async def test_total_page_budget_stops_before_next_request(monkeypatch):
    harness = Harness(monkeypatch)
    with pytest.raises(module.AccountCollectionError):
        await harness.collect(selected=plan(max_total_pages=13))
    assert len(harness.requests) == 13
    harness.assert_closed()


@pytest.mark.asyncio
async def test_top_level_row_budget_checked_incrementally(monkeypatch):
    harness = Harness(monkeypatch)
    with pytest.raises(module.AccountCollectionError):
        await harness.collect(selected=plan(max_total_rows=1))
    assert len(harness.requests) == 2
    harness.assert_closed()


@pytest.mark.asyncio
async def test_total_byte_budget_checked_before_another_request(monkeypatch):
    harness = Harness(monkeypatch)
    with pytest.raises(module.AccountCollectionError):
        await harness.collect(selected=plan(max_total_bytes=1024))
    sizes = [len(s.body) for s in harness.streams]
    assert sum(sizes[:-1]) <= 1024 < sum(sizes)
    assert len(harness.requests) < len(harness.script)
    harness.assert_closed()


@pytest.mark.asyncio
async def test_content_length_limit_rejected_before_body_consumption(monkeypatch):
    harness = Harness(monkeypatch, response_headers={"Content-Length": "65537"})
    with pytest.raises(module.AccountCollectionError):
        await harness.collect()
    assert len(harness.requests) == 1 and harness.streams[0].yield_count == 0
    harness.assert_closed()


@pytest.mark.asyncio
async def test_streaming_bytes_limit_stops_before_reading_remaining_chunks(monkeypatch):
    harness = Harness(monkeypatch, chunks=(b" " * 65537, b"never consume this"))
    with pytest.raises(module.AccountCollectionError):
        await harness.collect()
    assert len(harness.requests) == 1 and harness.streams[0].yield_count == 1
    harness.assert_closed()


@pytest.mark.asyncio
@pytest.mark.parametrize("ids", [("900", "900"), ("900", "901")])
async def test_cursor_duplicate_or_nonprogressing_page_rejected_before_next_get(
    monkeypatch, ids
):
    harness = Harness(
        monkeypatch,
        pages={
            "orders_pending": [
                [row("orders_pending", ids[0])],
                [row("orders_pending", ids[1])],
                [],
            ]
        },
    )
    with pytest.raises(module.AccountCollectionError):
        await harness.collect()
    assert len(harness.requests) == 6
    harness.assert_closed()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "options",
    [
        {"trust_env": True},
        {"follow_redirects": True},
        {"auth": ("synthetic-user", "synthetic-password")},
        {"headers": {"x-default-private": API_SECRET}},
        {"cookies": {"session": API_SECRET}},
        {"params": {"instId": "ETH-USDT-SWAP"}},
    ],
)
async def test_unsafe_owned_client_defaults_fail_before_any_request(
    monkeypatch, options
):
    harness = Harness(monkeypatch, client_options=options)
    with pytest.raises(module.AccountCollectionError):
        await harness.collect()
    assert harness.factory_calls == 1 and harness.requests == []
    harness.assert_closed()


@pytest.mark.asyncio
async def test_http_hooks_are_rejected_without_invoking_them(monkeypatch):
    async def hook(request):
        FORBIDDEN_CALLS.append("HTTP hook")
        raise AssertionError("HTTP hook executed")

    harness = Harness(monkeypatch, client_options={"event_hooks": {"request": [hook]}})
    with pytest.raises(module.AccountCollectionError):
        await harness.collect()
    assert harness.requests == []
    harness.assert_closed()


@pytest.mark.asyncio
async def test_custom_timezone_callback_not_executed_before_io(monkeypatch):
    class HostileTimezone(tzinfo):
        def utcoffset(self, dt):
            FORBIDDEN_CALLS.append("timezone callback")
            raise AssertionError("timezone callback executed")

    harness = Harness(monkeypatch)
    bad = datetime(2026, 9, 12, 10, tzinfo=HostileTimezone())
    with pytest.raises(module.AccountCollectionError):
        await harness.collect(clock=Clock({0: bad}))
    assert harness.factory_calls == 0 and harness.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("slot", ["__pydantic_extra__", "__pydantic_private__"])
async def test_hidden_plan_metadata_is_rejected_without_truthiness_or_serializer(
    monkeypatch, slot
):
    harness = Harness(monkeypatch)
    selected = plan()
    object.__setattr__(selected, slot, Opaque())
    with pytest.raises(module.AccountCollectionError):
        await module.collect_demo_account_records(
            credentials=credentials(),
            clock=forbid_clock,
            plan=selected,
            expected_plan_sha256=capture_api.plan_sha256(plan()),
            barrier_completed_at=BARRIER,
        )
    assert harness.factory_calls == 0


@pytest.mark.asyncio
async def test_custom_scalar_metaclass_hash_and_equality_are_never_called(monkeypatch):
    class Meta(type):
        def __hash__(cls):
            FORBIDDEN_CALLS.append("metaclass hash")
            raise AssertionError("metaclass hash executed")

        def __eq__(cls, other):
            FORBIDDEN_CALLS.append("metaclass equality")
            raise AssertionError("metaclass equality executed")

    class Scalar(metaclass=Meta):
        pass

    harness = Harness(monkeypatch)
    supplied = credentials()
    object.__setattr__(supplied, "api_secret", Scalar())
    with pytest.raises(module.AccountCollectionError):
        await harness.collect(supplied=supplied, clock=forbid_clock)
    assert harness.factory_calls == 0


@pytest.mark.asyncio
async def test_credential_subclass_is_rejected_before_attribute_access(monkeypatch):
    class CredentialsTrap(module.DemoAccountCredentials):
        def __getattribute__(self, name):
            FORBIDDEN_CALLS.append("credential subclass access")
            raise AssertionError("credential subclass executed")

    supplied = object.__new__(CredentialsTrap)
    harness = Harness(monkeypatch)
    with pytest.raises(module.AccountCollectionError):
        await harness.collect(supplied=supplied, clock=forbid_clock)
    assert harness.factory_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["api_key", "api_secret", "passphrase"])
@pytest.mark.parametrize(
    "bad",
    [
        "1234567",
        "x" * 257,
        "synthetic key",
        "synthetic\tkey",
        "synthetic\x00key",
        "synthetic密鑰",
    ],
)
async def test_exact_ascii_credential_bounds_are_enforced_before_io(
    monkeypatch, field, bad
):
    harness = Harness(monkeypatch)
    with pytest.raises(module.AccountCollectionError):
        await harness.collect(supplied=credentials(**{field: bad}), clock=forbid_clock)
    assert harness.factory_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("length", [8, 256])
async def test_credential_length_endpoints_work_with_same_packet_semantics(
    monkeypatch, length
):
    harness = Harness(monkeypatch)
    supplied = credentials(
        api_key="k" * length, api_secret="s" * length, passphrase="p" * length
    )
    result = await harness.collect(supplied=supplied)
    assert result.account_complete is False
    assert harness.requests[0].headers["OK-ACCESS-KEY"] == "k" * length
    assert all(
        token not in result.model_dump_json()
        for token in ("k" * length, "s" * length, "p" * length)
    )
    harness.assert_closed()


@pytest.mark.asyncio
async def test_credentials_are_snapshotted_before_transport_can_mutate_original(
    monkeypatch,
):
    supplied = credentials()

    def mutate_original(stream, index, data):
        object.__setattr__(supplied, "api_key", "mutated-synthetic-key")
        object.__setattr__(supplied, "api_secret", "mutated-synthetic-secret")
        object.__setattr__(supplied, "passphrase", "mutated-synthetic-passphrase")
        object.__setattr__(supplied, "session_binding_id", "different-session")

    harness = Harness(monkeypatch, change=mutate_original)
    result = await harness.collect(supplied=supplied)
    for request in harness.requests:
        assert request.headers["OK-ACCESS-KEY"] == API_KEY
        assert request.headers["OK-ACCESS-PASSPHRASE"] == PASSPHRASE
        prehash = (
            request.headers["OK-ACCESS-TIMESTAMP"].encode()
            + b"GET"
            + request.url.raw_path
        )
        signature = base64.b64encode(
            hmac.new(API_SECRET.encode(), prehash, hashlib.sha256).digest()
        ).decode()
        assert request.headers["OK-ACCESS-SIGN"] == signature
    assert result.plan.session_binding_id == "synthetic-private-session"
    harness.assert_closed()


@pytest.mark.asyncio
@pytest.mark.parametrize("secret", SECRETS)
async def test_secret_echo_in_plan_is_rejected_before_clock_or_signer(
    monkeypatch, secret
):
    harness = Harness(monkeypatch)
    with pytest.raises(module.AccountCollectionError):
        await harness.collect(selected=plan(plan_id=secret), clock=forbid_clock)
    assert harness.factory_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("escaped", [False, True])
async def test_known_secret_echo_in_unknown_raw_or_canonical_field_is_rejected(
    monkeypatch, escaped
):
    def echo(stream, index, data):
        body = wire([row(stream, note=API_SECRET)])
        if escaped:
            body = body.replace(
                API_SECRET.encode(), b"\\u0073" + API_SECRET[1:].encode()
            )
        return body

    harness = Harness(monkeypatch, change=echo)
    with pytest.raises(module.AccountCollectionError) as error:
        await harness.collect()
    assert str(error.value) == "credential_echo_rejected"
    assert_secret_free("".join(traceback.format_exception(error.value)))
    assert len(harness.requests) == 1
    harness.assert_closed()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exception", [ValueError, RuntimeError, module.AccountCollectionError]
)
@pytest.mark.parametrize("at", [0, 2, 64])
async def test_clock_exception_cannot_leak_secrets_in_message_or_exception_chain(
    monkeypatch, exception, at
):
    harness = Harness(monkeypatch)
    clock = Clock()

    def explode():
        if clock.calls == at:
            raise exception(API_KEY + API_SECRET + PASSPHRASE)
        return clock()

    with pytest.raises(module.AccountCollectionError) as error:
        await harness.collect(clock=explode)
    assert_secret_free("".join(traceback.format_exception(error.value)))
    assert error.value.__cause__ is None and error.value.__context__ is None
    harness.assert_closed()


@pytest.mark.asyncio
async def test_response_cookie_is_never_copied_into_later_signed_requests(monkeypatch):
    harness = Harness(
        monkeypatch,
        response_headers={
            "Set-Cookie": "private_session=" + API_KEY + "; Secure; Path=/"
        },
    )
    result = await harness.collect()
    assert all("cookie" not in request.headers for request in harness.requests)
    assert_secret_free(result.model_dump_json())
    harness.assert_closed()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "headers",
    [
        {"Content-Type": "text/plain"},
        {"Content-Type": ""},
        {"Content-Encoding": "gzip"},
        {"Content-Length": "-1"},
        {"Content-Length": "1.0"},
        {"Content-Length": "1"},
    ],
)
async def test_media_encoding_and_content_length_are_fail_closed(monkeypatch, headers):
    harness = Harness(monkeypatch, response_headers=headers)
    with pytest.raises(module.AccountCollectionError):
        await harness.collect()
    assert len(harness.requests) == 1
    harness.assert_closed()


@pytest.mark.asyncio
async def test_unknown_values_and_missing_source_clocks_stay_unknown(monkeypatch):
    def missing(stream, index, data):
        value = row(stream)
        if stream == "balance":
            value.pop("uTime")
        elif stream == "positions":
            value.pop("pos")
            value.pop("uTime")
        return [value] if stream in {"balance", "positions"} else None

    harness = Harness(monkeypatch, change=missing)
    result = await harness.collect()
    position = next(
        o for o in result.observations if o.request.stream == "positions"
    ).rows[0]
    missing_number = next(n for n in position.numbers if n.path == "pos")
    assert missing_number.raw is None and missing_number.value is None
    missing_time = next(t for t in position.source_times if t.path == "uTime")
    assert missing_time.raw is None and missing_time.value is None
    assert result.account_complete is False
    harness.assert_closed()


@pytest.mark.asyncio
async def test_cross_kind_algo_identity_collision_stops_inventory(monkeypatch):
    harness = Harness(
        monkeypatch,
        change=lambda stream, index, data: (
            [row(stream, "910")] if stream == "algo_oco" and data else None
        ),
    )
    with pytest.raises(module.AccountCollectionError):
        await harness.collect()
    assert len(harness.requests) == 9
    harness.assert_closed()


@pytest.mark.asyncio
async def test_page_size_is_a_hard_wire_row_bound(monkeypatch):
    harness = Harness(
        monkeypatch,
        pages={
            "orders_pending": [
                [row("orders_pending", "900"), row("orders_pending", "800")],
                [],
            ]
        },
    )
    with pytest.raises(module.AccountCollectionError):
        await harness.collect(selected=plan(page_size=1))
    assert len(harness.requests) == 5
    harness.assert_closed()


@pytest.mark.asyncio
async def test_valid_non_utc_clock_is_normalized_without_changing_source_times(
    monkeypatch,
):
    harness = Harness(monkeypatch)
    local = timezone(timedelta(hours=8))
    clock = Clock(
        {n: (NOW + timedelta(milliseconds=n + 1)).astimezone(local) for n in range(65)}
    )
    result = await harness.collect(clock=clock)
    assert result.observations[0].request_started_at == NOW + timedelta(milliseconds=2)
    assert result.observations[0].request_started_at.utcoffset() == timedelta(0)
    harness.assert_closed()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad", [None, 42, Opaque], ids=["none", "integer", "opaque_instance"]
)
async def test_noncallable_clock_is_rejected_without_factory(monkeypatch, bad):
    harness = Harness(monkeypatch)
    with pytest.raises(module.AccountCollectionError):
        await module.collect_demo_account_records(
            credentials=credentials(),
            clock=Opaque() if bad is Opaque else bad,
            plan=plan(),
            expected_plan_sha256=capture_api.plan_sha256(plan()),
            barrier_completed_at=BARRIER,
        )
    assert harness.factory_calls == 0


def test_public_api_has_no_client_signer_url_transport_or_execution_override():
    parameters = inspect.signature(module.collect_demo_account_records).parameters
    assert tuple(parameters) == (
        "credentials",
        "clock",
        "plan",
        "expected_plan_sha256",
        "barrier_completed_at",
    )
    assert all(
        value.kind is inspect.Parameter.KEYWORD_ONLY for value in parameters.values()
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "unsafe", ["verify_false", "hostname_false", "retry", "proxy", "mount"]
)
async def test_real_transport_configuration_rejected_before_send_with_no_network(
    monkeypatch, unsafe
):
    # Construct transport configuration only. send is forbidden independently,
    # so a failed guard cannot perform DNS, a socket connect, or private IO.
    send_calls = []

    async def no_send(*args, **kwargs):
        send_calls.append(True)
        raise AssertionError("unsafe transport reached send")

    monkeypatch.setattr(httpx.AsyncClient, "send", no_send)
    options = {"trust_env": False, "retries": 0}
    if unsafe == "verify_false":
        options["verify"] = False
    elif unsafe == "hostname_false":
        context = ssl.create_default_context()
        context.check_hostname = False
        options["verify"] = context
    elif unsafe == "retry":
        options["retries"] = 1
    elif unsafe == "proxy":
        options["proxy"] = "http://synthetic-proxy.invalid:8080"
    transport = httpx.AsyncHTTPTransport(**options)
    client_options = {"transport": transport, "trust_env": False}
    if unsafe == "mount":
        client_options["mounts"] = {
            "https://www.okx.com": httpx.MockTransport(lambda request: None)
        }
    client = httpx.AsyncClient(**client_options)
    monkeypatch.setattr(module, "_new_client", lambda: client)
    with pytest.raises(module.AccountCollectionError):
        await module.collect_demo_account_records(
            credentials=credentials(),
            clock=Clock(),
            plan=plan(),
            expected_plan_sha256=capture_api.plan_sha256(plan()),
            barrier_completed_at=BARRIER,
        )
    assert client.is_closed and send_calls == []


@pytest.mark.asyncio
async def test_foreign_factory_object_is_rejected_without_cleanup_callback(monkeypatch):
    monkeypatch.setattr(module, "_new_client", lambda: Opaque())
    with pytest.raises(module.AccountCollectionError):
        await module.collect_demo_account_records(
            credentials=credentials(),
            clock=Clock(),
            plan=plan(),
            expected_plan_sha256=capture_api.plan_sha256(plan()),
            barrier_completed_at=BARRIER,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "secret", ['synthetic-"-passphrase', "synthetic-\\-passphrase"]
)
async def test_legal_quote_and_backslash_secret_can_authenticate_without_echo(
    monkeypatch, secret
):
    harness = Harness(monkeypatch)
    result = await harness.collect(supplied=credentials(passphrase=secret))
    assert harness.requests[0].headers["OK-ACCESS-PASSPHRASE"] == secret
    assert result.account_complete is False
    harness.assert_closed()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "secret", ['synthetic-"-passphrase', "synthetic-\\-passphrase"]
)
async def test_json_escaped_credential_echo_is_rejected_after_decoding(
    monkeypatch, secret
):
    harness = Harness(
        monkeypatch, change=lambda stream, index, data: [row(stream, label=secret)]
    )
    with pytest.raises(module.AccountCollectionError) as error:
        await harness.collect(supplied=credentials(passphrase=secret))
    assert secret not in str(error.value)
    assert str(error.value) == "credential_echo_rejected"
    assert len(harness.requests) == 1
    harness.assert_closed()


@pytest.mark.asyncio
async def test_later_page_cannot_echo_earlier_request_signature_in_payload(monkeypatch):
    def prior_signature(stream, index, data):
        if stream == "balance":
            signature = harness.requests[0].headers["OK-ACCESS-SIGN"]
            return [row(stream, label=signature)]

    harness = Harness(monkeypatch, change=prior_signature)
    with pytest.raises(module.AccountCollectionError) as error:
        await harness.collect()
    assert str(error.value) == "credential_echo_rejected"
    assert harness.requests[0].headers["OK-ACCESS-SIGN"] not in str(error.value)
    harness.assert_closed()


@pytest.mark.asyncio
async def test_full_batch_rescan_rejects_prior_body_containing_later_signature(
    monkeypatch,
):
    timestamp = (
        (NOW + timedelta(milliseconds=8))
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )
    prehash = timestamp.encode() + b"GET/api/v5/account/balance"
    later_signature = base64.b64encode(
        hmac.new(API_SECRET.encode(), prehash, hashlib.sha256).digest()
    ).decode()
    harness = Harness(
        monkeypatch,
        change=lambda stream, index, data: (
            [row(stream, label=later_signature)] if index == 0 else None
        ),
    )
    with pytest.raises(module.AccountCollectionError) as error:
        await harness.collect()
    assert len(harness.requests) >= 3
    assert harness.requests[2].headers["OK-ACCESS-SIGN"] == later_signature
    assert later_signature not in str(error.value)
    assert str(error.value) == "credential_echo_rejected"
    harness.assert_closed()


@pytest.mark.asyncio
async def test_response_close_failure_is_sanitized_and_client_still_closes(monkeypatch):
    original = Stream.aclose

    async def failing_close(stream):
        await original(stream)
        raise RuntimeError(API_SECRET)

    monkeypatch.setattr(Stream, "aclose", failing_close)
    harness = Harness(monkeypatch)
    with pytest.raises(module.AccountCollectionError) as error:
        await harness.collect()
    assert_secret_free("".join(traceback.format_exception(error.value)))
    assert str(error.value) == "cleanup_failed"
    assert len(harness.requests) == 1
    harness.assert_closed()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["exception", "timeout"])
async def test_client_close_failure_or_timeout_cannot_return_packet(monkeypatch, mode):
    close_calls = []
    monkeypatch.setattr(module, "CLEANUP_SECONDS", 0.01)

    def configure(client):
        original = client.aclose

        async def failing_close():
            close_calls.append(True)
            await original()
            if mode == "timeout":
                await asyncio.Event().wait()
            raise RuntimeError(PASSPHRASE)

        client.aclose = failing_close

    harness = Harness(monkeypatch, configure_client=configure)
    with pytest.raises(module.AccountCollectionError) as error:
        await harness.collect()
    assert_secret_free("".join(traceback.format_exception(error.value)))
    assert str(error.value) == "cleanup_failed"
    assert len(harness.requests) == 21 and close_calls == [True]
    harness.assert_closed()


@pytest.mark.asyncio
async def test_cancellation_during_response_close_is_preserved_and_close_is_single(
    monkeypatch,
):
    entered = asyncio.Event()
    finish = asyncio.Event()
    original = Stream.aclose

    async def paused_close(stream):
        entered.set()
        await finish.wait()
        await original(stream)

    monkeypatch.setattr(Stream, "aclose", paused_close)
    harness = Harness(monkeypatch)
    task = asyncio.create_task(harness.collect())
    try:
        await asyncio.wait_for(entered.wait(), timeout=1)
        task.cancel()
        finish.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=1)
    finally:
        finish.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    assert len(harness.requests) == 1
    harness.assert_closed()


@pytest.mark.asyncio
async def test_cancellation_during_client_close_is_preserved_after_cleanup(monkeypatch):
    entered = asyncio.Event()
    finish = asyncio.Event()
    close_calls = []

    def configure(client):
        original = client.aclose

        async def paused_close():
            close_calls.append(True)
            entered.set()
            await finish.wait()
            await original()

        client.aclose = paused_close

    harness = Harness(monkeypatch, configure_client=configure)
    task = asyncio.create_task(harness.collect())
    try:
        await asyncio.wait_for(entered.wait(), timeout=1)
        task.cancel()
        finish.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=1)
    finally:
        finish.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    assert len(harness.requests) == 21 and close_calls == [True]
    harness.assert_closed()


@pytest.mark.asyncio
async def test_serialized_packet_budget_cannot_be_bypassed_by_small_raw_bodies(
    monkeypatch,
):
    harness = Harness(monkeypatch)
    selected = plan(max_response_bytes=1024, max_total_bytes=4096)
    monkeypatch.setattr(capture_api, "MAX_PACKET_BYTES", 8192)
    with pytest.raises(module.AccountCollectionError):
        await harness.collect(selected=selected)
    assert len(harness.requests) == 21
    assert (
        sum(len(stream.body) for stream in harness.streams) <= selected.max_total_bytes
    )
    assert all(
        len(stream.body) <= selected.max_response_bytes for stream in harness.streams
    )
    harness.assert_closed()


@pytest.mark.asyncio
@pytest.mark.parametrize("bound", ["max_batch_seconds", "max_request_seconds"])
async def test_synchronous_clock_delay_cannot_send_after_native_deadline(
    monkeypatch, bound
):
    harness = Harness(monkeypatch)
    clock = Clock()

    def delayed_clock():
        value = clock()
        if clock.calls == 2:
            # Valid UTC is sampled before a synchronous callback stalls. The
            # native bound must be checked before send, not at a later await.
            time.sleep(1.05)
        return value

    with pytest.raises(module.AccountCollectionError):
        await harness.collect(selected=plan(**{bound: 1}), clock=delayed_clock)
    assert clock.calls == 2
    assert harness.factory_calls == 1 and harness.requests == []
    harness.assert_closed()


def assert_diagnostic(error, *, stage, stream=None, page_index=None, reason):
    assert type(error) is module.AccountCollectionError
    assert error.args == ("account_records_invalid",)
    assert error.__cause__ is None and error.__context__ is None
    diagnostic = error.diagnostic
    assert type(diagnostic) is module.AccountCollectionDiagnostic
    assert diagnostic.stage == stage
    assert diagnostic.stream == stream
    assert diagnostic.page_index == page_index
    assert diagnostic.capture_reason == reason
    assert tuple(field.name for field in fields(diagnostic)) == (
        "stage",
        "stream",
        "page_index",
        "capture_reason",
    )
    assert not hasattr(diagnostic, "__dict__")
    assert_secret_free(repr(diagnostic))
    assert_secret_free(repr(error))
    assert "700001" not in repr(diagnostic) and "700000" not in repr(diagnostic)
    return diagnostic


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case,reason",
    [
        ("uid", "account_identity_mismatch"),
        ("main_uid", "account_identity_mismatch"),
        ("mode", "account_mode_unsupported"),
        ("code", "response_envelope_invalid"),
        ("extra_envelope", "response_envelope_fields_invalid"),
        ("json_number", "json_number_invalid"),
        ("json_float", "json_number_invalid"),
        ("secret_field", "secret_field_forbidden"),
        ("duplicate_json_key", "duplicate_json_key"),
        ("malformed_json", "response_json_invalid"),
    ],
)
async def test_real_parser_failure_has_bounded_static_diagnostic(
    monkeypatch, case, reason
):
    value = row("config_before")
    if case == "uid":
        value["uid"] = "700002"
    elif case == "main_uid":
        value["mainUid"] = "700003"
    elif case == "mode":
        value["posMode"] = "unsupported"
    elif case == "json_number":
        value["customValue"] = 17
    elif case == "json_float":
        value["customValue"] = 1.5
    elif case == "secret_field":
        value["apiKey"] = "foreign-response-secret-marker"
    body = wire([value])
    if case == "code":
        body = wire([value], code="51000", msg="foreign-response-message-marker")
    elif case == "extra_envelope":
        body = wire([value], extra="foreign-response-extra-marker")
    elif case == "duplicate_json_key":
        body = b'{"code":"0","code":"0","msg":"","data":[]}'
    elif case == "malformed_json":
        body = b'{"code":"0",'
    harness = Harness(monkeypatch, change=lambda *args: body)
    with pytest.raises(module.AccountCollectionError) as error:
        await harness.collect()
    assert_diagnostic(
        error.value,
        stage="parse_observation",
        stream="config_before",
        page_index=0,
        reason=reason,
    )
    assert len(harness.requests) == 1
    assert "foreign-response" not in repr(error.value.diagnostic)
    assert "foreign-response" not in "".join(traceback.format_exception(error.value))
    harness.assert_closed()


@pytest.mark.asyncio
async def test_parser_diagnostic_identifies_later_stream_and_exact_page(monkeypatch):
    harness = Harness(
        monkeypatch,
        pages={
            "orders_pending": [
                [row("orders_pending", "900")],
                [row("orders_pending", "900")],
                [],
            ]
        },
    )
    with pytest.raises(module.AccountCollectionError) as error:
        await harness.collect()
    assert_diagnostic(
        error.value,
        stage="parse_observation",
        stream="orders_pending",
        page_index=1,
        reason="source_cursor_not_exclusive",
    )
    assert len(harness.requests) == 6
    harness.assert_closed()


@pytest.mark.asyncio
async def test_real_config_change_is_diagnosed_at_verify_not_last_parser(monkeypatch):
    harness = Harness(
        monkeypatch,
        change=lambda stream, index, data: (
            [row(stream, posMode="long_short_mode")]
            if stream == "config_after"
            else None
        ),
    )
    with pytest.raises(module.AccountCollectionError) as error:
        await harness.collect()
    assert_diagnostic(
        error.value, stage="verify_records", reason="account_mode_changed"
    )
    assert len(harness.requests) == 21
    harness.assert_closed()


@pytest.mark.asyncio
async def test_real_serialized_packet_limit_is_diagnosed_at_freeze(monkeypatch):
    harness = Harness(monkeypatch)
    selected = plan(max_response_bytes=1024, max_total_bytes=4096)
    monkeypatch.setattr(capture_api, "MAX_PACKET_BYTES", 8192)
    with pytest.raises(module.AccountCollectionError) as error:
        await harness.collect(selected=selected)
    assert_diagnostic(error.value, stage="freeze_packet", reason="packet_bytes_limit")
    assert len(harness.requests) == 21
    harness.assert_closed()


@pytest.mark.asyncio
async def test_diagnostic_and_error_diagnostic_property_are_immutable(monkeypatch):
    harness = Harness(monkeypatch, change=lambda *args: wire([], code="51000"))
    with pytest.raises(module.AccountCollectionError) as error:
        await harness.collect()
    diagnostic = assert_diagnostic(
        error.value,
        stage="parse_observation",
        stream="config_before",
        page_index=0,
        reason="response_envelope_invalid",
    )
    for field in ("stage", "stream", "page_index", "capture_reason"):
        with pytest.raises(FrozenInstanceError):
            setattr(diagnostic, field, "replacement")
    with pytest.raises((AttributeError, TypeError)):
        diagnostic.response_body = b"hidden"
    with pytest.raises(AttributeError):
        error.value.diagnostic = None
    assert error.value.diagnostic is diagnostic
    assert not hasattr(diagnostic, "response_body")
    harness.assert_closed()


DIAGNOSTIC_STAGES = (
    ("parse_demo_account_observation", "parse_observation", "config_before", 0),
    ("verify_demo_account_records", "verify_records", None, None),
    ("freeze_demo_account_packet", "freeze_packet", None, None),
)


@pytest.mark.asyncio
@pytest.mark.parametrize("function,stage,stream,page", DIAGNOSTIC_STAGES)
async def test_capture_diagnostic_is_attached_only_by_actual_api_boundary(
    monkeypatch, function, stage, stream, page
):
    harness = Harness(monkeypatch)
    calls = []

    def fail_at_boundary(*args, **kwargs):
        calls.append(True)
        raise capture_api.AccountCaptureError("record_type_invalid")

    monkeypatch.setattr(capture_api, function, fail_at_boundary)
    with pytest.raises(module.AccountCollectionError) as error:
        await harness.collect()
    assert_diagnostic(
        error.value,
        stage=stage,
        stream=stream,
        page_index=page,
        reason="record_type_invalid",
    )
    assert calls == [True]
    assert len(harness.requests) == (1 if stream else 21)
    harness.assert_closed()


@pytest.mark.asyncio
async def test_verify_failure_nested_inside_freeze_stays_freeze_stage(monkeypatch):
    harness = Harness(monkeypatch)
    original = capture_api.verify_demo_account_records
    calls = []

    def fail_only_freeze_replay(*args, **kwargs):
        calls.append(True)
        if len(calls) == 2:
            raise capture_api.AccountCaptureError("observation_replay_mismatch")
        return original(*args, **kwargs)

    monkeypatch.setattr(
        capture_api, "verify_demo_account_records", fail_only_freeze_replay
    )
    with pytest.raises(module.AccountCollectionError) as error:
        await harness.collect()
    assert_diagnostic(
        error.value, stage="freeze_packet", reason="observation_replay_mismatch"
    )
    assert calls == [True, True]
    harness.assert_closed()


def dirty_capture_error(case):
    if case == "empty_args":
        return capture_api.AccountCaptureError()
    if case == "multiple_args":
        return capture_api.AccountCaptureError("record_type_invalid", API_SECRET)
    if case == "secret_arg":
        return capture_api.AccountCaptureError(API_SECRET)
    if case == "unknown_arg":
        return capture_api.AccountCaptureError("unreviewed_remote_code")
    if case == "opaque_arg":
        return capture_api.AccountCaptureError(Opaque())
    if case == "list_arg":
        return capture_api.AccountCaptureError(["record_type_invalid"])
    if case == "bool_arg":
        return capture_api.AccountCaptureError(True)
    error = capture_api.AccountCaptureError("record_type_invalid")
    if case == "notes":
        error.add_note(API_SECRET)
    elif case == "opaque_notes":
        error.__dict__["__notes__"] = Opaque()
    elif case == "hidden_secret":
        error.__dict__["response_body"] = API_SECRET
    elif case == "hidden_opaque":
        error.__dict__["unsafe"] = Opaque()
    elif case == "hidden_opaque_key":
        # Exact dictionary with a non-text key must not be traversed or repr'd.
        error.__dict__[17] = Opaque()
    else:
        raise AssertionError("unknown synthetic dirty-error case")
    return error


@pytest.mark.asyncio
@pytest.mark.parametrize("function,stage,stream,page", DIAGNOSTIC_STAGES)
@pytest.mark.parametrize(
    "case",
    [
        "empty_args",
        "multiple_args",
        "secret_arg",
        "unknown_arg",
        "opaque_arg",
        "list_arg",
        "bool_arg",
        "notes",
        "opaque_notes",
        "hidden_secret",
        "hidden_opaque",
        "hidden_opaque_key",
    ],
)
async def test_exact_but_impure_capture_error_reason_becomes_unknown_without_callbacks(
    monkeypatch, function, stage, stream, page, case
):
    harness = Harness(monkeypatch)
    unsafe_error = dirty_capture_error(case)

    def fail_at_boundary(*args, **kwargs):
        raise unsafe_error

    monkeypatch.setattr(capture_api, function, fail_at_boundary)
    with pytest.raises(module.AccountCollectionError) as error:
        await harness.collect()
    assert_diagnostic(
        error.value, stage=stage, stream=stream, page_index=page, reason="unknown"
    )
    assert "unreviewed_remote_code" not in repr(error.value.diagnostic)
    assert "response_body" not in repr(error.value.diagnostic)
    assert_secret_free("".join(traceback.format_exception(error.value)))
    harness.assert_closed()


@pytest.mark.asyncio
@pytest.mark.parametrize("function,stage,stream,page", DIAGNOSTIC_STAGES)
async def test_capture_error_subclass_has_no_diagnostic_and_no_attribute_execution(
    monkeypatch, function, stage, stream, page
):
    class ErrorTrap(capture_api.AccountCaptureError):
        def __getattribute__(self, name):
            FORBIDDEN_CALLS.append("capture error subclass attribute")
            raise AssertionError("error subclass attribute executed")

        def __str__(self):
            FORBIDDEN_CALLS.append("capture error subclass string")
            raise AssertionError("error subclass string executed")

    unsafe_error = ErrorTrap("record_type_invalid")
    harness = Harness(monkeypatch)

    def fail_at_boundary(*args, **kwargs):
        raise unsafe_error

    monkeypatch.setattr(capture_api, function, fail_at_boundary)
    with pytest.raises(module.AccountCollectionError) as error:
        await harness.collect()
    assert error.value.args == ("account_records_invalid",)
    assert error.value.diagnostic is None
    assert error.value.__cause__ is None and error.value.__context__ is None
    harness.assert_closed()


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["api_key", "api_secret", "passphrase"])
async def test_allowlisted_reason_containing_real_secret_is_reduced_to_unknown(
    monkeypatch, field
):
    harness = Harness(monkeypatch)

    def fail_at_boundary(*args, **kwargs):
        raise capture_api.AccountCaptureError("account_identity_mismatch")

    monkeypatch.setattr(capture_api, "parse_demo_account_observation", fail_at_boundary)
    with pytest.raises(module.AccountCollectionError) as error:
        await harness.collect(supplied=credentials(**{field: "identity"}))
    assert_diagnostic(
        error.value,
        stage="parse_observation",
        stream="config_before",
        page_index=0,
        reason="unknown",
    )
    assert "identity" not in repr(error.value.diagnostic)
    harness.assert_closed()


@pytest.mark.asyncio
async def test_issued_signature_cannot_become_a_capture_reason(monkeypatch):
    harness = Harness(monkeypatch)

    def fail_at_boundary(*args, **kwargs):
        raise capture_api.AccountCaptureError(
            harness.requests[0].headers["OK-ACCESS-SIGN"]
        )

    monkeypatch.setattr(capture_api, "verify_demo_account_records", fail_at_boundary)
    with pytest.raises(module.AccountCollectionError) as error:
        await harness.collect()
    assert_diagnostic(error.value, stage="verify_records", reason="unknown")
    assert harness.requests[0].headers["OK-ACCESS-SIGN"] not in repr(
        error.value.diagnostic
    )
    harness.assert_closed()


@pytest.mark.asyncio
@pytest.mark.parametrize("at", [0, 2, 64])
async def test_capture_error_from_clock_has_no_parser_diagnostic(monkeypatch, at):
    harness = Harness(monkeypatch)
    clock = Clock()

    def external_clock():
        if clock.calls == at:
            raise capture_api.AccountCaptureError("account_identity_mismatch")
        return clock()

    with pytest.raises(module.AccountCollectionError) as error:
        await harness.collect(clock=external_clock)
    assert error.value.args == ("account_records_invalid",)
    assert error.value.diagnostic is None
    assert error.value.__cause__ is None and error.value.__context__ is None
    harness.assert_closed()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "source", ["http_status", "http_transport", "credentials", "secret_echo"]
)
async def test_non_capture_failures_do_not_claim_capture_diagnostic(
    monkeypatch, source
):
    options = {}
    if source == "http_status":
        options["status"] = 401
    elif source == "http_transport":
        options["handler_error"] = httpx.ReadError(API_SECRET)
    elif source == "secret_echo":
        options["change"] = lambda stream, index, data: [row(stream, label=API_SECRET)]
    harness = Harness(monkeypatch, **options)
    with pytest.raises(module.AccountCollectionError) as error:
        await harness.collect(
            supplied=credentials(session_binding_id="other-session")
            if source == "credentials"
            else credentials()
        )
    assert error.value.diagnostic is None
    assert error.value.__cause__ is None and error.value.__context__ is None
    harness.assert_closed()


@pytest.mark.asyncio
@pytest.mark.parametrize("function,stage,stream,page", DIAGNOSTIC_STAGES)
async def test_reason_string_subclass_is_not_hashed_compared_or_stringified(
    monkeypatch, function, stage, stream, page
):
    class StringTrap(str):
        def __eq__(self, other):
            FORBIDDEN_CALLS.append("reason string equality")
            raise AssertionError("string equality executed")

        def __hash__(self):
            FORBIDDEN_CALLS.append("reason string hash")
            raise AssertionError("string hash executed")

        def __str__(self):
            FORBIDDEN_CALLS.append("reason string conversion")
            raise AssertionError("string conversion executed")

    unsafe_error = capture_api.AccountCaptureError(StringTrap("record_type_invalid"))
    harness = Harness(monkeypatch)

    def fail_at_boundary(*args, **kwargs):
        raise unsafe_error

    monkeypatch.setattr(capture_api, function, fail_at_boundary)
    with pytest.raises(module.AccountCollectionError) as error:
        await harness.collect()
    assert_diagnostic(
        error.value, stage=stage, stream=stream, page_index=page, reason="unknown"
    )
    harness.assert_closed()


@pytest.mark.asyncio
@pytest.mark.parametrize("function,stage,stream,page", DIAGNOSTIC_STAGES)
async def test_exception_metadata_dictionary_subclass_is_never_evaluated(
    monkeypatch, function, stage, stream, page
):
    class DictTrap(dict):
        def __bool__(self):
            FORBIDDEN_CALLS.append("exception metadata truthiness")
            raise AssertionError("metadata truthiness executed")

        def __iter__(self):
            FORBIDDEN_CALLS.append("exception metadata iteration")
            raise AssertionError("metadata iteration executed")

    unsafe_error = capture_api.AccountCaptureError("record_type_invalid")
    unsafe_error.__dict__ = DictTrap()
    harness = Harness(monkeypatch)

    def fail_at_boundary(*args, **kwargs):
        raise unsafe_error

    monkeypatch.setattr(capture_api, function, fail_at_boundary)
    with pytest.raises(module.AccountCollectionError) as error:
        await harness.collect()
    assert_diagnostic(
        error.value, stage=stage, stream=stream, page_index=page, reason="unknown"
    )
    harness.assert_closed()


@pytest.mark.asyncio
async def test_cleanup_failure_does_not_keep_a_stale_parser_diagnostic(monkeypatch):
    def configure(client):
        original = client.aclose

        async def failing_close():
            await original()
            raise RuntimeError(API_SECRET)

        client.aclose = failing_close

    harness = Harness(
        monkeypatch,
        configure_client=configure,
        change=lambda *args: wire([], code="51000"),
    )
    with pytest.raises(module.AccountCollectionError) as error:
        await harness.collect()
    assert error.value.args == ("cleanup_failed",)
    assert error.value.diagnostic is None
    assert error.value.__cause__ is None and error.value.__context__ is None
    harness.assert_closed()


@pytest.mark.asyncio
async def test_clock_cannot_supply_its_own_diagnostic_via_collection_error(monkeypatch):
    harness = Harness(monkeypatch)
    external = module.AccountCollectionDiagnostic(
        stage="parse_observation",
        stream="config_before",
        page_index=0,
        capture_reason="account_identity_mismatch",
    )

    def untrusted_clock():
        raise module.AccountCollectionError(
            "account_records_invalid", diagnostic=external
        )

    with pytest.raises(module.AccountCollectionError) as error:
        await harness.collect(clock=untrusted_clock)
    assert error.value.args == ("account_records_invalid",)
    assert error.value.diagnostic is None
    assert error.value.__cause__ is None and error.value.__context__ is None
    assert harness.factory_calls == 0


@pytest.mark.asyncio
async def test_oversized_reason_is_rejected_by_schema_and_unknown_at_capture_boundary(
    monkeypatch,
):
    oversized = "untrusted-reason-" * 65536
    with pytest.raises(ValueError, match="^diagnostic_invalid$"):
        module.AccountCollectionDiagnostic(
            stage="parse_observation",
            stream="config_before",
            page_index=0,
            capture_reason=oversized,
        )
    harness = Harness(monkeypatch)

    def fail_at_boundary(*args, **kwargs):
        raise capture_api.AccountCaptureError(oversized)

    monkeypatch.setattr(capture_api, "parse_demo_account_observation", fail_at_boundary)
    with pytest.raises(module.AccountCollectionError) as error:
        await harness.collect()
    diagnostic = assert_diagnostic(
        error.value,
        stage="parse_observation",
        stream="config_before",
        page_index=0,
        reason="unknown",
    )
    assert len(repr(diagnostic)) < 256
    assert "untrusted-reason" not in repr(diagnostic)
    harness.assert_closed()
