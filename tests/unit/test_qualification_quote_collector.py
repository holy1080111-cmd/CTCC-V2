"""MockTransport-only public captures; no real quote or trading sample."""

import ast
import asyncio
import hashlib
import inspect
import json
from datetime import UTC, datetime, timedelta, timezone
from decimal import ROUND_UP, Context, Decimal, Inexact, localcontext

import httpx
import pytest
from pydantic import ValidationError

from app.trade_qualification import quote_collector as module
from app.trade_qualification.quote_collector import (
    BASE_URL,
    ENDPOINTS,
    CollectedQuote,
    QuoteCollectionError,
    QuoteCollectionPolicy,
    collect_executable_quote,
    validate_collected_quote,
)

D = Decimal
NOW = datetime(2026, 9, 12, 1, tzinfo=UTC)
INSTRUMENT = "BTC-USDT-SWAP"
REPORT = "synthetic-public-quote"
POLICY = QuoteCollectionPolicy(max_age_seconds=10)


def _ms(value):
    delta = value.astimezone(UTC) - datetime(1970, 1, 1, tzinfo=UTC)
    return str(
        delta.days * 86400000 + delta.seconds * 1000 + delta.microseconds // 1000
    )


class Clock:
    def __init__(self, moments=None):
        self.moments = moments or tuple(
            NOW + timedelta(milliseconds=i) for i in range(10)
        )
        self.calls = 0

    def __call__(self):
        value = self.moments[self.calls]
        self.calls += 1
        return value


class Stream(httpx.AsyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks
        self.yielded = 0
        self.closed = False

    async def __aiter__(self):
        for chunk in self.chunks:
            self.yielded += 1
            yield chunk

    async def aclose(self):
        self.closed = True


def payload(role):
    row = {
        "instType": "SWAP",
        "instId": INSTRUMENT,
        "ts": _ms(NOW - timedelta(seconds=1)),
    }
    row.update(
        {
            "ticker": {"bidPx": "100", "askPx": "100.01", "bidSz": "2", "askSz": "3"},
            "mark": {"markPx": "100.005"},
            "funding": {
                "fundingRate": "-0.000123",
                "fundingTime": _ms(NOW + timedelta(hours=1)),
                "nextFundingTime": _ms(NOW + timedelta(hours=9)),
            },
        }[role]
    )
    return {"code": "0", "msg": "", "data": [row]}


async def capture(
    *,
    change=None,
    clock=None,
    policy=POLICY,
    barrier=None,
    client_options=None,
    instrument=INSTRUMENT,
    report=REPORT,
):
    requests, streams = [], []

    def handler(request):
        requests.append(request)
        role = {path: role for role, path in ENDPOINTS}[request.url.path]
        body = payload(role)
        if change is not None:
            replacement = change(role, body)
            if replacement is not None:
                body = replacement
        raw = body if type(body) is bytes else json.dumps(body).encode()
        stream = Stream((raw[: len(raw) // 2], raw[len(raw) // 2 :]))
        streams.append(stream)
        return httpx.Response(
            200,
            stream=stream,
            headers={
                "content-type": "application/json; charset=utf-8",
                "content-length": str(len(raw)),
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        trust_env=False,
        **(client_options or {}),
    ) as client:
        result = await collect_executable_quote(
            client=client,
            clock=clock or Clock(),
            report_id=report,
            instrument_id=instrument,
            policy=policy,
            barrier_completed_at=barrier,
        )
        assert not client.is_closed  # injected client lifetime belongs to caller
    assert all(stream.closed for stream in streams)
    return result, requests


async def test_three_fixed_unauthenticated_gets_preserve_independent_source_and_capture_clocks():
    clock = Clock()
    result, requests = await capture(
        clock=clock, barrier=NOW - timedelta(milliseconds=1)
    )
    assert clock.calls == 10
    assert [(request.method, request.url.path) for request in requests] == [
        ("GET", path) for _, path in ENDPOINTS
    ]
    assert all(
        str(request.url).startswith(BASE_URL + "/api/v5/") for request in requests
    )
    for role, request in zip(("ticker", "mark", "funding"), requests, strict=True):
        assert dict(request.url.params) == (
            {"instId": INSTRUMENT, "instType": "SWAP"}
            if role == "mark"
            else {"instId": INSTRUMENT}
        )
        assert set(request.headers) == {
            "host",
            "accept",
            "accept-encoding",
            "user-agent",
        }
        assert request.headers["accept-encoding"] == "identity"
        assert request.content == b""
        assert all(value == 2 for value in request.extensions["timeout"].values())
    quote = result.quote
    assert quote.bid == D(100) and quote.ask == D("100.01")
    assert quote.mark_price == D("100.005")
    assert quote.bid_size == D(2) and quote.ask_size == D(3)
    assert result.size_unit == "contracts"
    assert quote.funding_rate == D("-0.000123")
    assert quote.funding_time == NOW - timedelta(seconds=1)
    assert quote.request_started_at == NOW
    assert quote.received_at == NOW + timedelta(milliseconds=8)
    assert result.completed_at == NOW + timedelta(milliseconds=9)
    for index, observation in enumerate(result.provenance):
        assert observation.origin == BASE_URL
        assert observation.parameters == tuple(
            sorted(requests[index].url.params.items())
        )
        assert observation.request_started_at == NOW + timedelta(milliseconds=index * 3)
        assert observation.received_at == NOW + timedelta(milliseconds=index * 3 + 1)
        assert observation.completed_at == NOW + timedelta(milliseconds=index * 3 + 2)
        assert (
            observation.source_time < observation.request_started_at
        )  # cache may still be fresh
        assert (
            observation.body_sha256
            == hashlib.sha256(observation.response_body).hexdigest()
        )
        assert (
            observation.canonical_sha256
            == hashlib.sha256(observation.canonical_json.encode()).hexdigest()
        )
    assert result.provenance[0].timestamp_semantics == "ticker_generation"
    assert {item.timestamp_semantics for item in result.provenance[1:]} == {
        "exchange_data_return"
    }
    assert result.execution_authority is result.source_authenticity_verified is False
    assert validate_collected_quote(result) == result
    assert (
        validate_collected_quote(
            CollectedQuote.model_validate_json(result.model_dump_json())
        )
        == result
    )


async def test_wire_digest_and_canonical_digest_have_distinct_meaning_and_preserve_opaque_fields():
    def extras(_role, body):
        body["extra"] = {
            "integer": 5,
            "float": 0.5,
            "text": "未知",
            "array": [None, True],
        }
        return body

    result, _ = await capture(change=extras)
    for item in result.provenance:
        assert item.body_sha256 != item.canonical_sha256
        assert json.loads(item.canonical_json)["extra"]["text"] == "未知"
    assert validate_collected_quote(result) == result


@pytest.mark.parametrize("role", ["ticker", "mark", "funding"])
@pytest.mark.parametrize(
    "defect",
    [
        "missing_ts",
        "empty_ts",
        "numeric_ts",
        "seconds_ts",
        "future_own_receipt",
        "stale",
        "wrong_inst",
        "wrong_type",
        "duplicate_row",
        "empty_rows",
    ],
)
async def test_each_component_is_validated_independently(role, defect):
    def change(current, body):
        if current != role:
            return body
        row = body["data"][0]
        if defect == "missing_ts":
            row.pop("ts")
        elif defect == "empty_ts":
            row["ts"] = ""
        elif defect == "numeric_ts":
            row["ts"] = int(row["ts"])
        elif defect == "seconds_ts":
            row["ts"] = row["ts"][:-3]
        elif defect == "future_own_receipt":
            # Before the final batch clock but AFTER this endpoint's receipt.
            index = ("ticker", "mark", "funding").index(role)
            row["ts"] = _ms(NOW + timedelta(milliseconds=index * 3 + 2))
        elif defect == "stale":
            row["ts"] = _ms(NOW - timedelta(seconds=11))
        elif defect == "wrong_inst":
            row["instId"] = "ETH-USDT-SWAP"
        elif defect == "wrong_type":
            row["instType"] = "SPOT"
        elif defect == "duplicate_row":
            body["data"].append(dict(row))
        else:
            body["data"] = []
        return body

    with pytest.raises(QuoteCollectionError):
        await capture(change=change)


@pytest.mark.parametrize(
    "role,field",
    [
        ("ticker", "bidPx"),
        ("ticker", "askPx"),
        ("ticker", "bidSz"),
        ("ticker", "askSz"),
        ("mark", "markPx"),
        ("funding", "fundingRate"),
    ],
)
@pytest.mark.parametrize(
    "value",
    [None, "", "NaN", "Infinity", "1e9999", 0.01, " 0.01", "0.000000000000000000001"],
)
async def test_required_decimals_are_exact_nonempty_bounded_strings(role, field, value):
    def change(current, body):
        if current == role:
            body["data"][0][field] = value
        return body

    with pytest.raises(QuoteCollectionError):
        await capture(change=change)


@pytest.mark.parametrize(
    "field,value",
    [
        ("bidPx", "0"),
        ("askPx", "-1"),
        ("bidSz", "0"),
        ("askSz", "-2"),
        ("bidPx", "101"),
    ],
)
async def test_executable_side_sizes_and_spread_require_positive_uncrossed_geometry(
    field, value
):
    def change(role, body):
        if role == "ticker":
            body["data"][0][field] = value
        return body

    with pytest.raises(QuoteCollectionError):
        await capture(change=change)


async def test_empty_or_missing_funding_never_becomes_zero_or_settlement_time():
    def missing(role, body):
        if role == "funding":
            body["data"][0].pop("fundingRate")
        return body

    with pytest.raises(QuoteCollectionError):
        await capture(change=missing)

    def zero(role, body):
        if role == "funding":
            body["data"][0]["fundingRate"] = "0"
        return body

    result, _ = await capture(change=zero)
    assert result.quote.funding_rate == D(0)
    assert result.quote.funding_time != NOW + timedelta(hours=9)


@pytest.mark.parametrize(
    "barrier", [NOW, NOW + timedelta(seconds=1), NOW.replace(tzinfo=None)]
)
async def test_barrier_requires_strictly_later_request_not_equal_and_must_be_aware(
    barrier,
):
    with pytest.raises(QuoteCollectionError):
        await capture(barrier=barrier)


@pytest.mark.parametrize("index", [1, 2, 3, 9])
async def test_clock_reversal_at_headers_body_next_request_or_final_rejected(index):
    moments = [NOW + timedelta(milliseconds=i) for i in range(10)]
    moments[index] = NOW - timedelta(milliseconds=1)
    with pytest.raises(QuoteCollectionError):
        await capture(clock=Clock(moments))


async def test_final_freshness_recheck_rejects_ticker_that_aged_while_later_requests_completed():
    moments = [NOW + timedelta(milliseconds=i) for i in range(9)] + [
        NOW + timedelta(seconds=1)
    ]
    with pytest.raises(QuoteCollectionError):
        await capture(
            clock=Clock(moments), policy=QuoteCollectionPolicy(max_age_seconds=1)
        )


async def test_utc_offsets_and_hostile_decimal_context_do_not_change_capture():
    result, _ = await capture()
    moments = tuple(
        (NOW + timedelta(milliseconds=i)).astimezone(timezone(timedelta(hours=8)))
        for i in range(10)
    )
    with localcontext(Context(prec=6, rounding=ROUND_UP)) as context:
        context.traps[Inexact] = True
        equivalent, _ = await capture(clock=Clock(moments))
        assert validate_collected_quote(equivalent) == result


@pytest.mark.parametrize(
    "options",
    [
        {"headers": {"Authorization": "DO_NOT_SEND"}},
        {"headers": {"OK-ACCESS-KEY": "DO_NOT_SEND"}},
        {"headers": {"Cookie": "DO_NOT_SEND"}},
        {"cookies": {"session": "DO_NOT_SEND"}},
        {"auth": ("unused", "DO_NOT_SEND")},
        {"params": {"token": "DO_NOT_SEND"}},
        {"event_hooks": {"request": [lambda request: None]}},
    ],
)
async def test_shared_credential_or_hook_client_is_rejected_before_network(options):
    requests = []

    def handler(request):
        requests.append(request)
        raise AssertionError("must not send")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), trust_env=False, **options
    ) as client:
        with pytest.raises(QuoteCollectionError, match="public_client_not_isolated"):
            await collect_executable_quote(
                client=client,
                clock=Clock(),
                report_id=REPORT,
                instrument_id=INSTRUMENT,
                policy=POLICY,
            )
        if "cookies" in options:
            assert dict(client.cookies) == options["cookies"]
    assert not requests


async def test_server_cookies_are_never_replayed_and_client_reuse_is_rejected():
    requests = []
    streams = []

    def handler(request):
        requests.append(request)
        role = {path: role for role, path in ENDPOINTS}[request.url.path]
        stream = Stream((json.dumps(payload(role)).encode(),))
        streams.append(stream)
        return httpx.Response(
            200,
            stream=stream,
            headers={
                "content-type": "application/json",
                "set-cookie": (
                    f"anon_{role}=DO_NOT_REPLAY; Domain=www.okx.com; "
                    "Path=/; Secure; HttpOnly"
                ),
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=True, trust_env=False
    ) as client:
        result = await collect_executable_quote(
            client=client,
            clock=Clock(),
            report_id=REPORT,
            instrument_id=INSTRUMENT,
            policy=POLICY,
        )
        assert validate_collected_quote(result) == result
        assert len(requests) == 3 and all(stream.closed for stream in streams)
        assert dict(client.cookies) == {
            f"anon_{role}": "DO_NOT_REPLAY" for role in ("ticker", "mark", "funding")
        }
        for _role, path in ENDPOINTS:
            # Control only: build_request merges the applicable jar, but these
            # requests are never sent. The collector must keep using Request.
            merged = client.build_request("GET", "https://www.okx.com" + path)
            assert set(merged.headers["cookie"].split("; ")) == {
                f"anon_{name}=DO_NOT_REPLAY" for name in ("ticker", "mark", "funding")
            }
        for request in requests:
            assert request.method == "GET"
            assert request.url.host == "www.okx.com"
            assert set(request.headers) == {
                "host",
                "accept",
                "accept-encoding",
                "user-agent",
            }
            assert "cookie" not in request.headers
            assert "authorization" not in request.headers
            assert not any(key.startswith("ok-access-") for key in request.headers)
        assert result.execution_authority is False
        with pytest.raises(QuoteCollectionError, match="public_client_not_isolated"):
            await collect_executable_quote(
                client=client,
                clock=Clock(),
                report_id=REPORT,
                instrument_id=INSTRUMENT,
                policy=POLICY,
            )
        assert len(requests) == 3  # A new capture must not reuse the populated jar.
        assert len(client.cookies) == 3  # The collector does not clear caller state.


@pytest.mark.parametrize(
    "status,headers",
    [
        (
            302,
            {
                "location": "https://example.invalid/private",
                "set-cookie": (
                    "anon=DO_NOT_REPLAY; Domain=www.okx.com; Path=/; Secure"
                ),
            },
        ),
        (429, {}),
        (500, {}),
        (200, {"content-type": "text/html"}),
        (200, {"content-encoding": "gzip"}),
        (200, {"content-length": "-1"}),
    ],
)
async def test_http_errors_redirects_and_encoding_are_not_followed_or_retried(
    status, headers
):
    requests = []
    stream = Stream((b"SENSITIVE_SERVER_DETAIL",))

    def handler(request):
        requests.append(request)
        return httpx.Response(
            status,
            stream=stream,
            headers={"content-type": "application/json", **headers},
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=True, trust_env=False
    ) as client:
        with pytest.raises(QuoteCollectionError) as exc:
            await collect_executable_quote(
                client=client,
                clock=Clock(),
                report_id=REPORT,
                instrument_id=INSTRUMENT,
                policy=POLICY,
            )
    assert len(requests) == 1 and stream.closed
    assert requests[0].method == "GET" and requests[0].url.host == "www.okx.com"
    assert "cookie" not in requests[0].headers
    assert "authorization" not in requests[0].headers
    assert "SENSITIVE_SERVER_DETAIL" not in str(exc.value)


@pytest.mark.parametrize("declared", [True, False])
async def test_bounded_stream_stops_before_collecting_an_unbounded_body(declared):
    stream = Stream([b"x" * 1024] * 100)
    headers = {"content-type": "application/json"}
    if declared:
        headers["content-length"] = str(100 * 1024)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, stream=stream, headers=headers)
        ),
        trust_env=False,
    ) as client:
        with pytest.raises(QuoteCollectionError, match="response_too_large"):
            await collect_executable_quote(
                client=client,
                clock=Clock(),
                report_id=REPORT,
                instrument_id=INSTRUMENT,
                policy=POLICY,
            )
    assert stream.closed and stream.yielded < 100
    assert stream.yielded == (0 if declared else 33)


@pytest.mark.parametrize(
    "raw",
    [
        b'{"code":"0","code":"0","msg":"","data":[]}',
        b'{"x":NaN}',
        b'{"x":1e9999}',
        b"[" * 1100 + b"0" + b"]" * 1100,
        b"\xff",
        b"not json",
        b'{"code":0,"msg":"","data":[]}',
    ],
)
async def test_malformed_duplicate_nonfinite_or_unbounded_json_is_rejected(raw):
    with pytest.raises(QuoteCollectionError):
        await capture(change=lambda role, body: raw)


async def test_transport_error_is_not_retried_and_exception_does_not_reveal_detail():
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("SENSITIVE_TRANSPORT_DETAIL")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), trust_env=False
    ) as client:
        with pytest.raises(
            QuoteCollectionError, match="public_quote_transport_failed"
        ) as exc:
            await collect_executable_quote(
                client=client,
                clock=Clock(),
                report_id=REPORT,
                instrument_id=INSTRUMENT,
                policy=POLICY,
            )
    assert len(calls) == 1
    assert "SENSITIVE_TRANSPORT_DETAIL" not in str(exc.value)


@pytest.mark.parametrize(
    "defect",
    [
        "quote_price",
        "quote_extra",
        "provenance_extra",
        "body_hash",
        "canonical_hash",
        "body",
        "bundle",
        "authority",
        "policy_extra",
    ],
)
async def test_model_copy_tampering_is_rejected_by_nested_revalidation(defect):
    result, _ = await capture()
    if defect in {"quote_price", "quote_extra"}:
        values = {"bid": D(99)} if defect == "quote_price" else {"hidden": True}
        altered = result.model_copy(
            update={"quote": result.quote.model_copy(update=values)}
        )
    elif defect == "bundle":
        altered = result.model_copy(update={"bundle_sha256": "a" * 64})
    elif defect == "authority":
        altered = result.model_copy(update={"execution_authority": True})
    elif defect == "policy_extra":
        altered = result.model_copy(
            update={"policy": result.policy.model_copy(update={"hidden": True})}
        )
    else:
        updates = {
            "provenance_extra": {"hidden": True},
            "body_hash": {"body_sha256": "b" * 64},
            "canonical_hash": {"canonical_sha256": "c" * 64},
            "body": {"response_body": b"{}"},
        }[defect]
        first = result.provenance[0].model_copy(update=updates)
        altered = result.model_copy(
            update={"provenance": (first, *result.provenance[1:])}
        )
    with pytest.raises(QuoteCollectionError, match="collected_quote_invalid"):
        validate_collected_quote(altered)


async def test_collected_quote_and_nested_provenance_are_immutable():
    result, _ = await capture()
    with pytest.raises(ValidationError):
        result.quote.bid = D(1)
    with pytest.raises(ValidationError):
        result.provenance[0].ts_raw = "1"
    with pytest.raises(TypeError):
        result.provenance[0] = result.provenance[1]


@pytest.mark.parametrize("kind", ["generator", "large_tuple", "list", "missing"])
async def test_revalidation_checks_provenance_shape_before_consuming_any_items(kind):
    result, _ = await capture()
    touched = []

    def infinite():
        touched.append(True)
        while True:
            yield result.provenance[0]

    replacement = {
        "generator": infinite(),
        "large_tuple": (result.provenance[0],) * 10000,
        "list": list(result.provenance),
        "missing": None,
    }[kind]
    with pytest.raises(QuoteCollectionError):
        validate_collected_quote(result.model_copy(update={"provenance": replacement}))
    assert not touched


@pytest.mark.parametrize(
    "kind",
    ["generator", "large_tuple", "list", "pair_generator", "pair_long", "long_text"],
)
async def test_parameter_preflight_rejects_before_serializing_or_consuming(
    kind, monkeypatch
):
    result, _ = await capture()
    touched = []

    def infinite():
        touched.append(True)
        while True:
            yield "instId"

    replacement = {
        "generator": infinite(),
        "large_tuple": (("instId", INSTRUMENT),) * 10000,
        "list": [("instId", INSTRUMENT)],
        "pair_generator": (infinite(),),
        "pair_long": (("instId", INSTRUMENT, "extra"),),
        "long_text": (("instId", "x" * 65),),
    }[kind]
    altered = result.provenance[0].model_copy(update={"parameters": replacement})

    def forbidden_dump(*_args, **_kwargs):
        pytest.fail("invalid parameter shapes must be rejected before model_dump")

    monkeypatch.setattr(module.EndpointObservation, "model_dump", forbidden_dump)
    with pytest.raises(QuoteCollectionError):
        validate_collected_quote(
            result.model_copy(update={"provenance": (altered, *result.provenance[1:])})
        )
    assert not touched


@pytest.mark.parametrize(
    "field,value",
    [
        ("response_body", b"x" * 65537),
        ("response_body", bytearray(b"x")),
        ("canonical_json", "x" * 65537),
        ("canonical_json", ["x"]),
        ("ts_raw", "1" * 65),
        ("body_sha256", "x" * 65),
    ],
    ids=["large_bytes", "bytearray", "large_json", "json_list", "long_ts", "long_hash"],
)
async def test_payload_preflight_rejects_before_serializing(field, value, monkeypatch):
    result, _ = await capture()
    altered = result.provenance[0].model_copy(update={field: value})

    def forbidden_dump(*_args, **_kwargs):
        pytest.fail("unbounded payload must be rejected before model_dump")

    monkeypatch.setattr(module.EndpointObservation, "model_dump", forbidden_dump)
    with pytest.raises(QuoteCollectionError):
        validate_collected_quote(
            result.model_copy(update={"provenance": (altered, *result.provenance[1:])})
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("origin", "https://example.invalid"),
        ("parameters", (("instId", "ETH-USDT-SWAP"),)),
        ("method", "POST"),
    ],
)
async def test_provenance_origin_exact_parameters_and_get_method_are_bound(
    field, value
):
    result, _ = await capture()
    altered = result.provenance[0].model_copy(update={field: value})
    with pytest.raises(QuoteCollectionError):
        validate_collected_quote(
            result.model_copy(update={"provenance": (altered, *result.provenance[1:])})
        )


class WaitingStream(Stream):
    def __init__(self, error=False):
        super().__init__(())
        self.entered = asyncio.Event()
        self.error = error

    async def __aiter__(self):
        self.entered.set()
        if self.error:
            raise httpx.ReadError("SENSITIVE_STREAM_DETAIL")
        await asyncio.Future()
        yield b"unreachable"


@pytest.mark.parametrize(
    "failure", ["request_timeout", "batch_timeout", "stream_error", "cancel"]
)
async def test_streams_close_on_deadline_error_and_cancellation_without_swallowing_cancel(
    failure,
):
    stream = WaitingStream(error=failure == "stream_error")
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            200, stream=stream, headers={"content-type": "application/json"}
        )

    policy = QuoteCollectionPolicy(
        max_age_seconds=10,
        request_timeout_seconds=1 if failure == "request_timeout" else 5,
        batch_timeout_seconds=1 if failure == "batch_timeout" else 6,
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), trust_env=False
    ) as client:
        task = asyncio.create_task(
            collect_executable_quote(
                client=client,
                clock=Clock(),
                report_id=REPORT,
                instrument_id=INSTRUMENT,
                policy=policy,
            )
        )
        await stream.entered.wait()
        if failure == "cancel":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            with pytest.raises(
                QuoteCollectionError, match="public_quote_transport_failed"
            ) as exc:
                await task
            assert "SENSITIVE_STREAM_DETAIL" not in str(exc.value)
        assert not client.is_closed
    assert stream.closed and len(requests) == 1


class SlowCloseStream(Stream):
    def __init__(self, raw, *, delayed_body=False):
        super().__init__((raw,))
        self.close_started = asyncio.Event()
        self.release_close = asyncio.Event()
        self.delayed_body = delayed_body
        self.close_calls = 0

    async def __aiter__(self):
        if self.delayed_body:
            await asyncio.sleep(0.9)
        yield self.chunks[0]

    async def aclose(self):
        self.close_calls += 1
        self.close_started.set()
        await self.release_close.wait()
        self.closed = True


@pytest.mark.parametrize(
    "failure",
    [
        "cancel_during_close",
        "request_timeout_during_close",
        "batch_timeout_during_close",
    ],
)
async def test_eof_close_cannot_be_abandoned_after_httpx_sets_is_closed(failure):
    stream = SlowCloseStream(
        json.dumps(payload("ticker")).encode(),
        delayed_body=failure != "cancel_during_close",
    )
    policy = QuoteCollectionPolicy(
        max_age_seconds=10,
        request_timeout_seconds=1 if failure == "request_timeout_during_close" else 5,
        batch_timeout_seconds=1 if failure == "batch_timeout_during_close" else 6,
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, stream=stream, headers={"content-type": "application/json"}
            )
        ),
        trust_env=False,
    ) as client:
        task = asyncio.create_task(
            collect_executable_quote(
                client=client,
                clock=Clock(),
                report_id=REPORT,
                instrument_id=INSTRUMENT,
                policy=policy,
            )
        )
        await stream.close_started.wait()
        if failure == "cancel_during_close":
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
            stream.release_close.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            # Body EOF occurred at ~0.9s. Let the 1s deadline interrupt close,
            # then allow cleanup to finish within its independent 1s grace.
            await asyncio.sleep(0.2)
            stream.release_close.set()
            with pytest.raises(
                QuoteCollectionError, match="public_quote_transport_failed"
            ):
                await task
    assert stream.closed and stream.close_calls == 1


@pytest.mark.parametrize("cleanup_failure", ["exception", "timeout"])
async def test_body_cancellation_survives_a_subsequent_cleanup_failure(cleanup_failure):
    class BrokenCloseStream(WaitingStream):
        close_calls = 0

        async def aclose(self):
            self.close_calls += 1
            if cleanup_failure == "timeout":
                await asyncio.Future()
            raise RuntimeError("SENSITIVE_CLEANUP_DETAIL")

    stream = BrokenCloseStream()
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            200, stream=stream, headers={"content-type": "application/json"}
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), trust_env=False
    ) as client:
        task = asyncio.create_task(
            collect_executable_quote(
                client=client,
                clock=Clock(),
                report_id=REPORT,
                instrument_id=INSTRUMENT,
                policy=POLICY,
            )
        )
        await stream.entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert task.cancelled()
    assert stream.close_calls == 1 and len(requests) == 1
    assert not stream.closed  # A broken transport is not claimed to have closed.


async def test_environment_proxy_configuration_is_rejected_before_io():
    calls = []
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: calls.append(request)),
        trust_env=True,
    ) as client:
        with pytest.raises(QuoteCollectionError, match="public_client_not_isolated"):
            await collect_executable_quote(
                client=client,
                clock=Clock(),
                report_id=REPORT,
                instrument_id=INSTRUMENT,
                policy=POLICY,
            )
    assert not calls


@pytest.mark.parametrize(
    "instrument",
    [
        "BTC-USDT-SWAP?token=x",
        "../account/balance",
        "BTC-USDT",
        " btc-usdt-swap",
        "HTTPS://example.invalid",
        123,
    ],
)
async def test_instrument_cannot_change_host_path_or_add_query(instrument):
    with pytest.raises(QuoteCollectionError):
        await capture(instrument=instrument)


def test_collector_does_not_import_private_clients_settings_or_order_runtime():
    tree = ast.parse(inspect.getsource(module))
    forbidden = (
        "app.exchange",
        "app.demo_automation",
        "app.live_automation",
        "app.config",
        "app.okx_demo",
        "os",
        "pathlib",
    )
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert not any(alias.name.startswith(forbidden) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert not (node.module or "").startswith(forbidden)
    assert module.ENDPOINTS == ENDPOINTS
    assert len(ENDPOINTS) == 3
