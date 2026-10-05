"""Stage-bound native public sources; no credential or submission capability.

Legacy collector objects stay replay data. Only this invocation's private source
registry can issue a one-use handoff to the diagnostic coordinator.
"""

from __future__ import annotations

import asyncio
import os
import ssl
from threading import get_ident
from weakref import WeakKeyDictionary

import httpcore
import httpx
from websockets.asyncio.client import ClientConnection

from app.domain.native_clock import native_stamp
from app.domain.source_primitives import (
    PublicReceiptError,
    canonical,
    decode,
    sha,
    utc_from_ns,
    validate_stamps,
)
from app.public_market_source.public_clock import (
    _observe_owned_runtime_clock,
)
from app.public_market_source.public_runtime_journal import (
    MAX_RAW,
    _append,
    _packet_stage_matches,
    _record_clock,
    _runtime_attempt,
    _seal,
    _sealed_readback,
    replay_runtime_attempt,
)
from app.trade_qualification import candle_collector as candles
from app.trade_qualification import demo_public_origin as demo_origin
from app.trade_qualification import market_aux_collector as aux
from app.trade_qualification import public_market_collector as public
from app.trade_qualification import public_market_collector_v2 as public_v2
from app.trade_qualification import quote_collector as quotes
from app.trade_qualification import ws_collector as ws

_ISSUER = object()
_SOURCES = WeakKeyDictionary()
_RESULTS = WeakKeyDictionary()
_INITIAL_RESULTS = WeakKeyDictionary()
_SAFE_CLOCK_FAILURE_CODES = frozenset(
    {
        "clock_jump",
        "clock_lease_expired",
        "clock_reversed",
        "native_clock_domain_unsupported",
        "native_clock_filetime_invalid",
        "native_clock_sample_unbounded",
        "native_precise_clock_unavailable",
        "timestamp_invalid",
        "utc_conversion_failed",
    }
)


class PublicSourceRuntimeError(ValueError):
    """Fixed local codes; never a transport exception or response body."""


def _require_trusted_v2_demo_origin_profile(plan):
    """Refuse new Demo capture until an account-bound origin can be enforced.

    The v2 issuers currently label a Production WS socket as Demo. They also
    have no trusted account-region pin or simulated-trading REST header. A
    caller-supplied region/profile cannot repair that missing provenance.
    Historical v2 journals remain replayable as DENY diagnostics.
    """
    if plan.get("environment") != "demo" or plan.get("ws_origin") == ws.PUBLIC_WS_URL:
        raise PublicSourceRuntimeError("demo_public_origin_mismatch")
    try:
        route = demo_origin.reviewed_demo_public_route(plan.get("registration_region"))
    except demo_origin.DemoPublicOriginError:
        raise PublicSourceRuntimeError(
            "trusted_demo_public_origin_profile_unavailable"
        ) from None
    if (
        plan.get("rest_origin") != route.rest_origin
        or plan.get("ws_origin") != route.ws_origin
    ):
        raise PublicSourceRuntimeError("demo_public_origin_mismatch")
    # A reviewed route is only policy. No issuer currently binds it to an
    # authenticated Demo account/credential session and a native v2 invocation.
    raise PublicSourceRuntimeError("trusted_demo_public_origin_profile_unavailable")


class _Source:
    __slots__ = ("__weakref__",)

    def __init__(self, issuer):
        if issuer is not _ISSUER:
            raise PublicSourceRuntimeError("owned_public_source_required")

    def __copy__(self):
        raise PublicSourceRuntimeError("public_source_not_transferable")

    def __deepcopy__(self, memo):
        raise PublicSourceRuntimeError("public_source_not_transferable")

    def __reduce_ex__(self, protocol):
        raise PublicSourceRuntimeError("public_source_not_transferable")


class _CapturedPublic:
    __slots__ = ("__weakref__",)

    def __init__(self, issuer):
        if issuer is not _ISSUER:
            raise PublicSourceRuntimeError("owned_public_capture_required")

    def __copy__(self):
        raise PublicSourceRuntimeError("public_capture_not_transferable")

    def __deepcopy__(self, memo):
        raise PublicSourceRuntimeError("public_capture_not_transferable")

    def __reduce_ex__(self, protocol):
        raise PublicSourceRuntimeError("public_capture_not_transferable")


class _CapturedInitialPublic(_CapturedPublic):
    """Separate exact type: never accepted as a post-publication capture."""

    __slots__ = ()


class _CapturedPublicV2(_CapturedPublic):
    """Exact v2 post-publication handoff, never accepted by a v1 consumer."""

    __slots__ = ()


class _CapturedInitialPublicV2(_CapturedPublic):
    """Exact v2 initial handoff, distinct from either publication version."""

    __slots__ = ()


def _state(source, *, audit=False):
    if type(source) is not _Source:
        raise PublicSourceRuntimeError("owned_public_source_required")
    state = _SOURCES.get(source)
    if (
        state is None
        or state["pid"] != os.getpid()
        or state["thread"] != get_ident()
        or state["loop"] is not asyncio.get_running_loop()
    ):
        raise PublicSourceRuntimeError("owned_public_source_unavailable")
    if not audit:
        task = asyncio.current_task()
        if task is None or task.cancelling() or state["parent"].cancelling():
            raise asyncio.CancelledError
        if state["loop"].time() >= state["deadline"]:
            raise PublicSourceRuntimeError("public_source_expired")
    return state


def _sample(source):
    state = _state(source)
    stamp = native_stamp()
    validate_stamps((state["last"], stamp))
    at = utc_from_ns(stamp["utc_ns"])
    if (
        stamp["utc_ns"] <= state["not_before"]["utc_ns"]
        or stamp["monotonic_ns"] <= state["not_before"]["monotonic_ns"]
        or at >= state["expires_at"]
    ):
        raise PublicSourceRuntimeError("public_source_clock_or_expiry_invalid")
    state["last"] = stamp
    return at


def _check_inputs(source, *, clock, report_id, instrument_id, barrier_completed_at):
    state = _state(source)
    if (
        clock is not state["clock"]
        or report_id != state["report_id"]
        or instrument_id != state["instrument_id"]
        or barrier_completed_at != state["publication_completed_at"]
    ):
        raise PublicSourceRuntimeError("public_source_scope_mismatch")


def _new_owned_client(source, role):
    state = _state(source)
    if role not in ("quote", "candles", "market_aux") or role in state["client_roles"]:
        raise PublicSourceRuntimeError("public_source_client_scope_invalid")
    context = ssl.create_default_context()
    client = httpx.AsyncClient(
        verify=context, trust_env=False, follow_redirects=False, auth=None
    )
    state["clients"][client] = (role, context)
    state["client_roles"].add(role)
    _verified_client(source, client)
    return client


def _verified_client(source, client):
    state = _state(source)
    entry = state["clients"].get(client)
    if entry is None or type(client) is not httpx.AsyncClient:
        raise PublicSourceRuntimeError("owned_public_client_required")
    quotes._public_client(client, require_empty_cookies=False)
    transport = client._transport
    if (
        type(transport) is not httpx.AsyncHTTPTransport
        or type(transport._pool) is not httpcore.AsyncConnectionPool
        or transport._pool._retries != 0
    ):
        raise PublicSourceRuntimeError("native_public_transport_required")
    context = entry[1]
    if (
        type(context) is not ssl.SSLContext
        or transport._pool._ssl_context is not context
        or context.verify_mode != ssl.CERT_REQUIRED
        or context.check_hostname is not True
    ):
        raise PublicSourceRuntimeError("verified_public_tls_required")
    return entry


def _tls_proof(tls, context, hostname):
    if (
        type(tls) is not ssl.SSLObject
        or tls.context is not context
        or tls.server_hostname != hostname
        or tls.version() not in ("TLSv1.2", "TLSv1.3")
    ):
        raise PublicSourceRuntimeError("public_tls_binding_missing")
    certificate = tls.getpeercert(binary_form=True)
    if type(certificate) is not bytes or not certificate:
        raise PublicSourceRuntimeError("public_tls_binding_missing")
    return {
        "classification": "owned_native_tls",
        "hostname": hostname,
        "version": tls.version(),
        "peer_sha256": sha(certificate),
    }


def _http_tls(source, client, response):
    _, context = _verified_client(source, client)
    stream = response.extensions.get("network_stream")
    if stream is None:
        raise PublicSourceRuntimeError("public_tls_binding_missing")
    return _tls_proof(stream.get_extra_info("ssl_object"), context, "www.okx.com")


def _event(source, kind, metadata, raw=None):
    state = _state(source, audit=True)
    return _append(state["attempt"], kind, metadata, raw)


def _request_failure_code(error):
    """Retain only fixed, allow-listed clock rejection codes in source journals."""
    if type(error) is PublicReceiptError:
        args = error.args
        if (
            type(args) is tuple
            and len(args) == 1
            and type(args[0]) is str
            and args[0] in _SAFE_CLOCK_FAILURE_CODES
        ):
            return args[0]
    return "source_or_transport_rejected"


def _request_scope(source, client, request, started):
    state = _state(source)
    role, _ = _verified_client(source, client)
    if (
        type(request) is not httpx.Request
        or request.method != "GET"
        or request.url.scheme != "https"
        or request.url.host != "www.okx.com"
        or request.url.port not in (None, 443)
        or request.content
    ):
        raise PublicSourceRuntimeError("public_request_scope_invalid")
    allowed_paths = {
        "quote": {p for _, p in quotes.ENDPOINTS},
        "candles": {candles.ENDPOINT},
        "market_aux": {p for _, p in aux.ENDPOINTS},
    }
    pairs = tuple(request.url.params.multi_items())
    query = dict(pairs)
    if (
        request.url.path not in allowed_paths[role]
        or len(query) != len(pairs)
        or query.get("instId") != state["instrument_id"]
        or started != utc_from_ns(state["last"]["utc_ns"])
    ):
        raise PublicSourceRuntimeError("public_request_scope_invalid")
    expected = {"instId": state["instrument_id"]}
    if role == "quote" and request.url.path.endswith("/mark-price"):
        expected["instType"] = "SWAP"
    elif role == "market_aux":
        expected["sz" if request.url.path.endswith("/books") else "instType"] = (
            "5" if request.url.path.endswith("/books") else "SWAP"
        )
    elif role == "candles":
        selected = state["policy"].candles
        if (
            query.get("bar") not in {item.timeframe for item in selected.requests}
            or not query.get("limit", "").isascii()
            or not query.get("limit", "").isdigit()
            or not 1 <= int(query["limit"]) <= selected.page_size
        ):
            raise PublicSourceRuntimeError("public_request_query_invalid")
        expected.update(bar=query["bar"], limit=query["limit"])
        if "after" in query:
            if (
                not query["after"].isascii()
                or not query["after"].isdigit()
                or len(query["after"]) > 20
            ):
                raise PublicSourceRuntimeError("public_request_query_invalid")
            expected["after"] = query["after"]
    if query != expected:
        raise PublicSourceRuntimeError("public_request_query_invalid")
    if (
        set(request.headers) - {"host", "accept", "accept-encoding", "user-agent"}
        or request.headers.get("accept-encoding") != "identity"
    ):
        raise PublicSourceRuntimeError("public_request_headers_invalid")
    index = state["request_count"]
    if index >= 64:
        raise PublicSourceRuntimeError("public_request_limit")
    state["request_count"] += 1
    _event(
        source,
        "request",
        {
            "id": index,
            "role": role,
            "method": "GET",
            "origin": quotes.BASE_URL,
            "endpoint": request.url.path,
            "query": pairs,
            "started": state["last"],
        },
    )
    return index


async def _fetch_http(source, client, request, started, policy):
    """Retain actual bytes before parser rejection, without changing old DTOs."""
    index = _request_scope(source, client, request, started)
    body, response, cancelled = bytearray(), None, False
    rejection = None
    try:
        async with asyncio.timeout(policy.request_timeout_seconds):
            response = await client.send(
                request, auth=None, follow_redirects=False, stream=True
            )
            if type(response) is not httpx.Response:
                response = None
                raise PublicSourceRuntimeError("public_response_type_invalid")
            # If a returned response cannot be timed, retain its bounded safe
            # headers with an explicit absent receipt stamp before rejecting.
            clock_error = None
            try:
                received = _sample(source)
                receipt_stamp = _state(source)["last"]
            except BaseException as exc:  # noqa: BLE001 -- retain observed bytes before propagating
                received, receipt_stamp, clock_error = None, None, exc
            try:
                if clock_error is not None:
                    raise PublicSourceRuntimeError("public_receipt_clock_missing")
                proof = _http_tls(source, client, response)
            except (ValueError, OSError, AttributeError):
                proof = {
                    "classification": "unverified",
                    "hostname": "www.okx.com",
                    "version": None,
                    "peer_sha256": None,
                }
                rejection = "public_tls_binding_missing"
            headers = tuple(
                (k, v)
                for k, v in response.headers.multi_items()
                if k in {"content-type", "content-length", "content-encoding", "date"}
            )
            truncated = len(headers) > 16 or any(len(v) > 1024 for _, v in headers)
            _event(
                source,
                "headers",
                {
                    "id": index,
                    "received": receipt_stamp,
                    "status": response.status_code,
                    "tls": proof,
                    "safe_headers": tuple((k, v[:1024]) for k, v in headers[:16]),
                    "truncated": truncated,
                },
            )
            if clock_error is not None:
                raise clock_error
            if (
                response.status_code != 200
                or response.url != request.url
                or response.history
                or truncated
                or len(dict(headers)) != len(headers)
            ):
                rejection = rejection or "public_response_rejected"
            if (
                response.headers.get("content-type", "")
                .split(";", 1)[0]
                .strip()
                .lower()
                != "application/json"
                or response.headers.get("content-encoding", "identity").lower()
                != "identity"
            ):
                rejection = rejection or "public_response_encoding_invalid"
            length = response.headers.get("content-length")
            if length is not None and (
                not length.isascii()
                or not length.isdigit()
                or len(length) > 10
                or int(length) > policy.max_response_bytes
            ):
                rejection = rejection or "public_response_length_invalid"
            if response.is_closed or response.is_stream_consumed:
                raise PublicSourceRuntimeError("public_response_not_streaming")
            async for chunk in response.stream:
                if type(chunk) is not bytes:
                    raise PublicSourceRuntimeError("public_response_chunk_invalid")
                kept = chunk[: max(0, policy.max_response_bytes - len(body))]
                clock_error = None
                try:
                    _sample(source)
                    receipt_stamp = _state(source)["last"]
                except BaseException as exc:  # noqa: BLE001 -- retain observed bytes before propagating
                    receipt_stamp, clock_error = None, exc
                _event(
                    source,
                    "chunk",
                    {
                        "id": index,
                        "received": receipt_stamp,
                        "observed_bytes": len(chunk),
                        "observed_sha256": sha(chunk),
                        "truncated": len(kept) != len(chunk),
                    },
                    kept,
                )
                body.extend(kept)
                if clock_error is not None:
                    raise clock_error
                if len(kept) != len(chunk):
                    raise PublicSourceRuntimeError("public_response_limit")
            _sample(source)
            _event(
                source,
                "body_complete",
                {
                    "id": index,
                    "completed": _state(source)["last"],
                    "body_bytes": len(body),
                    "body_sha256": sha(bytes(body)),
                },
            )
            if rejection is not None:
                raise PublicSourceRuntimeError(rejection)
            if length is not None and len(body) != int(length):
                raise PublicSourceRuntimeError("public_response_length_mismatch")
    except asyncio.CancelledError:
        cancelled = True
        _event(source, "request_failed", {"id": index, "code": "cancelled"})
        raise
    except Exception as exc:
        _event(
            source,
            "request_failed",
            {"id": index, "code": _request_failure_code(exc)},
        )
        raise
    finally:
        if response is not None:
            await quotes._close_response(response, pending_cancel=cancelled)
    completed = _sample(source)
    _event(
        source, "response_closed", {"id": index, "completed": _state(source)["last"]}
    )
    _state(source)["responses"].append(
        (
            request.url.path,
            tuple(request.url.params.multi_items()),
            bytes(body),
            started,
            received,
            completed,
        )
    )
    return bytes(body), received, completed


def _ws_options(source, started):
    state = _state(source)
    if state["ws_claimed"] or started != utc_from_ns(state["last"]["utc_ns"]):
        raise PublicSourceRuntimeError("public_socket_already_claimed")
    state["ws_claimed"] = True
    state["ws_context"] = ssl.create_default_context()
    _event(
        source, "ws_connect", {"endpoint": ws.PUBLIC_WS_URL, "started": state["last"]}
    )
    return {"ssl": state["ws_context"]}


def _ws_connected(source, socket):
    state = _state(source)
    if type(socket) is not ClientConnection or state["socket"] is not None:
        raise PublicSourceRuntimeError("owned_public_socket_required")
    context = state["ws_context"]
    if context.verify_mode != ssl.CERT_REQUIRED or context.check_hostname is not True:
        raise PublicSourceRuntimeError("verified_public_tls_required")
    proof = _tls_proof(
        socket.transport.get_extra_info("ssl_object"), context, "ws.okx.com"
    )
    state["socket"] = socket
    _event(source, "ws_connected", {"connected": state["last"], "tls": proof})


def _ws_record(source, socket, kind, raw=None):
    state = _state(source)
    if socket is not state["socket"] or kind not in {
        "ws_subscribe",
        "ws_subscribed",
        "ws_closed",
    }:
        raise PublicSourceRuntimeError("public_socket_scope_invalid")
    _event(source, kind, {"observed": state["last"]}, raw)
    if kind != "ws_subscribed":
        state["ws_records"].append((kind, raw, state["last"]))


async def _receive_ws(source, socket, policy, kind):
    state = _state(source)
    if socket is not state["socket"] or kind not in {"ws_ack", "ws_ticker"}:
        raise PublicSourceRuntimeError("public_socket_scope_invalid")
    async with asyncio.timeout(policy.receive_timeout_seconds):
        raw = await socket.recv()
    # Preserve returned application bytes even if the immediately following
    # clock sample/cancellation check cannot establish their receipt time.
    if type(raw) is str:
        body = raw.encode("utf-8")
    elif type(raw) is bytes:
        body = raw
    else:
        raise PublicSourceRuntimeError("public_socket_message_type_invalid")
    kept = body[: policy.max_message_bytes]
    try:
        received = _sample(source)
    except BaseException:
        _event(
            source,
            kind,
            {
                "observed": None,
                "message_type": "text" if type(raw) is str else "binary",
                "observed_bytes": len(body),
                "observed_sha256": sha(body),
                "truncated": len(kept) != len(body),
            },
            kept,
        )
        raise
    _event(
        source,
        kind,
        {
            "observed": state["last"],
            "message_type": "text" if type(raw) is str else "binary",
            "observed_bytes": len(body),
            "observed_sha256": sha(body),
            "truncated": len(kept) != len(body),
        },
        kept,
    )
    if type(raw) is not str or not body or len(kept) != len(body):
        raise PublicSourceRuntimeError("public_socket_message_invalid")
    state["ws_records"].append((kind, body, state["last"]))
    return body, received


def _check_packet_binding(state, packet):
    observations = (
        *packet.quote.provenance,
        *(page for frame in packet.candles.frames for page in frame.pages),
        *packet.market_aux.provenance,
    )
    _check_raw_inventory(state, observations, packet.ws)


def _check_packet_binding_v2(state, packet):
    _, (_, _quote, provenance, candle_packet, market_aux, reference) = public_v2._parts(
        packet
    )
    _check_raw_inventory(
        state,
        (
            *provenance,
            *(page for frame in candle_packet.frames for page in frame.pages),
            *market_aux.provenance,
        ),
        reference,
    )


def _check_raw_inventory(state, observations, reference):
    actual = list(state["responses"])
    for item in observations:
        endpoint = getattr(item, "endpoint", candles.ENDPOINT)
        expected = (
            endpoint,
            tuple(item.parameters),
            item.response_body,
            item.request_started_at,
            item.received_at,
            item.completed_at,
        )
        matches = [
            i
            for i, value in enumerate(actual)
            if value[0] == expected[0]
            and sorted(value[1]) == sorted(expected[1])
            and value[2:] == expected[2:]
        ]
        if len(matches) != 1:
            raise PublicSourceRuntimeError("public_packet_source_mismatch")
        actual.pop(matches[0])
    if (
        actual
        or len(state["clients"]) != 3
        or any(not client.is_closed for client in state["clients"])
    ):
        raise PublicSourceRuntimeError("public_packet_inventory_mismatch")
    if [item[0] for item in state["ws_records"]] != [
        "ws_subscribe",
        "ws_ack",
        "ws_ticker",
        "ws_closed",
    ]:
        raise PublicSourceRuntimeError("public_socket_inventory_mismatch")
    # Exact raw fields are replayed by the existing WS validator; the runtime
    # additionally binds their application bytes to this concrete socket.
    expected_ws = [
        reference.subscription_body,
        reference.ack.raw_frame,
        reference.ticker.raw_frame,
    ]
    if [item[1] for item in state["ws_records"][:3]] != expected_ws or [
        utc_from_ns(item[2]["utc_ns"]) for item in state["ws_records"]
    ] != [
        reference.subscribe_started_at,
        reference.ack.received_at,
        reference.ticker.received_at,
        reference.closed_at,
    ]:
        raise PublicSourceRuntimeError("public_socket_packet_mismatch")


async def _capture_after_publication(publication, policy, root):
    return await _capture_owned(publication, policy, root, stage="post_publication")


async def _capture_initial_public(initial_scope, policy, root):
    return await _capture_owned(initial_scope, policy, root, stage="initial_public")


async def _capture_after_publication_v2(publication, policy, root):
    return await _capture_owned(
        publication, policy, root, stage="post_publication", _version=2
    )


async def _capture_initial_public_v2(initial_scope, policy, root):
    return await _capture_owned(
        initial_scope, policy, root, stage="initial_public", _version=2
    )


async def _capture_owned(scope, policy, root, *, stage, _version=1):
    # The common transport never accepts a caller plan/DTO as ownership. Each
    # stage consumes its own exact private issuer before any clock or IO.
    if type(stage) is not str or type(_version) is not int or _version not in (1, 2):
        raise PublicSourceRuntimeError("public_capture_stage_invalid")
    if stage == "post_publication":
        from app.trade_qualification.post_g12_public_runtime import (
            _take_publication,
            _take_publication_v2,
        )

        claim = (_take_publication_v2 if _version == 2 else _take_publication)(scope)
        version, boundary = f"ctcc.public.runtime_plan.v{_version}", claim["barrier"]
        carrier_type, results = (
            (_CapturedPublicV2 if _version == 2 else _CapturedPublic),
            _RESULTS,
        )
    elif stage == "initial_public":
        from app.trade_qualification.qualification_runtime import (
            _take_initial_scope,
            _take_initial_scope_v2,
        )

        claim = (_take_initial_scope_v2 if _version == 2 else _take_initial_scope)(
            scope
        )
        version, boundary = (
            f"ctcc.public.initial_runtime_plan.v{_version}",
            claim["invocation_started"],
        )
        carrier_type, results = (
            (_CapturedInitialPublicV2 if _version == 2 else _CapturedInitialPublic),
            _INITIAL_RESULTS,
        )
    else:
        raise PublicSourceRuntimeError("public_capture_stage_invalid")
    selected = (
        public_v2._policy_copy(policy) if _version == 2 else public._policy_copy(policy)
    )
    policy_digest = (
        public_v2._policy_digest(selected)
        if _version == 2
        else public._digest(selected)
    )
    if (stage == "initial_public" or _version == 2) and claim["plan"][
        "policy_sha256"
    ] != policy_digest:
        raise PublicSourceRuntimeError("public_initial_policy_changed")
    plan = {
        "schema_version": version,
        **claim["plan"],
        "policy_sha256": policy_digest,
    }
    if _version == 2 and (
        plan.get("stage") != stage
        or any(plan.get(key) != value for key, value in public_v2._plan_pins().items())
    ):
        raise PublicSourceRuntimeError("public_v2_owned_plan_required")
    if _version == 2:
        # This must precede journal creation, clock sampling and all network IO.
        _require_trusted_v2_demo_origin_profile(plan)
    source = _Source(_ISSUER)
    state = {
        **claim,
        "pid": os.getpid(),
        "thread": get_ident(),
        "loop": asyncio.get_running_loop(),
        "parent": asyncio.current_task(),
        "last": boundary,
        "not_before": boundary,
        "clients": {},
        "client_roles": set(),
        "request_count": 0,
        "responses": [],
        "ws_claimed": False,
        "ws_context": None,
        "socket": None,
        "ws_records": [],
    }
    state["policy"] = selected
    if _version == 2:
        state["plan"] = plan
    state["deadline"] = state["loop"].time() + selected.total_timeout_seconds
    state["clock"] = lambda: _sample(source)
    _SOURCES[source] = state
    packet = None
    try:
        with _runtime_attempt(root, plan) as attempt:
            state["attempt"] = attempt
            try:
                result = _record_clock(
                    attempt, "before", _observe_owned_runtime_clock(attempt, "before")
                )
                if result["outcome"] != "accepted":
                    raise PublicSourceRuntimeError("native_public_clock_rejected")
                _accept_clock_sample(state, result)
                if _version == 2:
                    packet = await public_v2._collect_owned_public_market_v2(source)
                else:
                    packet = await public.collect_public_market(
                        clock=state["clock"],
                        report_id=state["report_id"],
                        instrument_id=state["instrument_id"],
                        policy=selected,
                        barrier_completed_at=state["publication_completed_at"],
                        _source=source,
                    )
                _state(source)
                result = _record_clock(
                    attempt, "after", _observe_owned_runtime_clock(attempt, "after")
                )
                if result["outcome"] != "accepted":
                    raise PublicSourceRuntimeError("native_public_clock_rejected")
                _accept_clock_sample(state, result)
                if _version == 2:
                    packet = public_v2._parts(packet)[0]
                    _check_packet_binding_v2(state, packet)
                else:
                    packet = public.validate_collected_public_market(packet)
                    _check_packet_binding(state, packet)
                _sample(source)
                encoded = (
                    packet.packet_json
                    if _version == 2
                    else canonical(packet.model_dump(mode="json", round_trip=True))
                )
                _append(
                    attempt, "packet", {"bundle_sha256": packet.bundle_sha256}, encoded
                )
                digest = _seal(
                    attempt,
                    success=True,
                    stamp=state["last"],
                    packet_sha256=sha(encoded),
                )
                plan_raw, summary_raw, packet_raw = _sealed_readback(attempt)
                packet = _replay_captured_packet(plan_raw, summary_raw, packet_raw)
                if packet_raw != encoded or sha(summary_raw) != digest:
                    raise PublicSourceRuntimeError("public_capture_readback_changed")
                _state(source)
                carrier = carrier_type(_ISSUER)
                results[carrier] = (
                    state["invocation"],
                    state["parent"],
                    os.getpid(),
                    get_ident(),
                    packet,
                    digest,
                    state["last"],
                    state["expires_at"],
                )
                return carrier
            except (Exception, asyncio.CancelledError):
                try:
                    try:
                        terminal = native_stamp()
                    except PublicReceiptError:
                        terminal = None
                    _seal(attempt, success=False, stamp=terminal)
                except Exception:  # noqa: BLE001, S110 -- retain tail; no private exception logs
                    # Preserve incomplete native artifacts; no replacement root,
                    # cleanup or retry can turn missing readback into success.
                    pass
                raise
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 -- fixed boundary code only
        raise PublicSourceRuntimeError("public_source_capture_denied") from None
    finally:
        _SOURCES.pop(source, None)


def _consume_public_capture(value, invocation):
    return _consume_capture(value, invocation, _CapturedPublic, _RESULTS)


def _consume_initial_public_capture(value, invocation):
    return _consume_capture(value, invocation, _CapturedInitialPublic, _INITIAL_RESULTS)


def _consume_public_capture_v2(value, invocation):
    return _consume_capture(value, invocation, _CapturedPublicV2, _RESULTS)


def _consume_initial_public_capture_v2(value, invocation):
    return _consume_capture(
        value, invocation, _CapturedInitialPublicV2, _INITIAL_RESULTS
    )


def _consume_capture(value, invocation, expected, registry):
    if type(value) is not expected:
        raise PublicSourceRuntimeError("owned_public_capture_required")
    result = registry.pop(value, None)
    task = asyncio.current_task()
    if (
        result is None
        or result[0] is not invocation
        or result[1] is not task
        or result[2] != os.getpid()
        or result[3] != get_ident()
        or task is None
        or task.cancelling()
    ):
        raise PublicSourceRuntimeError("owned_public_capture_unavailable")
    stamp = native_stamp()
    validate_stamps((result[6], stamp))
    if utc_from_ns(stamp["utc_ns"]) >= result[7]:
        raise PublicSourceRuntimeError("public_capture_expired")
    packet = (
        public_v2._parts(result[4])[0]
        if expected in (_CapturedPublicV2, _CapturedInitialPublicV2)
        else public.validate_collected_public_market(result[4])
    )
    return packet, result[5], stamp


def _accept_clock_sample(state, result):
    evidence = result["observation"]
    if evidence["schema_version"] != "ctcc.windows_clock_observation.v2":
        raise PublicSourceRuntimeError("native_public_clock_version_required")
    stamp = evidence["sample"]
    validate_stamps(
        (state["last"], evidence["diagnostic"]["host_before"]["request_start"], stamp)
    )
    if utc_from_ns(stamp["utc_ns"]) >= state["expires_at"]:
        raise PublicSourceRuntimeError("public_source_expired")
    state["last"] = stamp


def _replay_captured_packet(plan_raw, summary_raw, packet_raw):
    """Recompute the legacy component parsers, never a persisted source permit."""
    plan = decode(plan_raw, MAX_RAW)
    summary = decode(summary_raw, MAX_RAW)
    if summary["disposition"] != "captured":
        raise PublicSourceRuntimeError("public_capture_incomplete")
    if plan["schema_version"] in {
        "ctcc.public.initial_runtime_plan.v2",
        "ctcc.public.runtime_plan.v2",
    }:
        return _replay_captured_packet_v2(plan_raw, plan, summary, packet_raw)
    # The low-level journal verifies the raw HTTP/WS joins. The qualification
    # layer replays its own typed semantic contracts without introducing an
    # account/strategy import into the public-source package.
    packet = public.CollectedPublicMarket.model_validate_json(packet_raw, strict=True)
    packet = public.validate_collected_public_market(packet)
    if (
        canonical(packet.model_dump(mode="json", round_trip=True)) != packet_raw
        or summary["packet_sha256"] != sha(packet_raw)
        or summary["plan_sha256"] != sha(plan_raw)
        or packet.report_id != plan["report_id"]
        or packet.instrument_id != plan["instrument_id"]
        or not _packet_stage_matches(
            plan,
            None
            if packet.barrier_completed_at is None
            else packet.barrier_completed_at.isoformat(),
            exact=True,
        )
        or public._digest(packet.policy) != plan["policy_sha256"]
    ):
        raise PublicSourceRuntimeError("public_capture_readback_mismatch")
    return packet


def _replay_captured_packet_v2(plan_raw, plan, summary, packet_raw):
    packet = public_v2.replay_collected_public_market_v2(
        packet_raw, expected_sha256=summary["packet_sha256"]
    )
    document = decode(packet_raw, public_v2.MAX_PACKET_BYTES)
    boundary = (
        plan["invocation_started"]
        if plan["stage"] == "initial_public"
        else plan["barrier"]
    )
    if (
        summary["plan_sha256"] != sha(plan_raw)
        or any(
            document[key] != plan[key]
            for key in (
                "report_id",
                "instrument_id",
                "invocation_id",
                "stage",
                "environment",
                "policy_sha256",
                *public_v2._plan_pins(),
            )
        )
        or not _packet_stage_matches(plan, document["barrier_completed_at"], exact=True)
        or public_v2._time(document["started_at"]) <= utc_from_ns(boundary["utc_ns"])
        or public_v2._time(document["completed_at"])
        >= public_v2._time(plan["expires_at"])
    ):
        raise PublicSourceRuntimeError("public_v2_capture_readback_mismatch")
    return packet


def replay_public_runtime(directory, *, expected_plan_sha256):
    """Offline integrity/semantic replay; the returned DTO grants no authority."""
    summary, events = replay_runtime_attempt(
        directory, expected_plan_sha256=expected_plan_sha256
    )
    packet = None
    if summary["disposition"] == "captured":
        packet = _replay_captured_packet(
            directory.read("plan.json", MAX_RAW),
            directory.read("summary.json", MAX_RAW),
            directory.read(f"event-{events[-1]['index']:04d}.raw", MAX_RAW),
        )
    return summary, events, packet
