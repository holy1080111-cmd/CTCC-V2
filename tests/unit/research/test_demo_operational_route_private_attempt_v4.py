"""Private failed native-route attempts over synthetic transport/TLS seams."""

import asyncio

import httpx
import pytest

from app.domain.source_primitives import decode, sha
from app.trade_qualification import demo_operational_route_v4 as route
from tests.unit.research.test_demo_operational_route_v4 import (
    _config,
    _native_config_fixture,
)
from tests.unit.research.test_demo_session_route_preflight import KEY, _client, _session
from tests.unit.research.test_public_journal_contracts import MemoryDirectory


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", ["malformed", "timeout", "late_cleanup", "rejected"]
)
async def test_failed_probe_retains_private_bounded_observed_bytes_without_lease(
    monkeypatch, failure
):
    session, private_client = _session(), _client()
    payload = (
        b'{"broken"'
        if failure == "malformed"
        else b'{"code":"0",'
        if failure == "timeout"
        else b'{"code":"50110","msg":"private rejection detail","data":[]}'
        if failure == "rejected"
        else _config(
            uid=session._plan.expected_uid, main_uid=session._plan.expected_main_uid
        )
    )

    class Body(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self):
            yield payload
            if failure == "timeout":
                await asyncio.Event().wait()

        async def aclose(self):
            self.closed = True

    body = Body()
    client, response = _native_config_fixture(monkeypatch, body)
    if failure == "timeout":
        monkeypatch.setattr(route, "_MAX_CONFIG_WALL_SECONDS", 0.03)
    if failure == "rejected":
        response.status_code = 401
    if failure == "late_cleanup":
        original_close = client.aclose

        async def late_close():
            await original_close()
            raise RuntimeError("synthetic late failure")

        monkeypatch.setattr(client, "aclose", late_close)
    directory = MemoryDirectory({})
    monkeypatch.setattr(route, "_native_attempt_directory", lambda value: value)
    with pytest.raises(route.DemoOperationalRouteV4Error):
        await route._probe_initial_demo_operational_route_retained_v4(
            session, private_client, proof_directory=directory
        )
    receipt = decode(directory.content["account-route-attempt.v4.json"])
    assert receipt["disposition"] == "rejected"
    assert receipt["raw_retained"] is True
    assert directory.content["account-route-attempt.v4.raw"] == payload
    assert receipt["observed_sha256"] == receipt["retained_sha256"] == sha(payload)
    assert receipt["observed_bytes"] == len(payload)
    assert receipt["body_stream_exhausted"] is (failure != "timeout")
    assert receipt["source_authenticity_verified"] is False
    assert (
        receipt["account_complete"] is False and receipt["execution_authority"] is False
    )
    if failure == "timeout":
        assert receipt["code"] == "timeout"
        assert receipt["body_completed"] is None
    assert body.closed and response.is_closed and client.is_closed
    assert not route._PRIVATE_ATTEMPTS and not route._OWNED_ROUTES
    assert type(route._PROBES.get(session)) is not route._InitialRouteLeaseV4


@pytest.mark.asyncio
@pytest.mark.parametrize("echo", ["api_key", "signature"])
async def test_credential_echo_omits_private_raw_and_headers_but_preserves_digest(
    monkeypatch, echo
):
    session, private_client = _session(), _client()
    payload = b""

    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            nonlocal payload
            token = (
                KEY
                if echo == "api_key"
                else route._PRIVATE_ATTEMPTS[session]["tokens"][-1]
            )
            payload = b'{"echo":"' + token.encode("ascii") + b'"}'
            yield payload

        async def aclose(self):
            pass

    _client_instance, response = _native_config_fixture(monkeypatch, Body())
    response.headers["date"] = KEY
    directory = MemoryDirectory({})
    monkeypatch.setattr(route, "_native_attempt_directory", lambda value: value)
    with pytest.raises(route.DemoOperationalRouteV4Error):
        await route._probe_initial_demo_operational_route_retained_v4(
            session, private_client, proof_directory=directory
        )
    receipt = decode(directory.content["account-route-attempt.v4.json"])
    assert receipt["raw_retained"] is False and receipt["raw_name"] is None
    assert receipt["code"] == "credential_safety_raw_omitted"
    assert receipt["observed_sha256"] == sha(payload)
    assert receipt["observed_bytes"] == len(payload)
    assert receipt["omitted_header_count"] == 1
    assert "account-route-attempt.v4.raw" not in directory.content
    assert all(
        KEY.encode() not in raw and payload not in raw
        for raw in directory.content.values()
    )
    assert not route._PRIVATE_ATTEMPTS and not route._OWNED_ROUTES


@pytest.mark.asyncio
async def test_cancelled_probe_preserves_safe_prefix_and_propagates_cancel(monkeypatch):
    session, private_client = _session(), _client()
    entered = asyncio.Event()
    payload = b'{"code":"0",'

    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield payload
            entered.set()
            await asyncio.Event().wait()

        async def aclose(self):
            pass

    client, response = _native_config_fixture(monkeypatch, Body())
    directory = MemoryDirectory({})
    monkeypatch.setattr(route, "_native_attempt_directory", lambda value: value)
    task = asyncio.create_task(
        route._probe_initial_demo_operational_route_retained_v4(
            session, private_client, proof_directory=directory
        )
    )
    await asyncio.wait_for(entered.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 2)
    receipt = decode(directory.content["account-route-attempt.v4.json"])
    assert receipt["code"] == "cancelled" and receipt["body_stream_exhausted"] is False
    assert directory.content["account-route-attempt.v4.raw"] == payload
    assert client.is_closed and response.is_closed
    assert not route._PRIVATE_ATTEMPTS and not route._OWNED_ROUTES


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["publish", "readback"])
async def test_late_attempt_retention_failure_burns_accepted_lease(
    monkeypatch, failure
):
    session, private_client = _session(), _client()
    payload = _config(
        uid=session._plan.expected_uid, main_uid=session._plan.expected_main_uid
    )

    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield payload

        async def aclose(self):
            pass

    client, response = _native_config_fixture(monkeypatch, Body())

    class LateDirectory(MemoryDirectory):
        def publish(self, name, raw):
            super().publish(name, raw)
            if name == "account-route-attempt.v4.json" and failure == "publish":
                raise OSError("synthetic late publish failure")

        def read(self, name, maximum):
            raw = super().read(name, maximum)
            return (
                b"changed"
                if name == "account-route-attempt.v4.json" and failure == "readback"
                else raw
            )

    directory = LateDirectory({})
    monkeypatch.setattr(route, "_native_attempt_directory", lambda value: value)
    with pytest.raises(
        route.DemoOperationalRouteV4Error, match="private_attempt_storage_failed"
    ):
        await route._probe_initial_demo_operational_route_retained_v4(
            session, private_client, proof_directory=directory
        )
    assert directory.content["account-route-attempt.v4.raw"] == payload
    # A late refusal retains already durable raw/journal bytes. The accepted
    # native response does not survive as a usable route lease.
    assert "account-route-attempt.v4.json" in directory.content
    assert route._PROBES.get(session) is route._SPENT
    assert not any(state["session"]() is session for state in route._LEASES.values())
    assert not route._PRIVATE_ATTEMPTS and not route._OWNED_ROUTES
    assert client.is_closed and response.is_closed


@pytest.mark.asyncio
async def test_preexisting_private_attempt_is_preserved_and_denied_before_io(
    monkeypatch,
):
    session, private_client = _session(), _client()
    name = "account-route-attempt.v4.plan.json"
    directory = MemoryDirectory({name: b"original-private-evidence"})
    monkeypatch.setattr(route, "_native_attempt_directory", lambda value: value)

    def forbidden():
        raise AssertionError("preexisting evidence reached native I/O")

    monkeypatch.setattr(route, "_native_client", forbidden)
    with pytest.raises(
        route.DemoOperationalRouteV4Error, match="private_attempt_storage_failed"
    ):
        await route._probe_initial_demo_operational_route_retained_v4(
            session, private_client, proof_directory=directory
        )
    assert directory.content == {name: b"original-private-evidence"}
    assert not route._PRIVATE_ATTEMPTS and session not in route._PROBES
