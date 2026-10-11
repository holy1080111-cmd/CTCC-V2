"""Additive V3 time contract; no region proof, network, or source authority."""

import asyncio
import copy
import os
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from threading import get_ident
from types import SimpleNamespace

import httpx
import pytest

from app.domain import native_clock
from app.domain.source_primitives import (
    PublicReceiptError,
    canonical,
    demo_public_v2_policy_sha256,
    demo_public_v3_time_policy_sha256,
    sha,
)
from app.public_market_source import public_runtime_journal as journal
from app.trade_qualification import demo_public_origin as origin
from app.trade_qualification import public_market_collector_v2 as public_v2
from app.trade_qualification import public_source_runtime as runtime
from tests.unit.research.test_owned_public_runtime_p1 import SyntheticClock
from tests.unit.research.test_public_journal_contracts import MemoryDirectory
from tests.unit.test_qualification_public_market_collector import (
    policy as legacy_policy,
)


def _policy():
    old = legacy_policy(counts=(240, 240, 240, 240))
    return public_v2.PublicMarketCollectionPolicyV2(
        candles=old.candles, market_aux=old.market_aux, ws=old.ws
    )


def _plan():
    route = origin.reviewed_demo_public_route("global")
    invocation = "a" * 32
    now = datetime(2026, 10, 11, tzinfo=UTC)
    return {
        "schema_version": "ctcc.public.initial_runtime_plan.v3",
        "stage": "initial_public",
        "invocation_id": invocation,
        "environment": "demo",
        "report_id": "initial-" + invocation,
        "instrument_id": "BTC-USDT-SWAP",
        "invocation_started": {
            "utc_ns": int(now.timestamp()) * 1_000_000_000,
            "monotonic_ns": 0,
        },
        "expires_at": (now + timedelta(seconds=30)).isoformat(),
        "rest_origin": route.rest_origin,
        "ws_origin": route.ws_origin,
        "registration_region": "global",
        "registration_region_authenticated": False,
        "account_plan_sha256": "b" * 64,
        "demo_public_origin_policy_sha256": demo_public_v2_policy_sha256("global"),
        "policy_sha256": public_v2._policy_digest(_policy(), route),
        "exchange_time_policy_sha256": demo_public_v3_time_policy_sha256("global"),
        **public_v2._plan_pins(route),
    }


def test_v3_plan_adds_only_exact_time_pin_and_keeps_v2_plan_replay():
    plan = _plan()
    journal._validate_plan(plan)
    old = {k: v for k, v in plan.items() if k != "exchange_time_policy_sha256"}
    old["schema_version"] = "ctcc.public.initial_runtime_plan.v2"
    journal._validate_plan(old)
    with pytest.raises(PublicReceiptError):
        journal._validate_plan({**plan, "exchange_time_policy_sha256": "0" * 64})
    with pytest.raises(PublicReceiptError):
        journal._validate_plan({**old, "exchange_time_policy_sha256": "0" * 64})


@pytest.mark.parametrize("role", ["time_pre", "time_post"])
def test_v3_time_request_is_exact_public_demo_get(role):
    route = origin.reviewed_demo_public_route("global")
    request = origin.build_demo_public_time_request_v3(route, role)
    assert request.method == "GET"
    assert str(request.url) == "https://openapi.okx.com/api/v5/public/time"
    assert request.url.query == b""
    assert request.headers["x-simulated-trading"] == "1"
    assert request.headers["user-agent"] == "CTCC-source-time/1"
    assert not any(key.startswith("ok-access-") for key in request.headers)
    origin.validate_demo_public_time_response_v3(
        route, role, request, httpx.Response(200, request=request)
    )
    for mutation in (
        httpx.Request(
            "GET", str(request.url) + "?instId=BTC-USDT-SWAP", headers=request.headers
        ),
        httpx.Request("POST", str(request.url), headers=request.headers),
        httpx.Request(
            "GET",
            str(request.url),
            headers={**dict(request.headers), "ok-access-key": "x"},
        ),
    ):
        with pytest.raises(origin.DemoPublicOriginError):
            origin.validate_demo_public_time_request_v3(route, role, mutation)


@pytest.mark.parametrize(
    "raw",
    [
        b'{"code":"0","msg":"","data":[{"ts":"1791676800000"}]}',
        b'{"msg":"","data":[{"ts":"1791676800000"}],"code":"0"}',
    ],
)
def test_v3_system_time_accepts_only_canonicalizable_exact_envelope(raw):
    server_ms, digest = journal._public_system_time(raw)
    assert server_ms == "1791676800000"
    assert len(digest) == 64


@pytest.mark.parametrize(
    "raw",
    [
        b'{"code":"0","msg":"","data":[{"ts":"1791676800000"}],"code":"0"}',
        b'{"code":"0","msg":"","data":[{"ts":1791676800000}]}',
        b'{"code":"0","msg":"","data":[{"ts":"1791676800000","x":0}]}',
        b'{"code":"0","msg":"","data":[]}',
        b"x" * 4097,
    ],
)
def test_v3_system_time_rejects_malformed_or_unbounded_raw(raw):
    with pytest.raises(PublicReceiptError):
        journal._public_system_time(raw)


def _time_prefix():
    plan = _plan()
    clock = SyntheticClock(datetime(2026, 10, 11, tzinfo=UTC))
    clock_raw = canonical(
        {
            "schema_version": native_clock.CLOCK_RESULT_VERSION,
            "outcome": "accepted",
            "observation": clock.health(),
        }
    )
    assert native_clock.replay_clock_observation(clock_raw)["outcome"] == "accepted"
    started, headers, chunk, body, closed, validated = (clock.stamp() for _ in range(6))
    server_ms = str((started["utc_ns"] // 1_000_000) + 1)
    raw = canonical({"code": "0", "msg": "", "data": [{"ts": server_ms}]})
    events = [
        ("clock_before", {"outcome": "accepted"}, clock_raw),
        (
            "request",
            {
                "id": 0,
                "role": "time_pre",
                "method": "GET",
                "origin": plan["rest_origin"],
                "endpoint": "/api/v5/public/time",
                "query": [],
                "started": started,
                "request_headers": journal._routed_request_headers(plan, "time_pre"),
            },
            None,
        ),
        (
            "headers",
            {
                "id": 0,
                "received": headers,
                "status": 200,
                "tls": {
                    "classification": "owned_native_tls",
                    "hostname": "openapi.okx.com",
                    "version": "TLSv1.3",
                    "peer_sha256": "1" * 64,
                },
                "safe_headers": [
                    ["content-type", "application/json"],
                    ["content-length", str(len(raw))],
                ],
                "truncated": False,
            },
            None,
        ),
        (
            "chunk",
            {
                "id": 0,
                "received": chunk,
                "observed_bytes": len(raw),
                "observed_sha256": sha(raw),
                "truncated": False,
            },
            raw,
        ),
        (
            "body_complete",
            {
                "id": 0,
                "completed": body,
                "body_bytes": len(raw),
                "body_sha256": sha(raw),
            },
            None,
        ),
        ("response_closed", {"id": 0, "completed": closed}, None),
        (
            "time_validated",
            {
                "id": 0,
                "validated": validated,
                "server_time_ms": server_ms,
                "canonical_body_sha256": sha(raw),
            },
            None,
        ),
    ]
    return plan, clock, events


def _bracket_prefix():
    """One synthetic market request and WS lifecycle between two time roles."""
    plan, clock, events = _time_prefix()
    tls = {
        "classification": "owned_native_tls",
        "hostname": "openapi.okx.com",
        "version": "TLSv1.3",
        "peer_sha256": "1" * 64,
    }

    def http_events(request_id, role, endpoint, query, raw):
        started, headers, chunk, body, closed = (clock.stamp() for _ in range(5))
        return [
            (
                "request",
                {
                    "id": request_id,
                    "role": role,
                    "method": "GET",
                    "origin": plan["rest_origin"],
                    "endpoint": endpoint,
                    "query": query,
                    "started": started,
                    "request_headers": journal._routed_request_headers(plan, role),
                },
                None,
            ),
            (
                "headers",
                {
                    "id": request_id,
                    "received": headers,
                    "status": 200,
                    "tls": tls,
                    "safe_headers": [
                        ["content-type", "application/json"],
                        ["content-length", str(len(raw))],
                    ],
                    "truncated": False,
                },
                None,
            ),
            (
                "chunk",
                {
                    "id": request_id,
                    "received": chunk,
                    "observed_bytes": len(raw),
                    "observed_sha256": sha(raw),
                    "truncated": False,
                },
                raw,
            ),
            (
                "body_complete",
                {
                    "id": request_id,
                    "completed": body,
                    "body_bytes": len(raw),
                    "body_sha256": sha(raw),
                },
                None,
            ),
            ("response_closed", {"id": request_id, "completed": closed}, None),
        ]

    events.extend(
        http_events(
            1,
            "candles",
            "/api/v5/market/candles",
            [["instId", "BTC-USDT-SWAP"], ["bar", "4H"], ["limit", "300"]],
            b"{}",
        )
    )
    events.extend(
        [
            (
                "ws_connect",
                {"endpoint": plan["ws_origin"], "started": clock.stamp()},
                None,
            ),
            (
                "ws_connected",
                {
                    "connected": clock.stamp(),
                    "tls": {**tls, "hostname": "wspap.okx.com"},
                },
                None,
            ),
            ("ws_subscribe", {"observed": clock.stamp()}, b"{}"),
            ("ws_subscribed", {"observed": clock.stamp()}, None),
            *(
                (
                    kind,
                    {
                        "observed": clock.stamp(),
                        "message_type": "text",
                        "observed_bytes": 2,
                        "observed_sha256": sha(b"{}"),
                        "truncated": False,
                    },
                    b"{}",
                )
                for kind in ("ws_ack", "ws_ticker")
            ),
            ("ws_closed", {"observed": clock.stamp()}, None),
        ]
    )
    post_start = clock.stamp()
    post_ms = str(post_start["utc_ns"] // 1_000_000 + 1)
    post_raw = canonical({"code": "0", "msg": "", "data": [{"ts": post_ms}]})
    # Reuse the exact request/response event shape, replacing the request start
    # with the already measured stamp used to derive this response's system time.
    post = http_events(2, "time_post", "/api/v5/public/time", [], post_raw)
    post[0][1]["started"] = post_start
    events.extend(post)
    events.append(
        (
            "time_validated",
            {
                "id": 2,
                "validated": clock.stamp(),
                "server_time_ms": post_ms,
                "canonical_body_sha256": sha(post_raw),
            },
            None,
        )
    )
    return plan, events


def _semantics(plan, values, *, disposition="rejected", market_packet=None):
    return journal._replay_semantics(
        plan,
        {"disposition": disposition, "completed": None},
        [
            {"index": index, "kind": kind, "metadata": meta}
            for index, (kind, meta, _) in enumerate(values)
        ],
        [raw for _, _, raw in values],
        market_packet=market_packet,
    )


def test_v3_rejected_attempt_retains_native_labelled_time_raw_and_replays(monkeypatch):
    plan, clock, events = _time_prefix()
    directory = MemoryDirectory({})

    @contextmanager
    def root(_):
        yield directory

    monkeypatch.setattr(journal, "_root_context", root)
    with journal._runtime_attempt(object(), plan) as attempt:
        for kind, meta, raw in events:
            journal._append(attempt, kind, meta, raw)
        head, prefix = journal._replay_open_v3_prefix(attempt)
        assert len(prefix) == len(events)
        assert head == sha(directory.content["event-0006.json"])
        retained = directory.content["event-0003.raw"]
        directory.content["event-0003.raw"] = retained + b" "
        with pytest.raises(PublicReceiptError, match="runtime_v3_prefix_raw_invalid"):
            journal._replay_open_v3_prefix(attempt)
        directory.content["event-0003.raw"] = retained
        journal._seal(attempt, success=False, stamp=clock.stamp())
    summary, replayed = journal.replay_runtime_attempt(
        directory, expected_plan_sha256=sha(directory.content["plan.json"])
    )
    assert summary["disposition"] == "rejected"
    assert len(replayed) == 7
    assert directory.content["event-0003.raw"] == events[3][2]
    assert summary["execution_authority"] is False
    directory.content["event-0003.raw"] += b" "
    with pytest.raises(PublicReceiptError):
        journal.replay_runtime_attempt(
            directory, expected_plan_sha256=sha(directory.content["plan.json"])
        )


def test_v3_rejected_bracket_prefix_requires_exact_two_time_roles():
    plan, values = _bracket_prefix()
    _semantics(plan, values)
    with pytest.raises(PublicReceiptError):
        _semantics(plan, values[:7] + values[12:])  # lost candle market request
    # A rejected attempt may retain an incomplete raw tail. It cannot be sealed
    # as a captured source when the second validation receipt is absent.
    _semantics(plan, values[:-1])
    with pytest.raises(PublicReceiptError):
        _semantics(plan, values[:-1], disposition="captured")


def test_v3_open_market_prefix_requires_packet_to_join_raw_http_and_ws():
    plan, values = _bracket_prefix()
    post_index = next(
        index
        for index, (kind, meta, _) in enumerate(values)
        if kind == "request" and meta["role"] == "time_post"
    )
    _semantics(plan, values[:post_index])
    with pytest.raises(PublicReceiptError, match="runtime_v2_packet_plan_mismatch"):
        _semantics(plan, values[:post_index], market_packet=b"{}")


def test_v3_time_source_timestamp_must_precede_headers_receipt():
    plan, _, values = _time_prefix()
    values = copy.deepcopy(values)
    header_ms = values[2][1]["received"]["utc_ns"] // 1_000_000
    server_ms = str(header_ms + 1)
    assert int(server_ms) * 1_000_000 < values[4][1]["completed"]["utc_ns"]
    raw = canonical({"code": "0", "msg": "", "data": [{"ts": server_ms}]})
    values[3] = (
        "chunk",
        {
            **values[3][1],
            "observed_bytes": len(raw),
            "observed_sha256": sha(raw),
        },
        raw,
    )
    values[4][1].update(body_bytes=len(raw), body_sha256=sha(raw))
    values[6][1].update(server_time_ms=server_ms, canonical_body_sha256=sha(raw))
    with pytest.raises(PublicReceiptError, match="runtime_v3_time_validation_invalid"):
        _semantics(plan, values)


@pytest.mark.parametrize("role_id", [0, 2])
@pytest.mark.parametrize(
    "mutation",
    [
        "status",
        "unverified_tls",
        "content_type",
        "encoding",
        "length",
        "truncated",
        "duplicate_header",
    ],
)
def test_v3_rejected_attempt_cannot_claim_invalid_time_response_validated(
    role_id, mutation
):
    plan, values = _bracket_prefix()
    values = copy.deepcopy(values)
    headers = next(
        meta for kind, meta, _ in values if kind == "headers" and meta["id"] == role_id
    )
    if mutation == "status":
        headers["status"] = 503
    elif mutation == "unverified_tls":
        headers["tls"] = {
            "classification": "unverified",
            "hostname": "openapi.okx.com",
            "version": None,
            "peer_sha256": None,
        }
    elif mutation == "content_type":
        headers["safe_headers"][0][1] = "text/plain"
    elif mutation == "encoding":
        headers["safe_headers"].append(["content-encoding", "gzip"])
    elif mutation == "length":
        headers["safe_headers"][1][1] = "999"
    elif mutation == "truncated":
        headers["truncated"] = True
    else:
        headers["safe_headers"].append(["content-type", "application/json"])
    with pytest.raises(PublicReceiptError, match="runtime_v3_time_validation_invalid"):
        _semantics(plan, values)


def test_v2_replay_never_accepts_new_time_validation_event():
    plan, _, values = _time_prefix()
    plan = {k: v for k, v in plan.items() if k != "exchange_time_policy_sha256"}
    plan["schema_version"] = "ctcc.public.initial_runtime_plan.v2"
    with pytest.raises(PublicReceiptError, match="runtime_v3_time_validation_invalid"):
        _semantics(plan, [values[0], values[-1]])


@pytest.mark.parametrize(
    "mutation",
    ["pre_status", "post_status", "post_raw", "post_future", "late_market", "late_ws"],
)
def test_v3_bracket_rejects_bad_response_or_post_market_event(mutation):
    plan, values = _bracket_prefix()
    values = copy.deepcopy(values)
    if mutation == "pre_status":
        values[2][1]["status"] = 503
    elif mutation == "post_status":
        next(meta for kind, meta, _ in values if kind == "headers" and meta["id"] == 2)[
            "status"
        ] = 503
    elif mutation == "post_raw":
        index = next(
            i
            for i, (kind, meta, _) in enumerate(values)
            if kind == "chunk" and meta["id"] == 2
        )
        kind, meta, _ = values[index]
        raw = b'{"code":"0","msg":"","data":[]}'
        meta["observed_bytes"] = len(raw)
        meta["observed_sha256"] = sha(raw)
        values[index] = (kind, meta, raw)
        complete = next(
            meta
            for kind, meta, _ in values
            if kind == "body_complete" and meta["id"] == 2
        )
        complete.update(body_bytes=len(raw), body_sha256=sha(raw))
        headers = next(
            meta for kind, meta, _ in values if kind == "headers" and meta["id"] == 2
        )
        headers["safe_headers"][1][1] = str(len(raw))
    elif mutation == "post_future":
        values[-1][1]["server_time_ms"] = str(
            int(values[-1][1]["server_time_ms"]) + 1000
        )
    elif mutation == "late_market":
        later = {
            key: value + 1_000_000 for key, value in values[-1][1]["validated"].items()
        }
        values.append(
            (
                "request",
                {
                    "id": 3,
                    "role": "market_aux",
                    "method": "GET",
                    "origin": plan["rest_origin"],
                    "endpoint": "/api/v5/market/books",
                    "query": [["instId", "BTC-USDT-SWAP"], ["sz", "5"]],
                    "started": later,
                    "request_headers": journal._routed_request_headers(
                        plan, "market_aux"
                    ),
                },
                None,
            )
        )
    else:
        later = {
            key: value + 1_000_000 for key, value in values[-1][1]["validated"].items()
        }
        values.append(
            ("ws_connect", {"endpoint": plan["ws_origin"], "started": later}, None)
        )
    expected = {
        "pre_status": "runtime_headers_rejected",
        "post_status": "runtime_headers_rejected",
        "post_raw": "runtime_v3_time_body_invalid",
        "post_future": "runtime_v3_time_validation_invalid",
        "late_market": "runtime_v3_time_order_invalid",
        "late_ws": "runtime_ws_order_invalid",
    }[mutation]
    with pytest.raises(PublicReceiptError, match=expected):
        _semantics(
            plan,
            values,
            disposition="captured" if mutation.endswith("status") else "rejected",
        )


@pytest.mark.parametrize(
    "mutation",
    ["query", "headers", "tls", "future_server", "observation_stale"],
)
def test_v3_time_replay_rejects_resigned_semantic_changes(mutation):
    plan, _, values = _time_prefix()
    values = copy.deepcopy(values)
    if mutation == "query":
        values[1][1]["query"] = [["instId", "BTC-USDT-SWAP"]]
    elif mutation == "headers":
        values[1][1]["request_headers"][0][1] = "text/plain"
    elif mutation == "tls":
        values[2][1]["tls"]["hostname"] = "www.okx.com"
    elif mutation == "future_server":
        values[6][1]["server_time_ms"] = str(
            int(values[6][1]["server_time_ms"]) + 60_000
        )
    else:
        values[6][1]["validated"]["monotonic_ns"] += 6_000_000_000
        values[6][1]["validated"]["utc_ns"] += 6_000_000_000
    summary = {"disposition": "rejected", "completed": None}
    with pytest.raises(PublicReceiptError):
        journal._replay_semantics(
            plan,
            summary,
            [
                {"index": index, "kind": kind, "metadata": meta}
                for index, (kind, meta, _) in enumerate(values)
            ],
            [raw for _, _, raw in values],
        )


@pytest.mark.asyncio
async def test_v3_time_owner_still_denies_before_client_clock_or_network(monkeypatch):
    owner = runtime._Source(runtime._ISSUER)
    task = asyncio.current_task()
    runtime._SOURCES[owner] = {
        "pid": os.getpid(),
        "thread": get_ident(),
        "loop": asyncio.get_running_loop(),
        "parent": task,
        "deadline": asyncio.get_running_loop().time() + 5,
        "plan": _plan(),
    }

    def forbidden(*_args, **_kwargs):
        pytest.fail("unverified_region_reached_clock_client_or_network")

    monkeypatch.setattr(runtime, "_new_owned_client", forbidden)
    monkeypatch.setattr(runtime, "_sample", forbidden)
    try:
        with pytest.raises(
            runtime.PublicSourceRuntimeError,
            match="trusted_demo_public_origin_profile_unavailable",
        ):
            await runtime._fetch_owned_public_time_v3(owner, "time_pre")
    finally:
        runtime._SOURCES.pop(owner, None)


@pytest.mark.asyncio
async def test_v3_post_time_ignores_mutable_state_phase_injection_before_io(
    monkeypatch,
):
    owner = runtime._Source(runtime._ISSUER)
    loop = asyncio.get_running_loop()
    runtime._SOURCES[owner] = {
        "pid": os.getpid(),
        "thread": get_ident(),
        "loop": loop,
        "parent": asyncio.current_task(),
        "deadline": loop.time() + 5,
        "plan": _plan(),
        "attempt": object(),
        "time_http": {"time_pre": {"raw": b"{}"}},
        "time_request_ids": {"time_pre": 0},
        "market_capture_complete_v3": True,
        "time_pre_validated": True,
        "clients": {},
        "client_roles": set(),
    }

    def forbidden(*_args, **_kwargs):
        pytest.fail("post_time_reached_client_or_network")

    original_new_client = runtime._new_owned_client
    monkeypatch.setattr(runtime, "_fence_v2_demo_transport", lambda _state: None)
    monkeypatch.setattr(runtime, "_new_owned_client", forbidden)
    try:
        for role in ("quote", "time_post"):
            with pytest.raises(
                runtime.PublicSourceRuntimeError, match="public_v3_time_phase_invalid"
            ):
                original_new_client(owner, role)
        with pytest.raises(
            runtime.PublicSourceRuntimeError, match="public_v3_time_phase_invalid"
        ):
            runtime._ws_options(owner, datetime(2026, 10, 11, tzinfo=UTC))
        with pytest.raises(
            runtime.PublicSourceRuntimeError, match="public_v3_time_phase_invalid"
        ):
            await runtime._fetch_owned_public_time_v3(owner, "time_post")
        runtime._V3_TIME_PHASES[owner] = runtime._V3TimePhase(
            attempt=runtime._SOURCES[owner]["attempt"],
            pre_request_id=0,
            pre_validation_head="a" * 64,
        )
        with pytest.raises(
            runtime.PublicSourceRuntimeError, match="public_v3_time_phase_invalid"
        ):
            await runtime._fetch_owned_public_time_v3(owner, "time_post")
        with pytest.raises(
            runtime.PublicSourceRuntimeError, match="public_v3_market_owner_required"
        ):
            runtime._complete_owned_public_market_v3(owner, object(), _owner=object())
        validated_event = {"kind": "time_validated", "metadata": {"id": 0}}
        validated_head = sha(canonical(validated_event))
        packet = SimpleNamespace(packet_json=b"{}")
        runtime._V3_TIME_PHASES[owner] = runtime._V3TimePhase(
            attempt=runtime._SOURCES[owner]["attempt"],
            pre_request_id=0,
            pre_validation_head=validated_head,
            market_prefix_head="b" * 64,
            market_packet=packet,
            market_packet_sha256=sha(packet.packet_json),
        )
        monkeypatch.setattr(
            journal,
            "_replay_open_v3_prefix",
            lambda _attempt, *, market_packet=None: ("c" * 64, [validated_event]),
        )
        with pytest.raises(
            runtime.PublicSourceRuntimeError, match="public_v3_time_phase_changed"
        ):
            await runtime._fetch_owned_public_time_v3(owner, "time_post")
    finally:
        runtime._V3_TIME_PHASES.pop(owner, None)
        runtime._SOURCES.pop(owner, None)


@pytest.mark.asyncio
async def test_v3_valid_post_phase_reaches_client_once_without_network(monkeypatch):
    owner = runtime._Source(runtime._ISSUER)
    loop = asyncio.get_running_loop()
    attempt = object()
    packet = SimpleNamespace(packet_json=b"synthetic-packet")
    validated_event = {"kind": "time_validated", "metadata": {"id": 0}}
    prefix_head = "c" * 64
    runtime._SOURCES[owner] = {
        "pid": os.getpid(),
        "thread": get_ident(),
        "loop": loop,
        "parent": asyncio.current_task(),
        "deadline": loop.time() + 5,
        "plan": _plan(),
        "attempt": attempt,
        "time_http": {"time_pre": {"raw": b"synthetic-time"}},
        "time_request_ids": {"time_pre": 0},
        "clients": {},
        "client_roles": set(),
    }
    runtime._V3_TIME_PHASES[owner] = runtime._V3TimePhase(
        attempt=attempt,
        pre_request_id=0,
        pre_validation_head=sha(canonical(validated_event)),
        market_prefix_head=prefix_head,
        market_packet=packet,
        market_packet_sha256=sha(packet.packet_json),
    )
    calls = []
    original_new_client = runtime._new_owned_client

    def checked_client(source, role):
        calls.append(("client", role))
        return original_new_client(source, role)

    def checked_binding(_state, _packet, *, expected_roles):
        assert _packet is packet
        assert expected_roles == ("quote", "candles", "market_aux", "time_pre")
        calls.append(("bind", None))

    async def no_network(source, client, _request, _started, _policy):
        assert source is owner
        assert runtime._SOURCES[owner]["clients"][client][0] == "time_post"
        assert runtime._V3_TIME_PHASES.get(owner) is None
        calls.append(("fetch_boundary", None))
        raise runtime.PublicSourceRuntimeError("synthetic_no_network")

    async def close_client(client, _timeout, *, pending_cancel=False):
        assert pending_cancel is False
        await client.aclose()

    monkeypatch.setattr(runtime, "_fence_v2_demo_transport", lambda _state: None)
    monkeypatch.setattr(
        journal,
        "_replay_open_v3_prefix",
        lambda _attempt, *, market_packet=None: (
            prefix_head,
            [validated_event],
        ),
    )
    monkeypatch.setattr(runtime, "_check_packet_binding_v2", checked_binding)
    monkeypatch.setattr(runtime, "_new_owned_client", checked_client)
    monkeypatch.setattr(
        runtime, "_sample", lambda _source: datetime(2026, 10, 11, tzinfo=UTC)
    )
    monkeypatch.setattr(runtime, "_fetch_http", no_network)
    monkeypatch.setattr(runtime.public, "_close_client", close_client)
    try:
        with pytest.raises(
            runtime.PublicSourceRuntimeError, match="synthetic_no_network"
        ):
            await runtime._fetch_owned_public_time_v3(owner, "time_post")
        assert calls == [
            ("bind", None),
            ("client", "time_post"),
            ("bind", None),
            ("fetch_boundary", None),
        ]
        assert runtime._V3_TIME_PHASES.get(owner) is None
        with pytest.raises(
            runtime.PublicSourceRuntimeError, match="public_v3_time_role_invalid"
        ):
            await runtime._fetch_owned_public_time_v3(owner, "time_post")
        assert calls.count(("client", "time_post")) == 1
    finally:
        runtime._V3_TIME_PHASES.pop(owner, None)
        runtime._SOURCES.pop(owner, None)
