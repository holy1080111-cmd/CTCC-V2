"""Offline V4 account-first route tests; no credentialed network requests."""

from __future__ import annotations

import asyncio
import json
import os
from threading import get_ident
from weakref import ref

import httpx
import pytest

from app.domain.source_primitives import sha
from app.trade_qualification import demo_operational_route_v4 as route_v4
from tests.unit.research.test_demo_session_route_preflight import _client, _session


def _config(*, uid="123456", main_uid="654321", **changes):
    row = {
        "uid": uid,
        "mainUid": main_uid,
        "acctLv": "2",
        "posMode": "net_mode",
    }
    row.update(changes)
    return json.dumps({"code": "0", "msg": "", "data": [row]}).encode()


def test_config_requires_exact_uid_main_uid_and_modes():
    raw = _config()
    route_v4._decode_accepted_config(
        raw, expected_uid="123456", expected_main_uid="654321"
    )
    for payload in (
        _config(uid="654321", main_uid="123456"),
        _config(uid="123456", main_uid="123456"),
        _config(posMode="unexpected"),
        _config(acctLv=""),
        b'{"code":"0","msg":"","data":[{"uid":"123456","uid":"123456"}]}',
    ):
        with pytest.raises(route_v4.DemoOperationalRouteV4Error):
            route_v4._decode_accepted_config(
                payload, expected_uid="123456", expected_main_uid="654321"
            )


@pytest.mark.parametrize("code", ["50110", "50119"])
def test_only_fixed_401_auth_codes_escape_bounded_body(code):
    raw = json.dumps(
        {"code": code, "msg": "sensitive IP text must not escape", "data": []}
    ).encode()
    assert route_v4._read_only_auth_rejection(401, raw) == code
    assert route_v4._read_only_auth_rejection(200, raw) is None
    assert route_v4._read_only_auth_rejection(401, raw + b" " * 4096) is None
    assert route_v4._read_only_auth_rejection(401, b"not json") is None
    assert (
        route_v4._read_only_auth_rejection(401, b'{"code":"50110","code":"50119"}')
        is None
    )


@pytest.mark.asyncio
async def test_mock_transport_cannot_issue_operational_lease(monkeypatch):
    calls = 0
    clients = 0

    def handler(_request):
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"code": "0", "msg": "", "data": []})

    monkeypatch.setattr(route_v4, "native_os_clock", dict)
    monkeypatch.setattr(route_v4, "validate_os_clock", lambda _value: None)
    monkeypatch.setattr(
        route_v4,
        "native_stamp",
        lambda: {"utc_ns": 1_760_000_000_000_000_000, "monotonic_ns": 100},
    )

    def client_factory():
        nonlocal clients
        clients += 1
        return httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            trust_env=False,
            follow_redirects=False,
        )

    monkeypatch.setattr(route_v4, "_native_client", client_factory)
    session, private_client = _session(), _client()
    with pytest.raises(
        route_v4.DemoOperationalRouteV4Error, match="demo_v4_native_tls_required"
    ):
        await route_v4.probe_initial_demo_operational_route_v4(session, private_client)
    assert calls == 0
    assert clients == 1
    with pytest.raises(
        route_v4.DemoOperationalRouteV4Error,
        match="demo_v4_session_already_probed",
    ):
        await route_v4.probe_initial_demo_operational_route_v4(session, private_client)
    assert clients == 1


def _synthetic_lease(session, private_client):
    """Exercise the consume boundary without pretending a mock GET is native."""
    task = asyncio.current_task()
    assert task is not None
    stamp = {"utc_ns": 1_760_000_000_000_000_000, "monotonic_ns": 100}
    raw = _config()
    lease = route_v4._InitialRouteLeaseV4(route_v4._ISSUER)
    route_v4._LEASES[lease] = {
        "task": ref(task),
        "loop": asyncio.get_running_loop(),
        "pid": os.getpid(),
        "thread": get_ident(),
        "session": ref(session),
        "client": ref(private_client),
        "session_binding_id": session._plan.session_binding_id,
        "credential_pin": session._credential_pin,
        "plan_sha256": session._pin,
        "origin": session._plan.origin,
        "uid": session._plan.expected_uid,
        "main_uid": session._plan.expected_main_uid,
        "validated": stamp,
        "raw": raw,
        "peer_sha256": "a" * 64,
        "request_start_ns": stamp["utc_ns"],
        "headers_received_ns": stamp["utc_ns"],
        "body_completed_ns": stamp["utc_ns"],
        "validated_ns": stamp["utc_ns"],
    }
    route_v4._PROBES[session] = lease
    observation = route_v4.InitialOperationalRouteObservationV4(
        _lease=lease,
        route_region="global",
        config_raw_sha256=sha(raw),
        tls_peer_sha256="a" * 64,
        request_start_ns=stamp["utc_ns"],
        headers_received_ns=stamp["utc_ns"],
        body_completed_ns=stamp["utc_ns"],
        validated_ns=stamp["utc_ns"],
    )
    route_v4._LEASES[lease]["observation"] = ref(observation)
    return observation


def _native_config_fixture(monkeypatch, body):
    """Keep native-client guard real; replace only network and TLS test seams."""
    client = route_v4._native_client()
    context = client._transport._pool._ssl_context

    class SyntheticTLS:
        server_hostname = "openapi.okx.com"

        def __init__(self):
            self.context = context

        def version(self):
            return "TLSv1.3"

        def getpeercert(self, *, binary_form):
            assert binary_form is True
            return b"synthetic-test-certificate"

    tls = SyntheticTLS()

    class NetworkStream:
        def get_extra_info(self, name):
            return tls if name == "ssl_object" else None

    response = httpx.Response(
        200,
        headers={"content-type": "application/json"},
        stream=body,
        extensions={"network_stream": NetworkStream()},
    )

    async def send(_request, **_kwargs):
        response._request = _request
        return response

    monkeypatch.setattr(client, "send", send)
    monkeypatch.setattr(route_v4, "_native_client", lambda: client)
    monkeypatch.setattr(route_v4.ssl, "SSLObject", SyntheticTLS)
    monkeypatch.setattr(route_v4, "native_os_clock", dict)
    monkeypatch.setattr(route_v4, "validate_os_clock", lambda _value: None)
    monkeypatch.setattr(
        route_v4,
        "native_stamp",
        lambda: {"utc_ns": 1_760_000_000_000_000_000, "monotonic_ns": 100},
    )
    return client, response


@pytest.mark.asyncio
async def test_slow_drip_config_body_hits_full_wall_deadline_and_closes(monkeypatch):
    class SlowBody(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self):
            yield b"{"
            for _ in range(20):
                await asyncio.sleep(0.015)
                yield b" "

        async def aclose(self):
            self.closed = True

    body = SlowBody()
    client, response = _native_config_fixture(monkeypatch, body)
    monkeypatch.setattr(route_v4, "_MAX_CONFIG_WALL_SECONDS", 0.05)
    session, private_client = _session(), _client()
    with pytest.raises(route_v4.DemoOperationalRouteV4Error):
        await route_v4.probe_initial_demo_operational_route_v4(session, private_client)
    assert body.closed and response.is_closed and client.is_closed
    with pytest.raises(
        route_v4.DemoOperationalRouteV4Error,
        match="demo_v4_session_already_probed",
    ):
        await route_v4.probe_initial_demo_operational_route_v4(session, private_client)


@pytest.mark.asyncio
async def test_cancelled_config_body_shield_closes_response_and_client(monkeypatch):
    entered = asyncio.Event()

    class HangingBody(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self):
            entered.set()
            await asyncio.Event().wait()
            yield _config()

        async def aclose(self):
            self.closed = True

    body = HangingBody()
    client, response = _native_config_fixture(monkeypatch, body)
    session, private_client = _session(), _client()
    task = asyncio.create_task(
        route_v4.probe_initial_demo_operational_route_v4(session, private_client)
    )
    await asyncio.wait_for(entered.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 2)
    assert body.closed and response.is_closed and client.is_closed
    assert route_v4._PROBES.get(session) is route_v4._STARTED


@pytest.mark.asyncio
async def test_client_close_failure_denies_even_valid_config(monkeypatch):
    class OneBody(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self):
            yield _config()

        async def aclose(self):
            self.closed = True

    body = OneBody()
    client, response = _native_config_fixture(monkeypatch, body)
    original_close = client.aclose

    async def close_then_fail():
        await original_close()
        raise RuntimeError("synthetic late close failure")

    monkeypatch.setattr(client, "aclose", close_then_fail)
    session, private_client = _session(), _client()
    with pytest.raises(
        route_v4.DemoOperationalRouteV4Error, match="demo_v4_cleanup_failed"
    ):
        await route_v4.probe_initial_demo_operational_route_v4(session, private_client)
    assert body.closed and response.is_closed and client.is_closed
    assert route_v4._PROBES.get(session) is route_v4._STARTED


@pytest.mark.asyncio
async def test_initial_lease_is_single_use_same_task_and_never_post_g12(monkeypatch):
    session, private_client = _session(), _client()
    monkeypatch.setattr(
        route_v4,
        "native_stamp",
        lambda: {"utc_ns": 1_760_000_000_000_000_100, "monotonic_ns": 200},
    )
    observation = _synthetic_lease(session, private_client)
    assert observation.registration_site == "UNKNOWN"
    assert observation.post_g12_eligible is False
    assert observation.public_source_authenticity_verified is False
    assert observation.execution_authority is False
    assert observation.admission == "DENY"
    assert (
        route_v4._consume_initial_route_v4(
            observation, session, private_client
        ).rest_origin
        == session._plan.origin
    )
    with pytest.raises(route_v4.DemoOperationalRouteV4Error):
        route_v4._consume_initial_route_v4(observation, session, private_client)

    foreign_session, foreign_client = _session(), _client()
    foreign = _synthetic_lease(foreign_session, foreign_client)

    async def consume_elsewhere():
        route_v4._consume_initial_route_v4(foreign, foreign_session, foreign_client)

    with pytest.raises(route_v4.DemoOperationalRouteV4Error):
        await asyncio.create_task(consume_elsewhere())
    with pytest.raises(route_v4.DemoOperationalRouteV4Error):
        route_v4._consume_initial_route_v4(foreign, foreign_session, foreign_client)


@pytest.mark.asyncio
async def test_expired_or_changed_lease_is_burned(monkeypatch):
    session, private_client = _session(), _client()
    monkeypatch.setattr(
        route_v4,
        "native_stamp",
        lambda: {
            "utc_ns": 1_760_000_000_000_000_000 + route_v4._LEASE_NS,
            "monotonic_ns": 100 + route_v4._LEASE_NS,
        },
    )
    expired = _synthetic_lease(session, private_client)
    with pytest.raises(route_v4.DemoOperationalRouteV4Error):
        route_v4._consume_initial_route_v4(expired, session, private_client)
    with pytest.raises(route_v4.DemoOperationalRouteV4Error):
        route_v4._consume_initial_route_v4(expired, session, private_client)

    monkeypatch.setattr(
        route_v4,
        "native_stamp",
        lambda: {"utc_ns": 1_760_000_000_000_000_100, "monotonic_ns": 200},
    )
    changed_session, changed_client = _session(), _client()
    changed = _synthetic_lease(changed_session, changed_client)
    changed_session._used = True
    with pytest.raises(route_v4.DemoOperationalRouteV4Error):
        route_v4._consume_initial_route_v4(changed, changed_session, changed_client)


@pytest.mark.asyncio
async def test_caller_cannot_mint_or_repackage_route_lease(monkeypatch):
    with pytest.raises(route_v4.DemoOperationalRouteV4Error):
        route_v4._InitialRouteLeaseV4(object())
    session, private_client = _session(), _client()
    monkeypatch.setattr(
        route_v4,
        "native_stamp",
        lambda: {"utc_ns": 1_760_000_000_000_000_100, "monotonic_ns": 200},
    )
    original = _synthetic_lease(session, private_client)
    forged = route_v4.InitialOperationalRouteObservationV4(
        _lease=original._lease,
        route_region=original.route_region,
        config_raw_sha256=original.config_raw_sha256,
        tls_peer_sha256=original.tls_peer_sha256,
        request_start_ns=original.request_start_ns,
        headers_received_ns=original.headers_received_ns,
        body_completed_ns=original.body_completed_ns,
        validated_ns=original.validated_ns,
    )
    with pytest.raises(route_v4.DemoOperationalRouteV4Error):
        route_v4._consume_initial_route_v4(forged, session, private_client)
    with pytest.raises(route_v4.DemoOperationalRouteV4Error):
        route_v4._consume_initial_route_v4(original, session, private_client)

    mutated_session, mutated_client = _session(), _client()
    mutated = _synthetic_lease(mutated_session, mutated_client)
    object.__setattr__(mutated, "tls_peer_sha256", "b" * 64)
    with pytest.raises(route_v4.DemoOperationalRouteV4Error):
        route_v4._consume_initial_route_v4(mutated, mutated_session, mutated_client)

    clock_session, clock_client = _session(), _client()
    clock_mutated = _synthetic_lease(clock_session, clock_client)
    object.__setattr__(clock_mutated, "headers_received_ns", 42)
    with pytest.raises(route_v4.DemoOperationalRouteV4Error):
        route_v4._consume_initial_route_v4(clock_mutated, clock_session, clock_client)
