"""Account-first V4 boundary tests; all account and public IO is synthetic."""

from __future__ import annotations

import asyncio
import hashlib
from types import SimpleNamespace

import httpx
import pytest

from app.trade_qualification import demo_initial_public_source_v4 as initial
from app.trade_qualification import demo_operational_route_v4 as route_v4
from app.trade_qualification.demo_public_origin import reviewed_demo_public_route
from tests.unit.research.test_demo_operational_route_v4 import _synthetic_lease
from tests.unit.research.test_demo_session_route_preflight import _client, _session

_BASE = 1_760_000_000_000_000_000


def _synthetic_account_first(
    monkeypatch, session, private_client, *, inside_probe=False
):
    account = None if inside_probe else _synthetic_lease(session, private_client)

    async def probe(_session, _client):
        return _synthetic_lease(session, private_client) if inside_probe else account

    samples = iter((100, 200, 300, 400, 500, 600))

    def stamp():
        value = next(samples)
        return {"utc_ns": _BASE + value, "monotonic_ns": 100 + value}

    monkeypatch.setattr(route_v4, "probe_initial_demo_operational_route_v4", probe)
    monkeypatch.setattr(
        route_v4,
        "native_stamp",
        lambda: {"utc_ns": _BASE + 50, "monotonic_ns": 150},
    )
    monkeypatch.setattr(initial, "native_stamp", stamp)
    monkeypatch.setattr(initial, "native_os_clock", dict)
    monkeypatch.setattr(initial, "validate_os_clock", lambda _value: None)
    return account


@pytest.mark.asyncio
async def test_account_probe_failure_never_constructs_public_client(monkeypatch):
    calls = 0

    async def rejected(_session, _client):
        raise route_v4.DemoOperationalRouteV4Error("demo_v4_config_invalid")

    def public_client():
        nonlocal calls
        calls += 1
        raise AssertionError("public client must not be constructed")

    monkeypatch.setattr(route_v4, "probe_initial_demo_operational_route_v4", rejected)
    monkeypatch.setattr(initial, "_time_client", public_client)
    with pytest.raises(
        initial.InitialDemoPublicSourceV4Error,
        match="demo_v4_initial_public_time_unavailable",
    ):
        await initial.capture_initial_demo_public_time_v4(_session(), _client())
    assert calls == 0


@pytest.mark.asyncio
async def test_one_use_account_lease_and_mock_public_transport_fail_before_send(
    monkeypatch,
):
    session, private_client = _session(), _client()
    _synthetic_account_first(monkeypatch, session, private_client)
    sends = 0
    clients = 0

    def public_client():
        nonlocal clients
        clients += 1

        def handle(_request):
            nonlocal sends
            sends += 1
            return httpx.Response(200, json={"code": "0", "msg": "", "data": []})

        return httpx.AsyncClient(
            transport=httpx.MockTransport(handle),
            trust_env=False,
            follow_redirects=False,
        )

    monkeypatch.setattr(initial, "_time_client", public_client)
    with pytest.raises(initial.InitialDemoPublicSourceV4Error):
        await initial.capture_initial_demo_public_time_v4(session, private_client)
    assert (clients, sends) == (1, 0)
    with pytest.raises(initial.InitialDemoPublicSourceV4Error):
        await initial.capture_initial_demo_public_time_v4(session, private_client)
    assert (clients, sends) == (1, 0)


@pytest.mark.asyncio
async def test_hidden_pool_proxy_is_rejected_before_public_send(monkeypatch):
    session, private_client = _session(), _client()
    _synthetic_account_first(monkeypatch, session, private_client)
    client = initial._time_client()
    client._transport._pool._proxy = object()
    sends = 0

    async def send(*_args, **_kwargs):
        nonlocal sends
        sends += 1
        raise AssertionError("proxy client must not send")

    monkeypatch.setattr(client, "send", send)
    monkeypatch.setattr(initial, "_time_client", lambda: client)
    with pytest.raises(initial.InitialDemoPublicSourceV4Error):
        await initial.capture_initial_demo_public_time_v4(session, private_client)
    assert sends == 0


@pytest.mark.asyncio
async def test_changed_route_after_client_enter_is_rejected_before_send(monkeypatch):
    session, private_client = _session(), _client()
    _synthetic_account_first(monkeypatch, session, private_client)
    original_checked = route_v4._checked_objects
    checks = 0
    sends = 0

    def changed_on_final_check(_session, _private_client):
        nonlocal checks
        checks += 1
        if checks == 3:
            return (
                SimpleNamespace(origin="https://eea.okx.com"),
                reviewed_demo_public_route("eea"),
            )
        return original_checked(_session, _private_client)

    client = initial._time_client()

    async def send(*_args, **_kwargs):
        nonlocal sends
        sends += 1
        raise AssertionError("changed account route must not send")

    monkeypatch.setattr(route_v4, "_checked_objects", changed_on_final_check)
    monkeypatch.setattr(client, "send", send)
    monkeypatch.setattr(initial, "_time_client", lambda: client)
    with pytest.raises(initial.InitialDemoPublicSourceV4Error):
        await initial.capture_initial_demo_public_time_v4(session, private_client)
    assert checks == 3
    assert sends == 0


@pytest.mark.asyncio
async def test_clock_change_after_client_enter_is_rejected_before_send(monkeypatch):
    session, private_client = _session(), _client()
    _synthetic_account_first(monkeypatch, session, private_client)
    checks = 0
    sends = 0

    def clock_check(_value):
        nonlocal checks
        checks += 1
        if checks == 2:
            raise ValueError("synthetic clock changed")

    client = initial._time_client()

    async def send(*_args, **_kwargs):
        nonlocal sends
        sends += 1
        raise AssertionError("changed clock must not send")

    monkeypatch.setattr(initial, "validate_os_clock", clock_check)
    monkeypatch.setattr(client, "send", send)
    monkeypatch.setattr(initial, "_time_client", lambda: client)
    with pytest.raises(initial.InitialDemoPublicSourceV4Error):
        await initial.capture_initial_demo_public_time_v4(session, private_client)
    assert checks == 2
    assert sends == 0


@pytest.mark.asyncio
async def test_cancellation_before_account_probe_returns_has_no_public_io(monkeypatch):
    entered = asyncio.Event()
    public_clients = 0

    async def probe(_session, _client):
        entered.set()
        await asyncio.Event().wait()

    def public_client():
        nonlocal public_clients
        public_clients += 1
        raise AssertionError("cancelled account probe must not open public client")

    monkeypatch.setattr(route_v4, "probe_initial_demo_operational_route_v4", probe)
    monkeypatch.setattr(initial, "_time_client", public_client)
    task = asyncio.create_task(
        initial.capture_initial_demo_public_time_v4(_session(), _client())
    )
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert public_clients == 0


@pytest.mark.asyncio
async def test_cancellation_after_lease_consume_before_public_send_closes_client(
    monkeypatch,
):
    session, private_client = _session(), _client()
    _synthetic_account_first(monkeypatch, session, private_client, inside_probe=True)
    entered = asyncio.Event()

    class BlockingClient:
        closed = False
        sends = 0

        async def __aenter__(self):
            entered.set()
            await asyncio.Event().wait()
            return self

        async def aclose(self):
            self.closed = True

        async def send(self, *_args, **_kwargs):
            self.sends += 1
            raise AssertionError("cancelled pre-send client must not send")

    client = BlockingClient()
    monkeypatch.setattr(initial, "_time_client", lambda: client)
    task = asyncio.create_task(
        initial.capture_initial_demo_public_time_v4(session, private_client)
    )
    await asyncio.wait_for(entered.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 2)
    assert client.closed is True
    assert client.sends == 0


@pytest.mark.asyncio
async def test_client_enter_deadline_closes_without_public_send(monkeypatch):
    session, private_client = _session(), _client()
    _synthetic_account_first(monkeypatch, session, private_client)

    class HangingClient:
        closed = False
        sends = 0

        async def __aenter__(self):
            await asyncio.Event().wait()

        async def aclose(self):
            self.closed = True

        async def send(self, *_args, **_kwargs):
            self.sends += 1
            raise AssertionError("expired enter must not send")

    client = HangingClient()
    monkeypatch.setattr(initial, "_time_client", lambda: client)
    monkeypatch.setattr(initial, "_MAX_PUBLIC_WALL_SECONDS", 0.05)
    with pytest.raises(initial.InitialDemoPublicSourceV4Error):
        await asyncio.wait_for(
            initial.capture_initial_demo_public_time_v4(session, private_client), 1
        )
    assert client.closed and client.sends == 0
    assert route_v4._PROBES.get(session) is route_v4._SPENT


def _synthetic_public_body(monkeypatch, body):
    """Test orchestration only; native transport rejection is tested separately."""
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            200, headers={"content-type": "application/json"}, stream=body
        )

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        trust_env=False,
        follow_redirects=False,
    )
    monkeypatch.setattr(initial, "_time_client", lambda: client)
    monkeypatch.setattr(initial.account_io, "_client_guard", lambda _client: None)
    monkeypatch.setattr(initial.time_diag, "_client_context", lambda _client: object())
    monkeypatch.setattr(
        initial.time_diag,
        "_tls",
        lambda _response, _context, _hostname: {"peer_sha256": "a" * 64},
    )
    # Account validates at BASE. Public server time is BASE+4 ms and must fall
    # between the actual dispatch (3 ms) and headers receipt (5 ms).
    samples = iter((1, 2, 3, 5, 6, 7))

    def stamp():
        offset = next(samples) * 1_000_000
        return {"utc_ns": _BASE + offset, "monotonic_ns": 100 + offset}

    monkeypatch.setattr(initial, "native_stamp", stamp)
    return client, requests


class _CompleteTimeBody(httpx.AsyncByteStream):
    closed = False
    raw = b'{"code":"0","msg":"","data":[{"ts":"1760000000004"}]}'

    async def __aiter__(self):
        yield self.raw

    async def aclose(self):
        self.closed = True


@pytest.mark.asyncio
async def test_complete_synthetic_time_replay_retains_exact_bytes_and_denies_authority(
    monkeypatch,
):
    session, private_client = _session(), _client()
    _synthetic_account_first(monkeypatch, session, private_client)
    body = _CompleteTimeBody()
    client, requests = _synthetic_public_body(monkeypatch, body)
    result = await initial.capture_initial_demo_public_time_v4(session, private_client)
    assert body.closed and client.is_closed
    assert len(requests) == 1
    assert requests[0].headers["x-simulated-trading"] == "1"
    assert not any(name.startswith("ok-access-") for name in requests[0].headers)
    assert result.public_raw_body == body.raw
    assert result.public_raw_sha256 == hashlib.sha256(body.raw).hexdigest()
    assert result.server_time_ms == 1_760_000_000_004
    assert result.account_validated_ns < result.request_start_ns
    assert result.request_start_ns <= result.server_time_ms * 1_000_000
    assert result.server_time_ms * 1_000_000 <= result.headers_received_ns
    assert result.durable_custody is False
    assert result.public_source_authenticity_verified is False
    assert result.execution_authority is False
    assert result.admission == "DENY"


@pytest.mark.asyncio
async def test_public_body_wall_deadline_closes_and_never_returns_partial_record(
    monkeypatch,
):
    session, private_client = _session(), _client()
    _synthetic_account_first(monkeypatch, session, private_client)

    class SlowBody(_CompleteTimeBody):
        async def __aiter__(self):
            yield b"{"
            while True:
                await asyncio.sleep(0.01)
                yield b" "

    body = SlowBody()
    client, requests = _synthetic_public_body(monkeypatch, body)
    monkeypatch.setattr(initial, "_MAX_PUBLIC_WALL_SECONDS", 0.05)
    with pytest.raises(initial.InitialDemoPublicSourceV4Error):
        await asyncio.wait_for(
            initial.capture_initial_demo_public_time_v4(session, private_client), 1
        )
    assert len(requests) == 1
    assert body.closed and client.is_closed
    assert route_v4._PROBES.get(session) is route_v4._SPENT


@pytest.mark.asyncio
async def test_late_public_close_failure_denies_otherwise_complete_record(monkeypatch):
    session, private_client = _session(), _client()
    _synthetic_account_first(monkeypatch, session, private_client)
    body = _CompleteTimeBody()
    client, requests = _synthetic_public_body(monkeypatch, body)
    original_close = client.aclose

    async def close_then_fail():
        await original_close()
        raise RuntimeError("synthetic close failure")

    monkeypatch.setattr(client, "aclose", close_then_fail)
    with pytest.raises(initial.InitialDemoPublicSourceV4Error):
        await initial.capture_initial_demo_public_time_v4(session, private_client)
    assert len(requests) == 1
    assert body.closed and client.is_closed
    assert route_v4._PROBES.get(session) is route_v4._SPENT


@pytest.mark.asyncio
async def test_cancellation_during_public_body_closes_response_without_observation(
    monkeypatch,
):
    session, private_client = _session(), _client()
    _synthetic_account_first(monkeypatch, session, private_client, inside_probe=True)
    entered = asyncio.Event()
    sends = 0

    class HangingBody(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self):
            entered.set()
            await asyncio.Event().wait()
            yield b'{"code":"0","msg":"","data":[{"ts":"1760000000000"}]}'

        async def aclose(self):
            self.closed = True

    body = HangingBody()

    def handler(_request):
        nonlocal sends
        sends += 1
        return httpx.Response(
            200, headers={"content-type": "application/json"}, stream=body
        )

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        trust_env=False,
        follow_redirects=False,
    )
    monkeypatch.setattr(initial, "_time_client", lambda: client)
    # Synthetic test seam only: the production issuer requires a native pool
    # and actual TLS peer, as separately tested above.
    monkeypatch.setattr(initial.account_io, "_client_guard", lambda _client: None)
    monkeypatch.setattr(initial.time_diag, "_client_context", lambda _client: object())
    monkeypatch.setattr(
        initial.time_diag,
        "_tls",
        lambda _response, _context, _hostname: {"peer_sha256": "a" * 64},
    )
    task = asyncio.create_task(
        initial.capture_initial_demo_public_time_v4(session, private_client)
    )
    await asyncio.wait_for(entered.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 2)
    assert sends == 1
    assert body.closed is True
    assert client.is_closed


@pytest.mark.parametrize(
    "account_ns, start_ns, server_ns",
    [
        (_BASE, _BASE + 2_000_000, _BASE + 3_000_000),
        (_BASE + 2_000_000, _BASE + 2_000_000, _BASE + 3_000_000),
        (
            _BASE - initial._MAX_ACCOUNT_TO_PUBLIC_NS,
            _BASE + 2_000_000,
            _BASE + 3_000_000,
        ),
        (_BASE, _BASE + 2_000_000, _BASE + 1_000_000),
        (_BASE, _BASE + 2_000_000, _BASE + 5_000_000),
    ],
)
def test_public_request_requires_fresh_account_and_causal_exchange_time(
    account_ns, start_ns, server_ns
):
    account = SimpleNamespace(validated_ns=account_ns)
    consumed = {"utc_ns": _BASE + 1_000_000, "monotonic_ns": 1_000_000}
    started = {"utc_ns": start_ns, "monotonic_ns": 2_000_000}
    headers = {"utc_ns": _BASE + 4_000_000, "monotonic_ns": 4_000_000}
    body = {"utc_ns": _BASE + 5_000_000, "monotonic_ns": 5_000_000}
    validated = {"utc_ns": _BASE + 6_000_000, "monotonic_ns": 6_000_000}
    if account_ns == _BASE and server_ns == _BASE + 3_000_000:
        initial._causal_time(
            account, consumed, started, headers, body, validated, server_ns // 1_000_000
        )
    else:
        with pytest.raises(initial.InitialDemoPublicSourceV4Error):
            initial._causal_time(
                account,
                consumed,
                started,
                headers,
                body,
                validated,
                server_ns // 1_000_000,
            )


def test_return_type_has_no_source_or_trading_authority():
    observation = initial.InitialDemoPublicTimeObservationV4(
        route_region="global",
        account_config_raw_sha256="a" * 64,
        account_tls_peer_sha256="b" * 64,
        public_raw_body=b"{}",
        public_raw_sha256="c" * 64,
        public_canonical_sha256="d" * 64,
        public_tls_peer_sha256="e" * 64,
        account_validated_ns=1,
        lease_consumed_ns=2,
        request_start_ns=3,
        headers_received_ns=4,
        body_completed_ns=5,
        validated_ns=6,
        server_time_ms=0,
    )
    assert observation.registration_site == "UNKNOWN"
    assert observation.durable_custody is False
    assert observation.public_source_authenticity_verified is False
    assert observation.account_complete is False
    assert observation.post_g12_eligible is False
    assert observation.execution_authority is False
    assert observation.admission == "DENY"
