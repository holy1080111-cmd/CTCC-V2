"""Real leaf parsers over synthetic HTTP and socket IO; never market samples."""

import ast
import asyncio
import inspect
import json
from datetime import timedelta, timezone
from decimal import Decimal

import httpx
import pytest
from pydantic import BaseModel, ValidationError, create_model, model_serializer

from app.trade_qualification import public_market_collector as module
from tests.unit.test_qualification_candle_collector import (
    INSTRUMENT,
    NOW,
    Clock,
    Stream,
    ms,
    rows,
)
from tests.unit.test_qualification_market_aux_collector import payload as aux_payload
from tests.unit.test_qualification_quote_collector import payload as quote_payload
from tests.unit.test_qualification_ws_collector import Socket, ticker

REPORT = "synthetic-public-packet"
BARRIER = NOW - timedelta(seconds=1)
ERROR = module.PublicMarketCollectionError


def policy(*, counts=(240, 240, 240, 241), ages=(5, 5, 5), **kwargs):
    return module.PublicMarketCollectionPolicy(
        quote=module.quotes.QuoteCollectionPolicy(max_age_seconds=ages[0]),
        candles=module.candles.CandleCollectionPolicy(
            requests=tuple(
                module.candles.FrameRequest(
                    timeframe=tf,
                    requested_confirmed_bars=count,
                )
                for tf, count in zip(module.candles.TIMEFRAMES, counts, strict=True)
            ),
        ),
        market_aux=module.aux.MarketAuxCollectionPolicy(max_age_seconds=ages[1]),
        ws=module.ws.WSCollectionPolicy(max_age_seconds=ages[2]),
        **kwargs,
    )


async def capture(
    monkeypatch,
    *,
    selected=None,
    clock=None,
    response_edit=None,
    at=NOW,
    final_time=None,
):
    selected = policy() if selected is None else selected
    socket = Socket()
    socket.ticker = ticker(at)
    requests, clients, streams, connects = [], [], [], []
    datasets = {
        item.timeframe: rows(item.timeframe, item.requested_confirmed_bars, at=at)
        for item in selected.candles.requests
    }

    async def handler(request):
        requests.append(request)
        await asyncio.sleep(0)  # ensure all four owners can make progress
        if request.url.path == module.candles.ENDPOINT:
            params = request.url.params
            data = datasets[params["bar"]]
            if params.get("after"):
                data = [row for row in data if int(row[0]) < int(params["after"])]
            value = {"code": "0", "msg": "", "data": data[: int(params["limit"])]}
        elif request.url.path in dict(module.quotes.ENDPOINTS).values():
            role = {path: name for name, path in module.quotes.ENDPOINTS}[
                request.url.path
            ]
            value = quote_payload(role)
            value["data"][0]["ts"] = ms(at)
        else:
            role = "books" if request.url.path.endswith("/books") else "open_interest"
            value = aux_payload(role)
            value["data"][0]["ts"] = ms(at)
        if response_edit:
            response_edit(request, value)
        body = json.dumps(value).encode()
        stream = Stream(body)
        streams.append(stream)
        return httpx.Response(
            200,
            stream=stream,
            headers={
                "content-type": "application/json",
                "content-length": str(len(body)),
                "set-cookie": "anonymous=1; Domain=www.okx.com; Path=/; Secure",
            },
        )

    def factory():
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler), trust_env=False
        )
        clients.append(client)
        return client

    async def connector(uri, **kwargs):
        connects.append((uri, kwargs))
        return socket

    monkeypatch.setattr(module, "_new_client", factory)
    monkeypatch.setattr(module.ws, "_NoRedirectConnect", connector)
    base_clock = clock or Clock(at=at)

    def capture_clock():
        if (
            final_time is not None
            and len(clients) == 3
            and all(c.is_closed for c in clients)
            and socket.closed
        ):
            return final_time
        return base_clock()

    try:
        result = await module.collect_public_market(
            clock=capture_clock,
            report_id=REPORT,
            instrument_id=INSTRUMENT,
            policy=selected,
            barrier_completed_at=at - timedelta(seconds=1),
        )
    finally:
        assert all(client.is_closed for client in clients)
        assert all(stream.closed for stream in streams)
        assert socket.closed
    return result, clients, requests, socket, connects


@pytest.fixture(scope="module")
async def packet():
    with pytest.MonkeyPatch.context() as patch:
        result, *_ = await capture(patch)
    return result


def resign(packet, **updates):
    value = packet.model_copy(update=updates)
    digest = module._digest(
        {k: v for k, v in value.__dict__.items() if k != "bundle_sha256"}
    )
    return value.model_copy(update={"bundle_sha256": digest})


@pytest.mark.asyncio
async def test_three_real_http_leaves_and_owned_socket_are_closed_and_pinned(
    monkeypatch,
):
    result, clients, requests, socket, connects = await capture(monkeypatch)
    assert len(clients) == 3 and len({id(c) for c in clients}) == 3
    assert len(requests) == 13 and len(connects) == 1
    assert len(socket.sent) == 1 and socket.receives == 2 and socket.closes == 1
    assert all(
        request.method == "GET" and request.url.host == "www.okx.com"
        for request in requests
    )
    assert all(
        not any(
            name in request.headers
            for name in ("cookie", "authorization", "ok-access-key")
        )
        for request in requests
    )
    assert all(
        list(c.cookies.jar) for c in clients
    )  # server really set matching cookies
    for field in ("quote", "candles", "market_aux", "ws"):
        component = getattr(result, field)
        assert component.policy == getattr(result.policy, field)
        assert component.barrier_completed_at == result.barrier_completed_at == BARRIER
        assert component.completed_at <= result.completed_at
    assert tuple(f.requested_count for f in result.candles.frames) == (
        240,
        240,
        240,
        241,
    )
    assert tuple(len(f.confirmed) for f in result.candles.frames) == (
        240,
        240,
        240,
        241,
    )
    assert all(
        f.verified_through == module.candles._floor(result.completed_at, f.timeframe)
        for f in result.candles.frames
    )
    assert all(getattr(result, name) is False for name in module._FLAGS)
    assert not hasattr(result, "market_snapshot")
    assert module.validate_collected_public_market(result) == result


def test_strict_json_roundtrip_and_frozen_deep_evidence(packet):
    restored = module.CollectedPublicMarket.model_validate_json(
        packet.model_dump_json(round_trip=True), strict=True
    )
    assert restored == packet
    assert module.validate_collected_public_market(restored) == packet
    with pytest.raises(ValidationError):
        packet.completed_at = NOW
    with pytest.raises(ValidationError):
        packet.candles.frames[0].confirmed[0].close = Decimal(100)


@pytest.mark.parametrize(
    "updates",
    [
        {"report_id": "another-report"},
        {"instrument_id": "ETH-USDT-SWAP"},
        {"barrier_completed_at": None},
        {"barrier_completed_at": BARRIER - timedelta(microseconds=1)},
        {"started_at": NOW + timedelta(seconds=1)},
        {"completed_at": NOW},
        {"completed_at": NOW + timedelta(seconds=31)},
        {"policy": policy(ages=(6, 5, 5))},
        {"policy": policy(counts=(240, 240, 240, 240))},
    ],
)
def test_even_resigned_packet_cannot_replace_pins_or_policy_or_clocks(packet, updates):
    with pytest.raises(ERROR, match="public_record_invalid"):
        module.validate_collected_public_market(resign(packet, **updates))


def test_wire_tamper_and_top_hash_tamper_rejected(packet):
    with pytest.raises(ERROR):
        module.validate_collected_public_market(
            packet.model_copy(update={"bundle_sha256": "0" * 64})
        )
    page = packet.candles.frames[0].pages[0]
    changed_page = page.model_copy(
        update={"response_body": page.response_body.replace(b"100.5", b"100.6")}
    )
    frame = packet.candles.frames[0].model_copy(
        update={"pages": (changed_page, *packet.candles.frames[0].pages[1:])}
    )
    changed = packet.candles.model_copy(
        update={"frames": (frame, *packet.candles.frames[1:])}
    )
    with pytest.raises(ERROR):
        module.validate_collected_public_market(resign(packet, candles=changed))


@pytest.mark.parametrize("flag", module._FLAGS)
@pytest.mark.parametrize("value", [True, 0, 1, "false"])
def test_authority_cannot_be_requested(packet, flag, value):
    with pytest.raises(ERROR):
        module.validate_collected_public_market(resign(packet, **{flag: value}))


@pytest.mark.parametrize(
    "role,ages", [("quote", (2, 5, 5)), ("market_aux", (5, 2, 5)), ("ws", (5, 5, 2))]
)
@pytest.mark.asyncio
async def test_each_source_age_at_batch_completion_has_exact_boundary(
    monkeypatch, role, ages
):
    result, *_ = await capture(monkeypatch, selected=policy(ages=ages))
    equal = resign(result, completed_at=NOW + timedelta(seconds=2))
    assert module.validate_collected_public_market(equal) == equal
    later = resign(result, completed_at=NOW + timedelta(seconds=2, microseconds=1))
    with pytest.raises(ValidationError, match=f"public_{role}_stale_at_completion"):
        module.CollectedPublicMarket.model_validate(module._plain(later), strict=True)


@pytest.mark.parametrize("before", [True, False])
@pytest.mark.asyncio
async def test_final_batch_crossing_closed_boundary_cannot_advance_candle_proof(
    monkeypatch, before
):
    boundary = NOW.replace(minute=15, second=0)
    final = boundary - timedelta(microseconds=1) if before else boundary
    if before:
        result, *_ = await capture(
            monkeypatch, at=boundary - timedelta(seconds=1), final_time=final
        )
        assert result.candles.frames[-1].verified_through == boundary - timedelta(
            minutes=5
        )
    else:
        with pytest.raises(ERROR):
            await capture(
                monkeypatch, at=boundary - timedelta(seconds=1), final_time=final
            )


@pytest.mark.parametrize(
    "kind",
    [
        "policy",
        "quote",
        "candle",
        "book",
        "ws",
        "hidden",
        "generator",
        "oversized",
        "subclass",
    ],
)
def test_raw_preflight_rejects_dirty_nested_objects_without_serializer(packet, kind):
    invoked = []

    class Trap(BaseModel):
        @model_serializer
        def serialize(self):
            invoked.append(True)
            return 5

    if kind == "policy":
        value = packet.model_copy(
            update={
                "policy": packet.policy.model_copy(
                    update={
                        "ws": packet.policy.ws.model_copy(
                            update={"max_age_seconds": Trap()}
                        )
                    }
                )
            }
        )
    elif kind == "quote":
        value = packet.model_copy(
            update={
                "quote": packet.quote.model_copy(
                    update={
                        "quote": packet.quote.quote.model_copy(update={"bid": Trap()})
                    }
                )
            }
        )
    elif kind == "candle":
        frame = packet.candles.frames[0].model_copy(
            update={"confirmed": (Trap(),) * 240}
        )
        value = packet.model_copy(
            update={
                "candles": packet.candles.model_copy(
                    update={"frames": (frame, *packet.candles.frames[1:])}
                )
            }
        )
    elif kind == "book":
        value = packet.model_copy(
            update={
                "market_aux": packet.market_aux.model_copy(
                    update={
                        "book": packet.market_aux.book.model_copy(
                            update={"bids": (Trap(),)}
                        )
                    }
                )
            }
        )
    elif kind == "ws":
        value = packet.model_copy(
            update={"ws": packet.ws.model_copy(update={"subscription_body": Trap()})}
        )
    elif kind == "hidden":
        value = packet.model_copy(
            update={"ws": packet.ws.model_copy(update={"unexpected": "ignored?"})}
        )
    elif kind in ("generator", "oversized"):
        sequence = (
            (part for part in packet.quote.provenance)
            if kind == "generator"
            else packet.quote.provenance * 10000
        )
        value = packet.model_copy(
            update={"quote": packet.quote.model_copy(update={"provenance": sequence})}
        )
    else:
        subclass = create_model("DerivedPacket", __base__=module.CollectedPublicMarket)
        value = subclass.model_construct(**packet.__dict__)
    with pytest.raises(ERROR):
        module.validate_collected_public_market(value)
    assert not invoked


@pytest.mark.parametrize(
    "key,value",
    [
        ("report_id", "bad id"),
        ("report_id", "valid-report\n"),
        ("instrument_id", "BTC-USDT"),
        ("clock", None),
        ("clock", lambda: NOW.replace(tzinfo=None)),
        ("barrier_completed_at", NOW),
        ("barrier_completed_at", NOW + timedelta(microseconds=1)),
        ("policy", policy().model_copy(update={"total_timeout_seconds": True})),
    ],
)
@pytest.mark.asyncio
async def test_invalid_inputs_rejected_before_any_client_or_socket(
    monkeypatch, key, value
):
    called = []

    def forbidden():
        called.append(True)
        raise AssertionError("no IO expected")

    monkeypatch.setattr(module, "_new_client", forbidden)
    monkeypatch.setattr(module.ws, "collect_ws_reference", forbidden)
    inputs = {
        "clock": lambda: NOW,
        "report_id": REPORT,
        "instrument_id": INSTRUMENT,
        "policy": policy(),
        "barrier_completed_at": BARRIER,
    }
    inputs[key] = value
    with pytest.raises(ERROR):
        await module.collect_public_market(**inputs)
    assert not called


@pytest.mark.asyncio
async def test_policy_serializer_trap_is_denied_before_io(monkeypatch):
    invoked = []

    class Trap(BaseModel):
        @model_serializer
        def serialize(self):
            invoked.append(True)
            return 5

    selected = policy()
    selected = selected.model_copy(
        update={"quote": selected.quote.model_copy(update={"max_age_seconds": Trap()})}
    )
    monkeypatch.setattr(module, "_new_client", lambda: pytest.fail("IO forbidden"))
    with pytest.raises(ERROR):
        await module.collect_public_market(
            clock=Clock(), report_id=REPORT, instrument_id=INSTRUMENT, policy=selected
        )
    assert not invoked


@pytest.mark.asyncio
async def test_default_client_is_fresh_no_env_no_auth_no_redirect_no_retry():
    first, second = module._new_client(), module._new_client()
    try:
        assert first is not second
        for client in (first, second):
            assert type(client) is httpx.AsyncClient
            assert client.trust_env is False and client.follow_redirects is False
            assert client.auth is None and not client.cookies and not client.params
            assert client._transport._pool._retries == 0
            assert not any(client._mounts.values())
    finally:
        await first.aclose()
        await second.aclose()


@pytest.mark.asyncio
async def test_equivalent_utc_offset_clock_normalizes_capture(monkeypatch):
    clock = Clock()
    result, *_ = await capture(
        monkeypatch, clock=lambda: clock().astimezone(timezone(timedelta(hours=8)))
    )
    assert result.started_at.utcoffset() == timedelta(0)
    assert module.validate_collected_public_market(result) == result


def test_no_legacy_market_bridge_or_orders_or_gate_wiring():
    tree = ast.parse(inspect.getsource(module))
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    assert not names & {
        "MarketSnapshot",
        "MarketDataService",
        "get_settings",
        "evaluate_data",
        "evaluate_prefix",
        "place_order",
    }
    source = inspect.getsource(module)
    assert "volume_quote_24h" not in source


def blocked_leaves(
    monkeypatch,
    *,
    failing=None,
    cleanup_error=False,
    block_cleanup=False,
    failure_error=None,
):
    """Only failure scheduling is stubbed; successful packet tests use real IO."""
    started, finished, cancelled, clients = [], [], [], []
    all_started = asyncio.Event()
    closing = asyncio.Event()

    def factory():
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(200)),
            trust_env=False,
        )
        clients.append(client)
        original_close = client.aclose

        async def close():
            closing.set()
            if block_cleanup:
                await asyncio.Event().wait()
            await original_close()
            if cleanup_error:
                raise RuntimeError("secret cleanup detail")

        monkeypatch.setattr(client, "aclose", close)
        return client

    def leaf(index):
        async def run(**kwargs):
            assert (
                kwargs["report_id"] == REPORT and kwargs["instrument_id"] == INSTRUMENT
            )
            assert kwargs["barrier_completed_at"] == BARRIER
            started.append(index)
            if len(started) == 4:
                all_started.set()
            try:
                await all_started.wait()
                if index == failing:
                    raise (
                        failure_error
                        if failure_error is not None
                        else RuntimeError("secret remote body")
                    )
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.append(index)
                raise
            finally:
                await asyncio.sleep(0)
                finished.append(index)

        return run

    monkeypatch.setattr(module, "_new_client", factory)
    monkeypatch.setattr(module.quotes, "collect_executable_quote", leaf(0))
    monkeypatch.setattr(module.candles, "collect_candles", leaf(1))
    monkeypatch.setattr(module.aux, "collect_market_aux", leaf(2))
    monkeypatch.setattr(module.ws, "collect_ws_reference", leaf(3))
    return started, finished, cancelled, clients, all_started, closing


async def blocked_capture(**kwargs):
    return await module.collect_public_market(
        clock=lambda: NOW,
        report_id=REPORT,
        instrument_id=INSTRUMENT,
        barrier_completed_at=BARRIER,
        policy=policy(**kwargs),
    )


@pytest.mark.parametrize("failing", range(4))
@pytest.mark.asyncio
async def test_each_leaf_failure_cancels_joins_siblings_and_closes_all_owned_clients(
    monkeypatch, failing
):
    started, finished, cancelled, clients, *_ = blocked_leaves(
        monkeypatch, failing=failing
    )
    with pytest.raises(ERROR, match="public_component_capture_failed") as caught:
        await blocked_capture()
    assert sorted(started) == sorted(finished) == list(range(4))
    assert sorted(cancelled) == [n for n in range(4) if n != failing]
    assert len(clients) == 3 and all(c.is_closed for c in clients)
    assert "secret" not in str(caught.value)
    assert caught.value.component_failures == (
        (["quote", "candles", "market_aux", "ws"][failing], "component_failed"),
    )


@pytest.mark.parametrize("cleanup_error", [False, True])
@pytest.mark.asyncio
async def test_caller_cancellation_is_preserved_even_if_cleanup_raises(
    monkeypatch, cleanup_error
):
    _, finished, cancelled, clients, ready, _ = blocked_leaves(
        monkeypatch, cleanup_error=cleanup_error
    )
    task = asyncio.create_task(blocked_capture())
    await ready.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.cancelled()
    assert sorted(finished) == sorted(cancelled) == list(range(4))
    assert all(c.is_closed for c in clients)


@pytest.mark.asyncio
async def test_total_deadline_cancels_and_joins_all_components_without_packet(
    monkeypatch,
):
    _, finished, cancelled, clients, *_ = blocked_leaves(monkeypatch)
    with pytest.raises(ERROR, match="public_batch_timeout"):
        await blocked_capture(total_timeout_seconds=1)
    assert sorted(finished) == sorted(cancelled) == list(range(4))
    assert all(c.is_closed for c in clients)


@pytest.mark.asyncio
async def test_cleanup_timeout_cannot_swallow_caller_cancellation(monkeypatch):
    _, finished, cancelled, clients, ready, closing = blocked_leaves(
        monkeypatch, block_cleanup=True
    )
    task = asyncio.create_task(blocked_capture())
    await ready.wait()
    task.cancel()
    await closing.wait()
    assert not task.done()  # cancellation waits for bounded cleanup, not detached IO
    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.cancelled() and len(finished) == len(cancelled) == 4
    # Cleanup failure does not claim clients are closed. Close unused mock clients
    # ourselves in this fixture; production emitted no packet and no authority.
    for client in clients:
        await httpx.AsyncClient.aclose(client)


@pytest.mark.asyncio
async def test_cleanup_failure_after_leaf_success_cannot_emit_a_packet(
    monkeypatch, packet
):
    clients = []

    def factory():
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(200)),
            trust_env=False,
        )
        clients.append(client)

        async def close():
            await httpx.AsyncClient.aclose(client)
            raise RuntimeError("secret close error")

        monkeypatch.setattr(client, "aclose", close)
        return client

    def success(component):
        async def result(**kwargs):
            return component

        return result

    monkeypatch.setattr(module, "_new_client", factory)
    for target, name, component in (
        (module.quotes, "collect_executable_quote", packet.quote),
        (module.candles, "collect_candles", packet.candles),
        (module.aux, "collect_market_aux", packet.market_aux),
        (module.ws, "collect_ws_reference", packet.ws),
    ):
        monkeypatch.setattr(target, name, success(component))
    with pytest.raises(ERROR):
        await blocked_capture()
    assert len(clients) == 3 and all(c.is_closed for c in clients)


def test_python_json_shaped_dicts_do_not_get_json_coercion(packet):
    with pytest.raises((ERROR, ValidationError)):
        module.CollectedPublicMarket.model_validate(
            json.loads(packet.model_dump_json()), strict=True
        )
    with pytest.raises((ERROR, ValidationError)):
        module.PublicMarketCollectionPolicy.model_validate(
            json.loads(packet.policy.model_dump_json()), strict=True
        )


def replace_path(value, path, update):
    key, *remaining = path
    if type(value) is tuple:
        parts = list(value)
        parts[key] = (
            replace_path(parts[key], remaining, update) if remaining else update
        )
        return tuple(parts)
    current = getattr(value, key)
    changed = replace_path(current, remaining, update) if remaining else update
    return value.model_copy(update={key: changed})


@pytest.mark.parametrize(
    "path",
    [
        ("bundle_sha256",),
        ("report_id",),
        ("instrument_id",),
        ("quote", "bundle_sha256"),
        ("quote", "quote", "report_id"),
        ("quote", "provenance", 0, "body_sha256"),
        ("candles", "bundle_sha256"),
        ("candles", "frames", 0, "frame_sha256"),
        ("market_aux", "bundle_sha256"),
        ("market_aux", "book", "instrument_id"),
        ("ws", "bundle_sha256"),
        ("ws", "ticker", "frame_sha256"),
    ],
)
def test_no_hash_resigning_whitespace_cannot_be_laundered(packet, path):
    original = packet
    for key in path:
        original = original[key] if type(key) is int else getattr(original, key)
    dirty = replace_path(packet, path, " " + original)
    with pytest.raises(ERROR):
        module.validate_collected_public_market(dirty)


@pytest.mark.parametrize("where", ["public_policy", "quote_record"])
def test_same_layout_wrong_policy_class_cannot_be_erased(packet, where):
    wrong = module.aux.MarketAuxCollectionPolicy.model_validate(
        dict(packet.policy.quote.__dict__), strict=True
    )
    if where == "public_policy":
        dirty = packet.model_copy(
            update={"policy": packet.policy.model_copy(update={"quote": wrong})}
        )
        with pytest.raises((ERROR, ValidationError)):
            module.PublicMarketCollectionPolicy.model_validate(
                {**packet.policy.__dict__, "quote": wrong}, strict=True
            )
    else:
        dirty = packet.model_copy(
            update={"quote": packet.quote.model_copy(update={"policy": wrong})}
        )
    with pytest.raises(ERROR):
        module.validate_collected_public_market(dirty)


@pytest.mark.asyncio
async def test_external_cancel_during_leaf_failure_cleanup_is_not_replaced(monkeypatch):
    _, finished, _, clients, _, closing = blocked_leaves(
        monkeypatch, failing=3, block_cleanup=True
    )
    task = asyncio.create_task(blocked_capture())
    await closing.wait()  # WS already failed; siblings are in owned client cleanup
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.cancelled() and len(finished) == 4
    for client in clients:
        await httpx.AsyncClient.aclose(client)


@pytest.mark.parametrize(
    "role,exception",
    [
        (0, module.quotes.QuoteCollectionError("future_component_timestamp")),
        (1, module.candles.CandleCollectionError("candle_page_budget_exceeded")),
        (2, module.aux.MarketAuxCollectionError("component_stale")),
        (3, module.ws.WSCollectionError("ws_reference_stale")),
    ],
)
@pytest.mark.asyncio
async def test_exact_known_leaf_safe_code_is_preserved_but_cancelled_siblings_are_not(
    monkeypatch, role, exception
):
    _, _, cancelled, _, *_ = blocked_leaves(
        monkeypatch, failing=role, failure_error=exception
    )
    with pytest.raises(ERROR) as caught:
        await blocked_capture()
    assert str(caught.value) == "public_component_capture_failed"
    assert caught.value.component_failures == (
        (["quote", "candles", "market_aux", "ws"][role], exception.args[0]),
    )
    assert len(cancelled) == 3
    with pytest.raises(AttributeError):
        caught.value.component_failures = ()
    with pytest.raises(AttributeError):
        caught.value._component_failures = ()


@pytest.mark.parametrize(
    "exception",
    [
        RuntimeError("apparently_safe_but_unknown"),
        module.quotes.QuoteCollectionError("unregistered_private_token_0123456789"),
        module.quotes.QuoteCollectionError("secret token=private\nremote body"),
        module.quotes.QuoteCollectionError("x" * 81),
        module.quotes.QuoteCollectionError("valid_code", "secret extra argument"),
    ],
)
@pytest.mark.asyncio
async def test_unknown_or_unsafe_source_error_details_are_never_retained(
    monkeypatch, exception
):
    blocked_leaves(monkeypatch, failing=0, failure_error=exception)
    with pytest.raises(ERROR) as caught:
        await blocked_capture()
    assert str(caught.value) == "public_component_capture_failed"
    assert caught.value.component_failures == (("quote", "component_failed"),)


@pytest.mark.asyncio
async def test_error_args_object_is_not_stringified(monkeypatch):
    invoked = []

    class Secret:
        def __str__(self):
            invoked.append(True)
            return "credential"

    blocked_leaves(
        monkeypatch,
        failing=0,
        failure_error=module.quotes.QuoteCollectionError(Secret()),
    )
    with pytest.raises(ERROR) as caught:
        await blocked_capture()
    assert caught.value.component_failures == (("quote", "component_failed"),)
    assert not invoked


@pytest.mark.parametrize(
    "role,error_type,valid,cross_role",
    [
        (
            "quote",
            module.quotes.QuoteCollectionError,
            "future_component_timestamp",
            "book_sequence_invalid",
        ),
        (
            "candles",
            module.candles.CandleCollectionError,
            "candle_page_budget_exceeded",
            "crossed_executable_quote",
        ),
        (
            "market_aux",
            module.aux.MarketAuxCollectionError,
            "book_sequence_invalid",
            "candle_off_grid",
        ),
        (
            "ws",
            module.ws.WSCollectionError,
            "ws_transport_timeout",
            "source_decimal_invalid",
        ),
    ],
)
def test_local_code_allowlist_is_bound_to_its_component(
    role, error_type, valid, cross_role
):
    assert module._component_failure(role, error_type(valid)) == (role, valid)
    assert module._component_failure(role, error_type(cross_role)) == (
        role,
        "component_failed",
    )
    assert module._component_failure(
        role, error_type("unregistered_private_token_0123456789")
    ) == (role, "component_failed")
    assert module._component_failure(role, RuntimeError(valid)) == (
        role,
        "component_failed",
    )


@pytest.mark.parametrize("role", ["quote", "candles", "market_aux"])
def test_shared_http_code_uses_the_actual_role_error_type(role):
    expected_type = {
        "quote": module.quotes.QuoteCollectionError,
        "candles": module.candles.CandleCollectionError,
        "market_aux": module.aux.MarketAuxCollectionError,
    }[role]
    assert module._component_failure(role, expected_type("source_decimal_invalid")) == (
        role,
        "source_decimal_invalid",
    )
    assert module._component_failure(
        role, module.ws.WSCollectionError("ws_transport_timeout")
    ) == (role, "component_failed")
