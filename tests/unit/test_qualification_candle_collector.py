"""Synthetic MockTransport-only OHLC captures, never observed market samples."""

import ast
import asyncio
import hashlib
import inspect
import json
from datetime import UTC, datetime, timedelta, timezone
from decimal import Context, Decimal, Inexact, localcontext

import httpx
import pytest
from pydantic import create_model

from app.domain.market import Candle
from app.market.quality.candles import BAR_SECONDS
from app.trade_qualification import candle_collector as module
from app.trade_qualification.candle_collector import (
    TIMEFRAMES,
    CandleCollectionError,
    CandleCollectionPolicy,
    CollectedCandles,
    FrameRequest,
    collect_candles,
    validate_collected_candles,
)

NOW = datetime(2026, 9, 12, 1, 10, 1, tzinfo=UTC)
INSTRUMENT = "BTC-USDT-SWAP"
REPORT = "synthetic-candle-pages"
D = Decimal


class Clock:
    def __init__(self, at=NOW, changes=None):
        self.at, self.calls, self.changes = at, 0, changes or {}

    def __call__(self):
        index = self.calls
        self.calls += 1
        return self.changes.get(index, self.at + timedelta(milliseconds=index))


class Stream(httpx.AsyncByteStream):
    def __init__(self, body):
        self.body, self.closed, self.reads = body, False, 0

    async def __aiter__(self):
        self.reads += 1
        yield self.body[: len(self.body) // 2]
        self.reads += 1
        yield self.body[len(self.body) // 2 :]

    async def aclose(self):
        self.closed = True


def policy(counts=(240, 240, 240, 240), **changes):
    return CandleCollectionPolicy(
        requests=tuple(
            FrameRequest(timeframe=tf, requested_confirmed_bars=count)
            for tf, count in zip(TIMEFRAMES, counts, strict=True)
        ),
        page_size=changes.pop("page_size", 100),
        **changes,
    )


def ms(at):
    delta = at - datetime(1970, 1, 1, tzinfo=UTC)
    return str(
        delta.days * 86400000 + delta.seconds * 1000 + delta.microseconds // 1000
    )


def rows(timeframe, count, *, at=NOW, unconfirmed=True):
    end = module._floor(at, timeframe)
    interval = timedelta(seconds=BAR_SECONDS[timeframe])
    result = []
    if unconfirmed:
        result.append([ms(end), "100", "101", "99", "100.5", "2", "0.02", "2.01", "0"])
    for index in range(1, count + 1):
        result.append(
            [
                ms(end - index * interval),
                "100",
                "101",
                "99",
                "100.5",
                str(index),
                str(D(index) / 100),
                str(D(index) * D("1.005")),
                "1",
            ]
        )
    return result


async def capture(
    *,
    selected=None,
    clock=None,
    edit=None,
    unconfirmed=True,
    barrier=None,
    options=None,
    headers=None,
    status=200,
    stream_factory=Stream,
    requests=None,
    streams=None,
):
    selected = selected or policy()
    clock = clock or Clock()
    requests = [] if requests is None else requests
    streams = [] if streams is None else streams
    datasets = {
        request.timeframe: rows(
            request.timeframe,
            request.requested_confirmed_bars,
            at=clock.at,
            unconfirmed=unconfirmed,
        )
        for request in selected.requests
    }
    indices = {tf: 0 for tf in TIMEFRAMES}

    def handler(request):
        requests.append(request)
        params = dict(request.url.params)
        tf = params["bar"]
        source = datasets[tf]
        after = params.get("after")
        output = [row[:] for row in source if after is None or int(row[0]) < int(after)]
        output = output[: int(params["limit"])]
        packet = {"code": "0", "msg": "", "data": output}
        index = indices[tf]
        indices[tf] += 1
        if edit is not None:
            changed = edit(tf, index, packet)
            if changed is not None:
                packet = changed
        body = packet if type(packet) is bytes else json.dumps(packet).encode()
        stream = stream_factory(body)
        streams.append(stream)
        response_headers = {
            "content-type": "application/json",
            "content-length": str(len(body)),
        }
        if headers:
            response_headers.update(headers)
        return httpx.Response(status, stream=stream, headers=response_headers)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), trust_env=False, **(options or {})
    ) as client:
        result = await collect_candles(
            client=client,
            clock=clock,
            report_id=REPORT,
            instrument_id=INSTRUMENT,
            policy=selected,
            barrier_completed_at=barrier,
        )
    return result, requests, streams, clock


async def test_four_frame_counts_public_requests_pages_and_volume_units():
    selected = policy((240, 240, 241, 241))
    result, requests, streams, clock = await capture(
        selected=selected, barrier=NOW - timedelta(microseconds=1)
    )
    assert len(requests) == 12 and clock.calls == 37
    assert tuple(frame.timeframe for frame in result.frames) == TIMEFRAMES
    assert result.volume_units == ("contracts", "base_currency", "quote_currency")
    assert all(stream.closed for stream in streams)
    for frame, request in zip(result.frames, selected.requests, strict=True):
        assert len(frame.confirmed) == request.requested_confirmed_bars
        assert len(frame.candles) == frame.requested_count
        assert all(type(row) is Candle and row.confirmed for row in frame.candles)
        assert frame.collected_until == frame.pages[0].received_at
        assert frame.verified_through == module._floor(
            frame.collected_until, frame.timeframe
        )
        assert (
            frame.confirmed[-1].timestamp
            + timedelta(seconds=BAR_SECONDS[frame.timeframe])
            == frame.verified_through
        )
        assert len(json.loads(frame.pages[0].response_body)["data"]) == 100
        assert json.loads(frame.pages[0].response_body)["data"][0][-1] == "0"
        assert (
            frame.pages[1].after
            == json.loads(frame.pages[0].response_body)["data"][-1][0]
        )
        assert tuple(page.limit for page in frame.pages) == (
            100,
            100,
            frame.requested_count - 199,
        )
        assert frame.confirmed[-1].volume_contracts == D(1)
        assert frame.confirmed[-1].volume_currency == D("0.01")
        assert frame.confirmed[-1].volume_quote == D("1.005")
        for page in frame.pages:
            assert page.body_sha256 == hashlib.sha256(page.response_body).hexdigest()
            assert (
                page.canonical_sha256
                == hashlib.sha256(page.canonical_json.encode()).hexdigest()
            )
            assert page.request_started_at > result.barrier_completed_at
            assert page.request_started_at <= page.received_at <= page.completed_at
            assert (
                page.source_authenticity_verified is page.execution_authority is False
            )
    for request in requests:
        assert request.method == "GET"
        assert request.url.host == "www.okx.com"
        assert request.url.path == module.ENDPOINT
        assert not request.content
        assert set(request.headers) == {
            "host",
            "accept",
            "accept-encoding",
            "user-agent",
        }
        assert dict(request.url.params)["instId"] == INSTRUMENT
    assert not any(
        (
            result.execution_authority,
            result.source_authenticity_verified,
            result.complete_path_verified,
            result.execution_recheck_performed,
        )
    )
    assert validate_collected_candles(result) == result
    assert (
        CollectedCandles.model_validate_json(
            result.model_dump_json(round_trip=True), strict=True
        )
        == result
    )


@pytest.mark.parametrize("count", (200, 300, 301, 1024))
@pytest.mark.parametrize("unconfirmed", (False, True))
async def test_exact_requested_count_never_trims_or_fills(count, unconfirmed):
    selected = policy((count,) * 4, page_size=300)
    result, _, _, _ = await capture(selected=selected, unconfirmed=unconfirmed)
    for frame in result.frames:
        assert len(frame.confirmed) == count
        raw_count = sum(page.row_count for page in frame.pages)
        assert raw_count == count + int(unconfirmed)
        assert all(row.confirmed for row in frame.confirmed)


async def test_first_capture_cutoff_does_not_advance_when_older_pages_cross_new_close():
    # 5m first headers at 01:14:59.998; later pages finish after 01:15.
    at = datetime(2026, 9, 12, 1, 14, 59, 970000, tzinfo=UTC)
    result, _, _, _ = await capture(clock=Clock(at))
    frame = result.frames[-1]
    assert frame.collected_until == at + timedelta(milliseconds=28)
    assert frame.verified_through == datetime(2026, 9, 12, 1, 10, tzinfo=UTC)
    assert result.completed_at > datetime(2026, 9, 12, 1, 15, tzinfo=UTC)
    assert frame.verified_through < module._floor(result.completed_at, "5m")
    assert frame.confirmed[-1].timestamp == datetime(2026, 9, 12, 1, 5, tzinfo=UTC)
    assert result.complete_path_verified is False


async def test_first_headers_at_exact_new_close_require_that_closed_tail():
    # The response still says the 01:10 bar is open, but receipt is exactly 01:15.
    at = datetime(2026, 9, 12, 1, 14, 59, 972000, tzinfo=UTC)
    requests = []
    # The strict page model wraps the source-row failure; the public boundary
    # exposes its stable capture error, never its raw validation payload.
    with pytest.raises(CandleCollectionError, match="public_candle_capture_invalid"):
        await capture(clock=Clock(at), requests=requests)
    assert len(requests) == 10  # First 5m page denied; no later page fetched.


@pytest.mark.parametrize(
    "field,value",
    (
        ("requested_confirmed_bars", 199),
        ("requested_confirmed_bars", 1025),
        ("requested_confirmed_bars", True),
        ("timeframe", "1m"),
    ),
)
async def test_invalid_frame_request_is_rejected_before_clock_or_io(field, value):
    selected = policy()
    request = selected.requests[0].model_copy(update={field: value})
    selected = selected.model_copy(
        update={"requests": (request, *selected.requests[1:])}
    )
    clock, requests = Clock(), []
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: requests.append(r)), trust_env=False
    ) as client:
        with pytest.raises(CandleCollectionError):
            await collect_candles(
                client=client,
                clock=clock,
                report_id=REPORT,
                instrument_id=INSTRUMENT,
                policy=selected,
            )
    assert not requests and clock.calls == 0


async def test_client_environment_proxy_is_rejected_pre_io():
    requests = []
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: requests.append(r)), trust_env=True
    ) as client:
        with pytest.raises(CandleCollectionError, match="public_client_not_isolated"):
            await collect_candles(
                client=client,
                clock=Clock(),
                report_id=REPORT,
                instrument_id=INSTRUMENT,
                policy=policy(),
            )
    assert not requests


@pytest.mark.parametrize("kind", ("proxy", "retry", "mount"))
async def test_known_proxy_retry_or_mounted_transport_is_denied_before_send(
    kind, monkeypatch
):
    options = {"trust_env": False}
    if kind == "proxy":
        options["proxy"] = "http://user:secret@127.0.0.1:1"
    elif kind == "retry":
        options["transport"] = httpx.AsyncHTTPTransport(retries=1)
    else:
        options["mounts"] = {"https://www.okx.com": httpx.MockTransport(lambda r: None)}
    clock = Clock()
    async with httpx.AsyncClient(**options) as client:

        async def forbidden(*args, **kwargs):
            raise AssertionError("no transport send is authorized by this test")

        monkeypatch.setattr(client, "send", forbidden)
        with pytest.raises(
            CandleCollectionError, match="public_client_transport_"
        ) as error:
            await collect_candles(
                client=client,
                clock=clock,
                report_id=REPORT,
                instrument_id=INSTRUMENT,
                policy=policy(),
            )
    assert "secret" not in str(error.value) and clock.calls == 0


async def test_storage_is_deeply_frozen_and_domain_copies_do_not_mutate_evidence():
    result, _, _, _ = await capture()
    frame = result.frames[0]
    with pytest.raises(ValueError):
        frame.confirmed[0].close = D(1)
    with pytest.raises(ValueError):
        frame.pages[0].after = "123"
    copied = frame.candles
    copied[0].close = D(1)
    assert frame.candles[0].close != 1
    assert validate_collected_candles(result) == result


@pytest.mark.parametrize(
    "defect",
    (
        "duplicate",
        "gap",
        "reverse",
        "offgrid",
        "future",
        "ohlc",
        "negative_volume",
        "interior_open",
        "stale_tail",
    ),
)
async def test_bad_first_page_fails_without_retry_or_later_timeframes(defect):
    requests, streams = [], []

    def edit(tf, index, body):
        data = body["data"]
        if defect == "duplicate":
            data[2] = data[1][:]
        elif defect == "gap":
            data.pop(2)
        elif defect == "reverse":
            data.reverse()
        elif defect == "offgrid":
            data[1][0] = str(int(data[1][0]) + 1)
        elif defect == "future":
            data[0][-1] = "1"
        elif defect == "ohlc":
            data[1][2] = "0.1"
        elif defect == "negative_volume":
            data[1][5] = "-1"
        elif defect == "interior_open":
            data[1][-1] = "0"
        else:
            data.pop(0)
            data.pop(0)

    with pytest.raises(CandleCollectionError):
        await capture(edit=edit, requests=requests, streams=streams)
    assert len(requests) == 1
    assert all(stream.closed for stream in streams)


@pytest.mark.parametrize("defect", ("gap", "overlap", "cursor_newer", "empty"))
async def test_bad_page_chain_stops_at_first_bad_page(defect):
    requests = []

    def edit(tf, index, body):
        if index != 1:
            return
        data = body["data"]
        if defect == "gap":
            data.pop(0)
        elif defect in {"overlap", "cursor_newer"}:
            shift = BAR_SECONDS[tf] * 1000 * (1 if defect == "overlap" else 2)
            for row in data:
                row[0] = str(int(row[0]) + shift)
        else:
            body["data"] = []

    with pytest.raises(CandleCollectionError):
        await capture(edit=edit, requests=requests)
    assert len(requests) == 2


@pytest.mark.parametrize(
    "bad", ("", "NaN", "Infinity", "1e2", " 1", "1 ", "-1", "0", True, 1, None)
)
async def test_prices_are_required_bounded_positive_decimal_strings(bad):
    def edit(tf, index, body):
        body["data"][1][1] = bad

    with pytest.raises(CandleCollectionError):
        await capture(edit=edit)


@pytest.mark.parametrize(
    "raw",
    (
        b'{"code":"0","code":"0","msg":"","data":[]}',
        b'{"code":"0","msg":"","data":NaN}',
        b'{"code":"0","msg":"","data":Infinity}',
        b'{"code":"0","msg":"","data":[[1,2,3]]}',
        b'{"code":"0","msg":"","data":[],"instId":"OTHER-SWAP"}',
        b"[" * 1000 + b"]" * 1000,
        b"\xff",
    ),
)
async def test_malformed_duplicate_deep_unknown_or_nonfinite_json_rejected(raw):
    with pytest.raises(CandleCollectionError):
        await capture(edit=lambda *args: raw)


async def test_page_budget_deficiency_does_not_return_short_coverage():
    requests = []
    selected = policy((200,) * 4, page_size=300, max_pages_per_frame=1)
    with pytest.raises(CandleCollectionError, match="candle_page_budget_exceeded"):
        await capture(selected=selected, requests=requests)
    assert len(requests) == 1


@pytest.mark.parametrize("at", (NOW, NOW + timedelta(microseconds=1)))
async def test_barrier_is_strict_before_any_get(at):
    requests = []
    with pytest.raises(CandleCollectionError, match="publication_barrier_not_crossed"):
        await capture(barrier=at, requests=requests)
    assert not requests


@pytest.mark.parametrize("index", (1, 2, 3, 36))
async def test_capture_clock_reversal_fails(index):
    with pytest.raises(CandleCollectionError):
        await capture(clock=Clock(changes={index: NOW - timedelta(seconds=1)}))


async def test_timezone_and_hostile_decimal_context_preserve_capture():
    original, _, _, _ = await capture()
    # Fixture raw volumes are constructed outside the hostile calculation context.
    with localcontext(Context(prec=100)):
        at = NOW.astimezone(timezone(timedelta(hours=8)))
    with localcontext(Context(prec=6, traps=[Inexact])):
        equivalent, _, _, _ = await capture(clock=Clock(at))
    assert original == equivalent


@pytest.mark.parametrize(
    "options",
    (
        {"auth": ("user", "secret")},
        {"cookies": {"secret": "value"}},
        {"headers": {"Authorization": "secret"}},
        {"headers": {"Cookie": "secret"}},
        {"headers": {"OK-ACCESS-KEY": "secret"}},
        {"params": {"secret": "value"}},
        {"event_hooks": {"request": [lambda request: None]}},
    ),
)
async def test_credentialed_shared_client_is_rejected_pre_io(options):
    requests = []
    with pytest.raises(CandleCollectionError, match="public_client_not_isolated"):
        await capture(options=options, requests=requests)
    assert not requests


async def test_incoming_cookies_never_become_outgoing_credentials():
    result, requests, _, _ = await capture(
        headers={"set-cookie": "public_id=abc; Domain=www.okx.com; Path=/; Secure"}
    )
    assert len(requests) == 12
    assert all("cookie" not in request.headers for request in requests)
    assert result.source_authenticity_verified is False


@pytest.mark.parametrize(
    "status,headers",
    (
        (302, {"location": "https://attacker.example/", "set-cookie": "id=a; Path=/"}),
        (429, {}),
        (500, {}),
        (200, {"content-encoding": "gzip"}),
        (200, {"content-type": "text/html"}),
        (200, {"content-length": "-1"}),
    ),
)
async def test_http_media_encoding_errors_close_without_redirect_or_retry(
    status, headers
):
    requests, streams = [], []
    with pytest.raises(CandleCollectionError):
        await capture(
            status=status,
            headers=headers,
            options={"follow_redirects": True},
            requests=requests,
            streams=streams,
        )
    assert len(requests) == 1 and all(stream.closed for stream in streams)


async def test_oversized_wire_response_rejected_before_unbounded_read():
    streams = []
    with pytest.raises(CandleCollectionError, match="response_too_large"):
        await capture(
            edit=lambda *args: b"x" * 200000,
            selected=policy(max_response_bytes=1024),
            streams=streams,
        )
    assert len(streams) == 1 and streams[0].closed and streams[0].reads == 0


@pytest.mark.parametrize(
    "field",
    (
        "bundle_sha256",
        "completed_at",
        "execution_authority",
        "source_authenticity_verified",
        "complete_path_verified",
        "execution_recheck_performed",
    ),
)
async def test_model_copy_does_not_create_valid_source_or_authority(field):
    result, _, _, _ = await capture()
    value = (
        "0" * 64
        if field == "bundle_sha256"
        else NOW
        if field == "completed_at"
        else True
    )
    with pytest.raises(CandleCollectionError):
        validate_collected_candles(result.model_copy(update={field: value}))


@pytest.mark.parametrize("level", ("frame", "page", "candle", "policy", "request"))
async def test_nested_hidden_fields_and_subclasses_are_rejected(level):
    result, _, _, _ = await capture()
    frame = result.frames[0]
    nested = {
        "frame": frame,
        "page": frame.pages[0],
        "candle": frame.confirmed[0],
        "policy": result.policy,
        "request": result.policy.requests[0],
    }[level]
    subclass = create_model("UntrustedCandleModel", __base__=type(nested))
    for changed in (
        nested.model_copy(update={"extra": True}),
        subclass.model_construct(**nested.__dict__),
    ):
        if level == "page":
            replacement = frame.model_copy(
                update={"pages": (changed, *frame.pages[1:])}
            )
        elif level == "candle":
            replacement = frame.model_copy(
                update={"confirmed": (changed, *frame.confirmed[1:])}
            )
        elif level == "request":
            replacement = None
            bad_policy = result.policy.model_copy(
                update={"requests": (changed, *result.policy.requests[1:])}
            )
        else:
            replacement = changed
        bad = (
            result.model_copy(
                update={"policy": bad_policy if level == "request" else changed}
            )
            if level in {"policy", "request"}
            else result.model_copy(update={"frames": (replacement, *result.frames[1:])})
        )
        with pytest.raises(CandleCollectionError):
            validate_collected_candles(bad)


@pytest.mark.parametrize(
    "field", ("frames", "pages", "confirmed", "parameters", "requests")
)
async def test_preflight_never_consumes_unbounded_iterators(field):
    result, _, _, _ = await capture()

    def never():
        raise AssertionError("iterator must not be consumed")
        yield  # pragma: no cover

    if field == "frames":
        bad = result.model_copy(update={field: never()})
    elif field == "requests":
        bad = result.model_copy(
            update={"policy": result.policy.model_copy(update={field: never()})}
        )
    else:
        frame = result.frames[0]
        if field == "parameters":
            page = frame.pages[0].model_copy(update={field: never()})
            frame = frame.model_copy(update={"pages": (page, *frame.pages[1:])})
        else:
            frame = frame.model_copy(update={field: never()})
        bad = result.model_copy(update={"frames": (frame, *result.frames[1:])})
    with pytest.raises(CandleCollectionError):
        validate_collected_candles(bad)


@pytest.mark.parametrize("cleanup_failure", (False, True))
async def test_caller_cancellation_survives_cleanup_failure(cleanup_failure):
    entered = asyncio.Event()
    streams = []

    class Waiting(Stream):
        async def __aiter__(self):
            entered.set()
            await asyncio.Future()
            yield b""  # pragma: no cover

        async def aclose(self):
            self.closed = True
            if cleanup_failure:
                raise RuntimeError("sensitive transport detail")

    task = asyncio.create_task(capture(stream_factory=Waiting, streams=streams))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.cancelled() and streams[0].closed


@pytest.mark.parametrize("timeout_kind", ("request", "batch"))
async def test_real_async_deadline_closes_and_never_retries(timeout_kind):
    streams, requests = [], []

    class Waiting(Stream):
        async def __aiter__(self):
            await asyncio.Future()
            yield b""  # pragma: no cover

    selected = policy(
        request_timeout_seconds=1 if timeout_kind == "request" else 5,
        batch_timeout_seconds=1 if timeout_kind == "batch" else 30,
    )
    with pytest.raises(CandleCollectionError, match="public_candle_transport_failed"):
        await capture(
            selected=selected,
            stream_factory=Waiting,
            requests=requests,
            streams=streams,
        )
    assert len(requests) == 1 and streams[0].closed


def test_collector_has_no_legacy_parser_settings_private_account_or_order_wiring():
    source = inspect.getsource(module)
    tree = ast.parse(source)
    forbidden = {
        "get_settings",
        "now",
        "sort",
        "sorted",
        "place_order",
        "parse_candles",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = (
                node.func.id
                if isinstance(node.func, ast.Name)
                else getattr(node.func, "attr", "")
            )
            # Only request parameter keys are canonicalized; candles are never sorted.
            if name == "sorted":
                assert ast.unparse(node) == "sorted(params.items())"
            else:
                assert name not in forbidden
        if isinstance(node, ast.ImportFrom):
            assert "private" not in (node.module or "")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "location", ["top_hash", "report", "instrument", "frame_hash", "page_hash"]
)
async def test_raw_pin_whitespace_cannot_be_repaired_by_revalidation(location):
    result, *_ = await capture()
    if location == "top_hash":
        dirty = result.model_copy(update={"bundle_sha256": " " + result.bundle_sha256})
    elif location in ("report", "instrument"):
        name = "report_id" if location == "report" else "instrument_id"
        dirty = result.model_copy(update={name: " " + getattr(result, name)})
    else:
        frame = result.frames[0]
        if location == "frame_hash":
            frame = frame.model_copy(update={"frame_sha256": " " + frame.frame_sha256})
        else:
            page = frame.pages[0].model_copy(
                update={"body_sha256": " " + frame.pages[0].body_sha256}
            )
            frame = frame.model_copy(update={"pages": (page, *frame.pages[1:])})
        dirty = result.model_copy(update={"frames": (frame, *result.frames[1:])})
    with pytest.raises(CandleCollectionError):
        validate_collected_candles(dirty)
    # The public JSON constructor must not trim the same invalid source pins
    # before the exact-record validator ever receives them.
    with pytest.raises(ValueError):
        CollectedCandles.model_validate_json(
            dirty.model_dump_json(round_trip=True), strict=True
        )
